from __future__ import annotations

import logging
import sqlite3

import pytest
from cryptography.fernet import Fernet

from universal_video_ai.secret_cipher import (
    ENCRYPTED_SECRET_PREFIX,
    SecretCipherConfigurationError,
    SecretCipherIntegrityError,
    TokenCipher,
)
from universal_video_ai.web.store import Store


def _raw_tokens(path, user_id, platform="youtube"):
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT access_token, refresh_token FROM social_accounts WHERE user_id=? AND platform=?",
            (user_id, platform),
        ).fetchone()


def test_access_and_refresh_tokens_are_encrypted_and_readable(tmp_path):
    path = tmp_path / "tokens.sqlite3"
    store = Store(path)
    owner = store.create_user("owner", "x")
    store.upsert_social_account(owner, "youtube", "access-secret", "refresh-secret")
    raw = _raw_tokens(path, owner)
    assert raw[0].startswith(ENCRYPTED_SECRET_PREFIX)
    assert raw[1].startswith(ENCRYPTED_SECRET_PREFIX)
    assert "access-secret" not in raw[0]
    assert "refresh-secret" not in raw[1]
    account = store.get_social_account(owner, "youtube")
    assert account["access_token"] == "access-secret"
    assert account["refresh_token"] == "refresh-secret"


def test_wrong_key_and_tampering_fail_without_exposing_secret(tmp_path, caplog):
    path = tmp_path / "integrity.sqlite3"
    first = Store(path, token_cipher=TokenCipher(Fernet.generate_key()))
    owner = first.create_user("owner", "x")
    first.upsert_social_account(owner, "youtube", "never-log-this", "refresh")
    wrong = Store(path, token_cipher=TokenCipher(Fernet.generate_key()))
    with caplog.at_level(logging.DEBUG), pytest.raises(SecretCipherIntegrityError) as failure:
        wrong.get_social_account(owner, "youtube")
    assert "never-log-this" not in str(failure.value)
    assert "never-log-this" not in caplog.text

    with sqlite3.connect(path) as conn:
        value = conn.execute(
            "SELECT access_token FROM social_accounts WHERE user_id=?", (owner,)
        ).fetchone()[0]
        replacement = value[:-1] + ("A" if value[-1] != "A" else "B")
        conn.execute(
            "UPDATE social_accounts SET access_token=? WHERE user_id=?", (replacement, owner)
        )
    with pytest.raises(SecretCipherIntegrityError):
        first.get_social_account(owner, "youtube")


def test_missing_key_blocks_new_secret_writes_and_legacy_requires_migration(tmp_path):
    path = tmp_path / "missing.sqlite3"
    store = Store(path, token_cipher=TokenCipher(b""))
    owner = store.create_user("owner", "x")
    with pytest.raises(SecretCipherConfigurationError):
        store.upsert_social_account(owner, "youtube", "new-secret", "refresh")
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO social_accounts "
            "(user_id,platform,access_token,refresh_token,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (owner, "youtube", "legacy-access", "legacy-refresh", 1.0, 1.0),
        )
    with pytest.raises(SecretCipherConfigurationError, match="migration"):
        store.get_social_account(owner, "youtube")
    assert store.token_cipher.decrypt_secret("legacy-access", allow_legacy=True) == "legacy-access"
    with pytest.raises(SecretCipherConfigurationError):
        store.migrate_legacy_social_account_tokens()


def test_legacy_migration_is_owner_scoped_idempotent_and_never_double_encrypts(tmp_path):
    path = tmp_path / "migration.sqlite3"
    store = Store(path)
    owner = store.create_user("owner", "x")
    foreign = store.create_user("foreign", "x")
    with store._connect() as conn:
        for user_id, token in ((owner, "owner-token"), (foreign, "foreign-token")):
            conn.execute(
                "INSERT INTO social_accounts "
                "(user_id,platform,access_token,refresh_token,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (user_id, "youtube", token, token + "-refresh", 1.0, 1.0),
            )
    assert store.migrate_legacy_social_account_tokens(owner) == 1
    first_raw = _raw_tokens(path, owner)
    assert first_raw[0].startswith(ENCRYPTED_SECRET_PREFIX)
    assert _raw_tokens(path, foreign)[0] == "foreign-token"
    assert store.migrate_legacy_social_account_tokens(owner) == 0
    assert _raw_tokens(path, owner) == first_raw
    assert store.migrate_legacy_social_account_tokens() == 1
    assert store.get_social_account(foreign, "youtube")["access_token"] == "foreign-token"


def test_social_account_listing_preserves_tenant_isolation_and_api_redaction(tmp_path, monkeypatch):
    import universal_video_ai.web.app as web_app

    path = tmp_path / "api.sqlite3"
    store = Store(path)
    owner = store.create_user("owner", "x")
    foreign = store.create_user("foreign", "x")
    store.upsert_social_account(owner, "youtube", "owner-access", "owner-refresh", account_name="Mine")
    store.upsert_social_account(foreign, "youtube", "foreign-access", "foreign-refresh", account_name="Theirs")
    monkeypatch.setattr(web_app, "store", store)

    class Client:
        def is_configured(self):
            return True

        def not_configured_message(self):
            return ""

    monkeypatch.setattr(web_app.oauth_module, "get_oauth_client", lambda platform: Client())
    payload = web_app.list_social_connections(user_id=owner)
    serialized = repr(payload)
    assert payload["youtube"]["account_name"] == "Mine"
    assert "owner-access" not in serialized and "owner-refresh" not in serialized
    assert "foreign-access" not in serialized and "Theirs" not in serialized


def test_cipher_randomized_authenticated_and_no_double_encryption():
    cipher = TokenCipher(Fernet.generate_key())
    first = cipher.encrypt_secret("same-secret")
    second = cipher.encrypt_secret("same-secret")
    assert first != second
    assert cipher.decrypt_secret(first) == cipher.decrypt_secret(second) == "same-secret"
    assert cipher.encrypt_secret(first) == first
    assert cipher.encrypt_secret(None) is None
    assert cipher.encrypt_secret("") == ""


@pytest.mark.parametrize("operation", ["upsert", "refresh"])
def test_refresh_write_encrypts_retained_legacy_refresh_token(tmp_path, operation):
    store = Store(tmp_path / "retained.sqlite3")
    owner = store.create_user("owner", "x")
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO social_accounts (user_id,platform,access_token,refresh_token,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?)", (owner, "youtube", "old-access", "legacy-retained-refresh", 1, 1)
        )
    if operation == "upsert":
        store.upsert_social_account(owner, "youtube", "new-access")
    else:
        store.update_social_access_token(owner, "youtube", "new-access", 9999)
    assert all(value.startswith(ENCRYPTED_SECRET_PREFIX) for value in _raw_tokens(store.db_path, owner))
    assert store.get_social_account(owner, "youtube")["refresh_token"] == "legacy-retained-refresh"


def test_migration_rolls_back_all_rows_if_a_ciphertext_cannot_authenticate(tmp_path):
    store = Store(tmp_path / "rollback.sqlite3")
    owner = store.create_user("owner", "x")
    store.upsert_social_account(owner, "youtube", "valid-secret", "refresh")
    with store._connect() as conn:
        conn.execute("UPDATE social_accounts SET access_token='legacy-visible',refresh_token='enc:v1:tampered'")
    with pytest.raises(SecretCipherIntegrityError):
        store.migrate_legacy_social_account_tokens()
    assert _raw_tokens(store.db_path, owner) == ("legacy-visible", "enc:v1:tampered")


def test_migrated_copy_refreshes_via_mock_without_touching_original(tmp_path):
    import hashlib
    import shutil
    from universal_video_ai.channel_agent.youtube import GoogleOAuthTokenService

    source = tmp_path / "original.sqlite3"
    original = Store(source)
    owner = original.create_user("owner", "x")
    with original._connect() as conn:
        conn.execute(
            "INSERT INTO social_accounts (user_id,platform,access_token,refresh_token,expires_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)", (owner, "youtube", "legacy-copy-access", "legacy-copy-refresh", 1, 1, 1)
        )
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    copied = tmp_path / "copy.sqlite3"
    shutil.copy2(source, copied)
    migrated = Store(copied)
    assert migrated.migrate_legacy_social_account_tokens() == 1
    assert b"legacy-copy-access" not in copied.read_bytes()
    assert b"legacy-copy-refresh" not in copied.read_bytes()

    class OAuth:
        def refresh_access_token_details(self, refresh_token):
            assert refresh_token == "legacy-copy-refresh"
            return {"access_token": "renewed-copy-access", "expires_in": 3600}

    service = GoogleOAuthTokenService(migrated, oauth_factory=OAuth)
    assert service.get_valid_access_token(owner, required_scopes=set()) == "renewed-copy-access"
    assert b"renewed-copy-access" not in copied.read_bytes()
    assert migrated.migrate_legacy_social_account_tokens() == 0
    with migrated._connect() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_oauth_echoed_exception_and_error_parameter_do_not_leak(tmp_path, monkeypatch, caplog):
    import universal_video_ai.web.app as web_app
    from starlette.requests import Request
    store = Store(tmp_path / "oauth-errors.sqlite3")
    owner = store.create_user("owner", "x")
    store.create_oauth_state("test-state", owner, "facebook")
    monkeypatch.setattr(web_app, "store", store)

    class OAuth:
        def exchange_code(self, *args):
            raise RuntimeError("provider echoed access=never-expose-me")

    monkeypatch.setattr(web_app.oauth_module, "get_oauth_client", lambda platform: OAuth())
    request = Request({"type": "http", "headers": [], "scheme": "http", "server": ("localhost", 80), "path": "/"})
    with caplog.at_level(logging.DEBUG):
        response = web_app.social_callback("facebook", request, code="test-code", state="test-state")
    assert response.status_code == 400
    assert "never-expose-me" not in caplog.text + response.body.decode()
    denied = web_app.social_callback("facebook", request, error="<script>never-expose-me</script>")
    assert "never-expose-me" not in denied.body.decode()


def test_missing_key_api_is_explicit_and_listing_needs_no_key(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import universal_video_ai.web.app as web_app
    from universal_video_ai.web.auth import get_current_user_id

    path = tmp_path / "no-key.sqlite3"
    stored = Store(path)
    owner = stored.create_user("owner", "x")
    stored.upsert_social_account(owner, "youtube", "secret", "refresh")
    monkeypatch.setattr(web_app, "store", Store(path, token_cipher=TokenCipher(b"")))
    class OAuth:
        def is_configured(self):
            return True
        def not_configured_message(self):
            return ""
    monkeypatch.setattr(web_app.oauth_module, "get_oauth_client", lambda platform: OAuth())
    web_app.app.dependency_overrides[get_current_user_id] = lambda: owner
    try:
        client = TestClient(web_app.app)
        assert client.get("/api/social/connections").status_code == 200
        response = client.get("/api/social/connect/youtube")
        assert response.status_code == 503
        assert "APP_SECRET_ENCRYPTION_KEY" in response.json()["detail"]
        assert "refresh" not in response.json()["detail"].lower()
    finally:
        web_app.app.dependency_overrides.pop(get_current_user_id, None)


def test_provider_state_cache_and_automation_reject_nested_credentials(tmp_path):
    from universal_video_ai.channel_agent.automation import AutomationOrchestrator, AutomationError
    from universal_video_ai.channel_agent.facebook_publishing import _safe_provider_state
    from universal_video_ai.provider_runtime import _safe_normalize
    payload = {"id": "safe", "nested": {"refresh_token": "sensitive", "API_KEY": "sensitive", "cookies": "sensitive"}}
    assert "sensitive" not in repr(_safe_provider_state(payload))
    assert "sensitive" not in repr(_safe_normalize(payload))
    store = Store(tmp_path / "config.sqlite3")
    owner = store.create_user("owner", "x")
    service = AutomationOrchestrator(store, handlers={"research": lambda *args: payload})
    with pytest.raises(AutomationError, match="credentials"):
        service.start(owner, mode="FULL_AUTONOMOUS", configuration=payload)
    run, _ = service.start(owner, mode="FULL_AUTONOMOUS", configuration={})
    result = service.advance(owner, run["id"])
    assert result["status"] == "failed"
    assert "sensitive" not in repr(result)
    assert b"sensitive" not in store.db_path.read_bytes()


def test_provider_keys_secrets_and_extra_are_encrypted_and_api_redacted(tmp_path, monkeypatch):
    import universal_video_ai.web.app as web_app
    store = Store(tmp_path / "provider.sqlite3")
    owner = store.create_user("owner", "x")
    store.upsert_provider_settings(owner, "openai", "private-api-key", "private-api-secret",
                                   extra={"refresh_token": "private-extra-token", "models": ["gpt-test"]})
    raw = store.db_path.read_bytes()
    for secret in (b"private-api-key", b"private-api-secret", b"private-extra-token"):
        assert secret not in raw
    settings = store.get_provider_settings(owner, "openai")
    assert settings["api_key"] == "private-api-key"
    assert settings["api_secret"] == "private-api-secret"
    store.upsert_provider_settings(owner, "openai", default_model="gpt-test")
    assert store.get_provider_settings(owner, "openai")["api_key"] == "private-api-key"
    monkeypatch.setattr(web_app, "store", store)
    assert "private-" not in repr(web_app.provider_settings(user_id=owner))
    foreign = store.create_user("foreign", "x")
    assert store.get_provider_settings(foreign, "openai") is None
    assert store.list_provider_settings(foreign) == []


def test_legacy_provider_migration_is_atomic_and_idempotent(tmp_path):
    store = Store(tmp_path / "legacy-provider.sqlite3")
    owner = store.create_user("owner", "x")
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO user_provider_settings (user_id,provider,api_key,api_secret,extra_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (owner, "openai", "legacy-api-key", "legacy-api-secret", '{"token":"legacy-extra-token"}', 1, 1),
        )
    with pytest.raises(SecretCipherConfigurationError):
        store.get_provider_settings(owner, "openai")
    assert store.migrate_legacy_social_account_tokens(owner) == 1
    assert store.migrate_legacy_social_account_tokens(owner) == 0
    assert store.get_provider_settings(owner, "openai")["extra"]["token"] == "legacy-extra-token"
    assert b"legacy-" not in store.db_path.read_bytes()


def test_missing_key_blocks_provider_credentials(tmp_path):
    store = Store(tmp_path / "provider-no-key.sqlite3", token_cipher=TokenCipher(b""))
    owner = store.create_user("owner", "x")
    with pytest.raises(SecretCipherConfigurationError):
        store.upsert_provider_settings(owner, "openai", "never-plaintext")
    assert b"never-plaintext" not in store.db_path.read_bytes()


def test_provider_cache_does_not_retain_echoed_credentials():
    from universal_video_ai.provider_runtime import execute_provider_call, reset_cost_report, _MEMORY_CACHE
    reset_cost_report(clear_cache=True)
    try:
        execute_provider_call("example", "models", {}, mode="CACHE", cacheable=True,
                              live=lambda: pytest.fail("live called"),
                              mock=lambda: {"models": ["safe"], "refresh_token": "echoed-credential"})
        assert "echoed-credential" not in repr(_MEMORY_CACHE)
    finally:
        reset_cost_report(clear_cache=True)


def test_migration_cli_missing_key_does_not_touch_database(tmp_path):
    import hashlib
    import os
    from pathlib import Path
    import subprocess
    import sys
    path = tmp_path / "cli.sqlite3"
    Store(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    environment = dict(os.environ, APP_SECRET_ENCRYPTION_KEY="")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "scripts/migrate_social_tokens.py"), "--db", str(path)],
        env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "APP_SECRET_ENCRYPTION_KEY" in result.stderr
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_oauth_http_debug_logs_and_result_repr_hide_credentials(caplog, monkeypatch):
    import requests
    from universal_video_ai.web.oauth import ConnectResult, FacebookOAuth
    result = ConnectResult("hidden-access", "hidden-refresh", None, "Page", "1")
    assert "hidden-" not in repr(result)
    with caplog.at_level(logging.DEBUG, logger="urllib3.connectionpool"):
        logging.getLogger("urllib3.connectionpool").debug(
            '%s', 'GET /oauth/access_token?client_secret=hidden-client&code=hidden-code'
        )
    assert "hidden-" not in caplog.text
    monkeypatch.setenv("FACEBOOK_APP_ID", "test-id")
    monkeypatch.setenv("FACEBOOK_APP_SECRET", "hidden-client")
    def failing(*args, **kwargs):
        raise requests.RequestException("provider echoed hidden-client")
    monkeypatch.setattr(requests, "get", failing)
    with pytest.raises(ValueError) as failure:
        FacebookOAuth().exchange_code("hidden-code", "http://localhost/callback")
    import traceback
    assert "hidden-client" not in "".join(traceback.format_exception(failure.value))
