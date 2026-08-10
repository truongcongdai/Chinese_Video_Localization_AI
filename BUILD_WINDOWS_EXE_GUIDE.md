# Hướng dẫn Build Windows EXE với License System

## Yêu cầu hệ thống

### Máy Build (Windows)
- **OS**: Windows 10/11 64-bit
- **RAM**: Tối thiểu 16GB (khuyến nghị 32GB)
- **Ổ cứng**: Tối thiểu 20GB trống
- **Python**: 3.10 hoặc 3.11 (tải từ python.org)
- **Visual C++ Build Tools**: Bắt buộc cho Nuitka

### Máy User (Windows)
- **OS**: Windows 10/11 64-bit
- **RAM**: Tối thiểu 8GB (khuyến nghị 16GB)
- **Ổ cứng**: Tối thiểu 10GB trống

## Bước 1: Chuẩn bị môi trường Windows

### 1.1 Cài đặt Python

1. Tải Python 3.10 hoặc 3.11 từ: https://www.python.org/downloads/
2. Chạy installer → **QUAN TRỌNG**: Check "Add Python to PATH"
3. Verify installation:
```cmd
python --version
pip --version
```

### 1.2 Cài đặt Visual C++ Build Tools (Bắt buộc cho Nuitka)

1. Tải từ: https://visualstudio.microsoft.com/visual-cpp-build-tools/
2. Chạy installer
3. Chọn "Desktop development with C++"
4. Cài đặt (khoảng 5-10GB)
5. Restart máy sau khi cài xong

### 1.3 Copy project sang Windows

1. Copy toàn bộ thư mục project sang máy Windows
2. Đặt ở đường dẫn ngắn, ví dụ: `C:\Chinese_Video_Localization_AI`
3. Tránh đường dẫn có space hoặc ký tự đặc biệt

## Bước 2: Cài đặt Dependencies

### Cách tự động (Khuyên dùng)

Chạy script cài đặt tự động:

```cmd
cd C:\Chinese_Video_Localization_AI
install_build.bat
```

### Cách thủ công

```cmd
cd C:\Chinese_Video_Localization_AI
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
pip install nuitka
```

## Bước 3: Cấu hình License System

### 3.1 Tạo RSA Key Pair

Có 2 cách:

#### Cách 1: Qua Admin UI (sau khi build xong)

1. Chạy app → Login admin → Quản trị
2. Bấm "🔑 Tạo key pair mới"
3. Copy private/public key

#### Cách 2: Tạo trước khi build

Tạo file `generate_keys.py`:

```python
from universal_video_ai.license import LicenseCrypto

private_key, public_key = LicenseCrypto.generate_key_pair()
print("=== PRIVATE KEY (Lưu cẩn thận - chỉ admin có) ===")
print(private_key)
print("\n=== PUBLIC KEY (Cho client) ===")
print(public_key)
```

Chạy:
```cmd
python generate_keys.py
```

### 3.2 Cấu hình .env

Copy `.env.example` → `.env` và cấu hình:

```env
# License System
LICENSE_ENABLED=true
LICENSE_PUBLIC_KEY=<paste_public_key_here>
LICENSE_PRIVATE_KEY=<paste_private_key_here>
LICENSE_FILE_PATH=./local_data/license.key
LICENSE_HARDWARE_BINDING=false

# Cấu hình khác
WEB_SESSION_SECRET=random-secret-key-change-this
WEB_PORT=8080
LOG_LEVEL=INFO
```

## Bước 4: Build EXE với Nuitka (Khuyên dùng)

### 4.1 Build với script tự động

```cmd
cd C:\Chinese_Video_Localization_AI
build_nuitka.bat
```

### 4.2 Build thủ công

```cmd
cd C:\Chinese_Video_Localization_AI
venv\Scripts\activate
python -m nuitka ^
    --standalone ^
    --onefile ^
    --enable-plugin=anti-bloat ^
    --enable-plugin=numpy ^
    --enable-plugin=pylint-warnings ^
    --windows-console-mode=force ^
    --windows-icon-from-ico=none ^
    --output-filename=ChineseVideoLocalizationAI.exe ^
    --include-data-dir=src/universal_video_ai/web/static=universal_video_ai/web/static ^
    --include-data-file=.env.example=.env.example ^
    --include-module=universal_video_ai.web.app ^
    --include-module=universal_video_ai.orchestrator ^
    --include-module=universal_video_ai.config ^
    --include-module=universal_video_ai.license ^
    --include-package=universal_video_ai ^
    --assume-yes-for-downloads ^
    --show-progress ^
    --show-memory ^
    --show-release-memory ^
    scripts/run_web.py
```

### 4.3 Thời gian build

- **Lần đầu**: 20-60 phút (tùy CPU)
- **Lần sau**: 10-30 phút (có cache)

File exe sẽ nằm ở thư mục hiện tại: `ChineseVideoLocalizationAI.exe`

## Bước 5: Chuẩn bị Package phân phối

### 5.1 Tạo thư mục phân phối

```cmd
mkdir Release
copy ChineseVideoLocalizationAI.exe Release\
copy .env Release\.env.example
mkdir Release\local_data
mkdir Release\temp
```

### 5.2 Tạo file README cho user

Tạo file `Release\README.txt`:

```
========================================
Chinese Video Localization AI
========================================

CÀI ĐẶT:
1. Giải nén thư mục này vào bất kỳ đâu (ví dụ: C:\VideoAI)
2. Đổi tên .env.example thành .env
3. Mở .env và cấu hình:
   - WEB_SESSION_SECRET: Đổi thành chuỗi ngẫu nhiên
   - WEB_PORT: Cổng chạy (mặc định 8080)
   - LICENSE_PUBLIC_KEY: Paste public key từ admin
4. Chạy ChineseVideoLocalizationAI.exe
5. Mở browser: http://localhost:8080

TÀI KHOẢN ĐẦU TIÊN:
- Tài khoản đầu tiên sẽ là admin
- Đăng ký tài khoản mới → tự động thành admin

CÀI ĐẶT LICENSE:
1. Nhận license key từ admin
2. Tạo file local_data\license.key
3. Paste license key vào file
4. Restart ứng dụng

HỖ TRỢ:
- Liên hệ admin nếu gặp lỗi
- Kiểm tra log trong console nếu có lỗi
```

### 5.3 Zip package

```cmd
cd Release
powershell Compress-Archive -Path * -DestinationPath ChineseVideoLocalizationAI.zip
```

## Bước 6: Tạo License cho User

### 6.1 Chạy app trên máy admin

```cmd
cd C:\Chinese_Video_Localization_AI
ChineseVideoLocalizationAI.exe
```

### 6.2 Tạo license qua Admin UI

1. Login admin → Quản trị
2. Vào "Quản lý License"
3. Điền thông tin:
   - User ID: ID duy nhất (ví dụ: user_001)
   - Tên: Tên user
   - Email: Email user
   - Loại License: trial/monthly/lifetime
   - Thời hạn: Số ngày
   - Token limit: 0 = vô hạn, hoặc số token
   - Tính năng: Chọn tính năng được phép (trial: bỏ chọn tất cả)
4. Bấm "Tạo License"
5. Copy license key

### 6.7 Gửi cho user

Gửi:
- File `ChineseVideoLocalizationAI.zip`
- License key
- Hướng dẫn cài đặt

## Troubleshooting

### Lỗi "Python not found"

- Kiểm tra Python đã được add to PATH
- Reinstall Python với option "Add to PATH"

### Lỗi "Visual C++ Build Tools not found"

- Cài Visual C++ Build Tools
- Restart máy
- Chạy lại build

### Lỗi "Out of memory"

- Tăng RAM lên 16GB+
- Đóng các ứng dụng khác
- Giảm `--jobs` parameter trong Nuitka

### Lỗi "Module not found"

- Kiểm tra tất cả dependencies đã cài
- Chạy `pip install -r requirements.txt` lại
- Kiểm tra Python version (3.10 hoặc 3.11)

### Lỗi "License system not enabled"

- Kiểm tra LICENSE_ENABLED=true trong .env
- Restart ứng dụng

### File exe quá lớn

- Bình thường: 2-5GB (do PyTorch)
- Không thể giảm đáng kể mà vẫn giữ đầy đủ tính năng

## Checklist Build

- [ ] Python 3.10/3.11 đã cài + add to PATH
- [ ] Visual C++ Build Tools đã cài
- [ ] Project đã copy sang Windows
- [ ] Dependencies đã cài (pip install -r requirements.txt)
- [ ] Nuitka đã cài (pip install nuitka)
- [ ] .env đã cấu hình với license keys
- [ ] Build Nuitka đã chạy thành công
- [ ] File exe đã tạo (ChineseVideoLocalizationAI.exe)
- [ ] Package phân phối đã chuẩn bị (Release folder)
- [ ] License key đã tạo cho user
- [ ] Hướng dẫn cài đặt đã gửi kèm

## Build với PyInstaller (Alternatives)

Nếu Nuitka gặp vấn đề, có thể dùng PyInstaller:

```cmd
pip install pyinstaller
pyinstaller build_exe.spec --clean
```

File exe sẽ nằm trong `dist\ChineseVideoLocalizationAI\`

**Lưu ý**: PyInstaller bảo mật kém hơn Nuitka (dễ decompile)

## Bảo mật Code

### Nuitka (Khuyên dùng)
- Python → C → Machine code
- Khó decompile
- Cần chuyên gia reverse engineering

### PyInstaller
- Python bytecode (.pyc)
- Dễ decompile với pyinstxtractor + decompyle3
- Chỉ làm khó người dùng thông thường

## Tối ưu hóa Build

### Giảm thời gian build

1. Sử dụng SSD thay vì HDD
2. Tăng CPU cores (sử dụng --jobs parameter)
3. Giảm số lần build (build 1 lần, dùng nhiều lần)

### Giảm kích thước file

1. Sử dụng UPX compression (mặc định trong Nuitka)
2. Loại bỏ unused dependencies
3. Sử dụng PyInstaller (nhưng bảo mật kém hơn)

## Kết luận

Sau khi build hoàn tất:
1. File exe: `ChineseVideoLocalizationAI.exe` (2-5GB)
2. Package phân phối: `ChineseVideoLocalizationAI.zip`
3. License key: Tạo qua Admin UI
4. Hướng dẫn: README.txt

Gửi package + license key cho user. User chỉ cần giải nén, cấu hình .env, cài license key và chạy.
