# Đánh giá nhà cung cấp DEMO-S01 — Demo Supplier 1

## Thông tin nhà cung cấp và người nhận xét — DEMO-S01

- **Nhà cung cấp:** Demo Supplier 1.
- **Mã nhà cung cấp:** DEMO-S01.
- **Supplier ID:** `3806e329-2ebe-5cfc-895d-1bd60894b348`.
- **Người nhận xét được phân công:** demo-warehouse-0.
- **User ID người nhận xét:** `bc7cf79e-ccce-5dea-8ac6-097ddc2a1a53`.
- **Vai trò trong hệ thống:** WAREHOUSE.
- **Bộ phận:** Demo department 1 (DEMO-D1).
- **Ngày lập báo cáo:** 15/10/2026.
- **Mốc dữ liệu:** 15/10/2026 18:00 (UTC+07:00).
- **Phạm vi hồ sơ:** các PO phát hành từ 01/05/2025 đến mốc dữ liệu nêu trên.

## 1. Chất lượng sản phẩm — DEMO-S01

### 1.1. Phạm vi ghi nhận nghiệm thu — DEMO-S01

Nhận xét của demo-warehouse-0 về DEMO-S01: hồ sơ có 240 lần ghi nhận nhận hàng đạt yêu cầu, liên quan 161 PO. Có 160 PO đã được ghi nhận đủ số lượng tại mốc báo cáo.

Hồ sơ nhận hàng chỉ thể hiện số lượng đã được tiếp nhận. Chưa có dữ liệu kiểm tra lỗi sản phẩm, số lượng bị từ chối, nguyên nhân lỗi hoặc kết quả thử nghiệm chất lượng. Vì vậy, không thể tính tỷ lệ lỗi hay kết luận toàn bộ sản phẩm của nhà cung cấp không có lỗi từ các phiếu nhận hàng này.

### 1.2. Nhóm sản phẩm trong danh mục — DEMO-S01

Các sản phẩm đang được ghi nhận là sẵn có trong danh mục của DEMO-S01:

| Mã sản phẩm | Tên sản phẩm | Nhóm sản phẩm |
|---|---|---|
| DEMO-P01 | Demo Product 1 | office |
| DEMO-P02 | Demo Product 2 | electronics |
| DEMO-P03 | Demo Product 3 | materials |
| DEMO-P04 | Demo Product 4 | office |
| DEMO-P05 | Demo Product 5 | electronics |
| DEMO-P06 | Demo Product 6 | materials |

## 2. Phản hồi xác nhận và xử lý phát sinh — DEMO-S01

### 2.1. Phản hồi xác nhận PO — DEMO-S01

Nhận xét của demo-warehouse-0 về DEMO-S01: có 153/172 PO có phản hồi xác nhận của nhà cung cấp được ghi nhận trước mốc báo cáo. 19 PO còn lại chưa có bản ghi xác nhận trong hồ sơ, không đồng nghĩa các PO đó đều chưa giao hàng.

Trong các PO đã có xác nhận, trung vị thời gian từ phát hành đến phản hồi là 1.51 ngày. Có 57 PO có nhiều hơn một bản ghi ngày hứa giao. Thay đổi ngày hứa không làm thay đổi hạn giao gốc khi đánh giá giao trễ.

### 2.2. Giới hạn nhận xét về xử lý khiếu nại — DEMO-S01

Hồ sơ của DEMO-S01 hiện có phản hồi xác nhận đơn và các lần cập nhật ngày hứa giao; chưa có biên bản khiếu nại chất lượng, đổi trả, phân tích nguyên nhân hoặc nghiệm thu biện pháp khắc phục. Không dùng tốc độ phản hồi xác nhận PO làm kết luận về tốc độ giải quyết khiếu nại.

## 3. Thực hiện giao nhận và hồ sơ cần lưu ý — DEMO-S01

### 3.1. Tổng hợp tại mốc báo cáo — DEMO-S01

Nhận xét của demo-warehouse-0 về DEMO-S01 dựa trên 172 PO:

- Đã ghi nhận nhận đủ: **160 PO**.
- Nhận đủ đúng hoặc trước hạn gốc: **77 PO**.
- Nhận đủ sau hạn gốc: **83 PO**, chiếm **51,88%** trong các PO đã nhận đủ.
- Chưa ghi nhận nhận đủ: **12 PO**, trong đó **1 PO** đã ghi nhận nhận một phần.
- Đã qua hạn gốc nhưng chưa ghi nhận nhận đủ: **1 PO**.

Tỷ lệ trên là thống kê của các PO đã hoàn tất trong phạm vi báo cáo, không phải điểm dự đoán của TabICLv2. Các PO còn mở không được gán là giao đúng hạn.

### 3.2. PO đã nhận đủ sau hạn gốc: DEMO-PO-00212 — DEMO-S01

Nhận xét của demo-warehouse-0 về DEMO-S01: PO **DEMO-PO-00212** đặt **270 unit Demo Product 3 (DEMO-P03)**, phát hành ngày **18/08/2025**, hạn giao gốc **12/09/2025**.
Hồ sơ đã ghi nhận đủ **270 unit**. Ngày giao của đợt hoàn tất là **08/10/2025**; thời điểm hệ thống ghi nhận đủ là **08/10/2025 16:00 (UTC+07:00)**. PO hoàn tất sau hạn gốc **26 ngày**.
Ngày hứa giao mới nhất đã được ghi nhận là **08/10/2025**, ghi nhận lúc **25/08/2025 12:57 (UTC+07:00)**. Đây là mốc cam kết; kết quả giao thực tế được đối chiếu theo phiếu nhận hàng.

### 3.3. PO đã nhận đủ đúng hạn: DEMO-PO-00950 — DEMO-S01

Nhận xét của demo-warehouse-0 về DEMO-S01: PO **DEMO-PO-00950** đặt **86 unit Demo Product 3 (DEMO-P03)**, phát hành ngày **21/09/2026**, hạn giao gốc **15/10/2026**.
Hồ sơ đã ghi nhận đủ **86 unit**. Ngày giao của đợt hoàn tất là **14/10/2026**; thời điểm hệ thống ghi nhận đủ là **14/10/2026 16:00 (UTC+07:00)**. PO hoàn tất đúng hoặc trước hạn gốc.
Ngày hứa giao mới nhất đã được ghi nhận là **15/10/2026**, ghi nhận lúc **27/09/2026 03:33 (UTC+07:00)**. Đây là mốc cam kết; kết quả giao thực tế được đối chiếu theo phiếu nhận hàng.

### 3.4. PO còn cần theo dõi: DEMO-PO-00940 — DEMO-S01

Nhận xét của demo-warehouse-0 về DEMO-S01: PO **DEMO-PO-00940** đặt **70 unit Demo Product 5 (DEMO-P05)**, phát hành ngày **17/09/2026**, hạn giao gốc **29/09/2026**.
Tại mốc báo cáo, hồ sơ mới ghi nhận **0 unit**; còn **70 unit** chưa được ghi nhận nhận đủ. Hạn giao gốc đã qua **16 ngày**. Cần đối chiếu thực nhận và kế hoạch giao phần còn lại; chưa có kết quả hoàn tất cuối cùng.
Ngày hứa giao mới nhất đã được ghi nhận là **21/10/2026**, ghi nhận lúc **19/09/2026 13:03 (UTC+07:00)**. Đây là mốc cam kết; kết quả giao thực tế được đối chiếu theo phiếu nhận hàng.

## 4. Cảnh báo và khuyến nghị hợp tác — DEMO-S01

### 4.1. Việc cần theo dõi — DEMO-S01

Nhận xét của demo-warehouse-0 về DEMO-S01: ưu tiên rà soát **1 PO đã qua hạn gốc nhưng chưa ghi nhận nhận đủ**. Cần kiểm tra có chậm cập nhật phiếu nhận hay thực tế còn thiếu hàng, đồng thời đối chiếu phản hồi của nhà cung cấp cho từng PO.

### 4.2. Điều kiện sử dụng nhận xét — DEMO-S01

Trạng thái hồ sơ nhà cung cấp DEMO-S01 trong dữ liệu nguồn là **active**. Báo cáo không tự thay đổi trạng thái này. Khi lập phương án mua tiếp, cần kết hợp điều kiện mua hiện hành, kết quả dự đoán phù hợp và đánh giá chất lượng bổ sung nếu nghiệp vụ yêu cầu.

Không dùng giao trễ để suy ra hàng bị lỗi, không coi xác nhận PO là đã giao đủ và không coi một PO nhận đủ là bằng chứng đã đóng khiếu nại chất lượng. Chưa đủ dữ liệu để đưa ra kết luận về xử lý đổi trả hoặc chất lượng toàn bộ sản phẩm.
