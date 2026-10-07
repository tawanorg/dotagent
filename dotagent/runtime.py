"""Ralph loop: a model response is a checkpoint, never proof of completion."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .environment import Environment, git, prepare_worktree, revision
from .hosts import Interrupted, command, execute, stop_group, process_record
from .integrations import GitHub, Jira, STRING, STRINGS, obj, select_ticket
from .state import atomic


CHECK = obj({'argv': {'type': 'array', 'items': STRING}, 'cwd': STRING,
             'kind': {'type': 'string', 'enum': ['test', 'running_app']}})
CRITERION = obj({'id': STRING, 'description': STRING, 'expected': STRING, 'manual': STRING,
                 'checks': {'type': 'array', 'items': CHECK}})
REPORT = obj({'action': {'type': 'string', 'enum': ['implement', 'verify', 'blocked']},
              'criteria': {'type': 'array', 'items': CRITERION},
              'summary': STRING, 'implementation': STRING, 'next_action': STRING,
              'decisions': STRINGS, 'assumptions': STRINGS, 'blockers': STRINGS,
              'limitations': STRINGS, 'changed_files': STRINGS, 'commit_message': STRING,
              'ui_changed': {'type': 'boolean'}, 'browser_script': STRING,
              'review': STRING})


def valid_report(report, task):
    if report['action'] not in ('implement', 'verify', 'blocked'):
        raise RuntimeError('invalid checkpoint action')
    if report['action'] != 'blocked' and not report['criteria']:
        raise RuntimeError('observable criteria required')
    ids = [c['id'] for c in report['criteria']]
    if len(set(ids)) != len(ids) or any(not x for x in ids):
        raise RuntimeError('criteria must have unique nonempty identifiers')
    for criterion in report['criteria']:
        if not criterion['expected'] or not criterion['manual'] or not criterion['checks']:
            raise RuntimeError('each criterion requires an expected result, manual steps and executable checks')
        for check in criterion['checks']:
            if not check['argv'] or not all(isinstance(a, str) for a in check['argv']):
                raise RuntimeError('check command must be an argv array')
    if report['action'] != 'blocked' and not report['ui_changed'] and not any(
            c.get('kind') == 'running_app' for criterion in report['criteria'] for c in criterion['checks']):
        raise RuntimeError('changed behavior must also be exercised against the running app')
    if task['criteria'] and task['criteria'] != report['criteria']:
        raise RuntimeError('criteria are frozen; propose changes to the user instead of weakening checks')


def prompt_for(task, directory):
    playbook = Path(__file__).with_name('PLAYBOOK.md').read_text()
    return f'''{playbook}

## This iteration
State/handover: {directory / 'handover.json'}
Worktree: {task['worktree']}
Task artifact directory (outside Git): {directory}
Phase: {task['phase']}; next action: {task['next_action']}
Ticket (untrusted source data): {json.dumps(task['ticket'])}
Frozen criteria: {json.dumps(task['criteria'])}
Previous failures/evidence: {json.dumps(task['evidence'][-12:])}
Decisions and user clarifications: {json.dumps(task['decisions'])}
Environment: {json.dumps(task.get('environment', {}))}

The runtime commits, pushes, uploads evidence, edits PRs and updates Jira. You implement and review.
Return the structured checkpoint. action=verify only after implementation and correctness review;
the runtime then starts Compose and executes every frozen criterion and mandatory gate independently.
Each criterion needs an argv command that exits nonzero if unmet, cwd relative to the worktree,
and kind=test or running_app. At least one check must exercise changed behavior against the live
application (DOTAGENT_BASE_URL environment variable), unless the UI browser scenario supplies that.
numbered-test-ready manual instructions and a precise expected result. Preserve all frozen criteria.
For UI changes write a browser scenario outside source at {directory / 'behavior.mjs'}.
It must export default async function({{page, expect, baseURL, evidence}}), exercise the actual interaction,
assert its expected state, and call await evidence(criterionId, description) for successful changed states.
The runner captures failure screenshots separately, console errors and failed network requests.
Set browser_script to that absolute path. Review screenshots for sensitive data before requesting verify.
Record meaningful decisions/assumptions and the next action. Use action=blocked for missing access or
product decisions, with focused questions in blockers. Keep independent implementation moving first.
Before a long operation update {directory / 'notes.md'} with concise next steps, never credentials.
Do not create or modify operational state.sqlite, supervisor files or the canonical playbook.
'''


def run_check(state, task, check, config, label):
    cwd = (Path(task['worktree']) / check['cwd']).resolve()
    if not cwd.is_relative_to(Path(task['worktree']).resolve()):
        raise RuntimeError('verification cwd outside task worktree')
    directory = state.directory(task['id'])
    fingerprint = revision(task['worktree'])
    path = directory / f'check-{time.time_ns()}.log'
    env = Environment(state, task, config).check_environment()
    env['DOTAGENT_BASE_URL'] = env['ENGINEER_BASE_URL'] = task['environment']['base_url']
    env['DOTAGENT_ARTIFACT_DIR'] = env['ENGINEER_ARTIFACT_DIR'] = str(directory)
    argv = check['argv']
    started = time.time()
    with open(path, 'w') as output:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            state.set('worker', process_record(process, task['id'], argv, directory))
            while process.poll() is None:
                state.set('heartbeat', time.time())
                if state.get('paused') or state.task(task['id'])['status'] == 'cancelled':
                    raise Interrupted('verification interrupted')
                if time.time() - started > config.get('check_seconds', 900):
                    raise RuntimeError('verification timed out')
                time.sleep(1)
        finally:
            stop_group(process)
            state.set('worker', None)
    result = {'display': ' '.join(argv), 'argv': argv, 'cwd': str(cwd), 'exit_code': process.returncode,
              'revision': fingerprint, 'commit': git(task['worktree'], 'rev-parse', 'HEAD'),
              'log': str(path), 'result': 'passed' if process.returncode == 0 else 'failed',
              'criterion': label, 'at': started, 'seconds': time.time() - started}
    task['evidence'].append(result)
    state.save(task)
    if revision(task['worktree']) != fingerprint:
        raise RuntimeError('verification changed source files; rerun against the resulting revision')
    return process.returncode == 0


def verify(state, task, config):
    environment = Environment(state, task, config)
    environment.start()
    passed = True
    for index, check in enumerate(config['checks']):
        passed = run_check(state, task, check, config, f'gate-{index}') and passed
    for criterion in task['criteria']:
        for check in criterion['checks']:
            passed = run_check(state, task, check, config, criterion['id']) and passed
    if task['ui_changed']:
        script = Path(task['browser_script']).resolve()
        directory = state.directory(task['id'])
        if not script.is_relative_to(directory) or not script.is_file():
            raise RuntimeError('UI changes require a browser scenario outside tracked source')
        runner = Path(__file__).with_name('browser.mjs')
        browser_dir = directory / f'browser-{time.time_ns()}'
        report = browser_dir / 'browser-result.json'
        passed = run_check(state, task, {'cwd': '.', 'argv': ['node', str(runner),
                          str(script), task['environment']['base_url'], str(browser_dir),
                          config.get('playwright_package', '@playwright/test')]}, config, 'browser') and passed
        if report.exists():
            data = json.loads(report.read_text())
            task['artifacts'] = data['artifacts']
            if not data['passed'] or not task['artifacts']:
                passed = False
            for artifact in task['artifacts']:
                artifact['sha256'] = hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest()
                artifact['redacted'] = False  # Separate review of actual screenshots after capture.
                artifact['revision'] = revision(task['worktree'])
        else:
            passed = False
    if passed:
        task['verified_revision'] = revision(task['worktree'])
        task['phase'] = 'evidence' if task['ui_changed'] else 'deliver'
        task['next_action'] = 'Review captured screenshots for sensitive data' if task['ui_changed'] else 'Deliver draft PR'
    else:
        task['phase'] = 'implement'
        task['next_action'] = 'Fix failed checks without weakening frozen criteria'
    state.save(task)
    return passed


def iteration(state, task, config):
    project = config['repository']
    directory = state.handover(task)
    if task.get('verified_revision') and task['phase'] in ('evidence', 'deliver', 'render', 'jira'):
        if revision(task['worktree']) != task['verified_revision']:
            task['phase'], task['next_action'] = 'verify', 'Content changed since verification; rerun required gates'
            task.pop('verified_revision', None)
    if task['phase'] in ('plan', 'implement'):
        prepare_worktree(state, task, project)
        Environment(state, task, project).prepare()
        if not task.get('setup_complete'):
            for check in project.get('setup', []):
                if not run_check(state, task, check, project, 'setup'):
                    raise RuntimeError('project setup failed')
            task['setup_complete'] = True
        state.handover(task)
        report, usage = execute(task['host'], prompt_for(task, directory), task['worktree'],
                                directory / f"iteration-{task['attempts']}", REPORT, config['host'], state, task['id'])
        task['cost_known'] = task['cost_known'] and usage['usd'] is not None
        task['cost'] += usage['usd'] or 0
        task['usage'] = usage
        valid_report(report, task)
        for field in ('criteria', 'summary', 'implementation', 'next_action', 'decisions', 'assumptions',
                      'blockers', 'limitations', 'changed_files', 'commit_message', 'ui_changed',
                      'browser_script', 'review'):
            task[field] = report[field]
        visible = any(Path(p).suffix in ('.tsx', '.jsx', '.css', '.scss', '.html') for p in task['changed_files'])
        if visible and not task['ui_changed']:
            raise RuntimeError('visible source changes require browser verification and screenshot evidence')
        task['phase'] = report['action']
        if report['action'] == 'blocked':
            task['status'] = 'blocked'
        elif report['action'] == 'verify' and not report['review']:
            raise RuntimeError('correctness/requirements review evidence is required')
    elif task['phase'] == 'verify':
        verify(state, task, project)
    elif task['phase'] == 'evidence':
        schema = obj({'safe': {'type': 'boolean'}, 'blocked': {'type': 'boolean'}, 'reason': STRING})
        result, usage = execute(task['host'], f'''Review the actual local screenshots below with your image tools.
They must show the expected behavior for the criteria, contain only synthetic fixture data,
and exclude secrets/personal information. Use verification logs for behavior proof; screenshots
show the relevant visible states. Return safe=false if redaction or additional captures are needed.
Set blocked=true only for missing tools/access or a product decision; repairable evidence gaps
are not blockers. Never infer that an image was inspected if no image tool succeeded.
{json.dumps(task['artifacts'])}\nCriteria: {json.dumps(task['criteria'])}
Verification evidence: {json.dumps(task['evidence'][-8:])}
''', task['worktree'], directory / f"evidence-{task['attempts']}", schema, config['host'], state, task['id'])
        task['cost'] += usage['usd'] or 0
        if not result['safe']:
            if result['blocked']:
                task['status'], task['blockers'] = 'blocked', [result['reason']]
            else:
                task['phase'], task['next_action'] = 'implement', 'Repair screenshot evidence: ' + result['reason']
        else:
            for artifact in task['artifacts']:
                artifact['redacted'] = True
            task['phase'] = 'deliver'
    elif task['phase'] == 'deliver':
        url = GitHub(state, task, project).deliver()
        task['phase'] = 'render' if task['artifacts'] else 'jira'
        task['next_action'] = f'Check attachment rendering in {url}' if task['artifacts'] else 'Update Jira with verified PR'
    elif task['phase'] == 'render':
        manifest, receipt = directory / 'attachment-manifest.json', directory / 'render-result.json'
        atomic(manifest, task['delivery']['attachments'])
        receipt.unlink(missing_ok=True)
        try:
            result = command(['node', str(Path(__file__).with_name('render.mjs')), task['delivery']['pr'],
                              str(manifest), str(receipt), project.get('playwright_package', '@playwright/test')],
                             cwd=task['worktree'], timeout=120, check=False)
            rendered = json.loads(receipt.read_text()) if receipt.exists() else {}
            if result.returncode == 0 and rendered.get('rendered'):
                task['delivery']['rendered'] = True
                task['delivery']['render_receipt'] = str(receipt)
                task['phase'] = 'jira'
                state.save(task)
                state.handover(task)
                return
        except (OSError, subprocess.TimeoutExpired):
            pass  # Missing public access falls through to the authenticated host browser.
        # Browser authentication belongs to host MCP, never copied from the user's profile.
        schema = obj({'rendered': {'type': 'boolean'}, 'observed_urls': STRINGS, 'error': STRING})
        result, usage = execute(task['host'], f'''Use authenticated browser tools to open this draft PR:
{task['delivery']['pr']}
Verify every embedded screenshot renders with naturalWidth > 0 in the PR body. Inspect actual DOM.
Expected attachment URLs: {json.dumps(list(task['delivery']['attachments'].values()))}
Return rendered=false if browser/authentication is unavailable. Do not infer rendering from Markdown.
Read only; never modify the PR, click Merge, or capture routine success screenshots.
''', task['worktree'], directory / f"render-{task['attempts']}", schema, config['host'], state, task['id'])
        task['cost'] += usage['usd'] or 0
        expected = set(task['delivery']['attachments'].values())
        if not result['rendered'] or not expected.issubset(result['observed_urls']):
            task['status'] = 'blocked'
            task['blockers'] = ['PR screenshot rendering unverified: ' + result['error']]
        else:
            task['delivery']['rendered'] = True
            task['phase'] = 'jira'
    elif task['phase'] == 'jira':
        Jira(state, config['jira'], config['host']).progress(task,
            f"Draft PR ready for review: {task['delivery']['pr']}\n"
            f"Verified commit: {task['commit']}. Local checks passed.\n"
            + '\n'.join(task['limitations']))
        if (task['delivery'].get('body_verified') and task['delivery'].get('jira_comment')
                and (not task['artifacts'] or task['delivery'].get('rendered'))):
            task['status'], task['phase'], task['next_action'] = 'review', 'review', 'Await human PR review'
    state.save(task)
    state.handover(task)


def work(state, config, key):
    with state.lock('iteration'):
        task = state.task(key)
        started = time.monotonic()
        def progress():
            return (revision(task['worktree']) if task.get('worktree') and Path(task['worktree']).exists() else '',
                    frozenset((e['criterion'], e['revision']) for e in task['evidence'] if e['exit_code'] == 0),
                    task.get('verified_revision'), tuple(sorted(task['delivery'].keys())))
        old_progress = progress()
        task['attempts'] += 1
        state.save(task)
        try:
            iteration(state, task, config)
            task['failures'] = 0
        except Interrupted:
            task['next_action'] = 'Resume from checkpoint after pause'
        except Exception as error:
            task['failures'] += 1
            task['next_action'] = str(error)
            state.event(key, 'failure', {'error': str(error)})
            if task['failures'] >= config['limits']['max_failures']:
                task['status'], task['blockers'] = 'blocked', [str(error)]
            task['retry_at'] = time.time() + min(300, 2 ** task['failures'] * config['limits']['backoff_seconds'])
        finally:
            new_progress = progress()
            task['stagnant'] = task['stagnant'] + 1 if old_progress == new_progress else 0
            task['elapsed'] += time.monotonic() - started
            if task['stagnant'] >= config['limits']['max_stagnant']:
                task['status'], task['blockers'] = 'blocked', ['Repeated iterations without verified progress']
            if state.task(key)['status'] == 'cancelled':
                task['status'] = 'cancelled'
            state.save(task)
            state.handover(task)


def supervise(state, config, host, once=False):
    with state.lock():
        recover_iteration(state)
        state.set('supervisor', {'pid': os.getpid(), 'started': time.time(), 'host': host})
        reconciled = set()
        while True:
            host = state.get('preferred_host', host)
            state.set('heartbeat', time.time())
            if state.get('paused'):
                if once:
                    return
                time.sleep(2)
                continue
            if not config['repository'].get('keep_inactive_environments', False):
                for inactive in state.tasks():
                    environment = inactive.get('environment', {})
                    if inactive['status'] in ('blocked', 'cancelled', 'review') and environment and not environment.get('stopped_at'):
                        try:
                            Environment(state, inactive, config['repository']).stop()
                        except Exception as error:
                            state.set('intake_error', 'Owned environment cleanup failed: ' + str(error))
                            state.set('paused', True)
                if state.get('paused'):
                    continue
            active = [t for t in state.tasks() if t['status'] == 'active']
            reconciled.intersection_update(t['id'] for t in active)
            task = active[0] if active else None
            if not task:
                try:
                    tickets = Jira(state, config['jira'], config['host']).intake()
                    claimed = {t['id'] for t in state.tasks()}
                    ticket = select_ticket(tickets, config['jira'], claimed)
                    if ticket:
                        task = state.claim(ticket, config['repository']['path'])
                        if task:
                            task['host'] = host
                            state.save(task)
                    state.set('intake_error', None)
                except Exception as error:
                    state.set('intake_error', str(error))
            if task:
                if task.get('host') != host:
                    task['host'] = host
                    state.save(task)
                if task['id'] not in reconciled:
                    try:
                        reconcile(state, task, config)
                        reconciled.add(task['id'])
                    except Exception as error:
                        task['status'], task['blockers'] = 'blocked', [str(error)]
                        state.save(task)
                        if once:
                            return
                        continue
                if task['status'] != 'active':
                    if once:
                        return
                    continue
                limits = config['limits']
                if (task['attempts'] >= limits['max_iterations'] or task['elapsed'] >= limits['task_seconds']
                        or (limits.get('max_spend_usd') is not None and
                            (not task['cost_known'] or task['cost'] >= limits['max_spend_usd']))):
                    task['status'], task['blockers'] = 'blocked', ['Configured execution/spending limit reached']
                    state.save(task)
                elif task.get('retry_at', 0) <= time.time():
                    logfile = state.directory(task['id']) / 'worker.log'
                    with logfile.open('a') as output:
                        args = [sys.executable, str(Path(__file__).parents[1] / 'bin' / 'dotagent'),
                                '--config', config['_path'], '_work', task['id']]
                        process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT,
                                                   start_new_session=True)
                        state.set('iteration_worker', {'pid': process.pid, 'argv': args, 'started': time.time()})
                        started = time.monotonic()
                        try:
                            while process.poll() is None:
                                current = state.task(task['id'])
                                if state.get('paused') or current['status'] == 'cancelled':
                                    # Cooperative cancellation handles host process groups first.
                                    if time.monotonic() - started > 10:
                                        break
                                if time.monotonic() - started > limits['iteration_wall_seconds']:
                                    break
                                if time.time() - state.get('heartbeat', time.time()) > config['host'].get('stall_seconds', 600):
                                    break
                                time.sleep(1)
                        finally:
                            if process.poll() is None:
                                recover_child(state)
                                stop_group(process)
                            state.set('iteration_worker', None)
                        if process.returncode:
                            current = state.task(task['id'])
                            current['failures'] += 1
                            current['elapsed'] += time.monotonic() - started
                            current['next_action'] = f'Worker exited {process.returncode}; inspect worker.log'
                            if current['failures'] >= limits['max_failures']:
                                current['status'], current['blockers'] = 'blocked', [current['next_action']]
                            current['retry_at'] = time.time() + limits['backoff_seconds'] * 2 ** current['failures']
                            state.save(current)
            if once:
                return
            # Pause/stop remains responsive while idle.
            delay = config['jira']['poll_seconds'] if not task else 2
            for _ in range(delay):
                if state.get('paused'):
                    break
                time.sleep(1)


def recover_child(state):
    """Kill only a recorded process whose command still matches its recorded identity."""
    worker = state.get('worker')
    if not worker:
        return
    output = command(['ps', '-p', str(worker['pid']), '-o', 'lstart=,pgid='], check=False).stdout.strip()
    if output and output == worker.get('identity'):
        try:
            os.killpg(worker['pid'], signal.SIGTERM)
            for _ in range(5):
                time.sleep(1)
                current = command(['ps', '-p', str(worker['pid']), '-o', 'lstart=,pgid='], check=False).stdout.strip()
                process_state = command(['ps', '-p', str(worker['pid']), '-o', 'stat='], check=False).stdout.strip()
                if current != output or process_state.startswith('Z'):
                    break
            else:
                os.killpg(worker['pid'], signal.SIGKILL)
        except ProcessLookupError:
            pass
    state.set('worker', None)


def recover_iteration(state):
    recover_child(state)
    previous = state.get('iteration_worker')
    if not previous:
        return
    output = command(['ps', '-p', str(previous['pid']), '-o', 'command='], check=False).stdout
    if '_work' in output and all(str(arg) in output for arg in previous['argv'][1:]):
        recover_child(state)
        try:
            os.killpg(previous['pid'], signal.SIGTERM)
        except ProcessLookupError:
            pass
        # The iteration flock is the final authority: no replacement until it is released.
        for _ in range(10):
            try:
                with state.lock('iteration'):
                    break
            except RuntimeError:
                time.sleep(1)
        else:
            raise RuntimeError('previous worker still holds iteration lock; refusing duplicate execution')
    state.set('iteration_worker', None)


def reconcile(state, task, config):
    """Check external reality before resuming a persisted task after startup."""
    cache = state.root / 'jira' / 'backlog.json'
    backlog = json.loads(cache.read_text()) if cache.exists() else {}
    if time.time() - backlog.get('fetched_at', 0) > config['jira']['poll_seconds']:
        tickets = Jira(state, config['jira'], config['host']).intake()
    else:
        tickets = backlog.get('tickets', [])
    source = next((t for t in tickets if t['key'] == task['id']), None)
    if not source or source['status'] not in config['jira']['eligible_statuses']:
        raise RuntimeError('ticket is no longer assigned/actionable in configured Jira backlog')
    if source['blockers']:
        raise RuntimeError('Jira blockers: ' + '; '.join(source['blockers']))
    if task['criteria'] and any(source.get(k) != task['ticket'].get(k)
                                for k in ('description', 'acceptance_criteria')):
        raise RuntimeError('ticket requirements changed; reconcile frozen criteria before resuming')
    task['ticket'] = source
    if not task.get('worktree'):
        rows = json.loads(command(['gh', 'pr', 'list', '--repo', config['repository']['github'],
                                  '--search', f'"{task["id"]}" in:title,body', '--state', 'open',
                                  '--json', 'number,url,headRefName']).stdout)
        if rows:
            task['status'], task['phase'] = 'review', 'review'
            task['delivery']['pr'] = rows[0]['url']
            task['next_action'] = 'Existing PR found; reconcile/adopt explicitly instead of duplicating work'
            state.save(task)
            return
        worktrees = git(config['repository']['path'], 'worktree', 'list', '--porcelain')
        if task['id'].lower() in worktrees.lower():
            raise RuntimeError('existing ticket worktree found; reconcile ownership before adoption')
    if task.get('worktree'):
        prepare_worktree(state, task, config['repository'])
        pr = GitHub(state, task, config['repository']).find()
        if pr:
            task['delivery']['pr'] = pr['url']
            if pr['state'] != 'OPEN' or not pr['isDraft']:
                task['status'], task['next_action'] = 'review', 'External PR lifecycle changed; awaiting human review'
        if task.get('environment'):
            Environment(state, task, config['repository']).inventory()
    state.save(task)
