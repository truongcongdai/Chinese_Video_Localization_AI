# Hướng dẫn triển khai License Server và Web UI

## Tổng quan

Hệ thống sử dụng License Server tập trung để quản lý licenses và user accounts:
- **License Server**: Chạy trên server (192.168.6.10 / 113.160.14.1:8000)
- **Web UI Clients**: Kết nối đến license server để xác thực và quản lý users
- **Admin Panel**: Quản lý licenses tại `http://192.168.6.10:8000/static/admin.html`

## Kiến trúc

```
┌─────────────────────────────────────────────────────────────┐
│                    License Server                             │
│              (192.168.6.10 / 113.160.14.1:8000)              │
│  - licenses.db (SQLite)                                      │
│  - Admin Panel: /static/admin.html                           │
│  - API: /api/licenses/*                                       │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
┌───────┴───────┐   ┌─────────┴─────────┐   ┌──────┴──────┐
│  Web UI Local  │   │  Web UI Client 1 │   │ Web UI ...  │
│  (localhost)   │   │  (192.168.6.x)   │   │  (External) │
│  :8080         │   │  :8080           │   │  :8080      │
└───────────────┘   └───────────────────┘   └─────────────┘
```

## Bước 1: Triển khai License Server trên Server

### 1.1 Chuẩn bị server

Server cần có:
- IP nội bộ: 192.168.6.10
- IP ra ngoài: 113.160.14.1
- Python 3.8+
- Port 8000 mở

### 1.2 Cài đặt License Server

```bash
# Upload folder license_server lên server
scp -r license_server root@192.168.6.10:/opt/

# SSH vào server
ssh root@192.168.6.10

# Cài đặt dependencies
cd /opt/license_server
pip install -r requirements.txt

# Chạy license server (lắng nghe trên tất cả interface)
python server.py
```

License server sẽ chạy với `host="0.0.0.0"` nên sẽ accessible từ cả 2 IP.

### 1.3 Chạy như service (Ubuntu)

```bash
# Tạo systemd service
nano /etc/systemd/system/license-server.service
```

Nội dung:
```ini
[Unit]
Description=License Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/license_server
ExecStart=/usr/bin/python3 /opt/license_server/server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
# Enable và start service
systemctl enable license-server
systemctl start license-server
systemctl status license-server
```

### 1.4 Kiểm tra

```bash
# Test từ server
curl http://localhost:8000/
curl http://192.168.6.10:8000/
curl http://113.160.14.1:8000/

# Test từ máy khác
curl http://192.168.6.10:8000/
```

## Bước 2: Cấu hình Web UI Client kết nối License Server

### 2.1 Trên máy local (localhost:8080)

**Cách 1: Dùng script (Windows - Run as Administrator)**
```cmd
cd D:\code\Chinese_Video_Localization_AI
set_license_server.bat http://192.168.6.10:8000
```

**Cách 2: Set Environment Variable**
```cmd
# Windows
setx LICENSE_SERVER_URL "http://192.168.6.10:8000" /M

# Linux/Mac
export LICENSE_SERVER_URL=http://192.168.6.10:8000
```

**Cách 3: Dùng default (113.160.14.1:8000)**
Nếu không cấu hình, web UI sẽ tự động dùng default URL: `http://113.160.14.1:8000`

### 2.2 Trên các máy client khác

Làm tương tự như trên, dùng IP nội bộ `192.168.6.10:8000` cho client trong LAN, hoặc `113.160.14.1:8000` cho client từ bên ngoài.

### 2.3 Kiểm tra cấu hình

**Windows:**
```cmd
reg query "HKLM\SOFTWARE\ChineseVideoLocalizationAI" /v LICENSE_SERVER_URL
```

**Linux/Mac:**
```bash
echo $LICENSE_SERVER_URL
```

## Bước 3: Quản lý Licenses và Users

### 3.1 Truy cập Admin Panel

Mở browser: `http://192.168.6.10:8000/static/admin.html`

Hoặc từ bên ngoài: `http://113.160.14.1:8000/static/admin.html`

### 3.2 Tạo License

1. Điền thông tin:
   - Customer Name: Tên khách hàng
   - Customer Email: Email
   - Plan Type: basic/pro/enterprise
   - Features: Các tính năng (comma separated)
   - Expiry Days: Số ngày (null = lifetime)
   - Max Jobs: Số job tối đa
   - Max Tokens: Số token tối đa
   - Notes: Ghi chú

2. Click "Create License"
3. Copy license key được sinh ra

 license key này sẽ được dùng để activate trên client.

### 3.3 User Registration với License Server

Khi `USE_LICENSE_SERVER=true`:
- User có thể đăng ký và nhận free trial (25 tokens, 30 ngày)
- User có thể activate license key để có unlimited access
- Tất cả user data được lưu trong local database của client
- License validation và usage tracking được gửi đến license server

### 3.4 Xem Usage Statistics

Trong admin panel, click vào license để xem:
- Jobs used
- Tokens used
- Machine ID
- Last check time

## Bước 4: Chạy Web UI

### 4.1 Trên máy local

```bash
cd dist
python generate_env.py
# Edit .env nếu cần
python run_web.py
```

Web UI sẽ:
- Tự động kết nối đến license server (theo cấu hình)
- Hiển thị license activation UI
- Gửi usage data đến license server

### 4.2 Trên các máy client

Làm tương tự, đảm bảo đã cấu hình `LICENSE_SERVER_URL` đúng.

## Troubleshooting

### License server không accessible

```bash
# Kiểm tra service
systemctl status license-server

# Kiểm tra port
netstat -tlnp | grep 8000

# Kiểm tra firewall
sudo ufw allow 8000
# hoặc
sudo iptables -A INPUT -p tcp --dport 8000 -j ACCEPT
```

### Client không kết nối được license server

```bash
# Test từ client
curl http://192.168.6.10:8000/
curl http://113.160.14.1:8000/

# Kiểm tra LICENSE_SERVER_URL
# Windows:
reg query "HKLM\SOFTWARE\ChineseVideoLocalizationAI" /v LICENSE_SERVER_URL
# Linux:
echo $LICENSE_SERVER_URL
```

### License validation failed

1. Kiểm tra license key đúng
2. Kiểm tra license status trong admin panel (active, not expired, not revoked)
3. Kiểm tra machine ID match (nếu license bị bind)

### Database issues

```bash
# Backup license server database
cp /opt/license_server/licenses.db /backup/licenses.db.$(date +%Y%m%d)

# Restore
cp /backup/licenses.db.20240101 /opt/license-server/licenses.db
```

## Security Notes

- License server hiện dùng HTTP, nên cân nhắc upgrade sang HTTPS cho production
- Admin panel chưa có authentication, nên cần restrict access (firewall, VPN)
- LICENSE_SERVER_URL được lưu trong Windows Registry (cần admin để sửa)
- License keys được lưu trong local SQLite database, không trong config files

## Network Diagram

```
Internet
    │
    ▼
┌─────────────────────────────────────┐
│  Firewall / Router                  │
│  113.160.14.1:8000 → 192.168.6.10:8000 │
└─────────────────────────────────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │  License Server      │
        │  192.168.6.10:8000    │
        │  (host="0.0.0.0")     │
        └───────────────────────┘
                    │
        ┌───────────┼───────────┐
        │           │           │
┌───────┴───┐ ┌─────┴─────┐ ┌───┴──────┐
│ Localhost │ │ LAN Client│ │ External │
│ :8080     │ │ :8080     │ │ :8080    │
└───────────┘ └───────────┘ └──────────┘
```
