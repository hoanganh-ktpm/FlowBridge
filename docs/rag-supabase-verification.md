# Kết quả kiểm chứng RAG trên Supabase

Cập nhật ngày 16/10/2026, sau khi người dùng cấu hình kết nối local. Mốc kiểm thử: 2026-10-16T11:07:49.830430+00:00.

## Trạng thái đã xác minh

- Kết nối PostgreSQL SSL qua Supabase session pooler thành công. Credential chỉ lưu trong `.env` đã được Git bỏ qua; không chứa trong tài liệu hoặc bằng chứng kiểm thử.
- Đã áp dụng migration `0003_core_knowledge`, tiếp nhận ba bảng Core có sẵn. pgvector thực tế `0.8.2`, schema `extensions`. Có đủ 37 cột, RLS và hai trigger bảo vệ source/chunk.
- Core hiện có **6 documents, 6 document_versions và 96 document_chunks/vector**. Mỗi supplier có một hồ sơ; reviewer và supplier khớp bộ seed đã có.
- Đã đối chiếu số dòng của 28 bảng public nghiệp vụ/vận hành trước và sau: không thay đổi. Đây là kiểm tra số dòng, không phải checksum toàn bộ dữ liệu nghiệp vụ.
- Import lại giữ nguyên sáu version ID, không sinh version thừa. Index lại giữ 96 chunk; lần đối soát sau khi index hoàn tất mất 1.507 giây, không cần embedding lại.

## Kiểm thử

Bốn integration test `asyncpg/pgvector` đã chạy trực tiếp với Supabase: **4 passed**. Dữ liệu riêng của test nằm trong outer transaction rồi rollback, không để lại hồ sơ thử. Các ca bao gồm index/retry/search/resolve, rollback generation lỗi, giữ reference sau đổi profile và loại source bị xóa.

Qwen embedding revision đã pin chạy cục bộ, kết hợp exact cosine pgvector + BM25 + RRF. Tám câu hỏi mẫu đều có mục nguồn kỳ vọng ở rank 1; DTO, supplier isolation, reviewer và trích dẫn UTF-8/checksum đều qua kiểm tra. Đây là smoke test trên dữ liệu giả lập, không phải benchmark chất lượng tổng quát. Thời gian từng ca trong JSON gồm cả bước kiểm tra lại các citation, không phải độ trễ endpoint đơn thuần.

Ví dụ: câu hỏi PO DEMO-PO-00212 trả đúng đoạn ghi hoàn tất sau hạn gốc **26 ngày**, kèm người nhận xét và reference tới version/chunk thật trong Core.

Cutoff trước publication đầu tiên trả kết quả rỗng. Không backdate known_from theo ngày sự kiện trong Markdown; vì vậy job cũ trước lần import này sẽ chưa thấy các tài liệu. Khi test API cần job phù hợp được tạo sau publication.

## HTTP và việc còn lại

Startup với database thật, readiness và OpenAPI đều thành công. Endpoint retrieve có trong Swagger, truy cập không bearer bị chặn 401. Hai flag local `FLOWBRIDGE_RAG_POSTGRES_ENABLED` và `FLOWBRIDGE_DOCS_ENABLED` đã bật. Không để lại server HTTP nền từ các phép kiểm tra này.

**IdP chưa được cấu hình**, nên chưa kiểm chứng HTTP có bearer hợp lệ và JobContext xuyên suốt. Không bypass authentication. Cần kết nối issuer/audience/introspection/client credential và identity/grant AI_SERVICE, sau đó thử hai endpoint bằng task UUID hợp lệ.

Code và các báo cáo hiện vẫn ở nhánh minnhungdev_features trên máy, chưa commit/push trong lần làm này.

## Bằng chứng

- `examples/rag/supabase_smoke_results.json`: tám truy vấn, index retry, import retry, count và cutoff.
- `examples/rag/supabase_http_readiness.json`: startup/readiness/Swagger và anonymous denial.
- `.cache/rag/supabase-before-core0003.json`: snapshot schema Core trước migration, chỉ lưu local.
- `.cache/rag/supabase-business-counts-before.json`: số dòng nghiệp vụ trước nâng cấp, chỉ lưu local.

Chạy lại CLI hoặc bật backend theo [hướng dẫn](rag-postgres-setup.md).

## Đối chiếu sau khi đồng bộ main — 17/10/2026

Nhánh `minnhungdev_features` đã fast-forward từ `ae20b28` lên `a7925d2`, trùng `origin/main` tại lần fetch ngày 16/10/2026. Khôi phục toàn bộ phần pgvector local sau cập nhật; giữ cả Keycloak directory, ML runtime và PostgreSQL knowledge service. Đã giải quyết xung đột ở CHANGELOG, checksum contract, main, router và manifest đóng gói; checksum được tính lại từ hai contract sau ghép.

- Suite mặc định: **250 passed, 25 deselected**. Chạy từ thư mục tạm, bỏ biến FLOWBRIDGE trong process test để không nạp `.env` Supabase thật.
- Bổ sung hai ca bật đồng thời RAG và ML, kiểm tra shutdown và cleanup khi schema probe thất bại. Toàn bộ file HTTP RAG sau bổ sung: **12 passed**, bao gồm hai ca mới. Đây là kiểm thử runtime với mock; không chứng nhận inference ML thật.
- Chạy lại riêng bốn integration test pgvector có outer rollback trên Supabase: **4 passed**.
- Startup thật sau ghép: readiness **200**, OpenAPI **200**, anonymous retrieve **401**, knowledge service được khởi tạo. Core vẫn có **6/6/96** dòng theo thứ tự documents/versions/chunks; Alembic vẫn `0003_core_knowledge` và không chạy thêm migration trong lần đồng bộ.
- Ruff, kiểm tra lockfile và build wheel/sdist bằng môi trường build cách ly đều thành công. Không còn file Git ở trạng thái unmerged.

Bản backup trước ghép nằm ngoài repo tại `DataMining/repo-sync-backups/20261016-235521`; stash `rag-pgvector-before-main-sync-20261016` cũng được giữ để đối chiếu. Credential `.env` vẫn được ignore, không nằm trong backup source hoặc gói build. Bằng chứng startup mới nằm ở `.cache/rag/main-sync-readiness.json`.

Local chưa cấu hình IdP nên chưa chạy luồng token Keycloak thật → AI_SERVICE → JobContext → truy hồi. Hướng dẫn setup đã bổ sung registry service client theo SSO mới. Không commit/push phần pgvector trong lần đồng bộ này.
