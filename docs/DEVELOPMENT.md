# Phát triển và tích hợp

## Môi trường hiện hành

Data Workspace có service riêng tại port 18090 và tích hợp vào Portal live mặc định.
Core/worker giữ port 18000; Vite proxy riêng `/api/v1/data-workspace` tới 18090.
Các lệnh `python -m scripts.data_workspace` chuẩn bị cấu hình local, chạy process và
quản lý migration riêng Tầng 2, không migration khi startup. Xem
[hướng dẫn và ma trận kiểm thử](DATA-WORKSPACE.md).

Demo mới dùng [PostgreSQL 16 Docker local](database/README.md): Sales, Warehouse, Logistics, Core và Hasura metadata tách database. Core/worker chạy Python .venv; Hasura local ở 18082. [Profile cũ](DOCKER.md) giữ Keycloak ở 18443; [Workflow](WORKFLOW.md) là profile Flowable độc lập. Secrets/TLS bị Git ignore; local CLI không đọc DSN Supabase cũ. Engine health không thay thế kiểm thử publication/read/revoke hoặc Workflow E2E.

## Phạm vi implementation

| Khối | Đã có | Phần cần triển khai |
|---|---|---|
| Business | Schema ba database, migration, seed tổng hợp và vertical slice APIs Sales/Warehouse/Logistics với transaction, idempotency, quyền theo subject/org | Các API P1 ngoài phạm vi order fulfillment tối thiểu |
| DX-OS Data Core | PostgreSQL runtime, Registry, Data Products/Contracts, Governance, Hasura provisioning và quyền đọc P0 | Agent/Tool runtime và mở rộng các boundary P1 |
| Contracts & Platform | API contracts, Identity/HTTP adapters, Workflow REST/actions/events và Business Workflow E2E trên Flowable/PostgreSQL local | Delegation production, APISIX runtime và audit integration mở rộng |

Mỗi thay đổi cần giữ DTO, trạng thái và quyền nhất quán với contracts, kèm implementation và kiểm thử có thể chạy độc lập.

## Contracts trước integration

- Business APIs: IDs, DTO, trạng thái, lỗi, Idempotency-Key, reservation/release, shipment/handover.
- Data operations: operation_ref, variables, context, kết quả và lỗi.
- Identity context: danh tính đã xác minh, human/service, application/department binding và actor workflow.
- Data Product/Grant: owner, cột và phạm vi đọc, thời hạn, cấp/thu hồi và trạng thái áp dụng.

Nguồn chuẩn nằm tại contracts. VerifiedIdentity hiện có chưa thay thế toàn bộ mô hình quyền và department binding cần xây tiếp.

## Phát triển độc lập

Business APIs dùng database fixture và identity mock. Data Core dùng nguồn PostgreSQL thử nghiệm và adapter mock khi cần. Workflow dùng mock Business APIs/Data Reader theo cùng contract. Portal dùng dữ liệu tổng hợp và API boundary riêng.

Mocks chỉ nằm trong test/development phù hợp; không tạo endpoint giả danh để bỏ qua quyền. Các khối không import repository hoặc đọc database nội bộ của nhau.

## Tiêu chí tích hợp

1. Contracts/fixtures thống nhất; từng khối có kiểm thử độc lập và cấu hình môi trường rõ.
2. Đăng ký nguồn → publish một Data Product → đọc đúng quyền → thu hồi quyền được kiểm tra thực tế.
3. Order Fulfillment qua APIs thật, User Tasks đúng quyền, nhánh thiếu hàng và xử lý lỗi sau reservation.
4. Agent sử dụng một registered Tool với quyền người gọi và audit; sau đó mở rộng registry/runtime.
5. Portal gọi API mới, deployment có thể dựng lại và E2E xuyên các khối.

Mỗi mốc có assertion và bằng chứng kiểm tra. Không coi scaffold, preview hoặc mock tests là runtime tích hợp hoàn chỉnh.

## Phân quyền và migration

Identity xác minh caller; Core quản lý quyền chia sẻ; Hasura thực thi quyền đọc; Business APIs kiểm quyền ghi và nghiệp vụ; Workflow Adapter kiểm quyền task. Reservation được kiểm trong transaction và phải idempotent.

Quản trị Core do IT với PLATFORM_ADMIN thực hiện xuyên phòng ban, gồm publish và cấp/thu quyền, không cần OWNER của phòng ban phê duyệt. Nhân viên/phụ trách phòng ban chỉ có quyền nghiệp vụ theo từng Business API và quyền đọc được IT cấp; membership STAFF/OWNER không cấp quyền quản trị Core. Admin role không tự thay thế grant đọc hoặc quyền ghi ở Business API. Contracts khai báo x-authorization-policy để các nhóm giữ đúng ba boundary này khi triển khai.

Core DB có một migration chain tại backend/app/core/db/migrations. Mỗi domain quản lý migrations riêng. Thay đổi contract, baseline, root lockfile và deployment cần kiểm tra tương thích với các module sử dụng.
