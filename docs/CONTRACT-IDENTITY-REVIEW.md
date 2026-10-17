# Sửa review contract và Identity — 16/10/2026

Repo FlowBridge, branch `huuhuandev_features`, base HEAD
`d03450c4ca255eeb2ef60bb75e01d17e2cd12174` cộng working-tree diff Identity có sẵn.
Không commit/push/deploy; không dependency/migration mới. Các task này không đóng
UserContext/JobRegistry production, Gateway hoặc CP-002.

| Task | Kết quả |
|---|---|
| CODE-00-15 | Giữ canonical Core tại 03-api, sync snapshot source archive, manifest và draft upstream hash sau đối chiếu semantic/shared schemas; Core dùng dialect mặc định OpenAPI 3.1 |
| CODE-01-03.03 | URI validation/metadata bốn field, correlation parameter và header Retry-After của response 409; test parity schema/security/response cho config/me |
| CODE-01-05.03 | Account/grant SQL và audit/event sink do Identity sở hữu; giữ schema, transaction/lock, role rules, retry và outbox payload |
| CODE-00-16 | Một OpenAPI composer/cache cho app/Identity/ML/RAG; từ chối path/method, operationId và component collision khác nghĩa; giữ namespace và profile |

Runtime giữ 46 operation app/AI, 48 khi bật Identity; ML thêm 5 thành 51/53.
RAG development giữ profile riêng một operation; không chứng nhận Files/ACL production.
Không refactor toàn SupplierDatabaseService hoặc chia model/service ngoài use case này.

## Kiểm đã chạy

- `python -m pytest tests/unit -q --tb=short --basetemp .cache/pytest-review-unit-final-02`:
  **263 passed in 20.30s**, session 88390, exit 0. Gồm sync/provenance guard,
  URI/schema/parameter/security/response parity, composition/collision/cache,
  route registry 46/48/51/53 và regression hiện có. Dùng Python trong `.venv`.
- `python -m pytest tests/integration/test_identity_accounts_database.py -m integration -q
  --tb=short --basetemp .cache/pytest-review-identity-sql-01`:
  **1 passed in 7.19s**, exit 0, Supabase thật. Kiểm link/bootstrap retry giữ profile,
  một grant/một pending event và payload đúng; chặn first-admin/admin giả;
  dry-run rollback; lỗi sau khi ghi audit rollback user/grant/outbox. Kết nối mới
  xác nhận không còn fixture user/department. Directory metadata tiêm riêng để
  cô lập SQL; không claim test này xác minh IdP.
- Ruff check các module backend và scripts/tests bị tác động đạt sau format.
- Sync Core `--write` đã chạy sau đối chiếu chỉ khác dialect, rồi kiểm checksum
  và sáu shared schema bằng regression; app/AI snapshot giữ nguyên.

## Lượt lỗi và giới hạn

- Thư mục tạm pytest mặc định không ghi được trong sandbox: 8 pass/5 setup errors
  ở lượt sync/composition đầu; đổi `--basetemp` vào `.cache` riêng. Hai lượt có
  asyncio trong sandbox không hoàn tất, đã dừng session 8186/59576; kết quả không
  tính pass. Suite unit cuối chạy ngoài sandbox, không đổi policy runtime.
- Parity mới ban đầu bắt thiếu response header 409; đã bổ sung theo YAML.
  URI FormatChecker không bật validator URI khi thiếu optional format dependency;
  đã dùng TypeAdapter AnyUrl của Pydantic có sẵn, negative cases đạt. Một lượt
  full regression trước sửa header có 261 pass/2 fail; lượt cuối 263 pass.
- `tests/integration/test_supplier_database.py -m integration -k 'bootstrap or first_admin or admin'`:
  **6 setup errors, 13 deselected**. DB báo revision `0003_core_knowledge`, checkout
  chỉ có 0001/0002, fixture bắt buộc `0002_api_idempotency`. Dừng trước tạo fixture;
  giữ guard và revision, không migrate/downgrade hoặc nhận suite này đã pass.
  Cần đối chiếu nguồn migration 0003 trước khi chạy lại toàn regression database.
- Một lệnh kiểm tool đã gọi nhầm uv.exe qua Python nên báo SyntaxError; sửa sang gọi executable trực tiếp, `uv lock --check --offline` đạt graph142. Build source/wheel trước lỗi gọi tool đã thành công.
- Không chạy lại browser/IAM, inference/Chroma, gateway hoặc audit OCI image trong
  lượt này. Evidence IAM/browser cũ giữ nguyên trong IDENTITY-CORE-TEST-RESULTS.md.

## Build và kiểm cuối

- Hatchling build source/wheel đạt: 167 source members trong một root `flowbridge-0.1.0`,
  65 wheel members; audit actual sensitive config trong bộ nhớ và denylist cache,
  dependency binary, engine/model/node_modules đạt. Không in secret hoặc giá trị .env.
- Giải nén ngoài checkout tại `contract-identity-clean-b1ed3f35/flowbridge-0.1.0`
  trong workspace visualizations; rebuild source/wheel bằng toolchain đang có đạt,
  không cài dependency hoặc truy cập engine.
- Regression từ source giải nén **91 passed in 15.47s**, session 74839 exit 0:
  sync Core độc lập, composition, Identity, database API contract, prediction và
  RAG HTTP unit. Assert package import thuộc source giải nén, không dùng checkout.
- `python -I` import wheel rebuild từ archive đạt; OpenAPI 48/53, unique operationId,
  URI format và mọi local ref resolve. Không mở database/IAM/model trong smoke này.
- Core/app/AI sync, Ruff check toàn backend/tests/scripts, format 29 file bị tác động
  và `git diff --check` đều đạt. Header GPL/no standalone-string comments kiểm cho
  code mới/đổi; không thay LICENSE/holder hoặc license nguồn ngoài.

Task CODE-00-15/CODE-00-16/CODE-01-03.03/CODE-01-05.03 hoàn tất trong scope trên.
Không CP mới vì context/resource và JobRegistry production vẫn chưa nghiệm thu.
Artifact build local phục vụ verification, không phải release đã publish. Bản cuối sau cập nhật evidence giữ167/65 members; audit source/wheel đạt, mọi byte runtime package trong wheel bằng wheel clean đã import.
