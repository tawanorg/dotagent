"""Dependency-free terminal dashboard; opening it never starts intake."""
import curses
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import webbrowser

from .state import State, redact
from .terminal import event_message


def discover_projects(config_path=None, project=None, config_home=None):
    from .cli import load_config
    home = Path(config_home or '~/.config/dotagent').expanduser()
    choices = []
    if config_path or project:
        choices.append(('--config' if config_path else '--project', config_path or project))
    choices += [('--project', str(p)) for p in sorted((home / 'projects').glob('*.toml'))]
    try:
        local = load_config()
        choices.insert(1 if config_path or project else 0, ('--config' if local.get('_legacy') else '--project', local['_path']))
    except (ValueError, OSError):
        pass
    result, seen = [], set()
    for option, value in choices:
        try:
            config = load_config(value if option == '--config' else None, value if option == '--project' else None)
            identity = config['_path']
            if identity in seen:
                continue
            seen.add(identity)
            result.append({'name': config['project_name'], 'config': config, 'selection': [option, value]})
        except (ValueError, OSError) as error:
            result.append({'name': Path(value).stem, 'error': str(error), 'selection': [option, value]})
    return result


def worker_logs(directory, limit=18):
    """Bound disk reads even after a multi-hour run."""
    lines = []
    for path in sorted(Path(directory).glob('*/events.jsonl'), key=lambda p: p.stat().st_mtime)[-3:]:
        try:
            with path.open('rb') as stream:
                size = path.stat().st_size
                stream.seek(max(0, size - 65536))
                if size > 65536:
                    stream.readline()
                raw = stream.read(65536).decode('utf-8', errors='replace')
            for line in raw.splitlines():
                try:
                    message = event_message(json.loads(line))
                except (ValueError, TypeError, AttributeError):
                    continue
                lines.extend(message.splitlines())
        except OSError:
            continue
    return lines[-limit:]


def action_argv(project, action, task=None, host='codex', text=None):
    args = [sys.executable, str(Path(__file__).parents[1] / 'bin/dotagent'), *project['selection'], action]
    if action in ('start', 'restart'):
        args += ['--detach', '--no-browser', '--host', host, '--workers', '1']
    elif action in ('resume', 'terminal', 'reconcile', 'draft'):
        if not task:
            raise ValueError('select a task first')
        args.append(task['id'])
        if action == 'draft':
            if not text or not text.strip():
                raise ValueError('draft authorization reason is required')
            args += ['--reason', text]
    elif action == 'task':
        if not text or not text.strip():
            raise ValueError('request is empty')
        args += ['--', text]
    elif action != 'pause':
        raise ValueError('unsupported dashboard action')
    return args


def dashboard(config_path=None, project=None):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError('dashboard needs an interactive terminal; use status --json for scripts')
    projects = discover_projects(config_path, project)
    if not projects:
        raise RuntimeError('No projects found. Register ~/.config/dotagent/projects/NAME.toml or create .dotagent.toml.')
    curses.wrapper(_screen, projects)


def _screen(screen, projects):
    curses.curs_set(0)
    screen.timeout(1000)
    pi = ti = 0
    host, notice = 'codex', 'Opening this dashboard does not start a project.'
    keys = {'s': 'start', 'R': 'restart', 'p': 'pause', 'r': 'resume', 'w': 'terminal', 'd': 'draft', 'x': 'reconcile', 'a': 'task'}

    def draw(y, text, selected=False):
        height, width = screen.getmaxyx()
        if y >= height - 1:
            return
        safe = ''.join(c if c.isprintable() else ' ' for c in redact(str(text)))
        try:
            screen.addnstr(y, 0, safe, max(0, width - 1), curses.A_REVERSE if selected else curses.A_NORMAL)
        except curses.error:
            pass

    def ask(prompt):
        screen.timeout(-1)
        height, width = screen.getmaxyx()
        screen.move(height - 2, 0)
        screen.clrtoeol()
        screen.addnstr(height - 2, 0, prompt, max(1, width - 1))
        screen.move(height - 1, 0)
        screen.clrtoeol()
        curses.echo()
        curses.curs_set(1)
        try:
            return screen.getstr(height - 1, 0, max(1, min(500, width - 1))).decode('utf-8', errors='replace').strip()
        finally:
            curses.noecho()
            curses.curs_set(0)
            screen.timeout(1000)

    while True:
        selected = projects[pi]
        config = selected.get('config')
        state = State(config['state_dir'], readonly=True) if config else None
        try:
            tasks = state.tasks() if state else []
            ti = min(ti, max(0, len(tasks) - 1))
            task = tasks[ti] if tasks else None
            screen.erase()
            draw(0, 'dotagent | ←/→ project  ↑/↓ task  q quit | host: ' + host + ' (h changes)')
            draw(1, 'Projects: ' + ' | '.join(('[' + p['name'] + ']') if i == pi else p['name'] for i, p in enumerate(projects)))
            if state:
                source = config.get('tasks', {})
                draw(2, ('PAUSED' if state.get('paused') else 'ENABLED') + ' | intake: ' + source.get('provider', '?') +
                     ' | ' + config.get('jira', {}).get('jql', 'local requests') + ' | one worker on start')
                draw(3, 's start  R restart  p pause  r resume task  o Studio  w worker  v PR  a add task')
                draw(4, 'd authorize limited draft  x reconcile external draft (no tracker updates)')
                for row, item in enumerate(tasks[max(0, ti-2):ti+3], 6):
                    draw(row, f"{item['id']}  {item['status']}/{item['phase']}  {item.get('next_action', '')}", item is task)
                next_row = 12
                errors = [state.get('intake_error'), state.get('github_mentions_error')]
                if task:
                    errors += task.get('blockers', [])
                    draw(next_row, 'Worktree: ' + task.get('worktree', 'not created'))
                    next_row += 1
                    draw(next_row, 'PR: ' + task.get('delivery', {}).get('pr', 'not created'))
                    next_row += 1
                for error in filter(None, errors):
                    draw(next_row, 'BLOCKER: ' + str(error))
                    next_row += 1
                draw(next_row, 'Live worker logs')
                if task:
                    directory = state.root / 'tasks' / hashlib.sha256(task['id'].encode()).hexdigest()[:12]
                    for line in worker_logs(directory, max(1, screen.getmaxyx()[0] - next_row - 3)):
                        next_row += 1
                        draw(next_row, line)
            else:
                draw(3, 'CONFIG ERROR: ' + selected['error'])
            draw(screen.getmaxyx()[0] - 2, notice)
            screen.refresh()
            key = screen.getch()
            if key in (ord('q'), 27):
                return
            if key == curses.KEY_RIGHT:
                pi, ti = (pi + 1) % len(projects), 0
            elif key == curses.KEY_LEFT:
                pi, ti = (pi - 1) % len(projects), 0
            elif key == curses.KEY_DOWN:
                ti = min(ti + 1, max(0, len(tasks) - 1))
            elif key == curses.KEY_UP:
                ti = max(0, ti - 1)
            elif key == ord('h'):
                host = 'claude' if host == 'codex' else 'codex'
            elif state and key in (ord('o'), ord('v')):
                url = state.get('studio_url') if key == ord('o') else (task or {}).get('delivery', {}).get('pr')
                notice = ('Opened ' + url) if url and webbrowser.open(url) else 'No URL available; start the project or deliver a draft first.'
            elif state and 0 <= key <= 255 and chr(key) in keys:
                action = keys[chr(key)]
                try:
                    text = None
                    if action == 'draft':
                        text = ask('Authorize incomplete draft: enter reason (blank cancels)')
                        if not text:
                            continue
                    elif action == 'task':
                        text = ask('New task request (blank cancels)')
                        if not text:
                            continue
                    elif action in ('start', 'restart', 'resume', 'reconcile'):
                        if ask(f'{action} {task["id"] if task and action in ("resume", "reconcile") else selected["name"]}? Type yes: ') != 'yes':
                            continue
                    argv = action_argv(selected, action, task, host, text)
                    draw(screen.getmaxyx()[0] - 2, action + '…')
                    screen.refresh()
                    result = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
                    notice = ('OK: ' if result.returncode == 0 else 'ERROR: ') + result.stdout.strip()[-1500:]
                except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                    notice = 'ERROR: ' + str(error)
        finally:
            if state:
                state.db.close()
