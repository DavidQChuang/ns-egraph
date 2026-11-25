import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from egraph import Graph
from ingest import parse_markdown

ROOT = Path(__file__).resolve().parents[1]
INGEST = ROOT / "ingest.py"

FIXTURE = """# Pump run log

Intro paragraph that is not an item.

## Sensor check

- [ ] Confirm sensor calibration
- [x] Power cycle the rig

| Step | Result |
|------|--------|
| Read pressure | 3.2 bar |
| Read flow | 14 l/min |

## Seal inspection

- [ ] Photograph the seal

```
- [ ] not a work item
| fake | row |
## not a section
```
"""

CAP_FIXTURE = """# Handoff

## Notes

- {long}
- two
- [ ] follow up
- three
- four

## Empty

Nothing to convert here.
"""


def run_ingest(cwd, *args):
    return subprocess.run([sys.executable, str(INGEST), *args], capture_output=True, text=True, cwd=cwd)


class TestIngest(unittest.TestCase):
    def test_fixture_counts_and_validate(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "run.md").write_text(FIXTURE, encoding="utf-8")
            r = run_ingest(d, "run.md", "out.json")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            summary = json.loads(r.stdout)
            self.assertEqual(summary["counts"], {"sources": 1, "questions": 2, "work_open": 2, "work_done": 1,
                                                 "observation_work": 1, "observations": 2})
            self.assertEqual(summary["skipped"], {"empty_sections": 0, "empty_items": 0, "outside_section": 0,
                                                  "over_max_items": 0, "fenced_code_blocks": 1})
            self.assertFalse(summary["stopped_at_max_items"])
            self.assertTrue(summary["validate"]["ok"], summary)
            self.assertEqual(summary["validate"]["errors"], [])

            g = Graph.load(Path(d, "out.json"))
            self.assertTrue(g.validate()["ok"])
            src = [k for k, n in g.d["nodes"].items() if n["kind"] == "source"]
            self.assertEqual(len(src), 1)
            self.assertEqual(g.cur(src[0])["title"], "Pump run log")
            self.assertEqual(g.cur(src[0])["payload"]["resource"], "run.md")
            obs = sorted(g.cur(k)["payload"]["quote"] for k, n in g.d["nodes"].items() if n["kind"] == "observation")
            self.assertEqual(obs, ["| Read flow | 14 l/min |", "| Read pressure | 3.2 bar |"])
            questions = sorted(g.cur(k)["title"] for k, n in g.d["nodes"].items() if n["kind"] == "question")
            self.assertEqual(questions, ["What about Seal inspection?", "What about Sensor check?"])
            done = [k for k, n in g.d["nodes"].items() if n["kind"] == "work" and g.state(k) == "done"]
            self.assertEqual(len(done), 2)  # "Power cycle" checkbox plus the observation work item for Sensor check

    def test_existing_output_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "run.md").write_text(FIXTURE, encoding="utf-8")
            Path(d, "out.json").write_text("keep me", encoding="utf-8")
            r = run_ingest(d, "run.md", "out.json")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("already exists", r.stderr)
            self.assertEqual(Path(d, "out.json").read_text(encoding="utf-8"), "keep me")

    def test_bullets_truncation_cap_and_empty_section(self):
        with tempfile.TemporaryDirectory() as d:
            long_text = "x" * 400
            Path(d, "handoff.md").write_text(CAP_FIXTURE.format(long=long_text), encoding="utf-8")
            r = run_ingest(d, "handoff.md", "out.json", "--max-items", "3")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            summary = json.loads(r.stdout)
            self.assertEqual(summary["counts"]["observations"], 3)
            self.assertEqual(summary["counts"]["work_open"], 1)
            self.assertEqual(summary["counts"]["questions"], 1)
            self.assertEqual(summary["skipped"]["over_max_items"], 1)
            self.assertEqual(summary["skipped"]["empty_sections"], 1)
            self.assertTrue(summary["stopped_at_max_items"])
            self.assertTrue(summary["validate"]["ok"])

            g = Graph.load(Path(d, "out.json"))
            quotes = [g.cur(k)["payload"]["quote"] for k, n in g.d["nodes"].items() if n["kind"] == "observation"]
            self.assertIn("x" * 300, quotes)
            self.assertNotIn("four", quotes)
            self.assertIn("two", quotes)

    def test_parse_title_falls_back_to_none_without_heading(self):
        parsed = parse_markdown("## Only a section\n\n- note one\n")
        self.assertIsNone(parsed["title"])
        self.assertEqual(parsed["events"], [(0, "observation", "note one")])


if __name__ == "__main__":
    unittest.main()
