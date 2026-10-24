# Engine Infrastructure

Cấu hình triển khai nằm duy nhất tại đây; Python adapter nằm trong Core/Platform, BPMN nằm ở workflows.

| Thư mục | Sở hữu |
|---|---|
| keycloak | Realm template và manifest công cụ upstream đã giữ lại |
| apisix | Cấu hình TLS/error/correlation đã giữ lại, image pin và route allowlist |
| postgres | PostgreSQL 16 local Compose, TLS/start và bootstrap roles/DB; migrations thuộc chủ sở hữu |
| hasura | Image/deployment và metadata baseline; Core provisioner quản lý runtime changes |
| flowable | Image/deployment và flowable_db |

Realm template đã bỏ client web-b/AI/PO của ứng dụng cũ, giữ Portal/API/Directory. Placeholder secret phải được render từ secret runtime trước khi import; không dùng template thô làm realm đang chạy. Chưa cấp quyền dữ liệu hoặc tạo workflow service account mới.

APISIX routes hiện rỗng có chủ đích: các endpoint v1 không còn được coi là API mới. Cấu hình TLS dùng /gateway-config/ca.pem và cần cấu hình cert/route từ deployment. Không chạy lại compose của v1 theo đường dẫn mới.

Profile local mới ở `postgres/docker-compose.local.yml`: PostgreSQL 16, năm DB riêng, Hasura metadata riêng và volume bền vững. Xem [Database Setup](../docs/database/README.md) cho các lệnh.

Profile test cũ ở docker-compose.yml chỉ có Keycloak HTTPS và Hasura v2 CE. Metadata Hasura dùng schema hdb_catalog trong database postgres Supabase hiện có; Core/worker chạy Windows .venv, các database ứng dụng giữ nguyên trên Supabase. Không có PostgreSQL container hoặc database mới. Secrets/realm thử được render vào file bị ignore; không sửa realm/volume ngoài project, không cần JDK trên host. Xem [Docker integration và Real E2E](../docs/DOCKER.md) cho quyền reader, fixture, cách chạy và cleanup; đây không phải production stack.
