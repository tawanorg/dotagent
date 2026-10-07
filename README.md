# Dotagent

A persistent Jira → isolated worktree → local verification → draft PR runtime.
Python 3.11+ standard library owns lifecycle and SQLite state. Native Claude Code
or Codex owns each bounded engineering iteration using its existing login.

## Install and run

From this dotfiles checkout:

```sh
python3 dotagent/install.py
dotagent doctor --host codex
dotagent start --host codex
# Or: dotagent start --host claude
```

`~/.local/bin` must be on PATH. The installer adds only the CLI/playbook links and
a config file if absent; it does not rerun the broad dotfiles installer or replace
existing host settings. Review `~/.config/dotagent/config.toml`: the provided
Coterie adapter uses `Jerawine/coterie-qb-prototype`, your assigned QB backlog,
`origin/main`, and the configured local checkout. No credentials belong there.

Keep the startup command in a terminal. For login/restart/wake resumption:

```sh
dotagent install-service --host codex
dotagent status
dotagent pause
dotagent resume
dotagent cancel QB-123
dotagent resume QB-123     # Explicitly retry after resolving a blocker
dotagent cleanup QB-123    # Stop owned services; preserve worktree and data
```

The macOS LaunchAgent uses RunAtLoad/KeepAlive and a 30-second restart throttle.
It resumes after login and wake; no laptop can execute while asleep or powered off.
Pause is persisted, including across restarts. Cancel is task-specific. Cleanup
never prunes Docker, deletes data or removes a worktree. Use your existing worktree
cleanup skill after review/merge when deliberate deletion is wanted.
An interactive `dotagent start --host …` resumes an installed paused supervisor
and applies the host choice to subsequent iterations; it does not create a second
worker. Service restarts preserve a deliberate pause.

`start --once` performs one intake/iteration for diagnosis. It is not an end-to-end
completion flag. The regular supervisor keeps iterating and polls while idle.

## Architecture and ownership

| Concern | Owner |
| --- | --- |
| Claims, lifecycle, retries, controls, budgets, evidence | Python supervisor + SQLite |
| Model/tool execution and native compaction | Selected authenticated CLI |
| Canonical engineering practices | Versioned `PLAYBOOK.md` |
| Jira authentication | Configured host's Atlassian MCP (Codex by default for both workers) |
| GitHub authentication/upload | `gh` native login and `--attach` |
| App isolation/readiness | Per-repository Compose adapter |
| Browser tests | Existing Playwright dependency; host MCP for interactive diagnosis/PR rendering |

Mastra offers workflow snapshots/checkpoints, but it would add a framework without
removing Git/Docker reconciliation. Hermes supplies another execution/memory host;
using it here would overlap native CLI ownership. Neither is installed or required.
See [capability research](CAPABILITIES.md) for current documentation and observed
versions. This is one logical agent with two thin host adapters, not chained agents.

Use the installed Matt Pocock skills selectively as routed in the playbook. Its
`handoff` skill writes a summary; its Claude background launcher does not manage
this runtime. The supervisor reuses the handover content convention and launches
replacement sessions itself. It never sends Claude slash commands to Codex.

## State and recovery

Operational files live in `~/.local/state/dotagent` with private permissions:

- `state.sqlite`: unique task claims, criteria, decisions, source ticket data,
  attempts, blockers, ownership, delivery state and event/usage ledger.
- `tasks/<hash>/handover.{json,md}`: durable task checkpoint and next action.
- `tasks/<hash>/notes.md`: worker notes before long operations.
- `tasks/<hash>/iteration-*/`: prompt, schema, host events, errors and result.
- `tasks/<hash>/compose.json`: resolved owned Compose configuration.
- `tasks/<hash>/check-*.log`: exact check output; evidence records include argv,
  cwd, exit status, content fingerprint and Git commit.
- `tasks/<hash>/evidence-*.png`: original screenshots; never staged into Git.
- `project-facts/`: inspected project setup facts; separate from task state.
- `proposals/`: proposed personal-playbook corrections, reviewed as explicit diffs.

SQLite uniqueness prevents duplicate ticket claims. An OS flock prevents duplicate
supervisors and iterations. Process identities and owned process groups allow
recovery without killing unrelated terminals. A new worker reads the handover,
reconciles actual state, and resumes the next incomplete phase. Changed content
invalidates verification. A plain `DONE` response cannot complete a task.

Repository metadata also records ticket ownership, refusing adoption by another
state directory. Claims are local to one machine. Do not run two machines
against the same backlog: distributed claiming is not implemented. Concurrency is
explicitly restricted to one task, although ports/resources are owned per task.
Existing actionable work precedes fresh work; then configured Jira priority wins,
with oldest-updated/key tie-breaking. Unknown priorities block selection. Review,
cancelled and locally blocked tasks are excluded until explicitly resumed.

## Configuration and limits

The example TOML is the schema reference. Commands are argv arrays with a worktree
relative cwd, not interpolated shell strings. `checks` are mandatory and independent
of worker-authored criteria. Add repository-specific package/financial gates there
or in the frozen criterion checks before implementation. Criteria cannot change
silently in later iterations.

Each host process has a duration and no-output timeout. Each task has iteration,
wall-time, retry and no-progress limits. Retries use bounded exponential backoff.
Fresh host sessions bound cross-iteration context growth; native host compaction
handles context inside an iteration. There is no portable reliable “90% context”
signal, so the runtime does not pretend to monitor one.

`limits.max_spend_usd`, when set, is a persistent aggregate USD budget covering
workers and the Jira bridge. Each Claude call reserves its maximum budget before
launch; unknown/failed costs retain the reservation. Native Claude budget enforcement
is used. Doctor rejects this option when either host is Codex because subscription
usage reports tokens but no reliable USD charge. Otherwise dollar cost is shown
as unknown and time/iteration limits remain enforced. Clear/increase limits
explicitly; resume does not silently erase consumed resources. Separate MCP/service
credits are not exposed as CLI model cost and must be capped at their providers.

Workers inherit native permissions. Codex defaults to workspace-write; Claude to
acceptEdits with noninteractive permission prompts disabled. Required commands
denied by policy remain blocked. Configure host-native rules/permissions for trusted
repositories; the runtime does not copy OAuth tokens or bypass all permissions.

## Environments

The adapter resolves Compose with a sanitized environment, allocates unique loopback
ports, rewrites project volume/network identities, adds ownership labels and applies
per-service CPU/memory limits. External volumes/networks, privileged containers and
writable binds outside the task worktree are rejected. Image/build caches are shared;
mutable databases, auth, search and mail storage are task-specific.
Blocked, cancelled and review tasks have their owned services stopped before the
next task, retaining worktrees/data. Set `repository.keep_inactive_environments=true`
only when deliberately keeping those stacks running for manual review.

Coterie pins all DB clients to its internal task Postgres, sets local auth mode and
matching app/Keycloak public URLs, and excludes inherited production DB/Graph/SMTP
credentials. All eight services and four setup jobs must pass readiness. Previously
applied SQL migrations cannot be modified/deleted without stopping for inspection.
Host checks receive explicit task-local database URLs. Destructive Postgres checks
use a separate `dotagent_test` database in that task's own database container.
Ports are transactionally reserved; Docker remains the final bind authority, so an
external process racing for a port produces an observable startup failure.

Ignored files are copied only through `repository.copy_ignored` explicit filenames;
symlinks/escapes/nonignored destinations are rejected. Complex `.worktreeinclude`
patterns are intentionally not approximated: translate the needed files into this
explicit allowlist. Local synthetic defaults usually need no copied secrets.

## Verification and delivery

The supervisor starts Docker Compose, waits for health and jobs, runs required gates
and criterion commands, then runs the UI scenario if applicable. The browser runner
captures console errors, failed requests and unexpected HTTP errors. Successful
evidence captures are separate from failure-only smoke screenshots. Sensitive-content
review happens before upload. App interactions must exercise the changed behavior;
health checks alone are not acceptance evidence.

The runtime commits only recorded task files and refuses unrelated staged changes.
Commit hooks remain enabled; changes made by hooks invalidate verification. PRs are
looked up by branch, created as drafts, then updated inside a managed body section.
Human text outside that section is preserved. Ready/closed PRs stop further delivery.

GitHub CLI 2.102.0 supports `gh pr edit --attach`. The runtime uploads one screenshot
at a time, reconciles the actual body even after nonzero/partial results, and identifies
uploads by content hash. Local references are never accepted as delivered evidence.
Browser inspection must confirm images actually render in the PR. Missing browser
authentication leaves evidence incomplete, with originals preserved locally.

Jira updates use one stable marker comment, found through paginated reads, updated
and read back through MCP. A write whose response is lost is reconciled on retry.
The workflow never merges, deploys, publishes packages or marks tickets done.

## Validation and troubleshooting

```sh
python3 -m unittest discover -s tests -v
python3 tests/dotagent_live.py /absolute/path/to/node_modules/@playwright/test
```

The opt-in live check starts two Docker apps, proves port/data isolation, runs a
browser interaction with console/network checks, injects a real behavior failure,
verifies rejection, fixes it and reruns. It stops only its own services and preserves
its ownership/evidence receipt under `~/.local/state/dotagent-validation`.

Use `dotagent status --json` and the task logs for failures. `doctor` checks binaries,
host/GitHub auth, Docker, Compose, refs, disk space and attachment support; actual
Jira/browser permissions are verified by live calls. Authenticate Jira using
`codex mcp login atlassian`; do not paste credentials into tickets or prompts.
If the host lacks a browser, Playwright browser install or MCP permission, keep the
affected criterion unverified. `Too many open files` is an OS resource error, not a
reason to kill unrelated processes or globally clean Docker.

For an always-on Linux machine, install the same runtime and native CLIs, authenticate
them there using supported mechanisms, and supervise this command with systemd:

```ini
[Service]
ExecStart=/absolute/python3 /absolute/dotfiles/bin/dotagent start --host codex
Restart=on-failure
RestartSec=30
```

Set an explicit PATH and configuration/state paths. Use one machine as backlog
owner. Browser authentication and Docker resources must be provisioned on that
machine; a laptop's credentials and local paths do not migrate automatically.
