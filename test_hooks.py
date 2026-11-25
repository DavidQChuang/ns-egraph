import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from recall import MAX_CHARS, checkpoint

HERE = Path(__file__).resolve().parent


class TestHookInstall(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="egraph hook ")
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)
        self.graphs = self.project / ".ns-egraph" / "graphs"
        self.graphs.mkdir(parents=True)
        shutil.copyfile(HERE / "examples" / "greenhouse.json", self.graphs / "greenhouse.json")
        tool = self.graphs.parent / "tool"
        shutil.copytree(HERE / "integrations", tool / "integrations")
        for filename in ("recall.py", "egraph.py"):
            shutil.copyfile(HERE / filename, tool / filename)
        self.kiro = json.loads((HERE / "integrations" / "kiro" / "hooks.json").read_text())
        self.legacy = json.loads((HERE / "integrations" / "kiro" / "agent-hooks-v2.json").read_text())
        self.claude = json.loads((HERE / "integrations" / "claude" / "settings.json").read_text())

    def run_hook(self, command, event=None, **env):
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"USER_PROMPT", "KIRO_SESSION_ID", "CLAUDE_ENV_FILE"}}
        result = subprocess.run(command, shell=True, cwd=self.project,
                                input=json.dumps(event) if event is not None else "",
                                env={**environment, **env}, capture_output=True, text=True, check=True)
        self.assertEqual(result.stderr, "")
        return result.stdout.strip()

    def kiro_command(self, trigger):
        return next(hook["action"]["command"] for hook in self.kiro["hooks"] if hook["trigger"] == trigger)

    def test_installed_claude_adapter_returns_host_envelope(self):
        command = self.claude["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        event = {"hook_event_name": "UserPromptSubmit", "session_id": "alpha", "prompt": "heat"}
        text = self.run_hook(command, event, CLAUDE_PROJECT_DIR=str(self.project))
        context = json.loads(text)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("greenhouse.json#h-5@2", context)

    def test_kiro_prompt_from_environment_and_legacy_json(self):
        command = self.kiro_command("UserPromptSubmit")
        text = self.run_hook(command, USER_PROMPT="Did we rule out heat in the east bed?")
        self.assertIn("greenhouse.json#h-5@2", text)
        self.assertLessEqual(len(text), MAX_CHARS)
        self.assertEqual(self.run_hook(command, USER_PROMPT="Explain photosynthesis"), "")
        legacy = self.legacy["hooks"]["userPromptSubmit"][0]["command"]
        self.assertIn("greenhouse.json#h-5@2",
                      self.run_hook(legacy, {"hook_event_name": "userPromptSubmit", "prompt": "heat"}))

    def test_kiro_restores_only_the_matching_session(self):
        checkpoint(self.graphs, Path("greenhouse.json"), "alpha", "q-11", False)
        for name in ("SessionStart", "PostCompact"):
            command = self.kiro_command(name)
            with self.subTest(name=name):
                self.assertEqual(self.run_hook(command, {"hook_event_name": name, "session_id": "beta"}), "")
                self.assertIn("Next unresolved item: greenhouse.json#q-11",
                              self.run_hook(command, {"hook_event_name": name, "session_id": "alpha"}))
        legacy = self.legacy["hooks"]["agentSpawn"][0]["command"]
        self.assertIn("greenhouse.json#q-11",
                      self.run_hook(legacy, {"hook_event_name": "agentSpawn"}, KIRO_SESSION_ID="alpha"))
        command = self.kiro_command("UserPromptSubmit")
        self.assertEqual(self.run_hook(command, USER_PROMPT="continue"), "")
        self.assertIn("greenhouse.json#q-11", self.run_hook(command, USER_PROMPT="continue", KIRO_SESSION_ID="alpha"))
        checkpoint(self.graphs, Path("greenhouse.json"), "alpha", None, True)
        self.assertEqual(self.run_hook(command, USER_PROMPT="continue", KIRO_SESSION_ID="alpha"), "")

    def test_kiro_invalid_input_is_quiet(self):
        command = self.kiro_command("UserPromptSubmit")
        self.assertEqual(self.run_hook(command, ["unexpected"], USER_PROMPT="heat"), "")
        self.assertEqual(self.run_hook(command, {"hook_event_name": []}, USER_PROMPT="heat"), "")
