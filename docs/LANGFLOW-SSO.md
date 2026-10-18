# Backend chat sau đăng nhập Keycloak

Cách chạy gom dịch vụ bằng Docker: [DOCKER-CHAT.md](DOCKER-CHAT.md).

API chuẩn giữ nguyên DTO0.4.1: `POST /api/v1/chat/messages` và
`GET /api/v1/chat/sessions/{session_id}/messages`. User phải được liên kết bằng
issuer+subject và có grant EMPLOYEE active. Backend kiểm token tại Keycloak,
kiểm quyền ứng dụng, reserve lượt chat rồi gọi flow ver2 cố định qua Langflow.
Giao diện tại8761 có tab **Trợ lý AI** cho EMPLOYEE; dùng chính phiên SSO để gọi
backend, không đưa API key Langflow xuống browser. Xem [client](../clients/sso/README.md).

## Cấu hình

Dùng SSO profile/API/DB hiện có theo [SSO.md](SSO.md). Không chạy lại seed,
reset realm hoặc tạo user giả. Cần apply migration mới trước khi dùng chat:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

`0005_chat_projection` thêm `chat_sessions` và `chat_turns` operational bên ngoài
baseline27bảng supplier. Bật RLS, revoke PUBLIC/anon/authenticated; backend dùng
DB role có quyền phù hợp như api_idempotency. Migration nối tiếp
`0004_knowledge_manager` đã có trên Supabase (nguồn `origin/minnhungdev_features`,
commit `5e96b38`); giữ nguyên SQL migration đã công bố. Không có policy truy cập trực tiếp
qua Supabase browser. Startup không tự migrate. Lệnh trên thay đổi DB được chọn
trong Settings; xác nhận đúng môi trường trước khi vận hành.

Backend Settings đọc các giá trị sau từ env/.env, hoặc profile runtime của launcher
SSO. Không đưa giá trị secret vào Git, CLI argument, log hoặc ảnh chụp:

| Biến | Giá trị |
|---|---|
| `FLOWBRIDGE_CHAT_ENABLED` | `true` |
| `FLOWBRIDGE_LANGFLOW_URL` | Origin Langflow, local mặc định `http://127.0.0.1:7860` |
| `FLOWBRIDGE_LANGFLOW_FLOW_ID` | `flowbridge-ver2-central` |
| `FLOWBRIDGE_LANGFLOW_API_KEY` | API key thật của chủ sở hữu các flow ver2 trong Langflow |
| `FLOWBRIDGE_LANGFLOW_BRIDGE_SECRET` | Secret ngẫu nhiên riêng, ít nhất32ký tự |
| `FLOWBRIDGE_LANGFLOW_TIMEOUT_SECONDS` | `180`, có thể cấu hình tối đa600 |

Tạo API key bằng chức năng API Keys của Langflow trên profile hiện có, giữ key ở
backend. Không dùng access token Keycloak làm API key Langflow. Backend preflight
`/_ops/flowbridge` để từ chối process editor/stock Langflow trước khi chạy model.

Trong `langflow/data/agent.env` bị ignore, cấu hình cùng
`FLOWBRIDGE_LANGFLOW_BRIDGE_SECRET` và `FLOWBRIDGE_LANGFLOW_FLOW_ID`; đặt
`FLOWBRIDGE_API_ORIGIN` về API SSO chuẩn đang chạy, mặc định8000. Với container,
phải dùng địa chỉ nội bộ của API truy cập được từ Langflow. Giữ config model/key
hiện có. Không cần nhập user bearer vào env hoặc sửa credential của9node đang
trỏ8001: context serving override origin/token theo request, cả flow A2A con.
Editor test vẫn giữ nguyên cấu hình metadata cũ.

Dừng process Langflow cũ trên7860 rồi chạy profile serving:

```powershell
.\langflow\start-sso.cmd
# Terminal khác, API với profile SSO hiện có:
.\.venv\Scripts\python.exe scripts/run_sso_api.py --identity-dir YOUR_EXISTING_IDENTITY_DIR
```

`start-sso` dùng upstream Langflow app/lifespan qua Uvicorn, thêm ASGI adapter;
không sửa source dependency hoặc flow metadata. Native Langflow auth luôn bật,
auto-login và skip-auth tắt trong profile này. Editor đăng nhập bằng cơ chế native
của profile hiện có; user ứng dụng chỉ gọi API chat sau login Keycloak. Giữ Langflow
ở loopback/mạng nội bộ, API key và bridge secret chỉ ở các process backend.
Các flow A2A con phải ở mode Internal, cùng project và đang được publish.

Để chạy ngoài Windows, đặt `PYTHONPATH` gồm `langflow/runtime` và `backend`, các
biến `LANGFLOW_COMPONENTS_PATH`, `LANGFLOW_CONFIG_DIR`, `LANGFLOW_SAVE_DB_IN_CONFIG_DIR`,
`LANGFLOW_A2A_ENABLED`, `FLOWBRIDGE_AGENT_ENV_FILE` tương đương launcher, rồi dùng
Python trong môi trường Langflow chạy:

```sh
python -m uvicorn flowbridge_agent.serving:create_app --factory --host 127.0.0.1 --port 7860 --no-access-log --env-file langflow/data/agent.env
```

Profile hiện tại hỗ trợ template/ID central của ver2 trong repo. Flow tùy biến
đổi ID ChatInput/ChatOutput phải cập nhật allowlist tweak trong adapter và kiểm lại
việc tắt history; không chỉ đổi flow_id sang một flow bất kỳ.

## Gọi API

Sau login, client refresh access token trước request và gửi `Authorization: Bearer`
cùng `Idempotency-Key` mới. Body ví dụ (UUID PR phải thuộc quyền caller):

```json
{"message":"Xem tình trạng yêu cầu mua hàng","request_id":"UUID_PR_THẬT"}
```

Response có session_id, answer, sources, warnings, needs_clarification. Lượt sau
gửi session_id đó; request_id nếu có phải khớp request đã gắn với session. Không
cho đổi PR của session. Session UUID không cấp quyền cho người khác. API history
hỗ trợ page/page_size và chỉ trả các lượt đã hoàn tất.

Mỗi lượt Langflow có execution/session UUID mới, không đọc memory từ các lượt
trước hoặc tự nạp lại lịch sử vào model. Lịch sử được lưu trong projection để UI
hiển thị; câu hỏi tiếp theo cần nêu đủ thông tin nghiệp vụ. API sources chỉ lấy từ
receipt RAG thực, không trích citation do model tự viết. needs_clarification hiện
false vì central trả text, chưa có output có cấu trúc để xác nhận cờ này.

Token caller nằm trong contextvar theo request; middleware loại các header riêng
trước khi chuyển cho Langflow. Các tool Data/ML dùng bearer đó khi gọi API chuẩn;
backend API vẫn kiểm quyền từng tài nguyên. Tool/cache từ execution khác bị từ chối.
Hai request đồng thời không dùng chung credential/context. Không thêm token vào
prompt, request JSON graph, metadata hay chat history. Native memory và cơ chế
send_message của agent được tắt trong context serving; ChatInput/Output tắt store
bằng tweak riêng cho mỗi lần chạy, không PATCH flow đã lưu.

## RAG

Giữ xác thực chuẩn `AI_SERVICE + X-Job-Context`. Không thêm nhánh giả user context.
Bật `FLOWBRIDGE_RAG_POSTGRES_ENABLED=true` và cấu hình
`FLOWBRIDGE_LANGFLOW_AI_CLIENT_ID`/`FLOWBRIDGE_LANGFLOW_AI_CLIENT_SECRET` trên backend
bằng service client Keycloak có grant AI_SERVICE trong DB và registry OIDC đúng.
Backend lấy service access token qua client_credentials; không ghi token vào flow.

Chat phải gắn request_id thuộc caller và đã có analysis task thật ở queued/running/
succeeded, cùng input_revision, feature_asof không ở tương lai, PR không cancelled.
Backend chọn task từ DB. Không tự tạo task hoặc dùng UUID demo dùng chung. Thiếu
task/credentials thì RAG không được đăng ký và response có warning
`RAG_REQUIRES_AUTHORIZED_ANALYSIS_JOB`; Data/ML vẫn dùng bearer human.

Khi đọc lại lịch sử/replay, backend kiểm owner/grant/PR, các request/order quan sát
từ API và kiểm lại nguồn RAG với ACL/version hiện tại; nguồn thu hồi không được trả
lại qua lịch sử. Đây là chat gắn nghiệp vụ PR, chưa phải RAG tự do theo user context.

## Retry và lỗi

Turn được commit ở trạng thái running trước external call; transaction được đóng
trong lúc Langflow/model chạy. Cùng actor+key và body trả lại kết quả đã lưu sau
kiểm quyền; body khác409. Một session chỉ có một turn running. Kết quả lỗi được lưu,
cùng key không chạy model lần nữa. Nếu process chết hoặc mất kết nối khi chưa rõ
kết quả, turn giữ running và retry trả409 ANALYSIS_IN_PROGRESS. Không tự reset,
retry hoặc chạy nền. Operator đối soát turn bằng DB tooling hiện có trước quyết
định tạo lượt mới; key mới là một yêu cầu thực thi mới có thể phát sinh chi phí.

401 cần đăng nhập/refresh lại,403 thiếu quyền,404 session/tài nguyên không thuộc
caller,503 bridge/model/config chưa sẵn sàng,504 timeout. Backend không trả raw
error Langflow/IdP. Sau lỗi không rõ kết quả, không tự cấp key mới để retry ngầm.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_chat_backend.py -q
.\langflow\.venv\Scripts\python.exe -m pytest langflow/tests/test_sso_serving.py -q
```

Backend suite dùng SQL thật trên SQLite bộ nhớ và HTTP mock, gồm introspection,
policy, ownership, durable retry/failure, history/pagination và grant/resource
revocation. SQLite không chứng nhận advisory lock PostgreSQL. Langflow suite dùng
ASGI và đường Internal A2A upstream, không gọi model. Không tự tải dependency hoặc
gọi OpenAI để nghiệm thu. Login thật, PostgreSQL migration/locking và hội thoại
model cần kiểm tích hợp riêng trên runtime đã cấu hình.

## Trạng thái kiểm tra tại máy ngày18/10/2026

- Suite backend liên quan:104 passed,1 failed;11 test chat mới đều đạt. Lỗi duy nhất là đối chiếu snapshot
  `ai-tools-api.yaml` với `../ho-so-olp/03-api/contracts/ai-tools-api.yaml`; snapshot
  hiện tại nguyên byte so với HEAD2464997 và HEAD cũng lệch bản ngoài repo.
  Không sửa contract hoặc bỏ assertion để làm test pass.
- Suite Langflow/tool/ver2/ver3 offline:142 passed, gồm7 test serving mới. Venv
  Langflow thiếu pytest-asyncio; đã dùng plugin có sẵn ở venv backend qua runner
  ignored `.cache/run_langflow_sso_tests.py`, không cài/sửa môi trường. Lệnh test
  trực tiếp serving ở trên chạy độc lập; chạy cả suite cũ cần plugin asyncio.
- SQL offline `alembic upgrade 0003_core_knowledge:head --sql` sinh thành công;
  không áp vào DB. Ruff và diff whitespace được kiểm riêng.
- Ngoài sandbox: Langflow7860vàbackend test8001đang chạy; API/Keycloak/clients
  tại các cổng local đã biết chưa chạy. `.env` chưa có OIDC hoặc bridge secrets.
  Có profile native cũ `.cache/identity-core-verification` (IAM19444/API18082),
  `.cache/identity-final` (IAM18443/API18080), không tự reset hoặc dùng tài khoản
  fixture để bootstrap quyền. Kết nối DB từ Settings bị timeout khi kiểm read-only.
- Chưa apply migration, ghi credential local, khởi động/dừng runtime, gọi model,
  commit/push hoặc nghiệm thu login thật. Muốn bật kết nối thật cần khôi phục DB,
  chạy profile IAM hiện có, cấu hình key/secret như trên rồi chạy profile serving.
