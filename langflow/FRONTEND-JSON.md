# JSON cho frontend — ver2

<!-- SPDX-License-Identifier: GPL-3.0-or-later; FlowBridge, see LICENSE. -->

Component mới **JSON cho frontend · ver2** nhận cổng `response` của Agent trung tâm,
xuất `Data` ở cổng `response`. Không dùng cổng Structured Response của Agent để dựng
số liệu, không thêm model, không chạy lại công cụ cũ và không sửa node/prompt/ML cũ.
Khi RAG đã xác minh nhà cung cấp, node có thể đọc thêm thống kê giao hàng qua API
đã xác thực; tùy chọn **Thống kê giao hàng từ database** mặc định bật trên node mới.

Flow `flows/ver2/central.json` đã được bổ sung nhánh sau theo xác nhận:

```text
Agent trung tâm.response ─┬→ Chat Output hiện có
                         └→ FrontendResponse mới → JSON/Data cho frontend
```

Chỉ thêm một node và một dây. Đường chat và toàn bộ node/cạnh cũ giữ nguyên.
Thay đổi nằm trong export ở repo. Dùng helper bên dưới để nạp vào phiên local.
Không cần thay đổi Supabase/migration.

## Dữ liệu đến từ đâu?

Langflow/lfx 1.12.6 giữ sự kiện `ToolContent.output` trong Message của Agent.
Runtime điền trường này ở `on_tool_end`; adapter đọc Reply có tương quan run từ ba
tool `hoi_du_lieu`, `hoi_du_doan`, `hoi_tri_thuc`, rồi lấy ToolResult trong `results`.
Các sự kiện có thể nằm trong `ContentBlock(type=group)` lồng nhau; adapter duyệt
nhóm native với giới hạn độ sâu/số block và không phân tích JSON trong đoạn văn.
Không bóc giá, điểm hoặc tỷ lệ từ `Message.text`, TextContent, output_schema hoặc
JSON do model viết trong câu trả lời. Không xuất raw trace/tool_input/prompt.

Đây là biên nội bộ của graph đã được cấp quyền, không phải endpoint nhận Message
tùy ý từ frontend. Backend gọi Langflow phải tự xác thực/phân quyền, ràng buộc
user/session và chọn flow/node cố định. `sender=Machine` hay run_id không phải bằng
chứng xác thực cho dữ liệu do người ngoài tự gửi. Node không tạo thêm quyền DB/RAG.

Message dùng riêng cho chat không có tool event sẽ có datasets rỗng và cảnh báo.
Sự kiện lỗi/sai schema/khác run không được dùng làm số liệu. Trường `answer` vẫn là
lời giải thích từ Agent, không được validator chứng nhận là đúng về mặt nội dung;
frontend vẽ chỉ từ `datasets` đã kiểm.

## Hợp đồng v1

Schema: `schemas/frontend-response.v1.json`. Nguồn Python:
`runtime/flowbridge_agent/frontend_response_contract.py`.

| Trường | Ý nghĩa |
|---|---|
| schema_version | Luôn `1.0` |
| status | completed / partial / needs_input / failed |
| run_id | ID lượt từ runtime A2A nếu có, không phải ID hội thoại |
| answer | Văn bản chat, không dùng để tính toán |
| datasets | Cột có kiểu/đơn vị, rows, context và source_ids |
| tables | dataset_id và fields theo thứ tự; nhãn/kiểu lấy từ columns |
| charts | dataset_id, loại, hướng, x_field, series, đơn vị trục, stacking |
| sources | Nguồn tool hoặc document/version/chunk/source_uri/checksum RAG |
| warnings | code/message và dataset_id khi có |
| questions | Câu hỏi bổ sung từ Reply đã nhận |
| error | code/message/retryable, null nếu không có lỗi xử lý |

Các trường chính luôn có kể cả list rỗng. `null` không chuyển thành 0. Tiền/số lượng
dùng chuỗi decimal chính xác; phần hiển thị/vẽ đổi sang Number có kiểm tra hữu hạn
và giới hạn độ chính xác. Không dùng giá trị Number phục vụ tính tiền/thanh toán.
Ngày có dạng ISO. Dataset chỉ chứa trường được allowlist, không xuất dict lồng tùy ý.

Mỗi table/chart chỉ tham chiếu dataset, không lặp bản sao rows. `series.field`
phải là cột số tồn tại; không vẽ series có null, nhãn trùng/thiếu hoặc đơn vị lệch.
JSON Schema kiểm cấu trúc; backend còn phải gọi `validate_response()` để kiểm quan
hệ dataset/cột/nguồn và các quy tắc biểu đồ mà JSON Schema đơn thuần không biểu đạt.

`context.complete` chỉ nói phần kết quả công cụ đã nhận trong phạm vi trả về, không
chứng minh toàn bộ giao dịch trong DB. `has_more/truncated` và giới hạn 200 dòng đều
được cảnh báo. Context không biết kỳ/cutoff thì để null, không đoán từ thời gian hiện tại.
Tối đa 20 receipts, 200 dòng/dataset và 1 MiB response; không âm thầm biến kết quả
vượt giới hạn thành một phản hồi đầy đủ.

## Bảng và biểu đồ hiện có

- Danh mục, báo giá, dòng phiếu yêu cầu mua/đơn đặt hàng: bảng cột được hỗ trợ.
- Giá: cột so sánh khi xác minh cùng sản phẩm, đơn vị và tiền tệ trong dữ liệu tool.
- ML: bảng candidates và unscored, giữ thứ hạng API. Chỉ vẽ điểm khi các dòng đủ
  context để so sánh và score_status=success. Điểm chưa hiệu chuẩn, không phải % trễ.
- RAG: bảng đoạn trích và nguồn chính xác. Không tính thống kê giao hàng từ đoạn trích.
- Giao hàng: số đếm thật từ đơn đặt hàng/lịch giao/phiếu nhận; bảng trạng thái,
  biểu đồ cột ngang và bảng mẫu số/tỷ lệ trễ riêng.

Contract có `bar`, `line`, `pie`; bar hỗ trợ orientation vertical/horizontal và
stacking none/normal/percent. Builder hiện tự dựng cột cho giá/điểm có dữ liệu
phù hợp và thống kê giao hàng từ database; chưa tự tạo biểu đồ đường/tròn/cột chồng.

Cột chồng % yêu cầu metric_definition, count_unit, denominator_definition, kỳ và
cutoff; tổng các series mỗi nhóm phải bằng 100% (sai số làm tròn tối đa 0.01).
API `POST /internal/v1/tools/supplier-delivery-statistics` nhận `supplier_id` trong body,
được bật bằng `FLOWBRIDGE_DELIVERY_STATISTICS_ENABLED=true` trên backend (mặc định tắt).
Không cần migration; khi tắt vẫn giữ nguyên tập API/schema baseline của nhóm. API dùng bearer
AI_SERVICE thật và `X-Job-Context`. Backend kiểm grant global đang hoạt động, task,
revision yêu cầu và phòng ban; cutoff lấy từ `feature_asof` của task trong database.
Đây là thống kê lịch sử nhà cung cấp trong hệ thống theo grant AI_SERVICE global,
không phải chỉ các đơn do tài khoản employee local tạo. Caller không được chọn
SQL, role, phòng ban hoặc cutoff tùy ý. Không cần bật ML và không ghi dữ liệu.

Một transaction PostgreSQL repeatable/read-only đọc các bảng. Chỉ lấy đơn đã
phát hành và tồn tại trước cutoff; lịch giao phải đã được tạo, biên nhận phải đã
được ghi nhận và có ngày giao thực tế không sau cutoff (UTC+07:00). Mỗi lịch giao
phải đạt đủ số lượng riêng; một đơn đúng hạn khi mọi lịch giao hoàn tất theo hạn
gốc. Ngày cam kết mới không thay hạn gốc. Đơn đã nhận đủ trễ / tổng đơn đã nhận đủ
là tỷ lệ trễ; mẫu số 0 trả null. Đơn mở quá hạn và phần còn lại chưa quá hạn là hai
nhóm riêng; nhận một phần là tập con của đơn mở, không cộng như nhóm độc lập.

Đơn thiếu/sai tổng lịch giao thuộc nhóm chưa phân loại, không đoán là đúng hạn.
Đơn hiện có trạng thái hủy/từ chối bị loại. Schema chưa lưu lịch sử thời điểm hủy,
nên response luôn cảnh báo `CANCELLATION_STATUS_IS_CURRENT_NOT_HISTORIZED`; không
coi đây là tái dựng đầy đủ trạng thái hủy tại cutoff quá khứ. Giới hạn 5.000 đơn
mỗi nhà cung cấp và 20.000 dòng mỗi bảng con; vượt giới hạn trả lỗi, không âm thầm
lấy một phần làm tổng số. Node đọc tối đa ba nhà cung cấp từ receipts đã kiểm.

Số liệu chart nằm trong dataset riêng cùng as_of, kỳ phát hành, đơn vị và định nghĩa
mẫu số. Phần `answer` tách rõ số database với nhận xét từ tài liệu; hai nguồn có
thể dùng mốc chốt khác nhau. Lỗi API thống kê chỉ tạo cảnh báo/partial, vẫn giữ
bằng chứng RAG đã lấy được và không bóc số từ câu trả lời để thay thế.

Truy vấn RAG chờ khóa xử lý tối đa 15 giây, tối đa bốn truy vấn đang chạy/chờ
mỗi process. Điều này tránh trả 429 ngay khi worker chỉ đang kiểm tra hàng đợi.
Khi hết thời gian chờ hoặc đầy hàng đợi, API vẫn trả 429 kèm Retry-After;
không bỏ qua phân quyền hoặc dùng tài liệu của lượt trước để thay thế.

## Chuẩn bị graph và chạy kiểm thử

Để mở bản ver2 đã cài trên máy này, chạy `langflow/start.cmd` rồi mở project
**FlowBridge - ver2**. Launcher dùng `langflow/.venv` nếu có; nếu thiếu, dùng lại
`.cache/rag/langflow-integration/venv` và database Langflow ở thư mục cache cùng cấp.
Không thay database Langflow bằng Supabase và không xóa flow RAG cũ.

`install_ver2_exports.py` nạp nguyên bốn export trong repo vào project riêng với
ID khớp A2A. Helper từ chối ghi đè graph khác và không sinh lại node/prompt/preset.
Nó đăng ký key LLM trong kho Credential local từ `data/agent.env`, không chạy model.
Key gọi HTTP run được tạo riêng cho chủ sở hữu flow, lưu tại
`data/langflow-client.json` được Git bỏ qua, có hiệu lực một ngày. Khi key hết hạn,
chạy helper `install_ver2_exports.py --refresh-key` bằng Python môi trường Langflow;
lệnh này chỉ làm mới key, không cài lại hay ghi đè graph đã chỉnh trên canvas.

Ứng dụng dùng backend SSO truyền caller và token AI theo từng request; xem
[hợp đồng chat](../docs/LANGFLOW-SSO.md). Không cần worker đăng nhập tài khoản
thử nghiệm hoặc bearer tĩnh trong cấu hình flow.

Trong canvas, chạy nhánh **JSON cho frontend · ver2** để xem Data. Playground chat
mặc định hiển thị Chat Output. Khi dùng HTTP run API của bản cài trên máy, đặt
`output_component="FrontendResponse-ver2-central"` để chọn đầu ra JSON; mặc định
`output_type="chat"` sẽ chỉ lấy Chat Output.

Kiểm tra hội thoại thật qua [stack SSO](../HUONG-DAN-CHAY.md#kiểm-thử-hệ-thống).
Đầu ra graph JSON phục vụ adapter backend; browser gọi API ứng dụng có xác thực.

Dùng Python trong môi trường Langflow của nhóm (có lfx theo lockfile), không đổi
môi trường ML. Launcher hiện có đã thêm `langflow/runtime` vào PYTHONPATH.

```powershell
# Chỉ dành cho export ver2 cũ CHƯA có nhánh JSON; xem kế hoạch, không ghi file:
.\langflow\.venv\Scripts\python.exe langflow/add_frontend_response.py `
  --source .cache/central-before-json.json
# Tạo export riêng (từ chối ghi đè file có sẵn hoặc thêm trùng node):
.\langflow\.venv\Scripts\python.exe langflow/add_frontend_response.py `
  --source .cache/central-before-json.json `
  --output .cache/frontend-json/central-with-json.json
```

Helper kiểm từng node cũ và thứ tự/cấu trúc cạnh cũ không đổi. Không có cờ tự áp
dụng flow live. Export ver2 trong repo đã có nhánh, không chạy helper thêm lần nữa.
Với flow live có thay đổi riêng, thêm node trong canvas và nối duy nhất cạnh như
sơ đồ; có thể đối chiếu export trong repo. Không import ghi đè cả flow có preset mới
của thành viên khác. Nếu node mới chưa hiện trong menu, cần nạp lại component theo
launcher hiện có; không Update All các node cũ.

```powershell
$env:PYTHONPATH = "$PWD/langflow/runtime;$PWD/backend"
.\langflow\.venv\Scripts\python.exe -m pytest langflow/tests/test_frontend_response.py `
  -q -c langflow/rag/pytest.ini
```

Tests dùng receipts có kiểm soát, native ToolContent event handler, serial hóa
Data/ResultData và một graph nhánh thật với Agent giả lập đếm lượt gọi. Không gọi
provider hoặc Supabase; không thay bằng nghiệm thu hội thoại model/SSO thật.

## Lấy JSON khi gọi từ backend

Với API thực thi theo graph của lfx, chọn output node theo ID
`FrontendResponse-ver2-central`. Nếu tự dùng Python Graph:

```python
runs = await graph.arun(inputs=[inputs], outputs=["FrontendResponse-ver2-central"])
wire = {"outputs": [run.model_dump(mode="json") for run in runs]}
payload = extract_frontend_response(wire)
```

Import helper từ `flowbridge_agent.frontend_response_api`. Adapter đọc typed object
trong ResultData.results hoặc ResultData.outputs theo serial hóa native, từ chối
JSON string trong Message.text, output thiếu/trùng và payload sai schema.

Nếu gọi HTTP Langflow, lớp gọi phải chọn output node theo API thực sự của bản
Langflow đang deploy. Chưa thêm Agent Hub/HTTP endpoint mới ở bước này. HTTP envelope
có thể vẫn gồm outputs/metadata của Langflow; helper lấy object hợp đồng để backend
trả cho frontend. Không đưa Langflow API key hoặc bearer DB trực tiếp ra trình duyệt.
