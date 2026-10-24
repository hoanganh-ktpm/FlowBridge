# Docker integration environment

Demo mới dùng [PostgreSQL 16 local](database/README.md), project `flowbridge-local`, năm DB riêng và Hasura ở 18082. Các bước dưới đây lưu lại profile Supabase cũ; không chạy chúng để dựng demo mới. Keycloak hiện có được tái sử dụng cho smoke test local mà không sửa realm.

## Phạm vi

Profile development/test từ 23/10/2026: Keycloak và Hasura chạy Docker; Core API/worker vẫn dùng Python .venv để phát triển độc lập. Không cần JDK hoặc Keycloak portable trên Windows. Không phải production deployment; không bật Portal, Business APIs hoặc Workflow trong profile này.

| Thành phần | Runtime | Dữ liệu |
|---|---|---|
| Keycloak 26.8.1 | Docker, HTTPS localhost:18443 | H2 dev-file trong volume riêng; realm flowbridge-it |
| Hasura v2.48.16 CE | Docker, loopback 127.0.0.1:18080 | Metadata do engine quản lý |
| Hasura metadata PostgreSQL 17.11 | Supabase hiện có | Schema hdb_catalog trong database postgres; role riêng |
| Core API/worker | Python .venv | dxos_core_db trên Supabase hiện tại |
| Sales/Warehouse/Logistics | Database hiện có | Supabase; không sao chép sang container |

Compose ở infra/docker-compose.yml, project flowbridge-integration; chỉ có hai service, images pin cả version/digest. Không tạo PostgreSQL container hoặc database Supabase mới. Metadata engine tách khỏi Core/nghiệp vụ bằng hdb_catalog trong database postgres hiện có. Phương án này đã được kiểm chứng với Supabase session pooler 5432 và TLS; không dùng transaction pooler 6543 cho metadata/Core locks.

## Khởi động

Chạy từ repository root, Docker Desktop phải dùng Linux engine:

```powershell
.\.venv\Scripts\python.exe -m scripts.docker_env
.\.venv\Scripts\python.exe -m scripts.e2e_environment metadata --allow-supabase-test-writes
docker compose --env-file .env.docker -f infra/docker-compose.yml config --quiet
docker compose --env-file .env.docker -f infra/docker-compose.yml up -d
docker compose --env-file .env.docker -f infra/docker-compose.yml ps
.\.venv\Scripts\python.exe -m scripts.check_docker
```

Generator đọc DXOS_CORE_URL hiện có trong .env nhưng không ghi đè .env, không kết nối/migrate/reset database. Nó tạo .env.docker và .tmp/docker-integration chứa secrets, certificate/key, rendered test realm và core.env, tất cả bị Git ignore. Cần OpenSSL đi kèm Git cho Windows để tạo certificate thử bảy ngày; không cài certificate vào trust store hệ thống. Nếu runtime state đã tồn tại, giữ nguyên secrets/realm; không tự rotate hoặc reset volume.

Lệnh metadata là thao tác ghi riêng, chỉ chạy khi được quản trị viên phê duyệt: xác minh database postgres và pgcrypto có sẵn trong extensions, tạo hdb_catalog và một LOGIN không phải superuser, chỉ cấp CONNECT postgres, USAGE/CREATE hdb_catalog và USAGE extensions. Hasura quản lý bảng nội bộ trong schema này; không chạy migration Core. Nếu phát hiện hdb_catalog không có ledger sở hữu tại máy, script dừng thay vì tiếp quản hoặc xóa. Giữ an toàn các file local để tái sử dụng tài khoản và secrets.

Realm thử dựa trên infra/keycloak/realm.template.json, có test users/client riêng cho integration. Password grant chỉ bật ở client flowbridge-e2e của realm thử, không sửa Portal client hoặc realm đang dùng. Không công khai port engine hoặc dùng tài khoản thử cho production. Hasura Metadata API cần admin secret; GraphQL cần internal JWT riêng, không nhận role từ client tùy ý. Console/telemetry/CORS tắt trong profile này.

## Core API và worker

Các biến chính được generator/runner render: FLOWBRIDGE_CORE_DATABASE_URL, FLOWBRIDGE_CORE_SOURCE_SCOPES; FLOWBRIDGE_OIDC_ISSUER, FLOWBRIDGE_OIDC_AUDIENCE, FLOWBRIDGE_OIDC_INTROSPECTION_URL, FLOWBRIDGE_OIDC_CLIENT_ID, FLOWBRIDGE_OIDC_CLIENT_SECRET, FLOWBRIDGE_OIDC_ALLOWED_CLIENTS, FLOWBRIDGE_OIDC_CA_BUNDLE; FLOWBRIDGE_HASURA_ENDPOINT, FLOWBRIDGE_HASURA_ADMIN_SECRET, FLOWBRIDGE_HASURA_ALLOWED_SOURCES, FLOWBRIDGE_HASURA_ALLOWED_DATABASE_HOSTS, FLOWBRIDGE_HASURA_SECRET_BINDINGS; FLOWBRIDGE_CORE_HASURA_JWT_SIGNING_KEY, FLOWBRIDGE_CORE_VERIFIED_HASURA_AUTH_PROFILE. Hasura nhận HASURA_GRAPHQL_METADATA_DATABASE_URL, FB_E2E_WAREHOUSE_URL, FB_E2E_SALES_URL từ hasura.env; .env.docker chứa FLOWBRIDGE_DOCKER_RUNTIME_DIR, FLOWBRIDGE_KEYCLOAK_ADMIN_PASSWORD, FLOWBRIDGE_HASURA_ADMIN_SECRET, FLOWBRIDGE_HASURA_JWT_CONFIG. Không chia sẻ các giá trị đã render.

```powershell
.\.tools\uv\bin\uv.exe run --locked --env-file .tmp/docker-integration/core.env uvicorn app.core.runtime:create_app --factory --host 127.0.0.1 --port 8000
```

Sau khi cấu hình source readers, secret bindings/scopes, principal bindings và xác minh JWT/ingress, worker chạy ở terminal riêng với cùng file:

```powershell
.\.tools\uv\bin\uv.exe run --locked --env-file .tmp/docker-integration/core.env python -m app.core.runtime worker
```

Không tự tạo principal/admin trong Core DB, đăng ký nguồn hoặc cấp grant khi dựng Docker. Reader mặc định vẫn tắt qua FLOWBRIDGE_CORE_VERIFIED_HASURA_AUTH_PROFILE=false cho tới khi kiểm profile thực tế. Runtime Core nạp CA_BUNDLE cho HTTPS Keycloak, không dùng verify=false. Các source DSN phải được cấp cả ở Core lẫn environment Hasura trước khi dùng from_env; không dùng migration/admin DSN làm nguồn Hasura. Không chạy suite tạo database tạm khi đang kiểm trên Supabase hiện tại.

## Real integration và E2E trên database hiện có

Yêu cầu .venv đã cài dependencies dev/database, Docker Desktop Linux engine và .env có DXOS_ADMIN_URL, DXOS_CORE_URL, DXOS_WAREHOUSE_URL, DXOS_SALES_URL, DXOS_LOGISTICS_URL. Không đưa giá trị các biến vào source/log. Bộ test chỉ chạy khi được chỉ định, không nằm trong suite offline mặc định.

```powershell
.\scripts\test_e2e.ps1 -AllowSupabaseTestWrites -PrepareMetadata -ApproveTableReaders
```

Runner thực hiện lần lượt Keycloak/Core integration, Hasura JWT/reader integration, sau đó E01–E09; dùng HTTP thật, production composition, Python native và Supabase thật, không identity override/mock. Kiểm tra IT, Sales, principal chưa đăng ký/DISABLED, token sai/hết hạn và giả role. Token hết hạn được cấp thật bởi client thử, TTL client được phục hồi rõ ràng về 600 giây trong finally. Profile này chỉ dành cho development, không phải cấu hình đăng nhập production.

Trước ghi fixture: xác minh bốn database độc lập, migration Core, nguồn Warehouse/Sales không RLS, SKU/UUID chưa tồn tại; lưu fingerprints toàn bộ bảng public nghiệp vụ và ledger tài nguyên. Tạo đúng một product fixture; bootstrap principal bằng subject/issuer Keycloak đã introspect và membership riêng, không tự coi tên user/group là quyền. Manifest import vẫn DRAFT, không tự publish/grant.

Reader mặc định bootstrap với column SELECT. Hasura v2 thực tế sinh SQL có SELECT *, vì vậy column-only reader bị PostgreSQL từ chối 42501. Cờ -ApproveTableReaders là phê duyệt riêng để cấp SELECT toàn hai bảng public.products (Warehouse), public.customers (Sales) cho đúng hai role test; không cấp ghi/DDL. **Các tài khoản PostgreSQL này đọc được mọi cột hai bảng**, kể cả cột Core/Hasura không công bố. Core và Hasura vẫn kiểm contract/grant cho consumer; đây không phải column isolation ở database account. Không tự mở quyền bảng nếu chưa được chấp thuận; môi trường production cần đánh giá read model/view và uniqueness evidence phù hợp.

E07 kiểm chứng cập nhật trường name của catalog fixture từ “DXOS E2E source 20” sang “DXOS E2E source 35” và đọc mới từ Warehouse, không sao chép sang Sales. Đây không phải nghiệm thu available_quantity NUMERIC; không nới scalar profile để ép inventory test qua.

Ledger, receipts và runtime logs nằm trong .tmp/docker-integration/runs/<run-id>, có thể chứa thông tin nhạy cảm, không phát hành. Nếu bị gián đoạn, không tạo fixture mới chồng lên:

```powershell
.\scripts\test_e2e.ps1 -AllowSupabaseTestWrites -ResumeCurrentRun
```

Các bước đã ghi nhận trong cùng ledger không được giả chạy lại; dùng run mới sau cleanup để nghiệm thu mới. Worker tự reclaim lease/retry, không sửa job status bằng SQL. GET resource/status dùng snapshot REPEATABLE READ, READ ONLY và không FOR UPDATE để không tranh row lock với Worker; command/worker vẫn SERIALIZABLE và giữ CAS/locking. Heartbeat và execution của cùng Worker mở guard transaction tuần tự để không tự gây serialization conflict; heartbeat vẫn chạy, việc claim giữa các process vẫn dùng database locking/fencing. Polling có timeout hữu hạn và xử lý 409 từ tranh chấp tạm thời. Fault test chỉ pause/unpause container Hasura đúng project, chạy hai Worker native để kiểm claim/retry, tạo metadata conflict trên source test rồi khôi phục mapping và publish qua API.

Cleanup có thể gọi riêng:

```powershell
.\.venv\Scripts\python.exe -m scripts.e2e_cleanup .tmp/docker-integration/runs/<run-id> --allow-supabase-test-writes
```

Cleanup revoke qua API/worker, disable product đã publish và application, xóa đúng fixture theo id/org_id/sku, kiểm lại fingerprints Warehouse/Sales/Logistics, vô hiệu hóa principal/membership test và giữ audit. Không DROP schema/table/database. Các registry/contracts/jobs/audit, source metadata không có permission consumer và ba role test được giữ để điều tra/tái sử dụng; metadata role/schema là tài nguyên engine hiện hành. Khi fingerprint khác baseline, dừng và báo lỗi, không tự khôi phục dữ liệu ngoài fixture.

## Dừng và bảo toàn dữ liệu

```powershell
docker compose --env-file .env.docker -f infra/docker-compose.yml stop
```

Không dùng down -v, prune hoặc xóa volume để xử lý lỗi; stop/up giữ realm và metadata. Docker project này không quản lý container/volume của dự án khác. Không in docker compose config đầy đủ, container environment, secrets.json hoặc token vào log/báo cáo.

## Bằng chứng

Ngày kiểm thử 23/10/2026: Docker Engine 29.2.0, Compose 5.0.2; đúng hai container Keycloak/Hasura. Metadata Hasura hoạt động trên postgres Supabase hiện có. Kết quả từng lượt E01–E09 được ghi trong ledger local; không dùng mô tả tài liệu hoặc health check làm bằng chứng PASS cho một lượt mới.

Lượt nghiệm thu 9860b946629e chạy bằng scripts/test_e2e.ps1: **4 passed** (386.50 giây), gồm Keycloak/Core integration, Hasura JWT/reader integration, E01–E08 và E09 fault/retry/publication failure. Không mock hoặc identity override. Cleanup thành công, fingerprints toàn bộ bảng public của Warehouse/Sales/Logistics khớp baseline; Core test registry/jobs/audit và ba role test được giữ, principal/membership test đã vô hiệu hóa. Regression offline sau hai bản sửa concurrency: **738 passed, 54 deselected** (36.69 giây). Các lần chạy bị gián đoạn hoặc thất bại trước đó đã cleanup riêng, không được tính PASS thay cho lượt nghiệm thu này.

Hasura có thể trả HTTP 200 kèm errors invalid-headers/invalid-jwt và không có data khi thiếu credential hoặc giả role; HTTP 200 không đồng nghĩa truy vấn được phép. Suite offline không thay thế nghiệm thu E2E.

Nguồn: [Keycloak container](https://www.keycloak.org/server/containers), [Hasura configuration](https://hasura.io/docs/2.0/deployment/graphql-engine-flags/reference/). Hướng dẫn luồng và ma trận E2E: [Automated Onboarding](AUTOMATED-ONBOARDING.md).
