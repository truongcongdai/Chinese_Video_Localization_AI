"""Publishing side effects, automation cancellation and budgets are durable."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
import requests

from tests.test_cp8_publishing import setup_publish, StubPublisher, submit
from tests.test_cp10_facebook_automation import facebook_setup, StubMeta
from tests.test_cp11_autonomous_operator import setup_operator, autonomous_config
from universal_video_ai.channel_agent.automation import AutomationOrchestrator, AutomationError
from universal_video_ai.channel_agent.autonomous_operator import AutonomousOperatorError
from universal_video_ai.channel_agent.production_publishing import ProductionPublishingService, ProductionPublishingError
from universal_video_ai.channel_agent.facebook_publishing import FacebookPublishingError
from universal_video_ai.channel_agent.coordination import reserve_provider_call
from universal_video_ai.web.store import Store


def test_one_publishing_execution_owns_remote_side_effect(tmp_path):
    store, owner, _, item, render, _ = setup_publish(tmp_path)
    entered, release = threading.Event(), threading.Event()
    class Publisher(StubPublisher):
        def begin(self, user, job):
            entered.set()
            assert release.wait(3)
            return super().begin(user, job)
    publisher = Publisher()
    first = ProductionPublishingService(store, publisher=publisher)
    second = ProductionPublishingService(Store(store.db_path), publisher=publisher)
    job = submit(first, owner, item, render, dry_run=False, confirm_publish=True)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(first.run, owner, item, job['id'])
        try:
            assert entered.wait(2)
            with pytest.raises(ProductionPublishingError, match='already owned'):
                second.run(owner, item, job['id'])
        finally:
            release.set()
        assert future.result(timeout=3)['external_video_id'] == 'video-123'
    assert publisher.begins == publisher.uploads == 1


def test_facebook_destination_change_stops_before_upload(tmp_path):
    store, owner, _, item, render, _, service = facebook_setup(tmp_path)
    job = service.submit(owner, item, render_job_id=render, dry_run=False, confirm_publish=True)
    account = store.get_social_account(owner, 'facebook')
    store.upsert_social_account(owner, 'facebook', 'fake-token', None, None, 'Other page', 'page-456', account['scopes'])
    with pytest.raises(FacebookPublishingError, match='Page changed'):
        service.run(owner, item, job['id'])
    assert service.publisher.uploads == 0


def test_ambiguous_facebook_upload_is_never_blindly_retried(tmp_path):
    class AcceptedButTimedOut(StubMeta):
        def upload(self, user_id, job):
            self.uploads += 1
            raise requests.Timeout('accepted remotely; response lost')
    publisher = AcceptedButTimedOut()
    store, owner, _, item, render, _, service = facebook_setup(tmp_path, publisher=publisher)
    job = service.submit(owner, item, render_job_id=render, dry_run=False, confirm_publish=True)
    with pytest.raises(requests.Timeout):
        service.run(owner, item, job['id'])
    assert service.get(owner, item, job['id'])['status'] == 'outcome_unknown'
    with pytest.raises(FacebookPublishingError, match='outcome is unknown'):
        service.run(owner, item, job['id'])
    assert publisher.uploads == 1


@pytest.mark.parametrize('cancel', [False, True])
def test_automation_second_advance_cannot_recover_a_live_stage(tmp_path, cancel):
    store = Store(tmp_path / 'automation.sqlite3')
    owner = store.create_user('owner', 'hash')
    entered, release = threading.Event(), threading.Event()
    calls = []
    def research(*args):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return {'trend_scan_id': 'completed'}
    first = AutomationOrchestrator(store, handlers={'research': research})
    second = AutomationOrchestrator(Store(store.db_path), handlers={'research': research})
    run, _ = first.start(owner, mode='MANUAL_STEP', configuration={'research_refresh': True})
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(first.advance, owner, run['id'])
        try:
            assert entered.wait(2)
            if cancel:
                second.cancel(owner, run['id'])
            else:
                with pytest.raises(AutomationError, match='already owned'):
                    second.advance(owner, run['id'])
        finally:
            release.set()
        result = future.result(timeout=3)
    assert calls == [1]
    if cancel:
        assert result['status'] == second.get(owner, run['id'])['status'] == 'cancelled'


def test_failed_stage_usage_survives_retry_and_restart(tmp_path):
    store = Store(tmp_path / 'budget.sqlite3')
    owner = store.create_user('owner', 'hash')
    calls = []
    def fail(*args):
        calls.append(1)
        raise OSError('transient transport failure')
    service = AutomationOrchestrator(store, handlers={'research': fail})
    run, _ = service.start(owner, mode='MANUAL_STEP', configuration={
        'research_refresh': True, 'budgets': {'max_external_api_calls': 1}})
    assert service.advance(owner, run['id'])['status'] == 'failed'
    restarted = AutomationOrchestrator(Store(store.db_path), handlers={'research': fail})
    assert restarted.retry(owner, run['id'])['status'] == 'failed'
    assert calls == [1]
    assert restarted.get(owner, run['id'])['refs']['provider_usage']['provider_calls'] == 1


def test_budget_is_enforced_at_each_leaf_operation(tmp_path):
    store = Store(tmp_path / 'fanout.sqlite3')
    owner = store.create_user('owner', 'hash')
    calls = []
    def assets(*args):
        for _ in range(4):
            reserve_provider_call('llm_calls')
            calls.append(1)
        return {'assets': []}
    service = AutomationOrchestrator(store, handlers={'assets': assets})
    run, _ = service.start(owner, mode='MANUAL_STEP', configuration={'budgets': {'max_llm_calls': 1}})
    from universal_video_ai.channel_agent.coordination import ExecutionLease
    with ExecutionLease(store.db_path, owner, f"automation:{run['id']}") as lease:
        with pytest.raises(RuntimeError, match='budget exceeded'):
            service._execute_stage(owner, 'assets', run['configuration'], {}, assets, lease=lease, run_id=run['id'])
    assert calls == [1]


def test_cycle_admission_is_atomic_and_incomplete_link_is_repaired(tmp_path, monkeypatch):
    store, owner, _, operator = setup_operator(tmp_path)
    config = operator.save_config(owner, 'channel', configuration=autonomous_config())
    original = operator.orchestrator.start
    monkeypatch.setattr(operator.orchestrator, 'start', lambda *a, **kw: (_ for _ in ()).throw(OSError('simulated crash')))
    with pytest.raises(OSError):
        operator.start_cycle(owner, config['id'], idempotency_key='durable', advance=False)
    monkeypatch.setattr(operator.orchestrator, 'start', original)
    repaired, created = operator.start_cycle(owner, config['id'], idempotency_key='durable', advance=False)
    assert not created and repaired['automation_run_id'] is not None
    with pytest.raises(AutonomousOperatorError, match='concurrent'):
        operator.start_cycle(owner, config['id'], idempotency_key='another', advance=False)


def test_network_retry_consumes_budget_before_another_http_request(tmp_path, monkeypatch):
    from universal_video_ai.channel_agent.coordination import ExecutionLease
    from universal_video_ai.network import post_with_retry
    store = Store(tmp_path / 'transport-budget.sqlite3')
    owner = store.create_user('owner', 'hash')
    attempts = []
    def post(*args, **kwargs):
        attempts.append(1)
        raise requests.Timeout('transient')
    monkeypatch.setattr(requests, 'post', post)
    monkeypatch.setattr('universal_video_ai.network.time.sleep', lambda _: None)
    def assets(*args):
        reserve_provider_call('llm_calls')
        post_with_retry('https://provider.invalid/generate')
    service = AutomationOrchestrator(store)
    run, _ = service.start(owner, mode='MANUAL_STEP', configuration={'budgets': {'max_llm_calls': 1}})
    with ExecutionLease(store.db_path, owner, f"automation:{run['id']}") as lease:
        with pytest.raises(RuntimeError, match='budget exceeded'):
            service._execute_stage(owner, 'assets', run['configuration'], {}, assets, lease=lease, run_id=run['id'])
    assert attempts == [1]
