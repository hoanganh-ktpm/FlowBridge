# Dữ liệu mẫu xuyên ba database

## Timeline generator 365 ngày

`scripts/database/simulation.py` tái sử dụng identity, NUMERIC và canonical hash của fixture cũ; batch loader dùng chung trong `seed.py`. Timeline tạo nhu cầu Poisson từ cấu hình riêng `synthetic_profiles.yaml`, xác nhận đơn, giữ đủ mọi dòng, nhập bổ sung, xếp lịch theo capacity rồi mới phát chứng cứ handoff. Ledger và tồn cuối được suy ra từ giao dịch; không gán random cờ trễ. Chỉ receipts POSTED tăng tồn; tồn đầu kỳ cũng có receipt/movement. Không có transaction xuyên DB, mô hình forecasting hoặc customer-delivery state.

Sau [khởi tạo PostgreSQL local](README.md):

```powershell
.venv/Scripts/python.exe -m scripts.database.synthetic generate --days 60 --scale 0.15 --dry-run
.venv/Scripts/python.exe -m scripts.database.synthetic generate --days 60 --scale 0.15
.venv/Scripts/python.exe -m scripts.database.synthetic generate --reset-demo
.venv/Scripts/python.exe -m scripts.database.synthetic validate
.venv/Scripts/python.exe -m scripts.database.synthetic stats
.venv/Scripts/python.exe -m scripts.database.synthetic generate
```

Mặc định: seed `20261024`, reference date `2026-10-20`, 365 ngày, scale `1`, 8 SKU và 40 khách. Chỉnh bằng `--seed`, `--reference-date`, `--days`, `--scale`. Fast mode bỏ kiểm tra pattern cả năm và báo SKIP rõ ràng. Full dataset có 7.040 orders/reservations/dispatches/shipments, 231 inbound plans, 229 receipts; 6.910 completed, 130 pending, 2.547 handoff đúng hạn và 4.363 trễ. Có 18 receipts trễ và 776 đơn chờ hàng được xử lý lại. Các số này thuộc cấu hình mặc định, không phải nhãn AI.

Mặc định không reset. `--reset-demo` chỉ chấp nhận loopback/demo names/owners, kiểm tra mọi business table không có dòng lạ hoặc chỉnh sửa sau seed, và không đụng Core/Hasura. Mỗi DB dùng session advisory lock và transaction riêng; ledger `.tmp/local-postgres/synthetic-run.json` ghi IN_PROGRESS trước khi commit. Nếu lỗi giữa các DB, sửa lỗi rồi chạy lại đúng cấu hình: database đã commit được xác minh, database chưa commit được nạp tiếp, không thêm movement/event trùng. Không xóa ledger hoặc volume để né lỗi. Khi COMPLETE, checksum mọi cột được giữ để chặn reset dữ liệu đã chỉnh sửa.

Validator đọc lại ba DB riêng, báo invariant và ID/sample lỗi. Nó kiểm tồn theo timeline, ledger hai chiều, reservation toàn đơn, completion từng dòng, Warehouse evidence, capacity, nguồn receipt/plan, SKU/UOM/refs, chronology, duplicate, sáu tình huống và trend/weekly/seasonal/intermittent/spikes. Các nguồn công bố không chứa profile/hidden parameters.

Smoke thật (cần test Keycloak hiện có, không sửa realm):

```powershell
$env:FLOWBRIDGE_LOCAL_E2E='1'
.venv/Scripts/python.exe -m pytest tests/e2e/test_local_postgres.py -m e2e -q
```

Smoke đi qua Core HTTP/worker, live discovery, sáu contract publications, SQL reader chỉ đọc view và Hasura JWT. Sales thấy thay đổi Warehouse qua Data Product; thay đổi thử được hoàn nguyên cùng receipt/ledger. Kiểm anonymous, outsider, IT không có grant và JWT sau revoke. Không thay thế nghiệm thu Business APIs/Workflow thật. Thiếu cấu hình Keycloak được báo BLOCKED/SKIP.

## Fixture bàn giao nhỏ (giữ để regression)

Bộ seed là một **ảnh chụp nghiệp vụ giả lập tại 15:00 ngày 20/10/2026, múi giờ UTC+07:00**. Tên người, khách hàng, địa chỉ liên hệ và số chứng từ dùng để trình diễn; email dùng miền `.example`. Tên sản phẩm và đơn vị được ghi rõ để đọc và đối chiếu được. Dữ liệu này không chứng minh các dịch vụ Hasura, Flowable hoặc vận chuyển đã được chạy.

UUID được tạo bằng UUIDv5 từ khóa ổn định. Cùng một đơn hàng, dòng hàng, mặt hàng và lần xuất kho được tham chiếu xuyên các database bằng đúng UUID đó. Khóa ngoại vật lý chỉ tồn tại bên trong từng database và luôn kèm `org_id`.

## Năm tình huống để demo

| Đơn bán hàng | Nội dung | Trạng thái tại mốc chụp | Bằng chứng cần đối chiếu |
| --- | --- | --- | --- |
| SO-2026-0099 | 3 laptop Dell Latitude 3540 và 5 chuột Logitech B100 | Đã giữ đủ hàng, chưa xuất | Reservation `RSV-2026-0099`; vẫn đóng góp vào lượng giữ chỗ |
| SO-2026-0100 | 10 laptop và 5 chuột | Hoàn tất bàn giao cho đơn vị vận chuyển qua hai đợt | `DSP-2026-0100-1` và `DSP-2026-0100-2`; ba dòng xuất kho có ledger âm, hai shipment đã nhận sự kiện Warehouse |
| SO-2026-0101 | 4 laptop | Chờ đủ hàng | Chỉ còn 2 laptop khả dụng; có lệnh thất bại `INSUFFICIENT_STOCK`, không tạo reservation hoặc giữ một phần |
| SO-2026-0102 | 12 ram giấy Double A A4 80 gsm | Đã chuẩn bị, Logistics đã chấp nhận manifest | Warehouse `PREPARED`, Logistics `ACCEPTED`, số đã bàn giao bằng 0, chưa trừ tồn vật lý |
| SO-2026-0103 | 2 chuột | Đã hủy trước khi cấp hàng | Reservation `CANCELLED` không có dòng, là dấu chặn chống lệnh cấp hàng đến muộn |

Không có thông tin “đã giao đến khách” trong bộ mẫu. `COMPLETED` của Sales và `HANDED_OVER` của Logistics chỉ phản ánh việc Warehouse bàn giao đủ cho đơn vị vận chuyển.

## Diễn biến tồn kho và giữ chỗ

Ngày 15/10, phiếu `GR-2026-0001` nhập tồn đầu kỳ: 8 laptop, 25 chuột và 50 ram giấy. Các số này đều có dòng phiếu nhập và bút toán tăng tương ứng, không được ghi độc lập vào số dư.

Sau khi giữ 3 laptop và 5 chuột cho đơn 0099, chỉ còn 5 laptop khả dụng. Vì đơn 0100 cần 10 laptop, lần xin cấp hàng ngày 16/10 thất bại hoàn toàn; chuột cũng không bị giữ riêng. Ngày 17/10, phiếu `GR-2026-0002` nhập thêm 7 laptop. Lúc này đủ để giữ toàn bộ 10 laptop và 5 chuột cho đơn 0100.

Ngày 18/10 lúc 10:00, đợt 1 bàn giao 6 laptop và 5 chuột. Ngày 19/10 lúc 09:00, đợt 2 bàn giao 4 laptop còn lại. Mỗi dòng chỉ tạo một movement âm; lượng giữ chỗ cũng giảm đúng bằng lượng đã bàn giao.

| Mặt hàng | Đơn vị | Tổng nhập | Đã bàn giao | Tồn vật lý | Đang giữ | Khả dụng |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| LAP-DELL-3540 — Dell Latitude 3540 | cái | 15 | 10 | 5 | 3 | 2 |
| MOU-LOGI-B100 — Logitech B100 USB | cái | 25 | 5 | 20 | 5 | 15 |
| PAP-DA-A4-80 — Double A A4 80 gsm | ram | 50 | 0 | 50 | 12 | 38 |

Các số dư trong seed được **tính từ ledger và các bộ đếm reservation**, không tự điền thành một bộ số riêng. Đợt giấy đã được Logistics chấp nhận vẫn giữ đủ 12 ram, vì chưa có bàn giao vật lý. Không cộng “cái” và “ram” thành một tổng số lượng chung.

## Hạn bàn giao và tính trễ

Đơn 0100 có hạn bàn giao gốc là **18/10/2026 17:00**. Đợt đầu sớm hơn hạn 7 giờ; đợt cuối lúc **19/10/2026 09:00**, nên toàn đơn hoàn tất trễ **16 giờ**. Mốc `Sales.completed_at` lấy `MAX(Warehouse.handed_over_at)` của các đợt làm đủ toàn bộ dòng hàng. Ngày Logistics chấp nhận manifest không được dùng thay ngày bàn giao thực tế.

Ví dụ này thể hiện vì sao phải giữ hạn gốc và liên thông đến từng dòng hàng: chỉ nhìn đợt đầu hoặc trạng thái “đã chuẩn bị” sẽ kết luận sai. Các đơn 0099, 0101 và 0102 chưa đến hạn tại mốc dữ liệu; đơn 0103 đã hủy nên cần loại khỏi mẫu số đánh giá thực hiện.

## Manifest và sự kiện mẫu

Mỗi dispatch có manifest hash SHA-256 của JSON chuẩn hóa: khóa object sắp xếp theo tên, UTF-8, không khoảng trắng thừa. Đối tượng gồm `org_id`, `warehouse_ref`, `dispatch_id`, `reservation_id`, `sales_order_ref`, `source_order_revision`, `manifest_version`, và `lines` theo số dòng. Mỗi dòng gồm `dispatch_line_id`, `reservation_line_id`, `sales_order_line_ref`, `product_ref`, `sku`, `uom`, `quantity` (chuỗi có ba chữ số thập phân). Logistics nhận đúng version/hash và các dòng nguồn; không ghép theo tên sản phẩm.

Những outbox có trạng thái `PUBLISHED` và inbox `PROCESSED` mô tả **ảnh chụp kỳ vọng của một lần trao đổi đã xử lý** trong dữ liệu mẫu. Chúng có cùng event ID, payload và hash xuyên bên gửi/bên nhận. Seed không phát sự kiện thật và không gọi các dịch vụ ngoài. Các outbox `PENDING` vẫn chờ worker của ứng dụng sau này; chạy seed không tự xử lý queue.

Thông tin người nhận để tạo nhãn nằm trong Sales và snapshot Logistics. Những payload liên thông mẫu chỉ chứa định danh, dòng hàng và bằng chứng nghiệp vụ cần thiết; không mang email, điện thoại khách hàng hoặc giá vốn Warehouse.

## Câu hỏi demo có đáp án xác định

- Đơn SO-2026-0100 gồm những món nào, đã bàn giao hết chưa, trễ bao nhiêu giờ?
- Vì sao đơn SO-2026-0100 từng chờ hàng rồi mới cấp đủ được?
- Đơn nào đang chờ hàng và thiếu bao nhiêu laptop? → 0101, cần 4 nhưng chỉ khả dụng 2.
- Đơn SO-2026-0102 đã được Logistics chấp nhận; tại sao tồn giấy chưa giảm?
- Tồn laptop 5 cái nhưng chỉ bán tiếp được 2 cái là vì sao?
- Lệnh cấp hàng đến muộn cho đơn 0103 phải bị chặn bởi dữ liệu nào?

Đây là bộ mẫu theo tình huống để kiểm thử liên thông và bất biến nghiệp vụ. Nó không phải tập dữ liệu ngẫu nhiên dùng để huấn luyện hay chứng minh độ chính xác mô hình thống kê.
