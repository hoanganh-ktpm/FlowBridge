# Phát triển và tích hợp

## Phân chia công việc

| Khối | Công việc ban đầu | Đầu ra tiếp theo |
|---|---|---|
| Business | Schema ba database, migration và seed tổng hợp | Sales/Warehouse/Logistics APIs, transaction, idempotency và business authorization |
| DX-OS Data Core | Core schema, models, Application/Source Registry | Data Products/Contracts, Governance, Hasura provisioning và quyền đọc |
| Contracts & Platform | Điều phối hợp đồng API, cấu hình môi trường | Identity, Gateway, Flowable, audit và ghép hệ thống |

Người điều phối contracts tổng hợp specification; chủ sở hữu nghiệp vụ/dữ liệu xác nhận DTO, trạng thái và quyền. Sau bước thiết kế, mỗi khối bàn giao implementation và kiểm thử có thể sử dụng được.

## Contracts trước integration

- Business APIs: IDs, DTO, trạng thái, lỗi, Idempotency-Key, reservation/release, shipment/handover.
- Data operations: operation_ref, variables, context, kết quả và lỗi.
- Identity context: danh tính đã xác minh, human/service, application/department binding và actor workflow.
- Data Product/Grant: owner, cột và phạm vi đọc, thời hạn, cấp/thu hồi và trạng thái áp dụng.

Nguồn chuẩn nằm tại contracts. VerifiedIdentity hiện có chưa thay thế toàn bộ mô hình quyền và department binding cần xây tiếp.

## Phát triển độc lập

Business APIs dùng database fixture và identity mock. Data Core dùng nguồn PostgreSQL thử nghiệm và adapter mock khi cần. Workflow dùng mock Business APIs/Data Reader theo cùng contract. Portal dùng dữ liệu tổng hợp và API boundary riêng.

Mocks chỉ nằm trong test/development phù hợp; không tạo endpoint giả danh để bỏ qua quyền. Các khối không import repository hoặc đọc database nội bộ của nhau.

## Các mốc bàn giao

1. Contracts/fixtures thống nhất; từng khối có kiểm thử độc lập và cấu hình môi trường rõ.
2. Đăng ký nguồn → publish một Data Product → đọc đúng quyền → thu hồi quyền được kiểm tra thực tế.
3. Order Fulfillment qua APIs thật, User Tasks đúng quyền, nhánh thiếu hàng và xử lý lỗi sau reservation.
4. Agent sử dụng một registered Tool với quyền người gọi và audit; sau đó mở rộng registry/runtime.
5. Portal gọi API mới, deployment có thể dựng lại và E2E xuyên các khối.

Mỗi mốc có assertion và bằng chứng kiểm tra. Không coi scaffold, preview hoặc mock tests là runtime tích hợp hoàn chỉnh.

## Phân quyền và migration

Identity xác minh caller; Core quản lý quyền chia sẻ; Hasura thực thi quyền đọc; Business APIs kiểm quyền ghi và nghiệp vụ; Workflow Adapter kiểm quyền task. Reservation được kiểm trong transaction và phải idempotent.

Core DB có một migration chain tại backend/app/core/db/migrations. Mỗi domain quản lý migrations riêng. Thay đổi contract, baseline, root lockfile và deployment được review cùng người tích hợp.

Agent và Portal cần người sở hữu cụ thể khi lập kế hoạch triển khai; có thể chuyển người đã bàn giao Business APIs sang các phần này để phân bổ tải công việc.
