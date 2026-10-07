# dotagent

Assigned Jira work → isolated worktree/Compose → verified draft PR.
**Mastra organises the work; Claude Code or Codex implements it.**

## Install

Requires Python 3.11+, Node 22.13+, Git, Docker Compose, authenticated `gh`, and an
authenticated Claude Code or Codex CLI. Jira uses the host's local Atlassian MCP.
No subscription tokens are copied into API clients.

```sh
git clone git@github.com:tawanorg/dotagent.git
cd dotagent
npm ci
npm run build
python3 dotagent/install.py
```

Put `~/.local/bin` on PATH. Installation preserves existing configuration and
unrelated links. When changing checkouts, explicitly update the owned links:
`~/.local/bin/dotagent` → checkout/bin/dotagent and
`~/.local/share/dotagent` → checkout/dotagent.

## One project, one brain

Put project settings in `.dotagent.toml` in the repository, or register
`~/.config/dotagent/projects/NAME.toml`. See [configuration](dotagent/config.example.toml).
Configure repository path, GitHub base, Compose layout, setup/check commands,
Jira site/JQL/status/priority rules and optional `[studio] port`.
Relative repository paths resolve against the configuration file.

Personal host/limit defaults come from `~/.config/dotagent/config.toml`.
Repository/Jira settings never inherit from another project.

From a configured repository:

```sh
dotagent doctor --host codex
dotagent start --host codex
# Or: dotagent start --host claude
```

From anywhere:

```sh
dotagent --project coterie start --host codex
dotagent --project coterie status
dotagent --project coterie studio
```

`studio` prints the URL. Open Workflows → dotagent to inspect phases, saved runs
and traces. Studio binds to **127.0.0.1**. It is an inspection interface; use CLI
controls for execution. Its API cannot launch native workers outside supervision.

Worktrees of one repository share identity and brain. Different repositories get
separate state, workflows, memories, claims and Studio ports. Port collisions
fail visibly; select another `studio.port`. One active task runs per project.
Starting another project is explicit; each enforces its configured resource limits.

## Controls and login startup

```sh
dotagent pause
dotagent resume
dotagent cancel QB-123
dotagent resume QB-123 --note "The approved behavior is ..."
dotagent cleanup QB-123
dotagent install-service --host codex
```

Each project has its own macOS LaunchAgent. It starts Studio and the scheduler,
preserves pause across restart, and resumes after login/wake. Interactive `start`
unpauses an installed service and applies the host choice to later iterations.
`start --once` dispatches one task, potentially through multiple Ralph iterations
until completion or suspension. An assistant response ending is never completion.

A laptop cannot execute asleep/off. For always-on Linux, install with Docker and
native CLI authentication, then use a systemd user service with the absolute
`dotagent --project NAME start --host codex --service` command, stable PATH and
Restart=on-failure. Do not run the same backlog on two machines: distributed
claims are not implemented.

## Growing project memory

Verified deliveries record lessons with PR, commit and decisions. Explicit user
clarifications are remembered with ticket provenance. New engineering sessions
receive bounded project memory and must recheck sources against current code.
Historical evidence is not an instruction.

```sh
dotagent memory remember "Pricing writes update search after commit" --source "AGENTS.md"
dotagent memory search pricing
dotagent memory list
dotagent memory correct "Corrected fact" --id MEMORY_ID --source "ADR-12" --reason "Architecture changed"
dotagent memory retire MEMORY_ID --reason "No longer applicable"
dotagent memory history
```

Corrections retire old facts transactionally; history remains auditable. Retrieval
uses literal local search with recent-entry fallback. Hermes and embeddings are
not required. Personal standards remain in [PLAYBOOK.md](dotagent/PLAYBOOK.md);
changes require a proposed, versioned edit.

## Ownership and storage

| Owner | Responsibility |
| --- | --- |
| Mastra | Phase transitions, Ralph loop, bounded retries, suspend/resume, snapshots, traces |
| Python adapters | Jira/GitHub, worktree/Compose, checks, evidence and handover |
| Native CLI | Model/tool execution, native authentication and context management |
| External supervisor | Process groups, heartbeats, crash recovery and restart backoff |

Private operational state stays outside Git under
`~/.local/state/dotagent/projects/<repository-id>/`:

- `mastra.db`: workflow snapshots and traces.
- `state.sqlite`: evidence projection, controls, mutation receipts, resource ownership.
- `brain.sqlite`: project knowledge, sources, corrections and history.
- `tasks/<hash>/`: handovers, logs, browser scripts and original screenshots.
- `runtime-config.json`, `mastra.log`: resolved configuration and process logs.

Only Mastra chooses phase transitions. Adapter receipts reconcile a crash between
an external mutation and a workflow snapshot; GitHub/Jira also reconcile remote
state before retries. Existing explicit `--config FILE` retains its state path.
Old tasks acquire Mastra run IDs on adoption; branches and evidence are retained.
Pause/stop the old installation before changing its CLI link. Project mode creates
isolated state and never silently moves another project's history.

## Verification and delivery

Unique Compose projects, loopback ports, databases and writable volumes preserve
shared infrastructure. Cleanup never globally prunes Docker. Frozen acceptance
criteria and configured gates run against a code fingerprint. UI work requires
actual browser interaction, console/network checks and reviewed screenshots.
Failures return to implementation; missing access remains blocked.

The runtime commits/pushes, creates or updates a **draft** PR, uploads screenshots
with supported `gh --attach`, checks rendering, and updates Jira idempotently.
It never merges, deploys or marks tickets done. Original images stay outside Git.
Native host usage appears in phase traces when available; unknown subscription
cost is not invented. LibSQL stores traces; aggregate Mastra metrics needing a
different storage backend are not enabled here.

## Test and troubleshoot

```sh
npm run typecheck
npm test
python3 -m unittest discover -s tests -v
npm run build
python3 tests/dotagent_live.py /absolute/path/to/node_modules/@playwright/test
```

- **No configuration:** add `.dotagent.toml` or select `--project NAME`; an unrelated directory never defaults to Coterie's backlog.
- **Build missing:** run `npm ci && npm run build`.
- **Repeated process crash:** inspect `mastra.log`; fix the cause, then `start` or `resume` clears the persisted retry budget.
- **Blocked task:** inspect status/Studio; resolve it and resume with a note. Never weaken criteria.
- **Browser unavailable:** leave evidence unverified; configure the project's Playwright installation.
- **Authentication:** use normal native host/gh/MCP login. Doctor checks prerequisites; real calls prove integration access.

See [validation](VALIDATION.md) and [capability research](CAPABILITIES.md).
