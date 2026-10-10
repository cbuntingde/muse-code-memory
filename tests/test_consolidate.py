import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import IsolatedCase  # noqa: E402
from muse_memory import consolidate as cons  # noqa: E402
from muse_memory.store import MemoryStore, ScopeStore  # noqa: E402


def cand(text: str, **kw) -> dict:
    base = {"text": text, "kind": "note", "topic": "test-topic",
            "importance": 3, "tags": [], "scope": "project-personal",
            "source": "auto", "session_id": ""}
    base.update(kw)
    return base


class ConsolidateTest(IsolatedCase):
    def test_file_candidate_create_append_duplicate(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        r1 = cons.file_candidate(cand("Always run the full test suite."), store)
        self.assertEqual(r1["action"], "created")
        r2 = cons.file_candidate(cand("Always run the full test suite."), store)
        self.assertEqual(r2["action"], "duplicate")

    def test_dedupe_removes_copies(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("one", "# One\n\n- Deploy on Fridays only.\n",
                          meta={"importance": 4})
        scope.write_topic("two", "# Two\n\n- Deploy on Fridays only!\n",
                          meta={"importance": 2})
        report = cons.dedupe_scope(scope, threshold=0.8)
        self.assertEqual(report["bullets_removed"], 1)

    def test_expire_deletes_expired(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("temp", "# Temp\n\n- short lived\n",
                          meta={"expires": time.time() - 10})
        report = cons.expire_scope(scope)
        self.assertEqual(report["expired"], 1)
        self.assertFalse((scope.dir / "temp.md").exists())

    def test_expire_archives_stale_trivia(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("old", "# Old\n\n- trivia\n",
                          meta={"importance": 1, "source": "auto",
                                "updated": time.time() - 100 * 86400})
        report = cons.expire_scope(scope, stale_days=90)
        self.assertEqual(report["archived"], 1)

    def test_trim_shrinks_oversized(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("big", "# Big\n\n" + "".join(f"- bullet number {i}\n"
                                                      for i in range(200)))
        report = cons.trim_scope(scope, max_chars=500)
        self.assertEqual(report["files_trimmed"], 1)
        self.assertLess(len((scope.dir / "big.md").read_text()), 2000)

    def test_dedupe_preserves_frontmatter(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("one", "# One\n\n- Deploy on Fridays only.\n",
                          meta={"importance": 4})
        scope.write_topic("two", "# Two\n\n- Deploy on Fridays only!\n",
                          meta={"importance": 2})
        cons.dedupe_scope(scope, threshold=0.8)
        for name in ("one.md", "two.md"):
            raw = (scope.dir / name).read_text(encoding="utf-8")
            self.assertTrue(raw.startswith("---\n"), name)

    def test_trim_preserves_frontmatter(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("big", "# Big\n\n" + "".join(f"- bullet number {i}\n"
                                                      for i in range(200)))
        cons.trim_scope(scope, max_chars=500)
        raw = (scope.dir / "big.md").read_text(encoding="utf-8")
        self.assertTrue(raw.startswith("---\n"))

    def test_cap_retires_lowest_value(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        for i in range(5):
            scope.write_topic(f"topic-{i}", f"# Topic {i}\n\n- v{i}\n",
                              meta={"importance": 2})
        scope.write_topic("standing-rule", "# Rule\n\n- Never break this.\n",
                          meta={"importance": 5})
        report = cons.enforce_file_cap(scope, max_files=3)
        self.assertEqual(report["retired_for_cap"], 3)
        self.assertTrue((scope.dir / "standing-rule.md").exists())

    def test_cleanup_runs_all_jobs(self) -> None:
        (self.work / ".git").mkdir()
        store = MemoryStore(self.work)
        store.ensure_all()
        report = cons.cleanup(store, {})
        self.assertEqual(set(report), {"global", "project-personal",
                                       "project-shared"})


if __name__ == "__main__":
    unittest.main()
