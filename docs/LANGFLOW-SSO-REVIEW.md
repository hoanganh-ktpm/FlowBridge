# Kiểm tra kết nối Langflow và SSO — 18/10/2026

Bổ sung sau triển khai trên `huuhuandev_features`: đã thêm adapter backend và
profile serving, xem [cấu hình hiện tại](LANGFLOW-SSO.md). Kiểm tra ngoài sandbox
xác nhận7860và8001đang mở;8000/8761/8762/18443/18082/19444đóng. Kết quả TCP trong
sandbox bên dưới chỉ phản ánh giới hạn sandbox. Kết nối Supabase read-only bị
timeout; chưa chạy migration0004 hoặc nghiệm thu SSO/model thật.

Phạm vi: kiểm tra source, cấu hình metadata Langflow bằng SQLite read-only và
khả năng kết nối local. Không sửa runtime, flow, credential, database nghiệp vụ;
không chạy model, migration hoặc workflow. Repo main, HEAD 2464997.

## Hiện trạng có bằng chứng

- Frontend `clients/sso/src/api.js` refresh access token bằng Keycloak SDK rồi
  gửi bearer cho API. Chưa có giao diện chat hoặc client gọi Langflow.
- Backend chuẩn introspect token tại IdP, kiểm issuer/audience/expiry/client;
  SupplierPolicy tiếp tục kiểm user active theo exact issuer+subject và grants.
- `scripts/langflow_test_backend.py` là app riêng tại loopback 8001, dùng local
  bearer và override authenticated_identity thành tài khoản DB của profile.
  Read-only và allowlist route; không chứng minh xác thực SSO.
- Metadata hiện có 4 flow ver2; 5 tool Dữ liệu và 4 tool ML đều trỏ 8001,
  có credential cố định. RAG có origin trống, không token/JobContext tại node.
  `langflow/data/agent.env` fallback origin 8000 nhưng user/AI token và JobContext
  đều trống. Không ghi giá trị credential vào báo cáo.
- Tool lấy bearer từ node hoặc env, không lấy phiên người dùng frontend.
  A2A cần truyền context người gọi qua cả flow con; context chỉ ở flow cha
  chưa đủ vì tool của flow con tự tạo BackendConfig.
- `sendChatMessage` và `getChatHistory` còn thuộc UNAVAILABLE trong
  supplier_assistant/database_service.py; chưa có adapter chat gọi Langflow.
- RAG PostgreSQL có nhánh riêng khi bật cấu hình, cần service AI_SERVICE và
  X-Job-Context là analysis task thật. Handler hiện từ chối X-User-Context.
  Không thể dùng trực tiếp token human thay service token cho RAG.
- Launcher Langflow bật LANGFLOW_AUTO_LOGIN=true; đây là đăng nhập editor,
  độc lập với xác thực API FlowBridge và quyền truy cập dữ liệu.
- Các cổng 7860/8000/8001/8761/8762/18443/18082/19444 không kết nối được từ
  phiên kiểm tra; Docker CLI không kết nối được Docker Desktop Linux engine.
  Không có bằng chứng runtime E2E đang hoạt động. Không suy trạng thái máy khác
  hoặc trạng thái ngoài khả năng quan sát của phiên sandbox này.
- Không có docs/PLAN-OPENCORE.md, PROJECT-RULES.md, TASK-API-TEST.md trong checkout;
  không tự tick task/CP theo snapshot cũ.

## Cách nối để dùng sau đăng nhập

Luồng đề xuất: UI đăng nhập Keycloak → API chat backend xác thực/kiểm quyền →
adapter gọi flow Langflow cố định trong mạng nội bộ → tool gọi API chuẩn
với context đúng người dùng → policy/DB hoặc ML → trả kết quả về UI.

1. Nối operation chat hiện có vào service/adapter Langflow; giữ API key Langflow
   ở backend. API key này xác thực backend với Langflow, không thay bearer user.
2. Adapter cấp context riêng cho từng request, xuyên các flow A2A con. Không lưu
   bearer của nhiều người vào env, flow metadata, prompt, chat history hoặc log.
   Cache/state/session phải phân vùng theo caller đã xác minh; session_id do
   browser gửi không phải bằng chứng quyền. Không cho client tùy ý đổi flow,
   upstream, credential hoặc tweaks của node.
3. Dữ liệu/ML dùng access token human còn hiệu lực gọi API SSO chuẩn; giữ kiểm
   quyền tài nguyên. Chọn origin thực tế của API/gateway, không dùng backend test
   8001. Browser refresh token trước request; backend không giữ refresh token
   trong flow. Request hết hạn cần có hành vi 401/đăng nhập lại rõ ràng.
4. RAG cần service token thật, grant AI_SERVICE và analysis task hợp lệ được
   backend kiểm thuộc phạm vi caller trước khi chuyển job context. Chat không có
   task cần thiết kế thêm user-delegation/ACL theo caller; runtime hiện chưa hỗ
   trợ nhánh đó. Không dùng chung task demo cho mọi user hoặc bịa UUID.
5. Thêm UI chat gọi API sau đăng nhập; xử lý 401/403/503 và tách lịch sử từng user.
   Workflow/email/outbox cần adapter/worker riêng nếu nghiệm thu toàn quy trình
   mua hàng; không coi chat/SSO hoạt động là chúng đã hoạt động.

Nếu mục tiêu là mở chính editor Langflow sau login, dùng external authentication
với IdP/proxy, kiểm tính năng thực của bản cài trước khi cấu hình. Tài liệu upstream:
https://docs.langflow.org/1.11.0/external-authentication
Đăng nhập editor không tự truyền bearer vào custom tools hiện tại.

## Kiểm tra cần chạy khi có runtime

Login thật → chat → catalog/ML bằng bearer user; anonymous bị 401; user thiếu grant
bị 403; user A/B đồng thời không trộn token, state hoặc lịch sử; token hết hạn và
logout; flow A2A con giữ caller; RAG task khác owner/phạm vi bị từ chối; backend
không ghi token vào log/history/metadata. Chạy API đọc trước, không tự tạo nghiệp
vụ hoặc gọi model trả phí để chứng minh kết nối.

Các kiểm tra runtime trên **chưa chạy** trong lượt review này. SQLite đọc lần đầu
bị lỗi encoding console, đã chạy lại UTF-8 thành công. Không có thay đổi dependency
hoặc migration; không commit/publish.
