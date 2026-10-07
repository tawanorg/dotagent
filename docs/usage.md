# dotagent user guide

[Back to the project overview](../README.md)

## Start in your coding assistant

```text
# Claude Code
/dotagent Add Active and Done filters to this Todo app. Verify it and open a draft PR.

# Codex (or select dotagent in /skills)
$dotagent Add Active and Done filters to this Todo app. Verify it and open a draft PR.
```

Keep talking in the same chat: “show progress”, “preserve keyboard shortcuts”,
“remember this approach for this project”, or “pause”. The host skill converts
that context into durable tasks, instructions and sourced project guidance.
An issue tracker is optional. The persistent worker continues after the chat turn
ends; `start --detach` returns control to the conversation and opens Studio.

## Trigger your Mac worker from GitHub

On a configured repository's issue, PR discussion, inline review or review summary:

```text
@dotagent work on this issue
@dotagent fix this PR using the review context; preserve the existing API
```

Enable the listener in that project's configuration:

```toml
[tasks]
provider = "local"
poll_seconds = 30

[github_mentions]
enabled = true
allowed_users = ["YOUR_GITHUB_LOGIN"]
poll_seconds = 30
```

Install the login service on the always-on Mac and start it once:

```sh
dotagent --project NAME install-service --host codex
dotagent --project NAME start --host codex
```

It polls through your authenticated `gh` CLI, so no public webhook endpoint or
inbound port is needed. Only listed users can trigger work; when omitted, the
allowlist is your authenticated GitHub login. The first poll watches **new**
comments, avoiding accidental execution of historical mentions. Optional `since`
is an explicit ISO UTC start time for replay. `dotagent poll` performs a check now.

Comments are deduplicated, full issue/PR/review context is captured, and existing
owned PRs receive follow-up instructions. A fresh PR task gets its own local
branch/worktree and pushes to the existing PR head without force; its existing
draft/review state is preserved. Cross-repository/fork heads remain blocked.
A source reply links the result; the listener never merges or marks an issue done.
Blocked/cancelled work requires explicit resume; new comments are preserved.

On macOS, each new task opens a visible Terminal with live Claude/Codex messages
and phase changes. Press **Enter or Ctrl-C to take over**: dotagent stops automation
for that task, waits for its write lock, then resumes its exact saved conversation
in the native Claude/Codex terminal UI. You can chat, interrupt and edit normally.
Other tasks keep running. Type `q` then Enter to close only the view.

Use `dotagent terminal ID` to reopen, `dotagent watch ID` in another terminal, or
`dotagent takeover ID` to take control directly. After exiting the native session,
the task stays held until `dotagent resume ID`; automation reconciles your edits
and reruns verification. A live human session prevents a second worker.
This uses supported session resume, not simultaneous attachment to a headless
process. If there is no saved conversation yet, a new native session loads the
handover. Set `[terminal] enabled=false` for headless operation. macOS automation
permission failures are recorded without stopping work.

The Mac must remain awake and connected. Login/wake resumption uses the existing
supervisor. Run a project on one machine: distributed claims are not implemented.

## Install

Requires Python 3.11+, Node 22.13+, Git, Docker Compose, authenticated `gh`, and an
authenticated Claude Code or Codex CLI. Optional Jira intake uses local Atlassian MCP.
No subscription tokens are copied into API clients.

```sh
git clone git@github.com:tawanorg/dotagent.git
cd dotagent
npm ci
npm run build
python3 dotagent/install.py
```

The installer also adds Codex's user skill under `~/.agents/skills/dotagent` and
Claude's slash skill under `~/.claude/skills/dotagent`. Reload the host if it was
already open. Put `~/.local/bin` on PATH. Installation preserves existing configuration and
unrelated links. When changing checkouts, explicitly update the owned links:
`~/.local/bin/dotagent` → checkout/bin/dotagent and
`~/.local/share/dotagent` → checkout/dotagent.

## One project, one brain

Put project settings in `.dotagent.toml` in the repository, or register
`~/.config/dotagent/projects/NAME.toml`. See [configuration](../dotagent/config.example.toml).
Configure repository path, GitHub base, Compose layout, setup/check commands,
optional task-source rules and `[studio] port`. The default is local requests;
`[tasks] provider = "jira"` opts into the existing `[jira]` integration. Older
configs with `[jira]` retain that behavior. GitHub/Linear/other issue links can
be read by the host skill and imported with their context and source URL.
Relative repository paths resolve against the configuration file.

Personal host/limit defaults come from `~/.config/dotagent/config.toml`.
Repository, task-source and mention-listener settings never inherit from another project.

From a configured repository:

```sh
dotagent doctor --host codex
dotagent start --host codex
# Or: dotagent start --host claude
```

From anywhere:

```sh
dotagent --project my-project start --host codex
dotagent --project my-project status
dotagent --project my-project studio
```

`start` automatically opens the project dashboard once its API is ready, including
when a service is already running. Use `start --no-browser` for terminal-only use;
login/service starts never open a browser. `studio` prints the URL. Open Workflows → dotagent to inspect phases, saved runs
and traces. Studio binds to **127.0.0.1**. It is an inspection interface; use CLI
controls for execution. Its API cannot launch native workers outside supervision.

Worktrees of one repository share identity and brain. Different repositories get
separate state, workflows, memories, claims and Studio ports. Port collisions
fail visibly; select another `studio.port`. The worker count is configurable with no fixed software ceiling:

```sh
dotagent start --host codex --workers 6
```

The default is `concurrency = 1`; `--workers N` persists the count per project.
Raising it fills more slots; lowering it lets current workers finish. Each active
worktree gets a separate process, run, log and resource ownership. All runs share
the project dashboard and brain, with ticket/branch/host/worktree labels.
CPU/memory/time/spending settings still apply. Project memories remain separate.

## Tasks and instructions

```sh
dotagent task "Add completed-task filtering" --id todo-filter
dotagent task --file /private/path/request.md --url https://github.com/OWNER/REPO/issues/123
dotagent instruct todo-filter "Keep the existing keyboard shortcuts"
dotagent learn "For this project, reproduce bugs before fixing them"
dotagent show todo-filter
```

Task IDs are stable local identifiers, not Jira keys. Retrying the same ID and
request reuses the task; conflicting content requires an explicit instruction.
Instructions survive stale worker writes and are applied at the next iteration
before verification/delivery. A completed task with a new instruction gets a new
workflow run on its existing worktree. An already-running external action cannot
be undone. Source issue content remains untrusted task data.

## Controls and login startup

```sh
dotagent pause
dotagent resume
dotagent cancel todo-filter
dotagent resume todo-filter --note "The approved behavior is ..."
dotagent cleanup todo-filter
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
not required. Personal standards remain in [PLAYBOOK.md](../dotagent/PLAYBOOK.md);
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

Unique Compose projects, machine-wide port reservations, databases and writable volumes preserve
shared infrastructure. Cleanup never globally prunes Docker. Frozen acceptance
criteria and configured gates run against a code fingerprint. UI work requires
actual browser interaction, console/network checks and reviewed screenshots.
Failures return to implementation; missing access remains blocked.

The runtime commits/pushes, creates or updates a **draft** PR, uploads screenshots
with supported `gh --attach`, checks rendering, and updates the configured task source idempotently.
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
python3 tests/dotagent_live.py /absolute/path/to/node_modules/@playwright/test --mastra
```

- **No configuration:** add `.dotagent.toml` or select `--project NAME`; an unrelated directory never defaults to another project's backlog.
- **Build missing:** run `npm ci && npm run build`.
- **Repeated process crash:** inspect `mastra.log`; fix the cause, then `start` or `resume` clears the persisted retry budget.
- **Blocked task:** inspect status/Studio; resolve it and resume with a note. Never weaken criteria. Inspect `github_mentions_error`/status for a rejected or inaccessible trigger; correct it and post a new mention.
- **Browser unavailable:** leave evidence unverified; configure the project's Playwright installation.
- **Authentication:** use normal native host/gh/MCP login. Doctor checks prerequisites; real calls prove integration access.

See [validation](../VALIDATION.md) and [capability research](../CAPABILITIES.md).

## Private project adapters

Most repositories only need Compose files, setup/check commands, `compose_env`
and `check_env`. When setup requires port-aware values or a readiness action,
set an absolute path under `[repository]`:

```toml
environment_adapter = "/absolute/path/to/private-adapter.py"
```

This is trusted local Python code, with the same privileges as your setup
commands. Keep project details and secrets outside the dotagent repository.
The file may define any of these hooks:

```python
def compose_env(ports):
    # Called after allocation; values feed the normalized Compose configuration.
    return {"APP_BASE_URL": f"http://localhost:{ports['app:3000']}"}


def check_env(ports):
    # Values for host-side verification, using this task's reserved ports.
    return {"APP_BASE_URL": f"http://127.0.0.1:{ports['app:3000']}"}


def ready(environment):
    # Optional idempotent setup after owned services are healthy.
    # environment.argv(...) scopes Compose commands to the task's project.
    pass
```

Port keys are `service:container_port`. Hooks must preserve isolation and be safe
to repeat after restart. Missing adapter files and old named adapters fail
explicitly; they do not silently fall back to another project's configuration.
For Jira, merge the optional [intake settings](../dotagent/config.jira.example.toml)
into your own project configuration.
