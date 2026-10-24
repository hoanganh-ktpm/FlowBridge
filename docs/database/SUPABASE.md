# Database DX-OS trên máy chủ Supabase

Cấu hình tham chiếu sử dụng **ba database nghiệp vụ độc lập và một database Core trên cùng PostgreSQL server**:

| Phần | Database | Schema | Bảng ứng dụng | Bản ghi mẫu | Migration head |
|---|---|---|---:|---:|---|
| Sales | `sales_db` | `public` | 7 | 38 | `sales_0001` |
| Warehouse | `warehouse_db` | `public` | 13 | 54 | `warehouse_0001` |
| Logistics | `logistics_db` | `public` | 6 | 22 | `logistics_0001` |
| Core | `dxos_core_db` | `public` | 12 | 0 sau reset | `core_0004` |

Có 38 bảng ứng dụng và bốn bảng `public.alembic_version`, mỗi database giữ lịch sử riêng. UUID tham chiếu nghiệp vụ được đối soát bởi seed/verify; PostgreSQL không tạo khóa ngoại trực tiếp xuyên database.

Kiểm chứng ngày **23/10/2026** sau Core realignment: schema check cả bốn database không có drift được Alembic phát hiện. Core ở core_0004, 12 bảng/0 dòng; Sales 38, Kho 54, Logistics 22 dòng được giữ nguyên và đối soát demo thành công. Không seed lại Core; 55 dòng metadata legacy đã bị xóa theo destructive migration có backup cục bộ. Đây chưa phải Core HTTP/Hasura E2E.

Các schema `sales`, `warehouse`, `logistics`, `dxos_core` từng tạo trong database `postgres` được giữ lại riêng, **không phải đích đang dùng và không tự đồng bộ** với bốn database trên. Dữ liệu FlowBridge cũ trong `postgres.public`/`postgres.core` cũng giữ nguyên.

## Xem dữ liệu bằng PostgreSQL client

Supabase Dashboard Table Editor chỉ hiển thị database `postgres`; không hiển thị các database tự tạo. [Giới hạn Dashboard chính thức](https://supabase.com/kb/troubleshooting/manually-created-databases-are-not-visible-in-the-supabase-dashboard).

Dùng pgAdmin, DBeaver hoặc `psql`. Với pgAdmin:

1. Dùng [pgadmin-servers.example.json](../../infra/postgres/pgadmin-servers.example.json) làm mẫu; thay host/user cục bộ. Cấu hình thực tế được giữ ngoài Git tại `infra/postgres/pgadmin-servers.local.json`.
2. Nhận mật khẩu PostgreSQL qua kênh riêng và điền khi kết nối.
3. Chọn đúng kết nối → database tương ứng → **Schemas → public → Tables**.
4. Chọn bảng rồi xem dữ liệu. Không cần cài PostgreSQL server local.

Ví dụ truy vấn sau chỉ chạy trên kết nối `sales_db`:

```sql
SELECT order_number, status
FROM public.sales_orders
ORDER BY order_number;
```

Trên kết nối `warehouse_db`:

```sql
SELECT sku, name, uom
FROM public.products
ORDER BY sku;
```

Không viết `sales_db.public.sales_orders JOIN warehouse_db.public.products` như một JOIN nội bộ PostgreSQL. Liên thông thực hiện qua backend/Hasura và quyền Data Contract ở bước tích hợp. Quyền thành viên Supabase Dashboard không thay thế thông tin kết nối PostgreSQL; phân quyền SQL cho từng thành viên/backend cần cấu hình theo vai trò thực tế.

## Cấu hình Python/backend

Dùng `.env.example` ở gốc làm mẫu nếu chưa có `.env`; không ghi đè cấu hình đang dùng. Điền URL/mật khẩu qua kênh riêng. Các biến chính:

```dotenv
DXOS_LAYOUT=databases
DXOS_ADMIN_URL=postgresql+psycopg://USER:ENCODED_PASSWORD@HOST:5432/postgres?sslmode=require
DXOS_SALES_URL=postgresql+psycopg://USER:ENCODED_PASSWORD@HOST:5432/sales_db?sslmode=require
DXOS_WAREHOUSE_URL=postgresql+psycopg://USER:ENCODED_PASSWORD@HOST:5432/warehouse_db?sslmode=require
DXOS_LOGISTICS_URL=postgresql+psycopg://USER:ENCODED_PASSWORD@HOST:5432/logistics_db?sslmode=require
DXOS_CORE_URL=postgresql+psycopg://USER:ENCODED_PASSWORD@HOST:5432/dxos_core_db?sslmode=require
```

Dùng session pooler cổng `5432` hoặc direct connection phù hợp. Không dùng anon/publishable/service-role API key thay mật khẩu PostgreSQL. Không để URL dùng chung hay schema overrides của layout trước gây hiểu nhầm cấu hình.

Công cụ quản trị dùng `scripts.database.engine.make_engine(...)` trong source checkout. Runtime của từng service tự tạo engine từ cấu hình của nó và chỉ dùng models cục bộ tại `app/db/models.py`; không import tooling hoặc Core. Models dùng schema `public`.

## Khởi tạo và đối soát

Đích mới cần tài khoản quản trị có quyền `CREATE DATABASE`:

```powershell
uv run --locked --group database python -m scripts.database init-databases
uv run --locked --group database python -m scripts.database upgrade
uv run --locked --group database python -m scripts.database seed
uv run --locked --group database python -m scripts.database verify
```

`bootstrap --demo` ghép các bước trên. Dùng `bootstrap` không có `--demo` nếu chỉ cần cấu trúc. Người nhận database nhóm đã có sẵn chỉ cần:

```powershell
uv run --locked --group database python -m scripts.database status
uv run --locked --group database python -m scripts.database check
uv run --locked --group database python -m scripts.database verify
```

Chỉ coi đối soát đạt khi status đúng head, check không có drift được Alembic hỗ trợ và verify thành công. Seed kiểm tra xung đột và không ghi đè dòng mẫu đã sửa. Mỗi database có transaction riêng; chưa có commit nguyên tử xuyên database.

Core sau reset để trống; seed tùy chọn chỉ có departments và applications/sources nháp, không tạo principal/admin/grant hoặc kết quả engine. Không chạy bootstrap/seed lại trên dữ liệu nhóm chỉ để xem bảng.

## Hasura và số lượng nguồn

Hasura cần sources riêng cho ba database nghiệp vụ, remote relationships và permission được Data Contract duyệt. Core giữ metadata nội bộ, không tự mở toàn bộ cho consumer. Xem [HASURA_HANDOFF.md](HASURA_HANDOFF.md).

CLI hiện chỉ quản lý bốn database demo. Core schema cho phép nhiều ứng dụng đăng ký nhiều nguồn, nhưng runtime Core đã có logic offline, nhưng persistence adapter và tích hợp N nguồn thật vẫn cần hoàn thiện; không được coi bốn tên demo là giới hạn sản phẩm.

## Layout bốn schema còn được hỗ trợ

Nếu chủ động chọn `DXOS_LAYOUT=schemas`, module dùng một database với bốn schema và bảng version riêng trong từng schema. Cách này là tùy chọn tương thích, không phải bố trí độc lập hiện tại. Cần thay đúng URL/schema theo template; cấu hình không tự chuyển hoặc đồng bộ dữ liệu. Không chỉnh sửa bản schema rồi kỳ vọng bốn database tự cập nhật.
