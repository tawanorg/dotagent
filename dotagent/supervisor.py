"""External process supervision only. Mastra owns ticket execution and retries."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from .hosts import command, process_record, stop_group
from .runtime import recover_child
from .state import atomic


def recover(state):
    recover_child(state)
    for record in state.get('mastra_processes', []):
        actual = command(['ps', '-p', str(record['pid']), '-o', 'lstart=,pgid='], check=False).stdout.strip()
        if actual and actual == record.get('identity'):
            try:
                os.killpg(record['pid'], signal.SIGTERM)
                time.sleep(0.2)
                current = command(['ps', '-p', str(record['pid']), '-o', 'lstart=,pgid='], check=False).stdout.strip()
                if current == actual:
                    os.killpg(record['pid'], signal.SIGKILL)
            except ProcessLookupError:
                pass
    state.set('mastra_processes', [])
    state.set('executing_phase', None)


def supervise(state, config, host, once=False):
    root = Path(__file__).resolve().parents[1]
    runner, server = root / 'dist/src/runner.js', root / '.mastra/output/index.mjs'
    if not runner.exists() or not server.exists():
        raise RuntimeError('Mastra build missing; run npm ci && npm run build in the dotagent checkout')
    with state.lock():
        recover(state)
        port = config.get('studio', {}).get('port', 4111)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
        config['_host'] = host
        config_file = state.root / 'runtime-config.json'
        atomic(config_file, config)
        env = dict(os.environ, DOTAGENT_RUNTIME_CONFIG=str(config_file),
                   DOTAGENT_PYTHON=sys.executable, PYTHONPATH=str(root),
                   MASTRA_STUDIO_PATH=str(root / '.mastra/output/studio'))
        state.set('supervisor', {'pid': os.getpid(), 'started': time.time(), 'host': host})
        state.set('studio_url', f'http://127.0.0.1:{port}/workflows')
        failures = state.get('process_failures', 0)
        while True:
            while state.get('process_failures', 0) >= config['limits']['max_failures']:
                if once:
                    return
                time.sleep(1)
            failures = state.get('process_failures', 0)
            processes = []
            state.set('scheduler_heartbeat', time.time())
            try:
                with (state.root / 'mastra.log').open('a') as output:
                    for name, path in [('studio', server), ('scheduler', runner)]:
                        child_env = dict(env, DOTAGENT_EXECUTE='1' if name == 'scheduler' else '0',
                                         DOTAGENT_ONCE='1' if once else '0')
                        child = subprocess.Popen(['node', str(path)], cwd=root, env=child_env,
                            stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                        processes.append(child)
                        state.set('mastra_processes', [process_record(p, None, ['node'], state.root) for p in processes])
                    started = time.monotonic()
                    while all(p.poll() is None for p in processes):
                        phase = state.get('executing_phase')
                        if phase and time.time() - phase['started'] > config['limits']['iteration_wall_seconds']:
                            raise RuntimeError('Engineering phase exceeded wall time; restarting from durable checkpoint')
                        heartbeat = max(state.get('scheduler_heartbeat', 0), state.get('heartbeat', 0))
                        if time.time() - heartbeat > max(config['jira']['poll_seconds'] + 30, config['host'].get('stall_seconds', 600)):
                            raise RuntimeError('Mastra heartbeat stalled')
                        if time.monotonic() - started > 60:
                            failures = 0
                            state.set('process_failures', 0)
                        time.sleep(1)
                    if once and processes[1].poll() == 0:
                        return
                    raise RuntimeError('Mastra process exited; inspect mastra.log')
            except KeyboardInterrupt:
                return
            except Exception as error:
                failures += 1
                state.set('process_failures', failures)
                state.set('intake_error', str(error))
                if failures >= config['limits']['max_failures']:
                    state.set('paused', True)
                    # Stay alive for inspection; explicit startup clears the crash budget.
                    state.set('supervisor_blocker', str(error))
            finally:
                recover_child(state)
                for child in reversed(processes):
                    stop_group(child)
                state.set('mastra_processes', [])
                state.set('executing_phase', None)
            time.sleep(min(30, config['limits']['backoff_seconds'] * 2 ** failures))
