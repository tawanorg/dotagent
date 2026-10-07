"""Worktrees and explicit, project-owned Compose resources."""
import hashlib
import json
import runpy
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import time

from .hosts import command, Interrupted, process_record, stop_group
from .state import atomic


def git(path, *args):
    return command(['git', '-C', str(path), *args]).stdout.strip()


def revision(path):
    """Include tracked and untracked content; a commit alone is insufficient evidence."""
    digest = hashlib.sha256()
    files = command(['git', '-C', str(path), 'ls-files', '-co', '--exclude-standard', '-z']).stdout
    for name in sorted(set(files.split('\0')) - {''}):
        file = Path(path) / name
        digest.update(name.encode())
        if file.is_symlink():
            digest.update(os.readlink(file).encode())
        elif file.is_file():
            digest.update(b'x' if file.stat().st_mode & 0o111 else b'-')
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
        else:
            digest.update(b'<deleted>')
    return digest.hexdigest()


def prepare_worktree(state, task, config):
    repo = Path(config['path']).expanduser().resolve()
    common = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    claims = common / 'dotagent-claims'
    claims.mkdir(exist_ok=True, mode=0o700)
    claim = claims / (hashlib.sha256(task['id'].encode()).hexdigest() + '.json')
    owner = {'state': str(state.root), 'task': task['id']}
    try:
        descriptor = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if json.loads(claim.read_text()) != owner:
            raise RuntimeError('ticket is owned by another runtime state directory')
    else:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(owner, stream)
            stream.flush()
            os.fsync(stream.fileno())
    if 'worktree' not in task:
        slug = task['id'].lower()
        target = task.get('target_pr')
        if target:
            if target['repository'] != config['github'] or target['state'] != 'open':
                raise RuntimeError('cannot adopt a closed or cross-repository PR')
            command(['git', 'check-ref-format', '--branch', target['branch']])
            fetched = 'refs/dotagent/' + hashlib.sha256((str(state.root)+task['id']).encode()).hexdigest()
            command(['git', '-C', str(repo), 'fetch', 'origin', '+refs/heads/' + target['branch'] + ':' + fetched])
            if git(repo, 'rev-parse', fetched) != target['sha']:
                raise RuntimeError('PR changed since intake; refresh context before adoption')
            task['branch'], task['base'] = f'dotagent/{slug}', target['sha']
        else:
            task['branch'] = f'dotagent/{slug}'
            task['base'] = git(repo, 'rev-parse', config.get('base', 'HEAD'))
        task['worktree'] = str(repo.parent / (repo.name + '-worktrees') / slug)
        state.save(task)  # Write intent before mutation; reconcile after a crash.
    path = Path(task['worktree'])
    registered = git(repo, 'worktree', 'list', '--porcelain')
    if f'worktree {path}\n' not in registered + '\n':
        if path.exists():
            raise RuntimeError('unowned directory occupies task worktree')
        exists = command(['git', '-C', str(repo), 'show-ref', '--verify',
                          f"refs/heads/{task['branch']}"], check=False).returncode == 0
        if exists:
            raise RuntimeError('branch already exists without owned worktree; reconcile manually')
        command(['git', '-C', str(repo), 'worktree', 'add', '-b', task['branch'], str(path), task['base']])
    if git(path, 'branch', '--show-current') != task['branch']:
        raise RuntimeError('task worktree changed branch')
    # Explicit ignored-file allowlist. Credentials remain local; no broad .env copying.
    for name in config.get('copy_ignored', []) + ['AGENTS.override.md']:
        src, dst = repo / name, path / name
        if not src.exists():
            continue
        if name == 'AGENTS.override.md' and command(['git', '-C', str(repo), 'check-ignore', '-q', '--', name], check=False).returncode:
            continue  # A tracked instruction already came with the worktree.
        if src.is_symlink() or not src.resolve().is_relative_to(repo) or not dst.resolve().is_relative_to(path):
            raise RuntimeError('unsafe ignored configuration path')
        if command(['git', '-C', str(repo), 'check-ignore', '-q', '--', name], check=False).returncode:
            raise RuntimeError(f'configuration copy source is not ignored: {name}')
        if command(['git', '-C', str(path), 'check-ignore', '-q', '--', name], check=False).returncode:
            raise RuntimeError(f'configuration copy destination is not ignored: {name}')
        if not dst.exists() and src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            os.chmod(dst, 0o600)
    return path


def allocate_port(state, owner, name, start=14000, end=24000):
    # A machine-wide reservation also covers ports prepared before Docker binds.
    root = Path(os.environ.get('DOTAGENT_RESOURCE_DIR', '~/.local/state/dotagent')).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = root / 'port-leases.sqlite'
    registry = sqlite3.connect(database, timeout=30)
    os.chmod(database, 0o600)
    identity = hashlib.sha256((str(state.root) + '\0' + owner).encode()).hexdigest()
    try:
        registry.execute('CREATE TABLE IF NOT EXISTS leases (port INTEGER PRIMARY KEY, owner TEXT, name TEXT, UNIQUE(owner,name))')
        with registry:
            registry.execute('BEGIN IMMEDIATE')
            existing = registry.execute('SELECT port FROM leases WHERE owner=? AND name=?', (identity,name)).fetchone()
            old = state.db.execute('SELECT port FROM ports WHERE owner=? AND name=?', (owner,name)).fetchone()
            port = existing[0] if existing else None
            if port is None and old and not registry.execute('SELECT 1 FROM leases WHERE port=?',(old[0],)).fetchone():
                port = old[0]  # Adopt an existing project lease without changing a running stack.
            if port is None:
                for candidate in range(start,end):
                    if registry.execute('SELECT 1 FROM leases WHERE port=?',(candidate,)).fetchone():
                        continue
                    if state.db.execute('SELECT 1 FROM ports WHERE port=?',(candidate,)).fetchone():
                        continue
                    with socket.socket() as sock:
                        try:
                            sock.bind(('127.0.0.1',candidate))
                        except OSError:
                            continue
                    port = candidate
                    break
            if port is None:
                raise RuntimeError('port pool exhausted')
            registry.execute('INSERT OR IGNORE INTO leases VALUES (?,?,?)',(port,identity,name))
        with state.db:
            state.db.execute('DELETE FROM ports WHERE owner=? AND name=?',(owner,name))
            state.db.execute('INSERT INTO ports VALUES (?,?,?)',(port,owner,name))
        return port
    finally:
        registry.close()


def safe_env():
    # Keep Docker's supported local context/auth; exclude app credentials and Compose overrides.
    return {k: v for k, v in os.environ.items()
            if k in {'PATH', 'HOME', 'USER', 'TMPDIR', 'SSH_AUTH_SOCK', 'DOCKER_HOST',
                     'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'}}


def isolate_compose(model, project, worktree, ports, cpus, memory):
    """Normalize first, then replace all resource identities; never merge port lists."""
    model['name'] = project
    for kind in ('volumes', 'networks'):
        for name, spec in model.get(kind, {}).items():
            if spec.get('external'):
                raise RuntimeError(f'external {kind} requires a dedicated isolation adapter: {name}')
            if kind == 'volumes' and (spec.get('driver_opts') or spec.get('driver', 'local') != 'local'):
                raise RuntimeError('custom volume drivers require a dedicated isolation adapter')
            spec['name'] = f'{project}_{name}'
            spec.setdefault('labels', {})['dev.dotagent.owner'] = project
    for name, spec in model['services'].items():
        if spec.get('privileged') or spec.get('network_mode') or spec.get('pid') or spec.get('devices'):
            raise RuntimeError(f'unsafe shared host configuration: {name}')
        if spec.get('volumes_from'):
            raise RuntimeError('volumes_from requires a dedicated isolation adapter')
        if spec.get('build'):
            spec['image'] = f'{project}-{name}:local'
            if isinstance(spec['build'], dict):
                spec['build'].pop('tags', None)
            spec['pull_policy'] = 'build'
        spec.pop('container_name', None)
        spec['cpus'], spec['mem_limit'] = str(cpus), memory
        spec.setdefault('labels', {})['dev.dotagent.owner'] = project
        for port in spec.get('ports', []):
            key = f"{name}:{port['target']}"
            port['published'], port['host_ip'] = str(ports[key]), '127.0.0.1'
        for mount in spec.get('volumes', []):
            if mount['type'] == 'bind' and not mount.get('read_only'):
                if not Path(mount['source']).resolve().is_relative_to(Path(worktree).resolve()):
                    raise RuntimeError(f'writable bind outside worktree: {name}')
    return model


class Environment:
    def __init__(self, state, task, config):
        self.state, self.task, self.config = state, task, config
        self.directory = state.directory(task['id'])
        if config.get('adapter'):
            raise ValueError('Built-in project adapters have moved to private environment_adapter files')
        path = config.get('environment_adapter')
        self.adapter = {}
        if path:
            path = Path(path).expanduser()
            if not path.is_absolute() or not path.is_file():
                raise ValueError('environment_adapter must name an existing absolute Python file')
            self.adapter = runpy.run_path(str(path))
            if any(name in self.adapter and not callable(self.adapter[name]) for name in ('compose_env', 'check_env', 'ready')):
                raise ValueError('environment adapter hooks must be callable')

    def prepare(self):
        task, config = self.task, self.config
        worktree = prepare_worktree(self.state, task, config)
        project = 'eng-' + hashlib.sha256(str(worktree).encode()).hexdigest()[:12]
        envfile = self.directory / 'compose.env'
        if not envfile.exists():
            atomic(envfile, '')
        args = ['docker', 'compose', '--env-file', str(envfile), '-p', project]
        for file in config['compose_files']:
            args += ['-f', str(worktree / file)]
        env = safe_env()
        env.update(config.get('compose_env', {}))
        model = json.loads(command(args + ['config', '--format', 'json'], cwd=worktree, env=env).stdout)
        ports = {f"{name}:{port['target']}": allocate_port(self.state, task['id'], f"{name}:{port['target']}")
                 for name, spec in model['services'].items() for port in spec.get('ports', [])}
        if 'compose_env' in self.adapter:
            env.update(self.adapter['compose_env'](ports))
            model = json.loads(command(args + ['config', '--format', 'json'], cwd=worktree, env=env).stdout)
        model = isolate_compose(model, project, worktree, ports,
                                config.get('cpus_per_service', 2), config.get('memory_per_service', '2g'))
        compose = self.directory / 'compose.json'
        atomic(compose, model)
        previous = task.get('environment', {})
        task['environment'] = {**previous, 'project': project, 'compose': str(compose),
                               'ports': ports, 'worktree': str(worktree),
                               'base_url': f"http://127.0.0.1:{ports[config['app_port']]}"}
        self.state.save(task)
        return task['environment']

    def argv(self, *args):
        env = self.task['environment']
        return ['docker', 'compose', '-p', env['project'], '-f', env['compose'], *args]

    def check_environment(self):
        env = safe_env()
        env.update(self.config.get('check_env', {}))
        ports = self.task['environment'].get('ports', {})
        if 'check_env' in self.adapter:
            env.update(self.adapter['check_env'](ports))
        return env

    def inventory(self):
        project = self.task['environment']['project']
        owned = {}
        for kind, args in [('containers', ['ps', '-aq']), ('networks', ['network', 'ls', '-q']),
                           ('volumes', ['volume', 'ls', '-q'])]:
            owned[kind] = command(['docker', *args, '--filter',
                                  f'label=com.docker.compose.project={project}']).stdout.split()
        self.task['environment']['resources'] = owned
        self.state.save(self.task)
        return owned

    def start(self, rebuild=True):
        self.prepare()
        env = self.task['environment']
        worktree = Path(self.task['worktree'])
        files = command(['git', '-C', str(worktree), 'ls-files', '-co', '--exclude-standard', '-z']).stdout
        migrations = {name: hashlib.sha256((worktree / name).read_bytes()).hexdigest()
                      for name in set(files.split('\0')) if name.endswith('.sql')
                      and any(part in ('drizzle', 'migrations') for part in Path(name).parts)
                      and (worktree / name).is_file()}
        for path, digest in env.get('migrations', {}).items():
            if migrations.get(path) != digest:
                raise RuntimeError('previously applied migration changed; preserve volume and inspect history')
        # Record intent before jobs can execute so a failed startup cannot forget applied migrations.
        env['migrations'] = migrations
        self.state.save(self.task)
        args = self.argv('up', '-d', *(['--build'] if rebuild else []))
        with (self.directory / 'compose-start.log').open('w') as output:
            process = subprocess.Popen(args, cwd=worktree, env=safe_env(), stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            started = time.monotonic()
            try:
                self.state.set('worker', process_record(process, self.task['id'], args, self.directory))
                while process.poll() is None:
                    self.state.set('heartbeat', time.time())
                    if self.state.get('paused') or self.state.get('human:' + self.task['id']) or self.state.task(self.task['id'])['status'] == 'cancelled':
                        raise Interrupted('Compose startup interrupted')
                    if time.monotonic() - started > self.config.get('startup_seconds', 1200):
                        raise RuntimeError('Compose startup timed out; owned resources retained')
                    time.sleep(1)
            finally:
                stop_group(process)
                self.state.set('worker', None)
                self.inventory()  # Include partially-created resources on failure.
        if process.returncode:
            raise RuntimeError('Compose startup failed; see compose-start.log')
        deadline = time.monotonic() + self.config.get('readiness_seconds', 300)
        while time.monotonic() < deadline:
            if self.state.get('paused') or self.state.get('human:' + self.task['id']) or self.state.task(self.task['id'])['status'] == 'cancelled':
                raise Interrupted('readiness interrupted')
            self.state.set('heartbeat', time.time())
            result = command(self.argv('ps', '-a', '--format', 'json'), env=safe_env())
            raw = result.stdout.strip()
            rows = json.loads(raw) if raw.startswith('[') else [json.loads(x) for x in raw.splitlines() if x]
            by_name = {r['Service']: r for r in rows}
            ready = all(by_name.get(n, {}).get('Health') == 'healthy'
                        for n in self.config['services'])
            jobs = all(by_name.get(n, {}).get('State') == 'exited'
                       and by_name[n].get('ExitCode') == 0 for n in self.config.get('jobs', []))
            if ready and jobs:
                if 'ready' in self.adapter:
                    self.adapter['ready'](self)
                env['ready_at'] = time.time()
                env.pop('stopped_at', None)
                self.state.save(self.task)
                return
            time.sleep(2)
        raise RuntimeError('readiness incomplete: all services must be healthy and jobs exit zero')

    def stop(self):
        """Preserve all volumes and shared infrastructure. No global prune or forced removal."""
        if 'environment' in self.task:
            self.inventory()
            command(self.argv('stop'), env=safe_env())
            self.task['environment']['stopped_at'] = time.time()
            self.state.save(self.task)
