# Tra cứu dữ liệu

Portal → **Dữ liệu và công cụ → Tra cứu dữ liệu** là sổ đọc bản ghi đã lưu của tổ
chức đang đăng nhập. Màn Tổng quan phục vụ kiểm tra kế hoạch và đơn đang chờ;
màn Tra cứu dùng để tìm bản ghi, đối chiếu số lượng và xem cả lịch sử đã hoàn
thành/hủy, không giới hạn theo kỳ dự báo.

Trang chính chỉ có tra mã nhanh và bốn lối mở danh sách. Bảng, bộ lọc, tìm kiếm
và phân trang nằm trong popup. Các nhóm dữ liệu có mục đích riêng:

- **Danh mục kho:** tên, mã, trạng thái; gồm cả kho chưa phát sinh tồn. Từ một kho
  có thể mở tồn kho hoặc đơn của đúng kho đó trong popup tiếp theo.
- **Danh mục mặt hàng:** tên, mã, đơn vị tính; gồm cả sản phẩm chưa nhập kho hoặc
  ngừng hoạt động. Đơn vị tính không phải số lượng. Nút **Xem tồn theo kho** mở
  số lượng ghi nhận của đúng mã sản phẩm.
- **Tồn kho thực tế:** mỗi dòng là một mặt hàng tại một kho. Hiện **Trong kho**,
  **Đã giữ cho đơn**, **Có thể dùng**, luôn kèm đơn vị, ví dụ `618 cái`, `608 cái`,
  `10 cái`. Đọc nguồn `warehouse.stock_availability` đã được cấp quyền; không
  chạy dự báo, không tạo lại số tồn từ seed. Thiếu bản ghi không tự chuyển thành
  tồn bằng 0; NULL hiển thị **Chưa có số liệu**. Điện thoại hiển thị thẻ số lượng.
- **Đơn hàng và dòng hàng:** mọi trạng thái, kể cả đơn nháp, hoàn tất và hủy. Mở
  đơn xem từng món với lượng khách đặt, ví dụ `12 cái` hoặc `1,125 ram`. Số dòng
  hàng trên danh sách không phải tổng số sản phẩm; không cộng khác đơn vị.

Lọc mã chính xác áp dụng trên toàn nguồn, lọc trạng thái dành cho các danh mục
và đơn. Tìm tên/mã nhanh chỉ tìm trong trang hiện tại và được ghi rõ phạm vi.
Danh sách 50 bản ghi/trang; dùng trang sau để tiếp tục, không tải toàn bộ lịch
sử vào trình duyệt. Trở lại popup cha giữ trang và tìm kiếm trước đó.

Phạm vi tổ chức được ràng buộc trên server. Bộ lọc `warehouse_id` chỉ thu hẹp
phạm vi cho tồn kho/đơn; không mở sang tổ chức khác. Tên kho/mặt hàng được nối
qua danh mục đã cấp quyền, giới hạn trong các định danh của trang vừa đọc và
kiểm lại quyền trước khi trả. Không có SQL, fixture hoặc đường đọc dự phòng.

Tra cứu hỗ trợ cache đọc của Workspace; khi nguồn liên quan thay đổi, các popup
đóng và thông báo yêu cầu cập nhật. Kết quả có giờ đọc, không cam kết snapshot
nguyên tử giữa các trang. Lỗi quyền xóa kết quả cũ, không giữ dữ liệu đã mất quyền.
Core giới hạn offset 10.000; vượt giới hạn yêu cầu lọc thêm, không trình một danh
sách thiếu như thể đã đọc đủ.

## Nguồn dữ liệu và triển khai

Migration `sales_0006` thêm `v_order_register` và `v_order_register_lines`;
`warehouse_0006` thêm `v_location_register` và `v_product_register`. Đây là view
đọc trên bảng hiện có; không tạo seed, đổi số lượng hay sửa ngày đơn hàng.
Các view không công bố điện thoại, địa chỉ nhận hàng hoặc thông tin đăng nhập.

Endpoint: `POST /api/v1/data-workspace/records`, với collection `warehouses`,
`products`, `orders`, `order_lines` hoặc `inventory`. `orders` và `inventory` hỗ trợ
lọc `warehouse_id`; `inventory` lọc mã theo SKU và không nhận bộ lọc trạng thái. Nguồn được ánh xạ bằng semantic role,
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

## Tổng quan tất cả kho cho người quản trị

Theo yêu cầu mở rộng phạm vi của người dùng, **Tổng quan kho** có chế độ tổng quan
tất cả kho thuộc các tổ chức đã được cấu hình rõ ràng cho quản trị. Người dùng
thường vẫn giữ phạm vi một tổ chức; màn Dữ liệu tổ chức phía trên không đổi phạm vi.

`POST /all-warehouses` yêu cầu PLATFORM_ADMIN. Server lấy danh sách tổ chức từ
`FLOWBRIDGE_WORKSPACE_OVERVIEW_ORGS`, không nhận tổ chức từ body người gọi. Mỗi
nguồn vẫn được đọc riêng với org_id tương ứng và token thật của người gọi,
kiểm grant qua Core rồi kiểm lại quyền trước khi trả kết quả. Quyền quản trị
không thay thế grant của Data Product. Nếu bị từ chối nguồn thì không trả một
kết quả thiếu như thể đã đọc đủ tất cả kho.

Để bật rõ phạm vi tất cả các kho **đã có trên profile local**, chạy:

```powershell
.\.venv\Scripts\python.exe -m scripts.decision_register_access enable-all-warehouses
.\.venv\Scripts\python.exe -m scripts.decision_demo_runtime restart workspace
```

Lệnh ghi danh sách scope đã xem xét vào ledger local, không tạo kho/sản phẩm và
không cấp thêm grant. Nếu sau này có tổ chức mới, cần cấu hình lại rõ ràng; không
tự mở rộng quyền khi phát hiện org mới. Runtime configure giữ danh sách đã duyệt.

Đã đọc trực tiếp hai kho hiện có: Bình Dương (8 mặt hàng, 351 đơn mở) và TP.HCM
(3 mặt hàng, 3 đơn mở). Tổng 11 mặt hàng theo kho, 354 đơn mở. Đơn mở TP.HCM đã
được chỉnh hạn tương lai trong đợt bảo trì fixture, giữ số lượng và lịch sử bàn
giao. Kho TP.HCM chỉ có ba ngày phát sinh lịch sử và chưa có thời gian chuẩn bị
được xác nhận, nên hiện số tồn/đơn nguồn và thông báo chưa đủ lịch sử dự báo;
không tạo thêm lịch sử giả hay ghi thiếu hàng bằng 0 để làm báo cáo qua.

Portal tải tổng quan theo hai bước: gửi `include_planning: false` để hiện tồn
kho và toàn bộ đơn chưa hoàn tất trước, rồi tính nền từng kho bằng
`include_planning: true` cùng `warehouse_id`. ID kho vẫn phải được tìm thấy trong
danh mục thuộc các phạm vi quản trị đã duyệt; body không được mở rộng org.
Tối đa hai kho tính đồng thời. Khi đang tính, lượng thiếu là `null` và trạng thái
`pending`; không biểu diễn thành 0. Lỗi tính toán giữ số liệu đã đọc và cho thử
lại; lỗi quyền truy cập xóa kết quả đang hiển thị. Tải lại hoặc rời màn hình hủy
yêu cầu cũ, không dùng kết quả của kỳ trước để ghi đè kỳ mới. Không lưu cache
dữ liệu nghiệp vụ hay quyền đọc giữa các lượt HTTP.

Màn **Tổng quan kho** có thẻ tóm tắt cho từng kho, thanh đếm mặt hàng theo trạng
thái dự báo, lọc/tìm hàng không dấu và danh sách đơn 20 dòng/trang. Chi tiết mặt
hàng của đơn mở trong bảng bên phải; không cần cuộn đến cuối danh sách. Nút thử
bổ sung giữ đúng kho, mặt hàng và kỳ đang xem, chỉ hiện trong phạm vi phân tích
được cấp quyền. Yêu cầu tổng quan ở máy chủ có giới hạn 60 giây và hủy công việc
khi người gọi ngắt kết nối để các lượt tải cũ không tiếp tục chiếm hàng chờ.
