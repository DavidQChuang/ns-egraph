import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from egraph import Graph
from recall import MAX_CHARS, checkpoint, matches, recall, resume

ROOT = Path(__file__).resolve().parents[1]


class TestRecall(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "graphs"
        self.root.mkdir()
        self.graph = self.root / "greenhouse.json"
        shutil.copyfile(ROOT / "examples" / self.graph.name, self.graph)

    def hook(self, name, session="session-a", **fields):
        event = dict(hook_event_name=name, session_id=session, **fields)
        result = subprocess.run(
            [sys.executable, "-B", ROOT / "recall.py", "claude-hook", self.root],
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
        for prompt in ("Continue the greenhouse investigation", "Inspect greenhouse.json.", "Please resume greenhouse"):
            with self.subTest(prompt=prompt):
                self.assertIn("Do seedlings recover", recall(self.root, prompt))

    def test_incidental_graph_name_does_not_recall_open_work(self):
        for prompt in ("What angle should I use for a greenhouse roof in a snowy climate?",
                       "Continue planning the greenhouse roof"):
            with self.subTest(prompt=prompt):
                self.assertEqual(recall(self.root, prompt), "")

    def test_two_hop_citation_takes_precedence_over_planning_link(self):
        for chosen, other in ((0, 1), (1, 0)):
            with self.subTest(chosen=chosen):
                graph = Graph.new("Dispatch", "Where does the tray go?")
                question = graph.add("question", "Where is dispatch?")
                sources = [graph.add("source", "Dispatch sheet") for _ in range(2)]
                obs = graph.add("observation", "Tray assigned", {"evidence": "The amber tray goes to Dock C."},
                                cites=[sources[chosen]])
                graph.link("answers", obs, question)
                task = graph.add("work", "Prepare tomorrow's tray", derives_from=[question])
                graph.save(self.graph)

                text = recall(self.root, f"{self.graph.name}#{question}")
                self.assertIn(f"{question} ← answers ← {self.graph.name}#{obs}@1", text)
                self.assertIn(f"{obs} → cites → {self.graph.name}#{sources[chosen]}@1", text)
                self.assertNotIn(f"{self.graph.name}#{sources[other]}@", text)
                self.assertNotIn(f"{self.graph.name}#{task}@", text)

    def test_direct_counterevidence_keeps_the_third_slot(self):
        graph = Graph.new("Dispatch", "Which dock?")
        hypothesis = graph.add("hypothesis", "The tray goes to Dock C")
        source = graph.add("source", "Dispatch sheet")
        support = graph.add("observation", "Tray label", {"evidence": "The label says Dock C."}, cites=[source])
        graph.link("supports", support, hypothesis)
        other = graph.add("observation", "Dispatch log entry", {"evidence": "The log says Dock C."})
        graph.link("supports", other, hypothesis)
        contradiction = graph.add("observation", "Dock closure", {"evidence": "Dock C is closed today."})
        graph.link("refutes", contradiction, hypothesis)
        graph.save(self.graph)

        text = recall(self.root, f"{self.graph.name}#{hypothesis}")
        self.assertIn(f"{hypothesis} ← supports ← {self.graph.name}#{support}@1", text)
        self.assertIn(f"{hypothesis} ← refutes ← {self.graph.name}#{contradiction}@1", text)
        self.assertNotIn(f"{self.graph.name}#{source}@", text)

    def test_long_records_can_use_the_larger_budget(self):
        graph = Graph.new("Dispatch", "Where is dispatch?")
        question = graph.add("question", "Where does the tray go? " + "A" * 2200)
        source = graph.add("source", "Dispatch sheet " + "B" * 2200)
        obs = graph.add("observation", "Tray assigned", {"evidence": "C" * 2200}, cites=[source])
        graph.link("answers", obs, question)
        graph.save(self.graph)

        text = recall(self.root, f"{self.graph.name}#{question}")
        self.assertEqual(len(text), 5000)
        self.assertEqual(len(text.splitlines()), 4)
        self.assertIn(f"{obs} → cites → {self.graph.name}#{source}@1", text)

    def test_misses_and_unreadable_graphs_stay_quiet(self):
        (self.root.parent / "config.json").write_text(json.dumps({"recall_mode": "excerpt"}), encoding="utf-8")
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

    def ranked(self, prompt):
        return [nid for *_, nid in matches([(self.graph, Graph.load(self.graph))], prompt)]

    def test_open_work_outranks_done_node_with_equal_overlap(self):
        graph = Graph.new("Field notes", "Recorded request.")
        done = graph.add("work", "Cryostat drift log")
        graph.set_state(done, "done")
        open_work = graph.add("work", "Cryostat drift check")
        graph.save(self.graph)
        self.assertLess(done, open_work)  # the done node has the lower id, so id order alone would pick it

        self.assertEqual(self.ranked("Is the cryostat drift settling?"), [open_work, done])

    def test_common_words_do_not_count_toward_overlap(self):
        graph = Graph.new("Field notes", "Recorded request.")
        graph.add("work", "Pulsar sedo beam tilt")
        graph.add("work", "Pulsar sedo gain check")
        graph.add("work", "Sedo pulsar cooling")
        cryostat = graph.add("work", "Thermal drift in cryostat")
        graph.save(self.graph)

        # Lower the minimum so that "pulsar" and "sedo", in 3 of 5 active titles, count as common.
        with mock.patch("recall.MIN_COMMON", 2):
            self.assertEqual(self.ranked("Did the pulsar sedo drift?"), [])
            # One common word plus one distinctive word is still below the two-word threshold.
            self.assertEqual(self.ranked("Plan the pulsar cooling"), [])
            self.assertEqual(self.ranked("Is the cryostat thermal drift stable?"), [cryostat])

    def test_small_store_keeps_shared_words(self):
        # With the default minimum of 12, a 5-node store keeps "pulsar" and "sedo" as matchable.
        graph = Graph.new("Field notes", "Recorded request.")
        beam = graph.add("work", "Pulsar sedo beam tilt")
        gain = graph.add("work", "Pulsar sedo gain check")
        cooling = graph.add("work", "Sedo pulsar cooling")
        graph.add("work", "Thermal drift in cryostat")
        graph.save(self.graph)

        self.assertCountEqual(self.ranked("Did the pulsar sedo drift?"), [beam, gain, cooling])
