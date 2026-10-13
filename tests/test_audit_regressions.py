import io
import json
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from helpers import IsolatedCase
from muse_memory import consolidate, extractor, lifecycle, rag
from muse_memory.store import MemoryRecord, MemoryStore, ScopeStore


class AuditRegressions(IsolatedCase):
    def test_drain_preserves_other_scopes(self):
        store = MemoryStore(self.work)
        for scope in ('global', 'project-personal'):
            lifecycle.queue_candidates([{'text': scope, 'scope': scope}] * 3, store)
        first = lifecycle.drain_pending(store, 3)
        second = lifecycle.drain_pending(store, 3)
        self.assertEqual(len(first) + len(second), 6)

    def test_topic_collision_preserves_both_facts(self):
        store = MemoryStore(self.work)
        for text in ('Zebras inhabit African grasslands.', 'Quantum particles exhibit entanglement.'):
            consolidate.file_candidate({'text': text, 'topic': 'x'}, store)
        body = store.all_records()[0].content
        self.assertIn('Zebras', body)
        self.assertIn('Quantum', body)

    def test_pin_survives_expiry_and_staleness(self):
        scope = ScopeStore('global', self.data_home / 'mem')
        scope.write_topic('pin', '- Preserve this fact.', {'pinned': True, 'expires': 1,
                                                        'updated': 1, 'importance': 1, 'source': 'auto'})
        consolidate.expire_scope(scope)
        self.assertTrue((scope.dir / 'pin.md').exists())

    def test_trim_keeps_content_when_first_bullet_exceeds_budget(self):
        scope = ScopeStore('global', self.data_home / 'mem')
        scope.write_topic('big', '- ' + 'x' * 1000 + '\n- second fact\n- third fact\n- fourth fact')
        consolidate.trim_scope(scope, 500)
        self.assertTrue(scope.read_all()[0].content.strip())

    def test_failed_manual_filing_reports_no_success(self):
        store = MemoryStore(self.work)
        with patch.object(consolidate, 'file_candidate', side_effect=OSError('disk full')):
            report = consolidate.consolidate_session([{'text': 'fact'}], store)
        self.assertEqual(report['candidates'], 0)
        self.assertEqual(report['errors'], ['disk full'])

    def test_redaction_json_and_quoted_spaces(self):
        for text in ('{"password": "fake credential value"}', 'password="fake credential value"'):
            cleaned, _ = extractor.redact_secrets(text)
            self.assertNotIn('fake', cleaned)
            self.assertEqual(extractor.redact_secrets(cleaned), (cleaned, []))

    def test_redaction_private_key_body(self):
        text = '-----BEGIN PRIVATE KEY-----\nFAKEKEYBODY\n-----END PRIVATE KEY-----'
        cleaned, _ = extractor.redact_secrets(text)
        self.assertNotIn('FAKEKEYBODY', cleaned)

    def test_long_paragraph_is_fully_indexed(self):
        rec = MemoryRecord('id', 'global', 'long.md', 'Long', 'word ' * 500 + 'uniquetail')
        chunks = rag.chunk_record(rec)
        self.assertIn('uniquetail', ''.join(c['text'] for c in chunks))

    def test_empty_index_health_is_safe(self):
        scope = ScopeStore('global', self.data_home / 'mem')
        scope.ensure()
        (scope.dir / '.rag-index.json').write_text('{}', encoding='utf-8')
        self.assertTrue(rag.index_health(scope)['stale'])

    def test_external_deletion_invalidates_rag(self):
        scope = ScopeStore('global', self.data_home / 'mem')
        scope.write_topic('old', '- Unique vanished memory.')
        scope.write_topic('new', '- Newer surviving memory.')
        rag.load_or_build(scope)
        (scope.dir / 'old.md').unlink()
        self.assertFalse(rag.load_or_build(scope).search('vanished'))

    def test_mcp_bad_method_does_not_kill_server(self):
        from mcp.server import serve
        requests = '\n'.join(json.dumps(r) for r in (
            {'jsonrpc': '2.0', 'id': 1, 'method': None},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'}))
        out = io.StringIO()
        with patch('sys.stdin', io.StringIO(requests)), patch('sys.stdout', out):
            serve()
        responses = [json.loads(s) for s in out.getvalue().splitlines()]
        self.assertEqual(responses[-1]['id'], 2)

    def test_mcp_save_rejects_invalid_arguments(self):
        from mcp.server import tool_save
        for args in ({'text': None}, {'text': 'fact', 'importance': 99},
                     {'text': 'fact', 'tags': 'rule'}):
            self.assertTrue(tool_save(args).startswith('error:'))

    def test_configured_scopes_are_honored(self):
        from muse_memory import config
        config.save_user_config({'scopes': ['global']})
        self.assertEqual(set(MemoryStore(self.work).scopes), {'global'})

    def test_capture_disabled_prevents_transcript_mining(self):
        from muse_memory import config
        config.save_user_config({'capture_enabled': False, 'auto_cleanup': False})
        with patch.object(lifecycle, 'find_session_file') as find:
            lifecycle.session_end(self.work, 'session')
        find.assert_not_called()

    def test_failed_filing_keeps_pending_for_retry(self):
        store = MemoryStore(self.work)
        lifecycle.queue_candidates([{'text': 'A durable fact.', 'topic': 'retry'}], store)
        with patch.object(consolidate, 'file_candidate', side_effect=OSError('disk full')):
            report = lifecycle.file_pending(store)
        self.assertEqual(report['candidates'], 0)
        self.assertEqual(store.get_scope('project-personal').pending_count(), 1)
        self.assertEqual(lifecycle.file_pending(store)['candidates'], 1)

    def test_archive_collision_preserves_metadata_and_content(self):
        scope = ScopeStore('global', self.data_home / 'mem')
        for text in ('old content', 'new content'):
            scope.write_topic('same', text, {'importance': 5, 'tags': ['rule']})
            self.assertTrue(scope.archive_topic('same.md'))
        records = scope.read_archive()
        self.assertEqual({r.content.strip() for r in records}, {'old content', 'new content'})
        self.assertTrue(all(r.importance == 5 and r.tags == ['rule'] for r in records))

    def test_concurrent_appends_preserve_all_content(self):
        from helpers import ROOT
        scope = ScopeStore('global', self.data_home / 'mem')
        scope.write_topic('parallel', '# Parallel')
        code = (
            'import sys; from pathlib import Path; from muse_memory.store import ScopeStore; '
            's=ScopeStore("global", Path(sys.argv[1])); '
            '[s.append_to_topic("parallel.md", "- worker " + sys.argv[2] + " item " + str(i)) '
            'for i in range(10)]')
        workers = [subprocess.Popen([sys.executable, '-c', code, str(scope.dir), str(i)],
                                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                   for i in range(3)]
        for worker in workers:
            _, err = worker.communicate(timeout=30)
            self.assertEqual(worker.returncode, 0, err.decode(errors='replace'))
        self.assertEqual(scope.read_all()[0].content.count('- worker '), 30)
