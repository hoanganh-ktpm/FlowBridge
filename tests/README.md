# Tests theo khối

- domain/: tests cho Sales/Warehouse/Logistics, chia thư mục theo API khi có code.
- core/: Registry, sources/products, Governance, Hasura provisioner và Agent/Tools.
- platform/: token validation, Keycloak Directory, HTTP/OpenAPI utilities đã port và các adapter Platform sau này.
- e2e/: quy trình xuyên các khối với engine thật khi tích hợp.

Từ gốc: `uv run --locked pytest tests/platform tests/core/hasura -q`. Platform và Hasura unit tests chạy bằng HTTP mocks; Hasura dùng fake repositories, không đòi Core DB hoặc database nghiệp vụ. Domain/E2E tests thật chưa triển khai.

Hasura payload fixtures nằm tại `tests/core/hasura/fixtures/metadata-api.json`. Live smoke test chỉ kiểm health/metadata, không mutate, mặc định bị loại bởi integration marker; xem [Hasura Integration](../backend/app/core/hasura_provisioner/README.md) để opt-in khi có engine.

Integration/E2E cần marker và môi trường tường minh. Không lấy mock hoặc thư mục trống làm bằng chứng hệ thống đã chạy.
