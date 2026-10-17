# FlowBridge - ver2

Bản riêng trong project Langflow **FlowBridge - ver2**, gồm bốn flow:

| Flow | Vai trò | Công cụ |
| --- | --- | --- |
| FlowBridge - ver2 | Agent trung tâm, nhận câu hỏi và tổng hợp | 3 A2A Internal |
| FlowBridge - ver2 - Dữ liệu | Agent tra dữ liệu nghiệp vụ | Sản phẩm, NCC, báo giá, PR, PO |
| FlowBridge - ver2 - Dự đoán ML | Agent chọn đúng phép dự đoán | Rank top-k, scenario một NCC, rủi ro PO, prediction đã lưu |
| FlowBridge - ver2 - Tri thức RAG | Agent tìm bằng chứng | Retrieve knowledge có ACL/JobContext |

Mở `langflow/start.cmd` bằng nhấp đúp trên Windows, chọn project rồi mở flow chính.
Launcher bật tính năng A2A bằng `LANGFLOW_A2A_ENABLED=true`. Nếu Langflow đã chạy từ
trước khi thêm cờ này, đóng server cũ rồi mở lại launcher một lần.

## Thiết kế bằng node chuẩn

Flow chính:

```text
Chat Input ─────────────┐
Current Date (VN) ──────┴─> Prompt Template ─> Agent trung tâm ─> Chat Output
                                               ↑ Tools
                              A2A Dữ liệu / A2A ML / A2A RAG
```

Mỗi flow con:

```text
Chat Input + Current Date ─> Prompt Template ─> Agent chuyên trách
                                                   ↑ API tools
Agent (Structured Response) ─> Parser Stringify ─> Data Operations ─> Chat Output
```

Dùng 25 node chuẩn, 4 Agent Langflow có lớp cấu hình tương thích OpenAI và 10 bản sao
node API nghiệp vụ (39 node/35 edge trong bốn flow). Các node chuẩn là Chat Input,
Chat Output, A2A Agent, Current Date,
Prompt Template, Parser và Data Operations. Không tự viết orchestration hay A2A
client/server. Không thêm node chỉ để trang trí hoặc thêm lượt model định dạng JSON.

Custom API nghiệp vụ có DTO canonical, phân quyền human/AI, endpoint
cố định, pagination có giới hạn, decimal và JSON lỗi thống nhất. HTTP Request node
thuần không thay thế đầy đủ các quy tắc đó. Ba custom service aggregator và custom
Agent/model ở v1 không được dùng trong ver2. Bốn Agent vẫn dùng orchestration, memory,
tool loop và Structured Response upstream; lớp ver2 chỉ truyền tùy chọn vào model
factory. Không sửa source/runtime/graph của v1 hoặc dependency Langflow.

Agent trung tâm thấy ba action `hoi_du_lieu`, `hoi_du_doan`, `hoi_tri_thuc`. A2A để
**Internal**: chọn đúng flow con trong cùng project bằng ID; không cần HTTP hoặc
Langflow API key giữa các agent. Ba flow con bật Serve as an A2A agent; flow trung
tâm không bật để tránh tự gọi. Session con được A2A native tách namespace; lịch sử
agent con mặc định 0 để tránh lấy nhầm dữ liệu nhiệm vụ trước.

## Chỉnh trên giao diện

- Mỗi Agent: **Chọn mô hình**, **API key / biến bí mật**, **System prompt**, giới hạn
  lượt model, output token, số tin nhắn lịch sử. Mặc định `gpt-5.6-luna`/OpenAI.
  Dropdown native có thể mở phần cấu hình provider để chọn/thêm model khác.
- Bốn Agent có **Endpoint OpenAI** và **Mức suy luận OpenAI**. Mặc định Chat
  Completions + none để Luna dùng function tools. Để reasoning low trở lên, chọn
  Responses; cấu hình Luna + Chat Completions + reasoning khác none bị chặn trước
  request. `auto`/không truyền effort có thể dùng mặc định medium từ provider,
  nên không dùng để né lỗi này. SDK retries mặc định0, timeout45s, chỉnh ở advanced.
- API key tham chiếu biến Credential riêng `FLOWBRIDGE_VER2_OPENAI_API_KEY` trong
  Settings → Global Variables. Installer lấy key đã cấu hình local nếu biến chưa
  tồn tại; không ghi key vào JSON/source hoặc thay credential của v1. Thay key của
  ver2 bằng sửa biến riêng, hoặc điền trực tiếp trên từng Agent.
- Launcher nạp `data/agent.env` bằng tùy chọn `--env-file` chuẩn. Langflow nhận diện
  provider OpenAI qua biến `OPENAI_API_KEY` trong môi trường và nhập Credential local;
  giữ credential người dùng đã tự chỉnh theo cơ chế upstream. Bước này không gọi
  endpoint xác minh key/model. Biến key riêng trên bốn Agent vẫn là cấu hình ver2.
- Node API giữ các ô tham số, description, Input JSON `{}`, khóa mặc định, output
  projection và nút xem contract như v1. Các node trong ver2 là bản sao cấu hình;
  chỉnh chúng không đổi node v1. API origin/token/timeout nằm ở cấu hình node/env,
  không phải argument do LLM tự truyền.
- A2A: chọn agent, Tool Mode, mở Actions để chỉnh slug/description. Để Message trống
  cho Agent điền `input_value`. Không chuyển External nếu vẫn dùng flow cùng project.
- Schema output và format instructions nằm ở từng Agent con; Parser chuyển Data
  thành chuỗi, Operations chỉ bỏ khung Markdown ngoài để gửi JSON nguyên vẹn.
  Output đang chọn của Agent con phải là **Structured Response**; chuyển sang
  Response thường sẽ làm Langflow bỏ đường nối vào Parser vì khác kiểu dữ liệu.

## Hợp đồng giữa agent

A2A native nhận một argument `input_value: string`, bên trong là JSON:

```json
{
  "task": "rank_suppliers",
  "question": "Chọn 3 NCC rủi ro thấp, tối đa 18 triệu VND mỗi cái",
  "business_date": "2026-10-18",
  "known": {
    "product_id": null,
    "quantity": null,
    "requested_delivery_date": null,
    "top_k": 3,
    "currency": "VND",
    "max_unit_price": "18000000"
  },
  "evidence": []
}
```

Đây là mẫu giải thích, không phải input đã đủ để chạy ML. Thiếu sản phẩm/số lượng/
ngày nhận thì hỏi người dùng trước. ID đã có thì không tra lại. Chỉ gửi các field
cần thiết cho task; không truyền credential, URL hoặc toàn bộ history.

Output sau Structured Response/Parser/Operations:

```json
{
  "service": "prediction",
  "status": "needs_input",
  "resolved": {},
  "results": [],
  "missing": ["product_id", "quantity", "requested_delivery_date"],
  "questions": ["Bạn cần sản phẩm nào, số lượng và ngày nhận hàng là bao nhiêu?"],
  "warnings": []
}
```

`results` giữ ToolResult thật `tool/status/input/data/error`, kể cả trace/currency/
warning/reference; `resolved` chỉ chứa ID/tham số xác minh được. Schema table native
kiểm kiểu các field ngoài; không chứng minh nguồn dữ liệu/ý định đúng. Validation
error của native Agent (`error`, `validation_error`, thiếu field, sai service) phải
được trung tâm xử lý như lỗi, không tiếp tục ML với ID đoán.

Đường dữ liệu được quy định trong prompt:

- Tên sản phẩm → Dữ liệu → `product_id` + `unit` → ML hoặc báo giá.
- Tên NCC → Dữ liệu → `supplier_id` → scenario hoặc RAG có filter NCC.
- Mã PO → Dữ liệu → `data.purchase_order_id` → ML `order_id`; UUID PO có sẵn gọi thẳng.
- PR nhiều item: hỏi chọn dòng trước; preferred supplier không phải NCC đã chốt.
- Rank → kết quả đã có score của từng ứng viên; không gọi scenario chấm lại.
- Rank → RAG chỉ khi người dùng yêu cầu chứng cứ, với NCC cần kiểm tra cụ thể.

Trần đơn giá được agent trung tâm đọc và chuyển vào `known.max_unit_price` rồi agent
ML truyền vào tool rank. Ví dụ 18 triệu VND/cái → `"18000000"`, `currency="VND"`.
Không tự đặt trần cho “giá tốt”; không biến tổng ngân sách thành đơn giá; thiếu tiền
tệ phải hỏi. Tool vẫn áp resolver: giá trị khóa > câu hỏi rõ ràng > mặc định node,
null dùng mặc định, `clear_defaults` chỉ khi người dùng yêu cầu bỏ filter được phép.

## Giới hạn và chi phí

Agent chính/Dữ liệu tối đa 4 model calls; ML/RAG tối đa 2 mỗi lần được giao việc.
Tắt date tool, calculator và tool retry; ngày giờ được node native thêm trực tiếp.
Prompt giới hạn 3 A2A/lượt và 1 API tool ở ML/RAG, nhưng native Agent không có hard
cap số tool hoặc tắt parallel calls qua các input hiện có. Lớp model config của ver2
đã bổ sung SDK retries0. Đây là hướng
native để thử kiến trúc; không bảo đảm model không bao giờ chọn sai/gọi dư.

Nhiều agent thường tốn thêm model calls so với v1. Một nhánh đơn giản thường cần
trung tâm chọn service, agent con chọn tool/đọc kết quả, rồi trung tâm tổng hợp;
đó không phải giá hoặc số request được bảo đảm. Chỉ chạy khi muốn thử. Codex chưa
chạy câu hỏi nào và vẫn phải xin phép từng OpenAI request khi test trong chat.

Để kết nối nghiệp vụ thật vẫn cần backend hoạt động và bearer người dùng hợp lệ.
RAG cần AI bearer và JobContext thật; thiếu thì tool RAG không đăng ký, agent báo
unavailable. Chưa nghiệm thu auth/backend/model hoặc độ chính xác chọn tool.

## Dựng lại riêng ver2

Từ root repo, khi Langflow đang mở:

```powershell
.\langflow\.venv\Scripts\python.exe langflow/setup_ver2.py --install
```

Lệnh chỉ CRUD metadata Langflow, không chạy flow hoặc gọi OpenAI/backend nghiệp vụ.
Chỉ cập nhật bốn endpoint `flowbridge-ver2-*` trong project đúng tên; không dùng
`setup_agent.py --install` cho ver2 vì lệnh đó cập nhật v1. Export bốn flow nằm ở
`langflow/flows/ver2/`; installer giải quyết ID thực của flow con trước khi nối A2A.
Nếu tự import JSON sang máy khác, phải import ba flow con trước và chọn lại chúng
trên ba node A2A. Sao lưu cấu hình tự chỉnh trước khi dựng lại để khỏi mất preset.

Nếu cần sửa lỗi Luna tool/reasoning trên một bộ ver2 đang tự chỉnh, dùng
`python langflow/repair_ver2_model.py` bằng Python trong venv Langflow. Lệnh chỉ thêm
model options và thay lớp cấu hình Agent; giữ preset/prompt/edge/ID, sao lưu vào
`data/ver2-before-model-fix-*` (ignore) và kiểm hash các workflow ngoài ver2. Không
chạy workflow. Không dùng Update All để ghi đè bốn Agent đã bổ sung tùy chọn bằng
Agent stock chưa có các ô này.

Tài liệu upstream: [A2A](https://docs.langflow.org/a2a-server),
[Tools](https://docs.langflow.org/agents-tools). Cấu hình này đối chiếu source bản
Langflow/lfx 1.12.6 cài local. Code/config nhóm GPL-3.0-or-later; xem LICENSE.
