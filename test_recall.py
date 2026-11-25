import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from egraph import Graph
from recall import MAX_CHARS, checkpoint, recall, resume

HERE = Path(__file__).resolve().parent


class TestRecall(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.graph = self.root / "greenhouse.json"
        shutil.copyfile(HERE / "examples" / self.graph.name, self.graph)

    def hook(self, name, session="session-a", **fields):
        event = dict(hook_event_name=name, session_id=session, **fields)
        result = subprocess.run(
            [sys.executable, "-B", HERE / "recall.py", "claude-hook", self.root],
            input=json.dumps(event), text=True, capture_output=True, check=True,
            env={**os.environ, "CLAUDE_ENV_FILE": str(self.root / "claude.env")},
        )
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"] if result.stdout else ""

    def test_relevant_query_returns_revision_and_counterevidence(self):
        text = recall(self.root, "Did we rule out heat in the east bed?")
        self.assertIn("greenhouse.json#h-5@2 [hypothesis / refuted]", text)
        self.assertIn("only east seedlings wilted", text)
        self.assertLessEqual(len(text.splitlines()), 4)
        self.assertLessEqual(len(text), MAX_CHARS)

    def test_graph_name_resumes_open_work(self):
        self.assertIn("Do seedlings recover", recall(self.root, "Continue the greenhouse investigation"))

    def test_misses_and_unreadable_graphs_stay_quiet(self):
        (self.root / "partial.json").write_text("{")
        self.assertEqual(self.hook("UserPromptSubmit", prompt="Explain photosynthesis"), "")
        self.assertIn("h-5@2", self.hook("UserPromptSubmit", prompt="heat"))

    def test_rejected_source_marks_downstream_conclusions(self):
        graph = Graph.load(self.graph)
        graph.verify_source("src-7", "rejected", "new review")
        graph.save(self.graph)
        text = recall(self.root, "greenhouse.json#c-10")
        self.assertIn("[claim / needs_review]", text)
        self.assertNotIn("12%", text)
        self.assertEqual(recall(self.root, "greenhouse.json#src-7"), "")

    def test_session_checkpoint_survives_compaction_and_stays_scoped(self):
        checkpoint(self.root, Path(self.graph.name), "session-a", "q-11", False)
        self.assertEqual(self.hook("SessionStart", source="startup"), "")
        self.assertIn("NS_EGRAPH_SESSION=session-a", (self.root / "claude.env").read_text())
        for source in ("compact", "resume"):
            self.assertIn("Next unresolved item: greenhouse.json#q-11", self.hook("SessionStart", source=source))
        self.assertIn("greenhouse.json#q-11", self.hook("UserPromptSubmit", prompt="continue"))
        self.assertEqual(self.hook("SessionStart", session="session-b", source="resume"), "")
        self.assertEqual(self.hook("UserPromptSubmit", prompt="Explain photosynthesis"), "")
        checkpoint(self.root, Path(self.graph.name), "session-a", None, True)
        self.assertEqual(resume(self.root, "session-a"), "")

    def test_resume_picks_open_work_after_saved_item_is_answered(self):
        checkpoint(self.root, Path(self.graph.name), "session-a", "q-11", False)
        graph = Graph.load(self.graph)
        graph.set_state("q-11", "answered")
        graph.save(self.graph)
        self.assertIn("greenhouse.json#w-12", resume(self.root, "session-a"))
