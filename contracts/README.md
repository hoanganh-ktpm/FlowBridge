# Contracts

Bộ **review candidate v0.1.1**, ngày 23/10/2026, để Core/Business/Platform/Portal cùng triển khai theo một boundary. Chưa phải API đã triển khai hoặc đã được cả team phê duyệt. Đọc [quyết định, luồng trạng thái, quyền và tiêu chí nghiệm thu](../docs/API-CONTRACTS.md) trước khi code integration.

| File | Phạm vi |
|---|---|
| [common.openapi.yaml](common.openapi.yaml) | Component dùng chung: ID, lỗi, scope SELECT, pagination và headers |
| [core-api.openapi.yaml](core-api.openapi.yaml) | Registry, Data Product/Contract, Request/Grant, publication job/audit; Operation/Tool P1 |
| [domain-api.openapi.yaml](domain-api.openapi.yaml) | Sales, Warehouse, Logistics; transaction giữ hàng, packing, handover và compensation |
| [platform-api.openapi.yaml](platform-api.openapi.yaml) | Registered operation executor và workflow adapter trung lập engine, P1 |
| [identity-context.schema.json](identity-context.schema.json) | VerifiedIdentity hiện có, giữ nguyên shape tương thích adapter |
| [resolved-identity-context.schema.json](resolved-identity-context.schema.json) | Principal/membership từ resolver tin cậy, không phải quyền do client khai |
| [fixtures/review-cases.json](fixtures/review-cases.json) | 63 fixture tổng hợp, gồm 26 payload sai có chủ đích; [provenance](fixtures/PROVENANCE.md) |

Mở một trong ba file `*-api.openapi.yaml` bằng OpenAPI viewer hỗ trợ 3.1 và local external `$ref`. `common.openapi.yaml` chỉ là thư viện component. JSON Schema identity được tham chiếu trực tiếp; JSON example không thay thế schema.

Mỗi endpoint có `x-priority` P0/P1 và `x-authorization`; các list có `x-pagination`. Không sao chép schema giữa service. Các kiểu lỗi và `X-Correlation-Id` bám boundary Platform hiện có; khai báo OpenAPI không tự triển khai validation/policy.

Kiểm tra từ repo root:

```sh
uv run --locked pytest tests/contracts -q
uv run --locked ruff check tests/contracts
```

Suite kiểm local refs, JSON Schema, boundary HTTP và fixtures, **không phải runtime/E2E**. Hai PDF tham chiếu vẫn là tài liệu thiết kế; trạng thái domain và lựa chọn HTTP được ghi rõ là đề xuất cần owner duyệt. Delegation workflow và tích hợp engine thật cần hoàn thiện trước nghiệm thu.

`portal/schemas/` giữ presentation/event schema cho UI cũ, chưa đại diện Core API mới. Không tự suy Portal v1 tương thích với contract mới; không import hoặc đồng bộ tự động contract từ archive.

Bản rà soát: [kết quả và giới hạn](../docs/API-CONTRACT-REVIEW.md). Bản đọc dành cho team: [PDF v0.1.1](../output/pdf/DXOS_FlowBridge_API_Contracts_v0.1.1.pdf), artifact sinh riêng không đưa vào source release.
