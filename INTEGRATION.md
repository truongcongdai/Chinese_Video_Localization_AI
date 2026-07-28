# Douyin Downloader Integration Guide

## Overview

This document describes the comprehensive refactoring of the Douyin video downloader to handle WAF protection, rate limiting, and full share text input without requiring cookies or login.

## Changes Made

### 1. New File: `douyin_urls.py`

**Location:** `src/universal_video_ai/downloader/douyin_urls.py`

**Purpose:** Extract Douyin URLs from full share text that users typically copy from the Douyin app.

**Key Functions:**
- `extract_douyin_urls(raw_text: str) -> List[str]`: Extracts all Douyin URLs from raw text input
- `is_douyin_url(url: str) -> bool`: Checks if a URL matches Douyin patterns
- `normalize_douyin_url(url: str) -> str`: Normalizes URLs to consistent format

**Supported URL Formats:**
- Short URLs: `https://v.douyin.com/AbCdEf/`
- Full URLs: `https://www.douyin.com/video/12345678901234567890/`
- Note URLs: `https://www.douyin.com/note/12345678901234567890/`
- Share URLs: `https://www.iesdouyin.com/share/video/12345678901234567890/`

**Example Usage:**
```python
from universal_video_ai.downloader.douyin_urls import extract_douyin_urls

# Full share text
share_text = "0.07 DhO:/ :4pm 12/09 l@C.HI 一口气看完《泪染寻爹》1-15大合集，团团开始上幼儿园啦 # 二次元 # ai漫剧 # 原创动漫 https://v.douyin.com/vLJ-YnqpUkI/ 复制此链接，打开Dou音搜索，直接观看视频！"
urls = extract_douyin_urls(share_text)
# Returns: ['https://v.douyin.com/vLJ-YnqpUkI/']
```

### 2. Refactored File: `douyin.py`

**Location:** `src/universal_video_ai/downloader/douyin.py`

**Major Changes:**

#### A. Circuit Breaker Pattern
- **Class:** `DouyinCircuitBreaker`
- **Purpose:** Manages adaptive cooldown to prevent IP blacklisting
- **Behavior:**
  - Tracks consecutive WAF hits and successes
  - Exponential backoff: 30s → 90s → 5min → 15min
  - Resets after 5 consecutive successes
  - Blocks requests when cooldown is active

#### B. Persistent Session
- **Function:** `_get_persistent_session()`
- **Purpose:** Single `requests.Session` for all HTTP requests
- **Benefits:**
  - Consistent User-Agent (anonymous Chrome)
  - Connection pooling
  - Cookie persistence (if any)
  - Reduced overhead

#### C. Adaptive Throttling
- **Function:** `_throttle_between_videos()`
- **Purpose:** Delays between video downloads
- **Configuration:**
  - Base delay: 8-18 seconds (randomized)
  - After 8 consecutive videos: 1-3 minute break
  - Prevents rate limiting

#### D. Playwright Fallback
- **Method:** `_download_douyin_playwright()`
- **Purpose:** Headless browser as WAF bypass
- **Configuration:**
  - Persistent context: `local_data/douyin_browser_profile`
  - Headless Chromium
  - Chinese locale
  - 3-second wait for JS execution
- **Trigger:** When HTTP scraping fails

#### E. HTML Validation
- **Function:** `_is_valid_html_response(html: str) -> bool`
- **Purpose:** Check if HTML contains actual video data
- **Markers:** `_ROUTER_DATA`, `__INITIAL_STATE__`, `_SSR_HYDRATED_DATA`, `play_addr`, `videoInfoRes`

#### F. .part File Downloads
- **Method:** `_download_video_from_url()`
- **Purpose:** Atomic download with validation
- **Process:**
  1. Download to `.part` file
  2. Validate file size (>100KB)
  3. Rename to final `.mp4` on success
  4. Cleanup on failure

#### G. Enhanced URL Extraction
- **Method:** `_extract_video_id(url: str)`
- **Improvements:**
  - Supports 16-22 digit video IDs
  - Multiple URL patterns
  - Query parameter extraction

### 3. Modified File: `app.py`

**Location:** `src/universal_video_ai/web/app.py`

**Changes:**

#### A. Import Addition
```python
from universal_video_ai.downloader.douyin_urls import extract_douyin_urls
```

#### B. `/api/jobs` Endpoint
- **Before:** Single job creation with raw URL
- **After:** 
  - Extracts URLs from full share text
  - Creates multiple jobs if multiple URLs found
  - Adjusts credit cost accordingly
  - Returns single job or list of jobs

**Example Response (Multiple URLs):**
```json
{
  "jobs": [
    {"id": "job_1", ...},
    {"id": "job_2", ...}
  ],
  "count": 2
}
```

## Installation Requirements

### Optional: Playwright (for WAF fallback)

If you want to enable the Playwright fallback (recommended for better WAF bypass):

```bash
pip install playwright
playwright install chromium
```

**Note:** Playwright is optional. The downloader will work without it, but will only use HTTP scraping.

## Configuration

### Environment Variables

No new environment variables required. The downloader uses sensible defaults:

- **Circuit Breaker Cooldowns:** 30s, 90s, 5min, 15min
- **Video Delay:** 8-18 seconds
- **Consecutive Video Limit:** 8 before long break
- **Playwright Profile:** `local_data/douyin_browser_profile`

### Adjusting Throttling

Edit `src/universal_video_ai/downloader/douyin.py`:

```python
_MIN_VIDEO_DELAY = (8, 18)  # (min_seconds, max_seconds)
_MAX_CONSECUTIVE_VIDEOS = 8  # Videos before long break
```

## Usage

### 1. Direct URL Input

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://v.douyin.com/vLJ-YnqpUkI/",
    "target_language": "vi"
  }'
```

### 2. Full Share Text Input

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "0.07 DhO:/ :4pm 12/09 l@C.HI 一口气看完《泪染寻爹》1-15大合集，团团开始上幼儿园啦 # 二次元 # ai漫剧 # 原创动漫 https://v.douyin.com/vLJ-YnqpUkI/ 复制此链接，打开Dou音搜索，直接观看视频！",
    "target_language": "vi"
  }'
```

### 3. Multiple URLs in One Request

If the input contains multiple Douyin URLs, multiple jobs will be created:

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "Check these videos: https://v.douyin.com/abc123/ and https://v.douyin.com/xyz789/",
    "target_language": "vi"
  }'
```

## Troubleshooting

### Issue: "Playwright not installed, skipping fallback"

**Solution:** Install Playwright:
```bash
pip install playwright
playwright install chromium
```

### Issue: "Circuit breaker active, waiting Xs before request"

**Cause:** Multiple WAF hits triggered cooldown

**Solution:** Wait for cooldown to expire, or increase cooldown steps in code

### Issue: "Cannot find video data in HTML"

**Cause:** WAF challenge or HTML structure change

**Solution:** Check debug HTML in `local_data/temp/output/web_jobs/*/debug_*.html`

### Issue: "Downloaded file too small"

**Cause:** Video URL returned error or incomplete download

**Solution:** Check network connectivity, retry after cooldown

## Architecture

### Download Flow

```
User Input (URL/Share Text)
    ↓
extract_douyin_urls() → Extract URLs
    ↓
normalize_douyin_url() → Clean URL
    ↓
DouyinDownloader.download()
    ↓
[Global Lock]
    ↓
_throttle_between_videos() → Wait if needed
    ↓
_resolve_short_url() → Get full URL
    ↓
_extract_video_id() → Get video ID
    ↓
_download_douyin_scraping()
    ↓
┌─────────────────────────┐
│ Try HTTP Scraping       │
│ _fetch_share_html_http()│
│   ↓                      │
│ _is_valid_html_response()│
│   ↓                      │
│ _parse_and_download...   │
└─────────────────────────┘
    ↓ (if failed)
┌─────────────────────────┐
│ Try Playwright Fallback  │
│ _download_douyin_...     │
│   ↓                      │
│ Launch Chromium          │
│   ↓                      │
│ Get HTML                 │
│   ↓                      │
│ _parse_and_download...   │
└─────────────────────────┘
    ↓
_download_video_from_url()
    ↓
Download to .part file
    ↓
Validate size (>100KB)
    ↓
Rename to .mp4
    ↓
Return DownloadResult
```

### Circuit Breaker Flow

```
Request
    ↓
wait_until_ready()
    ↓
[If blocked] → Wait for cooldown
    ↓
Execute Request
    ↓
[Success] → report_success()
    ↓
[After 5 successes] → Reset cooldown
    ↓
[WAF Hit] → report_waf()
    ↓
Increase cooldown step
    ↓
Block future requests
```

## Performance Considerations

### Throughput

- **Single Video:** ~8-18 seconds between downloads
- **Batch:** After 8 videos, 1-3 minute break
- **WAF Scenario:** Cooldown increases exponentially

### Resource Usage

- **HTTP Only:** Minimal (requests.Session)
- **With Playwright:** ~200-300MB RAM per Chromium instance
- **Profile Storage:** ~50-100MB in `local_data/douyin_browser_profile`

## Security

### Anonymous Operation

- No cookies required
- No login required
- No API keys required
- Consistent anonymous User-Agent

### Rate Limiting

- Circuit breaker prevents IP blacklisting
- Adaptive delays avoid detection
- Serialized requests prevent bursts

## Future Enhancements

### Potential Improvements

1. **Smart Music Matching:** Implement `smart_match` and `manual` background music strategies
2. **Proxy Support:** Add proxy rotation for different IPs
3. **Cookie Support:** Optional cookie file for authenticated requests
4. **Parallel Downloads:** Limited parallel processing with per-IP limits
5. **Cache:** Cache video IDs to avoid re-downloading

### Monitoring

Consider adding:
- Success/failure rate metrics
- Average download time tracking
- WAF hit frequency monitoring
- Circuit breaker state logging

## Support

For issues or questions:

1. Check logs for detailed error messages
2. Review debug HTML files in `local_data/temp/output/web_jobs/*/debug_*.html`
3. Verify Playwright installation if fallback is needed
4. Check network connectivity to Douyin servers

## Summary

The refactored Douyin downloader now:

✅ Handles full share text input
✅ Uses persistent session for consistency
✅ Implements circuit breaker for WAF protection
✅ Provides Playwright fallback for robustness
✅ Downloads with .part files for safety
✅ Throttles requests to avoid rate limiting
✅ Works without cookies or login
✅ Creates multiple jobs from multiple URLs
✅ Validates downloads before finalizing
