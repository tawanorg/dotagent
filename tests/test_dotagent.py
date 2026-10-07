"""Behavioral checks for ownership, recovery, evidence and delivery boundaries."""
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotagent.state import State
from dotagent.environment import allocate_port, isolate_compose, revision, git, prepare_worktree
from dotagent.hosts import command, execute, host_command, process_record
from dotagent.integrations import GitHub, Jira, select_ticket
from dotagent.runtime import recover_child, recover_iteration, valid_report, run_check


class EngineerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = State(self.root / 'state')
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        for args in [('init', '-b', 'main'), ('config', 'user.email', 'fixture@example.test'),
                     ('config', 'user.name', 'Fixture')]:
            command(['git', '-C', str(self.repo), *args])
        (self.repo / 'app.txt').write_text('before\n')
        command(['git', '-C', str(self.repo), 'add', '.'])
        command(['git', '-C', str(self.repo), 'commit', '-m', 'fixture'])
        self.ticket = dict(key='QB-1', summary='Fixture', url='https://example.test/QB-1',
                           priority='High', status='Dev Ready', updated='2026-01-01', blockers=[])
        self.task = self.state.claim(self.ticket, str(self.repo))

    def tearDown(self):
        self.state.db.close()
        self.tmp.cleanup()

    def test_duplicate_claim_and_process_lock(self):
        second = State(self.root / 'state')
        self.assertIsNone(second.claim(self.ticket, str(self.repo)))
        with self.state.lock():
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                with second.lock():
                    pass
        second.db.close()

    def test_stale_worker_save_cannot_erase_cancellation(self):
        stale = dict(self.task)
        self.task['status'] = 'cancelled'
        self.state.save(self.task)
        self.state.save(stale)
        self.assertEqual(self.state.task('QB-1')['status'], 'cancelled')
        stale['status'] = 'active'
        self.state.save(stale, allow_reactivate=True)
        self.assertEqual(self.state.task('QB-1')['status'], 'active')

    def test_worktree_resume_preserves_original_edits(self):
        (self.repo / 'app.txt').write_text('user work\n')
        path = prepare_worktree(self.state, self.task, {'path': str(self.repo)})
        self.assertEqual((path / 'app.txt').read_text(), 'before\n')
        self.assertEqual((self.repo / 'app.txt').read_text(), 'user work\n')
        self.assertEqual(path, prepare_worktree(self.state, self.task, {'path': str(self.repo)}))
        self.state.handover(self.task)
        self.assertFalse((path / 'handover.json').exists())
        self.assertEqual(json.loads((self.state.directory('QB-1') / 'handover.json').read_text())['branch'], 'dotagent/qb-1')
        other = State(self.root / 'other-state')
        duplicate = other.claim(self.ticket, str(self.repo))
        with self.assertRaisesRegex(RuntimeError, 'another runtime'):
            prepare_worktree(other, duplicate, {'path': str(self.repo)})
        other.db.close()

    def test_ports_and_data_isolation_preserve_external_resources(self):
        a = allocate_port(self.state, 'a', 'app', start=26000, end=27000)
        b = allocate_port(self.state, 'b', 'app', start=26000, end=27000)
        self.assertNotEqual(a, b)
        self.assertEqual(a, allocate_port(self.state, 'a', 'app', start=26000, end=27000))
        model = {'services': {'app': {'ports': [{'target': 80, 'published': '80'}],
                                     'volumes': [{'type': 'volume', 'source': 'data', 'target': '/data'}]}},
                 'volumes': {'data': {}}, 'networks': {'default': {}}}
        one = isolate_compose(copy.deepcopy(model), 'one', self.repo, {'app:80': a}, 1, '256m')
        two = isolate_compose(copy.deepcopy(model), 'two', self.repo, {'app:80': b}, 1, '256m')
        self.assertNotEqual(one['volumes']['data']['name'], two['volumes']['data']['name'])
        self.assertEqual(one['services']['app']['ports'][0]['host_ip'], '127.0.0.1')
        model['volumes']['data']['external'] = True
        with self.assertRaisesRegex(RuntimeError, 'external'):
            isolate_compose(model, 'bad', self.repo, {'app:80': a}, 1, '256m')

    def test_content_revision_survives_commit_but_changes_with_content(self):
        before = revision(self.repo)
        (self.repo / 'app.txt').write_text('after\n')
        after = revision(self.repo)
        self.assertNotEqual(before, after)
        command(['git', '-C', str(self.repo), 'commit', '-am', 'change'])
        self.assertEqual(after, revision(self.repo))
        (self.repo / 'new.txt').write_text('untracked')
        self.assertNotEqual(after, revision(self.repo))

    def test_failed_verification_never_counts_as_pass(self):
        self.task.update(worktree=str(self.repo), environment={'base_url': 'http://127.0.0.1:1'})
        self.state.save(self.task)
        passed = run_check(self.state, self.task, {'cwd': '.', 'argv': [sys.executable, '-c', 'raise SystemExit(7)']}, {}, 'criterion')
        self.assertFalse(passed)
        self.assertEqual(self.task['evidence'][-1]['exit_code'], 7)
        self.assertNotIn('verified_revision', self.task)

    def test_orphan_test_process_is_terminated_on_recovery(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        self.state.set('worker', process_record(child, 'QB-1', [], self.root))
        recover_child(self.state)
        child.wait(timeout=5)
        self.assertNotEqual(child.returncode, 0)
        self.assertIsNone(self.state.get('worker'))

    def test_recovery_does_not_kill_reused_or_unowned_pid(self):
        self.state.set('worker', {'pid': os.getpid(), 'identity': 'not this process', 'directory': str(self.root)})
        recover_child(self.state)
        self.assertIsNone(self.state.get('worker'))

    def test_crashed_supervisor_reaps_owned_worker_and_child_before_restart(self):
        script = self.root / 'worker.py'
        script.write_text('''import sys,time,subprocess
sys.path.insert(0,sys.argv[1])
from dotagent.state import State
from dotagent.hosts import process_record
s=State(sys.argv[2])
with s.lock('iteration'):
 p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
 s.set('worker',process_record(p,'QB-1',[],sys.argv[2]))
 s.set('ready',True)
 time.sleep(60)
''')
        args = [sys.executable, str(script), str(Path(__file__).resolve().parents[1]), str(self.state.root), '_work']
        process = subprocess.Popen(args, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not self.state.get('ready') and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(self.state.get('ready'))
            self.state.set('iteration_worker', {'pid': process.pid, 'argv': args})
            self.state.set('paused', True)
            replacement = State(self.state.root)
            self.assertTrue(replacement.get('paused'))
            recover_iteration(replacement)
            process.wait(timeout=5)
            with replacement.lock('iteration'):
                self.assertIsNone(replacement.get('worker'))
            self.assertIsNone(replacement.get('iteration_worker'))
            replacement.db.close()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait()

    def test_exhausted_spending_limit_prevents_host_launch(self):
        self.state.set('spend_usd', 5)
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'), patch('subprocess.Popen') as spawn:
            execute('claude', 'no request', self.repo, self.root / 'host', {},
                    {'max_spend_usd': 5}, self.state)
        spawn.assert_not_called()

    def test_existing_actionable_before_priority_and_unknown_priority_blocks(self):
        config = {'eligible_statuses': ['In Progress', 'Dev Ready'], 'active_statuses': ['In Progress'],
                  'priorities': ['Highest', 'High', 'Low']}
        active = dict(self.ticket, key='QB-2', priority='Low', status='In Progress')
        highest = dict(self.ticket, priority='Highest')
        self.assertEqual(select_ticket([highest, active], config, set())['key'], 'QB-2')
        self.assertEqual(select_ticket([highest, active], config, {'QB-2'})['key'], 'QB-1')
        with self.assertRaisesRegex(RuntimeError, 'priority'):
            select_ticket([dict(self.ticket, priority='Unknown')], config, set())

    def test_frozen_criteria_cannot_be_weakened(self):
        criterion = {'id': 'one', 'description': 'correct', 'manual': 'click', 'expected': '1',
                     'checks': [{'argv': ['true'], 'cwd': '.', 'kind': 'running_app'}]}
        self.task['criteria'] = [criterion]
        report = {'criteria': [dict(criterion, expected='anything')], 'action': 'verify', 'ui_changed': False}
        with self.assertRaisesRegex(RuntimeError, 'frozen'):
            valid_report(report, self.task)

    def test_native_hosts_keep_auth_and_do_not_invent_shared_commands(self):
        config = {'mcp_servers': []}
        codex = host_command('codex', self.repo, self.root, {'type': 'object'}, config)
        claude = host_command('claude', self.repo, self.root, {'type': 'object'}, config)
        self.assertIn('--output-schema', codex)
        self.assertIn('--json-schema', claude)
        self.assertNotIn('--bare', claude)
        self.assertNotIn('--dangerously-skip-permissions', claude)

    def test_commit_refuses_unrelated_staged_files(self):
        self.task.update(worktree=str(self.repo), changed_files=['app.txt'], artifacts=[])
        (self.repo / 'unrelated.txt').write_text('preserve')
        command(['git', '-C', str(self.repo), 'add', 'unrelated.txt'])
        with self.assertRaisesRegex(RuntimeError, 'unrelated staged'):
            GitHub(self.state, self.task, {}).commit()
        self.assertIn('unrelated.txt', git(self.repo, 'diff', '--cached', '--name-only'))

    def test_attachment_reconciliation_accepts_only_real_github_asset_urls(self):
        digest = 'a' * 64
        urls = GitHub.attachment_urls(f'![dotagent-{digest}](https://github.com/user-attachments/assets/123)')
        self.assertEqual(urls[digest], 'https://github.com/user-attachments/assets/123')
        self.assertFalse(GitHub.attachment_urls(f'![dotagent-{digest}](/tmp/screen.png)'))
        self.assertFalse(GitHub.attachment_urls(f'![dotagent-{digest}](https://example.test/screen.png)'))

    def test_delivery_reconciles_partial_upload_and_is_idempotent(self):
        remote = self.root / 'remote.git'
        command(['git', 'init', '--bare', str(remote)])
        command(['git', '-C', str(self.repo), 'remote', 'add', 'origin', str(remote)])
        (self.repo / 'app.txt').write_text('fixed')
        screenshot = self.root / 'shot.png'
        screenshot.write_bytes(b'fixture-image')
        digest = hashlib.sha256(screenshot.read_bytes()).hexdigest()
        self.task.update(worktree=str(self.repo), branch='main', changed_files=['app.txt'],
            artifacts=[{'path': str(screenshot), 'sha256': digest, 'redacted': True, 'criterion': 'one'}],
            verified_revision=revision(self.repo), commit_message='fix: fixture', summary='Problem fixed',
            implementation='One change', criteria=[{'id': 'one', 'manual': 'Click', 'expected': 'One'}])

        class FakeGitHub(GitHub):
            pr, creates, uploads = None, 0, 0
            def validate_remote(inner):
                pass  # This fixture pushes to its deliberately local bare remote.
            def find(inner):
                return copy.deepcopy(inner.pr)
            def gh(inner, *args, check=True):
                args = list(args)
                body = Path(args[args.index('--body-file') + 1]).read_text()
                if args[:2] == ['pr', 'create']:
                    inner.creates += 1
                    inner.pr = {'number': 1, 'url': 'https://github.com/test/repo/pull/1',
                                'body': body, 'isDraft': True, 'state': 'OPEN',
                                'headRefOid': git(self.repo, 'rev-parse', 'HEAD')}
                elif args[:2] == ['pr', 'edit']:
                    if '--attach' in args:
                        inner.uploads += 1
                        body = body.replace(str(screenshot), 'https://github.com/user-attachments/assets/fixture')
                    inner.pr['body'] = body
                return subprocess.CompletedProcess(args, 1 if '--attach' in args else 0, '', '')

        delivery = FakeGitHub(self.state, self.task, {'github': 'test/repo', 'pr_base': 'main'})
        delivery.deliver()
        first_commit = self.task['commit']
        first_body = delivery.pr['body']
        delivery.deliver()
        self.assertEqual((delivery.creates, delivery.uploads), (1, 1))
        self.assertEqual(self.task['commit'], first_commit)
        self.assertEqual(delivery.pr['body'], first_body)
        self.assertNotIn(str(screenshot), delivery.pr['body'])
        self.assertIn('https://github.com/user-attachments/assets/fixture', delivery.pr['body'])

    def test_closed_pr_is_checked_before_git_mutation(self):
        self.task.update(worktree=str(self.repo))
        delivery = GitHub(self.state, self.task, {})
        with patch.object(delivery, 'validate_remote'), \
             patch.object(delivery, 'find', return_value={'state': 'CLOSED', 'isDraft': False}), \
             patch.object(delivery, 'commit') as commit:
            with self.assertRaisesRegex(RuntimeError, 'awaiting human'):
                delivery.deliver()
            commit.assert_not_called()

    def test_jira_updates_use_same_marker_and_require_readback(self):
        integration = Jira(self.state, {'site': 'https://example.test'}, {})
        prompts = []
        def reply(prompt, schema):
            prompts.append(prompt)
            return {'comment_id': '42', 'body': 'dotagent:QB-1\nPR ready', 'error': ''}
        with patch.object(integration, 'call', side_effect=reply):
            integration.progress(self.task, 'PR ready')
            integration.progress(self.task, 'PR ready')
        self.assertEqual(prompts[0], prompts[1])
        self.assertEqual(self.task['delivery']['jira_comment'], '42')
        with patch.object(integration, 'call', return_value={'comment_id': '43', 'body': 'unrelated'}):
            with self.assertRaisesRegex(RuntimeError, 'read-back'):
                integration.progress(self.task, 'PR ready')


if __name__ == '__main__':
    unittest.main()
