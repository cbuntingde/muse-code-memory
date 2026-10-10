import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import ROOT, IsolatedCase  # noqa: E402
from muse_memory.__main__ import cmd_list  # noqa: E402
from muse_memory.store import MemoryStore, ScopeStore  # noqa: E402


def _list_args(cwd, **kw):
    base = {"cwd": cwd, "scopes": None, "json": False, "scope": None,
            "tag": None, "min_importance": 0, "format": "table"}
    base.update(kw)
    return SimpleNamespace(**base)


def _run_list(cwd, **kw) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_list(_list_args(cwd, **kw))
    return buf.getvalue()


def _seed_personal(work) -> MemoryStore:
    store = MemoryStore(work)
    store.ensure_all()
    personal = store.get_scope("project-personal")
    assert personal is not None
    personal.write_topic("standing-rule", "# Standing Rule\n\n- Always run tests.\n",
                         meta={"importance": 5, "tags": ["rule"]})
    personal.write_topic("db-choice", "# DB Choice\n\n- We chose Postgres.\n",
                         meta={"importance": 4, "tags": ["decision"]})
    personal.write_topic("login-fix", "# Login Fix\n\n- Fixed null token crash.\n",
                         meta={"importance": 3, "tags": ["fix"]})
    personal.write_topic("trivia-note", "# Trivia\n\n- Office plant is named Fern.\n",
                         meta={"importance": 1, "tags": []})
    return store


class GroupedIndexTest(IsolatedCase):
    def test_sections_group_records(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        scope.write_topic("standing-rule", "# Standing Rule\n\n- Always run tests.\n",
                          meta={"importance": 5, "tags": ["rule"]})
        scope.write_topic("db-choice", "# DB Choice\n\n- We chose Postgres.\n",
                          meta={"importance": 4, "tags": ["decision"]})
        scope.write_topic("plain", "# Plain\n\n- Just a note.\n")
        index = (scope.dir / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("## Standing rules", index)
        self.assertIn("## Decisions", index)
        self.assertIn("## Facts & Notes", index)
        self.assertIn("[Standing Rule](standing-rule.md)", index)
        self.assertIn("[DB Choice](db-choice.md)", index)

    def test_rows_carry_metadata_and_footer(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        scope.write_topic("db-choice", "# DB Choice\n\n- We chose Postgres.\n",
                          meta={"importance": 4, "tags": ["decision"]})
        index = (scope.dir / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("imp:4", index)
        self.assertRegex(index, r"\d{4}-\d{2}-\d{2}")
        self.assertIn("#decision", index)
        self.assertIn("topic", index.split("---")[-1])

    def test_frontmatter_and_cap_preserved(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        for i in range(50):
            scope.write_topic(f"topic-{i:02d}", f"# Topic {i}\n\n- value {i}\n")
        index = (scope.dir / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("type:", index.split("---")[1])
        self.assertIn("+2 more files", index)
        self.assertEqual(index.count("]("), 48)


class ListCommandTest(IsolatedCase):
    def test_default_table_lists_all(self) -> None:
        _seed_personal(self.work)
        out = _run_list(self.work)
        self.assertIn("Scope", out)
        self.assertIn("standing-rule.md", out)
        self.assertIn("trivia-note.md", out)
        self.assertRegex(out, r"\d{4}-\d{2}-\d{2}")

    def test_tag_filter(self) -> None:
        _seed_personal(self.work)
        out = _run_list(self.work, tag="decision")
        self.assertIn("db-choice.md", out)
        self.assertNotIn("login-fix.md", out)
        self.assertNotIn("trivia-note.md", out)

    def test_min_importance_filter(self) -> None:
        _seed_personal(self.work)
        out = _run_list(self.work, min_importance=4)
        self.assertIn("standing-rule.md", out)
        self.assertIn("db-choice.md", out)
        self.assertNotIn("login-fix.md", out)

    def test_scope_filter(self) -> None:
        store = _seed_personal(self.work)
        glob = store.get_scope("global")
        assert glob is not None
        glob.write_topic("editor-pref", "# Editor\n\n- Use spaces.\n")
        out = _run_list(self.work, scope="global")
        self.assertIn("editor-pref.md", out)
        self.assertNotIn("db-choice.md", out)

    def test_markdown_format(self) -> None:
        _seed_personal(self.work)
        out = _run_list(self.work, format="markdown")
        self.assertIn("| Scope |", out)
        self.assertIn("db-choice.md", out)

    def test_json_respects_filters(self) -> None:
        _seed_personal(self.work)
        out = _run_list(self.work, json=True, tag="fix")
        rows = json.loads(out)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file"], "login-fix.md")
        for key in ("scope", "file", "title", "importance", "tags",
                    "access_count"):
            self.assertIn(key, rows[0])

    def test_empty_store_message(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        out = _run_list(self.work)
        self.assertIn("no memories", out.lower())


class MemoriesManifestTest(unittest.TestCase):
    def test_plugin_registers_memories_command(self) -> None:
        manifest = json.loads(
            (ROOT / ".muse-plugin" / "plugin.json").read_text(encoding="utf-8"))
        commands = manifest["capabilities"]["commands"]
        entry = next((c for c in commands if c["id"] == "memories"), None)
        self.assertIsNotNone(entry)
        assert entry is not None
        path = ROOT / entry["path"]
        self.assertTrue(path.is_file())
        body = path.read_text(encoding="utf-8")
        self.assertIn("description:", body)
        self.assertIn("argument-hint:", body)

    def test_marketplace_catalog_valid(self) -> None:
        catalog = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"))
        entries = catalog.get("plugins", [])
        self.assertTrue(entries)
        manifest = json.loads(
            (ROOT / ".muse-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertIn(manifest["name"], {p.get("name") for p in entries})
        for entry in entries:
            source = entry.get("source", {})
            self.assertIn("source", source)
            target = (ROOT / source.get("path", ".")).resolve()
            self.assertTrue(
                (target / ".muse-plugin" / "plugin.json").is_file())


if __name__ == "__main__":
    unittest.main()
