# Kế hoạch tinh giản code FlowBridge

Ngày lập: 17/10/2026. Task `CODE-00-17`, thuộc PR 00 về nền code.
Chi tiết hóa: 18/10/2026, sync `SIMPLIFY-PLAN-20261018`.
Trạng thái: đã khảo sát sơ bộ và lập kế hoạch; chưa triển khai refactor.

## Mục tiêu và phạm vi

Giảm logic lặp, wrapper không có trách nhiệm riêng và xử lý exception dư thừa;
giữ hành vi nghiệp vụ, hợp đồng HTTP, quyền truy cập và tính đúng của transaction.
Ưu tiên backend, sau đó script và client React. Giữ layout `backend/flowbridge/`.
Không thêm dependency hoặc framework xử lý lỗi để thực hiện đợt tinh giản này.

## Hiện trạng đã kiểm tra

- Repo: branch `huuhuandev_features`, HEAD `dc8f2497b8d7e7b15f5de8cce42777deaaa9d64e`.
  Working tree có thay đổi UI/dev và chuyển layout chưa commit; giữ nguyên các diff đó.
- AST Python trên mã hiện tại: backend có 63 file, 11.343 dòng, 58 nhánh `except`,
  13 nhánh bắt rộng bằng `Exception`, 223 lệnh `raise`. Đây là số liệu khảo sát,
  không phải số lỗi hay số đoạn chắc chắn có thể xóa.
- `models/supplier_assistant.py` có 3.330 dòng mapping schema;
  `supplier_assistant/database_service.py` có 1.269 dòng. Độ dài file chưa chứng minh
  logic dư thừa; không xóa mapping hoặc chia file chỉ để giảm số dòng mỗi file.
- Mẫu cần rà: DB/timeout mapping trong `identity/dependencies.py`,
  `supplier_assistant/router.py`, `supplier_assistant/prediction/router.py`;
  bốn catch rộng trong `knowledge/vector_store.py`; catch rộng ở ML load/predict,
  rerank fallback, startup và readiness.
- `shared/errors.py` đã có CoreError, bảng mã lỗi và HTTP handlers. Phải kiểm chứng
  luồng middleware/FastAPI trước khi kết luận hai lớp xử lý lỗi trùng nhau.
- Bằng chứng từ task chuyển layout: 333 unit pass, 1 fail do snapshot khác nguồn
  `../ho-so-olp`; backend di chuyển giữ nguyên nội dung HEAD. Đây là baseline đã ghi,
  chưa phải kết quả kiểm thử cho refactor sắp tới.

## Quy tắc xử lý exception

- Chỉ catch khi cần phục hồi, dịch lỗi tại boundary, cleanup hoặc che dữ liệu nhạy cảm.
  Bỏ catch chỉ bắt rồi ném lại nguyên lỗi và các lớp dịch cùng lỗi nhiều lần nếu xác minh được.
- Service/policy phát lỗi nghiệp vụ; adapter dịch lỗi upstream; HTTP boundary tạo response.
  Giữ context manager/finally quản lý tài nguyên và transaction khi chúng có trách nhiệm thực.
- Thu hẹp catch rộng khi xác định được lỗi dự kiến từ thư viện đang dùng.
  Với SDK/model có lỗi không ổn định, giữ catch tại adapter nếu cần và ghi rõ lý do.
- Giữ nguyên ý nghĩa 400/401/403/404/409/422/503/504, error code, correlation ID,
  Retry-After và WWW-Authenticate theo từng API. Không gom mọi IntegrityError
  thành xung đột idempotency khi chưa xác định constraint/ngữ cảnh.
- Không đưa lỗi lập trình vào fallback thành công; fallback được hỗ trợ phải có
  kết quả/cảnh báo rõ. Không để raw exception, token, DSN hoặc input lọt vào response/log.
- Chỉ gom code khi trách nhiệm và semantics giống nhau; không tạo decorator,
  base service/repository hoặc framework tổng quát chỉ để giảm số dòng.

## Các bước thực hiện

| ID | Phạm vi | Đầu ra và điều kiện hoàn tất |
| --- | --- | --- |
| `CODE-00-17.01` | Chốt baseline và danh sách xử lý lỗi | Chạy baseline trên source thực tế; lập bảng vị trí → lỗi bắt → lý do → test → giữ/bỏ/thu hẹp/gom. Kiểm lại mọi vị trí trước sửa; ghi riêng lỗi contract đã có, không bỏ/skip test để nhận pass. |
| `CODE-00-17.02` | Bỏ xử lý dư thừa đã chứng minh | Xóa catch/rethrow, guard trùng và wrapper chuyển tiếp không có trách nhiệm riêng nếu có; không đổi signature/public behavior. Ghi danh sách đã xóa và test liên quan. |
| `CODE-00-17.03` | Giảm lặp tại boundary DB/HTTP | Rà ba boundary Identity/app/ML; gom đúng phần timeout/DB error có semantics chung, giữ transaction ownership và mapping IntegrityError theo nghiệp vụ. Kiểm rollback, timeout và error envelope. Chỉ chỉnh middleware khi có bằng chứng và regression tương ứng. |
| `CODE-00-17.04` | Rà catch rộng ở RAG/ML/startup | Phân loại đủ 13 catch rộng của baseline; thu hẹp hoặc bỏ khi có bằng chứng, ghi lý do cho catch cần giữ. Test lỗi model/vector store, reranker fallback, readiness và cleanup khi startup lỗi. |
| `CODE-00-17.05` | Tinh giản service, script và client | Tìm nhánh trùng trong database_service; rà script lifecycle/CLI và client api.js/business.jsx. Chỉ gom helper phục vụ nhiều caller có cùng semantics; loại code chết sau khi kiểm cả router động, CLI và feature flag. Giữ mã nguồn ngoài/notice, fixtures và contract cần phân phối. |
| `CODE-00-17.06` | Nghiệm thu và bàn giao | Unit, Ruff, diff check, contract parity và build archive/wheel sạch; integration thực theo phần DB/SSO/RAG/ML bị ảnh hưởng. Đo diff và số nhánh trước/sau; ghi test chưa chạy cùng lý do. Cập nhật tài liệu/checkpoint, không đóng task cha nếu thiếu nghiệm thu bắt buộc. |

Thực hiện tuần tự .01 → .02 → .03 → .04 → .05 → .06; mỗi bước có diff nhỏ
và checkpoint riêng để có thể review/tiếp tục độc lập. Nếu bước khảo sát không tìm
thấy code dư thừa thì ghi kết luận có bằng chứng, không tạo refactor gượng ép.

## Kiểm thử và tiêu chí chấp nhận

- Dùng test hiện có làm nền; chỉ bổ sung test chứng minh invariant chưa được bao phủ.
  Tập trung auth/ownership, validation, transaction rollback, idempotency,
  timeout, response sau khi gửi headers, redaction và resource cleanup.
- Thay đổi transaction/session cần SQL integration thật với fixture riêng; thay đổi
  IAM cần test SSO thật; đổi RAG/ML adapter cần upstream phù hợp. Mock không thay
  bằng chứng tích hợp. Không chạy engine chỉ để sửa tài liệu.
- API/schema/error behavior và trạng thái DB giữ đúng contract. Trường hợp muốn sửa
  hành vi đang sai phải ghi riêng bug, expected/actual và regression, tránh giấu vào refactor.
- Đánh giá bằng logic/nhánh và lượng code lặp đã loại bỏ, số lớp phải đi qua,
  net diff mã viết tay và kết quả test. Không đặt chỉ tiêu xóa phần trăm số dòng
  hoặc ép số exception về 0; tách file hoặc chuyển code sang helper chưa tự chứng minh đơn giản hơn.
- Không thay schema/migration/dependency, bỏ tính năng hoặc xóa contract/test chỉ để
  đạt số liệu đẹp. Nếu phát hiện cần thay ngoài phạm vi, ghi rõ trước khi thực hiện.
- Lỗi drift contract hiện có phải được theo dõi riêng, không coi toàn suite đã xanh.
  Không commit/push/deploy nếu chưa có yêu cầu tương ứng.

## Chi tiết thực hiện theo từng đợt

### Đợt 1 — CODE-00-17.01: baseline và bảng quyết định

1. Đọc checkpoint hiện hành, mục PR00 trong plan tổng, Git status/branch/HEAD.
   Chụp danh sách diff đầu vào để phân biệt UI/layout có sẵn với phần refactor.
   Không stage/reset toàn tree hoặc lấy HEAD làm baseline nội dung working tree.
2. Kiểm tra interpreter và import đang trỏ vào `backend/flowbridge/`. Dùng môi trường
   hiện có; không tự cài lại vì uv host khác phiên bản yêu cầu.
3. Chạy unit suite, Ruff và diff check theo phần lệnh bên dưới. Ghi số test collected,
   passed/failed/skipped/deselected và nguyên nhân, không chỉ ghi exit code.
4. Lập inventory bằng AST cho `try/except/finally`, context manager và wrapper;
   rà call path bằng CodeGraph trước khi đọc/sửa module. Tìm cả route tạo động,
   feature flags, CLI, worker và consumer client trước khi kết luận code không dùng.
5. Tạo `docs/evidence/CODE-00-17.md` khi triển khai, chứa bảng quyết định và baseline
   có thời điểm/branch/HEAD cùng mô tả diff đầu vào. Không cần thêm tool audit lâu dài
   vào source; script khảo sát tạm đặt trong `.cache/` và không chứa secret.

Mỗi dòng inventory phải có: file/symbol/line ở revision khảo sát, caller/boundary,
exception dự kiến, tác động hiện tại, test hiện có/thiếu, quyết định, lý do và task đích.
Giá trị quyết định: `giữ`, `bỏ`, `thu hẹp`, `gom`, `cần thêm bằng chứng`.
Chưa đọc đủ call path thì giữ trạng thái cần thêm bằng chứng.

Hoàn tất khi tất cả catch backend, catch rộng của script và các wrapper đề xuất xóa
được ghi nhận; có danh sách thay đổi cụ thể cho đợt2. Nếu baseline lệch số liệu cũ,
ghi số thực tế, không chỉnh test để khớp số lượng lịch sử.

### Đợt 2 — CODE-00-17.02: bỏ đoạn dư thừa có bằng chứng

1. Chọn nhóm rủi ro thấp từ inventory: catch chỉ `raise` lại, wrapper chỉ chuyển tiếp,
   guard trùng trong cùng control flow, biến trung gian/nhánh không còn tác dụng.
   Đây là loại ứng viên cần tìm, chưa phải các lỗi đã xác nhận tồn tại.
2. Với mỗi ứng viên, ghi ngắn gọn luồng trước/sau và điều kiện không đổi hành vi.
   Không coi kiểm tra DTO trùng kiểm tra business policy hoặc DB constraint;
   kiểm tra sớm có thể cần cho status code và rollback đúng.
3. Sửa một nhóm trách nhiệm mỗi lần; giữ public signature, exception cause được dùng,
   metadata/retry/header và thứ tự validation có ý nghĩa.
4. Chạy test đúng module ngay sau nhóm sửa. Chỉ thêm regression cho behavior chưa có
   bằng chứng, ví dụ lỗi lập trình phải tới boundary thay vì bị biến thành thành công.
5. Ghi net diff và nhánh/code lặp đã bỏ vào evidence, cập nhật checkpoint.

Hoàn tất khi từng đoạn bị xóa có caller và test chứng minh; không có catch mới để
bù cho catch đã bỏ, không mất finally/context cleanup. Nếu không có ứng viên đạt
điều kiện, kết luận không cần thay đổi và chuyển đợt tiếp theo.

### Đợt 3 — CODE-00-17.03: DB, transaction và HTTP

File đích: `identity/dependencies.py`, `supplier_assistant/router.py`,
`supplier_assistant/prediction/router.py`, `db.py`, `shared/errors.py`.

1. Vẽ luồng session → begin → service → commit/rollback → error response cho ba API
   boundary. Ghi rõ exception có thể phát sinh trước yield, trong service và lúc commit.
2. So sánh timeout của request/DB/pool, isolation level, lock timeout và quyền của từng
   boundary. Chỉ gom phần dịch lỗi thật sự giống nhau; giữ owner transaction hiện có.
   Nếu helper buộc thêm nhiều cờ/callback để mô tả khác biệt, ưu tiên giữ code trực tiếp.
3. Phân biệt timeout chờ pool, command timeout, unique conflict và lỗi DB khác.
   Không mở rộng catch tới toàn bộ service làm lỗi lập trình bị báo nhầm upstream503.
4. Kiểm tra FastAPI handler và ErrorBoundaryMiddleware: lỗi trước headers,
   lỗi trong streaming sau headers, correlation, log an toàn và cancellation.
   Chỉ bỏ một lớp khi test chứng minh lớp còn lại bao phủ đúng trách nhiệm đó.
5. Kiểm tra request cancellation vẫn truyền đúng và tài nguyên được giải phóng;
   không nuốt CancelledError/BaseException vào một response nghiệp vụ.

Kiểm thử tối thiểu: HTTP boundary, authentication, Identity, OpenAPI composition,
supplier boundaries và prediction API. Khi thay session/transaction/DB mapping,
chạy thêm SQL integration liên quan với fixture riêng. Assert rollback bằng kết nối
độc lập, replay idempotency không ghi trùng, denied request không đổi dữ liệu,
timeout/header/error code đúng. Không dùng API override/mock thay chứng cứ SQL/SSO.

Hoàn tất khi mapping lỗi có owner rõ, phần lặp đã giảm nếu có thể, và các khác biệt
nghiệp vụ còn nguyên. Thiếu engine để nghiệm thu phần đã sửa thì giữ task đang dở.

### Đợt 4 — CODE-00-17.04: RAG, ML, startup và readiness

Danh sách 13 catch rộng trong khảo sát17/10; xác minh lại trước sửa:

| File tính từ backend/flowbridge | Số catch rộng | Nội dung cần xác minh |
| --- | ---: | --- |
| `main.py` | 2 | Startup DB/schema: phân biệt lỗi upstream và lỗi lập trình; giữ cleanup và thông báo không chứa credential. |
| `operations/service.py` | 1 | Readiness khi DB lỗi; không trả ready sai, không che lỗi ngoài probe. |
| `shared/errors.py` | 1 | Boundary cuối, đặc biệt lỗi sau headers; thường cần giữ, chưa kết luận dư thừa. |
| `supplier_assistant/knowledge/vector_store.py` | 4 | Open/write/read/query Chroma; xác định loại lỗi SDK, tránh bọc lại nhiều lần. |
| `supplier_assistant/knowledge/embeddings.py` | 1 | Load model/cache; giữ lỗi thân thiện và không tự tải model. |
| `supplier_assistant/knowledge/retrieval.py` | 1 | Rerank fallback: điều kiện fallback hợp lệ, cảnh báo và ordering sau lỗi. |
| `supplier_assistant/knowledge/postgres_cli.py` | 1 | CLI exit code và stderr an toàn; không báo thành công khi thất bại. |
| `supplier_assistant/prediction/model.py` | 2 | Trạng thái sau load fail/infer fail, output không hợp lệ và queue release. |

1. Tách mỗi nhóm upstream thành một lát sửa/test; không thay toàn bộ13catch cùng lúc.
2. Với mỗi catch, xác định exception từ package/version hiện dùng và phạm vi operation;
   tham khảo source/doc upstream nếu cần trước khi thu hẹp loại lỗi.
3. Kiểm error chaining và thông tin sẽ tới CLI/server logs. Bỏ catch không được làm
   lộ token/DSN hoặc làm mất thông tin vận hành cần thiết đã được lọc.
4. Bổ sung case thực sự thiếu: cache/model không có, store hỏng, generation ghi thất bại
   không được kích hoạt, rerank fail, ML output sai, startup fail và cleanup.
5. Chạy unit rồi integration đúng adapter. Model/engine chưa sẵn sàng thì không tự
   download/cài extras; giữ phần thay đổi chưa nghiệm thu mở và ghi điều kiện còn thiếu.

Hoàn tất khi13catch được quyết định rõ, cộng các catch mới phát hiện trong baseline
nếu source thay đổi; mỗi catch còn giữ có lý do. Không yêu cầu giảm xuống một số định sẵn.

### Đợt 5 — CODE-00-17.05: service, script và client

Thực hiện ba lát độc lập, mỗi lát có checkpoint:

1. **Service:** rà `supplier_assistant/database_service.py`, repository/policy và
   prediction service. Ưu tiên đoạn tạo payload/transition/lookup lặp cùng semantics.
   Giữ guard state/revision/ownership và phép tính tiền. Chỉ tách helper khi giảm được
   phần lặp và số khái niệm phải hiểu; không xây generic CRUD engine hoặc chia service
   theo số dòng. ORM3.330dòng nằm ngoài mục tiêu cắt nếu chỉ phản ánh schema đang dùng.
2. **Script:** rà `dev_stack.py`, `gateway_stack.py`, `container_dev.py` và các CLI
   chuẩn bị/check/bootstrap. Phân biệt catch ở CLI exit với catch bảo toàn fixture sau
   lỗi/interrupt. Giữ ownership guard, manifest recovery và cleanup có đối soát;
   không tự sửa source demo bên ngoài hoặc seed data chỉ để chuẩn hóa style.
3. **Client:** rà `clients/sso/src/api.js`, `business.jsx`, `app.js` và `server.mjs`.
   Gom xử lý lỗi HTTP dùng chung nếu phù hợp; giữ khác biệt lỗi mạng/401/403/409,
   refresh phiên, thông báo người dùng và retry cùng idempotency key.
   Không tự retry POST có thể ghi trùng hoặc bỏ thông báo chỉ để code ngắn hơn.

Chạy SQL integration cho nghiệp vụ bị sửa, unit runner lifecycle cho script và
UI browser suite cho client. UI mock suite chỉ chứng minh UI; auth/session thay đổi
cần E2E SSO thật. Không coi unit lifecycle thay bằng chứng cleanup Docker thật.

### Đợt 6 — CODE-00-17.06: kiểm cuối và bàn giao

1. Chạy full unit một lần trên code cuối; chạy lại đúng suite sau thay đổi mới nếu có.
   Phân biệt lỗi baseline, lỗi mới và kiểm tra không chạy; mọi regression mới phải xử lý.
2. Kiểm Ruff, format các file Python đã sửa, diff check, notice GPL và contract parity.
   Không mass-format file không liên quan hoặc sửa snapshot ngoài phạm vi.
3. Build source tar.gz và wheel, giải nén vào thư mục mới ngoài source tree,
   rebuild/install hoặc import wheel độc lập. Kiểm archive không chứa secret,
   node_modules/venv/cache/binary tải về; giữ contract và license metadata cần thiết.
4. Hoàn tất integration theo ma trận tác động bên dưới. Xác minh trạng thái fixture,
   cleanup và session đã kết thúc trước khi ghi pass hoặc retry.
5. So sánh với baseline working tree: nhánh bị bỏ, catch rộng giữ/thu hẹp/bỏ,
   code lặp giảm, số lớp gọi giảm, net diff mã viết tay và kết quả test.
   Không tính xóa test/contract hoặc chỉ di chuyển file là thành quả tinh giản.
6. Cập nhật evidence, CHANGELOG nếu hành vi/hướng dẫn bị ảnh hưởng, plan và hai
   checkpoint. Chỉ tick cha CODE-00-17 khi mọi task con và nghiệm thu bắt buộc đạt.

## Lệnh kiểm tra và ma trận test

Các lệnh dưới chạy tại root FlowBridge bằng PowerShell. Đây là lệnh dự kiến,
chưa chạy cho đợt refactor. Dùng interpreter hiện có, không cần uv sync/cài thêm.

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
& ./.venv/Scripts/python.exe -c "import flowbridge; print(flowbridge.__file__)"
& ./.venv/Scripts/python.exe -m pytest tests/unit --collect-only -q
& ./.venv/Scripts/python.exe -m pytest tests/unit -q --tb=short
& ./.venv/Scripts/python.exe -m ruff check backend tests scripts
git diff --check
```

Format check chỉ truyền các file Python thực sự sửa. Không dùng format toàn repo
để phát sinh diff ngoài scope. Nếu pytest treo, lưu session, xác minh outcome/dừng
lượt cũ trước retry; không chạy nhiều lượt cùng DB/fixture hoặc suy diễn đã pass.

| Phần thay đổi | Suite unit hiện có cần chọn | Nghiệm thu bổ sung khi có tác động |
| --- | --- | --- |
| HTTP/validation/redaction | `test_http_boundary.py`, `test_authentication.py`, `test_database_api_contract.py`, `test_openapi_composition.py` | Gateway/SSO thật nếu đổi luồng auth hoặc middleware ảnh hưởng proxy. |
| DB/Identity | `test_identity_core.py`, `test_identity_directory.py`, `test_database_tooling.py`, `test_supplier_database_boundaries.py` | `test_identity_accounts_database.py`, `test_identity_core_database.py`, `test_supplier_database.py`, chọn theo phần sửa. |
| Startup/readiness | `test_lifespan.py`, `test_operations.py` | DB startup/readiness thật nếu thay probe hoặc lifecycle DB. |
| RAG | `test_knowledge.py`, `test_knowledge_http.py`, `test_knowledge_postgres.py`, `test_knowledge_postgres_http.py` | `test_knowledge_chroma.py`, `test_knowledge_pgvector_database.py` theo adapter sửa. |
| ML | `test_prediction_model.py`, `test_prediction_features.py`, `test_prediction_api.py` | `test_prediction_database.py` và smoke runtime/model nếu đổi model adapter. |
| Script/lifecycle | `test_dev_stack.py`, `test_gateway_profile.py`, `test_supplier_seed.py` theo file sửa | Docker runner có isolation/cleanup khi thay orchestration; không chạy seed/migration trên dữ liệu hiện có để test refactor. |
| UI | `npm run build`, `npm run test:ui` tại `clients/sso` | `npm run test:e2e` với profile SSO thật khi sửa auth/session; UI suite không thay integration. |

Tên file unit tính từ `tests/unit/`, integration tính từ `tests/integration/`.
Khi chạy integration chọn file cụ thể và dùng `-o addopts="" -m integration` để
không bị marker mặc định loại bỏ. Trước chạy đọc fixture/profile trong tài liệu
SSO/DEV-CONTAINERS/GATEWAY/RAG/ML hiện hành; không chạy toàn bộ integration bằng
một lệnh thiếu cấu hình. Dùng fixture UUID hoặc stack test riêng, không reset dev
state và không tự migrate để vượt revision guard. Required suite bị skip hoặc
engine unavailable được ghi chưa đạt, không tính là pass.

Lệnh build sau khi nghiệm thu phần runtime:

```powershell
& ./.venv/Scripts/python.exe -m hatchling build
```

Archive sạch phải được tạo lại từ payload cuối; không dùng artifact của lượt layout
trước để xác nhận refactor mới. Ghi vị trí local và kết quả rebuild ngoài tree trong evidence.

## Chia lượt làm và khôi phục khi gần hết context

| Lượt gợi ý | Phần thực hiện | Điểm dừng có thể bàn giao |
| --- | --- | --- |
| A | .01, rồi .02 nếu baseline và inventory đã rõ | Có bảng quyết định, diff rủi ro thấp và kết quả test của phần sửa. |
| B | .03 | DB/HTTP đạt kiểm thử tương ứng, hoặc ghi rõ integration còn thiếu. |
| C | .04, chia riêng Chroma/RAG và ML nếu cần | Mỗi adapter có quyết định catch và kết quả kiểm thử độc lập. |
| D | .05, từng lát service/script/client | Hoàn tất từng lát trước chuyển sang lát tiếp theo. |
| E | .06 | Báo cáo trước/sau, archive sạch và trạng thái task chính xác. |

Không gắn số lượt này với cam kết token hoặc thời gian. Có thể gộp lượt khi khối
lượng thực tế nhỏ; dừng ở boundary đã kiểm thử thay vì bắt đầu thay đổi lớn sát cuối lượt.
Không để toàn bộ kiểm thử đến lượt E mới chạy.

Checkpoint mỗi lượt ghi: task con, branch/HEAD/diff đầu vào, file và symbol đã sửa,
quyết định giữ/bỏ/thu hẹp, lệnh/kết quả, lỗi còn mở, session chưa biết outcome,
fixture/engine đang dùng và bước tiếp cụ thể. Nếu đang có lỗi thì ghi đang dở;
không tick hoàn tất dựa trên việc file tồn tại. Lượt sau đọc checkpoint và xác minh
source/process trước tiếp tục. Không tự commit hoặc hoàn tác diff có sẵn để tạo điểm dừng.

## Trạng thái

- [x] `CODE-00-17.01` Baseline và bảng quyết định đầy đủ (evidence `docs/evidence/CODE-00-17.md`).
- [ ] `CODE-00-17.02` Loại xử lý dư thừa đã chứng minh.
- [ ] `CODE-00-17.03` Boundary DB/HTTP.
- [ ] `CODE-00-17.04` Catch rộng RAG/ML/startup.
- [ ] `CODE-00-17.05` Service/script/client.
- [ ] `CODE-00-17.06` Nghiệm thu và bàn giao.
