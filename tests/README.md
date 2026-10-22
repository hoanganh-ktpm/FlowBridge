# Tests theo khối

- domain/: tests cho Sales/Warehouse/Logistics, chia thư mục theo API khi có code.
- core/: Registry, sources/products, Governance, Hasura provisioner và Agent/Tools.
- platform/: token validation, Keycloak Directory, HTTP/OpenAPI utilities đã port và các adapter Platform sau này.
- e2e/: quy trình xuyên các khối với engine thật khi tích hợp.

Từ gốc: uv run --locked pytest tests/platform -q. Unit Platform chạy bằng mocks và không import Core hoặc schema mua hàng. Tests Core/Domain/E2E mới chưa triển khai.

Integration/E2E cần marker và môi trường tường minh. Không lấy mock hoặc thư mục trống làm bằng chứng hệ thống đã chạy.
