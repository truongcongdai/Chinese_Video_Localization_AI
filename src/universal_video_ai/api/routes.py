# src/universal_video_ai/api/routes.py
"""
Flask routes for admin dashboard API.
"""

from __future__ import annotations

from flask import Flask, jsonify, request
from typing import Optional
import logging
from functools import wraps

from universal_video_ai.database import DatabaseManager
from universal_video_ai.monitoring import MetricsCollector

_logger = logging.getLogger(__name__)


def create_app(db_manager: DatabaseManager, metrics: MetricsCollector, admin_token: str) -> Flask:
    """Create Flask app with routes."""
    app = Flask(__name__)

    def require_token(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if token != admin_token:
                return jsonify({"error": "Unauthorized"}), 401
            return f(*args, **kwargs)

        return decorated

    @app.route("/api/health", methods=["GET"])
    def health():
        """Health check."""
        return jsonify({"status": "healthy"})

    @app.route("/api/admin/stats", methods=["GET"])
    @require_token
    def get_stats():
        """Get system statistics."""
        stats = metrics.get_summary_stats()
        return jsonify(stats)

    @app.route("/api/admin/jobs", methods=["GET"])
    @require_token
    def get_jobs():
        """Get recent jobs."""
        limit = request.args.get("limit", 50, type=int)
        jobs = metrics.get_all_jobs(limit=limit)
        return jsonify([j.to_dict() for j in jobs])

    @app.route("/api/admin/users/<int:user_id>", methods=["GET"])
    @require_token
    def get_user(user_id: int):
        """Get user details."""
        uc = db_manager.get_user_credits(user_id)
        user_metrics = metrics.get_user_metrics(user_id)
        return jsonify({
            "user_id": user_id,
            "credits": uc.credits,
            "total_used": uc.total_used,
            "subscription_tier": uc.subscription_tier,
            "jobs_completed": user_metrics.completed_jobs,
            "jobs_failed": user_metrics.failed_jobs,
        })

    @app.route("/api/admin/users/<int:user_id>/credits", methods=["POST"])
    @require_token
    def set_credits(user_id: int):
        """Set user credits (admin)."""
        data = request.get_json() or {}
        amount = data.get("amount", 0)
        reason = data.get("reason", "Admin adjustment")

        db_manager.set_user_credits(user_id, amount, reason=reason, admin_id=999)
        uc = db_manager.get_user_credits(user_id)
        return jsonify({"user_id": user_id, "new_balance": uc.credits})

    @app.route("/api/admin/users/<int:user_id>/tier", methods=["POST"])
    @require_token
    def set_tier(user_id: int):
        """Set user subscription tier."""
        data = request.get_json() or {}
        tier = data.get("tier", "free")

        db_manager.set_subscription_tier(user_id, tier)
        return jsonify({"user_id": user_id, "tier": tier})

    return app