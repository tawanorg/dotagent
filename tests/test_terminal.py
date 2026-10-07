"""Exclusive native session takeover at the worker boundary."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotagent.bridge import dispatch
from dotagent.hosts import Interrupted, execute
from dotagent.state import State
from dotagent.tasks import add_task
from dotagent.terminal import resume_command, takeover, return_to_automation


class TerminalTests(unittest.TestCase):
    def test_takeover_is_exclusive_and_resume_rechecks_human_changes(self):
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            config = {'repository': {'path': root}, 'host': {}}
            task = add_task(state, config, 'Fix filters', 'filters')
            task.update(worktree=root, host='codex', workflow_run='original', phase='deliver', criteria=[{'id':'filter'}])
            state.save(task)
            events = state.directory('filters') / 'iteration-0' / 'events.jsonl'
            events.parent.mkdir()
            session = '5897b3f3-e125-4c04-a24b-d892653995ba'
            events.write_text(json.dumps({'type':'thread.started','thread_id':session}) + '\n')
            def spawn(argv, **kwargs):
                self.assertEqual(argv[:3], ['codex','resume',session])
                self.assertTrue(state.get('human:filters'))
                self.assertEqual(dispatch(state, config, 'perform', {'ticket':'filters','runId':'original'}), {'interrupted':True})
                from dotagent.supervisor import recover_task
                scoped = State(root, scope='filters')
                scoped.set('executing_phase', {'ticket':'filters','phase':'deliver','attempt':1,'started':0})
                recover_task(scoped, config, 'Supervisor restart during human session', failed=False)
                self.assertIsNone(scoped.get('executing_phase'))
                scoped.db.close()
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    return_to_automation(state, 'filters')
                return subprocess.Popen([sys.executable, '-c', 'pass'])
            native_spawn = subprocess.Popen
            def native(argv, **kwargs):
                if argv[0] != 'codex':
                    return native_spawn(argv, **kwargs)
                with patch('subprocess.Popen', native_spawn):
                    return spawn(argv, **kwargs)
            with patch('sys.stdin.isatty', return_value=True), patch('dotagent.terminal.subprocess.Popen', side_effect=native):
                takeover(state, config, 'filters')
            self.assertTrue(state.get('human:filters'))  # No restart on terminal exit.
            state.set('process_failures', 3)
            return_to_automation(state, 'filters')
            result = state.task('filters')
            self.assertFalse(state.get('human:filters'))
            self.assertEqual(state.get('process_failures'), 0)
            self.assertEqual(result['phase'], 'implement')
            self.assertNotIn('workflow_run', result)
            self.assertEqual(result['workflow_history'], ['original'])
            self.assertTrue(state.instructions('filters'))
            # Late old-run close callbacks must clear ownership without blocking the new run.
            scoped = State(root, scope='filters')
            scoped.set('iteration_worker', {'pid':999999999,'identity':'gone'})
            dispatch(scoped, config, 'runner-ended', {'ticket':'filters','runId':'original','pid':999999999,'code':1})
            self.assertIsNone(scoped.get('iteration_worker'))
            self.assertEqual(scoped.task('filters')['status'], 'active')
            scoped.db.close()
            task['host'] = 'claude'
            events.write_text(json.dumps({'type':'system','subtype':'init','session_id':session})+'\n')
            self.assertEqual(resume_command(state, task, config)[:3], ['claude','--resume',session])
            state.db.close()

    def test_child_retains_exclusive_lock_after_launcher_closes_it(self):
        with tempfile.TemporaryDirectory() as root:
            state = State(root, scope='filters')
            with state.lock('iteration') as lock:
                child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], pass_fds=(lock.fileno(),))
            try:
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    with state.lock('iteration'):
                        pass
            finally:
                child.terminate()
                child.wait()
            with state.lock('iteration'):
                pass
            state.db.close()

    def test_human_hold_stops_a_running_native_worker(self):
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            add_task(state, {'repository':{'path':root}}, 'Fix filters', 'filters')
            state.set('human:filters', True)
            with patch('dotagent.hosts.host_command', return_value=[sys.executable, '-c', 'import sys,time;sys.stdin.read();time.sleep(30)']):
                with self.assertRaises(Interrupted):
                    execute('codex', 'task', root, Path(root)/'iteration', {}, {}, state, 'filters')
            self.assertIsNone(state.get('worker'))
            state.db.close()


if __name__ == '__main__':
    unittest.main()
