# FlowBridge Web

Giao diện Agent Hub với thanh bên có chế độ biểu tượng, ô nhập tự mở rộng,
vùng hội thoại căn giữa và câu trả lời liền mạch. Dùng shadcn/ui, nền trung tính,
font Be Vietnam Pro và màu nhận diện FlowBridge cho agent/biểu đồ.
Giữ hội thoại, thư viện agent hệ thống, bảng/biểu đồ và hoạt động; không có chức
năng tạo agent hoặc dashboard nghiệp vụ.

React + TypeScript + Vite, Tailwind CSS và component chính thức của
[shadcn/ui](https://ui.shadcn.com/docs/installation/vite). Sidebar, Card, Dialog,
DropdownMenu, Select, NativeSelect, Tabs, ScrollArea, Tooltip, Alert, Collapsible,
Table và InputGroup dùng component
trong `src/components/ui/`. Biểu đồ dùng Recharts.

Kích thước workspace bám tỷ lệ bản tham khảo ChatGPT: header 52 px, sidebar
248 px, cột đọc/composer tối đa 672 px; chữ nội dung 14 px, nút nhỏ 32–36 px.
Composer dùng InputGroup và tự mở rộng khi nhập nhiều dòng/chọn agent; bộ lọc
thư viện dùng Tabs, bảng dùng Table và form bổ sung dùng NativeSelect.
Màu lấy từ `flowbridge-tokens.css`; không import mã hoặc asset ChatGPT vào app.
Giấy phép và nguồn component upstream tại [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

## Chạy cùng backend/SSO hiện có

Stack `flowbridge-chat` dùng frontend mới cho `client-a`, tại
<http://127.0.0.1:8761/>. Origin này đã được đăng ký với Keycloak và CORS backend;
không mở rộng redirect bằng wildcard. Các volume/tài khoản/backend DB hiện có
được giữ nguyên. API và bridge phải là bản có Agent Hub; DB cần migration 0006.

Từ thư mục gốc repository:

```powershell
.\web\start.ps1
# Nếu đã chuẩn bị đầy đủ model/context RAG và ML:
.\web\start.ps1 -WithML -WithRAG
```

Script gọi launcher chuẩn của dự án và bật `FLOWBRIDGE_AGENT_HUB_ENABLED=true`.
Nếu chạy trực tiếp launcher cũ, cần giữ cờ này trong môi trường mỗi lần `up`:

```powershell
$env:FLOWBRIDGE_AGENT_HUB_ENABLED = 'true'
.\chat-docker.cmd up
```

Không tự migrate hoặc seed DB bằng frontend. Xem
[hướng dẫn Agent Hub](../docs/AGENT-HUB.md) và [hướng dẫn chạy hệ thống](../HUONG-DAN-CHAY.md).
ML/RAG chưa được cấu hình sẽ hiện agent chưa sẵn sàng theo catalog backend.

SSO sử dụng CA local của stack, cần được trình duyệt tin cậy theo
[hướng dẫn SSO](../docs/SSO.md). Frontend không tắt kiểm tra TLS.

## Giao diện đăng nhập

Chỉ hỗ trợ đăng nhập bằng tài khoản do quản trị viên cấp, không có đăng ký.
Trang chào và form đăng nhập dùng chung logo, bảng màu FlowBridge và shadcn/ui.
Form tài khoản/mật khẩu được phục vụ trực tiếp bởi Keycloak qua theme
`flowbridge` (Keycloakify); mật khẩu POST đến `loginAction` của Keycloak. Web vẫn
dùng Authorization Code + PKCE, không tự nhận mật khẩu hoặc tạo phiên riêng.

Overlay Docker build theme vào image identity, đồng thời giữ các volume hiện có.
Sau lần cài đầu, chọn login theme `flowbridge` trong realm, hoặc dùng helper sau
từ thư mục gốc (cần bản xuất credentials riêng của launcher):

```powershell
.\chat-docker.cmd credentials
New-Item -ItemType Directory -Force web/ref/qa | Out-Null
docker cp flowbridge-chat-identity-1:/state/certs/ca.pem web/ref/qa/identity-ca.pem
.\.venv\Scripts\python.exe scripts/apply_login_theme.py --ca web/ref/qa/identity-ca.pem
```

Helper chỉ cập nhật
`loginTheme`, kiểm tra theme đã cài và chính sách đăng ký không đổi; không cấp
tài khoản hoặc quyền. Không commit bản xuất credentials.

Khi sửa theme, build lại cả `identity` và `client-a`. Maven chỉ cần trong Docker
stage build theme, không cần cài trên máy để chạy `npm run dev`/`npm run build`.
Xem riêng form khi phát triển tại `http://127.0.0.1:5174/?auth-preview=login` hoặc
`?auth-preview=error`; các preview này không có trong production.

## Phát triển giao diện

```powershell
cd web
npm ci
npm run dev
```

Vite dùng cổng `5174` để tránh cổng `5173` của ứng dụng khác. Chỉ để xem trước
giao diện với fixture công khai từ hợp đồng API: <http://127.0.0.1:5174/?preview=1>.
Chế độ này chỉ tồn tại khi phát triển, không được bật trong production build.

SSO thật dùng origin `8761` hoặc Vite `http://127.0.0.1:5174/`. Vite proxy
`/api/v1` tới API trong cấu hình public để browser gọi cùng origin; không cần
mở rộng CORS backend. Profile mới đã có callback/origin 5174 chính xác.
Với Keycloak đang chạy từ profile cũ, chạy từ gốc repo để cập nhật client hiện có:

```powershell
.\.venv\Scripts\python.exe scripts/configure_web_dev.py --ca web/ref/qa/identity-ca.pem
.\.venv\Scripts\python.exe scripts/configure_web_dev.py --ca web/ref/qa/identity-ca.pem --apply
```

Helper đọc credential quản trị từ bản xuất riêng của launcher (như phần theme ở
trên), giữ nguyên callback 8761, tài khoản/quyền và PKCE S256; không dùng wildcard.
Để build và phục vụ native ở origin `8761` nếu
cổng chưa được container sử dụng:

```powershell
npm run build
# Chỉ đọc file cấu hình công khai, không dùng runtime.json/operator.json:
$env:FLOWBRIDGE_SSO_PUBLIC_CONFIG = 'D:\path\to\identity\public.json'
npm start
```

Trong Docker, server tự đọc `/state/public.json` được mount chỉ đọc. `/config.json`
chỉ xuất `issuer`, `clientId`, `apiUrl`. Có thể ghi đè bằng biến server
`FLOWBRIDGE_WEB_ISSUER`, `FLOWBRIDGE_WEB_CLIENT_ID`, `FLOWBRIDGE_WEB_API_URL`.
File `.env.local` và các biến `VITE_*` chỉ dành cho giá trị công khai.

## Luồng xác thực và bất đồng bộ

- Tin nhắn xuất hiện và ô nhập được làm trống ngay khi gửi; trong lúc API tiếp
  nhận, hiển thị rõ trạng thái gửi. Khi mất phản hồi, giữ tin nhắn cùng request
  key để kiểm tra lại mà không tạo trùng; khi bị từ chối, cho sửa lại nội dung.
- Tiến trình hiện ngay trong khung chat theo sự kiện SSE thật: chờ xử lý, suy
  nghĩ, tra cứu/tìm tài liệu và tổng hợp. Không dùng timer để giả bước xử lý.
  Chuyển động nhẹ có hỗ trợ `prefers-reduced-motion`. Hiệu ứng hiện dần câu trả
  lời chỉ chạy sau khi đã nhận kết quả đầy đủ, không phải token streaming.
- Lỗi backend hiển thị trong câu trả lời với chi tiết code/trace có thể mở ra;
  không tự thực thi lại. Frontend không sửa graph, cấu hình hoặc logic backend.
- Keycloak OIDC Authorization Code + PKCE S256; kiểm phiên bằng `check-sso`, lấy hồ
  sơ từ `/api/v1/me`. Token chỉ trong bộ nhớ, refresh trước khi gọi API, không lưu
  bearer/refresh token vào browser storage hoặc URL.
- Hội thoại cần revision; PATCH dùng `If-Match`. Agent Tri thức không yêu cầu
  chọn PR và không tải danh sách PR; mặc định tra tài liệu dùng chung đã xuất bản.
  Hội thoại cũ đã gắn PR vẫn giữ kiểm quyền/ngữ cảnh đã lưu.
- Mỗi lần tạo hội thoại/run có `Idempotency-Key`. Nếu kết quả POST chưa rõ, khóa
  nội dung và giữ body/key trong bộ nhớ; nút **Kiểm tra lượt gửi** dùng lại đúng yêu
  cầu. Không tự chạy lại model. Khi chưa rõ kết quả gửi, không reload trang vì
  request key không được lưu bền trong browser.
- SSE dùng streaming fetch với Authorization, ghép frame qua nhiều chunk, bỏ
  trùng sequence, nối lại bằng `Last-Event-ID`. Khi stream đóng/mất kết nối, GET
  result và metadata để phục hồi; cursor hết hạn không làm mất kết quả đã lưu.
- Đổi hội thoại hủy luồng theo dõi phía trình duyệt, không tự hủy run trên server.
  URL giữ ID hội thoại để mở lại sau reload/SSO callback; không lưu nội dung chat.
- Chỉ vẽ từ `datasets`/`charts` của RunResult. Hỗ trợ bar ngang/dọc, stack thường,
  stack 100%, line, pie, donut; null và mẫu số 0 không biến thành số liệu giả.
  Bảng/CSV giữ decimal string; số chart chỉ dùng cho hiển thị. Hiện nguồn, cảnh
  báo, cutoff và biểu mẫu `needs_input` theo hợp đồng.
- Activities ghép theo span ID/parent ID, giữ riêng từng lần gọi công cụ và thời
  gian thật từ `duration_ms`. Không giả lập token streaming.

## Cấu trúc

```text
web/
  src/
    app/                 # Phiên SSO, đăng nhập và bố cục
    components/ui/       # shadcn/ui
    components/workspace/ # Thanh bên, header, tìm hội thoại
    features/chat/       # Hội thoại, thư viện agent, SSE/activities
    features/results/    # Renderer bảng, biểu đồ, nguồn, câu hỏi
    hooks/               # Hook dùng chung của shadcn
    keycloak-theme/      # Form SSO, bản dịch và Keycloakify
    lib/                 # API client, view models, định dạng, fixture dev
    styles/              # Map token thương hiệu sang theme shadcn
  server/                # Cấu hình công khai
  tests/e2e/             # Luồng trình duyệt và HTTP/SSO doubles
  ref/                   # Tham khảo và ảnh QA local; Git/Docker bỏ qua
  server.mjs             # Static server production, không phục vụ source/secrets
  Dockerfile
  start.ps1
```

Logo sử dụng `../logo-flowbridge.png` nguyên bản. `flowbridge-tokens.css` là nguồn
màu chuẩn: nền trung tính, màu nhận diện xanh/tím cho agent và biểu đồ. Font
Be Vietnam Pro được đóng gói từ dependency `@fontsource/be-vietnam-pro`.

Ảnh tham khảo, bản lưu trang web, thử nghiệm màu và ghi chú đối chiếu thiết kế
được lưu riêng trong `ref/`. Thư mục này bị bỏ qua bởi Git và Docker; ứng dụng
không import hoặc phục vụ nội dung trong đó. Có thể build/test từ source không
có `ref/`. Các kiểm thử trình duyệt ghi ảnh QA mới vào `ref/qa/`.

## Kiểm tra

```powershell
npm run build
npm test
npm run test:e2e
```

Playwright dùng Chrome cài trên máy; có thể đổi `channel` trong config cho CI.
Kiểm thử frontend dùng fixture và mock HTTP/Keycloak; không gọi model hoặc ghi DB
thật. Kiểm thử live SSO cần tài khoản người dùng và chứng chỉ đúng của môi trường.
Mã nhóm dùng GPL-3.0-or-later; dependency giữ giấy phép upstream.
