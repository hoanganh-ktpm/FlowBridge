# API contracts — ứng viên thống nhất v0.1.1

Trạng thái: **REVIEW CANDIDATE**, ngày 23/10/2026. Đây là đề xuất cụ thể để team review và triển khai, chưa phải API đã chạy hoặc quyết định đã được tất cả thành viên duyệt. Không yêu cầu triển khai đủ 20 bảng trước luồng P0 đầu tiên.

Bản v0.1.1 đã được rà lại độc lập theo Core/Identity và Domain/Platform. [Báo cáo rà soát](API-CONTRACT-REVIEW.md) ghi các điểm đã sửa, bằng chứng kiểm tra và giới hạn còn lại. Specification hiện có 84 operation, 63 fixture (26 payload sai có chủ đích).

## 1. Căn cứ và phạm vi

- Thiết kế `dxos_core_db` v0.2, 29 trang: nguồn tên cột, quan hệ, enum và điều kiện governance. SHA256: `492959b86ef73a4379ca738125a55438f266a93738aa48be2b3461eaef0270cc`.
- Hướng dẫn nền tảng DX-OS/FlowBridge, 11 trang: bổ sung luồng đăng ký → công bố → xin quyền → provision → đọc, ranh giới Agent/Tool/Operation và workflow trung lập engine. SHA256: `c2f1fe728276bec488c069d3e7cdc9ef6531e01da87134117ffa2cccb5621914`.
- Repo tại `33f6c4e`: kiến trúc Core/Platform/Business/Portal; error envelope và `X-Correlation-Id` đã có.
- Chưa đọc được chat ChatGPT người dùng dẫn. Không gán các lựa chọn API dưới đây cho nội dung chat đó. Ảnh chỉ gợi ý tổ chức thư mục.
- Hai PDF là tài liệu tham chiếu thiết kế, không phải lệnh chạy migration hoặc quyền triển khai. Không sao chép PDF vào source release.

**Bám theo tài liệu:** single organization, PostgreSQL-first, Core giữ metadata, Business APIs ghi dữ liệu, quyền đọc/execute/ghi tách nhau, grant chỉ ACTIVE sau verify, workflow chưa chọn engine, không có Core File/upload/RAG trong phạm vi.

**Lựa chọn mới cần team duyệt:** đường dẫn, DTO HTTP, pagination, thời hạn idempotency, các trạng thái domain, phạm vi đơn hàng một kho, chính sách đổi contract và cách khởi chạy workflow. Những phần này không được PDF xác định sẵn.

## 2. Bộ file chuẩn và quyền sở hữu

| File | Nội dung | Owner triển khai | Consumer |
|---|---|---|---|
| `contracts/common.openapi.yaml` | ID, lỗi, pagination, headers, scope SELECT | Điều phối contracts + Platform | Tất cả |
| `contracts/core-api.openapi.yaml` | Registry, Data Products/Contracts, Requests/Grants, Jobs/Audit; registry Operation/Tool P1 | Core | Portal, Provisioner, Platform |
| `contracts/domain-api.openapi.yaml` | Sales, Warehouse, Logistics và transaction/idempotency | Business | Workflow/Executor, app nghiệp vụ |
| `contracts/platform-api.openapi.yaml` | Execute operation và workflow adapter boundary | Platform/Integration | Agent Runtime, Business |
| `contracts/identity-context.schema.json` | VerifiedIdentity hiện có, giữ tương thích | Platform Identity | Auth resolver |
| `contracts/resolved-identity-context.schema.json` | Principal/membership đã được backend resolve | Core policy + Platform | Policy/executor; chỉ nội bộ |
| `contracts/fixtures/review-cases.json` | Request/response tổng hợp, ca hợp lệ và không hợp lệ | Owner DTO + consumer | Mock/contract tests |

Mỗi schema chỉ có một nơi định nghĩa; các file khác dùng `$ref`. Không copy schema từ archive. `common.openapi.yaml` có `paths: {}` vì là thư viện component, không phải server triển khai. `operation_id` UUID chỉ bản ghi version; `operation_ref={operation_key,version}` là tham chiếu logic pin version; OpenAPI `operationId` chỉ tên operation của tài liệu HTTP.

Các endpoint P1 mang `x-priority: P1`; endpoint P0 không phụ thuộc việc triển khai Agent/Workflow. Chưa đặc tả LLM conversation/SSE, workflow user tasks, mapping/lineage P2 hoặc admin provisioning protocol của Hasura.

## 3. Quy ước HTTP dùng chung

- Gateway giữ nguyên public path `/api/v1/core`, `/api/v1/sales`, `/api/v1/warehouse`, `/api/v1/logistics`, `/api/v1/platform`; upstream có thể mount cùng prefix. Không gắn hostname/cổng máy dev vào contract.
- JSON dùng `snake_case`. ID Core/domain là UUID, business code như `ORDER-001`, `sku` là chuỗi riêng; engine ID/subject là opaque string. Không FK xuyên database hoặc suy hai ID sinh riêng là một.
- Timestamp RFC3339 UTC với `Z`; integer số lượng dương, không float. MVP một kho logic, SKU thống nhất giữa ba domain, không có batch/serial/unit conversion; thay đổi giả định phải sửa DTO/stock operation version.
- Response thành công là object trực tiếp; list `{items,next_cursor}`, không thêm envelope `success/data`. Cursor opaque; limit 1..100, default 20. Mỗi list khai `x-pagination`: mặc định `(created_at,id)` tăng dần; request dùng requested_at, grant/operation permission dùng granted_at, audit dùng occurred_at; ToolBinding dùng `(bound_at,tool_id)`, inventory theo sku. Cursor ràng buộc filter và chỉ trả tài nguyên caller được xem.
- Request dùng `additionalProperties: false`. Producer phải trả đúng response schema của phiên bản đang công bố. Consumer nên chịu được field response mới; khi thêm field phải cập nhật schema/version và regenerate validator của producer, không dùng schema strict cũ để tuyên bố response mới hợp lệ. Thêm required request field, đổi nghĩa/kiểu/enum response là breaking change và phải review version.
- `Authorization: Bearer` bắt buộc; từng receiver xác minh token audience và quyền tài nguyên. Không coi bearer scheme trong OpenAPI là đã triển khai policy.
- `X-Correlation-Id` tùy chọn ở request, backend chuẩn hóa và luôn echo response. 1..128 ký tự `[A-Za-z0-9._:-]`; không phải quyền hoặc idempotency key.
- Giữ error envelope đã có trong repo: `{code,message,request_id,retryable,details:[{field,reason}]}` với `application/json`. Chưa chuyển sang Problem Details vì sẽ đổi boundary hiện có.
- 400 JSON/HTTP hỏng cú pháp; 401 thiếu/hỏng/hết hạn token; 403 thiếu quyền; 404 tài nguyên không thấy được; 409 conflict/trạng thái/phiên bản/idempotency/thiếu hàng; 422 sai shape/schema; 429 có `Retry-After`; 503 upstream chưa sẵn; 504 timeout; 500 lỗi nội bộ đã lọc. 400 dùng code VALIDATION_ERROR, khớp boundary Platform hiện có. Không trả raw DSN/SQL/token/upstream body.
- Mọi mutation POST/PUT/PATCH yêu cầu `Idempotency-Key` (8..128 ký tự); PATCH và command sửa tài nguyên có `expected_revision` (grant dùng `expected_desired_revision`). OperationPermission không có cột revision trong PDF nên revoke dùng atomic ACTIVE→REVOKED và key, không thêm cột vào schema. Không nhận `status`, actor, audit timestamp hoặc applied revision tự khai nếu DTO không cho phép.

### Idempotency và optimistic concurrency

Key có scope `(verified caller issuer+subject, HTTP method, canonical path, key)`. Backend lưu fingerprint payload chuẩn hóa, HTTP status/body và resource reference; khác payload cùng key trả 409 `IDEMPOTENCY_CONFLICT`. Replay phải xác thực và kiểm quyền hiện hành trước khi trả kết quả đã lưu; không tiết lộ dữ liệu sau thu hồi quyền.

Backend claim key và ghi transaction nghiệp vụ nguyên tử sau khi xác thực/kiểm quyền/validate input. Lỗi trước khi nhận command (401/403 hoặc lỗi input) không tạo kết quả mutation đã hoàn tất. Cùng key đang xử lý trả 409 `INVALID_STATE`, `retryable=true`, `Retry-After: 1`. Replay hoàn tất giữ HTTP status và kết quả nghiệp vụ ban đầu, `Idempotency-Replayed: true`; trường trace `request_id` và header correlation phản ánh request hiện tại, audit liên kết request gốc. Không chạy lại optimistic check của payload đã thực thi. Key mới với revision cũ trả `VERSION_CONFLICT`.

Đề xuất giữ record HTTP ít nhất 7 ngày; unique invariant của reservation theo order, shipment theo reservation, workflow theo business/run tồn tại suốt vòng đời. Hết thời hạn HTTP không được làm lại tác động cũ. Muốn chạy lại nghiệp vụ phải có command/attempt mới được policy cho phép, không đổi key để né invariant. Thời hạn 7 ngày là đề xuất cần duyệt.

## 4. Identity và quyền

`VerifiedIdentity` hiện có giữ nguyên; auth resolver nối `(issuer,subject)` với `principals`, rồi tải membership từ Core. `GET /core/me` trả context đã resolve cho chính caller. DTO đó **không phải request body/header cho client khai quyền**. Service nhận caller token đã xác minh; không tin `X-Department`, `X-Role`, `X-Principal-Id`, `initiator_id` do browser/LLM gửi.

| Hành động | Policy tối thiểu |
|---|---|
| Quản trị principal/department/membership và đăng ký application/source endpoint | PLATFORM_ADMIN được cấp khả năng quản trị; binding identity phải đối chiếu IdP |
| Sửa metadata product/contract, approve/reject/revoke | OWNER còn hiệu lực của department suy từ Source → Application; PLATFORM_ADMIN không tự bypass quyền dữ liệu |
| Xin quyền cho principal | Chính principal đó; hoặc admin được ủy quyền rõ ràng |
| Xin quyền cho department | OWNER của consumer department; STAFF không xin thay cả phòng |
| Xem requests/grants/jobs | Requester/consumer hoặc owner tài nguyên; job source nhạy cảm chỉ owner/admin được cấp |
| Khám phá catalog | Principal tổ chức ACTIVE xem metadata product ACTIVE/current PUBLISHED contract khi source CONNECTED, application và department owner ACTIVE; draft/retired/disabled chỉ owner |
| Đọc Data Product | Principal/membership/department/application còn ACTIVE, product ACTIVE, contract đang phục vụ, grant ACTIVE/chưa hết hạn/applied=desired, Hasura policy phù hợp |
| Execute Operation | Quyền operation version hiện hành; HASURA_QUERY thêm grant; BUSINESS_API thêm quyền receiver |
| Workflow service ghi nghiệp vụ | Service identity + scope resource/run đáng tin cậy tại receiver; ghi tách actor service và initiator |

Membership OWNER không đồng nghĩa quyền mọi application hay mọi operation. Department grant và principal grant là hai đường độc lập; revoke một đường không tự xóa đường kia. Trong MVP, không union quyền từ nhiều grant để mở rộng cột: một lần đọc chọn một grant hợp lệ đủ toàn bộ scope; không đủ thì từ chối. Không cache quyết định quyền giữa requests; đã chấp nhận query đang chạy có thể hoàn tất theo chính sách timeout, không hứa hủy tức thì.

Principal provisioning quản trị nhận issuer/subject để **tra cứu IdP**, không nhận `kind` tự khai; service binding và kind lấy từ IdP/config đáng tin cậy. Human luôn có application/service_client_id null. Service có service_client_id, application_id có thể null với dịch vụ lõi. Membership REVOKED/expired, principal DISABLED hoặc department DISABLED không cấp quyền. Resolved context trả department_status theo từng membership; hình dạng context hợp lệ không có nghĩa mọi membership trong đó còn được dùng.

Catalog metadata được chia sẻ trong cùng tổ chức ở MVP, kể cả nhãn CONFIDENTIAL; nhãn này không cấp quyền xem hàng dữ liệu. Đây là lựa chọn mới cần owner duyệt. Nếu bản thân metadata phải bí mật, cần thêm catalog ACL trước triển khai; không giả định DB v0.2 đã có ACL đó. Quy tắc catalog cho phép consumer mới khám phá product để gửi request trước khi có grant.

Delegation của workflow là phần **chưa có implementation**: AS-issued access token với audience/resource scope phù hợp hoặc receiver tra binding run-resource từ kho tin cậy. Trước integration phải chọn và kiểm thử một cơ chế; không truyền credential người dùng dài hạn, không dùng JSON actor như bằng chứng ủy quyền. Không tạo custom auth engine trong task contract này.

## 5. Registry, publication và grants

### Luồng P0 chuẩn

1. Admin đăng ký department → principal binding/membership → application → source.
2. `POST /data-sources/{id}/connect` trả 202 cùng job CONNECT_SOURCE; poll job. CONNECTED chỉ nói nguồn kết nối được.
3. `GET /data-sources/{id}/schema` là discovery read-only có giới hạn và timeout, chỉ cấu trúc được duyệt. Chưa cần lưu `schema_snapshots` P1; không trả dữ liệu hàng.
4. Owner tạo product DRAFT → contract DRAFT → publish contract thành PUBLISHED bất biến.
5. `POST /data-products/{id}/publish` pin contract; READY → PROVISIONING → ACTIVE sau verify; lỗi ERROR. Trả 202 và job PUBLISH_PRODUCT. READY là trạng thái nội bộ sau validate, không do client PATCH.
6. Consumer gửi access request pin contract; Owner APPROVE tạo request APPROVED + grant PENDING_APPLY + job APPLY_GRANT trong **cùng transaction**. Response 202 có ba resource references. Reject/cancel không tạo grant.
7. Worker gọi Hasura ngoài transaction, verify revision rồi ACTIVE. Consumer poll job/grant; 202 không bảo đảm quyền đã dùng được.
8. Revoke tăng desired_revision và chuyển REVOKING, chặn request đọc mới ngay; enqueue REVOKE_GRANT. REVOKED sau verify. Expiry chặn ngay theo đồng hồ server rồi reconcile policy, không đợi worker mới chặn đọc.

Approve phải khóa/version-check cả request và product trong transaction để serialize với disable/đổi contract; kiểm lại `product.ACTIVE`, `request.contract_version_id == product.active_contract_id`, source CONNECTED, application/department/consumer và membership còn hiệu lực. Request chờ lâu không được tạo grant cho một contract đã ngừng phục vụ.

### Đường đọc dữ liệu P0

`GET /api/v1/core/data-products/{data_product_id}/rows` là boundary P0, không phụ thuộc registry Operation/Agent P1. Query bắt buộc `contract_version_id` và `columns=sku,available_quantity`; `limit` mặc định 20, tối đa 100; `offset` mặc định 0, tối đa 10000.

Core chọn một grant hiệu lực bao phủ toàn bộ cột yêu cầu và kiểm các điều kiện đọc ở bảng quyền. Consumer phải đưa đủ cột business_key vào columns để sắp xếp tăng dần xác định; thiếu key trả 422, grant không đủ trả 403. Không tự thêm cột ẩn vào response. Backend tạo query Hasura chỉ đọc từ metadata đã duyệt, kiểm policy Hasura theo grant, không dùng admin secret và không nhận SQL/GraphQL/role do client gửi.

Response `DataRows` gồm IDs pin version, columns, rows, limit, offset, has_more. Từng row được kiểm theo **projection** của contract: đúng key, kiểu, nullability, minimum và không có cột dư. Sai một row hoặc upstream có lỗi/partial data thì trả 503 và không trả hàng dữ liệu một phần. Cột string/boolean giữ kiểu JSON; integer phải là số nguyên chính xác trong phạm vi ±(2^53−1); number phải hữu hạn và chuyển đổi không làm sai precision đã cam kết. date là `YYYY-MM-DD`; date-time là RFC3339 UTC `Z`. PostgreSQL bigint/numeric không biểu diễn an toàn phải được view chuyển thành string và contract khai string, hoặc publication bị từ chối. Không âm thầm làm tròn.

Offset đọc dữ liệu đang thay đổi không bảo đảm snapshot; concurrent insert/delete có thể dịch hàng. `has_more=true` ở ngưỡng offset tối đa không mở quyền export toàn bộ. MVP chưa có filter tự do, join, mutation hoặc complete export qua endpoint này. Đây là adapter trên Hasura, không viết lại query/storage engine.

Scope P0 chỉ `{action:"SELECT",columns:[...],row_scope:{kind:"ALL"}}`; ALL cần owner phê duyệt. Chưa nhận SQL/GraphQL expression/filter DSL. Product cần giới hạn dòng phải dùng view đã giới hạn và không thể phân biệt consumer theo dòng trong bản P0 này; nhu cầu khác phải bổ sung DSL có schema trước khi cấp. Approve chỉ được thu hẹp cột so với requested_scope, không mở rộng; expiry áp theo quyết định owner. Direct grant ngoài access request tạm chưa mở API dù DB cho phép.

Status enum của Core giữ **đúng từng bảng** trong PDF, không gom một enum status dùng chung. Các điều kiện cross-record (contract thuộc product, scope subset, source ACTIVE/CONNECTED, FK/owner còn hiệu lực) phải kiểm service/DB, không thể chứng minh bằng OpenAPI.

### Revision, worker và đổi version

- `publication_jobs.target_revision` khớp resource revision lúc enqueue; grant dùng desired_revision. Worker giữ lease và kiểm revision lại trước verify/complete; job cũ không đổi target về ACTIVE.
- Job cũ bị superseded kết thúc FAILED với `last_error_code=STALE_REVISION`, không thêm enum ngoài PDF. Worker lỗi retry theo cấu hình, max_attempts mặc định 5; lease/attempt là field nội bộ không cho client ghi.
- Grant ACTIVE cần applied_revision=desired_revision. ERROR không cấp quyền. Không PATCH grant ACTIVE/applied_revision/enforcement_role.
- Product chỉ phục vụ **một active contract** trong MVP. Publish cùng contract đang ACTIVE là no-op an toàn; đổi sang contract khác khi có live grant (PENDING_APPLY/ACTIVE/REVOKING/ERROR) trả 409 INVALID_STATE. Owner thu hồi hết rồi publish mới; không tự chuyển grant hoặc operation sang latest. Đây là chính sách đơn giản được đề xuất, cần team duyệt.
- Contract PUBLISHED không sửa nội dung. Operation ACTIVE/Tool ACTIVE pin version và không đổi nội dung/target; cần bản version mới. Disable luôn qua policy, không cho client giả trạng thái triển khai.
- Data source nhận `secret_ref` ở create/update, nhưng response không trả ref, db_user/host/port/database_name. Owner cấu hình mới qua input được kiểm quyền. Chỉ runtime resolve secret; không nhận password/DSN trong API.
- Không expose CRUD trực tiếp cho publication_jobs hoặc audit_events. Audit append-only, actor/time do server đặt; audit domain vẫn thuộc domain.
- Không sửa `application.business_api_base_url` hoặc đưa ACTIVE về DRAFT khi còn ACTIVE BUSINESS_API operations tham chiếu; trả 409. DISABLED chặn execute mới; reactivation cần kiểm target/endpoint lại. Mỗi execute kiểm application/department đang ACTIVE và URL được duyệt, không để thay URL âm thầm đổi target của operation version bất biến.

## 6. Domain contract đề xuất cho Order Fulfillment

PDF chưa cung cấp schema Sales/Warehouse/Logistics. Vì vậy DTO/trạng thái dưới đây là **đề xuất để owner nghiệp vụ xác nhận**, không phải suy ra toàn bộ từ Core DB. MVP: một reservation cho toàn bộ order, một shipment cho reservation; không partial fulfillment/partial cancel/backorder, không quản lý tiền/thanh toán/địa chỉ PII trong contract này.

| Domain | State/command | Invariant |
|---|---|---|
| Sales | DRAFT → SUBMITTED → CONFIRMED → FULFILLED; SUBMITTED → REJECTED; DRAFT/SUBMITTED/CONFIRMED → CANCELLED khi compensation đã xong | Order items bất biến sau submit; status command có expected_revision và kiểm evidence ở backend |
| Warehouse | Reservation RESERVED → RELEASED hoặc CONSUMED | Reserve tất cả dòng trong một transaction; không đủ một SKU thì rollback toàn bộ; không âm available |
| Warehouse | Packing qua `pack` đặt packed_at; `handover` tiêu thụ reservation | Pack chỉ RESERVED; handover cần packed_at và shipment tương ứng; không release sau CONSUMED |
| Logistics | CREATED → IN_TRANSIT → DELIVERED; CREATED → CANCELLED | Unique reservation_id; pickup sau Warehouse handover được verify; cancel chỉ sau reservation RELEASED |

Không dùng lần đọc available_quantity làm bảo đảm giữ hàng: reserve phải lock/update có điều kiện ở warehouse_db. Cùng order không tạo thêm reservation dù khác HTTP key. SKU trùng trong một order/reservation bị 422; số lượng >0. Reserve body pin order_id và order revision; Warehouse phải xác thực line items với Sales qua API boundary hoặc bằng business command đáng tin cậy, không tin số lượng từ LLM.

Luồng đề xuất: tạo/submit order → Platform start workflow với `business_key=order_id` và run_number=1 → reserve → confirm → pack → create shipment → handover → pickup → delivered → fulfill. Start là command tách khỏi submit, để caller retry/recover đơn SUBMITTED; **submit không âm thầm tự start một lần nữa**. Portal/Integration phải hiển thị và phục hồi trường hợp submit xong nhưng start lỗi; chưa có cam kết all-or-nothing xuyên hai dịch vụ.

Reserve chỉ chấp nhận Sales order SUBMITTED đúng revision/lines. Sales confirm kiểm reservation còn RESERVED đúng order; createShipment và Warehouse handover kiểm Sales CONFIRMED qua boundary tin cậy; fulfill kiểm shipment DELIVERED; reject chỉ trước confirm. Nhờ vậy staff không thể bàn giao một đơn SUBMITTED rồi làm confirm bị kẹt vì reservation đã CONSUMED.

Lỗi sau reserve/trước handover: coordinator chuyển sang pha COMPENSATING bền vững, ngừng/fence command forward mới, **đợi và đối soát mọi reserve/create đã nhận cho đến khi biết outcome**, rồi release reservation → cancel shipment CREATED sau khi xác minh RELEASED → cancel/reject order. Warehouse phân xử release-vs-handover trong một transaction; RELEASED/CONSUMED là terminal. Chỉ workflow executor được tạo shipment trong MVP; Logistics staff xử lý pickup/deliver/cancel theo policy. Nếu cần staff tạo shipment độc lập, phải thiết kế thêm protocol chống late-create trước khi mở quyền đó.

Timeout hoặc list rỗng không chứng minh lệnh create chưa commit. Nếu outcome còn chưa biết thì giữ workflow WAITING/UNKNOWN để reconcile, không terminal order; việc kiểm run status trước HTTP call không tự hủy request đã được receiver nhận. Sau handover không release; cần quy trình hoàn trả riêng ngoài MVP. Platform lưu trạng thái retry/compensation trong engine/adapter bền vững, không dùng FastAPI BackgroundTasks làm job queue.

Staff chỉ cancel order DRAFT trực tiếp. Order SUBMITTED/CONFIRMED chỉ workflow service được scope đúng run mới cancel, phải gửi reservation_id để Sales xác minh RELEASED và mọi shipment tương ứng CANCELLED. Trước khi có reservation chỉ workflow được reject sau thất bại reserve đã xác định. Chỉ một workflow run được thực thi order tại một thời điểm; rerun phải fence run cũ và đối soát reservation/shipment trước. Phần cấp quyền/fencing run cần kiểm chứng khi implement adapter; không coi UUID trong body là bằng chứng.

Scope quyền domain nằm ở receiver, không suy rằng Sales staff được reserve vì được đọc stock. `inventory` endpoint là API nội bộ Warehouse; Sales/Agent đọc xuyên phòng ban qua Data Product/registered HASURA_QUERY, tránh mở đường né grant.

## 7. Operation/Tool và Workflow (P1)

- Core đăng ký operation version (HASURA_QUERY hoặc BUSINESS_API), quyền execute, tool version và binding Agent–Tool. Schema input/output canonical ở operation; tool không có bản copy schema.
- Platform `POST /operations/execute` nhận đúng `{operation_ref,variables}`. Không nhận URL, query text, caller role, secret hoặc arbitrary context từ consumer. Trace lấy từ header; workflow context lấy từ identity/run binding đáng tin cậy.
- Registry HASURA_QUERY yêu cầu product/contract/query đã duyệt; BUSINESS_API yêu cầu application và method/path/timeout/authentication được kiểm allowlist. `authentication={mode,secret_ref}` chỉ ở metadata quản trị; secret_ref không trả qua read API. Template chỉ map variables đã validate, không eval code. Base URL/redirect/DNS/egress phải kiểm theo cấu hình deployment.
- BUSINESS_API khai `request_config_json.target_operation_id` trỏ đúng operationId của Domain OpenAPI; method/path phải khớp chính xác target. `variables={path,query,body}`: path chỉ chứa đúng path parameters, query serialize theo target OpenAPI, body là JSON đúng request DTO (GET dùng null). Schema input của operation và schema canonical của target đều phải chấp nhận payload. Runtime không suy map từ tên field và không có template/eval DSL. Target contract đổi phải review/revalidate và tạo operation version mới khi thay ý nghĩa; không tự trôi theo schema mới.
- CALLER_TOKEN bắt buộc secret_ref null; SERVICE_CREDENTIAL bắt buộc ref hợp lệ. Kiểm audience ở receiver; service credential vẫn cần scope actor/resource/run đáng tin cậy, không nâng quyền của human caller chỉ vì executor có token mạnh hơn.
- Operation output validate theo output_schema trước khi trả; upstream output sai schema trả 503 UPSTREAM_UNAVAILABLE, audit lỗi đã lọc. Runtime mutation BUSINESS_API chuyển tiếp Idempotency-Key có namespace ổn định đến domain, không sinh key mới mỗi retry. Đọc cũng nhận key ở boundary POST nhưng không cache dữ liệu result bằng HTTP idempotency; chỉ dedup attempt đang chạy, execute lại quyền và đọc mới khi replay. Đây là ngoại lệ có chủ đích cho endpoint execute/read.
- HASURA_QUERY chỉ nhận một query operation chỉ đọc đã duyệt. Executor lấy `data` làm result và từ chối response có `errors` kể cả khi có partial data; alias trong query phải khớp output schema, không tự đổi tên trường bằng suy đoán. Query fixture dùng alias `items`; root field thực tế vẫn phải được provisioner/introspection kiểm tra trước activate.
- Executor chỉ activate BUSINESS_API target synchronous 200/201 JSON trong phiên bản này; đổi thành 200 OperationResult sau validation. 400/403/404/409/422/429 có envelope đã chuẩn hóa được bảo toàn; receiver từ chối credential chuyển 503 để không nhầm với caller đăng nhập sai ở Platform (401 tại cửa vào). Upstream 5xx/schema sai → 503; timeout → 504 với outcome mutation chưa biết. Không follow redirect; target 202/204/non-JSON chưa hỗ trợ. Key downstream có namespace xác định từ caller đã xác minh + operation version + incoming key, tránh hai caller dùng chung executor credential va chạm.
- Registry Agent đầy đủ/model settings và chat runtime chưa nằm trong OpenAPI này. Chỉ chốt Operation/Tool/binding boundary để runtime phát triển bằng mock; không giả vờ có endpoint chat tương thích Portal v1.
- Workflow adapter nhận `{application_id,workflow_key,business_key,run_number}`; chọn engine_ref/definition/execution_principal từ cấu hình backend. Client không chọn engine URL hoặc service identity.
- Trả 202 với business link PENDING_START, poll link bằng id. LINKED chỉ là đã nối engine run, **không phải workflow thành công**. Trạng thái runtime trả riêng `runtime_status` qua adapter; UNKNOWN khi chưa đối soát, không suy run thất bại từ timeout.
- Start timeout giữ PENDING_START và reconcile theo key; chỉ START_FAILED khi có bằng chứng start thất bại. Không start mới mù quáng. Unique `(application,workflow,business_key,run_number)` giữ nguyên; run_number tăng chỉ cho rerun được policy cho phép.
- `GET /api/v1/platform/workflow-runs` lookup đúng bộ query application_id, workflow_key, business_key, run_number; trả link hoặc 404 theo quyền. Dùng để phục hồi khi mất response 202; không tăng run_number chỉ để lấy lại link. 404 không chứng minh một start đang bay sẽ không commit. LINKED có external_run_id khi provider hỗ trợ ID; provider không trả ID phải có cách xác minh khác được adapter khai báo, nên schema không ép mọi engine đều có run ID.
- Chưa chọn Flowable/n8n/Camunda. `engine_ref` opaque, không default engine. Task/history của engine không nhân bản vào Core DB. Adapter runtime persistence cần review khi implement; schema Core v0.2 không tự cung cấp đủ runtime.

## 8. Checklist nghiệm thu để ghép team

| ID | Assertion cần có khi triển khai | Owner |
|---|---|---|
| AC-01 | Token sai audience/expired, giả department/role/actor đều bị từ chối; human/service binding đúng | Identity + Core |
| AC-02 | Source CONNECTED không làm product ACTIVE; job chưa verify không làm grant ACTIVE | Core + Provisioner |
| AC-03 | Approve atomically tạo request decision + grant + job; double approve/retry không tạo hai grant | Core |
| AC-04 | Grant hết hạn/revoke chặn request mới, giữ actor/initiator audit, không mất quyền khác còn hợp lệ | Governance + Hasura |
| AC-05 | Worker chết sau external apply, lease expired, retry và stale revision không hồi sinh quyền đã revoke | Provisioner |
| AC-06 | Đọc thật qua Hasura chỉ thấy cột được grant, không dùng admin secret; scope sai bị deny | Data Access |
| AC-07 | Hai reserve đồng thời không âm tồn; thiếu một SKU rollback tất cả; cùng order/key không giữ hai lần | Warehouse |
| AC-08 | Handover/cancel cạnh tranh không vừa consume vừa release; pickup/fulfill kiểm evidence thật | Business |
| AC-09 | Operation permission không thay grant; Tool binding không thay execute permission; output schema sai bị chặn | Executor |
| AC-10 | Start engine timeout rồi retry không tạo run thứ hai; compensation sau reserve có kiểm thử lỗi thật | Workflow |
| AC-11 | published contract immutable; live grants ngăn đổi active contract; expected revision sai không ghi đè | Core |
| AC-12 | Không trả/log password, bearer, secret_ref hoặc host/DSN qua read response; list/job/audit có policy | Mọi owner |
| AC-13 | Department DISABLED làm mất quyền OWNER/department grant; consumer mới vẫn khám phá catalog để xin quyền | Core + Identity |
| AC-14 | P0 rows kiểm đúng contract/projection/business keys; không cột thừa, không partial data, không làm tròn số sai | Core + Hasura |
| AC-15 | Giữ createShipment trước commit, bắt đầu compensation rồi cho commit tiếp: coordinator phải phát hiện/hủy trước terminal Sales | Workflow + Logistics |
| AC-16 | Handover trước CONFIRMED bị từ chối; reserve với order revision/lines sai không ghi tồn | Sales + Warehouse |
| AC-17 | Mất 202 start tìm lại bằng business/run tuple; hai caller cùng executor credential không dùng chung downstream idempotency record | Platform |
| AC-18 | Đổi application URL khi có ACTIVE operation bị conflict; approve chạy đua với product disable/switch không tạo stale grant | Core |

`tests/contracts/` chỉ kiểm cấu trúc OpenAPI/local refs, JSON Schema, examples và một số invariant của **specification**. Các assertion runtime trên chưa được chứng minh bởi fixtures/mock. Không cần chạy engine để sửa tài liệu/spec này.

## 9. Cách review và kiểm tra

Chạy từ repo root với môi trường hiện có:

```sh
uv run --locked pytest tests/contracts -q
uv run --locked ruff check tests/contracts
```

Mở `core-api.openapi.yaml`, `domain-api.openapi.yaml`, `platform-api.openapi.yaml` bằng OpenAPI viewer hỗ trợ 3.1 và external local refs; chỉ chọn `common.openapi.yaml` khi cần xem component dùng chung. Các file là nguồn chuẩn, không có bước sinh lại từ implementation chưa tồn tại.

Thứ tự review: (1) Business duyệt DTO/state/compensation; (2) Core duyệt mapping PDF, grants/version; (3) Platform duyệt identity/delegation/idempotency/start timeout; (4) consumer duyệt fixtures; sau đó ghi phiên bản đã duyệt trong Git/PR theo quyền được cấp. Thay đổi breaking phải thông báo consumer và sửa fixtures/acceptance cùng PR.

Nguồn chuẩn kỹ thuật dùng để viết specification: [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html). HTTP error format giữ tương thích code hiện tại; chưa áp dụng [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html). Không có dependency mới, migration, commit/push hoặc deploy trong công việc này.
