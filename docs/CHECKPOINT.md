# Checkpoint FlowBridge

## 2026-10-17 — CODE-ML-02: Cập nhật hợp đồng ML (hoàn tất)

- Phạm vi người dùng chốt: chỉ cập nhật hợp đồng API; đã hoàn tác mọi chỉnh sửa
  thử ở schemas.py/router.py. Không còn diff runtime, dependency hoặc migration.
- Repo FlowBridge, branch hoanganhdev_features, HEAD aebfe85. Cập nhật
  contracts/supplier-assistant-layer2-api.yaml lên 0.2.0: thêm 5 route ML và
  15 schema, request/response examples, bearer/role, idempotency, MAX rủi ro món
  còn mở, item/deadline đại diện và trạng thái thiếu dữ liệu/điểm chưa hiệu chuẩn.
- Phụ lục ML v1 phân biệt endpoint đã triển khai với các route thiết kế cũ;
  giữ nguyên path/schema cũ, không sửa snapshot app/AI 0.4.1 hoặc .env.
- Đã kiểm YAML không trùng key, OpenAPI 3.1, JSON Schema, 883 local refs,
  8 ví dụ JSON; 5 route mới khớp runtime. git diff --check đạt.
- Không chạy lại model/DB/integration vì chỉ đổi tài liệu; không commit/push/deploy.

## 2026-10-16 — CODE-ML-01: Prediction Service (hoàn tất code và kiểm thử)

- Yêu cầu: triển khai hai luồng ML cho custom node sau khi pull code mới; rủi ro PO
  đại diện bằng MAX điểm các PO item còn phải giao, kèm item/deadline quyết định.
- Repo `D:\OLP MNM 2025\FlowBridge`, branch `hoanganhdev_features`, HEAD đầu vào
  `ae20b282f4009634f97ddede1396f1e53397732c`; Git sạch khi bắt đầu. Không có plan/task
  được dẫn chiếu trong checkout; dùng ID bổ sung CODE-ML-01.
- Đã đọc hướng dẫn `D:\OLP MNM 2025\AGENTS.md`, checkpoint và auth/repository hiện có.
  Tích hợp cùng package và chính sách DB/IdP; không tạo microservice hoặc giả danh.
- Đã thêm `supplier_assistant/prediction/`: 19 features, SQL repository có giới hạn,
  context có provenance/cutoff, TabICLv2 adapter, API scenario/rank, lưu/đọc PO risk.
  Runtime bật bằng ML_ENABLED, giữ hợp đồng AI 0.4.1 cũ và RAG hiện có.
- Không đổi schema/migration, `.env`, dữ liệu nghiệp vụ, không commit/push/deploy.
  Dependency optional `ml`: tabicl 2.2.0 (BSD-3-Clause), pandas 2.3.3 (BSD-3-Clause);
  checkpoint upstream BSD-3-Clause, tải riêng ngoài source. uv lock cập nhật 142 packages.
- `uv lock` và `uv sync --locked --extra ml --inexact` hoàn tất ngoài sandbox; mạng
  sandbox chặn registry. Lock 142 packages, thêm 6 package vào graph đã có RAG. Không
  cài lại môi trường hoặc đổi pin dependency RAG.
- Đã tải checkpoint chính thức vào `.cache/models`, SHA-256
  `bdc7dbd5e4ff21f8f0456fcf90c6b7cdf72dbea960f2d05b19bec19f9b3d4ed0`.
  Context `.cache/ml/context.json` là 192 snapshot A/B giả lập riêng, có source_kind;
  không lấy CSV simulator lỗi làm context. Chỉ dùng smoke/integration; không có bằng
  chứng calibration/chất lượng nghiệp vụ, production chặn context synthetic.
- Model smoke chạy fit + predict_proba TabICLv2 thật thành công; startup thử riêng A
  categorical toàn NULL. Model/context nạp một lần, inference ở thread có serialization
  và giới hạn queue. Không tải model từ HTTP/startup, không đọc pickle do client cung cấp.
- HTTP: scenario/rank read-only; PO POST synchronous trả 200 sau commit (dependency
  function scope), giữ risk_predictions + durable retry atomically; GET theo quyền/
  freshness/context hash/checkpoint/policy. Không tạo report/proposal/analysis task giả.
- Thêm typed HTTP client, component Langflow ranking chỉ đọc, CLI download/demo/
  validate/smoke, docs/ML-SERVICE.md, ví dụ request, env template, gateway --include-ml,
  README/CHANGELOG. API AI 0.4.1 cũ vẫn fail closed, không thay wire contract cũ.
- `pytest tests/unit -q -rs`: **186 passed, 13 skipped in 8.45s**. 41 test ML mới đều
  qua; 13 skip nằm ở test_knowledge.py do thiếu optional rank_bm25 có sẵn từ code RAG.
  Không coi skip là test RAG đã qua. Ruff check + format (18 file thay đổi/code ML)
  và git diff --check đạt; uv lock --check --offline đạt.
- `pytest tests/integration/test_prediction_database.py -m integration`: **2 passed
  in 27.94s** ngoài sandbox. SQL và TabICL thật: HTTP rank/scenario qua lifecycle thật,
  read-only không tăng rows, PO persistence, cùng key không ghi trùng, ACL và rollback
  khi inference lỗi. Test tiêm Identity/approval, không chứng minh SSO/workflow thật.
  Fixture write rollback; không sửa/nạp lại dữ liệu demo. Không chạy migration/seed.
- Build offline wheel + source tar.gz đạt `.cache/ml-build/`; audit 118 source/52 wheel
  members, một source root, có ML docs/component, không secret/cache/weights/dependency
  binary. Import wheel bằng Python -I và validate OpenAPI đạt (45 paths/51 operations).
- Không còn session kiểm thử/download đang chạy. Chưa khởi động server lâu dài,
  sửa .env, cấu hình IdP, test trong Langflow UI, calibration/evaluation hoặc scheduler.
  Kiểm tra Settings cuối (chỉ boolean): DB đã cấu hình; ML_ENABLED=false,
  DOCS_ENABLED=false, 5 trường OIDC chưa đầy đủ. Không đưa credential vào log.
  Để dùng: cài extra ml, bật ML_ENABLED/DOCS_ENABLED, cấu hình IdP và mapping/grants
  theo docs, restart backend; dùng context observed đã review cho nghiệp vụ thật.

## 2026-10-16 — Giải quyết conflict PR #4 (RAG development)

- Yêu cầu: sửa conflict của PR #4 hiện có, cập nhật nhánh minnhungdev_features; không merge PR vào main.
- Head đầu vào: d3ce7e7; main được gộp: f24a2c9. Làm trong worktree riêng; giữ nguyên checkout hoanganhdev_features của người dùng.
- Đã xử lý 6 file conflict: .env.example, CHANGELOG.md, README.md, main.py, settings.py, pyproject.toml; bỏ cả marker cũ đã tồn tại trong CHANGELOG của main.
- Giữ API PostgreSQL, bearer introspection và OpenAPI từ main. RAG development giữ lifecycle, loopback guard và DSN trống; kiểm tra guard sau khi Settings ghép DSN từ host/user/password.
- Không khôi phục API/sample modules đã bị main loại bỏ. Router RAG dùng Header của FastAPI thay import shared.http đã bị xóa; bỏ nhánh Swagger Core sample.
- Cập nhật tests/unit/test_knowledge_http.py, examples/rag/README.md và docs/rag-local-report.md theo cách chạy riêng; giữ nguyên bằng chứng thử Qwen trước merge và ghi rõ thời điểm.
- Kiểm tra: 158 unit test pass, không skip; Ruff check/format pass; uv lock --check --offline pass. Hatchling build wheel và source tar.gz pass; kiểm nội dung archive và import wheel ngoài source, bootstrap/database/RAG OpenAPI pass.
- Sandbox Windows làm asyncio treo khi tạo socket nội bộ; đã dừng các lượt đó và chạy lại unit suite ngoài sandbox thành công. rank-bm25 dùng từ thư mục test tạm, không sửa môi trường đang dùng.
- Chưa chạy Supabase integration, migration/seed, Chroma thật hoặc tải/chạy Qwen; không thay dependency pin hoặc schema. Lockfile được Git tự gộp và kiểm tra đạt.
- Không có PLAN-OPENCORE.md/TASK-API-TEST.md trong snapshot để gắn ID CODE; phạm vi theo PR #4. Bước cập nhật Git: tạo merge commit từ bản đã kiểm tra, push thường vào minnhungdev_features rồi xác minh PR/remote SHA; không force-push.

## 2026-10-16 — Chẩn đoán 404 khi mở Swagger

- Người dùng xác nhận mở `http://127.0.0.1:8000/docs` bằng trình duyệt và nhận NOT_FOUND. Repo/branch/HEAD vẫn là FlowBridge / `hoanganhdev_features` / `928ebfa91a2f7ca2b94d4bd366f95acc3411f008`; giữ nguyên staged changes và conflict CHANGELOG.
- Settings trong tiến trình kiểm tra: database đã cấu hình, docs_enabled=False, IdP chưa cấu hình; chỉ đọc boolean, không hiển thị credential. `main.py` đặt docs_url/openapi_url=None khi docs tắt; đây là nguyên nhân phù hợp với 404 được báo. Biến môi trường của server người dùng có thể khác tiến trình kiểm tra.
- Hướng xử lý: dừng server cũ, đặt `FLOWBRIDGE_DOCS_ENABLED=true` trong PowerShell hoặc `.env`, khởi động lại rồi mở `/docs`. Không đổi source/runtime, `.env`, migration hoặc dữ liệu DB.
- Smoke ASGI offline trong sandbox session `60874` không trả output, đã dừng Ctrl+C (exit 1); không nhận test đã pass, không còn session chẩn đoán chạy. Không chạy lại suite/integration cho việc hướng dẫn cấu hình này. Bước tiếp theo: người dùng khởi động server với docs bật và kiểm tra trình duyệt; bearer/IdP vẫn cần cho API bảo vệ.

## 2026-10-15 — CODE-DB-02: Hoàn tất bỏ API sample, chỉ giữ API Supabase

- Yêu cầu mới thay phạm vi rà soát: người dùng yêu cầu chỉ giữ API dùng Supabase và giải thích phần cần bổ sung để API không bị chặn. Xóa runtime/fixture/config/sample tooling cũ; giữ kiểm quyền, migration, ORM và seed database. Không triển khai IdP/workflow/model khi chưa có cấu hình hoặc adapter thực tế.
- Repo `E:\CTU\CLB_Tin_hoc\OLP\FlowBridge`, branch `hoanganhdev_features`, HEAD `928ebfa91a2f7ca2b94d4bd366f95acc3411f008`. Giữ staged changes và conflict CHANGELOG có sẵn; không reset/commit/push. Plan/task/rules nằm ở `.ai-local/FlowBridge/` ngoài checkout; đã đọc mục sample CODE-00-07..14 và checkpoint, không tick nghiệm thu tích hợp.
- Rà AST 75 file Python và references: sample chỉ mount qua `main.py`; test Supabase có phụ thuộc `SampleSupplierAdapter` cho 4 payload. Bước tiếp theo: chuyển 4 payload cần thiết vào tests, bỏ sample runtime/test/tooling, giữ và bổ sung test contract/auth/route cho database.
- Chưa sửa runtime/dependency/migration hoặc chạy test trong task này. Trước kiểm tra dài sẽ chạy Ruff, unit không mạng, build/archive sạch; integration chỉ khi cần kiểm phần thay đổi với database, không chạy seed/migration lại. Chưa có tiến trình kiểm thử đang chạy.
- Thiếu để mở API: 5 biến OIDC RFC7662 và issuer+subject/user/grant thật; workflow authoritative, IdP provisioning, chat/model/document adapters cho 9 operation trả 503; durable outbox/email/analysis workers cho tác động bên ngoài. Không thay bằng HTTP impersonation.
- Đã xóa 45 file sample runtime/test/example/tooling và snapshot core khỏi package; giữ contract core ở `contracts/` làm thiết kế. Main chỉ ghép router database; Settings bỏ 2 trường sample. Gateway map app/AI, tách public/internal/admin. Chuyển đúng 4 payload còn dùng sang `tests/integration/analysis-payloads.json` và notice riêng, sửa integration để không import sample adapter.
- Dependency: bỏ `python-multipart` khỏi manifest, `uv lock --offline` với `.cache/uv` thành công (44 package). Thử `.cache/uv-build` trước đó fail vì thiếu cache index; không cài lại môi trường hoặc truy cập registry. Không thay migration/schema.
- Tài liệu README/Supabase/Alembic/CHANGELOG đang cập nhật. Giữ nguyên conflict marker CHANGELOG. Sửa mô tả sample đã lỗi thời trong nguồn `03-api/contracts/{app-api,ai-tools-api}.yaml`, đồng bộ snapshot/checksum bằng script; không đổi wire DTO/path/operation ID.
- Xác nhận bằng Settings: DB đã được cấu hình; cả 5 biến OIDC chưa cấu hình (chỉ kiểm boolean, không ghi giá trị). Chưa truy cập hoặc sửa dữ liệu Supabase trong task này.
- Trước kiểm tra dài: chuẩn bị chạy Ruff và toàn bộ unit offline, default collection loại integration. Sau đó kiểm archive/build sạch, và suite Supabase nếu thay đổi fixture cần kiểm chứng. Chưa có kết quả test mới ở checkpoint này.
- Ruff lint/format hiện đạt 40 file; `uv lock --check --offline` đạt 44 package. Lượt unit trong sandbox session `57607` không trả output, đã dừng Ctrl+C (exit 1); chưa có kết quả test, không chạy đồng thời lại. Sắp chạy lại unit với timeout chẩn đoán ngoài sandbox; không đổi môi trường hoặc chạy seed/migration.
- Unit ngoài sandbox hoàn tất **107 passed in 4.33s**, exit 0. Trước lệnh integration dài: chạy `pytest tests/integration -m integration` để xác minh payload vừa chuyển; phần lớn transaction rollback, case commit/concurrency có cleanup UUID riêng. Không chạy seed/migration hoặc sửa dữ liệu demo; đầu vào vẫn revision 0002 đã xác nhận ở CODE-DB-01. Sắp build vào `.cache/supabase-only-build` và kiểm archive sạch ngoài source tree.
- Build offline/no-build-isolation thành công source tar.gz và wheel tại `.cache/supabase-only-build/`; audit **71 sdist members**, **31 wheel members**, một root `flowbridge-0.1.0`, không sample runtime/cache/venv/dependency binary/secret. Wheel không có test fixtures hoặc `python-multipart` trong METADATA. Giải nén sạch tại workspace visualizations `flowbridge-supabase-only-verification/flowbridge-0.1.0`, rebuild offline thành công; Python `-I` import trực tiếp rebuilt wheel và tạo app/Swagger đủ 46 operation, giữ security, không sample context. Đây là build verification, không release/publish; CHANGELOG vẫn có conflict cũ.
- So sánh snapshot với Git index ban đầu: components/DTO, path, operationId, request/response/status/security/required role/parameter schema không đổi; chỉ mô tả trạng thái và ví dụ key được sửa. Sync check nguồn chuẩn/checksum đạt. Không còn import/tham chiếu module sample trong backend/tests/scripts.
- Integration session `65115` đang chạy ngoài sandbox, đã trả 13 dấu pass, chưa có tổng kết/exit; kiểm outcome trước retry. Chưa có session build/unit còn chạy.
- Kết quả cuối integration session `65115`: **15 passed in 162.09s**, exit 0, không skipped. Unit **107 passed**, Ruff lint/format **40 file** đạt, lock check offline **44 package** đạt. Test Supabase chỉ tiêm danh tính, không phải bằng chứng SSO/workflow/ML. Test commit/concurrency đã chạy cleanup của suite; không chạy lại migration/seed/reset.
- Hoàn tất cập nhật tài liệu gateway/contract và `03-api/contracts/README.md`; bỏ dòng sample config còn sót trong Alembic README. Build/audit cuối sau sửa tài liệu đạt **71 source members / 31 wheel members**; không sample runtime/secret/cache/dependency nhúng. `git diff --check` ngoài CHANGELOG đạt; conflict CHANGELOG có sẵn vẫn chưa xử lý.
- CODE-DB-02 hoàn tất trong phạm vi dọn API sample: runtime chỉ còn chế độ database, 46 operation theo contract (37 có đường xử lý DB, 9 chờ engine/adapter). Giữ scripts seed/generator vì chúng nạp dữ liệu vào Supabase và vẫn được test schema/synthetic data sử dụng; payload test nằm riêng trong tests, không trong wheel. Không thay wire DTO hoặc schema/migration database, không sửa `.env`, không commit/push/deploy. Không còn session kiểm thử/build đang chạy.
- Bước tiếp theo: cấu hình IdP RFC7662 với 5 biến OIDC hiện còn thiếu, bootstrap/map exact issuer+subject và grant vào users/user_roles để dùng API bảo vệ. Muốn hoàn tất nghiệp vụ xuyên engine cần adapter provisioning/workflow/chat/model/document và durable outbox/analysis/email worker; xem mục “Cần bổ sung để API không bị chặn” trong `docs/SUPABASE-API.md`. Không dùng danh tính mẫu hoặc bỏ policy để mở API.

## 2026-10-15 — CODE-DB-01: Hoàn tất phần database của API nghiệp vụ

- Yêu cầu hiện tại: chuyển API nghiệp vụ sang Supabase. Người dùng xác nhận chưa có Identity/SSO; sẽ tiêm danh tính trong test, runtime không giả danh hoặc bỏ kiểm quyền.
- Repo `E:\CTU\CLB_Tin_hoc\OLP\FlowBridge`, branch `hoanganhdev_features`, HEAD `928ebfa91a2f7ca2b94d4bd366f95acc3411f008`. Không có plan/task/rules được dẫn chiếu trong checkout; dùng task bổ sung ổn định `CODE-DB-01`, chưa đánh dấu hoàn tất. Giữ thay đổi staged và conflict CHANGELOG có sẵn; không commit/push.
- Đã thêm repository SQLAlchemy async, database service/policy và router cho contract app/AI 0.4.1; giữ sample mode độc lập. Thêm adapter introspection upstream RFC 7662 và Settings OIDC, không tự viết engine JWT/SSO. Model/migration `0002_api_idempotency` thêm riêng bảng retry response, bật RLS và revoke client grants.
- Các chức năng cần workflow approval, identity provisioning, chat/model/document engine vẫn fail closed 503; lệnh DB sinh outbox pending, chưa có worker xử lý event. Không coi queue/outbox là engine đã chạy.
- File mới: `identity/authentication.py`, `models/api_idempotency.py`, `supplier_assistant/{repository,policy,database_service}.py`, revision `alembic/versions/20261015_0002_api_idempotency.py`. File sửa: settings/main/models init/router/contract, pyproject (mặc định loại integration, chọn rõ bằng `-m integration`), baseline seed test tách metadata operational.
- Checkpoint trước lệnh dài/migration: Supabase đã xác nhận revision 0001 và 27 bảng bằng lượt kiểm thử bên dưới. Chuẩn bị áp dụng duy nhất revision 0002 thêm bảng operational; không seed/reset/drop dữ liệu cũ. Chưa chạy migration này hoặc test integration ở thời điểm ghi mục này.
- Ruff check backend/tests/revision mới pass. Regression đang chạy ngoài sandbox, session `94865`; phải kiểm outcome trước retry. Bước tiếp theo: viết integration có assertions và rollback fixture, review SQL 0002 offline, chạy migration mới, test HTTP persistence/quyền/idempotency trên Supabase, cập nhật tài liệu và evidence.
- Cập nhật giữa task: regression session `94865` hoàn tất **187 passed in 15.02s**. Đã review SQL offline riêng 0001→0002: chỉ CREATE bảng idempotency, notice, RLS/revoke và cập nhật alembic_version; không DROP. Đã áp dụng `0002_api_idempotency` lên Supabase thật thành công, exit 0 (5.45s), không seed hoặc sửa dữ liệu ứng dụng cũ. Không chạy lại migration nếu bị gián đoạn; tiếp theo chỉ kiểm current/check và HTTP integration.
- Integration lần đầu: 8 pass/3 fail (95.83s), fixture prediction dùng lead_days cũ gây constraint `ck_prediction_lead_anchor`; đã sửa fixture và kiểm invariant anchor ở service. Lần tiếp: 11 pass/1 fail (142.15s), negative token ngắn sai schema (422 thay 401); sau sửa negative token, nhóm bổ sung đạt 3 pass/1 fail (69.10s), expectation confirmation 200 khác contract 201; đã sửa test theo contract, không đổi status API để làm test pass.
- Test auth adapter HTTP mock: **9 passed in 1.08s**; đây không phải integration SSO. Supabase `alembic current` xác nhận `0002_api_idempotency (head)`, `alembic check` đạt không drift. Integration đã chứng minh commit request và chống tạo trùng đồng thời qua hai request + kết nối đọc độc lập; fixture của test được cleanup.
- Tài liệu đã cập nhật README, CHANGELOG (giữ conflict có sẵn), .env.example, contracts mapping, alembic README và `docs/SUPABASE-API.md`. Sdist bổ sung hướng dẫn mới. Sắp chạy suite integration cuối, regression và build archive cục bộ; chưa xác nhận hoàn tất các lượt cuối.
- Kết quả cuối: **15 integration passed in 180.31s**, session `24526`, không skip; **201 unit/sample passed, 15 integration deselected in 15.87s**, session `46013`. Có thêm 5 assertion cases cho NUMERIC(18,4) overflow/rounding. Không dùng test mock để thay bằng chứng SQL thật.
- HTTP smoke cuối với lifespan thật, không override auth: live/ready 200; business/admin thiếu bearer đều 401; OpenAPI đủ 46 operations. SQL read-only xác nhận revision 0002, RLS bảng mới bật, anon/authenticated không có SELECT/INSERT/UPDATE/DELETE; không còn identity fixture. Counts giữ `suppliers=6`, `products=6`, `purchase_requests=1000`, `api_idempotency=0` sau cleanup. Không chỉnh .env hoặc credential.
- Build: lần đầu uv cache mặc định bị lỗi quyền sandbox (session `85152`, đã kết thúc exit 1), đổi UV_CACHE_DIR vào `.cache/uv-build` thì build offline/no-build-isolation đạt. Archive `.cache/api-build/flowbridge-0.1.0.tar.gz` bản cuối có 113 members, một root đúng version, không `.env`/cache/dependency binaries. Giải nén sạch ngoài repo tại workspace visualizations, build wheel và import/app construction từ wheel bằng Python -I đạt. Chỉ là build verification, không chuẩn bị/publish release; artifact vẫn mang CHANGELOG có conflict cũ.
- Ruff lint và format check backend/tests/revision mới đạt; notice GPL/no standalone string comment của các file code mới đã kiểm. `git diff --check` ngoài CHANGELOG đạt; conflict marker CHANGELOG tồn tại từ đầu task và chưa giải quyết. Không thêm dependency, không đổi contract snapshot hoặc chạy seed. Không còn session kiểm thử/build đang chạy.
- Trạng thái: CODE-DB-01 hoàn tất phần persistence theo yêu cầu: 37 operation có đường xử lý database, 9 operation cần engine/adapter ngoài trả 503 như tài liệu. Không claim nghiệm thu 37 operation riêng lẻ hoặc tích hợp SSO/workflow/email/ML; test coverage là các luồng/invariants nêu trong suite. Chưa commit/push/deploy. Bước tiếp theo: cấu hình IdP thật và mapping issuer+subject; triển khai durable outbox dispatcher/workflow/email/model theo yêu cầu riêng, giải quyết conflict Git trước khi commit/release.

## 2026-10-15 — Kiểm thử API với cấu hình Supabase mới

- Mục tiêu: theo yêu cầu người dùng, dùng `.env` vừa bổ sung để kiểm thử kết nối và API với Supabase thật. Chỉ đọc database; không chạy migration/seed hoặc thay đổi dữ liệu.
- Repo thực tế: `E:\CTU\CLB_Tin_hoc\OLP\FlowBridge`; branch `hoanganhdev_features`; HEAD `928ebfa91a2f7ca2b94d4bd366f95acc3411f008`. Checkpoint phía dưới có lịch sử từ checkout/HEAD khác, không dùng thay bằng chứng hiện tại.
- Task: kiểm thử runtime/database, hoàn tất trong phạm vi đã có. Không tự gán CODE/CP: checkout không có `docs/PLAN-OPENCORE.md`, `docs/TASK-API-TEST.md` hoặc `docs/PROJECT-RULES.md` để đối chiếu.
- Đầu vào: giữ nguyên mọi thay đổi staged và conflict `UU CHANGELOG.md`; không giải quyết conflict, commit hoặc publish. Không sửa `.env`, code runtime, dependency hay migration.
- Lệnh `./.venv/Scripts/python.exe scripts/check_db.py` trong sandbox không trả kết quả, session `96617` đã dừng. Chạy lại với quyền mạng ngoài sandbox: exit 0, `SELECT 1` thành công (5.70s).
- Tạo script kiểm tra cục bộ `.cache/supabase_api_smoke.py` (Git ignore, GPL notice, không chứa credential). Lệnh `./.venv/Scripts/python.exe .cache/supabase_api_smoke.py` ngoài sandbox: exit 0 (6.36s). Dùng lifespan thật và HTTPX ASGITransport để gọi app trong tiến trình; không khởi động TCP server/gateway, không mock DB.
- Startup đọc PostgreSQL thật thành công; `GET /_ops/live` và `GET /_ops/ready` đều HTTP 200 và payload đúng. Shutdown đã giải phóng resources.
- Catalog thật có đủ 27/27 bảng nghiệp vụ trong ORM và revision `0001_supplier_schema`. Trong transaction READ ONLY, xác nhận cột ORM đọc được và COUNT: `suppliers=6`, `products=6`, `purchase_requests=1000`. Không đọc/ghi nội dung record hoặc hiển thị credential.
- `GET /api/v1/suppliers`, `/api/v1/products`, `/api/v1/purchase-requests` đều HTTP 404 với sample mode tắt. Đây là giới hạn triển khai hiện tại: router nghiệp vụ chỉ mount trong sample mode, chưa có đường API persistence thật. Không coi các assertion 404 hay test sample là nghiệm thu CRUD Supabase thành công.
- Regression: `./.venv/Scripts/python.exe -m pytest tests/unit/test_settings.py tests/unit/test_operations.py tests/unit/test_lifespan.py tests/unit/test_database_tooling.py -q --tb=no` ngoài sandbox: **47 passed in 4.01s**, exit 0. Không chạy lại full suite hoặc `alembic check` vì phạm vi là runtime/read-only, không thay đổi code/schema.
- File thay đổi trong lượt: checkpoint này và script tạm `.cache/supabase_api_smoke.py`; không còn tiến trình kiểm thử đang chạy. Bước tiếp theo nếu được yêu cầu triển khai: nối router → service/policy → repository dùng request-scoped AsyncSession và kiểm thử CRUD với DB thật; Supabase connectivity đã được xác nhận, nghiệp vụ chưa đạt.
- Rà diff cuối: `git diff --check` báo conflict marker có sẵn tại `CHANGELOG.md:5,8,14`; chưa xử lý vì ngoài yêu cầu test. `git check-ignore` xác nhận `.env` và script `.cache/` bị ignore; checkpoint hiện được Git theo dõi.

## 2026-10-15 — Tích hợp schema và chạy dữ liệu từ script của nhóm

- Yêu cầu trực tiếp: thiết lập và chạy dữ liệu từ script đã được thêm vào repo.
- File đầu vào thực tế là `alembic/script.py`: standalone installer gồm schema,
  3 hàm SQL và bộ sinh dữ liệu demo. Đã chuyển nguyên nội dung sang
  `scripts/supplier_demo_source.py`; không dùng installer để tạo schema ngoài Alembic.
- Kiểm tra catalog trước khi chạy: `public` không có bảng và không có 3 helper trùng tên.
- Tạo revision `0001_supplier_schema` và các asset schema/helper/verify cố định trong
  `alembic/sql/`; SQL trích nguyên văn từ script và được kiểm tra bằng test.
- Migration đã commit: 27 bảng ứng dụng, 293 cột, 56 FK, 83 CHECK, 27 PK,
  40 unique index thông thường, 3 unique index có điều kiện; RLS bật cả 27 bảng.
  Quyền bảng cho anon/authenticated bằng 0, không có policy/user trigger theo script.
- Thêm 27 ORM mapping trong `models/supplier_assistant.py`, đăng ký trong `models/__init__.py`.
  Đủ tên constraint, index và comment; `alembic check` sau cùng không phát hiện drift.
- Thêm `scripts/seed.py` dùng cấu hình asyncpg/SSL hiện có, không thêm driver.
  Seed từ chối DB có dữ liệu, dùng advisory lock và một transaction, kiểm tra schema,
  count từng bảng, FK/CHECK và hàm training trước commit. Không reset/xóa dữ liệu.
- Đã nạp mặc định 1.000 PR: tổng 25.422 dòng trong 27 bảng, 6 supplier, 6 product,
  998 PO, 3.863 prediction mô phỏng. Đọc lại tất cả bảng qua SQLAlchemy sau commit khớp.
- Hàm training trả 1.780 dòng có nhãn; CSV train 1.420, test 279, loại 81 dòng biên;
  queries 83. Không gửi email, không phát outbox thật, không chạy model AI/ML.
- CSV/report tại `demo_output/`, bị Git ignore. `.env` hiện có được dùng nhưng không
  đưa credential vào source, log hoặc checkpoint. Hướng dẫn cập nhật tại `alembic/README.md`.
- Test: 109 pass, 1 fail cũ `test_contract_snapshot_matches_provenance_checksum`.
  Lỗi checksum tương tự lượt thiết lập trước; không sửa contract/API ngoài yêu cầu.
- Các file contract tầng 2 và nội dung checkpoint có sẵn được giữ nguyên.
  Chưa nối API sample với database thật; chưa commit/push thay đổi Git.

## 2026-10-15 — Xóa các bảng tạo nhầm trên Supabase

- Yêu cầu hiện tại: xóa các bảng ứng dụng đã tạo trên Supabase được cấu hình cho dự án.
- Repo: `D:\OLP MNM 2025\FlowBridge`; branch `hoanganhdev_features`; HEAD `a94ad47`.
- Trạng thái: hoàn tất thao tác bảo trì theo yêu cầu trực tiếp của người dùng; không gắn ID CODE vì plan và checkpoint cũ không tồn tại trong checkout này.
- Đã kiểm tra catalog PostgreSQL thật: 26 bảng ứng dụng trong `public`; các schema còn lại thuộc dịch vụ Supabase hoặc extension.
- Đã chạy một câu `DROP TABLE` liệt kê đủ 26 bảng với `RESTRICT` trong một transaction; lock timeout 5 giây, statement timeout 30 giây. Không dùng `CASCADE` hoặc xóa schema.
- Giao dịch đã commit. Truy vấn lại sau commit xác nhận `public` còn 0 bảng; danh sách bảng ngoài `public` không đổi.
- Các bảng đã xóa: `analysis_tasks`, `approval_history`, `confirmation_tokens`, `departments`, `email_logs`, `goods_receipts`, `inventory_snapshots`, `notifications`, `outbox_events`, `products`, `proposal_items`, `proposals`, `purchase_order_items`, `purchase_order_schedules`, `purchase_orders`, `purchase_request_items`, `purchase_requests`, `risk_analysis_reports`, `risk_predictions`, `roles`, `supplier_confirmation_items`, `supplier_confirmations`, `supplier_products`, `suppliers`, `user_roles`, `users`.
- Kết nối dùng Settings/SQLAlchemy hiện có; không lưu thông tin đăng nhập. Kiểm tra catalog ban đầu trong sandbox bị treo và đã dừng trước khi thực hiện bất kỳ thay đổi nào. Các lệnh mạng sau đó chạy với quyền được phê duyệt.
- Không thêm dependency hoặc Alembic revision; đây là thao tác dọn bảng tạo nhầm, không triển khai schema ứng dụng mới. Chưa chạy migration tạo bảng hoặc seed.
- Không chạy unit test vì không sửa mã ứng dụng; bằng chứng kiểm tra là truy vấn trực tiếp sau commit.
- Thay đổi file trong lượt này: tạo checkpoint này. Các thay đổi có sẵn trong `.env.example`, `CHANGELOG.md`, `README.md`, `backend/src/flowbridge/db.py`, `backend/src/flowbridge/settings.py`, `pyproject.toml`, `tests/unit/test_settings.py` và các file Alembic/model/script/test chưa được Git theo dõi được giữ nguyên.
- Không còn việc cần thực hiện trong yêu cầu xóa bảng này. Khi có yêu cầu dựng schema mới, kiểm tra lại catalog và chuẩn bị migration trước khi chạy.

## 2026-10-15 — Hợp đồng tầng 02 Supplier Assistant

- Yêu cầu: chỉ viết hợp đồng YAML tầng API Tools dựa trên OpenCore tầng 1 và tài liệu Word/sơ đồ người dùng cung cấp; chưa triển khai mã.
- Repo: `D:\OLP MNM 2025\FlowBridge`; branch `hoanganhdev_features`; HEAD `a94ad4744f18c3a249780d4c52cfe4eba40727bc`.
- Trạng thái: hoàn tất bản thiết kế để review, không đánh dấu task triển khai CODE hoặc mốc CP. `docs/PLAN-OPENCORE.md` và `docs/TASK-API-TEST.md` không có trong checkout để đối chiếu ID.
- File mới: `contracts/supplier-assistant-layer2-api.yaml`, OpenAPI 3.1, version 0.1.0; 27 paths, 29 operations, 104 schemas, 11 event types. Chỉ bổ sung mục bàn giao này vào checkpoint, giữ mục bảo trì trước đó.
- Nguồn tầng 1: `contracts/opencore-api.yaml` version 0.2.0; khóa SHA-256 trong `x-upstream`. Các schema chung Id/Timestamp/Money/ErrorResponse/Evidence/Event giữ nguyên định nghĩa và đã đối chiếu cấu trúc.
- Quyết định: tách draft khỏi snapshot runtime 0.4.1; DTO nhiều dòng, tool dự đoán/so sánh theo dòng; manifest/cutoff/version dùng chung khi fanout; chat async và action intent có cấu trúc; worker có lease/fencing; RAG có ACL/provenance/generation; phê duyệt authoritative và outbox/saga khi khác engine; email unknown phải đối soát.
- Điểm cần thống nhất khi triển khai: Core Money hai chữ số và schema DB/sample bốn chữ số chưa tương thích mọi giá trị; draft yêu cầu adapter hoặc nâng đồng bộ contract, không làm tròn ngầm. Draft chưa tương thích wire với snapshot runtime; không tự sync hoặc coi endpoint đã tồn tại.
- Kiểm tra đã chạy bằng `.venv/Scripts/python.exe`: YAML không trùng key; `FastAPI OpenAPI.model_validate`; `Draft202012Validator.check_schema` cho mọi schema; resolve toàn bộ local refs; đối chiếu Core operationIds/shared schemas và consumer map; kiểm path/security/idempotency; validate 15 request examples, 8 response examples, 10 standard error examples và 9 negative schema cases. Tất cả pass. Hash hai snapshot app/AI runtime vẫn khớp manifest hiện có.
- Chưa chạy integration/runtime acceptance vì chỉ viết contract; không thêm dependency, migration, router, service, model hoặc sửa snapshot runtime. Các kịch bản `x-acceptance-scenarios` là yêu cầu nghiệm thu tương lai, không phải test runtime đã pass.
- Rà soát cuối: Core Files không có field version; đã quy định version nguồn tệp/PDF bằng `File.sha256` hiện có, không yêu cầu API revision tệp giả định. Sau chỉnh mapping, OpenAPI model và mọi JSON Schema vẫn pass; `git diff --check` pass.
- Bước tiếp theo khi có yêu cầu triển khai: review ranh giới/scope và precision, thống nhất nguồn contract, ánh xạ schema DB và đăng ký projection/policy/workflow với Core, rồi mới lập task triển khai. Không tự chạy migration, commit hoặc publish.
