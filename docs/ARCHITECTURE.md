# Khối công việc và ranh giới

## Phân chia

| Khối | Người phụ trách dự kiến | Có thể code độc lập bằng |
|---|---|---|
| services | Người 1 | Database fixture, identity/API mock |
| core | Người 2 | Metadata DB fixture, mock IdP/Hasura và sources thử nghiệm |
| platform | Người 3 | IdP/HTTP mock, workflow adapter và mock Business APIs |
| portal | Giao theo nhân lực/mốc | Fixtures và contracts frontend |

Core quản lý tích hợp dữ liệu, Platform cung cấp năng lực kỹ thuật dùng chung, Services sở hữu nghiệp vụ. Backend dùng package app, chia app.core và app.platform theo trách nhiệm. Mỗi Business API có package app và môi trường chạy độc lập. Module Core được tổ chức trong cùng backend để đơn giản hóa composition và quản lý dependency.

## Quy tắc giao tiếp

- Platform không import Core/domain. Core dùng interface công khai của Platform; giữa module Core không tùy tiện đọc repository của nhau.
- Services giao tiếp bằng API/contract, không import source hoặc truy vấn DB của nhau.
- Portal gọi API qua boundary; fixtures chỉ hỗ trợ phát triển độc lập.
- Contracts giữ schema/interface chung. Infra giữ engine config. Workflows giữ quy trình nghiệp vụ.
- Test tập trung theo khối, không rải thêm tests/README ở mỗi module con.

## Database và thực thi

Core sở hữu dxos_core_db và một migration history trong backend/app/core/db/migrations. Mỗi domain sở hữu migration riêng trong services/<domain>-api/migrations. Engine tự quản lý database runtime; Keycloak/Hasura tự quản lý metadata DB. Core chỉ lưu metadata, không gom dữ liệu nghiệp vụ về một kho trung tâm.

Hasura đọc dữ liệu được công bố; Business APIs thực hiện ghi có transaction và quyền. Provisioner xác nhận metadata/quyền đã áp dụng trước khi ACTIVE, có retry/reconcile. Agent chỉ gọi operation đã đăng ký bằng quyền actor. Workflow adapter quản lý giao tiếp engine, BPMN Sales → Warehouse → Logistics đặt trong workflows khi triển khai.

SSO xác minh danh tính, không tự cấp quyền nghiệp vụ. Core/Hasura/Business API/Workflow Adapter kiểm quyền tại receiver. Reservation phải nguyên tử, idempotent và có giải phóng/bù khi bước sau lỗi; không dựa riêng vào lần đọc tồn kho trước đó.

## Mã đã chuyển và phần còn thiếu

Đã port: IdentitySettings độc lập, token introspection, Keycloak Directory, generic HTTP utilities và test tương ứng; UI/theme/PKCE; template Keycloak/config APISIX.

Chưa triển khai: API/Core DB baseline, domain APIs, Hasura provisioner, Agent Runtime, workflow adapter/definition (engine chưa chọn) và Compose mới. Không giữ code v1 còn phụ thuộc SupplierPolicy/Settings mua hàng trong cây active. Langflow/ML/RAG/Agent Hub backend cũ vẫn trong archive để bảo toàn source; chỉ port tiếp phần thực sự cần.

Mã active không import source lưu trữ cục bộ. Template cấu hình được quản lý trong Git; credentials và runtime state được cung cấp riêng khi triển khai.
