# Hasura v2 Community Edition

**Configuration boundary cho engine; không chứa application code hoặc credentials.**

Python adapter nằm tại [Core Hasura Provisioner](../../backend/app/core/hasura_provisioner/README.md). Profile Docker thử ở ../docker-compose.yml, dùng metadata PostgreSQL local riêng và image v2.48.16 pin digest; xem [Docker integration](../../docs/DOCKER.md). Không có Kubernetes hoặc Hasura CLI project trong thư mục này.

## Baseline và runtime

`metadata/baseline.json` ghi nhận metadata v3 rỗng dành cho thiết kế/bootstrap **instance mới hoàn toàn**. Không có script tự apply. Không chạy `replace_metadata` hoặc CLI metadata apply từ baseline này trên instance đã có sources, products hoặc permissions: có thể xóa runtime configuration.

Data Products được đăng ký động; source/track/SELECT changes do provisioner thực hiện bằng Metadata API với resource-version guard. Runtime export dùng cho backup/review phải được lọc secret và lưu ngoài Git mặc định. Chỉ đưa cấu hình đã review, không có credentials vào source control.

## Ranh giới database

- `HASURA_GRAPHQL_METADATA_DATABASE_URL` thuộc environment của **Hasura engine**, trỏ tới metadata PostgreSQL riêng. Hasura tự quản lý internal schema; không đưa URI này vào backend config hoặc business source registry.
- Các biến `WAREHOUSE_READ_DATABASE_URL` và tương tự là kết nối **business sources**, không phải metadata database. Phải cấp ở environment Hasura trước khi thêm source bằng `from_env`.
- Backend dùng `FLOWBRIDGE_HASURA_ENDPOINT` và `FLOWBRIDGE_HASURA_ADMIN_SECRET` để gọi control plane. Chúng không phải connection string PostgreSQL.

Nguồn PostgreSQL cần tài khoản giới hạn theo [yêu cầu Hasura](https://hasura.io/docs/2.0/deployment/postgres-requirements/). Không dùng quyền owner/superuser chỉ để đơn giản hóa provisioning. Chưa bật event triggers, business mutations hoặc tính năng Enterprise.

License của release tham chiếu v2.48.16 được ghi tại [THIRD_PARTY_LICENSES](../../THIRD_PARTY_LICENSES.md); chưa cài hoặc xác nhận tương thích runtime với release đó.
