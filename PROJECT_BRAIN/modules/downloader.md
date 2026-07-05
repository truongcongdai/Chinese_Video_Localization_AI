# DownloadService - Knowledge Base

## Responsibility
Downloads videos from various platforms (YouTube, TikTok, etc.) to local storage.

## Must Never
- Extract audio from video
- Call ffmpeg
- Call speech services
- Process video content
- Modify video files

## Dependencies
- DownloaderFactory
- config/
- logger/
- exceptions/
- models/

## Produces
- DownloadResult (dataclass with video_path, metadata, platform)

## Consumers
- AudioPipeline
- LocalizationService
- TelegramBot

## Thread Safe
YES

## Singleton
NO

## Owner
Download Team

## Stability Level
★★★★★ Stable

## AI Rules
Changing this module requires:
- Architecture Review
- Protocol compliance check
- DownloadResult backward compatibility

## Future
- Support m3u8 streaming
- Support DASH
- Support encrypted streams
- Add retry logic for network failures
