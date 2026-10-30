# Rà soát định hướng Reup Agent — 26/10/2026

## Phạm vi và kết luận

Rà soát cấu trúc mã nguồn, luồng ASR/TTS/reup, remix planner, discovery,
automation, publishing, dependencies, CI và tài liệu phát hành. Đây là đánh
giá mã nguồn và kiểm thử offline; không phải chứng nhận hoạt động thực tế
trên Douyin/Bilibili/YouTube hay benchmark GTX 1660 Super. Không đọc `.env`,
không gọi đăng video thật, không thay đổi database người dùng.

Sản phẩm nên tập trung vào: tìm video phù hợp kênh → chọn nguồn được phép
sử dụng → tải → dịch/sub/lồng tiếng theo nhân vật → tùy chọn tóm tắt short
→ tạo thumbnail/metadata → QA → đăng hoặc lên lịch → đo kết quả.

Không cần viết lại toàn bộ. Giữ downloader, localization, registry giọng,
publishing pack, source dedup, OAuth, queue và learning. Tách creator sinh
kịch bản/hình ảnh thành chế độ tùy chọn, không nằm trên đường chạy reup.

## Phát hiện theo mức ưu tiên

| Ưu tiên | Bằng chứng | Tác động và hướng xử lý |
| --- | --- | --- |
| P0 | `segment.py:TranscriptSegment` chỉ có start/end/text; `orchestrator/service.py:_synthesize_timed_track*` truyền cùng voice cho mọi câu | Không giữ danh tính người nói trong reup. Thêm speaker ID xuyên ASR, dịch, lưu trữ, sửa câu, TTS, cache và render. |
| P0 | `web/channel_agent_router.py:_automation_service` gọi generate_blueprint/resume_script/generate_visual_plan/ProductionRenderService | Agent đang sản xuất nội dung mới, khác yêu cầu. Thêm workflow reup riêng dùng localization, không giả lập bằng cách đổi nhãn creator. |
| P0 | `trends/providers/agent_reach.py:_parse_agent_reach_output` luôn trả `[]`; search chỉ cho youtube/rss/web | Adapter tùy chọn này chưa cung cấp discovery hoạt động; không thể dùng nó làm bằng chứng hỗ trợ tự tìm Douyin/Bilibili. Viết adapter theo hợp đồng thực và test fixture. |
| P1 | `channel_agent/trends.py` là metadata-only YouTube discovery | Có nền tảng nghiên cứu YouTube; cần bổ sung connector Douyin/Bilibili và handoff nguồn được chọn sang job reup. Tải URL và tìm video theo chủ đề là hai khả năng riêng. |
| P1 | `remix/planner.py` liệt kê scene_detect_and_segment/storyboard_new_timeline nhưng các tên bước đó chỉ xuất hiện trong planner | Chưa có bằng chứng luồng long-to-short chọn nhiều đoạn quan trọng rồi dựng thành một bản tóm tắt hoàn chỉnh. Cần EDL có timestamp và renderer thực. |
| P1 | `production_publishing.py:YouTubeResumablePublisher.upload` luôn gửi Content-Range từ byte 0, lặp lại khi nhận 308 | Có lưu session nhưng chưa thực hiện tiếp tục từ offset server xác nhận. Bổ sung query trạng thái, parse Range, seek/chunk và xử lý mất response cuối. |
| P1 | `ProductionPublishingService.run` lưu external_video_id trước upload thumbnail; lần retry gặp ID sẽ gọi refresh rồi return | Nếu thumbnail lỗi, retry có thể không thử thumbnail lại. Tách trạng thái/retry từng thành phần. |
| P1 | `ProductionPublishingService.refresh` suy ra published từ uploadStatus/processingStatus mà không kiểm tra privacyStatus trong nhánh cuối | Video private hoặc quá giờ hẹn nhưng vẫn private có thể mang nhãn published. Phân biệt uploaded_private, processing, scheduled, public và lỗi lịch. |
| P1 | `social/youtube.py:YouTubeUploader.upload` đọc toàn bộ video và ghép multipart trong RAM | Luồng upload cũ tăng bộ nhớ với video dài; hợp nhất sau khi hoàn thiện resumable streaming. |
| P1 | `requirements.txt` bắt buộc diffusers/transformers/accelerate cùng stack CUDA | Cài đặt nặng dù người dùng chỉ cần reup. Tách requirements cơ bản, CUDA và creator tùy chọn; xác minh wheel/driver thực tế trước đổi phiên bản. |
| P1 | `docs/RELEASE_AUDIT_BLOCKERS.md` ghi nhận cookie/session từng xuất hiện trong lịch sử | Kiểm tra việc thay session/key nếu chưa làm. Đây là ghi nhận từ audit trước, không phải phát hiện rò rỉ mới. |
| P2 | web/app.py khoảng 7.900 dòng, web/store.py khoảng 4.970, static/app.js khoảng 6.280 | Tách router, orchestration, repository và UI theo tính năng khi sửa từng luồng; tránh refactor đại trà trước MVP. |

Các nhận định publishing trên được suy ra trực tiếp từ nhánh mã; cần test hồi
quy riêng để tái hiện trước khi sửa. Test hiện có dùng provider giả không đủ
chứng minh hành vi mạng thật.

## Hai người nói và chi phí

- Phụ đề nhiều người: ASR nhận lời + diarization xác định ai nói lúc nào.
  Whisper hiện tại không tự cung cấp speaker ID. Nếu mất hẳn lời người thứ
  hai thì còn phải kiểm tra ASR/audio/overlap, không chỉ thêm giọng TTS.
- Lồng tiếng nhiều người: ánh xạ speaker_0/speaker_1 sang hai giọng riêng;
  không đổi giọng ngẫu nhiên và không luân phiên theo số thứ tự câu.
- `tts/registry.py` đã có Edge/Piper; `channel_agent/production_render.py`
  đã có speaker_mapping cho vai trong voice plan. Có thể tái sử dụng ý tưởng
  này nhưng đây chưa phải tự nhận diện người nói trong video nguồn.
- pyannote community-1 chạy local, có CPU/GPU và chạy offline sau tải model;
  cần tài khoản/token và chấp nhận điều kiện model. Không mất phí API khi
  tự chạy; vẫn có điện, thời gian và chi phí máy.
- Edge TTS là dịch vụ online qua thư viện không yêu cầu API key; không xem
  nó như cam kết dịch vụ miễn phí không giới hạn. Piper chỉ dùng model có
  chất lượng/ngôn ngữ/giấy phép phù hợp đã được xác minh.
- Cho phép chọn một giọng, tự nhận diện, hoặc gán thủ công từng câu. Không
  ép hai người nếu clip chỉ có một người. Khi độ tin cậy thấp, hiện đoạn
  cần kiểm tra thay vì đoán giới tính từ speaker ID.
- Hai người nói chồng nhau vẫn khó: diarization không đồng nghĩa tách sạch
  từng tiếng nói hay đảm bảo nhận đủ lời. Không hứa tự động đúng 100%.

Tiêu chí nghiệm thu: một người không đổi giọng; hai người luân phiên giữ
đúng giọng; câu qua bước dịch/sửa/retry vẫn giữ speaker ID; timeline không
trôi; overlap được đánh dấu; dự án cũ không có speaker ID vẫn mở được.

## Một video dài thành một short tóm tắt

Tách hai chế độ: highlight giữ các đoạn gốc đáng chú ý; summary short chọn
nhiều đoạn tạo mạch mở đầu → tình tiết chính → kết quả, tùy chọn lời tóm tắt.

Luồng đề nghị:

1. ASR theo timestamp và chia chương/cảnh; cache transcript gốc.
2. Tóm tắt theo từng phần rồi tổng hợp để video dài không vượt context.
3. Chọn sự kiện từ transcript, kèm đoạn hình tương ứng. Với video ít lời,
   bổ sung keyframe/nhãn cảnh hoặc đánh dấu cần chọn tay.
4. Lưu edit decision list gồm source_start, source_end, lý do chọn,
   destination_start/end và liên kết lời/sub/nhân vật.
5. Kiểm tra đoạn nằm trong nguồn, không chồng sai, không cắt nửa câu,
   đủ bối cảnh và tổng thời lượng không vượt mục tiêu.
6. FFmpeg cắt/ghép, đổi timestamp subtitle/audio, crop 9:16 hoặc giữ hình
   với nền; không crop mất nhân vật và chữ quan trọng.
7. Xem preview và chỉnh thứ tự trước render. Tạo metadata từ bản short
   đã dựng, không từ nội dung bị loại.

MVP đặt mục tiêu 45–60 giây do lựa chọn sản phẩm, không coi đó là giới hạn
chung hiện hành của YouTube Shorts. Không cần sinh video bằng diffusion.

## Workflow Agent đúng nhu cầu

Trạng thái đề nghị:

`discovered → shortlisted → downloaded → transcribed → localized →
optional_short → packaged → qa_passed → scheduled/uploaded → published`.

Mỗi bước có failed/retry/cancel, artifact đã lưu và lý do dừng rõ ràng.

Hồ sơ kênh gồm chủ đề, keyword Việt/Trung/Anh, từ khóa loại trừ, nguồn ưu
tiên, ngôn ngữ đầu ra, độ dài, giọng, template thumbnail, lịch/múi giờ và
hạn mức mỗi ngày. Dùng lịch sử kênh để gợi ý; cho chủ kênh sửa cấu hình.

Discovery mỗi nền tảng cần adapter riêng với capability/health check,
pagination, giới hạn tốc độ, cache, retry có backoff, cookie hợp lệ khi cần.
Không vượt CAPTCHA/đăng nhập; nếu connector ngừng hoạt động, cho nhập URL
hoặc danh sách kênh nguồn. Bắt đầu từ nguồn theo dõi cố định rồi mở tìm kiếm.

Dedup theo platform+video ID, URL chuẩn hóa và fingerprint nội dung để phát
hiện cùng video đăng chéo; bổ sung lịch sử đã reup theo kênh đích. Không tải
toàn bộ kết quả tìm kiếm trước khi lọc chủ đề/thời lượng/nguồn phù hợp.

Dùng `publishing/PublishingPackService` để tái sử dụng thumbnail từ frame,
title/description/hashtag và retry từng thành phần; không cần SD-Turbo.
Adapter publishing cần nhận output của localization thay vì bắt buộc
production item với script/visual asset do creator tạo ra.

UI cho ba chế độ: duyệt từng bước, tự xử lý rồi duyệt đăng, và tự đăng theo
cấu hình đã bật cho kênh. Có nút dừng, daily cap, preview và lịch sử thao tác.
Không biến mọi clip thành một vòng hỏi xác nhận; tôn trọng policy đã lưu.

Lên lịch bằng upload sẵn rồi đặt publishAt trên YouTube khi phù hợp, để
không phụ thuộc máy nhà còn bật đúng giờ. Máy vẫn cần chạy cho discovery,
render và upload; trạng thái published phải xác nhận từ nền tảng.

## Cấu hình khởi điểm GTX 1660 Super

Đây là đề xuất cần benchmark trên máy thật, không phải số đo đã xác minh:

- Một tác vụ GPU nặng tại một thời điểm, bao gồm ASR/OCR/Demucs/LLM.
- Bắt đầu Whisper base/small; wrapper hiện tại đã có tránh fp16 tự động
  trên GTX 16xx. Không bật ép fp16 chỉ vì có CUDA.
- Diarization CPU trước để giảm tranh VRAM; chỉ chuyển GPU sau thử nghiệm.
- LLM local quantized nhỏ hoặc CPU theo RAM thực tế; unload/giới hạn GPU
  khi ASR/OCR chạy. Chất lượng tiếng Trung/Việt cần đánh giá trên clip thật.
- Thumbnail frame + chữ, render preview nhẹ rồi xuất 1080p; thử NVENC nếu
  FFmpeg/driver hỗ trợ. Không để job reup tự khởi động sinh hình/video AI.
- Cache transcript/diarization/dịch/audio từng câu, ghi checkpoint và giới
  hạn dung lượng/tuổi file tạm. Retry không làm lại toàn bộ video dài.

## Thứ tự đầu tư và đo hiệu quả

1. Sửa lỗi publishing và hoàn thiện đường reup tới publishing pack/publisher.
2. Speaker ID và mapping thủ công, sau đó thêm diarization tự động.
3. Agent reup dùng URL/danh sách nguồn và cấu hình kênh; creator tùy chọn.
4. Discovery YouTube, Bilibili, Douyin theo capability thực tế và mẫu thử.
5. Summary short có EDL/preview, sau đó tối ưu chọn cảnh/crop.
6. Tự đăng theo cấu hình và learning từ kết quả sau pilot.

Đo tỷ lệ job hoàn thành, phút xử lý/phút nguồn, lỗi phải sửa tay, độ đúng
người nói, phút xem/retention, CTR thumbnail và số video đăng trùng. Chọn
một niche và một kênh để pilot trước khi mở nhiều kênh. Không dùng số
lượng tính năng hay số test pass làm thước đo duy nhất của sản phẩm.

Nguồn bên ngoài cần có quyền sử dụng phù hợp; dịch/sub/đổi nhạc không tự
đảm bảo đủ điều kiện kiếm tiền. Bản quyền và reused content là hai vấn đề
khác nhau. Lưu bằng chứng nguồn/quyền sử dụng trong workflow hiện có.

## Tài liệu kỹ thuật đối chiếu

- https://huggingface.co/pyannote/speaker-diarization-community-1
- https://github.com/rany2/edge-tts
- https://developers.google.com/youtube/data/reference/rest/v3/videos
- https://support.google.com/youtube/answer/1311392?hl=en

Kết quả regression của lần rà soát được ghi trong phản hồi đi kèm; số
1.000 passed trong tài liệu release cũ không được xem là kết quả lần này.
