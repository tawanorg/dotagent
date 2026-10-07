# Work with dotagent in this conversation

Use the installed runtime; stay in the current chat to understand requests and
report real state. Read `~/.local/share/dotagent/PLAYBOOK.md` once for engineering
rules. Select the current repository or the user's explicit `--project NAME`.

- A request such as “fix the Todo filters” becomes `dotagent task --file FILE
  --id STABLE_ID`. Write the exact request, relevant chat context and agreed
  constraints to a private UTF-8 file outside source. Use a stable ID on retries.
  Read back `dotagent show ID`. Do not turn questions or brainstorming into work.
- For an issue/PR link, use the host's authenticated integration to read its body,
  comments, reviews and linked context. Preserve source URLs and distinguish
  quoted tracker content from the user's instructions. Pass `--url URL`; there
  is no Jira requirement. Missing integration access stays visible.
- Additional guidance: `dotagent instruct ID 'guidance'`. It is durable and
  applied before the next verification/delivery boundary. Already-running
  actions cannot be undone. Show the recorded instruction; do not claim it has
  been applied until status/evidence confirms it.
- “Remember this for this project”: `dotagent learn 'guidance'`. For a correction,
  inspect `memory list` and use `memory correct ... --id ... --reason ...` so old
  knowledge is retired; retain `--kind guidance` when correcting project preferences. Do not generalize a one-task instruction into a permanent
  rule without the user's intent. Personal playbook changes stay proposed diffs.
- “Start/work on it”: `dotagent start --host HOST --detach` (optionally
  `--workers N`). This starts the external supervisor and opens Studio, then
  returns to this conversation. Do not require a second chat or an open terminal.
- Live terminal: `dotagent terminal ID` on Mac or `dotagent watch ID` anywhere.
  Enter or Ctrl-C takes over in the actual native host session after task automation
  stops. `dotagent takeover ID` does this directly; it requires a real terminal,
  so open the terminal instead of running it through a non-interactive shell tool.
  After the human exits, `dotagent resume ID` reconciles edits and reruns checks.
- Status: `dotagent status`, then `dotagent show ID` for evidence and blockers.
  Forward pause/resume/cancel to the matching CLI commands. If there are multiple
  active tasks and the target is unclear, ask which one; do not guess.

If the executable is missing, install/build the standalone dotagent checkout as
its README describes. The host skill is the entry point; it does not itself
contain the runtime. If project configuration is missing, inspect repository
instructions and actual setup commands, then create a project-specific
`.dotagent.toml` (or private registry entry). Default to `[tasks] provider="local"`.
Never copy another project's repository, Jira, credentials or backlog settings.
Ask only for setup details that cannot be established from the repository.

`task`, `instruct`, `learn`, `show` and `status` do not start execution. Start when
requested or clearly implied by “work on/fix/implement”. Preserve host permissions;
never add bypass flags. The runtime can commit/push and deliver reviewable PRs,
but never merge, deploy, or mark external tasks done automatically.
