import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import IsolatedCase  # noqa: E402
from muse_memory.store import MemoryStore, ScopeStore  # noqa: E402


class StoreTest(IsolatedCase):
    def test_write_read_roundtrip(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        rec = scope.write_topic("api-conventions",
                                "# API Conventions\n\n- Use plural nouns.\n",
                                meta={"importance": 4, "tags": ["api"]})
        self.assertEqual(rec.file, "api-conventions.md")
        self.assertEqual(rec.title, "API Conventions")
        self.assertEqual(rec.importance, 4)
        back = scope.read_record(scope.dir / rec.file)
        self.assertEqual(back.content, "# API Conventions\n\n- Use plural nouns.\n")
        self.assertEqual(back.tags, ["api"])

    def test_index_lists_topics(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("b-topic", "# B\n\n- bee\n")
        scope.write_topic("a-topic", "# A\n\n- aye\n")
        index = (scope.dir / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("[B](b-topic.md)", index)
        self.assertIn("[A](a-topic.md)", index)

    def test_append_skips_duplicates(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("t", "# T\n\n- one\n")
        scope.append_to_topic("t.md", "- one")
        scope.append_to_topic("t.md", "- two")
        body = (scope.dir / "t.md").read_text(encoding="utf-8")
        self.assertEqual(body.count("- one"), 1)
        self.assertIn("- two", body)

    def test_concurrent_meta_writes_merge(self) -> None:
        import json  # noqa: E402

        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("a", "# A\n\n- aye\n")
        scope.write_topic("b", "# B\n\n- bee\n")
        view_a = ScopeStore("global", self.data_home / "mem")
        view_b = ScopeStore("global", self.data_home / "mem")
        view_a.set_file_meta("a.md", importance=5)
        view_b.set_file_meta("b.md", importance=1)
        disk = json.loads((scope.dir / ".memory-meta.json").read_text(
            encoding="utf-8"))
        self.assertEqual(disk["a.md"]["importance"], 5)
        self.assertEqual(disk["b.md"]["importance"], 1)

    def test_archive_and_delete(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("gone", "# Gone\n\n- x\n")
        self.assertTrue(scope.archive_topic("gone.md"))
        self.assertFalse((scope.dir / "gone.md").exists())
        self.assertTrue((scope.dir / "archive" / "gone.md").exists())
        self.assertFalse(scope.delete_topic("missing.md"))

    def test_store_all_scopes(self) -> None:
        (self.work / ".git").mkdir()
        store = MemoryStore(self.work)
        self.assertEqual(set(store.scopes), {"global", "project-personal",
                                             "project-shared"})
        store.ensure_all()
        for scope in store.scopes.values():
            self.assertTrue((scope.dir / "MEMORY.md").is_file())

    def test_touch_access_counts(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("t", "# T\n\n- x\n")
        scope.touch_access("t.md")
        scope.touch_access("t.md")
        self.assertEqual(scope.read_record(scope.dir / "t.md").access_count, 2)

    def test_write_topic_adds_frontmatter(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        body = "# API Conventions\n\n- Use plural nouns.\n"
        scope.write_topic("api-conventions", body,
                          meta={"importance": 4, "tags": ["api", "decision"],
                                "source": "auto"})
        raw = (scope.dir / "api-conventions.md").read_text(encoding="utf-8")
        self.assertTrue(raw.startswith("---\n"))
        self.assertIn("title: API Conventions", raw)
        self.assertIn("tags: api, decision", raw)
        self.assertIn("importance: 4", raw)
        self.assertIn("source: auto", raw)
        self.assertRegex(raw, r"created: \d{4}-\d{2}-\d{2}")
        # Content roundtrips byte-identical; frontmatter is documentary.
        back = scope.read_record(scope.dir / "api-conventions.md")
        self.assertEqual(back.content, body)

    def test_write_topic_replace_keeps_single_frontmatter(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("t", "# T\n\n- one\n")
        scope.write_topic("t", "# T\n\n- one\n\n- two\n")
        raw = (scope.dir / "t.md").read_text(encoding="utf-8")
        self.assertEqual(raw.count("---\n"), 2)
        self.assertIn("- two", raw)


if __name__ == "__main__":
    unittest.main()
