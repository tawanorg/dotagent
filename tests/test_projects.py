"""Project selection and durable knowledge through their public interfaces."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from dotagent.projects import resolve_config
from dotagent.state import State


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_home = self.root / 'config'
        self.config_home.mkdir()
        self.state_home = self.root / 'state'
        (self.config_home / 'config.toml').write_text('[host]\nstall_seconds = 42\n[limits]\nmax_iterations = 7\n')

    def repo(self, name):
        path = self.root / name
        subprocess.run(['git', 'init', '-q', str(path)], check=True)
        (path / '.dotagent.toml').write_text(
            f'[repository]\npath = "."\ngithub = "owner/{name}"\n[jira]\nsite = "https://example.atlassian.net"\n')
        return path

    def select(self, **kwargs):
        return resolve_config(config_home=self.config_home, state_home=self.state_home, **kwargs)

    def test_current_repository_selects_isolated_state_with_personal_defaults(self):
        first, second = self.repo('one'), self.repo('two')
        (first / 'nested').mkdir()
        a, b = self.select(cwd=first / 'nested'), self.select(cwd=second)
        self.assertEqual(a['repository']['path'], str(first.resolve()))
        self.assertEqual(a['host']['stall_seconds'], 42)
        self.assertEqual(a['limits']['max_iterations'], 7)
        self.assertFalse(a['_legacy'])
        self.assertNotEqual(a['state_dir'], b['state_dir'])
        self.assertNotEqual(a['studio']['port'], b['studio']['port'])
        for config in (a, b):
            state = State(config['state_dir'])
            self.addCleanup(state.db.close)
            self.assertIsNotNone(state.claim({'key': 'TASK-1', 'summary': 'same ticket key'}, config['repository']))
        self.assertEqual(a['state_dir'], self.select(cwd=first)['state_dir'])

    def test_registry_discovery_and_worktree_use_same_project(self):
        repo = self.repo('registered')
        registry = self.config_home / 'projects'
        registry.mkdir()
        (registry / 'customer.toml').write_text(
            f'[repository]\npath = "{repo}"\n[jira]\nsite = "https://customer.atlassian.net"\n[studio]\nport = 4444\n')
        (repo / '.dotagent.toml').unlink()
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'initial'], check=True)
        linked = self.root / 'linked'
        subprocess.run(['git', '-C', str(repo), 'worktree', 'add', '-q', '-b', 'linked', str(linked)], check=True)
        named, discovered = self.select(project='customer'), self.select(cwd=linked)
        self.assertEqual(named['_path'], discovered['_path'])
        self.assertEqual(named['_project_id'], discovered['_project_id'])
        self.assertEqual(discovered['repository']['path'], str(repo.resolve()))
        self.assertEqual(discovered['studio']['port'], 4444)

    def test_explicit_legacy_config_keeps_state_but_cannot_leak_to_other_repo(self):
        first, other = self.repo('legacy'), self.repo('other')
        old_state = self.root / 'legacy-state'
        path = self.config_home / 'config.toml'
        path.write_text(f'state_dir = "{old_state}"\n[repository]\npath = "{first}"\n[jira]\nsite = "https://legacy.atlassian.net"\n')
        explicit = self.select(config_path=path)
        self.assertEqual(explicit['state_dir'], str(old_state))
        self.assertTrue(explicit['_legacy'])
        (first / '.dotagent.toml').unlink()
        self.assertTrue(self.select(cwd=first)['_legacy'])
        (other / '.dotagent.toml').unlink()
        with self.assertRaisesRegex(ValueError, 'no project configuration'):
            self.select(cwd=other)

    def test_project_memory_survives_reopen_with_provenance_and_no_cross_project_results(self):
        from dotagent.memory import ProjectMemory
        a, b = self.select(cwd=self.repo('brain-one')), self.select(cwd=self.repo('brain-two'))
        memory = ProjectMemory(a)
        recorded = memory.remember('Price writes emit committed audit events.', source='src/events/audit.ts:42', kind='fact', revision='abc123')
        memory.close()
        restored = ProjectMemory(a)
        self.addCleanup(restored.close)
        self.assertEqual(restored.search('audit')[0], recorded)
        self.assertEqual(recorded['source'], 'src/events/audit.ts:42')
        self.assertEqual(recorded['revision'], 'abc123')
        self.assertEqual(restored.remember(recorded['text'], source=recorded['source'], kind='fact', revision='abc123')['id'], recorded['id'])
        self.assertEqual(len(restored.list()), 1)
        other = ProjectMemory(b)
        self.addCleanup(other.close)
        self.assertEqual(other.search('audit'), [])
        with self.assertRaisesRegex(ValueError, 'belongs to another project'):
            ProjectMemory({**b, 'state_dir': a['state_dir']})
        with self.assertRaisesRegex(ValueError, 'source'):
            restored.remember('unattributed claim', source='')

    def test_worktree_never_inherits_an_unrelated_parent_project(self):
        outer, source = self.repo('outer'), self.repo('source')
        (source / '.dotagent.toml').unlink()
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'initial'], check=True)
        linked = outer / 'linked'
        subprocess.run(['git', '-C', str(source), 'worktree', 'add', '-q', '-b', 'linked', str(linked)], check=True)
        with self.assertRaisesRegex(ValueError, 'no project configuration'):
            self.select(cwd=linked)

    def test_correction_retires_stale_knowledge_and_preserves_history_after_reopen(self):
        from dotagent.memory import ProjectMemory
        config = self.select(cwd=self.repo('correction'))
        memory = ProjectMemory(config)
        original = memory.remember('Run npm test.', source='README.md:5')
        replacement = memory.supersede(original['id'], 'Run npm run test:unit.',
                                       reason='Project migrated to separate test suites.',
                                       source='user correction', revision='def456')
        memory.close()
        memory = ProjectMemory(config)
        self.addCleanup(memory.close)
        self.assertEqual([row['id'] for row in memory.list()], [replacement['id']])
        self.assertEqual([row['id'] for row in memory.search('npm')], [replacement['id']])
        historical = next(row for row in memory.list(include_retired=True) if row['id'] == original['id'])
        self.assertEqual(historical['superseded_by'], replacement['id'])
        self.assertEqual(historical['retirement_reason'], 'Project migrated to separate test suites.')
        self.assertEqual(memory.supersede(original['id'], replacement['text'], source='user correction',
                                         revision='def456', reason=historical['retirement_reason'])['id'], replacement['id'])
        with self.assertRaisesRegex(ValueError, 'unknown project memory'):
            memory.supersede('missing-id', 'Must not survive transaction rollback.', source='user correction', reason='Invalid target')
        self.assertEqual(len(memory.list(include_retired=True)), 2)
        memory.retire(replacement['id'], reason='No longer relevant after removing npm.')
        self.assertEqual(memory.search('npm'), [])
        self.assertEqual(len(memory.list(include_retired=True)), 2)


if __name__ == '__main__':
    unittest.main()
