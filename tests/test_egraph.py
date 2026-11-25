import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from egraph import Graph, GraphError

ROOT = Path(__file__).resolve().parents[1]


def build():
    g = Graph.new("t", "Why did the seedlings wilt?")
    goal = g.add("goal", "Identify what caused the seedlings to wilt")
    h = g.add("hypothesis", "The soil dried out", {"priority": 2, "refs": ["bed-03"]})
    src = g.add("source", "Greenhouse log", {"section": "Monday"})
    w = g.add("work", "Read Monday's log", derives_from=[h])
    g.set_state(w, "running")
    obs = g.add("observation", "Low soil moisture", {"quote": "Soil moisture was 12%"}, produced_by=w, cites=[src])
    g.set_state(w, "done")
    return g, goal, h, src, w, obs


class TestEgraph(unittest.TestCase):
    def test_build_and_validate(self):
        g, goal, h, src, w, obs = build()
        v = g.validate()
        self.assertTrue(v["ok"], v)
        self.assertTrue(any("not yet verified" in x for x in v["warnings"]))

    def test_observation_needs_evidence_and_claim_needs_support(self):
        g, *_ = build()
        with self.assertRaises(GraphError) as c:
            g.add("observation", "bare")
        self.assertEqual(c.exception.code, "invalid_argument")
        with self.assertRaises(GraphError):
            g.add("claim", "unsupported")

    def test_evidence_cycle_rejected(self):
        g, goal, h, src, w, obs = build()
        c1 = g.add("claim", "c1", supports=[obs])
        c2 = g.add("claim", "c2", supports=[c1])
        with self.assertRaises(GraphError) as c:
            g.link("supports", c2, c1)
        self.assertEqual(c.exception.code, "cycle_detected")

    def test_dependency_gating(self):
        g, *_ = build()
        a = g.add("work", "a")
        b = g.add("work", "b")
        g.link("depends_on", b, a)
        ids = [f["id"] for f in g.frontier(20)]
        self.assertIn(a, ids)
        self.assertNotIn(b, ids)
        with self.assertRaises(GraphError):
            g.set_state(b, "running")

    def test_stale_execution(self):
        g, goal, h, src, w, obs = build()
        with self.assertRaises(GraphError) as c:
            g.add("observation", "late", {"quote": "x"}, produced_by=w)
        self.assertEqual(c.exception.code, "stale_execution")

    def test_source_verification_requires_verdict(self):
        g, goal, h, src, *_ = build()
        with self.assertRaises(GraphError):
            g.set_state(src, "verified")
        g.verify_source(src, "verified", "citation-report.json")
        self.assertEqual(g.state(src), "verified")
        g2, _, _, s2, *_ = build()
        g2.verify_source(s2, "mismatch", "r.json")
        self.assertEqual(g2.state(s2), "rejected")

    def test_hypothesis_state_needs_edge(self):
        g, goal, h, src, w, obs = build()
        with self.assertRaises(GraphError):
            g.set_state(h, "supported")
        g.link("supports", obs, h)
        g.set_state(h, "supported")
        self.assertEqual(g.state(h), "supported")

    def test_accept_and_invalidate(self):
        g, goal, h, src, w, obs = build()
        c = g.add("claim", "Water the dry bed first", supports=[obs])
        eid = g.link("supports", c, goal)
        acc = g.accept(goal, [eid], "enough for a design choice")
        self.assertEqual(g.state(goal), "met")
        self.assertIn(obs, acc["evidence"])
        g.revise(obs, {"quote": "Soil moisture was 12% at noon"})
        self.assertEqual(g.state(goal), "needs_review")
        self.assertTrue(g.d["acceptances"][0]["stale"])

    def test_state_change_does_not_invalidate(self):
        g, goal, h, src, w, obs = build()
        c = g.add("claim", "c", supports=[obs])
        eid = g.link("supports", c, goal)
        g.accept(goal, [eid], "ok")
        g.verify_source(src, "verified", "r")
        self.assertEqual(g.state(goal), "met")

    def test_accept_rejects_reported_only(self):
        g = Graph.new("t", "o")
        goal = g.add("goal", "g")
        o = g.add("observation", "blog says", {"evidence": "blog", "epistemic": "reported"})
        eid = g.link("supports", o, goal)
        with self.assertRaises(GraphError) as c:
            g.accept(goal, [eid], "x")
        self.assertEqual(c.exception.code, "stale_evidence")

    def test_accept_rejects_retracted(self):
        g, goal, h, src, w, obs = build()
        eid = g.link("supports", obs, goal)
        g.retract(obs, "misread")
        with self.assertRaises(GraphError):
            g.accept(goal, [eid], "x")

    def test_frontier_order_deterministic(self):
        g, goal, h, *_ = build()
        q = g.add("question", "low prio", {"priority": 0})
        f = g.frontier()
        self.assertEqual(f[0]["id"], h)
        self.assertEqual(f[-1]["id"], q)
        self.assertEqual(f, g.frontier())

    def test_stop_rules(self):
        g = Graph.new("t", "o")
        g.add("goal", "g")
        for i in range(3):
            w = g.add("work", f"w{i}")
            g.set_state(w, "done")
        g.add("question", "still open")
        self.assertIn("stagnation", g.stop_check()["reason"])
        self.assertIn("work budget", g.stop_check(max_work=2)["reason"])
        g2 = Graph.new("t", "o")
        self.assertEqual(g2.stop_check(stagnation=0)["reason"], "frontier empty")

    def test_observation_can_answer_question(self):
        # regression 2026-10-28 trial: procedure said evidence -> question, tool only allowed claims
        g, goal, h, src, w, obs = build()
        q = g.add("question", "q")
        g.link("answers", obs, q)
        self.assertNotIn("unanswered", " ".join(r for f in g.frontier(20) if f["id"] == q for r in f["reasons"]))

    def test_render_mermaid(self):
        g, *_ = build()
        m = g.render()
        self.assertTrue(m.startswith("flowchart TD"))
        self.assertIn("Why did the seedlings wilt?<br/>Origin · recorded by user", m)
        self.assertIn("|yields|", m)
        self.assertNotIn("-1{", m)

    def test_render_wraps_and_escapes_origin(self):
        g = Graph.new("t", 'Which "option" should we pick if the timetable changes again?', actor="Sam")
        self.assertIn('Which #34;option#34; should we pick if the<br/>timetable changes again?<br/>Origin · recorded by Sam',
                      g.render())

    def test_validate_detects_tampering(self):
        g, goal, h, src, w, obs = build()
        g.d["nodes"][obs]["revisions"][-1]["payload"].pop("quote")
        g.d["edges"].append({"id": "eX", "type": "supports", "src": "nope", "dst": goal})
        v = g.validate()
        self.assertFalse(v["ok"])
        self.assertTrue(any("without inline evidence" in e for e in v["errors"]))
        self.assertTrue(any("dangling" in e for e in v["errors"]))

    def test_legacy_load_update(self):
        g, _, hypothesis, *_ = build()
        g.d["schema"] = "ai-researcher-egraph/1"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "legacy.json"
            g.save(path)
            loaded = Graph.load(path)
            self.assertEqual(loaded.frontier(), g.frontier())
            loaded.revise(hypothesis, title="Revised", actor="editor")
            loaded.save(path)
            self.assertEqual(Graph.load(path).d["schema"], "ai-researcher-egraph/1")

    def test_standalone_cli(self):
        tool = ROOT / "egraph.py"
        with tempfile.TemporaryDirectory() as d:
            def run(*args):
                return subprocess.run([sys.executable, tool, "g.json", "--actor", "sam", *args],
                                      capture_output=True, text=True, cwd=d, check=True)

            run("init", "--title", "Delay", "--origin", "Why late?")
            run("add", "question", "--title", "Which order?", "--refs", "order-42")
            run("add", "source", "--title", "Timetable")
            run("verify-source", "src-3", "rejected", "--report", "review.txt")
            g = Graph.load(Path(d) / "g.json")
            self.assertEqual(g.d["schema"], "ns-egraph/1")
            self.assertEqual(g.cur("q-2")["actor"], "sam")
            self.assertEqual(g.cur("q-2")["payload"]["refs"], ["order-42"])
            self.assertIn("informs order-42", g.frontier()[0]["reasons"])
            self.assertEqual(g.state("src-3"), "rejected")

    def test_alias_warning_when_term_is_too_broad(self):
        g = Graph.new("t", "o")
        g.add("hypothesis", "Clock source Pulsar", {"aliases": ["Pulsar"]})
        g.add("question", "Pulsar timing drift?")
        g.add("question", "Check the pulsar cable")
        g.add("question", "PULSAR gate setting")
        g.add("question", "Pulsar")
        g.add("question", "Pulsars are noisy")  # whole word only: not a match
        v = g.validate()
        self.assertTrue(v["ok"], v)
        self.assertIn("alias 'Pulsar' on h-2 matches 4 other nodes; use a more specific term", v["warnings"])

    def test_alias_warning_threshold_and_inactive_nodes(self):
        g = Graph.new("t", "o")
        g.add("hypothesis", "Clock source", {"aliases": ["Pulsar"]})
        g.add("question", "Pulsar timing")
        g.add("question", "Pulsar cable")
        g.add("question", "Pulsar gate")
        self.assertTrue(any("alias 'Pulsar'" in w for w in g.validate()["warnings"]))
        g.set_state("q-4", "parked")  # parked is still active
        self.assertTrue(any("matches 3 other nodes" in w for w in g.validate()["warnings"]))
        w = g.add("work", "Pulsar sweep")
        g.set_state(w, "stopped")  # stopped nodes do not count
        self.assertTrue(any("matches 3 other nodes" in x for x in g.validate()["warnings"]))
        g2 = Graph.new("t", "o")
        g2.add("hypothesis", "Clock source", {"aliases": ["Pulsar"]})
        g2.add("question", "Pulsar timing")
        g2.add("question", "Pulsar cable")
        w2 = g2.add("work", "Pulsar sweep")
        g2.set_state(w2, "stopped")
        self.assertFalse([x for x in g2.validate()["warnings"] if "alias" in x])

    def test_alias_on_ref_id_gives_no_warning(self):
        g = Graph.new("t", "o")
        g.add("hypothesis", "Pump fault", {"aliases": ["ec1549b8"]})
        for title in ("Check pump log", "Read vendor note", "Compare runs", "Inspect seal"):
            g.add("question", title, {"refs": ["ec1549b8"]})
        self.assertEqual(g.validate()["warnings"], [])

    def test_cli_roundtrip(self):
        tool = str(ROOT / "egraph.py")
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "g.json")
            run = lambda *a: subprocess.run([sys.executable, tool, p, *a], capture_output=True, text=True)  # noqa: E731
            r = run("init", "--title", "t", "--origin", "o")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            r = run("add", "question", "--title", "Why?")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            r = run("validate")
            self.assertEqual(r.returncode, 0, r.stdout)
            r = run("link", "supports", "q-2", "origin-1")
            self.assertEqual(r.returncode, 3)
            self.assertFalse(json.loads(r.stdout)["ok"])


class TestBriefAndRecord(unittest.TestCase):
    CONTRACT = ('Reply contract: write one JSON line per finding to your evidence file: '
                '{"kind":"observation","title":"...","quote":"...","about":"<hypothesis, claim or question id from '
                'this brief>","relation":"confirms|contradicts|adds","by":"<work id from this brief>"}. '
                'Give about, by, or both. The relation is required with about. Cite only ids shown above.')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "g.json"

    def tearDown(self):
        self.tmp.cleanup()

    def egraph(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "egraph.py"), str(self.path), "--actor", "sam", *args],
                              capture_output=True, text=True)

    def save(self, g):
        g.save(self.path)

    def test_brief_prints_node_quote_and_neighbours(self):
        g, goal, h, src, w, obs = build()
        self.save(g)
        r = self.egraph("brief", obs)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "obs-6 observation [active] Low soil moisture",
            "Soil moisture was 12%",
            "Neighbours:",
            "  cites -> src-4 source [unverified] Greenhouse log",
            "  yields <- w-5 work [done] Read Monday's log",
            self.CONTRACT,
        ])

    def test_brief_arrows_and_order(self):
        g, goal, h, src, w, obs = build()
        self.save(g)
        r = self.egraph("brief", h)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "h-3 hypothesis [open] The soil dried out",
            "Neighbours:",
            "  derives <- origin-1 origin [recorded] Origin",
            "  derives -> w-5 work [done] Read Monday's log",
            self.CONTRACT,
        ])

    def test_brief_caps_neighbours_at_twelve_and_respects_max_chars(self):
        g = Graph.new("t", "o")
        h = g.add("hypothesis", "H")
        for i in range(13):
            g.add("question", f"Q{i}", derives_from=[h])
        self.save(g)  # h-2 has 14 neighbours: origin-1 (in) and q-3..q-15 (out)
        r = self.egraph("brief", h)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("  derives "), 12)
        self.assertIn("...(2 more neighbours not shown)", r.stdout)
        self.assertNotIn("q-8 ", r.stdout)  # sorted by id: q-3 .. q-7 fill the 12 slots
        r = self.egraph("brief", h, "--max-chars", "600")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLessEqual(len(r.stdout.rstrip("\n")), 600)
        shown = r.stdout.count("  derives ")
        hidden = int(re.search(r"\.\.\.\((\d+) more neighbours not shown\)", r.stdout).group(1))
        self.assertEqual(shown + hidden, 14)
        self.assertTrue(r.stdout.rstrip("\n").endswith(self.CONTRACT))

    def test_brief_unknown_node_exits_1(self):
        g, *_ = build()
        self.save(g)
        r = self.egraph("brief", "nope-99")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown node nope-99", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_record_by_only_attaches_to_running_work(self):
        g, goal, h, src, w, obs = build()
        w2 = g.add("work", "Probe again", derives_from=[h])
        g.set_state(w2, "running")
        self.save(g)
        f = self.dir / "records.jsonl"
        f.write_text(json.dumps({"kind": "observation", "title": "Only work", "quote": "Probe saw nothing",
                                 "by": w2}) + "\n", encoding="utf-8")
        r = self.egraph("record", str(f))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["ids"], ["obs-8"])
        g2 = Graph.load(self.path)
        self.assertEqual(len(g2.edges("yields", src=w2, dst="obs-8")), 1)
        self.assertEqual(g2.edges("supports", src="obs-8"), [])

    def test_record_adds_observations_edges_and_actor(self):
        g, goal, h, src, w, obs = build()
        w2 = g.add("work", "Probe again", derives_from=[h])  # counter is 6, so w-7
        self.save(g)
        lines = [
            {"kind": "observation", "title": "Probe reads 9%", "quote": "Probe read 9% at noon",
             "about": h, "relation": "confirms", "by": w2},
            "",
            {"kind": "observation", "title": "Log disagrees", "quote": "Monday's log says 14%",
             "about": h, "relation": "contradicts"},
        ]
        f = self.dir / "records.jsonl"
        f.write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n", encoding="utf-8")
        r = self.egraph("record", str(f))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"ok": True, "recorded": 2, "ids": ["obs-8", "obs-9"]})
        g2 = Graph.load(self.path)
        self.assertEqual(sum(1 for n in g2.d["nodes"].values() if n["kind"] == "observation"), 3)
        self.assertEqual(len(g2.edges("supports", src="obs-8", dst=h)), 1)
        self.assertEqual(len(g2.edges("yields", src=w2, dst="obs-8")), 1)
        self.assertEqual(len(g2.edges("refutes", src="obs-9", dst=h)), 1)
        self.assertEqual(g2.cur("obs-8")["actor"], "sam")
        self.assertEqual(g2.cur("obs-8")["payload"], {"quote": "Probe read 9% at noon", "epistemic": "observed"})
        linked = [e for e in g2.d["events"] if e["cmd"] == "Link" and e.get("actor") == "sam"]
        self.assertEqual(len(linked), 3)  # supports, yields, refutes

    def test_record_unknown_about_writes_nothing(self):
        g, goal, h, *_ = build()
        self.save(g)
        before = self.path.read_bytes()
        f = self.dir / "records.jsonl"
        f.write_text(json.dumps({"kind": "observation", "title": "ok", "quote": "x", "about": h,
                                 "relation": "confirms"}) + "\n"
                     + json.dumps({"kind": "observation", "title": "bad", "quote": "y", "about": "nope-99",
                                   "relation": "confirms"}) + "\n", encoding="utf-8")
        r = self.egraph("record", str(f))
        self.assertEqual(r.returncode, 1)
        self.assertIn("line 2: not_found", r.stderr)
        self.assertNotIn("line 1", r.stderr)
        self.assertEqual(self.path.read_bytes(), before)

    def test_record_invalid_relation_or_missing_quote_writes_nothing(self):
        g, goal, h, *_ = build()
        self.save(g)
        before = self.path.read_bytes()
        f = self.dir / "records.jsonl"
        f.write_text(json.dumps({"kind": "observation", "title": "a", "quote": "x", "about": h,
                                 "relation": "supports"}) + "\n"
                     + json.dumps({"kind": "observation", "title": "b", "about": h,
                                   "relation": "confirms"}) + "\n", encoding="utf-8")
        r = self.egraph("record", str(f))
        self.assertEqual(r.returncode, 1)
        self.assertIn("line 1: invalid_argument: relation must be one of", r.stderr)
        self.assertIn("line 2: invalid_argument: observation needs a quote", r.stderr)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
