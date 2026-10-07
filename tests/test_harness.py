"""Regressions from the QB-18 harness run; no network or shared project mutation."""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dotagent.cli import main
from dotagent.dashboard import action_argv, discover_projects, worker_logs
from dotagent.environment import git, lfs_pointer_assets, revision
from dotagent.integrations import GitHub
from dotagent.runtime import finish_draft, valid_report
from dotagent.state import State, atomic
from dotagent.supervisor import require_current_config, stop_supervisor, wait_for_port
from dotagent.terminal import event_message


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = State(self.root / 'state')
        self.addCleanup(self.state.db.close)
        self.config = {'state_dir': str(self.state.root), 'tasks': {'provider': 'local'}, 'host': {},
                       'limits': {}, 'repository': {'github': 'owner/repo', 'checks': []}}
        atomic(self.state.root / 'runtime-config.json', self.config)

    def task(self):
        repo = self.root / 'repo'
        repo.mkdir()
        for args in [('init', '-q'), ('config', 'user.email', 'fixture@example.test'),
                     ('config', 'user.name', 'Fixture')]:
            git(repo, *args)
        (repo / 'app.txt').write_text('fixture')
        git(repo, 'add', '.')
        git(repo, 'commit', '-qm', 'fixture')
        task = self.state.claim({'key': 'QB-18', 'summary': 'Fixture'}, str(repo))
        task.update(worktree=str(repo), branch=git(repo, 'branch', '--show-current'),
                    status='blocked', summary='Fixture summary', implementation='Fixture implementation',
                    changed_files=['app.txt'], review=['self-review'], review_revision=revision(repo),
                    criteria=[{'id': 'test', 'expected': 'passes', 'manual': 'run it',
                               'checks': [{'argv': ['test'], 'cwd': '.', 'kind': 'test'}]}],
                    blockers=['asset unavailable'], limitations=['suite fails'], commit_message='Fixture')
        log = self.root / 'failed.log'
        log.write_text('failed')
        task['evidence'] = [{'criterion': 'test', 'argv': ['test'], 'display': 'test', 'exit_code': 1,
                             'result': 'failed', 'revision': revision(repo), 'log': str(log)}]
        self.state.save(task)
        return task

    def test_config_change_pauses_before_start_or_resume(self):
        changed = copy.deepcopy(self.config)
        changed['tasks']['provider'] = 'jira'
        with self.state.lock():
            with patch('dotagent.cli.load_config', return_value=changed), patch('dotagent.cli.doctor') as doctor:
                for action in ('start', 'resume'):
                    self.state.set('paused', False)
                    with patch.object(sys, 'argv', ['dotagent', action]), self.assertRaises(SystemExit):
                        main()
                    self.assertTrue(self.state.get('paused'))
                doctor.assert_not_called()

    def test_identical_config_and_cli_preferences_are_applied_before_unpause(self):
        with self.state.lock():
            require_current_config(self.state, self.config)
            self.state.set('paused', True)
            with patch('dotagent.cli.load_config', return_value=self.config), patch('dotagent.cli.doctor', return_value=True), \
                 patch.object(sys, 'argv', ['dotagent', 'start', '--host', 'codex', '--workers', '1', '--no-browser']):
                main()
            self.assertEqual(self.state.get('preferred_workers'), 1)
            self.assertEqual(self.state.get('preferred_host'), 'codex')
            self.assertFalse(self.state.get('paused'))

    def test_unknown_owner_is_never_signalled_and_unrelated_port_is_preserved(self):
        with self.state.lock(), patch('dotagent.supervisor.os.kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'ownership'):
                stop_supervisor(self.state, timeout=0)
            kill.assert_not_called()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            with self.assertRaisesRegex(RuntimeError, 'unrelated process'):
                wait_for_port(listener.getsockname()[1], timeout=0)
            self.assertGreater(listener.fileno(), 0)

    def test_planning_checkpoint_can_add_live_criterion_without_weakening_frozen_checks(self):
        criterion = {'id': 'unit', 'manual': 'run', 'expected': 'pass',
                     'checks': [{'argv': ['unit'], 'kind': 'test'}]}
        report = {'action': 'implement', 'ui_changed': False, 'criteria': [criterion]}
        valid_report(report, {'criteria': []})
        report['action'] = 'verify'
        with self.assertRaisesRegex(RuntimeError, 'running app'):
            valid_report(report, {'criteria': [criterion]})
        live = {'id': 'live', 'manual': 'open', 'expected': 'works',
                'checks': [{'argv': ['live'], 'kind': 'running_app'}]}
        report['criteria'].append(live)
        valid_report(report, {'criteria': [criterion]})
        report['criteria'] = [live]
        with self.assertRaisesRegex(RuntimeError, 'frozen'):
            valid_report(report, {'criteria': [criterion]})

    def test_lfs_preflight_detects_pointer_without_changing_checkout(self):
        task = self.task()
        asset = Path(task['worktree']) / 'logo.png'
        asset.write_text('version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 42\n')
        git(task['worktree'], 'add', 'logo.png')
        before = revision(task['worktree'])
        self.assertEqual(lfs_pointer_assets(task['worktree']), ['logo.png'])
        self.assertEqual(revision(task['worktree']), before)
        asset.write_bytes(b'PNG real asset')
        self.assertEqual(lfs_pointer_assets(task['worktree']), [])

    def test_limited_draft_requires_current_review_and_does_not_set_verified_revision(self):
        task = self.task()
        fingerprint = revision(task['worktree'])
        original_evidence = copy.deepcopy(task['evidence'])
        def deliver(github, limited=False):
            self.assertTrue(limited)
            self.assertEqual(github.task['draft_authorization']['revision'], fingerprint)
            github.task['delivery']['pr'] = 'https://github.com/owner/repo/pull/1'
            return github.task['delivery']['pr']
        with patch.object(GitHub, 'validate_remote'), patch.object(GitHub, 'deliver', deliver):
            finish_draft(self.state, self.config, task['id'], 'User accepts disclosed failures for draft review')
        result = self.state.task(task['id'])
        self.assertEqual(result['evidence'], original_evidence)
        self.assertNotIn('verified_revision', result)
        self.assertEqual(result['delivery']['verification'], 'incomplete')
        self.assertEqual(result['status'], 'review')
        self.assertNotIn('jira_comment', result['delivery'])
        result['commit'] = 'test'
        self.config['repository']['checks'] = [{'argv': ['missing'], 'cwd': '.'}]
        body = GitHub(self.state, result, self.config['repository']).body({}, limited=True)
        self.assertIn('DRAFT WITH LIMITATIONS', body)
        self.assertIn('exit 1; failed', body)
        self.assertIn('unverified (no current evidence)', body)
        result['review_revision'] = 'stale'
        with self.assertRaisesRegex(RuntimeError, 'review of current content'):
            GitHub(self.state, result, self.config['repository']).deliver(limited=True)

    def test_limited_delivery_pushes_only_draft_and_reuses_existing_pr(self):
        task = self.task()
        remote = self.root / 'remote.git'
        git(self.root, 'init', '--bare', str(remote))
        git(task['worktree'], 'remote', 'add', 'origin', str(remote))
        task['draft_authorization'] = {'revision': revision(task['worktree']), 'reason': 'Explicit'}
        class FakeGitHub(GitHub):
            pr, creates = None, 0
            def validate_remote(inner):
                pass  # Deliberately local fixture remote.
            def find(inner):
                return copy.deepcopy(inner.pr)
            def gh(inner, *args, check=True):
                body = Path(args[args.index('--body-file') + 1]).read_text()
                if args[:2] == ('pr', 'create'):
                    self.assertIn('--draft', args)
                    inner.creates += 1
                    inner.pr = {'number': 1, 'url': 'https://github.com/owner/repo/pull/1',
                                'body': body, 'isDraft': True, 'state': 'OPEN',
                                'headRefOid': git(task['worktree'], 'rev-parse', 'HEAD')}
                else:
                    inner.pr['body'] = body
                return subprocess.CompletedProcess(args, 0, '', '')
        github = FakeGitHub(self.state, task, {**self.config['repository'], 'pr_base': 'main'})
        github.deliver(limited=True)
        first_body = github.pr['body']
        github.deliver(limited=True)
        self.assertEqual(github.creates, 1)
        self.assertEqual(github.pr['body'], first_body)
        self.assertIn('exit 1; failed', first_body)
        self.assertNotIn('verified_revision', task)
        github.pr['isDraft'] = False
        task['target_pr'] = {'number': 1}
        with self.assertRaisesRegex(RuntimeError, 'no longer draft'):
            github.deliver(limited=True)

    def test_authorization_never_bypasses_ordinary_delivery_or_stale_content(self):
        task = self.task()
        task['draft_authorization'] = {'revision': revision(task['worktree']), 'reason': 'Explicit'}
        github = GitHub(self.state, task, self.config['repository'])
        with patch.object(github, 'validate_remote'), patch.object(github, 'find', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'verification'):
                github.deliver()
            (Path(task['worktree']) / 'app.txt').write_text('changed after authorization')
            with self.assertRaisesRegex(RuntimeError, 'verification'):
                github.deliver(limited=True)

    def test_external_draft_reconciliation_is_idempotent_and_read_only_remote(self):
        task = self.task()
        evidence = copy.deepcopy(task['evidence'])
        pr = {'state': 'OPEN', 'isDraft': True, 'headRefOid': git(task['worktree'], 'rev-parse', 'HEAD'),
              'url': 'https://github.com/owner/repo/pull/383'}
        with patch.object(GitHub, 'validate_remote'), patch.object(GitHub, 'find', return_value=pr), \
             patch.object(GitHub, 'deliver') as deliver:
            for _ in range(2):
                self.assertEqual(finish_draft(self.state, self.config, task['id']), pr['url'])
            deliver.assert_not_called()
        result = self.state.task(task['id'])
        self.assertEqual(result['evidence'], evidence)
        self.assertEqual(result['blockers'], ['asset unavailable'])
        self.assertTrue(result['delivery']['external'])
        self.assertNotIn('verified_revision', result)
        pr['headRefOid'] = 'different'
        with patch.object(GitHub, 'validate_remote'), patch.object(GitHub, 'find', return_value=pr):
            with self.assertRaisesRegex(RuntimeError, 'differs'):
                finish_draft(self.state, self.config, task['id'])

    def test_dashboard_scopes_actions_and_handles_log_events(self):
        project = {'selection': ['--project', '/tmp/project with spaces.toml']}
        argv = action_argv(project, 'start')
        self.assertEqual(argv[2:4], project['selection'])
        self.assertEqual(argv[-2:], ['--workers', '1'])
        self.assertIn('--detach', argv)
        with self.assertRaises(ValueError):
            action_argv(project, 'resume')
        self.assertEqual(action_argv(project, 'draft', {'id': 'QB-18'}, text='Explicit')[-3:],
                         ['QB-18', '--reason', 'Explicit'])
        path = self.root / 'logs' / 'iteration-1'
        path.mkdir(parents=True)
        event = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '\x1b[31mhello\x1b[0m'}}
        (path / 'events.jsonl').write_text(json.dumps(event) + '\n{partial')
        self.assertEqual(worker_logs(path.parent), ['hello'])
        self.assertEqual(event_message({'type': 'assistant', 'message': {'content': [
            {'type': 'text', 'text': 'Claude'}, {'type': 'tool_use', 'name': 'Read'}]}}), 'Claude\nTools: Read')

    def test_explicit_dashboard_project_stays_selected_and_open_is_readonly(self):
        def load(config=None, project=None):
            name = project or config or 'local'
            return {'_path': name, 'project_name': name, 'state_dir': str(self.root / name)}
        with patch('dotagent.cli.load_config', side_effect=load):
            projects = discover_projects(project='selected', config_home=self.root)
        self.assertEqual(projects[0]['name'], 'selected')
        self.assertFalse((self.root / 'selected').exists())
        readonly = State(projects[0]['config']['state_dir'], readonly=True)
        self.assertEqual(readonly.tasks(), [])
        readonly.db.close()
        self.assertFalse((self.root / 'selected').exists())


if __name__ == '__main__':
    unittest.main()
