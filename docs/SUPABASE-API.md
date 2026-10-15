# API nghiệp vụ với PostgreSQL/Supabase

Code dùng GPL-3.0-or-later theo LICENSE. Giấy phép backend không cấp quyền đối với
dữ liệu Supabase, upload, tài liệu hoặc model bên ngoài.

## Cấu hình và chạy

Chạy từ thư mục FlowBridge, sau setup dependency trong README. Điền `.env` theo
`.env.example` và hướng dẫn [database](../alembic/README.md). Không ghi đè `.env`
đang có hoặc đưa credential vào Git.

```dotenv
FLOWBRIDGE_DOCS_ENABLED=true
FLOWBRIDGE_DATABASE_URL=
FLOWBRIDGE_DATABASE_HOST=YOUR_SESSION_POOLER_HOST
FLOWBRIDGE_DATABASE_USER=postgres.YOUR_PROJECT_REF
FLOWBRIDGE_DATABASE_PORT=5432
FLOWBRIDGE_DATABASE_NAME=postgres
FLOWBRIDGE_DATABASE_PASSWORD='YOUR_DATABASE_PASSWORD'
FLOWBRIDGE_DATABASE_SSL=require
FLOWBRIDGE_DATABASE_POOL_MODE=null
```

URL tường minh ưu tiên hơn các trường riêng. Password ở trường riêng không cần
percent-encode. Dùng Session pooler hoặc direct connection; không dùng transaction
pooler cho quy trình migration hiện tại.

```powershell
uv run --locked python scripts/check_db.py
uv run --locked alembic upgrade head
uv run --locked alembic current
uv run --locked python -m flowbridge.main
```

Revision `0002_api_idempotency` thêm một bảng operational, không chạy lại seed hoặc
xóa dữ liệu của 27 bảng baseline. Không sửa revision 0001 đã áp dụng. Đổi schema sau
này cần revision mới. API không tự tạo bảng trong startup. Dùng `/_ops/ready` để
kiểm kết nối; endpoint này không chứng nhận IdP/workflow/ML sẵn sàng.

## Danh tính và quyền

Backend gửi bearer token tới authorization server qua introspection theo
[RFC 7662](https://www.rfc-editor.org/rfc/rfc7662.html), dùng HTTP client có timeout
và không theo redirect. Không tự parse/verify JWT hoặc tự viết SSO engine.

```dotenv
FLOWBRIDGE_OIDC_ISSUER=https://YOUR_IDP/realms/YOUR_REALM
FLOWBRIDGE_OIDC_AUDIENCE=flowbridge
FLOWBRIDGE_OIDC_INTROSPECTION_URL=https://YOUR_IDP/realms/YOUR_REALM/protocol/openid-connect/token/introspect
FLOWBRIDGE_OIDC_CLIENT_ID=YOUR_BACKEND_CLIENT
FLOWBRIDGE_OIDC_CLIENT_SECRET='YOUR_BACKEND_CLIENT_SECRET'
```

Provider phải trả `active=true`, đúng `iss`, đúng `aud`, `sub` không trống và `exp`
chưa hết hạn. Cấu hình client có quyền introspect access token cho audience này.
Backend map **exact issuer + subject** sang `users`; user/role phải active, grant
chưa revoked, đúng account type và scope. Không tin `X-Role`, `X-User-Id` hoặc role
từ payload. Chưa có IdP: thiếu bearer trả 401, có bearer nhưng chưa cấu hình upstream
trả 503; không có chế độ giả danh qua HTTP. Bootstrap/mapping identity làm riêng,
không tự provision/cấp grant từ login payload.

EMPLOYEE chỉ xem/sửa PR của mình. APPROVER/WAREHOUSE đọc resource trong phòng ban
được cấp; quyền đọc không cấp quyền sửa PR người khác. Service role chỉ dùng cho
service account và internal routes; admin routes yêu cầu ADMIN. Mỗi project database
hiện là một ứng dụng single tenant; không có tenant claim hoặc tenant isolation giả.
Gateway phải tách public/internal/admin; kiểm bearer ở gateway không thay policy này.
RLS giữ nguyên: client `anon/authenticated` không có quyền truy cập bảng trực tiếp.
Không đặt DSN hoặc database credentials vào frontend.

## Phạm vi endpoint

46 operation của app/AI contract được mount khi có DB; 37 operation có đường xử lý
database, 9 operation báo dependency chưa sẵn sàng:

| Nhóm | Xử lý hiện tại |
|---|---|
| Catalog, profile, PR, proposal/PO reads | Truy vấn PostgreSQL, lọc quyền và phân trang trong SQL |
| Tạo/sửa/gửi/hủy PR | Snapshot giá từ supplier_products, Decimal 4 chữ số, revision/owner, outbox cùng transaction |
| Analysis register/claim/result/failure | Trạng thái, snapshot, kiểm revision/lineage và lưu kết quả; không chạy model |
| Tạo proposal nội bộ | Task/report/candidate hợp lệ, đủ dòng, cấp version dưới khóa PR, outbox pending |
| Phát PO nội bộ | Cần proposal current đã approved, nhóm supplier + currency, tạo items/schedules và outbox |
| Receipt | Khóa PR/PO/schedule, không nhận vượt lượng, cập nhật trạng thái bằng transaction |
| Supplier confirmation | Hash token gắn đúng PO, kiểm hạn/revoke/consume, revision chi tiết append-only |
| Email retry/result | Retry chỉ từ failed, delivery_unknown cần đối soát; outbox pending, không gửi email |
| User update/grant/revoke | Kiểm ADMIN, account/scope/department, audit outbox; không tự cấp quyền |
| Data lookup | Query theo bộ lọc; as-of chỉ cho receipt/prediction/report có timestamp quan sát |
| Proposal decision, user provision | 503: chưa có workflow authoritative/identity provisioning adapter |
| Chat/history, predict, compare, risk flow, retrieve/index knowledge | 503: chưa có model/chat/document adapter |

As-of cho catalog, PR hoặc PO hiện tại không có lịch sử đầy đủ sẽ trả 503. Không dùng
dữ liệu hiện tại để giả lịch sử. PR giữ ID của dòng còn lại khi sửa; xóa dòng đã được
history tham chiếu trả 409 theo FK, không xóa lịch sử. Một analysis task đang queued/running
chặn tạo task trùng PR. Claim theo trạng thái contract hiện tại; chưa có lease/recovery worker.

Các thao tác ghi cần `Idempotency-Key` đúng contract. Scope do server tạo từ actor và
operation; fingerprint gồm path/query/body. Cùng key/cùng input trả response đã lưu,
input khác trả 409. Advisory transaction lock bảo vệ các request đồng thời; response,
nghiệp vụ và outbox commit cùng transaction. Quyền hiện tại được kiểm lại trước replay.
Supplier replay chỉ với cùng token/key, không mở lại quyền dùng token cho lệnh mới.
Không tự xóa idempotency rows trước khi chốt retention policy.

Outbox là hàng đợi bền vững trong DB; chưa có dispatcher/consumer được triển khai.
Không coi outbox pending là workflow đã nhận, PO đã gửi email, model đã chạy hoặc
nhà cung cấp đã nhận đơn. Lỗi SQL/constraint/timeout trả envelope đã che thông tin.

## Kiểm thử

```powershell
# Mặc định: unit offline, không gọi Supabase.
uv run --locked pytest -q
# Chọn rõ integration: thiếu DB/migration phải fail, không skip.
uv run --locked pytest tests/integration -m integration -q
```

Integration dùng HTTPX ASGITransport và lifespan thật với PostgreSQL trên Supabase;
không qua TCP server/gateway. Chỉ dependency danh tính được override trong test;
policy users/roles/resource và toàn bộ SQL vẫn chạy thật. Bốn payload tổng hợp chỉ nằm trong `tests/integration/analysis-payloads.json`,
không đóng gói trong wheel hoặc phục vụ qua API. Các fixture thường được rollback toàn bộ. Test concurrency riêng commit bằng API, xác minh qua kết nối DB
độc lập rồi xóa đúng dữ liệu của user test UUID ngẫu nhiên trong `finally`. Nếu process
bị kill giữa test commit và cleanup, cần đối soát fixture trước khi chạy lại.
Không dùng suite này làm bằng chứng tích hợp SSO/workflow/model.


## Cần bổ sung để API không bị chặn

Backend đã bỏ API sample và hai biến `FLOWBRIDGE_SAMPLE_*`. Cờ sample còn trong
`.env` cũ không còn tác dụng; không có đường HTTP giả danh để bỏ xác thực.

| Hiện tượng | Cần bổ sung |
|---|---|
| 404 toàn bộ API nghiệp vụ | Cấu hình kết nối Supabase; bootstrap không DSN chỉ có probes. |
| 401 ở API bảo vệ | Access token hợp lệ trong `Authorization: Bearer …`. |
| 503 ở bước xác thực | IdP hỗ trợ RFC 7662 và đủ 5 biến `FLOWBRIDGE_OIDC_*` ở trên; client có quyền introspect, issuer/audience khớp token. |
| 403 sau xác thực | Map đúng `external_issuer` + `external_subject` vào user active; cấp role/grant active, đúng human/service và global/department. |
| 503 quyết định proposal | Workflow engine và adapter authoritative xác minh task/assignee/version, kèm worker chuyển outbox sang engine. |
| 503 provision user | Adapter provisioning tới IdP và quy trình bootstrap ADMIN đầu tiên; không tự cấp ADMIN khi đăng nhập. |
| 503 chat/history, predict, compare, risk flow | Adapter chat/model/analysis có dữ liệu đầu vào, version và worker thực thi. |
| 503 retrieve/index knowledge | Kho tài liệu/index và adapter kiểm ACL, provenance, version. |
| PR/task/email dừng ở queued/pending | Durable outbox dispatcher và consumer analysis/workflow/email; timeout/retry/idempotency và đối soát email outcome chưa rõ. |
| 409 phát PO từ proposal pending | Hoàn tất phê duyệt thật trước; database hiện yêu cầu proposal current đã approved. |

Chỉ thêm credential database không cấu hình được bước xác minh bearer. Adapter hiện
đọc token qua RFC 7662; nếu chọn cơ chế xác thực khác, cần adapter tương ứng và test
với provider thật. Các user/role tổng hợp trong seed không tự liên kết với tài khoản
đăng nhập. Bootstrap identity phải kiểm chứng issuer/subject và cấp quyền có chủ đích.
Không cần tắt kiểm quyền hoặc sửa RLS để mở các API đã triển khai.
