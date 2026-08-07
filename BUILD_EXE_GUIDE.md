# Hướng dẫn đóng gói project thành file .exe

## Tổng quan

Project này có thể đóng gói thành file .exe để chạy trên Windows mà không cần cài đặt Python. Tuy nhiên, do có nhiều dependencies nặng (PyTorch, Diffusers, Whisper), file exe sẽ rất lớn (2-5 GB).

## Lựa chọn phương án đóng gói

### 1. PyInstaller (Nhanh, dễ dùng)
- **Ưu điểm**: Build nhanh (10-30 phút), dễ cấu hình
- **Nhược điểm**: Code dễ decompile (chỉ đóng gói bytecode)
- **Dùng khi**: Không quan trọng bảo mật code

### 2. Nuitka (Khuyên dùng - Bảo mật tốt hơn)
- **Ưu điểm**: Compile Python → C → Machine code, khó decompile hơn nhiều
- **Nhược điểm**: Build lâu hơn (20-60 phút), cần Visual C++ Build Tools
- **Dùng khi**: Cần bảo mật code tốt hơn PyInstaller

### 3. Server Deployment (Bảo mật tuyệt đối)
- **Ưu điểm**: Code không bao giờ rời khỏi server
- **Nhược điểm**: Chi phí vận hành hàng tháng
- **Dùng khi**: Cần bảo mật tuyệt đối

## Yêu cầu hệ thống

### Máy build (nơi đóng gói)
- Windows 10/11
- Python 3.10 hoặc cao hơn
- RAM tối thiểu 8GB (khuyến nghị 16GB)
- Ổ cứng trống ít nhất 10GB
- **Cho Nuitka**: Visual C++ Build Tools (cần cài thêm)

### Máy chạy (nơi sử dụng exe)
- Windows 10/11
- RAM tối thiểu 8GB (khuyến nghị 16GB)
- Ổ cứng trống ít nhất 10GB
- GPU NVIDIA (khuyến nghị để tăng tốc xử lý video)

## Cách đóng gói

### Phương án A: PyInstaller (Nhanh, dễ dùng)

#### Cách 1: Sử dụng script tự động

1. Copy toàn bộ project sang máy Windows
2. Mở Command Prompt hoặc PowerShell tại thư mục project
3. Chạy script build:
   ```cmd
   build_exe.bat
   ```
4. Chờ quá trình build hoàn thành (có thể mất 10-30 phút)
5. File exe sẽ nằm trong thư mục `dist\ChineseVideoLocalizationAI\`

#### Cách 2: Dùng PyInstaller thủ công

1. Cài đặt PyInstaller:
   ```cmd
   pip install pyinstaller
   ```

2. Cài đặt dependencies:
   ```cmd
   pip install -r requirements.txt
   ```

3. Build exe:
   ```cmd
   pyinstaller build_exe.spec --clean
   ```

4. Copy file .env vào thư mục dist:
   ```cmd
   copy .env dist\ChineseVideoLocalizationAI\.env
   ```

### Phương án B: Nuitka (Bảo mật tốt hơn - Khuyên dùng)

#### Bước 1: Cài Visual C++ Build Tools

1. Download từ: https://visualstudio.microsoft.com/visual-cpp-build-tools/
2. Chạy installer
3. Chọn "Desktop development with C++"
4. Cài đặt (khoảng 5-10GB)

#### Bước 2: Build với Nuitka

1. Copy project sang máy Windows
2. Mở Command Prompt tại thư mục project
3. Chạy script build:
   ```cmd
   build_nuitka.bat
   ```
4. Chờ quá trình build (20-60 phút, lâu hơn PyInstaller)
5. File exe sẽ là `ChineseVideoLocalizationAI.exe` trong thư mục hiện tại

#### Bước 3: Cấu hình

1. Rename file env:
   ```cmd
   ren ChineseVideoLocalizationAI.env .env
   ```
2. Mở file .env và cấu hình:
   ```env
   WEB_SESSION_SECRET=your-secret-key-here
   WEB_PORT=8080
   ```

## Cấu hình trước khi chạy

1. Mở file `.env` trong thư mục `dist\ChineseVideoLocalizationAI\`
2. Thiết lập các biến môi trường quan trọng:
   ```env
   WEB_SESSION_SECRET=your-secret-key-here
   WEB_PORT=8080
   LOG_LEVEL=INFO
   ```
3. Cấu hình các API key cần thiết (OpenAI, Google, etc.) nếu có

## Cách chạy

### Chạy trực tiếp
1. Mở thư mục `dist\ChineseVideoLocalizationAI\`
2. Double-click vào `ChineseVideoLocalizationAI.exe`
3. Mở browser và truy cập `http://localhost:8080`

### Chạy qua Command Prompt
```cmd
cd dist\ChineseVideoLocalizationAI
ChineseVideoLocalizationAI.exe
```

## Lưu ý quan trọng

### Về kích thước file
- File exe sẽ rất lớn (2-5 GB) do bao gồm PyTorch và các thư viện ML
- Đây là bình thường, không thể giảm đáng kể mà vẫn giữ đầy đủ tính năng

### Về bảo mật code

### PyInstaller
- Đóng gói Python bytecode (.pyc), không phải mã nguồn gốc
- Code vẫn có thể được decompile bằng pyinstxtractor + decompyle3
- **Bảo mật thấp** - chỉ làm khó người dùng thông thường

### Nuitka (Khuyên dùng)
- Biến Python code → C code → Native machine code
- Rất khó decompile, cần chuyên gia reverse engineering
- **Bảo mật cao** - phù hợp cho thương mại

### Server Deployment (Bảo mật tuyệt đối)
- Code không bao giờ rời khỏi server
- User chỉ tương tác qua API/Web UI
- **Bảo mật tuyệt đối** - không thể trích xuất code

### Về performance
- Lần chạy đầu tiên sẽ chậm hơn do cần tải các model ML
- Các lần sau sẽ nhanh hơn khi model đã được cache
- Có GPU NVIDIA sẽ nhanh hơn rất nhiều

### Về dependencies
- Một số thư viện có thể cần cài đặt thêm runtime:
  - Visual C++ Redistributable
  - CUDA Toolkit (nếu dùng GPU)
  - FFmpeg (thường đã được bundle)

## Khắc phục sự cố

### Lỗi "Missing DLL"
- Cài đặt Visual C++ Redistributable từ Microsoft
- Download: https://aka.ms/vs/17/release/vc_redist.x64.exe

### Lỗi "CUDA out of memory"
- Giảm batch size trong config
- Hoặc tắt GPU bằng cách set biến môi trường:
  ```env
  CUDA_VISIBLE_DEVICES=-1
  ```

### Lỗi "Module not found"
- Kiểm tra file spec có bao gồm tất cả hidden imports
- Thêm module bị thiếu vào list `hiddenimports` trong `build_exe.spec`

### Lỗi "WEB_SESSION_SECRET is not set"
- Mở file .env trong thư mục exe
- Thêm dòng: `WEB_SESSION_SECRET=random-secret-string`

## Phân phối

### Để phân phối cho người khác
1. Zip toàn bộ thư mục `dist\ChineseVideoLocalizationAI\`
2. Gửi file zip cho người dùng
3. Họ chỉ cần giải nén và chạy exe

### Không cần cài đặt Python
- Người dùng không cần cài Python
- Chỉ cần Windows và các runtime cơ bản

## Alternatives

Nếu PyInstaller không phù hợp, có thể cân nhắc:

### Docker (Khuyên dùng cho production)
- Tạo Docker image từ Dockerfile có sẵn
- Chạy trên Windows với Docker Desktop
- Code vẫn được bảo vệ, dễ deploy

### Nuitka (Bảo mật cao hơn)
- Biến Python code thành C rồi compile
- Khó decompile hơn PyInstaller
- Cấu hình phức tạp hơn

### Server deployment
- Deploy lên cloud server
- Người dùng truy cập qua web
- Code không bao giờ rời khỏi server
- Chi phí vận hành hàng tháng

## Liên hệ

Nếu gặp vấn đề trong quá trình build, kiểm tra:
1. Log output của PyInstaller
2. File build log trong thư mục `build/`
3. Đảm bảo tất cả dependencies đã cài đặt đúng version
