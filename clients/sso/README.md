# Giao diện nghiệp vụ FlowBridge

Giao diện React cho nhân viên, người duyệt và nhà cung cấp; CSS thuần tối thiểu.
Mã nhóm dùng GPL-3.0-or-later theo LICENSE ở gốc repository.

## Chạy

Từ `clients/sso/`, Node >=20 và npm:

```powershell
npm ci --ignore-scripts
npm run build
npm run start:a
# Terminal khác nếu cần client B:
npm run start:b
```

Server local tự build React trước khi listen; `npm run build` dùng để kiểm tra build riêng.
Container build bundle trong Dockerfile, dùng `FLOWBRIDGE_CLIENT_PREBUILT=true`
để runtime user không cần ghi source hoặc bundle.
Mở [client A](http://127.0.0.1:8761) hoặc [client B](http://127.0.0.1:8762).
Keycloak và API phải được cấu hình/chạy trước theo [SSO](../../docs/SSO.md).
Config mặc định `.cache/identity/public.json` tính từ gốc repo, gồm issuer,
apiUrl và clients.a/b; đây là config công khai, không chứa client secret.
Có thể chọn file khác bằng `FLOWBRIDGE_SSO_PUBLIC_CONFIG` hoặc tham số `--config`.
Server dùng `--client a|b --port <port>`; origin/redirect phải khớp đăng ký IAM/CORS.
Không thêm wildcard redirect để chạy giao diện. Token SSO chỉ ở bộ nhớ Keycloak JS.

Nếu dùng Docker dev đang có, client nằm trong image Browser.Dockerfile;
cần build và recreate riêng client-a/client-b để thấy source mới.
Xem [hướng dẫn dev containers](../../docs/DEV-CONTAINERS.md); giữ IAM, API và volumes.

## Phạm vi

- ADMIN: trang **Tài liệu RAG** tại `/#knowledge`, quản lý Markdown PostgreSQL,
  lịch sử phiên bản, đồng bộ tự động và xóa tài liệu chưa được trích dẫn.
  Cần bật manager và migration 0004 theo [hướng dẫn](../../docs/rag-markdown-manager.md).

- Nhân viên: danh mục sản phẩm/NCC, yêu cầu phân trang và chi tiết, tạo PR nhiều
  dòng, sửa/gửi lại/hủy khi trạng thái và owner cho phép, xem PO và lịch giao.
  Tạo/sửa sẽ chuyển sang `analysing` theo backend, không có nút lưu nháp giả.
- Người duyệt: danh sách/chi tiết đề xuất trong quyền phòng ban. Nút duyệt/từ chối
  bị vô hiệu hóa và ghi chờ workflow vì backend chưa có authoritative adapter.
- Nhà cung cấp: nhập UUID đơn và token được cấp riêng, xem preview rồi xác nhận
  lịch giao hoặc từ chối kèm lý do. Không yêu cầu SSO; token gửi bằng header,
  không URL/storage. Không tự tạo token hoặc gửi email thay backend.
- Phân quyền cuối cùng thuộc API. Chuyển tab không cấp quyền; ADMIN không tự là
  EMPLOYEE/APPROVER. Quyền bị thu hồi vẫn được kiểm ở mỗi request.
- Các thao tác ghi có idempotency key; retry nội dung không đổi sau lỗi mạng dùng
  lại key trong phiên trang hiện tại. Không tự retry ngầm. Sau reload mất key;
  khi kết quả chưa rõ nên tải lại dữ liệu để đối soát trước khi tạo yêu cầu mới.
- Chưa có chat, tự động chạy phân tích, workflow approval, PDF/email hoặc E2E
  toàn bộ quy trình mua hàng. Tiền/số lượng giữ chuỗi decimal từ backend.

## Kiểm thử

```powershell
npm run test:ui
# Nếu dùng Chrome đã có thay Chromium của Playwright:
$env:FLOWBRIDGE_UI_BROWSER_EXECUTABLE = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
npm run test:ui
```

Suite riêng dùng cổng8781, config fixture `.cache/business-ui/public.json`, mock
Keycloak SDK và HTTP bằng Playwright routes. Nó kiểm hành vi UI, payload, quyền
hiển thị, lỗi API, retry và layout mobile; không ghi DB và không chứng nhận SSO,
workflow hoặc email thật. `npm run test:e2e` giữ suite IAM/API thật hiện có và
các điều kiện trong [SSO](../../docs/SSO.md).

## Dependency và đóng gói

React/React DOM19.3.1 và scheduler dependency dùng MIT; esbuild0.28.2 dùng MIT,
là build tool OSS tương thích Node >=20 và giấy phép GPL-3.0-or-later của mã nhóm.
Keycloak JS và Playwright giữ dependency/license hiện có. Phiên bản/integrity
khóa trong package-lock.json; lấy dependency bằng npm, không đưa node_modules,
binary hoặc bundle dist vào Git/source release. esbuild giữ legal comments và
notice license của dependency trong file `dist/business.js.LEGAL.txt` được server
phục vụ cùng bundle runtime. Không sửa source dependency.

## Bằng chứng kiểm tra 17/10/2026

- `npm run build`: đạt; `npm run test:ui` với Chrome host: 10 passed trong8.8s.
  Bao gồm create nhiều dòng/decimal, edit revision, submit/cancel, pagination/XSS,
  retry key sau lỗi mạng, approver read-only, supplier confirm/reject và mobile.
- `pytest tests/unit/test_dev_stack.py -q`: 17 passed trong0.81s.
- Source tar.gz211members không node_modules/dist/cache/.env; giải nén độc lập
  ngoài repo, `npm ci --offline --ignore-scripts` và build đạt. Bundle SHA256
  bằng checkout: B3D524CB12D9B671D7B7896FB468EB421A8EC918EE7258789E8376407A1939DD.
- Linux client image npm ci/build đạt. Chỉ recreate hai client của stack dev.
  Chromium trên trang thật A/B kiểm assets200, React render và mở login Keycloak
  thật với PKCES256/redirect đúng; không nhập credential hoặc tạo nghiệp vụ.
- Chưa nghiệm thu login-to-write bằng user thật trong lượt này; chưa chạy lại
  full SQL/SSO suite, workflow/email hoặc audit image toàn phần. Test UI mock
  không thay bằng chứng integration engine. Không migration/seed/reset MFA.
