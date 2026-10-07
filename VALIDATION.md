# Mastra / project brain validation — 7 October 2026

- `npm run typecheck`, `npm test` (3 tests), `python3 -m unittest discover -s tests -p 'test_*.py'` (32 tests), and `npm run build` passed (Mastra core 1.74.0).
- Real workflow tests cover fresh scheduler dispatch, failed verification/repair, replacement-instance resume, and repeated no-progress suspension.
- Python subprocess tests cover replayed receipts, stale workflow checkpoints and durable timeout accounting.
- Project tests cover registry/current-directory/worktree identity, separate task histories and brain ownership, reopen, correction history and rollback.
- Actual supervisor test killed scheduler PID 4779; replacement PID 5601 started and pause remained true.
- Studio workflow graph and saved synthetic run inspected in Chromium; no console errors or failed requests observed. Screenshot originals remain outside Git.
- Interactive dashboard-open test passes; the OS browser opened the registered Coterie dashboard at http://127.0.0.1:46473/workflows. No backlog execution was unpaused.
- Real parent scheduler launched two task workers concurrently; both verified their live Docker apps and suspended at the configured fixture iteration limit. Read-only GitHub lookup used a fixture; no Jira/model calls were made.
- Twelve concurrent subprocesses could not over-reserve the shared USD budget; settlement retries did not double-refund it.
- Recovery regression checks cover stopping the orphan scheduler before workers and detecting stalled Mastra calls outside engineering phases.
- Docker/Playwright fixture passed again: isolated ports/data, preservation of the other project, rejected +2 behavior and verified +1 repair.
- Matt Pocock Standards and Spec reviews found fresh-run initialization, timeout-budget and false-progress bugs. Regression checks reproduced them and the fixes passed. Process-result parsing now waits for stdout close; workflow API failures have bounded retries.

Current receipts: ~/.local/state/dotagent-mastra-validation/ and
~/.local/state/dotagent-validation/1791342389491629000/. This is a synthetic lifecycle
and application fixture, not a completed business Jira ticket. QB-628's approved
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
| Coterie adapter | Full isolated stack built successfully: eight healthy services, four completed setup jobs, four owned volumes and a separate test database |
| Failed verification | Injected `+2` behavior failed the expected `+1` browser assertion; repair passed |
| GitHub/Jira retry protocol | Regression checks covered partial upload reconciliation, one PR/upload on retry, stable Jira marker/read-back and preserving staged work |
| Live GitHub delivery | Draft PR #3 created; both uploaded images rendered at 1280×900 |
| Live Jira delivery | Retried QB-628 progress update reused comment 10782; no status transition |
| Hard budget exhaustion | Prevented worker launch |
| Full real Jira task | **Incomplete:** QB-628 requires production-copy migration verification; no approved sanitized snapshot was available |
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
`engineer-coterie-validation`. These receipt paths predate the dotagent rename. The real intake read QB-628's description, comments,
attachments and three acceptance criteria; it retained the verification blocker.
The user chose synthetic fixtures for runtime validation. No production dataset was
copied, no Jira ticket was marked done, and no application change was represented
as completed by these fixture results.

The review used Open Code Review delegation for file/rule selection and this session
for review. All 15 initially selected files and the eight excluded documentation/test
files were reviewed; subsequent patches were reviewed with their affected checks.
No OCR-managed LLM endpoint was configured. Draft PR upload/render results and any
full-stack provisioning limitation are recorded in the PR's delivery evidence.
