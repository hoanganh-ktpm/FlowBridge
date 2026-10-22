# Hasura Integration

**Provision nguồn PostgreSQL, công bố table/view và thực thi quyền SELECT bằng Hasura v2 CE.**

Module độc lập với SQLAlchemy models, Alembic và database nghiệp vụ. Có thể phát triển và chạy unit tests khi chưa có Core DB, Hasura hoặc Docker. Không có server entrypoint, admin endpoint công khai hay worker chạy nền trong module này.

Đã đối chiếu boundary với Core Database v0.2; xem [báo cáo audit](../../../../docs/HASURA_AUDIT.md) cho findings, bằng chứng kiểm thử và deployment blockers. Tương thích DTO/contract không đồng nghĩa đã ghép persistence hoặc chạy Hasura thật.

## Kiến trúc và giao diện

```text
hasura_provisioner/
├── client.py       HTTPX adapter cho Hasura Metadata API
├── schemas.py      DTO và payload SELECT, không phải ORM models
├── services.py     SourceRegistrationService, DataProductPublisher, HasuraPermissionService
├── ports.py        Protocols cho Core DB, contract, secret, identity và publication jobs
├── adapters.py     Resolve secret_ref qua environment allowlist
├── settings.py     Cấu hình môi trường bằng Pydantic Settings
└── errors.py       Lỗi có kiểu, không mang response body hoặc credentials
```

| Public interface | Trách nhiệm |
|---|---|
| `HasuraMetadataClient.check_health()` | Kiểm tra `/healthz`; không thay kiểm tra metadata consistency |
| `export_metadata()` / `list_sources()` | Đọc metadata v3 qua `export_metadata` API version 2; danh sách nguồn chỉ trả name/kind |
| `add_postgres_source(name, connection)` | Thêm nguồn PostgreSQL từ biến môi trường phía Hasura; không replace nguồn khác cấu hình |
| `track_table(source, table)` | Chỉ track đúng một table/view; không tự sinh quyền đọc |
| `create_select_permission(...)` / `drop_select_permission(...)` | Quản lý SELECT cho UUID phòng ban; role do backend sinh |
| `reload_metadata(sources)` | Reload danh sách nguồn cụ thể; không recreate trigger hoặc reload remote schemas |
| `get_inconsistent_metadata()` | Chỉ trả cờ consistency và số lỗi, không phát tán diagnostic chứa secret |
| `SourceRegistrationService.register(source_id, actor)` | Kiểm approval, source/host allowlist, secret_ref; xác nhận Hasura rồi mới mark CONNECTED |
| `DataProductPublisher.publish(product_id, actor)` | Kiểm approval và contract/schema qua adapter; track và xác nhận rồi mới mark ACTIVE |
| `HasuraPermissionService.reconcile(target, actor)` | Hợp nhất các grants hiệu lực của một product/phòng ban rồi áp dụng |
| `HasuraPermissionService.revoke(target, grant_id, actor)` | Xác nhận Core đã ghi desired revoke, tính lại quyền từ những grants còn lại |

Client dành riêng cho control plane nội bộ. Không đưa trực tiếp client hoặc admin secret cho Portal/Business APIs. HTTPX client được inject và do composition root sở hữu vòng đời; không tạo một connection pool mới cho mỗi request.

## Cấu hình

Tái sử dụng dependencies đã khóa; không cần `pip install psycopg2`, thêm SDK Supabase hoặc cài agent skills cho module này.

```sh
uv sync --locked --group dev --group build
uv run --locked pytest tests/core/hasura -q
```

Các biến mẫu nằm ở `.env.example` tại gốc repository:

```dotenv
FLOWBRIDGE_HASURA_ENDPOINT=http://127.0.0.1:8080
FLOWBRIDGE_HASURA_ADMIN_SECRET=
FLOWBRIDGE_HASURA_TIMEOUT_SECONDS=10
FLOWBRIDGE_HASURA_ALLOWED_SOURCES=["warehouse"]
FLOWBRIDGE_HASURA_ALLOWED_DATABASE_HOSTS=["warehouse-db.example.invalid"]
FLOWBRIDGE_HASURA_SECRET_BINDINGS={"warehouse-reader":"WAREHOUSE_READ_DATABASE_URL"}
FLOWBRIDGE_HASURA_SELECT_LIMIT=1000
```

Secret binding là mapping do operator quản lý, không phải input từ client. Cấp `WAREHOUSE_READ_DATABASE_URL` qua secret runtime ở **cả backend và Hasura**, cùng trỏ tới nguồn đã được duyệt. Ví dụ hình thức URI, không phải credential triển khai:

```text
postgresql://dxos_reader:<percent-encoded-password>@warehouse-db.example.invalid:5432/warehouse_db?sslmode=verify-full
```

Backend chỉ dùng URI để validate; payload gửi Hasura chứa `{"from_env":"WAREHOUSE_READ_DATABASE_URL"}`, không có mật khẩu. Không lưu URI vào Core DB. Resolver production có thể thay bằng secret manager mà không thay service. Không log env, model validation input, HTTP body hoặc metadata export; export từ một instance hiện hữu có thể chứa plaintext secrets do operator khác từng đăng ký.

MVP chấp nhận URI `postgres`/`postgresql`, có user/password/database, TLS `require`/`verify-ca`/`verify-full`. Query chỉ hỗ trợ `sslmode`, `connect_timeout`, `application_name`; chặn tham số override host/user/service/options. Ưu tiên `verify-full` và chứng chỉ CA phù hợp ở môi trường thật. `require` không tương đương xác minh danh tính server. Endpoint Hasura phải HTTPS, trừ HTTP loopback cho development. Source/host allowlist mặc định rỗng: deny by default.

Hasura được cấu hình pool tối đa 5 kết nối mỗi nguồn và không dùng prepared statements; operator cần tính tổng connection budget trước khi triển khai nhiều nguồn/instance. Không có SQLAlchemy engine/pool thứ hai trong module.

## Tích hợp với Core DB và Identity

Các DTO chỉ là projection tại boundary; không định nghĩa lại schema Core v0.2. Adapter Core cần map models thật sang DTO khi schema được bàn giao.

| Protocol | Hợp đồng bắt buộc khi triển khai adapter |
|---|---|
| `DataSourceRepository` | `get`, `mark_connected`; chỉ trả approval hiệu lực cho nguồn còn được phép hoạt động |
| `DataProductRepository` | `get`, `mark_active`; một product tương ứng duy nhất một source/schema/object trong MVP |
| `AccessGrantRepository` | `get`, `list_for_target`, `lock`, `mark_applied`, `mark_revoked`, `mark_failed`; đọc toàn bộ grants liên quan, không chỉ grant vừa thay đổi |
| `PublicationJobRepository` | `enqueue`, `get`, `claim`, `renew_lease`, `mark_succeeded`, `mark_failed`; interface P1 có revision/attempt/lease guards, chưa có persistence/worker implementation |
| `SecretResolver` | Resolve ref được operator cho phép sang env name và URI được bảo vệ bằng `SecretStr` |
| `IdentityResolver` | `require_admin` phải raise khi actor không được quyền, IdP không sẵn sàng hoặc identity không tin cậy |
| `DataContractValidator` | Đọc schema thực qua adapter được duyệt, xác minh object/columns/types và contract PUBLISHED; nhận `contract_id` để kiểm đúng version đã pin của từng grant, mismatch phải raise `ContractRejected` |

### Mapping Core v0.2

- `SourceRecord.name` nhận alias `hasura_source_name`, không phải `source_key`. Core adapter phải cung cấp `host`, `port`, `database_name`, `db_user`, `ssl_mode`, `db_type`, `status`, `revision`. URI do SecretResolver trả về phải khớp đầy đủ tuple kết nối này và host allowlist. Source DISABLED không được đăng ký lại.
- `ProductRecord.source_id` nhận alias `data_source_id`; `contract_id` nhận `active_contract_id`; `table` dựng từ `schema_name` và `object_name`. Đây là projection cho publication, không phải DTO catalog toàn phần: product chưa có contract không được publish. Publish yêu cầu source CONNECTED và product READY/PROVISIONING/ERROR/ACTIVE; apply quyền yêu cầu product ACTIVE.
- `SelectGrant` nhận alias `data_product_id`, `contract_version_id`, `consumer_department_id`, và typed `scope_json` đúng mẫu trang 27. Chỉ nhận `consumer_principal_id=null`; principal grants là ngoài MVP và phải bị từ chối rõ ràng, không tự đổi sang department grant.
- `desired_revision` và `applied_revision` tách riêng theo trang 15. Input cũ `revision` và property đọc `.revision` vẫn là alias của **desired_revision**, không phải cột `revision` mới trong bảng grants. ACTIVE đòi hai revision bằng nhau. `enforcement_role` là property do backend sinh; không nhận field này từ payload. Adapter lưu/kiểm role server-generated khi acknowledge.
- `approved` là kết quả policy đã xác minh do adapter cung cấp, **không phải cột mới** trong Core DB. Không bind raw request JSON hay nguyên ORM row vào các projection này. Không sử dụng `model_copy(update=...)` để validate dữ liệu bên ngoài.
- `ValidatedContract.status` phải PUBLISHED. Validator phải kiểm FK product/contract, physical schema, types và tính tương thích của version đã pin; không tự chuyển grant sang active/latest contract mới. Tên object/cột trong MVP là lowercase ASCII snake_case; quoted/mixed-case identifiers chưa được hỗ trợ.

Fixtures `tests/core/hasura/fixtures/core-v02.json` là projection tổng hợp tại adapter boundary, không phải seed hay raw ORM row. Không thêm SQLAlchemy models hoặc migrations.

`mark_*` phải compare-and-set theo revision, không ghi đè một thay đổi mới hơn. `mark_revoked` là xác nhận trạng thái thực thi; không đổi một desired state EXPIRED thành grant mới hoặc tự ý kích hoạt lại. Nên cập nhật các acknowledgement trong cùng transaction khi adapter được xây dựng.

Đối với grant, `revision` truyền cho `mark_*` chính là **desired_revision**. `mark_applied` phải kiểm lại desired revision, trạng thái/approval và expiry trước khi ghi applied_revision/ACTIVE. `mark_failed` đặt ERROR theo CAS cho grant bị loại khỏi effective permission; không báo ACTIVE cho grant đó. Source/product acknowledgements cũng phải kiểm revision và trạng thái mới nhất.

Worker P1 phải truyền `expected_revision` cho register/publish/revoke; với reconcile truyền cả `expected_grant_id` và `expected_revision`. Guard chạy trước external mutation; acknowledgement CAS chạy sau verification. Không dùng job cũ để tự lấy revision mới rồi apply như thể job còn hợp lệ.

`PublicationJob` projection kiểm đúng một target FK phù hợp job_type, target_revision, attempt budget và lease cho RUNNING. `claim` phải nhận job đến hạn hoặc reclaim lease hết hạn nguyên tử; renew/finish phải kiểm RUNNING, owner, attempt_count và lease chưa hết hạn bằng DB clock. Finish còn phải kiểm target_revision với **target hiện tại**, không chỉ so với row job. CAS thất bại phải raise, không silent success. Enqueue và thay đổi desired state dùng chung transaction Core. Retry backoff có trần, jitter và max_attempts do worker chịu trách nhiệm; không có retry loop trong HTTP client.

`AccessGrantRepository.lock` phải serialize **cả thay đổi desired grants và provisioning** trên cùng `(product_id, department_id)` giữa tất cả worker/API instances. Production cần DB advisory lock hoặc cơ chế tương đương; asyncio lock trong tests không phải distributed lock. Không giữ transaction DML dài qua HTTP; adapter phải thiết kế lock/session và transaction acknowledgement phù hợp.

Mọi service quản trị mặc định từ chối nếu không inject `IdentityResolver`. Không có fallback admin từ header/payload. VerifiedIdentity phải đến từ authentication boundary đã xác minh; không dựng từ request JSON. Chưa có Keycloak adapter cho authorization này, không sử dụng fake Admin ngoài tests.

## Ngữ nghĩa quyền và thu hồi

MVP chỉ hỗ trợ grant theo **phòng ban**, SELECT column allowlist và row scope `ALL` đã được duyệt. `ALL` là tất cả dòng của table/view được chia sẻ, không phải tất cả dữ liệu toàn database. Dữ liệu cần lọc theo kho/tenant phải dùng view phù hợp hoặc bổ sung row-scope compiler có kiểm thử; không truyền filter tùy ý từ client.

Role có dạng `dxos_department_<uuid-hex>` và không có field role trong grant DTO. Hasura không tự biết người dùng thuộc phòng ban nào: authentication/JWT/webhook tương lai phải cấp allowed roles từ membership đã xác minh, không nhận role tự khai. Mutation, aggregation và subscription không được cấp bởi module này. Mọi ghi nghiệp vụ tiếp tục qua Business APIs.

Các grant cùng product/phòng ban được hợp nhất theo union cột vì đều có cùng row scope `ALL`. Thu hồi một grant không xóa các cột còn được grant khác cho phép. Chỉ quản lý permission có ownership comment `flowbridge:select:v1`; permission trùng role nhưng không thuộc module gây conflict để operator review.

Core v0.2 có partial UNIQUE, bình thường chỉ một live grant cho mỗi product/department. Tests nhiều live grants kiểm tính phòng thủ của aggregation, không phải dữ liệu hợp lệ để seed vào schema đó. Có test riêng cho một live grant với terminal history. Không hợp nhất quyền các department thành một role; IdP/read boundary phải xác minh từng membership. Thu hồi department role không chạm permission của department khác hay principal role do thành phần khác quản lý.

Khi không còn grant hiệu lực, cleanup không phụ thuộc product/source còn được duyệt hoặc contract còn validate được. Grant sai approval/contract/columns bị loại và mark ERROR; permission được tính lại từ các grants còn hợp lệ rồi báo `ContractRejected` để review. Không xóa quyền hợp lệ khác vì một grant lỗi, không giữ cột của grant bị loại. Nếu cả product/source mất quyền hoạt động thì deny toàn target. Cache contract chỉ sống trong một lần reconcile, không cache quyết định quyền qua request.

Các mutation dùng resource version để phát hiện sửa metadata đồng thời. Không `clear_metadata`, `replace_metadata`, cascade drop hoặc tự động ghi đè toàn bộ metadata. P0 hiện đổi SELECT bằng hai request drop/create riêng, nên có thể có khoảng gián đoạn đọc; đây là giới hạn implementation hiện tại, không phải khẳng định mọi cơ chế bulk của Hasura đều không atomic. Không dùng `bulk_atomic`, vì operation đó chỉ hỗ trợ nhóm logical model/native query theo API reference v2. Nếu create thất bại sau drop, giữ trạng thái deny và retry reconcile; không khôi phục quyền đã thu hồi. Conflict được báo lên caller, không retry mutation mù.

Hasura được xác nhận bằng metadata read-back và consistency trước acknowledgement thành công. Đây chưa phải phép thử query dữ liệu thật. Nếu Hasura không truy cập được trong lúc revoke, quyền cũ có thể vẫn tồn tại: không được báo thu hồi thành công. Production cần authorization boundary từ chối role/grant đang thu hồi và publication worker retry. `expires_at` chỉ được kiểm khi reconcile; Hasura không đọc thời hạn trong Core DB. Cần scheduler và trusted read authorization kiểm hiệu lực mỗi request trước khi đưa hệ thống vào vận hành. Không đưa long-lived department JWT có quyền tĩnh vào production rồi coi revoke/expiry đã hoàn chỉnh.

`from_env` là tên biến ở process **Hasura**, không phải cơ chế đẩy secret từ Python sang engine. Backend validate bản URI của mình chưa chứng minh Hasura đang dùng cùng giá trị. P0 chỉ hỗ trợ bindings được operator cấp sẵn; đổi/rotate secret hoặc đăng ký một nguồn động mới cần operator đồng bộ môi trường và kiểm chứng bằng engine thật. Chưa có bằng chứng dynamic secret provisioning hay identity của database từ xa. Không coi CONNECTED metadata là health probe liên tục hoặc lời bảo đảm source đang online.

## Metadata và migration

| Nội dung | Chủ sở hữu |
|---|---|
| Core tables/models | Core SQLAlchemy + Alembic, không thay đổi trong module này |
| Business tables | Từng Business API và migration chain riêng |
| `hasura_metadata_db` / `hdb_catalog` | Hasura engine tự quản lý; không tạo Alembic models/migrations |
| Sources, tracked objects, SELECT permissions | Metadata API; baseline ở `infra/hasura/metadata/`, runtime do provisioner quản lý |

Chưa có `hasura_metadata_db` vẫn chạy được unit tests. Một PostgreSQL/Supabase database đơn thuần **không phải Hasura Metadata API**: chỉ có database chưa thể chạy live tests module này. Không dùng tài khoản database owner/admin làm nguồn đọc chia sẻ; cần tài khoản giới hạn đúng các bảng/view được duyệt và quyền hệ thống tối thiểu theo yêu cầu Hasura.

## Khi có Hasura instance sẵn

1. Operator khởi tạo Hasura v2 CE với metadata database riêng; task này không cài/khởi động infrastructure.
2. Cấp source DSN qua environment/secret runtime của Hasura và backend. Không đưa metadata DB vào danh sách business sources.
3. Bật Hasura authentication, bảo vệ Metadata API, không public admin secret; chưa cấp bất kỳ anonymous role nào.
4. Implement Core repositories, contract/schema validator và trusted IdentityResolver; inject vào services từ composition root.
5. Trên instance test chuyên dụng, đăng ký nguồn, publish view, reconcile Sales grant rồi kiểm query/revoke thực tế.

Kiểm tra live **read-only control plane**, tự skip nếu chưa opt-in/cấu hình:

```sh
# Set FLOWBRIDGE_HASURA_LIVE_TESTS=1, ENDPOINT và ADMIN_SECRET qua environment trước.
uv run --locked pytest tests/core/hasura/test_live.py -m integration -q
```

| Kịch bản | Bằng chứng hiện có |
|---|---|
| Payload v2, source/track idempotency, lỗi HTTP/timeout, che secret | Unit tests + metadata fixtures |
| Publish không ACTIVE khi lỗi, role backend-generated, union/revoke grant | Unit tests + fake repository |
| Warehouse publish `public.stock_available`, Sales chỉ thấy `sku`, `available_quantity` | Payload contract fixture; **PENDING INTEGRATION** với PostgreSQL/Hasura thật |
| Logistics chưa có grant thì query bị từ chối | Không sinh role permission trong fixture; **PENDING INTEGRATION** với JWT/query thật |
| Database update đã commit xuất hiện ở query kế tiếp | **PENDING INTEGRATION**, không được chứng minh bằng metadata mock |
| Request mới bị từ chối sau revoke/expiry | Metadata reconcile có test; **PENDING INTEGRATION** với authentication/read boundary và worker thật |

Không chạy E2E thật khi chưa có engine và adapters. Không tạo kết nối tới database người dùng để kiểm thử mock.

## Tham khảo chính thức

- [Hasura v2 Metadata API](https://hasura.io/docs/2.0/api-reference/metadata-api/index/)
- [Postgres sources](https://hasura.io/docs/2.0/api-reference/metadata-api/source/)
- [SELECT permissions](https://hasura.io/docs/2.0/api-reference/metadata-api/permission/)
- [Postgres requirements](https://hasura.io/docs/2.0/deployment/postgres-requirements/)
- [License audit](../../../../THIRD_PARTY_LICENSES.md)
