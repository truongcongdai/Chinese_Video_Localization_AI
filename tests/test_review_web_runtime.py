"""Cross-request ownership, file boundaries and money gates use real temp Store."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from universal_video_ai.web import app as web
from universal_video_ai.web.auth import COOKIE_NAME, create_session_cookie_value
from universal_video_ai.web.store import Store, ExecutionLeaseLost, InsufficientCreditsError
from universal_video_ai.web.identity_oauth import IdentityResult


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('WEB_SESSION_SECRET', 'public-test-secret-for-offline-regressions')
    store = Store(tmp_path / 'web.sqlite3')
    owner = store.create_user('owner', 'hash', credits=5)
    victim = store.create_user('victim', 'hash', email='victim@example.test')
    monkeypatch.setattr(web, 'store', store)
    monkeypatch.setattr(web, '_OUTPUT_BASE_DIR', tmp_path / 'output')
    return store, owner, victim


def request(owner=None, nonce=None):
    cookies = []
    if owner is not None:
        cookies.append(f'{COOKIE_NAME}={create_session_cookie_value(owner)}')
    if nonce:
        cookies.append(f'{web._IDENTITY_NONCE_COOKIE}={nonce}')
    return Request({'type': 'http', 'scheme': 'http', 'server': ('testserver', 80),
                    'path': '/', 'query_string': b'', 'headers': [(b'cookie', '; '.join(cookies).encode())]})


def test_empty_or_external_pack_never_serves_working_directory(runtime, tmp_path):
    store, owner, _ = runtime
    job = store.create_job(owner, 'https://example.test/video', 'vi')
    for raw in (None, '', str(tmp_path)):
        store.update_job(job.id, publishing_pack_path=raw)
        for handler, arguments in [(web.get_publishing_asset, ('marker.txt', job.id, owner)),
                                   (web.download_publishing_pack, (job.id, owner))]:
            with pytest.raises(HTTPException) as caught:
                handler(*arguments)
            assert caught.value.status_code == 404


def test_identity_requires_initiating_browser_before_provider_exchange(runtime, monkeypatch):
    store, owner, _ = runtime
    state = 'state-sentinel'
    store.create_identity_oauth_state(state, 'google')
    client = SimpleNamespace(exchange_code=Mock())
    monkeypatch.setattr(web.identity_oauth, 'get_identity_oauth_client', lambda _: client)
    response = web.identity_callback('google', request(owner), code='fake', state=state)
    assert 'state_invalid' in response.headers['location']
    client.exchange_code.assert_not_called()


@pytest.mark.parametrize('email,expected', [('new@example.test', 'registration_closed'),
                                           ('victim@example.test', 'account_link_required')])
def test_identity_never_bypasses_registration_or_auto_links_email(runtime, monkeypatch, email, expected):
    store, owner, victim = runtime
    monkeypatch.setattr(web, 'OPEN_REGISTRATION', False)
    state = 'state-sentinel'
    store.create_identity_oauth_state(state, 'google')
    client = SimpleNamespace(exchange_code=lambda *args: IdentityResult('remote-id', email, 'Person', True))
    monkeypatch.setattr(web.identity_oauth, 'get_identity_oauth_client', lambda _: client)
    response = web.identity_callback('google', request(owner, nonce=state), code='fake', state=state)
    assert expected in response.headers['location']
    assert 'set-cookie' not in response.headers
    assert store.get_user_by_oauth('google', 'remote-id') is None


def test_shared_social_consent_cannot_attach_victims_credentials(runtime, monkeypatch):
    store, owner, victim = runtime
    store.create_oauth_state('shared-link', owner, 'youtube')
    exchange = Mock(side_effect=AssertionError('foreign browser must not exchange the code'))
    monkeypatch.setattr(web, '_social_oauth_client', lambda _: SimpleNamespace(exchange_code=exchange))
    response = web.social_callback('youtube', request(victim), code='fake', state='shared-link')
    assert response.status_code == 400
    exchange.assert_not_called()
    assert store.get_social_account(owner, 'youtube') is None
    assert store.get_pending_social_connection('shared-link', owner) is None


def test_qr_handoff_requires_owner_login_before_provider_consent(runtime, monkeypatch):
    store, owner, victim = runtime
    store.create_oauth_state('handoff', owner, 'youtube')
    authorize = Mock(return_value='https://provider.invalid/consent')
    monkeypatch.setattr(web, '_social_oauth_client', lambda _: SimpleNamespace(authorize_url=authorize))
    logged_out = web.authorize_owned_social_connection('youtube', 'handoff', request())
    assert 'social_authorize=handoff' in logged_out.headers['location']
    with pytest.raises(HTTPException) as caught:
        web.authorize_owned_social_connection('youtube', 'handoff', request(victim))
    assert caught.value.status_code == 403
    authorize.assert_not_called()
    legitimate = web.authorize_owned_social_connection('youtube', 'handoff', request(owner))
    assert legitimate.headers['location'] == 'https://provider.invalid/consent'


def test_live_job_survives_other_worker_recovery_and_expired_writer_is_fenced(runtime):
    store, owner, _ = runtime
    job = store.create_job(owner, 'https://example.test', 'vi', charge_credits=1, lease_owner='worker-a')
    assert store.claim_job_execution(job.id, 'worker-a:run', reserved_owner='worker-a')
    other = Store(store.db_path)
    assert other.fail_interrupted_jobs() == 0
    with store._connect() as conn:
        conn.execute('UPDATE web_execution_leases SET expires_at=0 WHERE resource_id=?', (job.id,))
    assert other.fail_interrupted_jobs() == 1
    assert other.fail_interrupted_jobs() == 0
    assert store.get_user_by_id(owner)['credits'] == 5
    with store.execution_scope('job', job.id, 'worker-a:run'), pytest.raises(ExecutionLeaseLost):
        store.update_job(job.id, status='done')


def test_upload_admission_reserves_once_and_never_runs_negative(runtime):
    store, owner, _ = runtime
    store.set_credits(owner, 1)
    store.create_job(owner, 'upload:first', 'vi', charge_credits=1)
    with pytest.raises(InsufficientCreditsError):
        store.create_job(owner, 'upload:second', 'vi', charge_credits=1)
    assert store.get_user_by_id(owner)['credits'] == 0
    assert len(store.list_jobs_for_user(owner)) == 1


def test_topup_claim_adds_credit_once_across_connections(runtime):
    from concurrent.futures import ThreadPoolExecutor
    store, owner, _ = runtime
    topup = store.create_top_up_request(owner, credits=50, amount_vnd=50000, payment_method='offline', note='offline')
    def approve(_):
        return Store(store.db_path).approve_top_up_request(topup)
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(approve, range(2)))
    assert sum(bool(value) for value in outcomes) == 1
    assert store.get_user_by_id(owner)['credits'] == 55


@pytest.mark.parametrize('message', [
    'Dub exceeds source video by 1.000s; shorten the translation',
    'Dub exceeds requested mix duration; extend the timeline',
    'OpenAI TTS input exceeds 4096 characters; split the text',
])
def test_deterministic_audio_errors_do_not_retry_entire_pipeline(message):
    assert web._is_non_retryable_job_error(RuntimeError(message))


def test_thread_owned_job_renews_while_other_workers_recover(runtime):
    import threading
    import time
    store, owner, _ = runtime
    job = store.create_job(owner, 'https://example.test', 'vi')
    assert store.claim_job_execution(job.id, 'slow-thread', reserved_owner='', lease_seconds=0.15)
    entered, finish = threading.Event(), threading.Event()
    errors = []
    def work():
        try:
            with store.background_execution('job', job.id, 'slow-thread', lease_seconds=0.15):
                store.update_job(job.id, status='running')
                entered.set()
                assert finish.wait(3)
                store.update_job(job.id, status='done')
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert entered.wait(1)
        time.sleep(0.35)
        assert Store(store.db_path).fail_interrupted_jobs(lease_seconds=0.15) == 0
    finally:
        finish.set()
        thread.join(3)
    assert not thread.is_alive() and not errors
    assert store.get_job(job.id).status == 'done'


def test_scheduled_upload_keeps_ownership_until_remote_call_returns(runtime, tmp_path, monkeypatch):
    import threading
    import time
    store, owner, _ = runtime
    video = tmp_path / 'final.mp4'
    video.write_bytes(b'fake provider reads an opaque local file')
    job = store.create_job(owner, 'https://example.test', 'vi')
    store.update_job(job.id, status='done', final_video_path=str(video))
    post_id = store.create_scheduled_post(owner, job.id, ['youtube'], 'Title', '', [], time.time() - 1)
    monkeypatch.setattr(web, '_WEB_LEASE_SECONDS', 0.15)
    row = store.claim_due_scheduled_posts(time.time(), owner='post-owner', lease_seconds=0.15)[0]
    entered, finish = threading.Event(), threading.Event()
    def upload(*args, **kwargs):
        entered.set()
        assert finish.wait(3)
        return SimpleNamespace(platform='YouTube', success=True, message='fake upload', remote_url=None)
    monkeypatch.setattr(web, 'get_uploader', lambda _: SimpleNamespace(upload=upload))
    monkeypatch.setattr(web, '_resolve_publish_credentials', lambda *a: ('fake-token', 'fake-channel'))
    monkeypatch.setattr(web, '_social_publish_video_path', lambda _: video)
    thread = threading.Thread(target=web._run_scheduled_post, args=(row, 'post-owner'))
    thread.start()
    try:
        assert entered.wait(1)
        time.sleep(0.35)
        Store(store.db_path).recover_processing_scheduled_posts(lease_seconds=0.15)
        assert store.list_scheduled_posts(owner)[0]['status'] == 'processing'
        assert store.claim_due_scheduled_posts(time.time()) == []
    finally:
        finish.set()
        thread.join(3)
    assert not thread.is_alive()
    assert store.list_scheduled_posts(owner)[0]['status'] == 'done'
