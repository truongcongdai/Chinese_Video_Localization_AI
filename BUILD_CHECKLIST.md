# Checklist Build Windows EXE

## ✅ Trước khi bắt đầu

### Máy Build (Windows)
- [ ] Windows 10/11 64-bit
- [ ] RAM tối thiểu 16GB (khuyến nghị 32GB)
- [ ] Ổ cứng trống tối thiểu 20GB
- [ ] SSD (khuyến nghị để build nhanh hơn)

## ✅ Bước 1: Chuẩn bị Python

- [ ] Tải Python 3.10 hoặc 3.11 từ https://www.python.org/downloads/
- [ ] Chạy installer
- [ ] **QUAN TRỌNG**: Check "Add Python to PATH"
- [ ] Verify: `python --version` (trong CMD)
- [ ] Verify: `pip --version` (trong CMD)

## ✅ Bước 2: Cài Visual C++ Build Tools

- [ ] Tải từ https://visualstudio.microsoft.com/visual-cpp-build-tools/
- [ ] Chạy installer
- [ ] Chọn "Desktop development with C++"
- [ ] Cài đặt (5-10GB)
- [ ] Restart máy sau khi cài xong

## ✅ Bước 3: Chuẩn bị Project

- [ ] Copy project sang máy Windows
- [ ] Đặt ở đường dẫn ngắn (ví dụ: `C:\Chinese_Video_Localization_AI`)
- [ ] Tránh đường dẫn có space hoặc ký tự đặc biệt
- [ ] Mở CMD tại thư mục project

## ✅ Bước 4: Cài Dependencies

### Cách tự động (Khuyên dùng)
- [ ] Chạy `install_build.bat`
- [ ] Chờ cài đặt hoàn tất

### Cách thủ công
- [ ] Tạo virtual environment: `python -m venv venv`
- [ ] Activate: `venv\Scripts\activate`
- [ ] Upgrade pip: `python -m pip install --upgrade pip`
- [ ] Cài dependencies: `pip install -r requirements.txt`
- [ ] Cài Nuitka: `pip install nuitka`

## ✅ Bước 5: Cấu hình License System

### Tạo RSA Key Pair
- [ ] Chọn cách tạo key:
  - [ ] Cách 1: Qua Admin UI (sau khi build)
  - [ ] Cách 2: Tạo trước với script `generate_keys.py`

### Cấu hình .env
- [ ] Copy `.env.example` → `.env`
- [ ] Mở `.env` và cấu hình:
  - [ ] `LICENSE_ENABLED=true`
  - [ ] `LICENSE_PUBLIC_KEY=<paste_public_key>`
  - [ ] `LICENSE_PRIVATE_KEY=<paste_private_key>`
  - [ ] `LICENSE_FILE_PATH=./local_data/license.key`
  - [ ] `LICENSE_HARDWARE_BINDING=false`
  - [ ] `WEB_SESSION_SECRET=random-secret-key`
  - [ ] `WEB_PORT=8080`
  - [ ] `LOG_LEVEL=INFO`

## ✅ Bước 6: Build EXE với Nuitka

- [ ] Activate virtual environment (nếu dùng): `venv\Scripts\activate`
- [ ] Chạy `build_nuitka.bat`
- [ ] Chờ build hoàn tất (20-60 phút lần đầu)
- [ ] Kiểm tra file `ChineseVideoLocalizationAI.exe` đã tạo

## ✅ Bước 7: Chuẩn bị Package Phân Phối

- [ ] Tạo thư mục `Release`
- [ ] Copy `ChineseVideoLocalizationAI.exe` → `Release\`
- [ ] Copy `.env.example` → `Release\.env.example`
- [ ] Tạo thư mục `Release\local_data`
- [ ] Tạo thư mục `Release\temp`
- [ ] Tạo file `Release\README.txt` với hướng dẫn cài đặt
- [ ] Zip thư mục Release → `ChineseVideoLocalizationAI.zip`

## ✅ Bước 8: Tạo License cho User

### Chạy app trên máy admin
- [ ] Chạy `ChineseVideoLocalizationAI.exe`
- [ ] Mở browser: `http://localhost:8080`
- [ ] Đăng ký tài khoản admin đầu tiên

### Tạo license qua Admin UI
- [ ] Login admin
- [ ] Vào "Quản trị" → "Quản lý License"
- [ ] Điền thông tin user:
  - [ ] User ID
  - [ ] Tên
  - [ ] Email
  - [ ] Loại License (trial/monthly/lifetime)
  - [ ] Thời hạn (ngày)
  - [ ] Token limit (0 = vô hạn)
  - [ ] Tính năng được phép (trial: bỏ chọn tất cả)
- [ ] Bấm "Tạo License"
- [ ] Copy license key

## ✅ Bước 9: Gửi cho User

- [ ] File `ChineseVideoLocalizationAI.zip`
- [ ] License key
- [ ] Hướng dẫn cài đặt (README.txt)

## ✅ Bước 10: Test trên máy User (Tùy chọn)

- [ ] Giải nén package
- [ ] Đổi tên `.env.example` → `.env`
- [ ] Cấu hình `.env`
- [ ] Tạo file `local_data\license.key` với license key
- [ ] Chạy `ChineseVideoLocalizationAI.exe`
- [ ] Mở browser: `http://localhost:8080`
- [ ] Kiểm tra các tính năng theo license

## 📋 Thời gian ước tính

- Cài Python: 5-10 phút
- Cài Visual C++ Build Tools: 10-20 phút
- Cài dependencies: 15-30 phút
- Build EXE (lần đầu): 20-60 phút
- Build EXE (lần sau): 10-30 phút
- Tổng cộng: **1-2 giờ** lần đầu

## ⚠️ Các lỗi thường gặp

### Python not found
- [ ] Kiểm tra Python đã add to PATH
- [ ] Reinstall Python với "Add to PATH"

### Visual C++ Build Tools not found
- [ ] Cài Visual C++ Build Tools
- [ ] Restart máy

### Out of memory
- [ ] Tăng RAM lên 16GB+
- [ ] Đóng các ứng dụng khác

### Module not found
- [ ] Chạy `pip install -r requirements.txt` lại
- [ ] Kiểm tra Python version (3.10/3.11)

### License system not enabled
- [ ] Kiểm tra LICENSE_ENABLED=true trong .env
- [ ] Restart ứng dụng

## 📊 Kết quả

Sau khi hoàn tất:
- ✅ File exe: `ChineseVideoLocalizationAI.exe` (2-5GB)
- ✅ Package: `ChineseVideoLocalizationAI.zip`
- ✅ License key cho user
- ✅ Hướng dẫn cài đặt

## 🎯 Tips

- Sử dụng SSD để build nhanh hơn
- Tăng CPU cores để giảm thời gian build
- Build 1 lần, dùng nhiều lần
- Backup private key cẩn thận
- Test license trước khi gửi cho user
