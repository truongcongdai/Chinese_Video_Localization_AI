# Build Windows không phân phối source Python

## Yêu cầu trên máy build

- Windows 10/11 64-bit.
- Python 3.12 64-bit, có `py` launcher.
- Khoảng 30–50 GB ổ đĩa trống.
- RAM tối thiểu 16 GB; nên dùng 32 GB.
- FFmpeg Windows 64-bit đặt tại:
  - `vendor/ffmpeg/bin/ffmpeg.exe`
  - `vendor/ffmpeg/bin/ffprobe.exe`
- Có Internet trong lúc build để cài dependency và tải Chromium cho luồng
  Douyin/browser. Chromium được đóng kèm vào ZIP; máy khách không cần cài riêng.

Không copy `.env`, `cookies`, `local_data`, database hoặc video từ máy phát triển.

## Tạo bản phát hành

Mở PowerShell tại thư mục dự án:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\build_windows.ps1
```

Hoặc chạy `build_nuitka.bat` ở thư mục gốc; file này gọi đúng cùng pipeline.

Kết quả:

```text
build/windows/ChineseVideoAI-Windows-x64.zip
```

Giải nén ZIP trên máy đích và chạy `Start ChineseVideoAI.bat`. Trình duyệt sẽ
tự mở `http://127.0.0.1:8080`. File `.env`, database và thư mục `local_data`
được tạo tại máy đích, không nằm trong bản phát hành.

## Lưu ý

- Đây là bản `standalone`, không phải một file duy nhất. Torch/Whisper/EasyOCR
  khiến `onefile` rất lớn và chậm giải nén mỗi lần chạy.
- Nuitka không đưa source `.py` của ứng dụng vào gói theo cách đóng gói thông
  thường, nhưng không có giải pháp phần mềm phía người dùng nào chống phân tích
  ngược tuyệt đối.
- Model AI có thể tải trong lần chạy đầu nếu chưa được đóng kèm, vì vậy máy đích
  cần Internet. Muốn chạy hoàn toàn offline cần đóng riêng model đã chọn.
- Bản CPU và CUDA nên phát hành riêng. Script mặc định dùng các wheel mà pip chọn
  trên máy build; hãy build và kiểm thử trên cấu hình tương tự máy đích.
