#!/usr/bin/env python3
"""
Lightweight License Server for centralized license management
Runs on Ubuntu with minimal resources (1 CPU, 512MB RAM)
"""
import sqlite3
import hashlib
import hmac
import base64
import secrets
import json
import logging
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass, asdict
from fastapi import FastAPI, HTTPException, Depends, Cookie, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn
import os
import requests

# Database setup
DB_PATH = Path("licenses.db")
logger = logging.getLogger(__name__)
VALID_QUOTA_TYPES = {"credit", "time"}
VALID_LICENSE_STATUSES = {"active", "revoked", "expired"}
USER_MANAGEMENT_INTERNAL_URL = os.environ.get(
    "USER_MANAGEMENT_INTERNAL_URL", "http://127.0.0.1:8001"
).rstrip("/")
ADMIN_COOKIE_NAME = "license_admin_session"
ADMIN_SESSION_TTL_SECONDS = int(os.environ.get("LICENSE_ADMIN_SESSION_TTL_SECONDS", "28800"))
_admin_session_secret = os.environ.get("LICENSE_ADMIN_SESSION_SECRET", "").encode("utf-8")
_last_telemetry_cleanup_at = 0.0
if not _admin_session_secret:
    _admin_session_secret = secrets.token_bytes(32)
    logger.warning(
        "LICENSE_ADMIN_SESSION_SECRET is not set; admin sessions will expire when the service restarts"
    )

@dataclass
class License:
    id: Optional[int]
    license_key: str
    customer_name: str
    customer_email: str
    user_id: Optional[int]
    plan_type: str  # basic, pro, enterprise
    quota_type: str  # credit, time
    features: List[str]
    expiry_date: Optional[float]  # Unix timestamp
    max_jobs: int
    max_tokens: int
    status: str  # active, revoked, expired
    machine_id: Optional[str]  # For machine binding
    created_at: float
    updated_at: float
    notes: Optional[str]

class LicenseCreate(BaseModel):
    customer_name: str
    customer_email: str
    user_id: Optional[int] = None
    plan_type: str
    quota_type: str = "credit"
    features: List[str]
    expiry_days: Optional[int] = None  # None = lifetime
    max_jobs: int = 100
    max_tokens: int = 1000
    machine_id: Optional[str] = None
    notes: Optional[str] = None

class LicenseUpdate(BaseModel):
    user_id: Optional[int] = None
    status: Optional[str] = None
    plan_type: Optional[str] = None
    quota_type: Optional[str] = None
    expiry_days: Optional[int] = None
    clear_expiry: bool = False
    max_jobs: Optional[int] = None
    max_tokens: Optional[int] = None
    features: Optional[List[str]] = None
    notes: Optional[str] = None

class LicenseValidation(BaseModel):
    license_key: str
    machine_id: str

class LicenseResponse(BaseModel):
    valid: bool
    license: Optional[Dict]
    error: Optional[str]


class AdminLogin(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=512)


class TelemetryJob(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    title: Optional[str] = Field(default=None, max_length=500)
    source_url: Optional[str] = Field(default=None, max_length=2000)
    source_channel_title: Optional[str] = Field(default=None, max_length=500)
    status: str = Field(min_length=1, max_length=40)
    progress_note: Optional[str] = Field(default=None, max_length=1000)
    error: Optional[str] = Field(default=None, max_length=2000)
    target_language: Optional[str] = Field(default=None, max_length=40)
    created_at: float
    updated_at: float
    has_video: bool = False


class ClientTelemetry(BaseModel):
    license_key: str = Field(min_length=8, max_length=256)
    machine_id: str = Field(min_length=1, max_length=256)
    user_id: int = Field(gt=0)
    app_version: Optional[str] = Field(default=None, max_length=80)
    jobs: List[TelemetryJob] = Field(default_factory=list)


class AdminCreditSet(BaseModel):
    set_to: int = Field(ge=0, le=1_000_000_000)


class AdminRoleSet(BaseModel):
    is_admin: bool

app = FastAPI(title="License Server", version="1.0.0")

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Same-origin is the production path. Explicitly opt in extra browser origins
# only when a separate trusted frontend is intentionally deployed.
_cors_origins = [
    value.strip()
    for value in os.environ.get("LICENSE_CORS_ALLOWED_ORIGINS", "").split(",")
    if value.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=bool(_cors_origins) and "*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    init_db()

def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT UNIQUE NOT NULL,
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            user_id INTEGER,
            plan_type TEXT NOT NULL,
            quota_type TEXT NOT NULL DEFAULT 'credit',
            features TEXT NOT NULL,
            expiry_date REAL,
            max_jobs INTEGER NOT NULL,
            max_tokens INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            machine_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            notes TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS license_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_id INTEGER NOT NULL,
            machine_id TEXT NOT NULL,
            jobs_used INTEGER DEFAULT 0,
            tokens_used INTEGER DEFAULT 0,
            last_check REAL NOT NULL,
            FOREIGN KEY (license_id) REFERENCES licenses(id)
        )
    """)

    columns = {row["name"] for row in cursor.execute("PRAGMA table_info(licenses)")}
    if "user_id" not in columns:
        cursor.execute("ALTER TABLE licenses ADD COLUMN user_id INTEGER")
    if "quota_type" not in columns:
        cursor.execute("ALTER TABLE licenses ADD COLUMN quota_type TEXT NOT NULL DEFAULT 'credit'")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_licenses_user_id ON licenses(user_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_nodes (
            license_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            machine_id TEXT NOT NULL,
            app_version TEXT,
            last_seen REAL NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (license_id, machine_id),
            FOREIGN KEY (license_id) REFERENCES licenses(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_jobs (
            license_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            machine_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            title TEXT,
            source_url TEXT,
            source_channel_title TEXT,
            status TEXT NOT NULL,
            progress_note TEXT,
            error TEXT,
            target_language TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            has_video INTEGER NOT NULL DEFAULT 0,
            received_at REAL NOT NULL,
            PRIMARY KEY (machine_id, job_id),
            FOREIGN KEY (license_id) REFERENCES licenses(id)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_client_jobs_user_updated "
        "ON client_jobs(user_id, updated_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_client_jobs_status ON client_jobs(status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_client_nodes_seen ON client_nodes(last_seen DESC)"
    )
    
    conn.commit()
    conn.close()


def _encode_admin_session(user_id: int, username: str) -> str:
    payload = {
        "uid": int(user_id),
        "username": str(username),
        "exp": int(time.time()) + ADMIN_SESSION_TTL_SECONDS,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
    signature = hmac.new(_admin_session_secret, encoded, hashlib.sha256).hexdigest().encode("ascii")
    return (encoded + b"." + signature).decode("ascii")


def _decode_admin_session(token: str) -> Optional[Dict]:
    try:
        encoded, supplied_signature = token.encode("ascii").rsplit(b".", 1)
        expected_signature = hmac.new(
            _admin_session_secret, encoded, hashlib.sha256
        ).hexdigest().encode("ascii")
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return None
        padding = b"=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        if int(payload.get("exp", 0)) <= int(time.time()):
            return None
        return payload
    except Exception:
        return None


def require_admin_session(
    license_admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_COOKIE_NAME),
) -> Dict:
    session = _decode_admin_session(license_admin_session or "")
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    return session


def _account_server_request(method: str, path: str, **kwargs) -> requests.Response:
    try:
        response = requests.request(
            method,
            f"{USER_MANAGEMENT_INTERNAL_URL}{path}",
            timeout=5,
            **kwargs,
        )
    except requests.RequestException as exc:
        logger.warning("Account service unavailable for %s: %s", path, exc)
        raise HTTPException(status_code=503, detail="Account service is unavailable") from exc
    if response.status_code >= 400:
        detail = None
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            pass
        raise HTTPException(status_code=response.status_code, detail=detail or "Account service request failed")
    return response

def generate_license_key() -> str:
    """Generate a unique license key"""
    return secrets.token_urlsafe(32)

def license_from_row(row) -> License:
    """Convert database row to License dataclass"""
    return License(
        id=row["id"],
        license_key=row["license_key"],
        customer_name=row["customer_name"],
        customer_email=row["customer_email"],
        user_id=row["user_id"] if "user_id" in row.keys() else None,
        plan_type=row["plan_type"],
        quota_type=row["quota_type"] if "quota_type" in row.keys() else "credit",
        features=json.loads(row["features"]),
        expiry_date=row["expiry_date"],
        max_jobs=row["max_jobs"],
        max_tokens=row["max_tokens"],
        status=row["status"],
        machine_id=row["machine_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        notes=row["notes"]
    )


def license_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> Dict:
    """Serialize a license with aggregate usage and connected devices."""
    license_record = license_from_row(row)
    usage = conn.execute(
        "SELECT COUNT(*) AS device_count, COALESCE(SUM(jobs_used), 0) AS jobs_used, "
        "COALESCE(SUM(tokens_used), 0) AS tokens_used, MAX(last_check) AS last_check "
        "FROM license_usage WHERE license_id = ?",
        (license_record.id,),
    ).fetchone()
    payload = asdict(license_record)
    payload.update({
        "device_count": int(usage["device_count"] or 0),
        "jobs_used": int(usage["jobs_used"] or 0),
        "tokens_used": int(usage["tokens_used"] or 0),
        "last_check": usage["last_check"],
    })
    if license_record.expiry_date is not None:
        seconds_left = license_record.expiry_date - datetime.now().timestamp()
        payload["remaining_days"] = max(0, int((seconds_left + 86399) // 86400))
        if seconds_left < 0 and payload["status"] == "active":
            payload["status"] = "expired"
    else:
        payload["remaining_days"] = None
    return payload

# API Endpoints

@app.get("/")
async def root():
    return {"message": "License Server v1.0.0", "status": "running"}


@app.post("/api/admin/login")
def admin_login(body: AdminLogin, response: Response):
    account_response = _account_server_request(
        "POST",
        "/api/users/login",
        json={"username": body.username.strip(), "password": body.password},
    )
    account = account_response.json()
    if not account.get("success") or not account.get("is_admin"):
        raise HTTPException(status_code=403, detail="Tài khoản không có quyền quản trị")
    token = _encode_admin_session(int(account["user_id"]), str(account.get("username") or body.username))
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        token,
        max_age=ADMIN_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=os.environ.get("LICENSE_ADMIN_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
        path="/",
    )
    return {"ok": True, "username": account.get("username")}


@app.post("/api/admin/logout")
def admin_logout(response: Response):
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/admin/session")
def admin_session(session: Dict = Depends(require_admin_session)):
    return {"authenticated": True, "username": session.get("username")}


@app.post("/api/client/telemetry")
def ingest_client_telemetry(body: ClientTelemetry):
    """Accept a small, idempotent snapshot from a local EXE over port 8000."""
    global _last_telemetry_cleanup_at
    if len(body.jobs) > 100:
        raise HTTPException(status_code=413, detail="A telemetry batch can contain at most 100 jobs")

    now = time.time()
    conn = get_db()
    try:
        license_row = conn.execute(
            "SELECT * FROM licenses WHERE license_key = ?", (body.license_key,)
        ).fetchone()
        if not license_row:
            raise HTTPException(status_code=401, detail="Unknown license")
        license_record = license_from_row(license_row)
        if license_record.status != "active":
            raise HTTPException(status_code=403, detail="License is not active")
        if license_record.expiry_date and license_record.expiry_date < now:
            raise HTTPException(status_code=403, detail="License has expired")
        if license_record.machine_id and license_record.machine_id != body.machine_id:
            raise HTTPException(status_code=403, detail="License is bound to another machine")
        if license_record.user_id is not None and int(license_record.user_id) != body.user_id:
            raise HTTPException(status_code=403, detail="License belongs to another account")

        conn.execute(
            """INSERT INTO client_nodes
               (license_id, user_id, machine_id, app_version, last_seen, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(license_id, machine_id) DO UPDATE SET
                   user_id=excluded.user_id,
                   app_version=excluded.app_version,
                   last_seen=excluded.last_seen""",
            (
                license_record.id,
                body.user_id,
                body.machine_id,
                body.app_version,
                now,
                now,
            ),
        )
        for job in body.jobs:
            conn.execute(
                """INSERT INTO client_jobs
                   (license_id, user_id, machine_id, job_id, title, source_url,
                    source_channel_title, status, progress_note, error, target_language,
                    created_at, updated_at, has_video, received_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(machine_id, job_id) DO UPDATE SET
                     license_id=excluded.license_id,
                     user_id=excluded.user_id,
                     title=excluded.title,
                     source_url=excluded.source_url,
                     source_channel_title=excluded.source_channel_title,
                     status=excluded.status,
                     progress_note=excluded.progress_note,
                     error=excluded.error,
                     target_language=excluded.target_language,
                     created_at=excluded.created_at,
                     updated_at=excluded.updated_at,
                     has_video=excluded.has_video,
                     received_at=excluded.received_at""",
                (
                    license_record.id,
                    body.user_id,
                    body.machine_id,
                    job.id,
                    job.title,
                    job.source_url,
                    job.source_channel_title,
                    job.status,
                    job.progress_note,
                    job.error,
                    job.target_language,
                    job.created_at,
                    job.updated_at,
                    int(job.has_video),
                    now,
                ),
            )
        if now - _last_telemetry_cleanup_at >= 3600:
            retention_days = max(7, int(os.environ.get("TELEMETRY_RETENTION_DAYS", "180")))
            conn.execute(
                "DELETE FROM client_jobs WHERE updated_at < ?",
                (now - retention_days * 86400,),
            )
            _last_telemetry_cleanup_at = now
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "accepted": len(body.jobs), "server_time": now}


@app.get("/api/admin/users")
def admin_users(_session: Dict = Depends(require_admin_session)):
    return _account_server_request("GET", "/api/users").json()


@app.put("/api/admin/users/{user_id}/role")
def admin_set_role(
    user_id: int,
    body: AdminRoleSet,
    _session: Dict = Depends(require_admin_session),
):
    return _account_server_request(
        "PUT", f"/api/users/{user_id}", json={"is_admin": body.is_admin}
    ).json()


@app.post("/api/admin/users/{user_id}/credits")
def admin_set_credits(
    user_id: int,
    body: AdminCreditSet,
    _session: Dict = Depends(require_admin_session),
):
    # Use the original absolute-update endpoint so this facade also works
    # while an older account-service build is still running on port 8001.
    # Newer builds expose a delta endpoint too, but requiring it here caused
    # rolling upgrades to fail with a misleading 404 Not Found.
    return _account_server_request(
        "PUT", f"/api/users/{user_id}", json={"credits": body.set_to}
    ).json()


@app.get("/api/admin/stats")
def central_admin_stats(_session: Dict = Depends(require_admin_session)):
    now = time.time()
    conn = get_db()
    try:
        rows = conn.execute("SELECT status, COUNT(*) count FROM client_jobs GROUP BY status").fetchall()
        by_status = {row["status"]: int(row["count"]) for row in rows}
        online = conn.execute(
            "SELECT COUNT(*) count FROM ("
            "SELECT machine_id FROM client_nodes WHERE last_seen >= ? "
            "UNION "
            "SELECT lu.machine_id FROM license_usage lu "
            "JOIN licenses l ON l.id = lu.license_id "
            "WHERE l.user_id IS NOT NULL AND lu.last_check >= ?"
            ")",
            (now - 300, now - 300),
        ).fetchone()["count"]
        jobs_last_7d = conn.execute(
            "SELECT COUNT(*) count FROM client_jobs WHERE created_at >= ?", (now - 7 * 86400,)
        ).fetchone()["count"]
        reporting_users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) count FROM client_nodes"
        ).fetchone()["count"]
    finally:
        conn.close()
    return {
        "reporting_users": int(reporting_users),
        "total_jobs": sum(by_status.values()),
        "jobs_by_status": by_status,
        "jobs_last_7d": int(jobs_last_7d),
        "online_devices": int(online),
    }


@app.get("/api/admin/user-activity-summary")
def central_activity_summaries(_session: Dict = Depends(require_admin_session)):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT user_id, status, COUNT(*) count FROM client_jobs GROUP BY user_id, status"
        ).fetchall()
        seen_rows = conn.execute(
            "SELECT user_id, MAX(last_seen) last_seen, "
            "COUNT(DISTINCT machine_id) device_count FROM ("
            "SELECT user_id, machine_id, last_seen FROM client_nodes "
            "UNION ALL "
            "SELECT l.user_id, lu.machine_id, lu.last_check FROM license_usage lu "
            "JOIN licenses l ON l.id = lu.license_id WHERE l.user_id IS NOT NULL"
            ") GROUP BY user_id"
        ).fetchall()
    finally:
        conn.close()
    result: Dict[str, Dict] = {}
    for row in rows:
        key = str(row["user_id"])
        item = result.setdefault(
            key, {"user_id": row["user_id"], "total_jobs": 0, "queue_count": 0, "by_status": {}}
        )
        count = int(row["count"])
        item["by_status"][row["status"]] = count
        item["total_jobs"] += count
        if row["status"] in {"queued", "running", "review"}:
            item["queue_count"] += count
    for row in seen_rows:
        key = str(row["user_id"])
        item = result.setdefault(
            key, {"user_id": row["user_id"], "total_jobs": 0, "queue_count": 0, "by_status": {}}
        )
        item["last_seen"] = row["last_seen"]
        item["device_count"] = int(row["device_count"])
        item["online"] = float(row["last_seen"]) >= time.time() - 300
    return result


@app.get("/api/admin/users/{user_id}/activity")
def central_user_activity(
    user_id: int,
    limit: int = 100,
    _session: Dict = Depends(require_admin_session),
):
    limit = max(1, min(limit, 200))
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM client_jobs WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        counts = conn.execute(
            "SELECT status, COUNT(*) count FROM client_jobs WHERE user_id = ? GROUP BY status",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    by_status = {row["status"]: int(row["count"]) for row in counts}
    return {
        "user_id": user_id,
        "total_jobs": sum(by_status.values()),
        "queue_count": sum(by_status.get(name, 0) for name in ("queued", "running", "review")),
        "by_status": by_status,
        "jobs": [
            {
                "id": row["job_id"],
                "title": row["title"],
                "source_url": row["source_url"],
                "source_channel_title": row["source_channel_title"],
                "status": row["status"],
                "progress_note": row["progress_note"],
                "error": row["error"],
                "target_language": row["target_language"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "has_video": bool(row["has_video"]),
            }
            for row in rows
        ],
    }

@app.post("/api/licenses", response_model=Dict)
async def create_license(
    license_data: LicenseCreate,
    _session: Dict = Depends(require_admin_session),
):
    """Create a new license"""
    if license_data.quota_type not in VALID_QUOTA_TYPES:
        raise HTTPException(status_code=400, detail="quota_type must be credit or time")
    conn = get_db()
    cursor = conn.cursor()
    
    # Generate license key
    license_key = generate_license_key()
    
    # Calculate expiry date
    expiry_date = None
    if license_data.expiry_days:
        expiry_date = (datetime.now() + timedelta(days=license_data.expiry_days)).timestamp()
    
    now = datetime.now().timestamp()
    
    try:
        cursor.execute("""
            INSERT INTO licenses (
                license_key, customer_name, customer_email, user_id, plan_type, quota_type,
                features, expiry_date, max_jobs, max_tokens, status,
                machine_id, created_at, updated_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            license_key,
            license_data.customer_name,
            license_data.customer_email,
            license_data.user_id,
            license_data.plan_type,
            license_data.quota_type,
            json.dumps(license_data.features),
            expiry_date,
            license_data.max_jobs,
            license_data.max_tokens,
            "active",
            license_data.machine_id,
            now,
            now,
            license_data.notes
        ))
        
        conn.commit()
        license_id = cursor.lastrowid
        conn.close()
        
        return {
            "success": True,
            "license_key": license_key,
            "license_id": license_id,
            "message": "License created successfully"
        }
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="License key already exists")

@app.post("/api/licenses/validate", response_model=LicenseResponse)
async def validate_license(validation: LicenseValidation):
    """Validate a license key"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM licenses WHERE license_key = ?
    """, (validation.license_key,))
    
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return LicenseResponse(
            valid=False,
            license=None,
            error="License key not found"
        )
    
    license = license_from_row(row)
    
    # Check if license is revoked
    if license.status != "active":
        conn.close()
        return LicenseResponse(
            valid=False,
            license=asdict(license),
            error=f"License is {license.status}"
        )
    
    # Check if license is expired
    if license.expiry_date and datetime.now().timestamp() > license.expiry_date:
        # Update status to expired
        cursor.execute("""
            UPDATE licenses SET status = 'expired', updated_at = ?
            WHERE id = ?
        """, (datetime.now().timestamp(), license.id))
        conn.commit()
        conn.close()
        
        return LicenseResponse(
            valid=False,
            license={**asdict(license), "status": "expired"},
            error="License has expired"
        )
    
    # Check machine binding
    if license.machine_id and license.machine_id != validation.machine_id:
        conn.close()
        return LicenseResponse(
            valid=False,
            license=None,
            error="License is bound to a different machine"
        )
    
    # Get or create usage record
    cursor.execute("""
        SELECT * FROM license_usage
        WHERE license_id = ? AND machine_id = ?
    """, (license.id, validation.machine_id))
    
    usage_row = cursor.fetchone()
    
    if usage_row:
        jobs_used = usage_row["jobs_used"]
        tokens_used = usage_row["tokens_used"]
        
        # Update last check
        cursor.execute("""
            UPDATE license_usage SET last_check = ?
            WHERE id = ?
        """, (datetime.now().timestamp(), usage_row["id"]))
    else:
        jobs_used = 0
        tokens_used = 0
        
        # Create usage record
        cursor.execute("""
            INSERT INTO license_usage (license_id, machine_id, jobs_used, tokens_used, last_check)
            VALUES (?, ?, ?, ?, ?)
        """, (license.id, validation.machine_id, 0, 0, datetime.now().timestamp()))
    
    conn.commit()
    conn.close()
    
    return LicenseResponse(
        valid=True,
        license={
            **asdict(license),
            "jobs_used": jobs_used,
            "tokens_used": tokens_used
        },
        error=None
    )

@app.post("/api/licenses/{license_id}/usage")
async def update_usage(license_id: int, usage_data: Dict):
    """Update license usage (jobs, tokens)"""
    conn = get_db()
    cursor = conn.cursor()
    
    machine_id = usage_data.get("machine_id")
    jobs_delta = usage_data.get("jobs_delta", 0)
    tokens_delta = usage_data.get("tokens_delta", 0)
    
    if not machine_id:
        conn.close()
        raise HTTPException(status_code=400, detail="machine_id is required")
    
    cursor.execute("""
        SELECT * FROM license_usage
        WHERE license_id = ? AND machine_id = ?
    """, (license_id, machine_id))
    
    usage_row = cursor.fetchone()
    
    if usage_row:
        cursor.execute("""
            UPDATE license_usage
            SET jobs_used = jobs_used + ?, tokens_used = tokens_used + ?, last_check = ?
            WHERE id = ?
        """, (jobs_delta, tokens_delta, datetime.now().timestamp(), usage_row["id"]))
    else:
        cursor.execute("""
            INSERT INTO license_usage (license_id, machine_id, jobs_used, tokens_used, last_check)
            VALUES (?, ?, ?, ?, ?)
        """, (license_id, machine_id, jobs_delta, tokens_delta, datetime.now().timestamp()))
    
    conn.commit()
    conn.close()
    
    return {"success": True}

@app.get("/api/licenses", response_model=List[Dict])
async def list_licenses(_session: Dict = Depends(require_admin_session)):
    """List all licenses"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM licenses ORDER BY created_at DESC")
    rows = cursor.fetchall()
    licenses = [license_payload(conn, row) for row in rows]
    conn.close()
    return licenses


@app.get("/api/licenses/by-user/{user_id}", response_model=Dict)
async def get_license_by_user(user_id: int):
    """Return the latest active license assigned by the admin to a web user."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM licenses WHERE user_id = ? AND status = 'active' "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    # Licenses created before the user_id column existed were associated by
    # customer email. Resolve that legacy link once, then persist it so all
    # later EXE polls and telemetry stay on the indexed user_id path.
    if not row:
        try:
            account = _account_server_request("GET", f"/api/users/{user_id}").json()
        except HTTPException:
            account = {}
        email = str(account.get("email") or "").strip()
        if email:
            row = conn.execute(
                "SELECT * FROM licenses WHERE user_id IS NULL AND status = 'active' "
                "AND customer_email = ? COLLATE NOCASE ORDER BY created_at DESC LIMIT 1",
                (email,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE licenses SET user_id = ?, updated_at = ? WHERE id = ? AND user_id IS NULL",
                    (user_id, time.time(), row["id"]),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM licenses WHERE id = ?", (row["id"],)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="License not found")
    payload = license_payload(conn, row)
    conn.close()
    if payload["status"] == "expired":
        raise HTTPException(status_code=404, detail="Active license has expired")
    return {"license": payload}

@app.get("/api/licenses/{license_id}", response_model=Dict)
async def get_license(
    license_id: int,
    _session: Dict = Depends(require_admin_session),
):
    """Get license by ID"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM licenses WHERE id = ?", (license_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="License not found")

    payload = license_payload(conn, row)
    conn.close()
    return payload

@app.put("/api/licenses/{license_id}", response_model=Dict)
async def update_license(
    license_id: int,
    update_data: LicenseUpdate,
    _session: Dict = Depends(require_admin_session),
):
    """Update license"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM licenses WHERE id = ?", (license_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="License not found")
    if update_data.quota_type is not None and update_data.quota_type not in VALID_QUOTA_TYPES:
        conn.close()
        raise HTTPException(status_code=400, detail="quota_type must be credit or time")
    if update_data.status is not None and update_data.status not in VALID_LICENSE_STATUSES:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid license status")
    
    license = license_from_row(row)
    
    # Build update query
    updates = []
    params = []
    
    if update_data.status is not None:
        updates.append("status = ?")
        params.append(update_data.status)

    if update_data.user_id is not None:
        updates.append("user_id = ?")
        params.append(update_data.user_id)

    if update_data.plan_type is not None:
        updates.append("plan_type = ?")
        params.append(update_data.plan_type)

    if update_data.quota_type is not None:
        updates.append("quota_type = ?")
        params.append(update_data.quota_type)
    
    if update_data.clear_expiry:
        updates.append("expiry_date = ?")
        params.append(None)
    elif update_data.expiry_days is not None:
        expiry_date = (datetime.now() + timedelta(days=update_data.expiry_days)).timestamp()
        updates.append("expiry_date = ?")
        params.append(expiry_date)
    
    if update_data.max_jobs is not None:
        updates.append("max_jobs = ?")
        params.append(update_data.max_jobs)
    
    if update_data.max_tokens is not None:
        updates.append("max_tokens = ?")
        params.append(update_data.max_tokens)
    
    if update_data.features is not None:
        updates.append("features = ?")
        params.append(json.dumps(update_data.features))
    
    if update_data.notes is not None:
        updates.append("notes = ?")
        params.append(update_data.notes)
    
    if updates:
        updates.append("updated_at = ?")
        params.append(datetime.now().timestamp())
        params.append(license_id)
        
        query = f"UPDATE licenses SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        conn.commit()
    
    conn.close()
    
    return {"success": True, "message": "License updated successfully"}

@app.delete("/api/licenses/{license_id}", response_model=Dict)
async def delete_license(
    license_id: int,
    _session: Dict = Depends(require_admin_session),
):
    """Delete license"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM license_usage WHERE license_id = ?", (license_id,))
    cursor.execute("DELETE FROM licenses WHERE id = ?", (license_id,))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "License deleted successfully"}

@app.get("/api/licenses/{license_id}/usage")
async def get_license_usage(
    license_id: int,
    _session: Dict = Depends(require_admin_session),
):
    """Get license usage statistics"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM license_usage WHERE license_id = ?
    """, (license_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    usage = []
    for row in rows:
        usage.append({
            "machine_id": row["machine_id"],
            "jobs_used": row["jobs_used"],
            "tokens_used": row["tokens_used"],
            "last_check": row["last_check"]
        })
    
    return {"license_id": license_id, "usage": usage}


# Authenticated, same-origin facade used by the admin panel. The legacy
# /api/licenses routes remain available for older EXE builds during rollout;
# new administrative clients should only use these routes.
@app.get("/api/admin/licenses", response_model=List[Dict])
async def admin_list_licenses(_session: Dict = Depends(require_admin_session)):
    return await list_licenses()


@app.post("/api/admin/licenses", response_model=Dict)
async def admin_create_license(
    body: LicenseCreate,
    _session: Dict = Depends(require_admin_session),
):
    return await create_license(body)


@app.put("/api/admin/licenses/{license_id}", response_model=Dict)
async def admin_update_license(
    license_id: int,
    body: LicenseUpdate,
    _session: Dict = Depends(require_admin_session),
):
    return await update_license(license_id, body)


@app.delete("/api/admin/licenses/{license_id}", response_model=Dict)
async def admin_delete_license(
    license_id: int,
    _session: Dict = Depends(require_admin_session),
):
    return await delete_license(license_id)


@app.get("/api/admin/licenses/{license_id}/usage")
async def admin_license_usage(
    license_id: int,
    _session: Dict = Depends(require_admin_session),
):
    return await get_license_usage(license_id)

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.on_event("startup")
async def initialize_database() -> None:
    """Apply idempotent schema migrations before serving requests."""
    init_db()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
