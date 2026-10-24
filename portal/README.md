# Portal

Khối giao diện độc lập: React/TypeScript, design tokens, shadcn/ui, chat, bảng/biểu đồ, Activities, đăng nhập PKCE và Keycloak theme. Giữ dependency/lockfile và attribution hiện có.

Source ở src; SSO/session ở src/app; theme ở src/keycloak-theme; UI primitives ở src/components/ui; HTTP/API boundary ở src/lib. Fixtures hiện có nằm ở src/fixtures. JSON presentation schemas ở contracts/portal/schemas.

```powershell
npm ci
npm run build
npm test
npm run dev
```

Preview: http://127.0.0.1:5174/?preview=1. start.ps1 chỉ chạy Vite, không gọi launcher/backend/ML/RAG cũ. Có thể phát triển UI bằng fixtures mà không chờ các khối backend.

Đăng nhập giữ Keycloak SDK/PKCE. Người chưa có phiên được chuyển thẳng tới form tài khoản/mật khẩu trên Keycloak (`login-required`); Portal không thu mật khẩu. Form thật tái sử dụng `src/keycloak-theme/login/KcPage.tsx`, cùng AuthShell và design tokens với preview. Config công khai lấy qua FLOWBRIDGE_SSO_PUBLIC_CONFIG hoặc FLOWBRIDGE_WEB_API_URL/ISSUER/CLIENT_ID và VITE_*; không đưa secret vào browser. Demo local dùng cấu hình từ `python -m scripts.data_workspace serve-portal`.

Build theme bằng `npm run build:theme` khi có Java và Apache Maven trên PATH. Theme artifact ở `dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar` được ignore, không commit binary. Cài vào `/opt/keycloak/providers/flowbridge-theme.jar` của Keycloak, khởi động lại riêng Keycloak và chọn Login Theme `flowbridge` cho realm tương ứng. Không reset/import đè realm hoặc thay volume để cài theme. Hướng dẫn engine: [Keycloak themes](https://www.keycloak.org/ui-customization/themes).

Demo hiện tại đã cài theme trên `flowbridge-integration-keycloak-1`, realm `flowbridge-it`. Tài khoản consumer local `demo-workspace` và mật khẩu được lưu riêng trong `.tmp/data-workspace/demo-user.json`; không đưa mật khẩu này vào source/public config. Nó dùng grants SELECT có hạn của phòng Sales, không có quyền quản trị hoặc ghi nghiệp vụ. Xác minh form thật/tài khoản/logout/mobile bằng `node tests/live/login.mjs` khi môi trường local đã chạy.

Live mặc định là Data Workspace; các màn hình chat/Agent cũ giữ ở route riêng. Phạm vi và evidence thật tại [Data Workspace](../docs/DATA-WORKSPACE.md). Build/unit tests không tự thay thế kiểm thử API/SSO thật.

Luồng vận hành dùng chung, không chia giao diện theo phòng ban: Biểu đồ & dự đoán →
Kế hoạch hàng hóa → Thử phương án → Đơn hàng & giao nhận. Chỉ danh mục/quyền tải khi đăng nhập; đổi bộ lọc
không tự đọc dữ liệu. `/overview` kết hợp phạm vi, đối tượng và analytics từ cùng
một lần đọc Core. Ngày không phát sinh của hai view tổng hợp dùng chính sách rõ ràng
ở backend; nguồn rỗng, gián đoạn và mất quyền có trạng thái riêng. IT vẫn có form
mapping/preview với chính sách strict cho nguồn tùy chỉnh.

Dự đoán số liệu chọn 7/14/30 ngày tới, không cần chọn ngày bắt đầu. Nhập giả định và
bấm So sánh phương án mới tính lại; lưu/tải đề xuất chỉ bật cho kết quả đã áp dụng. Core
vẫn kiểm quyền từng lần đọc, snapshot cache chỉ dùng để tái sử dụng input mô phỏng.
Kiểm tra luồng mới trên SSO/dữ liệu thật: `node tests/live/data-workspace.mjs`;
không chạy fixture cấp grants trùng khi grants demo đang hoạt động.

Biểu đồ so sánh tối đa 3 đối tượng cùng đơn vị, làm mượt bằng bình quân 7 ngày và
xem bình quân từng thứ. Chọn sản phẩm bằng select `M08 - Bút bi`; mã là giá trị
gửi API, tên chỉ lấy từ nguồn được cấp quyền. Không yêu cầu nhập tự do tên/mã.
`Xem số liệu đã ghi nhận trong` mô tả khoảng dữ liệu đã có; `Chuẩn bị cho` là kỳ
sắp tới cần đáp ứng. Thay khoảng xem không tự thay lịch sử dùng để học dự đoán.

Kế hoạch đối chiếu Sales/Warehouse theo cùng kho/SKU/UOM và hạn từng đơn. Biểu đồ
kết hợp lượng khách có thể đặt thêm, đơn đến hạn chưa phân bổ và lượng hàng sẽ về;
biểu đồ số dư cho biết ngày thiếu; thanh ngang chỉ so lượng thiếu trong cùng UOM.
Lịch Logistics giữ đơn vị lượt bàn giao riêng. Thiếu nguồn kế hoạch mới không
chặn các biểu đồ lịch sử cũ. Nguồn thiếu được ghi rõ, không thay bằng 0.

Giả định tăng/giảm lượng khách đặt có mặc định 0%: ước tính 100, tăng 20% thì thử
120. Giao diện hiện phép tính trên sản phẩm đang chọn; chỉ điều chỉnh đơn mới
dự kiến, không đổi đơn đã xác nhận. Phương án chỉ lưu kết quả ở Tầng 2, chưa tạo
đơn mua/phiếu nhập. Bản tải xuống có lượng, ngày về, kết quả trước/sau và việc phối hợp.

Dự đoán mặc định Auto với 6 tháng lịch sử: so thống kê mùa vụ và hồi quy trễ trên hai
đoạn kiểm chứng theo thời gian, chỉ chọn ML khi MAE giảm hơn 5%. Người dùng có thể mở
Phương pháp dự đoán để chọn cách học và 90/180/365 ngày; origin luôn giải lại khi bấm.
P10/P50/P90 là khoảng thực nghiệm, P90 chỉ là mức chuẩn bị thận trọng từng ngày.
IT có thể lưu mapping rồi Mở báo cáo riêng; backend lấy cấu hình theo owner và kiểm
quyền nguồn, không hardcode thêm Data Product cho mỗi báo cáo tùy chỉnh. Mapping
riêng chưa phải cơ chế công bố dashboard cho phòng ban khác.
