"""Offline browser smoke test: real UI scripts, isolated synthetic jobs, no server writes."""
from __future__ import annotations
import logging
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

__all__ = ["main"]
_logger = logging.getLogger(__name__)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    assets = root / "src/universal_video_ai/web/static"
    output = root / "temp/history-review"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="vi-VN")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.dismiss())
        page.add_init_script("localStorage.setItem('onboarding_completed', 'true')")
        def route(request):
            url = urlsplit(request.request.url)
            if url.hostname != "workspace.test":
                request.abort()
                return
            name = "index.html" if url.path == "/" else Path(url.path).name
            target = assets / name
            if target.is_file():
                if target.suffix not in {".html", ".css", ".js"}:
                    request.fulfill(path=str(target))
                    return
                body = target.read_text(encoding="utf-8")
                if name == "app.js":
                    body = body.replace("\ninitAuth();", "\n")
                mime = "application/javascript" if name.endswith(".js") else (
                    "text/css" if name.endswith(".css") else "text/html")
                request.fulfill(body=body, content_type=mime)
            else:
                request.fulfill(json={})
        page.route("**/*", route)
        page.goto("http://workspace.test/", wait_until="load")
        jobs = [
            {"id": "sample-1", "status": "error", "title": "Cứu hộ trong thành phố — thiếu giọng ở câu 16"},
            {"id": "sample-2", "status": "cancelled", "title": "Nhật ký hành trình / Tập 02"},
            {"id": "sample-3", "status": "done", "title": "Một ngày bình yên ở vùng cao"},
        ]
        for job in jobs:
            job.update(source_url="https://example.com/video/" + job["id"], created_at=1788888000,
                       target_language="vi", progress=0, has_video=False, duration_seconds=104,
                       progress_note="Sẵn sàng xử lý", segments=[], source_segments=[])
        page.evaluate("""jobs => {
            window.demoJobs = jobs; window.demoRequests = [];
            api = async (url, options) => {
                window.demoRequests.push(url);
                if (url.startsWith('/api/jobs?') || url === '/api/jobs') return jobs;
                if (url === '/api/me') return {credits: 100};
                return {queued: [], skipped: [], errors: []};
            };
            document.querySelector('#landing-view').classList.add('hidden');
            document.querySelector('#app-view').classList.remove('hidden');
        }""", jobs)
        page.evaluate("refreshJobs()")
        page.locator("#history-date").fill("2026-09-08")
        page.locator("#history-date").dispatch_event("change")
        page.wait_for_function("demoRequests.some(url => url.includes('date_from=') && url.includes('date_to='))")
        page.locator("#history-select-all").check()
        page.wait_for_function("document.querySelector('#history-retry-selected').textContent.includes('(2)')")
        assert page.locator("#history-bulk-rerun").count() == 0
        for width, label in [(1440, "desktop"), (390, "mobile")]:
            page.set_viewport_size({"width": width, "height": 1000})
            panel = page.locator(".history-panel")
            panel.screenshot(path=str(output / (label + ".jpg")), type="jpeg", quality=85)
            assert panel.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), label
        page.locator("#history-retry-all").click()
        page.locator("#confirm-accept").click()
        page.wait_for_function("demoRequests.includes('/api/jobs/retry-all-incomplete')")
        assert not errors, errors
        _logger.info("PASS: single day, selection, global retry, desktop/mobile; screenshots=%s", output)
        browser.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
