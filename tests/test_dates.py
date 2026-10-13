import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import IsolatedCase  # noqa: E402
from muse_memory import dates, rag  # noqa: E402
from muse_memory.__main__ import cmd_recall  # noqa: E402
from muse_memory.store import MemoryStore, ScopeStore  # noqa: E402

# Monday 2026-10-12 12:00 UTC.
NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc).timestamp()


def day(year: int, month: int, d: int) -> tuple[float, float]:
    start = datetime(year, month, d, tzinfo=timezone.utc).timestamp()
    return start, start + 86400


class DateParseTest(unittest.TestCase):
    def test_yesterday_today_day_before(self) -> None:
        self.assertEqual(dates.parse_range("what did we do yesterday", NOW),
                         day(2026, 10, 11))
        self.assertEqual(dates.parse_range("completed today", NOW),
                         day(2026, 10, 12))
        self.assertEqual(dates.parse_range("day before yesterday", NOW),
                         day(2026, 10, 10))

    def test_last_weekday(self) -> None:
        # Today itself is Monday: "last Monday" is a week back.
        self.assertEqual(dates.parse_range("last monday", NOW),
                         day(2026, 10, 5))
        self.assertEqual(dates.parse_range("last Friday's deploy", NOW),
                         day(2026, 10, 9))

    def test_weeks_and_months(self) -> None:
        self.assertEqual(
            dates.parse_range("last week", NOW),
            (datetime(2026, 10, 5, tzinfo=timezone.utc).timestamp(),
             datetime(2026, 10, 12, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(
            dates.parse_range("last month", NOW),
            (datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp(),
             datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()))

    def test_nth_weekday_of_month(self) -> None:
        self.assertEqual(dates.parse_range("first Monday of August", NOW),
                         day(2026, 8, 3))
        self.assertEqual(
            dates.parse_range("last monday of august 2026", NOW),
            day(2026, 8, 31))

    def test_month_day_default_year(self) -> None:
        self.assertEqual(dates.parse_range("completed Aug 1st", NOW),
                         day(2026, 8, 1))
        # A future month-day rolls back to the previous year.
        july = datetime(2026, 7, 1, 12, tzinfo=timezone.utc).timestamp()
        self.assertEqual(dates.parse_range("completed Aug 1st", july),
                         day(2025, 8, 1))

    def test_iso_forms(self) -> None:
        self.assertEqual(dates.parse_range("on 2026-10-01", NOW),
                         day(2026, 10, 1))
        self.assertEqual(
            dates.parse_range("in 2026-10", NOW),
            (datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp(),
             datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()))

    def test_last_n_days(self) -> None:
        self.assertEqual(dates.parse_range("over the last 7 days", NOW),
                         (NOW - 7 * 86400, NOW))

    def test_last_n_months_keeps_month_end(self) -> None:
        # Asked Oct 31, "last 1 month" starts Sep 30, not Sep 28.
        ref = datetime(2026, 10, 31, 12, tzinfo=timezone.utc).timestamp()
        start, end = dates.parse_range("in the last 1 month", ref)
        self.assertEqual(end, ref)
        self.assertEqual(
            start, datetime(2026, 9, 30, tzinfo=timezone.utc).timestamp())

    def test_impossible_day_falls_through(self) -> None:
        # Feb 30 is invalid but must not blind the parser to a later date.
        self.assertEqual(dates.parse_range("feb 30 or 2026-10-01", NOW),
                         day(2026, 10, 1))

    def test_no_date_returns_none(self) -> None:
        self.assertIsNone(dates.parse_range("deploy process", NOW))

    def test_bare_may_is_not_a_month(self) -> None:
        # "may" is usually a verb; only a year makes it a month reference.
        self.assertIsNone(dates.parse_range("how may we deploy", NOW))
        self.assertEqual(
            dates.parse_range("what shipped in may 2026", NOW),
            (datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp(),
             datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(dates.parse_range("deployed may 5", NOW),
                         (datetime(2026, 5, 5, tzinfo=timezone.utc).timestamp(),
                          datetime(2026, 5, 5, tzinfo=timezone.utc).timestamp() + 86400))

    def test_split_query_strips_date_and_scaffold(self) -> None:
        cleaned, rng = dates.split_query("what did we complete yesterday",
                                         NOW)
        self.assertEqual(cleaned, "complete")
        self.assertEqual(rng, day(2026, 10, 11))

    def test_split_query_without_date(self) -> None:
        cleaned, rng = dates.split_query("deploy process", NOW)
        self.assertEqual(cleaned, "deploy process")
        self.assertIsNone(rng)

    def test_parse_day(self) -> None:
        self.assertEqual(dates.parse_day("2026-10-11"), day(2026, 10, 11))
        self.assertIsNone(dates.parse_day("yesterday"))
        self.assertIsNone(dates.parse_day("2026-13-01"))

    def test_label(self) -> None:
        start, end = day(2026, 10, 11)
        self.assertEqual(dates.label(start, end), "2026-10-11")
        self.assertEqual(
            dates.label(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp(),
                        datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()),
            "2026-09-01..2026-09-30")


def _backdate(scope: ScopeStore, filename: str, ts: float) -> None:
    scope.set_file_meta(filename, created=ts, updated=ts)


class DateRecallTest(IsolatedCase):
    def test_search_filters_by_filing_date(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        scope.write_topic("recent", "# Recent\n\n- Milestone completed: dark mode shipped.\n")
        scope.write_topic("old", "# Old\n\n- Milestone completed: splash screen shipped.\n")
        recent_ts = datetime(2026, 10, 11, 12, tzinfo=timezone.utc).timestamp()
        _backdate(scope, "recent.md", recent_ts)
        _backdate(scope, "old.md",
                  datetime(2026, 8, 1, 12, tzinfo=timezone.utc).timestamp())
        since, until = day(2026, 10, 11)
        hits = rag.search_all([scope], "milestone completed", since=since,
                              until=until, now=NOW)
        self.assertEqual([h.file for h in hits], ["recent.md"])

    def test_empty_query_lists_in_range(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        scope.write_topic("recent", "# Recent\n\n- Milestone completed: dark mode shipped.\n")
        scope.write_topic("old", "# Old\n\n- Chose the network library.\n")
        _backdate(scope, "recent.md",
                  datetime(2026, 10, 11, 12, tzinfo=timezone.utc).timestamp())
        _backdate(scope, "old.md",
                  datetime(2026, 8, 1, 12, tzinfo=timezone.utc).timestamp())
        since, until = day(2026, 10, 11)
        hits = rag.search_all([scope], "", since=since, until=until, now=NOW)
        self.assertEqual([h.file for h in hits], ["recent.md"])

    def test_lexical_miss_falls_back_to_range(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        scope.write_topic("recent", "# Recent\n\n- Milestone completed: dark mode shipped.\n")
        _backdate(scope, "recent.md",
                  datetime(2026, 10, 11, 12, tzinfo=timezone.utc).timestamp())
        since, until = day(2026, 10, 11)
        hits = rag.search_all([scope], "quantum quark", since=since,
                              until=until, now=NOW)
        self.assertEqual([h.file for h in hits], ["recent.md"])

    def test_explain_respects_range(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        scope.write_topic("recent", "# Recent\n\n- Milestone completed: dark mode shipped.\n")
        scope.write_topic("old", "# Old\n\n- Milestone completed: splash screen shipped.\n")
        _backdate(scope, "recent.md",
                  datetime(2026, 10, 11, 12, tzinfo=timezone.utc).timestamp())
        _backdate(scope, "old.md",
                  datetime(2026, 8, 1, 12, tzinfo=timezone.utc).timestamp())
        since, until = day(2026, 10, 11)
        rows = rag.explain_all([scope], "milestone completed",
                               since=since, until=until)
        self.assertEqual([r["file"] for r in rows], ["recent.md"])

    def test_recall_command_rejects_inverted_range(self) -> None:
        args = SimpleNamespace(cwd=self.work, scopes=None, top_k=0,
                               explain=False, json=True,
                               include_archive=False, since="2026-10-12",
                               until="2026-10-01",
                               query="completed")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cmd_recall(args), 1)
        self.assertIn("error", json.loads(buf.getvalue()))

    def test_mcp_recall_rejects_inverted_range(self) -> None:
        from mcp.server import tool_recall
        out = tool_recall({"query": "completed", "since": "2026-10-12",
                           "until": "2026-10-01"})
        self.assertTrue(out.startswith("error:"))

    def test_recall_command_answers_yesterday(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        personal = store.get_scope("project-personal")
        assert personal is not None
        personal.write_topic("milestone", "# Milestone\n\n- Milestone completed: dark mode shipped.\n")
        personal.write_topic("old", "# Old\n\n- Chose the network library.\n")
        now_ts = datetime.now(timezone.utc).timestamp()
        noon = datetime.now(timezone.utc).replace(
            hour=12, minute=0, second=0, microsecond=0).timestamp()
        _backdate(personal, "milestone.md", noon - 86400)
        _backdate(personal, "old.md", now_ts - 60 * 86400)
        args = SimpleNamespace(cwd=self.work, scopes=None, top_k=0,
                               explain=False, json=True,
                               include_archive=False, since=None,
                               until=None,
                               query="what was completed yesterday")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cmd_recall(args), 0)
        rows = json.loads(buf.getvalue())
        files = [r["file"] for r in rows]
        self.assertIn("milestone.md", files)
        self.assertNotIn("old.md", files)


if __name__ == "__main__":
    unittest.main()
