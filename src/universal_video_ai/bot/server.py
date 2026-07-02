# src/universal_video_ai/bot/server.py
"""
Simple health check server for monitoring.
Runs on port 8000.
"""

from __future__ import annotations

import logging
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import time

__all__ = ["HealthCheckServer", "start_health_check_server"]

_logger = logging.getLogger(__name__)


class HealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP request handler for health checks."""

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {
                "status": "healthy",
                "timestamp": time.time(),
                "service": "telegram-bot",
            }
            self.wfile.write(json.dumps(response).encode())
        elif self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Telegram Bot Running\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args) -> None:
        """Suppress default logging."""
        return


class HealthCheckServer:
    """Simple HTTP health check server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8000, logger: logging.Logger | None = None) -> None:
        self.host = host
        self.port = port
        self.logger = logger or _logger
        self.server = HTTPServer((host, port), HealthCheckHandler)
        self._thread: Thread | None = None
        self._running = False

    def start(self) -> None:
        """Start server in background thread."""
        if self._running:
            self.logger.warning("Health check server already running")
            return

        self._running = True
        self._thread = Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        self.logger.info("Health check server started on http://%s:%d", self.host, self.port)

    def stop(self) -> None:
        """Stop server."""
        if not self._running:
            return

        self._running = False
        self.server.shutdown()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.logger.info("Health check server stopped")


def start_health_check_server(host: str = "127.0.0.1", port: int = 8000) -> HealthCheckServer:
    """Convenience function to start server."""
    server = HealthCheckServer(host, port)
    server.start()
    return server