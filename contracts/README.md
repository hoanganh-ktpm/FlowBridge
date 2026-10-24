# Contracts

Hợp đồng chung là điểm nối các khối. Bộ API contract 0.2.0 dùng OpenAPI 3.1.1, theo phương pháp contract-first; trạng thái REVIEW CANDIDATE, database alignment và runtime integration chưa hoàn tất.

- identity-context.schema.json: shape VerifiedIdentity sau xác thực, tương ứng adapter Platform hiện có. Đây là dữ liệu nội bộ tin cậy, không phải JSON client tự khai để nhận quyền.
- portal/schemas/: JSON presentation/event schema được giữ cho UI; không đại diện API registry/domain mới.
- fixtures/review-cases.json: request/response tổng hợp, ca hợp lệ và sai có chủ đích; không phải seed hoặc dữ liệu DB thật.
- onboarding/: Application Manifest v1 JSON Schema, Warehouse/cross-source examples và offline schema fixture. Đây là declarative input, không phải HTTP/OpenAPI contract; xem [Onboarding](../docs/ONBOARDING.md).

## OpenAPI entrypoints

| File | Phạm vi |
|---|---|
| [core-api.openapi.yaml](core-api.openapi.yaml) | P0 Registry, onboarding, Data Sharing; P1 Operation/Tool registry |
| [sales-api.openapi.yaml](sales-api.openapi.yaml) | Sales P1, PROPOSED / PENDING_DB_ALIGNMENT |
| [warehouse-api.openapi.yaml](warehouse-api.openapi.yaml) | Warehouse P1, PROPOSED / PENDING_DB_ALIGNMENT |
| [logistics-api.openapi.yaml](logistics-api.openapi.yaml) | Logistics P1, PROPOSED / PENDING_DB_ALIGNMENT |
| [platform-api.openapi.yaml](platform-api.openapi.yaml) | Executor/workflow P1, public boundary độc lập engine |
| [common.openapi.yaml](common.openapi.yaml) | Thư viện HTTP components; không phải service |

Core entrypoint tham chiếu các bounded-context fragments: [application-onboarding](application-onboarding.yaml), [data-contracts](data-contracts.yaml), [data-governance](data-governance.yaml), [operation-registry](operation-registry.yaml). Không mở fragment như một server riêng. Mỗi schema có một nơi sở hữu; tests bundle entrypoints trong bộ nhớ và validate hoàn toàn offline.

93 operations: 55 P0 và 38 P1. Hai endpoint Agent/Tool binding lịch sử đã loại bỏ; các method/path/operationId còn lại giữ nguyên nhưng DTO/semantics có thay đổi có chủ đích. Không tuyên bố tương thích payload hay runtime. Canonical Data Contract giữ physical/public names, PostgreSQL/wire types và relationships. P0 read grants theo department; principal grants chưa được cam kết enforcement.

Ba lớp quyền độc lập: IT/PLATFORM_ADMIN quản trị toàn bộ DX-OS; Core + Hasura kiểm quyền đọc theo grant; Business API kiểm quyền nghiệp vụ riêng. STAFF/legacy OWNER không cấp quyền quản trị, publish hay duyệt trong Core. Mọi operation có x-authorization-policy để phân biệt admin, consumer và receiver; access requests là hồ sơ IT quản lý thay consumer, không phải luồng phòng ban tự xin/duyệt.

Quy ước, lifecycle, ownership, database gaps và thay đổi tương thích tại [API Contracts](../docs/API-CONTRACTS.md). Các giả định Business còn chờ owner xác nhận, không phải ORM đã tồn tại.

```sh
uv run --locked pytest tests/contracts -q
uv run --locked ruff check tests/contracts
```

Các kiểm tra này chỉ xác minh specification, refs và fixture shape; không gọi PostgreSQL, Hasura, Keycloak hoặc Workflow Engine.

Không import hoặc đồng bộ tự động contract v1 trong archive. Portal hiện vẫn có boundary API/fixtures v1 ở portal/src/lib và portal/src/fixtures; cần chuyển sang contract mới khi API được triển khai.
