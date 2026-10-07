"""Native CLI authentication; no extraction or reuse of OAuth credentials."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import tomllib

from .state import atomic


class Interrupted(RuntimeError):
    pass


def stop_group(process):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def process_record(process, task, argv, directory):
    identity = command(['ps', '-p', str(process.pid), '-o', 'lstart=,pgid='], check=False).stdout.strip()
    return {'pid': process.pid, 'task': task, 'argv': argv, 'directory': str(directory),
            'identity': identity, 'started': time.time()}


def command(argv, cwd=None, env=None, timeout=120, check=True):
    result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        # Full output belongs in private logs, never exception messages containing credentials.
        raise RuntimeError(f'{argv[0]} failed (exit {result.returncode})')
    return result


def codex_mcp_args(only):
    if only is None:
        return []
    path = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'config.toml'
    config = tomllib.loads(path.read_text()) if path.exists() else {}
    return [arg for name in config.get('mcp_servers', {}) if name not in only
            for arg in ['-c', f'mcp_servers.{name}.enabled=false']]


def host_command(host, cwd, directory, schema, config, bridge=False):
    if host == 'codex':
        atomic(directory / 'schema.json', schema)
        return ['codex', 'exec', '--json', '--color', 'never', '-C', str(cwd),
                '-s', 'read-only' if bridge else config.get('codex_sandbox', 'workspace-write'),
                '--add-dir', str(directory), '--output-schema', str(directory / 'schema.json'),
                '-o', str(directory / 'result.json'),
                *codex_mcp_args(['atlassian'] if bridge else config.get('mcp_servers')),
                *config.get('codex_args', []), '-']
    if host == 'claude':
        return ['claude', '-p', '--output-format', 'stream-json', '--verbose',
                '--permission-mode', config.get('claude_permission_mode', 'acceptEdits'),
                '--permission-prompts', 'none', '--add-dir', str(directory),
                '--json-schema', json.dumps(schema),
                *(['--max-budget-usd', str(config['iteration_budget_usd'])]
                  if config.get('iteration_budget_usd') else []),
                *config.get('claude_args', [])]
    raise ValueError('host must be codex or claude')


def execute(host, prompt, cwd, directory, schema, config, state, task=None, bridge=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    result_path = directory / 'result.json'
    result_path.unlink(missing_ok=True)
    atomic(directory / 'prompt.md', prompt)
    config = dict(config)
    reservation = 0
    if config.get('max_spend_usd') is not None:
        if host != 'claude':
            raise RuntimeError('hard USD budget unavailable for this host; configure time/token limits')
        spent = state.get('spend_usd', 0)
        reservation = min(config.get('iteration_budget_usd', 5), config['max_spend_usd'] - spent)
        if reservation <= 0:
            raise RuntimeError('persistent spending budget exhausted')
        # Reserve before spawn. A crash/unknown cost retains this conservative charge.
        state.set('spend_usd', spent + reservation)
        config['iteration_budget_usd'] = reservation
    argv = host_command(host, cwd, directory, schema, config, bridge)
    started = time.monotonic()
    last_size, last_activity = 0, started
    timeout = config.get('iteration_seconds', 1800)
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)
    with open(directory / 'events.jsonl', 'w') as log, open(directory / 'stderr.log', 'w') as err:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=log, stderr=err, text=True, start_new_session=True)
        try:
            state.set('worker', process_record(process, task, argv, directory))
            process.stdin.write(prompt)
            process.stdin.close()
            while process.poll() is None:
                now = time.monotonic()
                state.set('heartbeat', time.time())
                size = os.fstat(log.fileno()).st_size + os.fstat(err.fileno()).st_size
                if size > config.get('max_log_bytes', 50 * 1024 * 1024):
                    raise RuntimeError('worker log size limit reached; checkpoint retained')
                if size != last_size:
                    last_size, last_activity = size, now
                task_state = state.task(task) if task else None
                if state.get('paused') or (task_state and task_state['status'] == 'cancelled'):
                    raise Interrupted('paused or cancelled')
                if now - started > timeout:
                    raise RuntimeError('worker time limit; saved handover will be loaded on retry')
                if now - last_activity > config.get('stall_seconds', 600):
                    raise RuntimeError('worker stalled without output')
                time.sleep(1)
        finally:
            stop_group(process)
            state.set('worker', None)
            if reservation:
                # Account even failed/interrupted sessions; unknown cost keeps full reservation.
                for line in (directory / 'events.jsonl').read_text().splitlines():
                    try:
                        event = json.loads(line)
                        actual = event.get('total_cost_usd')
                        if event.get('type') == 'result' and isinstance(actual, (int, float)):
                            state.set('spend_usd', state.get('spend_usd') - reservation + actual)
                            break
                    except json.JSONDecodeError:
                        pass
    usage, result = {'usd': None, 'tokens': None}, None
    for line in (directory / 'events.jsonl').read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get('type') == 'turn.completed':
            usage['tokens'] = event.get('usage')
        if event.get('type') == 'result':
            usage['usd'] = event.get('total_cost_usd')
            usage['tokens'] = event.get('usage')
            result = event.get('structured_output')
            if event.get('is_error'):
                raise RuntimeError('Claude reported execution error; see private logs')
    state.event(task, 'host_usage', usage)
    if process.returncode:
        raise RuntimeError(f'{host} exited {process.returncode}; see {directory / "stderr.log"}')
    if host == 'codex':
        result = json.loads(result_path.read_text())
    elif result is not None:
        atomic(result_path, result)
    if not isinstance(result, dict):
        raise RuntimeError('host did not return a structured checkpoint')
    return result, usage
