# License Server

Lightweight centralized license management system for Chinese Video Localization AI.

## Features

- **Centralized License Management**: Create, manage, and revoke licenses from a single server
- **Machine Binding**: Bind licenses to specific machines for security
- **Usage Tracking**: Track jobs and tokens usage per license
- **Web Admin Panel**: Easy-to-use admin interface for license management
- **REST API**: Simple API for license validation and usage updates
- **SQLite Database**: No external database required - uses SQLite for minimal resource usage

## Requirements

- **CPU**: 1 core minimum
- **RAM**: 512MB minimum
- **OS**: Ubuntu 20.04+ (or any Linux)
- **Python**: 3.8+
- **Disk**: 100MB minimum

## Installation

### Option 1: Ubuntu Deployment (Recommended)

1. Upload files to your Ubuntu server:
   ```bash
   scp license_server/server.py root@your-server:/opt/license-server/
   scp license_server/static/admin.html root@your-server:/opt/license-server/static/
   scp license_server/deploy_ubuntu.sh root@your-server:/opt/license-server/
   ```

2. Run the deployment script:
   ```bash
   ssh root@your-server
   cd /opt/license-server
   chmod +x deploy_ubuntu.sh
   ./deploy_ubuntu.sh
   ```

3. Configure your domain name in Nginx:
   ```bash
   nano /etc/nginx/sites-available/license-server
   # Update server_name to your domain
   nginx -t
   systemctl restart nginx
   ```

4. Enable HTTPS (optional but recommended):
   ```bash
   certbot --nginx -d your-domain.com
   ```

### Option 2: Local Development

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the server:
   ```bash
   python server.py
   ```

3. Access admin panel at `http://localhost:8000/static/admin.html`

## API Endpoints

### Create License
```http
POST /api/licenses
Content-Type: application/json

{
  "customer_name": "John Doe",
  "customer_email": "john@example.com",
  "plan_type": "pro",
  "features": ["video_localization", "transcription", "translation"],
  "expiry_days": 365,
  "max_jobs": 100,
  "max_tokens": 1000,
  "machine_id": null,
  "notes": "Annual Pro license"
}
```

### Validate License
```http
POST /api/licenses/validate
Content-Type: application/json

{
  "license_key": "your-license-key-here",
  "machine_id": "machine-unique-id"
}
```

### Update License Usage
```http
POST /api/licenses/{license_id}/usage
Content-Type: application/json

{
  "machine_id": "machine-unique-id",
  "jobs_delta": 1,
  "tokens_delta": 1
}
```

### List Licenses
```http
GET /api/licenses
```

### Get License
```http
GET /api/licenses/{license_id}
```

### Update License
```http
PUT /api/licenses/{license_id}
Content-Type: application/json

{
  "status": "revoked",
  "expiry_days": null,
  "max_jobs": 200,
  "max_tokens": 2000,
  "notes": "Updated limits"
}
```

### Delete License
```http
DELETE /api/licenses/{license_id}
```

### Get License Usage
```http
GET /api/licenses/{license_id}/usage
```

## Client Configuration

To use the license server with the Chinese Video Localization AI application:

### Windows (Recommended)

1. Run `set_license_server.bat` as Administrator:
   ```bash
   set_license_server.bat
   ```
   Enter your license server URL when prompted.

2. Or set system environment variable:
   - Right-click "This PC" → Properties → Advanced system settings
   - Environment Variables → System variables → New
   - Variable name: `LICENSE_SERVER_URL`
   - Variable value: `http://your-license-server.com`

### Linux/Mac

Set environment variable:
```bash
export LICENSE_SERVER_URL=http://your-license-server.com
```

### Priority Order

The application checks configuration in this order:
1. Windows Registry (Windows only)
2. System environment variable
3. .env file (for backward compatibility)

Leave empty to use local database (backward compatibility).

## Admin Panel

Access the admin panel at `http://your-server/static/admin.html`

Features:
- Create new licenses
- View all licenses with status
- Edit license details (status, limits, expiry)
- Delete licenses
- View usage statistics

## Service Management

```bash
# Start service
systemctl start license-server

# Stop service
systemctl stop license-server

# Restart service
systemctl restart license-server

# Check status
systemctl status license-server

# View logs
journalctl -u license-server -f
```

## Security Considerations

1. **HTTPS**: Always use HTTPS in production
2. **Firewall**: Configure firewall to only allow necessary ports
3. **Authentication**: Add authentication to the admin panel (not implemented yet)
4. **Rate Limiting**: Consider adding rate limiting to API endpoints
5. **Backup**: Regularly backup the `licenses.db` file

## Backup

```bash
# Backup database
cp /opt/license-server/licenses.db /backup/licenses.db.$(date +%Y%m%d)

# Restore database
cp /backup/licenses.db.20240101 /opt/license-server/licenses.db
```

## Troubleshooting

### Service won't start
```bash
# Check logs
journalctl -u license-server -n 50

# Check port conflicts
netstat -tlnp | grep 8000
```

### Database locked
```bash
# Restart service
systemctl restart license-server
```

### Nginx errors
```bash
# Test nginx configuration
nginx -t

# Check nginx logs
tail -f /var/log/nginx/error.log
```

## License Plans

### Basic
- 50 jobs
- 500 tokens
- No expiry

### Pro
- 100 jobs
- 1000 tokens
- 1 year expiry

### Enterprise
- Unlimited jobs
- Unlimited tokens
- Custom expiry
- Priority support
