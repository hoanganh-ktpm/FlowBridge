# Langflow: tra cứu hồ sơ nhà cung cấp

Flow `supplier_knowledge.json`: **Chat Input → FlowBridge Supplier Knowledge → Chat Output**.
Node gọi API RAG đã có, trả nguyên đoạn trích và nguồn phiên bản. Chưa có bước LLM tổng hợp;
không gọi ML, không cần checkpoint TabICLv2 hoặc API model trả phí.

## Cài riêng Langflow

Chạy từ root repository, dùng môi trường riêng để giữ dependency backend/ML hiện tại:

```powershell
.\.tools\bootstrap\bin\uv.exe venv .cache/rag/langflow-integration/venv --python .venv/Scripts/python.exe
.\.tools\bootstrap\bin\uv.exe pip install --python .cache/rag/langflow-integration/venv/Scripts/python.exe -r langflow/rag/requirements.txt
```

Chốt `langflow-base==1.12.5` và `lfx==1.12.5`. Base cung cấp UI/API và các node dùng trong flow;
không cần cài thêm provider LLM. Xem [custom components](https://docs.langflow.org/components/custom/)
và [cách gọi flow qua API](https://docs.langflow.org/concepts-publish).

## Kết nối dịch vụ

Backend cần bật `FLOWBRIDGE_RAG_POSTGRES_ENABLED=true`, tắt `FLOWBRIDGE_ML_ENABLED` cho phiên
chạy chỉ thử RAG. Chuẩn bị kho tài liệu theo [hướng dẫn RAG](../../docs/rag-postgres-setup.md),
Keycloak và danh tính service theo [hướng dẫn SSO](../../docs/SSO.md).

Đặt các biến sau trong **tiến trình Langflow**, bằng secret/config local:

| Biến | Ý nghĩa |
|---|---|
| `FLOWBRIDGE_RAG_API_ORIGIN` | Origin backend, ví dụ `http://127.0.0.1:18083` cho local |
| `FLOWBRIDGE_RAG_TOKEN_URL` | HTTPS token endpoint của realm Keycloak |
| `FLOWBRIDGE_RAG_CLIENT_ID` | Client service AI, mặc định realm nhóm là `flowbridge-ai` |
| `FLOWBRIDGE_RAG_CLIENT_SECRET` | Secret của client AI |
| `FLOWBRIDGE_RAG_CA_BUNDLE` | CA tin cậy nếu dùng chứng chỉ Keycloak local |

URL và client secret được đọc từ môi trường server, không lấy từ câu hỏi và không lưu trong
file flow. Node lấy token client credentials mới mỗi lần gọi, gửi bearer cùng `X-Job-Context`
và `Idempotency-Key` tới endpoint retrieve. Có thể cung cấp bearer qua input secret tùy chọn
khi worker đã quản lý token; khi đó node không tự lấy token.

Danh tính service phải liên kết với user `account_type=service`, grant `AI_SERVICE` active.
Node chỉ truy hồi; không import, index, sửa tài liệu hoặc thực thi nghiệp vụ mua hàng.

## Import và chạy

Sau khi cấu hình các biến server ở trên, chạy Langflow local:

```powershell
$env:LANGFLOW_CONFIG_DIR = "$PWD/.cache/rag/langflow-integration/langflow-state"
$env:LANGFLOW_DO_NOT_TRACK = "true"
.\.cache\rag\langflow-integration\venv\Scripts\python.exe -m langflow run --host 127.0.0.1 --port 7860
```

1. Khởi động Langflow và import `supplier_knowledge.json`.
2. Mở node **FlowBridge - Supplier Knowledge**, đặt **Analysis task UUID** của tác vụ hợp lệ.
   Đây là UUID thật trong `analysis_tasks`, không phải request/PO ID. Tác vụ phải khớp revision
   PR và có cutoff sau publication tài liệu nếu muốn thấy các hồ sơ mới nhập.
3. Đặt **Supplier UUID** để lọc một nhà cung cấp, hoặc để trống để tìm trong phạm vi job.
4. Mở Playground, nhập câu hỏi. Chat Output hiển thị đoạn trích và `core-document://` citation.

Task UUID do worker/operator cung cấp; không để LLM tự đoán. Backend kiểm tra grant, task,
phòng ban và cutoff. Đây là flow theo job của AI service, chưa phải triển khai chat theo
UserContext của nhân viên. Token lỗi/quyền bị thu hồi phải báo lỗi, không trả kết quả rỗng giả.

Có thể xuất lại template không kèm credential/task riêng bằng Python trong môi trường Langflow:

```powershell
.\.cache\rag\langflow-integration\venv\Scripts\python.exe langflow/rag/flow.py
```

## Kiểm thử

Các test protocol chạy bằng lớp Component thật của LFX, upstream HTTP được kiểm soát:

```powershell
.\.cache\rag\langflow-integration\venv\Scripts\python.exe -m pytest langflow/rag/tests -q -c langflow/rag/pytest.ini
```

Helper `tests/e2e/rag_fixture.py` tạo/cleanup dữ liệu riêng cho một profile Keycloak local
có test users. Helper xác minh danh tính thật tại IdP; không override authentication.
Không dùng profile IAM production hoặc sửa quyền của tài khoản nghiệp vụ có sẵn.

```powershell
.\.venv\Scripts\python.exe tests/e2e/rag_fixture.py --identity-dir .cache/rag/langflow-integration/identity
# Sau khi thử xong và không cần tác vụ này trong Playground nữa:
.\.venv\Scripts\python.exe tests/e2e/rag_fixture.py --identity-dir .cache/rag/langflow-integration/identity --cleanup
```

Với profile local đã chuẩn bị, kiểm tra flow qua **Langflow HTTP API thật**:

```powershell
.\.venv\Scripts\python.exe langflow/rag/check_flow.py
```

Helper dùng `LANGFLOW_API_KEY` hoặc key local trong `.cache/rag/langflow-integration`;
đây là API key gọi Langflow, khác bearer Keycloak mà node dùng để gọi backend RAG.
Profile giữ flow ID/node ID và UUID tác vụ local. Với profile/flow khác, truyền `--profile`,
`--flow-id`, `--task-id` tương ứng. Cache, secret và kết quả chạy thử không đưa vào Git.
