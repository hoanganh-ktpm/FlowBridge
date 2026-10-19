# Agent Hub API — chạy backend và nối web/app

<!-- SPDX-License-Identifier: GPL-3.0-or-later; FlowBridge, see LICENSE. -->

Backend cung cấp đủ 12 operation trong [OpenAPI](../contracts/agent-hub-api.yaml),
prefix `/api/v1/agent-hub`. Chat cũ `/api/v1/chat/*` giữ nguyên. Module được bật
riêng; API list/create conversation không chạy model. Chỉ POST tạo run được nhận
mới mới đưa công việc vào hàng đợi để chạy Langflow.

## 1. Chuẩn bị và chạy

1. Cài backend theo README, dùng Python 3.12 và `uv sync --locked`.
2. Dùng DB/SSO đang có, tài khoản human có grant EMPLOYEE, theo
   [LANGFLOW-SSO](LANGFLOW-SSO.md). Không dùng API key Langflow thay bearer người dùng.
3. Xem SQL trước khi áp dụng migration:

   ```powershell
   .venv/Scripts/python -m alembic upgrade 0005_chat_projection:head --sql
   .venv/Scripts/python -m alembic upgrade head
   ```

   Migration `0006_agent_hub` thêm năm bảng `hub_*`, index duy nhất bảo vệ một run
   đang hoạt động mỗi hội thoại, RLS/revoke theo các migration hiện có và pgcrypto
   trong schema `extensions`. Không thay bảng nghiệp vụ hoặc bảng chat cũ.
   Nếu pgcrypto đã được cài ở schema khác, cần đối chiếu cấu hình DB với quản trị
   trước khi bật Hub; startup kiểm đúng `extensions.pgp_sym_encrypt` và từ chối
   chạy nếu thiếu. Không tự di chuyển extension đang dùng bởi ứng dụng khác.
4. Thêm cấu hình vào `.env` riêng của backend:

   ```dotenv
   FLOWBRIDGE_AGENT_HUB_ENABLED=true
   FLOWBRIDGE_AGENT_HUB_WORKER_ENABLED=true
   FLOWBRIDGE_AGENT_HUB_QUEUE_TIMEOUT_SECONDS=300
   FLOWBRIDGE_AGENT_HUB_CREDENTIAL_KEY=<khóa-ngẫu-nhiên-riêng-ít-nhất-32-ký-tự>
   ```

   Giữ các cấu hình `FLOWBRIDGE_LANGFLOW_*`, OIDC, DB và CORS đã thiết lập cho SSO.
   Khóa mã hóa phải khác bridge secret, giữ ổn định giữa các process/restart và
   không đưa vào Git/frontend. Worker chuyển tiếp access token của chính người
   dùng; token chỉ lưu dưới dạng pgcrypto PGP AES256 và xóa khi terminal. Không
   lưu refresh token, không giả danh bằng service token. IAM kiểm lại token trước
   khi chạy và các API công cụ tiếp tục kiểm quyền riêng. Token hết hạn làm run
   failed; hệ thống không tự refresh hoặc tự chạy lại.
5. Khởi động lại **SSO serving bridge** với runtime mới bằng
   `langflow/start-sso.cmd`, không dùng editor-only server để thay bridge.
   Graph ver2 đang phục vụ phải có `FrontendResponse-ver2-central`, được nối từ
   agent trung tâm. Export trong repo đã có node này. Không cần đổi graph theo
   từng người dùng; selection được áp dụng bằng context của mỗi lần chạy.
6. Khởi động backend bằng lệnh SSO đang dùng, ví dụ:

   ```powershell
   .venv/Scripts/python scripts/run_sso_api.py --identity-dir .cache/identity --port 8000
   ```

   Hoặc chạy factory `flowbridge.main:create_app` với cấu hình OIDC đầy đủ trong
   môi trường. Khi bật docs, `/openapi.json` có các Hub operation và schema đã
   resolve sẵn; wheel chứa hợp đồng lấy trực tiếp từ nguồn `contracts/` lúc build.

Đường gọi backend→Langflow dùng probe `agent-hub-v1`, sau đó NDJSON nội bộ chứa
activity/result. Chỉ output Data từ node cố định được nhận. Bridge bản cũ bị
phát hiện trước khi gọi model. Backend không điều phối ML/RAG/Data thay Langflow.
SSO chat cũ vẫn probe `flowbridge-caller-v1` và nhận response cũ.

Với profile Docker chat, rebuild image backend/Langflow để nạp runtime mới.
Profile đã có tùy chọn `FLOWBRIDGE_AGENT_HUB_ENABLED` ở API container và sinh một
credential key riêng trong volume `chat-config`; bật sau migration. Dùng các
lệnh quản lý profile trong [Hướng dẫn chạy](../HUONG-DAN-CHAY.md), không tạo stack IAM mới.
Gateway profile Identity hiện tại chỉ công bố các route Identity; web/app gọi
API origin đã cấu hình. Khi đặt reverse proxy trước Hub, chuyển nguyên Authorization,
Idempotency-Key, If-Match, Last-Event-ID, tắt buffering/compression cho SSE và không
tự retry POST. CORS backend đã cho phép các header này và expose ETag/Location/
Retry-After/X-Request-ID. Không công bố bridge secret/native Langflow key.

## 2. Trình tự gọi từ frontend

1. `GET /agents`: lấy metadata/available, không ping model. Chưa bật ML hoặc RAG
   thì agent tương ứng báo `configuration_unavailable`.
2. `POST /conversations` với Idempotency-Key mới:

   ```json
   {"title":"Tư vấn nhà cung cấp","selected_agents":["prediction"]}
   ```

   Với RAG, thêm `request_id` là UUID PR người dùng có quyền đọc. Backend chọn
   analysis job hợp lệ của đúng revision PR để cấp ngữ cảnh RAG theo cơ chế SSO
   có sẵn. Client không gửi job ID, role, tenant, service token hoặc dependency.
   `request_id` bất biến; không tự tạo PR/job khi chat. Khi không có job phù hợp,
   admission trả 503 `KNOWLEDGE_CONTEXT_REQUIRED`, chưa gọi model.
3. `POST /conversations/{id}/runs`, bearer + Idempotency-Key mới:

   ```json
   {"conversation_revision":1,"timezone":"Asia/Ho_Chi_Minh","message":"So sánh nhà cung cấp băng keo"}
   ```

   Lưu response 202, run_id và events_url. Retry do mất mạng dùng nguyên key/body;
   server trả lại cùng admission, không gọi model lần hai. Key khác là lượt mới.
4. Mở streaming fetch tới `events_url` với bearer. Lưu event ID sau khi áp dụng;
   reconnect cùng run bằng Last-Event-ID. Khi có terminal event, GET `result_url`
   để đọc JSON bảng/biểu đồ và GET `/metadata` để lấy selected/used/dependency_agents.
5. Khi `needs_input`, gửi một run mới với `response_to_run_id` và
   `question_answers` theo ID câu hỏi. Không sửa kết quả run trước.
6. PATCH conversation cần strong ETag từ GET, ví dụ `If-Match: "1"`. Run đang
   chạy giữ snapshot lựa chọn cũ. `selected_agents: []` chỉ chạy trung tâm.

## 3. Activities: công cụ, số giây và lỗi đúng vị trí

Mỗi lời gọi có `span_id` riêng và `parent_span_id` để dựng cây. Không gộp theo
tên tool: một tool gọi hai lần là hai dòng. Agent/tool bắt đầu phát `.started`;
kết thúc phát `.completed` với:

```json
{
  "span_id":"tool_01",
  "parent_span_id":"knowledge_01",
  "agent":"knowledge",
  "tool":"knowledge_search_supplier",
  "label":"Tìm tài liệu nhà cung cấp",
  "outcome":"failed",
  "duration_ms":3125,
  "error":{
    "code":"UPSTREAM_TIMEOUT",
    "message":"Bước xử lý này không hoàn tất; xem mã lỗi để đối chiếu.",
    "retryable":false,
    "trace_id":"run_example"
  }
}
```

Frontend hiển thị **Tri thức RAG → Tìm tài liệu nhà cung cấp → Lỗi → 3.1 s**.
`agent` xác định Dữ liệu/Dự đoán/Tri thức; `tool` là mã ổn định, `label` để hiển thị.
`outcome` gồm success, needs_input, failed, cancelled. `error` chỉ xuất khi failed;
không trả exception thô, prompt, SQL, API key hay nội dung suy luận của model.
Lỗi định dạng/gọi A2A cũng có dòng tool `hoi_du_lieu/hoi_du_doan/hoi_tri_thuc` với
agent đích, dù agent đó chưa chạy. Metadata used_agents chỉ ghi agent thực sự bắt đầu.

`duration_ms` đo bằng đồng hồ monotonic quanh lời gọi thật, bao gồm thời gian chờ
trong lời gọi; không bịa theo token. Trong lúc chạy, UI có thể đếm tạm từ
`occurred_at`, rồi thay bằng duration_ms chính thức khi kết thúc. Thời gian agent
bao gồm các tool con: không cộng cả agent và tool để tính tổng thời gian run.
Các tool A2A cũng bao gồm thời gian agent con. Tổng run tính từ metadata timestamps.

Node định dạng có thể gọi `supplier_delivery_statistics` sau khi agent trung tâm
kết thúc. Tool này hiển thị nhóm Dữ liệu, parent là central; không giả thêm một
lần chạy agent Dữ liệu vào used_agents. Đối với lỗi/timeout/restart, backend đóng
span còn dang dở bằng failed + `STEP_INTERRUPTED`; duration lúc này là thời gian
đã chờ trước khi dừng, không khẳng định model phía xa đã ngừng. Cancel dùng cancelled.
Lỗi admission (401/409/422/503) trả Problem HTTP và chưa tạo run/activity.

Web/app đọc `GET /runs/{id}/events` bằng streaming fetch có Authorization header,
giữ `run_id`, `sequence`, `span_id` và `parent_span_id` để cập nhật đúng dòng.
Mỗi `*.started` mở một dòng đang chạy; `*.completed` cập nhật `outcome`,
`duration_ms` và `error` của cùng `span_id`. Khi mất kết nối, gửi Last-Event-ID
để nhận lại phần thiếu; event trùng được bỏ qua theo sequence. Sau event terminal,
GET result_url lấy câu trả lời/bảng/biểu đồ. Cursor hết 24h trả 410: GET result
và metadata để phục hồi trạng thái. Render label/error như text, không innerHTML.
UI thuộc phạm vi frontend riêng; backend chưa phát token `answer.delta`.
Phiên bản này stream activities/run status; chưa stream token `answer.delta`.

## 4. JSON bảng/biểu đồ và khác biệt với Langflow

- GET run trả trực tiếp 11 trường RunResult, không bọc output Langflow hay JSON
  trong chuỗi. Dữ liệu số chỉ từ receipt đã kiểm, không bóc từ lời văn.
- `partial` của Langflow thành completed kèm `PARTIAL_RESULT`; không mất warnings
  hoặc tự biến thiếu dữ liệu thành số 0.
- Câu hỏi chuỗi trở thành `q_1`, `q_2` kiểu text; không đoán options/kind từ prose.
- Decimal string giữ độ chính xác và thêm scale; không chuyển tiền sang float.
- `as_of=null` khi upstream chưa có cutoff, kèm `AS_OF_UNKNOWN`. Không dùng giờ
  hiện tại giả làm mốc chốt dữ liệu. UI ghi “chưa có mốc chốt”.
- Ngữ cảnh product/unit/currency được chuyển vào filters. Nguồn document đổi
  document_version→version; tham chiếu ACL đầy đủ giữ riêng trong DB.
- Langflow cũ trả percent-stack bằng giá trị đã tính %. Adapter đổi stacking
  thành normal để frontend không chuẩn hóa phần trăm hai lần. Percent-stack của
  public contract vẫn yêu cầu số đếm gốc và mẫu số đầy đủ.
- Trước lưu, kiểm schema + kiểu từng ô + reference dataset/column/source + đơn vị
  chart + nhãn trùng/null + giới hạn. Sai dữ liệu trả failed, không dựng số thay thế.

## 5. Vận hành và giới hạn

Queue, kết quả, messages, SSE đều lưu PostgreSQL. Worker trong lifecycle process
claim bằng `FOR UPDATE SKIP LOCKED`; có thể chạy nhiều API process với cùng key.
Mỗi worker xử lý một run tại một thời điểm. Một conversation có tối đa một run
queued/running/cancelling. Worker chết: deadline hết thì failed và xóa credential,
không replay lời gọi model. Queued quá 300s mặc định cũng failed; chỉnh bằng setting.
Nếu tắt worker trên các API instance, phải giữ ít nhất một instance bật worker.

Cancel là dừng hợp tác: ngắt HTTP đang chờ và chặn xuất kết quả muộn. Không rollback
nghiệp vụ hoặc bảo đảm provider dừng tính phí lời gọi đã gửi. Không có retry OpenAI
ở backend này. Chính sách SDK/budget trong graph vẫn giữ cấu hình hiện có.

Cursor lưu membership/thứ tự snapshot 24h, giới hạn 10.000 mục mỗi snapshot;
vượt trả 413 rõ ràng. Quyền tài nguyên/citation kiểm lại khi đọc lịch sử/result/SSE,
kể cả tài nguyên kế thừa từ context lượt trước. Dữ liệu của owner khác trả 404.
Idempotency giữ bền vững (ít nhất 24h, hiện không tự xóa); bảng messages/results/events
không tự xóa lịch sử. SSE hết cửa sổ replay 24h dù bản ghi event còn lưu; cần retention
job theo chính sách triển khai nếu muốn xóa vật lý dữ liệu cũ.

PGP encryption chạy trên PostgreSQL: dùng kết nối TLS/DB tin cậy và bảo vệ DB logs.
Tham khảo [pgcrypto của PostgreSQL](https://www.postgresql.org/docs/current/pgcrypto.html).
Không log exception SQL/credential từ worker. Secret chỉ truyền bằng header nội bộ,
không đi vào prompt/tweaks/history graph.

## 6. Kiểm tra không tốn phí model

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python -m pytest tests/unit/test_agent_hub.py tests/unit/test_chat_backend.py tests/unit/test_chat_migration.py -q
$env:PYTHONPATH='backend;langflow/runtime'
langflow/.venv/Scripts/python -m pytest langflow/tests/test_hub_serving.py langflow/tests/test_sso_serving.py langflow/tests/test_ver2_coordination.py langflow/tests/test_ver2_protocol_nodes.py langflow/tests/test_ver2_knowledge_dependency.py langflow/tests/test_frontend_response.py langflow/tests/test_frontend_delivery_statistics.py -q
```

Các test này dùng SQLite, MockTransport, ASGI doubles và model giả lập; không gọi
OpenAI/ML/RAG thật. SQLite không chứng nhận locking/pgcrypto PostgreSQL. Migration
được kiểm bằng SQL offline; áp dụng DB thật và nghiệm thu model là bước vận hành riêng.
