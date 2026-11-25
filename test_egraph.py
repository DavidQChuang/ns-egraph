import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from egraph import Graph, GraphError


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
        tool = Path(__file__).with_name("egraph.py").resolve()
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

    def test_cli_roundtrip(self):
        tool = str(Path(__file__).with_name("egraph.py"))
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


if __name__ == "__main__":
    unittest.main()
