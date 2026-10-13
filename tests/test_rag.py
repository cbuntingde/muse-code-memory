import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import IsolatedCase  # noqa: E402
from muse_memory import rag  # noqa: E402
from muse_memory.store import ScopeStore  # noqa: E402


def seed(scope: ScopeStore) -> None:
    scope.write_topic("deploy-process",
                      "# Deploy process\n\n- Deploy with blue-green slots on Fridays.\n",
                      meta={"importance": 4})
    scope.write_topic("pasta-recipe",
                      "# Pasta recipe\n\n- Boil water with plenty of salt first.\n",
                      meta={"importance": 2})


class RagTest(IsolatedCase):
    def test_search_ranks_relevant_first(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        seed(scope)
        idx = rag.RagIndex()
        idx.build(scope.read_all())
        hits = idx.search("how do we deploy on Friday", top_k=2)
        self.assertTrue(hits)
        self.assertEqual(hits[0].file, "deploy-process.md")

    def test_search_no_match_returns_empty(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        seed(scope)
        idx = rag.RagIndex()
        idx.build(scope.read_all())
        self.assertEqual(idx.search("quantum chromodynamics quark"), [])

    def test_index_caches_and_rebuilds(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        seed(scope)
        first = rag.load_or_build(scope)
        self.assertTrue((scope.dir / ".rag-index.db").is_file())
        second = rag.load_or_build(scope)
        self.assertEqual(len(first.chunks), len(second.chunks))
        scope.write_topic("extra-note", "# Extra\n\n- Something new here.\n")
        third = rag.load_or_build(scope)
        self.assertGreater(len(third.chunks), len(first.chunks))

    def test_touch_retires_cached_index(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        seed(scope)
        rag.load_or_build(scope)
        self.assertFalse(rag.index_health(scope)["stale"])
        scope.touch_access("deploy-process.md")
        self.assertTrue(rag.index_health(scope)["stale"])

    def test_delete_invalidates_cached_index(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        seed(scope)
        self.assertTrue(rag.search_all([scope], "how do we deploy on Friday"))
        self.assertTrue(scope.delete_topic("deploy-process.md"))
        self.assertFalse((scope.dir / ".rag-index.db").exists())
        self.assertEqual(rag.search_all([scope], "how do we deploy on Friday"), [])

    def test_archive_invalidates_cached_index(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        seed(scope)
        self.assertTrue(rag.search_all([scope], "how do we deploy on Friday"))
        self.assertTrue(scope.archive_topic("deploy-process.md"))
        self.assertEqual(rag.search_all([scope], "how do we deploy on Friday"), [])

    def test_search_all_merges_scopes(self) -> None:
        a = ScopeStore("global", self.data_home / "a")
        b = ScopeStore("project-personal", self.data_home / "b")
        a.write_topic("editor", "# Editor\n\n- Always use spaces for indentation.\n")
        b.write_topic("deploy-process", "# Deploy\n\n- Deploy with blue-green slots.\n")
        hits = rag.search_all([a, b], "blue-green deploy slots", top_k=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0].scope, "project-personal")

    def test_format_context_truncates(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("long", "# Long\n\n" + "- word " * 500 + "\n")
        idx = rag.RagIndex()
        idx.build(scope.read_all())
        hits = idx.search("word", top_k=5)
        text = rag.format_context(hits, max_chars=200)
        self.assertLessEqual(len(text), 400)

    def test_format_context_always_emits_first_hit(self) -> None:
        scope = ScopeStore("global", self.data_home / "mem")
        scope.write_topic("long", "# Long\n\n" + "- word " * 500 + "\n")
        idx = rag.RagIndex()
        idx.build(scope.read_all())
        hits = idx.search("word", top_k=5)
        text = rag.format_context(hits, max_chars=200)
        self.assertIn("long.md", text)
        self.assertLessEqual(len(text), 400)

    def test_corrupt_index_shapes_recover(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        seed(scope)
        for bad in (b'not a sqlite database', b'', b'{"chunks": 5}'):
            (scope.dir / ".rag-index.db").write_bytes(bad)
            health = rag.index_health(scope)
            self.assertIn("stale", health)
            self.assertTrue(health["stale"])
            hits = rag.search_all([scope], "how do we deploy on Friday",
                                  top_k=2)
            self.assertTrue(hits)
            self.assertEqual(hits[0].file, "deploy-process.md")

    def test_legacy_json_index_is_retired(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        seed(scope)
        (scope.dir / ".rag-index.json").write_text("{}", encoding="utf-8")
        self.assertTrue(rag.index_health(scope)["stale"])
        rag.load_or_build(scope)
        self.assertTrue((scope.dir / ".rag-index.db").is_file())
        self.assertFalse((scope.dir / ".rag-index.json").exists())


if __name__ == "__main__":
    unittest.main()
