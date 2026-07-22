from universal_video_ai.downloader.douyin import _safe_video_filename


def test_safe_video_filename_limits_utf8_bytes_and_preserves_extension():
    filename = _safe_video_filename("一口气看完面点" * 50, "7664475888125343161")

    assert len(filename.encode("utf-8")) <= 200
    assert filename.endswith("_7664475888125343161.mp4")
    assert "\ufffd" not in filename


def test_safe_video_filename_removes_path_separators():
    filename = _safe_video_filename("folder/name\\video", "123")

    assert "/" not in filename
    assert "\\" not in filename
