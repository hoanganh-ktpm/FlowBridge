# Hợp đồng OpenCore

Snapshot chuẩn được đóng gói cùng Python package tại
[`opencore-api.yaml`](../backend/src/flowbridge/contracts/opencore-api.yaml), version 0.2.0.
Đây là một snapshot do nhóm xây dựng trong workspace OLP, từ `03-api/contracts/opencore-api.yaml`.
Không lấy từ dependency hoặc dự án bên ngoài.

SHA-256: `3cd8cd7e97f5e018aeadaca55952c07d9d1d93d2e40797cb2641298ee0d376f2`.
Giữ nguyên byte từ nguồn chuẩn. Khi sửa contract, sửa nguồn trước, đồng bộ snapshot,
cập nhật checksum và chạy `tests/sample/test_contract_coverage.py`. Khi nguồn workspace còn có,
test đối chiếu trực tiếp cả hai; source archive độc lập dùng checksum đã khóa.

Runtime, schema sample và mapping Gateway đều đọc snapshot này bằng package resources.
Không có bản YAML thứ hai dưới thư mục này. Quyền dùng snapshot được ghi trong sidecar `.license`.

Trong chế độ sample, `/openapi.json` giữ request/response từ contract, ghi rõ dữ liệu và
authentication mô phỏng, đặt `security: []`, lưu yêu cầu security sản phẩm trong
`x-production-security`. Đây là schema phát triển, không chứng minh đã tích hợp OIDC/engine.
