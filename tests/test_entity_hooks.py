"""Hook wiring for entity memory: capture, bootstrap, injection."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import ROOT, IsolatedCase  # noqa: E402
from muse_memory import entities  # noqa: E402
from muse_memory import lifecycle  # noqa: E402
from muse_memory.service import MemoryService  # noqa: E402
from muse_memory.store import MemoryStore  # noqa: E402


def run_hook(name: str, event: dict, case: IsolatedCase):
    env = {"XDG_DATA_HOME": str(case.data_home),
           "XDG_CONFIG_HOME": str(case.config_home),
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


def pending_kinds(case: IsolatedCase) -> list[str]:
    kinds = []
    scope = MemoryStore(case.work).get_scope("project-personal")
    assert scope is not None
    pending = scope.dir / ".pending.jsonl"
    if not pending.is_file():
        return kinds
    for line in pending.read_text(encoding="utf-8").splitlines():
        try:
            kinds.append(json.loads(line).get("kind"))
        except ValueError:
            continue
    return kinds


class EntityHookTest(IsolatedCase):
    def test_post_tool_use_queues_shell_command(self) -> None:
        code, _out, _err = run_hook("post_tool_use.py", {
            "tool_name": "powershell",
            "tool_input": {"command": "npm test"},
            "tool_response": "ok",
            "cwd": str(self.work), "session_id": "s1"}, self)
        self.assertEqual(code, 0)
        self.assertIn("project-command", pending_kinds(self))

    def test_post_tool_use_queues_touched_file(self) -> None:
        code, _out, _err = run_hook("post_tool_use.py", {
            "tool_name": "write_file",
            "tool_input": {"filePath": "src/auth.py"},
            "tool_response": "ok",
            "cwd": str(self.work), "session_id": "s1"}, self)
        self.assertEqual(code, 0)
        self.assertIn("project-file", pending_kinds(self))

    def test_post_tool_use_ignores_read_touches(self) -> None:
        code, _out, _err = run_hook("post_tool_use.py", {
            "tool_name": "read_file",
            "tool_input": {"filePath": "src/auth.py"},
            "tool_response": "ok",
            "cwd": str(self.work), "session_id": "s1"}, self)
        self.assertEqual(code, 0)
        self.assertNotIn("project-file", pending_kinds(self))

    def test_post_tool_use_ignores_plain_chatter(self) -> None:
        code, _out, _err = run_hook("post_tool_use.py", {
            "tool_name": "powershell",
            "tool_input": {"command": "echo hello"},
            "tool_response": "hello",
            "cwd": str(self.work), "session_id": "s1"}, self)
        self.assertEqual(code, 0)
        self.assertNotIn("project-command", pending_kinds(self))

    def test_session_start_bootstraps_then_injects(self) -> None:
        (self.work / "pyproject.toml").write_text("[project]\n",
                                                  encoding="utf-8")
        code, out, _err = run_hook("session_start.py", {
            "hook_event_name": "SessionStart", "session_id": "abc",
            "cwd": str(self.work)}, self)
        self.assertEqual(code, 0)
        # Bootstrap queued the scan; Stop files it; next start injects it.
        self.assertIn("project-profile", pending_kinds(self))
        lifecycle.stop_checkpoint(self.work, "")
        code, out, _err = run_hook("session_start.py", {
            "hook_event_name": "SessionStart", "session_id": "abc",
            "cwd": str(self.work)}, self)
        self.assertEqual(code, 0)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Project working set", ctx)
        self.assertIn("pyproject.toml", ctx)

    def test_user_prompt_submit_injects_without_rag_hit(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        entities.note_command(scope, "npm test", str(self.work))
        code, out, _err = run_hook("user_prompt_submit.py", {
            "prompt": "what is the weather like today",
            "cwd": str(self.work), "session_id": "s1"}, self)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Project working set", ctx)
        self.assertIn("npm test", ctx)

    def test_service_working_set_lists_provenance(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        scope = store.get_scope("project-personal")
        assert scope is not None
        entities.note_command(scope, "npm test", str(self.work))
        svc = MemoryService(self.work)
        result = svc.working_set()
        self.assertIn("npm test", result["text"])
        self.assertIn(("project-personal", "command-ledger.md"),
                      result["files"])


if __name__ == "__main__":
    unittest.main()
