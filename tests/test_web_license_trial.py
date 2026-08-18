from types import SimpleNamespace
import time

import pytest
from fastapi import HTTPException

from universal_video_ai.web import app as web_app
from universal_video_ai.web.store import Store


def _trial(*, tokens: int = 25, expires_in: float = 3600):
    return SimpleNamespace(
        tokens_remaining=tokens,
        expiry_date=time.time() + expires_in,
    )


def test_central_license_mode_accepts_active_free_trial(monkeypatch):
    monkeypatch.setattr(web_app, "USE_LICENSE_SERVER", True)
    monkeypatch.setattr(web_app.store, "get_machine_id", lambda: "machine-1")
    monkeypatch.setattr(web_app.store, "get_license_by_user", lambda user_id: None)
    monkeypatch.setattr(web_app.store, "get_free_trial", lambda user_id, machine_id: _trial())

    assert web_app._require_license_or_trial(7) == "trial"


@pytest.mark.parametrize(
    ("trial", "message"),
    [
        (_trial(tokens=0), "Đã hết token trial"),
        (_trial(expires_in=-1), "Trial đã hết hạn"),
    ],
)
def test_central_license_mode_rejects_unusable_trial(monkeypatch, trial, message):
    monkeypatch.setattr(web_app, "USE_LICENSE_SERVER", True)
    monkeypatch.setattr(web_app.store, "get_machine_id", lambda: "machine-1")
    monkeypatch.setattr(web_app.store, "get_license_by_user", lambda user_id: None)
    monkeypatch.setattr(web_app.store, "get_free_trial", lambda user_id, machine_id: trial)

    with pytest.raises(HTTPException, match=message):
        web_app._require_license_or_trial(7)


def test_central_license_mode_prompts_for_trial_or_license(monkeypatch):
    monkeypatch.setattr(web_app, "USE_LICENSE_SERVER", True)
    monkeypatch.setattr(web_app.store, "get_machine_id", lambda: "machine-1")
    monkeypatch.setattr(web_app.store, "get_license_by_user", lambda user_id: None)
    monkeypatch.setattr(web_app.store, "get_free_trial", lambda user_id, machine_id: None)

    with pytest.raises(HTTPException, match="bắt đầu dùng thử miễn phí"):
        web_app._require_license_or_trial(7)


def test_central_license_unlimited_limits_are_not_treated_as_exhausted(monkeypatch):
    license_record = SimpleNamespace(license_key="key-1")
    monkeypatch.setattr(web_app, "USE_LICENSE_SERVER", True)
    monkeypatch.setattr(web_app.store, "get_machine_id", lambda: "machine-1")
    monkeypatch.setattr(web_app.store, "get_license_by_user", lambda user_id: license_record)
    monkeypatch.setattr(
        web_app,
        "validate_license_with_server",
        lambda key, machine: {
            "valid": True,
            "license": {
                "jobs_used": 10,
                "tokens_used": 10,
                "max_jobs": -1,
                "max_tokens": -1,
            },
        },
    )

    assert web_app._require_license_or_trial(7) == "license"


def test_central_login_refreshes_cached_admin_role(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "username": "member",
                "email": "member@example.com",
                "is_admin": True,
                "credits": 42,
            }

    updates = {}
    monkeypatch.setattr(web_app, "USE_USER_MANAGEMENT_SERVER", True)
    monkeypatch.setattr(web_app.requests, "post", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(web_app.store, "get_user_by_identifier", lambda identifier: {"id": 7})
    monkeypatch.setattr(web_app.store, "set_admin", lambda user_id, value: updates.update(admin=(user_id, value)))
    monkeypatch.setattr(web_app.store, "set_credits", lambda user_id, value: updates.update(credits=(user_id, value)))
    monkeypatch.setattr(web_app, "_ensure_registration_trial", lambda user_id: updates.update(trial_user=user_id))
    monkeypatch.setattr(web_app, "_login_response", lambda user_id: {"user_id": user_id})

    result = web_app.login(web_app.LoginBody(identifier="member", password="password123"))

    assert result == {"user_id": 7}
    assert updates == {
        "admin": (7, True),
        "credits": (7, 42),
        "trial_user": 7,
    }


def test_license_admin_updates_role_at_the_user_source():
    html = (web_app._REPO_ROOT / "license_server" / "static" / "admin.html").read_text(encoding="utf-8")

    assert "`${USER_MANAGEMENT_API_BASE}/${userId}`" in html
    assert "usersApiSource === 'user-management'" in html


def test_store_links_central_license_id_to_one_local_user(tmp_path):
    store = Store(tmp_path / "web.sqlite3")
    first_user = store.create_user("first", "hash")
    second_user = store.create_user("second", "hash")
    remote = {
        "id": 91,
        "license_key": "central-key",
        "customer_name": "Customer",
        "customer_email": "customer@example.com",
        "plan_type": "basic",
        "features": ["video_localization"],
        "expiry_date": time.time() + 86400,
        "max_jobs": 100,
        "max_tokens": 1000,
        "status": "active",
        "notes": "central",
    }

    linked = store.link_remote_license(remote, first_user)

    assert linked.remote_license_id == 91
    assert linked.user_id == first_user
    assert linked.features == ["video_localization"]
    assert linked.to_dict()["features"] == ["video_localization"]
    with pytest.raises(ValueError, match="user khác"):
        store.link_remote_license(remote, second_user)


def test_activate_license_imports_key_from_central_server(monkeypatch):
    remote = {
        "id": 91,
        "license_key": "central-key",
        "features": ["video_localization"],
        "status": "active",
    }
    linked = SimpleNamespace(to_dict=lambda: {"license_key": "central-key", "remote_license_id": 91})
    captured = {}

    monkeypatch.setattr(web_app, "USE_LICENSE_SERVER", True)
    monkeypatch.setattr(web_app.store, "get_machine_id", lambda: "machine-1")
    monkeypatch.setattr(
        web_app,
        "validate_license_with_server",
        lambda key, machine: {"valid": True, "license": remote},
    )
    monkeypatch.setattr(
        web_app.store,
        "link_remote_license",
        lambda data, user_id: captured.update(data=data, user_id=user_id) or linked,
    )

    result = web_app.activate_license(web_app.LicenseActivateBody(license_key=" central-key "), user_id=7)

    assert result["ok"] is True
    assert result["license"]["remote_license_id"] == 91
    assert captured == {"data": remote, "user_id": 7}


def test_completed_job_updates_central_license_by_remote_id(monkeypatch):
    license_record = SimpleNamespace(id=3, remote_license_id=91)
    calls = []
    monkeypatch.setattr(web_app, "USE_LICENSE_SERVER", True)
    monkeypatch.setattr(web_app.store, "get_license_by_user", lambda user_id: license_record)
    monkeypatch.setattr(
        web_app.store,
        "update_license_usage",
        lambda license_id, user_id, **deltas: calls.append(("local", license_id, user_id, deltas)),
    )
    monkeypatch.setattr(web_app.store, "get_machine_id", lambda: "machine-1")
    monkeypatch.setattr(
        web_app,
        "update_license_usage_on_server",
        lambda license_id, machine_id, **deltas: calls.append(
            ("remote", license_id, machine_id, deltas)
        ) or True,
    )

    web_app._update_license_usage(7, "job-1")

    assert calls[0][0:3] == ("local", 3, 7)
    assert calls[1][0:3] == ("remote", 91, "machine-1")
