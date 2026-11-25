import unittest

from evals.run import grade


class TestEvalGrading(unittest.TestCase):
    def test_requires_evidence_and_provenance(self):
        case = {"required": ["dispatch.json#obs-5@1", "Dock C"], "forbidden": ["gallery.json#"]}
        self.assertEqual(grade(case, "dispatch.json#obs-5@1 Dock C"), [])
        self.assertTrue(grade(case, "Dock C"))
        self.assertTrue(grade(case, "dispatch.json#obs-5@1 Dock C\ngallery.json#c-6@1"))
        self.assertTrue(grade(case, ""))

    def test_distinguishes_silence_from_a_no_match_message(self):
        self.assertEqual(grade({"silent": True}, ""), [])
        self.assertTrue(grade({"silent": True}, "No matches"))

    def test_checks_the_context_budget_and_hook_boundary(self):
        case = {"host": "kiro", "required": ["Dock C"]}
        self.assertTrue(grade(case, "Dock C"))
        self.assertTrue(grade(case, "<ns_egraph_recall>\nDock C" + "x" * 5000 + "</ns_egraph_recall>"))
        self.assertEqual(grade(case, "<ns_egraph_recall>\nDock C\n</ns_egraph_recall>"), [])
