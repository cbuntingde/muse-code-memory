"""Redaction guard: secrets must never be filed as memories.

All credential samples below are fake stand-ins matching real key shapes
(AWS uses its official EXAMPLE key). They exist only to prove the patterns.
"""
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import ROOT, IsolatedCase  # noqa: E402
from muse_memory import consolidate as cons  # noqa: E402
from muse_memory import extractor  # noqa: E402
from muse_memory.__main__ import cmd_save  # noqa: E402
from muse_memory.store import MemoryStore  # noqa: E402

# Built by concatenation so no contiguous secret-shaped literal sits in the
# file (host secret-scanning would otherwise flag these obvious fakes).
FAKE_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_GHP = "ghp_" + "fakesecret0123456789abcdefghijklmnop"
FAKE_SLACK = "xoxb-" + "111222333444-5556667778888-FakeFakeFakeFakeFakeFake12"
FAKE_STRIPE = "sk_live_" + "fakesecretkey1234567890abcdef"
FAKE_JWT = ("eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            + ".fakesignature1234567890")
FAKE_BEARER = "Bearer " + "abcdefghij1234567890XYZ"
FAKE_PRIVKEY = "-----BEGIN " + "RSA PRIVATE KEY-----"
FAKE_WEBHOOK = ("https://hooks.slack.com/services/"
                + "T000/B000/XXXXXXXXXXXXXXXX")

DROP_SAMPLES = [
    (f"the aws key {FAKE_AWS} is used", "aws-key"),
    (f"github token {FAKE_GHP} for ci", "github-token"),
    (f"slack token {FAKE_SLACK} here", "slack-token"),
    (f"billing key {FAKE_STRIPE} active", "stripe-key"),
    (f"auth header {FAKE_BEARER} sent", "bearer-token"),
    (f"session jwt {FAKE_JWT} expired", "jwt"),
    (f"key block {FAKE_PRIVKEY} pasted", "private-key"),
    (f"alerts go to {FAKE_WEBHOOK} now", "slack-webhook"),
]


class RedactUnitTest(unittest.TestCase):
    def test_known_key_formats_redacted(self) -> None:
        for sample, kind in DROP_SAMPLES:
            cleaned, kinds = extractor.redact_secrets(sample)
            self.assertIn(kind, kinds, sample)
            self.assertIn(f"[redacted:{kind}]", cleaned, sample)
            for fragment in sample.split():
                if len(fragment) > 12 and " " not in fragment:
                    self.assertNotIn(fragment, cleaned, sample)

    def test_assignment_keeps_key_name_redacts_value(self) -> None:
        cleaned, kinds = extractor.redact_secrets(
            "deploy password = hunter2-vault for staging")
        self.assertIn("secret-assignment", kinds)
        self.assertIn("deploy password =", cleaned)
        self.assertNotIn("hunter2-vault", cleaned)

    def test_url_password_redacted_keeps_host(self) -> None:
        cleaned, kinds = extractor.redact_secrets(
            "primary is postgres://deploy:s3cr3t-pw@db:5432/app")
        self.assertIn("url-password", kinds)
        self.assertIn("postgres://deploy:", cleaned)
        self.assertIn("@db:5432/app", cleaned)
        self.assertNotIn("s3cr3t-pw", cleaned)

    def test_port_only_url_untouched(self) -> None:
        text = "dashboard at https://example.com:8080/status page"
        cleaned, kinds = extractor.redact_secrets(text)
        self.assertEqual(kinds, [])
        self.assertEqual(cleaned, text)

    def test_bare_security_words_untouched(self) -> None:
        for text in ("Fixed null token crash in login.",
                     "We decided to use Postgres.",
                     "The secret is that we deploy on Fridays."):
            cleaned, kinds = extractor.redact_secrets(text)
            self.assertEqual(kinds, [], text)
            self.assertEqual(cleaned, text)

    def test_redact_is_idempotent(self) -> None:
        once, kinds = extractor.redact_secrets(f"key {FAKE_GHP} here")
        twice, kinds2 = extractor.redact_secrets(once)
        self.assertEqual(once, twice)
        self.assertEqual(kinds, ["github-token"])
        self.assertEqual(kinds2, [])


class ExtractorRedactionTest(unittest.TestCase):
    def test_pasted_keys_are_dropped(self) -> None:
        text = (f"Note that the github token {FAKE_GHP} is used "
                "for CI deploys every Friday.")
        cands = extractor.extract_candidates(text, source="tool")
        self.assertEqual(cands, [])

    def test_assignment_sentences_kept_redacted(self) -> None:
        text = ("Remember that deploy password=hunter2-vault is required "
                "for staging access.")
        cands = extractor.extract_candidates(text, source="tool")
        self.assertEqual(len(cands), 1)
        self.assertNotIn("hunter2-vault", cands[0]["text"])
        self.assertIn("[redacted:secret-assignment]", cands[0]["text"])
        self.assertNotIn("hunter2", cands[0]["topic"])

    def test_topic_slugs_never_carry_secrets(self) -> None:
        text = ("Remember that deploy password=hunter2-vault is required "
                "for staging access.")
        cands = extractor.extract_candidates(text, source="tool")
        self.assertTrue(cands)
        for cand in cands:
            cleaned, kinds = extractor.redact_secrets(cand["topic"])
            self.assertEqual(kinds, [])
            self.assertEqual(cleaned, cand["topic"])


class FilingRedactionTest(IsolatedCase):
    def test_file_candidate_scrubs_raw_text(self) -> None:
        store = MemoryStore(self.work)
        store.ensure_all()
        result = cons.file_candidate(
            {"text": f"CI uses {FAKE_STRIPE} for billing.",
             "kind": "note", "topic": "billing-note", "importance": 3,
             "tags": [], "scope": "project-personal", "source": "auto",
             "session_id": ""}, store)
        scope = store.get_scope("project-personal")
        assert scope is not None
        body = (scope.dir / result["file"]).read_text(encoding="utf-8")
        self.assertNotIn("sk_live_fake", body)
        self.assertIn("[redacted:stripe-key]", body)

    def test_mcp_save_redacts_and_reports(self) -> None:
        from mcp.server import tool_save  # noqa: E402

        out = tool_save({"text": f"CI uses {FAKE_STRIPE} for billing.",
                         "scope": "project-personal", "importance": 3})
        self.assertIn("redacted", out)
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        joined = "\n".join(p.read_text(encoding="utf-8")
                           for p in scope.topic_files())
        self.assertNotIn("sk_live_fake", joined)

    def test_cli_save_redacts_direct_write(self) -> None:
        args = SimpleNamespace(cwd=str(self.work), scopes=None,
                               text=f"CI uses {FAKE_STRIPE} for billing.",
                               stdin=False, topic=None, scope="project-personal",
                               tag=[], importance=3, auto=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_save(args)
        report = json.loads(buf.getvalue())
        self.assertIn("stripe-key", report.get("redacted", []))
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        joined = "\n".join(p.read_text(encoding="utf-8")
                           for p in scope.topic_files())
        self.assertNotIn("sk_live_fake", joined)

    def test_failure_hook_fallback_redacts(self) -> None:
        env = {"XDG_DATA_HOME": str(self.data_home),
               "XDG_CONFIG_HOME": str(self.config_home),
               "PATH": __import__("os").environ.get("PATH", ""),
               "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
               "TEMP": __import__("os").environ.get("TEMP", ""),
               "TMP": __import__("os").environ.get("TMP", "")}
        proc = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "post_tool_use_failure.py")],
            input=json.dumps({
                "hook_event_name": "PostToolUseFailure", "session_id": "abc",
                "cwd": str(self.work), "tool_name": "powershell",
                "tool_error": f"E_CONN {FAKE_STRIPE} rejected by gateway"}),
            capture_output=True, text=True, timeout=60,
            env={k: v for k, v in env.items() if v}, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0)
        scope = MemoryStore(self.work).get_scope("project-personal")
        assert scope is not None
        pending = (scope.dir / ".pending.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("sk_live_fake", pending)
        self.assertIn("[redacted:stripe-key]", pending)


if __name__ == "__main__":
    unittest.main()
