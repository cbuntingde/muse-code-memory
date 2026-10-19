"""Regression tests: native read_memory must find the plugin's topics.

Muse's native memory tools (read_memory / add_memory / edit_memory) address
the same directories the plugin manages, with their own scope names and
relative paths. The plugin therefore has to (a) live in the directory Muse
looks in and (b) hand out a scope/path pointer that native can resolve.
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import IsolatedCase  # noqa: E402
from muse_memory import listing, paths, rag  # noqa: E402
from muse_memory.store import MemoryStore, index_preamble  # noqa: E402


def _native_stem(canon: str) -> str:
    """Muse sanitizes one dash per character, the plugin collapses runs."""
    return re.sub(r"[^A-Za-z0-9]", "-", canon).strip("-")


class NativeDirAdoptionTest(IsolatedCase):
    def _make_native(self, name: str) -> Path:
        root = paths.projects_root()
        root.mkdir(parents=True, exist_ok=True)
        native = root / name
        native.mkdir(parents=True, exist_ok=True)
        (native / "MEMORY.md").write_text(
            "---\ntype: project\n---\n\n# Native notes\n\n"
            "- kept by the plugin\n", encoding="utf-8")
        return native

    def test_adopts_native_dir_with_other_sanitizer(self) -> None:
        """A Windows drive letter makes the two sanitizers disagree."""
        canon = str(paths.canonical_cwd(self.work))
        native = self._make_native(f"{_native_stem(canon)}-495a58d059b674a3")
        self.assertEqual(paths.slug_for(self.work), native.name)
        self.assertEqual(paths.personal_project_dir(self.work), native)

    def test_ignores_dir_of_a_sibling_workspace(self) -> None:
        canon = str(paths.canonical_cwd(self.work))
        self._make_native(f"{_native_stem(canon)}-extra-112233445566")
        self.assertEqual(paths.slug_for(self.work),
                         paths.canonical_slug(self.work))

    def test_own_slug_used_when_no_native_dir(self) -> None:
        self.assertEqual(paths.slug_for(self.work),
                         paths.canonical_slug(self.work))

    def test_sync_copies_topics_into_native_dir(self) -> None:
        canon = str(paths.canonical_cwd(self.work))
        native = self._make_native(f"{_native_stem(canon)}-495a58d059b674a3")
        own = paths.projects_root() / paths.canonical_slug(self.work)
        own.mkdir(parents=True, exist_ok=True)
        (own / "rule-topic.md").write_text("# Rule\n\n- body\n", encoding="utf-8")
        (own / paths.META_FILE).write_text(
            json.dumps({"rule-topic.md": {"importance": 5}}),
            encoding="utf-8")
        self.assertEqual(paths.sync_native_topics(self.work), "copied")
        self.assertTrue((native / "rule-topic.md").is_file())
        meta = json.loads((native / paths.META_FILE).read_text(encoding="utf-8"))
        self.assertEqual(meta["rule-topic.md"]["importance"], 5)

    def test_sync_never_overwrites_existing_topic(self) -> None:
        canon = str(paths.canonical_cwd(self.work))
        native = self._make_native(f"{_native_stem(canon)}-495a58d059b674a3")
        (native / "rule-topic.md").write_text("# Native wins\n", encoding="utf-8")
        own = paths.projects_root() / paths.canonical_slug(self.work)
        own.mkdir(parents=True, exist_ok=True)
        (own / "rule-topic.md").write_text("# Plugin copy\n", encoding="utf-8")
        paths.sync_native_topics(self.work)
        self.assertEqual((native / "rule-topic.md").read_text(encoding="utf-8"),
                         "# Native wins\n")

    def test_sync_is_noop_without_native_dir(self) -> None:
        self.assertEqual(paths.sync_native_topics(self.work), "same")

    def test_index_rewrite_keeps_native_notes(self) -> None:
        canon = str(paths.canonical_cwd(self.work))
        native = self._make_native(f"{_native_stem(canon)}-495a58d059b674a3")
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        self.assertEqual(scope.dir, native)
        scope.write_topic("Plugin topic", "body text", {"importance": 4})
        scope.refresh_index()
        index = (native / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("kept by the plugin", index)
        self.assertIn("plugin-topic.md", index)

    def test_index_preamble_ignores_managed_sections(self) -> None:
        text = ("---\ntype: project\n---\n\n# Memory\n\n"
                "## Standing rules\n\n- [x](a.md)\n")
        self.assertEqual(index_preamble(text), "")


class ReadPointerTest(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = MemoryStore(self.work)
        self.store.ensure_all()
        self.scope = self.store.get_scope("project-personal")

    def test_scope_names_map_to_native(self) -> None:
        self.assertEqual(paths.native_scope_name("global"), "personal")
        self.assertEqual(paths.native_scope_name("project-personal"),
                         "personal_project")
        self.assertEqual(paths.native_scope_name("project-shared"), "project")

    def test_recall_line_carries_read_pointer(self) -> None:
        self.scope.write_topic("Rule topic", "cargo check is the build gate")
        hits = rag.search_all([self.scope], "cargo check", top_k=3)
        text = rag.format_context(hits, 4000)
        self.assertIn("[project-personal/rule-topic.md]", text)
        self.assertIn("read_memory personal_project/rule-topic.md", text)

    def test_listing_rows_carry_read_pointer(self) -> None:
        self.scope.write_topic("Rule topic", "body")
        rows = listing.list_rows(self.store)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["read"],
                         "personal_project/" + rows[0]["file"])
        self.assertIn("| Read |", listing.format_markdown(rows))


if __name__ == "__main__":
    unittest.main()
