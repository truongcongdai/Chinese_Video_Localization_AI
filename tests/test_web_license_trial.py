from types import SimpleNamespace
import asyncio
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


def test_periodic_account_sync_updates_local_credit_and_role_once(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"credits": 37, "is_admin": True}

    calls = []
    updates = []
    monkeypatch.setattr(web_app, "USE_USER_MANAGEMENT_SERVER", True)
    monkeypatch.setattr(web_app.store, "central_user_id", lambda user_id: 88)
    monkeypatch.setattr(
        web_app.requests, "get", lambda *args, **kwargs: calls.append((args, kwargs)) or FakeResponse()
    )
    monkeypatch.setattr(
        web_app.store, "set_credits", lambda user_id, value: updates.append(("credits", user_id, value))
    )
    monkeypatch.setattr(
        web_app.store, "set_admin", lambda user_id, value: updates.append(("admin", user_id, value))
    )
    web_app._central_account_last_sync.clear()

    web_app._sync_central_account(7)
    web_app._sync_central_account(7)

    assert len(calls) == 1
    assert updates == [("credits", 7, 37), ("admin", 7, True)]


def test_credit_mirror_falls_back_to_legacy_absolute_update(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code=200, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise web_app.requests.HTTPError(str(self.status_code))

        def json(self):
            return self._payload

    puts = []
    monkeypatch.setattr(web_app, "USER_MANAGEMENT_MIRROR_ENABLED", True)
    monkeypatch.setattr(web_app.store, "central_user_id", lambda user_id: 88)
    monkeypatch.setattr(web_app.requests, "post", lambda *args, **kwargs: FakeResponse(404))
    monkeypatch.setattr(
        web_app.requests, "get", lambda *args, **kwargs: FakeResponse(200, {"credits": 20})
    )
    monkeypatch.setattr(
        web_app.requests,
        "put",
        lambda *args, **kwargs: puts.append((args, kwargs)) or FakeResponse(200),
    )

    web_app._mirror_user_credit_delta(7, -3)

    assert puts[0][1]["json"] == {"credits": 17}


def test_default_account_and_trial_balances_are_fifteen(tmp_path):
    isolated_store = Store(tmp_path / "defaults.sqlite3")
    user_id = isolated_store.create_user("member", "hash")

    assert isolated_store.get_user_by_id(user_id)["credits"] == 15
    assert web_app.DEFAULT_USER_CREDITS == 15
    assert web_app.DEFAULT_TRIAL_TOKENS == 15


def test_license_admin_uses_authenticated_same_origin_facade():
    html = (web_app._REPO_ROOT / "license_server" / "static" / "admin.html").read_text(encoding="utf-8")

    assert "const ADMIN_API = `${location.origin}/api/admin`;" in html
    assert "`${ADMIN_API}/users/${id}/role`" in html
    assert ":8080/api" not in html
    assert ":8001/api" not in html
    assert "credentials:'same-origin'" in html


def test_failed_registration_email_never_exposes_otp(monkeypatch):
    invalidated = {}
    monkeypatch.setattr(web_app.store, "get_user_by_identifier", lambda identifier: None)
    monkeypatch.setattr(web_app.store, "create_verification_code", lambda *args: "123456")
    monkeypatch.setattr(
        web_app.store,
        "invalidate_verification_code",
        lambda identifier, purpose: invalidated.update(identifier=identifier, purpose=purpose),
    )
    monkeypatch.setattr(web_app, "_send_email_via_smtp", lambda *args: False)

    with pytest.raises(HTTPException) as exc_info:
        web_app.send_verification_code(
            web_app.SendVerificationCodeBody(contact_identifier="member@example.com")
        )

    assert exc_info.value.status_code == 503
    assert "123456" not in str(exc_info.value.detail)
    assert invalidated == {"identifier": "member@example.com", "purpose": "register"}


def test_registration_and_studio_defaults_are_safe_and_consolidated():
    html = (web_app._REPO_ROOT / "src" / "universal_video_ai" / "web" / "static" / "index.html").read_text(encoding="utf-8")

    assert 'id="publishing-enable-checkbox" checked' not in html
    assert 'id="publishing-panel" class="hidden"' in html
    assert 'data-feature="studio"' in html
    assert html.count('class="feature-tab"') == 1
    assert 'data-studio-feature="content-os"' in html
    assert 'data-studio-feature="trend"' in html
    assert 'data-studio-feature="ai-video"' in html
    assert 'data-studio-feature="affiliate"' in html
    assert "Mã chỉ được gửi qua email và không hiển thị trên trang." in html


def test_active_license_hides_activation_and_trial_actions():
    html = (web_app._REPO_ROOT / "src" / "universal_video_ai" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (web_app._REPO_ROOT / "src" / "universal_video_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'class="card hidden" id="license-card"' in html
    assert 'id="license-actions"' in html
    assert 'licenseCard.classList.toggle("hidden", licenseIsActive);' in app_js
    assert 'licenseActions.classList.toggle("hidden", licenseIsActive);' in app_js
    assert 'licenseCard.classList.remove("hidden");' in app_js
    assert 'licenseActions.classList.remove("hidden");' in app_js
    assert 'value="15"' in html


def test_bulk_retry_clones_eligible_selected_jobs_and_charges_once(monkeypatch):
    jobs = {
        "done-1": SimpleNamespace(id="done-1", user_id=7, status="done"),
        "error-1": SimpleNamespace(id="error-1", user_id=7, status="error"),
        "running-1": SimpleNamespace(id="running-1", user_id=7, status="running"),
    }
    created = []
    charged = []
    scheduled = []
    monkeypatch.setattr(web_app.store, "get_job", lambda job_id: jobs.get(job_id))
    monkeypatch.setattr(web_app.store, "get_user_by_id", lambda user_id: {"credits": 10})
    monkeypatch.setattr(
        web_app.store,
        "retry_job",
        lambda job_id, user_id: created.append(SimpleNamespace(id=f"new-{job_id}")) or created[-1],
    )
    monkeypatch.setattr(web_app, "_is_content_os_job", lambda job: False)
    monkeypatch.setattr(web_app, "_job_credit_cost", lambda user_id: 1)
    monkeypatch.setattr(web_app, "_adjust_user_credits", lambda user_id, delta: charged.append((user_id, delta)))
    monkeypatch.setattr(web_app, "_schedule_retried_job", lambda old, new: scheduled.append((old.id, new.id)))

    result = asyncio.run(web_app.bulk_retry_jobs(
        web_app.BulkDeleteBody(job_ids=list(jobs)), user_id=7,
    ))

    assert result["created"] == 2
    assert result["skipped"] == 1
    assert charged == [(7, -2)]
    assert scheduled == [("done-1", "new-done-1"), ("error-1", "new-error-1")]


def test_windows_build_embeds_public_license_server_default():
    build_script = (web_app._REPO_ROOT / "build_exe.bat").read_text(encoding="utf-8")
    wrapper = (web_app._REPO_ROOT / "scripts" / "run_web_wrapper.py").read_text(encoding="utf-8")
    app_source = (web_app._REPO_ROOT / "src" / "universal_video_ai" / "web" / "app.py").read_text(encoding="utf-8")

    assert "LICENSE_SERVER_URL=http://113.160.14.1:8000" in build_script
    assert "USER_MANAGEMENT_SERVER_URL=http://113.160.14.1:8001" in build_script
    assert "server_defaults.env" in wrapper
    assert 'default_url = "http://113.160.14.1:8000"' in app_source


def test_fresh_douyin_cookie_error_is_non_retryable_and_user_friendly():
    error = RuntimeError(
        "ERROR: [Douyin] 123: Fresh cookies (not necessarily logged in) are needed"
    )

    assert web_app._is_non_retryable_job_error(error) is True
    friendly = web_app._job_error_for_user(error)
    assert "Douyin yêu cầu cookie mới" in friendly
    assert "Traceback" not in friendly


def test_first_admin_uses_verified_registration_and_device_guard(monkeypatch, tmp_path):
    isolated_store = Store(tmp_path / "registration.sqlite3")
    first_code = isolated_store.create_verification_code("owner@example.com", "register")
    monkeypatch.setattr(web_app, "store", isolated_store)
    monkeypatch.setattr(web_app, "OPEN_REGISTRATION", False)
    monkeypatch.setattr(web_app, "USE_USER_MANAGEMENT_SERVER", False)
    monkeypatch.setattr(web_app, "USE_LICENSE_SERVER", False)
    monkeypatch.setattr(web_app, "_login_response", lambda user_id: {"user_id": user_id})

    result = web_app.register(web_app.RegisterBody(
        username="owner",
        contact_identifier="owner@example.com",
        password="password123",
        verification_code=first_code,
        device_id="browser-device-token-0001",
    ))

    owner = isolated_store.get_user_by_id(result["user_id"])
    assert owner["is_admin"] == 1
    assert owner["credits"] == 10_000

    second_code = isolated_store.create_verification_code("second@example.com", "register")
    monkeypatch.setattr(web_app, "OPEN_REGISTRATION", True)
    with pytest.raises(HTTPException, match="Thiết bị này đã nhận tài khoản"):
        web_app.register(web_app.RegisterBody(
            username="second",
            contact_identifier="second@example.com",
            password="password123",
            verification_code=second_code,
            device_id="browser-device-token-0001",
        ))


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
