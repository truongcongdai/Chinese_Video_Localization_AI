# Web UI (thay thế Telegram bot)

Web app này dùng lại 100% pipeline xử lý video hiện có (`LocalizationService`)
— chỉ thay giao diện Telegram bằng giao diện web.

## Chạy thử (local, không qua Docker)

### Cách 1: Tự động sinh file .env (khuyên dùng)

```bash
cd dist
python generate_env.py
# Chỉnh sửa file .env để điền thông tin SMTP, API keys nếu cần
python run_web.py
# mở http://localhost:8080
```

Script `generate_env.py` sẽ tự động sinh file `.env` với các giá trị ngẫu nhiên an toàn cho `WEB_SESSION_SECRET` và các field bảo mật khác. Bạn chỉ cần chỉnh sửa các field cần thiết như SMTP email/password.

### Cách 2: Tạo thủ công

```bash
pip install -r requirements.txt
export WEB_SESSION_SECRET=$(openssl rand -hex 32)
python scripts/run_web.py
# mở http://localhost:8080
```

Nếu đã có `WEB_SESSION_SECRET` trong `.env` thì không cần lệnh `export`.
Entrypoint local của giao diện web là **`scripts/run_web.py`**; không chạy
`scripts/run_bot.py` trừ khi bạn còn muốn dùng Telegram bot. Redis không bắt
buộc khi chạy local: nếu `redis://127.0.0.1:6379` không hoạt động, cache sẽ tự
chuyển sang bộ nhớ trong tiến trình.

Database local mặc định nên đặt `WEB_DB_PATH=./local_data/database.sqlite3`.
Không trỏ vào thư mục `temp/` cũ do Docker tạo nếu thư mục đó đang thuộc user
`root`/`nobody`, vì tiến trình local sẽ không có quyền ghi.

Lần đầu mở trang, hệ thống sẽ cho tạo 1 tài khoản admin (chỉ tạo được đúng 1
lần — sau đó là màn hình đăng nhập bình thường). Đây là auth đơn giản
(cookie + bcrypt), phù hợp cho một nhóm nhỏ tự host, không phải hệ thống
đăng ký công khai.

## Chạy qua Docker (cùng lúc với bot Telegram, hoặc thay hẳn)

`docker-compose.prod.yml` đã có sẵn service `web` (cổng 8080), dùng chung
image với bot Telegram — bạn có thể chạy song song hoặc tắt bớt service
`app` (Telegram) nếu không cần nữa.

1. Thêm vào file `.env`:
```
WEB_SESSION_SECRET=<openssl rand -hex 32>
```

2. Build & chạy:
```bash
sudo docker compose -f docker-compose.prod.yml build --no-cache
sudo docker compose -f docker-compose.prod.yml up -d
```

3. Mở `http://<ip-server>:8080`

## Tính năng đã có

- Đăng nhập (tài khoản admin đầu tiên tự đăng ký, các tài khoản sau do
  admin tạo trong trang Quản trị)
- Dán link video + chọn ngôn ngữ đích (Tiếng Việt / English)
- Theo dõi trạng thái xử lý (đang chờ / đang xử lý / xong / lỗi)
- Xem trước video ngay trên trình duyệt
- Tải video kết quả xuống
- Lịch sử tất cả video đã xử lý (lưu trong SQLite), mỗi dòng có sẵn nút
  "Xem trước" **và** "Đăng lên MXH" cạnh nhau
- Hệ thống credit: mỗi lần tạo video trừ `JOB_COST_CREDITS` (mặc định 1)
  credit của người dùng đó; nếu job lỗi, credit được hoàn lại tự động. Số
  credit còn lại hiện ngay trên đầu trang. Đặt `JOB_COST_CREDITS=0` để tắt
  hẳn cơ chế này.
- Đăng thẳng lên TikTok / Facebook / YouTube, **mỗi người dùng kết nối
  tài khoản mạng xã hội CỦA RIÊNG HỌ** qua nút "Kết nối" (OAuth), không
  còn giới hạn dùng chung 1 tài khoản cho cả server nữa (xem mục dưới)
- Trang Quản trị (chỉ admin thấy): thống kê tổng quan (số user, số video,
  video 7 ngày qua, số lượt đăng MXH thành công...), tạo tài khoản mới,
  cộng/trừ credit cho từng người dùng

## Đăng lên mạng xã hội

## Gen nội dung bằng AI miễn phí

Phương án không mất phí API là Ollama chạy model ngay trên máy. Máy CPU/no-GPU
nên bắt đầu với model nhỏ:

```bash
ollama serve
ollama pull qwen3:1.7b
```

Sau đó thêm vào `.env`:

```env
CREATOR_AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:1.7b
OLLAMA_TRANSLATION_TIMEOUT=90
OLLAMA_TRANSLATION_NUM_CTX=8192
OLLAMA_TRANSLATION_NUM_PREDICT=0
```

Nếu máy có GPU/RAM tốt, đổi lên `qwen3:4b` hoặc `qwen3:8b` để dịch tự nhiên hơn.
Nếu Ollama chạy trên máy khác, đặt `OLLAMA_BASE_URL=http://IP_MAY_GPU:11434`.
`OLLAMA_TRANSLATION_NUM_PREDICT=0` nghĩa là tự tính theo số segment; tăng timeout/context
khi dùng model lớn hoặc video dài.

Trong màn hình dịch, option `Gemini` là một mode riêng. Chọn `Gemini`, mở
`Kết nối provider`, dán Gemini API key, bấm lưu để hệ thống kiểm tra key và
tải danh sách model `generateContent`; sau đó chọn model Gemini cần dùng cho
bước sửa bản dịch theo ngữ cảnh.

Nếu máy yếu hoặc không muốn chạy model local, có thể dùng OpenRouter Free
(có giới hạn request/ngày):

```env
CREATOR_AI_PROVIDER=openrouter
OPENROUTER_API_KEY=your_key_here
OPENROUTER_MODEL=openrouter/free
```

Đặt `CREATOR_AI_PROVIDER=auto` để thử lần lượt Ollama, OpenRouter rồi Gemini.
Chỉ Gemini có Google Search grounding trong luồng hiện tại; Ollama/OpenRouter
tạo nội dung từ kiến thức model và không giả vờ đã tìm kiếm web.

Prompt gửi kèm chủ đề, ngôn ngữ, tỷ lệ khung hình, thời lượng và hiệu ứng đã
chọn. Một kết quả AI được dùng chung cho cả ba ô để keyword, cảnh và lời đọc
đồng nhất; khi đổi thông số hệ thống sẽ gen lại. Nếu chưa có key hoặc API lỗi,
giao diện thông báo rõ và dùng template local làm dự phòng.

### Cách hoạt động (mô hình "Connect" theo từng người dùng)

Từ bản này, việc đăng lên MXH theo đúng mô hình mọi ứng dụng SaaS thật sự
dùng: **admin chỉ đăng ký MỘT app nhà phát triển cho mỗi nền tảng** (một
lần, dùng chung cho cả server), rồi **từng người dùng tự bấm "Kết nối"**
trong modal "Đăng lên mạng xã hội" và đăng nhập bằng chính tài khoản
TikTok/Facebook/YouTube của họ — token nhận được chỉ gắn với riêng người
đó trong database, admin không nhìn thấy hay phải nhập hộ token của ai cả.

Khi bấm "Kết nối", ngoài việc mở cửa sổ đăng nhập trên trình duyệt hiện
tại, hệ thống còn hiện sẵn **mã QR** trỏ tới cùng link đăng nhập đó — nếu
người dùng đang ngồi máy tính nhưng đã đăng nhập TikTok/Facebook/YouTube
sẵn trên điện thoại, họ chỉ cần quét mã QR bằng điện thoại để xác nhận,
không cần gõ lại mật khẩu trên máy tính.

Việc admin phải đăng ký app là bắt buộc phía nền tảng, không có cách nào
bỏ qua — nhưng chỉ cần làm **một lần cho cả hệ thống**, không phải mỗi
người dùng tự đi xin API key như bản trước.

### Thiết lập app (admin làm 1 lần)

**YouTube** — console.cloud.google.com → tạo project → bật "YouTube Data
API v3" → màn hình đồng ý OAuth → Credentials → tạo OAuth Client ID (loại
**Web application**) → thêm Authorized redirect URI:
`<địa chỉ server của bạn>/api/social/callback/youtube`
```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

**Facebook** — developers.facebook.com → Create App (loại **Business**)
→ thêm sản phẩm "Facebook Login" → Settings → thêm Valid OAuth Redirect
URI: `<địa chỉ server của bạn>/api/social/callback/facebook`.
```
FACEBOOK_APP_ID=...
FACEBOOK_APP_SECRET=...
```
**Bạn nói chưa có Meta Business — không sao, tạo miễn phí tại
business.facebook.com trước, cần thiết cho bước Meta App Review** để đăng
lên Page thật (không phải tài khoản test); chưa được duyệt vẫn kết nối và
xem preview được nhưng đăng thật lên Page ngoài sandbox sẽ bị Meta chặn.

**TikTok** — developers.tiktok.com → Create app → thêm sản phẩm "Login
Kit" + "Content Posting API" → Redirect URI:
`<địa chỉ server của bạn>/api/social/callback/tiktok`
```
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
```
App chưa được TikTok audit chỉ đăng được lên tài khoản test bạn tự thêm
trong app, chưa đăng được cho người dùng bất kỳ.

Chưa cấu hình platform nào thì nút "Kết nối" tương ứng sẽ bị vô hiệu hoá
và hiện rõ hướng dẫn thiếu gì, thay vì báo lỗi mập mờ.

### Chế độ cũ (1 tài khoản dùng chung — mặc định bị tắt)

Nếu bạn chỉ có đúng 1 người dùng và muốn bỏ qua bước OAuth, đặt
`ALLOW_SHARED_SOCIAL_CREDENTIALS=true`, rồi set
thẳng các biến `TIKTOK_ACCESS_TOKEN`, `FACEBOOK_PAGE_ID` +
`FACEBOOK_PAGE_ACCESS_TOKEN`, `YOUTUBE_CLIENT_ID` + `YOUTUBE_CLIENT_SECRET`
+ `YOUTUBE_REFRESH_TOKEN` như bản trước — hệ thống sẽ dùng các giá trị này
làm dự phòng cho bất kỳ ai chưa tự "Kết nối" tài khoản riêng.

## Quản trị

Tài khoản admin đầu tiên (người đăng ký lúc mở web app lần đầu) sẽ thấy nút
"Quản trị" ở góc trên bên phải, dẫn tới trang riêng để:
- Xem thống kê: tổng số user, tổng số video đã xử lý, số video 7 ngày gần
  nhất, số video hoàn tất/lỗi, tổng số lượt đăng MXH thành công
- Tạo tài khoản mới cho người dùng khác (kèm số credit khởi điểm)
- Cộng/trừ credit cho từng tài khoản bất kỳ lúc nào

## Còn thiếu / giới hạn hiện tại (thành thật để bạn biết)

- Facebook: hệ thống tự lấy Page đầu tiên trong danh sách Page bạn quản lý
  khi kết nối — nếu bạn quản lý nhiều Page, hiện chưa có UI để chọn Page
  nào, luôn dùng Page đầu tiên trả về.
- Credit chỉ là cơ chế giới hạn mức dùng nội bộ, chưa gắn với thanh toán
  thật (không có Stripe/momo/v.v.).
- Chưa test được với dữ liệu thật (môi trường tôi code không có mạng ra
  ngoài) — bạn cần tự chạy thử và báo lỗi nếu có, đặc biệt là 3 luồng OAuth
  vì mỗi nền tảng có thể đổi chi tiết API theo thời gian.
