# Chạy thử RAG nhà cung cấp tại máy

RAG có CLI và endpoint HTTP development bật riêng, chạy riêng với API PostgreSQL vì chế độ development yêu cầu database không được cấu hình. Mỗi file trong `supplier_reviews/` là một nhà cung cấp. Nội dung và người nhận xét được giữ nguyên từ bộ dữ liệu mẫu đã xuất; `source_provenance.json` ghi nguồn, seed và cách phân công người nhận xét. Đây là dữ liệu giả lập để thử hệ thống.

## 1. Cài đặt

Chạy từ **thư mục gốc FlowBridge**, dùng Python 3.12 và uv 0.13.1 theo cấu hình repo:

```powershell
uv sync --locked --extra rag --group dev
$env:HF_HUB_DISABLE_TELEMETRY = "1"
$env:OMP_NUM_THREADS = "4"
```

Nhóm dependency `rag` là tùy chọn; backend thông thường không tự import Torch/Chroma hoặc tải mô hình.

## 2. Tạo chỉ mục

```powershell
uv run --no-sync python -m flowbridge.supplier_assistant.knowledge index --allow-download
```

Lần đầu tải Qwen3-Embedding-0.6B công khai vào `.cache/models/`; tải mô hình cần mạng và dung lượng ổ đĩa. Nội dung hồ sơ được embedding **tại máy**, không gọi API LLM bên ngoài. Model đã có cache thì chạy offline:

```powershell
uv run --no-sync python -m flowbridge.supplier_assistant.knowledge index
```

Chỉ mục được lưu ở `.cache/rag/`, đã được `.gitignore` loại khỏi Git. Chạy lại cùng dữ liệu/cấu hình giữ nguyên `index_version`, không thêm chunk trùng và không tính embedding lại. Lỗi embedding/ghi vector không kích hoạt thế hệ chỉ mục đang xây dở. Bộ sáu hồ sơ tạo 96 chunk; máy thử CPU FP32 mất khoảng 2 phút 22 giây cho lần index đầu sau khi tải xong model. Máy khác có thể khác thời gian.

## 3. Tìm đoạn trích

```powershell
uv run --no-sync python -m flowbridge.supplier_assistant.knowledge search "DEMO-S01 có hồ sơ giao trễ nào?" --supplier 3806e329-2ebe-5cfc-895d-1bd60894b348 --top-k 5
```

`--supplier` nhận UUID từ `catalog.json`. Không truyền bộ lọc này thì tìm trên mọi hồ sơ được cho phép trong catalog local. Có thể giới hạn thêm bằng một hoặc nhiều `--allow-document UUID`.

Kết quả JSON đúng cấu trúc `RetrievalResponse` hiện có trong contract: `query`, `chunks`, `index_version`, `warnings`. Mỗi chunk có đoạn nguyên văn, điểm xếp hạng và `reference` gồm đủ:

```json
{
  "document_id": "UUID hồ sơ local",
  "document_version": "sha256:hash toàn file",
  "chunk_id": "UUID ổn định của đoạn trong phiên bản này",
  "source_uri": "local-document://.../sha256:...#chars=123:456",
  "checksum": "SHA-256 toàn file gốc",
  "supplier_id": "UUID nhà cung cấp"
}
```

`relevance_score` là điểm RRF đã chuẩn hóa vào [0, 1] để phù hợp contract. **Đó không phải xác suất đúng, độ tin cậy, hoặc rủi ro giao trễ.** Không dùng điểm này làm xác suất đầu vào TabICLv2. Truy vấn không liên quan vẫn có thể nhận láng giềng dense; bước trả lời phải kiểm tra đoạn trích và từ chối kết luận khi thiếu căn cứ.

## 4. Xem nguồn và người nhận xét

Lấy `chunk_id` từ kết quả tìm kiếm:

```powershell
uv run --no-sync python -m flowbridge.supplier_assistant.knowledge inspect CHUNK_UUID
```

Kết quả bổ sung `heading_path`, người nhận xét/user ID, đường dẫn file, dòng đầu/cuối, vị trí ký tự, `known_from` và `report_asof`. Vị trí là chỉ số ký tự Unicode theo Python, bắt đầu từ 0, khoảng nửa mở `[start_char, end_char)`; số dòng bắt đầu từ 1. Đoạn trả về được đọc lại từ nguồn và đối chiếu hash, không lấy văn bản sao chép từ Chroma.

`inspect` tách riêng để giữ nguyên DTO API, vốn không cho phép thêm các trường người nhận xét vào `KnowledgeChunk`.

## 5. Thời điểm A/B và cập nhật hồ sơ

`report_asof` là mốc nghiệp vụ mà báo cáo tổng hợp. `known_from` là thời điểm tài liệu thực sự có sẵn; catalog mẫu dùng thời điểm xuất tài liệu được ghi trong manifest. Hai mốc này khác nhau.

```powershell
uv run --no-sync python -m flowbridge.supplier_assistant.knowledge search "giao trễ" --as-of "2026-10-15T18:00:00+07:00"
```

Bộ hồ sơ này được xuất sau mốc trên nên kết quả sẽ rỗng kèm `NO_ELIGIBLE_SOURCE`. Không đưa báo cáo tổng kết tương lai vào ngữ cảnh dự đoán lúc tạo PR (A), hoặc lúc theo dõi PO (B) sớm hơn thời điểm công bố. CLI không truyền `--as-of` được hiểu là tra cứu hồ sơ hiện có.

Khi thay nội dung Markdown:

1. Cập nhật hash SHA-256 trong `catalog.json`, kiểm tra đúng supplier/reviewer và cập nhật `known_from` theo bằng chứng công bố phiên bản mới.
2. Chạy lại `index`.
3. Chỉ mục chuyển sang thế hệ mới khi xây xong. Nguồn đã bị sửa/gỡ khỏi catalog bị chặn ngay trong truy vấn, dù chưa index lại.

Có thể tính hash bằng `Get-FileHash examples/rag/supplier_reviews/DEMO-S01.md -Algorithm SHA256`; lưu giá trị chữ thường vào catalog. Giữ nguyên kiểu xuống dòng khi muốn giữ nguyên checksum. Chỉ mục cũ được giữ tại máy để truy vết, nhưng lệnh `search`/`inspect` chỉ dùng thế hệ đang hoạt động. Chưa hỗ trợ truy vấn lịch sử nhiều phiên bản.

CLI hỗ trợ `--catalog PATH --index PATH --model-cache PATH` **trước** tên lệnh. Nếu tiến trình index bị dừng đột ngột, chỉ xóa `.write.lock` sau khi xác nhận không còn tiến trình ghi; sau đó chạy lại. Không chạy nhiều writer cùng thư mục index.

## 6. Kiểm thử

```powershell
uv run --no-sync ruff check backend tests
uv run --no-sync pytest -q
```

Test Chroma thật dùng vector nhỏ cố định để kiểm tra persistence/lọc/cập nhật; test đó không đo chất lượng Qwen.

## 7. API HTTP development — gọi từ Postman hoặc PowerShell

Endpoint này chạy RAG thật trên catalog local. Mặc định tắt; chỉ cho môi trường development/test, host loopback và DSN trống. Không dùng JWT/Core ACL trong chế độ này; phạm vi là toàn bộ catalog fixture do người vận hành cấu hình, có thể lọc theo supplier. Không nhận role, owner, allowlist hoặc đường dẫn file từ body.

Sau khi đã cài extra `rag` và index ở bước 1–2, mở PowerShell tại thư mục gốc repo và chạy:

```powershell
# Chạy trong thư mục gốc repo FlowBridge.
$env:FLOWBRIDGE_ENVIRONMENT = "development"
$env:FLOWBRIDGE_HOST = "127.0.0.1"
$env:FLOWBRIDGE_PORT = "8000"
$env:FLOWBRIDGE_DATABASE_URL = ""
$env:FLOWBRIDGE_DATABASE_PASSWORD = ""
$env:FLOWBRIDGE_RAG_DEV_ENABLED = "true"
$env:FLOWBRIDGE_DOCS_ENABLED = "true"
$env:OMP_NUM_THREADS = "4"
$env:HF_HUB_OFFLINE = "1"
.\.venv\Scripts\python.exe -m flowbridge.main
```

Giữ terminal này chạy. Mở Swagger tại **http://127.0.0.1:8000/docs**. Cả URL và password database phải trống, kể cả khi `.env` có cấu hình theo từng trường; Settings sẽ ghép DSN từ password trước khi kiểm tra RAG. API Core sample đã bị loại bỏ trên `main`. API PostgreSQL có xác thực chạy riêng khi tắt RAG development và cấu hình database.

**Postman:** chọn `POST http://127.0.0.1:8000/internal/v1/tools/retrieve-knowledge`, thêm header `Idempotency-Key: rag-demo-001`, chọn Body → raw → JSON:

```json
{
  "query": "PO DEMO-PO-00212 giao trễ như thế nào?",
  "supplier_id": "3806e329-2ebe-5cfc-895d-1bd60894b348",
  "top_k": 3
}
```

Hoặc mở **terminal PowerShell thứ hai**, chạy:

```powershell
$payload = @{
    query = "PO DEMO-PO-00212 giao trễ như thế nào?"
    supplier_id = "3806e329-2ebe-5cfc-895d-1bd60894b348"
    top_k = 3
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8000/internal/v1/tools/retrieve-knowledge" `
    -Headers @{ "Idempotency-Key" = "rag-demo-001" } `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($payload)) |
    ConvertTo-Json -Depth 8
```

Mong đợi HTTP 200; đoạn đầu nói về PO `DEMO-PO-00212`, đủ 270 unit và trễ 26 ngày. Response giữ DTO `RetrievalResponse`, thêm cảnh báo development trong `warnings` và header `X-FlowBridge-RAG-Mode: development-local-fixtures`. Chưa có bước LLM viết lại thành câu trả lời hội thoại.

Model được nạp ở request đầu và giữ trong app; mỗi tiến trình server có một model. Chỉ một truy vấn RAG được xử lý tại một thời điểm; request chồng lên trả HTTP 429, `Retry-After: 1`. Model không được tải từ Internet bởi endpoint. Lần đầu có thể mất hàng chục giây tùy CPU; lần sau không phải nạp lại model.

| Kết quả | Cách xử lý |
|---|---|
| 404 | Kiểm tra `FLOWBRIDGE_RAG_DEV_ENABLED=true` và khởi động lại đúng server. |
| 422 | Kiểm tra query không rỗng, UUID supplier, top_k 1–20 và header Idempotency-Key. Bỏ trường supplier_id nếu không lọc; không gửi null. |
| 429 | Đợi lượt tìm kiếm hiện tại hoàn tất rồi thử lại. |
| 503 MODEL_UNAVAILABLE | Chuẩn bị model bằng CLI `index --allow-download` trước khi chạy HTTP offline. |
| 503 UPSTREAM_UNAVAILABLE | Kiểm tra catalog, checksum và index bằng CLI; endpoint không tự index. |

`Idempotency-Key` hiện chỉ kiểm tra hình dạng header, không triển khai replay bền vững. API này chỉ tra cứu nguồn hiện có; body chưa có `as_of` hay ngữ cảnh A/B. Tham số CLI `--as-of` vẫn dùng cho thử nghiệm lọc thời điểm. Endpoint `index-knowledge` vẫn chưa được mount; tiếp tục dùng CLI để lập chỉ mục. Không cần tạo database để thử API này.

Các đường dẫn có thể cấu hình bằng `FLOWBRIDGE_RAG_DEV_CATALOG_PATH`, `FLOWBRIDGE_RAG_DEV_INDEX_PATH`, `FLOWBRIDGE_RAG_DEV_MODEL_CACHE_PATH`; đường dẫn tương đối tính từ thư mục khởi chạy.

## Phần dành cho bước tích hợp sau

- Thay `LocalSources` bằng adapter đọc Markdown từ document store PostgreSQL hiện có; dùng ID/version/URI do kho nguồn cấp, không coi UUID local là Core file ID thật.
- Backend lấy danh sách tài liệu được phép từ danh tính/quyền thực tế rồi tạo `RetrievalScope`. Hàm `local_operator_scope` chỉ phục vụ người vận hành CLI.
- Nối caller/ACL và document store thật vào endpoint retrieval; bổ sung endpoint index theo source references, rồi nối tool Langflow. Endpoint development hiện có không phải receiver production.
- Reranker có interface tùy chọn, mặc định tắt. Nếu bật cần adapter và đánh giá riêng; không mặc định gửi hồ sơ nội bộ lên cloud.
- RAG chỉ trả bằng chứng văn bản. Giá, trạng thái đơn và thống kê chính xác nên lấy bằng công cụ dữ liệu; TabICLv2 vẫn là mô hình dự đoán riêng.
