# ADR 0006 — Identity core với binding theo deployment

Ngày: 16/10/2026. Trạng thái: áp dụng. Tiếp nối [ADR SSO](SSO-ADR.md).

## Quyết định

Deployment hiện có một database nghiệp vụ và một issuer Keycloak. Chọn **single tenant, một application binding mỗi deployment** qua `Settings.core_identity` (`FLOWBRIDGE_CORE_IDENTITY` dạng JSON). Operator xác định tenant/application và gắn database, issuer, audience, public clients, service clients tương ứng. Không suy tenant từ username/email, header, prompt hoặc payload. Đây không phải cơ chế cách ly nhiều tenant trong cùng database; muốn mở rộng cần registry/migration và test cách ly riêng.

Không có tenant mặc định. Khi binding chưa được cấu hình, hai route core không mount và 46 operation app/AI giữ nguyên. Khi cấu hình thiếu/mâu thuẫn, Settings từ chối startup. Backend dùng Keycloak endpoints của đúng issuer đã cấu hình; tiếp tục RFC 7662, không tự viết JWT verifier hoặc phát token mới.

Adapter `identity/repository.py` đọc users/roles/user_roles/departments bằng metadata chung và một SQL statement mỗi lần resolve. Không import repository/policy Supplier Assistant. Chỉ identity active, role active, grant chưa thu hồi, đúng account type/scope và department active mới được nhận. Không cache quyền; tắt user/role hoặc thu hồi grant tác động lần resolve tiếp theo.

Principal public giữ năm trường contract core 0.2.0. `id` là ID registry nội bộ, không đánh đồng với IdP subject. Roles được định danh `application:ROLE`, hoặc `application:ROLE@department`. Permissions là giao của allowlist `role_permissions` và OAuth scopes do IAM đã xác minh. Grant phòng ban chỉ cho `identity:read` ở lớp này; quyền dữ liệu/phê duyệt theo tài nguyên phải có receiver policy, không được tự nâng thành quyền toàn tenant. ADMIN không có wildcard hoặc quyền duyệt mặc định.

Service phải nằm trong cả OIDC registry và core binding, có scope token cần thiết, có global role DB khớp binding; directory còn xác minh subject thuộc đúng service-account client. `/identity/me` chỉ nhận human, không fallback service thành user khi thiếu scope.

## Delegation và job

`X-User-Context` dùng **user access token do chính IAM tin cậy phát hành**, được introspect với issuer/audience/expiry/client/type như bearer. Không phát JWT riêng hay nhận JSON actor. Service cần cả `context:delegate` và permission yêu cầu; user cần permission đó; kết quả là giao quyền và cùng tenant/application. Duplicate/missing/invalid header, service giả user hoặc kèm JobContext đều bị từ chối. Token này là credential: chỉ chuyển qua kênh nội bộ bảo mật, không log/persist. Chỉ truyền cho receiver thuộc audience đã đăng ký; không dùng user token cho audience khác.

`resolve_job_context` là boundary cho adapter `JobRegistry` tin cậy của ingestion/workers về sau. Đối chiếu job active/expiry, tenant/application/client, dataset, permission và quyền hiện tại của initiator. Kết quả chỉ `data:read:<dataset>`, không cấp workflow permission. **Chưa có registry production**, nên thiếu adapter trả 503; không đọc `analysis_tasks.input_snapshot` hoặc tin job ID thay registry. CODE-01-02.04 vẫn chưa đạt tích hợp thật. Không mở endpoint Data/Workflow chưa triển khai chỉ để chứng minh context.

## IAM và MFA

Generator có tùy chọn tạo profile core mới: scope `identity:read`, browser flow dùng username/password và OTP REQUIRED, cookie SSO qua phiên đã xác thực. Không thay realm/profile có sẵn, không bật direct-access password grant, implicit flow hoặc wildcard redirect. Browser B hiện là client PKCE thứ hai dùng kiểm SSO và trường mobile_client_id trong profile test; chưa chứng minh ứng dụng mobile native.

Backend tin chính sách MFA của issuer đã quản lý; introspection không được diễn giải là tự chứng minh số factor. Operator phải import/kiểm flow và các client khi triển khai realm khác. Native H2/TLS local là môi trường test, không chứng nhận persistence/HA/network production.

Không migration/dependency mới. Build dùng toolchain hiện có; source không đóng gói state/token/OTP secret/engine distribution. Public config chỉ xuất endpoints, public client IDs, scopes và PKCE flag.

## Làm rõ sau review contract/Identity ngày 16/10/2026

Accounts/bootstrap sở hữu `IdentityAccountRepository` và `IdentityAuditSink` /
`IdentityOutbox`; adapter dùng metadata/schema chung và transaction caller hiện có.
Không tạo schema user/role thứ hai hoặc gọi service nghiệp vụ mua hàng để ghi event.
Ứng dụng nối adapter qua session, giữ nguyên chính sách role/grant và wire API.
Core URI được validate bằng Pydantic và serialize giữ chuỗi ban đầu; correlation
được công bố trong OpenAPI nhưng vẫn do middleware chuẩn hóa trước router.
Composition chuyển về `shared/openapi.py` với kiểm collision; contract/provenance
và phạm vi kiểm chứng ghi trong [evidence](CONTRACT-IDENTITY-REVIEW.md).
