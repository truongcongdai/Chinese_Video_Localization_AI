# Credential storage and migration

The application uses the existing owner-scoped `social_accounts` architecture.
Google/YouTube and TikTok access/refresh tokens and Facebook Page/user tokens
are encrypted before SQLite writes. The same cipher covers
`user_provider_settings.api_key`, `api_secret`, and `extra_json`.
OAuth app secrets supplied through environment variables remain in the environment.

`TokenCipher` uses versioned `enc:v1:` Fernet envelopes: AES-128-CBC,
HMAC-SHA256 authentication, and a fresh cryptographically random IV per value.
The key is a URL-safe base64 encoding of 32 random bytes, as specified in the
[cryptography Fernet documentation](https://cryptography.io/en/latest/fernet/).
Wrong keys, changed ciphertext, and malformed envelopes fail without exposing
the credential. Existing encrypted fields are authenticated and kept unchanged.

## Configure the key

Generate once on a trusted operator machine, assigning directly to the environment
rather than printing the key:

```powershell
$env:APP_SECRET_ENCRYPTION_KEY = python -c "from cryptography.fernet import Fernet; import sys; sys.stdout.write(Fernet.generate_key().decode('ascii'))"
```

Store this value in your deployment secret manager and inject the same value on
every restart and into every worker. Do not use the public deterministic pytest
key, a password, session secret, UUID, or a checked-in default.
The application never writes this master key to SQLite or returns/logs it.
`.env.example` deliberately has an empty placeholder. Restrict access if using a
local ignored `.env`; a managed environment secret is preferred.

Back up the key separately from the database in an access-controlled encrypted
secret vault. Record which backup uses which key. Test recovery on a database
copy. Losing the key makes encrypted credentials unusable. Changing the environment
value alone is not key rotation: existing ciphertext requires its original key.
For an encryption-key compromise, stop workers, revoke affected provider credentials,
and perform a reviewed offline re-encryption/reconnection procedure on a backed-up
copy before switching all workers together.

## Migrate a legacy database

Normal reads reject plaintext credentials with an explicit migration error.
New secret writes always require a valid key, including MOCK/DRY_RUN outside
pytest; no code path silently falls back to plaintext. Web startup without a key
can serve the UI and social connection metadata. Credential connection/storage
returns HTTP 503 with the configuration requirement before an OAuth/provider call.
The cipher's explicit `allow_legacy=True` option is only for controlled migration
inspection and is never used by normal application reads.

1. Stop every process writing the selected database and back up the database,
   its WAL/SHM files if present, and owned assets. Preserve the original.
2. Copy the stopped database to an isolated location. Configure the encryption
   key in the migration process environment.
3. Run against the COPY first:

```powershell
python scripts\migrate_social_tokens.py --db C:\safe-backups\acceptance-copy.sqlite3
# Optional: migrate only one owner's social/provider rows
python scripts\migrate_social_tokens.py --db C:\safe-backups\acceptance-copy.sqlite3 --user-id 1
```

The CLI requires an existing file and checks the key before initializing it.
One transaction encrypts legacy fields in both credential tables. It is
idempotent and restart-safe: an authentication error rolls back all row changes.
A repeated run authenticates existing ciphertext without double encryption.
An owner selection leaves other owners' logical values unchanged.
Secure deletion, VACUUM, and WAL truncation remove old SQLite cell/WAL copies
from the selected database; a busy checkpoint reports an error.
Filesystem snapshots and prior backups are outside that guarantee.

4. Verify integrity and mocked refresh on the copy. For an operational rollout,
   stop workers and explicitly run the same command on the backed-up deployment
   database, or deploy the verified copy with the matching key.
   Release acceptance itself never migrates the user's real runtime database.
5. Restart workers with that same key. If a key is missing, malformed, or wrong,
   correct the configuration; never bypass authentication or store plaintext.

## Redaction and refresh

OAuth callbacks write through Store; GoogleOAuthTokenService centralizes owned
refresh updates, and Meta Page selection uses the same owned upsert. A token
update also encrypts a retained legacy refresh token in the same transaction.
Social status queries select metadata only. Provider lists expose fixed masks
and sanitized metadata. Decrypted values stay in server-side provider calls;
they are not AutomationRun configuration, publishing packages, or frontend state.
Provider-cache values remove credential fields, OAuth responses are non-cacheable,
and OAuth HTTP diagnostics/errors and ConnectResult repr conceal credentials.
The documented Windows entrypoint disables URL access logging because callback
URLs contain one-use authorization codes. Configure reverse-proxy/access tracing
to omit OAuth callback query strings as well.

## Historical exposure

`POTENTIAL_HISTORICAL_SECRET_EXPOSURE`

CP11 history contains Chromium cookie/session artifacts. Later commits removed
the browser/build trees from the current index; this repair removes the remaining
runtime database and generated data. Removing index entries does not remove old
Git objects, forks, clones, or backups.

Rotate affected browser sessions and revoke/reconnect Google/YouTube, Meta, and
TikTok app grants where those sessions or stored tokens could have been exposed.
Rotate any provider API keys or OAuth app secrets present in affected files.
Use each provider's account/session controls, then reconnect through the app with
encryption configured. No credentials are automatically revoked by this release.
No history rewriting or force-push is performed.
