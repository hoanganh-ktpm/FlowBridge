# Database setup và ownership

FlowBridge triển khai cho **một doanh nghiệp**, với Core metadata và ba database nghiệp vụ độc lập. SQLAlchemy models giữ định nghĩa hiện hành; Alembic giữ lịch sử thay đổi. Không tạo bảng/seed/kết nối khi import models.

## Cấu trúc và migration heads

| Khối | Models | Migration history | Bảng hiện hành | Head |
|---|---|---|---:|---|
| Core | `backend/app/core/db/models.py` | `backend/app/core/db/migrations/versions/` | 12 | `core_0004` |
| Sales | `services/sales-api/app/db/models.py` | `services/sales-api/migrations/versions/` | 7 | `sales_0001` |
| Warehouse | `services/warehouse-api/app/db/models.py` | `services/warehouse-api/migrations/versions/` | 13 | `warehouse_0001` |
| Logistics | `services/logistics-api/app/db/models.py` | `services/logistics-api/migrations/versions/` | 6 | `logistics_0001` |

Tổng **38 bảng ứng dụng**, không tính bốn bảng alembic_version; có **7 revision files**. Core giữ nguyên ba revisions lịch sử và thêm core_0004 thay thiết kế cũ. Ba migration nghiệp vụ không đổi. Agent/Tool/Operation/Workflow metadata P1/P2 chưa triển khai; không coi proposal 20 bảng là toàn bộ schema phải tạo ngay.

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
