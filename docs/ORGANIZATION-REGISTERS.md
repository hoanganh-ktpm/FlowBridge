# Dữ liệu tổ chức

Portal → **Dữ liệu tổ chức** cho phép xem kho, mặt hàng và đơn hàng đang lưu ở
các hệ thống nguồn của tổ chức đang đăng nhập. Phạm vi tổ chức do Workspace
ràng buộc trên server; người gọi không thể truyền `org_id` khác.

- **Tất cả kho:** gồm cả kho chưa có dòng tồn kho hoặc chưa có lịch sử dự báo.
- **Tất cả mặt hàng:** gồm cả sản phẩm chưa nhập kho và sản phẩm ngừng hoạt động.
- **Tất cả đơn hàng:** không giới hạn theo kỳ kế hoạch; có đơn đang mở, đã hoàn
  tất, đã hủy và các trạng thái khác. Xem từng dòng hàng để giữ đúng tên, lượng,
  đơn vị đã lưu; không cộng các đơn vị khác nhau.

Danh sách phân trang 50 dòng, có lọc mã chính xác và trạng thái. Có thể bỏ lọc
để xem lại toàn bộ phạm vi được cấp quyền. Mỗi lần đổi trang/lọc/đọc chi tiết đều
qua Core bằng token của người gọi và kiểm quyền lại trước khi trả kết quả. Nếu
quyền bị thu hồi hoặc nguồn không đọc được, giao diện xóa kết quả cũ; không dùng
dữ liệu seed hay SQL dự phòng. Không cam kết snapshot nguyên tử giữa các trang.

Core hiện giới hạn offset 10.000; vượt giới hạn phải lọc thêm, không báo danh sách
thiếu là toàn bộ. Đây là tra cứu các trường nghiệp vụ đã công bố, không phải giao
diện xuất tất cả bảng nội bộ, dữ liệu cá nhân hoặc dữ liệu của tổ chức khác.

Các màn **Cần xử lý** và **Đơn hàng & giao nhận** tiếp tục tính toán đơn đang mở
trong kỳ đã chọn. Dùng màn Dữ liệu tổ chức để tra lịch sử đầy đủ; việc có dữ liệu
trong bảng không đồng nghĩa đủ lịch sử để dự báo cho mọi sản phẩm/kho.

## Nguồn dữ liệu và triển khai

Migration `sales_0006` thêm `v_order_register` và `v_order_register_lines`;
`warehouse_0006` thêm `v_location_register` và `v_product_register`. Đây là view
đọc trên bảng hiện có; không tạo seed, đổi số lượng hay sửa ngày đơn hàng.
Các view không công bố điện thoại, địa chỉ nhận hàng hoặc thông tin đăng nhập.

Endpoint: `POST /api/v1/data-workspace/records`, với collection `warehouses`,
`products`, `orders` hoặc `order_lines`. Nguồn được ánh xạ bằng semantic role,
không nhận tên bảng/câu SQL từ người dùng. Chi tiết API trong OpenAPI của Workspace.

Trên profile local đã có ownership ledger và hạ tầng đang hoạt động:

```powershell
.\.venv\Scripts\python.exe -m scripts.decision_register_access prepare
.\.venv\Scripts\python.exe -m scripts.decision_demo_runtime restart core
.\.venv\Scripts\python.exe -m scripts.decision_demo_runtime restart worker
.\.venv\Scripts\python.exe -m scripts.decision_register_access publish
.\.venv\Scripts\python.exe -m scripts.decision_demo_runtime restart workspace
```

`prepare` kiểm tra đúng các owner local trên 127.0.0.1:55432, chạy migrations,
cấp SELECT cho reader và cập nhật allowlist. `publish` dùng các lệnh Core để tạo
contract, công bố view và xin/cấp read grant cho nhóm hiện tại; giữ ledger trong
`.tmp/decision-demo/`. Không tạo lại grant đã hết hạn/thu hồi để né chính sách.
Các deployment khác phải công bố/cấp quyền qua quy trình Core của deployment đó.

Đã đối chiếu local ngày 26/10/2026: tổ chức đang dùng có 1 kho, 8 sản phẩm,
4.708 đơn (351 mở, 4.357 hoàn tất). Đây là số liệu thực đọc từ database local;
nguồn gốc các bản ghi vẫn là fixture giả lập, không phải dữ liệu doanh nghiệp thật.
