from pathlib import Path

from universal_video_ai.downloader.service import DownloadService


service = DownloadService()

result = service.download(

    "https://youtu.be/dQw4w9WgXcQ",

    Path("temp"),

)

print(result.title)

print(result.platform)