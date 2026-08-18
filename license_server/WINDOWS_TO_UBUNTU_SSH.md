# License Server - Windows to Ubuntu SSH Setup Guide

Guide to deploy license server on Ubuntu from Windows using SSH.

## Prerequisites

### Windows Machine
- Windows 10/11 with OpenSSH client (built-in) or PuTTY
- License server files from `license_server/` directory

### Ubuntu Server
- Ubuntu 20.04+ server with SSH access
- Root or sudo access
- Internet connection

## Option 1: Using Windows OpenSSH (Recommended)

### Step 1: Enable OpenSSH Client on Windows

**Windows 10/11 (Built-in):**
```cmd
# Check if OpenSSH is installed
ssh -V

# If not installed, install via PowerShell (Run as Administrator)
Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH*'
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

### Step 2: Connect to Ubuntu Server

```cmd
ssh root@your-server-ip
# Or with specific user
ssh username@your-server-ip
```

Enter password when prompted.

### Step 3: Upload License Server Files

```cmd
# From Windows, in the license_server directory
cd D:\code\Chinese_Video_Localization_AI\license_server

# Upload files to Ubuntu server
scp server.py root@your-server-ip:/opt/license-server/
scp static/admin.html root@your-server-ip:/opt/license-server/static/
scp requirements.txt root@your-server-ip:/opt/license-server/
scp deploy_ubuntu.sh root@your-server-ip:/opt/license-server/
```

### Step 4: Deploy via SSH

```cmd
# SSH into server
ssh root@your-server-ip

# Create directory
mkdir -p /opt/license-server/static

# Navigate to directory
cd /opt/license-server

# Make script executable
chmod +x deploy_ubuntu.sh

# Run deployment
./deploy_ubuntu.sh
```

### Step 5: Configure Domain (Optional)

```cmd
# Edit Nginx configuration
nano /etc/nginx/sites-available/license-server

# Update server_name to your domain
server_name your-domain.com;

# Test configuration
nginx -t

# Restart Nginx
systemctl restart nginx
```

### Step 6: Enable HTTPS (Optional)

```cmd
# Install Certbot
apt update
apt install certbot python3-certbot-nginx -y

# Get SSL certificate
certbot --nginx -d your-domain.com
```

## Option 2: Using PuTTY

### Step 1: Install PuTTY

Download from: https://www.putty.org/

### Step 2: Configure PuTTY

1. Open PuTTY
2. **Host Name**: your-server-ip
3. **Port**: 22
4. **Connection type**: SSH
5. Click "Open"

### Step 3: Upload Files using WinSCP

Download WinSCP from: https://winscp.net/

1. Open WinSCP
2. **Host name**: your-server-ip
3. **Port**: 22
4. **User name**: root
5. **Password**: your-password
6. Click "Login"

7. Navigate to `/opt/license-server` on server
8. Upload files from Windows:
   - `server.py`
   - `static/admin.html`
   - `requirements.txt`
   - `deploy_ubuntu.sh`

### Step 4: Deploy via PuTTY

```bash
# In PuTTY terminal
cd /opt/license-server
chmod +x deploy_ubuntu.sh
./deploy_ubuntu.sh
```

## Option 3: Using SSH Key Authentication (Recommended for Production)

### Step 1: Generate SSH Key on Windows

```cmd
# Using OpenSSH
ssh-keygen -t rsa -b 4096 -C "your-email@example.com"

# Save to default location: C:\Users\YourName\.ssh\id_rsa
# Enter passphrase (optional but recommended)
```

### Step 2: Copy Public Key to Ubuntu Server

**Method A: Using ssh-copy-id (if available)**
```cmd
type $env:USERPROFILE\.ssh\id_rsa.pub | ssh root@your-server-ip "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

**Method B: Manual copy**
```cmd
# Copy public key content
type C:\Users\YourName\.ssh\id_rsa.pub

# On Ubuntu server
ssh root@your-server-ip
mkdir -p ~/.ssh
nano ~/.ssh/authorized_keys
# Paste the public key content
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
```

### Step 3: Test SSH Key Authentication

```cmd
ssh root@your-server-ip
# Should login without password (or with passphrase if set)
```

### Step 4: Disable Password Authentication (Optional but Recommended)

```bash
# On Ubuntu server
sudo nano /etc/ssh/sshd_config

# Change these settings:
PasswordAuthentication no
PubkeyAuthentication yes

# Restart SSH
sudo systemctl restart sshd
```

## Automated Deployment Script

Create `deploy_from_windows.bat` on Windows:

```cmd
@echo off
set SERVER_IP=your-server-ip
set SERVER_USER=root
set REMOTE_DIR=/opt/license-server

echo Deploying license server to Ubuntu server...
echo.

echo Uploading files...
scp server.py %SERVER_USER%@%SERVER_IP%:%REMOTE_DIR%/
scp static/admin.html %SERVER_USER%@%SERVER_IP%:%REMOTE_DIR%/static/
scp requirements.txt %SERVER_USER%@%SERVER_IP%:%REMOTE_DIR%/
scp deploy_ubuntu.sh %SERVER_USER%@%SERVER_IP%:%REMOTE_DIR%/

echo.
echo Running deployment script...
ssh %SERVER_USER%@%SERVER_IP% "cd %REMOTE_DIR% && chmod +x deploy_ubuntu.sh && ./deploy_ubuntu.sh"

echo.
echo Deployment complete!
echo Access admin panel at: http://%SERVER_IP%:8000/static/admin.html
pause
```

Usage:
1. Edit `deploy_from_windows.bat` with your server IP
2. Run the script
3. Enter password when prompted (or use SSH key)

## Managing License Server via SSH

### Start/Stop Service

```cmd
ssh root@your-server-ip
systemctl start license-server
systemctl stop license-server
systemctl restart license-server
systemctl status license-server
```

### View Logs

```cmd
# Real-time logs
ssh root@your-server-ip "journalctl -u license-server -f"

# Last 100 lines
ssh root@your-server-ip "journalctl -u license-server -n 100"
```

### Backup Database

```cmd
ssh root@your-server-ip "cp /opt/license-server/licenses.db /backup/licenses.db.$(date +%%Y%%m%%d)"
```

### Update License Server

```cmd
# Upload new files
scp server.py root@your-server-ip:/opt/license-server/
scp requirements.txt root@your-server-ip:/opt/license-server/

# Restart service
ssh root@your-server-ip "systemctl restart license-server"
```

## Troubleshooting

### Connection Refused

```cmd
# Check if SSH is running on server
ssh root@your-server-ip "systemctl status ssh"

# Check firewall
ssh root@your-server-ip "ufw status"
```

### Permission Denied

```cmd
# Check file permissions
ssh root@your-server-ip "ls -la /opt/license-server"

# Fix permissions
ssh root@your-server-ip "chmod +x /opt/license-server/deploy_ubuntu.sh"
```

### SCP Fails

```cmd
# Ensure directory exists on server
ssh root@your-server-ip "mkdir -p /opt/license-server/static"
```

### Service Won't Start

```cmd
# Check service logs
ssh root@your-server-ip "journalctl -u license-server -n 50"

# Check if port is in use
ssh root@your-server-ip "netstat -tlnp | grep 8000"
```

## Security Best Practices

### 1. Use SSH Keys Only
Disable password authentication after setting up SSH keys

### 2. Change Default SSH Port
```bash
# On Ubuntu server
sudo nano /etc/ssh/sshd_config
# Change Port 22 to another port (e.g., 2222)
sudo systemctl restart sshd
```

### 3. Configure Firewall
```bash
# Allow only your IP
ssh root@your-server-ip "ufw allow from YOUR_IP_ADDRESS to any port 22"
ssh root@your-server-ip "ufw enable"
```

### 4. Use Fail2Ban
```bash
# On Ubuntu server
ssh root@your-server-ip "apt install fail2ban -y"
ssh root@your-server-ip "systemctl enable fail2ban"
ssh root@your-server-ip "systemctl start fail2ban"
```

## Quick Reference

### Common SSH Commands

```cmd
# Connect to server
ssh root@your-server-ip

# Connect with specific port
ssh -p 2222 root@your-server-ip

# Execute single command
ssh root@your-server-ip "systemctl status license-server"

# Copy file to server
scp local_file.txt root@your-server-ip:/remote/path/

# Copy directory to server
scp -r local_dir/ root@your-server-ip:/remote/path/

# Copy file from server
scp root@your-server-ip:/remote/file.txt local_path/
```

### License Server Management

```cmd
# Check status
ssh root@your-server-ip "systemctl status license-server"

# Restart service
ssh root@your-server-ip "systemctl restart license-server"

# View logs
ssh root@your-server-ip "journalctl -u license-server -f"

# Backup database
ssh root@your-server-ip "cp /opt/license-server/licenses.db /backup/"
```

## Next Steps

After deployment:

1. **Configure Client**: Use `set_license_server.bat` on Windows to point to your Ubuntu server
2. **Test Connection**: Access admin panel at `http://your-server-ip:8000/static/admin.html`
3. **Create License**: Create first license through admin panel
4. **Setup HTTPS**: Configure SSL certificate for production use
