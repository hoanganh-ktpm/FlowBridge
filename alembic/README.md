# SQLAlchemy + Alembic cho FlowBridge

## Trạng thái sau khi tích hợp script của nhóm

Đã chạy thành công trên Supabase: revision `0001_supplier_schema`, 27 bảng ứng dụng,
293 cột, 56 FK, 83 CHECK, 3 hàm SQL và RLS trên cả 27 bảng. Đã commit 25.422 dòng
dữ liệu tổng hợp, đọc lại số dòng từng bảng sau commit khớp báo cáo. `alembic check`
không phát hiện thay đổi cần migrate giữa Model và database.

- Script gốc được chuyển nguyên nội dung từ `alembic/script.py` sang
  `scripts/supplier_demo_source.py`; file nguồn được loại riêng khỏi Ruff để giữ nguyên.
- Schema/helper/verification SQL được trích nguyên văn thành `alembic/sql/*_0001.sql`,
  áp dụng bởi `alembic/versions/20261015_0001_supplier_schema.py`. Không sửa revision
  hoặc SQL này sau khi đã áp dụng; thay đổi về sau cần revision mới.
- `models/supplier_assistant.py` có đủ 27 ORM mapping, tên constraint/index và comment.
- `scripts/seed.py` chỉ nạp dữ liệu qua asyncpg sau migration; không cần cài psycopg.
  Nếu bảng ứng dụng đã có dữ liệu, lệnh dừng trước INSERT để tránh trùng/ghi đè.
  Seed dùng một transaction, kiểm ràng buộc/số dòng/hàm training trước commit.
- CSV và báo cáo tại `demo_output/`, bị Git ignore: 1.000 PR, 998 PO, 6 supplier,
  6 product; 1.780 dòng training có nhãn (train 1.420, test 279, loại 81 dòng biên),
  83 dòng query chưa có nhãn. Không gửi email/phát sự kiện hoặc chạy AI/ML thật.

Với database mới, chạy tại thư mục gốc:

```powershell
& ./.venv/Scripts/python.exe -m alembic upgrade head
& ./.venv/Scripts/python.exe scripts/seed.py
& ./.venv/Scripts/python.exe -m alembic current
& ./.venv/Scripts/python.exe -m alembic check
```

Database hiện tại đã có dữ liệu: không cần chạy lại seed. Migration dừng nếu có bảng
ứng dụng trùng tên; không xóa hay ghi đè. Installer gốc có CREATE TABLE nên không
chạy trực tiếp trên DB đã migrate. Chỉ dùng wrapper seed cho quy trình của dự án.
Xem trước bằng `python scripts/seed.py --dry-run --out .cache/seed-preview`, không
truy cập DB. Có thể truyền `--orders` (50–20000), `--seed`, `--as-of`, `--out`.

RLS/quyền được giữ nguyên theo script: anon/authenticated không có quyền bảng.
Backend chỉ giữ API PostgreSQL với router/service/repository; xem
[hướng dẫn API database](../docs/SUPABASE-API.md). Revision tiếp theo
`0002_api_idempotency` thêm bảng retry response operational; không sửa baseline 0001.

Chạy các lệnh dưới đây tại thư mục gốc repo. Backend dùng SQLAlchemy async và
`asyncpg`, dùng chung Settings với Alembic và script kiểm tra kết nối.
Không cần cài thêm `psycopg2` hoặc tạo engine thứ hai trong `main.py`.

## 1. Cài dependency

```powershell
uv sync --locked --group build
```

Nếu uv chưa có trên PATH, cài vào thư mục công cụ của dự án:

```powershell
python -m pip install --target .tools/uv "uv==0.13.1"
& ./.tools/uv/bin/uv.exe sync --locked --group build
```

Trong các lệnh tiếp theo có thể thay `uv` bằng `& ./.tools/uv/bin/uv.exe`.

## 2. Cấu hình Supabase

`.env` là cấu hình riêng trên máy và bị Git ignore. Nếu chưa có, sao chép
`.env.example` thành `.env`. Lấy host/user chính xác từ Supabase Dashboard > Connect
> Session pooler (5432). Cấu hình ví dụ:

```dotenv
FLOWBRIDGE_DATABASE_URL=
FLOWBRIDGE_DATABASE_HOST=aws-0-ap-southeast-1.pooler.supabase.com
FLOWBRIDGE_DATABASE_PORT=5432
FLOWBRIDGE_DATABASE_NAME=postgres
FLOWBRIDGE_DATABASE_USER=postgres.YOUR_PROJECT_REF
FLOWBRIDGE_DATABASE_PASSWORD='YOUR_REAL_PASSWORD'
FLOWBRIDGE_DATABASE_SSL=require
FLOWBRIDGE_DATABASE_POOL_MODE=null
```

Điền mật khẩu database, không phải API key. Password ở trường riêng được giữ nguyên;
`URL.create()` xử lý ký tự đặc biệt khi tạo URL. Với dấu nháy/backslash trong mật khẩu,
escape theo cú pháp dotenv. Không đưa mật khẩu vào Git hoặc log.

Nếu đã có DSN `postgresql+asyncpg://...`, có thể dùng `FLOWBRIDGE_DATABASE_URL` thay
cho các trường riêng; password trong DSN cần percent-encode. DSN được ưu tiên.
Không thêm `sslmode=require` của psycopg2 vào URL asyncpg: SSL được truyền qua
`FLOWBRIDGE_DATABASE_SSL` (`require`, `verify-full`, hoặc `disable` cho DB local).
`require` bắt buộc mã hóa TLS; dùng `verify-full` khi cần xác minh chứng chỉ và hostname.

Session pooler dùng được trên mạng IPv4. Direct connection là lựa chọn ưu tiên cho
migration khi môi trường truy cập được; nếu dùng direct, thay cả host và user theo
Dashboard (user thường là `postgres`). Không đổi sang transaction pooler 6543 cho
quy trình migration này. Runtime có thể dùng pool `queue` với giới hạn hiện có hoặc
`null`; cấu hình local chọn `null` để Supabase quản lý pooling. Alembic luôn dùng
`NullPool` và đóng engine sau khi chạy.

```powershell
uv run --locked python scripts/check_db.py
```

Script chỉ chạy `SELECT 1`, không tạo bảng hay thêm dữ liệu. Password trống và URL
trống khiến script báo chưa cấu hình; backend development vẫn có thể chạy không DB.
Biến môi trường trong terminal được ưu tiên hơn `.env`; xóa biến cũ nếu nó đang
ghi đè cấu hình. Development thiếu URL và password chỉ chạy bootstrap vận hành.

## 3. Định nghĩa Model và quản lý migration

Đã có 27 Model nghiệp vụ đăng ký trong `models/__init__.py`, khớp script của nhóm và
revision `0001_supplier_schema`. Không có `create_all()` ở startup.

1. Thêm các ORM model vào `backend/src/flowbridge/models/`, kế thừa `Base` từ
   `flowbridge.models.base`. Model mặc định thuộc schema `public`.
2. Import tất cả model trong `models/__init__.py` và thêm tên vào `__all__` để
   Alembic nạp đủ metadata. Pydantic `schemas.py` là hợp đồng dữ liệu API, không phải ORM.
3. Khi dùng `CheckConstraint`, đặt `name` rõ ràng theo naming convention của Base.
4. Database mới áp dụng migration ban đầu đã có; không sinh lại một initial migration:

```powershell
uv run --locked alembic upgrade head
uv run --locked alembic current
```

Autogenerate cần kết nối database thật và đọc schema hiện có. Khi chưa import model,
cấu hình chặn autogenerate để tránh sinh migration rỗng gây hiểu nhầm là đã tạo bảng.
Alembic ghi revision đã áp dụng vào `public.alembic_version`. Chưa có revision thì
`upgrade head` chưa tạo bảng nghiệp vụ.

Chỉ so sánh schema mặc định (`public`), không quét `auth` hay `storage`. Các bảng
trong DB không có model tương ứng được bỏ qua để tránh đề xuất xóa bảng của Supabase
hoặc ứng dụng khác. Vì vậy **xóa một Model không tự sinh `drop_table`**: cần viết
migration xóa bảng tường minh và kiểm tra dữ liệu trước khi áp dụng.
Nếu bảng cùng tên đã tồn tại, phải kiểm tra schema/baseline trước; không `stamp head`
chỉ để bỏ qua lỗi. Không chỉnh bảng bằng SQL thủ công rồi quên đồng bộ migration.

## 4. Nạp dữ liệu từ seed của nhóm

Model/migration và seed đã thống nhất schema. Với database mới, tạo bảng bằng
`upgrade head`, rồi dùng wrapper tích hợp:

```powershell
uv run --locked python scripts/seed.py
```

Seed async có thể dùng `Settings`, `create_engine` và `create_session_factory` từ
backend. Script phải chủ động commit và dispose engine trong `finally`; nên hỗ trợ
chạy lại không tạo dữ liệu trùng. Script seed sync hoặc yêu cầu DSN khác cần được
điều chỉnh cho `asyncpg` trước khi chạy. Wrapper hiện tại đã được chạy thành công;
không dùng installer gốc để tạo lại bảng.

## 5. Những lần thay đổi sau

```powershell
uv run --locked alembic revision --autogenerate -m "describe schema change"
# Kiểm tra: đổi tên thường cần sửa migration thành rename thay vì drop/add.
uv run --locked alembic upgrade head
uv run --locked alembic check
```

Index, khóa ngoại và constraint thông thường có thể được sinh từ model. Function,
trigger, RLS policy, extension và chuyển dữ liệu thường cần migration viết thêm.
API dữ liệu thật dùng repository async và session theo request; xem hướng dẫn
[Supabase API](../docs/SUPABASE-API.md) về quyền, idempotency và các engine còn thiếu.

Không cần `init.sql` riêng. Nếu cần xuất SQL cho database mới hoàn toàn, sau khi đã
có revision:

```powershell
uv run --locked python -X utf8 -m alembic upgrade head --sql > init.sql
```

SQL này chứa cả thao tác quản lý revision; không chạy nguyên file trên database đã
được migrate. Database đang ở revision nào thì xuất đúng khoảng revision tương ứng.

Tham khảo: [Alembic asyncio](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic),
[Supabase connections](https://supabase.com/docs/guides/database/connecting-to-postgres).

## Kiểm chứng thiết lập

Đã kiểm tra kết nối thực tế qua Supabase session pooler, áp dụng revision, nạp dữ liệu,
đối chiếu schema bằng `alembic check` và đọc lại đủ 27 bảng sau commit.
Bộ test có 109 test đạt;
1 test cũ `test_contract_snapshot_matches_provenance_checksum` thất bại vì checksum
mong đợi khác file contract hiện có. File contract trong working tree giống bản HEAD,
không bị thay đổi trong thiết lập này. Test migration mới chạy trên SQLite tạm,
kiểm tra autogenerate, upgrade/downgrade và giữ nguyên bảng không do ORM quản lý.
