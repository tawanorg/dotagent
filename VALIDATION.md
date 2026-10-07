# Validation — 7 October 2026

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

Run the deterministic suite from dotfiles:

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
