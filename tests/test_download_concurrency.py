from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, CancelledError
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from universal_video_ai.channel_agent.automation import AutomationOrchestrator
from universal_video_ai.channel_agent.autonomous_operator import AutonomousChannelOperator, AutonomousOperatorError
from universal_video_ai.downloader.concurrency import DownloadSlots, download_scope
from universal_video_ai.downloader.service import DownloadService
from universal_video_ai.web.store import Store


def setup(tmp_path, monkeypatch, limit):
    store = Store(tmp_path / "slots.sqlite3")
    owner = store.create_user("owner", "x")
    foreign = store.create_user("foreign", "x")
    operator = AutonomousChannelOperator(store, AutomationOrchestrator(store))
    operator.save_config(owner, "channel", configuration={"max_concurrent_downloads": limit})
    monkeypatch.setenv("WEB_DB_PATH", str(store.db_path))
    return store, owner, foreign, operator


def wait_until(predicate):
    deadline = time.monotonic() + 8
    while not predicate():
        assert time.monotonic() < deadline, "worker did not reach expected state"
        time.sleep(0.01)


@pytest.mark.parametrize("limit,total", [(1, 3), (2, 5)])
def test_real_download_boundary_limits_and_drains_queue(tmp_path, monkeypatch, limit, total):
    store, owner, _, _ = setup(tmp_path, monkeypatch, limit)
    guard = threading.Lock()
    release = threading.Event()
    active = peak = entered = 0

    def download(service, url, path):
        nonlocal active, peak, entered
        with guard:
            active += 1
            entered += 1
            peak = max(peak, active)
        try:
            assert release.wait(8)
            return url
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(DownloadService, "_download", download)
    with ThreadPoolExecutor(max_workers=total) as pool:
        futures = [pool.submit(DownloadService(owner, use_cache=False).download, str(i), tmp_path) for i in range(total)]
        try:
            wait_until(lambda: entered == limit)
            time.sleep(0.12)
            assert active == limit and entered == limit
            assert sum(not f.done() for f in futures) == total
        finally:
            release.set()
        assert [f.result(timeout=8) for f in futures] == [str(i) for i in range(total)]
    assert peak == limit and active == 0 and entered == total


@pytest.mark.parametrize("failure", [RuntimeError, CancelledError])
def test_failure_cancel_and_retry_release_slot(tmp_path, monkeypatch, failure):
    store, owner, _, _ = setup(tmp_path, monkeypatch, 1)
    calls = 0
    def download(service, url, path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise failure("controlled failure")
        return "retried"
    monkeypatch.setattr(DownloadService, "_download", download)
    service = DownloadService(owner, use_cache=False)
    with pytest.raises(failure):
        service.download("same-logical-video", tmp_path)
    assert service.download("same-logical-video", tmp_path) == "retried"
    slots = DownloadSlots(store.db_path, owner)
    with slots.configuration_lock():
        assert slots.active_count() == 0


def test_waiting_cancel_does_not_execute_or_release_someone_elses_slot(tmp_path, monkeypatch):
    store, owner, _, _ = setup(tmp_path, monkeypatch, 1)
    cancellation = threading.Event()
    monkeypatch.setattr(DownloadService, "_download", lambda *a: pytest.fail("cancelled download executed"))
    slots = DownloadSlots(store.db_path, owner)
    with slots.acquire():
        with ThreadPoolExecutor() as pool:
            waiting = pool.submit(DownloadService(owner, use_cache=False).download, "queued", tmp_path, cancel_event=cancellation)
            cancellation.set()
            with pytest.raises(CancelledError):
                waiting.result(timeout=5)
        with slots.configuration_lock():
            assert slots.active_count() == 1


def test_owners_are_isolated_and_configuration_cannot_lower_below_active(tmp_path, monkeypatch):
    store, owner, foreign, operator = setup(tmp_path, monkeypatch, 1)
    operator.save_config(foreign, "channel", configuration={"max_concurrent_downloads": 2})
    with DownloadSlots(store.db_path, owner).acquire():
        with DownloadSlots(store.db_path, foreign).acquire():
            with DownloadSlots(store.db_path, foreign).acquire():
                with pytest.raises(AutonomousOperatorError, match="active downloads"):
                    operator.save_config(foreign, "channel", configuration={"max_concurrent_downloads": 1})
                assert operator.get_config(owner, "channel")["configuration"]["max_concurrent_downloads"] == 1


def test_same_owner_configs_use_conservative_shared_limit(tmp_path, monkeypatch):
    store, owner, _, operator = setup(tmp_path, monkeypatch, 2)
    operator.save_config(owner, "second-channel", configuration={"max_concurrent_downloads": 1})
    assert DownloadSlots(store.db_path, owner).configured_limit() == 1


def test_autonomous_handler_binds_database_owner_and_limit(tmp_path, monkeypatch):
    store, owner, foreign, operator = setup(tmp_path, monkeypatch, 1)
    monkeypatch.setenv("WEB_DB_PATH", str(tmp_path / "wrong.sqlite3"))
    observed = []
    def underlying(service, url, path):
        slots = DownloadSlots(store.db_path, owner)
        with slots.configuration_lock():
            observed.append(slots.active_count())
        return "downloaded"
    monkeypatch.setattr(DownloadService, "_download", underlying)
    handler = lambda u, c, r: {"result": DownloadService(u, use_cache=False).download("owned-video", tmp_path)}
    assert operator.orchestrator._execute_stage(owner, "assets", {"max_concurrent_downloads": 1}, {}, handler) == {"result": "downloaded"}
    assert observed == [1]
    with download_scope(store.db_path, owner, 1), pytest.raises(ValueError, match="owner"):
        DownloadService(foreign, use_cache=False).download("foreign", tmp_path)


def test_process_crash_releases_capacity_without_stale_claims(tmp_path, monkeypatch):
    store, owner, _, _ = setup(tmp_path, monkeypatch, 1)
    ready = tmp_path / "ready"
    program = """
import sys, time
from pathlib import Path
from universal_video_ai.downloader.concurrency import DownloadSlots
with DownloadSlots(sys.argv[1], int(sys.argv[2])).acquire():
    Path(sys.argv[3]).write_text("ready")
    time.sleep(60)
"""
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    child = subprocess.Popen([sys.executable, "-c", program, str(store.db_path), str(owner), str(ready)], env=environment)
    try:
        wait_until(ready.exists)
        restarted = DownloadSlots(store.db_path, owner)
        with restarted.configuration_lock():
            assert restarted.active_count() == 1
        child.terminate()
        child.wait(timeout=8)
        with restarted.acquire():
            with restarted.configuration_lock():
                assert restarted.active_count() == 1
        with restarted.configuration_lock():
            assert restarted.active_count() == 0
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=8)
