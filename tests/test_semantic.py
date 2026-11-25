import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from egraph import Graph
from recall import build_index, retrieve


class FakeModel:
    indexed = []

    def passage_embed(self, texts, **_):
        for text in texts:
            self.indexed.append(text)
            yield [1, 0] if "East soil was drier" in text else [0, 1]

    def query_embed(self, query):
        yield [1, 0] if "drooping" in query.lower() else [-1, 0]


class TestSemanticRecall(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "greenhouse.json"
        shutil.copyfile(Path(__file__).resolve().parents[1] / "examples" / self.path.name, self.path)
        self.model = FakeModel()
        self.model.indexed = []
        self.loader = self.enter_patch("semantic_recall.load_model", return_value=self.model)
        self.enter_patch("semantic_recall.backend_id", return_value="fake:1")

    def enter_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_fallback_uses_existing_graph_walk_and_suppresses_misses(self):
        build_index(self.root, download=True)
        method, text = retrieve(self.root, "Why were the plants drooping?", semantic=True)
        self.assertEqual(method, "semantic")
        self.assertIn("obs-8@1", text)
        self.assertIn("East 12%, west 26%", text)
        self.assertEqual(retrieve(self.root, "How does photosynthesis turn sunlight into sugar?", semantic=True),
                         ("", ""))
        self.assertEqual(retrieve(self.root, "Why were the plants drooping?"), ("", ""))
        self.loader.assert_any_call(self.root, download=True)
        self.loader.assert_any_call(self.root)

    def test_lexical_and_qualified_misses_skip_model(self):
        build_index(self.root)
        self.loader.reset_mock()
        self.assertEqual(retrieve(self.root, "heat", semantic=True)[0], "lexical")
        self.assertEqual(retrieve(self.root, "greenhouse.json#obs-8@999 drooping plants", semantic=True), ("", ""))
        self.loader.assert_not_called()

    def test_revised_vectors_are_skipped_until_index_is_refreshed(self):
        build_index(self.root)
        count = len(self.model.indexed)
        graph = Graph.load(self.path)
        graph.revise("obs-8", {"evidence": "East 14%, west 26%"})
        graph.save(self.path)
        self.assertEqual(retrieve(self.root, "Why were the plants drooping?", semantic=True), ("", ""))
        build_index(self.root)
        # Revised evidence also moves the accepted goal to needs_review.
        self.assertEqual(len(self.model.indexed), count + 2)
        self.assertIn("obs-8@2", retrieve(self.root, "Why were the plants drooping?", semantic=True)[1])

    def test_missing_index_and_model_fail_quietly(self):
        self.assertEqual(retrieve(self.root, "Why were the plants drooping?", semantic=True), ("", ""))
        self.loader.assert_not_called()
        build_index(self.root)
        self.loader.side_effect = RuntimeError("unavailable")
        self.assertEqual(retrieve(self.root, "Why were the plants drooping?", semantic=True), ("", ""))
