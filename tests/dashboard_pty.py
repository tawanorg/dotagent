"""Real terminal smoke: python3 tests/dashboard_pty.py (no network or intake)."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotagent.state import State

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    repo = root / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    config = root / 'fixture.toml'
    config.write_text(f"""
project_name = "PTY fixture"
state_dir = "{root / 'state'}"
[tasks]
provider = "local"
[repository]
path = "{repo}"
github = "fixture/repo"
pr_base = "main"
compose_files = ["compose.yml"]
app_port = "app:3000"
services = ["app"]
checks = [{{cwd = ".", argv = ["true"]}}]
""")
    state = State(root / 'state')
    task = state.claim({'key': 'FIXTURE', 'summary': 'Terminal smoke'}, str(repo))
    task.update(status='blocked', phase='blocked', blockers=['Synthetic blocker'], next_action='Inspect fixture')
    state.save(task)
    state.set('paused', True)
    events = state.directory('FIXTURE') / 'iteration-1'
    events.mkdir()
    (events / 'events.jsonl').write_text(json.dumps({'type': 'item.completed',
        'item': {'type': 'agent_message', 'text': 'Synthetic live worker log'}}) + '\n')
    before = state.task('FIXTURE')
    state.db.close()
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 32, 140, 0, 0))
    argv = [sys.executable, str(Path(__file__).resolve().parents[1] / 'bin/dotagent')]
    if '--no-args' not in sys.argv:
        argv += ['--config', str(config), 'dashboard']
    process = subprocess.Popen(argv,
                               stdin=slave, stdout=slave, stderr=slave,
                               env={**os.environ, 'TERM': 'xterm-256color', 'DOTAGENT_CONFIG': str(config)}, close_fds=True)
    os.close(slave)
    output = b''
    try:
        deadline = time.monotonic() + 10
        while b'Synthetic live worker log' not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 65536)
            if process.poll() is not None:
                break
        assert b'PTY fixture' in output, output
        assert b'Synthetic blocker' in output, output
        assert b'Synthetic live worker log' in output, output
        os.write(master, b'h')
        time.sleep(0.1)
        os.write(master, b'q')
        deadline = time.monotonic() + 5
        while process.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    break
        assert process.wait(timeout=1) == 0
        state = State(root / 'state', readonly=True)
        assert state.get('paused') is True
        assert state.task('FIXTURE') == before
        assert state.get('supervisor') is None
        state.db.close()
        print('PASS: real PTY rendered project, task, blocker and live log; host toggle and quit worked; no intake/state changes.')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)
