# Core database — single-enterprise P0

Core phục vụ **một doanh nghiệp**, theo thiết kế Core v0.2 và API Contracts 0.2.0 cùng quyết định IT quản trị DX-OS. Không có bảng organizations hoặc org_id trong schema Core hiện hành.

## Phạm vi hiện tại

Head **core_0004** có **12 bảng**: 11 bảng P0 của thiết kế v0.2 và core_command_receipts phục vụ HTTP idempotency. Agent/Operation/Tool, lịch sử schema, workflow references và lineage P1/P2 chưa tạo bảng trong mốc này; bổ sung bằng revision mới khi có implementation.

- Principal là người/service được đối chiếu issuer + subject, không lưu mật khẩu hoặc token.
- Department và membership mô tả nhân sự; STAFF/OWNER không phải quyền quản trị DX-OS.
- Application có đơn vị phụ trách; không đồng nghĩa mọi người dùng ứng dụng thuộc đơn vị đó.
- Product gắn nguồn; owner suy ra qua Source → Application → Department, không nhân bản owner_department_id.
- Contract versions độc lập với danh tính Product; FK kép bảo đảm version thuộc đúng Product.
- P0 access request/grant chỉ cấp theo phòng ban và SELECT/ALL; principal consumer để NULL. Quyền execute/nghiệp vụ không suy ra từ grant.
- Publication jobs dùng target/revision, bounded attempts, lease và due indexes.
- Audit giữ actor/initiator/correlation; receipts chỉ lưu phản hồi metadata của lệnh, không lưu kết quả đọc dữ liệu nghiệp vụ.

## Data dictionary

PK là khóa chính; NN là bắt buộc; NULL cho phép vắng. UUID do service hoặc database tạo; thời gian dùng timestamptz. Các trường actor tham chiếu principals; bootstrap chỉ được dùng NULL khi có kiểm soát. Default updated_at không tự cập nhật khi UPDATE: adapter/service phải ghi thời gian mới.

### core_command_receipts

| Cột | Kiểu | Quy tắc |
|---|---|---|
| namespace | text | PK; NN |
| fingerprint | text | NN |
| status | integer | NN |
| body | jsonb | NN |
| location | text | NULL |
| created_at | timestamptz | NN |
| expires_at | timestamptz | NN |

### principals

| Cột | Kiểu | Quy tắc |
|---|---|---|
| id | char(32) | PK; NN |
| issuer | text | NN |
| subject | text | NN |
| kind | text | NN |
| display_name | text | NULL |
| service_client_id | text | NULL |
| application_id | char(32) | NULL; FK applications.id |
| platform_role | text | NN |
| status | text | NN |
| created_at | timestamptz | NN |
| updated_at | timestamptz | NN |
| created_by | char(32) | NULL; FK principals.id |
| updated_by | char(32) | NULL; FK principals.id |
| revision | bigint | NN |

### audit_events

| Cột | Kiểu | Quy tắc |
|---|---|---|
| id | char(32) | PK; NN |
| event_key | text | NULL |
| occurred_at | timestamptz | NN |
| recorded_at | timestamptz | NN |
| actor_principal_id | char(32) | NULL; FK principals.id |
| actor_kind | text | NN |
| initiator_principal_id | char(32) | NULL; FK principals.id |
| action | text | NN |
| resource_type | text | NN |
| resource_ref | text | NN |
| outcome | text | NN |
| request_id | text | NN |
| workflow_run_ref | text | NULL |
| diff_json | jsonb | NULL |
| error_code | text | NULL |
| content_hash | text | NULL |

### departments

| Cột | Kiểu | Quy tắc |
|---|---|---|
| id | char(32) | PK; NN |
| code | text | NN |
| name | text | NN |
| description | text | NULL |
| keycloak_group_ref | text | NULL |
| status | text | NN |
| created_at | timestamptz | NN |
| updated_at | timestamptz | NN |
| created_by | char(32) | NULL; FK principals.id |
| updated_by | char(32) | NULL; FK principals.id |
| revision | bigint | NN |

### applications

| Cột | Kiểu | Quy tắc |
|---|---|---|
| id | char(32) | PK; NN |
| department_id | char(32) | NN; FK departments.id |
| app_key | text | NN |
| name | text | NN |
| description | text | NULL |
| business_api_base_url | text | NULL |
| status | text | NN |
| created_at | timestamptz | NN |
| updated_at | timestamptz | NN |
| created_by | char(32) | NULL; FK principals.id |
| updated_by | char(32) | NULL; FK principals.id |
| revision | bigint | NN |

### department_memberships

| Cột | Kiểu | Quy tắc |
|---|---|---|
| id | char(32) | PK; NN |
| principal_id | char(32) | NN; FK principals.id |
| department_id | char(32) | NN; FK departments.id |
| membership_role | text | NN |
| status | text | NN |
| expires_at | timestamptz | NULL |
| created_at | timestamptz | NN |
| updated_at | timestamptz | NN |
| created_by | char(32) | NULL; FK principals.id |
| updated_by | char(32) | NULL; FK principals.id |
| revision | bigint | NN |

### data_sources

| Cột | Kiểu | Quy tắc |
|---|---|---|
| id | char(32) | PK; NN |
| application_id | char(32) | NN; FK applications.id |
| source_key | text | NN |
| db_type | text | NN |
| host | text | NN |
| port | integer | NN |
| database_name | text | NN |
| db_user | text | NN |
| secret_ref | text | NN |
| ssl_mode | text | NN |
| hasura_source_name | text | NN |
| status | text | NN |
| last_checked_at | timestamptz | NULL |
| created_at | timestamptz | NN |
| updated_at | timestamptz | NN |
| created_by | char(32) | NULL; FK principals.id |
| updated_by | char(32) | NULL; FK principals.id |
| revision | bigint | NN |

### data_products

| Cột | Kiểu | Quy tắc |
|---|---|---|
| id | char(32) | PK; NN; FK data_contract_versions.data_product_id |
| data_source_id | char(32) | NN; FK data_sources.id |
| product_key | text | NN |
| name | text | NN |
| description | text | NULL |
| schema_name | text | NN |
| object_name | text | NN |
| object_type | text | NN |
| classification | text | NN |
| active_contract_id | char(32) | NULL; FK data_contract_versions.id |
| status | text | NN |
| created_at | timestamptz | NN |
| updated_at | timestamptz | NN |
| created_by | char(32) | NULL; FK principals.id |
| updated_by | char(32) | NULL; FK principals.id |
| revision | bigint | NN |

### data_contract_versions

| Cột | Kiểu | Quy tắc |
|---|---|---|
| id | char(32) | PK; NN |
| data_product_id | char(32) | NN; FK data_products.id |
| version | text | NN |
| contract_json | jsonb | NN |
| status | text | NN |
| published_at | timestamptz | NULL |
| published_by | char(32) | NULL; FK principals.id |
| created_at | timestamptz | NN |
| updated_at | timestamptz | NN |
| created_by | char(32) | NULL; FK principals.id |
| updated_by | char(32) | NULL; FK principals.id |
| revision | bigint | NN |

### access_requests

| Cột | Kiểu | Quy tắc |
|---|---|---|
| id | char(32) | PK; NN |
| data_product_id | char(32) | NN; FK data_products.id; FK data_contract_versions.data_product_id |
| contract_version_id | char(32) | NN; FK data_contract_versions.id |
| requested_by | char(32) | NN; FK principals.id |
| consumer_department_id | char(32) | NULL; FK departments.id |
| consumer_principal_id | char(32) | NULL; FK principals.id |
| requested_scope_json | jsonb | NN |
| reason | text | NN |
| status | text | NN |
| requested_at | timestamptz | NN |
| decided_by | char(32) | NULL; FK principals.id |
| decided_at | timestamptz | NULL |
| decision_note | text | NULL |
| revision | bigint | NN |
| created_at | timestamptz | NN |
| updated_at | timestamptz | NN |
| created_by | char(32) | NULL; FK principals.id |
| updated_by | char(32) | NULL; FK principals.id |

### access_grants

| Cột | Kiểu | Quy tắc |
|---|---|---|
| id | char(32) | PK; NN |
| data_product_id | char(32) | NN; FK data_contract_versions.data_product_id; FK data_products.id |
| contract_version_id | char(32) | NN; FK data_contract_versions.id |
| access_request_id | char(32) | NULL; FK access_requests.id |
| consumer_department_id | char(32) | NULL; FK departments.id |
| consumer_principal_id | char(32) | NULL; FK principals.id |
| scope_json | jsonb | NN |
| enforcement_role | text | NN |
| desired_revision | bigint | NN |
| applied_revision | bigint | NN |
| status | text | NN |
| expires_at | timestamptz | NULL |
| granted_by | char(32) | NN; FK principals.id |
| granted_at | timestamptz | NN |
| revoked_by | char(32) | NULL; FK principals.id |
| revoked_at | timestamptz | NULL |
| updated_at | timestamptz | NN |

### publication_jobs

| Cột | Kiểu | Quy tắc |
|---|---|---|
| id | char(32) | PK; NN |
| job_type | text | NN |
| data_source_id | char(32) | NULL; FK data_sources.id |
| data_product_id | char(32) | NULL; FK data_products.id |
| access_grant_id | char(32) | NULL; FK access_grants.id |
| target_revision | bigint | NN |
| idempotency_key | text | NN |
| status | text | NN |
| attempt_count | integer | NN |
| max_attempts | integer | NN |
| next_attempt_at | timestamptz | NN |
| locked_by | text | NULL |
| lease_expires_at | timestamptz | NULL |
| last_error_code | text | NULL |
| last_error_message | text | NULL |
| requested_by | char(32) | NULL; FK principals.id |
| created_at | timestamptz | NN |
| updated_at | timestamptz | NN |
| correlation_id | text | NN |

## Constraints và ranh giới thực thi

Database kiểm NOT NULL, PK/UNIQUE/FK, trạng thái, revision và các JSON object cơ bản. Quy tắc nổi bật:

- Human không có application/service_client binding; service bắt buộc client ID, có thể không thuộc ứng dụng nếu là service lõi.
- Principal issuer/subject và issuer/service client là duy nhất theo profile. Không tự cấp PLATFORM_ADMIN từ token groups.
- Published/retired contract cần actor/time; active Product cần contract pointer cùng Product.
- Một live grant mỗi Product/phòng ban; applied_revision không vượt desired_revision, ACTIVE phải bằng nhau.
- Scope có action SELECT, danh sách cột và row_scope ALL. Kiểm tên cột/subset/contract canonical sâu vẫn thuộc service.
- Job có đúng target phù hợp job_type; RUNNING cần lease; target/type/revision và idempotency key chống enqueue trùng.
- Receipt có namespace actor/method/path/key, fingerprint, HTTP status/body/location và retention tối thiểu 7 ngày.

Schema không tự kiểm chứng Hasura, xác minh OIDC, kiểm chữ ký, thực thi expiry, nhận job hoặc cập nhật metadata engine. Không tuyên bố đã có PostgreSQL CoreStore adapter. Adapter cần map UUID/datetime và các projection nội bộ; enforcement_role của grant được tạo bởi trusted provisioning policy, không nhận từ caller. Publication correlation_id có cột riêng, không còn phải suy từ audit.

Tài khoản runtime cần được cấp quyền SQL tối thiểu; audit append-only và contract published bất biến phải được bảo vệ trong application policy/SQL grants hoặc trigger triển khai sau. Các CHECK hiện tại không thay thế những policy đó. Không dùng tài khoản migrate làm credential đọc Hasura.

## Chuyển đổi schema cũ

core_0001 → core_0003 là lịch sử gói bàn giao 22 bảng multi-organization, giữ nguyên để tái lập lịch sử. core_0004 bỏ metadata cũ rồi tạo schema P0 trên. Đây là **destructive replacement**, không chuyển đổi danh tính hoặc grant tự động.

Migration từ chối Core có dữ liệu nếu chưa được cho phép tường minh. Công cụ realign-core chỉ chấp nhận dxos_core_db độc lập, xác minh tên database/revision/table set, khóa bảng, lưu backup cục bộ rồi upgrade/check trong cùng transaction. Không DROP DATABASE, DROP SCHEMA CASCADE hoặc thay đổi domain. Downgrade chỉ khôi phục cấu trúc cũ, không khôi phục dữ liệu đã xóa; dữ liệu cần phục hồi bằng quy trình được review từ backup.

Các cột org_id đang có trong ba database nghiệp vụ được giữ nguyên để không phá dữ liệu/FK và snapshot bàn giao. Chúng là phạm vi tổ chức nội bộ của bộ demo một doanh nghiệp, không chứng minh Core hỗ trợ multi-tenant; không có FK tới Core organizations.

Xem [Database setup](README.md) và [Core P0 runtime](../CORE-P0.md).
