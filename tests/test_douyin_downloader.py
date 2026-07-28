from universal_video_ai.downloader.douyin import DouyinDownloader, _safe_video_filename
from universal_video_ai.downloader.platform import Platform
from universal_video_ai.downloader.platform_detector import PlatformDetector
from universal_video_ai.web.app import _extract_first_video_url


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
