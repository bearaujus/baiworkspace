"""Regression cases found while migrating a multi-repository workspace."""
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

from bai.core import Workspace, Error
from bai import memory, tooling
from bai.cli import main


class MigrationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Workspace(self.temp.name)
        self.ws.init()

    def test_evidence_is_hashable_without_becoming_search_content(self):
        source = self.ws.path('artifacts/legacy/credential-validation/notes.md')
        source.parent.mkdir(parents=True)
        source.write_text('Opaque historical evidence, opened only by explicit citation.')
        self.ws.create('knowledge', 'flow', 'Service flow')
        self.ws.update('knowledge', 'flow', {'status': 'current', 'sources': ['evidence:legacy/credential-validation/notes.md']}, 1)
        memory.checkpoint(self.ws, 'knowledge', 'flow', 2)
        self.assertEqual(memory.review(self.ws)['findings'], [])
        self.assertEqual(memory.context(self.ws, 'opaque historical')['results'], [])
        source.write_text('Changed evidence')
        self.assertEqual(memory.review(self.ws)['findings'][0]['id'], 'flow')
        with self.assertRaises(Error):
            memory.source_path(self.ws, 'evidence:../private/secrets.json')

    def test_imports_preserve_unknowns_without_startup_warning_flood(self):
        self.ws.create('tasks', 'old-task', 'Old deployment')
        self.ws.update('tasks', 'old-task', {'historical': True, 'status': 'unknown', 'origin': {'source_date': '2020-01-01'}}, 1)
        self.ws.create('knowledge', 'old-learning', 'Old learning', provenance='historical-record')
        self.ws.update('knowledge', 'old-learning', {'historical': True}, 1)
        result = memory.review(self.ws)
        self.assertEqual(result['historical_pending'], 1)
        self.assertEqual(result['findings'], [])
        self.assertTrue(memory.review(self.ws, include_historical=True)['findings'])
        self.assertEqual(self.ws.record('tasks', 'old-task')['status'], 'unknown')
        output = io.StringIO()
        with patch('sys.stdout', output):
            main(['--root', str(self.ws.root), 'status', '--json'])
        self.assertEqual(json.loads(output.getvalue())['tasks'], [])
        self.assertEqual(json.loads(output.getvalue())['historical_open_count'], 1)

    def test_active_handoff_and_tool_survive_large_history(self):
        for i in range(20):
            name = f'old-{i}'
            self.ws.create('tasks', name, 'CSV migration scope')
            self.ws.update('tasks', name, {'historical': True, 'summary': 'CSV migration scope ' * 100}, 1)
        self.ws.create('tasks', 'ongoing', 'CSV migration')
        result = memory.context(self.ws, 'CSV migration', budget=5000)
        ids = [r['id'] for r in result['results']]
        self.assertIn('ongoing', ids)
        self.assertIn('csv-summary', ids)

    def test_command_cwd_settings_and_explicit_stdin(self):
        directory = self.ws.path('tools/native')
        directory.mkdir(parents=True)
        script = directory / 'test.py'
        script.write_text('import os,sys\nprint(os.path.basename(os.getcwd()))\nprint(os.environ["MODE"])\nprint(sys.stdin.read())\nprint(repr(sys.argv[1:]))\n')
        data = self.ws.path('artifacts/input.txt')
        data.write_text('fixture input')
        manifest = {'schema_version': 1, 'id': 'native', 'description': 'Native command fixture', 'keywords': ['native'],
                    'runtime': 'command', 'entry': 'python-test', 'cwd': '.bai/tools/native', 'arguments': ['test.py'],
                    'dependencies': [], 'environment': {'MODE': 'offline'}, 'inputs': 'explicit stdin file',
                    'outputs': 'fixture result', 'effects': 'read-only', 'secrets': {}, 'verification': 'offline', 'recovery': 'read-only'}
        tooling.register(self.ws, manifest)
        with patch('bai.tooling.shutil.which', return_value=sys.executable):
            result = tooling.run(self.ws, 'native', ['a b;literal'], 'dev', stdin_file=data)
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['stdout'].splitlines()[:3], ['native', 'offline', 'fixture input'])
        self.assertIn('a b;literal', result['stdout'])

    def test_task_declared_tools_and_learning_survive_vocabulary_mismatch(self):
        self.ws.create('tasks', 'release', 'Rollout reconciliation')
        self.ws.update('tasks', 'release', {'tools': ['csv-summary', 'demo-service-check'], 'learnings': ['input-contract']}, 1)
        self.ws.create('knowledge', 'input-contract', 'Exact input format', memory_kind='procedure')
        result = memory.context(self.ws, 'rollout reconciliation')
        ids = [r['id'] for r in result['results']]
        self.assertIn('csv-summary', ids)
        self.assertIn('input-contract', ids)
        self.assertEqual([r['id'] for r in result['results'] if r['kind'] == 'tool'], ['csv-summary', 'demo-service-check'])

    def test_pretty_json_budget_retains_current_procedure(self):
        self.ws.create('tasks', 'rollout', 'Storage rollout')
        self.ws.create('knowledge', 'procedure', 'How to continue storage work', memory_kind='procedure')
        self.ws.update('tasks', 'rollout', {'learnings': ['procedure']}, 1)
        for i in range(8):
            self.ws.create('knowledge', f'related-{i}', 'Storage background', memory_kind='system')
            self.ws.update('knowledge', f'related-{i}', {'summary': 'Storage background ' * 100}, 1)
        result = memory.context(self.ws, 'storage rollout', budget=2500)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, indent=2)) + 1, 2500)
        self.assertIn('procedure', [r['id'] for r in result['results']])

    def test_concept_query_does_not_prioritize_incidental_active_history(self):
        self.ws.create('tasks', 'rollout', 'Ongoing deployment')
        self.ws.path('tasks/rollout.md').write_text('Package human bot roles are mentioned in this unrelated rollout.')
        self.ws.create('knowledge', 'roles', 'Package human bot roles', memory_kind='system')
        self.ws.create('knowledge', 'request-path', 'Request orchestration', memory_kind='system')
        self.ws.update('knowledge', 'roles', {'links': ['request-path']}, 1)
        result = memory.context(self.ws, 'package human bot roles', budget=2500)
        self.assertEqual(result['results'][0]['id'], 'roles')
        self.assertIn('request-path', [r['id'] for r in result['results']])

    def test_tool_search_uses_current_task_links_for_broad_intent(self):
        self.ws.create('tasks', 'access-review', 'Access discrepancy investigation')
        self.ws.update('tasks', 'access-review', {'tools': ['csv-summary']}, 1)
        for query in ('access', 'ongoing work'):
            result = tooling.find(self.ws, query)
            found = next(t for t in result['tools'] if t['id'] == 'csv-summary')
            self.assertEqual(found['matched_tasks'], ['access-review'])

    def test_resume_routes_generic_phrases_to_unfinished_work(self):
        self.ws.create('tasks', 'shipment', 'Reconcile shipments')
        self.ws.update('tasks', 'shipment', {'status': 'waiting', 'next_step': 'Obtain receipt.'}, 1)
        self.ws.create('tasks', 'history', 'Previous work')
        self.ws.update('tasks', 'history', {'historical': True}, 1)
        self.ws.create('tasks', 'finished', 'Finish work')
        self.ws.update('tasks', 'finished', {'status': 'complete'}, 1)
        for query in ('continue', 'Continue yesterday’s work.', 'What is waiting on me?',
                      'What were we working on?', 'Use the scope I gave you last time.'):
            with self.subTest(query=query):
                output = io.StringIO()
                with patch('sys.stdout', output):
                    code = main(['--root', str(self.ws.root), 'context', '--query', query, '--json'])
                self.assertEqual(code, 0)
                packet = json.loads(output.getvalue())
                self.assertEqual(packet['resume'], {'candidate_count': 1, 'needs_choice': False})
                self.assertEqual([item['id'] for item in packet['results']], ['shipment'])
                self.assertEqual(packet['results'][0]['next_step'], 'Obtain receipt.')
        self.ws.create('tasks', 'ledger', 'Audit ledger')
        packet = memory.context(self.ws, 'continue')
        self.assertEqual(packet['resume'], {'candidate_count': 2, 'needs_choice': True})
        self.assertEqual([item['id'] for item in packet['results'][:2]], ['ledger', 'shipment'])
        packet = memory.context(self.ws, 'What’s waiting on me?')
        self.assertEqual([item['id'] for item in packet['results']], ['shipment'])

    def test_resume_follows_link_to_latest_handoff_and_current_scope(self):
        self.ws.create('tasks', 'shipment', 'Reconcile outbound shipments')
        self.ws.update('tasks', 'shipment', {'status': 'waiting'}, 1)
        self.ws.create('tasks', 'receipt', 'Review receipt')
        self.ws.update('tasks', 'receipt', {
            'related_tasks': ['shipment'], 'scope': 'Only include the tangerine batch.',
            'next_step': 'Obtain the pallet receipt.',
        }, 1)
        self.ws.create('tasks', 'ledger', 'Audit ledger')
        self.ws.path('tasks/ledger.md').write_text('Also discussed shipments.', encoding='utf-8')
        for query in ('continue outbound shipments', 'tangerine', 'pallet'):
            with self.subTest(query=query):
                packet = memory.context(self.ws, query)
                task = packet['results'][0]
                self.assertEqual(task['id'], 'receipt')
                self.assertEqual(task['scope'], 'Only include the tangerine batch.')
                self.assertEqual(task['related_tasks'], ['shipment'])
                self.assertTrue(task['updated'])
        self.assertFalse(memory.context(self.ws, 'continue outbound shipments')['resume']['needs_choice'])
        self.assertEqual(memory.context(self.ws, 'continue')['resume']['candidate_count'], 2)

    def test_leading_map_and_linked_memory_fit_before_repo_briefs(self):
        repos = [f'shipping-service-{i:02}' for i in range(22)]
        for name in repos:
            self.ws.register_repo(name, self.ws.root)
        self.ws.create('knowledge', 'routing', 'Shipment routing', repos, memory_kind='system')
        self.ws.update('knowledge', 'routing', {'links': ['handling']}, 1)
        self.ws.create('knowledge', 'handling', 'Handling procedure', memory_kind='procedure')
        packet = memory.context(self.ws, 'shipment routing', budget=2000)
        self.assertEqual([item['id'] for item in packet['results'][:2]], ['routing', 'handling'])
        self.assertLess(len(packet['related_repositories']), len(repos))
        self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False, indent=2)) + 1, 2000)

    def test_tool_word_forms_and_aliases_match_in_both_searches(self):
        manifest = {k: v for k, v in tooling.get(self.ws, 'csv-summary').items() if not k.startswith('_')}
        manifest.update(id='app-check', description='Run app checks.', keywords=['verify', 'tests'],
                        aliases=['preflight'])
        tooling.register(self.ws, manifest)
        for query in ('test', 'tests', 'verify', 'verification', 'preflight'):
            with self.subTest(query=query):
                self.assertIn('app-check', [t['id'] for t in tooling.find(self.ws, query)['tools']])
                self.assertIn('app-check', [e['id'] for e in memory.context(self.ws, query)['results']])


if __name__ == '__main__':
    unittest.main()
