# Mapping Gateway cho API PostgreSQL/Supabase

Xuất JSON mapping từ cùng registry 46 operation app/AI của backend:

```sh
uv run --locked python -m flowbridge.gateway --upstream http://127.0.0.1:8000
```

Mỗi dòng có method/path/operation_id, upstream và visibility. `/api/*` là public,
`/internal/*` là internal, `/admin/*` là admin. `/_ops/*` không nằm trong mapping.
Khi triển khai, tách route/admin credentials và network policy theo từng visibility;
không đưa toàn bộ mapping vào listener public. Cờ cũ `--include-application` đã bỏ.

Đây chỉ là dữ liệu đầu vào cho cấu hình gateway; chương trình không chạy proxy hoặc
thực thi policy. APISIX chưa được tích hợp. Backend vẫn xác minh bearer/supplier token
và kiểm quyền tài nguyên trong database. OIDC tại gateway không thay policy backend.
Mapping được sinh khi cần; không lưu thêm bản cấu hình dễ lệch contract.

Deployment bật `FLOWBRIDGE_CORE_IDENTITY` thêm `--include-identity` khi xuất mapping
để có hai route Identity core (48 operation với DB, 53 nếu đồng thời `--include-ml`).
Mặc định giữ 46 app/AI operation. Xem [Identity core](../../docs/IDENTITY-CORE.md).
