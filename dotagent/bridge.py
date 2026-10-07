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
from .integrations import Jira, select_ticket
from .hosts import Interrupted


def view(state, task):
    evidence = [(e['criterion'], e['revision']) for e in task['evidence'] if e['exit_code'] == 0]
    content = revision(task['worktree']) if task.get('worktree') and Path(task['worktree']).exists() else ''
    progress = hashlib.sha256(json.dumps([content, evidence, task.get('verified_revision'),
                                         task['delivery']], sort_keys=True).encode()).hexdigest()
    return dict(ticket=task['id'], runId=task['workflow_run'], phase=task['phase'],
                status=task['status'], attempts=task['attempts'], failures=task['failures'],
                stagnant=task['stagnant'], elapsed=task['elapsed'], cost=task['cost'],
                costKnown=task['cost_known'], paused=bool(state.get('paused')),
                retryAt=task.get('retry_at', 0), nextAction=task['next_action'],
                blockers=task['blockers'], progress=progress)


def dispatch(state, config, operation, data):
    state.set('scheduler_heartbeat', time.time())
    if operation == 'control':
        return {'revision': state.get('control_revision', 0), 'paused': bool(state.get('paused'))}
    if operation == 'next':
        if state.get('paused'):
            return {'paused': True, 'task': None}
        if not config['repository'].get('keep_inactive_environments', False):
            for task in state.tasks():
                env = task.get('environment', {})
                if task['status'] in ('blocked', 'cancelled', 'review') and env and not env.get('stopped_at'):
                    Environment(state, task, config['repository']).stop()
        active = [t for t in state.tasks() if t['status'] == 'active']
        task = active[0] if active else None
        if not task:
            tickets = Jira(state, config['jira'], config['host']).intake()
            ticket = select_ticket(tickets, config['jira'], {t['id'] for t in state.tasks()})
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
        return [t['workflow_run'] for t in state.tasks() if t['status'] == 'cancelled' and t.get('workflow_run')]
    if operation == 'recover':
        recover_child(state)
        return {}
    task = state.task(data['ticket'])
    if not task or task.get('workflow_run') != data.get('runId'):
        raise RuntimeError('Task does not belong to this workflow run')
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
            if state.get('paused') or task['status'] != 'active':
                return {'interrupted': True}
            if data['phase'] != task['phase'] or data['attempt'] != task['attempts'] + 1:
                return {'reconciled': True}
            pending = task.get('pending_result')
            if pending:
                # Replay a completed action instead of repeating its external mutations.
                return pending
            state.set('executing_phase', {'ticket': task['id'], 'phase': task['phase'], 'started': time.time()})
            try:
                outcome = perform(state, task, config)
            except Interrupted:
                return {'interrupted': True}
            except Exception as error:
                state.event(task['id'], 'failure', {'error': str(error)})
                outcome = {'error': str(error)}
            finally:
                state.set('executing_phase', None)
            outcome.update(receipt=str(uuid.uuid4()), progress=view(state, task)['progress'])
            task['pending_result'] = outcome
            state.save(task)
            state.handover(task)
            return outcome
    if operation == 'checkpoint':
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
        state.handover(task)
        return view(state, task)
    raise RuntimeError('Unknown bridge operation')


def main():
    os.umask(0o077)
    config = json.loads(Path(os.environ['DOTAGENT_RUNTIME_CONFIG']).read_text())
    state = State(config['state_dir'])
    data = json.load(sys.stdin)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = dispatch(state, config, data['operation'], data.get('input', {}))
        print(json.dumps(redact(result)))
    except Exception as error:
        print(json.dumps({'bridgeError': redact(str(error))}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
