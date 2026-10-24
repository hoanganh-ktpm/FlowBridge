# Database setup và ownership

## PostgreSQL 16 local (mặc định cho demo mới)

Compose `infra/postgres/docker-compose.local.yml` dùng project `flowbridge-local`, named volume `flowbridge-local_postgres_data`, loopback `127.0.0.1:55432`, healthcheck và TLS. `sales_db`, `warehouse_db`, `logistics_db`, `dxos_core_db`, `hasura_metadata_db` là **năm database riêng**. Mỗi chủ sở hữu có LOGIN riêng; ba reader chỉ SELECT sáu view được chỉ định. Không tạo `intelligence_db` khi chưa có implementation, không FDW hoặc FK xuyên DB. Các đoạn Supabase/schema-layout bên dưới là hướng dẫn tương thích cũ, không cần cho local.

Từ repo root trên Windows, với `.venv` đã cài nhóm `database` và Git OpenSSL:

```powershell
.venv/Scripts/python.exe -m scripts.database.local configure
.venv/Scripts/python.exe -m scripts.database.local up
.venv/Scripts/python.exe -m scripts.database.local bootstrap
.venv/Scripts/python.exe -m scripts.database.local upgrade
.venv/Scripts/python.exe -m scripts.database.local check
.venv/Scripts/python.exe -m scripts.database.local readers
.venv/Scripts/python.exe -m scripts.database.local verify
.venv/Scripts/python.exe -m scripts.database.local products
.venv/Scripts/python.exe -m scripts.database.local hasura
```

`configure --port N` chọn port lần đầu; những lần sau giữ credentials/port và tái tạo host config. Port đang dùng khiến `up` dừng; dùng `restart` với stack hiện có. Secrets/certificates ở `.tmp/local-postgres`, bị Git ignore; giữ thư mục này để sử dụng lại volume. Không ghi đè `.env` hoặc dùng Supabase cũ. TLS `require` dùng certificate dev tự ký; không áp dụng cấu hình này cho production.

Init SQL chỉ bootstrap DB/roles. Lệnh `bootstrap` chạy lại được trên volume hiện có; migrations và seed luôn là thao tác riêng. `stop`, `up`, `restart` giữ volume; không dùng `down -v`. Hasura local ở port **18082**, metadata trong DB riêng; không sửa instance cũ ở 18080. Bản draft sáu products được xuất cục bộ, **không tự publish/grant**. `FLOWBRIDGE_CORE_SOURCE_VIEW_KEYS` chỉ định grain của view: discovery kiểm tra toàn bộ key không NULL/trùng trong snapshot có timeout, kể cả khi publish và đọc có quản trị. Không coi đây là UNIQUE constraint được PostgreSQL cưỡng chế cho mọi lần ghi sau đó.

Mapping time series: `sales.confirmed_demand_daily`: `sku | day | confirmed_quantity`; `logistics.accepted_shipments_daily`: `carrier_code | day | accepted_shipments`. Ngày nghiệp vụ dùng `Asia/Ho_Chi_Minh`. Ngày không có bản ghi vẫn là thiếu dữ liệu ở interface; consumer chỉ fill zero sau khi xác minh completeness. Nhu cầu là **gross confirmed** theo snapshot không đổi, kể cả đơn hoàn tất/hủy sau xác nhận; không join events. `open_orders` trả dòng đơn gốc còn mở, không khẳng định lượng còn thiếu sau handoff một phần. Giá vốn, PII và tham số generator không có trong các view.

FlowBridge triển khai cho **một doanh nghiệp**, với Core metadata và ba database nghiệp vụ độc lập. SQLAlchemy models giữ định nghĩa hiện hành; Alembic giữ lịch sử thay đổi. Không tạo bảng/seed/kết nối khi import models.

## Cấu trúc và migration heads

| Khối | Models | Migration history | Bảng hiện hành | Head |
|---|---|---|---:|---|
| Core | `backend/app/core/db/models.py` | `backend/app/core/db/migrations/versions/` | 15 | `core_0006` |
| Sales | `services/sales-api/app/db/models.py` | `services/sales-api/migrations/versions/` | 8 | `sales_0003` |
| Warehouse | `services/warehouse-api/app/db/models.py` | `services/warehouse-api/migrations/versions/` | 14 | `warehouse_0002` |
| Logistics | `services/logistics-api/app/db/models.py` | `services/logistics-api/migrations/versions/` | 8 | `logistics_0003` |

Models hiện có **45 bảng ứng dụng**, không tính bốn bảng alembic_version; có **14 revision files**. `core_0005` thêm registry quan hệ/job target; `core_0006` thêm `workflow_actions` và `workflow_event_inbox` cho definition/idempotency metadata, không sao chép execution state của engine. Hai revision đã được áp dụng trên Supabase theo phê duyệt riêng khi nghiệm thu Workflow; số dòng của 12 bảng cũ được đối chiếu không đổi trong transaction migration. Lỗi prefix lặp của bốn lệnh DROP CHECK trong `core_0005` được sửa bằng `op.f(...)` theo phê duyệt; không reset dữ liệu. Ba revision nghiệp vụ `_0002` bổ sung 2 bảng, 2 cột và 6 views; không sửa lịch sử `_0001`. Proposal 20 bảng cũ không phải baseline bắt buộc; bảng Agent từ migration lịch sử được giữ để xử lý trong migration riêng.

Flowable local mới sở hữu `flowable_db`; xem [Business Workflow](../WORKFLOW.md). Profile cũ từng có 62 bảng nội bộ trong `postgres.flowable_runtime`, qua role riêng không có CREATE trên `public`. Các bảng engine không thuộc 41 bảng ứng dụng hoặc migration chain của Core.

Mỗi Business API import models cục bộ qua app.db.models, tự sở hữu engine/session, transaction và quyền ghi nghiệp vụ. Không import Core, service khác hoặc scripts.database vào runtime. SQL helpers/technical tables cục bộ được giữ độc lập để các nhóm phát triển riêng.

- `scripts/database/`: CLI quản trị, engine/configuration, kiểm tra và guarded Core replacement.
- `scripts/database/migrations/`: Alembic environment/template dùng chung; versions vẫn ở từng chủ sở hữu.
- `scripts/database/fixtures/`: dữ liệu tổng hợp chỉ nạp khi gọi seed rõ ràng.
- `alembic.ini`: bốn profile cho autogenerate; không tạo migration chain thứ hai.
- `services/<domain>-api/schema.dbml`: thiết kế database nghiệp vụ.
- `infra/postgres/pgadmin-servers.example.json`: cấu hình client không có thông tin bí mật.
- `tests/core/db`, `tests/domain`, `tests/e2e/database`: kiểm thử đúng boundary.

Tooling đa database chạy từ source checkout, không phải dependency của service. Registry quản trị nạp ba package app bằng namespace riêng từ đường dẫn cố định, không phải giao tiếp runtime xuyên service. Source distribution có tooling/histories; wheel backend không chứa CLI quản trị đa database.

## Cài đặt và cấu hình

```sh
uv sync --locked --group dev --group database
uv run --locked --group database python -m scripts.database --help
```

Dùng root pyproject.toml/uv.lock; không cài lockfile cũ của gói bàn giao đè lên môi trường. Nhóm database bổ sung psycopg 3 và python-dotenv; SQLAlchemy/Alembic vẫn theo lockfile hiện hành.

Nếu chưa có cấu hình, lấy `.env.example` ở gốc làm mẫu rồi điền cục bộ. **Không ghi đè .env đang có.** Đặt DXOS_LAYOUT=databases và bốn DXOS_{CORE,SALES,WAREHOUSE,LOGISTICS}_URL trỏ đúng bốn database khác nhau. DXOS_ADMIN_URL chỉ cho thao tác khởi tạo được cho phép. Credential runtime/API/Hasura không dùng tài khoản migrate. Xem [Supabase](SUPABASE.md).

Secret nằm ngoài Git trong .env; pgAdmin cấu hình thật tại infra/postgres/pgadmin-servers.local.json. Backup ở backups/ và PDF tham chiếu ở docs/reference/database/ đều bị ignore; không đưa chúng vào source submission. Bản handoff gốc được bảo toàn local trong archive, không import từ đó.

## Database đã có sẵn

Các lệnh sau kết nối thật, chỉ chạy trên môi trường được cho phép:

```sh
uv run --locked --group database python -m scripts.database status
uv run --locked --group database python -m scripts.database check
uv run --locked --group database python -m scripts.database verify
```

status kiểm revision; check so schema bằng các khả năng Alembic hỗ trợ; verify đối soát snapshot demo nghiệp vụ đang tĩnh. Không coi chúng là kiểm chứng quyền Hasura hoặc luồng API E2E. Không bootstrap/seed lại chỉ để xem dữ liệu.

## Khởi tạo môi trường thử mới

Sau khi được phép tạo database mới: bootstrap tạo đích còn thiếu và upgrade bốn histories; bootstrap --demo thêm fixture tổng hợp. Có thể upgrade từng khối bằng `upgrade --database warehouse` hoặc profile tương ứng. Không dùng create_all() thay migrations.

Seed mới có **114 dòng nghiệp vụ** (Sales 38, Kho 54, Logistics 22) và **9 dòng Core tùy chọn** (3 departments, 3 applications DRAFT, 3 sources DRAFT). Không seed principal/admin, quyền đang hiệu lực hoặc kết quả engine giả. Tổng seed đủ bộ là 123 dòng; Core thật sau reset để trống, không tự seed lại.

Mỗi database có transaction riêng. Seed có kiểm tra conflict, không ghi đè dữ liệu đã sửa; không có commit chung xuyên database. Khi đổi schema, tạo revision mới trong đúng history và review DDL trước khi upgrade. Không sửa revisions đã áp dụng.

## Chuyển Core cũ sang mô hình hiện tại

core_0004 thay 22 bảng multi-organization bằng **11 bảng P0 theo thiết kế Core v0.2 + core_command_receipts**. Principal, phòng ban, application, product/contract, department grant và publication jobs theo đúng hướng backend. Không đổi API contracts hoặc biến OWNER/STAFF thành admin. Xem [Core schema](CORE_SCHEMA.md).

**Đây là chuyển đổi phá hủy metadata cũ.** Upgrade thông thường từ chối Core có dữ liệu. Khi được chủ hệ thống cho phép bỏ dữ liệu legacy, dùng lệnh có giới hạn:

```sh
uv run --locked --group database python -m scripts.database realign-core --confirm-database dxos_core_db
```

Lệnh chỉ chấp nhận database Core độc lập đúng tên; kiểm URL, revision core_0003 và table set, khóa bảng, sao lưu metadata vào backups/ rồi upgrade + check trong một transaction. Không DROP DATABASE, không DROP SCHEMA CASCADE, không sửa database phòng ban. Backup là logical JSON có DDL/rows, không thay full pg_dump; phục hồi cần tooling được review. Downgrade phục hồi cấu trúc cũ nhưng không khôi phục dữ liệu đã xóa.

## Trạng thái tích hợp và kiểm thử

Core schema khớp **mô hình P0** và có PostgreSQL CoreStore/native wiring cho Core HTTP. Adapter map UUID/datetime, suy Owner Department qua source/application, tạo enforcement role từ department tin cậy; transaction, receipt, rollback và worker fencing được kiểm trên database thử riêng. Không dùng integration tests để khẳng định SQL role production đã harden hoặc Hasura/SSO E2E đạt. Xem [Automated Onboarding](../AUTOMATED-ONBOARDING.md).

```sh
uv run --locked pytest tests/core/db tests/domain -q
uv run --locked pytest -q
```

Offline tests kiểm DDL nghiệp vụ giữ nguyên, lịch sử migrations, schema Core P0, FK kép, reset guards, fixtures và import độc lập. Test PostgreSQL xuyên database nằm ở tests/e2e/database, mặc định bị loại. Chỉ khi có server thử riêng được cho phép tạo/xóa database tạm, đặt DXOS_TEST_ADMIN_URL rồi chạy:

```sh
uv run --locked --group database pytest tests/e2e/database -m "integration and e2e" -q
```

CI database dùng PostgreSQL container riêng. Không dùng URL database nhóm làm test admin target. [Core P0 runtime](../CORE-P0.md) liệt kê các điều kiện engine/auth/persistence còn thiếu.

Milestone database ban đầu ngày 23/10/2026: 724 offline tests passed, 38 integration/E2E deselected; Ruff và package build đạt. Đã áp dụng core_0004 thật trên Supabase theo yêu cầu reset, Core còn 0 dòng; schema check cả bốn database không có drift được Alembic phát hiện. Demo business verification đạt với 38/54/22 dòng Sales/Kho/Logistics giữ nguyên.

Cập nhật milestone automated onboarding: đã chạy 37 database migration/seed integration tests trên cluster PostgreSQL 17.11 local riêng, không Docker. Core HTTP/PostgreSQL có nhóm persistence tests riêng; xem [bằng chứng và lệnh chạy](../AUTOMATED-ONBOARDING.md). Không thay đổi/migrate database Supabase đang dùng trong milestone này; chưa nghiệm thu Core–Hasura–Keycloak E2E.

## Tài liệu

- [Core Schema](CORE_SCHEMA.md): bảng/cột, constraints và các quyết định P0.
- [Business Design](DESIGN.md), [Demo Data](DEMO_DATA.md), [Quy trình](QUY_TRINH_BAN_GIAO.md): ba database nghiệp vụ và ranh giới bàn giao carrier.
- [Hasura Integration](HASURA_HANDOFF.md): sources, read permissions và trách nhiệm Business API.

Các mô tả Core 22 bảng/55 dòng trong handoff cũ chỉ là lịch sử, không phải schema/head hiện hành.
