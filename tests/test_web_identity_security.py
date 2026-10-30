"""Offline identity-provider verification and credential-safe failure handling."""
from __future__ import annotations

import json
import logging
import traceback

import pytest
import requests

from universal_video_ai.web.identity_oauth import (
    FacebookIdentityOAuth,
    GitHubIdentityOAuth,
    GoogleIdentityOAuth,
    IdentityOAuthError,
    IdentityResult,
)


@pytest.fixture(autouse=True)
def isolated_provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use fake app credentials and refuse any unfaked HTTP operation."""
    for name in (
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID",
        "GITHUB_CLIENT_SECRET", "FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET",
    ):
        monkeypatch.setenv(name, "fake-identity-test-credential")

    def blocked(*args: object, **kwargs: object) -> None:
        pytest.fail("Identity security tests must not make network requests")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


def reply(data: object, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.reason = "provider rejected request"
    response.url = (
        "https://provider.invalid/oauth/token?client_secret=SECRET_SENTINEL"
        "&code=CODE_SENTINEL&access_token=TOKEN_SENTINEL"
    )
    response._content = json.dumps(data).encode()
    return response


def fake_google(
    monkeypatch: pytest.MonkeyPatch, profile: dict[str, object], status: int = 200,
) -> None:
    monkeypatch.setattr(requests, "post", lambda *a, **kw: reply({"access_token": "fake-token"}))
    monkeypatch.setattr(requests, "get", lambda *a, **kw: reply(profile, status))


def test_identity_email_verification_defaults_to_false() -> None:
    result = IdentityResult("stable-id", "contact@example.test", "Display Name")
    assert result.email_verified is False


@pytest.mark.parametrize("verification", [False, None, "true", 1])
def test_google_does_not_expose_an_unverified_email(
    monkeypatch: pytest.MonkeyPatch, verification: object,
) -> None:
    fake_google(monkeypatch, {
        "sub": "stable-id", "email": "claimed@example.test",
        "email_verified": verification,
    })
    result = GoogleIdentityOAuth().exchange_code("fake-code", "https://app.test/callback")
    assert result.provider_user_id == "stable-id"
    assert result.email is None
    assert result.email_verified is False


def test_google_requires_an_explicit_verification_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_google(monkeypatch, {"sub": "stable-id", "email": "claimed@example.test"})
    result = GoogleIdentityOAuth().exchange_code("fake-code", "https://app.test/callback")
    assert result.email is None and result.email_verified is False


@pytest.mark.parametrize("email", [None, "", "   ", ["claimed@example.test"]])
def test_google_verified_flag_without_a_valid_email_is_not_verified(
    monkeypatch: pytest.MonkeyPatch, email: object,
) -> None:
    fake_google(monkeypatch, {"sub": "stable-id", "email": email, "email_verified": True})
    result = GoogleIdentityOAuth().exchange_code("fake-code", "https://app.test/callback")
    assert result.email is None and result.email_verified is False


def test_google_returns_a_verified_email(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_google(monkeypatch, {
        "sub": "stable-id", "email": "verified@example.test", "email_verified": True,
    })
    result = GoogleIdentityOAuth().exchange_code("fake-code", "https://app.test/callback")
    assert result.email == "verified@example.test" and result.email_verified is True


def fake_github(
    monkeypatch: pytest.MonkeyPatch, emails: object, email_status: int = 200,
) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(requests, "post", lambda *a, **kw: reply({"access_token": "fake-token"}))

    def get(url: str, **kwargs: object) -> requests.Response:
        calls.append(url)
        if url.endswith("/user/emails"):
            return reply(emails, email_status)
        return reply({"id": 123, "login": "github-user", "email": "public@example.test"})

    monkeypatch.setattr(requests, "get", get)
    return calls


@pytest.mark.parametrize("emails", [
    [],
    [{"email": "victim@example.test", "primary": True, "verified": False}],
    [{"email": "victim@example.test", "primary": True, "verified": "true"}],
    [{"email": "victim@example.test", "primary": True, "verified": 1}],
    [{"email": "", "primary": True, "verified": True}],
    {"message": "malformed email response"},
])
def test_github_never_falls_back_to_public_or_unverified_email(
    monkeypatch: pytest.MonkeyPatch, emails: object,
) -> None:
    calls = fake_github(monkeypatch, emails)
    result = GitHubIdentityOAuth().exchange_code("fake-code", "https://app.test/callback")
    assert calls[-1].endswith("/user/emails")
    assert result.provider_user_id == "123"
    assert result.email is None and result.email_verified is False


def test_github_prefers_the_verified_primary_email(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_github(monkeypatch, [
        {"email": "secondary@example.test", "verified": True, "primary": False},
        {"email": "primary@example.test", "verified": True, "primary": True},
    ])
    result = GitHubIdentityOAuth().exchange_code("fake-code", "https://app.test/callback")
    assert result.email == "primary@example.test" and result.email_verified is True


def test_github_can_use_a_verified_secondary_email(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_github(monkeypatch, [
        {"email": "unverified@example.test", "verified": False, "primary": True},
        {"email": "verified@example.test", "verified": True, "primary": False},
    ])
    result = GitHubIdentityOAuth().exchange_code("fake-code", "https://app.test/callback")
    assert result.email == "verified@example.test" and result.email_verified is True


def test_github_email_lookup_failure_preserves_identity_without_trusting_public_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_github(monkeypatch, {"message": "denied"}, email_status=403)
    result = GitHubIdentityOAuth().exchange_code("fake-code", "https://app.test/callback")
    assert result.provider_user_id == "123"
    assert result.email is None and result.email_verified is False


def test_facebook_email_is_not_a_verification_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([
        reply({"access_token": "fake-token"}),
        reply({"id": "facebook-id", "email": "contact@example.test", "name": "User"}),
    ])
    monkeypatch.setattr(requests, "get", lambda *a, **kw: next(responses))
    result = FacebookIdentityOAuth().exchange_code("fake-code", "https://app.test/callback")
    assert result.provider_user_id == "facebook-id"
    assert result.email_verified is False


@pytest.mark.parametrize("provider", [GoogleIdentityOAuth, GitHubIdentityOAuth, FacebookIdentityOAuth])
@pytest.mark.parametrize("stage", ["token", "profile"])
def test_provider_http_errors_are_safe_to_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    provider: type[GoogleIdentityOAuth | GitHubIdentityOAuth | FacebookIdentityOAuth], stage: str,
) -> None:
    client = provider()

    def respond(url: str, **kwargs: object) -> requests.Response:
        if url == client.TOKEN_URL:
            return reply({"access_token": "fake-token"}, 400 if stage == "token" else 200)
        return reply({}, 400)

    monkeypatch.setattr(requests, "post", respond)
    monkeypatch.setattr(requests, "get", respond)
    code = "CODE_SENTINEL"
    with caplog.at_level(logging.ERROR), pytest.raises(IdentityOAuthError) as caught:
        try:
            client.exchange_code(code, "https://app.test/callback")
        except IdentityOAuthError:
            logging.getLogger(__name__).exception("Identity callback failed")
            raise
    rendered = caplog.text + "".join(traceback.format_exception(caught.value))
    assert caught.value.__suppress_context__ is True
    for secret in ("SECRET_SENTINEL", "CODE_SENTINEL", "TOKEN_SENTINEL", "provider.invalid"):
        assert secret not in rendered


@pytest.mark.parametrize("failure", [
    requests.ConnectionError("SECRET_SENTINEL"),
    requests.Timeout("SECRET_SENTINEL"),
    requests.exceptions.JSONDecodeError("SECRET_SENTINEL", "TOKEN_SENTINEL", 0),
    ValueError("SECRET_SENTINEL"),
])
def test_transport_and_parse_errors_do_not_expose_provider_contents(
    monkeypatch: pytest.MonkeyPatch, failure: Exception,
) -> None:
    def fail(*args: object, **kwargs: object) -> requests.Response:
        raise failure

    monkeypatch.setattr(requests, "post", fail)
    with pytest.raises(IdentityOAuthError) as caught:
        GoogleIdentityOAuth().exchange_code("fake-code", "https://app.test/callback")
    rendered = "".join(traceback.format_exception(caught.value))
    assert "SECRET_SENTINEL" not in rendered and "TOKEN_SENTINEL" not in rendered
