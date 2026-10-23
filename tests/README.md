# Tests theo khối

- domain/: tests cho Sales/Warehouse/Logistics, chia thư mục theo API khi có code.
- core/: Registry, sources/products, Governance, Hasura provisioner và Agent/Tools.
- platform/: token validation, Keycloak Directory, HTTP/OpenAPI utilities đã port và các adapter Platform sau này.
- e2e/: quy trình xuyên các khối với engine thật khi tích hợp.
- contracts/: kiểm tra OpenAPI/JSON Schema, refs, fixtures tổng hợp và consistency của API Contracts; không phụ thuộc database hoặc service thật. Chạy `uv run --locked pytest tests/contracts -q`; xem [API Contracts](../docs/API-CONTRACTS.md).

Từ gốc: `uv run --locked pytest tests/platform tests/core/hasura -q`. Platform và Hasura unit tests chạy bằng HTTP mocks; Hasura dùng fake repositories, không đòi Core DB hoặc database nghiệp vụ. Domain/E2E tests thật chưa triển khai.

Hasura payload fixtures nằm tại `tests/core/hasura/fixtures/metadata-api.json`. Live smoke test chỉ kiểm health/metadata, không mutate, mặc định bị loại bởi integration marker; xem [Hasura Integration](../backend/app/core/hasura_provisioner/README.md) để opt-in khi có engine.

Integration/E2E cần marker và môi trường tường minh. Không lấy mock hoặc thư mục trống làm bằng chứng hệ thống đã chạy.

`tests/core/runtime/` kiểm vertical slice qua HTTP, verifier/policy/services/worker và Hasura clients với injected doubles; response shape/status/headers được đối chiếu baseline OpenAPI. Chạy `uv run --locked pytest tests/core/runtime -q`. Không có database hoặc upstream thật; SQL transaction/lease durability, engine permissions và fresh-data visibility sau commit vẫn DEFERRED tại [Core P0](../docs/CORE-P0.md).
