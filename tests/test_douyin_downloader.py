import requests
import pytest

from universal_video_ai.downloader.douyin import DouyinDownloader, _safe_video_filename
from universal_video_ai.downloader.platform import Platform
from universal_video_ai.downloader.platform_detector import PlatformDetector
from universal_video_ai.downloader.ytdlp_downloader import (
    _cookiefile_for,
    _cookies_from_browser_for,
)
from universal_video_ai.web.app import _extract_first_video_url, _is_non_retryable_job_error


def test_safe_video_filename_limits_utf8_bytes_and_preserves_extension():
    filename = _safe_video_filename("一口气看完面点" * 50, "7664475888125343161")

    assert len(filename.encode("utf-8")) <= 200
    assert filename.endswith("_7664475888125343161.mp4")
    assert "\ufffd" not in filename


def test_safe_video_filename_removes_path_separators():
    filename = _safe_video_filename("folder/name\\video", "123")

    assert "/" not in filename
    assert "\\" not in filename


def test_extract_video_id_from_full_video_url():
    assert (
        DouyinDownloader()._extract_video_id(
            "https://www.douyin.com/video/7666342861993938219"
        )
        == "7666342861993938219"
    )


def test_extract_video_id_from_share_video_url():
    assert (
        DouyinDownloader()._extract_video_id(
            "https://www.iesdouyin.com/share/video/7661876018903083641/?region=CN"
        )
        == "7661876018903083641"
    )


def test_extract_video_id_from_note_url():
    assert (
        DouyinDownloader()._extract_video_id(
            "https://www.douyin.com/note/7659736267420192052"
        )
        == "7659736267420192052"
    )


def test_extract_video_id_from_modal_id_query():
    assert (
        DouyinDownloader()._extract_video_id(
            "https://www.douyin.com/user/MS4wLjABAAAA?modal_id=7666342861993938219"
        )
        == "7666342861993938219"
    )


def test_extract_video_id_from_aweme_id_query():
    assert (
        DouyinDownloader()._extract_video_id(
            "https://www.douyin.com/discover?aweme_id=7661876018903083641"
        )
        == "7661876018903083641"
    )


def test_rejects_nested_placeholder_as_douyin_video_url():
    placeholder = (
        "https://aweme.snssdk.com/aweme/v1/play/"
        "?video_id=https%3A%2F%2Flf3-static.bytednsdoc.com%2Fobj%2Feden-cn%2Fnulog"
    )

    assert DouyinDownloader._is_plausible_video_url(placeholder) is False
    assert (
        DouyinDownloader._is_plausible_video_url(
            "https://aweme.snssdk.com/aweme/v1/play/?video_id=v0300abc&ratio=720p"
        )
        is True
    )


def test_ytdlp_discovers_managed_cookie_file(tmp_path, monkeypatch):
    cookie_file = tmp_path / "douyin.com.cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.delenv("DOUYIN_COOKIES_FILE", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_FILE", raising=False)

    class ManagedCookies:
        def find_cookie_for_domain(self, domain):
            assert domain == "douyin.com"
            return cookie_file

    monkeypatch.setattr(
        "universal_video_ai.downloader.ytdlp_downloader.CookieManager",
        ManagedCookies,
    )

    assert _cookiefile_for(Platform.DOUYIN) == str(cookie_file.resolve())


def test_browser_cookie_loading_is_explicit_and_supports_profile(monkeypatch):
    monkeypatch.delenv("YTDLP_COOKIES_FROM_BROWSER", raising=False)
    monkeypatch.setenv("DOUYIN_COOKIES_FROM_BROWSER", "chrome:Profile 1")

    assert _cookies_from_browser_for(Platform.DOUYIN) == ("chrome", "Profile 1")


def test_fresh_cookie_error_is_actionable_and_not_hidden(tmp_path, monkeypatch):
    downloader = DouyinDownloader()
    monkeypatch.setattr(downloader, "_resolve_short_url", lambda url: url)
    monkeypatch.setattr(downloader, "_download_douyin_scraping", lambda video_id, output_dir: None)
    monkeypatch.setattr(
        downloader._ytdlp_fallback,
        "download",
        lambda url, output_dir: (_ for _ in ()).throw(RuntimeError("Fresh cookies are needed")),
    )

    with pytest.raises(RuntimeError, match="Douyin yêu cầu cookie mới"):
        downloader.download("https://www.douyin.com/video/7638999539815756520", tmp_path)

    assert _is_non_retryable_job_error(RuntimeError("Fresh cookies are needed")) is True


def test_missing_secretstorage_is_not_retried():
    error = RuntimeError(
        "secretstorage not available as the `secretstorage` module is not installed"
    )

    assert _is_non_retryable_job_error(error) is True


def test_extract_video_url_from_douyin_share_text():
    share_text = (
        "3.07 04/08 :9pm t@r.EH qRk:/ 《狂飙》深度解析78：笑着倒醋，转身开枪！"
        "https://v.douyin.com/KGHqCtz6ZAI/ 复制此链接，打开Dou音搜索，直接观看视频！"
    )

    assert _extract_first_video_url(share_text) == "https://v.douyin.com/KGHqCtz6ZAI/"


def test_platform_detector_accepts_douyin_share_text():
    share_text = (
        "《狂飙》深度解析78 # 抖音精选 "
        "https://v.douyin.com/KGHqCtz6ZAI/ 复制此链接"
    )

    assert PlatformDetector().detect(share_text) == Platform.DOUYIN


class _StreamingResponse:
    def __init__(self, status_code, headers, chunks):
        self.status_code = status_code
        self.headers = headers
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self._chunks


def test_direct_stream_resumes_existing_partial_file(tmp_path, monkeypatch):
    downloader = DouyinDownloader()
    output_path = tmp_path / "video.mp4"
    part_path = tmp_path / "video.mp4.part"
    part_path.write_bytes(b"abc")
    requests_seen = []

    def fake_get(url, **kwargs):
        requests_seen.append((url, kwargs))
        return _StreamingResponse(206, {"Content-Range": "bytes 3-5/6"}, [b"def"])

    monkeypatch.setattr("universal_video_ai.downloader.douyin.requests.get", fake_get)

    size = downloader._download_stream_with_resume("https://cdn/video", output_path, {"User-Agent": "test"})

    assert size == 6
    assert output_path.read_bytes() == b"abcdef"
    assert not part_path.exists()
    assert requests_seen[0][1]["headers"]["Range"] == "bytes=3-"


def test_direct_stream_retries_interruption_from_received_byte(tmp_path, monkeypatch):
    downloader = DouyinDownloader()
    output_path = tmp_path / "video.mp4"
    calls = []

    class InterruptedResponse(_StreamingResponse):
        def iter_content(self, chunk_size):
            del chunk_size
            yield b"abc"
            raise requests.exceptions.ChunkedEncodingError("connection lost")

    responses = [
        InterruptedResponse(200, {"Content-Length": "6"}, []),
        _StreamingResponse(206, {"Content-Range": "bytes 3-5/6"}, [b"def"]),
    ]

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    monkeypatch.setattr("universal_video_ai.downloader.douyin.requests.get", fake_get)
    monkeypatch.setattr("universal_video_ai.downloader.douyin.time.sleep", lambda delay: None)

    size = downloader._download_stream_with_resume("https://cdn/video", output_path, {})

    assert size == 6
    assert output_path.read_bytes() == b"abcdef"
    assert calls[1][1]["headers"]["Range"] == "bytes=3-"
