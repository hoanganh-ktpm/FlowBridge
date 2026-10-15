# Supplier Assistant API — database v0.4.1

## Nguồn và phạm vi

Contract app/AI 0.4.1 được đối chiếu với tài liệu người dùng cung cấp:
`supplier_assistant_database_schema_v0.4.1_TabICLv2.pdf`, 34 trang, ngày 15/10/2026.
SHA-256: `96f97be9205d14147fd0b79badc9cb539473a530add8ff48855c3b7675fdd06b`.
Dictionary C01–C27, role/ownership N01–N02, features E01–E04 và invariants G–K
là căn cứ mapping. PDF giữ nguyên; không được đưa vào package hoặc mặc định tái cấp phép.
Schema code/config do nhóm tạo dùng GPL-3.0-or-later; quyền của tài liệu, dữ liệu/model
bên ngoài cần được xác định riêng trước khi phân phối.

Runtime chỉ giữ API PostgreSQL/Supabase với 40 operation ứng dụng và 6 AI tools.
Module `supplier_assistant/` đi qua router → database service/policy → repository async.
DTO lấy từ JSON Schema contract, tách ORM. Baseline 0001 có 27 bảng; revision 0002
thêm bảng idempotency operational. [Hướng dẫn API](../docs/SUPABASE-API.md) ghi rõ
cấu hình, quyền và phần cần engine/adapter. Core 0.2.0 là thiết kế, không có route
core trả fixture trong runtime.

## Thay đổi không tương thích từ app/AI 0.1.0

| Trước | v0.4.1 |
|---|---|
| `id` dạng `PR-001`, `SUP-A` trên DTO nghiệp vụ | UUID ở `request_id`, `supplier_id`…; mã hiển thị ở `request_code`, `supplier_code`, `po_number` |
| PR có một supplier/product/quantity | Header PR + `items[]`; supplier mong muốn nullable theo từng dòng |
| `quantity` integer, tiền hai chữ số | Chuỗi NUMERIC(18,4), ví dụ `10.5000`, `10000.0000`; server tính line_total |
| `created_by` từ user subject, role tổng quát | `requested_by`/actor UUID nội bộ; map identity exact issuer + subject; sáu role có account_type/scope |
| `workflow_status`, `analysis_status`, `request_version` | `status`, `input_revision` và analysis task riêng; proposal có `version_no`/`is_current` |
| Người dùng POST `/api/v1/proposals` | AI_SERVICE POST `/internal/v1/proposals`, tự tạo pending/items/logical submit |
| PO đơn dòng, một result object | PO header/items/schedules; issue trả danh sách nhóm supplier + currency |
| Receipt/delivery history tóm tắt | goods_receipts append-only; actual_delivery_date khác knowledge received_at |
| Confirmation header không có chi tiết | Một quyết định/PO + detail revisions từng schedule; lượng có thể null khi chỉ hứa ngày |
| Năm feature cũ, score luôn bắt buộc | Fixed-v1 đúng 19 X, A/B chung schema/context; status lỗi giữ score null |
| File ID là nguồn duy nhất của RAG | Document ID + version + chunk + source URI + checksum; adapter tới kho Markdown hiện có |
| Email pending/unknown | queued/sending/sent/failed/delivery_unknown; outcome chưa rõ không tự retry |

Không bổ sung cột `version` vào mọi bảng. `expected_input_revision`, `expected_version_no`
và `expected_revision_no` là điều kiện command dựa trên cột domain tương ứng.
Idempotency-Key được scope theo actor/operation, kiểm fingerprint và lưu response
trong cùng transaction nghiệp vụ. Quyền hiện tại được kiểm lại trước replay.

## Mapping dictionary

Schema Row giữ đúng tên cột, SQL type trong `x-sql-type`, nullability và enum/domain.
Mỗi schema Row có `x-database-table`; DTO aggregate lồng items/schedules có
`x-database-aggregate`. Row/aggregate là shape API, không có nghĩa đã tạo bảng.

| Bảng PDF | DTO Row | Khóa chính |
|---|---|---|
| suppliers | Supplier | supplier_id |
| products | Product | product_id |
| supplier_products | SupplierProduct | supplier_product_id |
| purchase_requests | PurchaseRequestRow | request_id |
| purchase_request_items | PurchaseRequestItem | request_item_id |
| analysis_tasks | AnalysisTask | task_id |
| risk_predictions | Prediction | prediction_id |
| risk_analysis_reports | RiskAnalysisReport | report_id |
| analysis_supplier_candidates | AnalysisSupplierCandidate | candidate_id |
| proposals | ProposalRow | proposal_id |
| proposal_items | ProposalItem | proposal_item_id |
| approval_history | ApprovalHistory | approval_id |
| purchase_orders | PurchaseOrderRow | purchase_order_id |
| purchase_order_items | PurchaseOrderItem | po_item_id |
| purchase_order_schedules | PurchaseOrderSchedule | schedule_id |
| goods_receipts | GoodsReceipt | receipt_id |
| confirmation_tokens | ConfirmationToken | token_id |
| supplier_confirmations | SupplierConfirmationRow | confirmation_id |
| supplier_confirmation_items | SupplierConfirmationItem | confirmation_item_id |
| email_logs | EmailLog | email_log_id |
| outbox_events | OutboxEvent | event_id |
| departments | Department | department_id |
| notifications | Notification | notification_id |
| inventory_snapshots | InventorySnapshot | inventory_snapshot_id |
| users | User | user_id |
| roles | Role | role_id |
| user_roles | UserRole | user_role_id |

Không có raw CRUD công khai cho outbox, token hashes, grants hoặc immutable prediction.
Các Row này phục vụ thiết kế/schema; command chuyên biệt giới hạn field client được gửi.
notifications/inventory_snapshots là tùy chọn theo PDF; không tạo persistence trùng Open-Core.
Chat dùng DTO projection; chưa có adapter persistence/chat hoặc bảng chat riêng.

## Vai trò và actor trong database

| Role | Account/scope | Quyền |
|---|---|---|
| EMPLOYEE | human/global | PR của mình và đọc kết quả liên quan |
| APPROVER | human/department | Đọc theo grant; quyết định cần workflow authoritative |
| WAREHOUSE | human/department | Ghi nhận receipt trong phòng ban |
| ADMIN | human/global | Quản lý profile/grant; không tự có APPROVER |
| AI_SERVICE | service/global | Task/report/candidate và lập proposal nội bộ |
| PO_SERVICE | service/global | Issue/dispatch và confirmation revisions nội bộ |

Bearer phải được IdP xác minh; backend map exact issuer + subject sang user active,
kiểm account type, grant active và scope. Header role/user ID không cấp quyền.
Supplier confirmation dùng token hash gắn với PO, kiểm hạn/revoke/consume trong DB.
Không có actor fixture được chọn theo operation. Admin/internal cần network policy riêng.

## TabICLv2 và cutoff

`feature_schema_version=tabicl_supplier_fixed_v1`; `feature_snapshot.x` đóng, bắt buộc đủ
19 key, không thêm missing flags, role/department hoặc target. A/B là PR_CREATED và
PO_MONITORING; PO_MONITORING cần request/item/revision/po_item đúng lineage, giữ Q gốc và
deadline. Requested lead là mirror audit của days_to_deadline theo ngày Asia/Ho_Chi_Minh.

Endpoint predict chưa có model/history adapter nên trả 503. Các prediction tổng hợp
trong seed và payload integration không phải bằng chứng model đã chạy hoặc đạt độ chính xác.

As-of DB, p50/p90 supplier-product, smoothing, context split/label leakage, calendar và
promise/receipt knowledge cutoff cần integration/model tests ở giai đoạn B.

## Kiểm thử và giới hạn

```sh
uv run --locked pytest tests/unit -q
uv run --locked pytest tests/integration -m integration -q
```

Unit offline kiểm cấu hình/lifecycle/auth adapter/HTTP validation, checksum và đủ 46 route.
Integration dùng PostgreSQL thật, tiêm riêng danh tính vì chưa có IdP. Bốn payload tổng
hợp chỉ phục vụ integration và nằm trong tests, không được runtime import hoặc trả qua HTTP.
Phần lớn fixture rollback; test commit có cleanup riêng. Chưa nghiệm thu SSO/workflow/email/
model/document engine; outbox pending chưa tạo tác động bên ngoài. Xem phạm vi và phần
cần bổ sung trong hướng dẫn API database. PDF supplement không phải migration tự chạy.
