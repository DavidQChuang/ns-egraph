import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestInstall(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="egraph install ")
        self.addCleanup(directory.cleanup)
        self.project = Path(directory.name)

    def installer(self, action, host, *args, ok=True):
        command = [sys.executable, "-B", ROOT / "install.py", action, host, "--project", self.project, *args]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def write_json(self, relative, data):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_claude_merge_and_remove_preserve_other_settings(self):
        original = {
            "permissions": {"allow": ["Read"]},
            "hooks": {"UserPromptSubmit": [{"matcher": "example", "hooks": [
                {"type": "command", "command": "echo custom"},
            ]}]},
        }
        path = self.write_json(".claude/settings.local.json", original)
        self.installer("install", "claude")
        once = path.read_bytes()
        self.installer("install", "claude")
        self.assertEqual(path.read_bytes(), once)
        settings = json.loads(once)
        self.assertEqual(settings["permissions"], original["permissions"])
        self.assertEqual(len(settings["hooks"]["UserPromptSubmit"]), 2)
        self.assertTrue((self.project / ".claude/rules/ns-egraph.md").is_file())

        self.installer("remove", "claude")
        self.installer("remove", "claude")
        self.assertEqual(json.loads(path.read_bytes()), original)
        self.assertFalse((self.project / ".claude/rules/ns-egraph.md").exists())

    def test_both_hosts_launch_and_remove_independently(self):
        self.installer("install", "claude")
        self.installer("install", "kiro")
        memory = self.project / ".ns-egraph/graphs"
        graph = memory / "greenhouse.json"
        shutil.copyfile(ROOT / "examples/greenhouse.json", graph)
        (self.project / ".ns-egraph" / "config.json").write_text(json.dumps({"recall_mode": "excerpt"}),
                                                                encoding="utf-8")
        settings = json.loads((self.project / ".claude/settings.local.json").read_bytes())
        claude = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        kiro_path = self.project / ".kiro/hooks/ns-egraph.json"
        kiro = json.loads(kiro_path.read_bytes())["hooks"][0]["action"]["command"]
        event = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "test", "prompt": "heat"})
        subdirectory = self.project / "nested"
        subdirectory.mkdir()
        for host, command, cwd in (("claude", claude, subdirectory), ("kiro", kiro, self.project)):
            with self.subTest(host=host):
                result = subprocess.run(command, shell=True, cwd=cwd, input=event, text=True, capture_output=True,
                                        env={**os.environ, "CLAUDE_PROJECT_DIR": str(self.project), "USER_PROMPT": "heat"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("greenhouse.json#h-5@2", result.stdout)

        self.installer("remove", "claude")
        self.assertTrue(kiro_path.is_file())
        self.assertFalse((self.project / ".claude/settings.local.json").exists())
        self.installer("remove", "kiro")
        self.assertFalse(kiro_path.exists())
        self.assertFalse((self.project / ".kiro/steering/ns-egraph.md").exists())
        self.assertEqual(graph.read_bytes(), (ROOT / "examples/greenhouse.json").read_bytes())

    def test_kiro_profile_hooks_and_resource_are_merged_and_removed(self):
        for variant, hooks in (("map", {"userPromptSubmit": [{"command": "echo custom"}]}),
                               ("list", [{"name": "custom", "trigger": "SessionStart",
                                         "action": {"type": "command", "command": "echo custom"}}])):
            with self.subTest(variant=variant):
                profile = f".kiro/agents/{variant}.json"
                original = {"name": variant, "hooks": hooks, "resources": ["file://AGENTS.md"]}
                path = self.write_json(profile, original)
                self.installer("install", "kiro", "--kiro-agent", profile)
                settings = json.loads(path.read_bytes())
                self.assertIn("file://.kiro/steering/ns-egraph.md", settings["resources"])
                installed_hooks = settings["hooks"] if variant == "list" else settings["hooks"]["userPromptSubmit"]
                self.assertIn(".ns-egraph/tool/integrations", json.dumps(installed_hooks))
                self.assertFalse((self.project / ".kiro/hooks/ns-egraph.json").exists())
                once = path.read_bytes()
                self.installer("install", "kiro")
                self.assertEqual(path.read_bytes(), once)
                self.installer("remove", "kiro")
                self.assertEqual(json.loads(path.read_bytes()), original)

    def test_remove_refuses_edited_steering_and_keeps_hooks(self):
        self.installer("install", "kiro")
        path = self.project / ".kiro/steering/ns-egraph.md"
        path.write_text("My custom guidance\n")
        result = self.installer("remove", "kiro", ok=False)
        self.assertIn("local edits", result.stderr)
        self.assertEqual(path.read_text(), "My custom guidance\n")
        self.assertTrue((self.project / ".kiro/hooks/ns-egraph.json").is_file())

    def test_install_refuses_unrelated_existing_rule(self):
        path = self.project / ".claude/rules/ns-egraph.md"
        path.parent.mkdir(parents=True)
        path.write_text("My unrelated local rule\n")
        self.installer("install", "claude", ok=False)
        self.assertEqual(path.read_text(), "My unrelated local rule\n")
        self.assertFalse((self.project / ".claude/settings.local.json").exists())

    def test_install_refuses_symlinked_host_paths(self):
        outside = tempfile.TemporaryDirectory(prefix="outside hook ")
        self.addCleanup(outside.cleanup)
        steering = self.project / ".kiro/steering"
        steering.parent.mkdir()
        steering.symlink_to(outside.name, target_is_directory=True)
        result = self.installer("install", "kiro", ok=False)
        self.assertIn("local project path", result.stderr)
        self.assertFalse((self.project / ".kiro/hooks/ns-egraph.json").exists())
        self.assertEqual(list(Path(outside.name).iterdir()), [])

    def test_install_checks_semantic_interpreter_before_registering_hooks(self):
        self.installer("install", "kiro", "--semantic", ok=False)
        hook_path = self.project / ".kiro/hooks/ns-egraph.json"
        self.assertFalse(hook_path.exists())
        relative = ".ns-egraph/tool/.venv/Scripts/python.exe" if os.name == "nt" else ".ns-egraph/tool/.venv/bin/python"
        interpreter = self.project / relative
        interpreter.parent.mkdir(parents=True)
        interpreter.touch()
        self.installer("install", "kiro", "--semantic")
        hooks = json.loads(hook_path.read_bytes())["hooks"]
        self.assertTrue(all(relative in hook["action"]["command"] and "--semantic" in hook["action"]["command"]
                            for hook in hooks))
