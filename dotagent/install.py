#!/usr/bin/env python3
"""Install only dotagent; leave other dotfile links and host settings untouched."""
from pathlib import Path
import os
import shutil

root = Path(__file__).resolve().parents[1]
links = {
    Path.home() / '.local/bin/dotagent': root / 'bin/dotagent',
    Path.home() / '.local/share/dotagent': root / 'dotagent',
    Path.home() / '.agents/skills/dotagent': root / 'skills/codex/dotagent',
    Path.home() / '.claude/skills/dotagent': root / 'skills/claude/dotagent',
}
for target, source in links.items():
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() and target.resolve() == source.resolve():
        continue
    if target.exists() or target.is_symlink():
        raise SystemExit(f'Existing installation preserved: {target}; update the link explicitly.')
    target.symlink_to(source)
os.chmod(root / 'bin/dotagent', 0o755)
config = Path.home() / '.config/dotagent/config.toml'
config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
if not config.exists():
    config.write_text('# Personal defaults only. Configure each project separately.\n[host]\nstall_seconds = 600\n[limits]\nmax_iterations = 30\n')
    os.chmod(config, 0o600)
print(f'Installed dotagent. Configuration: {config}')
