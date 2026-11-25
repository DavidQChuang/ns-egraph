import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrations.hooks import auto_checkpoint, read_checkpoint  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
