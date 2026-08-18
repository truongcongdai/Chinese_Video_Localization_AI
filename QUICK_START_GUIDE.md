# Hướng dẫn nhanh - Admin và User

## ADMIN Cần làm gì

### 1. Triển khai License Server trên Server (192.168.6.10)

```bash
# Upload license_server folder lên server
scp -r license_server root@192.168.6.10:/opt/

# SSH vào server
ssh root@192.168.6.10
cd /opt/license_server
pip install -r requirements.txt

# Chạy license server
python server.py
```

Hoặc chạy như service (khuyên dùng):
```bash
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
systemctl enable license-server
systemctl start license-server
```

### 2. Truy cập Admin Panel

Mở browser: `http://192.168.6.10:8000/static/admin.html`

### 3. Tạo License cho User

1. Điền thông tin khách hàng
2. Click "Create License"
3. Copy license key sinh ra
4. Gửi license key cho user

### 4. Quản lý Licenses

- Xem tất cả licenses trong admin panel
- Edit/revoke licenses khi cần
- Xem usage statistics (jobs, tokens used)

---

## USER Cần làm gì ở máy họ

### 1. Cài đặt Web UI

```bash
# Download hoặc copy folder dist
cd dist
python generate_env.py
```

### 2. Cấu hình License Server URL

**Cách 1: Dùng script (Windows - Run as Administrator)**
```cmd
cd D:\path\to\Chinese_Video_Localization_AI
set_license_server.bat http://192.168.6.10:8000
```

**Cách 2: Set Environment Variable**
```cmd
# Windows
setx LICENSE_SERVER_URL "http://192.168.6.10:8000" /M

# Linux/Mac
export LICENSE_SERVER_URL=http://192.168.6.10:8000
```

**Cách 3: Dùng default (không cần cấu hình)**
Nếu không cấu hình, sẽ tự động dùng `http://113.160.14.1:8000`

### 3. Chạy Web UI

```bash
cd dist
python run_web.py
```

### 4. Đăng ký và Activate License

1. Mở browser: `http://localhost:8080`
2. Đăng ký tài khoản (nhận free trial 25 tokens, 30 ngày)
3. Nhập license key từ admin để activate unlimited access
4. Bắt đầu sử dụng

---

## Tóm tắt nhanh

| Bước | Admin | User |
|------|-------|------|
| 1 | Triển khai license server trên 192.168.6.10:8000 | Cài đặt web UI |
| 2 | Truy cập admin panel | Cấu hình LICENSE_SERVER_URL |
| 3 | Tạo license cho user | Chạy web UI |
| 4 | Gửi license key cho user | Đăng ký và activate license |
| 5 | Quản lý licenses và xem usage | Sử dụng hệ thống |

---

## Troubleshooting

### Admin: License server không chạy
```bash
systemctl status license-server
netstat -tlnp | grep 8000
```

### User: Không kết nối được license server
```bash
# Test connection
curl http://192.168.6.10:8000/

# Kiểm tra cấu hình
# Windows:
reg query "HKLM\SOFTWARE\ChineseVideoLocalizationAI" /v LICENSE_SERVER_URL
# Linux:
echo $LICENSE_SERVER_URL
```

### User: License validation failed
- Kiểm tra license key đúng
- Kiểm tra license status trong admin panel
- Kiểm tra machine ID match
