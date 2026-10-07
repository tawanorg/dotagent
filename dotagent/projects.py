"""Choose one repository's configuration without borrowing another project's settings."""
import copy
import hashlib
from pathlib import Path
import re
import subprocess
import tomllib


def repository_identity(path):
    """Return (canonical main worktree, stable ID shared by all its worktrees)."""
    path = Path(path).expanduser().resolve()
    def git(*args):
        result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f'not a Git working repository: {path}')
        return result.stdout.strip()
    common = Path(git('rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    roots = [line[9:] for line in git('worktree', 'list', '--porcelain').splitlines()
             if line.startswith('worktree ')]
    if not roots or git('rev-parse', '--is-bare-repository') == 'true':
        raise ValueError(f'not a Git working repository: {path}')
    return Path(roots[0]).resolve(), hashlib.sha256(str(common).encode()).hexdigest()[:16]


def _read(path):
    return tomllib.loads(path.read_text())


def _merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        result[key] = _merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def resolve_config(config_path=None, project=None, cwd=None, config_home=None, state_home=None):
    """Explicit legacy config wins; otherwise select local/registered project settings."""
    if config_path and project:
        raise ValueError('choose --config or --project, not both')
    config_home = Path(config_home or '~/.config/dotagent').expanduser().resolve()
    state_home = Path(state_home or '~/.local/state/dotagent').expanduser().resolve()
    cwd = Path(cwd or Path.cwd()).expanduser().resolve()
    global_path = config_home / 'config.toml'
    global_config = _read(global_path) if global_path.is_file() else {}
    legacy = bool(config_path)
    selected = Path(config_path).expanduser().resolve() if legacy else None
    if project:
        candidate = Path(project).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        if candidate.is_dir():
            cwd = candidate.resolve()
            selected = cwd / '.dotagent.toml' if (cwd / '.dotagent.toml').is_file() else None
        elif candidate.is_file():
            selected = candidate.resolve()
        elif re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', str(project)):
            selected = config_home / 'projects' / f'{project}.toml'
        else:
            raise ValueError(f'unknown project: {project}')
    if selected is None:
        try:
            current_root, current_id = repository_identity(cwd)
        except ValueError:
            current_root, current_id = None, None
        for directory in (cwd, *cwd.parents):
            candidate = directory / '.dotagent.toml'
            if candidate.is_file():
                selected = candidate
                break
            if directory == current_root or (directory / '.git').exists():
                break
        if selected is None and current_id:
            matches = []
            for candidate in sorted((config_home / 'projects').glob('*.toml')):
                registered = _read(candidate).get('repository', {}).get('path')
                if not registered:
                    continue
                registered = Path(registered).expanduser()
                if not registered.is_absolute():
                    registered = candidate.parent / registered
                try:
                    if repository_identity(registered)[1] == current_id:
                        matches.append(candidate)
                except ValueError:
                    continue  # Registry entries can refer to repositories not mounted today.
            if len(matches) > 1:
                raise ValueError('multiple project configurations match this repository; select --project NAME')
            selected = matches[0] if matches else None
            old_repo = global_config.get('repository', {}).get('path')
            if selected is None and old_repo:
                old_repo = Path(old_repo).expanduser()
                if not old_repo.is_absolute():
                    old_repo = global_path.parent / old_repo
                try:
                    if repository_identity(old_repo)[1] == current_id:
                        selected, legacy = global_path, True
                except ValueError:
                    pass
    if selected is None:
        raise ValueError('no project configuration; create .dotagent.toml or select --project NAME / --config FILE')
    config = _read(selected)
    if not legacy:
        # Repository and Jira settings always belong to the selected project.
        defaults = {k: v for k, v in global_config.items() if k not in ('repository', 'jira', 'tasks', 'github_mentions', 'state_dir', 'project_name', 'studio') and not k.startswith('_')}
        config = _merge(defaults, config)
    repository = config.setdefault('repository', {})
    path = Path(repository.get('path', '.')).expanduser()
    if not path.is_absolute():
        path = selected.parent / path
    root, identity = repository_identity(path)
    repository['path'] = str(root)
    config.update(_path=str(selected), _project_id=identity, _project_root=str(root), _legacy=legacy)
    config.setdefault('project_name', selected.stem if selected.name != '.dotagent.toml' else root.name)
    if not legacy:
        config['state_dir'] = str(state_home / 'projects' / identity)
    config.setdefault('state_dir', str(state_home))
    config.setdefault('studio', {}).setdefault('port', 12000 + int(identity[:8], 16) % 40000)
    if not isinstance(config['studio']['port'], int) or isinstance(config['studio']['port'], bool) or not 1024 <= config['studio']['port'] <= 65535:
        raise ValueError('studio.port must be an integer between 1024 and 65535')
    return config
