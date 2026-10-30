"""Run regression tests with external Python network connections disabled."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
from typing import Any

__all__ = ["main"]


def main() -> int:
    """Keep real provider calls out of regression; preserve local Redis sockets."""
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        raise SystemExit("Unset RUN_LIVE_TESTS before using the offline test runner.")
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root / "src"))
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise OSError("External Python network connections are disabled during offline regression tests")

    def connect(sock: socket.socket, address: Any) -> Any:
        if sock.family == getattr(socket, "AF_UNIX", None):
            return original_connect(sock, address)
        return blocked(sock, address)

    def connect_ex(sock: socket.socket, address: Any) -> int:
        if sock.family == getattr(socket, "AF_UNIX", None):
            return original_connect_ex(sock, address)
        return blocked(sock, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.create_connection = blocked
    socket.getaddrinfo = blocked
    os.environ["PROVIDER_EXECUTION_MODE"] = "MOCK"

    import pytest

    # conftest.py isolates credentials, dotenv loading, and runtime databases
    # before test modules are imported. Subprocesses have their own transports;
    # network-using provider executables must remain mocked by their tests.
    return int(pytest.main(sys.argv[1:] or ["-q"]))


if __name__ == "__main__":
    raise SystemExit(main())
