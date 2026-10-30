import json
from types import SimpleNamespace

import pytest
import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

from universal_video_ai.downloader.bilibili import (
    BilibiliDownloader, BilibiliMetadataError, CompatibleBilibiliIE,
)
from universal_video_ai.web.app import _is_non_retryable_job_error


@pytest.fixture
def extractor(monkeypatch):
    ydl = yt_dlp.YoutubeDL({'quiet': True, 'noplaylist': True})
    ie = CompatibleBilibiliIE(ydl)
    calls = []
    video = {'bvid': 'BV13V99YGEFh', 'title': '舞蹈测评', 'aid': 123,
             'cid': 111, 'owner': {'name': 'test uploader', 'mid': 12},
             'pages': [{'cid': 111, 'part': 'first'}, {'cid': 222, 'part': 'second'}],
             'stat': {'like': 14, 'view': 100}, 'desc': 'description'}
    monkeypatch.setattr(ie, '_download_webpage_handle', lambda url, *a, **kw: ('<html></html>', SimpleNamespace(url=url)))
    monkeypatch.setattr(ie, '_sign_wbi', lambda query, *a: query)
    def api(url, *args, **kwargs):
        calls.append((url, kwargs))
        if url.endswith('/view/detail'):
            return {'code': 0, 'data': {'View': video, 'Tags': [{'tag_name': '舞蹈'}]}}
        if url.endswith('/pagelist'):
            return {'code': 0, 'data': video['pages']}
        pytest.fail('Unexpected network request: ' + url)
    monkeypatch.setattr(ie, '_download_json', api)
    def playback(bvid, cid, **kw):
        calls.append(('playback', {'bvid': bvid, 'cid': cid}))
        return {'timelength': 40821, 'dash': {
            'video': [{'id': 80, 'baseUrl': 'https://media.example/video.m4s', 'codecs': 'avc1.64001f', 'width': 1920, 'height': 1080}],
            'audio': [{'id': 30280, 'baseUrl': 'https://media.example/audio.m4s', 'codecs': 'mp4a.40.2'}]}}
    monkeypatch.setattr(ie, '_download_playinfo', playback)
    monkeypatch.setattr(ie, '_get_chapters', lambda *a: [])
    monkeypatch.setattr(ie, 'extract_subtitles', lambda *a: {})
    return ie, calls, video


def test_new_page_uses_api_metadata_and_keeps_part_audio_and_title(extractor):
    ie, calls, _ = extractor
    info = ie._real_extract('https://www.bilibili.com/video/BV13V99YGEFh?p=2')
    assert info['id'] == 'BV13V99YGEFh_p2'
    assert info['title'] == '舞蹈测评 p02 second'
    assert info['uploader'] == 'test uploader'
    assert info['tags'] == ['舞蹈']
    assert info['like_count'] == 14 and info['duration'] == 40.821
    assert any(fmt.get('vcodec') == 'none' for fmt in info['formats'])
    assert ('playback', {'bvid': 'BV13V99YGEFh', 'cid': 222}) in calls
    details = [kw for url, kw in calls if url.endswith('/view/detail')]
    assert len(details) == 1 and details[0]['query']['bvid'] == 'BV13V99YGEFh'


def test_existing_initial_state_does_not_request_api_fallback(extractor, monkeypatch):
    ie, calls, video = extractor
    markup = 'window.__INITIAL_STATE__=' + json.dumps({'videoData': video, 'upData': video['owner']})
    monkeypatch.setattr(ie, '_download_webpage_handle', lambda url, *a, **kw: (markup, SimpleNamespace(url=url)))
    info = ie._real_extract('https://www.bilibili.com/video/BV13V99YGEFh?p=1')
    assert info['id'] == 'BV13V99YGEFh_p1'
    assert not any(url.endswith('/view/detail') for url, _ in calls)


def test_localized_url_selects_same_part(extractor):
    ie, _, _ = extractor
    info = ie._real_extract('https://www.bilibili.com/zh-Hans/video/BV13V99YGEFh?p=2')
    assert info['id'] == 'BV13V99YGEFh_p2'


def test_rate_limit_page_does_not_use_metadata_to_skip_access_check(extractor, monkeypatch):
    ie, calls, _ = extractor
    monkeypatch.setattr(ie, '_download_webpage_handle', lambda url, *a, **kw: ('window._riskdata_={"v_voucher":"required"}', SimpleNamespace(url=url)))
    with pytest.raises(ExtractorError, match='rate limit'):
        ie._real_extract('https://www.bilibili.com/video/BV13V99YGEFh')
    assert not calls


def test_api_denial_remains_login_required(extractor, monkeypatch):
    ie, _, _ = extractor
    monkeypatch.setattr(ie, '_download_json', lambda *a, **kw: {'code': -403, 'message': 'forbidden'})
    with pytest.raises(ExtractorError, match='only available for registered users'):
        ie._real_extract('https://www.bilibili.com/video/BV13V99YGEFh')


def test_source_error_stops_job_retries_but_timeout_still_retries():
    class FailedYDL:
        def add_info_extractor(self, extractor):
            assert isinstance(extractor, CompatibleBilibiliIE)
        def extract_info(self, *args, **kwargs):
            raise DownloadError('Unable to extract initial state')
    with pytest.raises(BilibiliMetadataError) as failure:
        BilibiliDownloader().extract_info(FailedYDL(), 'https://www.bilibili.com/video/BV13V99YGEFh')
    assert _is_non_retryable_job_error(failure.value)
    try:
        raise RuntimeError('wrapped pipeline') from failure.value
    except RuntimeError as wrapped:
        assert _is_non_retryable_job_error(wrapped)
    assert not _is_non_retryable_job_error(TimeoutError('network timeout'))
