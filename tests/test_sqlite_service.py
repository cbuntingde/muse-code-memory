"""SQLite FTS5 index, service facade, proposals, trash, shared-scope guard."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import IsolatedCase
from muse_memory import consolidate as cons
from muse_memory import lifecycle, rag
from muse_memory.service import MemoryService
from muse_memory.store import MemoryStore, ScopeStore


def _seed(scope: ScopeStore) -> None:
    scope.write_topic("deploy-process",
                      "# Deploy process\n\n- Deploy with blue-green slots on Fridays.\n",
                      meta={"importance": 4})
    scope.write_topic("pasta-recipe",
                      "# Pasta recipe\n\n- Boil water with plenty of salt first.\n",
                      meta={"importance": 2})


class SqliteIndexTest(IsolatedCase):
    def test_fts5_search_ranks_and_persists(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        _seed(scope)
        hits = rag.search_all([scope], "how do we deploy on Friday", top_k=2)
        self.assertEqual(hits[0].file, "deploy-process.md")
        self.assertTrue((scope.dir / ".rag-index.db").is_file())
        # Second load is served from the persisted database.
        idx = rag.load_or_build(scope)
        self.assertEqual(idx.search("blue-green slots")[0].file,
                         "deploy-process.md")

    def test_read_only_search_writes_no_index(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        _seed(scope)
        hits = rag.search_all([scope], "blue-green deploy",
                              top_k=2, read_only=True)
        self.assertTrue(hits)
        self.assertFalse((scope.dir / ".rag-index.db").exists())
        self.assertFalse((scope.dir / ".rag-index.json").exists())

    def test_deleted_db_rebuilds_from_topics(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        _seed(scope)
        rag.load_or_build(scope)
        (scope.dir / ".rag-index.db").unlink()
        hits = rag.search_all([scope], "blue-green deploy", top_k=1)
        self.assertEqual(hits[0].file, "deploy-process.md")


class ServiceTest(IsolatedCase):
    def test_save_recall_list_roundtrip(self) -> None:
        svc = MemoryService(self.work)
        result = svc.save("Always run the full test suite before merging.",
                          scope="project-personal", importance=4)
        self.assertEqual(result["action"], "created")
        self.assertIn("test suite", svc.recall_text("tests before merging"))
        self.assertTrue(svc.list_rows())

    def test_save_rejects_unknown_scope(self) -> None:
        svc = MemoryService(self.work)
        with self.assertRaises(ValueError):
            svc.save("A durable fact for scope testing.", scope="nope")

    def test_shared_scope_blocks_credential_like_text(self) -> None:
        (self.work / ".git").mkdir()
        svc = MemoryService(self.work)
        result = svc.save("Deploy password: fak3secretvalue for the server.",
                          scope="project-shared", importance=3)
        self.assertEqual(result["action"], "blocked-secret")
        shared = svc.store.get_scope("project-shared")
        assert shared is not None
        self.assertEqual(shared.topic_files(), [])


class SupersedeTest(IsolatedCase):
    def test_save_supersedes_archives_and_links(self) -> None:
        svc = MemoryService(self.work)
        first = svc.save("We use MySQL for session storage today.",
                         topic="database-choice", importance=3)
        old_file = first["file"]
        second = svc.save("We decided to use Postgres for session storage.",
                          topic="database-choice-postgres", importance=4,
                          supersedes=old_file)
        self.assertEqual(second.get("superseded"), old_file)
        scope = svc.store.get_scope("project-personal")
        assert scope is not None
        self.assertFalse((scope.dir / old_file).exists())
        self.assertTrue(any(p.name == old_file
                            for p in (scope.dir / "archive").iterdir()))
        new_rec = scope.read_record(scope.dir / second["file"])
        self.assertEqual(scope.file_meta(second["file"]).get("supersedes"),
                         old_file)
        self.assertIn("Postgres", new_rec.content)


class ProposalTest(IsolatedCase):
    def _queue(self, store, **over) -> None:
        cand = {"text": "A low confidence durable-ish note for proposals.",
                "kind": "note", "topic": "proposal-note", "importance": 2,
                "tags": [], "scope": "project-personal", "source": "tool",
                "session_id": ""}
        cand.update(over)
        lifecycle.queue_candidates([cand], store)

    def test_low_confidence_held_then_approved(self) -> None:
        store = MemoryStore(self.work)
        self._queue(store)
        report = lifecycle.file_pending(
            store, auto_file_importance=4, proposal_ttl_days=7)
        self.assertEqual(report["candidates"], 0)
        self.assertEqual(report["proposals_held"], 1)
        scope = store.get_scope("project-personal")
        assert scope is not None
        self.assertEqual(scope.pending_count(), 1)
        approved = lifecycle.file_pending(store, approve_all=True)
        self.assertEqual(approved["candidates"], 1)
        self.assertEqual(scope.pending_count(), 0)

    def test_expired_proposal_is_dropped(self) -> None:
        store = MemoryStore(self.work)
        self._queue(store, queued_at=time.time() - 8 * 86400)
        report = lifecycle.file_pending(
            store, auto_file_importance=4, proposal_ttl_days=7)
        self.assertEqual(report["proposals_expired"], 1)
        scope = store.get_scope("project-personal")
        assert scope is not None
        self.assertEqual(scope.pending_count(), 0)

    def test_access_log_drains_to_counts(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.write_topic("deploy", "# Deploy\n\n- Deploy on Fridays.\n")
        lifecycle.queue_access(store, "project-personal",
                               ["deploy.md", "deploy.md"])
        self.assertEqual(
            scope.read_record(scope.dir / "deploy.md").access_count, 0)
        self.assertEqual(lifecycle.drain_access(store), 1)
        self.assertEqual(
            scope.read_record(scope.dir / "deploy.md").access_count, 1)


class TrashRestoreTest(IsolatedCase):
    def test_trash_then_restore(self) -> None:
        scope = ScopeStore("project-personal", self.data_home / "mem")
        scope.write_topic("note", "# Note\n\n- A note worth keeping.\n")
        self.assertTrue(scope.trash_topic("note.md"))
        self.assertFalse((scope.dir / "note.md").exists())
        self.assertEqual(scope.trashed_files(), ["note.md"])
        self.assertTrue(scope.restore_topic("note.md"))
        self.assertTrue((scope.dir / "note.md").is_file())
        self.assertEqual(scope.trashed_files(), [])

    def test_shared_file_candidate_blocked(self) -> None:
        (self.work / ".git").mkdir()
        store = MemoryStore(self.work)
        result = cons.file_candidate(
            {"text": "The api_key = fak3secretvalue123 for deploys.",
             "topic": "leak", "scope": "project-shared"}, store)
        self.assertEqual(result["action"], "blocked-secret")


if __name__ == "__main__":
    unittest.main()
