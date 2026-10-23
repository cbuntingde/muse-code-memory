import datetime
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import ROOT, IsolatedCase  # noqa: E402
from muse_memory import lifecycle  # noqa: E402
from muse_memory.store import MemoryStore  # noqa: E402


class LifecycleTest(IsolatedCase):
    def test_queue_and_drain_pending(self) -> None:
        store = MemoryStore(self.work)
        cands = [{"text": "We decided to use Postgres.", "kind": "decision",
                  "topic": "db-choice", "importance": 4, "tags": ["decision"],
                  "scope": "project-personal", "source": "tool",
                  "session_id": "s1"}]
        self.assertEqual(lifecycle.queue_candidates(cands, store), 1)
        drained = lifecycle.drain_pending(store)
        self.assertEqual(len(drained), 1)
        self.assertEqual(lifecycle.drain_pending(store), [])

    def test_stop_checkpoint_bounds_one_run(self) -> None:
        store = MemoryStore(self.work)
        cands = [{"text": f"Durable project fact number {i} for drain cap.",
                  "kind": "fact", "topic": f"drain-cap-{i}", "importance": 4,
                  "tags": [], "scope": "project-personal", "source": "tool",
                  "session_id": ""} for i in range(80)]
        lifecycle.queue_candidates(cands, store)
        report = lifecycle.stop_checkpoint(self.work, "")
        self.assertEqual(report.get("filed"), lifecycle.STOP_DRAIN_MAX)
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        self.assertEqual(scope.pending_count(), 80 - lifecycle.STOP_DRAIN_MAX)

    def test_find_session_file_prefers_newest(self) -> None:
        from muse_memory import paths
        base = paths.muse_data_dir() / "sessions"
        old = base / "2020" / "01" / "01" / "sess-abc" / "session.jsonl"
        new = base / "2026" / "05" / "06" / "sess-abc" / "session.jsonl"
        for path in (old, new):
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
        self.assertEqual(lifecycle.find_session_file("sess-abc"), new)
        self.assertEqual(lifecycle.find_session_file("sess-a"), new)
        self.assertIsNone(lifecycle.find_session_file("missing"))
        self.assertIsNone(lifecycle.find_session_file(""))

    def test_session_end_files_and_cleans(self) -> None:
        store = MemoryStore(self.work)
        lifecycle.queue_candidates(
            [{"text": "Always run the full test suite before merging code.",
              "kind": "rule", "topic": "test-rule", "importance": 4,
              "tags": ["rule"], "scope": "project-personal", "source": "tool",
              "session_id": ""}], store)
        report = lifecycle.session_end(self.work, "")
        actions = report["consolidation"]["actions"]
        self.assertEqual(actions.get("created", 0), 1)
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        self.assertTrue((scope.dir / "test-rule.md").is_file())


class HookContractTest(IsolatedCase):
    def _run_hook(self, name: str, event: dict) -> tuple[int, str, str]:
        env = {"XDG_DATA_HOME": str(self.data_home),
               "XDG_CONFIG_HOME": str(self.config_home),
               "PATH": __import__("os").environ.get("PATH", ""),
               "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
               "USERPROFILE": __import__("os").environ.get("USERPROFILE", ""),
               "HOMEDRIVE": __import__("os").environ.get("HOMEDRIVE", ""),
               "HOMEPATH": __import__("os").environ.get("HOMEPATH", ""),
               "TEMP": __import__("os").environ.get("TEMP", ""),
               "TMP": __import__("os").environ.get("TMP", "")}
        proc = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / name)],
            input=json.dumps(event), capture_output=True, text=True,
            timeout=60, env={k: v for k, v in env.items() if v},
            cwd=str(ROOT))
        return proc.returncode, proc.stdout, proc.stderr

    def test_session_start_emits_context(self) -> None:
        code, out, _ = self._run_hook("session_start.py", {
            "hook_event_name": "SessionStart", "session_id": "abc",
            "cwd": str(self.work)})
        self.assertEqual(code, 0)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Memory digest", ctx)
        self.assertIn("muse-memory", payload["systemMessage"])
        self.assertIn("no memories stored yet", payload["systemMessage"])

    def test_session_start_reports_counts(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.write_topic("seeded-topic", "# Seeded\n\n- A seeded fact.\n")
        code, out, _ = self._run_hook("session_start.py", {
            "hook_event_name": "SessionStart", "session_id": "abc",
            "cwd": str(self.work)})
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("hookSpecificOutput", payload)
        self.assertIn("1 topics indexed", payload["systemMessage"])

    def test_user_prompt_submit_recalls(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.write_topic("deploy-process",
                          "# Deploy\n\n- Deploy with blue-green slots.\n",
                          meta={"importance": 4})
        code, out, _ = self._run_hook("user_prompt_submit.py", {
            "hook_event_name": "UserPromptSubmit", "session_id": "abc",
            "cwd": str(self.work), "prompt": "how do we deploy blue-green?"})
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("blue-green",
                      payload["hookSpecificOutput"]["additionalContext"])
        self.assertIn("muse-memory: recalled",
                      payload["systemMessage"])

    def test_user_prompt_submit_answers_time_question(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        scope.write_topic("milestone",
                          "# Milestone\n\n- Milestone completed: dark mode shipped.\n")
        noon = datetime.datetime.now(datetime.timezone.utc).replace(
            hour=12, minute=0, second=0, microsecond=0).timestamp()
        scope.set_file_meta("milestone.md", created=noon - 86400,
                            updated=noon - 86400)
        code, out, _ = self._run_hook("user_prompt_submit.py", {
            "hook_event_name": "UserPromptSubmit", "session_id": "abc",
            "cwd": str(self.work),
            "prompt": "what did we complete yesterday"})
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("milestone.md",
                      payload["hookSpecificOutput"]["additionalContext"])

    def test_post_tool_use_queues_silently(self) -> None:
        code, out, _ = self._run_hook("post_tool_use.py", {
            "hook_event_name": "PostToolUse", "session_id": "abc",
            "cwd": str(self.work), "tool_name": "edit_file",
            "tool_result": "We decided to use Postgres for sessions."})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        pending = (scope.dir / ".pending.jsonl").read_text(encoding="utf-8")
        self.assertIn("Postgres", pending)

    def test_stop_files_pending(self) -> None:
        self.test_post_tool_use_queues_silently()
        code, out, _ = self._run_hook("stop.py", {
            "hook_event_name": "Stop", "session_id": "abc",
            "cwd": str(self.work), "stop_hook_active": False})
        self.assertEqual(code, 0)
        self.assertIn("filed", out)

    def test_session_end_is_silent_success(self) -> None:
        code, out, _ = self._run_hook("session_end.py", {
            "hook_event_name": "SessionEnd", "session_id": "abc",
            "cwd": str(self.work)})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_hooks_survive_empty_stdin(self) -> None:
        for name in ("session_start.py", "user_prompt_submit.py",
                     "post_tool_use.py", "post_tool_use_failure.py",
                     "pre_compact.py", "stop.py", "session_end.py"):
            proc = subprocess.run(
                [sys.executable, str(ROOT / "hooks" / name)],
                input="", capture_output=True, text=True, timeout=60, cwd=str(ROOT))
            self.assertEqual(proc.returncode, 0, name)


class ShellCommandTest(unittest.TestCase):
    def _tool_text(self, event):
        sys.path.insert(0, str(ROOT / "hooks"))
        import _common
        return _common.tool_text(event)

    def test_json_encoded_tool_input_yields_command(self) -> None:
        from muse_memory import extractor
        _name, text = self._tool_text({
            "tool_name": "powershell",
            "tool_input": json.dumps({"command": "python3 --version",
                                      "description": "check toolchain"}),
            "tool_result": "Python 3.14.0"})
        self.assertIn("python3 --version", text)
        cands = extractor.extract_candidates(text, source="tool")
        self.assertTrue(cands)
        self.assertEqual(cands[0]["kind"], "build")

    def test_bare_string_input_is_not_command_context(self) -> None:
        _name, text = self._tool_text({
            "tool_name": "powershell",
            "tool_input": "just some prose",
            "tool_result": "Python 3.14.0"})
        self.assertNotIn("shell command", text)


class BuildMemoryTest(IsolatedCase):
    """Environment/build discovery must file promptly and recall next time."""

    def _run_hook(self, name: str, event: dict) -> tuple[int, str, str]:
        import os
        env = {"XDG_DATA_HOME": str(self.data_home),
               "XDG_CONFIG_HOME": str(self.config_home),
               "PATH": os.environ.get("PATH", ""),
               "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
               "USERPROFILE": os.environ.get("USERPROFILE", ""),
               "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
               "HOMEPATH": os.environ.get("HOMEPATH", ""),
               "TEMP": os.environ.get("TEMP", ""),
               "TMP": os.environ.get("TMP", "")}
        proc = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / name)],
            input=json.dumps(event), capture_output=True, text=True,
            timeout=60, env={k: v for k, v in env.items() if v},
            cwd=str(ROOT))
        return proc.returncode, proc.stdout, proc.stderr

    def test_version_check_queues_build_candidate(self) -> None:
        code, out, _ = self._run_hook("post_tool_use.py", {
            "hook_event_name": "PostToolUse", "session_id": "abc",
            "cwd": str(self.work), "tool_name": "powershell",
            "tool_input": {"command": "python3 --version"},
            "tool_result": "Python 3.14.0"})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        pending = (scope.dir / ".pending.jsonl").read_text(encoding="utf-8")
        self.assertIn("python3 --version", pending)

    def test_build_kind_files_without_approval(self) -> None:
        store = MemoryStore(self.work)
        lifecycle.queue_candidates(
            [{"text": "Tests run with python3 -m unittest discover -s tests.",
              "kind": "build", "topic": "build-test", "importance": 3,
              "tags": ["project", "build"], "scope": "project-personal",
              "source": "tool", "session_id": ""}], store)
        report = lifecycle.stop_checkpoint(self.work, "")
        self.assertEqual(report.get("filed"), 1)
        self.assertEqual(report.get("proposals_held"), 0)

    def test_build_memory_recalls_next_session(self) -> None:
        from muse_memory.service import MemoryService
        self.test_version_check_queues_build_candidate()
        report = lifecycle.stop_checkpoint(self.work, "")
        self.assertGreaterEqual(report.get("filed", 0), 1)
        _cleaned, _since, _until, hits = MemoryService(self.work).recall(
            "how do I build and test this project")
        self.assertTrue(hits)
        top = (hits[0].title + " " + hits[0].text).lower()
        self.assertIn("python", top)

    def test_stop_mines_recent_assistant_text(self) -> None:
        from muse_memory import paths
        day = (paths.muse_data_dir() / "sessions" / "2026" / "05" / "07"
               / "sess-mine")
        day.mkdir(parents=True)
        (day / "session.jsonl").write_text(json.dumps(
            {"payload": {"type": "text", "text":
                         "We decided to use Postgres for session storage "
                         "going forward in this project."}}) + "\n",
            encoding="utf-8")
        report = lifecycle.stop_checkpoint(self.work, "sess-mine")
        self.assertGreaterEqual(report.get("filed", 0), 1)
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        blob = "\n".join(r.content for r in scope.read_all())
        self.assertIn("Postgres", blob)

    def test_session_start_surfaces_build_memory(self) -> None:
        event = {"hook_event_name": "SessionStart", "session_id": "abc",
                 "cwd": str(self.work)}
        _code, out, _err = self._run_hook("session_start.py", event)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("Build:", ctx)
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        scope.write_topic(
            "build-and-test",
            "# Build and Test\n\n"
            "- Run the suite with python3 -m unittest discover -s tests.\n",
            meta={"importance": 4, "tags": ["project", "build"]})
        _code, out, _err = self._run_hook("session_start.py", event)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Build:", ctx)
        self.assertIn("unittest", ctx)


class McpServerTest(IsolatedCase):
    def test_tool_save_returns_confirmation(self) -> None:
        from mcp.server import tool_save  # noqa: E402

        text = tool_save({"text": "Always run the full test suite before merging.",
                          "scope": "project-personal", "importance": 4})
        self.assertIn("muse-memory saved: project-personal/", text)
        self.assertIn("(created)", text)
        self.assertIn(".md", text)

    def test_tool_save_honors_configured_default_scope(self) -> None:
        from mcp.server import tool_save  # noqa: E402

        cfg = self.config_home / "muse" / "memory" / "config.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"default_scope": "global"}),
                       encoding="utf-8")
        out = tool_save({"text": "Cross-project preference for testing saves."})
        self.assertIn("muse-memory saved: global/", out)

    def test_tool_save_rejects_unknown_scope(self) -> None:
        from mcp.server import tool_save  # noqa: E402

        out = tool_save({"text": "Something durable for testing.",
                         "scope": "nope"})
        self.assertTrue(out.startswith("error:"))

    def test_tool_list_returns_markdown_table(self) -> None:
        from mcp.server import tool_list, tool_save  # noqa: E402

        tool_save({"text": "Always run the full test suite before merging.",
                   "scope": "project-personal", "importance": 4})
        out = tool_list({})
        self.assertIn("| Scope |", out)
        self.assertIn(".md", out)
        self.assertNotIn("no memories", out.lower())

    def test_tool_list_respects_filters_and_limit(self) -> None:
        from mcp.server import tool_list, tool_save  # noqa: E402

        tool_save({"text": "Always run the full test suite before merging.",
                   "scope": "project-personal", "importance": 4})
        tool_save({"text": "Office plant is named Fern.",
                   "scope": "project-personal", "importance": 1})
        self.assertIn("no memories", tool_list({"tag": "nosuchtag"}).lower())
        out = tool_list({"min_importance": 4})
        self.assertNotIn("Fern", out)
        limited = tool_list({"limit": 1})
        self.assertIn("+1 more", limited)

    def test_initialize_list_call(self) -> None:
        import os
        env = dict(os.environ)
        script = (
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
            '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n'
            '{"jsonrpc":"2.0","id":3,"method":"tools/call",'
            '"params":{"name":"memory_save","arguments":{"text":'
            '"MCP roundtrip note for testing.","importance":2}}}\n'
            '{"jsonrpc":"2.0","id":4,"method":"tools/call",'
            '"params":{"name":"memory_recall","arguments":{"query":'
            '"MCP roundtrip testing"}}}\n'
        )
        proc = subprocess.run([sys.executable, str(ROOT / "mcp" / "server.py")],
                              input=script, capture_output=True, text=True,
                              timeout=60, env=env, cwd=str(self.work))
        self.assertEqual(proc.returncode, 0)
        lines = [json.loads(line) for line in proc.stdout.splitlines()
                 if line.strip()]
        by_id = {msg["id"]: msg for msg in lines if "id" in msg}
        self.assertIn("serverInfo", by_id[1]["result"])
        names = [t["name"] for t in by_id[2]["result"]["tools"]]
        self.assertIn("memory_recall", names)
        self.assertIn("memory_save", names)
        self.assertIn("memory_list", names)
        saved = by_id[3]["result"]["content"][0]["text"]
        self.assertIn("created", saved)
        recalled = by_id[4]["result"]["content"][0]["text"]
        self.assertIn("MCP roundtrip", recalled)


if __name__ == "__main__":
    unittest.main()
