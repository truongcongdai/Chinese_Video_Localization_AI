# Video Localization với Timestamps - Ví dụ Minh Họa

## Tổng quan

Ví dụ này minh họa workflow hoàn chỉnh cho video localization dựa trên timestamps:
- Detect text gốc từ video theo timestamps (OCR)
- Tách câu theo timestamps (0-3s, 3-6s, ...)
- Tạo ô trắng che text gốc (blur box) theo vị trí detect
- Dịch text sang **2 ngôn ngữ**: Tiếng Anh (EN) và Tiếng Việt (VI)
- Chèn subtitle dịch vào ô trắng đó
- Tạo voice đọc theo timestamps tương ứng
- Render video final với subtitle dịch và voice mới

## Workflow

### 1. Detect Text Regions (OCR)

```
Video gốc → OCR (PaddleOCR/EasyOCR) → Text Regions với timestamps
```

**Kết quả:** Danh sách các vùng text với:
- Thời gian bắt đầu/kết thúc (seconds)
- Text gốc detect được
- Tọa độ (x, y, width, height)

**Ví dụ:**
```python
TextRegion(
    start_time=0.0,
    end_time=3.0,
    text="你好世界",
    x=100, y=800, width=400, height=50
)
```

### 2. Convert sang Timeline Segments

```
 Text Regions → Timeline Segments
```

Mỗi region được convert thành TimelineSegment với timestamps tương ứng.

### 3. Dịch Text

```
Timeline Segments (Chinese) → Translator → Timeline Segments (Vietnamese)
```

Mỗi segment được dịch riêng biệt, giữ nguyên timestamps.

**Ví dụ:**
- 0-3s: "你好世界" → "Xin chào thế giới"
- 3-6s: "这是一个测试" → "Đây là một bài kiểm tra"
- 6-9s: "视频本地化示例" → "Ví dụ bản địa hóa video"

### 4. Generate Subtitle File

```
Translated Segments → SRT File
```

File SRT được generate với timestamps mapping chính xác.

**File example_translated.srt:**
```
1
00:00:00,000 --> 00:00:03,000
Xin chào thế giới

2
00:00:03,000 --> 00:00:06,000
Đây là một bài kiểm tra

3
00:00:06,000 --> 00:00:09,000
Ví dụ bản địa hóa video
```

### 5. Generate TTS Audio

```
Translated Segments → TTS Engine → Audio Files (per segment)
```

Mỗi segment được tạo audio riêng biệt, sau đó concat theo timestamps.

### 6. Render Final Video

```
Video gốc + Blur Box + Subtitle Dịch + TTS Audio → Video Final
```

FFmpeg được sử dụng để:
- Blur text gốc theo vị trí detect
- Burn subtitle dịch vào video
- Mix audio TTS với video

## Cấu trúc Files

```
examples/
├── timestamp_localization_example.py  # Script ví dụ workflow (hỗ trợ 2 ngôn ngữ)
├── blur_box_config.yaml              # Cấu hình blur box coordinates (có cả EN và VI)
├── example_original.srt              # Subtitle gốc (Chinese)
├── example_translated_en.srt         # Subtitle dịch tiếng Anh
├── example_translated_vi.srt         # Subtitle dịch tiếng Việt (có dấu)
└── README.md                         # File này
```

## Cấu hình Blur Box

File `blur_box_config.yaml` định nghĩa các vùng cần blur:

```yaml
blur_regions:
  - start_time: 0.0
    end_time: 3.0
    x: 100
    y: 800
    width: 400
    height: 50
    original_text: "你好世界"
    translated_text: "Xin chào thế giới"
```

## Chạy Ví dụ

```bash
# Chạy script ví dụ với tiếng Việt (mặc định)
python examples/timestamp_localization_example.py --lang vi

# Chạy script ví dụ với tiếng Anh
python examples/timestamp_localization_example.py --lang en

# Chạy với video cụ thể
python examples/timestamp_localization_example.py --lang en --video /path/to/video.mp4 --output /path/to/output
```

**Lưu ý:** Script hiện tại sử dụng data giả lập. Để chạy với video thật:

1. Thay đổi `video_path` trong `main()`
2. Cài đặt OCR library (PaddleOCR, EasyOCR, hoặc Tesseract)
3. Cài đặt translator backend (Google Translate, DeepL, v.v.)
4. Cài đặt TTS backend (EdgeTTS đã có sẵn)

## Mapping Timestamps - Chi Tiết

### Quy tắc Mapping Timestamps

**Nguyên tắc cốt lõi:** Video gốc 0-3s nói gì thì 0-3s của bản final phải dịch tương tự theo đó, tách câu như timestamps, có ô trắng detect được chữ của video gốc và che đè để tránh bản quyền, xong chèn chữ subtitle dịch mới lên ô vuông đó, bản voice sẽ đọc theo timestamps của câu tương tự timestamps đó.

### Ví Dụ Cụ Thể

**Tiếng Anh (EN):**

| Video Gốc | Text Gốc | Vị trí | Bản Final EN | Hành Động | Voice EN |
|-----------|----------|--------|--------------|-----------|----------|
| 0-3s | 你好世界 | (100,800,400,50) | 0-3s | Che text gốc bằng ô trắng, chèn "Hello World" | Đọc "Hello World" từ 0-3s |
| 3-6s | 这是一个测试 | (100,800,500,50) | 3-6s | Che text gốc bằng ô trắng, chèn "This is a test" | Đọc "This is a test" từ 3-6s |
| 6-9s | 视频本地化示例 | (100,800,600,50) | 6-9s | Che text gốc bằng ô trắng, chèn "Video localization example" | Đọc "Video localization example" từ 6-9s |

**Tiếng Việt (VI):**

| Video Gốc | Text Gốc | Vị trí | Bản Final VI | Hành Động | Voice VI |
|-----------|----------|--------|--------------|-----------|----------|
| 0-3s | 你好世界 | (100,800,400,50) | 0-3s | Che text gốc bằng ô trắng, chèn "Xin chào thế giới" | Đọc "Xin chào thế giới" từ 0-3s |
| 3-6s | 这是一个测试 | (100,800,500,50) | 3-6s | Che text gốc bằng ô trắng, chèn "Đây là một bài kiểm tra" | Đọc "Đây là một bài kiểm tra" từ 3-6s |
| 6-9s | 视频本地化示例 | (100,800,600,50) | 6-9s | Che text gốc bằng ô trắng, chèn "Ví dụ bản địa hóa video" | Đọc "Ví dụ bản địa hóa video" từ 6-9s |

### Workflow Chi Tiết Cho Mỗi Segment

**Segment 0-3s:**
1. **Video gốc 0-3s:** Detect text "你好世界" tại vị trí (100, 800, 400, 50)
2. **Bản final EN 0-3s:** 
   - Tạo ô trắng tại (100, 800, 400, 50) che text gốc
   - Chèn subtitle dịch "Hello World" vào ô trắng đó
3. **Voice EN 0-3s:** TTS đọc "Hello World" từ 0-3s
4. **Bản final VI 0-3s:** 
   - Tạo ô trắng tại (100, 800, 400, 50) che text gốc
   - Chèn subtitle dịch "Xin chào thế giới" vào ô trắng đó
5. **Voice VI 0-3s:** TTS đọc "Xin chào thế giới" từ 0-3s

**Segment 3-6s:**
1. **Video gốc 3-6s:** Detect text "这是一个测试" tại vị trí (100, 800, 500, 50)
2. **Bản final EN 3-6s:**
   - Tạo ô trắng tại (100, 800, 500, 50) che text gốc
   - Chèn subtitle dịch "This is a test" vào ô trắng đó
3. **Voice EN 3-6s:** TTS đọc "This is a test" từ 3-6s
4. **Bản final VI 3-6s:**
   - Tạo ô trắng tại (100, 800, 500, 50) che text gốc
   - Chèn subtitle dịch "Đây là một bài kiểm tra" vào ô trắng đó
5. **Voice VI 3-6s:** TTS đọc "Đây là một bài kiểm tra" từ 3-6s

**Segment 6-9s:**
1. **Video gốc 6-9s:** Detect text "视频本地化示例" tại vị trí (100, 800, 600, 50)
2. **Bản final EN 6-9s:**
   - Tạo ô trắng tại (100, 800, 600, 50) che text gốc
   - Chèn subtitle dịch "Video localization example" vào ô trắng đó
3. **Voice EN 6-9s:** TTS đọc "Video localization example" từ 6-9s
4. **Bản final VI 6-9s:**
   - Tạo ô trắng tại (100, 800, 600, 50) che text gốc
   - Chèn subtitle dịch "Ví dụ bản địa hóa video" vào ô trắng đó
5. **Voice VI 6-9s:** TTS đọc "Ví dụ bản địa hóa video" từ 6-9s

### Quan Trọng

- **Timestamps của text dịch PHẢI khớp với timestamps của text gốc** để đảm bảo voice đọc đúng thời điểm
- **Ô trắng (blur box) phải che chính xác vị trí text gốc** để tránh bản quyền
- **Subtitle dịch được chèn vào ô trắng đó** thay vì đè lên text gốc
- **Voice đọc theo timestamps tương ứng** với từng segment

## Nâng cấp Renderer

Hiện tại `Renderer` chỉ hỗ trợ 1 blur box global. Để blur nhiều region theo timestamps, cần nâng cấp:

1. Thêm support cho multiple blur boxes với timestamps
2. Sử dụng FFmpeg `drawbox` hoặc `delogo` filter với timeline
3. Hoặc split video theo timestamps, blur từng phần, rồi concat lại

**Ví dụ FFmpeg command cho timeline-based blur:**
```bash
ffmpeg -i input.mp4 \
  -vf "select='between(t,0,3)',drawbox=x=100:y=800:w=400:h=50:color=white:t=fill" \
  -vf "select='between(t,3,6)',drawbox=x=100:y=800:w=500:h=50:color=white:t=fill" \
  -vf "subtitles=translated.srt" \
  -i tts_audio.wav \
  -c:v libx264 -c:a aac \
  output.mp4
```

## Các Công Nghệ Sử Dụng

- **OCR:** PaddleOCR, EasyOCR, Tesseract
- **Translation:** Google Translate, DeepL, OpenAI
- **TTS:** EdgeTTS, Azure TTS, Google TTS
- **Video Processing:** FFmpeg
- **Subtitle:** SRT, VTT, ASS formats

## Lưu ý Bản Quyền

- Che text gốc bằng blur box để tránh vi phạm bản quyền
- Subtitle dịch được chèn vào ô trắng thay vì đè lên text gốc
- Voice mới được tạo từ text dịch, không sử dụng voice gốc

## Kết quả

**Video final tiếng Anh:**
- Text gốc bị che bởi ô trắng
- Subtitle dịch tiếng Anh hiển thị trong ô trắng
- Voice tiếng Anh đọc theo timestamps tương ứng
- Không vi phạm bản quyền text gốc

**Video final tiếng Việt:**
- Text gốc bị che bởi ô trắng
- Subtitle dịch tiếng Việt (có dấu) hiển thị trong ô trắng
- Voice tiếng Việt đọc theo timestamps tương ứng
- Không vi phạm bản quyền text gốc

## Hỗ trợ Đa Ngôn Ngữ

Script hiện tại hỗ trợ 2 ngôn ngữ đích:
- **Tiếng Anh (EN):** `--lang en`
- **Tiếng Việt (VI):** `--lang vi` (mặc định)

Để thêm ngôn ngữ mới:
1. Thêm field `translated_text_xx` vào class `TextRegion`
2. Cập nhật `blur_box_config.yaml` với text dịch mới
3. Cập nhật script để xử lý ngôn ngữ mới
4. Tạo file subtitle example cho ngôn ngữ mới
