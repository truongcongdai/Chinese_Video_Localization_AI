# Hệ thống License Management

## Tổng quan

Hệ thống license cho phép bạn đóng gói project thành file .exe và kiểm soát việc sử dụng của người dùng thông qua:
- **License key được encrypt** (RSA + AES)
- **Hardware binding** (tùy chọn - bind license vào máy cụ thể)
- **Token-based trial** (giới hạn số lần dùng thử)
- **Expiration date** (hạn sử dụng theo tháng/năm)
- **Admin UI** (tạo/gia hạn license trực tiếp từ web interface)

## Kiến trúc

### Components

1. **License Module** (`src/universal_video_ai/license/`)
   - `crypto.py`: RSA + AES encryption/decryption
   - `fingerprint.py`: Hardware fingerprinting (Machine ID, CPU, MAC address)
   - `manager.py`: License validation, token tracking, expiration check

2. **API Endpoints** (`src/universal_video_ai/web/app.py`)
   - `GET /api/admin/license/status`: Kiểm tra trạng thái license
   - `POST /api/admin/license/create`: Tạo license mới
   - `POST /api/admin/license/renew`: Gia hạn license
   - `POST /api/admin/license/validate`: Validate license key
   - `POST /api/admin/license/install`: Cài đặt license vào system
   - `GET /api/admin/license/generate-keys`: Tạo RSA key pair mới

3. **Admin UI** (`src/universal_video_ai/web/static/index.html`)
   - Section "Quản lý License" trong admin panel
   - Tạo license mới với các tùy chọn
   - Gia hạn license hiện tại
   - Xem trạng thái license

## Cấu hình

### 1. Cấu hình trong .env

Thêm các biến sau vào file `.env`:

```env
# License System
LICENSE_ENABLED=true
LICENSE_PUBLIC_KEY=<public_key_pem>
LICENSE_PRIVATE_KEY=<private_key_pem>
LICENSE_FILE_PATH=./local_data/license.key
LICENSE_HARDWARE_BINDING=false
```

### 2. Tạo RSA Key Pair

Có 2 cách để tạo key pair:

#### Cách 1: Qua Admin UI (Khuyên dùng)

1. Đăng nhập với tài khoản admin
2. Vào trang Quản trị
3. Bấm "🔑 Tạo key pair mới"
4. Copy private key và public key
5. Lưu vào .env

#### Cách 2: Dùng Python script

```python
from universal_video_ai.license import LicenseCrypto

private_key, public_key = LicenseCrypto.generate_key_pair()
print("Private Key:")
print(private_key)
print("\nPublic Key:")
print(public_key)
```

## Quy trình sử dụng

### Bước 1: Tạo License cho User

1. Đăng nhập admin → Quản trị
2. Điền thông tin:
   - **User ID**: ID duy nhất của user
   - **Tên**: Tên user
   - **Email**: Email user
   - **Loại License**: trial/monthly/lifetime
   - **Thời hạn**: Số ngày (30, 60, 90...)
   - **Token limit**: 0 = vô hạn, hoặc số token cụ thể (15, 20...)
   - **Bind vào hardware**: Check nếu muốn bind vào máy
   - **Ghi chú**: Ghi chú thêm
3. Bấm "🔐 Tạo License"
4. Copy license key và gửi cho user

### Bước 2: User cài đặt License

User có 2 cách để cài đặt license:

#### Cách 1: Qua Admin UI (nếu user có quyền admin)

1. Đăng nhập vào web UI
2. Vào Quản trị → Quản lý License
3. Paste license key vào
4. Bấm "Cài đặt"

#### Cách 2: Tạo file license.key thủ công

1. Tạo file `local_data/license.key` trong thư mục cài đặt
2. Paste license key vào file
3. Khởi động lại ứng dụng

### Bước 3: Gia hạn License

Khi license hết hạn:

1. Admin vào Quản trị → Gia hạn License
2. Điền User ID và số ngày muốn thêm
3. Bấm "🔄 Gia hạn License"
4. Copy license key mới và gửi cho user
5. User cài đặt license key mới như Bước 2

## Các loại License

### 1. Trial License (Dùng thử)

```json
{
  "license_type": "trial",
  "duration_days": 15,
  "token_limit": 20
}
```

- Dùng thử trong 15 ngày
- Giới hạn 20 token (20 lần xử lý video)
- Không bind hardware (mặc định)

### 2. Monthly License (Hàng tháng)

```json
{
  "license_type": "monthly",
  "duration_days": 30,
  "token_limit": 0
}
```

- Hết hạn sau 30 ngày
- Không giới hạn token (token_limit = 0)
- Có thể bind hardware

### 3. Lifetime License (Vĩnh viễn)

```json
{
  "license_type": "lifetime",
  "duration_days": 0,
  "token_limit": 0
}
```

- Không hết hạn
- Không giới hạn token
- Có thể bind hardware

## Hardware Binding

Hardware binding giúp ngăn chặn việc chia sẻ license giữa các máy khác nhau.

### Cách bật:

1. Tạo license với option "Bind vào hardware" = true
2. License sẽ được bind vào máy tạo license
3. Nếu user cài trên máy khác → license sẽ bị từ chối

### Cách tắt:

Set `LICENSE_HARDWARE_BINDING=false` trong .env

## Token System

Mỗi lần user tạo job (xử lý video), 1 token sẽ bị trừ:

- Nếu `token_limit = 0`: Không giới hạn
- Nếu `token_limit > 0`: Khi hết token → không thể tạo job mới

### Kiểm tra token:

Admin có thể xem số token còn/đã dùng trong:
- Admin UI → Quản lý License → Kiểm tra trạng thái

## Bảo mật

### Mức độ bảo mật:

1. **Nuitka Build**: Python → C → Machine code (khó decompile)
2. **RSA 2048-bit**: Encryption mạnh cho license key
3. **AES-256**: Encryption cho license data
4. **Hardware Fingerprint**: Ngăn chia sẻ license

### Lưu ý quan trọng:

- **Private Key KHÔNG BAO GIỜ được gửi cho client**
- Chỉ cần Public Key trên máy client để validate
- Private Key chỉ dùng trên máy admin để tạo license
- Backup private key cẩn thận (mất = không thể tạo license mới)

## Build EXE với License System

### Cập nhật requirements.txt

Đã thêm `cryptography>=41.0.0` vào requirements.txt

### Build với Nuitka (Khuyên dùng)

```cmd
# Cài Visual C++ Build Tools trước
# Sau đó chạy:
build_nuitka.bat
```

License module đã được thêm vào build script.

### Build với PyInstaller

```cmd
pyinstaller build_exe.spec --clean
```

License module đã được thêm vào spec file.

## Troubleshooting

### Lỗi "License system not enabled"

- Kiểm tra `LICENSE_ENABLED=true` trong .env
- Khởi động lại ứng dụng

### Lỗi "Private key not configured"

- Thêm `LICENSE_PRIVATE_KEY` vào .env (chỉ cho admin)
- Hoặc tạo key pair mới qua Admin UI

### Lỗi "License expired"

- License đã hết hạn sử dụng
- Admin cần gia hạn license cho user

### Lỗi "License is bound to a different machine"

- Hardware binding được bật
- License được cài trên máy khác
- Admin cần tạo license mới cho máy này

### Lỗi "License token đã hết hạn"

- User đã dùng hết token
- Admin cần gia hạn license hoặc tăng token limit

## Workflow cho Admin

### Setup lần đầu:

1. Cài đặt project trên máy admin
2. Set `LICENSE_ENABLED=true` trong .env
3. Tạo RSA key pair qua Admin UI
4. Lưu private/public key vào .env
5. Bắt đầu tạo license cho user

### Tạo license cho user mới:

1. Thu thập thông tin user (ID, tên, email)
2. Chọn loại license (trial/monthly/lifetime)
3. Set thời hạn và token limit
4. Tạo license key
5. Gửi license key cho user

### Gia hạn license:

1. User báo license hết hạn
2. Admin vào Gia hạn License
3. Điền User ID và số ngày thêm
4. Tạo license key mới
5. Gửi license key mới cho user

## Workflow cho User

### Cài đặt lần đầu:

1. Nhận file exe từ admin
2. Chạy exe
3. Nhận license key từ admin
4. Cài đặt license key
5. Bắt đầu sử dụng

### Khi license hết hạn:

1. Liên hệ admin để gia hạn
2. Nhận license key mới
3. Cài đặt license key mới
4. Tiếp tục sử dụng

## Ví dụ thực tế

### Ví dụ 1: Tạo trial license 15 ngày, 20 token

```
User ID: user_001
Tên: Nguyễn Văn A
Email: nguyenvana@example.com
Loại: trial
Thời hạn: 15 ngày
Token limit: 20
Bind hardware: false
```

### Ví dụ 2: Tạo monthly license, không giới hạn token

```
User ID: user_002
Tên: Trần Thị B
Email: tranthib@example.com
Loại: monthly
Thời hạn: 30 ngày
Token limit: 0 (vô hạn)
Bind hardware: true
```

### Ví dụ 3: Gia hạn thêm 30 ngày

```
User ID: user_001
Thêm ngày: 30
Token limit mới: (trống - giữ nguyên)
```

## Tích hợp với hệ thống hiện tại

License system hoạt động song song với hệ thống credit hiện tại:

- **Credit**: Quản lý nội bộ bởi admin (nạp/xóa credit)
- **License**: Quản lý offline qua license key (hạn sử dụng/token)

Hai hệ thống độc lập, có thể dùng 1 hoặc cả 2:

- Chỉ dùng credit: Set `LICENSE_ENABLED=false`
- Chỉ dùng license: Set `JOB_COST_CREDITS=0`
- Dùng cả 2: Cả 2 đều được check

## Feature-Based Licensing (Kiểm soát từng tính năng)

Hệ thống license hỗ trợ kiểm soát truy cập từng tính năng riêng biệt:

### Các tính năng có sẵn:

1. **localization** - Dịch Video
2. **trend** - Quét Trend
3. **content-os** - Content OS
4. **ai-video** - Tạo Video AI
5. **affiliate** - Product Ad

### Cách hoạt động:

- **Khi tạo license**: Admin chọn các tính năng được phép
- **Khi user cài license**: Chỉ các tính năng được chọn sẽ hiển thị trong UI
- **Trial license**: Bỏ chọn tất cả → user được dùng thử tất cả tính năng
- **Paid license**: Chọn các tính năng user đã mua

### Ví dụ:

#### License chỉ cho Dịch Video:
```
enabled_features: ["localization"]
→ User chỉ thấy tab "Dịch Video"
```

#### License cho Dịch Video + Content OS:
```
enabled_features: ["localization", "content-os"]
→ User chỉ thấy 2 tab này
```

#### Trial license (tất cả tính năng):
```
enabled_features: null hoặc []
→ User thấy tất cả 5 tab
```

### API Endpoint:

**GET /api/license/features** (public)
```json
{
  "enabled": true,
  "features": ["localization", "content-os"]
}
```

Frontend gọi endpoint này khi load và ẩn/hiện các tab tương ứng.

## Kết luận

Hệ thống license cung cấp giải pháp offline hoàn chỉnh để:
- Bảo vệ code (Nuitka + encryption)
- Kiểm soát thời gian sử dụng (expiration date)
- Giới hạn dùng thử (token system)
- Ngăn chia sẻ (hardware binding)
- Kiểm soát từng tính năng (feature-based licensing)
- Quản lý dễ dàng (Admin UI)

Liên hệ hỗ trợ nếu gặp vấn đề trong quá trình cài đặt hoặc sử dụng.
