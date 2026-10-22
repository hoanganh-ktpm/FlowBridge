# Business Workflows

**Các quy trình BPMN sử dụng năng lực Workflow của DX-OS.**

Order Fulfillment là quy trình mẫu dự kiến: Sales tạo đơn → kiểm tra tồn → giữ hàng → xác nhận đóng gói → tạo vận đơn → bàn giao → cập nhật đơn.

BPMN được đặt tại đây khi triển khai; Flowable adapter thuộc backend/app/platform/workflow, cấu hình engine thuộc infra/flowable. Chưa có BPMN thực thi trong phiên bản hiện tại.

Khi xây quy trình cần định nghĩa nhánh thiếu hàng, timeout/retry, idempotency, giải phóng reservation và quyền User Tasks. Không ghi cứng quy tắc domain vào adapter engine dùng chung.
