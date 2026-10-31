"""Project entity memory: profile, command ledger, file registry."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import IsolatedCase  # noqa: E402
from muse_memory import consolidate as cons  # noqa: E402
from muse_memory import entities  # noqa: E402
from muse_memory import lifecycle  # noqa: E402
from muse_memory.store import MemoryStore  # noqa: E402


def _scope(case: IsolatedCase):
    store = MemoryStore(case.work)
    store.ensure_all()
    scope = store.get_scope("project-personal")
    assert scope is not None
    return store, scope


class EntityManagerTest(IsolatedCase):
    def test_note_command_creates_pinned_ledger(self) -> None:
        _store, scope = _scope(self)
        result = entities.note_command(scope, "npm test", str(self.work))
        self.assertEqual(result["action"], "created")
        self.assertTrue((scope.dir / "command-ledger.md").is_file())
        meta = scope.file_meta("command-ledger.md")
        self.assertTrue(meta.get("pinned"))
        self.assertIn("build", meta.get("tags", []))
        self.assertIn("npm test", (scope.dir / "command-ledger.md").read_text())

    def test_note_command_idempotent_and_bumps_seen(self) -> None:
        _store, scope = _scope(self)
        first = entities.note_command(scope, "npm test", str(self.work),
                                      seen="2026-01-01")
        second = entities.note_command(scope, "npm test", str(self.work),
                                       seen="2026-10-31")
        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "appended")
        body = (scope.dir / "command-ledger.md").read_text()
        self.assertEqual(body.count("npm test"), 1)
        self.assertIn("2026-10-31", body)

    def test_note_command_ignores_chatter(self) -> None:
        _store, scope = _scope(self)
        self.assertIsNone(entities.note_command(scope, "make dinner",
                                                str(self.work)))
        self.assertFalse((scope.dir / "command-ledger.md").is_file())

    def test_canonical_command_folds_npm_run(self) -> None:
        self.assertEqual(entities.canonical_command("npm  run  test"),
                         "npm test")
        self.assertEqual(entities.canonical_command("cargo test --all"),
                         "cargo test --all")

    def test_note_file_records_workspace_relative(self) -> None:
        _store, scope = _scope(self)
        target = self.work / "src" / "auth.py"
        target.parent.mkdir(parents=True)
        target.write_text("# auth\n", encoding="utf-8")
        result = entities.note_file(scope, str(target), "auth middleware",
                                    str(self.work))
        self.assertEqual(result["action"], "created")
        body = (scope.dir / "file-registry.md").read_text()
        self.assertIn("src/auth.py", body)
        self.assertNotIn(str(target), body)

    def test_extract_file_mentions_needs_project_context(self) -> None:
        hits = entities.extract_file_mentions(
            "The auth middleware lives in src/auth.py and handles login.")
        self.assertTrue(any(path == "src/auth.py" for path, _note in hits))
        self.assertEqual(
            entities.extract_file_mentions(
                "I added sugar to coffee and added milk too, tasty."),
            [])

    def test_scan_profile_finds_manifest_and_remote(self) -> None:
        (self.work / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (self.work / "README.md").write_text("# Demo\n", encoding="utf-8")
        git_config = self.work / ".git" / "config"
        git_config.parent.mkdir(parents=True)
        git_config.write_text('[remote "origin"]\n\turl = example/demo.git\n',
                              encoding="utf-8")
        bullets = entities.scan_profile(str(self.work))
        joined = "\n".join(bullets)
        self.assertIn("pyproject.toml", joined)
        self.assertIn("Demo", joined)
        self.assertIn("example/demo.git", joined)

    def test_ensure_profile_idempotent(self) -> None:
        _store, scope = _scope(self)
        first = entities.ensure_profile(scope, str(self.work))
        second = entities.ensure_profile(scope, str(self.work))
        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "duplicate")
        self.assertTrue((scope.dir / "project-profile.md").is_file())

    def test_working_set_marks_missing_files(self) -> None:
        _store, scope = _scope(self)
        keep = self.work / "src" / "keep.py"
        keep.parent.mkdir(parents=True)
        keep.write_text("# keep\n", encoding="utf-8")
        entities.note_file(scope, str(keep), "kept module", str(self.work))
        entities.note_file(scope, "src/gone.py", "removed module",
                           str(self.work))
        block = entities.working_set_text(scope, str(self.work))
        self.assertIn("src/keep.py", block)
        self.assertNotIn("src/keep.py` (missing)", block)
        self.assertIn("`src/gone.py` (missing)", block)

    def test_file_candidate_routes_entity_kinds(self) -> None:
        store, _scope_obj = _scope(self)
        result = cons.file_candidate(
            {"text": "Build command for this project: `npm test`.",
             "kind": "project-command", "command": "npm test",
             "topic": "command-ledger", "importance": 2, "tags": [],
             "scope": "project-personal", "source": "tool", "session_id": ""},
            store)
        self.assertIn(result["action"], ("created", "appended", "duplicate"))
        self.assertEqual(result["file"], "command-ledger.md")

    def test_entity_kinds_auto_file_below_threshold(self) -> None:
        store, _scope_obj = _scope(self)
        lifecycle.queue_candidates(
            [{"text": "Important project file: `src/auth.py`.",
              "kind": "project-file", "path": "src/auth.py",
              "note": "auth middleware",
              "topic": "file-registry", "importance": 2, "tags": [],
              "scope": "project-personal", "source": "tool",
              "session_id": ""}], store)
        report = lifecycle.file_pending(
            store, auto_file_importance=4, proposal_ttl_days=7)
        self.assertEqual(report["proposals_held"], 0)
        self.assertTrue(report["files"])

    def test_session_start_entities_requeues_on_change(self) -> None:
        from muse_memory import config as config_mod
        (self.work / "pyproject.toml").write_text("[project]\n",
                                                  encoding="utf-8")
        cfg = config_mod.load(self.work)
        first = lifecycle.session_start_entities(MemoryStore(self.work), cfg)
        self.assertEqual(first["queued"], 1)
        lifecycle.stop_checkpoint(self.work, "")
        store = MemoryStore(self.work)
        quiet = lifecycle.session_start_entities(store, cfg)
        self.assertEqual(quiet["queued"], 0)
        self.assertIn("pyproject.toml", quiet["block"])
        (self.work / "package.json").write_text("{}\n", encoding="utf-8")
        stale = lifecycle.session_start_entities(MemoryStore(self.work), cfg)
        self.assertEqual(stale["queued"], 1)

    def test_ensure_profile_never_clobbers_foreign_topic(self) -> None:
        _store, scope = _scope(self)
        (scope.dir / "project-profile.md").write_text(
            "# Mine\n\n- Hands off.\n", encoding="utf-8")
        result = entities.ensure_profile(scope, str(self.work))
        self.assertEqual(result["action"], "skipped")
        self.assertIn("Hands off.",
                      (scope.dir / "project-profile.md").read_text(
                          encoding="utf-8"))

    def test_ledger_orders_newest_first(self) -> None:
        _store, scope = _scope(self)
        entities.note_command(scope, "npm test", str(self.work),
                              seen="2026-01-01")
        entities.note_command(scope, "cargo test", str(self.work),
                              seen="2026-10-31")
        body = (scope.dir / "command-ledger.md").read_text(encoding="utf-8")
        self.assertLess(body.index("cargo test"), body.index("npm test"))

    def test_extract_mentions_backslash_paths_and_skips_git(self) -> None:
        hits = entities.extract_file_mentions(
            "The auth code lives in src\\auth.py for all logins.")
        self.assertTrue(any(path == "src\\auth.py" for path, _n in hits))
        self.assertEqual(
            entities.extract_file_mentions(
                "Our remote is example/demo.git for releases and more."),
            [])

    def test_shared_scope_still_fail_closed_for_entities(self) -> None:
        (self.work / ".git").mkdir()  # shared scope only exists in a repo
        store, _scope_obj = _scope(self)
        self.assertIsNotNone(store.get_scope("project-shared"))
        result = cons.file_candidate(
            {"text": "Deploy password = s3cret-value-here for releases.",
             "kind": "project-command", "command": "make deploy",
             "topic": "command-ledger", "importance": 4, "tags": [],
             "scope": "project-shared", "source": "tool", "session_id": ""},
            store)
        self.assertEqual(result["action"], "blocked-secret")


if __name__ == "__main__":
    unittest.main()
