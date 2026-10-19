# Hợp đồng API FlowBridge

## Agent Hub cho Web/App và Langflow ver2

[Agent Hub OpenAPI 1.0.1](agent-hub-api.yaml) và [hướng dẫn tích hợp](agent-hub/README.md)
mô tả 12 operation: thư viện ba agent, hội thoại/lựa chọn agent, chat bất đồng bộ,
kết quả JSON, metadata, Activities SSE và hủy run. Langflow tự điều phối và gọi
phụ thuộc; API truyền/lưu yêu cầu và kết quả theo quyền người dùng.

[Schema kết quả 1.0](agent-hub/schemas/run-result.schema.json) giữ khung mẫu người
dùng cung cấp, dùng chung dataset cho bảng và biểu đồ. Có quy tắc cột chồng/100%,
tiền decimal, mẫu số tỷ lệ, provenance, lỗi, idempotency, revision và reconnect.
Ví dụ nằm trong [agent-hub/examples](agent-hub/examples/PROVENANCE.md).

Đã có **backend bật tùy chọn sau migration0006**, xem [cách chạy](../docs/AGENT-HUB.md). Không thay snapshot
App/AI/Core hoặc manifest checksum hiện tại. Nguồn chuẩn Agent Hub nằm tại đây;
không đưa vào script đồng bộ upstream của App/AI/Core. Khi phân phối giữ nguyên
`agent-hub-api.yaml` và cây `agent-hub/` vì OpenAPI dùng external schema/reference.

## Hợp đồng nghiệp vụ hiện có

Runtime dùng `app-api.yaml` (40 operation) và `ai-tools-api.yaml` (6 operation)
trong `backend/flowbridge/contracts/`, version 0.4.1. Hai snapshot có chung DTO;
router/validator/Swagger và gateway mapping đọc cùng nguồn trong package.
[Mapping database](supplier-assistant-v0.4.1.md) giải thích UUID, tiền, role và lineage.

Nguồn chuẩn trong workspace là `ho-so-olp/03-api/contracts/{app-api,ai-tools-api}.yaml`.
Sửa nguồn trước, rồi kiểm tra hoặc đồng bộ từ root FlowBridge:

```sh
uv run --locked python scripts/sync_application_contracts.py --source ../ho-so-olp/03-api/contracts
uv run --locked python scripts/sync_application_contracts.py --source ../ho-so-olp/03-api/contracts --write
```

Script nhận `--source` tường minh. Source archive độc lập đã có snapshot và manifest
`application-contracts.sha256`, không cần workspace cha để chạy/test. Cung cấp directory
nguồn contract đã quản lý version khi muốn đồng bộ từ archive độc lập.
`tests/unit/test_database_api_contract.py` kiểm checksum, route coverage, security và
source drift khi workspace có nguồn tương ứng. Chạy `uv run --locked pytest tests/unit -q`.

Swagger database giữ yêu cầu bearer/supplier confirmation token của contract. Các chức
năng cần engine chưa tích hợp trả 503 theo [phạm vi hiện tại](../docs/SUPABASE-API.md).
Không có schema sample bỏ security. Các ví dụ request trong contract chỉ minh họa shape,
không bảo đảm record/ID/token đó tồn tại trong database đang chạy.

`contracts/opencore-api.yaml` mô tả 28 operation thiết kế; hai operation Identity được
triển khai khi bật binding theo [Identity core](../docs/IDENTITY-CORE.md). DTO Python và
HTTP response được kiểm với schema core; không phục dựng sample hoặc mount 26 operation
còn lại. `supplier-assistant-layer2-api.yaml` vẫn là thiết kế. Không suy ra engine
Gateway/Storage/Workflow đã tích hợp từ các file này. License code/config theo
notice từng file; dữ liệu và nguồn bên ngoài có điều kiện sử dụng riêng.

## Core canonical và provenance

Nguồn chuẩn Core là `ho-so-olp/03-api/contracts/opencore-api.yaml` trong workspace;
`contracts/opencore-api.yaml` là bản sao phân phối trong source archive. Core dùng
dialect mặc định OpenAPI 3.1, cùng cách công bố của app/AI. Không đổi wire/version 0.2.0.

```sh
uv run --locked python scripts/sync_core_contracts.py --source ../ho-so-olp/03-api/contracts
uv run --locked python scripts/sync_core_contracts.py --source ../ho-so-olp/03-api/contracts --write
# Archive độc lập: kiểm bản sao, manifest và draft mà không cần workspace cha.
uv run --locked python scripts/sync_core_contracts.py --source contracts
```

`core-contracts.sha256` khóa đúng byte upstream; `x-upstream.sha256` trong draft Tầng 2
phải trỏ cùng bản đó. Script kiểm version và sáu shared schema trước khi ghi. `--write`
chỉ cho phép sửa khác biệt metadata dialect/byte/provenance đã review; nếu contract đổi
ngữ nghĩa phải review và nâng bản sao/schema draft phù hợp trước khi sync. Không đổi
hash để bỏ qua schema drift. Script mặc định chỉ kiểm và không sửa file.

## Nguồn OpenAPI runtime

`shared/openapi.py` sở hữu composition và cache. App/AI dùng snapshot 0.4.1; Identity
và ML đóng góp schema sinh từ router/DTO với namespace `Core*` và `ML*`; RAG development
có fragment riêng theo profile. Trùng path+method, operationId hoặc component khác nghĩa
làm việc xuất OpenAPI thất bại. Component giống hệt nhau có thể dùng chung.

Hai API Identity có kiểm parity URI, correlation, security, response/error schema
và header 409 với Core YAML.
