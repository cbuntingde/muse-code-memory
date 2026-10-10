import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muse_memory import extractor  # noqa: E402


class ExtractorTest(unittest.TestCase):
    def test_extracts_decision(self) -> None:
        cands = extractor.extract_candidates(
            "We decided to use Postgres for the session store going forward.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "decision")
        self.assertGreaterEqual(cands[0]["importance"], 4)

    def test_extracts_rule(self) -> None:
        cands = extractor.extract_candidates(
            "You must never commit secrets to the repository.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "rule")

    def test_extracts_fix(self) -> None:
        cands = extractor.extract_candidates(
            "Fixed the crash: the root cause was a missing await on startup.")
        self.assertTrue(cands)
        self.assertIn(cands[0]["kind"], ("fix", "gotcha"))

    def test_skips_chatter(self) -> None:
        self.assertEqual(extractor.extract_candidates("Thanks, let me check that."), [])
        self.assertEqual(extractor.extract_candidates("Hello world test test"), [])
        self.assertEqual(extractor.extract_candidates("short"), [])

    def test_global_scope_hint(self) -> None:
        cands = extractor.extract_candidates(
            "Remember that I prefer tabs in my editor across all projects.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["scope"], "global")

    def test_project_scope_hint(self) -> None:
        cands = extractor.extract_candidates(
            "Remember the repo requires a migration before the test suite runs.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["scope"], "project-personal")

    def test_tool_failure_boosts_importance(self) -> None:
        cands = extractor.extract_candidates(
            "The deploy failed because the migration was missing.",
            source="tool-failure")
        self.assertTrue(cands)
        self.assertGreaterEqual(cands[0]["importance"], 3)


if __name__ == "__main__":
    unittest.main()
