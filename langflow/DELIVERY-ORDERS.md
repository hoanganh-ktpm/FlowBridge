# Tra cứu đơn giao trễ và diễn giải rủi ro đơn

## Hành vi cần giữ khi tích hợp

Đây là hợp đồng đã có kiểm thử hồi quy, không phải yêu cầu đóng băng mọi code.
Thay đổi cần giữ các hành vi dưới đây hoặc cập nhật hợp đồng và kiểm thử cùng lúc.

- Câu hỏi “đơn nào của NCC đó trễ” dùng **Dữ liệu**, task `delivery_orders`.
  RAG giải thích nhận xét/nguyên nhân có nguồn; không thay database khi liệt kê đơn.
- Mỗi lịch giao được đối chiếu với **hạn gốc** và phiếu nhận đã được ghi nhận tại
  `as_of`. Nhận một phần chưa phải nhận đủ. Ngày hứa mới không thay hạn đánh giá.
- Tách đơn đã nhận đủ trễ với đơn đang quá hạn/chưa nhận đủ. Ngày nhận đủ của
  lịch chưa hoàn tất là null. Số ngày trễ của lịch đang mở tính đến `as_of`.
- Một đơn có nhiều hạn giao: trình bày từng lịch. Không lấy ngày nhận đủ toàn đơn
  trừ hạn cuối cùng để che một lịch giao sớm đã bị trễ.
- Giữ mã đơn, tên hàng, đơn vị, lượng theo lịch, lượng nhận, ngày và số ngày trễ
  trong kết quả Agent lẫn JSON frontend. Dataset chi tiết đếm **lịch giao**;
  `total_matches` đếm **đơn**. Không cộng số dòng bảng thành số đơn.
- Tối đa 5 lịch/đơn và 5 mã phiếu nhận/lịch trong phản hồi. Có cắt phải giữ cờ;
  hỏi chi tiết một đơn thì dùng mã đơn đó qua công cụ đọc đơn hiện có.
- Giữ phân trang, tình trạng thiếu dữ liệu, lỗi 401/403/429/503 và trường hợp
  NCC nhập nhằng. Không tự chọn NCC đầu tiên, không biến lỗi API thành “không có đơn”.
- Không ghi database, chạy SQL từ model, gọi thêm model sửa JSON hoặc bỏ kiểm quyền.
  Danh sách chi tiết dùng bearer người hỏi và cùng quyền đọc PR/PO của hệ thống.
  `scope=caller_visible_orders`: nhân viên đọc đơn gắn PR của mình; người duyệt/kho
  đọc trong phòng ban được cấp quyền. Người được ADMIN cấp `PROCUREMENT_VIEWER`
  đọc PR/PO và lịch sử giao hàng toàn công ty (`scope=company_procurement_reader`).
  Đây là quyền đọc, không cấp quyền
  tạo/sửa PR, duyệt đề xuất, nhận hàng hay sửa tài liệu. Không dùng AI token để mở rộng phạm vi.
  Tên sản phẩm là nhãn danh mục hiện tại, không phải tên
  lịch sử đã đóng băng; trạng thái hủy cũng là trạng thái hiện tại.

## Giao tiếp với công cụ

Adapter `Chuẩn hóa giao tiếp A2A` hiện có bổ sung khả năng qua mô tả công cụ lúc chạy:

```json
{
  "task": "delivery_orders",
  "question": "Những đơn nào của NCC-03 giao trễ?",
  "business_date": "2026-10-19",
  "known": {
    "supplier": "NCC-03",
    "delivery_filter": "late",
    "page": 1,
    "page_size": 5
  },
  "evidence": []
}
```

`late`: có ít nhất một lịch đã hoàn tất trễ hoặc đang thiếu hàng sau hạn.
`completed_late`: chỉ đơn đã nhận đủ trễ. `open_overdue`: phần còn thiếu đang quá hạn.
`all`: mọi đơn phân loại được và chưa rõ lịch, trừ đơn hủy/từ chối.
Chưa hỗ trợ lọc kỳ/mặt hàng tại đây; tham số chưa hỗ trợ bị từ chối, không bị bỏ qua.
Không tìm thấy ở trang ngoài phạm vi không có nghĩa NCC chưa từng có đơn trễ.

Nhánh này resolve NCC từ API danh mục, sau đó gọi
`POST /internal/v1/tools/supplier-delivery-orders` bằng bearer người hỏi.
Tổng số và chi tiết dùng cùng snapshot chỉ đọc, cùng bộ phân loại và quyền đọc PR/PO.
Mốc chốt do server đặt lúc đọc, không nhận cutoff hoặc user/role do model đưa vào.
`business_date` không ghi đè mốc này. Thống kê AI cũ vẫn ở endpoint riêng, dùng
JobContext và `scope=global_ai_service`; không trộn hai phạm vi/mốc chốt. Có `next_page` thì gọi trang đó,
giữ NCC/filter. Mỗi lookup dùng ngân sách A2A hiện có nhưng không cần lượt model con.
Cache/chứng cứ chỉ thuộc một root execution. Khi phục vụ SSO, token của caller
được ưu tiên; không rơi về token test local khi caller thiếu quyền. Endpoint danh sách
không cần AI token/JobContext và từ chối context header ngoài hợp đồng.

Không sửa file graph, ID, cấu hình model, prompt lưu trên canvas hoặc dây nối.
Cần khởi động lại backend và Langflow để Python nạp runtime mới; không Update All
Components hay import đè flow đang chỉnh. Khả năng model chọn đúng task cần thử chat
thật; kiểm thử công cụ không chứng minh chất lượng hội thoại.

## Rủi ro dự đoán của cả đơn

Đây là bài toán khác với xác nhận đơn đã giao trễ. Xem `docs/ML-SERVICE.md`.
`remaining_value_weighted_v2` dùng trung bình điểm theo giá trị hàng còn chưa nhận,
giữ riêng dòng có điểm cao nhất và cảnh báo. Không diễn giải thành xác suất cả đơn.
Đọc policy trả về vì bản replay `max_open_item_v1` vẫn có nghĩa MAX cũ.

## Xem các dòng lịch sử tạo đặc trưng ML

Câu “cho xem 11 bản ghi lịch sử” sau kết quả ML dùng Data task `history_samples`.
known giữ `product`/`supplier` từ kịch bản trước, hoặc UUID đã xác minh;
`reference=result.history_reference` của kết quả mới. Kết quả cũ có thể dùng
`feature_asof=trace.feature_asof`, `expected_count=11`. Không chạy lại ML, không gọi
RAG để đoán các mẫu. Không có cutoff/reference thì đọc lịch sử hiện tại với cảnh
báo rõ ràng, không nhận đó là nguyên tập 11 mẫu cũ. Số lượng trùng chưa xác minh
thành viên tập mẫu. Giữ `reference_matches`/`expected_count_matches` trong output.

`hist_sample_count` đếm dòng hàng đã nhận đủ cùng NCC/sản phẩm/đơn vị trong cửa sổ
lịch sử, không phải toàn bộ ngữ cảnh TabICL. Trả bảng mã đơn, số lượng, hạn gốc,
ngày nhận đủ và số ngày trễ của cả dòng đúng hạn lẫn trễ; không chỉ chọn mẫu trễ.
API `/api/v1/ml/history` yêu cầu `PROCUREMENT_VIEWER`; lỗi 403 là thiếu quyền,
404/422/503 hoặc thiếu công cụ không được gọi chung là “không có quyền”.
Mô tả task được bổ sung vào tool adapter lúc chạy, giữ nguyên prompt/model/node
đang lưu trong canvas. Kiểm thử `test_prediction_history.py` và `test_ver2_history.py`
giữ hành vi này, gồm thay mẫu mà vẫn giữ nguyên số lượng và receipt đến sau cutoff.

## Cấp quyền người phụ trách mua hàng

Dùng bootstrap IAM hiện có, directory xác minh người dùng Keycloak và ADMIN đang
hoạt động trong database. Lệnh mặc định dry-run; thêm `--apply` mới commit:

```powershell
python scripts/bootstrap_identity.py --subject <KEYCLOAK_USER_UUID> --grant PROCUREMENT_VIEWER --granted-by <APPLICATION_ADMIN_UUID>
```

Với profile Keycloak local thêm `--identity-dir <THU_MUC_IDENTITY_LOCAL>`.
Bootstrap tạo role human/global nếu chưa có, giữ nguyên role đã tồn tại, cấp grant
có kiểm kiểu/phạm vi và ghi audit outbox trong cùng transaction. Không cấp cho mọi
nhân viên, không sửa role EMPLOYEE, không cần thêm bảng/migration. Role này chỉ mở
quyền đọc; ghi nghiệp vụ vẫn kiểm đúng vai trò và phạm vi cũ. Thu hồi qua API quản
trị grant hiện có; quyền được đọc lại từ DB ở lần gọi sau, không tin role trong prompt.

## Câu hỏi nghiệm thu trên Langflow

1. “Liệt kê 5 đơn của NCC-03 có lịch giao trễ. Cho mã đơn, tên hàng, hạn gốc,
   ngày nhận đủ, số ngày trễ và mốc chốt dữ liệu.”
2. “Xem 5 đơn tiếp theo của NCC-03, giữ cùng điều kiện.”
3. “Chỉ các đơn đã nhận đủ nhưng nhận trễ của NCC-03.”
4. “Các đơn của NCC-03 đang quá hạn mà chưa nhận đủ?”
5. “NCC đó có đơn nào trễ không?” sau khi đã chốt NCC khác trong cùng hội thoại.
6. “Giải thích nguyên nhân trễ của đơn [mã thật vừa trả] và dẫn nguồn tài liệu.”
7. “Chỉ trong tháng 9” — hiện phải báo giới hạn/hỏi rõ, không nhận vơ đã lọc.

Kiểm thử offline: `test_ver2_delivery_orders.py`, `test_delivery_order_details.py`,
`test_order_risk_aggregation.py` và các bộ coordination/frontend hiện có.
Test HTTP thật có thể đọc Supabase qua token thật, không cần gọi provider LLM.

## Đọc nội dung đơn theo mã đầy đủ

`purchase_get_order(identifier="DEMO-PO-00656")` và `purchase_get_request` tìm mã
đầy đủ bằng bộ lọc SQL `po_number` / `request_code` trước phân trang. Không cần
thêm ngày hoặc tăng giới hạn quét danh sách. Bộ lọc là so sánh bằng, không phân
biệt hoa/thường, vẫn kết hợp quyền đọc yêu cầu mua hiện có. Backend cũ bỏ qua bộ
lọc hoặc trả mã khác không được nhận là đã xác minh. Không thấy mã chỉ có nghĩa
không thấy trong phạm vi caller, không chứng minh đơn không tồn tại toàn công ty.

Sau khi xác minh UUID, công cụ đọc chi tiết và bổ sung tên/mã sản phẩm từ danh mục
hiện tại. Đơn vị, số lượng và giá hợp đồng giữ nguyên trên dòng đơn. Thiếu danh
mục thì vẫn trả dữ kiện đơn đã đọc kèm cảnh báo thiếu tên; không tự đặt tên hàng.
Với đơn đặt hàng, công cụ còn đối chiếu chính xác `supplier_id` của đơn trong danh
mục NCC để trả `supplier_code` / `supplier_name`; giữ định danh này cho lượt hỏi
tiếp theo. Không lấy NCC đầu danh sách hoặc NCC được nhớ từ đơn khác trong chat.
Từ khóa/mã chưa đầy đủ vẫn dùng quét có giới hạn và báo rõ khi chưa quét đủ.
Không suy ra ngày nhận thực tế từ trạng thái đơn hoặc lịch hẹn giao. Ngày cam kết
không phải ngày nhận đủ; tên NCC trên đơn chưa chứng minh nguyên nhân giao trễ.

Tra đơn còn tự gọi `supplier-delivery-orders` với `purchase_order_id` đã xác minh,
`delivery_filter=all`, trang 1/1 dòng. Backend lọc UUID **trước** phân trang và kết
hợp supplier + quyền đọc cũ, đọc phiếu nhận trong snapshot chỉ đọc. Không quét các
trang lịch sử NCC để tìm một đơn. Đáp ứng cũ bỏ qua bộ lọc/khác mã/lịch giao thay
đổi giữa hai lần đọc không được nhận là bằng chứng của đơn đang hỏi.

`delivery_evidence` giữ mốc chốt, scope và `orders.items[].schedules[]` gồm lượng
nhận, ngày nhận đủ, ngày trễ và ID phiếu nhận. Ngày nhận đủ là ngày sớm nhất tổng
lượng nhận đạt lượng theo lịch; xét cả ngày giao thực tế và thời điểm ghi nhận
trước cutoff. Ngày cam kết không tham gia phép tính. Không có phiếu nhận thì giữ
completion_date=null; dịch vụ lỗi thì cảnh báo lượt tra chưa lấy được bằng chứng,
không khẳng định database không có phiếu. Không đưa tỷ lệ 100%/0% của một đơn vào
tool lookup để tránh diễn giải nhầm thành tỷ lệ của toàn bộ NCC. Bằng chứng này
xác nhận tình trạng giao; nguyên nhân cần tài liệu nguồn riêng.

## Tìm đơn có nhiều mặt hàng và câu hỏi số lượng chưa rõ

Data task `find_multi_item_orders` nhận `min_distinct_products` (mặc định 2),
`page`, `page_size` (mặc định 1, tối đa 10). API danh sách đơn áp dụng
`COUNT(DISTINCT product_id)` theo từng đơn trong SQL, kết hợp quyền đọc rồi mới
phân trang/count. Nhiều dòng cùng một sản phẩm không thành nhiều loại hàng.
Response echo `min_distinct_products`; adapter không nhận backend cũ bỏ qua bộ
lọc là kết quả hợp lệ. Không cần model con, không quét 500 đơn hoặc yêu cầu người
dùng chọn từng mã. Số khớp bằng 0 trong truy vấn đã lọc khác với quét bị cắt. Chưa
có đơn thỏa thì nói rõ, không tự tạo thêm đơn mẫu. Tên sản phẩm lấy theo UUID;
giữ nguyên lượng/đơn vị/giá từng dòng, không cộng khác đơn vị trong JSON.

Câu “mặt hàng nào có số lượng nhiều nhất” chưa xác định đại lượng thì Agent hỏi
người dùng muốn tồn kho, tổng lượng đặt mua hay phần chưa nhận. Phân trang danh
mục không phải lý do thiếu tồn kho. `inventory_snapshots` là tồn tại mốc snapshot,
không tự suy thành tồn kho hiện tại hoặc lấy lượng nhận thay thế tồn. Sau khi chốt
đại lượng, cần công cụ truy xuất đúng nguồn/phạm vi/kỳ và đơn vị; không tự chọn
nghĩa hoặc xếp hạng tổng số ram/cái/cuộn như một đại lượng chung.

## Tìm nhóm mua từ nhiều nhà cung cấp

“Tìm đơn hàng nhiều nhà cung cấp” dùng `find_multi_supplier_requests`:
`known={min_distinct_suppliers:2,page:1,page_size:1}`; có thể thêm `request_code`
đầy đủ. Mỗi PO một NCC, nên công cụ tìm **yêu cầu mua chung** có các PO của ít
nhất hai NCC khác nhau. `find_multi_supplier_orders` và `multi_supplier_orders`
là alias được chuẩn hóa ở adapter trung tâm; không thay đổi cấu trúc PO.

API danh sách PR lọc `COUNT(DISTINCT supplier_id)` của các PO chưa hủy/từ chối
theo cùng request_id, kết hợp phạm vi quyền đọc trước phân trang. Mỗi nhóm trả
mã yêu cầu, số NCC và các mã đơn con. Không gom các yêu cầu khác nhau, không tính
hai đơn của cùng NCC là hai NCC. Giới hạn trang nhóm 10, con 100; vượt giới hạn
hoặc dữ liệu đổi giữa các lần đọc trả lỗi rõ, không coi đó là không có nhóm.

Adapter xác minh echo bộ lọc, parent ID từng PO, số NCC thật và nhãn danh mục;
frontend có bảng cùng request_code, po_number và tên NCC. Không cần model con.
Danh sách task được công bố trên mô tả tool lúc chạy; lỗi tên task không được
mô tả là thiếu API nghiệp vụ hoặc thiếu dữ liệu trong database.

## Tiến độ giao của cả yêu cầu mua

`task=request`, `known.identifier` là mã yêu cầu chung. Runtime gọi công cụ PR
một lần, không dùng model con để chọn lại PR/PO. Kết quả `request_delivery_evidence`
tìm các PO theo `request_id` chính xác tại backend, giữ quyền đọc trước count/page
và xác minh echo bộ lọc cùng parent của từng đơn. Tối đa 10 đơn được đối chiếu với
phiếu nhận; nếu còn trang, thiếu quyền/dịch vụ hoặc đơn thay đổi giữa các lần đọc
thì trả partial, **không tính tỷ lệ cả nhóm từ phần đã đọc**. Các đơn bị hủy/từ
chối được loại riêng. Mỗi PO là một snapshot được cấp quyền; `observed_from/to`
ghi khoảng đọc, không tuyên bố toàn nhóm được đọc trong một snapshot nguyên tử.

`summary.late_completed_orders_percent` chỉ tính đơn nhận đủ trễ / tất cả đơn đã
nhận đủ trong đúng yêu cầu, không phải tỷ lệ lịch sử của NCC hay xác suất ML.
Chưa có đơn nhận đủ thì giữ null; số đơn chưa nhận đủ quá hạn đếm riêng. Không
gộp số lượng khác đơn vị để tính tỷ lệ. Mã PO gửi vào công cụ PR (hoặc ngược lại)
bị chặn bằng `PURCHASE_RESOURCE_MISMATCH`, không báo nhầm thành không tìm thấy
hoặc thiếu quyền. Chưa có phiếu nhận chỉ xác nhận tình trạng ghi nhận tại mốc đọc.
