# Checkpoint vận hành FlowBridge

- Ngày: 2026-10-20, múi giờ Asia/Saigon.
- Yêu cầu hiện tại: khởi động backend theo `chat-docker.cmd up` với cấu hình sẵn có.
- Git: branch `hoanganhdev_features`, HEAD `b944ad1ba8ea3bc914bf21449aa5a0a3f8cfec6f`; working tree sạch trước thao tác.
- Không có `docs/PLAN-OPENCORE.md` trong checkout hiện tại; chưa xác định task CODE từ plan. Không sửa mã ứng dụng, dependency hoặc migration.
- `chat-docker.cmd doctor` ngoài sandbox: PASS cấu hình Compose và Linux Engine. Truy cập Docker trong sandbox không thành công.
- `chat-docker.cmd up` hoàn tất, exit code 0 (exec session `28133` đã kết thúc): build prepare/check-db/API/client/Langflow thành công; DB read-only connectivity và migration head PASS; Keycloak, Langflow và API healthy; client chạy và Compose wait thành công.
- Launcher nâng cấp flow ver2 data/coordination/protocol thành công, giữ model và credential presets theo thông báo của launcher. State/volume được giữ; không chạy migration/reset database nghiệp vụ.
- HTTP đã kiểm tra: `http://127.0.0.1:8761/`, `http://127.0.0.1:18082/docs`, `http://127.0.0.1:18082/openapi.json` đều trả 200. `chat-docker.cmd status` xác nhận bốn dịch vụ đang chạy.
- Trạng thái yêu cầu: hoàn tất khởi động backend ở chế độ mặc định, chưa bật `--ml --rag`. Bước tiếp theo của người dùng: mở UI và đăng nhập SSO để sử dụng.
- Không có kiểm thử chat/model hoặc đăng nhập SSO trong yêu cầu vận hành này.

## Yêu cầu tài khoản test quản trị

- Người dùng yêu cầu thêm một tài khoản mặc định có quyền cao nhất để tự test.
- Dự kiến: `flowbridge-test-admin`, human roles ADMIN/EMPLOYEE/APPROVER/WAREHOUSE; department-scoped grants thuộc phòng `TEAM-TEST`. Mật khẩu ngẫu nhiên dự kiến lưu trong `.cache/chat-stack/flowbridge-chat/test-admin.json`, không đưa vào Git/chat.
- Working tree đã có thay đổi của người dùng tại `.dockerignore`, `README.md`, `infra/chat/compose.yaml`, `logo-flowbridge.png`, `web/`; không sửa các mục này. HEAD vẫn `b944ad1`.
- Stack đã dừng từ trước; đã bật lại các container hiện có bằng `docker start`, không rebuild thay đổi frontend của người dùng.
- Kiểm tra DB read-only: có ADMIN active và phòng TEAM-TEST, nhưng ba ADMIN cùng issuer local đều trả NOT_FOUND khi resolve subject tại Keycloak hiện tại. Hai lần chạy helper dừng trước bước tạo user/cấp quyền. Chưa có tài khoản mới hoặc grant được ghi.
- Auto-review từ chối thao tác dùng bản ghi ADMIN legacy `FlowBridge dev admin` làm granted_by sau khi xác minh IdP thất bại; lý do: vượt kiểm tra danh tính hiện tại để cấp quyền rộng chưa được người dùng cho phép rõ ràng. Không thực thi lại hoặc dùng cách vòng qua từ chối này.
- Helper trong `.cache/create-test-admin.py` đã bỏ fallback legacy, giữ guard yêu cầu ADMIN được IdP xác minh. Bước tiếp theo cần phê duyệt rõ ràng quy trình khôi phục quản trị hoặc danh tính ADMIN hiện hành hợp lệ.

## Hoàn tất tài khoản test sau phê duyệt

- Người dùng đã đồng ý rõ ràng dùng bản ghi ADMIN cũ `FlowBridge dev admin` để cấp quyền cho `flowbridge-test-admin` trên môi trường test, đúng bốn role và phòng TEAM-TEST nêu trên. Điểm chặn trước đã được giải quyết bằng phê duyệt này.
- Helper chỉ cho phép sử dụng bản ghi legacy đã chỉ định khi truyền `--approved-legacy-admin` đúng UUID, đồng thời kiểm tra ADMIN vẫn active và thuộc issuer hiện tại. Danh tính đích được xác minh bằng KeycloakDirectory; không sửa chính sách xác thực hoặc mã backend.
- Tạo user Keycloak thành công. Bootstrap dry-run rollback thành công, sau đó apply commit thành công; app user ID `36e9cae9-b741-4233-b120-adcd54f9c12a`, grants_added=4. Đọc lại database xác nhận ADMIN/EMPLOYEE global, APPROVER/WAREHOUSE thuộc TEAM-TEST.
- Mật khẩu ngẫu nhiên nằm trong `.cache/chat-stack/flowbridge-chat/test-admin.json`; `git check-ignore` xác nhận file bị bỏ qua. Không đưa mật khẩu/token/OTP vào checkpoint hoặc output.
- Kiểm tra giao thức SSO thành công: mật khẩu được chấp nhận và chuyển đến trang thiết lập OTP lần đầu. Không đăng ký OTP thay người dùng, chưa hoàn tất phiên đăng nhập hoặc gọi model/API cần bearer mới.
- Script xác minh ban đầu không đọc được form HTML vì theme mới render React; đã điều chỉnh đọc loginAction trong kcContext, lần kiểm tra cuối exit 0 và password_accepted=true.
- Yêu cầu tài khoản test đã hoàn tất; người dùng mở UI, đăng nhập với mật khẩu trong file riêng và quét QR bằng Authenticator.

## Kiểm tra bản build lỗi trong Docker Desktop

- Người dùng gửi build ID `moh0u9xjrcohaa2o7ida2t8rs` của web runtime. `docker buildx history inspect` xác nhận lỗi ngày 2026-10-20 lúc 10:02 Asia/Saigon: `base name (${KEYCLOAK_IMAGE}) should not be blank`; build lúc đó chỉ truyền PLAYWRIGHT_IMAGE.
- Bản web runtime mới hơn `q2zh0zvyqvtay6u0l7ff51rzv` hoàn tất lúc 10:06:18 cùng ngày, có cả KEYCLOAK_IMAGE và PLAYWRIGHT_IMAGE. Compose hiện tại đã khai báo cả hai build args. Không cần sửa lại hoặc rebuild chỉ để kiểm tra bản ghi cũ.
- Kiểm tra trực tiếp hiện tại: Keycloak/API/Langflow healthy, client-a Up; UI `http://127.0.0.1:8761/` và Swagger `http://127.0.0.1:18082/docs` đều HTTP 200.
- Kết luận: lỗi lịch sử đã được xử lý ở lần build sau; không phải lỗi của dịch vụ đang chạy. Không thay đổi mã ứng dụng hoặc dữ liệu.

## Cập nhật nhánh JSON frontend của ver2

- Người dùng yêu cầu cập nhật flow đang chạy sau khi xác nhận source đã có `FrontendResponse-ver2-central` nhưng graph Docker chưa có node/dây nối. Giữ endpoint ver2; không chuyển sang ver3.
- Branch/HEAD vẫn `hoanganhdev_features` / `b944ad1`. Giữ nguyên các thay đổi sẵn có của người dùng.
- Sửa `scripts/upgrade_chat_flows.py` để dùng helper `add_frontend_response.prepare` khi central thiếu node; giữ node đã có và cơ chế backup/concurrent edit/rollback. Đưa helper vào `infra/chat/Langflow.Dockerfile`; cập nhật `HUONG-DAN-CHAY.md`, `CHANGELOG.md`.
- Build image Langflow thành công, Ruff và `git diff --check` PASS. Hash các graph/node/edge trước cập nhật lưu tại ignored `.cache/langflow-before-frontend-hashes.json`, không chứa credential.
- Session `4880` hoàn tất exit 0: Langflow healthy, updater PASS. Backup bốn flow ver2 ở `/var/lib/langflow/flowbridge-backups/20261020T034318193687Z` trong volume.
- Đối chiếu hash trước/sau PASS cho cả 9 endpoint ứng dụng: chỉ ver2 central thêm một node/một edge, mọi node/edge cũ và flow ứng dụng khác giữ nguyên. Langflow tạo lại ID các example dựng sẵn khi restart; không nhầm với mất flow ứng dụng. Các graph còn cùng ID ngoài central cũng giữ nguyên hash.
- Gọi API metadata có xác thực và dựng native `Graph.from_payload(...).topological_sort()` PASS; node và edge JSON đúng một bản. Chạy updater lần hai PASS `metadata already current`. API/Langflow/Identity healthy, client đang chạy.
- Hai lượt pytest sandbox (`52620`, `87744`) đã dừng, không có kết quả suite hoàn chỉnh. Lượt chẩn đoán đạt test bảo toàn export, sau đó faulthandler xác định kẹt tại Windows `asyncio` → `_fallback_socketpair` → `socket.accept`, trước khi graph thực thi. Chạy lại ngoài sandbox trong session `3733` hoàn tất exit 0: **50 passed, 8 dependency deprecation warnings in 52.67s** cho `test_frontend_response.py`, `test_frontend_delivery_statistics.py`, `test_hub_serving.py`, `test_sso_serving.py`.
- Ruff và `git diff --check` cuối PASS; hai snapshot hash đều được Git ignore. Không gọi model hoặc chạy migration nghiệp vụ. Cập nhật vận hành đã hoàn tất; bước tiếp là người dùng F5 canvas ver2 và tự thử hội thoại. Chưa commit/push.

## Dựng lại toàn bộ frontend theo bộ lưu ChatGPT

- Người dùng yêu cầu xem kỹ cấu trúc trong thư mục ChatGPT rồi làm lại toàn bộ frontend. Đã rà module trong `web/chatgpt.com/cdn/assets`, đọc CSS và các module thread/composer/message/page. HTML hội thoại chỉ có No Content; không giả nhận có ảnh chụp tham chiếu hoàn chỉnh.
- Chỉnh web app/sidebar/header/chat/composer/library, shared AuthShell và Keycloak login; dựng lại workspace.css; đồng bộ token CSS/JSON với nền trung tính và font Be Vietnam Pro đã có trong dependency. Không thêm dependency, thay backend hoặc sửa graph Langflow trong task này.
- Build TypeScript/Vite PASS; 9 unit tests PASS. Toàn bộ 12 E2E PASS (46.9s), gồm kiểm tra hình học cột đọc/composer ở 1440/1024/390/320px, auth forms, API idempotency và SSE recovery. Đã xem ảnh desktop/mobile/login/library/chat sau khi đổi font.
- `web/start.ps1` hoàn tất exit 0 (session `92618`), build web runtime và Keycloak theme thành công; cấu hình vẫn Agent Hub=true, ML=false, RAG=false. Compose wait PASS, Keycloak/Langflow/API healthy, client Up; updater báo metadata ver2 đã current, không chỉnh graph mới.
- UI `http://127.0.0.1:8761/` và Swagger trả HTTP 200. Browser kiểm thử fresh profile ban đầu bị ERR_CERT_AUTHORITY_INVALID vì chưa tin CA local. Đã dùng đúng cơ chế E2E của dự án (chỉ pin SPKI certificate của profile trong browser tạm, không ignoreHTTPSErrors/không sửa trust store hoặc TLS backend) để kiểm tra production: chuyển web → SSO thật thành công, form username/password hiển thị, font Be Vietnam Pro đúng, 0 page errors, không tràn ngang.
- Đã xem ảnh production `.qa/redesign-live-login.png` và `.qa/redesign-live-sso.png`; không đăng nhập tài khoản/không enroll OTP/không gọi model trong lượt browser này. Người dùng vẫn cần browser tin CA local theo docs/SSO.md.
- Đã hoàn tất yêu cầu dựng lại giao diện và triển khai local. Bộ ảnh xem thử bốn viewport nằm trong ignored `web/.qa/redesign-*.png`; tài liệu đối chiếu tại `web/DESIGN.md`. Chưa commit/push.

## Chẩn đoán agent Dự đoán/Tri thức bị khóa

- Người dùng hỏi vì sao chưa dùng được hai agent. Xác minh container API đang chạy FLOWBRIDGE_CHAT_ML=false, FLOWBRIDGE_CHAT_RAG=false, Agent Hub=true. Catalog backend đánh dấu prediction theo ml_enabled và knowledge theo rag_postgres_enabled + service client config, nên UI khóa đúng trạng thái backend.
- API general readiness 200; ML readiness 404 vì router chỉ được cài khi bật ML.
- Artifact local có TabICL checkpoint (110368038 bytes), context synthetic-smoke-v1 và Qwen3-Embedding-0.6B đúng revision 97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3 (có model.safetensors).
- Preflight read-only qua container: RAG schema ready, 18 published versions và 471 chunks. Service account flowbridge-ai tại IdP hiện tại subject 45fae7f0-2d81-44da-8d76-caad49b94622 được Directory xác minh, nhưng có 0 AI_SERVICE active grants trong DB.
- Chưa thay cấu hình/runtime hoặc cấp grant trong lượt chẩn đoán. Để dùng đủ: khởi động bằng web/start.ps1 -WithML -WithRAG, kiểm tra model readiness thực tế, cấp đúng AI_SERVICE cho service identity đã xác minh bằng ADMIN ứng dụng hiện hành. Tri thức cần PR/task hợp lệ; context ML hiện có là bộ synthetic dùng thử, chưa phải dữ liệu huấn luyện nghiệp vụ đã nghiệm thu.

## Dọn source web để chuẩn bị nộp thi

- Ngày 2026-10-20, Asia/Saigon. Yêu cầu: gom ảnh/tài liệu tham khảo và tệp không dùng trong web vào `web/ref/`, ignore toàn bộ và kiểm tra source sau khi dọn.
- Git vẫn `hoanganhdev_features` / `b944ad1`; giữ nguyên các thay đổi có trước. `web/` chưa được track từ đầu lượt; không commit/push hoặc thay index. `docs/PROJECT-RULES.md` và `docs/PLAN-OPENCORE.md` vẫn không tồn tại trong checkout; không tự tạo task CODE.
- Chuyển 14 JPG, `chatgpt.com/`, logo phụ `web/logo.png`, HTML xem thử màu, JSON token tham khảo, ghi chú `DESIGN.md`, `.qa/` và component popover không được import vào `web/ref/`. Giữ logo gốc `logo-flowbridge.png` và CSS token đang được ứng dụng/theme dùng.
- Thêm ignore `/web/ref/` ở gốc, `/ref/` và generated Keycloak dev resources trong `web/.gitignore`; chặn `web/ref/**` trong Docker context. Đổi đường dẫn ảnh E2E và hướng dẫn CA sang `ref/qa/`, cập nhật README/CHANGELOG và bỏ liên kết source tới tài liệu chỉ có local.
- Bật `noUnusedLocals`/`noUnusedParameters`; bỏ prop `compact` thừa ở Brand và tham số hội thoại không dùng trong fixture helper. Không sửa nghiệp vụ/API/DB/flow, không triển khai lại stack.
- `npm run build`: PASS; `npm test`: 9 passed. Playwright trong sandbox không kết thúc khởi động server, đã dừng; chạy lại ngoài sandbox: **12 passed (1.4m)**, exit 0, gồm SSO doubles, idempotency/SSE, lỗi, bảng/biểu đồ và mobile. Không dùng tài khoản thật/gọi model.
- Sao 76 file source web theo danh sách Git không bị ignore, logo và contract fixtures vào ignored `.tmp/web-source-check-0sh4phub`; không sao `ref/`, output, generated dev resources hoặc dependency. Dùng junction tới dependency đã cài để build bản sao: PASS, exit 0. Đây là kiểm tra source không phụ thuộc ref, không phải chứng nhận cài dependency mới hoàn toàn.
- Lệnh thử gỡ dependency thừa bằng npm offline dừng do thiếu artifact trong npm cache; manifest/lockfile không thay đổi. Không cài mới/nâng cấp dependency.
- `git check-ignore`, kiểm tra danh sách Git, rà import ảnh/preview tham khảo, quét chữ ký private key/token trong source web và `git diff --check`: PASS. Không có file ref, node_modules, dist, test-results hoặc generated Keycloak dev resources trong danh sách source web của Git.
- Hoàn tất phạm vi dọn web và kiểm hồi quy frontend. Chưa audit toàn bộ backend, release hoặc thể lệ cuộc thi; không coi kiểm thử mock là bằng chứng tích hợp dịch vụ thật. Không commit/push.

## Bật đầy đủ ML/RAG theo kỳ vọng dùng cả hai agent

- Người dùng làm rõ rằng họ kỳ vọng Docker đã bật hai agent. Đã giải thích lần chạy trước dùng chế độ cơ bản và bắt đầu bật đầy đủ bằng `web/start.ps1 -WithML -WithRAG` (session `73260`). Docker đang build optional ML/RAG dependencies; phải kiểm tra outcome trước khi chạy lại. Đủ dung lượng đĩa tại thời điểm bắt đầu.
- Đã xác minh ADMIN `flowbridge-test-admin` hiện tại qua IdP và grant ADMIN active trong DB; xác minh target service account thuộc client flowbridge-ai. Bootstrap AI_SERVICE dry-run rollback rồi apply thành công (session `55602` exit 0), user ID service `3a4e019e-d8fb-4a7f-ab85-52d4d34ea6f0`; đọc lại xác nhận đúng một global AI_SERVICE active grant. Không dùng ADMIN legacy.
- Bước tiếp theo: đợi build/up, kiểm ML readiness/model thật, RAG với task hiện có hợp lệ và catalog khả dụng; không chỉ dựa vào healthcheck chung. Chưa gọi LLM hoặc tạo dữ liệu nghiệp vụ mới.

### Trạng thái bàn giao sau khi bật ML/RAG

- Build/up đầy đủ session 73260 đã hoàn tất exit 0; API nhận ML=true, RAG=true và Agent Hub=true. Đã cấp đúng AI_SERVICE như trên.
- Trước yêu cầu dừng test: ML readiness 200, suy luận thử HTTP 200; context synthetic-smoke-v1 chỉ dùng test. Lượt truy xuất RAG đầu trả 504 khi CPU tăng cao; chưa xác nhận truy xuất thành công.
- Thêm OMP_NUM_THREADS, MKL_NUM_THREADS và OPENBLAS_NUM_THREADS cho API, mặc định 4 qua FLOWBRIDGE_ML_CPU_THREADS, nhằm giảm dùng CPU quá mức khi nạp/chạy model local. Khởi động lại API/client không build (session 35811) hoàn tất exit 0; cả hai Healthy.
- Người dùng yêu cầu để họ tự test. Dừng kiểm thử chức năng tiếp theo; chỉ xác nhận lần restart hoàn tất. Không tuyên bố RAG đã đạt end-to-end. Lệnh bật đầy đủ lần sau: .\web\start.ps1 -WithML -WithRAG. Chưa commit/push.
- Khi thu hồi tiến trình kiểm thử cũ 84816 để dừng theo yêu cầu, tiến trình đã hoàn tất exit 0: readiness ML 200, prediction 200 success và knowledge 200 với 2 chunks. Đây là kết quả có sẵn của lượt trước, không khởi chạy test mới. Kết quả này xác nhận truy xuất RAG trực tiếp thành công; chưa xác nhận toàn bộ hội thoại UI/LLM sau restart.

## Thu gọn frontend bằng shadcn/ui theo HTML ChatGPT mới

- Ngày 2026-10-20, Asia/Saigon. Người dùng yêu cầu viết lại frontend bằng shadcn/ui, giữ mã màu và chức năng, giảm kích thước theo thư mục ChatGPT và HTML đính kèm.
- Git `hoanganhdev_features` / `b944ad1ba8ea3bc914bf21449aa5a0a3f8cfec6f`; giữ nguyên thay đổi có trước. `web/` vẫn chưa track. PROJECT-RULES/PLAN-OPENCORE vẫn không có trong checkout; không tự tạo task CODE.
- Đối chiếu HTML đính kèm và CSS local trong ignored `web/ref/chatgpt.com`: toolbar 46–52 px, composer controls khoảng 36 px, cột chat 42rem. Chỉ dùng tham khảo bố cục/tỷ lệ; không import source/asset ChatGPT vào ứng dụng.
- Workspace: header 52 px, sidebar 248 px (icon 56 px), cột đọc/composer 672 px, nội dung 14 px; bỏ eyebrow/subtitle và thẻ gợi ý lớn, giữ nút chọn agent nhỏ. Thu gọn thư viện, activity panel và login; trang chào dùng lại AuthShell chung với Keycloak.
- Thêm InputGroup/NativeSelect/Table từ registry shadcn/ui chính thức new-york-v4; giữ MIT và nguồn tại `web/THIRD-PARTY-NOTICES.md`. Chỉ chỉnh alias import; dùng dependency hiện có, không đổi manifest/lockfile. Composer/search dùng InputGroup; bộ lọc dùng Tabs; tables dùng Table; form bổ sung dùng NativeSelect/Label; details dùng Collapsible; warning dùng Alert.
- Sửa `web/src/styles/{workspace,globals}.css`, app/login, các component workspace/chat/results liên quan, README web và CHANGELOG. Giữ canonical brand tokens, font, API/SSE/idempotency/SSO. Không sửa backend/DB/Langflow hay rebuild stack ML/RAG trong task này.
- Kiểm tra trung gian: build PASS, 9 unit tests PASS. E2E đầu 10/12 PASS: một lỗi cold-start SSO, một lỗi composer mobile cao 76 px do placeholder dài. Rút placeholder thành Hỏi FlowBridge; kiểm bốn viewport PASS. Trace lần SSO tiếp theo xác nhận screenshot mất ~47 giây, vượt timeout; kiểm riêng lại SSO PASS (766 ms), không sửa logic xác thực hay nới timeout cố định trong config.
- Preview dev riêng tại `http://127.0.0.1:5174/?preview=1`, session `84701`. Build cuối session `46709`, full E2E cuối session `83238` đang chạy; cần kiểm outcome trước khi retry. Đã xem ảnh desktop welcome/library/chat và mobile 390 px.
- Kết quả cuối: `npm run build` PASS (session 46709, exit 0); `npm test` 9 passed (session 15686); full `npm run test:e2e` **12 passed (32.5s)**, session 83238 exit 0. Bốn viewport 1440/1024/390/320 đạt composer rỗng <60 px, cột <=672 px, header <=56 px, không tràn ngang; component slot InputGroup/Table và Tabs lọc được kiểm.
- Đã xem ảnh kết quả cuối mobile 320/390, desktop welcome/library/chat và login. Preview dev giữ tại session 84701, URL đã gửi tới panel Codex (open_in_codex trả queued). Bản Docker 8761 chưa rebuild trong task này; không nhận mock E2E là kiểm tích hợp backend thật.
- `git diff --check` PASS. Yêu cầu sửa source frontend hoàn tất; chưa commit/push. Công việc bật ML/RAG từ mục checkpoint trước là độc lập, không thay trạng thái hoặc tự chạy lại.

## Sửa SSO thật cho frontend Vite cổng 5174

- Người dùng mở `/` thay vì preview và gặp `Invalid parameter: redirect_uri`. Xác minh Vite 5174/config và Docker 8761 đều HTTP 200; Keycloak client A chỉ cho callback 8761, backend preflight 5174 trả 400.
- Thêm helper `scripts/configure_web_dev.py`, dry-run rồi apply vào client `flowbridge-web-a` hiện có qua Admin REST + HTTPS CA cụ thể. Chỉ thêm callback/logout `http://127.0.0.1:5174/` và webOrigin tương ứng; giữ callback 8761 và toàn bộ quyền/tài khoản/PKCE S256. Đã đọc lại client xác minh. Lượt apply đầu đã cập nhật nhưng check so sánh list sai thứ tự do Keycloak dùng set; sửa check set, apply idempotent lần cuối PASS (session 5996 exit 0).
- Template realm local thêm đúng 5174 cho profile mới. Vite configure public apiUrl cùng origin và proxy `/api/v1` tới backend trong cấu hình public; không thay CORS backend hoặc restart ML/RAG. Cập nhật README web, SSO docs, CHANGELOG; không đổi dependency/migration.
- Build đầu lỗi implicit-any do context async Vite config; thêm type ViteDevServer. Build cuối PASS (session 29773 exit 0); 2 E2E PKCE + idempotency/SSE PASS (6.9 s); Ruff helper, template exact-redirect/PKCE và diff whitespace PASS.
- Live TLS probes: 5174 và 8761 auth endpoint HTTP 200, không có invalid redirect; callback 5175 HTTP 400. Dev public apiUrl đúng 5174; proxy `/api/v1/me` không bearer trả 401 như dự kiến. Không đăng nhập tài khoản người dùng/gọi model.
- Browser trong ứng dụng điều hướng `/` sang SSO bị ERR_CERT_AUTHORITY_INVALID cho localhost; không bypass cảnh báo/không sửa TLS hoặc trust store. CUA sau đó bị policy chặn trang lỗi data URL; chưa xác nhận đăng nhập trọn vẹn trong IAB. Người dùng cần mở lại 5174 từ đầu bằng browser đã tin CA local theo docs/SSO.md.
- Source và allowlist live đã sửa; Vite vẫn chạy cổng 5174. Chưa commit/push.

## Bỏ bước chọn PR của agent Tri thức

- Người dùng yêu cầu bỏ thông báo/điều kiện Chọn yêu cầu mua hàng (PR) khi dùng Tri thức. Đã xác minh frontend có picker và hai guard, backend admission/bridge/RAG cũng yêu cầu job; không chỉ ẩn UI rồi để gửi tin thất bại.
- Bỏ picker/tải PR/state/guard và lời nhắc PR khỏi frontend. Thêm tra cứu RAG human EMPLOYEE cho tài liệu global đã xuất bản khi hội thoại không gắn PR; không tự chọn/tạo PR/job, không cấp quyền đọc tài liệu department. Hội thoại đã có PR tiếp tục nhánh job/ACL cũ.
- Source thay đổi: web composer/use-hub/library/styles, Hub service/router/adapter + contract endpoint /knowledge/search, ChatService receipt revalidation, Langflow caller/serving/tools. Giữ token verified user, allowlist selection, published/withdrawal/source verification; không đổi schema/migration/dependency/role.
- Test đang chạy: backend session 28562; Langflow mocked bridge session 68047; web build + 2 E2E session 28564. Chưa triển khai API/Langflow live; cần kiểm outcome trước build/restart.
