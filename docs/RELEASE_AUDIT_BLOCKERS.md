# Release Audit Blockers

## Resolution audit: 2026-09-09

The CP0-CP11 release blocker repair passes its engineering gates. No CP12 work
is included. The final push/tag gates must run against the closure commit;
this report does not substitute for remote/tag verification.

| Mandatory blocker | Resolution | Evidence |
| --- | --- | --- |
| Plaintext credentials | Authenticated TokenCipher storage for social access/refresh tokens and provider key/secret/extra fields; normal plaintext reads rejected; explicit atomic migration | Secret, migration, refresh, redaction, and tenant tests; disposable runtime-copy migration |
| Download concurrency setting unused | Owner-scoped atomic OS slots at DownloadService execution; shared across threads/local workers; lowest saved owner cap applies | Limit 1/3 requests and limit 2/5 requests; release on completion/failure/cancel; real process-crash recovery; owner/config isolation |
| Tracked runtime/build artifacts | 275 remaining generated/runtime paths removed from index only; precise ignores; source files no longer ignored | 1 runtime DB, 29 generated media files, 245 generated reports/subtitles; local hashes preserved |
| Uncommitted closure state | All intended repairs, tests, index removals, and preserved closure documentation prepared for one closure commit | Exact staged path review and clean-tree/remote checks required before RC tag |

Full regression: **1000 passed, 5 skipped, 0 failed, 0 collection errors**.
**LIVE_PROVIDER_CALLS=0**. Python compileall, all 9 JavaScript files,
OpenAPI (0 method/path collisions), and git diff whitespace checks pass.
Windows entrypoint starts with an ephemeral key and with a missing key;
credential writes require configuration explicitly.

Fresh/repeated initialization and compatible legacy-copy migrations pass
integrity and foreign-key checks. Two web database copies were tested; two
credential rows were migrated in the older copy. The root Telegram database
has a different users schema and is intentionally excluded from web migration.
The CLI rejects incompatible databases before writing. All three original
runtime database hashes remain unchanged.

Current index: **0 unintended runtime DBs, cookie DBs, browser profiles,
generated media, acceptance/temp artifacts, or caches**. One intentional media
asset remains: `src/universal_video_ai/web/static/demo-localization.mp4`, the
landing-page demonstration explicitly referenced by `static/index.html`.
No database or binary test fixtures are tracked.

## Historical credential risk

**POTENTIAL_HISTORICAL_SECRET_EXPOSURE**

CP11 history contains Chromium cookie/session artifacts, including four
Cookies/Cookies-journal/session-state paths. Browser/build trees had already
been removed from the current index by commits after CP11; this repair removes
the remaining tracked runtime database/data. Existing Git history is retained.
No history rewrite, force-push, credential revocation, or local user-data deletion
is performed. Rotate potentially affected sessions/OAuth grants/API keys as
described in [credential storage and migration](CREDENTIAL_STORAGE.md).

## Remaining user acceptance boundaries

Actual Ubuntu-host startup and live provider publishing/OAuth acceptance were
not performed. They remain user acceptance checks after the offline RC.
Provider App Review/permissions and external service availability remain
deployment prerequisites. The supported scheduler deployment is one web
application process; download slots additionally coordinate local workers using
the same database path and a local filesystem. Distributed scheduling and
network-filesystem locking are outside this release.

## Original closure audit (preserved historical record)

The 2026-09-07 audit returned PROJECT_FINAL_CLOSURE_RESULT: BLOCKED and did not
create a release candidate or tag. It reported plaintext social tokens,
4,093 tracked runtime files under scripts/local_data, a duplicated 4,313-file
Windows build tree, and 33 other media/database artifacts. CP11 was committed
and pushed as `ad990b5453b901502178acf0eff5b3b9059fa5c7`; the then-current
regression was 944 passed, 5 skipped, with zero live provider calls.

This repair actually began on a clean `dai2` at
`11849e2aac8178860ee982586c21e47807013b6b`, equal to the local origin/dai2
reference, after commits 4c5f009f and 11849e2a. The reviewed closure documents
and initial cipher were already committed and were preserved. Initial
modified/untracked inventory: none; unknown files removed: none.
