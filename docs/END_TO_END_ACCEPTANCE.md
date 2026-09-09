# End-to-End Dry-Run Acceptance

Date: 2026-09-07

This acceptance uses deterministic fixtures, local components, MOCK, CACHE,
and DRY_RUN boundaries. It does not claim live YouTube, Meta, paid translation,
external LLM, or Ubuntu-host acceptance.

## Product flow result

| Stage | Acceptance evidence | Result |
| --- | --- | --- |
| Source/channel discovery | Controlled channel catalog with canonical external IDs | PASS |
| Dedup decision | NEW, QUEUED, PROCESSING, SUCCESS, retryable FAILED, SKIPPED, and duplicate URL forms classified | PASS |
| Download/import | NEW executes once; retryable failure reuses its logical source; other states skip | PASS (fixture) |
| Queue | One logical row, persisted attempt number, owner scope | PASS |
| Probe, ASR, OCR | Direct localization orchestration tests preserve media and evidence timestamps | PASS (mock/local) |
| Translation/adaptation | Timestamped source segments map to translated segments; first event at `0.0` is retained | PASS (mock/local) |
| TTS | Registry, Vietnamese voices, distinct-speaker rules, and provider boundary checked | PASS |
| Subtitle | Timed SRT/ASS generation and opening cue coverage checked | PASS |
| Original subtitle cleanup | Frame bounds, cleanup-area cap, temporal boxes, and no global union checked | PASS |
| Render and QC | Direct renderer and CP7B render/QC paths checked with controlled media/providers | PASS |
| Production Item | Approved opportunity creates one persisted, rights-gated item | PASS |
| Script/assets | Versioned approvals, QA, resume, and competitor-media rejection checked | PASS |
| Publishing package | YouTube and Facebook packages remain dry-run/private and require rights/approval | PASS |
| Analytics snapshot | Immutable owner-scoped snapshot behavior checked with fake provider data | PASS |
| Learning report | Evidence-grounded report and bounded future-work adjustment checked | PASS |
| Autonomous cycle | Research through learning persisted in one idempotent MOCK cycle | PASS |
| Cost/provider report | Budgets and mode visible; normal pytest live count is zero | PASS |

## Queue rerun acceptance

- QUEUED/failed/completed logical jobs keep the same job/source identity.
- `RESTART_FROM_BEGINNING` creates a new attempt and clears pipeline outputs,
  while completed items require confirmation.
- `RETRY_FROM_FAILED_STAGE` preserves earlier valid source and translation
  outputs and reruns the failed stage.
- `repeat_publish` remains false and no remote publication is triggered.
- Successful retries update the logical row to SUCCESS; older failed attempts
  remain nested.

## Channel dedup fixture

- A (NEW): eligible and downloaded once.
- B (QUEUED): skipped.
- C (PROCESSING): skipped.
- D (SUCCESS): skipped.
- E (FAILED below retry limit): retried on the same logical source.
- F (alternate URL for A's external ID): collapses into A.

Canonical uniqueness is `(user_id, platform, external_video_id)` in storage.

## Autonomous restart and policy acceptance

The controlled cycle records research, competitor, opportunities, shortlist,
production, script, assets, render, publish dry-run, analytics, and learning.
On reconstruction with the same database, a running step resumes on the same
cycle/run and completed stages are reused. Idempotency and concurrency limits
prevent duplicate cycles and overcommitted renders. Conservative approval gates
are all enabled by default; only explicitly disabled owner-policy gates bypass
waiting.

Hourly, daily, and weekly next-run calculations are timezone-aware. `run_once`
creates at most one missed cycle, while `skip` and `reschedule` advance the
schedule without a catch-up burst.

## Runtime/UI acceptance

The documented Windows entrypoint started on a fresh isolated database. A real
headless Chromium session registered the first account, opened Localization and
AI Channel Agent, and found the research, competitor, Content Brain,
opportunity, learning, production queue, automation, autonomous operator, and
provider-mode controls. There were no duplicate DOM IDs or uncaught page
errors. The two browser console resource errors were the expected unauthenticated
`/api/me` and provider-discovery 401 responses before registration.

An initial audit found Edge voice verification bypassing MOCK mode during UI
load. The closure fix now permits real voice verification only in explicit LIVE
mode. A second browser/server audit loaded both `/api/voices` requests without
any Edge synthesis call.

## Database acceptance

Fresh initialization, repeated initialization, and two initializations of a
temporary copy of `local_data/database.sqlite3` passed. Required CP11 tables
were present, `PRAGMA integrity_check` returned `ok`, foreign-key checks returned
zero errors, and the original runtime database SHA-256 remained unchanged.

## Release blocker revalidation: 2026-09-09

The complete regression now passes **1000 tests**, with **5 skipped**, zero
failures/collection errors and **LIVE_PROVIDER_CALLS=0**. The skips cover two
environment-specific Content OS checks, one FFmpeg renderer test, and unavailable
optional DeepL/Azure backends. This is offline acceptance, not provider approval.

Encryption tests cover raw SQLite storage, access/refresh/provider credentials,
random IVs, wrong keys/tampering, duplicate-encryption protection, atomic rollback,
owner-scoped/idempotent legacy migration, mocked refresh after migrating a copy,
and API/log/cache/config redaction. Download tests execute the real shared service
boundary with controlled handlers, queued excess calls, completion/failure/cancel
release, retries, owner isolation, lower-cap protection, and a killed worker process.

The offline validator passed:
- Fresh and repeated web DB initialization; compatible legacy migration on copies.
- Two web database copies; one old Telegram schema preserved without migration.
- Integrity, foreign keys, and unchanged logical row counts.
- Windows startup with a temporary encryption key and with no key.
- Zero OpenAPI method/path collisions, compileall, all 9 JS syntax checks.
- No executable shell=True calls in the audited source/scripts.
- Zero unintended tracked runtime/build artifacts.
- All three original database hashes and all 275 untracked local files preserved.

The validator can be repeated after committing with:
```powershell
python scripts/release_validation.py --runtime-baseline temp/release_acceptance/runtime_hashes.json --artifact-manifest temp/release_acceptance/artifacts.json --output temp/release_acceptance/validation-postcommit.json
```

The local acceptance manifests contain paths/hashes only and remain ignored.
They were captured before repair; the validator verifies them before and after
running. It never opens the original runtime databases through SQLite.
