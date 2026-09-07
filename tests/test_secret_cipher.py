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


def test_missing_key_blocks_new_secret_writes_but_legacy_read_is_compatible(tmp_path):
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
    account = store.get_social_account(owner, "youtube")
    assert account["access_token"] == "legacy-access"
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
