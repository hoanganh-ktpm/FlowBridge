# Tests theo khối

- domain/: tests cho Sales/Warehouse/Logistics, chia thư mục theo API khi có code.
- core/: Registry, sources/products, Governance, Hasura provisioner và Workflow persistence.
- platform/: token validation, Keycloak Directory, HTTP/OpenAPI utilities đã port và các adapter Platform sau này.
- e2e/: quy trình xuyên các khối với engine thật khi tích hợp.
- contracts/: kiểm tra OpenAPI/JSON Schema, refs, fixtures tổng hợp và consistency của API Contracts; không phụ thuộc database hoặc service thật. Chạy `uv run --locked pytest tests/contracts -q`; xem [API Contracts](../docs/API-CONTRACTS.md).

Từ gốc: `uv run --locked pytest tests/platform tests/core/hasura -q`. Platform và Hasura unit tests chạy bằng HTTP mocks; Hasura dùng fake repositories, không đòi Core DB hoặc database nghiệp vụ. Nhóm `tests/e2e/database/` đã chạy 37 tests đạt trên PostgreSQL 17.11 local riêng sau relocation; không thay bằng chứng Core–Hasura–SSO E2E.

Hasura payload fixtures nằm tại `tests/core/hasura/fixtures/metadata-api.json`. Live smoke test chỉ kiểm health/metadata, không mutate, mặc định bị loại bởi integration marker; xem [Hasura Integration](../backend/app/core/hasura_provisioner/README.md) để opt-in khi có engine.

Integration/E2E cần marker và môi trường tường minh. Không lấy mock hoặc thư mục trống làm bằng chứng hệ thống đã chạy.

`tests/core/db/` kiểm configuration, DDL fingerprints, migration histories, Core P0/reset guards và fixtures offline. `tests/domain/test_database_models.py` kiểm import độc lập và số lượng business fixtures. `tests/e2e/database/` kiểm xuyên các database bằng server thử được chỉ định; cần cả nhóm dependency `database` và `DXOS_TEST_ADMIN_URL`. Mặc định không chạy hoặc dùng `.env` của database nhóm; xem [database testing](../docs/database/README.md).

`tests/core/runtime/` kiểm vertical slice qua HTTP, verifier/policy/services/worker và Hasura clients với injected doubles; response shape/status/headers được đối chiếu baseline OpenAPI. Chạy `uv run --locked pytest tests/core/runtime -q`. Không có database hoặc upstream thật trong nhóm test này.

`tests/core/persistence/` chạy Core HTTP với PostgreSQL thật và identity boundary override có chủ đích; không phải SSO/Hasura E2E. Windows có thể chạy `./scripts/test_postgres.ps1` để dựng/dừng cluster TLS local riêng, không Docker và không dùng database đang chạy. Test E2E opt-in ở `tests/e2e/test_automated_onboarding.py` chỉ dùng HTTP/token/Hasura thật, mặc định SKIP khi chưa cấu hình. Hướng dẫn và giới hạn tại [Automated Onboarding](../docs/AUTOMATED-ONBOARDING.md).
