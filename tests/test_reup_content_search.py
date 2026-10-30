"""Regressions for content queries, Chinese discovery and unrelated title hits."""
from pathlib import Path

import pytest

from universal_video_ai.reup.discovery import Candidate, SourceDiscovery
from universal_video_ai.reup.keywords import KeywordPlanner, content_keywords, content_relevance, normalize_keyword
from universal_video_ai.web.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'keywords.sqlite3')


def beauty_direction():
    return {'id': 'beauty', 'category_id': '24', 'content_keywords': ['fashion', 'dance', 'beauty'],
            'evidence': [{'title': 'A stylish outfit', 'description': 'Fashion and dance', 'tags': ['fashion', 'dance']}]}


def translated_report(store):
    report = {'directions': [beauty_direction()], 'warnings': []}
    KeywordPlanner(store, translate=lambda terms, language: ['时尚', '舞蹈', '美妆']).enrich(
        report, {'platforms': ['douyin', 'bilibili', 'tiktok', 'youtube']})
    return report


def test_title_is_never_a_query_when_tags_are_missing():
    title = 'Mesmerizing Beauty – A Goddess with Unstoppable Charm! part 65'
    terms = content_keywords([{'snippet': {'title': title, 'description': 'Fashion outfits, street dance, makeup looks'}}])
    assert title not in terms
    assert any('fashion' in term for term in terms)
    assert any('dance' in term for term in terms)
    assert not any('65' in term or 'goddess' in term for term in terms)
    assert normalize_keyword('GáiXinh ThờiTrang khoa học') == 'gái xinh thời trang khoa học'


def test_blank_model_localizes_known_chinese_queries_without_network(store):
    calls = []
    def translate(terms, language):
        calls.append((terms, language))
        return ['时尚', '舞蹈', '美妆']
    planner = KeywordPlanner(store, translate=translate)
    for _ in range(2):
        report = {'directions': [beauty_direction()], 'warnings': []}
        planner.enrich(report, {'platforms': ['douyin', 'bilibili', 'tiktok']}, client=None)
        queries = report['directions'][0]['search_queries']
        assert queries['douyin'] == ['穿搭', '舞蹈']
        assert queries['bilibili'] == queries['douyin']
        assert all(len(query.split()) >= 2 for query in queries['tiktok'])
    assert calls == []  # Known content anchors work without a translation request.


def test_every_query_keeps_a_content_axis_instead_of_pairing_appearance_synonyms(store):
    direction = beauty_direction()
    direction['content_keywords'] = ['hot girls', 'pretty girls', 'fashion', 'dance']
    report = {'directions': [direction], 'warnings': []}
    KeywordPlanner(store, translate=lambda terms, language: ['美女', '漂亮女孩', '时尚', '舞蹈']).enrich(report, {'platforms': ['douyin', 'youtube']})
    for query in direction['search_queries']['youtube']:
        assert 'fashion' in query or 'dance' in query
    for query in direction['search_queries']['douyin']:
        assert '穿搭' in query or '舞蹈' in query


def test_untranslated_terms_are_not_sent_to_chinese_platforms(store, monkeypatch):
    direction = beauty_direction()
    direction['content_keywords'] = ['ceramic glazing', 'sourdough baking']
    report = {'directions': [direction], 'warnings': []}
    KeywordPlanner(store, translate=lambda terms, language: terms).enrich(report, {'platforms': ['douyin', 'bilibili']})
    direction = report['directions'][0]
    assert direction['search_queries'] == {'douyin': [], 'bilibili': []}
    discovery = SourceDiscovery()
    monkeypatch.setattr(discovery, 'search', lambda *args: pytest.fail('Untranslated query must not be searched'))
    result = discovery.scan({'platforms': ['douyin', 'bilibili'], **direction})
    assert all(row['status'] == 'translation_required' for row in result['platform_status'])


def test_famous_music_title_is_rejected_and_chinese_content_is_selected(store, monkeypatch):
    direction = translated_report(store)['directions'][0]
    bad = Candidate('https://www.youtube.com/watch?v=abcdefghijk', 'youtube', 'abcdefghijk',
                    'Justin Bieber - Beauty And A Beat (Official Music Video) ft. Nicki Minaj', view_count=1376924931)
    good = Candidate('https://www.douyin.com/video/123456789', 'douyin', '123456789', '时尚穿搭与舞蹈', view_count=12000)
    discovery = SourceDiscovery()
    monkeypatch.setattr(discovery, 'search', lambda platform, query: [bad] if platform == 'youtube' else [good])
    result = discovery.scan({'platforms': ['youtube', 'douyin'], **direction})
    assert [row['video_id'] for row in result['candidates']] == ['123456789']
    assert result['candidates'][0]['relevance']['matched_concepts'] == ['fashion', 'dance']


def test_single_generic_word_or_appearance_synonyms_do_not_prove_content_relevance(store):
    direction = translated_report(store)['directions'][0]
    assert not content_relevance({'title': 'A song about beauty'}, direction['content_profile'])['accepted']
    profile = {'concepts': [{'term': 'hot girls', 'aliases': ['美女']}, {'term': 'beautiful girls', 'aliases': ['漂亮']},
                           {'term': 'dance', 'aliases': ['舞蹈']}],
               'minimum_concepts': 2, 'context_terms': ['dance']}
    assert not content_relevance({'title': '漂亮美女'}, profile)['accepted']
    assert content_relevance({'title': '美女舞蹈'}, profile)['accepted']


def test_blocked_source_is_attempted_once_and_reported_once(store, monkeypatch):
    direction = translated_report(store)['directions'][0]
    calls = []
    def search(platform, query):
        calls.append((platform, query))
        raise ValueError('requires verification')
    discovery = SourceDiscovery()
    monkeypatch.setattr(discovery, 'search', search)
    result = discovery.scan({'platforms': ['douyin'], **direction})
    assert len(calls) == 1 and len(result['warnings']) == 1
    assert result['platform_status'][0]['status'] == 'verification_required'
    assert result['platform_status'][0]['queries'][0] == '穿搭'


def test_bilibili_http_412_falls_back_to_public_browser_search(monkeypatch):
    discovery = SourceDiscovery()
    monkeypatch.setattr(discovery, '_indexed_search', lambda *args: (_ for _ in ()).throw(ValueError('HTTP 412')))
    calls = []
    expected = [Candidate('https://www.bilibili.com/video/BV1test123?p=1', 'bilibili', 'BV1test123:p1', '时尚舞蹈')]
    def browse(platform, url, limit):
        calls.append((platform, url))
        return expected
    monkeypatch.setattr(discovery, '_public_browse', browse)
    assert discovery.search('bilibili', '时尚 舞蹈') == expected
    assert calls[0][0] == 'bilibili' and 'search.bilibili.com/all?keyword=' in calls[0][1]


def test_bilibili_empty_index_also_uses_its_public_search_page(monkeypatch):
    discovery = SourceDiscovery()
    monkeypatch.setattr(discovery, '_indexed_search', lambda *args: [])
    calls = []
    monkeypatch.setattr(discovery, '_public_browse', lambda *args: calls.append(args) or [])
    assert discovery.search('bilibili', '时尚 舞蹈') == []
    assert calls and calls[0][0] == 'bilibili'


def test_bilibili_search_page_metadata_parses_author_strings_and_highlighted_titles():
    payload = {'data': {'result': [{'bvid': 'BV1test123', 'title': '<em class="keyword">时尚</em>舞蹈',
        'author': 'creator', 'mid': 12, 'play': 5000, 'pubdate': 1793059200, 'tag': '时尚,舞蹈'}]}}
    result = SourceDiscovery.public_metadata('bilibili', payload)
    assert len(result) == 1 and result[0].view_count == 5000
    assert '<em' not in result[0].title and '时尚' in result[0].tags


def test_discovery_diagnostics_are_owner_scoped_and_survive_repository_restart(store):
    from universal_video_ai.reup.service import ReupRepository
    owner = store.create_user('owner', 'hash')
    other = store.create_user('other', 'hash')
    report = {'platform_status': [{'platform': 'douyin', 'status': 'verification_required', 'queries': ['时尚 舞蹈']}]}
    ReupRepository(store).save_discovery(owner, report)
    assert ReupRepository(store).discovery(owner) == report
    assert ReupRepository(store).discovery(other) == {}


def test_failed_candidate_does_not_block_a_better_source_with_daily_limit_one(store):
    from universal_video_ai.reup.service import ReupRepository
    from universal_video_ai.web.reup_router import ReupProfileBody
    owner = store.create_user('owner', 'hash')
    repo = ReupRepository(store)
    config = ReupProfileBody(topic='fashion', daily_limit=1).model_dump()
    first = repo.enqueue(owner, {'url': 'https://youtu.be/abcdefghijk'}, config)
    assert repo.enqueue(owner, {'url': 'https://www.douyin.com/video/123456789'}, config) is None
    repo.update(owner, first['id'], status='failed')
    assert repo.enqueue(owner, {'url': 'https://www.douyin.com/video/123456789'}, config) is not None


def test_idiomatic_chinese_and_context_anchor_do_not_require_demographic_tag_overlap(store):
    class LocalModel:
        available = True
        def generate_json(self, prompt):
            return {'directions': [{'id': 'beauty', 'keywords': ['hot girls', 'dance', 'fashion'],
                                    'keywords_zh': ['热女孩', '舞蹈', '时尚']}]}
    report = {'directions': [beauty_direction()], 'warnings': []}
    KeywordPlanner(store).enrich(report, {'platforms': ['douyin', 'youtube']}, LocalModel())
    direction = report['directions'][0]
    assert direction['search_queries']['douyin'] == ['舞蹈', '穿搭']
    assert not any('热女孩' in query for query in direction['search_queries']['douyin'])
    assert content_relevance({'title': 'Summer outfit ideas'}, direction['content_profile'])['accepted']
    assert content_relevance({'title': '街舞教学'}, direction['content_profile'])['accepted']
    assert content_relevance({'title': 'Dancing practice'}, direction['content_profile'])['accepted']
    assert not content_relevance({'title': 'Pretty girls'}, direction['content_profile'])['accepted']
    assert not content_relevance({'title': 'Beauty And A Beat (Official Music Video)'}, direction['content_profile'])['accepted']


@pytest.mark.parametrize('action,status', [('retry', 'failed'), ('process', 'discovered'), ('resume', 'paused'), ('approve', 'ready')])
def test_obsolete_automatic_candidate_cannot_be_restarted_or_published(store, tmp_path, action, status):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from universal_video_ai.web.auth import get_current_user_id
    from universal_video_ai.web.reup_router import ReupProfileBody, install_reup_routes
    owner = store.create_user('owner', 'hash')
    app = FastAPI()
    async def submit(*args):
        pytest.fail('Obsolete source must never reach processing')
    runtime = install_reup_routes(app, store, submit, tmp_path)
    config = ReupProfileBody(strategy_mode='auto', platforms=['youtube']).model_dump()
    config['source_evidence'] = {'title': 'Beauty And A Beat', 'view_count': 1000000000}
    row = runtime.repository.enqueue(owner, {'url': 'https://youtu.be/abcdefghijk'}, config)
    runtime.repository.update(owner, row['id'], status=status)
    app.dependency_overrides[get_current_user_id] = lambda: owner
    response = TestClient(app).post(f"/api/reup-agent/runs/{row['id']}/{action}")
    assert response.status_code == 409
    assert runtime.repository.get(owner, row['id'])['status'] == status


def test_model_cannot_remove_grounded_content_and_leave_every_source_without_queries(store):
    class AppearanceOnlyModel:
        available = True
        def generate_json(self, prompt):
            return {'directions': [{'id': 'selected', 'keywords': ['hot girls', 'trendy', 'china girl'],
                                    'keywords_zh': ['美女', '潮流', '中国女孩']}]}
    direction = {'id': 'selected', 'category_id': '24', 'content_keywords': ['hot girls', 'trendy', 'china girl'],
                 'evidence': [{'title': 'Fashion and dance trends', 'tags': ['HotGirls', 'Beauty', 'Fashion', 'Dance', 'Trendy', 'china girl']}]}
    report = {'directions': [direction], 'warnings': []}
    def no_network(*args):
        pytest.fail('Grounded known anchors must not need a translation request')
    KeywordPlanner(store, translate=no_network).enrich(report, {'platforms': ['douyin', 'tiktok', 'bilibili']}, AppearanceOnlyModel())
    assert direction['id'] == 'selected'
    assert {'fashion', 'dance'} <= set(direction['content_keywords'])
    assert {'舞蹈', '穿搭'} <= set(direction['search_queries']['douyin'])
    assert all(direction['search_queries'][platform] for platform in ('douyin', 'tiktok', 'bilibili'))
    assert set(direction['keyword_status'].values()) == {'ready'}
    assert content_relevance({'title': '街舞教学'}, direction['content_profile'])['accepted']
    assert not content_relevance({'title': 'Beauty And A Beat Official Music Video'}, direction['content_profile'])['accepted']


def test_tag_order_does_not_starve_fashion_and_dance_with_equal_frequency_appearance_tags():
    tags = ['HotGirls', 'Beauty', 'Fashion', 'Dance', 'Luxury', 'Elegant', 'Trendy', 'china girl', 'beautiful girl']
    first = content_keywords([{'tags': tags}])
    second = content_keywords([{'tags': list(reversed(tags))}])
    assert first == second
    assert {'fashion', 'dance'} <= set(first)


def test_unknown_keyword_translations_are_cached_and_reused(store):
    calls = []
    def translate(terms, language):
        calls.append((terms, language))
        return ['陶瓷釉', '酸面包烘焙']
    planner = KeywordPlanner(store, translate=translate)
    for _ in range(2):
        report = {'directions': [{'id': 'craft', 'content_keywords': ['ceramic glazing', 'sourdough baking'], 'evidence': []}], 'warnings': []}
        planner.enrich(report, {'platforms': ['douyin']})
        assert report['directions'][0]['search_queries']['douyin']
    assert len(calls) == 1


def test_empty_query_is_not_counted_as_a_search_or_as_a_login_failure(monkeypatch):
    discovery = SourceDiscovery()
    monkeypatch.setattr(discovery, 'search', lambda *a, **kw: pytest.fail('No request may be sent without a query'))
    result = discovery.scan({'platforms': ['douyin', 'tiktok', 'bilibili'], 'search_queries': {p: [] for p in ('douyin', 'tiktok', 'bilibili')},
                             'content_profile': {'concepts': [{'term': 'hot girls', 'aliases': ['hot girls']}], 'context_terms': []}})
    assert all(row['status'] == 'no_query' for row in result['platform_status'])
    assert not any(row['status'] in {'searched', 'translation_required', 'login_required'} for row in result['platform_status'])
    assert not result['candidates']


def test_appearance_only_direction_without_grounded_content_never_invents_a_topic(store):
    direction = {'id': 'generic', 'content_keywords': ['hot girls', 'trendy', 'china girl'], 'evidence': []}
    report = {'directions': [direction], 'warnings': []}
    KeywordPlanner(store).enrich(report, {'platforms': ['douyin', 'tiktok']}, local_only=True)
    assert all(not queries for queries in direction['search_queries'].values())
    assert set(direction['keyword_status'].values()) == {'no_context'}
    assert not content_relevance({'title': 'trendy hot girls china girl'}, direction['content_profile'])['accepted']


def test_local_cache_repair_preserves_existing_valid_translations_for_unknown_concepts(store):
    direction = {'id': 'craft', 'content_keywords': ['ceramic glazing', 'sourdough baking'], 'evidence': [],
                 'content_profile': {'concepts': [{'term': 'ceramic glazing', 'aliases': ['ceramic glazing', '陶瓷釉']},
                                                   {'term': 'sourdough baking', 'aliases': ['sourdough baking', '酸面包烘焙']}]}}
    report = {'directions': [direction], 'warnings': []}
    planner = KeywordPlanner(store, translate=lambda *args: pytest.fail('Repair cannot call external translation'))
    planner.enrich(report, {'platforms': ['douyin']}, local_only=True)
    assert direction['search_queries']['douyin'] == ['陶瓷釉', '酸面包烘焙']
