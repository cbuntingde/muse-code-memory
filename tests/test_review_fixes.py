import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import IsolatedCase  # noqa: E402
from muse_memory import extractor  # noqa: E402
from muse_memory.store import MemoryStore  # noqa: E402


class OpenerStripTest(unittest.TestCase):
    def test_sure_prefixed_decision_captured(self) -> None:
        cands = extractor.extract_candidates(
            "Sure, we decided to use Postgres going forward "
            "for the session store.")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], "decision")

    def test_let_me_prefixed_decision_captured(self) -> None:
        cands = extractor.extract_candidates(
            "Let me check: we will use Redis for caching from now on "
            "in this project.")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], "decision")

    def test_filler_still_skipped(self) -> None:
        self.assertEqual(extractor.extract_candidates(
            "Thanks, let me check that."), [])
        self.assertEqual(extractor.extract_candidates(
            "Sure, lorem ipsum placeholder draft for testing "
            "purposes only here."), [])


class SharedBlockTest(unittest.TestCase):
    def test_hex_commit_sha_not_blocked(self) -> None:
        reasons = extractor.blocked_for_shared(
            "pinned to commit abc123def456789012345678901234567890abcd "
            "for the deploy")
        self.assertEqual(reasons, [])

    def test_real_secret_still_blocked(self) -> None:
        fake = "AKIA" + "IOSFODNN7EXAMPLE"
        self.assertIn("aws-key", extractor.blocked_for_shared(
            f"deploy key {fake} leaked"))

    def test_opaque_token_still_blocked(self) -> None:
        token = "xK9mP2qR7vT4yH6jN3bV8cF1dS5aZ0qWeR"
        self.assertIn("high-entropy-token",
                      extractor.blocked_for_shared(f"token {token} here"))


class ReadRecordTest(IsolatedCase):
    def test_missing_file_returns_empty_record(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        rec = scope.read_record(scope.dir / "gone.md")
        self.assertEqual(rec.content, "")


class AppendTopicTest(IsolatedCase):
    def test_substring_addition_is_appended(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.write_topic("deploy", "# Deploy\n\n- Our deploy window is Friday morning, not Thursday.\n")
        rec = scope.append_to_topic("deploy.md", "- Our deploy window is Friday morning")
        assert rec is not None
        self.assertIn("- Our deploy window is Friday morning\n", rec.content)
        self.assertIn("not Thursday", rec.content)

    def test_exact_repeat_is_not_duplicated(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.write_topic("deploy", "# Deploy\n\n- Our deploy window is Friday morning.\n")
        before = (scope.dir / "deploy.md").read_text(encoding="utf-8")
        scope.append_to_topic("deploy.md", "- Our deploy window is Friday morning.")
        after = (scope.dir / "deploy.md").read_text(encoding="utf-8")
        self.assertEqual(before, after)


class FileCandidateDupTest(IsolatedCase):
    def test_substring_text_is_not_duplicate(self) -> None:
        from muse_memory import consolidate as cons  # noqa: E402

        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.write_topic("deploy", "# Deploy\n\n- Our deploy window is Friday morning.\n")
        result = cons.file_candidate(
            {"text": "Friday morning", "kind": "note", "topic": "friday-note",
             "importance": 3, "tags": [], "scope": "project-personal",
             "source": "auto", "session_id": ""}, store)
        self.assertNotEqual(result["action"], "duplicate")


class SessionLookupTest(IsolatedCase):
    def _session(self, name: str) -> Path:
        day = (self.data_home / "muse" / "sessions" / "2026" / "01" / "02" / name)
        day.mkdir(parents=True, exist_ok=True)
        (day / "session.jsonl").write_text("{}\n", encoding="utf-8")
        return day / "session.jsonl"

    def test_exact_match_wins(self) -> None:
        from muse_memory import lifecycle  # noqa: E402

        want = self._session("sessionAAA")
        self._session("sessionAAAB")
        self.assertEqual(lifecycle.find_session_file("sessionAAA"), want)
        self.assertEqual(
            lifecycle.find_session_file("sessionAAAB"),
            want.parent.parent / "sessionAAAB" / "session.jsonl")

    def test_ambiguous_prefix_returns_none(self) -> None:
        from muse_memory import lifecycle  # noqa: E402

        self._session("sessionAAA")
        self._session("sessionAAAB")
        self.assertIsNone(lifecycle.find_session_file("sessionAA"))

    def test_missing_returns_none(self) -> None:
        from muse_memory import lifecycle  # noqa: E402

        self.assertIsNone(lifecycle.find_session_file("nope"))


class CliSaveDedupeTest(IsolatedCase):
    def test_second_cli_save_is_duplicate(self) -> None:
        from muse_memory.__main__ import cmd_save  # noqa: E402

        def run_save():
            args = SimpleNamespace(
                cwd=str(self.work), scopes=None,
                text="We decided to use Postgres going forward.",
                stdin=False, topic=None, scope="project-personal",
                tag=[], importance=3, auto=False)
            buf = io.StringIO()
            with redirect_stderr(io.StringIO()):
                from contextlib import redirect_stdout
                with redirect_stdout(buf):
                    code = cmd_save(args)
            return code, json.loads(buf.getvalue())

        code1, first = run_save()
        code2, second = run_save()
        self.assertEqual((code1, code2), (0, 0))
        self.assertEqual(first["file"], second["file"])
        self.assertEqual(second.get("action"), "duplicate")
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        self.assertEqual(len(scope.topic_files()), 1)


class TopKSentinelTest(IsolatedCase):
    def test_zero_top_k_uses_configured_default(self) -> None:
        from muse_memory.service import MemoryService  # noqa: E402

        svc = MemoryService(cwd=self.work)
        svc.save("We decided to use Postgres going forward.")
        default_hits = svc.recall("postgres")[3]
        zero_hits = svc.recall("postgres", top_k=0)[3]
        self.assertTrue(default_hits)
        self.assertEqual([h.chunk_id for h in zero_hits],
                         [h.chunk_id for h in default_hits])


class CreatedTitleTest(IsolatedCase):
    def test_acronym_survives_title(self) -> None:
        from muse_memory import consolidate as cons  # noqa: E402

        store = MemoryStore(self.work)
        store.ensure_all()
        result = cons.file_candidate(
            {"text": "Always use the API client for requests.",
             "kind": "rule", "topic": "api-client",
             "importance": 4, "tags": ["rule"], "scope": "project-personal",
             "source": "manual", "session_id": ""}, store)
        scope = store.get_scope("project-personal")
        assert scope is not None
        rec = scope.read_record(scope.dir / result["file"])
        self.assertIn("API", rec.title)


class ConfigWarningTest(IsolatedCase):
    def test_invalid_value_warns_on_stderr(self) -> None:
        from muse_memory import config as config_mod  # noqa: E402

        cfg_file = self.config_home / "muse" / "memory" / "config.json"
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(json.dumps({"recall_top_k": "lots"}),
                            encoding="utf-8")
        buf = io.StringIO()
        with redirect_stderr(buf):
            cfg = config_mod.load(self.work)
        self.assertEqual(cfg["recall_top_k"], 5)
        self.assertIn("recall_top_k", buf.getvalue())

    def test_malformed_json_warns_on_stderr(self) -> None:
        from muse_memory import config as config_mod  # noqa: E402

        cfg_file = self.config_home / "muse" / "memory" / "config.json"
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text("{oops", encoding="utf-8")
        buf = io.StringIO()
        with redirect_stderr(buf):
            cfg = config_mod.load(self.work)
        self.assertEqual(cfg["recall_top_k"], 5)
        self.assertTrue(buf.getvalue().strip())


class ScopeValidationTest(IsolatedCase):
    def test_unavailable_scope_lists_available(self) -> None:
        from muse_memory.service import MemoryService  # noqa: E402

        svc = MemoryService(cwd=self.work, scopes=["project-personal"])
        with self.assertRaises(ValueError) as ctx:
            svc.save("We decided to use Postgres.", scope="global")
        self.assertIn("project-personal", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
