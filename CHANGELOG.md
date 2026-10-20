# Changelog

## Unreleased — Dọn repository và thống nhất hướng dẫn

- Sửa tra cứu danh mục với chuỗi mã + tên + đơn vị (ví dụ DEMO-P03), kiểm
  mô tả/quy cách còn lại để không tự chọn khi mâu thuẫn; dùng lại được lựa chọn
  để đọc nhà cung cấp. Bỏ cảnh báo biểu đồ rỗng khi chỉ xác nhận sản phẩm.
- Giữ nội dung trả lời khi dữ liệu bảng/biểu đồ không hợp lệ hoặc dịch vụ trả
  lỗi; loại phần trình bày sai qua validator và cô lập lỗi render ở frontend.
- Docker chat bật ML/RAG mặc định trong launcher, build và API runtime;
  hỗ trợ `--no-ml --no-rag` để tắt khi cần.
- Bỏ bước chọn PR khi chat với agent Tri thức. Hội thoại không gắn PR tra tài liệu
  global đã xuất bản bằng danh tính người dùng có EMPLOYEE, giữ kiểm ACL/thu hồi nguồn;
  hội thoại cũ có PR tiếp tục dùng job/ngữ cảnh đã được kiểm quyền.
- Sửa đăng nhập frontend Vite cổng 5174: đăng ký callback/origin local chính xác
  trong Keycloak và proxy API qua Vite; có helper cập nhật client cũ, giữ PKCE,
  callback 8761 và không mở rộng CORS backend.
- Thu gọn frontend theo tỷ lệ ChatGPT: header 52 px, sidebar 248 px và cột chat
  672 px; thay thẻ gợi ý lớn bằng nút nhỏ, giảm kích thước thư viện và form SSO.
  Dùng InputGroup, NativeSelect, Table chính thức của shadcn/ui, Tabs cho bộ lọc
  và Collapsible cho phần chi tiết; giữ bảng màu và các luồng API hiện có.
- Gom ảnh, bản lưu trang web và thử nghiệm thiết kế không dùng vào `web/ref/`,
  loại khỏi Git/Docker; chuyển ảnh QA vào cùng thư mục. Bỏ component chưa dùng,
  bật kiểm tra TypeScript cho biến/tham số thừa và sửa hướng dẫn frontend.
- Dựng lại frontend theo cấu trúc vùng của bản lưu ChatGPT: sidebar nhóm rõ ràng,
  cột đọc căn giữa, composer gọn tự mở rộng, thư viện agent có bộ lọc, bảng/biểu đồ
  và panel hoạt động cùng một hệ khoảng cách. Đồng bộ giao diện web và Keycloak,
  dùng nền trung tính và font Be Vietnam Pro; giữ logic SSO/API/SSE hiện có.
- Docker chat bổ sung node JSON frontend còn thiếu trong flow ver2 khi khởi động,
  dùng backup và kiểm concurrent edit hiện có, giữ nguyên nhánh đã được chỉnh.
- Gộp hướng dẫn Docker/chat vào `HUONG-DAN-CHAY.md`, thiết kế SSO/Identity vào
  hướng dẫn tương ứng, và tài liệu RAG vào `docs/RAG.md`.
- Bỏ checkpoint, plan cũ, báo cáo nghiệm thu theo lượt và thiết kế RAG đã thay thế.
- Gỡ backend Langflow test dùng danh tính local, launcher/kết nối/test phụ thuộc,
  worker đăng nhập thử nghiệm và runner chat trực tiếp. Chat ứng dụng dùng SSO.
- Xóa credential test/cổng backend cũ khỏi flow ver3; cập nhật manifest đóng gói
  và liên kết tài liệu. Giữ unit, SQL integration và E2E của hệ thống.
- Sửa kiểm tra schema seed để chỉ đếm baseline 27 bảng; các bảng vận hành chat/Hub
  đăng ký chung metadata không làm sai kết quả kiểm baseline.

## 2026-10-19 — Agent Hub backend và Activities

- Triển khai opt-in 12 API từ hợp đồng Agent Hub: owner ACL, revision/ETag,
  idempotency, queue PostgreSQL, messages/results, snapshot cursor, cancel và SSE replay.
- Thêm migration0006 và pgcrypto cho credential forwarding; worker không tự chạy
  lại model khi gián đoạn. API chat/bridge cũ và graph export giữ nguyên.
- Bridge agent-hub-v1 xuất JSON node frontend cùng activity trực tiếp từ lời gọi
  agent/tool: span, agent, tool, thời gian và lỗi đúng vị trí. Selection theo run,
  phụ thuộc vẫn do Langflow; web/app nhận event để tự dựng giao diện.
- Contract1.0.1 bổ sung PR context tùy chọn và as_of=null khi upstream không biết;
  adapter chuyển partial/questions/decimal/source/percent stacking một cách xác định.
- Hướng dẫn cấu hình, migration, giới hạn và test offline tại docs/AGENT-HUB.md.

## Unreleased

- Thêm khung chat Trợ lý AI vào client SSO: EMPLOYEE gửi qua backend, xem lịch sử,
  chọn PR, khôi phục phiên bằng check-sso sau reload. Retry dùng đúng key/body,
  không tự chạy model lại hoặc đưa Langflow credential xuống browser. Kiểm UI
  desktop/mobile, quyền, literal text và lỗi mạng bằng Playwright.

- Thêm overlay Docker chat trên stack dev chuẩn: lệnh chat-docker.cmd quản lý
  Keycloak/API/Langflow/client; sao lưu SQLite bằng backup API, giữ encryption key
  và named volumes. Tự tạo secret bridge/native API, không đưa user token vào config;
  migration chạy bằng action riêng, optional ML/RAG dùng cache model read-only.
  Startup nâng metadata ver2 cũ bằng graph migration có sẵn qua API key nội bộ,
  backup/rollback và giữ cấu hình model. Nghiệm thu PKCE/OTP→chat→Data/history thật
  có config opt-in riêng; fixture cleanup chặn lượt chạy chưa rõ kết quả.

- Nối API chat/history hiện có tới Langflow ver2 bằng adapter phía backend và
  caller context riêng xuyên native Internal A2A. Thêm migration0005 cho lịch sử,
  ownership và chống thực thi trùng; kiểm lại grant/tài nguyên/nguồn khi replay.
  Profile start-sso giữ credential ngoài prompt/metadata, bỏ memory native và
  override kết nối test8001 theo request. RAG cần task thật và AI_SERVICE.
  Giữ migration knowledge0004 đã công bố từ nhánh minnhungdev_features để nối
  tiếp Supabase hiện có; không sửa revision bằng stamp hoặc chạy lại SQL0004.

- Thêm trang quản lý Markdown RAG trong React hiện có cho ADMIN: tạo/sửa có lịch sử, job đồng bộ bền vững, xuất bản nguồn/vector trong cùng transaction, retry/reindex và xóa tài liệu chưa được báo cáo trích dẫn. Migration 0004 bổ sung bảng quản lý/guard; tính năng bật riêng bằng cấu hình, giữ retrieval Langflow theo cutoff và không đổi ML.

- Thêm custom component và flow Langflow chỉ truy hồi RAG qua bearer Keycloak + JobContext; cấu hình service credential ở server, xuất đoạn trích/citation, môi trường Langflow riêng và helper kiểm thử. Không gọi ML hoặc LLM trong flow này.
- Thêm hợp đồng thiết kế Agent Hub Web/App 1.0.0 trong contracts/: 12 operation,
  JSON Schema kết quả/sự kiện 1.0 theo mẫu người dùng, bảng và biểu đồ dùng chung
  dataset, cột chồng/100%, tiền decimal, SSE, idempotency/revision, ACL và mapping
  Langflow tự gọi agent phụ thuộc. Có ví dụ và hướng dẫn; chưa triển khai endpoint,
  không sửa workflow/runtime và không chạy kiểm thử theo yêu cầu người dùng.

- Khoanh sửa dependency định danh ML→Data ở ver2: tách câu hỏi con khỏi mục tiêu
  tư vấn NCC; chỉ xác nhận lookup hoàn tất bằng receipt thật khi lỗi duy nhất là
  handoff ngoài phạm vi. Giữ các lỗi/quyền/nhập nhằng và các nhánh khác; user tự test.

- Thêm 11 node xử lý giao tiếp ver2, chỉ nối lại dây và giữ nguyên mọi node cũ:
  adapter A2A chuẩn hóa task, gắn receipt thật và đóng dependency đã xong; kiểm
  reply + Type Convert native. Không thêm model định dạng hoặc nới ngân sách.

- Sửa ver2 gọi ML khi thiếu lượng/ngày: runtime yêu cầu tra sản phẩm hoặc hỏi bổ sung
  trước khi xếp hạng. Tách lỗi chạy agent và lỗi reply, giữ trace và mã lỗi cụ thể;
  không diễn giải thiếu đầu vào/lỗi A2A thành backend ML ngừng hoạt động.

- Sửa định danh RuntimeA2A ver3: tên/ID riêng và source khớp component đăng ký,
  tránh editor nhận nhầm thành A2AAgent native làm mất route. Controller nhận ba
  dịch vụ qua ba cổng riêng, bỏ tìm tên/metadata trong list tool chung; repair giữ cấu hình và
  backup trước khi đổi. Không chạy hội thoại/test OpenAI theo yêu cầu người dùng.

- Thêm FlowBridge ver3: clone bốn flow ver2 hiện tại, lưu bản clone nguyên trạng
  trước khi phân tầng; giữ nguyên API tool/preset/credential và ver2. Cổng chat có
  schema, state theo user/flow/session với CAS/TTL, controller hữu hạn và ba nhánh
  ServiceBridge không LLM; runtime giữ evidence/UUID, renderer dùng ToolResult.
  Kiểm hồi quy offline và engine native A2A/ML không OpenAI; dữ liệu ML demo vẫn
  có thể insufficient_data. Thêm export, verifier và khôi phục có guard.

- Bật RAG Supabase/pgvector trên backend Langflow test: token local ánh xạ vào
  AI_SERVICE và JobContext thật, giữ policy/cutoff và toàn bộ session read-only.
  Thêm chuẩn bị profile, nối/khôi phục riêng tool RAG và tự nối lại qua launcher;
  dùng chunk có sẵn, không import/reindex. 23 unit tests đạt; truy hồi thật200 với
  3chunk đúng NCC, guards token/context đạt và DB không đổi. Không gọi OpenAI.

- Sửa OpenAI400 additionalProperties ở Structured Response ver2 khi Agent con không
  có tool: object động dùng function_calling không strict và Pydantic local; giữ
  ToolResult/JSON lồng nhau, schema/prompt/preset/kết nối. Thêm repair riêng3Agent,
  cập nhật installer/export;9 kiểm thử offline đạt, không gọi OpenAI để test.

- Thêm launcher backend test riêng tại127.0.0.1:8001 cho Langflow ver2: token local,
  reuse active human/grant thật, API allowlist và PostgreSQL read-only; giữ SSO backend
  thường/v1. Nối9 node Dữ liệu/ML qua Credential riêng, backup/khôi phục connection.
  Unit7pass/API smoke đạt; ML vẫn insufficient_data khi thiếuq_ref, chưa gọi OpenAI.

- Sửa Luna function tools trong Langflow ver2: thêm endpoint/reasoning chỉnh được vào lớp kế thừa Agent chuẩn, mặc định Chat Completions + none, chặn cấu hình bất tương thích trước request và tắt SDK retry mặc định. Giữ preset/dây nối/ID và workflow v1; kiểm serialization offline, chưa gọi OpenAI để nghiệm thu lại.

- Thêm project Langflow độc lập FlowBridge - ver2: Agent trung tâm và ba agent Dữ liệu/ML/RAG qua A2A Internal native; model/prompt/schema chỉnh được, giữ API adapter canonical và workflow cũ. Chưa chạy inference/OpenAI hoặc nghiệm thu backend.

- Đồng bộ đường dẫn Langflow với package `backend/flowbridge/` khi gộp main; giữ cấu hình và hợp đồng node hiện có.
- Việt hóa node Langflow, chuẩn hóa prompt/description, hợp nhất tham số câu hỏi với mặc định/khóa, output JSON chung và hợp đồng chuyển dữ liệu giữa 10 tool. Thêm schema/ví dụ theo contract và 20 tình huống chưa chạy; chưa nghiệm thu lựa chọn tool của model.

- Thêm flow Langflow với OpenAI model tùy chọn, 10 node tác vụ chỉnh tham số/Input JSON/description và 3 tầng Database/ML/RAG; hỗ trợ khóa input, JSON output, giới hạn lượt gọi, cache, credential local và câu hỏi mẫu. Chưa nghiệm thu model/backend theo yêu cầu hoãn test.
- Gom setup Langflow local vào `langflow/`, giữ dữ liệu/flow mẫu và thêm `start.cmd` để mở bằng nhấp đúp trên Windows.
- Bỏ lớp thư mục `src` của backend: package Python chuyển sang `backend/flowbridge/`; cập nhật build, Alembic, script đồng bộ contract và tài liệu. Tên import `flowbridge` giữ nguyên.

- Thêm giao diện nghiệp vụ React tại client SSO: catalog, yêu cầu mua hàng nhiều dòng, xem PO/đề xuất và phản hồi nhà cung cấp bằng token. Quyết định duyệt chờ workflow; không giả tác động email/phân tích. Thêm build esbuild, lock React/React DOM và suite kiểm thử browser UI riêng.

- Sửa Swagger của API PostgreSQL dùng server URL tương đối theo địa chỉ phục vụ OpenAPI, thay host ví dụ cổng8000. Try it out dùng đúng cổng dev/proxy prefix; giữ snapshot contract và cơ chế bearer authentication.

- Thêm profile APISIX standalone cho hai API Identity core: TLS/CA, OIDC bearer introspection, public allowlist, network isolation, proxy trust tường minh, correlation và limits. Thêm runner nghiệm thu Docker riêng với cleanup/recovery và HTTP/browser assertions; các receiver core khác chưa được public. Nghiệm thu từ archive sạch:296unit container,21HTTP,1browser IAM/API và log/cleanup đạt.
- Bổ sung response413 cho hai Identity operation trong contract chuẩn/snapshot, giữ schema ErrorResponse và đồng bộ provenance. Image APISIX3.19.0 khóa tag+digest; không thêm Python dependency hoặc migration.

- Khôi phục migration `0003_core_knowledge` và SQL gốc từ nhánh nhóm, gỡ lệch source/DB mà không migrate/stamp/seed. Fixture SQL/SSO kiểm chính xác Alembic heads thay hằng 0002; 19 SQL nghiệp vụ và 8 unit tooling/migration đạt, có đối chiếu catalog Core read-only.
- Thêm container development/test: multi-stage backend runtime/tooling/unit, Compose Keycloak/backend/clients và runner lifecycle/fixture/cleanup; image tag+digest pin, loopback ports, secrets DB tách khỏi IAM/browser. Đã build/chạy trên Docker Linux, 280 unit và SQL Identity pass; full SSO/business container còn cần nghiệm thu riêng sau khi gỡ lệch migration. Không tự migrate/seed hoặc tải model.
- Sửa build context thiếu migration/config và lệnh down bỏ sót client ở profile tùy chọn; down/up giữ state, hướng dẫn recovery khi restart các container chung namespace mạng.
- Thêm NumPy/rank-bm25 cùng version đã khóa vào nhóm dev để unit không cần full model extras; graph142 không đổi. Thêm kiểm profile/secret boundary/recovery/revision guard và tài liệu cài/chạy/kiểm container; giữ native SSO launcher/client loopback mặc định.

- Sửa provenance Core/draft bằng sync/checksum có kiểm semantic/shared schema; giữ canonical 03-api và wire Core 0.2.0. Identity OpenAPI bổ sung URI/correlation/Retry-After 409; URI public config được validate.
- Tách accounts/bootstrap sang SQL adapter và transactional audit/outbox do Identity sở hữu; giữ schema/lock/guard/retry. Gom OpenAPI composition/cache app/Identity/ML/RAG, từ chối collision; thêm parity/regression và SQL rollback acceptance.

- Thêm hai API Identity core khi cấu hình binding tenant/application tường minh: config public và human principal từ introspection + live DB grants; namespace OpenAPI riêng, token scope và service-account/client binding được kiểm lại. Không tự nâng ADMIN/quyền phòng ban thành quyền toàn hệ thống.
- Thêm UserContext qua IAM-issued access token và giao quyền service/user; JobContext boundary fail closed khi chưa có registry production. Profile core mới bắt buộc OTP, scope identity:read và PKCE; thêm unit/SQL/browser tests, mapping gateway tùy chọn `--include-identity` và hướng dẫn/ADR. Không migration hoặc dependency mới.

- Thêm Prediction Service bật bằng `FLOWBRIDGE_ML_ENABLED`: 19 features, context có provenance/cutoff, TabICLv2 2.2.0 và batch inference ngoài event loop; model readiness riêng, không tải checkpoint trong HTTP/startup.
- Thêm 5 endpoint `/api/v1/ml/*` và `/internal/v1/ml/*` cho scenario, ranking, chấm/lưu PO và đọc prediction/rủi ro hiện hành. Tái sử dụng IdP/role/ownership, PostgreSQL và idempotency; rủi ro PO là MAX các item còn mở, trả đúng item/deadline và trạng thái thiếu dữ liệu.
- Thêm optional dependency `ml`, CLI chuẩn bị/kiểm tra artifacts, typed HTTP client, custom component Langflow và hướng dẫn cấu hình. Giữ API AI 0.4.1 cũ/RAG; không migration hoặc scheduler/cảnh báo. Điểm ML chưa hiệu chuẩn; context synthetic không dùng production.

- Thêm `POST /internal/v1/tools/retrieve-knowledge` bật bằng `FLOWBRIDGE_RAG_DEV_ENABLED` cho loopback development/test: RAG thật trên fixture, model dùng lại theo lifespan, worker không chặn event loop, giới hạn một lượt tìm kiếm và lỗi chuẩn. Giữ API PostgreSQL và OpenAPI có xác thực khi flag tắt; không khôi phục các API sample đã bỏ trên main.
- Thêm hướng dẫn Postman/PowerShell và kiểm thử HTTP/lifecycle; chế độ này chưa dùng caller/ACL production, không tự tải model hoặc lập chỉ mục.
- Thêm RAG local bằng CLI: 6 hồ sơ Markdown, Qwen3-Embedding-0.6B pin revision, Chroma cosine + BM25/RRF, chỉ mục theo thế hệ, nguồn/version/checksum/heading/reviewer và lọc document/supplier/known_from. Reranker mặc định tắt.
- Thêm extra `rag`, lockfile, kiểm thử và tài liệu sử dụng/báo cáo; HTTP development được bổ sung ở bước tiếp theo; chưa nối Langflow, document store hoặc ACL production.
- Tích hợp Keycloak SSO thật qua SDK code+PKCE và backend introspection; thêm registry client/human/service, CA trust, CORS allowlist, directory verified-link và bootstrap transaction mặc định rollback. Thêm realm config/native local launcher, hai client browser và E2E Keycloak+HTTP+Supabase không override identity. Không khôi phục sample identity hoặc lưu mật khẩu ở DB; không có migration mới.

- Chỉ giữ API PostgreSQL/Supabase: bỏ nhánh sample, fixture runtime, API core mô phỏng, công cụ/ví dụ/test sample và dependency python-multipart. Chuyển 4 payload cần cho integration vào tests; giữ contract/auth/HTTP checks cho API database. Gateway mapping chỉ xuất API app/AI và tách nhãn admin/internal/public. Bỏ biến cấu hình FLOWBRIDGE_SAMPLE_*; cập nhật cách chạy và các dependency còn thiếu.

- Chuyển API nghiệp vụ sang PostgreSQL/Supabase khi cấu hình DSN và tắt sample: repository async, quyền theo owner/phòng ban và grant active, transaction/outbox, PR/analysis/proposal/PO/receipt/confirmation/admin persistence. Thêm migration `0002_api_idempotency` để replay request an toàn và chống gọi trùng đồng thời; giữ sample mode độc lập.
- Thêm adapter bearer introspection upstream RFC 7662; chưa cấu hình IdP thì từ chối truy cập, không giả actor. Workflow approval, provision identity, chat/model/document engine trả 503 khi chưa có adapter. Thêm suite integration Supabase chọn tường minh, test commit qua kết nối độc lập và dọn fixture riêng; không thay dữ liệu demo có sẵn.

- Tích hợp script Supplier Assistant: baseline Alembic tạo 27 bảng/293 cột, 56 FK, 83 CHECK và 3 helper cùng RLS; thêm đủ ORM mapping, wrapper seed async và báo cáo CSV. Đã áp dụng revision `0001_supplier_schema`, nạp và đọc lại 25.422 dòng trên Supabase; `alembic check` đạt.
- Thiết lập Base ORM và Alembic async dùng chung Settings/engine, SSL và tùy chọn NullPool; thêm cấu hình Supabase theo trường riêng với password được encode tự động, script `SELECT 1` và hướng dẫn migration/seed. Chưa thêm model nghiệp vụ, revision hoặc seed của nhóm.
- Gom adapter fixture nhỏ của Data/Files/Workflow/Events vào service cùng nhóm chức năng, bỏ wrapper Identity chỉ chuyển tiếp và dùng chung phân trang cho Supplier Assistant. Giữ nguyên hợp đồng API, guard quyền/revision/token và cơ chế sample không lưu dữ liệu.
- Bỏ khai báo jsonSchemaDialect tùy chọn ở ba contract và snapshot để Swagger dùng dialect mặc định OpenAPI 3.1; đồng bộ checksum, giữ nguyên endpoint và schema dữ liệu.
- Đồng bộ app/AI OpenAPI lên 0.4.1 theo schema Supplier Assistant 27 bảng: UUID local actors, PR nhiều dòng, NUMERIC(18,4), owner/department grants, AI tự lập/gửi proposal, PO schedules/receipts và confirmation revisions. Đây là thay đổi không tương thích với DTO ứng dụng 0.1.0; Open-Core 0.2.0 giữ nguyên.
- Thêm 40 operation ứng dụng và 6 AI tools sample, chung Swagger với 28 operation core. Tách admin/internal, từ chối public manual proposal; endpoint predict trả insufficient_data/score null khi chưa có model. Không tích hợp DB/IdP/workflow/email/ML thật.
- Thêm provenance/checksum, script đồng bộ snapshot app/AI, request examples và kiểm tra HTTP `--scope all`; DTO request/response dùng cùng contract và có tests cho UUID/decimal/19 X/actor/revision/null/sample guard.
- Thêm chế độ API mẫu bật tường minh trong development/test: 28 operation Identity, Data/Storage, Workflow và events/notifications/audit; response đúng hợp đồng v0.2.0, chưa persistence hoặc xác thực OIDC thật.
- Thêm multipart upload, download bytes tổng hợp và metadata/hash nhất quán; dữ liệu fixture có ID dùng chung, request ghi không thay đổi fixture.
- Thêm mapping Gateway từ contract, ví dụ gọi đủ operation, Swagger schema sample và script kiểm tra HTTP có assertion. APISIX/engine thật để giai đoạn sau.
- Từ chối sample trong production hoặc khi có DSN; thêm cấu hình giới hạn upload và HTTP/contract tests không I/O engine.
- Thêm python-multipart 0.0.32, PyYAML 6.0.3, jsonschema 4.26.0 và referencing 0.37.0 qua manifest/lockfile; đóng gói contract cùng package, ví dụ và script trong source archive.
- Thêm lỗi API chuẩn, correlation ID có kiểm tra định dạng và cô lập request; che chi tiết validation/exception nhạy cảm.
- Thêm `/_ops/live` và `/_ops/ready` tách khỏi schema nghiệp vụ; readiness có deadline cấu hình, trả 503 khi bootstrap không có DB và chỉ chứng nhận runtime backend.
- Khai báo Starlette 1.8.0 trực tiếp để dùng giao diện ASGI đã có trong graph, không đổi phiên bản package upstream.
- Thêm app factory, cấu hình có validation, lifespan quản lý HTTP client/engine và session theo request; chạy module hỗ trợ cấu hình host/port/log level.
- Thêm unit test dữ liệu mock, cấu hình và vòng đời tài nguyên; chưa tạo/kết nối DB thật theo yêu cầu của nhóm. Đóng gói test trong source archive.
- Khởi tạo manifest Python 3.12, cấu hình uv/Ruff/pytest và package tại `backend/src/flowbridge`.
- Áp dụng GPL-3.0-or-later theo lựa chọn của nhóm; giữ LICENSE GPL v3 toàn văn.
- Bổ sung template môi trường, ignore rules và cấu hình LF.
- Áp dụng quy tắc code/review/license/test/release theo thể lệ hiện tại.

Chưa có release công khai. Runtime hiện dùng Supabase và Keycloak SSO với cấu hình riêng; sample API đã bỏ. Đã có adapter ML/RAG local tùy cấu hình; workflow/email, ACL tài liệu production và nghiệm thu production vẫn còn các task tích hợp riêng.

### PostgreSQL knowledge store 2026-10-16

- Add/adopt three Core tables with immutable publication and byte-accurate chunk guards.
- Add pgvector exact-cosine retrieval with existing BM25/RRF, per-version atomic generations, live authorization and stable citations.
- Wire synchronous knowledge tools to verified AI_SERVICE and database JobContext; retain the separate fixture development mode.
- Add operator import/index/inspect commands, setup documentation and bounded SQL validation evidence.
