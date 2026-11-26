import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrations.hooks import auto_checkpoint, read_checkpoint, write_state  # noqa: E402
from recall import checkpoint  # noqa: E402


def egraph(path: Path, *args: str) -> dict:
    out = subprocess.run([sys.executable, str(ROOT / "egraph.py"), str(path), *args],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


class AutoCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "graphs"
        self.root.mkdir()
        self.graph = self.root / "demo.json"
        egraph(self.graph, "init", "--title", "Demo", "--origin", "Why?")
        egraph(self.graph, "add", "goal", "--title", "Ship the thing")  # goal-2
        self.session = "auto-test-session"

    def tearDown(self):
        self.tmp.cleanup()

    def test_changed_graph_gets_checkpoint_without_next(self):
        auto_checkpoint(self.root, self.session)
        data = read_checkpoint(self.root, self.session)
        self.assertEqual(data["graph"], "demo.json")
        self.assertIsNone(data["next"])
        self.assertFalse(data["done"])

    def test_done_checkpoint_is_not_revived(self):
        checkpoint(self.root, Path("demo.json"), self.session, None, True)
        egraph(self.graph, "add", "goal", "--title", "Another goal")
        auto_checkpoint(self.root, self.session)
        self.assertTrue(read_checkpoint(self.root, self.session)["done"])

    def test_explicit_next_for_same_graph_is_kept(self):
        checkpoint(self.root, Path("demo.json"), self.session, "goal-2", False)
        egraph(self.graph, "add", "goal", "--title", "Another goal")
        auto_checkpoint(self.root, self.session)
        self.assertEqual(read_checkpoint(self.root, self.session)["next"], "goal-2")


class BoundAutoCheckpointTests(unittest.TestCase):
    """Two graphs in the store, with B the most recently changed one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "graphs"
        self.root.mkdir()
        self.session = "bound-test-session"
        for name in ("A.json", "B.json"):
            egraph(self.root / name, "init", "--title", name, "--origin", "Why?")
            egraph(self.root / name, "add", "goal", "--title", "Ship the thing")
        os.utime(self.root / "A.json", (1_000_000_000, 1_000_000_000))
        os.utime(self.root / "B.json", (2_000_000_000, 2_000_000_000))

    def tearDown(self):
        self.tmp.cleanup()

    def bind(self, bindings: dict) -> None:
        write_state(self.root, self.session, {"bindings": bindings})

    def test_binding_overrides_most_recently_changed_graph(self):
        self.bind({"A.json": 2})
        auto_checkpoint(self.root, self.session)
        self.assertEqual(read_checkpoint(self.root, self.session)["graph"], "A.json")

    def test_without_bindings_most_recently_changed_graph_is_used(self):
        auto_checkpoint(self.root, self.session)
        self.assertEqual(read_checkpoint(self.root, self.session)["graph"], "B.json")

    def test_highest_count_wins_among_bindings(self):
        self.bind({"A.json": 1, "B.json": 3})
        auto_checkpoint(self.root, self.session)
        self.assertEqual(read_checkpoint(self.root, self.session)["graph"], "B.json")

    def test_done_checkpoint_for_bound_graph_is_not_revived(self):
        checkpoint(self.root, Path("A.json"), self.session, None, True)
        before = read_checkpoint(self.root, self.session)
        self.bind({"A.json": 2})
        egraph(self.root / "A.json", "add", "goal", "--title", "Another goal")
        auto_checkpoint(self.root, self.session)
        self.assertEqual(read_checkpoint(self.root, self.session), before)
        self.assertTrue(read_checkpoint(self.root, self.session)["done"])


if __name__ == "__main__":
    unittest.main()
