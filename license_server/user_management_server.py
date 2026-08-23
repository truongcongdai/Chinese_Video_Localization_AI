#!/usr/bin/env python3
"""
User Management Server for centralized user account management
Runs alongside License Server on the same machine
"""
import sqlite3
import hashlib
import secrets
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass, asdict
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
import os
import bcrypt

# Database setup
DB_PATH = "users.db"
DEFAULT_USER_CREDITS = 15
REFERRAL_BONUS_CREDITS = max(0, int(os.environ.get("REFERRAL_BONUS_CREDITS", "5")))


@dataclass
class User:
    id: Optional[int]
    username: str
    email: str
    password_hash: str
    credits: int
    is_admin: bool
    created_at: float
    updated_at: float
    reset_token: Optional[str] = None
    reset_token_expiry: Optional[float] = None
    referral_code: Optional[str] = None
    referred_by_user_id: Optional[int] = None
    referral_rewarded_at: Optional[float] = None


class UserRegister(BaseModel):
    username: str
    email: str
    password: str
    referral_code: Optional[str] = None


class UserLogin(BaseModel):
    username: str
    password: str


class UserUpdate(BaseModel):
    credits: Optional[int] = None
    is_admin: Optional[bool] = None
    password: Optional[str] = None


class CreditsAdjust(BaseModel):
    amount: int


class ResetPasswordRequest(BaseModel):
    email: str


class ResetPasswordConfirm(BaseModel):
    token: str
    new_password: str


app = FastAPI(title="User Management Server", version="1.0.0")

# The desktop and license services call this API server-to-server. Browser
# access is disabled by default; opt in only a known management origin.
_cors_origins = [
    value.strip()
    for value in os.environ.get("USER_MANAGEMENT_CORS_ALLOWED_ORIGINS", "").split(",")
    if value.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=bool(_cors_origins) and "*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            credits INTEGER DEFAULT 0,
            is_admin BOOLEAN DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            reset_token TEXT,
            reset_token_expiry REAL
        )
    """)

    existing_columns = {
        row["name"] for row in cursor.execute("PRAGMA table_info(users)").fetchall()
    }
    for column, ddl in (
        ("referral_code", "ALTER TABLE users ADD COLUMN referral_code TEXT"),
        ("referred_by_user_id", "ALTER TABLE users ADD COLUMN referred_by_user_id INTEGER"),
        ("referral_rewarded_at", "ALTER TABLE users ADD COLUMN referral_rewarded_at REAL"),
    ):
        if column not in existing_columns:
            cursor.execute(ddl)
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code "
        "ON users(referral_code) WHERE referral_code IS NOT NULL"
    )
    for row in cursor.execute("SELECT id FROM users WHERE referral_code IS NULL").fetchall():
        cursor.execute(
            "UPDATE users SET referral_code = ? WHERE id = ?",
            (_new_referral_code(conn), row["id"]),
        )

    # Preserve a recoverable admin account across upgrades. Fresh central
    # installs previously created every account as a regular user even though
    # the local EXE promoted its first account.
    if not cursor.execute("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1").fetchone():
        first = cursor.execute("SELECT id FROM users ORDER BY created_at ASC LIMIT 1").fetchone()
        if first:
            cursor.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (first["id"],))
    
    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    """Hash password using bcrypt"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against hash"""
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))


def generate_reset_token() -> str:
    """Generate password reset token"""
    return secrets.token_urlsafe(32)


def _new_referral_code(conn: sqlite3.Connection) -> str:
    """Generate a short global referral code without ambiguous characters."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        code = "".join(secrets.choice(alphabet) for _ in range(7))
        if not conn.execute(
            "SELECT 1 FROM users WHERE referral_code = ?", (code,)
        ).fetchone():
            return code
    return secrets.token_hex(6).upper()


def user_from_row(row) -> User:
    """Convert database row to User dataclass"""
    return User(
        id=row["id"],
        username=row["username"],
        email=row["email"],
        password_hash=row["password_hash"],
        credits=row["credits"],
        is_admin=bool(row["is_admin"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        reset_token=row["reset_token"],
        reset_token_expiry=row["reset_token_expiry"],
        referral_code=row["referral_code"],
        referred_by_user_id=row["referred_by_user_id"],
        referral_rewarded_at=row["referral_rewarded_at"],
    )


# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    init_db()


# API Endpoints

@app.get("/")
async def root():
    return {"message": "User Management Server v1.0.0", "status": "running"}


@app.post("/api/users/register", response_model=Dict)
async def register_user(user_data: UserRegister):
    """Register a new user"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if username or email already exists
    is_first_user = cursor.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
    cursor.execute("SELECT id FROM users WHERE username = ? OR email = ?", 
                   (user_data.username, user_data.email))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Username or email already exists")

    referrer_id = None
    submitted_code = (user_data.referral_code or "").strip().upper()
    if submitted_code:
        referrer = cursor.execute(
            "SELECT id FROM users WHERE referral_code = ?", (submitted_code,)
        ).fetchone()
        if referrer is None:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid referral code")
        referrer_id = int(referrer["id"])
    
    # Hash password
    password_hash = hash_password(user_data.password)
    
    now = datetime.now().timestamp()
    referral_code = _new_referral_code(conn)
    starting_credits = DEFAULT_USER_CREDITS + (
        REFERRAL_BONUS_CREDITS if referrer_id is not None else 0
    )
    
    try:
        cursor.execute("""
            INSERT INTO users (
                username, email, password_hash, credits, is_admin,
                referral_code, referred_by_user_id, referral_rewarded_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_data.username, user_data.email, password_hash,
            starting_credits, is_first_user, referral_code, referrer_id,
            now if referrer_id is not None else None, now, now,
        ))
        user_id = cursor.lastrowid
        if referrer_id is not None and REFERRAL_BONUS_CREDITS:
            cursor.execute(
                "UPDATE users SET credits = credits + ?, updated_at = ? WHERE id = ?",
                (REFERRAL_BONUS_CREDITS, now, referrer_id),
            )
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "user_id": user_id,
            "message": "User registered successfully",
            "credits": starting_credits,
            "is_admin": is_first_user,
            "referral_code": referral_code,
            "referred_by_user_id": referrer_id,
            "referral_bonus_credits": REFERRAL_BONUS_CREDITS if referrer_id is not None else 0,
        }
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Username or email already exists")


@app.post("/api/users/login", response_model=Dict)
async def login_user(user_data: UserLogin):
    """Login user"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE username = ?", (user_data.username,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    user = user_from_row(row)
    
    if not verify_password(user_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    return {
        "success": True,
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "credits": user.credits,
        "is_admin": user.is_admin,
        "referral_code": user.referral_code,
    }


@app.post("/api/users/reset-password", response_model=Dict)
async def request_password_reset(request: ResetPasswordRequest):
    """Request password reset"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE email = ?", (request.email,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        # Don't reveal if email exists
        return {"success": True, "message": "If email exists, reset token sent"}
    
    user = user_from_row(row)
    
    # Generate reset token
    reset_token = generate_reset_token()
    reset_token_expiry = (datetime.now() + timedelta(hours=1)).timestamp()
    
    cursor.execute("""
        UPDATE users 
        SET reset_token = ?, reset_token_expiry = ?, updated_at = ?
        WHERE id = ?
    """, (reset_token, reset_token_expiry, datetime.now().timestamp(), user.id))
    
    conn.commit()
    conn.close()
    
    # TODO: Send email with reset token
    # For now, return token for testing
    return {
        "success": True,
        "message": "Reset token sent to email",
        "reset_token": reset_token  # Remove in production
    }


@app.post("/api/users/reset-password/confirm", response_model=Dict)
async def confirm_password_reset(request: ResetPasswordConfirm):
    """Confirm password reset with token"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE reset_token = ?", (request.token,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    
    user = user_from_row(row)
    
    # Check if token is expired
    if user.reset_token_expiry and datetime.now().timestamp() > user.reset_token_expiry:
        conn.close()
        raise HTTPException(status_code=400, detail="Reset token has expired")
    
    # Update password
    password_hash = hash_password(request.new_password)
    
    cursor.execute("""
        UPDATE users 
        SET password_hash = ?, reset_token = NULL, reset_token_expiry = NULL, updated_at = ?
        WHERE id = ?
    """, (password_hash, datetime.now().timestamp(), user.id))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Password reset successfully"}


@app.get("/api/users", response_model=List[Dict])
async def list_users():
    """List all users (admin only)"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    users = []
    for row in rows:
        user = user_from_row(row)
        user_dict = asdict(user)
        # Remove sensitive data
        user_dict.pop("password_hash", None)
        user_dict.pop("reset_token", None)
        users.append(user_dict)
    
    return users


@app.get("/api/users/{user_id}", response_model=Dict)
async def get_user(user_id: int):
    """Get user by ID"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    
    user = user_from_row(row)
    user_dict = asdict(user)
    user_dict.pop("password_hash", None)
    user_dict.pop("reset_token", None)
    
    return user_dict


@app.put("/api/users/{user_id}", response_model=Dict)
async def update_user(user_id: int, update_data: UserUpdate):
    """Update user (admin only)"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    
    user = user_from_row(row)
    
    # Build update query
    updates = []
    params = []
    
    if update_data.credits is not None:
        updates.append("credits = ?")
        params.append(update_data.credits)
    
    if update_data.is_admin is not None:
        updates.append("is_admin = ?")
        params.append(update_data.is_admin)
    
    if update_data.password:
        updates.append("password_hash = ?")
        params.append(hash_password(update_data.password))
    
    if updates:
        updates.append("updated_at = ?")
        params.append(datetime.now().timestamp())
        params.append(user_id)
        
        query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        conn.commit()
    
    conn.close()
    
    return {"success": True, "message": "User updated successfully"}


@app.post("/api/users/{user_id}/credits", response_model=Dict)
async def adjust_user_credits(user_id: int, body: CreditsAdjust):
    """Internal account-service endpoint used by the public port-8000 facade."""
    conn = get_db()
    cursor = conn.execute(
        "UPDATE users SET credits = MAX(0, credits + ?), updated_at = ? WHERE id = ?",
        (body.amount, datetime.now().timestamp(), user_id),
    )
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    conn.commit()
    row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    credits = int(row["credits"])
    conn.close()
    return {"success": True, "credits": credits}


@app.delete("/api/users/{user_id}", response_model=Dict)
async def delete_user(user_id: int):
    """Delete user (admin only)"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "User deleted successfully"}


if __name__ == "__main__":
    init_db()
    # Keep the current default during the rolling EXE upgrade: older builds
    # still authenticate directly on 8001. Set USER_MANAGEMENT_HOST=127.0.0.1
    # only after those clients have moved behind the port-8000 facade.
    uvicorn.run(app, host=os.environ.get("USER_MANAGEMENT_HOST", "0.0.0.0"), port=8001)
