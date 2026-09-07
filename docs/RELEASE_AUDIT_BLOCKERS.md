# Release Audit Blockers

The 2026-09-07 closure audit did not create `v1.0.0-rc1` because these required
security/hygiene gates are not green:

1. `social_accounts.access_token` and `social_accounts.refresh_token` are
   persisted directly as SQLite text. Owner scoping and API redaction pass, but
   at-rest token encryption and a migration/key-rotation design are required.
2. The Git index tracks runtime/generated data: 4,093 files under
   `scripts/local_data`, including Chromium `Cookies` databases, plus a
   duplicated 4,313-file Windows build tree and 33 tracked media/database
   artifacts. These need an explicitly reviewed removal/history-remediation
   decision; the closure audit did not delete them automatically.
3. Ubuntu source portability was reviewed, but the actual Ubuntu host was not
   available. Live YouTube and Facebook provider acceptance was also not run.

CP11 itself was committed and pushed as `ad990b5453b901502178acf0eff5b3b9059fa5c7`.
The code/test runtime gates passed, including 944 passed tests and zero normal
pytest live provider calls. The items above still prevent an RC tag.
