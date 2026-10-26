# Demo Decision Workspace trên máy local

Phạm vi nghiệp vụ dừng ở **bàn giao đủ hàng cho đơn vị vận chuyển bên thứ ba**.
Đặt lịch vận chuyển hoặc duyệt phương án chưa phải đã bàn giao. Dữ liệu trong
profile này là dữ liệu giả lập có tên sản phẩm dễ đọc; không phải giao dịch thật.

## Mở bản đang chạy trên máy Hùng

Mở `http://127.0.0.1:5174/#data`, đăng nhập SSO. Tài khoản `minhhung` được phân công
làm người điều phối trong profile local này. Các vai trò Kho, Vận chuyển và Quản lý
là ba tài khoản độc lập. Không dùng tài khoản điều phối để xác nhận thay họ.

Tài khoản quản trị `minhhung` hiện mở **Tổng quan kho và đơn hàng** theo cấu hình đã
duyệt: Bình Dương và TP.HCM đều được đọc từ database. Nút **Làm mới dữ liệu**
đọc lại cả hai; kho thiếu lịch sử vẫn hiện tồn/đơn nguồn và lý do chưa tính được
dự báo. Mỗi kho có thẻ tóm tắt và nút **Xem tồn kho / Xem đơn hàng**. Vùng chi tiết
chỉ hiển thị kho đang chọn; có tìm kiếm, lọc mặt hàng có thể thiếu và phân trang đơn.
Nút **Thử phương án nhập hàng** chỉ hiện cho kho thuộc phạm vi phân tích được cấp.
Đây là tổng quan quản trị đã cấu hình, không mở rộng phạm vi
cho tài khoản Kho/Vận chuyển thông thường. Xem ORGANIZATION-REGISTERS.md.

1. **Tổng quan kho:** ở thẻ Bình Dương chọn **Xem tồn kho**, lọc **Có thể thiếu**.
   Bấm **Thử bổ sung** ở dòng bàn phím KB-K120 để mở đúng kho và mặt hàng.
   Muốn xem đơn, chọn **Xem đơn hàng**, tìm mã đơn hoặc tên mặt hàng.
2. **Kiểm tra hàng và thử phương án:** bấm **Tính lại dự báo** khi đổi lựa chọn,
   nhập lượng và ngày/giờ bổ sung,
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

### Giữ dữ liệu trong phiên và thông báo thay đổi

Portal lưu các kết quả đọc trong `sessionStorage`, tách theo tài khoản và chế độ
live/preview. Tải lại trang vẫn kiểm tra SSO/quyền và manifest `/changes` trước khi
phục hồi kết quả; không chạy lại bảng/dự báo chỉ vì chuyển trang. Nút làm mới bỏ
qua cache. Đăng xuất, đổi quyền, đổi ngày hoặc thay đổi nguồn làm mất hiệu lực
kết quả cũ. Không lưu token hay lệnh ghi trong cache.

Manifest kiểm tra khoảng 8 giây sau mỗi phản hồi, chỉ đọc revision của outbox
đã commit tại Sales/Kho/Logistics và revision hồ sơ thuộc người tham gia hiện tại.
Đây là kiểm tra nhẹ định kỳ, không phải WebSocket/push và không tự chạy lại dự báo.
Nguồn đổi sẽ tạo thông báo trong Portal của những người được phân công cùng kho;
thao tác trên màn hình cũ bị chặn tới khi bấm **Cập nhật dữ liệu ngay**. Trang đang
xem được đọc lại, lựa chọn kho/kỳ và giả định nhập thử được giữ. Không tự duyệt hay
tạo giao dịch. Các bước kiểm tra điều kiện/quy trình luôn đọc mới.

Phạm vi theo dõi là tổ chức và kho đã được cấp quyền `operations.events.read`;
không tự mở rộng quyền. Tổng quan khác tổ chức chưa có quyền theo dõi tương ứng
không được lưu cache lâu. Khi manifest lỗi/thiếu nguồn, bỏ cache và báo rõ giới
hạn theo dõi. Sửa trực tiếp bằng SQL hoặc luồng ghi không tạo native outbox event
chưa được phát hiện; phải bấm làm mới hoặc bổ sung bộ phát sự kiện tại hệ nguồn.
Thông báo lưu trong phiên trình duyệt, chưa phải hộp thư thông báo trên server.

### Biểu đồ theo đơn hàng

Trong **Tổng quan kho → Biểu đồ phân tích → Theo đơn hàng**, hai góc nhìn được
tách khỏi biểu đồ mặt hàng và chỉ tải khi mở:

- **Hạn bàn giao:** thanh xếp chồng đếm đơn có vướng mắc/chưa xác minh/đáp ứng
  theo kế hoạch. Nhóm riêng đơn đang mở đã quá hạn; kỳ trên 14 ngày gộp 7 ngày.
  Chọn thanh để xem đúng nhóm đơn. Mỗi đơn chỉ được tính một lần.
- **Điều kiện từng đơn:** ma trận hàng trong Kho, thời gian chuẩn bị, lịch lấy
  hàng, lượt nhận của nhà vận chuyển. Lọc theo hạn/điều kiện, ưu tiên hạn sớm,
  phân trang 10 đơn. Chọn mã đơn hoặc ô điều kiện để xem món thiếu, lượng thiếu,
  hạn chuẩn bị và lịch lấy hàng; có đường dẫn mở kế hoạch đúng đơn.

Request `/all-warehouses` thêm `include_order_analysis=true`, bắt buộc chọn một
kho được phép và `include_planning=true`. Chỉ chế độ này đọc thêm schedule và
capacity; không đọc lịch sử receipts chỉ để phân tích đơn. Nếu thiếu lịch sử dự
báo, giữ được hạn các đơn với điều kiện chưa biết; không tạo số liệu thay thế.
Quyền nguồn vẫn được kiểm tra lại sau tính toán. Không tạo schema hay quyền mới.

Điều kiện hàng của một đơn lấy từ `order_supply` xét tất cả SKU, không dùng một
SKU đại diện để kết luận toàn đơn. Số lượng khác đơn vị không cộng chung. Đơn có
hạn sau kỳ được đếm riêng và không dùng làm mẫu số của biểu đồ. Số vướng từng
điều kiện có thể trùng đơn; không cộng thành tổng. Kết quả chỉ là đánh giá kế
hoạch tại thời điểm đọc, không phải tỷ lệ trễ thực tế, xác suất, hoặc bằng chứng
đã giữ hàng/đóng gói/bàn giao. Đơn đã quá hạn không tự quy lỗi cho một bộ phận.

### Thao tác theo sơ đồ và Dialog

Các trang giữ hai nhóm điều hướng hiện có. Khối có mũi tên là điểm mở nội dung,
không phải lệnh tự động chạy nghiệp vụ:

- Tổng quan kho: chọn Bán hàng/Kho/Vận chuyển để mở dữ liệu đúng kho trong Dialog,
  rồi đi tiếp tới đơn, thử bổ sung hoặc đọc lịch bàn giao. Mở khối dùng dữ liệu đã
  tải; không gọi lại nguồn chỉ để xem. Chi tiết đơn và nguồn tính mở ở giữa màn hình.
- Phương án bổ sung: sơ đồ dẫn tới dữ liệu đầu vào, hộp thoại nhập thử, kết quả và
  hồ sơ phối hợp. Đóng hộp thoại giữ giả định đã nhập. Chỉ So sánh mới tính lại;
  lưu/gửi vẫn là hành động riêng. Dialog hiển thị lỗi ngay trong vùng đang thao tác.
- Hồ sơ phối hợp: mở hồ sơ trong Dialog; chọn đối chiếu, phản hồi, tiến độ hoặc
  lịch sử. Các ô bằng chứng mở thời điểm và nguồn của bản ghi đã tải. Chưa có bản
  ghi không được suy ra đã xong; các loại bằng chứng không phải chuỗi bắt buộc.
- Tra cứu dữ liệu: chọn nhóm kho/mặt hàng/đơn; mở dòng hàng trong Dialog, giữ bộ
  lọc và trang danh sách khi quay lại.
- Phân tích và dự báo: chọn phạm vi qua Dialog hoặc sơ đồ; xu hướng và nhịp tuần
  hiển thị luân phiên. Mở rộng biểu đồ không chạy lại dự báo hay đọc thêm nguồn.
- Thiết lập IT: chia chọn nguồn, ghép cột, quy tắc kiểm tra thành ba bước. Xem
  trước và bảng cột ở Dialog; kết quả xem trước trả muộn bị bỏ nếu cấu hình đã sửa.

Dialog dùng giới hạn chiều cao, cuộn trong vùng nội dung và hỗ trợ Escape/focus
trở về điểm mở. Khi workspace nhận thay đổi nguồn, các Dialog đang mở đóng để
hiện thông báo yêu cầu cập nhật; người dùng không tiếp tục trên kết quả cũ.
