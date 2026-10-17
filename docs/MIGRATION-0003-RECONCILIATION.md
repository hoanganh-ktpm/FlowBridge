# Đối chiếu migration 0003 — 17/10/2026

DB đã áp dụng `0003_core_knowledge`, nhưng nhánh `huuhuandev_features` tại HEAD
`70ed9da` chỉ có source 0001/0002. Đây là source migration bị thiếu giữa các nhánh;
không xử lý bằng stamp, downgrade hoặc migration rỗng.

## Nguồn và thay đổi

Khôi phục hai file từ commit nhóm
`99c9b699a4598f886a8522e39ae85ee3482b5f55`, có trong `origin/minnhungdev_features`:

| File | SHA-256 bản gốc trong Git |
|---|---|
| `alembic/versions/20261016_0003_core_knowledge.py` | `3a28bb3b4dbe9566cbee3ac80e76941bb606413048b50b6b530954962f132f8a` |
| `alembic/sql/core_0003.sql` | `52612b9fab6c2aab1e1cf1fd8f20f50d972b45d2b44acec4a2e042b1b63967b4` |

Chỉ bổ sung notice GPL-3.0-or-later và chuyển mô tả module Python thành comment `#`.
Đã đối chiếu nội dung sau khi loại đúng các thay đổi comment này với Git gốc:
DDL và logic upgrade/downgrade giữ nguyên. Không lấy thêm API/runtime RAG,
contract hoặc dependency khác của commit. Không thay source migration 0001/0002.

Fixture `test_supplier_database.py` và `tests/e2e/identity_fixture.py` đọc heads
bằng Alembic ScriptDirectory rồi so sánh chính xác tập revision hiện tại của DB.
Guard vẫn từ chối DB sai revision; không nhận tùy ý revision cao hơn 0002.

## Bằng chứng mới

- `python -m alembic heads` và `python -m alembic current`: cùng
  `0003_core_knowledge (head)`.
- `python -m alembic check`: `No new upgrade operations detected`.
  Kiểm tra này có phạm vi ORM public; không tự chứng nhận schema Core ngoài ORM.
- Catalog Supabase đọc trong transaction READ ONLY: ba bảng Core, đủ 37 cột;
  hai trigger function có body bằng SQL gốc; đủ index cần dùng và trigger enabled;
  pgvector 0.8.2 ở `extensions`, embedding vector(1024); RLS bật,
  `anon`/`authenticated` không có schema usage hoặc quyền bảng.
- Số dòng Core trước/sau regression giữ 6 documents / 6 versions / 96 chunks.
  Đây là đối chiếu catalog/số dòng, không phải checksum toàn dữ liệu.
- `python -m pytest tests/unit/test_database_tooling.py tests/unit/test_knowledge_migration.py -q`:
  **8 passed in 1.73s**, exit0. Test mới kiểm SQL offline upgrade
  và việc downgrade từ chối xóa evidence.
- `python -m pytest tests/integration/test_supplier_database.py -m integration -q`:
  **19 passed in 127.56s**, session84620 exit0, không skip. Suite dùng fixture
  transaction rollback; ca kiểm commit qua kết nối khác tự cleanup fixture.
- Rebuild Docker tooling `check-db`/`tools`: session75471 exit0.
  `check-db ... db-check --require-head`: PASS read-only connectivity và migration head.
  Không recreate API/IAM hoặc reset volumes.
- Ruff check/format bốn file Python thay đổi và `git diff --check`: đạt.
- Hatchling build source tar.gz182 members; revision/SQL/test/evidence bằng byte
  source hiện tại, không có env/cache/dependency directories. Không coi kiểm nội dung
  archive này là clean archive Engine build hoặc upgrade DB sạch.

Lệnh unit thực tế dùng `-p no:cacheprovider --basetemp .cache/pytest-migration-<UUID>`.
Audit host trong sandbox ban đầu không trả kết quả, đã dừng; audit mạng được duyệt
đạt. Phép kiểm catalog đầu tiên lỗi do so sánh tên type `extensions.vector(1024)`
với tên rút gọn `vector(1024)` theo search_path; chuẩn hóa tên trong script kiểm rồi
chạy đạt, không sửa DB để vượt assertion. Báo cáo catalog và script tạm nằm trong
`.cache/migration-reconcile/`, bị Git ignore và không chứa credential/nội dung hồ sơ.

## Giới hạn và bước tiếp

Không chạy upgrade/stamp/downgrade/seed trên DB hiện có, không sửa `.env`, không
commit/push. Source đã khớp revision và regression SQL đã được gỡ chặn.

Chưa kiểm upgrade 0003 từ một database sạch trong lượt này. Chưa chạy lại full SSO
container, full business suite trong container hoặc fixture recovery sau lỗi thật;
image audit và clean archive Engine build cũng còn mở. Không đóng
CODE-00-04.02/CODE-00-05.02, CP-001 hoặc PR01/CP-002 từ việc gỡ lệch migration.
Schema `core` của pilot này không thay nghiệm thu Data/Files/Workflow theo roadmap.
