# Hướng dẫn dùng backend FlowBridge

## 1. Chạy Docker bằng một lệnh

Cần Docker Desktop chạy Linux containers và Compose >= 2.35.0. Không cần cài
Python, Java hoặc Node trên máy.

**Trước lần chạy đầu**, chuẩn bị các file:

| File | Cần chỉnh gì? |
|---|---|
| `.env` | Kết nối Supabase. Nếu chưa có, sao chép từ `.env.example` rồi điền thông tin thật |
| `compose.env` | Cổng, bật/tắt ML/RAG, migration và tài khoản test |
| `langflow/data/agent.env` | API key và cấu hình provider/model theo mẫu `langflow/agent.env.example` |
| `langflow/data/langflow.db` và `secret_key` | Nhận cặp file có đủ 4 flow ver2 từ người bàn giao, hoặc chuẩn bị theo hướng dẫn Langflow |

Mở Docker Desktop, mở terminal tại thư mục `FlowBridge`, chạy:

```powershell
docker compose up
```

Compose tự build, chuẩn bị cấu hình, bật SSO, kiểm/nâng schema DB, tạo tài khoản
test, cập nhật metadata flow và mở backend + Langflow + hai UI.
Trong `compose.env`, `FLOWBRIDGE_COMPOSE_MIGRATE=true` cho phép nâng schema
**Supabase đang cấu hình**; đổi thành `false` nếu muốn chỉ kiểm schema hiện có.
Không tự seed hoặc xóa dữ liệu.

Khi khởi động xong, mở **[UI FlowBridge](http://127.0.0.1:8761)**.
Muốn chạy nền, dùng `docker compose up -d`.

## 2. Tài khoản test dùng chung cho cả nhóm

Compose tự tạo và cấp quyền cho các tài khoản sau:

| Tài khoản | Mật khẩu mặc định | Dùng để test |
|---|---|---|
| `tester01` | `FlowBridgeTest@2026` | Nhân viên: dữ liệu, yêu cầu mua hàng, chat |
| `tester02` | `FlowBridgeTest@2026` | Nhân viên thứ hai, kiểm tra phân quyền |
| `tester03` | `FlowBridgeTest@2026` | Nhân viên thứ ba |
| `team-admin` | `FlowBridgeTest@2026` | Quản trị ứng dụng, không có quyền nhân viên |

Dùng `tester01` để làm các bước tiếp theo. Có thể chỉnh
`FLOWBRIDGE_TEAM_TEST_PASSWORD` trong `compose.env` **trước khi tạo tài khoản**.
Compose giữ nguyên mật khẩu và OTP của tài khoản đã tồn tại.

Nếu DB đã có ADMIN khác và bước tạo tài khoản thất bại, người phụ trách điền
`FLOWBRIDGE_TEAM_GRANTED_BY` trong `compose.env` bằng `user.user_id` của ADMIN
ứng dụng, rồi chạy lại `docker compose up`.

Giữ cùng cổng SSO và dùng cùng Supabase để các máy nhận cùng user/quyền backend.
Mỗi người nên dùng tài khoản riêng. OTP và thay đổi mật khẩu trên từng máy không
tự đồng bộ sang các máy khác.

## 3. Đăng nhập SSO

1. Trên UI, bấm **Đăng nhập**. Trang sẽ chuyển sang Keycloak.
2. Nhập tài khoản và mật khẩu ở trên.
3. Nếu được yêu cầu OTP, nhập mã từ ứng dụng Authenticator.
   Lần đầu có thể cần quét QR để thiết lập. Nếu không có mã OTP của tài khoản
   đã thiết lập, nhờ người quản trị hỗ trợ.
4. Khi quay lại UI và thấy **Đã đăng nhập**, bấm **Xem hồ sơ**.
   Mở **Thông tin phiên và hồ sơ**, kiểm tra `status: 200`.

## 4. Test nhanh trên UI

### Xem dữ liệu

1. Chọn tab **Nhân viên**.
2. Bấm **Tra cứu sản phẩm / nhà cung cấp** để xem danh mục.
3. Bấm **Tải yêu cầu** để xem yêu cầu mua hàng.
4. Nếu có dữ liệu, bấm **Xem chi tiết**. Danh sách rỗng có thể do tài khoản
   chưa có yêu cầu.

### Thử chat

1. Chọn tab **Trợ lý AI**.
2. Nhập: `Liệt kê 2 sản phẩm đang hoạt động, gồm mã và tên.`
3. Bấm **Gửi tin nhắn** và đợi phản hồi, không gửi liên tục.

Chat cần cấu hình model hợp lệ và có thể phát sinh phí.

## 5. Test API bằng Swagger

1. Trên UI, bấm **Sao chép access token cho Swagger**.
2. Mở [Swagger](http://127.0.0.1:18082/docs).
3. Bấm **Authorize**, dán token vào ô bearer, rồi xác nhận.
   Chỉ dán token, không thêm chữ `Bearer`.
4. Chọn `GET /api/v1/me` → **Try it out** → **Execute**.
5. Kết quả mong đợi: **HTTP 200**. Có thể thử thêm
   `GET /api/v1/products` và `GET /api/v1/suppliers`.

Token hết hạn thì quay lại UI, bấm **Làm mới phiên** và sao chép token mới.

## 6. Dừng và xử lý lỗi

```powershell
# Xem trạng thái
docker compose ps -a

# Xem log
docker compose logs --tail 80

# Dừng, giữ dữ liệu
docker compose down

# Chạy lại
docker compose up
```

| Lỗi | Cách xử lý |
|---|---|
| Docker chưa chạy | Mở Docker Desktop và chờ Engine sẵn sàng |
| Không mở được UI | Chạy `status` và `logs` để kiểm tra |
| Login được nhưng API 403 hoặc tab bị khóa | Nhờ quản trị kiểm tra liên kết tài khoản và quyền EMPLOYEE |
| Lỗi chứng chỉ khi đăng nhập | Nhờ người bàn giao thiết lập tin cậy chứng chỉ local |
| Chat lỗi hoặc chờ quá lâu | Kiểm tra Langflow, cấu hình model và log |
| Lỗi database hoặc migration | Nhờ người phụ trách kiểm tra cấu hình DB; không tự reset dữ liệu |

Cần cài mới hoặc hướng dẫn chi tiết: xem [Hướng dẫn chạy dự án](HUONG-DAN-CHAY.md).
