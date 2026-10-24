# Tích hợp database với Hasura

Module database tạo cấu trúc và dữ liệu mẫu. Chưa đăng ký source, track bảng, tạo relationship hoặc áp permission trong Hasura.

## Nguồn dữ liệu và quyền

Layout chính là **ba database nghiệp vụ độc lập** `sales_db`, `warehouse_db`, `logistics_db`; Core ở `dxos_core_db`. Mỗi database dùng schema `public`. Hasura cần một PostgreSQL source riêng cho mỗi database nghiệp vụ được duyệt, chẳng hạn `sales`, `warehouse`, `logistics`. Cùng máy chủ không biến chúng thành một source và không cho phép FK PostgreSQL xuyên database.

- Dùng role kết nối riêng, ít quyền. Tài khoản chạy migration không phải tài khoản consumer.
- Core là metadata nội bộ. Không tự track toàn bộ Core hoặc mở `command_receipts`, `outbox_events`, `inbox_receipts`, audit, traces và secret refs cho consumer.
- Core P0 đọc theo principal/membership phòng ban và grant hiện hành; Hasura role/session context phải do backend tin cậy tạo, có giới hạn cột và số hàng. Core một doanh nghiệp không suy quyền từ org claim hoặc tên ứng dụng do caller tự gửi.
- `scope_json.columns`/`row_scope` của access_grants là cấu hình mong muốn, không tự thành permission Hasura. P0 chỉ SELECT/ALL trên Product đã duyệt; nếu nguồn có dữ liệu ngoài phạm vi doanh nghiệp, phải giới hạn bằng nguồn/view được kiểm chứng trước khi công bố. Chỉ ACTIVE sau khi worker đã áp metadata và kiểm thử allow/deny.
- Contract catalog Warehouse không cung cấp `products.unit_cost`. Không mở giá vốn chỉ vì được đọc tồn kho.
- Recipient/phone/address chỉ chia sẻ cho Logistics theo quyền cần in nhãn; không đưa toàn bộ PII cho consumer dùng chung.

Các bảng trùng tên giữa sources, như `command_receipts`, cần tùy chỉnh GraphQL root/type hoặc cấu hình namespace phù hợp. Định danh dataset phải gồm source + schema + table; không chỉ tên bảng.

Không cần Supabase Table Editor hay Data API để cấu hình PostgreSQL sources trong Hasura. Phân quyền tài khoản SQL, Hasura và người dùng ứng dụng phải cấu hình riêng.

## Quan hệ cần cấu hình

FK nội bộ chứa `org_id` và ID cha; giữ đủ cả hai cột trong object/array relationship. Tham chiếu giữa các database là logical references; tạo **remote relationships** sau khi Data Contract và mapping được duyệt.

| Source đọc / bảng | Tham chiếu | Source đích / bảng |
|---|---|---|
| `sales` / `public.sales_order_lines` | `org_id, product_ref` | `warehouse` / `public.products(org_id, id)` |
| `warehouse` / `public.reservations` | `org_id, source_order_ref` | `sales` / `public.sales_orders(org_id, id)` |
| `warehouse` / `public.reservation_lines` | `org_id, source_order_line_ref` | `sales` / `public.sales_order_lines(org_id, id)` |
| `logistics` / `public.shipments` | `org_id, warehouse_dispatch_ref` | `warehouse` / `public.dispatches(org_id, id)` |
| `logistics` / `public.shipments` | `org_id, sales_order_ref` | `sales` / `public.sales_orders(org_id, id)` |

Không suy ra relationship chỉ từ hai cột trùng tên hoặc tự nối theo SKU. Consumer cần grants đang ACTIVE phù hợp ở cả hai đầu. Availability là `on_hand_quantity - reserved_quantity`; module chưa tạo view availability, chỉ thêm qua migration khi chốt contract.

Core `data_sources` hỗ trợ nhiều ứng dụng/nhiều nguồn trong một doanh nghiệp. CLI quản lý bốn database demo; Core có PostgreSQL CoreStore adapter, API/discovery/provisioner và schema P0 core_0004. Profile [Docker Integration](../DOCKER.md) mô tả phạm vi đã kiểm với Hasura thật; chưa triển khai đầy đủ các quan hệ và Business APIs bên dưới. Xem [schema hiện hành](CORE_SCHEMA.md).

## Business API chịu trách nhiệm phép ghi

- Giữ hàng: khóa balance/reservation, kiểm tra đủ mọi dòng rồi giữ nguyên tử trong Warehouse.
- Soạn nhiều đợt: tổng planned quantities không vượt phần giữ hàng còn lại.
- Bàn giao vật lý tại Warehouse: trừ tồn/giữ chỗ, ghi ledger và outbox trong cùng transaction.
- Logistics ACCEPTED chưa phải rời kho; HANDED_OVER cần bằng chứng Warehouse đã xác minh.
- Sales COMPLETED cần đủ từng dòng gốc và timestamp MAX của các đợt bàn giao.
- Hủy và bàn giao phải serialize; idempotency kiểm tra actor/hash, không chỉ status.

CHECK/FK không tự triển khai các thao tác trên. Mỗi database có transaction riêng; chưa có commit nguyên tử xuyên database. Inbox/outbox mới có cấu trúc lưu trữ, chưa có worker vận chuyển sự kiện. Không mở write mutations trực tiếp cho consumer hoặc bỏ qua service.

## Layout tùy chọn bốn schema

`DXOS_LAYOUT=schemas` vẫn hỗ trợ một PostgreSQL source và manual relationships giữa các schema sau khi được duyệt. Bản bốn schema trong database `postgres` được giữ riêng, không phải đích hiện tại và không tự đồng bộ với bốn database. Phải chọn một bố trí cho mỗi triển khai và track đúng đích; không trộn bảng của hai bản như cùng một nguồn dữ liệu.
