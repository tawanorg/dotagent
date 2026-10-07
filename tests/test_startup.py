import http.server
import tempfile
import threading
import unittest
from unittest.mock import patch
from dotagent.state import State
from dotagent.supervisor import open_studio


class StartupTests(unittest.TestCase):
    def test_start_opens_the_ready_project_workflow_dashboard(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"dotagent": {}}')
            def log_message(self, *_):
                pass
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_port
            try:
                with patch('webbrowser.open', return_value=True) as browser:
                    self.assertTrue(open_studio(state, {'studio': {'port': port}}, timeout=2))
                    browser.assert_called_once_with(f'http://127.0.0.1:{port}/workflows', new=2)
            finally:
                server.shutdown()
                server.server_close()
                state.db.close()

    def test_recovery_stops_scheduler_before_reaping_detached_workers(self):
        from dotagent.supervisor import recover
        from types import SimpleNamespace
        events = []
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            state.set('mastra_processes', [{'pid': 321, 'identity': 'owned 321'}])
            with patch('dotagent.supervisor.command', return_value=SimpleNamespace(stdout='owned 321')), \
                 patch('dotagent.supervisor.os.killpg', side_effect=lambda *_: events.append('stop-scheduler')), \
                 patch('dotagent.supervisor.time.sleep'), \
                 patch('dotagent.supervisor.recover_iteration'), \
                 patch('dotagent.supervisor.recover_tasks', side_effect=lambda *_: events.append('recover-tasks')):
                recover(state, {})
            self.assertEqual(events[-1], 'recover-tasks')
            self.assertEqual(events[0], 'stop-scheduler')
            state.db.close()

    def test_watchdog_detects_stalled_mastra_calls_without_killing_live_phases(self):
        import time
        from dotagent.supervisor import stalled_task
        with tempfile.TemporaryDirectory() as root:
            state = State(root, scope='TASK-1')
            config = {'host': {'stall_seconds': 10}, 'limits': {'iteration_wall_seconds': 100}}
            state.set('iteration_worker', {'started': time.time() - 20})
            self.assertIn('outside', stalled_task(state, config))
            state.set('heartbeat', time.time())
            self.assertIsNone(stalled_task(state, config))
            state.set('heartbeat', time.time() - 20)
            state.set('executing_phase', {'started': time.time() - 20})
            self.assertIsNone(stalled_task(state, config))
            state.set('executing_phase', {'started': time.time() - 101})
            self.assertIn('wall time', stalled_task(state, config))
            state.db.close()
