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

    def test_extracts_milestone(self) -> None:
        cands = extractor.extract_candidates(
            "Phase 2 finished: auth refactor merged and all tests pass.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "milestone")

    def test_extracts_project_change(self) -> None:
        cands = extractor.extract_candidates(
            "Deployed v1.2 of the API endpoint to production with migrations.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "project-change")

    def test_ignores_everyday_chatter_with_action_verbs(self) -> None:
        self.assertEqual(extractor.extract_candidates(
            "Added sugar to my coffee this morning before the daily standup."), [])
        self.assertEqual(extractor.extract_candidates(
            "I updated my status on the project board this morning before standup."), [])

    def test_project_build_needs_project_context(self) -> None:
        cands = extractor.extract_candidates(
            "Added OAuth login to the API endpoint with a database migration.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "project-build")

    def test_ship_verb_needs_no_context(self) -> None:
        cands = extractor.extract_candidates(
            "Deployed v1.2 to production last night with no downtime at all.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "project-change")

    def test_generic_task_statement_is_not_a_milestone(self) -> None:
        self.assertEqual(extractor.extract_candidates(
            "Each task takes a long time to complete when the queue is full."), [])

    def test_merged_needs_project_context(self) -> None:
        self.assertEqual(extractor.extract_candidates(
            "I merged the leftover documents into one file last night."), [])
        cands = extractor.extract_candidates(
            "Merged branch feature into main for the upcoming release cycle.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "project-build")
        cands = extractor.extract_candidates(
            "Merged pull request 42 with all checks passing last night.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "project-build")

    def test_extracts_project_stack(self) -> None:
        cands = extractor.extract_candidates(
            "Our stack is Python 3.14, SQLite FTS5 and PowerShell.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "project-stack")
        self.assertGreaterEqual(cands[0]["importance"], 4)

    def test_extracts_repo_uses_lowercase_stack(self) -> None:
        cands = extractor.extract_candidates(
            "The repo uses python and sqlite for storage.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "project-stack")

    def test_extracts_dev_docs_reference(self) -> None:
        cands = extractor.extract_candidates(
            "The development docs live in docs/architecture.md.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "project-docs")
        self.assertGreaterEqual(cands[0]["importance"], 4)

    def test_plan_without_file_is_not_captured(self) -> None:
        self.assertEqual(extractor.extract_candidates(
            "The plan is to ship the release in June."), [])

    def test_stack_verb_is_not_captured(self) -> None:
        self.assertEqual(extractor.extract_candidates(
            "We stacked the boxes in the warehouse this morning."), [])

    def test_tool_failure_boosts_importance(self) -> None:
        cands = extractor.extract_candidates(
            "The deploy failed because the migration was missing.",
            source="tool-failure")
        self.assertTrue(cands)
        self.assertGreaterEqual(cands[0]["importance"], 3)


if __name__ == "__main__":
    unittest.main()
