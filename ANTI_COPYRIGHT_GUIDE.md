# Hướng dẫn Chống Quét Bản Quyền (Anti-Copyright)

## Tổng quan

Hệ thống đã tích hợp các bộ lọc video để tránh bị Content ID quét bản quyền trên TikTok, Facebook, YouTube, và các nền tảng khác.

## Các bộ lọc có sẵn

### 1. Letterbox (Khung đen)
- **Mô tả**: Thêm khung đen vào các cạnh video
- **Định dạng**: `left:right:top:bottom` (pixels)
- **Ví dụ**: `100:100:0:0` - thêm 100px khung đen trái/phải
- **Hiệu quả**: Thay đổi tỷ lệ khung hình, tránh fingerprint

### 2. Zoom
- **Mô tả**: Zoom vào trung tâm video
- **Phạm vi**: 1.0 - 1.3 (1.0 = không zoom)
- **Khuyến nghị**: 1.05 - 1.1
- **Hiệu quả**: Thay đổi composition, tránh detection

### 3. Flip Horizontal (Lật ngang)
- **Mô tả**: Lật video theo chiều ngang (mirror)
- **Hiệu quả**: Đảo ngược không gian, rất hiệu quả tránh fingerprint

### 4. Speed Factor (Tốc độ)
- **Mô tả**: Thay đổi tốc độ video nhẹ
- **Phạm vi**: 0.95 - 1.05
- **Khuyến nghị**: 1.02 - 1.03
- **Hiệu quả**: Tránh audio fingerprint

### 5. Brightness (Độ sáng)
- **Mô tả**: Điều chỉnh độ sáng
- **Phạm vi**: -1.0 đến 1.0
- **Khuyến nghị**: 0.05 - 0.1
- **Hiệu quả**: Thay đổi màu sắc nhẹ

### 6. Contrast (Độ tương phản)
- **Mô tả**: Điều chỉnh độ tương phản
- **Phạm vi**: 0 đến 2
- **Khuyến nghị**: 1.05 - 1.1
- **Hiệu quả**: Thay đổi dynamic range

### 7. Saturation (Độ bão hòa)
- **Mô tả**: Điều chỉnh độ bão hòa màu
- **Phạm vi**: 0 đến 2
- **Khuyến nghị**: 1.05 - 1.1
- **Hiệu quả**: Thay đổi màu sắc

### 8. Noise (Nhiễu)
- **Mô tả**: Thêm nhiễu/grain vào video
- **Phạm vi**: 0 đến 100
- **Khuyến nghị**: 3 - 8
- **Hiệu quả**: Làm mờ fingerprint, nhưng có thể giảm chất lượng

### 9. Rotation (Xoay)
- **Mô tả**: Xoay video nhẹ
- **Phạm vi**: 0 đến 5 độ
- **Khuyến nghị**: 0.3 - 0.8 độ
- **Hiệu quả**: Thay đổi góc quay, tránh detection

### 10. Crop (Cắt cạnh)
- **Mô tả**: Cắt các cạnh video
- **Định dạng**: `left:right:top:bottom` (pixels)
- **Ví dụ**: `10:10:10:10` - cắt 10px từ mỗi cạnh
- **Hiệu quả**: Thay đổi kích thước và composition

## Preset Khuyến Nghị

### Preset Nhẹ (Giữ chất lượng cao)
```
Letterbox: 50:50:0:0
Zoom: 1.03
Flip: Bật
Speed: 1.01
Brightness: 0.03
Contrast: 1.03
Saturation: 1.03
Noise: 2
Rotation: 0.3
Crop: null
```

### Preset Trung Bình (Cân bằng)
```
Letterbox: 100:100:0:0
Zoom: 1.05
Flip: Bật
Speed: 1.02
Brightness: 0.05
Contrast: 1.05
Saturation: 1.05
Noise: 5
Rotation: 0.5
Crop: null
```

### Preset Mạnh (Tránh detection tối đa)
```
Letterbox: 150:150:0:0
Zoom: 1.08
Flip: Bật
Speed: 1.03
Brightness: 0.08
Contrast: 1.1
Saturation: 1.1
Noise: 8
Rotation: 0.8
Crop: 15:15:15:15
```

## Lưu ý Quan Trọng

1. **Không lạm dụng**: Quá nhiều filter có thể làm giảm chất lượng video đáng kể
2. **Test trước**: Luôn test với video ngắn trước khi áp dụng cho video dài
3. **Kết hợp thông minh**: Không cần dùng tất cả filter, chọn 3-5 filter phù hợp nhất
4. **Giữ nguyên nội dung**: Filter chỉ thay đổi hình thức, không thay đổi nội dung
5. **Theo dõi**: Nếu vẫn bị quét, điều chỉnh preset và test lại

## Cách Sử Dụng Trên Web UI

1. Đánh dấu checkbox "Bật chống quét bản quyền"
2. Panel anti-copyright sẽ hiện ra
3. Điều chỉnh các thông số theo preset hoặc tự chỉnh
4. Bấm "Bắt đầu xử lý"
5. Hệ thống sẽ tự động áp dụng filter khi render video

## Kỹ thuật Nâng Cao

### Kết hợp nhiều filter
Để hiệu quả nhất, nên kết hợp:
- **Spatial filters**: Zoom, Flip, Crop, Rotation (thay đổi không gian)
- **Color filters**: Brightness, Contrast, Saturation (thay đổi màu)
- **Temporal filters**: Speed (thay đổi thời gian)
- **Noise**: Làm mờ fingerprint

### Tránh detection theo nền tảng

**TikTok**:
- Tập trung vào Zoom + Flip + Speed
- Letterbox có thể không cần thiết (TikTok đã crop)

**YouTube**:
- Dùng Letterbox + Zoom + Color adjustment
- YouTube rất nhạy với audio fingerprint

**Facebook**:
- Tất cả filter đều hiệu quả
- Focus vào Noise + Rotation

## FAQ

**Q: Filter có làm giảm chất lượng video không?**
A: Có, nhưng preset nhẹ và trung bình giữ chất lượng tốt. Chỉ preset mạnh mới giảm đáng kể.

**Q: Có đảm bảo 100% tránh được quét bản quyền không?**
A: Không. Filter chỉ giảm khả năng bị quét, không đảm bảo 100%. Nền tảng liên tục cập nhật algorithm.

**Q: Tốc độ xử lý có chậm hơn không?**
A: Có, vì cần re-encode video. Thời gian tăng 20-50% tùy số filter.

**Q: Có thể lưu preset không?**
A: Hiện tại chưa có tính năng lưu preset, nhưng sẽ thêm trong tương lai.

**Q: Filter có ảnh hưởng đến subtitle không?**
A: Không, subtitle được render sau khi áp dụng filter.
