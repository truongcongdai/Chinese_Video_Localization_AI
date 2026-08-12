#!/usr/bin/env python3
"""
Lightweight License Server for centralized license management
Runs on Ubuntu with minimal resources (1 CPU, 512MB RAM)
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
from pydantic import BaseModel
import uvicorn

# Database setup
DB_PATH = "licenses.db"

@dataclass
class License:
    id: Optional[int]
    license_key: str
    customer_name: str
    customer_email: str
    plan_type: str  # basic, pro, enterprise
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
    plan_type: str
    features: List[str]
    expiry_days: Optional[int] = None  # None = lifetime
    max_jobs: int = 100
    max_tokens: int = 1000
    machine_id: Optional[str] = None
    notes: Optional[str] = None

class LicenseUpdate(BaseModel):
    status: Optional[str] = None
    expiry_days: Optional[int] = None
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

app = FastAPI(title="License Server", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT UNIQUE NOT NULL,
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            plan_type TEXT NOT NULL,
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
    
    conn.commit()
    conn.close()

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
        plan_type=row["plan_type"],
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

# API Endpoints

@app.get("/")
async def root():
    return {"message": "License Server v1.0.0", "status": "running"}

@app.post("/api/licenses", response_model=Dict)
async def create_license(license_data: LicenseCreate):
    """Create a new license"""
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
                license_key, customer_name, customer_email, plan_type,
                features, expiry_date, max_jobs, max_tokens, status,
                machine_id, created_at, updated_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            license_key,
            license_data.customer_name,
            license_data.customer_email,
            license_data.plan_type,
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
    if license.status == "revoked":
        conn.close()
        return LicenseResponse(
            valid=False,
            license=None,
            error="License has been revoked"
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
            license=None,
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
async def list_licenses():
    """List all licenses"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM licenses ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    licenses = []
    for row in rows:
        license = license_from_row(row)
        licenses.append(asdict(license))
    
    return licenses

@app.get("/api/licenses/{license_id}", response_model=Dict)
async def get_license(license_id: int):
    """Get license by ID"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM licenses WHERE id = ?", (license_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="License not found")
    
    license = license_from_row(row)
    return asdict(license)

@app.put("/api/licenses/{license_id}", response_model=Dict)
async def update_license(license_id: int, update_data: LicenseUpdate):
    """Update license"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM licenses WHERE id = ?", (license_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="License not found")
    
    license = license_from_row(row)
    
    # Build update query
    updates = []
    params = []
    
    if update_data.status is not None:
        updates.append("status = ?")
        params.append(update_data.status)
    
    if update_data.expiry_days is not None:
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
async def delete_license(license_id: int):
    """Delete license"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM licenses WHERE id = ?", (license_id,))
    cursor.execute("DELETE FROM license_usage WHERE license_id = ?", (license_id,))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "License deleted successfully"}

@app.get("/api/licenses/{license_id}/usage")
async def get_license_usage(license_id: int):
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

if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=8000)
