# Web UI (thay thế Telegram bot)

Web app này dùng lại 100% pipeline xử lý video hiện có (`LocalizationService`)
— chỉ thay giao diện Telegram bằng giao diện web.

## Chạy thử (local, không qua Docker)

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

## Kiểm thử hồi quy offline

Từ thư mục gốc dự án, dùng virtualenv hiện có:

```bash
source .venv/bin/activate
python scripts/run_tests.py -q
node --test tests/*.js
```

`run_tests.py` chặn kết nối TCP/DNS của Python ra ngoài và bật MOCK. Test dùng
database/thư mục runtime tạm, không nạp `.env`; Redis integration dùng Unix socket
cục bộ nếu có `redis-server`. Transport của chương trình con vẫn phải được mock
trong test. Không đặt `RUN_LIVE_TESTS=1` khi dùng bộ chạy này.

Các thay đổi sau review và hướng dẫn chuyển database Docker cũ nằm trong
[báo cáo sửa lỗi](docs/REVIEW_FIXES_2026-10-30.md).

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

## Reup Agent: bắt đầu từ kênh, tự khảo sát trend và tìm video

Kênh đích tự động hiện là **YouTube đã đăng nhập**. Tab Reup Agent chia
thành bốn bước, mỗi lần chỉ hiện phần đang làm:

1. **Kết nối kênh**: đăng nhập YouTube, kiểm tra tên kênh, chọn thị trường
   khán giả và các nền tảng cần khảo sát. Bấm **Phân tích kênh & khảo sát trend**.
2. **Chiến lược cho kênh**: Agent tự xác định kênh đã có video hay chưa,
   chọn sẵn hướng phù hợp nhất và trình bày kế hoạch phát triển. Bấm
   **Dùng chiến lược Agent đề xuất**, hoặc chọn hướng khác. Mở phần căn cứ
   để xem video của kênh, ví dụ social và từ khóa do Agent tự tạo.
3. **Bắt đầu chạy**: chọn cách thực hiện và xem tóm tắt kênh/chủ đề/nguồn.
   Mặc định xử lý xong sẽ chờ bạn duyệt. Bấm **Tìm và xử lý video** để chạy
   một lượt, hoặc chọn **Tự tìm thêm video định kỳ** rồi bấm bắt đầu.
4. **Theo dõi video**: lọc video đang xử lý, chờ duyệt hoặc cần kiểm tra.
   **Xem video & duyệt đăng** mở video kết quả cùng nội dung đăng. Xác nhận
   quyền sử dụng nguồn và duyệt khi sẵn sàng. Có nút tắt chạy định kỳ khi
   Agent đang bật; lượt đăng giữ đúng kênh đích đã chọn.

Giọng đọc, video ngắn và các cài đặt AI/lịch quét nằm trong các mục mở rộng
ở bước 3. Không cần nhập ID kênh, chủ đề, từ khóa hay link video nguồn.
“Nguồn” là video Agent tự tìm được trên social, không phải đầu vào bắt người
dùng cung cấp. Các cấu hình nhập nguồn cũ không giới hạn việc phân tích tự
động khi dùng giao diện này. Chuyển tab rồi quay lại giữ các chỉnh sửa
chưa lưu; **Tải lại** sẽ nạp lại cài đặt đã lưu và tiến độ mới nhất.
Cấu hình tự lưu khi bấm bắt đầu, không cần bấm **Lưu cài đặt** trước.

- Kênh đã có video: duyệt toàn bộ trang uploads qua API, lấy metadata,
  lượt xem và tương tác; nhóm chủ đề qua tags, không gộp mọi video chỉ vì
  cùng danh mục YouTube. Tự tạo truy vấn để tìm video tương tự trên social.
  Một trend khác chủ đề không thay thế hướng nội dung của kênh.
  Quét theo từng đợt, lưu con trỏ trang để tiếp tục sau gián đoạn; chỉ đề
  xuất khi đã duyệt hết danh sách video truy cập được.
- Kênh chưa có video: đề xuất từ mẫu phổ biến YouTube và dữ liệu công khai
  lấy được từ Douyin, TikTok, Bilibili đã chọn. Mẫu social tham gia trực tiếp
  vào việc chọn chủ đề và chiến lược; YouTube market lỗi không chặn các mẫu
  social còn dùng được. Lỗi đọc kênh hoặc thiếu quyền không được coi là kênh mới.
- Mỗi hướng có điểm, video làm căn cứ và cụm từ khóa nội dung rút từ tags,
  mô tả và các phần tiêu đề có nghĩa; không lấy nguyên tên video làm truy vấn.
  Agent tự dùng `OLLAMA_MODEL` đã cấu hình khi ô model để trống. Douyin và
  Bilibili dùng từ khóa tiếng Trung; khi model không cung cấp bản dịch hợp lệ,
  hệ thống dùng bộ dịch và lưu cache từ khóa. Nếu dịch thất bại, nguồn đó được
  đánh dấu chờ dịch thay vì tìm bằng tiêu đề hoặc từ khóa sai ngôn ngữ.
- **Tìm và xử lý video** lưu cấu hình hiện tại, phân tích nếu cần rồi tìm trên
  YouTube, Douyin, TikTok và/hoặc Bilibili. Video đã có trên kênh đích được
  loại khỏi nguồn YouTube; lịch sử nguồn tiếp tục chống tải/đăng trùng.
- Video social đã tìm được trong bước nghiên cứu được dùng lại khi chạy,
  với bộ lọc chủ đề và nền tảng đã chọn. Xếp hạng dùng độ mới, lượt xem theo
  tuổi video và tương tác khi có dữ liệu, thay vì chỉ chọn lượt xem tích lũy.
  Mẫu không có số liệu không được ghi thành tăng trưởng đã đo. Căn cứ chiến
  lược và số liệu của video được giữ trong cấu hình từng lượt xử lý.
- Kết quả phải khớp các khái niệm nội dung, có kiểm tra ngữ cảnh để tránh
  chọn một MV chỉ vì tên bài có chữ “Beauty”. Các nguồn social có hàng đợi
  riêng, tránh để lượt xem tích lũy YouTube lấn hết kết quả social phù hợp.
  Bilibili được thử qua trang tìm kiếm công khai khi bộ trích xuất lỗi hoặc
  trả rỗng; CAPTCHA vẫn cần một phiên được xác minh. Phản hồi recommendation
  không được coi là kết quả tìm kiếm TikTok.
- Từ khóa nguồn được bản địa hóa theo cách dùng tìm kiếm (ví dụ `舞蹈`,
  `穿搭`), không dịch sát chữ thành `热女孩`. Bộ lọc xét các mảng nội dung
  như những hướng thay thế: video nhảy không bắt buộc đồng thời nói về thời
  trang và đời sống xa hoa. Khớp từ mô tả ngoại hình đơn thuần không đủ để chọn.
- Bước theo dõi lưu và hiển thị từ khóa cùng trạng thái từng nền tảng; lỗi
  xác minh, HTTP 412 và thiếu bản dịch được phân biệt, không lặp cảnh báo cho
  từng query. Nguồn bị lỗi không được thử lại ở mọi chiến lược trong cùng
  đợt phân tích. Lượt thất bại không chiếm giới hạn video mới mỗi ngày.
- Kết quả lượt tìm mới nhất và toàn bộ lịch sử có phạm vi riêng. Một lượt tìm
  rỗng không hiển thị video lỗi từ lượt cũ như kết quả mới. Nguồn tự động cũ
  chưa qua bộ lọc nội dung không được retry/process/resume/approve; lịch sử
  vẫn còn để kiểm tra. Mỗi nguồn có số video nhận được, đủ liên quan và bị loại.
- Chế độ **Xử lý video, tôi duyệt trước khi đăng** đưa nguồn vào pipeline tải
  và bản địa hóa, sau đó chờ duyệt. **Xử lý và tự đăng theo cài đặt** dùng
  quyền riêng tư/lịch đã chọn.
  Bật quét định kỳ để lặp lại; phân tích được dùng lại tối đa 6 giờ khi cấu
  hình không đổi. Có thể bấm Phân tích để cập nhật sớm hơn.

Phân tích cơ bản dùng metadata, tags, lượt xem và tương tác, không khẳng
định đã đọc nội dung hình ảnh, retention hay doanh thu của mọi video.
Mẫu công khai từ Douyin/TikTok/Bilibili được ghi riêng với bảng phổ biến YouTube;
khi bị chặn đăng nhập/xác minh, UI báo nguồn chưa truy cập được. Không tạo
dữ liệu trend giả. Facebook/TikTok vẫn có luồng kết nối và đăng video thường;
phân tích và tự đăng của Reup Agent hiện dùng kênh YouTube.

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

Mở tab **Tài khoản social** để kết nối Facebook Page, YouTube và TikTok
trước khi tạo video. Các kết nối cũng hiển thị trong **Dịch Video → bước 3:
Cấu hình & chạy** và trong hộp **Đăng lên mạng xã hội**. Mỗi nơi đều dùng
cùng trạng thái tài khoản của người dùng đang đăng nhập.

Với video xử lý thông thường, bấm **Đăng lên mạng xã hội** ngay dưới video
kết quả hoặc trên từng dòng lịch sử, chọn nền tảng, nhập nội dung rồi chọn
**Đăng ngay** hoặc **Hẹn giờ đăng**. YouTube tải lên ở chế độ riêng tư;
công khai video trong YouTube Studio. Kết nối tài khoản không tự đăng video.

Từ bản này, việc đăng lên MXH theo đúng mô hình mọi ứng dụng SaaS thật sự
dùng: **admin chỉ đăng ký MỘT app nhà phát triển cho mỗi nền tảng** (một
lần, dùng chung cho cả server), rồi **từng người dùng tự bấm "Đăng nhập"**
trong tab "Tài khoản social" hoặc hộp "Đăng lên mạng xã hội" và đăng nhập bằng chính tài khoản
TikTok/Facebook/YouTube của họ — token nhận được chỉ gắn với riêng người
đó trong database, admin không nhìn thấy hay phải nhập hộ token của ai cả.

Khi bấm "Đăng nhập", ngoài việc mở cửa sổ đăng nhập trên trình duyệt hiện
tại, hệ thống còn hiện sẵn **mã QR** trỏ tới cùng link đăng nhập đó — nếu
người dùng đang ngồi máy tính nhưng đã đăng nhập TikTok/Facebook/YouTube
sẵn trên điện thoại, họ chỉ cần quét mã QR bằng điện thoại để xác nhận,
không cần gõ lại mật khẩu trên máy tính.

Việc admin phải đăng ký app là bắt buộc phía nền tảng, không có cách nào
bỏ qua — nhưng chỉ cần làm **một lần cho cả hệ thống**, không phải mỗi
người dùng tự đi xin API key như bản trước.

### Thiết lập app (admin làm 1 lần)

Trong **Tài khoản social → Thiết lập kết nối**, chọn nền tảng, mở trang
quản lý ứng dụng, thêm địa chỉ chuyển về đang hiển thị, rồi nhập Client ID
(hoặc App ID/Client key) và khóa bí mật. Bấm **Lưu thiết lập kết nối** là có
thể dùng ngay, không sửa code hay `.env`, không cần khởi động lại. Chỉ admin
được thay đổi phần này; khóa được mã hóa trong database và không trả lại UI.
Để trống ô khóa khi lưu lại cùng ứng dụng để giữ khóa hiện tại.

Các biến môi trường bên dưới vẫn được hỗ trợ cho triển khai cũ; cấu hình
lưu từ UI được ưu tiên. Người dùng thông thường không nhập các thông tin
ứng dụng này: họ bấm **Đăng nhập Facebook/YouTube/TikTok**, đăng nhập trực
tiếp trên trang của nền tảng rồi cấp quyền.

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

Chưa thiết lập nền tảng nào thì nút "Đăng nhập" tương ứng sẽ bị vô hiệu hoá
và hướng dẫn quản trị viên mở phần thiết lập kết nối trên giao diện.

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

## Khôi phục tìm nguồn Douyin, TikTok và Bilibili

Trong **Reup Agent → Kết nối kênh → Đăng nhập nguồn video**, mở cửa sổ nguồn
và hoàn tất đăng nhập/xác minh bình thường. Phiên được tự động lưu trong lúc
bạn đăng nhập. Đóng cửa sổ khi xong, hoặc bấm **Hoàn tất** trên Reup Agent;
không cần nhớ bấm Lưu phiên trước khi đóng như bản cũ.

UI tự kiểm tra tìm kiếm sau khi cửa sổ đóng. **Đã ghi nhớ đăng nhập** cho
biết đã nhận cookie phiên; **Nguồn truy cập được** chỉ xuất hiện khi tìm kiếm
thực sự trả video. Có thể bấm **Kiểm tra tìm nguồn** để kiểm tra lại, rồi tìm
video lại để cập nhật danh sách. Kiểm tra phiên chỉ đọc metadata, không tạo
job, trừ credit hay đăng video. Tài khoản nguồn khác với kênh YouTube dùng
để đăng video.

Nếu trang nguồn báo **502/503/504**, phiên hiện tại được giữ. Bấm **Tải lại
cửa sổ nguồn** hoặc thử kiểm tra sau; không cần đăng nhập lại. Nếu yêu cầu
từ UI gặp 504, ứng dụng kiểm tra lại trạng thái cửa sổ thay vì tự mở một cửa
sổ đăng nhập mới. Đọc trạng thái và mở cửa sổ không chờ kết quả tìm kiếm.

Mỗi tài khoản ứng dụng có hồ sơ Chromium riêng, dùng chung cho đăng nhập và
tìm nguồn, tại `source_sessions/<user>/<platform>/` cạnh database. Khi đổi tài khoản, hồ sơ mới được lưu riêng trong thư mục này.
Cookie xuất ra cho bước tải video vẫn được lưu riêng theo tài khoản.
Không đưa thư mục phiên vào Git hoặc chia sẻ. Cơ chế hồ sơ trình duyệt theo
[Playwright persistent context](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context).

Mở ứng dụng bằng `http://localhost:8080` trên desktop của máy chạy web để mở
cửa sổ nguồn. Máy chủ không có màn hình vẫn có thể cấu hình file cookie
Netscape bằng `DOUYIN_COOKIES_FILE`, `TIKTOK_COOKIES_FILE`,
`BILIBILI_COOKIES_FILE` hoặc `YTDLP_COOKIES_FILE`.

Phiên hết hạn hoặc xác minh mới vẫn cần thao tác tại nền tảng. Việc lưu phiên
không bảo đảm mọi nền tảng hoặc mọi video luôn truy cập được. Kết quả tìm
kiếm lưu đệm 5 phút; nguồn yêu cầu xác minh tạm nghỉ 10 phút, lỗi 502/503/504
nghỉ ngắn 30 giây. Nút kiểm tra cho phép thử lại bằng phiên hiện tại.


## Ghi nhớ đăng nhập và đổi tài khoản

Đăng nhập ứng dụng được lưu trên trình duyệt theo thời hạn phiên tối đa
30 ngày. Khởi động lại server với cùng `.env` và `WEB_SESSION_SECRET` giữ
phiên đăng nhập; đăng xuất vẫn xóa cookie đăng nhập ứng dụng.

Mỗi tài khoản ứng dụng lưu riêng phiên Douyin/TikTok/Bilibili. Các lần mở
nguồn và tìm video sẽ nạp lại cookie còn hạn, kể cả khi hồ sơ Chromium đã tồn
tại. Dùng **Dùng phiên đã lưu** để kiểm tra nguồn hoặc tìm video như thường
lệ; không cần bấm đăng nhập lại mỗi lần khởi động.

Chọn **Đổi tài khoản** khi muốn thay tài khoản nguồn. Ứng dụng mở một hồ sơ
mới, giữ phiên trước đó trong lúc bạn đăng nhập. Tài khoản mới chỉ trở thành
phiên đang dùng khi đã nhận đăng nhập và đóng cửa sổ/hoàn tất; nếu đóng trước
khi đăng nhập thành công, phiên cũ vẫn được giữ. Đổi tài khoản ứng dụng sẽ
nạp bộ phiên nguồn của đúng tài khoản đó. Phiên hết hạn hoặc yêu cầu xác minh
mới từ nền tảng vẫn cần bạn xác thực lại.

Database và thư mục phiên dùng đường dẫn ổn định theo thư mục dự án khi
`WEB_DB_PATH` là đường dẫn tương đối. Đổi thư mục đứng trong terminal không
tự tạo một bộ phiên khác.


## CAPTCHA trên trang tìm kiếm

Đăng nhập và được phép tìm kiếm là hai trạng thái riêng của nền tảng.
Khi Douyin/TikTok yêu cầu CAPTCHA, bấm **Xác minh tìm kiếm**. Ứng dụng mở
đúng truy vấn đang bị chặn bằng hồ sơ nguồn đã lưu. Bạn tự hoàn tất xác minh
trong cửa sổ đó; Agent chỉ đọc danh sách video sau khi yêu cầu xác minh đã
hết và trang hiển thị kết quả. Ứng dụng không tự giải CAPTCHA.

Kết quả đọc ngay từ cửa sổ này được giữ theo tài khoản nguồn và truy vấn
trong 5 phút. Đóng cửa sổ hoặc bấm **Hoàn tất**, sau đó tìm video lại trong
Reup Agent; không mở thêm một trình duyệt nền cho truy vấn vừa xác minh.
Bước xác minh/đọc danh sách không tạo job xử lý hay đăng video.

Bilibili ưu tiên trang tìm kiếm trong phiên trình duyệt đã lưu thay vì gọi
trước API `bilisearch` có thể trả HTTP 412. Các URL bản địa hóa như
`/zh-Hans/video/...` được chuẩn hóa về cùng mã video.

Nếu tìm được video Bilibili nhưng tải lỗi `Unable to extract initial state`,
bộ tải dùng API chi tiết công khai để bổ sung metadata mà trang HTML mới
không còn nhúng. Việc chọn phần video, định dạng, kiểm tra quyền truy cập và
ghép âm thanh vẫn do yt-dlp xử lý. Chỉ tải các định dạng nền tảng cung cấp
cho phiên hiện tại. Tệp được tải từng phần và thử tiếp đoạn bị ngắt khi CDN
mất kết nối. Lỗi cần xác minh hoặc quyền truy cập sẽ dừng job, không tự lặp
lại toàn bộ quy trình ba lần.

Sau khi cập nhật, khởi động lại web. Với job Bilibili đã lỗi trước cập nhật,
mở lịch sử, bấm thử lại job liên kết rồi thử lại lượt tương ứng trong Reup
Agent. Bản sửa không tự chạy lại lịch sử hoặc tạo job đăng video mới.

## Tải video Douyin và tiếp tục lượt bị ngắt

Job Douyin của Reup Agent dùng hồ sơ trình duyệt của đúng tài khoản nguồn,
cùng cookie và thông tin phát đã đọc trong phiên tìm kiếm. Thông tin phát
được lưu riêng trong hồ sơ nguồn, không đưa URL CDN có chữ ký ra API danh
sách video, và hết hạn sau 10 phút. Khi không có dữ liệu phát còn hạn, bộ
tải mở trang video bằng hồ sơ đó để đọc phản hồi bình thường của nền tảng.

Nếu trang video vẫn trả 403 hoặc CAPTCHA, ứng dụng báo rõ nguồn chưa cho
tải và tạm nghỉ yêu cầu tải của phiên này trong 2 phút, thay vì yêu cầu
xuất cookie hay liên tục gọi yt-dlp. Phiên đăng nhập vẫn được giữ. Trên thẻ
video bị lỗi, bấm **Kiểm tra video nguồn** để mở đúng video bằng phiên đã
lưu. Bạn tự hoàn tất xác minh nếu có; ứng dụng không tự giải CAPTCHA.
Khi thông tin phát đã được đọc, đóng cửa sổ rồi bấm **Thử lại** ngay trên
thẻ video. Dữ liệu phát mới còn hạn được dùng ngay kể cả trong thời gian
tạm nghỉ nguồn.

Nút **Thử lại** chạy lại job liên kết bằng cơ chế retry hiện có, giữ nguyên
job ID, kiểm tra tài khoản sở hữu và credit. Không cần thử lại riêng ở
Lịch sử. Các bước đăng đã có checkpoint vẫn tiếp tục theo checkpoint cũ.
Job bị ngắt khi server khởi động lại được hiển thị là cần thử lại; đây là
trạng thái chờ thao tác, không ghi traceback lỗi chương trình cho từng lượt.
