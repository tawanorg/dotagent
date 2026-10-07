# dotagent validation — 7 October 2026

## QB-18 harness hardening

- Python regression suite: 52 passed, including 11 new workflow/dashboard checks.
  Tests cover fail-closed configuration drift, start worker/host preferences,
  planning checkpoints without weakening existing criteria, LFS pointers,
  stale draft authorization/review, truthful failed/unverified evidence,
  local Git push and idempotent draft replay, and external-draft reconciliation.
- `python3 tests/dashboard_pty.py` and the `--no-args` variant render the real curses
  dashboard through a PTY: project/task/blocker/live logs, host toggle and quit.
  Task data and pause state stay unchanged; no intake/supervisor is started.
- `npm run typecheck`, all 3 Mastra workflow tests, and `npm run build` passed.
  Build still emits the existing Mastra static configuration warning before
  successfully producing Studio and server output.
- Self-review covered process ownership, current-content evidence, explicit
  draft authorization, preserved verification failures, and readonly dashboard
  discovery. Native Codex chat-side panel support remains unverified/unavailable:
  the documented conversation-panel entrypoint targets ChatGPT and this client
  exposed no in-app browser.
- Live restart of an installed project, live remote exception-draft publishing,
  and interactive Claude takeover are not established by these isolated checks.
  External tracker completion, merge and deployment were not performed.

## Public overview and reusable project setup

- README presents the developer workflow, real demo captures, native handoff and the installed technology stack; detailed commands remain in docs/usage.md.
- The 11-node Mermaid workflow parsed and rendered in Chromium. A shareable PNG was uploaded as a PR attachment; SVG and PNG originals remain outside Git. Documentation file links resolve.
- Project-specific runtime hooks were extracted into a private local adapter. The public runtime loads an explicitly configured trusted Python file; no project identity is built in. Existing private configuration was migrated without unpausing services.
- Adapter regression covers allocated ports, Compose environment, host checks, readiness command ownership and rejection of a relative adapter path. All 41 Python tests pass.

## Conversational tasks, GitHub mentions and terminal handoff

- Python regression suite: 41 passed. Mastra workflow suite: 3 passed. TypeScript typecheck, production Studio build, Python compile, browser/render syntax checks and Todo baseline test passed.
- New tests cover plain-language intake without Jira, durable guidance, authorized GitHub mentions with review context, duplicate polling, source-independent followups, cross-project port reservations and exclusive human takeover.
- Terminal tests stop a real subprocess on human hold, prevent duplicate writers, retain inherited ownership locks after launcher exit, tolerate supervisor recovery with a human-held lock, clear late runner registrations and reset crash budgets on explicit return.
- Native Codex 0.160.1 execution returned structured output, then its exact saved session reopened in the interactive TUI. An older daemon-backed Todo session failed bounded-history resume; new execution and interactive resume use supported `--no-daemon` ownership. Claude's resume contract was checked against installed CLI and official documentation; its interactive UI was not exercised.
- Actual Todo issue: https://github.com/tawanorg/dotagent-todo-demo/issues/1. The authorized mention was polled, Codex implemented filters in its owned worktree, seven local checks passed, and actual before/after browser evidence passed screenshot review. The task is currently held in a native human Codex session at delivery; no completed Todo PR is claimed.
- Five demo/promotion images were uploaded to https://github.com/tawanorg/dotagent/pull/1. All five decoded in the actual PR page (1672×941, three 1600×1000, and 2400×1756). README embeds stable attachment URLs. Screenshots remain outside Git; promotional artwork and the API-backed issue report are labelled.
- Native Terminal opened successfully. This environment's Computer Use tool denies Terminal inspection, so no native Terminal screenshot was captured. Actual app interaction was inspected with Argent.
- The configured Todo supervisor and Studio at http://127.0.0.1:14200/workflows remain running. An unrelated paused project service was preserved. Physical sleep/reboot was not forced.

Receipts and shareable image ZIP: `~/.local/state/dotagent-todo-demo-evidence/`.
Native session receipt: `~/.local/state/dotagent-terminal-validation/`.
Claude slash execution remains unverified interactively. The Codex skill was found
by a real native host. An initial strict read-only status failure was fixed by
opening SQLite read-only for status/show/studio; a native read-only Codex session
then ran `dotagent --project todo-demo status` successfully.

## Earlier Mastra / project brain validation

- `npm run typecheck`, `npm test` (3 tests), `python3 -m unittest discover -s tests -p 'test_*.py'` (32 tests), and `npm run build` passed (Mastra core 1.74.0).
- Real workflow tests cover fresh scheduler dispatch, failed verification/repair, replacement-instance resume, and repeated no-progress suspension.
- Python subprocess tests cover replayed receipts, stale workflow checkpoints and durable timeout accounting.
- Project tests cover registry/current-directory/worktree identity, separate task histories and brain ownership, reopen, correction history and rollback.
- Actual supervisor test killed scheduler PID 4779; replacement PID 5601 started and pause remained true.
- Studio workflow graph, two labelled worker runs and saved suspension inspected in Chromium; no console errors or failed requests observed. Screenshot originals remain outside Git.
- Draft PR https://github.com/tawanorg/dotagent/pull/1 contains two uploaded attachments beside manual testing step 3. Both decode at 1600×1000 in GitHub's authenticated API `body_html`. The private PR page returned 404 in the unsigned browser; full signed-in PR-page rendering remains unverified.
- Executable code tested at `be5cb0b`; subsequent validation-only documentation changes do not change that code.
- Interactive dashboard-open test passes; the OS browser opened the registered project dashboard at http://127.0.0.1:46473/workflows. No backlog execution was unpaused.
- Real parent scheduler launched two task workers concurrently; both verified their live Docker apps and suspended at the configured fixture iteration limit. Read-only GitHub lookup used a fixture; no Jira/model calls were made.
- Twelve concurrent subprocesses could not over-reserve the shared USD budget; settlement retries did not double-refund it.
- Recovery regression checks cover stopping the orphan scheduler before workers and detecting stalled Mastra calls outside engineering phases.
- Docker/Playwright fixture passed again: isolated ports/data, preservation of the other project, rejected +2 behavior and verified +1 repair.
- Matt Pocock Standards and Spec reviews found fresh-run initialization, timeout-budget and false-progress bugs. Regression checks reproduced them and the fixes passed. Process-result parsing now waits for stdout close; workflow API failures have bounded retries.

Current receipts: ~/.local/state/dotagent-mastra-validation/ and
~/.local/state/dotagent-validation/1791342389491629000/. This is a synthetic lifecycle
and application fixture, not a completed business Jira ticket. The pilot ticket’s approved
production-copy verification remains unavailable. Physical laptop sleep/reboot was
not forced. Native host/GitHub/Jira checks below were performed before the migration;
no new real Jira ticket was automatically executed during this upgrade.

Dependency audit reports upstream advisories, including braces <=3.0.3 in Mastra's
build tooling (GHSA-vfj7-8cjw-p6xm); no patched compatible release is reported by npm.
No force downgrade was applied. Studio is loopback-only and cannot execute native workers.
LibSQL records traces; aggregate metrics storage is not configured.

## Earlier adapter validation (before Mastra migration)


Observed results on this Mac; fixture checks are not production-ticket completion.

| Check | Result |
| --- | --- |
| Python regression suite | 23 tests passed, including existing dotfiles tests |
| Python compilation / browser runner syntax | Passed |
| Duplicate claims and separate state-directory adoption | Rejected duplicates |
| Cancellation versus stale worker writes | Cancellation preserved; explicit resume works |
| Process crash recovery | Owned worker and test child terminated before replacement; unrelated PID preserved |
| Login supervisor restart | launchd restarted the terminated supervisor; persisted pause preserved |
| Context handover | Fresh Python/host sessions loaded the saved task and advanced implementation → verification → evidence → delivery |
| Codex adapter | Native authenticated execution returned structured output and read real Jira via Atlassian MCP |
| Claude adapter | Native subscription-authenticated print mode returned structured output and usage |
| Native repair iteration | Codex reproduced `2 !== 1`, repaired the counter, added a regression check; independent runtime browser verification passed |
| Docker/browser fixture | Two task projects used separate ports and volumes; one task's writes/stop did not affect the other |
| Private project adapter | Full isolated stack built successfully: eight healthy services, four completed setup jobs, four owned volumes and a separate test database |
| Failed verification | Injected `+2` behavior failed the expected `+1` browser assertion; repair passed |
| GitHub/Jira retry protocol | Regression checks covered partial upload reconciliation, one PR/upload on retry, stable Jira marker/read-back and preserving staged work |
| Live GitHub delivery | Draft PR #3 created; both uploaded images rendered at 1280×900 |
| Live Jira delivery | Retried pilot-ticket progress update reused its existing comment; no status transition |
| Hard budget exhaustion | Prevented worker launch |
| Full real Jira task | **Incomplete:** The pilot ticket requires production-copy migration verification; no approved sanitized snapshot was available |
| Physical sleep/reboot | Not forced; process restart and persisted pause were tested |

Run the deterministic suite from the standalone dotagent checkout:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q dotagent
node --check dotagent/browser.mjs
```

Run the isolated live fixture with the installed project Playwright dependency:

```sh
python3 tests/dotagent_live.py /absolute/path/to/node_modules/@playwright/test
```

Local operational receipts, original images and detailed logs are outside Git under
`~/.local/state/engineer-validation`, `engineer-real-ticket-validation`, and
a private project validation directory. These receipt paths predate the dotagent rename. The real intake read the pilot ticket’s description, comments,
attachments and three acceptance criteria; it retained the verification blocker.
The user chose synthetic fixtures for runtime validation. No production dataset was
copied, no Jira ticket was marked done, and no application change was represented
as completed by these fixture results.

The review used Open Code Review delegation for file/rule selection and this session
for review. All 15 initially selected files and the eight excluded documentation/test
files were reviewed; subsequent patches were reviewed with their affected checks.
No OCR-managed LLM endpoint was configured. Draft PR upload/render results and any
full-stack provisioning limitation are recorded in the PR's delivery evidence.
