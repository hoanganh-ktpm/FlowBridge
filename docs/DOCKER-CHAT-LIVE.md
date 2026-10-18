# Nghiệm thu Docker chat thật — 2026-10-18

## Bổ sung ngày2026-10-19: Khung chat trên trang8761

- Tab **Trợ lý AI** cho EMPLOYEE đã build/recreate riêng client-a. Dùng bearer
  SSO vào backend, không gửi Langflow API key/editor password xuống browser.
- UI suite13passed6.5s: payload/auth, lịch sử/reload, literal text chống HTML,
  retry cùng key/body khi mạng lỗi, grant gating và mobile. QA ảnh chỉ dùng mock
  và synthetic data, không chụp credential/OTP của bài kiểm thật.
- E2E cuối1passed24.5s: loginPKCE/OTP, bấm Gửi tin nhắn từ UI, trả đúng mã/tên
  2sản phẩm đối chiếu API, reload vẫn đăng nhập và khôi phục hội thoại; replay200,
  history200 đúng một lượt, anonymous401. Không bật/nghiệm thu ML/RAG trong bài này.
- Hai lần đầu phát hiện reload mất phiên; check-sso dùng URL chứa query hội thoại
  bị IdP từ chối redirect_uri. Đã sửa callback luôn chính xác origin + `/`, chỉ
  giữ UUID tạm qua redirect rồi xóa. Không wildcard redirect, không lưu bearer/
  content trong browserstorage. Diagnostic anonymous và E2E xác minh fix thật.
- Dùng fixture được user yêu cầu giữ ở CODE-DOCKER-CHAT-03, không tạo/cleanup lại
  tài khoản hoặc dữ liệu test. Các lệnh fixture bên dưới chỉ dùng cho profile
  nghiệm thu mới, không chạy lại trên fixture đang dùng thủ công.

Repo `huuhuandev_features`, base HEAD `2464997`, thay đổi chưa commit. Bài kiểm
chạy qua trình duyệt Chromium, Keycloak, backend HTTP, PostgreSQL và Langflow/model
thật. Không override authentication, không dùng bearer giả hoặc mock model.

## Kết quả

- Docker doctor đạt. Image prepare/tooling/API/Langflow/client/browser build đạt.
  Keycloak, API và Langflow healthy; client A chạy tại port8761. Stack giữ chạy.
- Supabase ban đầu ở `0004_knowledge_manager`, thiếu migration trong main hiện tại.
  Fetch tất cả nhánh tìm được nguồn `origin/minnhungdev_features`, commit5e96b38.
  Đưa wrapper và `alembic/sql/core_0004.sql` vào nhánh hiện tại; SQL nguyên byte.
  Chat chưa áp được đặt revision0005 nối tiếp0004; migrate thật exit0 chỉ áp delta0005.
- Snapshot Langflow native là graph ver2 cũ. Chat đầu200 nhưng model báo thiếu tác
  vụ catalog, nên chưa đạt nghiệp vụ. Đã nâng bản Docker bằng các graph migration
  có sẵn; kiểm lại24trường preset trên4agent không đổi model/credential. Lần nâng
  tiếp theo báo metadata current, không PATCH hoặc gọi model.
- E2E cuối: **1 passed, 38.0s**. Login PKCE/OTP thật; `/api/v1/me`200; câu hỏi đọc
  danh mục trả mã và tên của đúng2sản phẩm đối chiếu API thật. POST chat200,
  replay200 cùng kết quả, history200 chỉ có1user+1assistant, nội dung khớp hoàn toàn;
  anonymous history401, history không chứa bearer.
- Lần test trung gian sau nâng metadata đã trả đúng sản phẩm nhưng assertion so
  JSON serialized với chuỗi nhiều dòng sai. Sửa so trường `content` trực tiếp;
  chạy toàn bài cuối đạt, không nới assertion dữ liệu.
- Fixture chuẩn xác minh IdP thật trước tạo user/grant/department test riêng;
  cleanup exit0 và xác minh xóa DB test. Chat cleanup scoped owner, từ chối turn
  còn running. Không xóa user/grant/dữ liệu có sẵn, không reset realm hoặc native DB.
- Unit helper/chat/migration: **42 passed, 4.59s**. Ruff lint/format, diff check đạt.
  ML/RAG optional chưa bật; kết quả này chứng minh nhánh Data/chat, không chứng
  nhận ML prediction, retrieval/indexing, tenant khác hoặc production deployment.

## Chạy lại có chủ đích

Bài này gọi model thật theo cấu hình hiện có, có thể phát sinh phí. Stack phải
healthy và DB head khớp. Không chạy fixture lên profile có user đã liên kết thủ công;
fixture sẽ từ chối để tránh nhận nhầm ownership dữ liệu cần cleanup.

```powershell
.\chat-docker.cmd up

# Các lệnh sau dùng cùng project và manifest với launcher mặc định.
docker compose --project-name flowbridge-chat --env-file infra/dev/images.env --env-file infra/chat/images.env -f compose.yaml -f infra/chat/compose.yaml run --build --rm --no-deps tools
docker compose --project-name flowbridge-chat --env-file infra/dev/images.env --env-file infra/chat/images.env -f compose.yaml -f infra/chat/compose.yaml run --build --rm --no-deps browser npx playwright test --config chat.config.mjs
docker compose --project-name flowbridge-chat --env-file infra/dev/images.env --env-file infra/chat/images.env -f compose.yaml -f infra/chat/compose.yaml run --rm --no-deps tools python scripts/container_dev.py cleanup
```

Config chat là opt-in, không thuộc suite SSO mặc định. Không tự retry model khi
lỗi; cùng Idempotency-Key được giữ cho replay. Nếu bị gián đoạn, kiểm turn trước
cleanup; không xóa fixture manifest khi chưa biết kết quả.

Sau cleanup, các grant test không còn. Tài khoản sử dụng thường xuyên cần liên
kết issuer+subject và EMPLOYEE theo quy trình trong [SSO.md](SSO.md), không tự cấp
ADMIN vì tài khoản có quyền quản trị Keycloak. Khung chat hiện có trên8761;
Swagger/client API vẫn dùng được cho tích hợp trực tiếp.

Không xuất credential trong bài nghiệm thu. Một bước phụ xuất mật khẩu local đã
bị auto-review từ chối vì ngoài yêu cầu; không thực thi hoặc tìm cách thay thế.
Không có token/password trong tài liệu, ảnh, trace, video hoặc output nghiệm thu.
