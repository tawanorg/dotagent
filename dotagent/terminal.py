"""Visible worker output and exclusive handoff to the host's native terminal UI."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shlex
import select
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

from .hosts import command, process_record, codex_mcp_args
from .state import State, redact


def live(record):
    if not record:
        return False
    actual = command(['ps', '-p', str(record['pid']), '-o', 'lstart=,pgid='], check=False).stdout.strip()
    return bool(actual and actual == record.get('identity'))


def resume_command(state, task, config):
    host = task.get('host', 'codex')
    session = None
    for path in sorted(state.directory(task['id']).glob('*/events.jsonl'), key=lambda p:p.stat().st_mtime, reverse=True):
        with path.open() as stream:
            for _ in range(30):
                try:
                    event = json.loads(stream.readline(1024 * 1024))
                    value = event.get('thread_id') if host == 'codex' and event.get('type') == 'thread.started' else event.get('session_id') if host == 'claude' else None
                    if value:
                        session = str(uuid.UUID(value))
                        break
                except (ValueError, TypeError):
                    continue
        if session:
            break
    prompt = (f"The human has taken control of dotagent task {task['id']}. Automation for this task is paused. "
              f"Read {Path(__file__).with_name('PLAYBOOK.md')} and {state.directory(task['id']) / 'handover.md'}. "
              "Inspect current Git state. Continue as an interactive conversation; the earlier JSON output contract "
              "does not apply to this human session. Wait for the human's instructions. Do not resume dotagent yourself.")
    if host == 'codex':
        return ['codex', *(['resume', session] if session else []), '-C', task['worktree'],
                '-s', config.get('host', {}).get('codex_sandbox', 'workspace-write'), '--no-daemon', '--no-alt-screen',
                *codex_mcp_args(config.get('host', {}).get('mcp_servers')), prompt]
    if host == 'claude':
        return ['claude', *(['--resume', session] if session else []), '--permission-mode', 'default', prompt]
    raise ValueError('host must be codex or claude')


def takeover(state, config, key):
    task = state.task(key)
    if not task or not task.get('worktree'):
        raise ValueError('task has no worktree yet')
    if not sys.stdin.isatty():
        raise RuntimeError('takeover needs an interactive terminal')
    scoped = State(state.root, scope=key)
    try:
        with scoped.lock('human-' + scoped._scope_hash) as human_lock, ExitStack() as held:
            if live(state.get('human-session:' + key)):
                raise RuntimeError('human session is already running')
            state.set('human:' + key, True)
            state.set('control_revision', time.time())
            print('Taking control. Waiting for this task to stop writing…', flush=True)
            deadline = time.monotonic() + 120
            while True:
                try:
                    iteration_lock = held.enter_context(scoped.lock('iteration'))
                    break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('task still stopping; remains held for you. Retry takeover shortly')
                    time.sleep(0.2)
            task = state.task(key)
            state.handover(task)
            argv = resume_command(state, task, config)
            env = dict(os.environ)
            env.pop('CLAUDECODE', None)
            # The child keeps ownership if this launcher dies before registration.
            process = subprocess.Popen(argv, cwd=task['worktree'], env=env,
                                       pass_fds=(human_lock.fileno(), iteration_lock.fileno()))
            try:
                state.set('human-session:' + key, process_record(process, key, argv[:-1], state.directory(key)))
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
            try:
                while process.poll() is None:
                    try:
                        process.wait()
                    except KeyboardInterrupt:
                        pass  # Native TUI receives Ctrl-C too; keep ownership until it exits.
            finally:
                if process.poll() is not None:
                    state.set('human-session:' + key, None)
            state.event(key, 'human-session-ended', {'exit_code': process.returncode})
            print('Task remains under human control. Run dotagent resume ' + key + ' to return it to automation.', flush=True)
    finally:
        scoped.db.close()


def return_to_automation(state, key, note=None):
    if not state.get('human:' + key):
        return
    scoped = State(state.root, scope=key)
    try:
        with scoped.lock('human-' + scoped._scope_hash):
            if live(state.get('human-session:' + key)):
                raise RuntimeError('exit the human Claude/Codex session before resuming automation')
            from .supervisor import recover_task
            recover_task(scoped, {}, 'Human returned task to automation', failed=False)
            with scoped.lock('iteration'):
                task = state.task(key)
                if task.get('workflow_run'):
                    task.setdefault('workflow_history', []).append(task.pop('workflow_run'))
                task.pop('pending_result', None)
                task.pop('workflow_failed', None)
                task.update(status='active', phase='implement' if task['criteria'] else 'plan', workflow_api_failures=0,
                            attempts=0, failures=0, stagnant=0, retry_at=0,
                            next_action='Reconcile human changes and rerun all verification', blockers=[])
                state.save(task, allow_reactivate=True)
                state.instruct(key, 'Human session ended. Inspect current changes, preserve them, update the handover, and rerun verification before delivery.')
                if note:
                    state.instruct(key, note)
                state.set('paused', False)
                state.set('process_failures', 0)
                state.set('human:' + key, False)
                return True
    finally:
        scoped.db.close()


def open_terminal(state, config, key):
    if not state.task(key):
        raise ValueError('unknown task')
    if sys.platform != 'darwin' or not config.get('terminal', {}).get('enabled', True):
        return False
    setting = 'terminal:' + key
    previous = state.get(setting)
    if previous:
        actual = command(['ps','-p',str(previous['pid']),'-o','lstart=,pgid='],check=False).stdout.strip()
        if actual and actual == previous.get('identity'):
            return True
    selection = ['--config' if config.get('_legacy') else '--project', config['_path']]
    argv = [sys.executable, str(Path(__file__).parents[1]/'bin/dotagent'), *selection, 'watch', key]
    shell = 'exec ' + shlex.join(argv)
    try:
        result = command(['osascript','-e','tell application "Terminal" to do script '+json.dumps(shell)], timeout=5, check=False)
        if result.returncode:
            raise RuntimeError('Terminal automation unavailable; run dotagent watch '+key)
    except Exception as error:
        state.set('terminal_error', str(error))
        return False
    return True


def event_message(event):
    """Readable host events, shared by watch and dashboard; strip terminal control sequences."""
    item = event.get('item', {})
    kind = event.get('type')
    message = item.get('text', '') if kind == 'item.completed' and item.get('type') == 'agent_message' else ''
    if item.get('type') == 'command_execution' and kind in ('item.started', 'item.completed'):
        message = ('Running: ' if kind == 'item.started' else f"Exit {item.get('exit_code')}: ") + item.get('command', '')
    if item.get('type') == 'mcp_tool_call' and kind in ('item.started', 'item.completed'):
        message = f"{kind}: {item.get('server', '')}/{item.get('tool', '')}"
    if kind == 'assistant':
        parts = event.get('message', {}).get('content', [])
        message = '\n'.join(c.get('text', '') for c in parts if c.get('type') == 'text')
        calls = [c.get('name', '') for c in parts if c.get('type') == 'tool_use']
        if calls:
            message += '\nTools: ' + ', '.join(calls)
    import re
    message = re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))', '', message)
    return redact(''.join(c for c in message if c in '\n\t' or ord(c) >= 32 and ord(c) != 127))


def watch(state, config, key):
    if not state.task(key):
        raise ValueError('unknown task')
    record = process_record(SimpleNamespace(pid=os.getpid()), key, [], state.root)
    state.set('terminal:'+key, record)
    print(f"dotagent · {config.get('project_name','project')} · {key}", flush=True)
    print('Live worker · Enter or Ctrl-C: take over in Claude/Codex · q then Enter: close view', flush=True)
    previous, offsets = None, {}
    try:
        while True:
            task = state.task(key)
            current = (task['status'],task['phase'],task['next_action'],json.dumps(task['blockers']))
            if current != previous:
                print(f"\n[{time.strftime('%H:%M:%S')}] {task['status']} / {task['phase']} · {task['next_action']}",flush=True)
                if task.get('worktree'): print(task['worktree'],flush=True)
                for blocker in task['blockers']: print('Blocked: '+blocker,flush=True)
                if task['delivery'].get('pr'): print('PR: '+task['delivery']['pr'],flush=True)
                previous = current
            # Show work and command outcomes; avoid raw auth/usage envelopes.
            for path in sorted(state.directory(key).glob('*/events.jsonl'), key=lambda p:p.stat().st_mtime)[-3:]:
                with path.open() as stream:
                    stream.seek(offsets.get(str(path),0))
                    while True:
                        position=stream.tell();line=stream.readline()
                        if not line or not line.endswith('\n'):
                            offsets[str(path)]=position
                            break
                        try:
                            event=json.loads(line)
                        except ValueError:
                            continue
                        message = event_message(event)
                        if message: print(message, flush=True)
            if sys.stdin.isatty() and select.select([sys.stdin], [], [], 1)[0]:
                if sys.stdin.readline().strip().lower() == 'q':
                    return
                takeover(state, config, key)
                return
            if not sys.stdin.isatty():
                time.sleep(1)
    except KeyboardInterrupt:
        if sys.stdin.isatty():
            takeover(state, config, key)
    finally:
        if state.get('terminal:'+key)==record: state.set('terminal:'+key,None)
