"""Catch-up suite: diagnostics, health, removal, config, export, archive recall."""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import ROOT, IsolatedCase  # noqa: E402
from muse_memory import consolidate as cons  # noqa: E402
from muse_memory import lifecycle, rag  # noqa: E402
from muse_memory.__main__ import (cmd_archive, cmd_config, cmd_doctor, cmd_export,  # noqa: E402
                                  cmd_forget, cmd_import, cmd_pending, cmd_pin,
                                  cmd_recall, cmd_stats, cmd_unpin)
from muse_memory.store import MemoryStore, ScopeStore  # noqa: E402


def _run(fn, **kw) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = fn(SimpleNamespace(**kw))
    return code, buf.getvalue()


def _seed(work) -> MemoryStore:
    store = MemoryStore(work)
    store.ensure_all()
    personal = store.get_scope("project-personal")
    assert personal is not None
    personal.write_topic("deploy-process",
                         "# Deploy\n\n- Deploy with blue-green slots.\n",
                         meta={"importance": 4, "tags": ["decision"]})
    personal.write_topic("trivia-note", "# Trivia\n\n- Fern is a plant.\n",
                         meta={"importance": 1})
    return store


class DiagnosticsTest(IsolatedCase):
    def test_hook_crash_lands_in_log(self) -> None:
        sys.path.insert(0, str(ROOT / "hooks"))
        try:
            import _common
        finally:
            sys.path.remove(str(ROOT / "hooks"))

        def boom(event: dict) -> None:
            raise RuntimeError("boom-diagnostic-marker")

        _common.run(boom)
        from muse_memory import paths
        log = paths.diagnostics_log()
        self.assertTrue(log.is_file())
        self.assertIn("boom-diagnostic-marker",
                      log.read_text(encoding="utf-8"))

    def test_ci_workflow_exists(self) -> None:
        wf = ROOT / ".github" / "workflows" / "ci.yml"
        self.assertTrue(wf.is_file())
        body = wf.read_text(encoding="utf-8")
        self.assertIn("unittest", body)
        self.assertIn("validate", body)


class StatsTest(IsolatedCase):
    def test_cli_stats_richer(self) -> None:
        store = _seed(self.work)
        lifecycle.queue_candidates(
            [{"text": "Queued fact here.", "kind": "note", "topic": "q",
              "importance": 3, "tags": [], "scope": "project-personal",
              "source": "tool", "session_id": ""}], store)
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.archive_topic("trivia-note.md")
        code, out = _run(cmd_stats, cwd=str(self.work), scopes=None)
        self.assertEqual(code, 0)
        personal = json.loads(out)["scopes"]["project-personal"]
        for key in ("importance", "pending", "archived", "index"):
            self.assertIn(key, personal)
        self.assertEqual(personal["pending"], 1)
        self.assertEqual(personal["archived"], 1)
        self.assertEqual(personal["importance"].get("4"), 1)

    def test_mcp_stats_richer(self) -> None:
        from mcp.server import tool_stats  # noqa: E402

        _seed(self.work)
        personal = json.loads(tool_stats({}))["project-personal"]
        for key in ("importance", "pending", "archived", "index"):
            self.assertIn(key, personal)


class ForgetArchiveTest(IsolatedCase):
    def test_cli_forget_deletes(self) -> None:
        store = _seed(self.work)
        code, out = _run(cmd_forget, cwd=str(self.work), scopes=None,
                         file="trivia-note.md", scope="project-personal")
        self.assertEqual(code, 0)
        self.assertIn("deleted", out)
        scope = store.get_scope("project-personal")
        assert scope is not None
        self.assertFalse((scope.dir / "trivia-note.md").exists())

    def test_cli_forget_missing_errors(self) -> None:
        _seed(self.work)
        code, out = _run(cmd_forget, cwd=str(self.work), scopes=None,
                         file="nope.md", scope="project-personal")
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_cli_archive_moves(self) -> None:
        store = _seed(self.work)
        code, out = _run(cmd_archive, cwd=str(self.work), scopes=None,
                         file="project-personal/trivia-note.md", scope=None)
        self.assertEqual(code, 0)
        self.assertIn("archived", out)
        scope = store.get_scope("project-personal")
        assert scope is not None
        self.assertTrue((scope.dir / "archive" / "trivia-note.md").exists())

    def test_manifest_registers_removal_commands(self) -> None:
        manifest = json.loads(
            (ROOT / ".muse-plugin" / "plugin.json").read_text(encoding="utf-8"))
        ids = {c["id"]: c["path"]
               for c in manifest["capabilities"]["commands"]}
        for cmd_id in ("forget", "archive"):
            self.assertIn(cmd_id, ids)
            body = (ROOT / ids[cmd_id]).read_text(encoding="utf-8")
            self.assertIn("description:", body)
            self.assertIn("argument-hint:", body)


class ConfigTest(IsolatedCase):
    def test_invalid_values_fall_back_to_defaults(self) -> None:
        from muse_memory import config as config_mod  # noqa: E402

        cfg_file = self.config_home / "muse" / "memory" / "config.json"
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(json.dumps({
            "recall_top_k": "lots", "min_score": -5,
            "max_files_per_scope": 0, "capture_enabled": "yes",
            "default_scope": "nope", "recall_max_chars": 8000,
        }), encoding="utf-8")
        cfg = config_mod.load(self.work)
        self.assertEqual(cfg["recall_top_k"], 5)
        self.assertEqual(cfg["min_score"], 0.05)
        self.assertEqual(cfg["max_files_per_scope"], 40)
        self.assertIs(cfg["capture_enabled"], True)
        self.assertEqual(cfg["default_scope"], "project-personal")
        self.assertEqual(cfg["recall_max_chars"], 8000)

    def test_cli_config_shows_effective(self) -> None:
        code, out = _run(cmd_config, cwd=str(self.work), scopes=None)
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertIn("effective", report)
        self.assertEqual(report["effective"]["recall_top_k"], 5)
        self.assertIn("user_config", report)


class DoctorPendingTest(IsolatedCase):
    def test_pending_shows_queued(self) -> None:
        store = _seed(self.work)
        lifecycle.queue_candidates(
            [{"text": "Queued fact here.", "kind": "note", "topic": "q",
              "importance": 3, "tags": [], "scope": "project-personal",
              "source": "tool", "session_id": ""}], store)
        code, out = _run(cmd_pending, cwd=str(self.work), scopes=None,
                         limit=5, json=True)
        self.assertEqual(code, 0)
        queued = json.loads(out)["scopes"]["project-personal"]["queued"]
        self.assertEqual(queued, 1)

    def test_doctor_flags_oversized_and_stale(self) -> None:
        store = _seed(self.work)
        scope = store.get_scope("project-personal")
        assert scope is not None
        rag.search_all([scope], "blue-green deploy", top_k=1)
        scope.write_topic("big", "# Big\n\n" + "- word " * 5000 + "\n")
        code, out = _run(cmd_doctor, cwd=str(self.work), scopes=None,
                         json=True)
        self.assertEqual(code, 0)
        personal = json.loads(out)["scopes"]["project-personal"]
        self.assertIn("big.md", personal["oversized"])
        self.assertTrue(personal["index"]["stale"])
        self.assertIn("diagnostics", json.loads(out))


class ExportImportTest(IsolatedCase):
    def test_export_import_roundtrip(self) -> None:
        store = _seed(self.work)
        bundle = str(Path(self.tmp.name) / "bundle.md")
        code, _ = _run(cmd_export, cwd=str(self.work), scopes=None,
                       scope=None, output=bundle)
        self.assertEqual(code, 0)
        scope = store.get_scope("project-personal")
        assert scope is not None
        before = {p.name: p.read_text(encoding="utf-8")
                  for p in scope.topic_files()}
        self.assertTrue(before)
        for p in scope.topic_files():
            scope.delete_topic(p.name)
        self.assertEqual(scope.topic_files(), [])
        code, out = _run(cmd_import, cwd=str(self.work), scopes=None,
                         bundle=bundle, scope=None)
        self.assertEqual(code, 0)
        self.assertIn("imported", out)
        after = {p.name: p.read_text(encoding="utf-8")
                 for p in scope.topic_files()}
        self.assertEqual(before, after)

    def test_import_restores_metadata(self) -> None:
        store = _seed(self.work)
        bundle = str(Path(self.tmp.name) / "bundle.md")
        _run(cmd_export, cwd=str(self.work), scopes=None,
             scope=None, output=bundle)
        scope = store.get_scope("project-personal")
        assert scope is not None
        for p in scope.topic_files():
            scope.delete_topic(p.name)
        _run(cmd_import, cwd=str(self.work), scopes=None,
             bundle=bundle, scope=None, force=False)
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        rec = scope.read_record(scope.dir / "deploy-process.md")
        self.assertEqual(rec.importance, 4)
        self.assertIn("decision", rec.tags)

    def test_import_skips_existing_without_force(self) -> None:
        _seed(self.work)
        bundle = str(Path(self.tmp.name) / "bundle.md")
        _run(cmd_export, cwd=str(self.work), scopes=None,
             scope=None, output=bundle)
        code, out = _run(cmd_import, cwd=str(self.work), scopes=None,
                         bundle=bundle, scope=None, force=False)
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["imported"], [])
        self.assertTrue(report["skipped"])
        code, out = _run(cmd_import, cwd=str(self.work), scopes=None,
                         bundle=bundle, scope=None, force=True)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["imported"])

    def test_export_scope_report_lists_only_exported(self) -> None:
        _seed(self.work)
        bundle = str(Path(self.tmp.name) / "bundle.md")
        code, out = _run(cmd_export, cwd=str(self.work), scopes=None,
                         scope="global", output=bundle)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["scopes"], ["global"])


class PinTest(IsolatedCase):
    def test_pin_prioritizes_capped_index(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        for i in range(49):
            scope.write_topic(f"topic-{i:02d}", f"# Topic {i}\n\n- v{i}\n",
                              meta={"importance": 1})
        scope.write_topic("pinned-gem", "# Gem\n\n- keep me\n",
                          meta={"importance": 1})
        scope.set_file_meta("pinned-gem.md", pinned=True)
        scope.refresh_index()
        index = (scope.dir / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("[Gem](pinned-gem.md)", index)
        self.assertIn("more files", index)

    def test_cap_skips_pinned(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        scope.write_topic("pinned-gem", "# Gem\n\n- keep me\n",
                          meta={"importance": 1})
        scope.set_file_meta("pinned-gem.md", pinned=True)
        for i in range(4):
            scope.write_topic(f"topic-{i}", f"# Topic {i}\n\n- v{i}\n",
                              meta={"importance": 1})
        report = cons.enforce_file_cap(scope, max_files=2)
        self.assertEqual(report["retired_for_cap"], 3)
        self.assertTrue((scope.dir / "pinned-gem.md").exists())

    def test_cli_pin_unpin(self) -> None:
        _seed(self.work)
        code, out = _run(cmd_pin, cwd=str(self.work), scopes=None,
                         file="trivia-note.md", scope="project-personal")
        self.assertEqual(code, 0)
        self.assertIn("pinned", out)
        store = MemoryStore(self.work)
        scope = store.get_scope("project-personal")
        assert scope is not None
        self.assertTrue(scope.read_record(scope.dir / "trivia-note.md").pinned)
        code, _ = _run(cmd_unpin, cwd=str(self.work), scopes=None,
                       file="trivia-note.md", scope="project-personal")
        self.assertEqual(code, 0)
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        self.assertFalse(
            scope.read_record(scope.dir / "trivia-note.md").pinned)


class ArchiveRecallTest(IsolatedCase):
    def test_archive_hidden_by_default_shown_on_request(self) -> None:
        store = _seed(self.work)
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.archive_topic("deploy-process.md")
        self.assertEqual(rag.search_all([scope], "blue-green deploy slots",
                                       top_k=3), [])
        hits = rag.search_all([scope], "blue-green deploy slots", top_k=3,
                              include_archive=True)
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].archived)
        self.assertIn("(archived)", rag.format_context(hits, 4000))

    def test_cli_recall_include_archive(self) -> None:
        store = _seed(self.work)
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.archive_topic("deploy-process.md")
        base = {"cwd": str(self.work), "scopes": None,
                "query": "blue-green deploy slots", "top_k": 3,
                "json": False, "explain": False}
        _, plain = _run(cmd_recall, include_archive=False, **base)
        self.assertIn("no relevant memory", plain)
        _, flagged = _run(cmd_recall, include_archive=True, **base)
        self.assertIn("(archived)", flagged)


class ExplainTest(IsolatedCase):
    def test_explain_shows_components(self) -> None:
        _seed(self.work)
        _, out = _run(cmd_recall, cwd=str(self.work), scopes=None,
                      query="blue-green deploy slots", top_k=2, json=False,
                      include_archive=False, explain=True)
        self.assertIn("lexical", out)
        self.assertIn("deploy-process.md", out)
        self.assertIn("score", out)


if __name__ == "__main__":
    unittest.main()
