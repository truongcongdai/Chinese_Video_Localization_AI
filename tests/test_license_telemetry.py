import time
import asyncio
from types import SimpleNamespace

from license_server import server
from license_server import user_management_server


def _insert_license(db_path, *, user_id=22, key="licensed-telemetry-key"):
    server.DB_PATH = db_path
    server.init_db()
    now = time.time()
    conn = server.get_db()
    conn.execute(
        """INSERT INTO licenses
           (license_key, customer_name, customer_email, user_id, plan_type,
            quota_type, features, expiry_date, max_jobs, max_tokens, status,
            machine_id, created_at, updated_at, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            key, "Member", "member@example.com", user_id, "pro", "credit",
            "[]", now + 86400, -1, 100, "active", None, now, now, None,
        ),
    )
    conn.commit()
    conn.close()


def test_telemetry_ingest_is_idempotent_and_keeps_metadata_only(tmp_path, monkeypatch):
    db_path = tmp_path / "licenses.sqlite3"
    monkeypatch.setattr(server, "DB_PATH", db_path)
    _insert_license(db_path)
    now = time.time()
    payload = server.ClientTelemetry(
        license_key="licensed-telemetry-key",
        machine_id="machine-22",
        user_id=22,
        app_version="1.2.3",
        jobs=[server.TelemetryJob(
            id="job-1",
            title="A source video",
            source_url="https://example.com/video",
            status="running",
            progress_note="Transcribing",
            created_at=now - 20,
            updated_at=now,
        )],
    )

    assert server.ingest_client_telemetry(payload)["accepted"] == 1
    payload.jobs[0].status = "done"
    payload.jobs[0].has_video = True
    assert server.ingest_client_telemetry(payload)["accepted"] == 1

    conn = server.get_db()
    jobs = conn.execute("SELECT * FROM client_jobs").fetchall()
    nodes = conn.execute("SELECT * FROM client_nodes").fetchall()
    conn.close()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "done"
    assert jobs[0]["has_video"] == 1
    assert "path" not in jobs[0].keys()
    assert len(nodes) == 1


def test_admin_session_is_signed_and_expires(monkeypatch):
    monkeypatch.setattr(server, "_admin_session_secret", b"test-secret")
    token = server._encode_admin_session(4, "operator")

    assert server._decode_admin_session(token)["username"] == "operator"
    assert server._decode_admin_session(token + "tampered") is None


def test_existing_account_service_gets_recoverable_first_admin(tmp_path, monkeypatch):
    db_path = tmp_path / "users.sqlite3"
    monkeypatch.setattr(user_management_server, "DB_PATH", str(db_path))
    user_management_server.init_db()
    conn = user_management_server.get_db()
    now = time.time()
    conn.execute(
        """INSERT INTO users
           (username, email, password_hash, credits, is_admin, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("owner", "owner@example.com", "hash", 25, False, now, now),
    )
    conn.commit()
    conn.close()

    user_management_server.init_db()
    conn = user_management_server.get_db()
    owner = conn.execute("SELECT is_admin FROM users WHERE username = 'owner'").fetchone()
    conn.close()
    assert owner["is_admin"] == 1


def test_legacy_email_license_is_linked_to_central_user_once(tmp_path, monkeypatch):
    db_path = tmp_path / "licenses.sqlite3"
    monkeypatch.setattr(server, "DB_PATH", db_path)
    _insert_license(db_path, user_id=None, key="legacy-email-license")
    monkeypatch.setattr(
        server,
        "_account_server_request",
        lambda *args, **kwargs: SimpleNamespace(
            json=lambda: {"id": 41, "email": "member@example.com"}
        ),
    )

    result = asyncio.run(server.get_license_by_user(41))

    assert result["license"]["user_id"] == 41
    conn = server.get_db()
    linked_user_id = conn.execute(
        "SELECT user_id FROM licenses WHERE license_key = 'legacy-email-license'"
    ).fetchone()["user_id"]
    conn.close()
    assert linked_user_id == 41


def test_admin_credit_update_works_with_legacy_absolute_user_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(
        server,
        "_account_server_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs))
        or SimpleNamespace(json=lambda: {"success": True}),
    )

    result = server.admin_set_credits(
        7, server.AdminCreditSet(set_to=15), _session={"uid": 1}
    )

    assert result == {"success": True}
    assert calls == [("PUT", "/api/users/7", {"json": {"credits": 15}})]


def test_new_central_account_starts_with_fifteen_credits(tmp_path, monkeypatch):
    db_path = tmp_path / "users.sqlite3"
    monkeypatch.setattr(user_management_server, "DB_PATH", str(db_path))
    user_management_server.init_db()

    result = asyncio.run(user_management_server.register_user(
        user_management_server.UserRegister(
            username="new-member", email="new@example.com", password="password123"
        )
    ))

    assert result["credits"] == 15
