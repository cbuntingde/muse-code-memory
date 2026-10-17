"""Regression tests: capture must not file machine artifacts; hooks and MCP
must decode UTF-8; the file cap and fragment sweep must run outside SessionEnd."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import ROOT, IsolatedCase  # noqa: E402
from muse_memory import consolidate as cons  # noqa: E402
from muse_memory import extractor, lifecycle  # noqa: E402
from muse_memory.store import MemoryStore, ScopeStore  # noqa: E402


def _hook_env(data_home: Path, config_home: Path) -> dict:
    env = {"XDG_DATA_HOME": str(data_home), "XDG_CONFIG_HOME": str(config_home),
           "PATH": os.environ.get("PATH", ""),
           "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
           "USERPROFILE": os.environ.get("USERPROFILE", ""),
           "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
           "HOMEPATH": os.environ.get("HOMEPATH", ""),
           "TEMP": os.environ.get("TEMP", ""),
           "TMP": os.environ.get("TMP", "")}
    return {k: v for k, v in env.items() if v}


class ExtractorFragmentGuardTest(unittest.TestCase):
    def test_json_payload_sentence_rejected(self) -> None:
        text = ('"find": "def run(main) -> None; never fail the session", '
                '"path": "hooks/x.py"')
        self.assertEqual(extractor.extract_candidates(text), [])

    def test_line_numbered_code_rejected(self) -> None:
        text = ("Never 57| call emit() twice: stdout must hold exactly one "
                "JSON document.")
        self.assertEqual(extractor.extract_candidates(text), [])

    def test_escaped_text_rejected(self) -> None:
        # literal backslash-n (JSON-escaped payload), not a real newline
        text = ("Fixed the hook: the payload had \\n inside and stdout got "
                "two documents.")
        self.assertEqual(extractor.extract_candidates(text), [])

    def test_mojibake_rejected(self) -> None:
        text = ("Deployed the fix after the dash got mangled into \ufffd "
                "during capture last night.")
        self.assertEqual(extractor.extract_candidates(text), [])

    def test_session_path_rejected(self) -> None:
        text = ("Merged records from C:\\Users\\x\\.local\\share\\muse"
                "\\sessions\\2026 into the project store for the release.")
        self.assertEqual(extractor.extract_candidates(text), [])

    def test_clean_prose_still_captured(self) -> None:
        cands = extractor.extract_candidates(
            "We decided to use Postgres for the session store going forward.")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "decision")

    def test_python_import_dump_rejected(self) -> None:
        text = ("Hooks that must stay read-only call recall with read_only=True "
                "and queue their access signals instead of touching metadata. "
                "from muse_memory import config as config_mod")
        self.assertEqual(extractor.extract_candidates(text), [])

    def test_diff_hunk_rejected(self) -> None:
        text = ("Never merge branches @@ -12,3 +14,5 @@ without review.")
        self.assertEqual(extractor.extract_candidates(text), [])

    def test_digit_pipe_without_space_rejected(self) -> None:
        text = ("Never skip the bounded 5|Bounded retry path in hooks. 123|Listing residue is rejected too.")
        self.assertEqual(extractor.extract_candidates(text), [])

    def test_system_reminder_artifact_rejected(self) -> None:
        text = ("Always use Get-Content to inspect output. </system-reminder>")
        self.assertEqual(extractor.extract_candidates(text), [])

    def test_fragment_reason_flags_and_clears(self) -> None:
        self.assertIsNone(extractor.fragment_reason(
            "We decided to use Postgres for the session store."))
        self.assertEqual(extractor.fragment_reason('{"find": "x"}'),
                         "json-payload")


class RetireFragmentsTest(IsolatedCase):
    def test_archives_fragments_keeps_manual_and_pinned(self) -> None:
        scope = ScopeStore("project-personal", self.config_home / "mem")
        scope.write_topic("rule-find-def-run",
                          '# Rule\n\n- {"find": "def run()", "path": "x.py"}\n',
                          meta={"source": "tool"})
        scope.write_topic("plain-fact", "# Plain\n\n- Deploy on Fridays only.\n",
                          meta={"source": "tool"})
        scope.write_topic("manual-json",
                          '# Manual\n\n- {"a": "b", "c": "d"}\n',
                          meta={"source": "manual"})
        scope.write_topic("pinned-json", '# Pinned\n\n- {"find": "keep"}\n',
                          meta={"source": "tool"})
        scope.set_file_meta("pinned-json.md", pinned=True)
        report = cons.retire_fragments(scope)
        self.assertEqual(report["fragments_retired"], 1)
        self.assertFalse((scope.dir / "rule-find-def-run.md").exists())
        self.assertTrue((scope.dir / "archive" / "rule-find-def-run.md").is_file())
        self.assertTrue((scope.dir / "plain-fact.md").is_file())
        self.assertTrue((scope.dir / "manual-json.md").is_file())
        self.assertTrue((scope.dir / "pinned-json.md").is_file())

    def test_source_less_files_are_treated_as_manual(self) -> None:
        # Native Muse memory files have no meta sidecar entry; the sweep
        # must never archive them (read_record defaults source to manual).
        scope = ScopeStore("project-personal", self.config_home / "mem")
        scope.write_topic("native-style",
                          '# Native\n\n- {"find": "native file body"}\n')
        report = cons.retire_fragments(scope)
        self.assertEqual(report["fragments_retired"], 0)
        self.assertTrue((scope.dir / "native-style.md").is_file())

    def test_cleanup_reports_fragments(self) -> None:
        (self.work / ".git").mkdir()
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.write_topic("junk-json",
                          '# Junk\n\n- {"find": "x", "replace": "y"}\n',
                          meta={"source": "tool"})
        report = cons.cleanup(store, {})
        self.assertEqual(report["project-personal"]["fragments_retired"], 1)


class StopCapTest(IsolatedCase):
    def test_stop_enforces_file_cap(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        for i in range(45):
            scope.write_topic(f"topic-{i:02d}", f"# Topic {i}\n\n- value {i}\n",
                              meta={"importance": 2})
        report = lifecycle.stop_checkpoint(self.work, "s1")
        self.assertEqual(report["retired_for_cap"], 5)
        remaining = [p for p in scope.dir.glob("*.md")
                     if p.name != "MEMORY.md"]
        self.assertEqual(len(remaining), 40)


class StartupMaintenanceTest(IsolatedCase):
    def test_start_is_read_only_then_session_end_retires(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.write_topic("junk", '# Junk\n\n- {"find": "x", "path": "y.py"}\n',
                          meta={"source": "tool"})
        scope.write_topic("keep", "# Keep\n\n- Deploy on Fridays only.\n",
                          meta={"source": "tool"})
        proc = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "session_start.py")],
            input=json.dumps({"hook_event_name": "SessionStart",
                              "session_id": "abc", "cwd": str(self.work)}),
            capture_output=True, text=True, timeout=60,
            env=_hook_env(self.data_home, self.config_home), cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertIn("muse-memory", payload["systemMessage"])
        # Read-only hot path: startup must not retire or move anything.
        self.assertTrue((scope.dir / "junk.md").exists())
        self.assertFalse((scope.dir / ".rag-index.db").exists())
        # Maintenance now runs at SessionEnd instead.
        from muse_memory import lifecycle
        lifecycle.session_end(self.work, "abc")
        self.assertFalse((scope.dir / "junk.md").exists())
        self.assertTrue((scope.dir / "archive" / "junk.md").is_file())
        self.assertTrue((scope.dir / "keep.md").is_file())


class Utf8StdioTest(IsolatedCase):
    def test_hook_decodes_utf8_stdin(self) -> None:
        event = {"hook_event_name": "PostToolUse", "session_id": "s",
                 "cwd": str(self.work), "tool_name": "powershell",
                 "tool_result": ("We decided to use Postgres \u2014 the "
                                 "managed option \u2014 for sessions.")}
        proc = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "post_tool_use.py")],
            input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
            capture_output=True, timeout=60,
            env=_hook_env(self.data_home, self.config_home), cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0)
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        lines = (scope.dir / ".pending.jsonl").read_text(
            encoding="utf-8").splitlines()
        cands = [json.loads(line) for line in lines if line.strip()]
        self.assertTrue(cands)
        self.assertIn("\u2014", cands[0]["text"])
        self.assertNotIn("\u00ef\u00bf\u00bd", cands[0]["text"])

    def test_mcp_server_decodes_utf8(self) -> None:
        script = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "memory_save",
                        "arguments": {
                            "text": "We decided to use Postgres \u2014 managed option.",
                            "importance": 2}}}) + "\n"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "mcp" / "server.py")],
            input=script.encode("utf-8"), capture_output=True, timeout=60,
            env=dict(os.environ), cwd=str(self.work))
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout.decode("utf-8").splitlines()[0])
        text = payload["result"]["content"][0]["text"]
        self.assertIn("(created)", text)
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        files = [p for p in scope.dir.glob("*.md") if p.name != "MEMORY.md"]
        self.assertEqual(len(files), 1)
        saved = files[0].read_text(encoding="utf-8")
        self.assertIn("\u2014", saved)


class FailureFallbackGateTest(IsolatedCase):
    """post_tool_use_failure files a raw error note only when extraction
    found nothing AND the text is prose, not a machine payload."""

    def _run_failure_hook(self, error: str) -> None:
        event = {"hook_event_name": "PostToolUseFailure", "session_id": "s",
                 "cwd": str(self.work), "tool_name": "edit_file",
                 "error": error}
        proc = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "post_tool_use_failure.py")],
            input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
            capture_output=True, timeout=60,
            env=_hook_env(self.data_home, self.config_home), cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0)

    def _pending(self) -> list[dict]:
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        path = scope.dir / ".pending.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_payload_error_fallback_not_filed(self) -> None:
        self._run_failure_hook(
            '{"find": "def run(main)", "path": "hooks/x.py", "oldStart": 1}')
        self.assertEqual(self._pending(), [])

    def test_prose_error_fallback_still_filed(self) -> None:
        self._run_failure_hook(
            "disk quota exceeded while writing the cache file on the server")
        pend = self._pending()
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["kind"], "gotcha")
        self.assertIn("disk quota exceeded", pend[0]["text"])


if __name__ == "__main__":
    unittest.main()
