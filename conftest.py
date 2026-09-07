"""Global regression guard: normal pytest must never consume live quota."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

from universal_video_ai.provider_runtime import get_cost_report, reset_cost_report


_TEST_RUNTIME_DIR = None


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    global _TEST_RUNTIME_DIR
    if os.getenv("RUN_LIVE_TESTS") != "1":
        os.environ["PROVIDER_EXECUTION_MODE"] = "MOCK"
        # App imports initialize Store immediately. Point them at an isolated
        # disposable database before test modules are imported, so regression
        # can never migrate or write the configured runtime database.
        _TEST_RUNTIME_DIR = tempfile.TemporaryDirectory(prefix="uvai-pytest-")
        os.environ["WEB_DB_PATH"] = str(
            Path(_TEST_RUNTIME_DIR.name) / "regression.sqlite3"
        )
    reset_cost_report(clear_cache=True)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    report = get_cost_report()
    terminalreporter.write_sep("=", "provider cost report")
    terminalreporter.write_line(f"PROVIDER_CALLS={report.provider_calls}")
    terminalreporter.write_line(f"CACHE_HITS={report.cache_hits}")
    terminalreporter.write_line(f"MOCK_PROVIDER_CALLS={report.mock_calls}")
    terminalreporter.write_line(f"LIVE_PROVIDER_CALLS={report.live_calls}")
    if os.getenv("RUN_LIVE_TESTS") != "1" and report.live_calls:
        terminalreporter.write_line("ERROR: normal pytest performed live provider calls", red=True, bold=True)


def pytest_sessionfinish(session, exitstatus):
    global _TEST_RUNTIME_DIR
    if os.getenv("RUN_LIVE_TESTS") != "1" and get_cost_report().live_calls:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    # The web app keeps its YouTube-research SQLite connection open for the
    # process lifetime. Close that test-only handle before removing the
    # disposable runtime directory (required on Windows).
    app_module = sys.modules.get("universal_video_ai.web.app")
    database = getattr(app_module, "youtube_research_database", None)
    connection = getattr(database, "_conn", None)
    if connection is not None:
        connection.close()
    if _TEST_RUNTIME_DIR is not None:
        _TEST_RUNTIME_DIR.cleanup()
        _TEST_RUNTIME_DIR = None
