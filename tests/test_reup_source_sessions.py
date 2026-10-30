"""Source recovery uses private login state and bounded, independent retries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.cookiejar import MozillaCookieJar
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from universal_video_ai.downloader.channel import ChannelListingService, _cookiefile_for_platform
from universal_video_ai.downloader.platform import Platform
from universal_video_ai.downloader.ytdlp_downloader import _cookiefile_for
from universal_video_ai.reup.discovery import Candidate, DiscoveryUnavailable, SourceDiscovery, discovery_issue
from universal_video_ai.reup.source_sessions import SourceSessions, source_cookie_file


def state(value="private"):
    return {"cookies": [{"name": "sessionid", "value": value, "domain": ".douyin.com", "path": "/",
                         "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"}],
            "origins": [{"origin": "https://www.douyin.com", "localStorage": [{"name": "login", "value": value}]}]}


def test_session_is_private_owner_scoped_and_survives_restart(tmp_path):
    sessions = SourceSessions(tmp_path)
    payload = state()
    payload["cookies"].append({"name": "other", "value": "unrelated", "domain": ".example.com"})
    payload["origins"].append({"origin": "https://douyin.com.evil.test", "localStorage": []})
    sessions.save(1, "douyin", payload)
    saved = sessions.state_path(1, "douyin")
    assert saved.stat().st_mode & 0o777 == 0o600
    assert saved.parent.stat().st_mode & 0o777 == 0o700
    assert len(json.loads(saved.read_text())["cookies"]) == 1
    assert len(json.loads(saved.read_text())["origins"]) == 1
    restarted = SourceSessions(tmp_path)
    assert restarted.status(1, "douyin")["saved"] is True
    assert restarted.status(2, "douyin")["saved"] is False
    assert restarted.context_options(1, "douyin")["storage_state"] == str(saved)
    assert "storage_state" not in restarted.context_options(2, "douyin")
    jar = MozillaCookieJar(str(saved.parent / "cookies.txt"))
    jar.load(ignore_discard=True, ignore_expires=True)
    assert [(cookie.name, cookie.value) for cookie in jar] == [("sessionid", "private")]


def test_download_credentials_follow_task_scope_without_changing_environment(tmp_path, monkeypatch):
    sessions = SourceSessions(tmp_path)
    for owner in (1, 2):
        sessions.save(owner, "douyin", state(str(owner)))
    monkeypatch.setenv("DOUYIN_COOKIES_FILE", "operator-config")
    def run(owner):
        with sessions.download_scope(owner):
            file = source_cookie_file("douyin")
            assert _cookiefile_for(Platform.DOUYIN) == file
            assert _cookiefile_for_platform(Platform.DOUYIN) == file
            return file
    with ThreadPoolExecutor(2) as pool:
        first, second = list(pool.map(run, [1, 2]))
    assert first != second and "/1/" in first and "/2/" in second
    assert source_cookie_file("douyin") is None


def test_in_flight_scrape_cannot_overwrite_a_new_login_and_unchanged_state_keeps_cache(tmp_path):
    sessions = SourceSessions(tmp_path)
    sessions.save(1, "douyin", state("old"))
    old = sessions.signature(1, "douyin")
    sessions.save(1, "douyin", state("new"))
    fresh = sessions.signature(1, "douyin")
    sessions.save(1, "douyin", state("old"), expected_signature=old)
    assert json.loads(sessions.state_path(1, "douyin").read_text())["cookies"][0]["value"] == "new"
    sessions.save(1, "douyin", state("new"), expected_signature=fresh)
    assert sessions.signature(1, "douyin") == fresh


def test_challenge_pauses_all_queries_and_market_only_for_that_owner(tmp_path, monkeypatch):
    sessions = SourceSessions(tmp_path)
    discovery = SourceDiscovery(sessions=sessions)
    calls = []
    def blocked(*args):
        calls.append(args)
        raise ValueError("requires verification")
    monkeypatch.setattr(discovery, "_public_browse", blocked)
    with discovery.user_scope(1):
        for operation in (lambda: discovery.search("douyin", "舞蹈"), lambda: discovery.market("douyin"),
                          lambda: discovery.search("douyin", "穿搭")):
            with pytest.raises(DiscoveryUnavailable) as exc:
                operation()
            assert exc.value.issue["status"] == "verification_required"
            assert 0 < exc.value.issue["retry_after_seconds"] <= 600
    assert len(calls) == 1
    with discovery.user_scope(2), pytest.raises(DiscoveryUnavailable):
        discovery.market("douyin")
    assert len(calls) == 2
    # Saving a fresh session automatically invalidates the pause.
    sessions.save(1, "douyin", state())
    monkeypatch.setattr(discovery, "_public_browse", lambda *args: [])
    with discovery.user_scope(1):
        assert discovery.search("douyin", "舞蹈") == []
    with discovery.user_scope(2), pytest.raises(DiscoveryUnavailable):
        discovery.search("douyin", "舞蹈")


def test_cooldown_expires_and_explicit_retry_does_not_clear_other_owners(monkeypatch):
    discovery = SourceDiscovery()
    clock = [1000.0]
    monkeypatch.setattr("universal_video_ai.reup.discovery.time.monotonic", lambda: clock[0])
    calls = []
    def blocked(*args):
        calls.append(args)
        raise ValueError("requires verification")
    monkeypatch.setattr(discovery, "_public_browse", blocked)
    with discovery.user_scope(1):
        with pytest.raises(DiscoveryUnavailable):
            discovery.market("douyin")
        discovery.reset(2, "douyin")
        with pytest.raises(DiscoveryUnavailable):
            discovery.market("douyin")
        assert len(calls) == 1
        clock[0] += 601
        with pytest.raises(DiscoveryUnavailable):
            discovery.market("douyin")
        assert len(calls) == 2
        discovery.reset(1, "douyin")
        with pytest.raises(DiscoveryUnavailable):
            discovery.market("douyin")
        assert len(calls) == 3


def test_failed_market_endpoint_does_not_prevent_a_working_search(monkeypatch):
    discovery = SourceDiscovery()
    calls = []
    def browse(platform, url, limit):
        calls.append(url)
        if "/explore" in url:
            raise ValueError("No visible tiktok results; login, layout or restrictions")
        return [Candidate("https://www.tiktok.com/@a/video/123", "tiktok", "123", "dance fashion")]
    monkeypatch.setattr(discovery, "_public_browse", browse)
    with pytest.raises(DiscoveryUnavailable):
        discovery.market("tiktok")
    assert discovery.search("tiktok", "dance fashion")[0].video_id == "123"
    assert len(calls) == 2


def test_parallel_same_query_is_fetched_once_and_results_are_copied(monkeypatch):
    discovery = SourceDiscovery()
    calls = []
    candidate = Candidate("https://www.douyin.com/video/123", "douyin", "123", "舞蹈")
    monkeypatch.setattr(discovery, "_public_browse", lambda *args: calls.append(args) or [candidate])
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: discovery.search("douyin", "舞蹈"), range(4)))
    assert len(calls) == 1
    results[0].clear()
    assert discovery.search("douyin", "舞蹈") == [candidate]


def test_hydration_supports_bilibili_assignment_and_tiktok_search_only():
    entry = {"bvid": "BV1test123", "title": "舞蹈", "author": "creator", "play": 30}
    raw = "window.__INITIAL_STATE__ = " + json.dumps({"videoData": [entry]}) + ";(function(){throw 1})()"
    assert SourceDiscovery.embedded_metadata("bilibili", raw, searching=True)[0].view_count == 30
    item = {"id": "123", "desc": "dance", "author": {"uniqueId": "creator"}, "statsV2": {"playCount": "1234"}}
    payload = {"__DEFAULT_SCOPE__": {"webapp.search": {"itemList": [item]}, "webapp.recommend": {"itemList": [{**item, "id": "999"}]}}}
    found = SourceDiscovery.embedded_metadata("tiktok", json.dumps(payload), searching=True)
    assert [row.video_id for row in found] == ["123"]
    assert found[0].view_count == 1234
    assert SourceDiscovery.embedded_metadata("bilibili", "window.__INITIAL_STATE__ = evil()", searching=True) == []


@pytest.mark.parametrize("detail,expected", [("HTTP 504; login required", "upstream_error"), ("source browser busy", "session_open"), ("HTTP 429; login required", "rate_limited"), ("requires verification", "verification_required"),
    ("HTTP 412", "http_412"), ("No visible tiktok results; login", "no_results"), ("redirected away", "login_required")])
def test_error_classification_does_not_claim_a_login_or_success_without_evidence(detail, expected):
    assert discovery_issue(ValueError(detail))["status"] == expected


@pytest.fixture
def login_browser(tmp_path, monkeypatch):
    from playwright.sync_api import Error as BrowserError
    sessions = SourceSessions(tmp_path)
    monkeypatch.setenv("DISPLAY", ":test")
    opened, closed, dismiss, allow_navigation = (threading.Event() for _ in range(4))
    allow_navigation.set()
    handlers = {}
    context_handlers = {}
    response_status = [200]
    reloads = []
    page = SimpleNamespace()
    def full_export():
        pytest.fail("Interactive login must not export storage state or create helper tabs")
    context = SimpleNamespace(pages=[page], on=lambda name, fn: context_handlers.setdefault(name, fn),
                              cookies=lambda: state()["cookies"], storage_state=full_export)
    def navigate(*args, **kwargs):
        opened.set()
        allow_navigation.wait(2)
        if 'response' in context_handlers:
            context_handlers['response'](SimpleNamespace(url='https://www.douyin.com/', status=response_status[0], request=SimpleNamespace(resource_type='document')))
        if 'domcontentloaded' in handlers:
            handlers['domcontentloaded']()
    def tick(_):
        if dismiss.wait(.005):
            if 'close' in handlers: handlers['close']()
            context.pages = []
            raise BrowserError('closed')
    page.on = lambda name, fn: handlers.setdefault(name, fn)
    page.goto = navigate
    page.reload = lambda **kwargs: reloads.append(True) or navigate()
    page.is_closed = lambda: not context.pages
    page.wait_for_timeout = tick
    page.bring_to_front = lambda: None
    @contextmanager
    def browser_context(*args, **kwargs):
        try:
            yield context
        finally:
            closed.set()
    monkeypatch.setattr(sessions, 'browser_context', browser_context)
    return SimpleNamespace(sessions=sessions, opened=opened, closed=closed, dismiss=dismiss,
                           allow_navigation=allow_navigation, response_status=response_status, reloads=reloads)


def wait_for(condition, timeout=2):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition(): return
        threading.Event().wait(.01)
    assert condition()


def test_login_autosaves_before_the_user_closes_the_window_and_survives_restart(login_browser, tmp_path):
    browser = login_browser
    sessions = browser.sessions
    sessions.start(1, 'douyin')
    assert browser.opened.wait(2)
    wait_for(lambda: sessions.status(1, 'douyin')['saved'])
    assert sessions.status(1, 'douyin')['authenticated_hint']
    assert sessions.status(1, 'douyin')['browser_open']
    browser.dismiss.set()  # Closing the window needs no manual Save action.
    assert browser.closed.wait(2)
    sessions._logins[(1, 'douyin')]['thread'].join(2)
    status = SourceSessions(tmp_path).status(1, 'douyin')
    assert status['saved'] and status['authenticated_hint'] and not status['browser_open']
    assert not sessions.status(2, 'douyin')['saved']


def test_slow_source_navigation_does_not_block_status_or_open_a_duplicate_window(login_browser):
    import time
    browser = login_browser
    browser.allow_navigation.clear()
    browser.sessions.start(1, 'douyin')
    assert browser.opened.wait(2)
    started = time.monotonic()
    assert browser.sessions.status(1, 'douyin')['browser_open']
    assert time.monotonic() - started < .5
    original = browser.sessions._logins[(1, 'douyin')]['thread']
    browser.sessions.start(1, 'douyin')
    assert browser.sessions._logins[(1, 'douyin')]['thread'] is original
    browser.allow_navigation.set()
    browser.sessions.finish(1, 'douyin')
    assert browser.closed.wait(2)
    original.join(2)


def test_source_504_keeps_window_and_session_and_can_reload_without_new_login(login_browser):
    browser = login_browser
    browser.response_status[0] = 504
    browser.sessions.save(1, 'douyin', state())
    browser.sessions.start(1, 'douyin')
    wait_for(lambda: '504' in browser.sessions.status(1, 'douyin')['message'])
    status = browser.sessions.status(1, 'douyin')
    assert status['saved'] and status['browser_open']
    browser.response_status[0] = 200
    browser.sessions.control(1, 'douyin', 'reload')
    wait_for(lambda: bool(browser.reloads))
    browser.sessions.finish(1, 'douyin')
    assert browser.closed.wait(2)
    browser.sessions._logins[(1, 'douyin')]['thread'].join(2)
    assert browser.sessions.status(1, 'douyin')['saved']


def test_session_http_routes_are_authenticated_owner_scoped_and_local_for_windows(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from universal_video_ai.web.auth import get_current_user_id
    from universal_video_ai.web.reup_router import install_reup_routes
    from universal_video_ai.web.store import Store
    app = FastAPI()
    store = Store(tmp_path / "web.sqlite")
    owner = store.create_user("owner", "hash")
    other = store.create_user("other", "hash")
    runtime = install_reup_routes(app, store, lambda *args: None, tmp_path)
    app.dependency_overrides[get_current_user_id] = lambda: owner
    runtime.source_sessions.save(owner, "douyin", state())
    client = TestClient(app, client=("127.0.0.1", 1234))
    response = client.get("/api/reup-agent/source-sessions")
    assert response.status_code == 200 and response.json()["sources"][0]["saved"]
    assert "private" not in response.text and "state.json" not in response.text
    app.dependency_overrides[get_current_user_id] = lambda: other
    assert not client.get("/api/reup-agent/source-sessions").json()["sources"][0]["saved"]
    monkeypatch.setattr(runtime.source_sessions, "start", lambda *a: {"status": "opening"})
    assert client.post("/api/reup-agent/source-sessions/douyin/login").status_code == 200
    remote = TestClient(app, client=("192.0.2.1", 1234))
    assert remote.post("/api/reup-agent/source-sessions/douyin/login").status_code == 400
    assert client.post("/api/reup-agent/source-sessions/invalid/login").status_code == 422
    assert client.post("/api/reup-agent/source-sessions/douyin/save").status_code == 400
    app.dependency_overrides.clear()
    assert client.get("/api/reup-agent/source-sessions").status_code == 401


def test_parallel_platforms_inherit_account_scope_and_keep_independent_progress(tmp_path, monkeypatch):
    sessions = SourceSessions(tmp_path)
    discovery = SourceDiscovery(sessions=sessions)
    barrier = threading.Barrier(2)
    def search(platform, query):
        assert discovery._owner.get() == 7
        barrier.wait(timeout=2)
        url = 'https://www.douyin.com/video/123' if platform == 'douyin' else 'https://www.tiktok.com/@creator/video/123'
        return [Candidate(url, platform, '123', 'dance')]
    monkeypatch.setattr(discovery, 'search', search)
    with discovery.user_scope(7):
        result = discovery.scan({'platforms': ['douyin', 'tiktok'], 'topic': 'dance'})
    assert {row['platform'] for row in result['candidates']} == {'douyin', 'tiktok'}
    assert all(row['status'] == 'searched' for row in result['platform_status'])
    assert discovery._owner.get() == 0


def test_web_job_downloads_inherit_the_owners_saved_credentials(tmp_path, monkeypatch):
    import asyncio
    from importlib import import_module
    from universal_video_ai.web.store import Store
    app = import_module('universal_video_ai.web.app')
    store = Store(tmp_path / 'jobs.sqlite')
    sessions = SourceSessions(tmp_path / 'sessions')
    owners = [store.create_user(name, 'hash') for name in ('first', 'second')]
    jobs = []
    for owner in owners:
        sessions.save(owner, 'douyin', state(str(owner)))
        jobs.append(store.create_job(owner, 'https://www.douyin.com/video/123', 'vi'))
    monkeypatch.setattr(app, 'store', store)
    monkeypatch.setattr(app, 'reup_runtime', SimpleNamespace(source_sessions=sessions))
    async def slot():
        pass
    observed = {}
    async def run(job_id):
        # asyncio.to_thread mirrors the actual downloader's execution path.
        file = await asyncio.to_thread(source_cookie_file, 'douyin')
        jar = MozillaCookieJar(file)
        jar.load(ignore_discard=True, ignore_expires=True)
        observed[job_id] = next(iter(jar)).value
    monkeypatch.setattr(app, '_acquire_job_run_slot', slot)
    monkeypatch.setattr(app, '_release_job_run_slot', slot)
    monkeypatch.setattr(app, '_run_job_unlocked', run)
    async def scenario():
        await asyncio.gather(*(app._run_job(job.id) for job in jobs))
    asyncio.run(scenario())
    assert observed == {job.id: str(job.user_id) for job in jobs}
    assert source_cookie_file('douyin') is None


def test_access_check_results_are_scoped_and_stale_checks_do_not_replace_fresh_login(tmp_path):
    sessions = SourceSessions(tmp_path)
    sessions.save(1, 'douyin', state('old'))
    old = sessions.signature(1, 'douyin')
    sessions.record_access(1, 'douyin', {'status': 'searched', 'message': 'found'}, count=3, signature=old)
    assert sessions.status(1, 'douyin')['access']['count'] == 3
    assert not sessions.status(2, 'douyin')['access']
    sessions.save(1, 'douyin', state('new'))
    sessions.record_access(1, 'douyin', {'status': 'login_required'}, signature=old)
    assert not sessions.status(1, 'douyin')['access']


def test_metadata_check_returns_immediately_deduplicates_and_never_enqueues_jobs(tmp_path, monkeypatch):
    import asyncio
    from universal_video_ai.web.reup_router import ReupRuntime
    from universal_video_ai.web.store import Store
    store = Store(tmp_path / 'check.sqlite')
    owner = store.create_user('owner', 'hash')
    async def submit(*args):
        pytest.fail('A connection check must not process videos')
    runtime = ReupRuntime(store, submit, tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def search(platform, query, limit):
        assert runtime.discovery._owner.get() == owner
        calls.append((platform, query))
        entered.set()
        assert release.wait(2)
        return [Candidate('https://www.douyin.com/video/123', 'douyin', '123', 'dance')]
    monkeypatch.setattr(runtime.discovery, 'search', search)
    async def scenario():
        assert runtime.start_source_check(owner, 'douyin')['checking']
        assert runtime.start_source_check(owner, 'douyin')['checking']
        # The event loop remains responsive while the source is slow.
        assert await asyncio.to_thread(entered.wait, 2)
        assert runtime.source_status(owner, 'douyin')['checking']
        release.set()
        await runtime.source_check_tasks[(owner, 'douyin')]
        assert runtime.source_status(owner, 'douyin')['access']['status'] == 'searched'
        assert not runtime.source_status(owner, 'douyin')['checking']
        assert runtime.repository.list(owner) == []
    asyncio.run(scenario())
    assert len(calls) == 1


def test_bilibili_browser_504_is_not_overwritten_by_index_412(monkeypatch):
    discovery = SourceDiscovery()
    monkeypatch.setattr(discovery, '_indexed_search', lambda *a: (_ for _ in ()).throw(ValueError('HTTP 412')))
    monkeypatch.setattr(discovery, '_public_browse', lambda *a: (_ for _ in ()).throw(ValueError('HTTP 504')))
    with pytest.raises(DiscoveryUnavailable) as error:
        discovery.search('bilibili', '舞蹈')
    assert error.value.issue['status'] == 'upstream_error'


def test_check_endpoint_updates_status_without_starting_processing_or_exposing_credentials(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from universal_video_ai.web.auth import get_current_user_id
    from universal_video_ai.web.reup_router import install_reup_routes
    from universal_video_ai.web.store import Store
    app = FastAPI()
    store = Store(tmp_path / 'check-http.sqlite')
    owner = store.create_user('owner', 'hash')
    async def submit(*args):
        pytest.fail('Checking a source must not submit a localization job')
    runtime = install_reup_routes(app, store, submit, tmp_path)
    runtime.source_sessions.save(owner, 'douyin', state('private-token'))
    app.dependency_overrides[get_current_user_id] = lambda: owner
    monkeypatch.setattr(runtime.discovery, 'search', lambda *a, **kw: [Candidate('https://www.douyin.com/video/123', 'douyin', '123', 'dance')])
    with TestClient(app, client=('127.0.0.1', 1234)) as client:
        started = client.post('/api/reup-agent/source-sessions/douyin/check')
        assert started.status_code == 200
        def checked():
            return client.get('/api/reup-agent/source-sessions').json()['sources'][0].get('access', {}).get('status') == 'searched'
        wait_for(checked)
        response = client.get('/api/reup-agent/source-sessions')
        assert response.json()['sources'][0]['saved']
        assert 'private-token' not in response.text
        assert runtime.repository.list(owner) == []


def test_switching_source_commits_new_login_only_after_success_and_survives_restart(tmp_path, monkeypatch):
    from universal_video_ai.reup.source_sessions import source_cookie_file
    sessions = SourceSessions(tmp_path)
    sessions.save(1, 'douyin', state('account-a'))
    sessions.save(2, 'douyin', state('other-owner'))
    old_directory = sessions.directory(1, 'douyin')
    monkeypatch.setenv('DISPLAY', ':test')
    def new_login(owner, platform, login):
        assert sessions.directory(owner, platform) != old_directory
        assert not sessions.state_path(owner, platform).exists()  # Fresh login, no account-A cookies.
        sessions.save(owner, platform, state('account-b'))
    monkeypatch.setattr(sessions, '_login_window', new_login)
    sessions.start(1, 'douyin', switch=True)
    sessions._logins[(1, 'douyin')]['thread'].join(2)
    restarted = SourceSessions(tmp_path)
    assert restarted.directory(1, 'douyin') != old_directory
    assert json.loads(restarted.state_path(1, 'douyin').read_text())['cookies'][0]['value'] == 'account-b'
    assert json.loads(restarted.state_path(2, 'douyin').read_text())['cookies'][0]['value'] == 'other-owner'
    assert json.loads((old_directory / 'state.json').read_text())['cookies'][0]['value'] == 'account-a'
    with restarted.download_scope(1):
        assert source_cookie_file('douyin') == str(restarted.directory(1, 'douyin') / 'cookies.txt')


def test_abandoned_source_switch_keeps_existing_login(tmp_path, monkeypatch):
    sessions = SourceSessions(tmp_path)
    sessions.save(1, 'douyin', state('account-a'))
    original = sessions.state_path(1, 'douyin')
    monkeypatch.setenv('DISPLAY', ':test')
    monkeypatch.setattr(sessions, '_login_window', lambda owner, platform, login: sessions.save(owner, platform, {'cookies': [], 'origins': []}))
    sessions.start(1, 'douyin', switch=True)
    sessions._logins[(1, 'douyin')]['thread'].join(2)
    assert sessions.state_path(1, 'douyin') == original
    assert json.loads(original.read_text())['cookies'][0]['value'] == 'account-a'
    assert 'giữ phiên' in sessions.status(1, 'douyin')['message']


def test_switch_during_metadata_search_cannot_cache_old_account_results_under_new_account(tmp_path, monkeypatch):
    sessions = SourceSessions(tmp_path)
    discovery = SourceDiscovery(sessions=sessions)
    sessions.save(1, 'douyin', state('account-a'))
    slot = 'b' * 32
    with sessions._slot_scope(1, 'douyin', slot):
        sessions.save(1, 'douyin', state('account-b'))
    def changed(*args):
        sessions._activate(1, 'douyin', slot)
        return [Candidate('https://www.douyin.com/video/123', 'douyin', '123', 'old account')]
    monkeypatch.setattr(discovery, '_public_browse', changed)
    with discovery.user_scope(1):
        with pytest.raises(DiscoveryUnavailable) as error:
            discovery.search('douyin', 'dance')
        assert error.value.issue['status'] == 'session_changed'
        expected = [Candidate('https://www.douyin.com/video/456', 'douyin', '456', 'new account')]
        monkeypatch.setattr(discovery, '_public_browse', lambda *args: expected)
        assert discovery.search('douyin', 'dance') == expected


def test_indexed_cookie_rewrite_does_not_bypass_the_failure_cooldown(tmp_path, monkeypatch):
    cookies = tmp_path / 'cookies.txt'
    cookies.write_text('before')
    monkeypatch.setenv('BILIBILI_COOKIES_FILE', str(cookies))
    discovery = SourceDiscovery()
    calls = []
    def indexed(*args):
        calls.append(True)
        cookies.write_text('rewritten by extractor')
        raise ValueError('HTTP 412')
    monkeypatch.setattr(discovery, '_indexed_search', indexed)
    monkeypatch.setattr(discovery, '_public_browse', lambda *a: (_ for _ in ()).throw(ValueError('HTTP 412')))
    for _ in range(2):
        with pytest.raises(DiscoveryUnavailable): discovery.search('bilibili', '舞蹈')
    assert len(calls) == 1


def test_browser_session_search_skips_the_bilibili_index_that_returns_412(tmp_path, monkeypatch):
    discovery = SourceDiscovery(sessions=SourceSessions(tmp_path))
    expected = [Candidate('https://www.bilibili.com/video/BV1test123?p=1', 'bilibili', 'BV1test123:p1', '舞蹈')]
    monkeypatch.setattr(discovery, '_indexed_search', lambda *a: pytest.fail('Saved browser search must not first request the blocked index'))
    monkeypatch.setattr(discovery, '_public_browse', lambda *a: expected)
    with discovery.user_scope(1):
        assert discovery.search('bilibili', '舞蹈') == expected


def test_interactive_search_snapshot_is_bound_to_user_query_account_and_ttl(tmp_path):
    import time
    sessions = SourceSessions(tmp_path)
    rows = [Candidate('https://www.douyin.com/video/123', 'douyin', '123', '舞蹈').to_dict()]
    sessions.save_search(1, 'douyin', '舞蹈', rows)
    restarted = SourceSessions(tmp_path)
    assert restarted.read_search(1, 'douyin', '舞蹈')
    assert not restarted.read_search(2, 'douyin', '舞蹈')
    assert not restarted.read_search(1, 'douyin', '穿搭')
    file = restarted._search_file(1, 'douyin', '舞蹈')
    data = json.loads(file.read_text()); data['collected_at'] = time.time() - 301; file.write_text(json.dumps(data))
    assert not restarted.read_search(1, 'douyin', '舞蹈')
    sessions.save_search(1, 'douyin', '舞蹈', rows)
    sessions._activate(1, 'douyin', 'c' * 32)
    assert not sessions.read_search(1, 'douyin', '舞蹈')


def test_verification_endpoint_uses_the_owners_failed_search_query(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from universal_video_ai.web.auth import get_current_user_id
    from universal_video_ai.web.reup_router import install_reup_routes
    from universal_video_ai.web.store import Store
    store = Store(tmp_path / 'verify.sqlite')
    owner = store.create_user('owner', 'hash')
    app = FastAPI()
    runtime = install_reup_routes(app, store, lambda *a: None, tmp_path)
    runtime.repository.save_discovery(owner, {'platform_status': [{'platform': 'douyin', 'queries': ['舞蹈'], 'status': 'verification_required'}]})
    calls = []
    monkeypatch.setattr(runtime.source_sessions, 'start', lambda *a, **kw: calls.append((a, kw)) or {'status': 'waiting'})
    app.dependency_overrides[get_current_user_id] = lambda: owner
    client = TestClient(app, client=('127.0.0.1', 1234))
    assert client.post('/api/reup-agent/source-sessions/douyin/verify').status_code == 200
    assert calls == [((owner, 'douyin'), {'query': '舞蹈'})]
    remote = TestClient(app, client=('192.0.2.1', 1234))
    assert remote.post('/api/reup-agent/source-sessions/douyin/verify').status_code == 400


def test_verified_window_results_enter_the_real_reup_source_queue_without_a_second_browser(tmp_path, monkeypatch):
    import asyncio
    from universal_video_ai.web.reup_router import ReupRuntime, ReupProfileBody
    from universal_video_ai.web.store import Store
    store = Store(tmp_path / 'captured-run.sqlite')
    owner = store.create_user('owner', 'hash')
    async def submit(*args):
        pytest.fail('Manual discovery should not start localization automatically')
    runtime = ReupRuntime(store, submit, tmp_path)
    config = ReupProfileBody(topic='舞蹈', platforms=['douyin'], automation='manual').model_dump()
    runtime.repository.save_profile(owner, config)
    runtime.source_sessions.save_search(owner, 'douyin', '舞蹈', [Candidate('https://www.douyin.com/video/123456789', 'douyin', '123456789', '舞蹈教学').to_dict()])
    monkeypatch.setattr(runtime.discovery, '_public_browse', lambda *a: pytest.fail('Do not reopen headless search after the verified window'))
    result = asyncio.run(runtime.scan(owner))
    assert result['videos_found'] == 1 and result['runs_created'] == 1
    assert result['runs'][0]['status'] == 'discovered'
    assert result['runs'][0]['source_url'] == 'https://www.douyin.com/video/123456789'


def test_verified_search_does_not_immediately_open_another_query_that_triggers_captcha(tmp_path, monkeypatch):
    sessions = SourceSessions(tmp_path)
    sessions.save_search(1, 'douyin', '舞蹈', [Candidate('https://www.douyin.com/video/123', 'douyin', '123', '舞蹈教学').to_dict()])
    discovery = SourceDiscovery(sessions=sessions)
    monkeypatch.setattr(discovery, '_public_browse', lambda *a: pytest.fail('Use verified results before starting unverified secondary queries'))
    with discovery.user_scope(1):
        result = discovery.scan({'platforms': ['douyin'], 'search_queries': {'douyin': ['舞蹈', '穿搭']}})
    assert len(result['candidates']) == 1
    assert result['platform_status'][0]['status'] == 'searched'
    assert result['platform_status'][0]['queries'] == ['舞蹈']
    assert result['platform_status'][0]['basis'] == 'interactive_search'


def test_playback_is_private_expires_and_changes_with_source_account(tmp_path, monkeypatch):
    import time
    sessions = SourceSessions(tmp_path)
    payload = {'aweme_id': '123', 'desc': '舞蹈', 'video': {'play_addr': {'url_list': ['https://v3.douyinvod.com/123.mp4']}}}
    assert sessions.save_media(1, payload) == 1
    original = sessions.directory(1, 'douyin')
    file = original / 'media-123.json'
    assert file.stat().st_mode & 0o777 == 0o600
    assert sessions.read_media(1, '123') and not sessions.read_media(2, '123')
    assert not sessions.read_media(1, '../123')
    future = time.time() + 601
    monkeypatch.setattr('universal_video_ai.reup.source_sessions.time.time', lambda: future)
    assert not sessions.read_media(1, '123')
    monkeypatch.undo()
    sessions._activate(1, 'douyin', 'd' * 32)
    assert not sessions.read_media(1, '123')
    assert sessions.save_media(1, payload, directory=original) == 0


def test_download_context_inherits_owner_and_resets_after_thread(tmp_path):
    import asyncio
    from universal_video_ai.reup.source_sessions import source_download_context
    sessions = SourceSessions(tmp_path)
    async def run():
        with sessions.download_scope(7):
            context = await asyncio.to_thread(source_download_context)
            assert context[0] is sessions and context[1] == 7
            assert context[2]['douyin'] == sessions.directory(7, 'douyin')
        assert source_download_context() is None
    asyncio.run(run())


def test_successful_source_check_reuses_its_actual_candidates_in_the_next_scan(tmp_path, monkeypatch):
    import asyncio
    from universal_video_ai.web.reup_router import ReupRuntime
    from universal_video_ai.web.store import Store
    store = Store(tmp_path / 'source-check.sqlite')
    owner = store.create_user('owner', 'hash')
    runtime = ReupRuntime(store, lambda *a: None, tmp_path)
    monkeypatch.setattr(runtime, 'source_query', lambda *a: '科学')
    rows = [Candidate('https://www.douyin.com/video/123456789', 'douyin', '123456789', '科学实验')]
    monkeypatch.setattr(runtime.discovery, '_public_browse', lambda *a: rows)
    asyncio.run(runtime._check_source(owner, 'douyin'))
    monkeypatch.setattr(runtime.discovery, '_public_browse', lambda *a: pytest.fail('Do not reopen a query that just passed its source check'))
    with runtime.discovery.user_scope(owner):
        found = runtime.discovery.scan({'platforms': ['douyin'], 'search_queries': {'douyin': ['科学', '实验']}})
    assert len(found['candidates']) == 1
    assert found['platform_status'][0]['status'] == 'searched'
    assert found['platform_status'][0]['queries'] == ['科学']
    assert not runtime.source_sessions.read_search(owner + 1, 'douyin', '科学')
