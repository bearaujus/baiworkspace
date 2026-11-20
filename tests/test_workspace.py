"""Offline behavior tests. All workspaces, credentials and repos are synthetic."""
from __future__ import annotations

import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
import zipfile

from bai import backup, credentials, memory, tooling
from bai.cli import main
from bai.core import Error, Workspace, atomic, contained, digest, encoded, read_json, relative, write_json

PROJECT = Path(__file__).resolve().parents[1]
DUMMY = 'synthetic-test-only/value+123456'


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bai-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'clone with spaces'
        self.ws = Workspace(self.root)
        self.ws.init()

    def repo(self, name):
        path = self.base / name
        path.mkdir()
        self.ws.register_repo(name, path, f'{name} service', aliases=[name + '-alias'])
        return path

    def task(self, name='test-task'):
        return self.ws.create('tasks', name, 'Investigate service')

    def learning(self, name='service-flow', repos=(), kind='learning'):
        return self.ws.create('knowledge', name, 'Service request flow', repos, kind)

    def set_secret(self, key='SERVICE_TOKEN', value=DUMMY, profile='dev'):
        with patch('bai.credentials.prompt', return_value=value):
            return credentials.setup(self.ws, profile, [key], update=True)

    def tool(self, source='print("ok")', name='test-tool', secrets=None, effects='read-only'):
        entry = self.ws.path(f'tools/{name}/main.py')
        atomic(entry, source.encode())
        manifest = {'schema_version': 1, 'id': name, 'description': 'Lookup service status', 'keywords': ['service', 'status'],
                    'runtime': 'python', 'entry': f'.bai/tools/{name}/main.py', 'arguments': [], 'dependencies': [],
                    'inputs': 'Test arguments', 'outputs': 'Test result', 'effects': effects, 'secrets': secrets or {},
                    'verification': 'Offline unit test', 'recovery': 'Read current state before retrying.'}
        tooling.register(self.ws, manifest)
        return manifest

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['--root', str(self.root), *args])
        return code, out.getvalue(), err.getvalue()


class StorageTests(Fixture):
    def test_init_is_idempotent_and_keeps_personal_content(self):
        atomic(self.ws.path('preferences.md'), b'Prefer concise responses.\n')
        self.assertFalse(self.ws.init()['initialized'])
        self.assertEqual(self.ws.path('preferences.md').read_text(), 'Prefer concise responses.\n')

    def test_repo_alias_path_and_rebinding(self):
        path = self.repo('ticket-api')
        self.assertEqual(self.ws.resolve_repo('ticket-api-alias'), 'ticket-api')
        self.assertEqual(self.ws.resolve_repo(str(path / 'src')), 'ticket-api')
        other = self.base / 'moved repo'
        other.mkdir()
        self.ws.register_repo('ticket-api', other, rebind=True)
        self.assertEqual(self.ws.repo_path('ticket-api'), other.resolve())
        self.assertEqual(self.ws.require()['repositories']['ticket-api']['summary'], 'ticket-api service')

    def test_repo_resolution_prefers_ids_and_reports_conflicting_aliases(self):
        self.ws.register_repo('ticket-api', self.root, aliases=['service', 'policy-config'])
        nested = self.root / 'policy'
        nested.mkdir()
        self.ws.register_repo('policy-config', nested, aliases=['service'])
        self.assertEqual(self.ws.resolve_repo('POLICY-CONFIG'), 'policy-config')
        with self.assertRaisesRegex(Error, 'ambiguous.*policy-config.*ticket-api'):
            self.ws.resolve_repo('service')
        self.assertEqual(self.ws.resolve_repo(nested / 'README.md'), 'policy-config')
        with contextlib.chdir(self.root):
            self.assertIsNone(self.ws.resolve_repo('wrong-repo'))
            self.assertEqual(self.ws.resolve_repo('.'), 'ticket-api')
            self.assertEqual(self.ws.resolve_repo('./policy'), 'policy-config')

    def test_task_update_accepts_optional_fields_missing_from_v1_record(self):
        task = self.task()
        for key in ('related_tasks', 'historical', 'origin'):
            task.pop(key)
        write_json(self.ws.record_path('tasks', task['id']), task)
        self.ws.create_topic('tickets', 'Ticketing', 'Maintain ticket request flow', ['ticket work'])
        revised = self.ws.update('tasks', task['id'], {'topic': 'ticket work', 'aliases': ['repair tickets'],
                                 'done_when': 'Confirm the response code.', 'waiting_on': 'user', 'status': 'waiting',
                                 'related_tasks': [], 'historical': False, 'origin': {}}, 1)
        self.assertEqual(revised['topic'], 'tickets')
        self.assertEqual(revised['waiting_on'], 'user')
        self.assertEqual(revised['done_when'], 'Confirm the response code.')
        self.assertEqual(revised['revision'], 2)
        self.assertEqual(read_json(self.ws.path('history/tasks-test-task-r1.json')), task)
        with self.assertRaisesRegex(Error, 'waiting_on'):
            self.ws.update('tasks', task['id'], {'waiting_on': 'agent'}, 2)
        self.assertIsNone(self.ws.update('tasks', task['id'], {'topic': None}, 2)['topic'])

    def test_task_update_retries_after_history_is_written(self):
        task = self.task()
        path = self.ws.record_path('tasks', task['id'])

        def fail_record_write(target, record):
            if target == path:
                raise OSError('record update interrupted')
            write_json(target, record)

        change = {'summary': 'First reviewed decision'}
        with patch('bai.core.write_json', side_effect=fail_record_write), self.assertRaises(OSError):
            self.ws.update('tasks', task['id'], change, 1)
        history = self.ws.path('history/tasks-test-task-r1.json')
        self.assertEqual(read_json(history), task)
        self.assertEqual(read_json(path), task)
        with patch('bai.core.write_json', wraps=write_json) as writes:
            revised = self.ws.update('tasks', task['id'], change, 1)
        writes.assert_called_once_with(path, revised)
        self.assertEqual(revised['revision'], 2)
        with self.assertRaisesRegex(Error, 'Revision changed'):
            self.ws.update('tasks', task['id'], change, 1)
        atomic(self.ws.path('history/tasks-test-task-r2.json'), encoded(task))
        with self.assertRaisesRegex(Error, 'history conflicts'):
            self.ws.update('tasks', task['id'], {'summary': 'Conflicting history'}, 2)
        self.assertEqual(read_json(path), revised)

    def test_topic_cli_requires_current_open_set_and_reports_history(self):
        self.repo('ticket-api')
        self.repo('policy-config')
        self.learning('ticket-requirements')

        def call(*args):
            code, output, error = self.cli(*args, '--json')
            self.assertEqual(code, 0, error)
            return json.loads(output)

        topic = call('topic', 'add', 'tickets', '--title', 'Ticketing', '--purpose', 'Maintain ticketing',
                     '--aliases', 'ticket service', '--reference-repos', 'policy-config',
                     '--implementation-repos', 'ticket-api', '--requirements', 'ticket-requirements')
        self.assertEqual(topic['reference_repos'], ['policy-config'])
        self.assertEqual(topic['implementation_repos'], ['ticket-api'])
        self.assertEqual(topic['requirements'], ['ticket-requirements'])
        self.assertEqual(call('topic', 'list')['topics'], [topic])
        initial = call('topic', 'show', 'ticket service')
        self.assertEqual(initial['tasks'], [])
        token = initial['open_set']
        self.assertEqual(token, call('task', 'list', '--open', '--topic', 'tickets')['open_set'])

        new_args = ('task', 'new', 'first-ticket', '--topic', 'ticket service', '--title', 'Fix ticket requests')
        code, _, error = self.cli(*new_args, '--json')
        self.assertEqual(code, 2)
        self.assertIn('--expected-open-set', json.loads(error)['error'])
        self.assertFalse(self.ws.path('tasks/first-ticket.md').exists())
        created = call(*new_args, '--done-when', 'Verify the reply.', '--aliases', 'ticket outage',
                       '--expected-open-set', token)
        self.assertEqual(created['topic'], 'tickets')
        self.assertEqual(created['aliases'], ['ticket outage'])
        self.assertEqual(created['done_when'], 'Verify the reply.')
        code, _, error = self.cli('task', 'new', 'second-ticket', '--title', 'Another ticket',
                                  '--topic', 'tickets', '--expected-open-set', token, '--json')
        self.assertEqual(code, 2)
        conflict = json.loads(error)
        self.assertIn('open task set changed', conflict['error'])
        self.assertEqual(conflict['topic'], 'tickets')
        self.assertEqual(conflict['candidate_count'], 1)
        self.assertEqual(conflict['candidates'][0]['id'], created['id'])
        self.assertEqual(conflict['candidates'][0]['done_when'], 'Verify the reply.')
        self.assertFalse(self.ws.path('tasks/second-ticket.md').exists())
        self.assertEqual(call('topic', 'show', 'tickets')['tasks'], [created])
        self.ws.update('tasks', created['id'], {'status': 'complete'}, 1)
        shown = call('topic', 'show', 'tickets', '--history')
        self.assertEqual(shown['open_set'], token)
        self.assertEqual(shown['tasks'], [])
        self.assertEqual([task['id'] for task in shown['history']], [created['id']])

        changes = self.ws.path('artifacts/topic-patch.json')
        write_json(changes, {'purpose': 'Diagnose ticket requests', 'aliases': ['ticket work']})
        revised = call('topic', 'update', 'ticket service', '--file', str(changes), '--expected-revision', '1')
        self.assertEqual(call('topic', 'show', 'ticket work')['revision'], 2)
        self.assertEqual(revised['purpose'], 'Diagnose ticket requests')
        self.assertEqual(read_json(self.ws.path('history/topics-tickets-r1.json')), topic)

    def test_simultaneous_guarded_task_creators_see_the_winner(self):
        self.ws.create_topic('tickets', 'Ticketing', 'Maintain ticketing')
        token = self.ws.open_set([], 'tickets')
        barrier = Barrier(4)

        def create(index):
            barrier.wait(timeout=5)
            return subprocess.run([sys.executable, '-B', str(PROJECT / 'bai.py'), '--root', str(self.root),
                                   'task', 'new', f'ticket-{index}', '--title', f'Ticket repair {index}',
                                   '--topic', 'tickets', '--expected-open-set', token, '--json'],
                                  capture_output=True, text=True, timeout=10)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(create, range(4)))
        self.assertEqual(sorted(r.returncode for r in results), [0, 2, 2, 2], results)
        winner = next(json.loads(r.stdout) for r in results if r.returncode == 0)
        records, warnings = self.ws.records('tasks')
        self.assertEqual(records, [winner])
        self.assertEqual(warnings, [])
        for result in results:
            if result.returncode == 2:
                self.assertEqual(json.loads(result.stderr)['candidates'][0]['id'], winner['id'])

    def test_compare_and_swap_preserves_newer_decision(self):
        self.task()
        self.ws.update('tasks', 'test-task', {'summary': 'Latest decision'}, 1)
        with self.assertRaises(Error):
            self.ws.update('tasks', 'test-task', {'summary': 'Stale decision'}, 1)
        self.assertEqual(self.ws.record('tasks', 'test-task')['summary'], 'Latest decision')
        self.assertTrue(self.ws.path('history/tasks-test-task-r1.json').is_file())

    def test_independent_tasks_and_writer_lock(self):
        self.task('first')
        self.task('second')
        with self.ws.lock():
            with self.assertRaises(Error):
                self.ws.update('tasks', 'first', {'summary': 'Blocked writer'}, 1)
        self.ws.update('tasks', 'first', {'summary': 'First updated'}, 1)
        self.assertEqual(self.ws.record('tasks', 'second')['revision'], 1)
        self.assertFalse(self.ws.path('write.lock').exists())

    def test_invalid_update_does_not_change_record(self):
        self.task()
        before = digest(self.ws.record_path('tasks', 'test-task'))
        for patch_data in ({'revision': 5}, {'status': 'deployed'}, {'surprise': 3}, {'repos': ['unregistered']}):
            with self.assertRaises(Error):
                self.ws.update('tasks', 'test-task', patch_data, 1)
        self.assertEqual(before, digest(self.ws.record_path('tasks', 'test-task')))

    def test_orphan_narrative_is_never_overwritten(self):
        atomic(self.ws.path('tasks/test-task.md'), b'Existing user narrative')
        with self.assertRaises(Error):
            self.task()
        self.assertEqual(self.ws.path('tasks/test-task.md').read_text(), 'Existing user narrative')

    def test_unicode_paths_and_cli_json(self):
        path = self.base / 'repository-\u03bb'
        path.mkdir()
        code, output, error = self.cli('repo', 'add', 'unicode-repo', '--path', str(path), '--summary', 'Unicode \u03bb', '--json')
        self.assertEqual(code, 0, error)
        self.assertIn('\u03bb', json.loads(output)['path'])

    def test_portable_paths_reject_traversal_and_windows_devices(self):
        for name in ('../private', '/tmp/x', 'C:/secret', 'a\\b', 'a/../b', 'a//b', 'NUL.txt', 'a/x:stream', 'a/file.'):
            with self.subTest(name=name), self.assertRaises(Error):
                relative(name)
        self.assertEqual(str(relative('tools/example/main.py')), 'tools/example/main.py')

    def test_symlink_cannot_pull_external_data_into_memory(self):
        outside = self.base / 'outside.md'
        outside.write_text('outside data')
        link = self.ws.path('knowledge/escape.md')
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('Symlink creation unavailable on this Windows account.')
        with self.assertRaises(Error):
            contained(self.ws.data, 'knowledge/escape.md')

    def test_json_cli_error_and_fresh_root_from_other_cwd(self):
        code, _, err = self.cli('task', 'show', 'absent', '--json')
        self.assertEqual(code, 2)
        self.assertIn('error', json.loads(err))
        proc = subprocess.run([sys.executable, '-B', str(PROJECT / 'bai.py'), '--root', str(self.root), 'status', '--json'],
                              cwd=self.base, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['tasks'], [])


class MemoryTests(Fixture):
    def test_connected_system_returns_other_repository_briefs(self):
        for name in ('ticket-api', 'policy-config', 'cloud-ops'):
            self.repo(name)
        self.learning(repos=['ticket-api', 'policy-config', 'cloud-ops'], kind='system')
        self.ws.update('knowledge', 'service-flow', {'status': 'current', 'summary': 'Tickets use policy; direct maintenance is a separate path.',
            'relationships': [{'from': 'policy-config', 'to': 'ticket-api', 'relation': 'configures'}]}, 1)
        result = memory.context(self.ws, 'tickets', 'ticket-api')
        ids = {(x['kind'], x['id']) for x in result['results']}
        for name in ('ticket-api', 'policy-config', 'cloud-ops'):
            self.assertIn(('repo', name), ids)
        self.assertIn(('system', 'service-flow'), ids)

    def test_changed_source_requires_review(self):
        repo = self.repo('ticket-api')
        (repo / 'router.py').write_text('version = 1\n')
        self.learning(repos=['ticket-api'])
        self.ws.update('knowledge', 'service-flow', {'status': 'current', 'sources': ['ticket-api:router.py']}, 1)
        memory.checkpoint(self.ws, 'knowledge', 'service-flow', 2)
        self.assertEqual(memory.review(self.ws)['findings'], [])

        def result():
            return next(e for e in memory.context(self.ws, 'service flow')['results'] if e['id'] == 'service-flow')

        original = result()
        self.assertNotIn('review_needed', original)
        (repo / 'router.py').write_text('version = 2\n')
        self.assertEqual(memory.review(self.ws)['findings'][0]['id'], 'service-flow')
        changed = result()
        self.assertIn('changed', changed['review_needed'])
        self.assertEqual(changed['status'], 'current')
        self.assertEqual(changed['provenance'], original['provenance'])
        (repo / 'router.py').unlink()
        self.assertIn('unavailable', result()['review_needed'])

    def test_metadata_and_narrative_changes_are_detected(self):
        self.task()
        self.ws.update('tasks', 'test-task', {'scope': 'Local inspection', 'reuse_decision': 'No script required.'}, 1)
        memory.checkpoint(self.ws, 'tasks', 'test-task', 2)
        self.assertFalse(any('handoff review' in w for w in memory.doctor(self.ws)['warnings']))
        atomic(self.ws.path('tasks/test-task.md'), b'Changed scope and evidence\n')
        self.assertTrue(any('handoff review' in w for w in memory.doctor(self.ws)['warnings']))
        result = memory.context(self.ws, 'changed scope')
        self.assertIn('test-task', [r['id'] for r in result['results']])

    def test_checkpoint_requires_reviewed_scope_reuse_and_valid_references(self):
        self.task()
        with self.assertRaises(Error):
            memory.checkpoint(self.ws, 'tasks', 'test-task', 1)
        self.ws.update('tasks', 'test-task', {'scope': 'Read-only inspection', 'reuse_decision': 'New script because existing tool does not fit.', 'tools': ['missing']}, 1)
        with self.assertRaises(Error):
            memory.checkpoint(self.ws, 'tasks', 'test-task', 2)

    def test_checkpoint_cli_reports_committed_revision_when_postcommit_steps_fail(self):
        task = self.task()
        self.ws.update('tasks', task['id'], {'scope': 'Read-only inspection',
                       'reuse_decision': 'No script required.'}, 1)
        self.ws.path('cache/index.json').mkdir()
        with patch('bai.memory.review', side_effect=OSError('review unavailable')):
            code, output, error = self.cli('checkpoint', task['id'], '--expected-revision', '2', '--json')
        self.assertEqual(code, 0, error)
        result = json.loads(output)
        self.assertEqual(result['revision'], 3)
        self.assertTrue(result['checkpointed'])
        self.assertFalse(result['cache_refreshed'])
        self.assertFalse(result['memory_review_completed'])
        self.assertEqual(len(result['warnings']), 2)
        current = self.ws.record('tasks', task['id'])
        self.assertEqual(current['revision'], 3)
        self.assertEqual(current['reviewed'], memory.review_hashes(self.ws, 'tasks', current))

    def test_context_never_reads_credentials_raw_artifacts_or_history(self):
        self.set_secret()
        atomic(self.ws.path('artifacts/raw.md'), b'raw-only-sentinel')
        atomic(self.ws.path('history/old.md'), b'history-only-sentinel')
        with patch('bai.credentials.store', side_effect=AssertionError('Do not load credentials for context')):
            for query in (DUMMY, 'raw-only-sentinel', 'history-only-sentinel'):
                self.assertEqual(memory.context(self.ws, query)['results'], [])

    def test_invalid_record_does_not_hide_valid_memory(self):
        self.task()
        atomic(self.ws.path('tasks/broken.json'), b'{bad')
        result = memory.context(self.ws, 'Investigate service')
        self.assertIn('test-task', [r['id'] for r in result['results']])
        self.assertTrue(result['warnings'])
        self.assertFalse(memory.doctor(self.ws)['ok'])

    def test_supersession_preserves_history_and_demotes_old_claim(self):
        for name in ('old-flow', 'new-flow'):
            self.learning(name)
        self.ws.update('knowledge', 'old-flow', {'status': 'current', 'summary': 'Legacy service routing'}, 1)
        self.ws.update('knowledge', 'new-flow', {'status': 'current', 'summary': 'Current service routing', 'supersedes': ['old-flow']}, 1)
        result = memory.context(self.ws, 'service routing')
        ids = [x['id'] for x in result['results'] if x['kind'] == 'learning']
        self.assertLess(ids.index('new-flow'), ids.index('old-flow'))
        self.assertEqual(next(x['status'] for x in result['results'] if x['id'] == 'old-flow'), 'superseded')

    def test_sources_cannot_reference_credentials(self):
        for source in ('workspace:private/secrets.json', 'workspace:artifacts/raw.md', 'ticket-api:.env', 'ticket-api:config/credentials.json'):
            with self.assertRaises(Error):
                memory.source_path(self.ws, source)

    def test_tool_retrieval_and_greeting(self):
        result = memory.context(self.ws, 'csv input rows')
        self.assertIn('csv-summary', [e['id'] for e in result['results'] if e['kind'] == 'tool'])
        self.assertIn('no historical operations', memory.context(self.ws, 'hello')['message'])

    def test_context_budget_is_bounded(self):
        for i in range(15):
            self.learning(f'flow-{i}')
            self.ws.update('knowledge', f'flow-{i}', {'summary': 'service ' * 200}, 1)
        result = memory.context(self.ws, 'service', limit=20, budget=2000)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 2000)
        self.assertGreater(result['omitted'], 0)

    def test_full_chat_handoff_scenario_uses_existing_tool_and_learning(self):
        self.repo('ticket-api')
        self.task()
        self.learning(repos=['ticket-api'])
        atomic(self.ws.path('tasks/test-task.md'), b'# Evidence\nUser explained ticket routing; live service unverified.\n')
        self.ws.update('knowledge', 'service-flow', {'status': 'current', 'sources': ['workspace:tasks/test-task.md'],
                       'summary': 'Ticket API routes requests using policy configuration.'}, 1)
        memory.checkpoint(self.ws, 'knowledge', 'service-flow', 2)
        self.ws.update('tasks', 'test-task', {'scope': 'Explain flow and summarize a local CSV.', 'reuse_decision': 'Reused csv-summary.',
                       'tools': ['csv-summary'], 'learnings': ['service-flow'], 'status': 'complete', 'summary': 'Flow recorded and CSV structure checked.'}, 1)
        result = memory.checkpoint(self.ws, 'tasks', 'test-task', 2)
        self.assertEqual(result['memory_review']['findings'], [])
        fresh = Workspace(self.root)
        contexts = memory.context(fresh, 'ticket routing CSV', 'ticket-api')['results']
        self.assertIn('service-flow', [x['id'] for x in contexts])
        self.assertIn('csv-summary', [x['id'] for x in contexts])
        self.assertEqual(fresh.record('tasks', 'test-task')['deployment'], 'Not performed.')

    def test_superseded_memory_id_does_not_retire_same_named_tool(self):
        self.learning('csv-summary')
        self.learning('new-memory')
        self.ws.update('knowledge', 'new-memory', {'status': 'current', 'supersedes': ['csv-summary']}, 1)
        results = memory.context(self.ws, 'CSV input')['results']
        self.assertEqual(next(x['status'] for x in results if x['kind'] == 'tool' and x['id'] == 'csv-summary'), 'current')


class CredentialAndToolTests(Fixture):
    def test_setup_skips_existing_and_set_rotates(self):
        self.set_secret()
        with patch('bai.credentials.prompt', side_effect=AssertionError('Must not reprompt')):
            self.assertEqual(credentials.setup(self.ws, 'dev', ['SERVICE_TOKEN'])['changed_keys'], [])
        self.set_secret(value='synthetic-rotated')
        self.assertEqual(credentials.values(self.ws, 'dev', ['SERVICE_TOKEN']), {'SERVICE_TOKEN': 'synthetic-rotated'})

    def test_listing_and_removal_never_show_values(self):
        self.tool(secrets={'SERVICE_TOKEN': 'Test service token'})
        self.set_secret()
        listing = credentials.listing(self.ws, 'dev')
        self.assertNotIn(DUMMY, json.dumps(listing))
        self.assertIn('test-tool', next(k['tools'] for k in listing['keys'] if k['key'] == 'SERVICE_TOKEN'))
        removed = credentials.remove(self.ws, 'dev', 'SERVICE_TOKEN', yes=True)
        self.assertTrue(removed['removed'])
        with self.assertRaises(Error):
            credentials.values(self.ws, 'dev', ['SERVICE_TOKEN'])

    def test_missing_profile_does_not_fall_back(self):
        self.set_secret()
        with self.assertRaises(Error):
            credentials.values(self.ws, 'production', ['SERVICE_TOKEN'])

    def test_noninteractive_and_echoing_input_fail_without_save(self):
        with patch('sys.stdin.isatty', return_value=False):
            with self.assertRaises(Error):
                credentials.setup(self.ws, 'dev', ['SERVICE_TOKEN'])
        self.assertFalse(self.ws.path('private/secrets.json').exists())
        import getpass
        with patch('sys.stdin.isatty', return_value=True), patch('getpass.getpass', side_effect=getpass.GetPassWarning):
            with self.assertRaises(Error):
                credentials.prompt('SERVICE_TOKEN')

    def test_cancelled_multi_key_input_is_atomic(self):
        with patch('bai.credentials.prompt', side_effect=[DUMMY, KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                credentials.setup(self.ws, 'dev', ['FIRST', 'SECOND'])
        self.assertFalse(self.ws.path('private/secrets.json').exists())

    def test_concurrent_secret_change_is_not_overwritten(self):
        def concurrent(key):
            write_json(self.ws.path('private/secrets.json'), {'schema_version': 1, 'profiles': {'dev': {'OTHER': 'synthetic-other'}}})
            return DUMMY
        with patch('bai.credentials.prompt', side_effect=concurrent), self.assertRaises(Error):
            credentials.setup(self.ws, 'dev', ['SERVICE_TOKEN'])
        self.assertEqual(list(credentials.store(self.ws)['profiles']['dev']), ['OTHER'])

    def test_runner_injects_declared_profile_keys_and_redacts(self):
        self.tool('import os,sys\nfrom urllib.parse import quote\nv=os.environ["SERVICE_TOKEN"]\nprint(v)\nprint(quote(v,safe=""),file=sys.stderr)\nprint("extra="+str("UNRELATED_SECRET" in os.environ))\n', secrets={'SERVICE_TOKEN': 'Test token'})
        self.set_secret()
        self.set_secret(key='OTHER_TOKEN')
        with patch.dict(os.environ, {'UNRELATED_SECRET': 'synthetic-parent-secret'}):
            result = tooling.run(self.ws, 'test-tool', [], 'dev')
        self.assertEqual(result['exit_code'], 0)
        self.assertNotIn(DUMMY, json.dumps(result))
        self.assertIn('[REDACTED]', result['stdout'])
        self.assertIn('[REDACTED]', result['stderr'])
        self.assertIn('extra=False', result['stdout'])

    def test_runner_arguments_are_literal_and_exit_propagates(self):
        self.tool('import sys\nprint(repr(sys.argv[1:]))\nsys.exit(7)\n')
        args = ['a b', '$(echo unsafe)', ';echo bad', '--json', 'value"quote']
        code, out, _ = self.cli('tools', 'run', 'test-tool', '--json', '--', *args)
        self.assertEqual(code, 7)
        result = json.loads(out)
        for value in args:
            self.assertIn(value, result['stdout'])

    def test_external_write_and_retired_tool_require_explicit_intent(self):
        manifest = self.tool(effects='external-write')
        with self.assertRaises(Error):
            tooling.run(self.ws, 'test-tool', [], 'dev')
        self.assertEqual(tooling.run(self.ws, 'test-tool', [], 'dev', execute=True)['exit_code'], 0)
        manifest['replaced_by'] = 'replacement'
        tooling.register(self.ws, manifest, replace=True)
        with self.assertRaises(Error):
            tooling.run(self.ws, 'test-tool', [], 'dev', execute=True)

    def test_local_tool_updates_preserve_builtin_protection_on_native_paths(self):
        manifest = self.tool()
        manifest['description'] = 'Updated local tool contract'
        tooling.register(self.ws, manifest, replace=True)
        self.assertEqual(tooling.get(self.ws, 'test-tool')['description'], manifest['description'])
        builtin = {k: v for k, v in tooling.get(self.ws, 'csv-summary').items() if not k.startswith('_')}
        with self.assertRaises(Error):
            tooling.register(self.ws, builtin, replace=True)

    def test_runner_timeout_reports_unknown_outcome(self):
        self.tool('import time\ntime.sleep(5)\n')
        result = tooling.run(self.ws, 'test-tool', [], 'dev', timeout=.05)
        self.assertEqual(result['exit_code'], 124)
        self.assertIn('unknown', result['outcome'])

    def test_truncated_output_does_not_leak_a_split_secret(self):
        self.tool('import os\nprint("a" * 100 + os.environ["SERVICE_TOKEN"])\n', secrets={'SERVICE_TOKEN': 'Dummy token'})
        self.set_secret()
        with patch('bai.tooling.MAX_OUTPUT', 110):
            result = tooling.run(self.ws, 'test-tool', [], 'dev')
        self.assertTrue(result['truncated'])
        self.assertNotIn('synthetic', result['stdout'])

    def test_invalid_and_duplicate_manifests_fail_before_execution(self):
        manifest = self.tool()
        write_json(self.ws.path('tools/duplicate.json'), manifest)
        with self.assertRaises(Error):
            tooling.run(self.ws, 'test-tool', [], 'dev')
        self.assertTrue(tooling.find(self.ws)['warnings'])

    def test_csv_summary_bom_duplicates_and_invalid_width(self):
        source = self.base / 'input.csv'
        source.write_text('\ufeffid,name\n1,alpha\n1,alpha\n\n', encoding='utf-8')
        result = tooling.run(self.ws, 'csv-summary', [str(source)], 'dev')
        self.assertEqual(json.loads(result['stdout']), {'columns': ['id', 'name'], 'rows': 2, 'blank_rows': 1})
        source.write_text('id,name\n1,alpha,unexpected\n')
        result = tooling.run(self.ws, 'csv-summary', [str(source)], 'dev')
        self.assertNotEqual(result['exit_code'], 0)
        self.assertNotIn('unexpected', result['stderr'])

    def test_external_repo_tool_resolves_without_modifying_repo(self):
        repo = self.repo('service')
        (repo / 'check.py').write_text('print("external repo tool")\n')
        manifest = self.tool()
        manifest.update(repo='service', entry='check.py')
        tooling.register(self.ws, manifest, replace=True)
        self.assertIn('external repo tool', tooling.run(self.ws, 'test-tool', [], 'dev')['stdout'])


class BackupTests(Fixture):
    def plain(self):
        archive = self.base / 'snapshot.zip'
        backup.pack(self.ws, archive)
        return archive

    def rewrite(self, source, mutate):
        with zipfile.ZipFile(source) as archive:
            files = {i.filename: archive.read(i.filename) for i in archive.infolist()}
        mutate(files)
        with zipfile.ZipFile(source, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)

    def test_snapshot_includes_secrets_and_excludes_cache(self):
        self.set_secret()
        atomic(self.ws.path('cache/unused.json'), b'{}')
        archive = self.plain()
        with zipfile.ZipFile(archive) as z:
            self.assertIn('private/secrets.json', z.namelist())
            self.assertNotIn('cache/unused.json', z.namelist())
            self.assertNotIn('write.lock', z.namelist())

    def test_snapshot_roundtrip_preserves_topics_and_task_membership(self):
        topic = self.ws.create_topic('tickets', 'Ticketing', 'Maintain ticketing')
        task = self.ws.create('tasks', 'ticket-check', 'Check ticket requests', topic='tickets',
                              expected_open_set=self.ws.open_set([], 'tickets'))
        restored = Workspace(self.base / 'restored')
        backup.unpack(self.plain(), restored.data)
        self.assertEqual(restored.record('topics', 'tickets'), topic)
        self.assertEqual(restored.record('tasks', task['id']), task)

    def test_unpack_rejects_checksum_corruption(self):
        archive = self.plain()
        self.rewrite(archive, lambda files: files.update({'preferences.md': b'corrupt'}))
        with self.assertRaises(Error):
            backup.unpack(archive, self.base / 'staged')

    def test_unpack_rejects_traversal_and_symlinks(self):
        archive = self.plain()
        self.rewrite(archive, lambda files: files.update({'../escape': b'bad'}))
        with self.assertRaises(Error):
            backup.unpack(archive, self.base / 'staged')
        self.assertFalse((self.base / 'escape').exists())
        archive = self.plain()
        with zipfile.ZipFile(archive, 'a') as z:
            info = zipfile.ZipInfo('tools/escape')
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, '../outside')
        with self.assertRaises(Error):
            backup.unpack(archive, self.base / 'staged-links')

    def test_unpack_rejects_unsupported_schema_and_size(self):
        archive = self.plain()
        def newer(files):
            m = json.loads(files['manifest.json']); m['schema_version'] = 99
            files['manifest.json'] = encoded(m)
        self.rewrite(archive, newer)
        with self.assertRaises(Error):
            backup.unpack(archive, self.base / 'newer')
        with patch('bai.backup.MAX_BYTES', 1), self.assertRaises(Error):
            backup.snapshot(self.ws)

    def test_backup_refuses_existing_output_and_cleanup_on_encryption_failure(self):
        target = self.base / 'existing.age'
        target.write_bytes(b'keep')
        with self.assertRaises(Error):
            backup.create(self.ws, target)
        self.assertEqual(target.read_bytes(), b'keep')
        target = self.base / 'new.age'
        with patch('bai.backup.age', side_effect=Error('simulated encryption failure')), self.assertRaises(Error):
            backup.create(self.ws, target)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.base.glob('.bai-backup-*')))

    def test_existing_restore_data_never_overwritten(self):
        source = self.base / 'backup.age'
        source.write_bytes(b'fake encrypted data')
        before = digest(self.ws.path('preferences.md'))
        with self.assertRaises(Error):
            backup.restore(self.ws, source)
        self.assertEqual(before, digest(self.ws.path('preferences.md')))

    def test_wrong_passphrase_or_cancel_does_not_activate_restore(self):
        source = self.base / 'backup.age'
        source.write_bytes(b'synthetic ciphertext')
        root = self.base / 'fresh'
        root.mkdir()
        ws = Workspace(root)
        with patch('bai.backup.age', side_effect=Error('Wrong passphrase')), self.assertRaises(Error):
            backup.restore(ws, source)
        self.assertFalse(ws.data.exists())
        self.assertFalse(list(root.glob('.bai-restore-*')))

    def test_malformed_secrets_rejected_without_showing_values(self):
        write_json(self.ws.path('private/secrets.json'), {'schema_version': 99, 'value': DUMMY})
        archive = self.plain()
        with self.assertRaises(Error) as failure:
            backup.unpack(archive, self.base / 'stage')
        self.assertNotIn(DUMMY, str(failure.exception))

    def test_missing_narrative_rejected_during_restore_validation(self):
        self.task()
        self.ws.path('tasks/test-task.md').unlink()
        archive = self.plain()
        with self.assertRaises(Error):
            backup.unpack(archive, self.base / 'stage')

    def test_output_race_does_not_overwrite_file(self):
        target = self.base / 'race.age'
        def race(mode, source, encrypted):
            encrypted.write_bytes(b'synthetic-ciphertext')
            target.write_bytes(b'concurrent-owner')
        with patch('bai.backup.age', side_effect=race), self.assertRaises(Error):
            backup.create(self.ws, target)
        self.assertEqual(target.read_bytes(), b'concurrent-owner')

    def test_source_change_during_snapshot_aborts(self):
        original = backup.snapshot
        calls = 0
        def changing(ws):
            nonlocal calls
            calls += 1
            if calls == 2:
                atomic(ws.path('preferences.md'), b'concurrent edit')
            return original(ws)
        with patch('bai.backup.snapshot', side_effect=changing), self.assertRaises(Error):
            self.plain()

    @unittest.skipUnless(shutil.which('age') and shutil.which('age-keygen'), 'age binaries not installed')
    def test_real_age_roundtrip_with_ephemeral_test_identity(self):
        # Tests actual age encryption and restoration without a passphrase in argv,
        # a terminal prompt, network, real user credentials or printing private keys.
        key = self.base / 'test-identity.txt'
        subprocess.run(['age-keygen', '-o', str(key)], check=True, capture_output=True)
        public = subprocess.run(['age-keygen', '-y', str(key)], check=True, capture_output=True, text=True).stdout.strip()
        def test_crypto(mode, source, target):
            args = ['age', '-r', public] if mode == 'encrypt' else ['age', '-d', '-i', str(key)]
            result = subprocess.run([*args, '-o', str(target), str(source)], capture_output=True)
            if result.returncode:
                raise Error('Test age operation failed.')
        self.set_secret()
        self.task()
        target = self.base / 'encrypted.age'
        root = self.base / 'restored clone'
        root.mkdir()
        restored = Workspace(root)
        with patch('bai.backup.age', side_effect=test_crypto):
            backup.create(self.ws, target)
            self.assertNotIn(DUMMY.encode(), target.read_bytes())
            result = backup.restore(restored, target)
        self.assertEqual(result['restored'], str(restored.data))
        self.assertEqual(credentials.values(restored, 'dev', ['SERVICE_TOKEN'])['SERVICE_TOKEN'], DUMMY)
        self.assertEqual(restored.record('tasks', 'test-task')['title'], 'Investigate service')
        self.assertTrue(restored.path('cache/index.json').exists())
        self.assertFalse(list(root.glob('.bai-restore-*')))


class ProjectTests(unittest.TestCase):
    def test_startup_adapters_and_budget(self):
        self.assertLess((PROJECT / 'AGENTS.md').stat().st_size, 6500)
        for name in ('CLAUDE.md', 'GEMINI.md', '.github/copilot-instructions.md', '.cursor/rules/workspace.mdc'):
            self.assertIn('AGENTS.md', (PROJECT / name).read_text(encoding='utf-8'))

    def test_public_docs_links_and_no_private_workspace_paths(self):
        import re
        for path in PROJECT.rglob('*.md'):
            if '.bai' in path.parts:
                continue
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('/Users/', text, path)
            self.assertNotIn('\u2014', text, path)
            for link in re.findall(r'\[[^\]]+\]\(([^)]+)\)', text):
                if '://' not in link and not link.startswith('#'):
                    self.assertTrue((path.parent / link.split('#')[0]).exists(), f'{path}: {link}')

    def test_tool_template_valid_and_personal_gitignore_present(self):
        tooling.validate(read_json(PROJECT / 'templates/tool.json'))
        self.assertIn('/.bai/', (PROJECT / '.gitignore').read_text(encoding='utf-8').splitlines())
        self.assertIn('/backups/', (PROJECT / '.gitignore').read_text(encoding='utf-8').splitlines())


if __name__ == '__main__':
    unittest.main()
