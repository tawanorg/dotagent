"""One guarded operation per call; lifecycle transitions are chosen by Mastra."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

from .state import State, atomic, redact
from .runtime import perform, reconcile, recover_child
from .environment import Environment, revision
from .tasks import intake
from .hosts import Interrupted


def view(state, task):
    evidence = sorted({(e['criterion'], e['revision']) for e in task['evidence'] if e['exit_code'] == 0})
    content = revision(task['worktree']) if task.get('worktree') and Path(task['worktree']).exists() else ''
    progress = hashlib.sha256(json.dumps([content, evidence, task.get('verified_revision'),
                                         task['delivery'], task.get('instructions_ack', 0)], sort_keys=True).encode()).hexdigest()
    return dict(ticket=task['id'], runId=task['workflow_run'], phase=task['phase'],
                worktree=task.get('worktree', ''), branch=task.get('branch', ''), host=task.get('host', ''),
                status=task['status'], attempts=task['attempts'], failures=task['failures'],
                stagnant=task['stagnant'], elapsed=task['elapsed'], cost=task['cost'],
                costKnown=task['cost_known'], paused=bool(state.get('paused') or state.get('human:' + task['id'])),
                retryAt=task.get('retry_at', 0), nextAction=task['next_action'],
                blockers=task['blockers'], progress=progress, runnerLog=str(state.directory(task['id']) / 'worker.log'))


def dispatch(state, config, operation, data):
    if state.scope is None:
        state.set('scheduler_heartbeat', time.time())
    else:
        state.set('heartbeat', time.time())
    if operation == 'control':
        from .github_mentions import poll
        try:
            poll(state, config)
        except Exception as error:
            state.set('github_mentions_error', str(error))
        return {'revision': state.get('control_revision', 0), 'paused': bool(state.get('paused')),
                'workers': state.get('preferred_workers', config.get('concurrency', 1))}
    if operation == 'next':
        if state.get('paused'):
            return {'paused': True, 'task': None}
        if not config['repository'].get('keep_inactive_environments', False):
            for task in state.tasks():
                env = task.get('environment', {})
                if (task['status'] in ('blocked', 'cancelled', 'review') and env and not env.get('stopped_at')
                        and not state.get('human:' + task['id'])
                        and task['id'] not in data.get('exclude', [])):
                    Environment(state, task, config['repository']).stop()
        excluded = set(data.get('exclude', []))
        for saved in state.tasks():
            notes = state.instructions(saved['id'])
            if saved['id'] not in excluded and not state.get('human:' + saved['id']) and saved['status'] == 'review' and notes and notes[-1]['seq'] > saved.get('instructions_ack', 0):
                saved.setdefault('workflow_history', []).append(saved.pop('workflow_run', None))
                saved.update(status='active', phase='implement', attempts=0, failures=0, stagnant=0, retry_at=0, blockers=[])
                state.save(saved)
        active = [t for t in state.tasks() if t['status'] == 'active' and t['id'] not in excluded and not state.get('human:' + t['id'])
                  and t.get('retry_at', 0) <= time.time()]
        task = active[0] if active else None
        if not task:
            ticket = intake(state, config)
            if ticket:
                task = state.claim(ticket, config['repository']['path'])
        if not task:
            return {'paused': False, 'task': None}
        task['host'] = state.get('preferred_host', config.get('_host', 'codex'))
        task.setdefault('workflow_run', str(uuid.uuid4()))
        try:
            reconcile(state, task, config)
        except Exception as error:
            task['status'], task['blockers'] = 'blocked', [str(error)]
        state.save(task)
        return {'paused': False, 'task': view(state, task)}
    if operation == 'intake-error':
        state.set('intake_error', data.get('error'))
        return {}
    if operation == 'cancelled-runs':
        return [t['workflow_run'] for t in state.tasks() if t['status'] == 'cancelled' and t.get('workflow_run') and t['id'] not in data.get('exclude', [])]
    if operation == 'recover':
        recover_child(state)
        return {}
    task = state.task(data['ticket'])
    if operation == 'runner-ended' and task and task.get('workflow_run') != data.get('runId'):
        # A human handoff can replace the run before the old child's close event.
        current = state.get('iteration_worker')
        if current and current['pid'] == data['pid']:
            from .supervisor import recover_task
            recover_task(state, config, 'Superseded task runner stopped', failed=False)
        return {}
    if not task or task.get('workflow_run') != data.get('runId'):
        raise RuntimeError('Task does not belong to this workflow run')
    if operation == 'register-runner':
        from .hosts import process_record
        from types import SimpleNamespace
        if state.get('iteration_worker'):
            raise RuntimeError('Task runner already registered')
        state.set('iteration_worker', process_record(SimpleNamespace(pid=data['pid']), task['id'], data['argv'], state.directory(task['id'])))
        if config.get('_path'):
            from .terminal import open_terminal
            open_terminal(state, config, task['id'])
        return {}
    if operation == 'runner-ended':
        from .supervisor import recover_task
        phase = state.get('executing_phase')
        current = state.get('iteration_worker')
        if current and current['pid'] == data['pid']:
            recover_task(state, config, 'Task runner exited unexpectedly')
        if data.get('code') != 0 and not phase and not state.task(task['id']).get('pending_result'):
            return dispatch(state, config, 'workflow-api-error', {**data, 'reason': 'Task runner exited before checkpoint'})
        return {}
    if operation == 'workflow-api-error':
        task['workflow_api_failures'] = task.get('workflow_api_failures', 0) + 1
        task['retry_at'] = time.time() + min(300, 2 ** task['workflow_api_failures'] * config['limits']['backoff_seconds'])
        if task['workflow_api_failures'] >= config['limits']['max_failures']:
            task['status'], task['workflow_failed'] = 'blocked', True
            task['blockers'] = ['Repeated Mastra API failure; inspect logs: ' + data['reason']]
        state.save(task)
        return view(state, task)
    if operation == 'workflow-api-ok':
        task['workflow_api_failures'] = 0
        state.save(task)
        return {}
    if operation == 'workflow-terminal':
        task['status'] = 'blocked'
        task['workflow_failed'] = True
        task['blockers'] = ['Mastra run failed; inspect Studio, resolve and resume: ' + data['reason']]
        state.save(task)
        return view(state, task)
    if operation == 'read':
        return view(state, task)
    if operation == 'perform':
        with state.lock('iteration'):
            if state.get('paused') or state.get('human:' + task['id']) or task['status'] != 'active':
                return {'interrupted': True}
            if data['phase'] != task['phase'] or data['attempt'] != task['attempts'] + 1:
                return {'reconciled': True}
            pending = task.get('pending_result')
            if pending:
                # Replay a completed action instead of repeating its external mutations.
                return pending
            from .hosts import process_record
            from types import SimpleNamespace
            state.set('bridge_worker', process_record(SimpleNamespace(pid=os.getpid()), task['id'], [], state.directory(task['id'])))
            state.set('executing_phase', {'ticket': task['id'], 'phase': task['phase'], 'attempt': task['attempts'] + 1, 'started': time.time()})
            try:
                outcome = perform(state, task, config)
            except Interrupted:
                return {'interrupted': True}
            except Exception as error:
                state.event(task['id'], 'failure', {'error': str(error)})
                outcome = {'error': str(error)}
            finally:
                state.set('executing_phase', None)
                state.set('bridge_worker', None)
            outcome.update(receipt=str(uuid.uuid4()), progress=view(state, task)['progress'])
            task['pending_result'] = outcome
            state.save(task)
            state.handover(task)
            return outcome
    if operation == 'checkpoint':
        if state.get('human:' + task['id']):
            return view(state, task)
        receipt = data.get('receipt')
        if receipt and task.get('last_receipt') == receipt:
            return view(state, task)
        if data.get('expectedAttempts', task['attempts']) != task['attempts']:
            return view(state, task)
        pending = task.get('pending_result')
        if receipt and (not pending or pending['receipt'] != receipt):
            raise RuntimeError('Checkpoint receipt does not match completed action')
        allowed = {'phase', 'status', 'attempts', 'failures', 'stagnant', 'elapsed', 'nextAction', 'blockers', 'retryAt'}
        patch = data['patch']
        if set(patch) - allowed:
            raise RuntimeError('Invalid lifecycle checkpoint fields')
        for key, value in patch.items():
            task[{'nextAction': 'next_action', 'retryAt': 'retry_at'}.get(key, key)] = value
        if receipt:
            task['last_receipt'] = receipt
            task.pop('pending_result', None)
        instructions = state.instructions(task['id'])
        if task['status'] == 'review' and instructions and instructions[-1]['seq'] > task.get('instructions_ack', 0):
            task.update(status='active', phase='implement', next_action='Apply new user instructions')
        if task['status'] == 'review' and task['delivery'].get('pr'):
            from .memory import ProjectMemory
            memory = ProjectMemory(config)
            try:
                lesson = (task.get('summary', task['ticket']['summary']) + '\n' + task.get('implementation', '')
                          + '\nDecisions: ' + '; '.join(task['decisions']))[:20000]
                memory.remember(lesson, source=task['delivery']['pr'], kind='lesson', revision=task.get('commit'))
            finally:
                memory.close()
        state.save(task)
        if patch.get('stagnant') == 0 and patch.get('failures') == 0:
            state.set('process_failures', 0)
        state.handover(task)
        return view(state, task)
    raise RuntimeError('Unknown bridge operation')


def main():
    os.umask(0o077)
    config = json.loads(Path(os.environ['DOTAGENT_RUNTIME_CONFIG']).read_text())
    data = json.load(sys.stdin)
    state = State(config['state_dir'], scope=data.get('input', {}).get('ticket'))
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = dispatch(state, config, data['operation'], data.get('input', {}))
        print(json.dumps(redact(result)))
    except Exception as error:
        print(json.dumps({'bridgeError': redact(str(error))}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
