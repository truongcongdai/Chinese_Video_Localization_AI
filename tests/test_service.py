from pathlib import Path
from unittest.mock import Mock, patch

from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.downloader.platform import Platform
from universal_video_ai.downloader.service import DownloadService


def test_download_service_selects_downloader_without_network(tmp_path: Path) -> None:
    """Unit tests must never download a public video during test collection."""
    url = "https://youtu.be/example"
    expected = DownloadResult(
        success=True,
        platform=Platform.YOUTUBE,
        original_url=url,
        final_url=url,
        video_path=tmp_path / "video.mp4",
        title="Example",
    )
    downloader = Mock()
    downloader.download.return_value = expected
    service = DownloadService(use_cache=False)

    with patch(
        "universal_video_ai.downloader.service.DownloaderFactory.create",
        return_value=downloader,
    ) as create, patch(
        "universal_video_ai.downloader.service.validate_video_file",
        return_value=(True, "ok"),
    ):
        result = service.download(url, tmp_path)

    create.assert_called_once_with(Platform.YOUTUBE)
    downloader.download.assert_called_once_with(url=url, output_dir=tmp_path)
    assert result == expected
