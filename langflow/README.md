# Langflow cho FlowBridge

API chat sau đăng nhập Keycloak dùng profile riêng
[backend ↔ Langflow SSO](../docs/LANGFLOW-SSO.md) và `start-sso.cmd`.
Không dùng `start-test-backend.cmd` cho đường này. Profile mới giữ flow hiện có,
truyền caller theo request và kiểm quyền tại API chuẩn.

**FlowBridge - ver3** clone đầy đủ từ ver2 rồi thay phần điều phối bằng cổng chat,
state/controller và service bridge không LLM; giữ API tool/preset/kết nối ver2.
Xem [sơ đồ, ngân sách, kiểm thử và khôi phục ver3](VER3.md).

Thử riêng **ver2** khi chưa cấu hình SSO: mở Langflow bằng `start.cmd`, sau đó nhấp
đúp [start-test-backend.cmd](start-test-backend.cmd). Backend test chạy riêng tại
`http://127.0.0.1:8001`; các node Dữ liệu/ML của ver2 dùng credential local tự tạo.
F5 tab ver2 trước khi chat. Xem [cách chạy/dừng và giới hạn](VER2.md#test-local-khi-chưa-có-sso).
RAG có thể dùng Markdown/chunk đã lập chỉ mục trên Supabase; xem phần chuẩn bị RAG
trong hướng dẫn trên. Không cần nhập hoặc lập chỉ mục lại tài liệu.

Nhấp đúp start.cmd, mở **FlowBridge - Trợ lý mua hàng**.

**Nhập câu hỏi → Trợ lý mua hàng → Trả lời người dùng**.
Mô hình OpenAI cấp model; các node tác vụ đăng ký công cụ qua Dịch vụ dữ liệu,
Dịch vụ dự đoán và Dịch vụ tri thức (RAG). Service chỉ gom tool, không có LLM phụ.

Agent đọc câu hỏi, chọn công cụ và điền tham số có cấu trúc. Các đường nối trên canvas
là cổng Tool/LanguageModel/Message; JSON kết quả đi qua cuộc gọi tool của agent.
Agent lấy dữ liệu từ tool trước để tạo input cho tool sau theo các mapping bên dưới.

| Node | Input cần có | Output dùng tiếp |
|---|---|---|
| Tìm sản phẩm | query; limit=5 | data.items[].product_id, unit |
| Tìm nhà cung cấp | query; limit=5 | data.items[].supplier_id |
| Tra báo giá | product_id; supplier_id tùy chọn | data.items[].current_unit_price, currency, MOQ, lead time |
| Xem yêu cầu mua | identifier đúng mã/UUID PR | data.items của dòng được chọn |
| Xem đơn đặt hàng | identifier đúng mã/UUID PO | data.purchase_order_id → order_id của node rủi ro |
| Xếp hạng nhà cung cấp | product_id, quantity, ngày giao | data.candidates gồm supplier_id, giá và result |
| Chấm rủi ro nhà cung cấp | product_id, supplier_id, quantity, ngày giao | data.scenario, result, trace |
| Xem rủi ro đơn hàng | order_id | data.items, điểm MAX, is_complete |
| Đọc kết quả đã lưu | prediction_id | bản ghi lịch sử và provenance |
| Tìm bằng chứng tài liệu | query; supplier_id tùy chọn; top_k=3 | data.chunks[].text + reference |

Chỉ lấy ID từ tra cứu khi success, đúng một kết quả và truncated=false. Nhiều kết quả
phải hỏi chọn. Không truyền tên/mã vào field UUID, nhầm request_id với purchase_order_id,
hoặc truyền cả JSON output vào một field ID.

Mỗi node có **Mô tả sử dụng**, tham số riêng, **Đầu vào JSON mặc định**, **Khóa giá trị
đã cấu hình**, nút **Xem JSON và cách nối** và các output sau:

- **Công cụ cho agent**: nối vào Service trong flow hiện tại.
- **Kết quả JSON**: gọi trực tiếp API bằng input node, không dùng model.
- **Hợp đồng JSON**: xem schema input/output, endpoint, mặc định, input khóa và handoff;
  chỉ dựng metadata, không gọi API.

Nút xem JSON trình bày mẫu tham khảo. UUID minh họa không được tự điền vào dữ liệu
chạy. Input JSON thực thi mặc định là {}; các ô typed đặt limit=5, top_k=3,
ranking_policy=risk_first. UUID/số lượng/ngày chưa biết để trống. Currency/giá trần
ban đầu null, không âm thầm đặt VND hoặc giá trần.

Thứ tự hợp nhất tham số:

1. Input đã khóa tại node giữ cố định và được bỏ khỏi schema agent phải điền.
2. Khi chưa khóa, giá trị người dùng nêu rõ ghi đè mặc định node.
3. Tham số null/không ghi đè dùng giá trị mặc định của node, kể cả preset bắt buộc
   do người vận hành đã điền.
4. Trường bắt buộc còn thiếu phải hỏi/tra cứu hợp lệ. DTO canonical kiểm lại trước HTTP.

Input JSON trên node ghi đè ô typed trùng tên. Nếu đã điền top_k trong Input JSON,
hãy sửa/xóa mục đó khi muốn điều chỉnh bằng ô top-k. Khóa bật sẽ cố định mọi input
có giá trị, kể cả mặc định top-k; null không tạo ra một ID hay điều kiện khóa giả.

Nếu người vận hành đặt bộ lọc mặc định như giá trần/currency/supplier_id nhưng chưa
khóa, schema tự thêm clear_defaults. Agent chỉ dùng danh sách này khi người dùng nói
rõ bỏ bộ lọc mặc định; trường khóa không thể bị xóa. Control này được adapter xử lý,
không chuyển sang API backend.

Ví dụ câu hỏi → tham số (giả sử đã biết UUID sản phẩm):

| Câu hỏi | Tool và tham số |
|---|---|
| “Giá hiện nay bao nhiêu?” | Tra báo giá; không cần quantity/ngày giao; không gọi ML |
| “100 cái, giao 01/12/2026, top 5, tối đa 18 triệu đồng/cái” | Ranking: quantity="100", date="2026-12-01", top_k=5, max_unit_price="18000000", currency="VND" |
| “100 cái, giao 01/12/2026, chọn NCC ít rủi ro, giá hợp lý” | Ranking: top_k dùng mặc định 3; giá trần/currency null nếu node không đặt |
| “100 cái, tổng ngân sách 180 triệu gồm thuế/phí” | Làm rõ trần đơn giá, không nhét tổng tiền vào max_unit_price |
| “NCC này rủi ro thế nào?” | Scenario với supplier_id cụ thể; không nhận top_k/giá |
| “PO-001 có rủi ro không?” | Xem PO lấy purchase_order_id → đọc rủi ro PO |
| “Có nguồn nội bộ về ứng viên đầu tiên không?” | supplier_id của đúng ứng viên → RAG; không chấm lại |

Trong bảng, date viết tắt cho requested_delivery_date; khóa JSON thực tế luôn theo schema.
Ranking chỉ hỗ trợ risk_first: lọc điều kiện rồi xếp rủi ro tăng dần. Không tự tối ưu
giá rẻ nhất, thêm bộ lọc địa phương hoặc danh sách supplier khi API không hỗ trợ.

Tất cả node trả cùng khung JSON:

~~~json
{
  "tool": "prediction_rank_suppliers",
  "status": "success",
  "input": {
    "product_id": "00000001-0001-4001-8001-000000000001",
    "quantity": "100",
    "requested_delivery_date": "2026-12-01",
    "top_k": 3,
    "ranking_policy": "risk_first",
    "currency": "VND",
    "max_unit_price": "18000000"
  },
  "data": {},
  "error": null
}
~~~

Đây là minh họa khung; data={} không phải kết quả đã chạy. data thực tế tuân schema
theo từng tool trong flows/node-contracts.json. input phản ánh giá trị cuối đã dùng.
Lỗi có data=null, error gồm code/http_status/retryable và fields khi xác định được
field sai; không trả raw credential hoặc thông báo upstream nhạy cảm.

status của khung là trạng thái tra cứu/thực thi; trạng thái nghiệp vụ PR/PO ở
data.status. Kết quả dự đoán lịch sử có thể failed/snapshot_only dù đọc bản ghi thành
công; agent phải đọc trạng thái bên trong. partial/insufficient_data/null/unscored
không phải điểm 0. Điểm ML chưa hiệu chuẩn thành xác suất.

Dữ liệu ranking đã kèm giá, lead time và điểm; dùng lại, không gọi báo giá/chấm lại
từng NCC. RAG dùng text/reference cùng đoạn, không ghép nguồn giữa các NCC.
Kết quả thiếu/nhập nhằng/lỗi quyền phải dừng làm rõ; không tự nới điều kiện.

Trong Controls/Edit có địa chỉ API, access token, timeout, số trang quét và các trường
đầu ra. Trống output fields giữ toàn bộ data. Nếu chọn bớt field có thể thiếu dữ liệu
cho bước sau; mặc định flow giữ toàn bộ. Credential trống dùng data/agent.env hoặc env.
OpenAI API key và bearer backend khác nhau; không đặt chúng vào câu hỏi/Input JSON.
Cấu hình local dành cho một người; ứng dụng nhiều người phải tiêm caller theo request.

Mô hình OpenAI cho chọn/nhập model ID, key, token limit, reasoning; Controls có
temperature, timeout và SDK retries. Mặc định gpt-5.6-luna, low, 600 tokens, retries=0.
Prompt và các giới hạn chỉnh được tại Trợ lý mua hàng; mặc định 4 model/3 tool calls,
tuần tự, không tool retry. [OpenAI Docs](https://developers.openai.com/api/docs/guides/function-calling)
là nguồn tham khảo về description, schema strict và tham số nullable; schema đúng
không chứng minh model luôn hiểu đúng ý định.

RAG chỉ đăng ký khi có AI token và JobContext UUID analysis task thật. Không bịa
context hoặc dùng user token thay AI token. Top-k ML 1–10, RAG 1–20. Không gắn index
tài liệu, chấm/lưu PO hoặc các API ghi vào agent đọc dữ liệu này.

Adapter dùng app/AI 0.4.1 và DTO ML runtime. Catalog chưa có keyword search: quét tối
đa số trang cấu hình ×100 dòng rồi báo truncated; không có snapshot đồng nhất xuyên
các trang. Một tool có thể cần nhiều backend HTTP request; không phải nhiều lượt model.

Dựng lại/import metadata từ root repo:

~~~powershell
.\langflow\.venv\Scripts\python.exe langflow/setup_agent.py --install
~~~

Lệnh sinh procurement-agent.json và node-contracts.json, kiểm schema/ví dụ cấu hình,
rồi cập nhật flow cùng endpoint. Nó không chạy model/API nghiệp vụ. Bản trước lưu
trong data/procurement-before-vietnamese-routing.json. Lệnh ghi đè mặc định source;
hãy lưu/export flow đang tự chỉnh trước khi dựng lại.

20 tình huống và tham số mong đợi ở scenarios.json, chưa chạy. evaluate.py mặc định
chỉ in case; paid mode cần --approve-one-openai-request, chỉ gọi một model request,
không tự chạy tool hoặc gọi tiếp. Codex phải hỏi trước từng request khi test trong
chat. Agent trong Playground vẫn tự động theo yêu cầu người dùng.

Hiện chỉ kiểm cấu hình/schema/giao diện; chưa nghiệm thu câu hỏi/LLM hoặc tích hợp
backend thật. Không coi JSON mẫu/schema/flow tồn tại là bằng chứng chọn đúng tool.

## Bản nhiều agent riêng

`FlowBridge - ver2` nằm trong project riêng, gồm Agent trung tâm và ba agent con
Dữ liệu/ML/RAG, nối bằng A2A Internal có sẵn. Dùng model selector/Agent/Prompt/Date/
Structured Response/Parser/Operations chuẩn. Bốn Agent mở rộng cấu hình endpoint/
reasoning OpenAI (Chat Completions + none mặc định), giữ engine upstream; API nghiệp
vụ dùng custom adapter. Ba Agent con dùng thêm adapter Structured Response cho JSON
object động ở nhánh native không có tool; vẫn kiểm Pydantic tại local, giữ nguyên
schema/JSON và không thêm model call. Bộ ver2 cũ sửa riêng bằng
`langflow/repair_ver2_structured.py` với Python trong venv Langflow, rồi F5.
Xem [hướng dẫn và hợp đồng ver2](VER2.md). Không thay workflow trợ lý mua hàng v1.

Code/config nhóm tạo: GPL-3.0-or-later, xem LICENSE. Langflow/lfx MIT theo requirements.
Không đóng gói .venv, data, model weights hoặc cache.
