# Mapping Gateway cho API mẫu

Xuất JSON mapping từ cùng registry 28 operation của backend:

```sh
uv run --locked python -m flowbridge.gateway --upstream http://127.0.0.1:8000
```

Mỗi dòng có method/path/operation_id, upstream và visibility. `/api/v1/core/*` thuộc
mapping public; `/internal/v1/core/*` dành cho đường nội bộ ở giai đoạn tích hợp.
`/_ops/*` và Admin API không có trong mapping public.

Giai đoạn mẫu gọi trực tiếp `http://127.0.0.1:8000`. APISIX chưa chạy, nên cả public và
internal API đều gọi được trên listener development loopback này; nhãn visibility chưa
tạo network boundary. Chưa có TLS, OIDC hay rate limit từ Gateway. Dùng cấu hình upstream
APISIX và kiểm thử network thật ở giai đoạn tích hợp; chương trình trên chỉ xuất mapping.

Mapping được sinh khi cần, không lưu thêm một bản cấu hình route dễ lệch contract.
