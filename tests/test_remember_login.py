from http.cookies import SimpleCookie

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from universal_video_ai.web.auth import (COOKIE_NAME, SESSION_MAX_AGE_SECONDS, create_session_cookie_value,
    get_current_user_id, read_session_cookie_value, remember_existing_session, set_session_cookie)


def test_login_cookie_survives_browser_close_for_the_signed_session_lifetime(monkeypatch):
    monkeypatch.setenv('WEB_SESSION_SECRET', 'remember-login-regression-test')
    response = Response()
    set_session_cookie(response, 42)
    cookie = SimpleCookie(response.headers['set-cookie'])[COOKIE_NAME]
    assert cookie['max-age'] == str(SESSION_MAX_AGE_SECONDS)
    assert cookie['httponly']
    assert cookie['samesite'] == 'lax'
    assert read_session_cookie_value(cookie.value) == 42


def test_existing_valid_session_becomes_persistent_without_resigning_or_changing_owner(monkeypatch):
    monkeypatch.setenv('WEB_SESSION_SECRET', 'remember-login-regression-test')
    original = create_session_cookie_value(7)
    def app():
        instance = FastAPI()
        @instance.get('/me')
        def me(request: Request, response: Response):
            owner = get_current_user_id(request)
            remember_existing_session(request, response)
            return {'id': owner}
        return instance
    # Different app instances represent a server restart; same configured secret.
    for instance in (app(), app()):
        client = TestClient(instance)
        client.cookies.set(COOKIE_NAME, original)
        result = client.get('/me')
        assert result.json() == {'id': 7}
        cookie = SimpleCookie(result.headers['set-cookie'])[COOKIE_NAME]
        assert cookie.value == original
        assert 0 < int(cookie['max-age']) <= SESSION_MAX_AGE_SECONDS
    bad = TestClient(app())
    bad.cookies.set(COOKIE_NAME, original + 'tampered')
    assert bad.get('/me').status_code == 401


def test_full_web_login_response_remembers_user_and_logout_removes_cookie(monkeypatch, tmp_path):
    from importlib import import_module
    from universal_video_ai.web.store import Store
    app = import_module('universal_video_ai.web.app')
    monkeypatch.setenv('WEB_SESSION_SECRET', 'remember-login-regression-test')
    store = Store(tmp_path / 'remember-web.sqlite')
    owner = store.create_user('remember-owner', 'hash')
    monkeypatch.setattr(app, 'store', store)
    result = app._login_response(owner)
    cookie = SimpleCookie(result.headers['set-cookie'])[COOKIE_NAME]
    assert cookie['max-age'] == str(SESSION_MAX_AGE_SECONDS)
    client = TestClient(app.app)
    client.cookies.set(COOKIE_NAME, cookie.value)
    me = client.get('/api/me')
    assert me.status_code == 200 and me.json()['id'] == owner
    assert int(SimpleCookie(me.headers['set-cookie'])[COOKIE_NAME]['max-age']) > 0
    assert 'Max-Age=0' in client.post('/api/logout').headers['set-cookie']


def test_persisting_an_existing_cookie_does_not_extend_its_original_30_day_expiration(monkeypatch):
    import time
    from itsdangerous.timed import TimestampSigner
    monkeypatch.setenv('WEB_SESSION_SECRET', 'remember-login-regression-test')
    with monkeypatch.context() as earlier:
        earlier.setattr(TimestampSigner, 'get_timestamp', lambda self: int(time.time()) - 86400)
        original = create_session_cookie_value(7)
    request = Request({'type': 'http', 'headers': [(b'cookie', f'{COOKIE_NAME}={original}'.encode())]})
    response = Response()
    remember_existing_session(request, response)
    cookie = SimpleCookie(response.headers['set-cookie'])[COOKIE_NAME]
    assert cookie.value == original
    assert SESSION_MAX_AGE_SECONDS - 86460 <= int(cookie['max-age']) <= SESSION_MAX_AGE_SECONDS - 86400
