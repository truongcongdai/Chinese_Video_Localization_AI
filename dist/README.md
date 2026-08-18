# Chinese Video Localization AI - Web UI

## Hướng dẫn nhanh cho User

### Bước 1: Cài đặt

```bash
# Tạo file .env với cấu hình mặc định
python generate_env.py
```

### Bước 2: Cấu hình License Server

**Cách 1: Dùng script (Windows - Run as Administrator)**
```cmd
set_license_server.bat http://192.168.6.10:8000
```

**Cách 2: Dùng default (không cần cấu hình)**
Nếu không cấu hình, sẽ tự động dùng `http://113.160.14.1:8000`

**Cách 3: Set Environment Variable**
```cmd
# Windows
setx LICENSE_SERVER_URL "http://192.168.6.10:8000" /M

# Linux/Mac
export LICENSE_SERVER_URL=http://192.168.6.10:8000
```

### Bước 3: Chạy Web UI

```bash
python run_web.py
```

Mở browser: `http://localhost:8080`

### Bước 4: Đăng ký và Activate License

1. Đăng ký tài khoản (nhận free trial 25 tokens, 30 ngày)
2. Nhập license key từ admin để activate unlimited access
3. Bắt đầu sử dụng

---

## Tài liệu hướng dẫn

- **QUICK_START_GUIDE.md** - Hướng dẫn tóm tắt cho admin và user
- **LICENSE_SERVER_DEPLOYMENT.md** - Hướng dẫn triển khai chi tiết

---

## Yêu cầu hệ thống

- Python 3.8+
- Windows/Linux/Mac
- Kết nối internet đến license server

---

## Troubleshooting

### Không kết nối được license server
```bash
# Test connection
curl http://192.168.6.10:8000/

# Kiểm tra cấu hình
# Windows:
reg query "HKLM\SOFTWARE\ChineseVideoLocalizationAI" /v LICENSE_SERVER_URL
# Linux:
echo $LICENSE_SERVER_URL
```

### License validation failed
- Kiểm tra license key đúng
- Liên hệ admin để kiểm tra license status

---

## Liên hệ

Nếu cần hỗ trợ, liên hệ admin của bạn.
