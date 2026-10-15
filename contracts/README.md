# Hợp đồng API FlowBridge

Runtime dùng `app-api.yaml` (40 operation) và `ai-tools-api.yaml` (6 operation)
trong `backend/src/flowbridge/contracts/`, version 0.4.1. Hai snapshot có chung DTO;
router/validator/Swagger và gateway mapping đọc cùng nguồn trong package.
[Mapping database](supplier-assistant-v0.4.1.md) giải thích UUID, tiền, role và lineage.

Nguồn chuẩn trong workspace là `03-api/contracts/{app-api,ai-tools-api}.yaml`.
Sửa nguồn trước, rồi kiểm tra hoặc đồng bộ từ root FlowBridge:

```sh
uv run --locked python scripts/sync_application_contracts.py --source ../03-api/contracts
uv run --locked python scripts/sync_application_contracts.py --source ../03-api/contracts --write
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

`contracts/opencore-api.yaml` và `supplier-assistant-layer2-api.yaml` là tài liệu thiết kế,
không được mount thành API sample hoặc đóng gói làm registry runtime. Không suy ra engine
Identity/Gateway/Storage/Workflow đã tích hợp từ các file này. License code/config theo
notice từng file; dữ liệu và nguồn bên ngoài có điều kiện sử dụng riêng.
