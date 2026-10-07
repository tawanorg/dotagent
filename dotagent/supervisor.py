"""External process supervision only. Mastra owns ticket execution and retries."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid
import json
import urllib.request
import webbrowser

from .hosts import command, process_record, stop_group
from .runtime import recover_child, recover_iteration
from .state import State, atomic


def open_studio(state, config, timeout=30):
    """Open only after the project's Mastra API is ready; None means not ready."""
    base = f"http://127.0.0.1:{config.get('studio', {}).get('port', 4111)}"
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(base + '/api/workflows', timeout=1) as response:
                ready = 'dotagent' in json.loads(response.read(1024 * 1024))
            if ready:
                url = base + '/workflows'
                state.set('studio_url', url)
                opened = webbrowser.open(url, new=2)
                print('Studio: ' + url, flush=True)
                return opened
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.2)


def record_failure(state, config, reason, phase=None):
    """Count crashes durably; preserve elapsed work for the next Mastra checkpoint."""
    failures = state.get('process_failures', 0) + 1
    state.set('process_failures', failures)
    state.set('intake_error', reason)
    if phase:
        task = state.task(phase['ticket'])
        if (task and task['status'] == 'active' and task['phase'] == phase['phase']
                and task['attempts'] + 1 == phase['attempt']):
            outcome = task.setdefault('pending_result', {'error': reason, 'receipt': str(uuid.uuid4())})
            outcome['seconds'] = max(outcome.get('seconds', 0), time.time() - phase['started'])
            state.save(task)
            state.handover(task)
    if failures >= config['limits']['max_failures']:
        state.set('paused', True)
        state.set('supervisor_blocker', reason)
    return failures


def recover_task(state, config, reason, failed=True):
    """Reap one registered task group; never stop another worktree's worker."""
    phase = state.get('executing_phase')
    record = state.get('iteration_worker')
    recover_child(state)
    if record:
        actual = command(['ps', '-p', str(record['pid']), '-o', 'lstart=,pgid='], check=False).stdout.strip()
        owned = bool(actual and actual == record.get('identity'))
        adapter = state.get('bridge_worker')
        if not owned and adapter:
            actual = command(['ps', '-p', str(adapter['pid']), '-o', 'lstart=,pgid='], check=False).stdout.strip()
            owned = bool(actual and actual == adapter.get('identity') and actual.split()[-1] == str(record['pid']))
        if owned:
            try:
                os.killpg(record['pid'], signal.SIGTERM)
                time.sleep(0.2)
                os.killpg(record['pid'], signal.SIGKILL)
            except ProcessLookupError:
                pass
    # A surviving action must never overlap its replacement.
    with state.lock('iteration'):
        if phase:
            task = state.task(phase['ticket'])
            if (task and task['status'] == 'active' and task['phase'] == phase['phase']
                    and task['attempts'] + 1 == phase['attempt']):
                outcome = task.setdefault('pending_result', {
                    **({'error': reason} if failed else {'interrupted': True}), 'receipt': str(uuid.uuid4())})
                outcome['seconds'] = max(outcome.get('seconds', 0), time.time() - phase['started'])
                state.save(task)
                state.handover(task)
        for key in ('iteration_worker', 'executing_phase', 'bridge_worker'):
            state.set(key, None)


def recover_tasks(state, config, reason, failed=True):
    for scope in state.scopes():
        scoped = State(state.root, scope=scope)
        try:
            recover_task(scoped, config, reason, failed)
        finally:
            scoped.db.close()


def recover(state, config):
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
    recover_iteration(state)  # Legacy unscoped worker migration.
    recover_tasks(state, config, 'Supervisor interrupted during task execution')
    state.set('executing_phase', None)


def stalled_task(state, config):
    active = state.get('executing_phase')
    if active:
        if time.time() - active['started'] > config['limits']['iteration_wall_seconds']:
            return 'Task phase exceeded wall time'
    else:
        runner = state.get('iteration_worker')
        if runner and time.time() - max(runner['started'], state.get('heartbeat', 0)) > config['host'].get('stall_seconds', 600):
            return 'Task runner stalled outside an engineering phase'
    return None


def supervise(state, config, host, once=False, browser=False):
    root = Path(__file__).resolve().parents[1]
    runner, server = root / 'dist/src/runner.js', root / '.mastra/output/index.mjs'
    if not runner.exists() or not server.exists():
        raise RuntimeError('Mastra build missing; run npm ci && npm run build in the dotagent checkout')
    with state.lock():
        interrupted = state.get('executing_phase')
        recover(state, config)
        if interrupted:
            record_failure(state, config, 'Supervisor interrupted during an engineering phase', interrupted)
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
            failure, failed_phase = None, None
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
                    while all(p.poll() is None for p in processes):
                        if browser and open_studio(state, config, timeout=0) is not None:
                            browser = False
                        for scope in state.scopes():
                            scoped = State(state.root, scope=scope)
                            try:
                                reason = stalled_task(scoped, config)
                                if reason:
                                    recover_task(scoped, config, reason)
                            finally:
                                scoped.db.close()
                        phase = state.get('executing_phase')
                        if phase and time.time() - phase['started'] > config['limits']['iteration_wall_seconds']:
                            raise RuntimeError('Engineering phase exceeded wall time; restarting from durable checkpoint')
                        heartbeat = max(state.get('scheduler_heartbeat', 0), state.get('heartbeat', 0))
                        if time.time() - heartbeat > max(config['jira']['poll_seconds'] + 30, config['host'].get('stall_seconds', 600)):
                            raise RuntimeError('Mastra heartbeat stalled')
                        time.sleep(1)
                    if once and processes[1].poll() == 0:
                        return
                    raise RuntimeError('Mastra process exited; inspect mastra.log')
            except KeyboardInterrupt:
                return
            except Exception as error:
                failure, failed_phase = str(error), state.get('executing_phase')
            finally:
                recover_child(state)
                for child in reversed(processes):
                    stop_group(child)
                recover_tasks(state, config, failure or 'Supervisor stopped', failed=bool(failure))
                state.set('mastra_processes', [])
                state.set('executing_phase', None)
                if failure:
                    failures = record_failure(state, config, failure, failed_phase)
            time.sleep(min(30, config['limits']['backoff_seconds'] * 2 ** failures))
