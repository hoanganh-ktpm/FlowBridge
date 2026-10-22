# Engine Infrastructure

Cấu hình triển khai nằm duy nhất tại đây; Python adapter nằm trong Core/Platform, BPMN nằm ở workflows.

| Thư mục | Sở hữu |
|---|---|
| keycloak | Realm template và manifest công cụ upstream đã giữ lại |
| apisix | Cấu hình TLS/error/correlation đã giữ lại, image pin và route allowlist |
| postgres | Bootstrap development cho bảy database logic và users riêng |
| hasura | Image/deployment và metadata baseline; Core provisioner quản lý runtime changes |
| flowable | Image/deployment và flowable_db |

Realm template đã bỏ client web-b/AI/PO của ứng dụng cũ, giữ Portal/API/Directory. Placeholder secret phải được render từ secret runtime trước khi import; không dùng template thô làm realm đang chạy. Chưa cấp quyền dữ liệu hoặc tạo workflow service account mới.

APISIX routes hiện rỗng có chủ đích: các endpoint v1 không còn được coi là API mới. Cấu hình TLS dùng /gateway-config/ca.pem và cần cấu hình cert/route từ deployment. Chưa có renderer/Compose mới, không chạy lại compose của v1 theo đường dẫn mới.

Dự kiến sales_db, warehouse_db, logistics_db, dxos_core_db, flowable_db, keycloak_db, hasura_metadata_db. PostgreSQL bootstrap chỉ tạo database/roles; schema do ứng dụng/engine sở hữu. Compose sẽ được viết sau khi service entrypoints và cấu hình được chốt; lượt sắp xếp này không tự dựng engine hoặc thay dữ liệu/volumes.
