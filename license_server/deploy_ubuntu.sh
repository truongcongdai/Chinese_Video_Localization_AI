#!/bin/bash
# License Server Deployment Script for Ubuntu
# Requirements: Ubuntu 20.04+, 1 CPU, 512MB RAM minimum

set -e

echo "=========================================="
echo "License Server Deployment for Ubuntu"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root${NC}"
    exit 1
fi

# Configuration
APP_NAME="license-server"
APP_DIR="/opt/$APP_NAME"
SERVICE_NAME="$APP_NAME"
PORT=8000

echo -e "${GREEN}[1/8] Updating system packages...${NC}"
apt update && apt upgrade -y

echo -e "${GREEN}[2/8] Installing dependencies...${NC}"
apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx

echo -e "${GREEN}[3/8] Creating application directory...${NC}"
mkdir -p $APP_DIR
cd $APP_DIR

echo -e "${GREEN}[4/8] Setting up Python virtual environment...${NC}"
python3 -m venv venv
source venv/bin/activate

echo -e "${GREEN}[5/8] Installing Python dependencies...${NC}"
pip install --upgrade pip
pip install fastapi uvicorn[standard] pydantic python-multipart

echo -e "${GREEN}[6/8] Copying application files...${NC}"
# Copy server.py and static files to the server
# You'll need to manually upload these files or use git
echo "Please upload the following files to $APP_DIR:"
echo "  - server.py"
echo "  - static/admin.html"
echo ""
read -p "Press Enter after uploading files..."

echo -e "${GREEN}[7/8] Creating systemd service...${NC}"
cat > /etc/systemd/system/$SERVICE_NAME.service <<EOF
[Unit]
Description=License Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin"
ExecStart=$APP_DIR/venv/bin/uvicorn server:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl start $SERVICE_NAME

echo -e "${GREEN}[8/8] Setting up Nginx reverse proxy...${NC}"
cat > /etc/nginx/sites-available/$APP_NAME <<EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /static {
        alias $APP_DIR/static;
    }
}
EOF

ln -sf /etc/nginx/sites-available/$APP_NAME /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

echo ""
echo -e "${GREEN}=========================================="
echo "Deployment completed successfully!"
echo "==========================================${NC}"
echo ""
echo "Service status:"
systemctl status $SERVICE_NAME --no-pager
echo ""
echo "Nginx status:"
systemctl status nginx --no-pager
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Configure your domain name in /etc/nginx/sites-available/$APP_NAME"
echo "2. Run: certbot --nginx -d your-domain.com"
echo "3. Access admin panel at: http://your-server-ip/static/admin.html"
echo "4. API endpoints available at: http://your-server-ip/api/"
echo ""
echo -e "${YELLOW}Service management:${NC}"
echo "  Start:   systemctl start $SERVICE_NAME"
echo "  Stop:    systemctl stop $SERVICE_NAME"
echo "  Restart: systemctl restart $SERVICE_NAME"
echo "  Status:  systemctl status $SERVICE_NAME"
echo "  Logs:    journalctl -u $SERVICE_NAME -f"
echo ""
