from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader
from yt_dlp.extractor.bilibili import BiliBiliIE, BiliBiliBangumiIE
from yt_dlp.utils import ExtractorError


class BilibiliMetadataError(RuntimeError):
    """The source needs intervention, so repeating the job will not help."""


class CompatibleBilibiliIE(BiliBiliIE):
    """Support pages that moved video metadata from HTML to the details API.

    Keep yt-dlp's formats, part selection, access checks and download logic.
    Supply only the missing initial metadata using Bilibili's public response.
    This local adapter can be removed once the installed extractor supports it.
    """

    _VALID_URL = BiliBiliIE._VALID_URL.replace(
        r'bilibili\.com/', r'bilibili\.com/(?:[a-z]{2}(?:-[A-Za-z]{2,8})?/)?', 1)

    def _real_extract(self, url):
        self._source_url = url
        return super()._real_extract(url)

    def _search_json(self, start_pattern, string, name, video_id, **kwargs):
        result = super()._search_json(start_pattern, string, name, video_id, **kwargs)
        if result or name != 'initial state':
            return result
        risk = super()._search_json(r'\bwindow\._riskdata_\s*=', string, 'risk', video_id, default={})
        if risk.get('v_voucher'):
            return result  # Preserve the upstream rate-limit check.
        match = self._match_valid_url(self._source_url)
        prefix, identity = match.group('prefix', 'id')
        query = {'platform': 'web'}
        if prefix.upper() == 'BV':
            query['bvid'] = 'BV' + identity
        else:
            query['aid'] = identity
        headers = {**self.geo_verification_headers(), 'Referer': self._source_url, 'Origin': 'https://www.bilibili.com'}
        response = self._download_json(
            'https://api.bilibili.com/x/web-interface/wbi/view/detail', video_id,
            query=self._sign_wbi(query, video_id), headers=headers,
            note='Downloading video details for updated Bilibili pages')
        code = response.get('code')
        if code in {-403, -404}:
            return {'error': {'trueCode': code}}
        if code not in {None, 0}:
            raise ExtractorError(f'Bilibili video details unavailable (code {code})', expected=True)
        data = response.get('data') or {}
        video = data.get('View') or {}
        redirect = video.get('redirect_url')
        if redirect and BiliBiliBangumiIE.suitable(redirect):
            return result  # Let the existing extractor follow its Bangumi branch.
        if not video.get('bvid') or not video.get('title'):
            raise ExtractorError('Bilibili video details contain no playable video metadata', expected=True)
        owner = video.get('owner') or {}
        return {'videoData': video, 'upData': owner, 'tags': data.get('Tags') or []}


class BilibiliDownloader(YTDLPDownloader):

    def __init__(self):
        super().__init__(Platform.BILIBILI)

    def get_extra_options(self) -> dict:
        # Resume a short CDN read at the byte range that failed, instead of
        # restarting localization and downloading the whole video again.
        return {'retries': 3, 'fragment_retries': 3, 'socket_timeout': 30,
                'http_chunk_size': 1024 * 1024}

    def extract_info(self, ydl, url: str) -> dict:
        ydl.add_info_extractor(CompatibleBilibiliIE(ydl))
        try:
            return ydl.extract_info(url, download=True, ie_key=CompatibleBilibiliIE.ie_key())
        except Exception as error:
            message = str(error).lower()
            if any(marker in message for marker in (
                'unable to extract initial state', 'video details contain no playable',
                'video details unavailable (code', 'exceeded the rate limit',
                'http error 412', 'http error 403', 'sign in', 'login required',
                'geo-restricted', 'supporter-only', 'only available for registered users',
            )):
                raise BilibiliMetadataError(
                    'Bilibili chưa cho tải video này. Phiên đã lưu được giữ; '
                    'mở nguồn gốc để kiểm tra quyền truy cập hoặc xác minh, rồi thử lại video. '
                    'Ứng dụng dừng tự thử lại vì lỗi nguồn không thay đổi.') from error
            raise
