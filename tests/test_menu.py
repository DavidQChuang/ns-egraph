import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from egraph import Graph
from integrations.hooks import prompt_context, recall_mode
from integrations.kiro.hook import context
from recall import (MENU_HEADER, MENU_SIZE, checkpoint, load_graphs, load_questions, menu_candidates, menu_text,
                    questions_path, resume)

RECALL = Path(__file__).resolve().parents[1] / "recall.py"


class MenuTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="egraph menu ")
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)
        self.directory = self.project / ".ns-egraph" / "graphs"
        self.directory.mkdir(parents=True)
        self.sessions = self.project / ".ns-egraph" / "sessions"

    def save(self, name, titles, origin="Why did the tray wilt?"):
        """Save one graph with a hypothesis per title. Returns the node ids in title order."""
        graph = Graph.new(name, origin)
        nids = [graph.add("hypothesis", title) for title in titles]
        graph.save(self.directory / name)
        return nids

    def load(self):
        return list(load_graphs(self.directory))

    def session_data(self, session):
        return json.loads((self.sessions / f"{session}.json").read_text(encoding="utf-8"))

    def merge(self, payload):
        """Run `recall.py questions` on a merge file holding payload. Returns the completed process."""
        source = self.project / "merge.json"
        source.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run([sys.executable, str(RECALL), "questions", str(self.directory), str(source)],
                              cwd=RECALL.parent, capture_output=True, text=True)

    def two_graphs(self):
        """Graph A has gearbox titles, graph B has thermal titles. Returns (A ids, B ids)."""
        a = self.save("a.json", ["Gearbox oil temperature", "Gearbox seal leak"])
        b = self.save("b.json", ["Thermal sensor drift", "Thermal sensor calibration"])
        return a, b

    def test_origin_statement_is_not_a_candidate(self):
        graph = Graph.new("Tray", "Why did the tray wilt?")
        graph.add("hypothesis", "Pump pressure drop")
        observation = graph.add("observation", "Tray wilt seen", {"quote": "the tray wilted"})
        graph.retract(observation, "recorded in error")
        graph.save(self.directory / "tray.json")
        self.assertEqual(menu_candidates(self.load(), "tray wilt"), [])

    def test_gate_needs_two_rare_words_or_one_number(self):
        pump = self.save("a.json", ["Pump pressure drop", "Sensor calibration batch"])[0]
        graphs = self.load()
        self.assertEqual(menu_candidates(graphs, "pressure reading"), [])
        candidates = menu_candidates(graphs, "pressure drop again")
        self.assertEqual([(path.name, nid) for _, path, nid in candidates], [("a.json", pump)])

        graph = Graph.new("Relay", "Why did the relay chatter?")
        valve = graph.add("hypothesis", "Valve 4417 seated")
        graph.add("observation", "Relay check", {"evidence": "Relay chatter 9921 logged"})
        graph.save(self.directory / "relay.json")
        candidates = menu_candidates(self.load(), "check 4417")
        self.assertEqual([(path.name, nid) for _, path, nid in candidates], [("relay.json", valve)])
        self.assertEqual(menu_candidates(self.load(), "9921")[0][1].name, "relay.json")

    def test_word_in_more_than_ten_percent_of_nodes_is_not_rare(self):
        titles = [f"record sample number{i:02d}" for i in range(30)]
        for i in range(6):
            titles[i] += " gradient"
        titles[10] += " wilting"
        titles[20] += " wilting tray"
        nids = self.save("store.json", titles)
        candidates = menu_candidates(self.load(), "gradient wilting tray")
        self.assertEqual([(path.name, nid) for _, path, nid in candidates], [("store.json", nids[20])])

    def test_at_most_five_candidates_ordered_by_score_file_and_node(self):
        first = self.save("a.json", ["Reading 1003", "Reading 1004", "Reading 1005", "Reading 1006"])
        second = self.save("b.json", ["Reading 1001", "Reading 1002", "Reading 1007"])
        candidates = menu_candidates(self.load(), "1001 1002 1003 1004 1005 1006 1007")
        self.assertEqual(len(candidates), MENU_SIZE)
        self.assertEqual([(path.name, nid) for _, path, nid in candidates],
                         [("a.json", first[0]), ("a.json", first[1]), ("a.json", first[2]),
                          ("a.json", first[3]), ("b.json", second[0])])
        scores = [score for score, _, _ in candidates]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_session_binding_keeps_later_calls_in_the_bound_graph(self):
        a, b = self.two_graphs()
        first = menu_text(self.directory, "gearbox leak", session="s1")
        self.assertEqual(first.splitlines()[1], f"1. a.json#{a[1]}: Gearbox seal leak")
        self.assertEqual(self.session_data("s1")["bindings"], {"a.json": 1})

        # Graph B matches this prompt best, but the session is bound to graph A.
        better_for_b = "gearbox leak thermal sensor drift"
        self.assertEqual(menu_candidates(self.load(), better_for_b)[0][1].name, "b.json")
        scoped = menu_text(self.directory, better_for_b, session="s1")
        self.assertEqual(scoped.splitlines()[1:], [f"1. a.json#{a[1]}: Gearbox seal leak"])
        self.assertEqual(self.session_data("s1")["bindings"], {"a.json": 2})

        # A prompt with no shared words in graph A gives nothing and does not rebind the session.
        self.assertEqual(menu_text(self.directory, "thermal sensor drift", session="s1"), "")
        self.assertEqual(self.session_data("s1")["bindings"], {"a.json": 2})

    def test_session_file_keeps_other_keys(self):
        self.two_graphs()
        self.sessions.mkdir(parents=True)
        (self.sessions / "s1.json").write_text(json.dumps({"injected": ["a.json#h-1@1"], "started": 5.0}))
        self.assertIn("Gearbox seal leak", menu_text(self.directory, "gearbox leak", session="s1"))
        data = self.session_data("s1")
        self.assertEqual(data["injected"], ["a.json#h-1@1"])
        self.assertEqual(data["started"], 5.0)
        self.assertEqual(data["bindings"], {"a.json": 1})

    def test_no_session_writes_no_session_file(self):
        self.two_graphs()
        self.assertIn("Gearbox seal leak", menu_text(self.directory, "gearbox leak"))
        self.assertFalse(self.sessions.exists())

    def test_graph_name_restricts_candidates(self):
        self.two_graphs()
        self.save("c.json", ["Thermal sensor calibration drift"])
        query = "thermal sensor drift calibration"
        unscoped = [path.name for _, path, _ in menu_candidates(self.load(), query)]
        self.assertIn("c.json", unscoped)
        text = menu_text(self.directory, query, graph_name="b.json")
        lines = text.splitlines()[1:]
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(re.match(r"^\d+\. b\.json#", line) for line in lines))
        with self.assertRaises(ValueError):
            menu_text(self.directory, query, graph_name="missing.json")

    def test_output_lines_and_title_cut(self):
        self.save("a.json", ["Gearbox leak check " + "filler " * 60, "Gearbox leak short"])
        lines = menu_text(self.directory, "gearbox leak").splitlines()
        self.assertEqual(lines[0], MENU_HEADER)
        pattern = re.compile(r"^\d+\. [\w.-]+\.json#[\w-]+: ")
        for line in lines[1:]:
            self.assertRegex(line, pattern)
        long_title = lines[1].split(": ", 1)[1]
        self.assertEqual(len(long_title), 140)
        self.assertTrue(long_title.endswith("..."))
        self.assertEqual(lines[2].split(": ", 1)[1], "Gearbox leak short")

    def test_menu_is_the_default_mode_and_excerpt_needs_config(self):
        leak = self.save("a.json", ["Gearbox leak"])[0]
        self.assertEqual(recall_mode(self.directory), "menu")
        menu = prompt_context(self.directory, "gearbox leak", "s1")
        self.assertEqual(menu.method, "menu")
        self.assertIn("<ns_egraph_recall>", menu)
        self.assertIn(f"1. a.json#{leak}: Gearbox leak", menu)

        config = self.project / ".ns-egraph" / "config.json"
        config.write_text(json.dumps({"recall_mode": "excerpt"}))
        self.assertEqual(recall_mode(self.directory), "excerpt")
        excerpt = prompt_context(self.directory, "gearbox leak", "s2")
        self.assertEqual(excerpt.method, "lexical")
        self.assertIn(f"a.json#{leak}@1", excerpt)

        for value in ("bogus", None):
            config.write_text(json.dumps({"recall_mode": value}))
            self.assertEqual(recall_mode(self.directory), "menu")
        config.write_text("not json")
        self.assertEqual(recall_mode(self.directory), "menu")

    def test_shared_numeric_id_is_not_rare_but_a_job_id_is(self):
        titles = [f"Shift report 2026 line {i}" for i in range(6)]
        titles += [f"Shift report line {i}" for i in range(23)]
        titles.append("Job 4471 bearing check")
        nids = self.save("store.json", titles)
        self.assertEqual(len(nids), 30)
        graphs = self.load()
        # 2026 is in 6 nodes, above the limit of max(3, 0.10 * 30) = 3, so it does not match.
        self.assertEqual(menu_candidates(graphs, "shift report 2026"), [])
        candidates = menu_candidates(graphs, "job 4471")
        self.assertEqual([(path.name, nid) for _, path, nid in candidates], [("store.json", nids[29])])

    def test_question_sidecar_makes_a_node_a_candidate(self):
        target = self.save("a.json", ["Coolant loop check", "Pump pressure drop"])[0]
        query = "turbine bearing noise"
        self.assertEqual(menu_candidates(self.load(), query), [])
        self.assertEqual(menu_text(self.directory, query), "")

        result = self.merge({f"a.json#{target}": ["Turbine bearing wear under load?"]})
        self.assertEqual((result.returncode, result.stdout.strip()),
                         (0, f"1 entries written to {questions_path(self.directory)}"))
        candidates = menu_candidates(self.load(), query, questions=load_questions(self.directory))
        self.assertEqual([(path.name, nid) for _, path, nid in candidates], [("a.json", target)])
        lines = menu_text(self.directory, query).splitlines()
        self.assertEqual(lines[1:], [f"1. a.json#{target}: Coolant loop check"])

    def test_invalid_merge_exits_1_and_writes_nothing(self):
        nids = self.save("a.json", ["Gearbox leak"])
        graph = Graph.load(self.directory / "a.json")
        origin = next(nid for nid in graph.d["nodes"] if graph.kind(nid) == "origin")
        sidecar = questions_path(self.directory)
        self.assertFalse(sidecar.exists())

        result = self.merge({"a.json#missing-node": ["Why?"], f"a.json#{origin}": ["Why?"]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("a.json#missing-node: no node missing-node in a.json", result.stderr)
        self.assertIn(f"a.json#{origin}: origin nodes cannot take questions", result.stderr)
        self.assertFalse(sidecar.exists())

        self.assertEqual(self.merge({f"a.json#{nids[0]}": ["Why?"]}).returncode, 0)
        before = sidecar.read_bytes()
        self.assertEqual(self.merge({"a.json#missing-node": ["Why?"]}).returncode, 1)
        self.assertEqual(sidecar.read_bytes(), before)

    def test_valid_merge_replaces_same_key_and_keeps_other_keys(self):
        nids = self.save("a.json", ["Gearbox leak", "Pump pressure drop"])
        first_key, second_key = f"a.json#{nids[0]}", f"a.json#{nids[1]}"
        self.assertEqual(self.merge({first_key: ["old question"], second_key: ["keep me"]}).returncode, 0)
        result = self.merge({first_key: ["new question"]})
        self.assertEqual((result.returncode, result.stdout.strip().split(" ")[0]), (0, "1"))
        data = json.loads(questions_path(self.directory).read_text(encoding="utf-8"))
        self.assertEqual(data, {first_key: ["new question"], second_key: ["keep me"]})

    def test_sidecar_is_not_loaded_as_a_graph(self):
        a, _ = self.two_graphs()
        before = len(self.load())
        self.assertEqual(self.merge({f"a.json#{a[0]}": ["Why did the gearbox leak?"]}).returncode, 0)
        self.assertTrue(questions_path(self.directory).is_file())
        self.assertEqual(questions_path(self.directory).parent, self.directory.parent)
        self.assertEqual(len(self.load()), before)


    def reading_graph(self, count=8):
        """Save count hypotheses with one numeric id each. Returns (node ids, query naming every id)."""
        numbers = range(1001, 1001 + count)
        nids = self.save("a.json", [f"Reading {n}" for n in numbers])
        return nids, " ".join(str(n) for n in numbers)

    def hook(self, event, session="s1", **fields):
        """Run one kiro hook event for a session and return its stdout text."""
        return context(self.directory, {"hook_event_name": event, "session_id": session, **fields})

    def test_excluded_keys_are_skipped_before_the_size_limit(self):
        nids, query = self.reading_graph()
        excluded = {f"a.json#{nids[0]}", f"a.json#{nids[1]}"}
        candidates = menu_candidates(self.load(), query, exclude=excluded)
        keys = [f"{path.name}#{nid}" for _, path, nid in candidates]
        self.assertEqual(len(keys), MENU_SIZE)
        self.assertEqual(set(keys) & excluded, set())
        text = menu_text(self.directory, query, exclude=excluded)
        self.assertEqual(len(text.splitlines()), MENU_SIZE + 1)
        self.assertEqual(set(re.findall(r"[\w.-]+\.json#[\w-]+", text)), set(keys))

    def test_second_prompt_in_a_session_does_not_repeat_shown_keys(self):
        nids, query = self.reading_graph()
        first = set(re.findall(r"[\w.-]+\.json#[\w-]+", self.hook("UserPromptSubmit", prompt=query)))
        second = set(re.findall(r"[\w.-]+\.json#[\w-]+", self.hook("UserPromptSubmit", prompt=query + " again")))
        self.assertEqual(len(first), MENU_SIZE)
        self.assertEqual(second, {f"a.json#{nid}" for nid in nids} - first)
        self.assertEqual(len(second), 3)
        shown = self.session_data("s1")["shown"]
        self.assertEqual(len(shown), 8)
        self.assertEqual(set(shown), first | second)

    def test_session_start_clears_shown_keys(self):
        _, query = self.reading_graph()
        first = set(re.findall(r"[\w.-]+\.json#[\w-]+", self.hook("UserPromptSubmit", prompt=query)))
        self.hook("SessionStart")
        data = self.session_data("s1")
        self.assertEqual(data["shown"], [])
        self.assertIn("started", data)
        again = set(re.findall(r"[\w.-]+\.json#[\w-]+", self.hook("UserPromptSubmit", prompt=query)))
        self.assertEqual(again, first)

    def test_no_session_id_means_no_dedupe_and_no_session_file(self):
        _, query = self.reading_graph()
        first = prompt_context(self.directory, query, "")
        second = prompt_context(self.directory, query, "")
        keys = re.compile(r"[\w.-]+\.json#[\w-]+")
        self.assertEqual(len(keys.findall(first)), MENU_SIZE)
        self.assertEqual(keys.findall(first), keys.findall(second))
        self.assertFalse(self.sessions.exists())

    def test_resume_lists_three_other_open_items(self):
        graph = Graph.new("Pump", "Why did the pump stall?")
        nids = [graph.add("hypothesis", f"Open pump check {i}") for i in range(5)]
        graph.save(self.directory / "pump.json")
        checkpoint(self.directory, Path("pump.json"), "s1", nids[0], False)
        lines = resume(self.directory, "s1").splitlines()
        self.assertEqual(lines[2], f"Next unresolved item: pump.json#{nids[0]}@1 "
                                   f"[hypothesis / open] Open pump check 0")
        open_lines = [line for line in lines if line.startswith("Open items: ")]
        self.assertEqual(len(open_lines), 1)
        entries = open_lines[0].removeprefix("Open items: ").split("; ")
        self.assertEqual(len(entries), 3)
        titles = {nid: f"Open pump check {i}" for i, nid in enumerate(nids)}
        for entry in entries:
            nid, title = entry.split(" ", 1)
            self.assertIn(nid, titles)
            self.assertNotEqual(nid, nids[0])
            self.assertEqual(title, titles[nid])

    def test_resume_adds_no_open_items_line_when_next_is_the_only_one(self):
        graph = Graph.new("Solo", "Why did the solo unit stop?")
        only = graph.add("hypothesis", "Solo check")
        graph.save(self.directory / "solo.json")
        checkpoint(self.directory, Path("solo.json"), "s2", only, False)
        text = resume(self.directory, "s2")
        self.assertIn(f"Next unresolved item: solo.json#{only}@1", text)
        self.assertNotIn("Open items:", text)

if __name__ == "__main__":
    unittest.main()
