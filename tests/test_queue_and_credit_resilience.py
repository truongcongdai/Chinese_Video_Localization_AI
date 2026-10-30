"""Admission, leases and financial transactions survive concurrent workers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from unittest.mock import Mock

import pytest

from universal_video_ai.jobs.queue import JobQueue, JobPriority, RedisQueue
from universal_video_ai.jobs.worker import RedisWorker
from universal_video_ai.jobs.service import JobService
from universal_video_ai.jobs.models import JobConfig, JobStatus
from universal_video_ai.database.manager import DatabaseManager
from universal_video_ai.cache.sqlite_cache import SQLiteCache


@pytest.fixture(params=['memory', 'redis'])
def leased_queue(request, monkeypatch):
    import redis
    if request.param == 'memory':
        monkeypatch.setattr(redis, 'from_url', Mock(side_effect=ConnectionError('offline')))
        yield RedisQueue(lease_seconds=0.3)
        return
    binary = shutil.which('redis-server')
    if binary is None:
        pytest.skip('redis-server is required for Lua integration checks')
    with tempfile.TemporaryDirectory(prefix='uvai-redis-') as folder:
        socket = Path(folder) / 'r.sock'
        process = subprocess.Popen([binary, '--port', '0', '--unixsocket', str(socket),
                                    '--save', '', '--appendonly', 'no', '--dir', folder],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if socket.exists() or process.poll() is not None:
                    break
                time.sleep(0.01)
            assert process.poll() is None, 'Redis failed to start'
            client = redis.Redis(unix_socket_path=str(socket), decode_responses=True)
            assert client.ping()
            yield RedisQueue(client=client, lease_seconds=0.3)
        finally:
            process.terminate()
            process.wait(timeout=5)


def test_atomic_claim_and_priority_fifo(leased_queue):
    q = leased_queue
    q.enqueue('low', priority=JobPriority.LOW)
    q.enqueue('z-first-urgent', priority=JobPriority.URGENT)
    q.enqueue('a-second-urgent', priority=JobPriority.URGENT)
    assert not q.enqueue('low')
    assert q.dequeue().job_id == 'z-first-urgent'
    assert q.dequeue().job_id == 'a-second-urgent'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: q.dequeue(), range(2)))
    assert sum(item is not None for item in results) == 1
    assert next(item for item in results if item is not None).job_id == 'low'


def test_expired_claim_reclaimed_and_old_completion_rejected(leased_queue, monkeypatch):
    q = leased_queue
    clock = Mock(return_value=time.time())
    monkeypatch.setattr('universal_video_ai.jobs.queue.time.time', clock)
    q.enqueue('crashed')
    first = q.dequeue()
    assert q.dequeue() is None
    clock.return_value += q.lease_seconds + 1
    recovered = q.dequeue()
    assert recovered.job_id == first.job_id
    assert recovered.attempts == first.attempts + 1
    assert recovered.claim_token != first.claim_token
    assert not q.mark_status(first.job_id, JobStatus.COMPLETED, claim_token=first.claim_token)
    assert not q.requeue(first)
    assert q.mark_status(recovered.job_id, JobStatus.COMPLETED, claim_token=recovered.claim_token)


def test_cancel_cannot_be_overwritten_by_retry_or_completion(leased_queue):
    q = leased_queue
    q.enqueue('cancel')
    envelope = q.dequeue()
    assert q.cancel('cancel')
    assert not q.requeue(envelope)
    assert not q.mark_status('cancel', JobStatus.COMPLETED, claim_token=envelope.claim_token)
    assert q.get_status('cancel') == JobStatus.CANCELLED
    assert q.dequeue() is None


def test_retry_due_time_is_respected(leased_queue, monkeypatch):
    q = leased_queue
    clock = Mock(return_value=time.time())
    monkeypatch.setattr('universal_video_ai.jobs.queue.time.time', clock)
    q.enqueue('retry', priority=JobPriority.URGENT)
    envelope = q.dequeue()
    assert q.requeue(envelope, delay_seconds=5)
    q.enqueue('low', priority=JobPriority.LOW)
    assert q.dequeue().job_id == 'low'
    assert q.dequeue() is None
    clock.return_value += 5
    assert q.dequeue().job_id == 'retry'


def test_worker_heartbeat_preserves_claim(leased_queue):
    q = leased_queue
    q.enqueue('slow')
    envelope = q.dequeue()
    entered, finish = threading.Event(), threading.Event()
    def callback(_):
        entered.set()
        assert finish.wait(3)
    worker = RedisWorker(q, callback)
    thread = threading.Thread(target=worker.process_job, args=(envelope,))
    thread.start()
    try:
        assert entered.wait(1)
        time.sleep(q.lease_seconds * 2)
        assert q.dequeue() is None
    finally:
        finish.set()
        thread.join(3)
    assert not thread.is_alive()
    assert q.get_status('slow') == JobStatus.COMPLETED


def test_service_retries_transient_callback_and_clears_error(tmp_path):
    service = JobService(use_queue=True, max_workers=1)
    job = service.create_job(JobConfig('https://example.test', tmp_path))
    calls = []
    def callback(_):
        calls.append(1)
        if len(calls) < 3:
            raise OSError('transient')
        return tmp_path / 'result.mp4'
    try:
        service.run_job_async(job.job_id, callback)
        done = threading.Event()
        threading.Thread(target=lambda: (service.queue._queue.join(), done.set()), daemon=True).start()
        assert done.wait(3), 'unfinished retry accounting left join blocked'
        result = service.get_job(job.job_id)
        assert result.status == JobStatus.COMPLETED
        assert result.error is None
        assert len(calls) == 3
    finally:
        service.shutdown()


def test_memory_queue_executes_priority_and_fifo(tmp_path):
    queue = JobQueue(max_workers=0)
    queue.start()
    try:
        queue.enqueue('low', lambda _: None, JobPriority.LOW)
        queue.enqueue('z-first', lambda _: None, JobPriority.URGENT)
        queue.enqueue('a-second', lambda _: None, JobPriority.URGENT)
        assert [queue._queue.get_nowait().job_id for _ in range(3)] == ['z-first', 'a-second', 'low']
    finally:
        queue.stop()


def test_credit_deduction_is_atomic_across_connections(tmp_path):
    first = DatabaseManager(tmp_path / 'db.sqlite3')
    first.init_schema()
    first.get_user_credits(1)
    second = DatabaseManager(tmp_path / 'db.sqlite3')
    barrier = threading.Barrier(2)
    def deduct(manager):
        barrier.wait(timeout=2)
        return manager.deduct_credits(1, 2)
    try:
        with ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(deduct, [first, second]))
        assert sorted(outcomes) == [False, True]
        assert first.get_user_credits(1).credits == 1
        assert first.get_user_credits(1).total_used == 2
        assert len(first.get_audit_log(1)) == 1
    finally:
        first._conn.close()
        second._conn.close()


def test_audit_failure_rolls_back_credit_change(tmp_path):
    manager = DatabaseManager(tmp_path / 'db.sqlite3')
    manager.init_schema()
    manager.get_user_credits(1)
    manager._conn.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
    manager._conn.commit()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            manager.deduct_credits(1, 1)
        assert manager.get_user_credits(1).credits == 3
        assert manager.get_user_credits(1).total_used == 0
    finally:
        manager._conn.close()


def test_sqlite_cache_closes_every_connection(tmp_path, monkeypatch):
    opened = []
    original = SQLiteCache._connect
    def connect(self):
        connection = original(self)
        opened.append(connection)
        return connection
    monkeypatch.setattr(SQLiteCache, '_connect', connect)
    cache = SQLiteCache(tmp_path / 'cache.sqlite3')
    for index in range(20):
        assert cache.set(str(index), {'text': 'cached'})
        assert cache.get(str(index)) == {'text': 'cached'}
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')


def test_trend_scanner_uses_engagement_and_aware_dates():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from universal_video_ai.trends.service import TrendScanner
    from universal_video_ai.trends.models import TrendItemCandidate
    from universal_video_ai.trends.scoring import calculate_trend_score
    now = datetime.now(timezone.utc).isoformat()
    low = TrendItemCandidate('youtube', 'https://example.test/low', view_count=1, published_at=now)
    high = TrendItemCandidate('youtube', 'https://example.test/high', view_count=1000000, published_at=now)
    assert calculate_trend_score(low) > 0  # The valid UTC date contributes recency.
    scanner = TrendScanner()
    scanner._providers['agent_reach'] = SimpleNamespace(search=lambda *args: [low, high])
    result = scanner.scan('topic', use_agent_reach_fallback=True)
    assert [item.source_url for item in result.items] == [high.source_url, low.source_url]
    assert high.trend_score > low.trend_score
