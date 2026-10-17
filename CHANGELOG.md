# Changelog

## Unreleased

- Gom setup Langflow local vào `langflow/`, giữ dữ liệu/flow mẫu và thêm `start.cmd` để mở bằng nhấp đúp trên Windows.
- Khôi phục migration `0003_core_knowledge` và SQL gốc từ nhánh nhóm, gỡ lệch source/DB mà không migrate/stamp/seed. Fixture SQL/SSO kiểm chính xác Alembic heads thay hằng 0002; 19 SQL nghiệp vụ và 8 unit tooling/migration đạt, có đối chiếu catalog Core read-only.
- Thêm container development/test: multi-stage backend runtime/tooling/unit, Compose Keycloak/backend/clients và runner lifecycle/fixture/cleanup; image tag+digest pin, loopback ports, secrets DB tách khỏi IAM/browser. Đã build/chạy trên Docker Linux, 280 unit và SQL Identity pass; full SSO/business container còn cần nghiệm thu riêng sau khi gỡ lệch migration. Không tự migrate/seed hoặc tải model.
- Sửa build context thiếu migration/config và lệnh down bỏ sót client ở profile tùy chọn; down/up giữ state, hướng dẫn recovery khi restart các container chung namespace mạng.
- Thêm NumPy/rank-bm25 cùng version đã khóa vào nhóm dev để unit không cần full model extras; graph142 không đổi. Thêm kiểm profile/secret boundary/recovery/revision guard và tài liệu cài/chạy/kiểm container; giữ native SSO launcher/client loopback mặc định.

- Sửa provenance Core/draft bằng sync/checksum có kiểm semantic/shared schema; giữ canonical 03-api và wire Core 0.2.0. Identity OpenAPI bổ sung URI/correlation/Retry-After 409; URI public config được validate.
- Tách accounts/bootstrap sang SQL adapter và transactional audit/outbox do Identity sở hữu; giữ schema/lock/guard/retry. Gom OpenAPI composition/cache app/Identity/ML/RAG, từ chối collision; thêm parity/regression và SQL rollback acceptance.

- Thêm hai API Identity core khi cấu hình binding tenant/application tường minh: config public và human principal từ introspection + live DB grants; namespace OpenAPI riêng, token scope và service-account/client binding được kiểm lại. Không tự nâng ADMIN/quyền phòng ban thành quyền toàn hệ thống.
- Thêm UserContext qua IAM-issued access token và giao quyền service/user; JobContext boundary fail closed khi chưa có registry production. Profile core mới bắt buộc OTP, scope identity:read và PKCE; thêm unit/SQL/browser tests, mapping gateway tùy chọn `--include-identity` và hướng dẫn/ADR. Không migration hoặc dependency mới.

- Thêm Prediction Service bật bằng `FLOWBRIDGE_ML_ENABLED`: 19 features, context có provenance/cutoff, TabICLv2 2.2.0 và batch inference ngoài event loop; model readiness riêng, không tải checkpoint trong HTTP/startup.
- Thêm 5 endpoint `/api/v1/ml/*` và `/internal/v1/ml/*` cho scenario, ranking, chấm/lưu PO và đọc prediction/rủi ro hiện hành. Tái sử dụng IdP/role/ownership, PostgreSQL và idempotency; rủi ro PO là MAX các item còn mở, trả đúng item/deadline và trạng thái thiếu dữ liệu.
- Thêm optional dependency `ml`, CLI chuẩn bị/kiểm tra artifacts, typed HTTP client, custom component Langflow và hướng dẫn cấu hình. Giữ API AI 0.4.1 cũ/RAG; không migration hoặc scheduler/cảnh báo. Điểm ML chưa hiệu chuẩn; context synthetic không dùng production.

- Thêm `POST /internal/v1/tools/retrieve-knowledge` bật bằng `FLOWBRIDGE_RAG_DEV_ENABLED` cho loopback development/test: RAG thật trên fixture, model dùng lại theo lifespan, worker không chặn event loop, giới hạn một lượt tìm kiếm và lỗi chuẩn. Giữ API PostgreSQL và OpenAPI có xác thực khi flag tắt; không khôi phục các API sample đã bỏ trên main.
- Thêm hướng dẫn Postman/PowerShell và kiểm thử HTTP/lifecycle; chế độ này chưa dùng caller/ACL production, không tự tải model hoặc lập chỉ mục.
- Thêm RAG local bằng CLI: 6 hồ sơ Markdown, Qwen3-Embedding-0.6B pin revision, Chroma cosine + BM25/RRF, chỉ mục theo thế hệ, nguồn/version/checksum/heading/reviewer và lọc document/supplier/known_from. Reranker mặc định tắt.
- Thêm extra `rag`, lockfile, kiểm thử và tài liệu sử dụng/báo cáo; HTTP development được bổ sung ở bước tiếp theo; chưa nối Langflow, document store hoặc ACL production.
- Tích hợp Keycloak SSO thật qua SDK code+PKCE và backend introspection; thêm registry client/human/service, CA trust, CORS allowlist, directory verified-link và bootstrap transaction mặc định rollback. Thêm realm config/native local launcher, hai client browser và E2E Keycloak+HTTP+Supabase không override identity. Không khôi phục sample identity hoặc lưu mật khẩu ở DB; không có migration mới.

- Chỉ giữ API PostgreSQL/Supabase: bỏ nhánh sample, fixture runtime, API core mô phỏng, công cụ/ví dụ/test sample và dependency python-multipart. Chuyển 4 payload cần cho integration vào tests; giữ contract/auth/HTTP checks cho API database. Gateway mapping chỉ xuất API app/AI và tách nhãn admin/internal/public. Bỏ biến cấu hình FLOWBRIDGE_SAMPLE_*; cập nhật cách chạy và các dependency còn thiếu.

- Chuyển API nghiệp vụ sang PostgreSQL/Supabase khi cấu hình DSN và tắt sample: repository async, quyền theo owner/phòng ban và grant active, transaction/outbox, PR/analysis/proposal/PO/receipt/confirmation/admin persistence. Thêm migration `0002_api_idempotency` để replay request an toàn và chống gọi trùng đồng thời; giữ sample mode độc lập.
- Thêm adapter bearer introspection upstream RFC 7662; chưa cấu hình IdP thì từ chối truy cập, không giả actor. Workflow approval, provision identity, chat/model/document engine trả 503 khi chưa có adapter. Thêm suite integration Supabase chọn tường minh, test commit qua kết nối độc lập và dọn fixture riêng; không thay dữ liệu demo có sẵn.

- Tích hợp script Supplier Assistant: baseline Alembic tạo 27 bảng/293 cột, 56 FK, 83 CHECK và 3 helper cùng RLS; thêm đủ ORM mapping, wrapper seed async và báo cáo CSV. Đã áp dụng revision `0001_supplier_schema`, nạp và đọc lại 25.422 dòng trên Supabase; `alembic check` đạt.
- Thiết lập Base ORM và Alembic async dùng chung Settings/engine, SSL và tùy chọn NullPool; thêm cấu hình Supabase theo trường riêng với password được encode tự động, script `SELECT 1` và hướng dẫn migration/seed. Chưa thêm model nghiệp vụ, revision hoặc seed của nhóm.
- Gom adapter fixture nhỏ của Data/Files/Workflow/Events vào service cùng nhóm chức năng, bỏ wrapper Identity chỉ chuyển tiếp và dùng chung phân trang cho Supplier Assistant. Giữ nguyên hợp đồng API, guard quyền/revision/token và cơ chế sample không lưu dữ liệu.
- Bỏ khai báo jsonSchemaDialect tùy chọn ở ba contract và snapshot để Swagger dùng dialect mặc định OpenAPI 3.1; đồng bộ checksum, giữ nguyên endpoint và schema dữ liệu.
- Đồng bộ app/AI OpenAPI lên 0.4.1 theo schema Supplier Assistant 27 bảng: UUID local actors, PR nhiều dòng, NUMERIC(18,4), owner/department grants, AI tự lập/gửi proposal, PO schedules/receipts và confirmation revisions. Đây là thay đổi không tương thích với DTO ứng dụng 0.1.0; Open-Core 0.2.0 giữ nguyên.
- Thêm 40 operation ứng dụng và 6 AI tools sample, chung Swagger với 28 operation core. Tách admin/internal, từ chối public manual proposal; endpoint predict trả insufficient_data/score null khi chưa có model. Không tích hợp DB/IdP/workflow/email/ML thật.
- Thêm provenance/checksum, script đồng bộ snapshot app/AI, request examples và kiểm tra HTTP `--scope all`; DTO request/response dùng cùng contract và có tests cho UUID/decimal/19 X/actor/revision/null/sample guard.
- Thêm chế độ API mẫu bật tường minh trong development/test: 28 operation Identity, Data/Storage, Workflow và events/notifications/audit; response đúng hợp đồng v0.2.0, chưa persistence hoặc xác thực OIDC thật.
- Thêm multipart upload, download bytes tổng hợp và metadata/hash nhất quán; dữ liệu fixture có ID dùng chung, request ghi không thay đổi fixture.
- Thêm mapping Gateway từ contract, ví dụ gọi đủ operation, Swagger schema sample và script kiểm tra HTTP có assertion. APISIX/engine thật để giai đoạn sau.
- Từ chối sample trong production hoặc khi có DSN; thêm cấu hình giới hạn upload và HTTP/contract tests không I/O engine.
- Thêm python-multipart 0.0.32, PyYAML 6.0.3, jsonschema 4.26.0 và referencing 0.37.0 qua manifest/lockfile; đóng gói contract cùng package, ví dụ và script trong source archive.
- Thêm lỗi API chuẩn, correlation ID có kiểm tra định dạng và cô lập request; che chi tiết validation/exception nhạy cảm.
- Thêm `/_ops/live` và `/_ops/ready` tách khỏi schema nghiệp vụ; readiness có deadline cấu hình, trả 503 khi bootstrap không có DB và chỉ chứng nhận runtime backend.
- Khai báo Starlette 1.8.0 trực tiếp để dùng giao diện ASGI đã có trong graph, không đổi phiên bản package upstream.
- Thêm app factory, cấu hình có validation, lifespan quản lý HTTP client/engine và session theo request; chạy module hỗ trợ cấu hình host/port/log level.
- Thêm unit test dữ liệu mock, cấu hình và vòng đời tài nguyên; chưa tạo/kết nối DB thật theo yêu cầu của nhóm. Đóng gói test trong source archive.
- Khởi tạo manifest Python 3.12, cấu hình uv/Ruff/pytest và package tại `backend/src/flowbridge`.
- Áp dụng GPL-3.0-or-later theo lựa chọn của nhóm; giữ LICENSE GPL v3 toàn văn.
- Bổ sung template môi trường, ignore rules và cấu hình LF.
- Áp dụng quy tắc code/review/license/test/release theo thể lệ hiện tại.

Chưa có release công khai. Runtime hiện dùng Supabase và Keycloak SSO với cấu hình riêng; sample API đã bỏ. Đã có adapter ML/RAG local tùy cấu hình; workflow/email, ACL tài liệu production và nghiệm thu production vẫn còn các task tích hợp riêng.

### PostgreSQL knowledge store 2026-10-16

- Add/adopt three Core tables with immutable publication and byte-accurate chunk guards.
- Add pgvector exact-cosine retrieval with existing BM25/RRF, per-version atomic generations, live authorization and stable citations.
- Wire synchronous knowledge tools to verified AI_SERVICE and database JobContext; retain the separate fixture development mode.
- Add operator import/index/inspect commands, setup documentation and bounded SQL validation evidence.
