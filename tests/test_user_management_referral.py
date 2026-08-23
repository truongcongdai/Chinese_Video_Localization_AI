import asyncio

import pytest
from fastapi import HTTPException

from license_server import user_management_server as account_server


def test_central_referral_rewards_both_accounts_once(monkeypatch, tmp_path):
    monkeypatch.setattr(account_server, "DB_PATH", str(tmp_path / "users.sqlite3"))
    monkeypatch.setattr(account_server, "REFERRAL_BONUS_CREDITS", 5)
    account_server.init_db()

    inviter = asyncio.run(account_server.register_user(account_server.UserRegister(
        username="inviter", email="inviter@example.com", password="password123",
    )))
    invitee = asyncio.run(account_server.register_user(account_server.UserRegister(
        username="invitee", email="invitee@example.com", password="password123",
        referral_code=inviter["referral_code"].lower(),
    )))

    assert invitee["credits"] == account_server.DEFAULT_USER_CREDITS + 5
    assert invitee["referral_bonus_credits"] == 5
    with account_server.get_db() as conn:
        inviter_row = conn.execute(
            "SELECT credits FROM users WHERE id = ?", (inviter["user_id"],)
        ).fetchone()
        invitee_row = conn.execute(
            "SELECT referred_by_user_id, referral_rewarded_at FROM users WHERE id = ?",
            (invitee["user_id"],),
        ).fetchone()
    assert inviter_row["credits"] == account_server.DEFAULT_USER_CREDITS + 5
    assert invitee_row["referred_by_user_id"] == inviter["user_id"]
    assert invitee_row["referral_rewarded_at"] is not None


def test_central_registration_rejects_unknown_referral(monkeypatch, tmp_path):
    monkeypatch.setattr(account_server, "DB_PATH", str(tmp_path / "users.sqlite3"))
    account_server.init_db()

    with pytest.raises(HTTPException, match="Invalid referral code"):
        asyncio.run(account_server.register_user(account_server.UserRegister(
            username="invitee", email="invitee@example.com", password="password123",
            referral_code="UNKNOWN",
        )))
