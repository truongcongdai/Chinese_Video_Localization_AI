#!/usr/bin/env python3
"""Verify that the interpreter used to run the web app has its runtime deps."""
from __future__ import annotations

import importlib
import sys

REQUIRED = ("fastapi", "uvicorn", "librosa", "mutagen")
OPTIONAL = ("openai",)


def _check(name: str, *, required: bool) -> bool:
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        level = "MISSING" if required else "OPTIONAL"
        print(f"[{level}] {name}: {exc}")
        return not required
    version = getattr(module, "__version__", "unknown")
    print(f"[OK] {name} {version}")
    return True


def main() -> int:
    print(f"Python executable: {sys.executable}")
    print(f"Python version: {sys.version.split()[0]}")
    ok = all(_check(name, required=True) for name in REQUIRED)
    for name in OPTIONAL:
        _check(name, required=False)

    try:
        from fastapi import FastAPI
        app = FastAPI(title="dependency-check")
        assert app.title == "dependency-check"
        print("[OK] fastapi.FastAPI can be instantiated")
    except Exception as exc:
        print(f"[MISSING] fastapi.FastAPI: {exc}")
        ok = False

    if not ok:
        print("\nInstall into this exact interpreter with:")
        print(f"  {sys.executable} -m pip install -r requirements.txt")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
