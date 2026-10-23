# Khối công việc và ranh giới

## Phân chia

| Khối | Người phụ trách dự kiến | Có thể code độc lập bằng |
|---|---|---|
| services | Người 1 | Database fixture, identity/API mock |
| core | Người 2 | Metadata DB fixture, mock IdP/Hasura và sources thử nghiệm |
| platform | Người 3 | IdP/HTTP mock, Flowable và mock Business APIs |
| portal | Giao theo nhân lực/mốc | Fixtures và contracts frontend |

Core quản lý tích hợp dữ liệu, Platform cung cấp năng lực kỹ thuật dùng chung, Services sở hữu nghiệp vụ. Backend dùng package app, chia app.core và app.platform theo trách nhiệm. Mỗi Business API có package app và môi trường chạy độc lập. Module Core được tổ chức trong cùng backend để đơn giản hóa composition và quản lý dependency.

## Quy tắc giao tiếp

- Platform không import Core/domain. Core dùng interface công khai của Platform; giữa module Core không tùy tiện đọc repository của nhau.
- Services giao tiếp bằng API/contract, không import source hoặc truy vấn DB của nhau.
- Portal gọi API qua boundary; fixtures chỉ hỗ trợ phát triển độc lập.
- Contracts giữ schema/interface chung. Infra giữ engine config. Workflows giữ quy trình nghiệp vụ.
- Test tập trung theo khối, không rải thêm tests/README ở mỗi module con.

## Database và thực thi

Core sở hữu dxos_core_db và một migration history trong backend/app/core/db/migrations. Mỗi domain sở hữu migration riêng trong services/<domain>-api/migrations. Engine tự quản lý Flowable/Keycloak/Hasura metadata DB. Core chỉ lưu metadata, không gom dữ liệu nghiệp vụ về một kho trung tâm.

Hasura đọc dữ liệu được công bố; Business APIs thực hiện ghi có transaction và quyền. Provisioner xác nhận metadata/quyền đã áp dụng trước khi ACTIVE, có retry/reconcile. Agent chỉ gọi operation đã đăng ký bằng quyền actor. Workflow adapter quản lý giao tiếp engine, BPMN Sales → Warehouse → Logistics đặt trong workflows khi triển khai.

SSO xác minh danh tính, không tự cấp quyền nghiệp vụ. Core/Hasura/Business API/Workflow Adapter kiểm quyền tại receiver. Reservation phải nguyên tử, idempotent và có giải phóng/bù khi bước sau lỗi; không dựa riêng vào lần đọc tồn kho trước đó.

## Ba lớp phân quyền

| Lớp | Quyền | Nơi thực thi |
|---|---|---|
| Quản trị DX-OS | IT có PLATFORM_ADMIN quản trị toàn bộ registry, nguồn, contracts, validate/plan, publish, cấp/thu quyền, cấu hình và audit; không cần OWNER của phòng ban duyệt riêng | Core API/backend sau xác thực và resolve principal role đáng tin cậy |
| Đọc dữ liệu dùng chung | Consumer đọc đúng Data Product/cột theo grant IT cấp; kiểm hiệu lực tại mỗi request | Core kiểm grant, Hasura thực thi read permissions |
| Nghiệp vụ từng phần mềm | Tạo đơn, giữ hàng, cập nhật tồn kho/vận chuyển theo quyền riêng của ứng dụng | Business API Sales/Warehouse/Logistics |

Phòng ban là thông tin tổ chức/nhóm consumer, không phải nguồn quyền quản trị DX-OS. STAFF/OWNER trong thiết kế DB cũ không cho phép cấu hình, publish, duyệt hoặc cấp quyền. owner_department_id là metadata liên kết, không phải role. Consumer là nhóm sử dụng, không thêm enum role vào DB; principal hiện dùng NONE hoặc PLATFORM_ADMIN.

IT toàn quyền quản trị không bỏ qua schema/state/revision/verification và không biến token quản trị thành quyền ghi nghiệp vụ hay quyền đọc hàng dữ liệu bằng Hasura admin secret. Khi dùng endpoint dữ liệu/execute thông thường, IT vẫn theo grant/execute/receiver policy tương ứng; IT có thể cấp quyền đó qua thao tác quản trị có audit. Service account không tự có quyền chỉ vì là service. Chi tiết normative tại [API Contracts](API-CONTRACTS.md).

## Mã đã chuyển và phần còn thiếu

Đã port: IdentitySettings độc lập, token introspection, Keycloak Directory, generic HTTP utilities và test tương ứng; UI/theme/PKCE; template Keycloak/config APISIX.

Hasura provisioner đã có Metadata Client và services đăng ký nguồn, publish table/view, reconcile SELECT permissions; fake repositories và HTTP mock cho phép phát triển không cần database/engine. Source tại app/core/hasura_provisioner, cấu hình baseline duy nhất ở infra/hasura. Core DB models/migrations và authorization adapters được ghép qua Protocols, không bị module Hasura định nghĩa lại.

Chưa triển khai: API/Core DB baseline, domain APIs, Hasura live integration/Core adapters/publication worker, Agent Runtime, Flowable adapter/BPMN và Compose mới. Không giữ code v1 còn phụ thuộc SupplierPolicy/Settings mua hàng trong cây active. Langflow/ML/RAG/Agent Hub backend cũ vẫn trong archive để bảo toàn source; chỉ port tiếp phần thực sự cần.

Mã active không import source lưu trữ cục bộ. Template cấu hình được quản lý trong Git; credentials và runtime state được cung cấp riêng khi triển khai.
