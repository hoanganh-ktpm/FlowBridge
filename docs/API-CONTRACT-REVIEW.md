# Rà soát API contracts v0.1.1

Ngày 23/10/2026. Phạm vi: specification, fixtures, kiểm tra contract và tài liệu. **REVIEW CANDIDATE** để owner/consumer thống nhất triển khai; không phải xác nhận runtime đã hoạt động. Không thay dependency, migration hoặc source runtime.

## Các điểm đã sửa

| Vấn đề | Contract sau rà soát |
|---|---|
| Luồng P0 thiếu endpoint đọc dữ liệu được cấp quyền | Thêm `readDataProductRows`: pin contract version, projection chứa business keys, giới hạn trang; một grant hiệu lực phải bao trùm toàn bộ projection. Backend dựng SELECT từ metadata tin cậy. |
| Department đã vô hiệu hóa vẫn có thể bị hiểu là có quyền | Resolved identity có `department_status`; policy yêu cầu principal, department và application liên quan còn hoạt động. Siết điều kiện human/service và auth config. |
| Người xin quyền lần đầu không thấy catalog | Đề xuất cho principal nội bộ hoạt động xem metadata product ACTIVE/contract published; dữ liệu vẫn cần grant. Metadata CONFIDENTIAL cũng thuộc lựa chọn này, cần owner duyệt. |
| Approval có thể chạy trên request/contract đã cũ | Approve kiểm lại version, trạng thái và owner/consumer trong transaction; serialize với disable và đổi active contract. |
| Application URL đổi làm sai operation đã đăng ký | Chặn đổi base URL hoặc chuyển ACTIVE về DRAFT khi còn BUSINESS_API active tham chiếu. Disable chặn call mới; activate phải kiểm lại. |
| Business API thiếu quy tắc mapping DTO và HTTP | Thêm `target_operation_id`, đối chiếu method/path với Domain operationId; variables chỉ gồm path/query/body; validate schema hai phía. Chốt chuyển status, timeout outcome chưa biết và namespace idempotency. |
| Workflow compensation có race với create shipment đang chạy | Coordinator ghi COMPENSATING bền vững, fence lệnh tiến mới, drain/reconcile lệnh đã nhận đến khi biết outcome rồi release/cancel/terminal. Timeout hoặc list rỗng không phải bằng chứng chưa commit. |
| Domain cho phép transition/evidence không nhất quán | Reserve kiểm Sales SUBMITTED; create shipment chỉ executor, Sales CONFIRMED và reservation đã packed; handover kiểm Sales. Response conditional schema ràng buộc evidence theo trạng thái. |
| Start workflow timeout nhưng caller chưa biết link ID | Thêm `findWorkflowLink` bằng tuple app/workflow/business/run. 404 không cho phép suy rằng start đang chạy sẽ không commit; retry dùng lại tuple/key. |
| Quy ước HTTP chưa đủ rõ | Bổ sung 400; phân biệt producer strict schema và consumer chịu field mới; replay giữ kết quả nghiệp vụ, correlation phản ánh request hiện tại. |

Có 84 operations, 63 fixtures (37 hợp lệ, 26 sai có chủ đích), tiêu chí nghiệm thu AC-01 đến AC-18. Xem [quyết định và chính sách đầy đủ](API-CONTRACTS.md), [bộ file chuẩn](../contracts/README.md).

## Bằng chứng kiểm tra

Kiểm trên working tree dựa trên HEAD `33f6c4ede9e76161a3b48064aab53f461665d842`, branch `hoanganhdev_features`; thay đổi của task chưa commit.

| Kiểm tra | Kết quả |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests/contracts -q` | 79 passed, 4.57s |
| `.venv/Scripts/python.exe -m ruff check tests/contracts` | All checks passed |
| Bốn file OpenAPI qua `jsonschema.Draft202012Validator` | 0 lỗi mỗi file với [meta-schema OpenAPI 3.1 chính thức](https://spec.openapis.org/oas/3.1/schema/2022-10-07) |
| `git diff --check` | Passed |
| PDF xuất và kiểm tra | 94 trang A4, đủ 84 operationId; 97 schema và 63 fixtures trong catalog. Đã render toàn bộ trang và kiểm tra contact sheets, xem chi tiết sơ đồ/bảng/code; không phát hiện chữ tràn lề, ký tự thay thế hoặc trang trắng. |

SHA256 meta-schema đã dùng: `da01ba28852cac0de53893797cb8d1942bc3b05084f526dcc216717dec314ed0`. Validator cấu trúc và tests local `$ref`/fixtures bổ sung cho nhau; không chứng minh policy thực thi, transaction, retry hoặc tích hợp upstream thật.

PDF mô tả xuất từ chính OpenAPI, identity schemas, fixtures và tài liệu quyết định: `output/pdf/DXOS_FlowBridge_API_Contracts_v0.1.1.pdf`. Artifact được phân phối riêng, không đưa binary sinh ra vào source release. Các fingerprint trong PDF giúp nhận biết snapshot; khi sửa contract phải xuất lại.

SHA256 PDF bàn giao: `74bbe66f667738e665e288d7df6f5556a4bce3d31a2f3ef95135fb058792ec6d`.

## Nguồn và giới hạn

- Thiết kế database v0.2: 29 trang; SHA256 `492959b86ef73a4379ca738125a55438f266a93738aa48be2b3461eaef0270cc`.
- Hướng dẫn hệ thống bổ sung: 11 trang; SHA256 `c2f1fe728276bec488c069d3e7cdc9ef6531e01da87134117ffa2cccb5621914`.
- Link chat ChatGPT chưa truy cập được; không nhận là đã đối chiếu nội dung chat. Ảnh đính kèm chỉ là ví dụ tổ chức file.
- Các trạng thái/DTO Domain, visibility metadata, một kho, single-active-contract và retention idempotency 7 ngày là lựa chọn đề xuất để team duyệt.
- Workflow trung lập engine. Delegation, receiver credential, persistence, fencing/drain và reconciliation phải được cụ thể hóa khi implement, có test lỗi/concurrency thật theo acceptance cases.
- Chưa chạy runtime/engine E2E vì task chỉ sửa specification. Không đánh dấu các endpoint hoặc các AC runtime hoàn thành từ 79 contract tests.
- Không commit, push, deploy hoặc phát hành trong task này.
