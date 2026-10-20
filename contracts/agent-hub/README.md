# Agent Hub — hợp đồng Web/App và Langflow ver2

Phiên bản API **1.0.1**, phiên bản JSON kết quả/sự kiện **1.0**, ngày **19/10/2026**.
Trạng thái: **đã triển khai, bật tùy chọn sau migration 0006**.
Hướng dẫn chạy và adapter đầu ra thực tế: [AGENT-HUB](../../docs/AGENT-HUB.md).
Đây là hợp đồng Agent Hub riêng, không thay thế App/AI database API 0.4.1 hoặc
OpenCore. Không suy ra API đang hoạt động từ ví dụ hoặc file OpenAPI.

## 1. Nguồn chuẩn và phạm vi

| Tệp | Vai trò |
|---|---|
| [../agent-hub-api.yaml](../agent-hub-api.yaml) | OpenAPI 3.1: 12 operation công khai, request, metadata, lỗi HTTP |
| [schemas/run-result.schema.json](schemas/run-result.schema.json) | JSON Schema 2020-12 chuẩn của response kết quả |
| [schemas/run-event.schema.json](schemas/run-event.schema.json) | JSON Schema của JSON nằm trong SSE `data:` |
| [examples/delivery-result.json](examples/delivery-result.json) | Mẫu đầu ra người dùng cung cấp, giữ nguyên byte |
| [examples/supplier-comparison.json](examples/supplier-comparison.json) | Ví dụ tổng hợp: tỷ lệ trễ, cột chồng số lượng/100% và giá |
| [examples/needs-input.json](examples/needs-input.json) | Chọn sản phẩm, số lượng và ngày giao |
| [examples/failed.json](examples/failed.json) | Run đã nhận nhưng thực thi thất bại |
| [examples/run-events.sse](examples/run-events.sse) | Timeline ML gọi ngầm Data và kết thúc |
| [examples/client-flow.http](examples/client-flow.http) | Trình tự HTTP tham khảo cho đội web/app |

OpenAPI tham chiếu các schema và ví dụ qua đường dẫn tương đối; phải phân phối cả
`agent-hub-api.yaml` và thư mục `agent-hub/`. Không duy trì một schema copy thứ hai
trong backend hoặc frontend. Nếu công cụ SDK cần bản bundle, tạo artifact từ nguồn
này bằng công cụ OpenAPI phù hợp; không sửa bản bundle rồi coi là nguồn chuẩn.

Mẫu gốc do người dùng cung cấp là `template (1).json`. Quyền và provenance của mẫu
ghi ở [examples/PROVENANCE.md](examples/PROVENANCE.md). Các record, giá và UUID/mã
trong ví dụ không chứng minh dữ liệu tồn tại trong hệ thống đang chạy.

Phạm vi đợt đầu: thư viện ba agent, lựa chọn agent cho hội thoại, chat, kết quả,
lịch sử, Activities và hủy run. Chưa có API tạo/publish agent, chia sẻ hội thoại,
upload tài liệu, file download/export, hoặc quản trị model. Không nhận bất kỳ command
SQL, prompt hệ thống, URL tool hoặc credential tùy ý từ client.

## 2. Ranh giới trách nhiệm

```text
Web/App: lựa chọn agent + câu hỏi
  -> Agent Hub API: xác thực/quyền, persistence, revision/idempotency, admission
  -> Langflow ver2: chọn agent chính, tự gọi phụ thuộc, thực thi tool, thu evidence
  -> Response Builder + Validator trong Langflow: JSON kết quả chuẩn
  -> Agent Hub API: kiểm lại schema/ngữ nghĩa ở boundary, lưu kết quả và sự kiện
  -> Web/App: đọc JSON, dựng bảng/biểu đồ, hiển thị nguồn và Activities
```

API không tự quyết định cần Data/ML/RAG, không chạy thêm model để lập kế hoạch và
không tự dựng một bộ điều phối thay Langflow. Queue/executor và persistence có thể
nằm ở tầng ứng dụng, nhưng tác vụ của executor là gọi đúng flow cùng snapshot đã
được admission. Không dùng background task trong tiến trình HTTP làm cam kết bền vững.

Langflow phải có dữ liệu JSON gốc từ các công cụ hoặc kho receipt của đúng run để
dựng `datasets`. Chỉ nối văn bản cuối của Agent trung tâm vào node định dạng không
đủ để bảo đảm toàn vẹn số liệu. Không đưa JSON nghiệp vụ qua LLM rồi dùng bản LLM
viết lại làm dữ liệu authoritative.

Các node mới chỉ được đề xuất trong hợp đồng: bộ dựng/kiểm tra response bằng code,
có thể tận dụng Data Operations/Type Convert cho các bước phù hợp. Đợt viết hợp đồng
này không sửa node, graph, prompt, model, key hoặc runtime ver2/ver3.

## 3. Danh sách endpoint

Prefix chung: `/api/v1/agent-hub`. Tất cả endpoint yêu cầu bearer người dùng.

| Method | Path | Kết quả |
|---|---|---|
| GET | `/agents` | AgentCatalog, chỉ agent người dùng có quyền nhìn thấy |
| POST | `/conversations` | 201 Conversation + Location + ETag |
| GET | `/conversations` | ConversationPage |
| GET | `/conversations/{conversation_id}` | Conversation + ETag |
| PATCH | `/conversations/{conversation_id}` | Conversation mới + ETag; cần If-Match |
| GET | `/conversations/{conversation_id}/messages` | MessagePage |
| POST | `/conversations/{conversation_id}/runs` | 202 AcceptedRun; không đợi inference |
| GET | `/conversations/{conversation_id}/runs` | RunPage để khôi phục phiên trên app |
| GET | `/runs/{run_id}` | RunResult đúng mẫu, không bọc trong `data` |
| GET | `/runs/{run_id}/metadata` | RunMetadata: lựa chọn/thực dùng, thời gian, trace |
| GET | `/runs/{run_id}/events` | text/event-stream |
| POST | `/runs/{run_id}/cancel` | 200 cancelled hoặc 202 cancelling |

Danh sách có `items` và `next_cursor` (null khi hết). `limit` mặc định 20, tối đa
100. Cursor opaque, kiểm scope và hạn; cursor phân trang hết hạn trả 400
`INVALID_CURSOR`, không dùng mã 410 dành riêng event retention. Filter quyền diễn ra
trước phân trang. Snapshot cố định thành viên/thứ tự để đổi `updated_at` không gây
trùng hoặc mất trang; trạng thái resource vẫn được đọc theo quyền hiện tại.
Hội thoại sort updated_at DESC/id DESC; runs sort accepted_at DESC/id DESC;
messages sort sequence ASC. Reload danh sách từ trang đầu để nhận phần mới.

Không có API riêng cho từng loại biểu đồ. Mọi biểu đồ tham chiếu `datasets` trong
RunResult. Một run có thể chỉ trả text, hoặc đồng thời text + nhiều bảng + nhiều chart.

## 4. Lựa chọn agent và phụ thuộc trong Langflow

`selected_agents`: tập ID chuyên môn, không trùng, tối đa ba phần tử, không có nghĩa
ưu tiên theo vị trí. ID chuẩn: `database`, `prediction`, `knowledge`. Central luôn
điều phối, không phải một lựa chọn. `[]` chỉ central; thiếu hoặc null không bật tất cả.

Lựa chọn lưu trên conversation. POST run bắt buộc có `conversation_revision` và
chụp lại lựa chọn trong cùng transaction admission. Đổi selection bằng PATCH với
strong `If-Match`, ví dụ `"2"`, chỉ tác động run mới. Không nhận selection trực tiếp
trong POST run để tránh hai nguồn mặc định/override mâu thuẫn.

| Đường gọi | Policy đợt đầu |
|---|---|
| central -> database/prediction/knowledge | Chỉ agent đã chọn và user được phép dùng |
| prediction -> database | Cho phép phụ thuộc đúng task được allowlist trong ver2 |
| knowledge -> database | Cho phép phụ thuộc đúng task được allowlist trong ver2 |
| database -> agent khác | Không cho phép |
| prediction <-> knowledge | Không tự mở nếu chưa có thiết kế/policy riêng |

Agent được chọn có thể không được gọi nếu câu hỏi không cần. Dependency gọi lười,
không gọi sẵn tất cả. Chọn prediction cho phép Langflow tự gọi database theo nhiệm
vụ phụ thuộc mà không hỏi bật thêm; không cho central dùng database như agent chính
ngoài selection. Ví dụ resolve_entities cho ML rank/score không được mở rộng thành
read_offers hoặc một task tự phát. Việc liệt kê hay bật agent không tự cấp quyền dữ liệu.

`used_agents` trong metadata là các agent chuyên môn đã thực sự bắt đầu, duy nhất,
thứ tự lần dùng đầu tiên. `dependency_agents` là các agent đã dùng ngoài selection
thông qua dependency hợp lệ. Nếu database đã được chọn nhưng ML gọi database như
phụ thuộc, event vẫn có role=dependency, nhưng metadata không liệt kê database trong
dependency_agents. `used_agents` không bao gồm central và không suy ra từ schema tool.

Policy được thực thi trong runtime Langflow, cả central tool visibility lẫn nested
A2A guard; prompt không phải cơ chế quyền. Không đổi dây nối, biến global hoặc
allowlist dùng chung khi một người dùng đổi selection. Cache/receipt phải thuộc
đúng run, identity, selector, phạm vi quyền và thời điểm; không tái sử dụng chéo user.
ML và RAG trong cùng run có thể chia sẻ receipt đã xác minh nếu cùng yêu cầu; không
coi cache trúng là một lượt API tool vừa thực thi. Lưu provenance đúng nguồn ban đầu.

## 5. Admission, revision và idempotency

POST conversation/run/cancel bắt buộc `Idempotency-Key` dài 16–128 ký tự. Scope theo
principal đã xác minh, tenant nếu có, method, canonical path và key. Giữ record ít
nhất 24 giờ; cùng key/cùng body ngữ nghĩa trả nguyên HTTP status/body/Location đã
chốt, không tạo thêm message, run hoặc sự kiện. Quyền hiện tại kiểm lại trước replay.

Fingerprint chuẩn hóa JSON object key order và selection set order; giữ nguyên text,
timezone và nội dung thực của request. Không trim/đổi số hay ngày để làm hai yêu cầu
khác nghĩa thành một. Khác fingerprint trả 409 `IDEMPOTENCY_CONFLICT`, retryable=false.
Claim chưa commit trả 409 `IDEMPOTENCY_IN_PROGRESS`, retryable=true, Retry-After=2.
Idempotency không tự ngăn user tạo hai run bằng hai key mới; admission còn kiểm active run.

Thứ tự khi POST run: xác thực/quyền -> tra idempotency/replay -> validate request và
revision -> kiểm không có run active -> transaction lưu run, hai message, snapshot,
idempotency và yêu cầu dispatch bền vững -> 202. Nếu không lưu/dispatch bền vững được,
trả 503, không hứa run đang chạy. Không trả run_id giả cho request bị từ chối.

Mỗi conversation chỉ có một run queued/running/cancelling; thêm run trả 409
`CONVERSATION_BUSY`. Conversation revision chỉ tăng do metadata/title/selection
thay đổi, không tăng khi có message/run mới. POST run revision cũ trả 409
`CONVERSATION_REVISION_CONFLICT`; PATCH thiếu If-Match trả 428, lệch trả 412.
PATCH no-op giữ revision. CAS giữa PATCH và admission phải nguyên tử.

Replay 202 vẫn có status queued trong AcceptedRun dù run hiện đã terminal: đây là
biên nhận ban đầu. Client lấy trạng thái hiện tại ở result/metadata, không suy ra từ replay.
Không tự retry workflow sau lỗi mà có thể đã tạo tác động nghiệp vụ. Một retry executor
phải dùng cùng run_id và dedup dispatch, đồng thời bảo toàn idempotency của command
nghiệp vụ; không coi một HTTP timeout là bằng chứng command chưa được thực hiện.

## 6. Lifecycle, lịch sử và bổ sung thông tin

```text
queued -> running -> completed | needs_input | failed
queued -> cancelled | failed
running -> cancelling -> cancelled | failed
```

Cancellation và terminal completion được serialize bằng CAS. Nếu terminal commit
trước yêu cầu cancel: cancel trả 409 (cancelled sẵn thì 200). Nếu cancellation được
chấp nhận trước: không công bố completed sau đó; chờ in-flight call kết thúc hoặc
timeout rồi đóng run cancelled/failed. `cancelling` không đồng nghĩa upstream đã dừng.
Không rollback PR/PO hay chi phí model đã phát sinh; outcome nghiệp vụ chưa rõ phải
đối soát theo contract nghiệp vụ, không tự phát lại command.

`completed`, `needs_input`, `failed`, `cancelled` là terminal, result immutable.
`needs_input` không giữ executor/model chờ người dùng. Tạo run mới với
`response_to_run_id` và `question_answers` (key = questions[].id) để tiếp tục; parent
phải là needs_input, thuộc cùng conversation và còn là lượt terminal mới nhất.
Không replay câu trả lời clarification cũ vào ngữ cảnh đã thay đổi; trả 409
`CLARIFICATION_CONTEXT_STALE` khi parent không còn mới nhất. Thiếu câu trả lời bắt
buộc, thừa question ID, option không tồn tại hoặc sai kiểu trả 422 `INVALID_QUESTION_ANSWER`.
Free text mới không có response_to_run_id vẫn là câu hỏi mới, không bị ép vào form.

Các kind: text=string không rỗng; number=JSON number hữu hạn hoặc decimal string
chuẩn; date=YYYY-MM-DD hợp lệ; single_choice=đúng options[].value, không gửi label
thay ID. Backend validate shape; Langflow xác minh ý nghĩa và đối tượng bằng tool.
Không tin UUID/mã user gửi là entity đã được xác minh.

Admission tạo user message và assistant placeholder có sequence tăng theo conversation.
User text rỗng khi chỉ gửi structured answers; question_answers giữ đầy đủ câu trả lời.
Assistant text rỗng trong lúc chạy, terminal đồng bộ từ result.answer, answers luôn {}.
User result_url=null; assistant result_url trỏ run từ đầu. Message.updated_at thay đổi
khi placeholder được hoàn tất; reload lịch sử cập nhật message theo id, không append trùng.
HTTP pagination không tự stream thay đổi; dùng events rồi fetch lại message/result khi terminal.

## 7. Thời gian và ngày tương đối

CreateRun bắt buộc timezone IANA; server xác minh thay vì chỉ regex. Chấp nhận alias
Asia/Saigon và có thể canonicalize thành Asia/Ho_Chi_Minh trong metadata. accepted_at
do server tạo; business_date = ngày tại timezone đó khi admission, immutable trong run.
Ví dụ accepted_at 19/10/2026 giờ Việt Nam thì ngày mốt là 21/10/2026. Không dùng ngày
hardcode trong prompt hoặc ngày lúc retry executor. Ngày do user nói rõ vẫn giữ đúng ý nghĩa.

Timestamps RFC3339 có offset; server ưu tiên UTC Z. Mẫu gốc +00:00 có microsecond
vẫn hợp lệ. `context.as_of` là cutoff thực của dữ liệu, không mặc định bằng thời gian
chạy. Từ 1.0.1, `as_of=null` nếu upstream không cung cấp cutoff; kèm cảnh báo
`AS_OF_UNKNOWN`, UI không được hiển thị như số liệu chốt tại hiện tại. period_from/period_to đi cùng nhau, from <= to, ngày biên bao gồm cả hai phía.
Nguồn dùng timestamp cần quy đổi biên theo timezone dataset và ghi rõ trong context.

## 8. RunResult chuẩn và ngữ nghĩa trạng thái

Giữ đúng 11 trường root của mẫu: schema_version, status, run_id, answer, datasets,
tables, charts, sources, warnings, questions, error. Không thêm wrapper Langflow,
raw receipts hoặc string JSON vào answer. Các list luôn là array, không null.
Text có thể là Markdown an toàn; frontend không thực thi HTML/script nhúng.

| Status | Nội dung được phép |
|---|---|
| queued/running/cancelling | answer="", arrays rỗng, error=null; tiến độ qua SSE |
| completed | answer và/hoặc dữ liệu có ích; questions=[], error=null |
| needs_input | questions có ít nhất một mục; error=null; có thể kèm lựa chọn đã xác minh |
| failed | error bắt buộc; không xuất dataset/table/chart/source; answer có thể giải thích lỗi |
| cancelled | error=null; không xuất dataset/table/chart/source/questions |

completed phải có answer không trắng hoặc ít nhất một dataset; không trả thành công
rỗng để che lỗi. Không có dữ liệu hợp lệ là completed + lời giải thích + dataset rows=[]
nếu tool đã xác nhận phạm vi thật rỗng; khác lỗi API hoặc thiếu quyền. Không có records
không phải tỷ lệ 0%. Run failed vẫn được GET bằng HTTP 200 vì resource tồn tại.

Nếu một phần tác vụ tùy chọn thất bại nhưng vẫn đáp ứng được phần có ích, completed
có warning `PARTIAL_RESULT`; chỉ xuất dữ liệu đã xác minh và nói rõ phần thiếu trong
answer. Nếu phần cốt lõi thất bại, status=failed. Không đưa số giả/nhà cung cấp tự đặt
vào dataset để lấp phần thiếu. `complete=false` mô tả độ đầy đủ của dataset, không
thay thế trạng thái lỗi của run.

## 9. Dataset và bảng — quy tắc bắt buộc ngoài JSON Schema

Validator chạy JSON Schema 2020-12 với format assertions **bật**, sau đó kiểm:

1. ID duy nhất trong mỗi collection; column.field duy nhất trong dataset; question.id
   và option.value duy nhất; agent ID duy nhất. Không yêu cầu ID bảng khác ID chart.
2. Mỗi row có đúng tập field đã khai báo trong columns; không có extra field hoặc
   key thiếu. Field nullable vẫn hiện diện với giá trị null; nullable mặc định false.
3. Kiểu cell đúng column.type: integer là số nguyên trong miền JS safe integer;
   number hữu hạn và an toàn cho mục đích hiển thị; boolean không coi là integer;
   date/datetime đúng định dạng; decimal là string `^-?(0|[1-9][0-9]*)(\.[0-9]+)?$`
   với đúng scale chữ số sau dấu chấm (scale=0 không có dấu chấm).
4. Decimal không scientific notation, không dấu phân cách hàng nghìn. Tiền, quantity
   nghiệp vụ dùng decimal string giữ precision/scale upstream (API 0.4.1 thường scale=4).
   Tính bằng decimal ở server; client chỉ chuyển số phục vụ tọa độ hiển thị trong miền
   an toàn, tooltip/bảng dùng giá trị gốc. Không ghi ngược số làm tròn vào nghiệp vụ.
5. format currency bắt buộc type decimal, currency ISO 4217 và scale. Percent dùng
   number theo thang **0–100**, unit="%", không gửi 0.1 để biểu diễn 10%.
   Các phần trăm không bị giới hạn <=100 cho mọi bài toán, nhưng tỷ lệ giao trễ phải
   nằm 0–100 theo định nghĩa thống kê. Validator kiểm công thức phù hợp loại metric.
6. source_ids trỏ nguồn thực sự có trong sources; dataset không có provenance bị từ
   chối. Mỗi source thực sự được dẫn chiếu bởi dataset hoặc dùng chứng minh answer/RAG.
7. Table.dataset_id tồn tại; fields tồn tại, không trùng; thứ tự fields là thứ tự cột.
   Label/type/unit lấy từ columns, không sao chép một bản metadata cột khác vào table.
8. context.complete=false bắt buộc warning `DATASET_INCOMPLETE` gắn đúng dataset_id.
   complete=true chỉ khi toàn bộ phạm vi truy vấn đã được tính, không vì trang đầu
   API trả HTTP 200. Mẫu số phải đúng phạm vi, quyền và cutoff của tử số.

Rows giữ thứ tự server cung cấp. Sắp xếp cục bộ trong bảng chỉ thay view; không sửa
dataset hoặc thứ tự biểu đồ khác. Không truy vấn dữ liệu bổ sung từ một URL do model
đặt trong dataset. Không có executable JS/HTML hoặc chart library options trong schema.

## 10. Biểu đồ và cột chồng

`type`: bar, line, pie, donut. `orientation`: vertical/horizontal. `stacking`: none,
normal, percent. Frontend có renderer theo whitelist, không eval cấu hình từ model.

`x_field` **luôn là trường phân nhóm hoặc thời gian**, không có nghĩa trục ngang vật
lý. `series[].field` luôn là số đo. Với bar horizontal, categories hiển thị trên trục
đứng, measures trên trục ngang; x_label/y_label vẫn lần lượt là nhãn category/measure.
Không đổi field hoặc tráo đơn vị chỉ vì orientation. Với pie/donut, x_field là nhãn
miếng, một series là giá trị; orientation=vertical là quy ước placeholder và bỏ qua
khi render, stacking=none; label/unit vẫn dùng cho tooltip.

| Cấu hình | Dataset/điều kiện |
|---|---|
| bar + none | Một hoặc nhiều series, nhóm cạnh nhau khi nhiều series |
| bar + normal | Ít nhất hai series không âm, cùng đơn vị, cộng theo mỗi category |
| bar + percent | Ít nhất hai series là **số lượng gốc**, không âm, cùng đơn vị; mỗi category chuẩn hóa tổng thành 100% khi vẽ |
| line | vertical + none; categories có thứ tự xác định; thiếu điểm là gap |
| pie/donut | Một series không âm; tổng >0 để vẽ; không biến null thành 0 |

Bar percent dùng raw counts trong dataset, `y_unit="%"`; series.column.unit vẫn là
đơn gốc. Tooltip có thể hiển thị số lượng và tỷ trọng. Đây là ngoại lệ có chủ đích
với quy tắc chart.y_unit phải khớp unit của series; không chuẩn hóa phần trăm hai lần.
Nếu muốn vẽ tỷ lệ đã tính sẵn (on_time_pct/late_pct cộng thành 100), dùng bar normal
và các cột percent unit="%", không dùng stacking=percent.

Chart validator còn kiểm dataset/fields tồn tại; x_field không null, giá trị category
duy nhất trong dataset của chart; series.field duy nhất và thuộc integer/number/decimal;
series không trỏ x_field. Bar/line nhiều series phải cùng đơn vị/tiền tệ, y_unit khớp
metadata (ví dụ column.unit="VND/cuộn", column.currency="VND"). Không có dual-axis trong
1.0: tỷ lệ và giá là hai chart riêng. Giá chỉ so sánh cùng sản phẩm, đơn vị quy đổi,
tiền tệ, phạm vi thời gian và điều kiện thuế/chiết khấu, thể hiện trong context.filters.

Null là thiếu dữ liệu, không zero. Bar bỏ mark null và ghi rõ thiếu; line không nối
qua gap. Stack chỉ vẽ category có đủ tất cả series; mẫu thiếu series phải gắn warning
`CHART_DATA_INCOMPLETE`. Tổng raw counts=0 trong percent stack thì category không có
tỷ lệ xác định, render trống/N/A, không mặc định một nhóm 100%. Pie/donut có null
hoặc tổng 0 thì không render chart; bảng vẫn hiển thị. Không tự gộp duplicate category
hoặc tự chọn SUM/AVG ở frontend; cần server tạo dataset đã aggregate đúng.

**Định nghĩa mẫu người dùng:** 71 nhận đủ đúng hạn + 110 nhận đủ trễ = 181 nhận đủ;
tỷ lệ trễ = 110/181*100 = 60.77348...%, hiển thị 60,77%. Tổng tất cả nhóm là 188,
nhưng không dùng 188 làm mẫu số tỷ lệ trễ trên đơn đã nhận đủ. Nếu vẽ stack cả năm
nhóm để thấy cơ cấu 188 đơn, tỷ trọng 110/188 là cơ cấu tổng đơn, phải đặt tiêu đề/
denominator_definition khác, không gọi đó là tỷ lệ trễ theo định nghĩa trên.

Tính tỷ lệ bằng code từ dữ liệu công cụ, làm tròn chỉ lúc hiển thị; số trong answer,
bảng và chart cùng công thức. Không lấy số làm tròn trong answer để dựng dataset.

## 11. Giới hạn và xử lý đầu ra không hợp lệ

Giới hạn hợp đồng: message 20.000 ký tự Unicode; title 200; answer 100.000; tối đa
20 dataset/20 table/20 chart/100 source/20 question; mỗi dataset 64 column, 2.000 row;
tổng tối đa 5.000 row/run; tối đa 12 series/chart. HTTP request tối đa 128 KiB UTF-8;
JSON result không nén tối đa 2 MiB UTF-8. Kiểm cả limit schema lẫn limit tổng byte.

Không cắt JSON giữa chừng. Nếu có thể đáp ứng bằng aggregate đúng yêu cầu thì dựng
aggregate từ nguồn đủ; nếu chủ động giới hạn một phạm vi thì complete=false, warning
và answer giải thích. Không tự sample rồi công bố tỷ lệ toàn bộ. Nếu vẫn vượt giới
hạn, failed `OUTPUT_LIMIT_EXCEEDED`, đề nghị thu hẹp câu hỏi. Endpoint phân trang dataset
hoặc file export cần hợp đồng mở rộng; không tự sáng tạo URL trong 1.0.

Schema/type/ID/reference hoặc số liệu mâu thuẫn -> không phát payload lỗi ra client.
Nếu một chart tùy chọn có cấu hình lỗi nhưng dataset còn đúng, bỏ chart đó và thêm
`CHART_OMITTED`; nếu dataset sai hoặc kết quả cốt lõi sai, failed
`OUTPUT_VALIDATION_FAILED`. Không chạy LLM sửa số liệu, không đổi lỗi thành completed.
Validator ở API giữ vai trò boundary; việc đóng gói JSON vẫn được làm trong Langflow.

## 12. SSE và Activities

GET events dùng Authorization header; browser native EventSource không cấu hình
Authorization tùy ý, nên dùng streaming fetch/SSE client hỗ trợ header. Không truyền
token trong URL hoặc localStorage snippet của hợp đồng. Server/proxy tắt buffer,
Content-Type text/event-stream, Cache-Control no-store,no-transform; heartbeat comment
`: heartbeat` khoảng 15 giây, không tính sequence và không lưu làm activity.

Mỗi event có id opaque duy nhất trong run và sequence nguyên tăng. SSE `event:` phải
khớp JSON.type, `id:` khớp JSON.id. `data:` là một JSON theo run-event.schema.json.
`run.*` status phải tương ứng tên event (started -> running). Event hợp lệ gồm
queued/started/cancelling/terminal, agent/tool started/completed và answer.delta.
Span ID + parent_span_id dùng ghép start/complete và cây gọi khi các nhánh chạy song song.
Central role=coordinator, invoked_by=null, parent_span_id=null; agent trực tiếp role=primary,
invoked_by=central; dependency phải khớp allowlist và span cha. Mỗi complete chỉ khớp
một start trong cùng run, giữ nguyên agent/tool/role; duration_ms không âm.

Chỉ phát hoạt động thực tế, không prompt, chain of thought, SQL, credential, raw tool
input/output hoặc dữ liệu không có quyền. Nhãn như "Đang truy vấn lịch giao" do runtime
tạo theo hoạt động đã bắt đầu, không phát trạng thái suy nghĩ giả từ text model.
answer.delta chỉ là bản nháp câu trả lời hiển thị; terminal answer thay toàn bộ bản nháp,
không append tiếp và không lấy số từ delta để vẽ chart. Có thể không phát delta.

Event stream được persist và có thể replay. Last-Event-ID -> trả event **sau** ID đó
rồi theo dõi live, không bỏ khe giữa replay/tail. Không có header -> replay từ đầu còn
retention rồi tail. Client dedup run_id+id, kiểm sequence; gap -> reconnect hoặc fetch
result/metadata. Giữ toàn bộ events khi run active và ít nhất 24 giờ sau terminal;
metadata.events_expires_at=null khi active, có hạn chính xác khi terminal.

ID thuộc run khác hoặc không hợp lệ -> 400 INVALID_EVENT_CURSOR; ID của run nhưng đã
hết retention -> 410 EVENT_CURSOR_EXPIRED (không xóa result). Biết ID hết hạn nhờ tombstone
hoặc watermark lưu cùng run, không đoán từ ID. Kết nối lại terminal run replay phần
còn thiếu rồi đóng; nếu cursor đã là terminal event thì trả stream rỗng và đóng.

Commit RunResult terminal và terminal event nguyên tử qua cơ chế persistence/outbox
trước khi delivery. Nhận run.completed/needs_input/failed/cancelled -> GET result.
Nếu auth hết hạn hoặc mất quyền khi đang stream, đóng kết nối; reconnect phải xác thực
lại. Lỗi transport không tự hủy hoặc chạy lại run; GET result luôn là nguồn chuẩn.

## 13. Lỗi, retry và quyền

Lỗi trước admission hoặc đọc resource dùng HTTP non-2xx và Problem `{error, issues?}`.
`issues` chỉ nêu field/code/message, không phản chiếu secret hoặc payload nguyên bản.
Lỗi sau admission lưu failed RunResult với error cùng cấu trúc code/message/retryable/
trace_id. `retryable=true` cho biết có thể thử lại sau khi điều kiện thay đổi, không
tự cấp phép phát lại side effect hoặc tạo loop retry vô hạn.

| HTTP / vị trí | Code tiêu biểu | Hành động client |
|---|---|---|
| 400 | INVALID_JSON, INVALID_CURSOR, INVALID_EVENT_CURSOR, INVALID_HEADER | Sửa request/cursor |
| 401 | UNAUTHENTICATED | Refresh qua IAM; WWW-Authenticate Bearer |
| 403 | FORBIDDEN, AGENT_NOT_ALLOWED | Hiển thị thiếu quyền, không tự đổi agent |
| 404 | NOT_FOUND | Resource không tồn tại hoặc bị che bởi ACL |
| 409 | IDEMPOTENCY_CONFLICT | Không retry cùng key với nội dung khác |
| 409 | IDEMPOTENCY_IN_PROGRESS, CONVERSATION_BUSY | Chờ/khôi phục run hiện tại; tôn trọng Retry-After nếu có |
| 409 | CONVERSATION_REVISION_CONFLICT | GET conversation, cập nhật lựa chọn trước ý định gửi mới |
| 409 | CLARIFICATION_CONTEXT_STALE, RUN_ALREADY_TERMINAL | Tải lại ngữ cảnh/trạng thái |
| 410 | EVENT_CURSOR_EXPIRED | GET result/metadata, không chạy lại AI |
| 412 / 428 | REVISION_PRECONDITION_FAILED / PRECONDITION_REQUIRED | GET ETag rồi PATCH đúng điều kiện |
| 413 / 422 | PAYLOAD_TOO_LARGE / VALIDATION_FAILED, INVALID_QUESTION_ANSWER | Sửa dữ liệu |
| 429 | RATE_LIMITED | Chờ Retry-After |
| 500 / 503 | INTERNAL_ERROR / EXECUTION_UNAVAILABLE, AGENT_UNAVAILABLE | Retry cùng key nếu admission chưa rõ |
| failed result | DEPENDENCY_UNAVAILABLE, DEPENDENCY_FAILED, TOOL_CALL_FAILED | Hiển thị nguyên nhân an toàn, cho user quyết định retry |
| failed result | EXECUTION_TIMEOUT, EXECUTION_INTERRUPTED, EXECUTION_LIMIT_REACHED | Không tự nới budget/chạy lại |
| failed result | OUTPUT_VALIDATION_FAILED, OUTPUT_LIMIT_EXCEEDED | Không cố render dữ liệu sai |

Error/warning code là chuỗi UPPER_SNAKE_CASE mở rộng được, client có fallback message;
status/chart type là enum đóng của 1.0. Không phân loại lỗi bằng cách tìm chữ trong answer.

IAM hiện có xác minh token, receiver map issuer+subject sang actor/grants. Tenant nếu
hỗ trợ lấy từ trusted context, không từ JSON/header tùy ý hoặc prompt. Quyền được kiểm
trên conversation, run, nguồn và mọi tool; agent dependency không vượt quyền. Không
dùng token demo/service-user toàn cục cho mọi web user. Worker chạy service identity
kèm context người khởi tạo được xác minh; kiểm lại grant khi thực thi và khi đọc/replay.
Nếu mất quyền dữ liệu giữa lúc chạy, fail an toàn và không xuất một phần bị cấm.

URL nguồn chỉ https đã kiểm quyền/allowlist, không có token/secret. Frontend không
fetch URL tùy ý để dựng chart; không có API trả raw trace theo trace_id. CORS allowlist
origin theo cấu hình, cho phép Authorization/Idempotency-Key/If-Match/Last-Event-ID,
expose ETag/Location/Retry-After/X-Request-ID; không wildcard credentials. JSON responses
đều Cache-Control no-store. Transport production dùng HTTPS.

## 14. Mapping tích hợp Langflow

Đây là yêu cầu cho lần triển khai sau, không phải public endpoint mới:

| Dữ liệu | Nguồn và nơi dùng |
|---|---|
| run_id, conversation_id, revision snapshot | Hub cấp; đưa vào execution context riêng mỗi run |
| question/message + validated question_answers | Request hiện tại; nối history đúng conversation |
| selected_agents snapshot | Hub truyền nguyên trạng; Langflow lọc primary tools theo selection |
| accepted_at, timezone, business_date | Server tạo/chốt; mọi agent dùng cùng cutoff ngày nghiệp vụ |
| auth context reference | Trusted server binding; không nhét bearer vào prompt, history hoặc SSE |
| receipt/evidence | Runtime thu từ tool thực, đúng run và quyền; không chấp nhận evidence do LLM tự viết |
| activity events | Runtime/adapter phát khi hoạt động thật; Hub lưu và chuyển tiếp |
| RunResult | Node cuối dựng/validate; Hub kiểm boundary rồi lưu terminal |

Public run_id và ID trace/request bên trong Langflow có thể khác; giữ mapping server-side,
không ép thay correlation ID hiện có của ver2. Không sửa limit model/A2A theo client;
đụng budget trả lỗi rõ, không tự chuyển model/agent hoặc âm thầm gọi workflow khác.
Không parse Chat Output Markdown thành số liệu. Nếu Langflow bọc JSON trong envelope
transport, adapter chỉ unwrap ở đúng boundary đã biết rồi validate, không heuristic
scan mọi chuỗi JSON trong answer. Không thêm một flow inference để chữa output invalid.

## 15. Tương thích và bàn giao

`/api/v1/agent-hub` version hóa HTTP resource; `schema_version=1.0` version hóa result
và event. Thêm field/enum hoặc đổi cách hiểu stacking/mẫu số phải cập nhật nguồn chuẩn,
ví dụ, changelog và policy version; payload schema hiện tại additionalProperties=false
nên không tự xem field mới là tương thích. Phải thương lượng phiên bản hoặc nâng API
khi thay đổi breaking; không cho model tự quyết schema_version.

Frontend lưu selected agents từ Conversation, hiển thị actual agents từ Metadata/
Activities; dựng bảng/chart từ result sau khi terminal; fallback an toàn với lỗi,
empty/null/complete=false. Backend triển khai endpoint/persistence/durable dispatch/
authorization/SSE theo OpenAPI. Langflow triển khai run-scoped selection/dependency
guard, evidence bridge và node JSON cuối. Không một phần nào được coi đã có chỉ vì
hợp đồng này mô tả nó.

Lần bàn giao này **không chạy kiểm thử, không gọi model/API nghiệp vụ, không đổi DB,
không restart server hoặc sửa workflow**, theo yêu cầu người dùng tự kiểm thử.
Các tình huống cần nghiệm thu khi triển khai: payload mẫu gốc; biểu đồ ngang; stack
normal/percent; tiền decimal; chia cho 0/null; duplicate ID/reference lỗi; đổi selection
giữa run; hai user đồng thời; replay idempotency; SSE reconnect/retention; cancellation
race; mất quyền; dependency thất bại; output lớn/không hợp lệ. Đây là danh sách nghiệm
thu cần thực hiện sau, không phải báo cáo các kiểm thử đã đạt.

## Bổ sung triển khai 1.0.1

- CreateConversation nhận `request_id` tùy chọn, được kiểm quyền và bất biến. Khi
  không gắn PR, knowledge dùng danh tính human/EMPLOYEE tra tài liệu global đã xuất
  bản qua `POST /api/v1/agent-hub/knowledge/search`. Ngữ cảnh PR/job cũ chỉ dùng cho
  hội thoại đã gắn PR, giữ nguyên kiểm quyền và revision. Không nhận job,
  token công cụ hoặc danh sách dependency từ client.
- Event agent/tool.completed có `duration_ms` và khi failed có `error` gồm
  code/message/retryable/trace_id. Agent/tool.started không có duration/error.
  Span được đóng khi run gián đoạn để frontend không treo; xem quy tắc thời gian,
  handoff và tool thống kê trong [hướng dẫn runtime](../../docs/AGENT-HUB.md).
- Web/app dùng SSE RunEvent để dựng bảng công cụ, agent, số giây và lỗi;
  giữ Last-Event-ID khi nối lại. Chưa phát answer.delta.
- Public as_of nhận null khi không biết; không sửa byte mẫu gốc. Adapter giữ
  tiền decimal, ánh xạ partial thành completed + warning, câu hỏi chuỗi thành
  text question và percent đã tính thành normal stacking để tránh tính lần hai.
- Cursor snapshot tối đa 10.000 mục, quá giới hạn trả 413. Không tự xóa lịch sử
  hoặc tự retry model sau lỗi/worker restart. Bằng chứng kiểm thử ghi ở checkpoint.
