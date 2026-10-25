# Demo Decision Workspace trên máy local

Phạm vi nghiệp vụ dừng ở **bàn giao đủ hàng cho đơn vị vận chuyển bên thứ ba**.
Đặt lịch vận chuyển hoặc duyệt phương án chưa phải đã bàn giao. Dữ liệu trong
profile này là dữ liệu giả lập có tên sản phẩm dễ đọc; không phải giao dịch thật.

## Mở bản đang chạy trên máy Hùng

Mở `http://127.0.0.1:5174/#data`, đăng nhập SSO. Tài khoản `minhhung` được phân công
làm người điều phối trong profile local này. Các vai trò Kho, Vận chuyển và Quản lý
là ba tài khoản độc lập. Không dùng tài khoản điều phối để xác nhận thay họ.

1. **Cần xử lý:** chọn Kho trung tâm Bình Dương, sản phẩm KB-K120 và cập nhật kế hoạch.
   Bảng và biểu đồ đối chiếu đơn đã nhận, tồn kho, hàng về và lịch bàn giao.
2. **Chi tiết & phương án:** cập nhật dự báo ML, nhập lượng và ngày/giờ bổ sung,
   so sánh rồi lưu phương án. Số thiếu của SKU toàn kho khác số lượng của một đơn.
   Mức thiếu giảm về 0 không đảm bảo mọi đơn, mặt hàng khác và lịch xe đều đạt.
3. Chọn `SO-PHOIHOP-001` để thử đơn một mặt hàng, hoặc `SO-PHOIHOP-002` để thử
   đơn hai mặt hàng. Đơn thứ hai cần đủ cả bàn phím và chuột, không thể hoàn tất
   chỉ bằng bổ sung bàn phím. Chọn lịch bàn giao dự kiến đáp ứng điều kiện và lưu hồ sơ.
4. Mở hồ sơ, chọn **Gửi Kho và Vận chuyển xác minh**. Hai người phụ trách vào
   **Việc của tôi** bằng tài khoản riêng. Sau hai xác nhận, quản lý độc lập mới duyệt.
5. Phương án được duyệt vẫn cần giao dịch tại ứng dụng nghiệp vụ: nhận hàng,
   giữ hàng cho toàn đơn, chuẩn bị xuất, đặt lịch thật và ghi nhận bàn giao.
   Xem thứ tự, quyền và payload trong [DECISION-OWNER-OPERATIONS.md](DECISION-OWNER-OPERATIONS.md).
   Portal hiện chưa có form thay thế toàn bộ các màn hình nghiệp vụ này.
6. **Theo dõi kết quả:** đối soát sự kiện nguồn và kiểm tra bàn giao thực tế.
   Chỉ đủ bằng chứng của mọi dòng hàng và đúng quy trình được duyệt mới hoàn tất.

Trong hồ sơ, bốn nút hỏi giải thích bằng chứng, so sánh, người đang chờ và kết quả.
JSON có bảng/biểu đồ từ số liệu có cấu trúc; trợ lý không tự tính KPI bằng LLM,
không tự duyệt và không tự ghi sổ nghiệp vụ.

Với profile ngày 26/10/2026, hai nhà vận chuyển cũ đã kín lượt ngày 27/10. Lịch
17:00 ngày 26/10 cũng sớm hơn mốc chuẩn bị mà mô phỏng theo hạn đang chứng minh.
Nhánh hợp lệ của đơn một mặt hàng dùng lượng bổ sung 1.000 bàn phím ngày 26/10,
nhà vận chuyển dự phòng `ANTIN-EXPRESS` lúc **10:00 ngày 27/10/2026**. Đây là một
kịch bản thử có điều kiện, không phải lượng mua tối ưu hay lượt đã được đặt thật.
Profile v2 công bố năm lượt/ngày cho nhà vận chuyển dự phòng trong bảy ngày;
không hủy lịch cũ hoặc đổi hai ngày đầy thành còn trống để qua kiểm thử.

`fulfilled_at` trong mô phỏng là mốc phân bổ đủ theo thứ tự hạn đang xét, không
phải nghiệm tối ưu về lúc chuẩn bị sớm nhất. Lịch sớm hơn mốc đó cộng thời gian
chuẩn bị sẽ bị chặn. Thông báo `DECISION_CONDITIONS_UNMET` nêu rõ mốc/thiếu lượt;
người xác minh trả hồ sơ kèm lý do để điều phối lập phiên bản mới. Không sửa trực
tiếp phiên bản cũ hay dùng quyền quản trị để bỏ điều kiện.

## Profile local và dữ liệu

Các script chỉ làm việc với owner DB ở `127.0.0.1:55432`; không đọc `.env` Supabase
cũ để tìm đích thay thế. Sales, Warehouse và Logistics vẫn là ba database riêng.
Workspace giữ hồ sơ riêng; Core/Hasura quản trị chia sẻ và quyền đọc. Không có
transaction nguyên tử xuyên các database.

Lịch sử 240 ngày dùng bộ sinh ledger đã kiểm tra: phát sinh đơn Poisson, mùa vụ,
nhu cầu gián đoạn/đột biến, chậm hàng về và năng lực vận chuyển giới hạn. Sau khi
đổi tên, các tham chiếu, ledger tồn, phiếu nhập, lượng giữ và vận đơn vẫn được đối
chiếu. Hai đơn thử có nhu cầu vượt phần hàng chưa phân bổ; một kế hoạch hàng về
sau hạn giúp minh họa vì sao tổng hàng đủ nhưng không đáp ứng đúng giờ.

Tài nguyên và mật khẩu ban đầu nằm trong `.tmp/decision-demo/`, được Git ignore.
Không gửi nguyên thư mục này lên Git. `credentials.json` có tài khoản của từng
vai trò; mật khẩu `minhhung` cũ không được sao chép sang file này. Đăng nhập lần
đầu tài khoản mới phải đổi mật khẩu và tự đăng ký Authenticator trên Keycloak.
Script không đăng ký OTP thay người dùng và không bật password grant cho Portal.

## Dựng profile trên hạ tầng local đã chuẩn bị

Đây là bộ ghép demo, **chưa phải trình cài đặt một lệnh cho máy trắng**. Cần Python
và dependencies theo lockfile, Node/Portal dependencies, Docker Desktop Linux,
PostgreSQL local đã migrate, 12 Data Products đã công bố trong Core/Hasura và
Keycloak local HTTPS `18443`, realm `flowbridge-it`, CA/admin config có sẵn.
Xem [database/README.md](database/README.md), [DATA-WORKSPACE.md](DATA-WORKSPACE.md)
và [AUTHENTICATION.md](AUTHENTICATION.md). Không chạy lại profile Supabase cũ trong
DOCKER.md để dựng demo này.

Workspace DB cần được khởi tạo bằng `scripts.data_workspace init-local` trước;
không ghi đè binding cũ nếu máy đã có Workspace. Bộ ghép demo sử dụng DSN local đã
lưu, và cấu hình org mới trong file riêng của demo. Dừng các launcher cũ đang
chiếm cổng trước khi start; script không tự tắt process không thuộc mình.

Chạy từ gốc repository, khi hạ tầng trên đã sẵn sàng:

```powershell
.\.venv\Scripts\python.exe -m scripts.decision_demo_data preview
.\.venv\Scripts\python.exe -m scripts.decision_demo_data seed-local
.\.venv\Scripts\python.exe -m scripts.decision_demo_runtime configure
.\.venv\Scripts\python.exe -m scripts.decision_demo_runtime start
.\.venv\Scripts\python.exe -m scripts.decision_demo_access
.\.venv\Scripts\python.exe -m scripts.decision_demo_probe
```

Trên máy đã có bản ghi Portal `minhhung`, có thể gắn rõ tài khoản đó bằng
`configure --coordinator minhhung`. Script kiểm tra issuer/subject/binding đã lưu;
không đổi mật khẩu, không kích hoạt lại principal hoặc membership đã bị thu hồi.
Membership được định danh theo principal, tránh nhầm với tài khoản demo trước đó.

Seed lưu ledger riêng cho từng domain sau khi commit. Chạy lại giữ các domain đã
xong, kể cả thay đổi phát sinh qua API; không reset dữ liệu. Nếu bị ngắt giữa các
domain, chạy lại đúng ngày ghi trong `data-resources.json`, ví dụ `--day 2026-10-26`.
Ngày/org khác hoặc dữ liệu xung đột sẽ dừng để đối soát, không xóa hay nhận bừa dữ liệu.

Profile đã seed bằng v1 cần chạy rõ `python -m scripts.decision_demo_data
extend-local-capacity` một lần để thêm lịch nhà vận chuyển dự phòng. Lệnh này chỉ
thêm bảy dòng lịch mới vào Logistics local thuộc ledger hiện tại, chạy lại đối
chiếu cùng khóa và không sửa/hủy đặt lịch đang có. Profile mới đã bao gồm phần này.

Access setup dùng Core API cho yêu cầu/cấp quyền đọc có hạn 48 giờ, đăng ký 23
native actions và triển khai BPMN đã review. Grant hết hạn/bị thu hồi cần quản trị
viên xử lý rõ ràng; không âm thầm cấp lại. Platform admin của setup chỉ phục vụ
bootstrap local, không trở thành người duyệt nghiệp vụ.

```powershell
.\.venv\Scripts\python.exe -m scripts.decision_demo_runtime status
.\.venv\Scripts\python.exe -m scripts.decision_demo_runtime restart workspace
.\.venv\Scripts\python.exe -m scripts.decision_demo_runtime stop
```

Stop/restart chỉ dùng PID/command/run-id và container/label thuộc ledger demo.
Stop giữ database, users và dữ liệu; không dùng `docker compose down -v` để dọn demo.

## Đọc gộp và đo tốc độ

`FLOWBRIDGE_WORKSPACE_SCAN_PAGES=4` bật `/data-products/{id}/scan` trong profile.
Mỗi scan tối đa bốn trang, mỗi trang tối đa 100 dòng; khóa nghiệp vụ phải đầy đủ,
không đọc quan hệ lồng nhau và không vượt ngân sách độ phức tạp/offset. Core
kiểm quyền và metadata trước/sau, không trả dữ liệu một phần khi bị thu hồi hoặc
drift. Workspace phát hiện trùng khóa xuyên các scan, giữ lọc org/kho tại Core,
và kiểm quyền lại trước khi trả kết quả tổng hợp. Không cache quyền giữa các lượt.
Đích Core cũ chưa có `/scan` giữ cấu hình mặc định một trang; không fallback sang SQL.

Core có pool opt-in `FLOWBRIDGE_CORE_DATABASE_POOL_SIZE=8`, mặc định vẫn NullPool.
Pool không dùng transaction pooler Supabase 6543. Kiểm thử PostgreSQL riêng kiểm tra
tái sử dụng connection không rò chế độ read-only/advisory lock và không kích hoạt
lại membership bị thu hồi khi chạy lại cấu hình.

`decision_demo_probe` đo OAuth dịch vụ thật → Workspace → Core → Hasura → PostgreSQL,
kiểm lịch sử, hai đơn thử, ML và metric mô phỏng. Kết quả thành công có timestamp tại
`.tmp/decision-demo/live-probe.json`. Đây **không phải** bằng chứng hoàn tất MFA bốn
vai trò. Không đọc artifact cũ như kết quả của một lượt probe vừa thất bại.

Hai lượt đo cuối ngày 26/10 khi máy ít tải: danh sách kho 1,881–1,986 giây,
kế hoạch 9,935–9,944 giây, scenario 5,809–6,142 giây. Lượt trước khi tối ưu nhóm
đơn/đang chạy kiểm thử từng mất 41,417 và 23,468 giây; các lượt đầu có timeout
60 giây. Không tăng timeout để coi như đã khắc phục. Hai mẫu đo không phải SLO,
p95 hay kiểm thử tải production; vẫn cần đo khi nhiều người dùng đồng thời.

## Kiểm thử và phần nghiệm thu còn lại

Để kiểm thử giao dịch owner bằng tài khoản SSO hiện tại, bản Vite development có
trang `http://127.0.0.1:5174/?native-operator=1`. Trang này chỉ chạy trên loopback,
không nhận token qua ô nhập, không cấp thêm quyền và không đăng ký công cụ ghi
cho Agent. Chọn một action đã đăng ký, nhập DTO và giữ Idempotency-Key khi cần
đối soát/thử lại. API chủ sở hữu vẫn quyết định quyền và kiểm tra phiên bản.
Thao tác bàn giao có xác nhận riêng cho tình huống giả lập; không dùng trang này
để xác nhận hàng thật chưa được bàn giao. Nút “Về Workspace” thoát chế độ kiểm thử.
Trang này bị loại khỏi bản production; không phải màn nghiệp vụ dành cho khách hàng.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.decision_test_environment --include-business --include-core-pool
.\.venv\Scripts\python.exe -m scripts.decision_workflow_test_environment
cd portal
npm test
npm run build
npm run test:e2e -- tests/e2e/data-workspace.spec.ts
npm run test:e2e -- tests/e2e/native-owner.spec.ts
```

Hai runner tích hợp tạo PostgreSQL/Flowable cô lập có ownership ledger rồi dọn
đúng tài nguyên của lượt đó. XML/log riêng trong `.tmp/decision-tests/`. Một test
bị skip không được tính là đã đạt; bridge workflow có profile riêng.

M12 chỉ hoàn tất sau khi ghi nhận đủ các bước sau trên môi trường thật:

- Người điều phối lập/gửi hồ sơ bằng SSO MFA.
- Kho và Vận chuyển xác nhận bằng hai tài khoản độc lập; quản lý duyệt sau đó.
- Các owner ghi giao dịch hợp lệ; đối chiếu đủ hàng bàn giao rồi mới đóng hồ sơ.
- Thay đổi nguồn quan trọng buộc sửa phiên bản; thu hồi quyền chặn đọc/thao tác.
- Đo lại độ trễ nhiều lượt và nghiệm thu kịch bản trình diễn 15 phút.

Các test tự động cô lập đã kiểm nhiều nhánh này, nhưng không thay thế những bước
người dùng thật chưa chạy. Không đánh dấu nghiệm thu trọn vẹn chỉ từ test mock.

Kết quả trực tiếp ngày 26/10/2026: bốn tài khoản vai trò đã đăng nhập MFA; hồ sơ
SO-PHOIHOP-001 được trả lại ở phiên bản 1, lập phiên bản 2, được Kho/Vận chuyển
xác nhận lại và quản lý duyệt. Kiểm tra thiếu bằng chứng bàn giao đã bị chặn,
giữ hồ sơ ở APPROVED. Sau đó Kho đã ghi nhập sớm 40 cái, giữ đủ 188 cái và chuẩn
bị phiếu xuất; Vận chuyển đặt lịch An Tín; Kho ghi bàn giao giả lập đủ 188/188 cái.
Gửi lại cùng mã không tạo thêm sự kiện hoặc trừ tồn lần hai. Logistics đã đối
chiếu sang HANDED_OVER; Sales đối chiếu sang COMPLETED. Core xác minh bằng chứng,
Flowable kết thúc đúng phiên bản 2 và Portal hiển thị Đã bàn giao. Đối soát sự kiện
nguồn thêm 7 quan sát vào lịch sử. Đây là giao dịch API/DB local thật trên dữ liệu giả lập,
không phải hàng hóa thực ngoài đời. Đợt nhập 40 cái không được coi là thực hiện
đủ giả định thêm 1.000 cái trong scenario đã duyệt. Còn kiểm thử live thay đổi
nguồn/thu hồi quyền và đo tải/trình diễn trước khi đánh dấu M12 hoàn tất.
