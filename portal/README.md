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

Client web yêu cầu TOTP trên Keycloak và API kiểm `acr=2`. Người chưa có OTP đăng ký qua QR gốc của Keycloak; phiên password-only cũ phải đăng nhập lại. Cấu hình, cách cập nhật realm hiện có và live test dùng credential test riêng: [SSO và MFA](../docs/AUTHENTICATION.md).

Build theme bằng `npm run build:theme` khi có Java và Apache Maven trên PATH. Theme artifact ở `dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar` được ignore, không commit binary. Cài vào `/opt/keycloak/providers/flowbridge-theme.jar` của Keycloak, khởi động lại riêng Keycloak và chọn Login Theme `flowbridge` cho realm tương ứng. Không reset/import đè realm hoặc thay volume để cài theme. Hướng dẫn engine: [Keycloak themes](https://www.keycloak.org/ui-customization/themes).

Demo hiện tại đã cài theme trên `flowbridge-integration-keycloak-1`, realm `flowbridge-it`. Tài khoản consumer local `demo-workspace` và mật khẩu được lưu riêng trong `.tmp/data-workspace/demo-user.json`; không đưa mật khẩu này vào source/public config. Nó dùng grants SELECT có hạn của phòng Sales, không có quyền quản trị hoặc ghi nghiệp vụ. Xác minh form thật/tài khoản/logout/mobile bằng `node tests/live/login.mjs` khi môi trường local đã chạy.

Live mặc định là Data Workspace; các màn hình chat/Agent cũ giữ ở route riêng. Phạm vi và evidence thật tại [Data Workspace](../docs/DATA-WORKSPACE.md). Build/unit tests không tự thay thế kiểm thử API/SSO thật.

Luồng vận hành dùng chung, không chia giao diện theo phòng ban: Tổng quan →
Dự báo nhu cầu → Phương án nhập hàng → Đơn hàng & giao nhận; kết quả đã lưu mở từ thanh tiêu đề.
Tổng quan có hai tab: Đặt hàng & nguồn hàng và Phân tích lịch sử. Chỉ danh mục/quyền tải khi đăng nhập; đổi bộ lọc
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
`Xem số liệu đã ghi nhận trong` mô tả khoảng dữ liệu đã có; `Kỳ kế hoạch` là kỳ
sắp tới cần đáp ứng. Thay khoảng xem không tự thay lịch sử dùng để học dự đoán.

Kế hoạch đối chiếu Sales/Warehouse theo cùng kho/SKU/UOM và hạn từng đơn. Biểu đồ
kết hợp lượng khách có thể đặt thêm, đơn đến hạn chưa phân bổ và lượng hàng sẽ về;
biểu đồ số dư cho biết ngày thiếu. Tổng quan dùng hai đường lượng đặt/thực nhập
và cột chồng nguy cơ thiếu theo sản phẩm, cùng UOM: phần thiếu cho đơn đã nhận
và phần thiếu tăng thêm khi tính nhu cầu dự báo. Lịch sử chọn gộp ngày hoặc tuần.
Lịch Logistics giữ đơn vị lượt bàn giao riêng. Thiếu nguồn kế hoạch mới không
chặn các biểu đồ lịch sử cũ. Nguồn thiếu được ghi rõ, không thay bằng 0.

Giả định tăng/giảm lượng khách đặt có mặc định 0%: ước tính 100, tăng 20% thì thử
120. Giao diện hiện phép tính trên sản phẩm đang chọn; chỉ điều chỉnh đơn mới
dự kiến, không đổi đơn đã xác nhận. Phương án chỉ lưu kết quả ở Tầng 2, chưa tạo
đơn mua/phiếu nhập. Bản tải xuống có lượng, ngày về, kết quả trước/sau và việc phối hợp.

Dự báo nhu cầu là chức năng riêng, chỉ chọn ML Ridge, mặc định học 6 tháng,
kiểm chứng trên hai đoạn theo thời gian. Có thể chọn 90/180/365 ngày học và 7/14/30
ngày dự báo; thiếu lịch sử trả lỗi rõ ràng, không tự fallback sang thống kê.
Tổng quan vẫn dùng dự báo nhịp tuần của planner, giải thích trong Nguồn & cách tính.
P10/P50/P90 là khoảng thực nghiệm, P90 chỉ là mức chuẩn bị thận trọng từng ngày.
IT có thể lưu mapping rồi Mở báo cáo riêng; backend lấy cấu hình theo owner và kiểm
quyền nguồn, không hardcode thêm Data Product cho mỗi báo cáo tùy chỉnh. Mapping
riêng chưa phải cơ chế công bố dashboard cho phòng ban khác.

Card, Tabs, Table, Badge, Checkbox, Collapsible, NativeSelect, Label, Button,
Tooltip và Sheet dùng primitives shadcn/ui hiện có. Nguồn/cách tính và độ mới
nằm trong phần chi tiết; không lặp các thẻ tổng/bình quân/tăng trưởng ở lịch sử.
`FLOWBRIDGE_WORKSPACE_SOURCE_NAMES` cấu hình tên Data Product theo vai trò cho
từng deployment; chi tiết và giới hạn tại DATA-WORKSPACE.md phần L.


Danh mục tên kho/sản phẩm tải bằng /planning-options khi vào màn hình (không tải
số lượng vận hành). Hiện tên kho và mã, nhiều kho yêu cầu chọn; chuyển chức năng
chỉ dùng lại danh mục trong bộ nhớ màn hình. Tùy chọn biểu đồ là menu nổi, giả định
là Sheet có Áp dụng/Hủy, không làm giãn bố cục chính. QR TOTP có theme native riêng;
preview development tại ?auth-preview=totp và ?auth-preview=totp-manual. Không dùng
preview để xác thực tài khoản thật. Chi tiết cache/quyền ở DATA-WORKSPACE.md M.

Phương án nhập hàng học ML đúng SKU/kho và hiển thị ba trường hợp: ML gốc,
nhu cầu/hàng về sau điều chỉnh và sau bổ sung. Có tab số dư, luồng nhu cầu/hàng
về và bảng tác động; gợi ý lượng/ngày bổ sung cần tính lại trước khi lưu.
Nguồn đầu vào ở Sheet. Nhập dư được nêu rõ bằng hàng cuối kỳ; +20% là lượng
sản phẩm đặt mới, không phải số khách riêng biệt. Chi tiết giới hạn và cách
tính ngưỡng nhu cầu tại DATA-WORKSPACE.md N.

Ô Nhập thêm nhận số nguyên không âm bằng bàn phím, không có nút tăng/giảm.
Cho phép xóa trống khi sửa và yêu cầu nhập lại trước khi so sánh; gợi ý lượng
bổ sung được làm tròn lên số nguyên khi hiển thị/điền.

Dự báo chọn ngày bắt đầu/kết thúc trong 90 ngày tới và tính cả ngày cuối.
Đối chiếu nguồn hàng tính từ hôm nay đến ngày cuối đã chọn. Các nút thông tin
cạnh trường lọc/ngày giải thích ý nghĩa bằng Tooltip. Lịch bàn giao ghi rõ là
dự kiến, chưa phải kết quả giao thực tế. Chi tiết ở DATA-WORKSPACE.md O.
