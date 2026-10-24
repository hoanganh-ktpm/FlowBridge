# Khối công việc và ranh giới

## Thành phần

| Khối | Trách nhiệm | Kiểm thử độc lập |
|---|---|---|
| services | Nghiệp vụ và transaction của từng ứng dụng | Database fixture, identity/API mock |
| core | Registry, chia sẻ dữ liệu và governance | Metadata DB fixture, mock IdP/Hasura và sources thử nghiệm |
| platform | Identity, HTTP và adapter hạ tầng | IdP/HTTP mock, Flowable và mock Business APIs |
| portal | Giao diện và truy cập API | Fixtures và contracts frontend |

Core quản lý tích hợp dữ liệu, Platform cung cấp năng lực kỹ thuật dùng chung, Services sở hữu nghiệp vụ. Backend dùng package app, chia app.core và app.platform theo trách nhiệm. Mỗi Business API có package app và môi trường chạy độc lập. Module Core được tổ chức trong cùng backend để đơn giản hóa composition và quản lý dependency.

## Quy tắc giao tiếp

- Platform không import Core/domain. Core dùng interface công khai của Platform; giữa module Core không tùy tiện đọc repository của nhau.
- Services giao tiếp bằng API/contract, không import source hoặc truy vấn DB của nhau.
- Portal gọi API qua boundary; fixtures chỉ hỗ trợ phát triển độc lập.
- Contracts giữ schema/interface chung. Infra giữ engine config. Workflows giữ quy trình nghiệp vụ.
- Test tập trung theo khối, không rải thêm tests/README ở mỗi module con.

## Database và thực thi

Demo local dùng PostgreSQL 16 với năm database độc lập (ba domain, Core, Hasura metadata), mỗi DB có owner riêng. Migration `_0002` chỉ thêm inbound planning, capacity calendar và sáu view. Generator điều phối timeline bằng ba kết nối quản trị độc lập, không phải coupling runtime giữa các service. Không có shared business DB, cross-DB FK hoặc FDW. Xem [Database Setup](database/README.md).

Core sở hữu dxos_core_db và một migration history trong backend/app/core/db/migrations. Mỗi domain sở hữu migration riêng trong services/<domain>-api/migrations. Engine tự quản lý Flowable/Keycloak/Hasura metadata DB. Core chỉ lưu metadata, không gom dữ liệu nghiệp vụ về một kho trung tâm.

Hasura đọc dữ liệu được công bố; Business APIs thực hiện ghi có transaction và quyền. Provisioner xác nhận metadata/quyền đã áp dụng trước khi ACTIVE, có retry/reconcile. Workflow adapter quản lý giao tiếp engine, BPMN Sales → Warehouse → Logistics đặt trong workflows khi triển khai.

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

Core P0 có FastAPI composition, HTTP handlers cho vertical slice Data Sharing, canonical onboarding, trusted principal/grant policy và worker claim/lease/retry/revision verification. PostgreSQL CoreStore và native HTTP/worker composition đã được kiểm trên cluster thử riêng; identity boundary trong integration tests được override có chủ đích, không thay bằng chứng SSO/Hasura E2E. Chi tiết tại [Automated Onboarding](AUTOMATED-ONBOARDING.md).

Database models/migrations của Core và ba domain được đặt tại module sở hữu; CLI quản trị/seed ở scripts/database không được import từ runtime nghiệp vụ. Head core_0006 bổ sung registry quan hệ và Workflow definition/dispatch metadata vào Core một doanh nghiệp; đã được áp dụng trên Supabase theo phê duyệt nghiệm thu riêng. Các khối principals, phòng ban/memberships, applications/sources, products/contracts, department grants, jobs/audit/receipts giữ nguyên mô hình. Adapter nằm trong app/core/db, không đọc schema nội bộ nghiệp vụ. Ba history nghiệp vụ giữ nguyên. Xem [Database Setup](database/README.md).

Nested reads dùng Relationship Registry do IT quản trị và Hasura local/remote relationships; Core không tự JOIN dữ liệu trong Python. Mỗi cây truy vấn phải có grants bao phủ toàn bộ Products/cột/key dưới cùng một department role; kiểm trạng thái trước và sau khi đọc, không dùng admin secret cho dữ liệu. Chi tiết phạm vi, giới hạn và trạng thái E2E tại [Relationship Queries](RELATIONSHIP-QUERIES.md).

Profile Data Sharing Docker hóa Keycloak/Hasura v2 CE; profile [Workflow](WORKFLOW.md) bổ sung riêng Flowable 7.2.0 để nghiệm thu engine. Core/worker chạy native, PostgreSQL local phục vụ workflow nghiệp vụ; profile Supabase cũ được ghi lại tại [Docker Integration](DOCKER.md). Hasura và Flowable sở hữu metadata/runtime riêng, không dùng bảng Core làm execution store. Đã kiểm onboarding → publication → grant → đọc catalog → revoke bằng token Keycloak và Hasura thật. Workflow router/ports tái sử dụng chính sách Core, không tạo quyền ghi qua Hasura. Vertical slice Business APIs và Order Fulfillment đã được kiểm trong profile local; production stack còn cần hoàn thiện. Backend AI cũ chỉ còn trong lưu trữ lịch sử, không thuộc chức năng hiện hành.

Mã active không import source lưu trữ cục bộ. Template cấu hình được quản lý trong Git; credentials và runtime state được cung cấp riêng khi triển khai.
