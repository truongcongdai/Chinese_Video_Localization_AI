"""Real Chromium, deterministic source pages, no external platform requests."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from universal_video_ai.downloader.channel import ChannelListingService
from universal_video_ai.reup.discovery import SourceDiscovery
from universal_video_ai.reup.source_sessions import SourceSessions


def test_douyin_search_saves_playback_without_exposing_signed_urls_in_candidates(browse, tmp_path):
    sessions = SourceSessions(tmp_path)
    payload = {'data': [{'aweme_info': {'aweme_id': '123456789', 'desc': '舞蹈教学', 'video': {
        'play_addr': {'url_list': ['https://v3.douyinvod.com/video.mp4?private_signature=fixture']}}}}]}
    def route(request):
        if '/aweme/v1/web/search/item/' in request.request.url:
            request.fulfill(json=payload)
        else:
            request.fulfill(content_type='text/html; charset=utf-8', body="<script>fetch('/aweme/v1/web/search/item/')</script>")
    browse(route)
    discovery = SourceDiscovery(sessions=sessions)
    with discovery.user_scope(1):
        rows = discovery.search('douyin', '舞蹈', limit=1)
    assert len(rows) == 1
    assert sessions.read_media(1, '123456789')
    assert 'private_signature' not in json.dumps(rows[0].to_dict())


def test_douyin_browser_403_blocks_following_downloads_without_reopening(browse, tmp_path):
    from universal_video_ai.downloader.douyin import DouyinDownloader, DouyinSourceError
    sessions = SourceSessions(tmp_path)
    requests = []
    def route(request):
        requests.append(request.request.url)
        if '/aweme/v1/web/aweme/detail/' in request.request.url:
            request.fulfill(status=403, body='Forbidden')
        else:
            request.fulfill(content_type='text/html', body="<script>fetch('/aweme/v1/web/aweme/detail/?aweme_id=123')</script>")
    browse(route)
    downloader = DouyinDownloader()
    with sessions.download_scope(1):
        with pytest.raises(DouyinSourceError, match='HTTP 403'):
            downloader.download('https://www.douyin.com/video/123', tmp_path / 'first')
        count = len(requests)
        with pytest.raises(DouyinSourceError, match='HTTP 403'):
            downloader.download('https://www.douyin.com/video/456', tmp_path / 'second')
        assert len(requests) == count


@pytest.fixture(scope="module")
def driver():
    with playwright.sync_playwright() as value:
        executable = shutil.which("google-chrome") or shutil.which("chromium")
        if not executable and Path(value.chromium.executable_path).is_file():
            executable = value.chromium.executable_path
        if not executable:
            pytest.skip("Chrome or Chromium is required")
        yield value, executable


@pytest.fixture
def browse(driver, monkeypatch):
    real, executable = driver
    def install(handler):
        def launch(**options):
            browser = real.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox"])
            def new_context(**kwargs):
                context = browser.new_context(**kwargs)
                context.route("**/*", handler)
                return context
            return SimpleNamespace(new_context=new_context, close=browser.close)
        def persistent(profile, **options):
            options.pop('headless', None)
            options.pop('timeout', None)
            context = real.chromium.launch_persistent_context(profile, executable_path=executable, headless=True, args=['--no-sandbox'], **options)
            context.route('**/*', handler)
            return context
        @contextmanager
        def wrapped():
            yield SimpleNamespace(chromium=SimpleNamespace(launch=launch, launch_persistent_context=persistent))
        monkeypatch.setattr(playwright, "sync_playwright", wrapped)
        monkeypatch.setattr(ChannelListingService, "_playwright_launch_candidates", staticmethod(lambda **kw: [("test", {})]))
    return install


def test_douyin_saved_session_is_used_for_browser_requests_and_modal_video_links(browse, tmp_path):
    sessions = SourceSessions(tmp_path)
    sessions.save(7, "douyin", {"cookies": [{"name": "source_session", "value": "test-cookie", "domain": ".douyin.com", "path": "/", "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"}], "origins": []})
    headers = []
    def route(request):
        headers.append(request.request.headers.get("cookie", ""))
        request.fulfill(content_type="text/html; charset=utf-8", body='<a href="/?modal_id=123456789" title="舞蹈教学">video</a>')
    browse(route)
    discovery = SourceDiscovery(sessions=sessions)
    with discovery.user_scope(7):
        result = discovery.search("douyin", "舞蹈", limit=1)
    assert result[0].url == "https://www.douyin.com/video/123456789"
    assert result[0].title == "舞蹈教学"
    assert any("source_session=test-cookie" in header for header in headers)


def test_delayed_tiktok_search_response_supplies_metadata_without_video_links(browse):
    item = {"id": "12345", "desc": "street dance", "author": {"uniqueId": "creator"}, "statsV2": {"playCount": "4567"}}
    def route(request):
        if "/api/search/" in request.request.url:
            request.fulfill(json={"data": [{"item": item}]})
        else:
            request.fulfill(content_type="text/html; charset=utf-8", body='<script>setTimeout(() => fetch("/api/search/item/full/"), 200)</script>')
    browse(route)
    discovery = SourceDiscovery()
    result = discovery.search("tiktok", "street dance", limit=1)
    assert result[0].url == "https://www.tiktok.com/@creator/video/12345"
    assert result[0].view_count == 4567


def test_bilibili_initial_state_is_read_when_anchor_links_are_absent(browse):
    payload = {"videoData": [{"bvid": "BV1test123", "title": "舞蹈教学", "author": "creator", "play": 42}]}
    def route(request):
        request.fulfill(content_type="text/html; charset=utf-8", body="<script>window.__INITIAL_STATE__=" + json.dumps(payload) + ";</script>")
    browse(route)
    result = SourceDiscovery()._public_browse("bilibili", "https://search.bilibili.com/all?keyword=test", 1)
    assert result[0].view_count == 42
    assert result[0].url == "https://www.bilibili.com/video/BV1test123?p=1"


def test_visible_challenge_is_reported_and_browser_is_reusable_after_failure(browse):
    def route(request):
        request.fulfill(content_type="text/html; charset=utf-8", body='<div id="captcha-verify-container">Verify</div>')
    browse(route)
    discovery = SourceDiscovery()
    with pytest.raises(ValueError, match="verification"):
        discovery._public_browse("douyin", "https://www.douyin.com/search/test", 1)
    def recovered(request):
        request.fulfill(content_type="text/html; charset=utf-8", body='<a href="/video/12345" title="舞蹈">video</a>')
    browse(recovered)
    assert discovery._public_browse("douyin", "https://www.douyin.com/search/test", 1)[0].video_id == "12345"


def test_private_profile_retains_indexeddb_state_for_later_discovery(browse, tmp_path):
    sessions = SourceSessions(tmp_path)
    def route(request):
        request.fulfill(content_type='text/html; charset=utf-8', body='''<script>
          const open = indexedDB.open('source-auth', 1);
          open.onupgradeneeded = () => open.result.createObjectStore('state');
          open.onsuccess = () => {
            const read = open.result.transaction('state').objectStore('state').get('login');
            read.onsuccess = () => { if (read.result === 'kept') {
              document.body.innerHTML = '<a href="/video/123456789" title="舞蹈">video</a>';
            } };
          };
        </script><body></body>''')
    browse(route)
    with playwright.sync_playwright() as driver:
        with sessions.browser_context(driver, 7, 'douyin', headless=True) as context:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto('https://www.douyin.com/')
            page.evaluate('''() => new Promise((resolve, reject) => {
              const request = indexedDB.open('source-auth', 1);
              request.onupgradeneeded = () => request.result.createObjectStore('state');
              request.onsuccess = () => {
                const transaction = request.result.transaction('state', 'readwrite');
                transaction.objectStore('state').put('kept', 'login');
                transaction.oncomplete = () => resolve();
                transaction.onerror = reject;
              };
            })''')
            sessions.save(7, 'douyin', context.storage_state())
    # The profile, beyond the JSON cookie/localStorage export, must be reused.
    discovery = SourceDiscovery(sessions=sessions)
    with discovery.user_scope(7):
        result = discovery.search('douyin', '舞蹈', limit=1)
    assert result[0].video_id == '123456789'


def test_504_from_search_page_is_a_temporary_source_error_not_a_login_error(browse):
    from universal_video_ai.reup.discovery import discovery_issue
    def route(request):
        request.fulfill(status=504, content_type='text/html', body='Gateway timeout')
    browse(route)
    with pytest.raises(ValueError) as error:
        SourceDiscovery().search('tiktok', 'dance', limit=1)
    assert discovery_issue(error.value)['status'] == 'upstream_error'


def test_real_login_worker_autosaves_set_cookie_before_the_user_closes_window(driver, tmp_path, monkeypatch):
    import threading
    import time
    _, executable = driver
    monkeypatch.setenv('DISPLAY', ':headless-test')
    monkeypatch.setattr(ChannelListingService, '_playwright_launch_candidates', staticmethod(lambda **kw: [('test', {'headless': True, 'executable_path': executable, 'args': ['--no-sandbox']})]))
    original = SourceSessions.browser_context
    close_window = threading.Event()
    @contextmanager
    def local_source(self, browser_driver, owner, platform, **kwargs):
        with original(self, browser_driver, owner, platform, **kwargs) as context:
            def route(request):
                if request.request.url.endswith('/logged-in'):
                    request.fulfill(status=200, headers={'Content-Type': 'application/json',
                        'Set-Cookie': 'sessionid=test-login; Domain=.douyin.com; Path=/; HttpOnly; Secure; SameSite=Lax'}, body='{}')
                else:
                    request.fulfill(content_type='text/html; charset=utf-8', body='<script>fetch("/logged-in")</script><body>Signed in</body>')
            context.route('**/*', route)
            for page in context.pages:
                wait = page.wait_for_timeout
                def tick(milliseconds, page=page, wait=wait):
                    if close_window.is_set():
                        page.close()  # User closes the source tab; no Save API.
                    else:
                        wait(min(milliseconds, 50))
                page.wait_for_timeout = tick
            yield context
    monkeypatch.setattr(SourceSessions, 'browser_context', local_source)
    sessions = SourceSessions(tmp_path)
    sessions.start(7, 'douyin')
    thread = sessions._logins[(7, 'douyin')]['thread']
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not sessions.status(7, 'douyin')['authenticated_hint']:
            threading.Event().wait(.05)
        assert sessions.status(7, 'douyin')['authenticated_hint'], sessions.status(7, 'douyin')
        assert sessions.status(7, 'douyin')['browser_open']
        close_window.set()
        thread.join(5)
        assert not thread.is_alive()
        restarted = SourceSessions(tmp_path).status(7, 'douyin')
        assert restarted['saved'] and restarted['authenticated_hint'] and not restarted['browser_open']
    finally:
        close_window.set()
        sessions.close()
        thread.join(5)


def test_busy_login_page_does_not_spawn_helper_tabs_reload_or_export_storage(driver, tmp_path, monkeypatch):
    import threading
    import time
    _, executable = driver
    monkeypatch.setenv('DISPLAY', ':headless-test')
    monkeypatch.setattr(ChannelListingService, '_playwright_launch_candidates', staticmethod(lambda **kw: [('test', {'headless': True, 'executable_path': executable, 'args': ['--no-sandbox']})]))
    original = SourceSessions.browser_context
    close_window = threading.Event()
    new_pages, main_navigations, exports, cookie_reads = [], [], [], []
    @contextmanager
    def busy_source(self, browser_driver, owner, platform, **kwargs):
        with original(self, browser_driver, owner, platform, **kwargs) as context:
            def route(request):
                url = request.request.url
                if 'passport.douyin.com' in url:
                    request.fulfill(content_type='text/html', body='<body>Previous login origin</body>')
                elif url.endswith('/logged-in'):
                    request.fulfill(headers={'Content-Type': 'application/json', 'Set-Cookie': 'sessionid=test-login; Domain=.douyin.com; Path=/; HttpOnly; Secure; SameSite=Lax'}, body='{}')
                elif url.endswith('/poll'):
                    request.fulfill(json={'ok': True})
                else:
                    request.fulfill(content_type='text/html', body='<input id="login" autofocus><script>fetch("/logged-in");setInterval(() => fetch("/poll"), 30)</script>')
            context.route('**/*', route)
            # A visited, closed origin made full storage exports open helper tabs.
            prior = context.new_page()
            prior.goto('https://passport.douyin.com/')
            prior.evaluate("localStorage.setItem('handoff', 'kept')")
            prior.close()
            context.on('page', lambda page: new_pages.append(page))
            storage = context.storage_state
            def full_export(*args, **kwargs):
                exports.append(True)
                return storage(*args, **kwargs)
            context.storage_state = full_export
            cookies = context.cookies
            def read_cookies(*args, **kwargs):
                cookie_reads.append(time.monotonic())
                return cookies(*args, **kwargs)
            context.cookies = read_cookies
            for page in context.pages:
                page.on('framenavigated', lambda frame, page=page: main_navigations.append(frame.url) if frame == page.main_frame else None)
                wait = page.wait_for_timeout
                def tick(milliseconds, page=page, wait=wait):
                    if close_window.is_set(): page.close()
                    else: wait(min(milliseconds, 50))
                page.wait_for_timeout = tick
            yield context
    monkeypatch.setattr(SourceSessions, 'browser_context', busy_source)
    sessions = SourceSessions(tmp_path)
    sessions.start(7, 'douyin')
    thread = sessions._logins[(7, 'douyin')]['thread']
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not sessions.status(7, 'douyin')['authenticated_hint']:
            assert not exports
            threading.Event().wait(.05)
        assert sessions.status(7, 'douyin')['authenticated_hint']
        threading.Event().wait(2.2)  # Keep many XHR responses flowing while autosaving.
        assert not exports
        assert not new_pages
        assert main_navigations == ['https://www.douyin.com/']
        assert 2 <= len(cookie_reads) <= 6  # Background polling stays bounded under load.
        close_window.set()
        thread.join(5)
        assert not thread.is_alive()
        assert SourceSessions(tmp_path).status(7, 'douyin')['authenticated_hint']
    finally:
        close_window.set()
        sessions.close()
        thread.join(5)


def test_restart_rehydrates_saved_session_cookie_even_when_existing_profile_lost_its_cookie_db(browse, tmp_path):
    import time
    sessions = SourceSessions(tmp_path)
    signed = {'cookies': [{'name': 'sessionid', 'value': 'remembered', 'domain': '.douyin.com', 'path': '/',
                           'expires': -1, 'httpOnly': True, 'secure': True, 'sameSite': 'Lax'},
                          {'name': 'expired', 'value': 'discard', 'domain': '.douyin.com', 'path': '/',
                           'expires': time.time() - 60, 'httpOnly': False, 'secure': True, 'sameSite': 'Lax'}], 'origins': []}
    sessions.save(1, 'douyin', signed)
    browse(lambda route: route.fulfill(content_type='text/html', body='<body>source</body>'))
    with playwright.sync_playwright() as driver:
        with sessions.browser_context(driver, 1, 'douyin', headless=True) as context:
            assert any(cookie['name'] == 'sessionid' for cookie in context.cookies())
            context.clear_cookies()  # Simulate browser dropping session cookies.
    assert (sessions.directory(1, 'douyin') / 'browser' / 'Default' / 'Preferences').exists()
    restarted = SourceSessions(tmp_path)
    with playwright.sync_playwright() as driver:
        with restarted.browser_context(driver, 1, 'douyin', headless=True) as context:
            cookies = {cookie['name']: cookie['value'] for cookie in context.cookies()}
            assert cookies['sessionid'] == 'remembered'
            assert 'expired' not in cookies
        with restarted.browser_context(driver, 2, 'douyin', headless=True) as context:
            assert not any(cookie['name'] == 'sessionid' for cookie in context.cookies())


def test_bilibili_localized_protocol_relative_links_keep_real_heading_instead_of_counters(browse):
    browse(lambda route: route.fulfill(content_type='text/html; charset=utf-8', body='''
      <a href="//www.bilibili.com/zh-Hans/video/BV1test123/"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="><span>5.3万 8 00:41</span></a>
      <a href="//www.bilibili.com/zh-Hans/video/BV1test123/"><h3 title="舞蹈教学">舞蹈教学</h3></a>'''))
    result = SourceDiscovery()._public_browse('bilibili', 'https://search.bilibili.com/zh-Hans/all?keyword=test', 1)
    assert result[0].url == 'https://www.bilibili.com/video/BV1test123?p=1'
    assert result[0].title == '舞蹈教学'


def test_inline_tiktok_challenge_is_reported_as_verification_not_no_results(browse):
    browse(lambda route: route.fulfill(content_type='text/html', body='<div>Drag the slider to fit the puzzle</div>'))
    from universal_video_ai.reup.discovery import discovery_issue
    with pytest.raises(ValueError) as error:
        SourceDiscovery().search('tiktok', 'dance', limit=1)
    assert discovery_issue(error.value)['status'] == 'verification_required'


def test_interactive_search_collects_only_after_simulated_user_verification_and_reuses_results(driver, tmp_path, monkeypatch):
    import threading
    import time
    _, executable = driver
    monkeypatch.setenv('DISPLAY', ':headless-test')
    monkeypatch.setattr(ChannelListingService, '_playwright_launch_candidates', staticmethod(lambda **kw: [('test', {'headless': True, 'executable_path': executable, 'args': ['--no-sandbox']})]))
    original = SourceSessions.browser_context
    manual_verification = threading.Event()
    close_window = threading.Event()
    navigated = []
    @contextmanager
    def fixture_site(self, browser_driver, owner, platform, **kwargs):
        with original(self, browser_driver, owner, platform, **kwargs) as context:
            def route(request):
                navigated.append(request.request.url)
                request.fulfill(content_type='text/html; charset=utf-8', headers={
                    'Set-Cookie': 'sessionid=fixture; Domain=.douyin.com; Path=/; HttpOnly; Secure; SameSite=Lax'},
                    body='<div id="captcha-verify-container">Test fixture awaiting user confirmation</div>')
            context.route('**/*', route)
            for page in context.pages:
                wait = page.wait_for_timeout
                def tick(milliseconds, page=page, wait=wait):
                    if manual_verification.is_set():
                        manual_verification.clear()
                        page.set_content('<a href="/video/123456789" title="舞蹈教学">video</a>')
                    if close_window.is_set(): page.close()
                    else: wait(min(milliseconds, 50))
                page.wait_for_timeout = tick
            yield context
    monkeypatch.setattr(SourceSessions, 'browser_context', fixture_site)
    sessions = SourceSessions(tmp_path)
    sessions.start(7, 'douyin', query='舞蹈')
    thread = sessions._logins[(7, 'douyin')]['thread']
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and 'yêu cầu xác minh' not in sessions.status(7, 'douyin')['message']:
            threading.Event().wait(.05)
        assert not sessions.read_search(7, 'douyin', '舞蹈')
        assert any('/search/' in url for url in navigated)
        manual_verification.set()  # Simulated user on a local fixture, no CAPTCHA solver.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not sessions.read_search(7, 'douyin', '舞蹈'):
            threading.Event().wait(.05)
        assert sessions.read_search(7, 'douyin', '舞蹈')
        assert not sessions.read_search(8, 'douyin', '舞蹈')
        close_window.set()
        thread.join(5)
        assert not thread.is_alive()
        discovery = SourceDiscovery(sessions=SourceSessions(tmp_path))
        monkeypatch.setattr(discovery, '_public_browse', lambda *a: pytest.fail('Must not open a second browser after user verification'))
        with discovery.user_scope(7):
            discovery._blocked[discovery._credential_key('douyin')] = (time.monotonic() + 600, {'status': 'verification_required', 'message': 'prior check'})
            results = discovery.search('douyin', '舞蹈', limit=3)
        assert results[0].video_id == '123456789'
    finally:
        close_window.set()
        sessions.close()
        thread.join(5)
