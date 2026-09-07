from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from universal_video_ai.channel_agent.automation import AutomationOrchestrator
import universal_video_ai.channel_agent.automation as automation_module
from universal_video_ai.channel_agent.autonomous_operator import (
    AutonomousChannelOperator,
    AutonomousOperatorError,
    AutonomousOperatorNotFound,
)
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.channel_agent_router import AutonomousConfigBody, router
from universal_video_ai.web.store import Store


def setup_operator(tmp_path, now=2_000_000_000.0):
    store = Store(tmp_path / "operator.sqlite3")
    owner = store.create_user("owner", "x")
    foreign = store.create_user("foreign", "x")
    handlers = {
        "opportunity": lambda user, config, refs: {"opportunity_id": 101},
        "production": lambda user, config, refs: {"production_item_id": 202},
    }
    orchestrator = AutomationOrchestrator(store, handlers=handlers, now=lambda: now)
    return store, owner, foreign, AutonomousChannelOperator(
        store, orchestrator, now=lambda: now
    )


def autonomous_config(**overrides):
    config = {
        "target_platforms": [],
        "approval_policy": {
            "require_opportunity_approval": False,
            "require_script_approval": False,
            "require_asset_approval": False,
            "require_publish_approval": False,
        },
        "refresh_analytics": False,
        "generate_learning": False,
        "max_concurrent_cycles": 1,
        "daily_cycle_limit": 5,
        "weekly_cycle_limit": 10,
    }
    config.update(overrides)
    return config


def test_config_is_persistent_owner_scoped_secret_free_and_conservative(tmp_path):
    store, owner, foreign, operator = setup_operator(tmp_path)
    saved = operator.save_config(
        owner, "owned-channel", enabled=True, provider_mode="MOCK",
        timezone_name="Asia/Bangkok", configuration={"target_platforms": ["youtube"]},
    )
    assert saved["mode"] == "FULL_AUTONOMOUS" and saved["enabled"]
    assert all(saved["configuration"]["approval_policy"].values())
    assert saved["configuration"]["default_publishing_privacy"] == "private"
    restarted = AutonomousChannelOperator(Store(store.db_path), AutomationOrchestrator(Store(store.db_path)))
    assert restarted.get_config(owner, "owned-channel")["id"] == saved["id"]
    with pytest.raises(AutonomousOperatorNotFound):
        restarted.get_config(foreign, "owned-channel")
    with pytest.raises(AutonomousOperatorError, match="credentials"):
        operator.save_config(owner, "bad", configuration={"access_token": "secret"})
    assert "secret" not in str(operator.list_configs(owner))


def test_full_autonomous_cycle_persists_and_runs_all_safe_stages_in_mock(tmp_path):
    store, owner, _, operator = setup_operator(tmp_path)
    config = operator.save_config(
        owner, "channel", enabled=True, provider_mode="MOCK",
        configuration=autonomous_config(),
    )
    cycle, created = operator.start_cycle(
        owner, config["id"], idempotency_key="one-cycle", advance=True
    )
    assert created and cycle["status"] == "completed" and cycle["progress"] == 100
    run = operator.orchestrator.get(owner, cycle["automation_run_id"])
    assert run["mode"] == "FULL_AUTONOMOUS"
    assert run["refs"]["production_item_id"] == 202
    assert run["refs"]["provider_usage"]["llm_calls"] >= 2
    assert run["refs"]["provider_usage"]["tts_requests"] == 1
    assert cycle["cost_report"]["live_calls"] == 0
    assert cycle["cost_report"]["mock_calls"] >= 1
    restarted = AutonomousChannelOperator(Store(store.db_path), AutomationOrchestrator(Store(store.db_path)))
    assert restarted.get_cycle(owner, cycle["id"])["status"] == "completed"


def test_cycle_idempotency_and_concurrency_limit(tmp_path):
    _, owner, _, operator = setup_operator(tmp_path)
    config = operator.save_config(
        owner, "channel", provider_mode="MOCK", configuration=autonomous_config()
    )
    first, created = operator.start_cycle(owner, config["id"], idempotency_key="same", advance=False)
    same, created_again = operator.start_cycle(owner, config["id"], idempotency_key="same", advance=False)
    assert created and not created_again and same["id"] == first["id"]
    with pytest.raises(AutonomousOperatorError, match="concurrent"):
        operator.start_cycle(owner, config["id"], idempotency_key="different", advance=False)


def test_provider_budget_failure_is_persisted_at_exact_step(tmp_path):
    _, owner, _, operator = setup_operator(tmp_path)
    config = operator.save_config(
        owner, "channel", provider_mode="MOCK",
        configuration=autonomous_config(budgets={
            "max_llm_calls": 0, "max_external_api_calls": 0,
            "max_tts_requests": 0, "max_upload_attempts": 0,
            "max_live_acceptance_calls": 0,
        }),
    )
    cycle, _ = operator.start_cycle(owner, config["id"], advance=True)
    assert cycle["status"] == "failed" and cycle["current_stage"] == "research"
    assert "budget exceeded" in cycle["error"].lower()


def test_scheduler_timezone_next_run_and_missed_run_once_without_burst(tmp_path):
    store, owner, _, operator = setup_operator(tmp_path)
    config = operator.save_config(
        owner, "channel", enabled=True, provider_mode="MOCK", cadence="daily",
        local_time="09:00", timezone_name="Asia/Bangkok", missed_run_policy="run_once",
        configuration=autonomous_config(),
    )
    due = operator.now() - 7200
    with store._connect() as conn:
        conn.execute("UPDATE autonomous_channel_configs SET next_run_at=? WHERE id=?", (due, config["id"]))
    cycles = operator.run_due(at=operator.now(), user_id=owner)
    assert len(cycles) == 1
    assert operator.run_due(at=operator.now(), user_id=owner) == []
    refreshed = operator.get_config(owner, "channel")
    assert refreshed["next_run_at"] > operator.now()


@pytest.mark.parametrize("policy", ["skip", "reschedule"])
def test_missed_run_skip_policies_advance_schedule_without_cycle(tmp_path, policy):
    store, owner, _, operator = setup_operator(tmp_path)
    config = operator.save_config(
        owner, "channel", enabled=True, provider_mode="MOCK", missed_run_policy=policy,
        configuration=autonomous_config(),
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE autonomous_channel_configs SET next_run_at=? WHERE id=?",
            (operator.now() - 7200, config["id"]),
        )
    assert operator.run_due(at=operator.now(), user_id=owner) == []
    assert operator.list_cycles(owner) == []
    assert operator.get_config(owner, "channel")["next_run_at"] > operator.now()


def test_scheduler_and_cycle_idor_do_not_touch_foreign_owner(tmp_path):
    store, owner, foreign, operator = setup_operator(tmp_path)
    mine = operator.save_config(
        owner, "mine", enabled=True, provider_mode="MOCK", configuration=autonomous_config()
    )
    theirs = operator.save_config(
        foreign, "theirs", enabled=True, provider_mode="MOCK", configuration=autonomous_config()
    )
    with store._connect() as conn:
        conn.execute("UPDATE autonomous_channel_configs SET next_run_at=?", (operator.now(),))
    cycles = operator.run_due(at=operator.now(), user_id=owner)
    assert len(cycles) == 1 and cycles[0]["user_id"] == owner
    assert operator.list_cycles(foreign) == []
    with pytest.raises(AutonomousOperatorNotFound):
        operator.start_cycle(owner, theirs["id"])
    with pytest.raises(AutonomousOperatorNotFound):
        operator.get_cycle(foreign, cycles[0]["id"])


def test_pause_resume_and_stop_after_current_are_persistent(tmp_path):
    store, owner, _, operator = setup_operator(tmp_path)
    config = operator.save_config(owner, "channel", enabled=True, configuration=autonomous_config())
    paused = operator.set_enabled(owner, config["id"], False)
    assert not paused["enabled"] and not paused["stop_after_current"]
    resumed = operator.set_enabled(owner, config["id"], True)
    assert resumed["enabled"]
    stopped = operator.set_enabled(owner, config["id"], False, stop_after_current=True)
    assert not stopped["enabled"] and stopped["stop_after_current"]
    restarted = AutonomousChannelOperator(Store(store.db_path), AutomationOrchestrator(Store(store.db_path)))
    assert restarted.get_config(owner, "channel")["stop_after_current"]


def test_fresh_and_repeated_autonomous_schema_preserves_legacy_data(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES ('preserved')")
    store = Store(path)
    owner = store.create_user("owner", "x")
    AutonomousChannelOperator(store, AutomationOrchestrator(store))
    AutonomousChannelOperator(Store(path), AutomationOrchestrator(Store(path)))
    with store._connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "preserved"
    assert {"autonomous_channel_configs", "autonomous_channel_cycles"}.issubset(tables)


def test_autonomous_routes_require_auth_and_ui_exposes_safety_state():
    routes = [route for route in router.routes if "/autonomous/" in route.path]
    assert routes
    for route in routes:
        assert any(dep.call is get_current_user_id for dep in route.dependant.dependencies)
    body = AutonomousConfigBody(channel_key="owned")
    assert body.mode == "FULL_AUTONOMOUS" and body.provider_mode == "DRY_RUN"
    root = Path(__file__).parents[1]
    html = (root / "src/universal_video_ai/web/static/index.html").read_text(encoding="utf-8")
    js = (root / "src/universal_video_ai/web/static/app.js").read_text(encoding="utf-8")
    for text in ("Autonomous Operator", "FULL_AUTONOMOUS", "LIVE", "Stop after current cycle"):
        assert text in html
    for text in ("LIVE CALLS =", "Start cycle now", "approval_policy", "provider-cost"):
        assert text in html + js


def test_autonomous_shortlist_is_bounded_by_score_confidence_rights_and_gate(tmp_path, monkeypatch):
    store = Store(tmp_path / "shortlist.sqlite3")
    owner = store.create_user("owner", "x")
    changed = []
    class Opportunities:
        def __init__(self, store):
            pass
        def generate(self, user_id, limit):
            assert limit == 3
            return {"items": [
                {"id": 1, "final_score": 0.99, "confidence": "high", "rights_status": "blocked"},
                {"id": 2, "final_score": 0.60, "confidence": "high", "rights_status": "cleared"},
                {"id": 3, "final_score": 0.85, "confidence": "high", "rights_status": "cleared"},
            ]}
        def change_status(self, user_id, item_id, status):
            changed.append((user_id, item_id, status))
    monkeypatch.setattr(automation_module, "ContentOpportunityService", Opportunities)
    service = AutomationOrchestrator(store)
    config = {
        "max_opportunities": 3, "production_limit": 1, "auto_shortlist": True,
        "minimum_opportunity_score": 0.7, "minimum_opportunity_confidence": "medium",
        "approval_policy": {"require_opportunity_approval": False},
    }
    result = service._default_execute(owner, "opportunity", config, {})
    assert result["opportunity_id"] == 3
    assert result["shortlist_reason"]["bounded_limit"] == 1
    assert changed == [(owner, 3, "approved")]


def test_restart_recovers_cycle_running_step_without_duplicate_cycle(tmp_path):
    store, owner, _, operator = setup_operator(tmp_path)
    config = operator.save_config(
        owner, "channel", provider_mode="MOCK", configuration=autonomous_config()
    )
    cycle, _ = operator.start_cycle(owner, config["id"], idempotency_key="recover", advance=False)
    run_id = cycle["automation_run_id"]
    store.update_automation_step(owner, run_id, "research", {"status": "running"})
    with store._connect() as conn:
        conn.execute(
            "UPDATE autonomous_channel_cycles SET status='running',current_stage='research' WHERE id=?",
            (cycle["id"],),
        )
    handlers = {
        "opportunity": lambda user, config, refs: {"opportunity_id": 101},
        "production": lambda user, config, refs: {"production_item_id": 202},
    }
    restarted_store = Store(store.db_path)
    restarted = AutonomousChannelOperator(
        restarted_store,
        AutomationOrchestrator(restarted_store, handlers=handlers, now=operator.now),
        now=operator.now,
    )
    result = restarted.advance_cycle(owner, cycle["id"])
    assert result["status"] == "completed"
    assert len(restarted.list_cycles(owner)) == 1
    assert result["steps"][0]["status"] == "completed"


def test_persisted_render_concurrency_limit_waits_instead_of_overcommitting(tmp_path):
    store = Store(tmp_path / "stage-limit.sqlite3")
    owner = store.create_user("owner", "x")
    service = AutomationOrchestrator(store, handlers={"render": lambda u, c, r: {"render_job_id": 1}})
    config = {
        "target_platforms": [], "research_refresh": False, "competitor_refresh": False,
        "production_item_id": 99, "max_concurrent_renders": 1,
        "approval_policy": {key: False for key in (
            "require_opportunity_approval", "require_script_approval",
            "require_asset_approval", "require_publish_approval",
        )},
    }
    blocked, _ = service.start(owner, mode="FULL_AUTONOMOUS", configuration=config)
    other, _ = service.start(owner, mode="FULL_AUTONOMOUS", configuration=config)
    for run in (blocked, other):
        for stage in ("research", "competitor", "opportunity", "production", "script", "assets"):
            store.update_automation_step(owner, run["id"], stage, {"status": "completed"})
    store.update_automation_step(owner, blocked["id"], "render", {"status": "running"})
    result = service.advance(owner, other["id"])
    assert result["status"] == "waiting_approval"
    assert "max_concurrent_renders=1" in result["waiting_reason"]
