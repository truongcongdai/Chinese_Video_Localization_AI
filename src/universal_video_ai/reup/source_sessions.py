"""Private browser state for source discovery, isolated by app account.

Login is interactive: the user completes the platform's normal login and
verification in a dedicated window. State is checkpointed automatically.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from http.cookiejar import Cookie, MozillaCookieJar
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
import hashlib
from urllib.parse import parse_qs

from universal_video_ai.downloader.channel import ChannelListingService

_logger = logging.getLogger(__name__)
SOURCE_HOME = {"douyin": "https://www.douyin.com/", "tiktok": "https://www.tiktok.com/",
               "bilibili": "https://www.bilibili.com/"}
_COOKIE_FILES: ContextVar[dict[str, str]] = ContextVar("reup_source_cookie_files", default={})
_DOWNLOAD_CONTEXT: ContextVar[tuple | None] = ContextVar("reup_source_download_context", default=None)


def source_cookie_file(platform: str) -> str | None:
    """The current job's source credentials; never mutate process-wide env."""
    candidate = _COOKIE_FILES.get().get(platform.lower())
    return candidate if candidate and Path(candidate).is_file() else None


def source_download_context():
    """The current download's owner and account directories, inherited by to_thread."""
    return _DOWNLOAD_CONTEXT.get()


class SourceSessions:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self._lock = threading.RLock()
        self._logins: dict[tuple[int, str], dict] = {}
        self._browser_locks: dict[tuple[int, str], threading.Lock] = {}
        self._slots = ContextVar(f"source_slots_{id(self)}", default={})

    def _base_directory(self, owner: int, platform: str) -> Path:
        if owner < 1 or platform not in SOURCE_HOME:
            raise ValueError("Nguồn hoặc tài khoản không hợp lệ.")
        return self.root / str(owner) / platform

    def directory(self, owner: int, platform: str) -> Path:
        base = self._base_directory(owner, platform)
        override = self._slots.get().get((owner, platform))
        if override:
            return base / "accounts" / override
        marker = base / "active.json"
        if marker.is_file():
            slot = json.loads(marker.read_text())["slot"]
            if not isinstance(slot, str) or len(slot) != 32 or any(c not in "0123456789abcdef" for c in slot):
                raise ValueError("Hồ sơ tài khoản nguồn không hợp lệ; phiên cũ chưa bị xóa.")
            return base / "accounts" / slot
        return base

    @contextmanager
    def _slot_scope(self, owner: int, platform: str, slot: str):
        token = self._slots.set({**self._slots.get(), (owner, platform): slot})
        try:
            yield
        finally:
            self._slots.reset(token)

    def _activate(self, owner: int, platform: str, slot: str) -> None:
        base = self._base_directory(owner, platform)
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=base, prefix=".active-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"slot": slot, "switched_at": time.time()}, stream)
            os.replace(temporary, base / "active.json")
        finally:
            Path(temporary).unlink(missing_ok=True)

    def state_path(self, owner: int, platform: str) -> Path:
        return self.directory(owner, platform) / "state.json"

    def context_options(self, owner: int, platform: str) -> dict:
        options = {"locale": "en-US" if platform == "tiktok" else "zh-CN",
                   "viewport": {"width": 1440, "height": 1000}}
        if owner and self.state_path(owner, platform).is_file():
            options["storage_state"] = str(self.state_path(owner, platform))
        return options

    def signature(self, owner: int, platform: str) -> int:
        if not owner or platform not in SOURCE_HOME:
            return 0
        try:
            return self.state_path(owner, platform).stat().st_mtime_ns
        except FileNotFoundError:
            return 0

    @contextmanager
    def download_scope(self, owner: int):
        directories = {platform: self.directory(owner, platform) for platform in SOURCE_HOME}
        files = {platform: str(directory / "cookies.txt") for platform, directory in directories.items()}
        token = _COOKIE_FILES.set(files)
        context_token = _DOWNLOAD_CONTEXT.set((self, owner, directories))
        try:
            yield
        finally:
            _DOWNLOAD_CONTEXT.reset(context_token)
            _COOKIE_FILES.reset(token)

    @staticmethod
    def _auth_cookie_present(state: dict, platform: str) -> bool:
        names = {"douyin": {"sessionid", "sessionid_ss", "sid_guard"},
                 "tiktok": {"sessionid", "sessionid_ss", "sid_tt"},
                 "bilibili": {"SESSDATA"}}[platform]
        return any(item.get("name") in names and item.get("value") and
                   (item.get("expires", -1) <= 0 or item["expires"] > time.time())
                   for item in state.get("cookies", []))

    def status(self, owner: int, platform: str) -> dict:
        signature = self.signature(owner, platform)
        authenticated = False
        if signature:
            try:
                authenticated = self._auth_cookie_present(json.loads(self.state_path(owner, platform).read_text()), platform)
            except (OSError, ValueError, TypeError):
                pass
        access = {}
        try:
            access = json.loads((self.directory(owner, platform) / "access.json").read_text())
            if access.get("signature") != signature:
                access = {}
        except (OSError, ValueError, TypeError):
            access = {}
        with self._lock:
            login = self._logins.get((owner, platform), {})
            thread = login.get("thread")
            active = bool(thread and thread.is_alive())
            default = "Đã ghi nhớ phiên đăng nhập. Các lần mở sau sẽ dùng lại phiên này." if authenticated else (
                "Đã lưu phiên truy cập; bấm Kiểm tra tìm nguồn để kiểm tra khả năng tìm video." if signature else
                "Chưa lưu phiên nguồn. Có thể kiểm tra truy cập trước khi đăng nhập.")
            return {"platform": platform, "saved": bool(signature), "authenticated_hint": authenticated,
                    "updated_at": signature / 1e9 if signature else None, "browser_open": active,
                    "status": login.get("status", "saved" if signature else "not_connected"), "switching": bool(active and login.get("slot")),
                    "message": login.get("message", default), "access": access, "video_id": login.get("video_id", "")}

    def record_access(self, owner: int, platform: str, issue: dict, *, count: int = 0, query: str = "", signature: int | None = None) -> None:
        directory = self.directory(owner, platform)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock:
            current = self.signature(owner, platform)
            if signature is not None and signature != current:
                return
            value = {"signature": current, "checked_at": time.time(), "count": count, "query": query, **issue}
            fd, temporary = tempfile.mkstemp(dir=directory, prefix=".access-")
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(value, stream, ensure_ascii=False)
                os.replace(temporary, directory / "access.json")
            finally:
                Path(temporary).unlink(missing_ok=True)

    def _search_file(self, owner: int, platform: str, query: str) -> Path:
        return self.directory(owner, platform) / ("search-" + hashlib.sha256(query.encode()).hexdigest()[:24] + ".json")

    def save_search(self, owner: int, platform: str, query: str, rows: list[dict], *, directory: Path | None = None) -> None:
        if not rows:
            return
        target = self._search_file(owner, platform, query)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = {"query": query, "platform": platform, "collected_at": time.time(), "candidates": rows[:20]}
        with self._lock:
            if directory is not None and (self.directory(owner, platform) != directory or target.parent != directory):
                return
            fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".search-")
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(value, stream, ensure_ascii=False)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        self.record_access(owner, platform, {"status": "searched", "message": f"Đã đọc {len(rows[:20])} video từ cửa sổ tìm kiếm vừa xác minh. Có thể tìm video lại."}, count=len(rows[:20]), query=query)

    def read_search(self, owner: int, platform: str, query: str) -> list[dict]:
        try:
            value = json.loads(self._search_file(owner, platform, query).read_text())
            if value.get("query") != query or value.get("platform") != platform or not 0 <= time.time() - value.get("collected_at", 0) <= 300:
                return []
            return value.get("candidates", [])[:20]
        except (OSError, ValueError, TypeError):
            return []

    def save_media(self, owner: int, payload: object, *, directory: Path | None = None, user_agent: str = "") -> int:
        from universal_video_ai.downloader.douyin import douyin_media_items
        items = douyin_media_items(payload)
        with self._lock:
            current = self.directory(owner, "douyin")
            if directory is not None and current != directory:
                return 0
            current.mkdir(parents=True, exist_ok=True, mode=0o700)
            for item in items[:30]:
                item["user_agent"] = user_agent[:500]
                target = current / ("media-" + item["id"] + ".json")
                fd, temporary = tempfile.mkstemp(dir=current, prefix=".media-")
                try:
                    with os.fdopen(fd, "w") as stream:
                        json.dump({"collected_at": time.time(), "item": item}, stream, ensure_ascii=False)
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            return len(items[:30])

    def read_media(self, owner: int, video_id: str) -> dict | None:
        if not video_id.isdigit():
            return None
        try:
            data = json.loads((self.directory(owner, "douyin") / ("media-" + video_id + ".json")).read_text())
            if 0 <= time.time() - data["collected_at"] <= 600 and data["item"]["id"] == video_id:
                return data["item"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def download_issue(self, owner: int, message: str | None = None) -> str | None:
        target = self.directory(owner, "douyin") / "download-access.json"
        if message is not None:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".download-access-")
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump({"message": message, "signature": self.signature(owner, "douyin"), "checked_at": time.time()}, stream)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return message
        try:
            value = json.loads(target.read_text())
            if value["signature"] == self.signature(owner, "douyin") and 0 <= time.time() - value["checked_at"] < 120:
                return value["message"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    @contextmanager
    def browser_context(self, driver, owner: int, platform: str, *, headless: bool):
        """Use the same private Chromium profile for login and discovery."""
        with self._lock:
            lock = self._browser_locks.setdefault((owner, platform), threading.RLock())
        if not lock.acquire(blocking=False):
            raise ValueError("source browser busy: đóng cửa sổ nguồn đang mở rồi kiểm tra tìm nguồn.")
        context = None
        try:
            directory = self.directory(owner, platform)
            profile = directory / "browser"
            existing_profile = (profile / "Default" / "Preferences").is_file()
            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            options = self.context_options(owner, platform)
            legacy_state = options.pop("storage_state", None)
            for _, launch in ChannelListingService._playwright_launch_candidates(headless=headless):
                try:
                    context = driver.chromium.launch_persistent_context(str(profile), **options, **{**launch, "timeout": 15000})
                    break
                except Exception:
                    continue
            if context is None:
                raise ValueError("No browser available: chưa mở được trình duyệt nguồn; kiểm tra Chrome và cửa sổ nguồn đang mở.")
            context.set_default_timeout(3000)
            if legacy_state:
                state = json.loads(Path(legacy_state).read_text())
                # Chromium may discard session cookies, or fail to restore its
                # cookie DB. Rehydrate our saved, unexpired cookies every time.
                context.add_cookies([cookie for cookie in state.get("cookies", []) if cookie.get("expires", -1) <= 0 or cookie["expires"] > time.time()])
                if not existing_profile:
                    origins = json.dumps(state.get("origins", []))
                    context.add_init_script(script="""(() => { for (const origin of """ + origins + """) {
                    if (location.origin === origin.origin) for (const item of origin.localStorage || []) {
                        localStorage.setItem(item.name, item.value);
                    }
                } })();""")
            yield context
        finally:
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass  # Closing the window can already have closed its context.
            finally:
                lock.release()

    def save(self, owner: int, platform: str, state: dict, *, expected_signature: int | None = None) -> None:
        with self._lock:
            if expected_signature is not None and self.signature(owner, platform) != expected_signature:
                return  # A newer interactive login wins over an in-flight scan.
            self._save(owner, platform, state)

    def _save(self, owner: int, platform: str, state: dict) -> None:
        directory = self.directory(owner, platform)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        # Only retain platform cookies/origins. Never copy an unrelated login.
        domain = f"{platform}.com"
        from urllib.parse import urlparse
        belongs = lambda host: host == domain or host.endswith("." + domain)
        cookies = [item for item in state.get("cookies", []) if belongs(str(item.get("domain", "")).lstrip("."))]
        origins = [item for item in state.get("origins", []) if belongs(urlparse(item.get("origin", "")).hostname or "")]
        serialized = json.dumps({"cookies": cookies, "origins": origins})
        state_file = self.state_path(owner, platform)
        if state_file.is_file() and state_file.read_text(encoding="utf-8") == serialized:
            return
        jar = MozillaCookieJar()
        for item in cookies:
            expiry = item.get("expires", -1)
            jar.set_cookie(Cookie(0, item["name"], item["value"], None, False, item["domain"],
                                  item["domain"].startswith("."), item["domain"].startswith("."),
                                  item.get("path", "/"), True, item.get("secure", False),
                                  int(expiry) if expiry > 0 else None, expiry <= 0, None, None,
                                  {"HttpOnly": None} if item.get("httpOnly") else {}, False))
        # Publish the state last: its timestamp also invalidates discovery caches.
        for name, writer in (
            ("cookies.txt", lambda path: jar.save(path, ignore_discard=True, ignore_expires=True)),
            ("state.json", lambda path: Path(path).write_text(serialized, encoding="utf-8")),
        ):
            fd, temporary = tempfile.mkstemp(dir=directory, prefix=".session-")
            os.close(fd)
            try:
                writer(temporary)
                os.chmod(temporary, 0o600)
                os.replace(temporary, directory / name)
            finally:
                Path(temporary).unlink(missing_ok=True)

    def start(self, owner: int, platform: str, *, switch: bool = False, query: str = "", video_id: str = "") -> dict:
        self.directory(owner, platform)
        if video_id and (platform != "douyin" or not video_id.isdigit() or len(video_id) > 24 or switch or query):
            raise ValueError("Video nguồn cần kiểm tra không hợp lệ.")
        if os.name == "posix" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise ValueError("Máy chạy web chưa có màn hình để mở đăng nhập. Mở web từ desktop của máy này hoặc cấu hình file cookie nguồn trong .env.")
        key = (owner, platform)
        with self._lock:
            login = self._logins.get(key)
            if login and login["thread"].is_alive():
                if switch and not login.get("slot"):
                    raise ValueError("Đóng cửa sổ nguồn hiện tại trước khi đổi tài khoản. Phiên đang dùng được giữ lại.")
                if (query or video_id) and login.get("slot"):
                    raise ValueError("Hoàn tất đổi tài khoản trước khi xác minh tìm kiếm.")
                if query or video_id:
                    login["query"] = query[:200]
                    login["video_id"] = video_id
                    login["navigate"].set()
                login["focus"].set()
                return self.status(owner, platform)
            if sum(item["thread"].is_alive() for item in self._logins.values()) >= 3:
                raise ValueError("Đang có cửa sổ nguồn. Hoàn tất hoặc đóng cửa sổ đó trước.")
            login = {"status": "opening", "message": "Đang mở cửa sổ nguồn trên máy chạy web…",
                     "slot": uuid.uuid4().hex if switch else None,
                     "query": query[:200],
                     "video_id": video_id,
                     **{name: threading.Event() for name in ("finish", "cancel", "focus", "reload", "navigate")}}
            thread = threading.Thread(target=self._login, args=(owner, platform, login), daemon=True)
            login["thread"] = thread
            self._logins[key] = login
            thread.start()
        return self.status(owner, platform)

    def control(self, owner: int, platform: str, action: str) -> dict:
        self.directory(owner, platform)
        with self._lock:
            login = self._logins.get((owner, platform))
            if not login or not login["thread"].is_alive():
                raise ValueError("Cửa sổ nguồn đã đóng. Phiên đã lưu vẫn được giữ; bấm Kiểm tra tìm nguồn.")
            login[action].set()
            if action == "finish":
                login.update(status="saving", message="Đang lưu phiên và đóng cửa sổ nguồn…")
        return self.status(owner, platform)

    def finish(self, owner: int, platform: str) -> dict:
        return self.control(owner, platform, "finish")

    def _login(self, owner: int, platform: str, login: dict) -> None:
        slot = login.get("slot")
        if not slot:
            self._login_window(owner, platform, login)
            return
        with self._lock:
            lock = self._browser_locks.setdefault((owner, platform), threading.RLock())
        if not lock.acquire(blocking=False):
            with self._lock:
                login.update(status="error", message="Nguồn đang được kiểm tra. Phiên cũ được giữ; thử đổi tài khoản sau khi kiểm tra xong.")
            return
        try:
            with self._slot_scope(owner, platform, slot):
                self._login_window(owner, platform, login)
                pending = self.state_path(owner, platform)
                authenticated = pending.is_file() and self._auth_cookie_present(json.loads(pending.read_text()), platform)
            if authenticated:
                self._activate(owner, platform, slot)
                text = "Đã ghi nhớ tài khoản nguồn mới. Các lần mở sau sẽ dùng tài khoản này."
            else:
                text = "Chưa nhận được đăng nhập tài khoản mới. Vẫn giữ phiên nguồn trước đó."
            with self._lock:
                login.update(status="saved" if self.signature(owner, platform) else "closed", message=text)
        except Exception:
            _logger.info("Source account switch did not finish platform=%s", platform)
            with self._lock:
                login.update(status="error", message="Chưa đổi được tài khoản nguồn; phiên trước đó được giữ lại.")
        finally:
            lock.release()

    def _login_window(self, owner: int, platform: str, login: dict) -> None:
        navigation_error = False
        def update(**values):
            with self._lock:
                login.update(values)
        try:
            from playwright.sync_api import sync_playwright, Error as BrowserError
            from universal_video_ai.reup.discovery import SourceDiscovery
            from urllib.parse import urlparse
            with sync_playwright() as driver:
                with self.browser_context(driver, owner, platform, headless=False) as context:
                    snapshotting = False
                    last_snapshot = 0.0
                    captured = {}
                    last_collection = 0.0
                    last_video_collection = 0.0
                    def collect_video():
                        nonlocal last_video_collection
                        identity = login.get("video_id", "")
                        if not identity or time.monotonic() - last_video_collection < 1.5:
                            return
                        last_video_collection = time.monotonic()
                        from universal_video_ai.downloader.douyin import DouyinDownloader
                        try:
                            if DouyinDownloader()._extract_video_id(page.url) != identity or SourceDiscovery.verification_visible(page):
                                return
                            payload = page.evaluate("() => [window._ROUTER_DATA, window.__INITIAL_STATE__, window.RENDER_DATA].filter(x => x && typeof x === 'object')")
                            self.save_media(owner, payload, user_agent=page.evaluate("() => navigator.userAgent"))
                            if self.read_media(owner, identity):
                                update(message="Đã đọc được thông tin phát của video này. Đóng cửa sổ rồi bấm Thử lại ngay trên thẻ video; không cần xuất cookie.")
                        except BrowserError:
                            pass
                    def collect_search(force=False):
                        nonlocal last_collection
                        query = login.get("query", "")
                        if not query or (not force and time.monotonic() - last_collection < 2):
                            return
                        last_collection = time.monotonic()
                        try:
                            if not SourceDiscovery.matches_search_url(platform, page.url, query):
                                return
                            if SourceDiscovery.verification_visible(page):
                                update(message="Trang tìm kiếm yêu cầu xác minh. Hoàn tất trực tiếp trong cửa sổ này; kết quả video sẽ tự được đọc và lưu.")
                                return
                            for candidate in SourceDiscovery.page_metadata(platform, page):
                                if candidate.title.strip():
                                    captured[candidate.video_id] = candidate.to_dict()
                            if captured:
                                self.save_search(owner, platform, query, list(captured.values()))
                                update(message=f"Đã đọc {min(20, len(captured))} video từ trang tìm kiếm. Đóng cửa sổ hoặc bấm Hoàn tất để dùng kết quả trong Reup Agent.")
                        except BrowserError:
                            pass
                    def checkpoint(force=False):
                        nonlocal snapshotting, last_snapshot
                        if snapshotting or (not force and time.monotonic() - last_snapshot < 1):
                            return
                        snapshotting = True
                        try:
                            # storage_state() may create temporary visible tabs
                            # for previously visited origins. During interactive
                            # login, read only cookies; Chromium's persistent
                            # profile already retains localStorage and IndexedDB.
                            state = {"cookies": context.cookies(), "origins": []}
                            previous = self.state_path(owner, platform)
                            if previous.is_file():
                                try:
                                    state["origins"] = json.loads(previous.read_text()).get("origins", [])
                                except (OSError, ValueError, TypeError):
                                    pass
                            if state.get("cookies") or state.get("origins"):
                                self.save(owner, platform, state)
                                if not navigation_error and not login.get("query") and not login.get("video_id"):
                                    text = ("Đã nhận và tự động lưu phiên đăng nhập. Bạn có thể đóng cửa sổ nguồn; Agent sẽ kiểm tra truy cập." if self._auth_cookie_present(state, platform)
                                            else "Phiên truy cập đang được tự động lưu. Đăng nhập nếu nền tảng yêu cầu; có thể đóng cửa sổ sau khi hoàn tất.")
                                    update(message=text)
                        except BrowserError:
                            pass  # The most recent checkpoint and profile remain.
                        finally:
                            last_snapshot = time.monotonic()
                            snapshotting = False
                    def watch_page(page):
                        page.on("close", lambda *_: checkpoint(True))
                        if login.get("query"):
                            page.on("domcontentloaded", lambda *_: collect_search(True))
                    context.on("page", watch_page)
                    def on_response(response):
                        nonlocal navigation_error
                        host = urlparse(response.url).hostname or ""
                        if host != f"{platform}.com" and not host.endswith(f".{platform}.com"):
                            return
                        if response.request.resource_type == "document":
                            if response.status in {502, 503, 504}:
                                navigation_error = True
                                update(message=f"Trang nguồn đang lỗi HTTP {response.status}. Phiên đã lưu vẫn được giữ; bấm Tải lại cửa sổ nguồn, không cần đăng nhập lại.")
                            elif response.status < 400:
                                navigation_error = False
                        video_id = login.get("video_id", "")
                        if video_id and platform == "douyin" and "/aweme/" in urlparse(response.url).path:
                            try:
                                from universal_video_ai.downloader.douyin import douyin_media_items
                                payload = response.json()
                                if any(item["id"] == video_id for item in douyin_media_items(payload)) and not SourceDiscovery.verification_visible(page):
                                    self.save_media(owner, payload, user_agent=page.evaluate("() => navigator.userAgent"))
                                    update(message="Đã đọc được thông tin phát của video này. Đóng cửa sổ rồi bấm Thử lại trên thẻ video.")
                            except (BrowserError, ValueError):
                                pass
                        query = login.get("query", "")
                        if query and "/search/" in urlparse(response.url).path:
                            try:
                                if not SourceDiscovery.matches_search_url(platform, page.url, query):
                                    return
                                params = parse_qs(urlparse(response.url).query)
                                if any(params.get(key) and params[key][0] != query for key in ("q", "keyword", "search_keyword")):
                                    return
                                payload = response.json()
                                found = SourceDiscovery.public_metadata(platform, payload)
                                for candidate in found:
                                    if candidate.title.strip():
                                        captured[candidate.video_id] = candidate.to_dict()
                                if found and not SourceDiscovery.verification_visible(page):
                                    self.save_search(owner, platform, query, list(captured.values()))
                                    if platform == "douyin":
                                        self.save_media(owner, payload, user_agent=page.evaluate("() => navigator.userAgent"))
                            except (BrowserError, ValueError):
                                pass
                    context.on("response", on_response)
                    for existing in context.pages:
                        watch_page(existing)
                    page = context.pages[0] if context.pages else context.new_page()
                    def navigate(reload=False):
                        nonlocal navigation_error
                        try:
                            query = login.get("query", "")
                            video_id = login.get("video_id", "")
                            target = f"https://www.douyin.com/video/{video_id}" if video_id else SourceDiscovery.search_url(platform, query) if query else SOURCE_HOME[platform]
                            from universal_video_ai.downloader.douyin import DouyinDownloader
                            same_page = DouyinDownloader()._extract_video_id(page.url) == video_id if video_id else not query or SourceDiscovery.matches_search_url(platform, page.url, query)
                            if reload and same_page:
                                page.reload(wait_until="commit", timeout=15000)
                            else:
                                page.goto(target, wait_until="commit", timeout=15000)
                                if query:
                                    update(message="Hoàn tất yêu cầu của nền tảng trong cửa sổ tìm kiếm này. Kết quả video sẽ tự được đọc khi trang hiển thị danh sách.")
                                elif video_id:
                                    update(message="Kiểm tra video có phát trong cửa sổ này và hoàn tất xác minh nếu được yêu cầu. Ứng dụng sẽ đọc thông tin phát khi nền tảng cho phép.")
                        except BrowserError:
                            if not page.is_closed():
                                navigation_error = True
                                update(message="Trang nguồn tải chậm hoặc mất kết nối. Cửa sổ và phiên được giữ; có thể bấm Tải lại cửa sổ nguồn.")
                    navigate()
                    update(status="waiting")
                    checkpoint(True)
                    deadline = time.monotonic() + 900
                    while not login["cancel"].is_set() and time.monotonic() < deadline:
                        if login["finish"].is_set():
                            collect_search(True)
                            collect_video()
                            checkpoint(True)
                            break
                        pages = [item for item in context.pages if not item.is_closed()]
                        if not pages:
                            break
                        if page.is_closed():
                            page = pages[0]
                        if login["navigate"].is_set():
                            login["navigate"].clear()
                            captured.clear()
                            navigate()
                        if login["reload"].is_set():
                            login["reload"].clear()
                            navigate(reload=True)
                        if login["focus"].is_set():
                            login["focus"].clear()
                            page.bring_to_front()
                        checkpoint()
                        collect_search()
                        collect_video()
                        try:
                            page.wait_for_timeout(250)
                        except BrowserError:
                            break
                    checkpoint(True)
            saved = bool(self.signature(owner, platform))
            video_id = login.get("video_id", "")
            if video_id:
                text = ("Đã lưu thông tin phát. Bấm Thử lại trên thẻ video để tải bằng phiên hiện tại." if self.read_media(owner, video_id) else
                        "Phiên được giữ. Nền tảng chưa trả thông tin phát cho video này; kiểm tra khả năng xem trên trang nguồn.")
                update(status="saved" if saved else "closed", message=text)
            else:
                update(status="saved" if saved else "closed", message=("Cửa sổ đã đóng, phiên được giữ. Đang sẵn sàng kiểm tra tìm nguồn." if saved
                   else "Cửa sổ đã đóng trước khi có phiên để lưu. Chưa xác nhận đăng nhập nguồn."))
        except Exception as error:
            _logger.info("Source window ended platform=%s error_type=%s", platform, type(error).__name__)
            saved = bool(self.signature(owner, platform))
            update(status="saved" if saved else "error", message=("Cửa sổ nguồn đã đóng; phiên được tự động lưu và giữ lại. Bấm Kiểm tra tìm nguồn." if saved
                   else str(error) if isinstance(error, ValueError) else "Chưa mở hoặc lưu được phiên nguồn. Kiểm tra trình duyệt rồi thử lại."))

    def close(self) -> None:
        with self._lock:
            for login in self._logins.values():
                login["cancel"].set()
