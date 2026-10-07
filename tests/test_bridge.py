"""Exercise the real JSON subprocess boundary, including a saved crash checkpoint."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dotagent.state import State


class BridgeTests(unittest.TestCase):
    def test_replayed_action_and_stale_workflow_cannot_repeat_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'runtime-config.json'
            config.write_text(json.dumps({'state_dir': directory, 'repository': {}}))
            state = State(directory)
            task = state.claim({'key': 'TEST-1', 'summary': 'Delivery'}, directory)
            task.update(workflow_run='run-1', phase='deliver', pending_result={
                'receipt': 'receipt-1', 'pr': 'https://github.com/example/repo/pull/1',
                'artifacts': False,
            })
            state.save(task)

            def call(operation, **data):
                result = subprocess.run([sys.executable, '-m', 'dotagent.bridge'],
                    input=json.dumps({'operation': operation, 'input': {
                        'ticket': 'TEST-1', 'runId': 'run-1', **data,
                    }}), text=True, capture_output=True,
                    env=dict(os.environ, DOTAGENT_RUNTIME_CONFIG=str(config)))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)

            self.assertEqual(call('perform', phase='deliver', attempt=1)['receipt'], 'receipt-1')
            call('checkpoint', receipt='receipt-1', expectedAttempts=0,
                 patch={'phase': 'jira', 'attempts': 1})
            self.assertEqual(call('perform', phase='deliver', attempt=1), {'reconciled': True})
            stale = call('checkpoint', expectedAttempts=0, patch={'phase': 'deliver', 'attempts': 1})
            self.assertEqual(stale['phase'], 'jira')
            replay = call('checkpoint', receipt='receipt-1', patch={'phase': 'deliver'})
            self.assertEqual(replay['phase'], 'jira')
            state.db.close()


if __name__ == '__main__':
    unittest.main()
