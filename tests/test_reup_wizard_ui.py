"""Exercise the guided UI in a real browser with all provider traffic mocked."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import shutil
from urllib.parse import urlsplit

import pytest

playwright = pytest.importorskip("playwright.sync_api")
_STATIC = Path(__file__).resolve().parents[1] / "src/universal_video_ai/web/static"


def direction(identity: str, topic: str) -> dict:
    return {"id": identity, "topic": topic, "category": "Giáo dục", "reason": "Dựa trên video đã đăng và mẫu thị trường.",
            "supporting_videos": 12, "score": 6.5,
            "search_queries": {"youtube": ["science"], "douyin": ["科学"]},
            "plan": {"goal": "Phát triển nội dung phù hợp với khán giả", "steps": ["Khảo sát video tương tự trên social", "Xử lý và chuẩn bị đăng"], "measurement": "Dựa trên metadata thực."},
            "social_evidence": [{"platform": "douyin", "video_id": "123456789", "title": "科学实验", "views": 30000,
                                 "url": "https://www.douyin.com/video/123456789"}],
            "evidence": [{"video_id": "abcdefghijk", "title": "Thí nghiệm thú vị", "views": 12000,
                          "url": "https://www.youtube.com/watch?v=abcdefghijk"}]}


def run(identity: str, status: str) -> dict:
    return {"id": identity, "status": status, "channel_id": "mine", "job_id": f"job-{identity}",
            "source_url": "https://www.youtube.com/watch?v=abcdefghijk", "title": f"Video {identity}",
            "config": {"publish_mode": "private", "topic": "Khoa học quanh ta",
                       "source_evidence": {"url": "https://www.youtube.com/watch?v=abcdefghijk", "platform": "youtube",
                                           "title": "Video khoa học", "view_count": 20000, "ranking": {"method": "metadata_proxy"}}}, "refs": {}}


@dataclass
class MockApp:
    connected: bool = True
    configured: bool = True
    profile: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    runs: list = field(default_factory=list)
    analysis: dict = field(default_factory=lambda: {"status": "not_analyzed", "report": None, "running": False})
    analysis_error: bool = False
    scan_error: bool = False
    analysis_polls: int = 0
    new_channel: bool = False
    platform_status: list = field(default_factory=list)
    discovery_state: dict = field(default_factory=dict)
    source_sessions: dict = field(default_factory=dict)
    login_gateway_error: bool = False
    owner_id: int = 1

    def connections(self) -> dict:
        return {"youtube": {"connected": self.connected, "configured": self.configured,
                            "account_ref": "mine" if self.connected else None,
                            "account_name": "Kênh Khám Phá" if self.connected else None, "updated_at": 1}}

    def ready(self, *, new_channel: bool | None = None) -> dict:
        new_channel = self.new_channel if new_channel is None else new_channel
        return {"status": "ready", "running": False, "videos_scanned": 123,
                "report": {"mode": "new_channel" if new_channel else "existing_channel", "channel": {"channel_id": "mine", "title": "Kênh Khám Phá"},
                           "videos_analyzed": 0 if new_channel else 123, "market_sample_size": 50, "market_region": "VN",
                           "market_coverage": [{"platform": "youtube", "sample_size": 30}, {"platform": "douyin", "sample_size": 20}],
                           "directions": [direction("topic:1", "Khoa học quanh ta"), direction("topic:2", "Khám phá thiên nhiên")], "warnings": []}}

    def route(self, route) -> None:
        req = route.request
        path = urlsplit(req.url).path
        if path == "/":
            route.fulfill(path=str(_STATIC / "index.html"), content_type="text/html")
            return
        if path.startswith("/assets/"):
            file = _STATIC / path.rsplit("/", 1)[-1]
            if file.is_file():
                route.fulfill(path=str(file))
            else:
                route.fulfill(status=404)
            return
        body = req.post_data_json if req.post_data and req.method in {"POST", "PUT"} else None
        self.calls.append((path, req.method, deepcopy(body)))
        data = {}
        if path == "/api/bootstrap":
            data = {"features": {}, "needs_registration": False}
        elif path == "/api/me":
            data = {"id": self.owner_id, "username": "test", "credits": 100, "referral_code": "test"}
        elif path == "/api/languages":
            data = {"targets": [{"code": "vi", "label": "Tiếng Việt"}], "sources": [{"code": "auto", "label": "Auto"}]}
        elif path == "/api/provider-settings":
            data = {"providers": []}
        elif path == "/api/jobs":
            data = []
        elif path == "/api/social/connections":
            data = self.connections()
        elif path.startswith("/api/social/connect/"):
            data = {"authorize_url": "http://reup.test/mock-consent", "qr_code_url": "/assets/favicon.svg"}
        elif path == "/api/reup-agent/source-sessions":
            data = {"owner_id": self.owner_id, "sources": [self.source_sessions.get(platform, {"platform": platform, "saved": False, "status": "not_connected", "message": "Đăng nhập khi nguồn yêu cầu xác minh."}) for platform in ("douyin", "tiktok", "bilibili")]}
        elif path.startswith("/api/reup-agent/source-sessions/"):
            platform, action = path.split("/")[-2:]
            if action == "login" and self.login_gateway_error:
                self.source_sessions[platform] = {"platform": platform, "saved": False, "browser_open": True, "status": "waiting", "message": "Cửa sổ nguồn đang mở."}
                route.fulfill(status=504, body="Gateway timeout")
                return
            if action == "verify":
                data = {**self.source_sessions.get(platform, {}), "platform": platform, "browser_open": True,
                        "status": "waiting", "message": "Hoàn tất xác minh trong cửa sổ tìm kiếm."}
            elif action == "switch":
                data = {**self.source_sessions.get(platform, {}), "platform": platform, "browser_open": True, "switching": True,
                        "status": "waiting", "message": "Đăng nhập tài khoản mới; phiên trước được giữ đến khi hoàn tất."}
            elif action in {"check", "retry"}:
                data = {**self.source_sessions.get(platform, {}), "platform": platform, "checking": False,
                        "access": {"status": "searched", "message": "Đã kiểm tra và nhận được 3 video.", "count": 3}}
            else:
                data = {"platform": platform, "saved": action == "save", "status": "saved" if action == "save" else "waiting", "browser_open": action != "save",
                        "message": "Đã lưu phiên." if action == "save" else "Phiên được tự động lưu. Hoàn tất đăng nhập rồi đóng cửa sổ."}
            self.source_sessions[platform] = data
        elif path == "/api/reup-agent/profile":
            if req.method == "PUT":
                self.profile = {**body, "channel_id": "mine"}
            data = {**self.profile, "channel_id": "mine"} if self.connected else self.profile
        elif path == "/api/reup-agent/strategy/analyze":
            self.analysis = {"status": "scanning", "report": None, "running": True, "videos_scanned": 12}
            self.analysis_polls = 0
            data = {"started": True}
        elif path == "/api/reup-agent/strategy":
            if self.analysis.get("running"):
                self.analysis_polls += 1
                if self.analysis_polls >= 2:
                    self.analysis = {"status": "error", "running": False, "report": None, "error": "Chưa đọc được kênh. Hãy thử lại."} if self.analysis_error else self.ready()
            data = self.analysis
        elif path == "/api/reup-agent/scan":
            if self.scan_error:
                route.fulfill(status=400, json={"detail": "Nguồn đang yêu cầu xác minh. Đổi nguồn hoặc thử lại."})
                return
            self.runs = [run("review", "ready"), run("active", "localizing")]
            self.discovery_state = {'run_ids': [row['id'] for row in self.runs], 'runs_created': len(self.runs)}
            data = {"runs": self.runs, "warnings": [], "platform_status": self.platform_status}
        elif path == "/api/reup-agent/discovery":
            data = {"platform_status": self.platform_status, "warnings": [], **self.discovery_state}
        elif path == "/api/reup-agent/runs":
            data = self.runs
        elif path.endswith('/verify_source'):
            data = {'platform': 'douyin', 'status': 'waiting', 'browser_open': True, 'saved': True,
                    'video_id': '123456789', 'message': 'Đang kiểm tra đúng video trong phiên đã lưu.'}
            self.source_sessions['douyin'] = data
        elif path.endswith("/package"):
            data = {"title": "Video đã xử lý", "description": "Mô tả để duyệt trước khi đăng", "thumbnail_url": "/assets/favicon.svg", "hashtags": ["#khoahoc"]}
        elif path.endswith("/approve"):
            target = next(item for item in self.runs if item["id"] == path.split("/")[-2])
            target["status"] = "uploaded_private"
            data = target
        elif path == "/api/voices":
            data = {"voices": []}
        elif path.startswith("/api/jobs/") and path.endswith("/video"):
            route.fulfill(status=200, body=b"", content_type="video/mp4")
            return
        elif path == "/api/speakers/readiness":
            data = {"ready": False, "installed": False}
        route.fulfill(json=data)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as driver:
        executable = shutil.which("google-chrome") or shutil.which("chromium")
        if not executable and Path(driver.chromium.executable_path).is_file():
            executable = driver.chromium.executable_path
        if not executable:
            pytest.skip("Install Chrome or Playwright Chromium to run UI tests")
        browser = driver.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox"])
        yield browser
        browser.close()


@pytest.fixture
def ui(browser):
    app = MockApp()
    context = browser.new_context(viewport={"width": 1440, "height": 1080})
    context.add_init_script("localStorage.setItem('onboarding_completed','true');localStorage.setItem('ui-language','vi')")
    context.route("**/*", app.route)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page, app
    assert not errors
    context.close()


def open_reup(page) -> None:
    page.goto("http://reup.test/")
    page.locator('.feature-tab[data-feature="reup-agent"]').click()
    playwright.expect(page.locator("#reup-channel-name")).not_to_have_text("Đang kiểm tra kết nối…")


def test_disconnected_channel_has_one_clear_next_action_and_gates_forward_steps(ui):
    page, app = ui
    app.connected = False
    open_reup(page)
    playwright.expect(page.locator("#reup-step-1")).to_be_visible()
    playwright.expect(page.locator("#reup-analyze")).to_be_disabled()
    playwright.expect(page.locator("#reup-sources, #reup-topic")).to_have_count(0)
    page.locator('[data-reup-step="3"]').click()
    playwright.expect(page.locator("#reup-step-1")).to_be_visible()
    playwright.expect(page.locator("#reup-message")).to_contain_text("Đăng nhập")
    page.evaluate("window.open = () => null")
    page.locator("#reup-connect-youtube").click()
    playwright.expect(page.locator("#social-connect-content a")).to_have_attribute("href", "http://reup.test/mock-consent")
    page.locator("#social-connect-close").click()
    app.connected = True
    page.evaluate("document.dispatchEvent(new CustomEvent('social-connections-updated', {detail: {youtube: {connected:true, configured:true, account_ref:'mine', account_name:'Kênh Khám Phá'}}}))")
    playwright.expect(page.locator("#reup-channel-name")).to_have_text("Kênh Khám Phá")
    playwright.expect(page.locator("#reup-analyze")).to_be_enabled()
    assert not any(path == "/api/reup-agent/scan" for path, _, _ in app.calls)


def test_guided_analysis_card_selection_run_and_video_review(ui):
    page, app = ui
    open_reup(page)
    playwright.expect(page.locator("#personal-stats-card")).to_be_hidden()
    playwright.expect(page.locator("#job-queue-card")).to_be_hidden()
    playwright.expect(page.locator(".result-column")).to_be_hidden()
    page.screenshot(path="/tmp/reup-wizard-channel.png", full_page=True, animations="disabled")
    page.locator("#reup-analyze").click()
    playwright.expect(page.locator("#reup-step-2")).to_be_visible()
    playwright.expect(page.locator("#reup-strategy-status")).to_contain_text("123", timeout=12000)
    page.locator('input[name="reup-direction-choice"][value="topic:2"]').check()
    page.locator("#reup-direction-next").click()
    playwright.expect(page.locator("#reup-step-3")).to_be_visible()
    playwright.expect(page.locator("#reup-summary-topic")).to_have_text("Khám phá thiên nhiên")
    playwright.expect(page.locator("#reup-summary-channel")).to_have_text("Kênh Khám Phá")
    assert not page.locator("#reup-processing-options").evaluate("e => e.open")
    assert not page.locator("#reup-advanced-options").evaluate("e => e.open")
    assert page.locator("#app-view > header").evaluate("e => getComputedStyle(e).position") == "static"
    page.screenshot(path="/tmp/reup-wizard-start.png", full_page=True, animations="disabled")
    page.locator("#reup-scan").click()
    playwright.expect(page.locator("#reup-step-4")).to_be_visible()
    playwright.expect(page.locator("#reup-stat-review")).to_have_text("1")
    assert app.profile["strategy_direction_id"] == "topic:2"
    assert app.profile["automation"] == "assisted"
    page.locator('[data-reup-filter="review"]').click()
    playwright.expect(page.locator(".reup-run-card")).to_have_count(1)
    page.locator(".reup-source-evidence summary").click()
    playwright.expect(page.locator(".reup-source-evidence")).to_contain_text("Khoa học quanh ta")
    playwright.expect(page.locator(".reup-source-evidence")).to_contain_text("20.000 lượt xem")
    page.locator('[data-reup-preview="review"]').click()
    playwright.expect(page.locator("#reup-package-preview video")).to_have_attribute("src", "/api/jobs/job-review/video")
    page.locator("#reup-package-approve").click()
    playwright.expect(page.locator("#reup-message")).to_contain_text("Xác nhận quyền")
    assert not any(path.endswith("/approve") for path, _, _ in app.calls)
    page.locator("#reup-review-rights").check()
    page.locator("#reup-package-approve").click()
    playwright.expect(page.locator("#reup-stat-done")).to_have_text("1")
    assert len([path for path, _, _ in app.calls if path.endswith("/approve")]) == 1
    page.locator('.feature-tab[data-feature="localization"]').click()
    playwright.expect(page.locator("#personal-stats-card")).to_be_visible()
    playwright.expect(page.locator(".result-column")).to_be_visible()
    assert page.locator(".ui-language-picker").evaluate("e => e.parentElement === document.body")


def test_analysis_and_scan_failures_can_be_retried_without_entering_manual_sources(ui):
    page, app = ui
    app.analysis_error = True
    open_reup(page)
    page.locator("#reup-analyze").click()
    playwright.expect(page.locator("#reup-strategy-status")).to_contain_text("Chưa đọc được kênh", timeout=12000)
    playwright.expect(page.locator("#reup-reanalyze")).to_be_enabled()
    playwright.expect(page.locator("#reup-direction-next")).to_be_disabled()
    app.analysis_error = False
    page.locator("#reup-reanalyze").click()
    playwright.expect(page.locator("#reup-direction-next")).to_be_enabled(timeout=12000)
    page.locator("#reup-direction-next").click()
    app.scan_error = True
    page.locator("#reup-scan").click()
    playwright.expect(page.locator("#reup-message")).to_contain_text("Nguồn đang yêu cầu xác minh")
    playwright.expect(page.locator("#reup-runs")).not_to_contain_text("Agent đang tìm")
    playwright.expect(page.locator("#reup-view-settings")).to_be_visible()
    page.locator("#reup-view-settings").click()
    playwright.expect(page.locator("#reup-summary-topic")).to_have_text("Khoa học quanh ta")
    playwright.expect(page.locator("#reup-step-3")).to_be_visible()


def test_existing_settings_custom_voice_and_unsaved_edits_survive_tab_switch(ui):
    page, app = ui
    app.analysis = app.ready(new_channel=True)
    app.profile = {"strategy_mode": "auto", "strategy_direction_id": "topic:2", "daily_limit": 2, "speaker_voices": {"speaker_0": "custom-voice"}, "enabled": False}
    open_reup(page)
    playwright.expect(page.locator("#reup-step-2")).to_be_visible()
    playwright.expect(page.locator("#reup-strategy-status")).to_contain_text("Kênh mới")
    page.locator("#reup-direction-next").click()
    page.locator("#reup-limit").fill("5")
    playwright.expect(page.locator("#reup-voice0")).to_have_value("custom-voice")
    page.locator('.feature-tab[data-feature="localization"]').click()
    page.locator('.feature-tab[data-feature="reup-agent"]').click()
    playwright.expect(page.locator("#reup-limit")).to_have_value("5")
    page.locator("#reup-automation").select_option("auto")
    page.locator("#reup-scan").click()
    playwright.expect(page.locator("#reup-message")).to_contain_text("Xác nhận quyền")
    assert not any(path == "/api/reup-agent/scan" for path, _, _ in app.calls)
    page.set_viewport_size({"width": 390, "height": 844})
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    assert page.locator("#reup-scan").evaluate("e => e.getBoundingClientRect().width >= e.parentElement.getBoundingClientRect().width * .95")
    assert page.locator("#reup-scan").bounding_box()["height"] < 80
    page.locator("#reup-rights").check()
    playwright.expect(page.locator("#reup-message")).to_be_hidden()
    page.screenshot(path="/tmp/reup-wizard-mobile.png", full_page=True, animations="disabled")
    page.locator("#reup-enabled").check()
    playwright.expect(page.locator("#reup-scan")).to_have_text("Bật Agent và bắt đầu →")
    page.locator("#reup-scan").click()
    playwright.expect(page.locator("#reup-stop-agent")).to_be_visible()
    assert app.profile["enabled"] is True
    page.locator("#reup-stop-agent").click()
    playwright.expect(page.locator("#reup-profile-state")).to_have_text("Chạy theo yêu cầu")
    assert app.profile["enabled"] is False


def test_review_cannot_publish_to_a_different_connected_channel(ui):
    page, app = ui
    old = run("old-channel", "ready")
    old["channel_id"] = "previous-channel"
    app.runs = [old]
    open_reup(page)
    playwright.expect(page.locator("#reup-step-4")).to_be_visible()
    page.locator('[data-reup-preview="old-channel"]').click()
    playwright.expect(page.locator("#reup-package-preview")).to_contain_text("không phải kênh đích")
    playwright.expect(page.locator("#reup-package-approve")).to_have_count(0)
    assert not any(path.endswith("/approve") for path, _, _ in app.calls)


@pytest.mark.parametrize("new_channel", [False, True])
def test_agent_starts_from_channel_and_selects_a_strategy_without_source_or_topic_inputs(ui, new_channel):
    page, app = ui
    app.new_channel = new_channel
    app.profile = {"strategy_mode": "manual", "topic": "stale topic", "keywords": ["stale filter"],
                   "source_urls": ["https://youtu.be/abcdefghijk"]}
    open_reup(page)
    playwright.expect(page.locator("#reup-sources, #reup-topic, #reup-keywords, #reup-strategy-mode, #reup-choose-manually")).to_have_count(0)
    page.locator("#reup-analyze").click()
    playwright.expect(page.locator("#reup-step-2")).to_be_visible()
    playwright.expect(page.locator("#reup-direction-next")).to_be_enabled(timeout=12000)
    playwright.expect(page.locator("#reup-strategy-heading")).to_have_text("Chiến lược xây kênh mới" if new_channel else "Chiến lược phát triển từ nội dung đã đăng")
    playwright.expect(page.locator("#reup-market-coverage")).to_contain_text("Douyin: 20")
    playwright.expect(page.locator("#reup-strategy-plan")).to_contain_text("Khảo sát video tương tự")
    playwright.expect(page.locator('input[name="reup-direction-choice"]').first).to_be_checked()
    page.screenshot(path=f"/tmp/reup-channel-{'new' if new_channel else 'existing'}-strategy.png", full_page=True, animations="disabled")
    if new_channel:
        page.set_viewport_size({"width": 390, "height": 844})
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        page.screenshot(path="/tmp/reup-channel-new-mobile.png", full_page=True, animations="disabled")
    page.locator("#reup-direction-next").click()
    playwright.expect(page.locator("#reup-step-3")).to_be_visible()
    playwright.expect(page.locator("#reup-summary-topic")).to_have_text("Khoa học quanh ta")
    page.locator("#reup-scan").click()
    playwright.expect(page.locator("#reup-step-4")).to_be_visible()
    playwright.expect(page.locator("#reup-stat-review")).to_have_text("1")
    assert app.profile["strategy_mode"] == "auto"
    assert app.profile["automation"] == "assisted"
    assert app.profile["topic"] == "" and app.profile["source_urls"] == [] and app.profile["keywords"] == []
    assert app.profile["strategy_direction_id"] == ""
    assert any(path == "/api/reup-agent/strategy/analyze" for path, _, _ in app.calls)
    actions = [(path, method) for path, method, _ in app.calls]
    assert actions.index(("/api/reup-agent/profile", "PUT")) < actions.index(("/api/reup-agent/scan", "POST"))


def test_forward_navigation_requires_analysis_and_never_asks_for_manual_sources(ui):
    page, app = ui
    open_reup(page)
    page.locator('[data-reup-step="3"]').click()
    playwright.expect(page.locator("#reup-step-2")).to_be_visible()
    playwright.expect(page.locator("#reup-message")).to_contain_text("khảo sát trend")
    playwright.expect(page.locator("#reup-sources, #reup-topic")).to_have_count(0)
    assert not any(path == "/api/reup-agent/scan" for path, _, _ in app.calls)


def test_source_diagnostics_show_chinese_queries_and_distinguish_blocked_platforms(ui):
    page, app = ui
    app.analysis = app.ready()
    app.platform_status = [
        {"platform": "douyin", "queries": ["时尚 舞蹈"], "status": "verification_required", "accepted": 0, "message": "Cần xác minh phiên thu thập"},
        {"platform": "bilibili", "queries": ["时尚 穿搭"], "status": "http_412", "accepted": 0, "message": "HTTP 412"},
        {"platform": "tiktok", "queries": ["fashion dance"], "status": "searched", "accepted": 2},
    ]
    open_reup(page)
    page.locator("#reup-direction-next").click()
    page.locator("#reup-scan").click()
    playwright.expect(page.locator("#reup-source-status")).to_contain_text("时尚 舞蹈")
    playwright.expect(page.locator("#reup-source-status")).to_contain_text("HTTP 412")
    playwright.expect(page.locator("#reup-source-status")).to_contain_text("2 video đủ liên quan")
    playwright.expect(page.locator("#reup-activity-help")).to_contain_text("1/3 nền tảng")
    page.locator('.feature-tab[data-feature="localization"]').click()
    page.locator('.feature-tab[data-feature="reup-agent"]').click()
    playwright.expect(page.locator("#reup-source-status")).to_contain_text("时尚 穿搭")
    page.set_viewport_size({"width": 390, "height": 844})
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    page.screenshot(path="/tmp/reup-multilingual-source-status.png", full_page=True, animations="disabled")


def test_empty_latest_search_does_not_display_an_old_wrong_candidate_or_offer_retry(ui):
    page, app = ui
    old = run('obsolete-music', 'failed')
    old['title'] = 'Justin Bieber - Beauty And A Beat'
    old['config']['strategy_mode'] = 'auto'
    app.runs = [old]
    app.discovery_state = {'run_ids': [], 'runs_created': 0, 'videos_found': 0}
    app.platform_status = [{'platform': 'youtube', 'queries': ['fashion dance'], 'status': 'searched',
                            'retrieved': 20, 'accepted': 0, 'rejected': 20}]
    open_reup(page)
    playwright.expect(page.locator('#reup-runs')).to_contain_text('Lượt tìm mới nhất chưa có video phù hợp')
    playwright.expect(page.locator('.reup-run-card')).to_have_count(0)
    playwright.expect(page.locator('#reup-stat-error')).to_have_text('0')
    playwright.expect(page.locator('#reup-activity-help')).to_contain_text('0 video phù hợp')
    playwright.expect(page.locator('#reup-source-status')).to_contain_text('Đã nhận 20 video')
    playwright.expect(page.locator('#reup-history-count')).to_contain_text('(1)')
    page.screenshot(path='/tmp/reup-empty-latest-search.png', full_page=True, animations='disabled')
    page.locator('[data-reup-scope="history"]').click()
    playwright.expect(page.locator('.reup-run-card')).to_have_count(1)
    playwright.expect(page.locator('#reup-stat-error')).to_have_text('1')
    playwright.expect(page.locator('[data-reup-action="retry"]')).to_have_count(0)
    playwright.expect(page.locator('.reup-run-card')).to_contain_text('không được chạy lại')


def test_blocked_source_opens_login_and_saves_session_without_connecting_publish_account(ui):
    page, app = ui
    app.analysis = app.ready()
    app.platform_status = [{"platform": "douyin", "queries": ["舞蹈"], "status": "unavailable", "accepted": 0}]
    open_reup(page)
    page.locator("#reup-direction-next").click()
    page.locator("#reup-scan").click()
    page.locator("#reup-source-status").get_by_role("button", name="Đăng nhập / kiểm tra nguồn").click()
    playwright.expect(page.locator("#reup-step-1")).to_be_visible()
    playwright.expect(page.locator("#reup-source-connections")).to_have_attribute("open", "")
    page.locator("#reup-source-login-douyin").click()
    save = page.locator('[data-source-platform="douyin"][data-source-action="save"]')
    playwright.expect(save).to_be_visible()
    save.click()
    playwright.expect(page.locator("#reup-source-sessions")).to_contain_text("Nguồn truy cập được")
    assert app.source_sessions["douyin"]["saved"] is True
    assert not any(path.startswith("/api/social/connect/") for path, _, _ in app.calls)
    page.set_viewport_size({"width": 390, "height": 844})
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")


def test_closing_logged_in_source_window_autochecks_and_does_not_request_another_login(ui):
    page, app = ui
    open_reup(page)
    page.locator('[data-reup-step="1"]').click()
    page.locator('#reup-source-connections > summary').click()
    page.locator('#reup-source-login-douyin').click()
    playwright.expect(page.locator('[data-source-platform="douyin"][data-source-action="save"]')).to_be_visible()
    app.source_sessions['douyin'] = {'platform': 'douyin', 'saved': True, 'authenticated_hint': True, 'browser_open': False,
                                      'status': 'saved', 'message': 'Phiên được tự động lưu.'}
    playwright.expect(page.locator('#reup-source-sessions')).to_contain_text('Nguồn truy cập được', timeout=10000)
    calls = [path for path, method, _ in app.calls if method == 'POST' and '/source-sessions/douyin/' in path]
    assert calls.count('/api/reup-agent/source-sessions/douyin/login') == 1
    assert calls.count('/api/reup-agent/source-sessions/douyin/check') == 1
    assert '/api/reup-agent/source-sessions/douyin/save' not in calls
    assert not any(path == '/api/reup-agent/scan' for path, _, _ in app.calls)
    page.reload()
    page.locator('.feature-tab[data-feature="reup-agent"]').click()
    page.locator('[data-reup-step="1"]').click()
    page.locator('#reup-source-connections > summary').click()
    playwright.expect(page.locator('#reup-source-sessions')).to_contain_text('Nguồn truy cập được')


def test_login_request_504_recovers_status_without_reopening_the_source_window(ui):
    page, app = ui
    app.login_gateway_error = True
    open_reup(page)
    page.locator('[data-reup-step="1"]').click()
    page.locator('#reup-source-connections > summary').click()
    page.locator('#reup-source-login-douyin').click()
    playwright.expect(page.locator('#reup-message')).to_contain_text('HTTP 504')
    playwright.expect(page.locator('#reup-source-login-douyin')).to_have_text('Xem cửa sổ nguồn')
    assert sum(path == '/api/reup-agent/source-sessions/douyin/login' for path, _, _ in app.calls) == 1
    app.source_sessions['douyin'] = {'platform': 'douyin', 'saved': True, 'browser_open': False, 'status': 'saved', 'message': 'Đã tự lưu phiên.'}
    playwright.expect(page.locator('#reup-source-sessions')).to_contain_text('Nguồn truy cập được', timeout=10000)
    page.set_viewport_size({'width': 390, 'height': 844})
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')


def test_empty_query_is_not_shown_as_searched_and_offers_keyword_repair(ui):
    page, app = ui
    app.analysis = app.ready()
    app.platform_status = [{'platform': platform, 'queries': [], 'status': 'searched', 'accepted': 0, 'retrieved': 0}
                           for platform in ('douyin', 'tiktok', 'bilibili')]
    open_reup(page)
    page.locator('#reup-direction-next').click()
    page.locator('#reup-scan').click()
    playwright.expect(page.locator('#reup-source-status')).to_contain_text('Chưa có từ khóa · chưa tìm kiếm')
    playwright.expect(page.locator('#reup-activity-help')).to_contain_text('0/3 nền tảng')
    page.locator('#reup-source-status').get_by_role('button', name='Xem và chuẩn bị từ khóa').first.click()
    playwright.expect(page.locator('#reup-step-2')).to_be_visible()
    assert not any(path.startswith('/api/reup-agent/source-sessions/') and path.endswith('/login') for path, _, _ in app.calls)


def test_repeat_search_uses_the_existing_wizard_validation_and_preserves_user_settings(ui):
    page, app = ui
    app.analysis = app.ready()
    open_reup(page)
    page.locator('[data-reup-step="4"]').click()
    button = page.locator('#reup-search-again')
    playwright.expect(button).to_be_enabled()
    with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/api/reup-agent/scan')):
        button.click()
    playwright.expect(page.locator('#reup-step-4')).to_be_visible()
    assert sum(path == '/api/reup-agent/scan' and method == 'POST' for path, method, _ in app.calls) == 1


def test_a_direction_without_queries_cannot_start_a_zero_query_run(ui):
    page, app = ui
    app.analysis = app.ready()
    app.analysis['report']['directions'][0]['search_queries'] = {'douyin': [], 'tiktok': [], 'bilibili': []}
    open_reup(page)
    page.locator('[data-reup-step="2"]').click()
    playwright.expect(page.locator('#reup-direction-next')).to_be_disabled()
    playwright.expect(page.locator('#reup-search-again')).to_be_disabled()
    page.locator('[data-reup-step="3"]').click()
    playwright.expect(page.locator('#reup-step-2')).to_be_visible()
    assert not any(path == '/api/reup-agent/scan' for path, _, _ in app.calls)


def test_saved_source_login_is_reused_after_page_restart_without_login_request(ui):
    page, app = ui
    app.source_sessions['douyin'] = {'platform': 'douyin', 'saved': True, 'authenticated_hint': True,
                                     'browser_open': False, 'status': 'saved', 'message': 'Đã ghi nhớ đăng nhập.'}
    open_reup(page)
    page.locator('[data-reup-step="1"]').click()
    page.locator('#reup-source-connections > summary').click()
    playwright.expect(page.locator('#reup-source-sessions')).to_contain_text('Đã ghi nhớ đăng nhập')
    playwright.expect(page.locator('[data-source-platform="douyin"][data-source-action="check"]')).to_have_text('Dùng phiên đã lưu')
    page.reload()
    page.locator('.feature-tab[data-feature="reup-agent"]').click()
    page.locator('[data-reup-step="1"]').click()
    page.locator('#reup-source-connections > summary').click()
    playwright.expect(page.locator('#reup-source-sessions')).to_contain_text('Đã ghi nhớ đăng nhập')
    assert not any(path.endswith('/douyin/login') or path.endswith('/douyin/switch') for path, method, _ in app.calls if method == 'POST')
    with page.expect_request(lambda request: request.url.endswith('/douyin/switch') and request.method == 'POST'):
        page.locator('[data-source-platform="douyin"][data-source-action="switch"]').click()
    playwright.expect(page.locator('#reup-source-sessions')).to_contain_text('Đang đổi tài khoản nguồn')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')


def test_changing_application_account_loads_its_own_source_sessions_and_settings(ui):
    page, app = ui
    app.source_sessions['douyin'] = {'platform': 'douyin', 'saved': True, 'authenticated_hint': True,
                                     'browser_open': False, 'status': 'saved', 'message': 'Account A remembered.'}
    open_reup(page)
    page.locator('[data-reup-step="1"]').click()
    page.locator('#reup-source-connections > summary').click()
    playwright.expect(page.locator('#reup-source-sessions')).to_contain_text('Account A remembered.')
    app.owner_id = 2
    app.profile = {'daily_limit': 7}
    app.source_sessions = {}
    page.locator('.feature-tab[data-feature="localization"]').click()
    page.locator('.feature-tab[data-feature="reup-agent"]').click()
    playwright.expect(page.locator('#reup-source-sessions')).not_to_contain_text('Account A remembered.')
    playwright.expect(page.locator('#reup-limit')).to_have_value('7')
    playwright.expect(page.locator('[data-source-platform="douyin"][data-source-action="switch"]')).to_have_count(0)
    assert not any(path.endswith('/douyin/login') for path, method, _ in app.calls if method == 'POST')


def test_captcha_action_opens_search_verification_instead_of_another_login(ui):
    page, app = ui
    app.source_sessions['douyin'] = {'platform': 'douyin', 'saved': True, 'authenticated_hint': True, 'browser_open': False,
                                    'status': 'saved', 'message': 'Phiên đăng nhập còn hạn.',
                                    'access': {'status': 'verification_required', 'message': 'Trang tìm kiếm cần xác minh.'}}
    open_reup(page)
    page.locator('[data-reup-step="1"]').click()
    page.locator('#reup-source-connections > summary').click()
    verify = page.locator('[data-source-platform="douyin"][data-source-action="verify"]')
    playwright.expect(verify).to_have_text('Xác minh tìm kiếm')
    with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/douyin/verify')):
        verify.click()
    assert not any(path.endswith('/douyin/login') for path, method, _ in app.calls if method == 'POST')
    playwright.expect(page.locator('#reup-source-sessions')).to_contain_text('cửa sổ tìm kiếm')


def test_failed_douyin_card_opens_its_video_without_starting_localization(ui):
    page, app = ui
    source = run('blocked', 'failed')
    source['source_url'] = 'https://www.douyin.com/video/123456789'
    source['error'] = 'Douyin chưa cho tải video trong phiên hiện tại.'
    source['config']['source_evidence'] = {}
    app.runs = [source]
    open_reup(page)
    button = page.locator('[data-reup-id="blocked"][data-reup-action="verify_source"]')
    playwright.expect(button).to_have_text('Kiểm tra video nguồn')
    before = len(app.calls)
    button.click()
    playwright.expect(page.locator('#reup-source-sessions')).to_contain_text('Đang kiểm tra đúng video')
    assert any(path == '/api/reup-agent/runs/blocked/verify_source' and method == 'POST' for path, method, _ in app.calls)
    unexpected = [(path, method) for path, method, _ in app.calls[before:]
                  if method == 'POST' and (path.startswith('/api/jobs/') or path.endswith('/process') or path.endswith('/retry'))]
    assert not unexpected, unexpected


def test_new_successful_source_check_replaces_stale_verification_action_without_inflating_results(ui):
    page, app = ui
    app.analysis = app.ready()
    app.runs = [run('active', 'localizing')]
    app.platform_status = [{'platform': 'douyin', 'queries': ['科学'], 'status': 'verification_required',
                            'message': 'Cần xác minh khi tìm kiếm.', 'retrieved': 0, 'accepted': 0},
                           {'platform': 'bilibili', 'queries': ['科学'], 'status': 'searched', 'retrieved': 20, 'accepted': 7}]
    app.discovery_state = {'query_version': 4, 'completed_at': 100}
    app.source_sessions['douyin'] = {'platform': 'douyin', 'saved': True, 'authenticated_hint': True,
        'status': 'saved', 'access': {'status': 'searched', 'count': 3, 'query': '科学', 'checked_at': 200,
                                    'message': 'Đã kiểm tra và nhận được 3 video.'}}
    open_reup(page)
    card = page.locator('.reup-source-status-grid > article').first
    playwright.expect(card.locator('.reup-status')).to_have_text('Có thể tìm lại')
    playwright.expect(card).to_contain_text('Lượt tìm trước: Đã nhận 0 video')
    playwright.expect(card).to_contain_text('Kết quả kiểm tra chưa được cộng vào lượt tìm')
    playwright.expect(card.get_by_role('button', name='Xác minh trang tìm kiếm')).to_have_count(0)
    playwright.expect(page.locator('#reup-activity-help')).to_contain_text('7 video phù hợp')
    playwright.expect(page.locator('#reup-activity-help')).to_contain_text('1 nguồn vừa kiểm tra thành công')
    assert not any(path.endswith('/scan') and method == 'POST' for path, method, _ in app.calls)
    card.get_by_role('button').click()
    playwright.expect(page.locator('#reup-message')).to_contain_text('Đã tìm được')
    assert any(path == '/api/reup-agent/scan' and method == 'POST' for path, method, _ in app.calls)
    assert not any(path.endswith('/verify') or path.endswith('/login') for path, _, _ in app.calls)


@pytest.mark.parametrize('checked_at,query', [(50, '科学'), (200, '别的')])
def test_older_or_different_query_health_check_does_not_clear_scan_verification(ui, checked_at, query):
    page, app = ui
    app.runs = [run('active', 'localizing')]
    app.platform_status = [{'platform': 'douyin', 'queries': ['科学'], 'status': 'verification_required', 'accepted': 0}]
    app.discovery_state = {'query_version': 4, 'completed_at': 100}
    app.source_sessions['douyin'] = {'platform': 'douyin', 'saved': True, 'status': 'saved',
        'access': {'status': 'searched', 'count': 3, 'query': query, 'checked_at': checked_at, 'message': 'Đã kiểm tra 3 video.'}}
    open_reup(page)
    card = page.locator('.reup-source-status-grid > article').first
    playwright.expect(card.locator('.reup-status')).to_have_text('Cần xác minh phiên thu thập')
    playwright.expect(card.get_by_role('button', name='Xác minh trang tìm kiếm')).to_be_visible()
    playwright.expect(card).to_contain_text('trước lượt tìm' if checked_at < 100 else 'Đây là từ khóa khác')
