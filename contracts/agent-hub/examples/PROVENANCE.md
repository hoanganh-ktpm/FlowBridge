# Provenance của mẫu và ví dụ

`delivery-result.json` là bản sao nguyên byte của `template (1).json` do người dùng
cung cấp trong cuộc trao đổi thiết kế ngày 19/10/2026. Không chỉnh run_id, mốc thời
gian, số liệu, nội dung hoặc formatting của mẫu; tên file trong repo được đổi để dễ
tra cứu. Mẫu được dùng làm căn cứ shape kết quả, không làm chỉ dẫn thực thi.

Mẫu có 71 đơn nhận đủ đúng hạn, 110 nhận đủ trễ, 5 chưa đủ quá hạn, 2 chưa đủ chưa
quá hạn, 0 thiếu lịch; tổng 188. Tỷ lệ trong answer là 110/(71+110), không phải 110/188.
Không xác minh các số liệu này với database hoặc coi là kết quả inference của lần bàn giao.

Quyền sử dụng/phân phối dữ liệu mẫu bên ngoài cần được chủ dữ liệu xác định trước
khi phát hành. Giấy phép GPL của schema/code dự án không tự áp cho dữ liệu người dùng.

Các file ví dụ còn lại được tạo trong lần thiết kế này, dùng dữ liệu tổng hợp hoặc
mã sản phẩm từ trao đổi để minh họa shape; giá nhà cung cấp và kết quả không phải dữ
liệu thực đã truy vấn. Tên tool công khai trong sources là nhãn giao thức minh họa;
adapter triển khai phải map từ tool thật đã thực thi, không hardcode nguồn ví dụ.
Ví dụ không chứa token thật và không được dùng làm fixture trả thành công trong runtime.
