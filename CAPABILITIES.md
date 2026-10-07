# Dotagent capability research — 2026-10-07

## Recommendation

Use one Python stdlib supervisor/state machine (SQLite + subprocess + process lock) with native Codex/Claude CLI workers. It owns ticket lifecycle, ownership, verification ledger, bounded retries, budgets and delivery reconciliation. Native hosts own model/tool execution and context management. Canonical playbook is versioned source; operational checkpoints are external durable files; project facts and personal preference proposals are separate. No Mastra or Hermes runtime needed for one laptop and one active task. Both can be reconsidered for hosted orchestration or messaging; neither eliminates the required Git/Docker reconciliation and verification gates.

## Installed capabilities actually inspected

- `codex --version`: codex-cli 0.160.1. `codex login status`: logged in using ChatGPT.
- `codex exec --help`: supports `--json`, `--output-schema FILE`, `-o FILE`, `-C DIR`, `--add-dir DIR`, stdin prompt (`-`), `resume`, `fork`, `--ephemeral`, sandbox modes and config overrides. Do not use ephemeral if native resume desired. Native CLI login is supported; no copying subscription tokens into an API client.
- `claude --version`: 2.1.292. `claude auth status`: logged in through claude.ai, Max subscription. No credential values inspected.
- Claude supports `-p`, `--output-format stream-json`, `--verbose`, `--json-schema JSON`, `--session-id UUID`, `--resume UUID`, `--max-budget-usd N`, `--autocompact auto|tokens` (100k–1M), `--permission-mode` and `--permission-prompts none`. `--bare` explicitly skips subscription OAuth/keychain and requires API credentials; avoid bare for this user's current login and existing practices.
- Claude supports `--bg --name NAME`, agents/attach/logs/stop/rm, but do not add this separate background manager beneath an external worker supervisor.
- `gh --version`: 2.102.0 (2026-09-30). `gh pr edit --help` confirms **native supported `--attach`**. This is new as of Sept 2026 and removes need for browser upload fallback on this machine.
- `firecrawl --status`: CLI 1.23.3, authenticated, concurrency 2, remaining credits -673 / 1000. Official web fetch used for current documentation instead. Do not treat Firecrawl as currently usable without credit remediation.
- `command -v hermes` and `command -v mastra`: absent. No dedicated Mastra skill found in available skill catalog or targeted installed path search (only CopilotKit references).
- Available session tool surfaces include Serena, Context7, Atlassian, Chrome DevTools including file upload, and Argent. Presence is not proof a spawned CLI has same authenticated MCP connections; inspect its startup metadata/config separately.
- Shell tool intermittently fails `Too many open files (os error 24)`; capability commands above succeeded between failures. This is an environment resource failure to surface in doctor, not a reason to kill unrelated processes.

## Handover

Installed Matt Pocock plugin 1.3.1 contains `skills/productivity/handoff/SKILL.md` (name `handoff`, `disable-model-invocation: true`). It writes a summary into OS temp, references existing artifacts, adds suggested skills, and redacts sensitive values. It does not compact or resume by itself. Reuse its content convention but save checkpoints to durable external operational state, not temp that may vanish after restart.

The installed `skills/in-progress/claude-handoff/SKILL.md` instructs launching `claude --bg --name NAME -- "$(cat SUMMARY)"`; installed CLI supports this, but it is Claude-specific and creates another worker outside supervisor ownership. Do not assume it works in Codex or execute it beneath supervisor. Replace with durable checkpoint + next bounded native CLI session. Handoff names are commands/skills, not a universal context API.

Claude supports native auto-compaction. For bounded external sessions, persist checkpoints each iteration and launch fresh sessions. Capture host stream usage where available; do not infer context fullness from accumulated total tokens (cached/history totals differ). Claude result costs are cumulative across resumed conversation; avoid double counting or use fresh sessions and per-run figures. Codex usage may provide tokens without actual dollar billing; show unknown cost rather than invented values. Explicit cash cap must fail closed if no enforceable cost signal, or use independently configured token/time caps clearly labeled.

## GitHub attachment delivery

Use `gh pr edit NUMBER --body-file BODY --attach '/absolute/artifact.png#Description'`. Body may contain the exact local file path as a temporary input placeholder; gh rewrites it to the uploaded URL. Never leave that placeholder in persisted PR description. Up to 50 files per invocation. Push access required.

**Partial failure matters:** gh may successfully update PR with some attachments and exit nonzero. Read actual PR body after any attempted mutation, extract URL associated with a stable artifact marker/hash, and checkpoint it before retrying. Keep local originals outside tracked source. Verify PR DOM image complete and naturalWidth > 0; URL presence alone does not prove rendering.

Official supported CLI path makes private upload APIs/browser session token extraction unnecessary. Browser remains appropriate for render verification. Authentication failure must leave evidence delivery incomplete.

## Evaluated alternatives

Mastra current official workflow docs offer structured steps, storage snapshots, suspension/resumption, streaming and workflow runners. Could wrap native child processes, but still needs external process supervision and custom side-effect reconciliation. Adding Node/Mastra/storage adapter solely to run one sequential laptop worker overlaps stdlib state store without reducing required engineering. Dedicated skill is absent; no Mastra API invented or used.

Hermes official repository documentation describes persistent sessions/memory/skills and an optional Codex app-server runtime. That mode delegates terminal/files/MCP execution to Codex while Hermes owns shell/session database/memory. This is a real supported integration, not a hypothetical one, but adds a second lifecycle/memory owner for this request. Hermes documentation also describes borrowing native CLI logins and warns of rotating refresh-token collisions. Do not adopt that mechanism: native CLI workers keep native authentication and shared playbook. Hermes session imports are migration features, not equivalent task lifecycle or verified delivery.

## Official sources checked

- https://code.claude.com/docs/en/headless — native print mode, stream-json, structured output, native context loading, bare-mode API requirement, SIGTERM semantics, cost totals and startup MCP metadata.
- https://developers.openai.com/codex/noninteractive — current native Codex noninteractive documentation (redirects to https://learn.chatgpt.com/docs/non-interactive-mode).
- https://mastra.ai/docs/workflows/overview — current workflow API, lifecycle and execution engines.
- https://mastra.ai/docs/workflows/snapshots — storage-backed workflow snapshots.
- https://github.com/nousresearch/hermes-agent/blob/main/website/docs/user-guide/features/codex-app-server-runtime.md — optional native Codex runtime, found through Context7 `/nousresearch/hermes-agent`.
- https://github.com/nousresearch/hermes-agent/blob/main/website/docs/user-guide/security.md — login adoption and refresh-token collision risks, Context7.
- https://github.com/nousresearch/hermes-agent/blob/main/website/docs/user-guide/sessions.md — imports from Codex and Claude, Context7.
- https://docs.github.com/en/github-cli/github-cli/attaching-files-with-github-cli — supported attach/rewrite mechanism and push-access requirement.
- https://cli.github.com/manual/gh_pr_edit — attachment handling and partial-failure behavior, matches installed CLI help.
- https://github.blog/changelog/2026-09-01-github-cli-media-in-issues-pull-requests-and-comments/ — feature release.

Research made no repository changes, sent no external messages, and performed no delivery mutations. Successful end-to-end auth/model calls, Jira intake, attachment upload and browser render are implementation validation work still required.
