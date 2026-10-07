"""Public state boundaries for independent workers and a shared spending cap."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dotagent.state import State


class ConcurrentStateTests(unittest.TestCase):
    def test_task_workers_are_isolated_but_pause_and_supervisor_lock_are_shared(self):
        with tempfile.TemporaryDirectory() as root:
            project = State(root)
            first, second, duplicate = State(root, scope='TASK-1'), State(root, scope='TASK-2'), State(root, scope='TASK-1')
            for state in (project, first, second, duplicate):
                self.addCleanup(state.db.close)
            first.set('worker', {'pid': 101})
            second.set('worker', {'pid': 202})
            first.set('heartbeat', 10)
            second.set('heartbeat', 20)
            first.set('executing_phase', {'ticket': 'TASK-1'})
            self.assertEqual(duplicate.get('worker'), {'pid': 101})
            self.assertEqual(second.get('heartbeat'), 20)
            self.assertIsNone(second.get('executing_phase'))
            self.assertIsNone(project.get('worker'))
            self.assertEqual(project.scopes(), ['TASK-1', 'TASK-2'])
            project.set('paused', True)
            self.assertTrue(first.get('paused'))
            with first.lock('iteration'), second.lock('iteration'):
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    with duplicate.lock('iteration'):
                        pass
            with first.lock():
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    with second.lock():
                        pass
            project.set('worker', {'pid': 303})
            reopened = State(root)
            self.addCleanup(reopened.db.close)
            self.assertEqual(reopened.get('worker'), {'pid': 303})

    def test_concurrent_processes_cannot_overreserve_and_settlement_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            self.addCleanup(state.db.close)
            script = '''
import json, sys
from dotagent.state import State
s = State(sys.argv[1], scope=sys.argv[2])
print('ready', flush=True)
sys.stdin.read(1)
try:
    receipt, amount = s.reserve_spend(3, 1)
    print(json.dumps({'receipt': receipt, 'amount': amount}))
except RuntimeError as error:
    print(json.dumps({'error': str(error)}))
finally:
    s.db.close()
'''
            processes = [subprocess.Popen([sys.executable, '-c', script, root, f'TASK-{index}'],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                         for index in range(12)]
            try:
                for process in processes:
                    self.assertEqual(process.stdout.readline().strip(), 'ready')
                for process in processes:
                    process.stdin.write('x')
                    process.stdin.flush()
                results = []
                for process in processes:
                    output, error = process.communicate(timeout=30)
                    self.assertEqual(process.returncode, 0, error)
                    results.append(json.loads(output))
                reservations = [row for row in results if 'receipt' in row]
                self.assertEqual(sum(row['amount'] for row in reservations), 3)
                self.assertEqual(state.get('spend_usd'), 3)
                self.assertEqual(len(reservations), 3)
                settle = 'import sys; from dotagent.state import State; State(sys.argv[1]).settle_spend(sys.argv[2], 0.25)'
                settlements = [subprocess.Popen([sys.executable, '-c', settle, root, row['receipt']],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                               for row in reservations]
                for process in settlements:
                    output, error = process.communicate(timeout=30)
                    self.assertEqual(process.returncode, 0, error)
                self.assertEqual(state.get('spend_usd'), 0.75)
                state.settle_spend(reservations[0]['receipt'], 0.25)
                self.assertEqual(state.get('spend_usd'), 0.75)
                _, amount = state.reserve_spend(3, 5)
                self.assertEqual(amount, 2.25)
                self.assertEqual(state.get('spend_usd'), 3)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()

    def test_invalid_or_conflicting_cost_cannot_refund_reserved_spending(self):
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            self.addCleanup(state.db.close)
            receipt, _ = state.reserve_spend(0.3, 0.1)
            for invalid in (-1, float('nan'), float('inf'), True):
                with self.assertRaises(ValueError):
                    state.settle_spend(receipt, invalid)
            self.assertEqual(state.get('spend_usd'), 0.1)
            state.settle_spend(receipt, 0.05)
            with self.assertRaisesRegex(ValueError, 'different cost'):
                state.settle_spend(receipt, 0)
            self.assertEqual(state.get('spend_usd'), 0.05)
            state.reserve_spend(0.3, 0.1)
            _, remaining = state.reserve_spend(0.3, 0.2)
            self.assertEqual(remaining, 0.15)
            self.assertEqual(state.get('spend_usd'), 0.3)


if __name__ == '__main__':
    unittest.main()
