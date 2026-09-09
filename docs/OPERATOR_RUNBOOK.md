# Operator Runbook

## First-time setup

1. Create and activate a project virtual environment.
2. Install `requirements.txt`; install FFmpeg/ffprobe on `PATH`.
3. Copy `.env.example` to `.env`, set a strong `WEB_SESSION_SECRET`, and choose
   writable database/temp/output paths.
4. Leave `PROVIDER_EXECUTION_MODE=DRY_RUN` and `RUN_LIVE_TESTS=0`.
5. Start with `python scripts/run_web.py`, create the first admin, and verify
   the provider-mode badge before connecting accounts.
6. Optionally start Redis and Ollama. Neither is required for the first local
   localization dry run.

## Normal localization

Open Localization, enter an owned/permitted URL or import, choose source and
target languages, select a real available voice, review the estimate, and run.
Inspect subtitles and QC before downloading or opening any publishing action.

## Download whole channel

Enable the channel option, scan first, and review the preflight counts. Confirm
the channel belongs to the intended owner. The operator queues only NEW and
eligible failed sources.

## What gets skipped

QUEUED, PROCESSING, and SUCCESS sources are skipped. Duplicate URL forms with
the same platform video ID collapse to one logical source. Terminal failures
beyond the retry policy and manually skipped sources are not started.

## How failed videos retry

An eligible FAILED source gets a new nested attempt on the same logical source
and job. The top-level status becomes SUCCESS after a successful retry; older
failures remain in expanded attempt history.

## Run all again

Use `Run all again` to restart download through render while preserving source
identity and configuration history. Completed items require confirmation. The
action creates an execution attempt, not a duplicate source. Publishing stays
off.

## Retry failed stage

Use `Retry from failed stage` when earlier source, transcript, translation, or
other validated outputs are reusable. Confirm the attempt counter increases and
the failed stage reruns. Publish separately after review.

## Provider mode and avoiding live cost

Use MOCK for deterministic tests, CACHE for repeatable cached/mock work, and
DRY_RUN for operation previews. Do not set `RUN_LIVE_TESTS=1` in a normal test
shell. After `pytest -q`, require `LIVE_PROVIDER_CALLS=0`.

## Live acceptance

Use only owned content and private/test destinations. Back up the database,
set tight budgets, connect the intended owner account, set
`PROVIDER_EXECUTION_MODE=LIVE`, then explicitly set `RUN_LIVE_TESTS=1` for the
controlled acceptance shell. Start with one item or one cycle. Review the cost
report and revoke the opt-in immediately afterward.

## Autonomous operator

In Automation, save the channel key, timezone, cadence, provider mode, budgets,
concurrency limits, and approval policy. Conservative defaults keep all gates
on and `auto_publish` off. Start one cycle and inspect each persisted stage,
output reference, and cost counter.

## Pause, resume, and failed-cycle recovery

Pause prevents new scheduled work. Resume re-enables it. Stop after current
lets the active cycle finish but prevents the next schedule. After a process
restart, open the existing cycle and resume it. Never create a replacement
cycle simply because the process restarted. For failure, fix the reported
dependency, retry/resume the persisted cycle, and confirm completed outputs are
not duplicated.

## Publishing safety

Keep publishing in dry-run/private mode until assets, metadata, destination,
OAuth owner, and rights are reviewed. `research_only` is never equivalent to
`rights_ready`. Competitor media and voices are not automatically reused or
cloned. Queue reruns never republish automatically. Public publishing requires
an explicit action, cleared rights, and the configured approval gate.

## Encrypted credential operations

Read [credential storage and migration](CREDENTIAL_STORAGE.md) before connecting
accounts, upgrading a legacy database, restoring a backup, or rotating exposed
sessions. Configure the same master key for every restart; test migration on a
copy and keep the key backup separate from database backups.

## Download capacity enforcement (RC repair)

Download capacity is owner-scoped within one SQLite deployment, matching the
existing owner scope of cycle/render/publish limits. Multiple saved channel
configs for one owner share the lowest configured download limit; paused configs
still constrain unfinished work. Different owners have separate capacity.
The setting is applied at `DownloadService.download`, including channel queue,
retry, and Run All Again paths. Autonomous handlers bind their database/owner
context before invoking existing services. Competitor research remains metadata-only.

An atomic OS-lock claim precedes execution. Excess calls wait without creating
a failed attempt. Completion and exceptions release capacity; cancellation keeps
the slot until the underlying synchronous download actually stops. A cancelled
waiter never invokes a downloader. A worker crash releases its kernel locks;
lock marker files do not consume capacity and must not be deleted while workers
are running. Existing queue restart reconciliation marks interrupted attempts for
bounded retry, preserving logical source identity and history. Existing channel
dedup prevents queue re-submission of queued, processing, and successful videos.

Lowering a cap below current active downloads is rejected until they finish.
All workers must share the same canonical database path on a local filesystem
with working advisory locks. The supported web deployment is one application
process; a distributed/network-filesystem scheduler remains outside this release.
Without an autonomous config, the shared service retains a defensive ceiling of
20 per owner in addition to the existing pipeline's download rate limits.
