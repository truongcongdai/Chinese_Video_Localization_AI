"""Offline release checks. Runtime databases are hashed and copied, never opened."""
from __future__ import annotations

import argparse
import gc
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def startup(directory, *, key):
    directory.mkdir()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = dict(os.environ, WEB_PORT=str(port), WEB_DB_PATH=str(directory / "web.sqlite3"),
                       DATABASE_PATH=str(directory / "web.sqlite3"), TEMP_DIR=str(directory / "temp"),
                       OUTPUT_DIR=str(directory / "output"), LOGS_DIR=str(directory / "logs"),
                       APP_SECRET_ENCRYPTION_KEY=key, LOG_LEVEL="ERROR")
    log_path = directory / "startup.log"
    with log_path.open("wb") as log:
        child = subprocess.Popen([sys.executable, str(ROOT / "scripts/run_web.py")],
                                 cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 50
            while True:
                assert child.poll() is None, "Windows entrypoint exited before startup."
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=1) as response:
                        assert response.status == 200
                    break
                except (OSError, urllib.error.URLError):
                    assert time.monotonic() < deadline, "Windows startup timed out."
                    time.sleep(0.2)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
                assert response.status == 200
        finally:
            child.terminate()
            child.wait(timeout=10)
    if key:
        assert key not in log_path.read_text(encoding="utf-8", errors="replace")
    assert (directory / "web.sqlite3").is_file()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-baseline", type=Path, required=True)
    parser.add_argument("--artifact-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.runtime_baseline.read_text())
    artifacts = json.loads(args.artifact_manifest.read_text(encoding="utf-8"))
    assert all(digest(path) == expected for path, expected in baseline.items()), "Runtime DB changed before acceptance."
    assert all(Path(path).is_file() and digest(path) == expected
               for path, expected in artifacts["local_hashes"].items()), "Untracked runtime data changed."
    report = {}
    sys.path.insert(0, str(ROOT / "src"))
    from cryptography.fernet import Fernet
    with tempfile.TemporaryDirectory(prefix="uvai-release-") as temporary:
        directory = Path(temporary)
        key = Fernet.generate_key().decode("ascii")
        os.environ.update(APP_SECRET_ENCRYPTION_KEY=key, RUN_LIVE_TESTS="0", PROVIDER_EXECUTION_MODE="MOCK",
                          WEB_SESSION_SECRET="offline-release-session-secret",
                          WEB_DB_PATH=str(directory / "app.sqlite3"), DATABASE_PATH=str(directory / "app.sqlite3"),
                          TEMP_DIR=str(directory / "temp"), OUTPUT_DIR=str(directory / "output"),
                          LOGS_DIR=str(directory / "logs"), REDIS_URL="")
        from universal_video_ai.web.store import Store
        from universal_video_ai.channel_agent.automation import AutomationOrchestrator
        from universal_video_ai.channel_agent.autonomous_operator import AutonomousChannelOperator
        from universal_video_ai.secret_cipher import SecretCipherConfigurationError, TokenCipher

        def initialize(path):
            store = Store(path)
            AutonomousChannelOperator(store, AutomationOrchestrator(store))
            return store

        fresh = initialize(directory / "fresh.sqlite3")
        owner = fresh.create_user("acceptance-owner", "fixture-hash")
        fresh.upsert_social_account(owner, "youtube", "fixture-access", "fixture-refresh")
        fresh.upsert_provider_settings(owner, "openai", "fixture-api-key", "fixture-api-secret")
        assert initialize(fresh.db_path).get_social_account(owner, "youtube")["access_token"] == "fixture-access"
        with fresh._connect() as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        conn.close()
        report["fresh_db"] = report["repeated_init"] = "PASS"
        migrated_copies = []
        for index, path in enumerate(baseline):
            target = directory / f"runtime-copy-{index}.sqlite3"
            shutil.copy2(path, target)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(path + suffix)
                if sidecar.is_file():
                    shutil.copy2(sidecar, Path(str(target) + suffix))
            # Read/initialize/migrate only the COPY.
            with sqlite3.connect(target) as conn:
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                          for table in ("users", "social_accounts", "jobs", "user_provider_settings") if table in names}
            user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.close()
            assert integrity == "ok"
            if "id" not in user_columns or "social_accounts" not in names:
                migrated_copies.append({"copy": index, "kind": "non-web legacy database",
                                        "migration": "not applicable; preserved", "integrity": "PASS"})
                continue
            store = initialize(target)
            first = store.migrate_legacy_social_account_tokens()
            assert store.migrate_legacy_social_account_tokens() == 0
            initialize(target)
            with store._connect() as conn:
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
                assert all(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count
                           for table, count in counts.items())
            conn.close()
            migrated_copies.append({"copy": index, "migrated_rows": first, "integrity": "PASS", "row_counts": "preserved"})
        report["legacy_and_token_migration"] = migrated_copies
        no_key = Store(directory / "no-key.sqlite3", token_cipher=TokenCipher(b""))
        try:
            no_key.upsert_social_account(1, "youtube", "must-not-write")
            raise AssertionError("Missing key allowed a secret write.")
        except SecretCipherConfigurationError:
            pass
        report["missing_key"] = "explicit configuration error"

        import universal_video_ai.web.app as web_app
        pairs = [(method, route.path) for route in web_app.app.routes for method in getattr(route, "methods", [])]
        assert not [pair for pair, count in Counter(pairs).items() if count > 1]
        assert web_app.app.openapi()["paths"]
        report["openapi_method_path_collisions"] = 0

        startup(directory / "startup-with-key", key=key)
        startup(directory / "startup-no-key", key="")
        report["windows_startup"] = "PASS with ephemeral key and without key"

        from universal_video_ai.provider_runtime import get_cost_report
        assert get_cost_report().live_calls == 0
        report["LIVE_PROVIDER_CALLS"] = 0
        web_app.youtube_research_database._conn.close()
        gc.collect()

    subprocess.run([sys.executable, "-m", "compileall", "-q", "src", "tests"], cwd=ROOT, check=True)
    report["compileall"] = "PASS"
    scripts = sorted((ROOT / "src").rglob("*.js")) + sorted((ROOT / "tests").rglob("*.js"))
    for script in scripts:
        subprocess.run(["node", "--check", str(script)], check=True, capture_output=True)
    report["javascript"] = {"result": "PASS", "files": len(scripts)}
    shell_true = []
    for path in list((ROOT / "src").rglob("*.py")) + list((ROOT / "scripts").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(k.arg == "shell" and isinstance(k.value, ast.Constant)
                                                  and k.value.value is True for k in node.keywords):
                shell_true.append(str(path.relative_to(ROOT)))
    assert not shell_true
    report["shell_true"] = 0
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    unintended = [path for path in tracked if path and (
        path.startswith(("scripts/local_data/", "scripts/downloads/", "src/universal_video_ai/temp/",
                         "Chinese_Video_Localization_AI_Windows_Build/", "temp/", "logs/", "cookies/",
                         "build/", "dist/", "node_modules/"))
        or "/__pycache__/" in path or "/.pytest_cache/" in path
        or Path(path).suffix.lower() in {".sqlite", ".sqlite3", ".db", ".pyc"}
        or Path(path).name in {"Cookies", "Cookies-journal", "Login Data"}
    )]
    assert not unintended, f"Unintended tracked artifacts: {unintended}"
    report["unintended_tracked_artifacts"] = 0
    report["retained_assets"] = artifacts["retained_assets"]
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    assert all(digest(path) == expected for path, expected in baseline.items()), "Runtime DB changed during acceptance."
    assert all(Path(path).is_file() and digest(path) == expected
               for path, expected in artifacts["local_hashes"].items()), "Runtime data changed during acceptance."
    report["runtime_db_unchanged"] = report["local_artifacts_preserved"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
