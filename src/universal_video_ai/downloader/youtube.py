from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any, Optional
import requests
from pydantic import BaseModel

from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader, _downloaded_filepath


class YoutubeDownloader(YTDLPDownloader):

    def __init__(self):
        super().__init__(Platform.YOUTUBE)


# ------------------------------------------------------------------ Pydantic Models for Web API --

class YouTubeDownloadBody(BaseModel):
    """Request body for YouTube download operations."""
    url: str
    format: str = "best"  # best, bestvideo, bestaudio, mp4, mp3
    quality: str = "best"  # best, 720p, 1080p, etc.


class YouTubeMetadataResponse(BaseModel):
    """Response model for YouTube metadata."""
    title: str
    description: str
    uploader: str
    duration: int
    view_count: int
    upload_date: str
    thumbnail: str
    tags: List[str]


# ------------------------------------------------------------------ YouTube Tools for Web API --

class YouTubeTools:
    """YouTube tools for web API - download video, audio, subtitles, thumbnails, metadata."""
    
    def __init__(self, user_id: int, base_dir: Path = Path("downloads")):
        self.user_id = user_id
        self.base_dir = base_dir
    
    def _get_downloads_dir(self, subfolder: str) -> Path:
        """Get downloads directory for specific subfolder."""
        return self.base_dir / str(self.user_id) / "youtube" / subfolder
    
    def download_video(self, url: str, format: str = "best") -> Dict[str, Any]:
        """Download video from YouTube."""
        import yt_dlp
        
        downloads_dir = self._get_downloads_dir("videos")
        downloads_dir.mkdir(parents=True, exist_ok=True)
        
        requested_format = 'bv*+ba/b' if format == 'best' else format
        ydl_opts = {
            'format': requested_format,
            'merge_output_format': 'mp4',
            'outtmpl': str(downloads_dir / '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_file = _downloaded_filepath(ydl, info)
                
                if downloaded_file.exists():
                    return {
                        "ok": True,
                        "filename": downloaded_file.name,
                        "path": str(downloaded_file),
                        "title": info.get('title'),
                        "duration": info.get('duration'),
                    }
                else:
                    raise RuntimeError("Download failed")
        except Exception as exc:
            raise RuntimeError(f"Download failed: {exc}") from exc
    
    def extract_audio(self, url: str) -> Dict[str, Any]:
        """Extract audio from YouTube video."""
        import yt_dlp
        
        downloads_dir = self._get_downloads_dir("audio")
        downloads_dir.mkdir(parents=True, exist_ok=True)
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': str(downloads_dir / '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info).replace('.webm', '.mp3').replace('.m4a', '.mp3')
                downloaded_file = Path(filename)
                
                if downloaded_file.exists():
                    return {
                        "ok": True,
                        "filename": downloaded_file.name,
                        "path": str(downloads_dir),
                        "title": info.get('title'),
                        "duration": info.get('duration'),
                    }
                else:
                    raise RuntimeError("Audio extraction failed")
        except Exception as exc:
            raise RuntimeError(f"Audio extraction failed: {exc}") from exc
    
    def download_subtitles(self, url: str) -> Dict[str, Any]:
        """Download subtitles from YouTube video."""
        import yt_dlp
        
        downloads_dir = self._get_downloads_dir("subtitles")
        downloads_dir.mkdir(parents=True, exist_ok=True)
        
        ydl_opts = {
            'writesubtitles': True,
            'subtitleslangs': ['vi', 'en'],
            'subtitlesformat': 'srt',
            'outtmpl': str(downloads_dir / '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                subtitles = []
                for lang in ['vi', 'en']:
                    if lang in info.get('subtitles', {}):
                        sub_data = info['subtitles'][lang]
                        for sub in sub_data:
                            if sub.get('ext') == 'srt':
                                subtitles.append({
                                    "language": lang,
                                    "url": sub.get('url'),
                                })
                
                return {
                    "ok": True,
                    "subtitles": subtitles,
                    "title": info.get('title'),
                }
        except Exception as exc:
            raise RuntimeError(f"Subtitle download failed: {exc}") from exc
    
    def download_thumbnail(self, url: str) -> Dict[str, Any]:
        """Download thumbnail from YouTube video."""
        import yt_dlp
        
        downloads_dir = self._get_downloads_dir("thumbnails")
        downloads_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                thumbnail_url = info.get('thumbnail')
                
                if not thumbnail_url:
                    raise RuntimeError("Thumbnail not found")
                
                # Download thumbnail
                response = requests.get(thumbnail_url, timeout=30)
                response.raise_for_status()
                
                thumbnail_path = downloads_dir / f"{info.get('title', 'thumbnail')}.jpg"
                with open(thumbnail_path, 'wb') as f:
                    f.write(response.content)
                
                return {
                    "ok": True,
                    "filename": thumbnail_path.name,
                    "path": str(thumbnail_path.parent),
                    "url": thumbnail_url,
                }
        except Exception as exc:
            raise RuntimeError(f"Thumbnail download failed: {exc}") from exc
    
    def get_metadata(self, url: str) -> Dict[str, Any]:
        """Extract metadata from YouTube video."""
        import yt_dlp
        
        try:
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                
                return {
                    "title": info.get('title', ''),
                    "description": info.get('description', ''),
                    "uploader": info.get('uploader', ''),
                    "duration": info.get('duration', 0),
                    "view_count": info.get('view_count', 0),
                    "upload_date": info.get('upload_date', ''),
                    "thumbnail": info.get('thumbnail', ''),
                    "tags": info.get('tags', []),
                }
        except Exception as exc:
            raise RuntimeError(f"Metadata extraction failed: {exc}") from exc
