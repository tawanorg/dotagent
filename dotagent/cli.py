"""One startup command, observable state, and explicit controls."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys

from .environment import Environment
from .hosts import command
from .integrations import remote_matches
from .supervisor import supervise, open_studio
from .projects import resolve_config
from .memory import ProjectMemory
from .state import State, atomic


def load_config(path=None, project=None):
    config = resolve_config(config_path=path, project=project)
    config.setdefault('state_dir', '~/.local/state/dotagent')
    config.setdefault('host', {})
    config.setdefault('limits', {})
    for key, value in {'max_iterations': 30, 'max_failures': 3, 'max_stagnant': 4,
                       'task_seconds': 28800, 'iteration_wall_seconds': 3600,
                       'backoff_seconds': 10}.items():
        config['limits'].setdefault(key, value)
    config['jira'].setdefault('poll_seconds', 300)
    config['jira'].setdefault('cwd', config['repository']['path'])
    if config['limits'].get('max_spend_usd') is not None:
        config['host']['max_spend_usd'] = config['limits']['max_spend_usd']
    if type(config.get('concurrency', 1)) is not int or config.get('concurrency', 1) < 1:
        raise ValueError('concurrency must be a positive worker count')
    for name in ('path', 'github', 'pr_base', 'compose_files', 'app_port', 'services', 'checks'):
        if not config['repository'].get(name):
            raise ValueError(f'missing repository.{name}')
    for name in ('site', 'jql', 'priorities', 'eligible_statuses'):
        if not config['jira'].get(name):
            raise ValueError(f'missing jira.{name}')
    return config


def doctor(config, host):
    results = []
    def probe(name, argv):
        try:
            outcome = command(argv, timeout=30, check=False)
            ok = outcome.returncode == 0
        except (OSError, RuntimeError, TimeoutError):
            ok = False
        results.append({'check': name, 'passed': ok})
    for tool in set(['git', 'docker', 'gh', host, config['jira'].get('host', 'codex'), 'node']):
        results.append({'check': tool + ' on PATH', 'passed': shutil.which(tool) is not None})
    probe('GitHub authentication', ['gh', 'auth', 'status'])
    probe('GitHub repository access', ['gh', 'repo', 'view', config['repository']['github'], '--json', 'nameWithOwner'])
    probe('Docker daemon', ['docker', 'info', '--format', '{{.ServerVersion}}'])
    probe('Compose', ['docker', 'compose', 'version'])
    probe('repository', ['git', '-C', config['repository']['path'], 'rev-parse', '--show-toplevel'])
    probe('base ref', ['git', '-C', config['repository']['path'], 'rev-parse', config['repository'].get('base', 'HEAD')])
    results.append({'check': 'origin matches configured GitHub repository',
                    'passed': remote_matches(config['repository']['path'], config['repository']['github'])})
    probe('host authentication', ['codex', 'login', 'status'] if host == 'codex' else ['claude', 'auth', 'status'])
    if host == 'claude':
        auth = command(['claude', 'auth', 'status'], check=False)
        results.append({'check': 'Claude login active', 'passed': json.loads(auth.stdout or '{}').get('loggedIn', False)})
    help_text = command(['gh', 'pr', 'edit', '--help']).stdout
    results.append({'check': 'GitHub attachment upload (--attach)', 'passed': '--attach' in help_text})
    results.append({'check': 'free disk >= configured minimum',
                    'passed': shutil.disk_usage(config['repository']['path']).free >=
                    config.get('min_free_gb', 5) * 1024 ** 3})
    if config['limits'].get('max_spend_usd') is not None:
        results.append({'check': 'hard dollar cap requires cost-reporting hosts for worker AND Jira bridge',
                        'passed': host == 'claude' and config['jira'].get('host', 'codex') == 'claude'})
    root = Path(__file__).resolve().parents[1]
    results.append({'check': 'Mastra build and scheduler', 'passed':
                    (root / '.mastra/output/index.mjs').exists() and (root / 'dist/src/runner.js').exists()})
    for row in results:
        print(('PASS ' if row['passed'] else 'FAIL ') + row['check'])
    print('Jira OAuth and browser permissions are checked by live calls; registration alone is not proof.')
    return all(r['passed'] for r in results)


def install_service(config, host):
    executable = str(Path(__file__).parents[1] / 'bin' / 'dotagent')
    if sys.platform == 'darwin':
        label = 'dev.dotagent.' + config.get('_project_id', 'default')
        target = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
        state = Path(config['state_dir']).expanduser()
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Preserve project selection; CLI resolves global defaults with the project file.
        selection = ['--project', config['_path']] if not config.get('_legacy') else ['--config', config['_path']]
        data = {'Label': label,
                'ProgramArguments': [sys.executable, executable, *selection,
                                     'start', '--host', host, '--service'],
                'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30,
                'EnvironmentVariables': {'PATH': ':'.join(dict.fromkeys([
                    str(Path.home() / '.local/bin'),
                    str(Path.home() / '.local/share/fnm/aliases/default/bin'),
                    '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin',
                    *[p for p in os.environ['PATH'].split(':') if 'fnm_multishells' not in p]]))},
                'StandardOutPath': str(state / 'supervisor.log'),
                'StandardErrorPath': str(state / 'supervisor-error.log')}
        atomic(target, plistlib.dumps(data).decode())
        domain = f'gui/{os.getuid()}'
        command(['launchctl', 'bootout', domain, str(target)], check=False)
        command(['launchctl', 'bootstrap', domain, str(target)])
        print(target)
    else:
        raise RuntimeError('Use documented systemd service for an always-on Linux host')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog='dotagent')
    parser.add_argument('--config', default=os.environ.get('DOTAGENT_CONFIG', os.environ.get('ENGINEER_CONFIG')))
    parser.add_argument('--project', help='Project name, repository directory or project TOML')
    commands = parser.add_subparsers(dest='action', required=True)
    for name in ('start', 'doctor', 'install-service'):
        sub = commands.add_parser(name)
        sub.add_argument('--host', choices=['codex', 'claude'], default='codex')
        if name == 'start':
            sub.add_argument('--once', action='store_true', help='One intake/iteration, useful for diagnostics')
            sub.add_argument('--workers', type=int, help='Concurrent task workers; any positive count')
            sub.add_argument('--no-browser', action='store_true', help='Do not open the project dashboard')
            sub.add_argument('--service', action='store_true', help=argparse.SUPPRESS)
    commands.add_parser('status').add_argument('--json', action='store_true')
    commands.add_parser('pause')
    resume = commands.add_parser('resume')
    resume.add_argument('task', nargs='?')
    resume.add_argument('--note', help='Persist a user decision or clarification in the handover')
    commands.add_parser('cancel').add_argument('task')
    commands.add_parser('cleanup').add_argument('task')
    memory = commands.add_parser('memory')
    memory.add_argument('operation', choices=['list', 'search', 'remember', 'retire', 'correct', 'history'])
    memory.add_argument('text', nargs='?', default='')
    memory.add_argument('--source', default='user correction')
    memory.add_argument('--kind', default='fact')
    memory.add_argument('--id', help='Existing memory to correct')
    memory.add_argument('--reason', default='Explicit user correction')
    commands.add_parser('studio')

    args = parser.parse_args()
    try:
        config = load_config(args.config, args.project)
        state = State(config['state_dir'])
        if args.action == 'memory':
            brain = ProjectMemory(config)
            if args.operation == 'remember':
                result = brain.remember(args.text, source=args.source, kind=args.kind)
            elif args.operation == 'search':
                result = brain.search(args.text)
            elif args.operation == 'retire':
                result = brain.retire(args.text, reason=args.reason)
            elif args.operation == 'correct':
                result = brain.supersede(args.id, args.text, source=args.source, reason=args.reason)
            else:
                result = brain.list(include_retired=args.operation == 'history')
            print(json.dumps(result, indent=2))
            brain.close()
            return
        if args.action == 'studio':
            print(state.get('studio_url', f"http://127.0.0.1:{config.get('studio', {}).get('port', 4111)}"))
            return
        if args.action == 'doctor':
            raise SystemExit(0 if doctor(config, args.host) else 1)
        if args.action == 'status':
            rows = [{'ticket': t['id'], 'status': t['status'], 'phase': t['phase'],
                     'last_verified': next((e for e in reversed(t['evidence']) if e['exit_code'] == 0), None),
                     'blockers': t['blockers'], 'next_action': t['next_action'],
                     'worktree': t.get('worktree'), 'branch': t.get('branch'), 'host': t.get('host'),
                     'pr': t['delivery'].get('pr')} for t in state.tasks()]
            if args.json:
                print(json.dumps({'paused': state.get('paused', False), 'tasks': rows,
                                  'intake_error': state.get('intake_error')}, indent=2))
            else:
                print(config.get('project_name', 'dotagent') + ': ' + ('Paused' if state.get('paused') else 'Ready'))
                print('Studio: ' + state.get('studio_url', 'not started'))
                for row in rows:
                    print(f"{row['ticket']} | {row['status']}/{row['phase']} | {row['next_action']}")
                    if row['worktree']:
                        print(f"  {row['host']} | {row['branch']} | {row['worktree']}")
                    for blocker in row['blockers']:
                        print('  Blocked: ' + blocker)
                if state.get('intake_error'):
                    print('Intake: ' + state.get('intake_error'))
            return
        if args.action == 'pause':
            state.set('paused', True)
            print('Pause requested; active worker will checkpoint and stop.')
        elif args.action == 'resume':
            if args.task:
                task = state.task(args.task)
                if not task:
                    raise RuntimeError('unknown task')
                if task['status'] == 'review':
                    raise RuntimeError('task is already awaiting review')
                if task.pop('workflow_failed', False):
                    task.setdefault('workflow_history', []).append(task.pop('workflow_run'))
                task.update(status='active', failures=0, stagnant=0, retry_at=0, blockers=[])
                if args.note:
                    task['decisions'].append('User clarification: ' + args.note)
                    brain = ProjectMemory(config)
                    brain.remember(args.note, source=task['id'], kind='decision')
                    brain.close()
                if task['phase'] == 'blocked':
                    task['phase'] = 'implement' if task['criteria'] else 'plan'
                state.save(task, allow_reactivate=True)
            state.set('paused', False)
            state.set('process_failures', 0)
            state.set('control_revision', __import__('time').time())
            print('Resumed. Start the supervisor if it is not installed as a service.')
        elif args.action in ('cancel', 'cleanup'):
            task = state.task(args.task)
            if not task:
                raise RuntimeError('unknown task')
            if args.action == 'cancel':
                task['status'] = 'cancelled'
                state.save(task)
                print('Cancelled; worktree and volumes preserved. Run cleanup to stop owned services.')
            else:
                if task['status'] == 'active':
                    raise RuntimeError('pause/cancel task before cleanup')
                Environment(state, task, config['repository']).stop()
                print('Owned services stopped. Worktree and volumes preserved.')
        elif args.action == 'install-service':
            install_service(config, args.host)
        elif args.action == 'start':
            if not doctor(config, args.host):
                raise RuntimeError('doctor failed; fix prerequisites before startup')
            if args.workers is not None:
                if args.workers < 1:
                    raise RuntimeError('--workers must be positive')
                state.set('preferred_workers', args.workers)
            if not args.service:
                state.set('preferred_host', args.host)
                state.set('paused', False)
                state.set('process_failures', 0)
                state.set('control_revision', __import__('time').time())
            try:
                with state.lock():
                    pass
            except RuntimeError:
                print('Supervisor already running; startup preference applied.' if not args.service
                      else 'Supervisor already running.')
                if not args.service and not args.no_browser:
                    if open_studio(state, config) is None:
                        print('Studio is not ready; inspect status and local mastra.log.')
                return
            supervise(state, config, args.host, args.once, browser=not args.service and not args.no_browser)
    except KeyboardInterrupt:
        print('Stopped. Checkpoint remains available.', file=sys.stderr)
    except Exception as error:
        print('dotagent: ' + str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
