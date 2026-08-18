# License Server - Windows Setup Guide

Complete guide to install and run the license server on Windows.

## Requirements

- **OS**: Windows 10/11 or Windows Server 2016+
- **Python**: 3.8+ (Download from https://www.python.org/downloads/)
- **RAM**: 512MB minimum
- **Disk**: 100MB minimum
- **Port**: 8000 (default)

## Installation

### Step 1: Install Python

1. Download Python from https://www.python.org/downloads/
2. Run installer and **check "Add Python to PATH"**
3. Verify installation:
   ```cmd
   python --version
   pip --version
   ```

### Step 2: Install Dependencies

```cmd
cd license_server
pip install -r requirements.txt
```

### Step 3: Run the Server

**Option A: Run directly (Development)**
```cmd
python server.py
```
Server will start at `http://localhost:8000`

**Option B: Run as Windows Service (Production)**

#### Install NSSM (Non-Sucking Service Manager)

1. Download NSSM from https://nssm.cc/download
2. Extract to `C:\nssm`
3. Add `C:\nssm` to system PATH

#### Install as Service

```cmd
nssm install ChineseVideoLicenseServer
```

Configure the service:
- **Path**: `python.exe` (full path, e.g., `C:\Python39\python.exe`)
- **Startup directory**: Full path to license_server folder
- **Arguments**: `server.py`
- **Service name**: ChineseVideoLicenseServer

Click "Install service"

#### Manage Service

```cmd
# Start service
nssm start ChineseVideoLicenseServer

# Stop service
nssm stop ChineseVideoLicenseServer

# Restart service
nssm restart ChineseVideoLicenseServer

# Remove service
nssm remove ChineseVideoLicenseServer confirm
```

#### View Service Logs

```cmd
# Open service editor
nssm edit ChineseVideoLicenseServer
```

Or check logs in:
- Windows Event Viewer → Windows Logs → Application
- Or custom log file if configured

### Step 4: Configure Firewall

Allow port 8000 through Windows Firewall:

```cmd
netsh advfirewall firewall add rule name="Chinese Video License Server" dir=in action=allow protocol=TCP localport=8000
```

### Step 5: Access Admin Panel

Open browser: `http://localhost:8000/static/admin.html`

## Configuration

### Change Port

Edit `server.py` and modify:
```python
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)  # Change 8000 to desired port
```

### Database Location

By default, database is created as `licenses.db` in the server directory.

To change location, edit `server.py`:
```python
DB_PATH = "path/to/your/licenses.db"
```

## Production Deployment

### Option 1: IIS Reverse Proxy

1. Install IIS with CGI and URL Rewrite modules
2. Create reverse proxy rule to forward requests to localhost:8000
3. Configure HTTPS with SSL certificate

### Option 2: Nginx for Windows

1. Download Nginx for Windows from http://nginx.org/en/download.html
2. Edit `nginx.conf`:
   ```nginx
   server {
       listen 80;
       server_name your-domain.com;
       
       location / {
           proxy_pass http://localhost:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }
   }
   ```
3. Start Nginx: `nginx.exe`

### Option 3: Cloudflare Tunnel (Recommended for Remote Access)

1. Install Cloudflare Tunnel: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/
2. Create tunnel pointing to localhost:8000
3. Get public URL from Cloudflare dashboard

## Security Hardening

### 1. Use HTTPS

If using IIS or Nginx, configure SSL certificate:
- Self-signed for internal use
- Let's Encrypt for public domain
- Commercial certificate for production

### 2. Restrict Access

Edit `server.py` to add IP whitelist:
```python
ALLOWED_IPS = ["192.168.1.100", "10.0.0.50"]  # Add allowed IPs

@app.middleware("http")
async def check_ip(request: Request, call_next):
    client_ip = request.client.host
    if client_ip not in ALLOWED_IPS:
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    return await call_next(request)
```

### 3. Add API Key Authentication

Edit `server.py` to add API key check:
```python
API_KEY = "your-secret-api-key-here"

@app.middleware("http")
async def check_api_key(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        api_key = request.headers.get("X-API-Key")
        if api_key != API_KEY:
            return JSONResponse(status_code=401, content={"error": "Invalid API key"})
    return await call_next(request)
```

Update client to send API key in headers.

## Backup

### Automated Backup Script

Create `backup.bat`:
```cmd
@echo off
set BACKUP_DIR=C:\backups\license-server
set TIMESTAMP=%date:~-4,4%%date:~-7,2%%date:~-10,2%
set DB_PATH=licenses.db

if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
copy "%DB_PATH%" "%BACKUP_DIR%\licenses.db.%TIMESTAMP%"
echo Backup completed: %BACKUP_DIR%\licenses.db.%TIMESTAMP%
```

Schedule with Windows Task Scheduler:
1. Open Task Scheduler
2. Create Basic Task
3. Trigger: Daily at 2:00 AM
4. Action: Start program → `backup.bat`

### Restore

```cmd
copy C:\backups\license-server\licenses.db.20240101 licenses.db
```

## Monitoring

### Windows Performance Monitor

1. Open Performance Monitor
2. Add counters for Python process
3. Monitor CPU, Memory, Disk I/O

### Log Monitoring

View logs in real-time:
```cmd
# If using file logging
type server.log

# Or use PowerShell
Get-Content server.log -Wait -Tail 50
```

## Troubleshooting

### Port Already in Use

```cmd
# Find process using port 
netstat -ano | findstr :8000

# Kill process (replace PID with actual process ID)
taskkill /PID <PID> /F
```

### Service Won't Start

```cmd
# Check service status
sc query ChineseVideoLicenseServer

# View service logs
nssm edit ChineseVideoLicenseServer
```

### Database Locked

```cmd
# Stop service
nssm stop ChineseVideoLicenseServer

# Delete lock file if exists
del licenses.db-shm
del licenses.db-wal

# Restart service
nssm start ChineseVideoLicenseServer
```

### Python Not Found

Ensure Python is in system PATH:
```cmd
where python
```

If not found, reinstall Python with "Add to PATH" option.

### Permission Denied

Run Command Prompt as Administrator when:
- Installing service
- Modifying firewall rules
- Accessing protected directories

## Testing

### Test API Endpoints

```cmd
# Test health check
curl http://localhost:8000/

# Test license validation
curl -X POST http://localhost:8000/api/licenses/validate -H "Content-Type: application/json" -d "{\"license_key\":\"test\",\"machine_id\":\"test-machine\"}"
```

### Test from Client Machine

```cmd
# Replace with your server IP
curl http://YOUR_SERVER_IP:8000/api/licenses
```

## Uninstall

### Remove Service

```cmd
nssm stop ChineseVideoLicenseServer
nssm remove ChineseVideoLicenseServer confirm
```

### Remove Files

```cmd
# Delete license server directory
rmdir /s /q license_server

# Remove firewall rule
netsh advfirewall firewall delete rule name="Chinese Video License Server"
```

## Support

For issues or questions:
- Check logs in server directory
- Verify Python version: `python --version`
- Check dependencies: `pip list`
- Test connectivity: `telnet localhost 8000`
