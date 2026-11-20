import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bai import memory
from bai.core import Workspace


class ContextBriefTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ws = Workspace(Path(temporary.name))
        self.ws.init()
        for name in ('scanner-manual', 'document-worker', 'shipping-ui'):
            checkout = self.ws.root / name
            checkout.mkdir()
            self.ws.register_repo(name, checkout)

    def brief(self, query='', **options):
        return memory.context(self.ws, query, brief=True, **options)

    def add_topic(self, name='document-import', aliases=('paper trail',)):
        self.ws.create_topic(name, 'Document intake', 'Restore scanner jobs and publish resulting documents.',
                             aliases, ['scanner-manual'], ['document-worker'], ['read-only-manual'])

    def add_requirement(self):
        self.ws.create('knowledge', 'read-only-manual', 'Scanner manual is a reference',
                       ['scanner-manual', 'document-worker'], memory_kind='preference')
        source = self.ws.root / 'scanner-manual' / 'guide.md'
        source.write_text('Examples are maintained as reference material.', encoding='utf-8')
        self.ws.update('knowledge', 'read-only-manual', {
            'summary': 'Read examples in scanner-manual and apply fixes in document-worker.',
            'status': 'current', 'sources': ['scanner-manual:guide.md'],
        }, 1)
        memory.checkpoint(self.ws, 'knowledge', 'read-only-manual', 2)
        return source

    def add_wait(self, name, title, *, topic=None, waiting_on=None, **fields):
        token = self.ws.open_set(self.ws.records('tasks')[0], topic) if topic else None
        self.ws.create('tasks', name, title, ['document-worker'], topic=topic, waiting_on=waiting_on,
                       expected_open_set=token)
        return self.ws.update('tasks', name, {'status': 'waiting', **fields}, 1)

    def test_linked_legacy_waits_remain_distinct_and_prioritize_user(self):
        self.add_wait('scan-repair', 'Restore damaged scan', scope='Repair the scanner import.',
                      verification='Passed offline fixture checks.', next_step='Recheck the original scan.')
        self.ws.path('tasks/scan-repair.md').write_text(
            'The user must attach the original scan before live verification.', encoding='utf-8')
        self.add_wait('publish-queue', 'Publish scan catalog', related_tasks=['scan-repair'],
                      next_step='Poll the background publish job for its receipt.')
        for query in ('What’s waiting on me?', 'Is there anything I need to send you?',
                      'Do you need anything from me before you can finish?'):
            with self.subTest(query=query):
                packet = self.brief(query)
                self.assertEqual([row['id'] for row in packet['results']], ['scan-repair', 'publish-queue'])
                self.assertEqual(packet['waiting_on_counts'], {'user': 1, 'external': 1, 'unknown': 0})
                self.assertEqual(packet['resume'], {'candidate_count': 2, 'needs_choice': False})
                self.assertEqual(packet['results'][0]['waiting_on_inferred'], 'narrative')
                self.assertIn('original scan', packet['results'][0]['waiting_evidence'])

    def test_waits_in_one_topic_include_roles_and_a_sourced_stale_requirement(self):
        source = self.add_requirement()
        self.add_topic()
        self.add_wait('scan-repair', 'Restore damaged scan', topic='document-import', waiting_on='user',
                      done_when='Verify the original scan imports.', next_step='User sends the damaged scan.')
        self.add_wait('publish-queue', 'Publish scan catalog', topic='document-import', waiting_on='external',
                      related_tasks=['scan-repair'], next_step='Poll the publish receipt.')
        source.write_text('Examples were changed.', encoding='utf-8')
        packet = self.brief('What’s waiting on me?')
        self.assertEqual(packet['topic'], 'document-import')
        self.assertEqual([row['id'] for row in packet['results'][:2]], ['scan-repair', 'publish-queue'])
        role_row, requirement = packet['results'][2:4]
        self.assertEqual(role_row['reference_repos'], ['scanner-manual'])
        self.assertEqual(role_row['implementation_repos'], ['document-worker'])
        self.assertTrue(requirement['requirement'])
        self.assertEqual(requirement['sources'], ['scanner-manual:guide.md'])
        self.assertIn('changed', requirement['review_needed'])
        self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False, indent=2)) + 1, 4000)

        short = self.brief('What’s waiting on me?', limit=1, budget=2000)
        self.assertEqual(len(short['results']), 1)
        self.assertEqual(short['results'][0]['id'], 'scan-repair')
        self.assertEqual(short['requirements'], {'count': 1, 'review_needed': 1, 'inspect': 'topic show document-import'})
        self.assertEqual(short['omitted'], 3)
        self.assertIn('read-only-manual', short['warnings'][0])

    def test_resume_from_another_repo_and_recurrence_use_the_same_topic(self):
        self.add_requirement()
        self.add_topic()
        self.add_wait('scan-repair', 'Restore damaged scan', topic='document-import', waiting_on='user',
                      scope='Prepare a local fix and verify the damaged scan.',
                      done_when='The damaged scan imports in a live run.',
                      verification='Offline fixture checks passed.', next_step='User sends the damaged scan.')
        self.add_wait('publish-queue', 'Publish scan catalog', topic='document-import', waiting_on='external',
                      next_step='Poll the publish receipt.')
        for query in ('Could we pick up the damaged scan?', 'Fix the damaged scan again',
                      'Where did we leave off with the damaged scan?'):
            with self.subTest(query=query):
                packet = self.brief(query, repo='shipping-ui')
                self.assertEqual(packet['topic'], 'document-import')
                self.assertEqual(packet['results'][0]['id'], 'scan-repair')
                self.assertFalse(packet['resume']['needs_choice'])
        self.ws.update('tasks', 'scan-repair', {'status': 'complete'}, 2)
        packet = self.brief('The damaged scan fails again', repo='shipping-ui')
        self.assertEqual(packet['results'][0]['id'], 'scan-repair')
        self.assertTrue(packet['results'][0]['historical'])
        self.assertIn('publish-queue', [row['id'] for row in packet['results'][1:]])
        self.assertEqual(packet['resume']['candidate_count'], 1)
        self.assertFalse(packet['resume']['needs_choice'])
        self.ws.update('tasks', 'publish-queue', {'status': 'complete'}, 2)
        packet = self.brief('The damaged scan fails again', repo='shipping-ui')
        self.assertEqual(packet['resume']['candidate_count'], 0)

    def test_topic_aliases_scope_and_token_do_not_depend_on_matching_task(self):
        self.add_requirement()
        self.add_topic()
        self.add_topic('shipping-audit', ('parcel audit',))
        packet = self.brief('Set up a fresh paper trail for next week', repo='shipping-ui')
        self.assertEqual(packet['topic'], 'document-import')
        self.assertEqual(packet['open_set'], self.ws.open_set([], 'document-import'))
        self.assertEqual(packet['resume']['candidate_count'], 0)
        packet = self.brief('Can we continue paper trail and parcel audit?', limit=1)
        self.assertIsNone(packet['topic'])
        self.assertEqual(packet['topic_candidate_count'], 2)
        self.assertTrue(packet['resume']['needs_choice'])
        self.assertEqual(packet['omitted'], 1)
        self.assertEqual(self.brief(topic='paper trail')['topic'], 'document-import')

    def test_tight_continuation_brief_keeps_all_three_task_choices(self):
        source = self.add_requirement()
        self.add_topic('transfers')
        titles = ('Repair intake', 'Reconcile staging', 'Verify export')
        for index, title in enumerate(titles):
            self.add_wait(f'item-{index}', title, topic='transfers',
                          scope=f'{title} for this transfer. ' * 30,
                          verification='Checked part of the pipeline. ' * 30,
                          done_when=f'{title} succeeds on today’s batch. ' * 30,
                          next_step=f'{title} on the fixture. ' * 30)
        source.write_text('Examples were changed.', encoding='utf-8')
        packet = self.brief('continue', topic='transfers', limit=3, budget=2000)
        self.assertEqual(packet['resume'], {'candidate_count': 3, 'needs_choice': True})
        self.assertEqual({row['id']: row['title'] for row in packet['results']},
                         {f'item-{index}': title for index, title in enumerate(titles)})
        self.assertTrue(all(row.get('done_when') for row in packet['results']))
        self.assertGreater(packet['omitted'], 0)
        self.assertIn('read-only-manual', packet['warnings'][0])
        self.assertEqual(packet['requirements']['inspect'], 'topic show transfers')
        self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False, indent=2)) + 1, 2000)

    def test_repo_only_brief_does_not_search_unrelated_narratives(self):
        self.ws.create('tasks', 'unrelated', 'Audit parcel receipts', ['shipping-ui'])
        with patch('bai.memory.brief_body', side_effect=AssertionError('Unexpected narrative read')):
            packet = self.brief(repo='scanner-manual')
        self.assertEqual([row['id'] for row in packet['results']], ['scanner-manual'])

    def test_legacy_action_and_external_blocker_wording_are_inferred(self):
        self.add_wait('repair', 'Repair scan', next_step='Please provide the original scan.')
        self.add_wait('publish', 'Publish catalog', next_step='Upstream catalog publisher must finish. The user has no action.')
        self.add_wait('unclear', 'Check index', next_step='Await confirmation.')
        packet = self.brief('What do I need to provide?')
        self.assertEqual([row['id'] for row in packet['results']], ['repair', 'publish', 'unclear'])
        self.assertEqual([row['waiting_on'] for row in packet['results']], ['user', 'external', 'unknown'])
