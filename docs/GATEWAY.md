# APISIX Gateway

Gateway dùng Apache APISIX OSS 3.19.0 (Apache-2.0), chạy thành dịch vụ riêng;
backend FlowBridge giữ GPL-3.0-or-later. Không copy hoặc sửa source upstream.
Image `apache/apisix:3.19.0-debian` được khóa bằng OCI index digest trong
`infra/gateway/images.env`; xác minh lại trước khi cập nhật. Image upstream và
dependency bên trong cần audit riêng trước release; license root không chứng
nhận toàn bộ image. Không đưa image/binary/cache vào source archive.

## Quyết định triển khai

Standalone data plane đọc YAML, tắt Admin API và Control API, không cần etcd.
`infra/gateway/config.yaml` sở hữu cấu hình Nginx/APISIX; `routes.yaml` là
allowlist hai operation Identity đã triển khai. Unit test đối chiếu router thật.
Script chỉ render giá trị deployment/secret vào volume riêng và gọi Docker,
OpenSSL, pytest/Playwright; không thực hiện xác thực hoặc proxy bằng Python.

| Đường dẫn public | Method | Xử lý |
|---|---|---|
| `/api/v1/core/identity/config` | GET | Không cần token |
| `/api/v1/core/identity/me` | GET | Bearer introspection tại APISIX và kiểm lại tại backend |
| Hai đường dẫn trên | OPTIONS | CORS preflight theo allowlist |
| Các đường khác, internal/admin/ops/docs/app | — | Không có route public |

Chưa tạo private route cho API core chưa triển khai. Không suy ra private
Data/Files/Workflow đã nghiệm thu từ việc public listener trả 404.

TLS gateway và IAM dùng khóa riêng, CA local của project test. APISIX xác minh
CA của IAM; không tắt `ssl_verify`. Bearer được giữ nguyên cho receiver, không
chuyển claim thành actor đáng tin qua header. Realm test dùng hostname Docker
`identity`, PKCE/MFA như profile core, không sửa realm/state cũ.

Public client test chỉ ở network `edge`. IAM/API ở `backend`; chỉ gateway có cả
hai network. API không publish host port; cổng IAM và gateway chỉ bind loopback
để debug local. Đây là profile development/test, không phải deployment production.

`FLOWBRIDGE_TRUSTED_PROXY_IPS` mặc định `[]`: launcher không tin forwarding từ
client trực tiếp. Gateway profile cấp đúng IP backend của gateway cho Uvicorn.
Gateway bỏ header tenant/user/actor/role và forwarding do caller tự khai, đặt
lại `X-Forwarded-For/Proto`; receiver vẫn kiểm token, grant và resource policy.
Không dùng wildcard trust. Lệnh Uvicorn tự gọi ngoài launcher cần cấu hình
`--forwarded-allow-ips` tương ứng; không dựa vào mặc định của Uvicorn.

Correlation hợp lệ theo Id (1–128 ký tự) được giữ; giá trị sai hoặc header lặp
được thay bằng request ID Nginx. Access log chỉ có peer/method/path/status/ID,
không query string hoặc Authorization. Runner kiểm log không chứa token/password
của fixture và kiểm các route bị chặn không xuất hiện trong log receiver.
`FLOWBRIDGE_HTTP_ACCESS_LOG_ENABLED` mặc địnhfalse, profile Gateway bật log
Uvicorn đã lọc query string và gắn correlation; không dùng access log mặc định
chứa full request URI. Runner chờ Nginx flush buffer3s trước đọc evidence.
Các lỗi gateway dùng envelope Core với correlation; upstream giữ
envelope của receiver.

Giới hạn local: 10 request/giây/IP/route, burst20, body1024 byte, timeout
connect/send/read3s, không retry upstream. Rate/OIDC/timeout/health cấu hình ở
`infra/gateway/routes.yaml`, body/TLS/log ở `config.yaml`; script chỉ render
secret, origin và địa chỉ deployment. Chỉ hai GET Identity được mở; khi
bổ sung upload cần cấu hình riêng theo route. Health probe nội bộ dùng
`/_ops/live`; path này không được public. Contract bổ sung413 cho hai Identity
operation;429/504 đã có. Đổi giới hạn qua cấu hình APISIX trước khởi động.

## Chạy và kiểm thử

Cần Docker Linux, Compose>=2.35, Python/toolchain đã setup theo README và DB test
có migration head/dữ liệu nền đúng. Runner không migrate, stamp hoặc seed.

```powershell
.venv/Scripts/python.exe scripts/gateway_stack.py config --env-file .env
.venv/Scripts/python.exe scripts/gateway_stack.py test --env-file .env
```

`test` tạo project `flowbridge-gateway-test-<12 hex>` riêng, build image từ source,
chuẩn bị IAM mới, kiểm DB head read-only trước fixture, chạy HTTP từ edge rồi
browser IAM/receiver, kiểm log, cleanup fixture và down đúng project. Mọi lỗi
test/engine làm lệnh thất bại; không skip để nhận pass. `.env` chỉ được đọc qua
secret preparation, không đưa vào image/IAM/browser/gateway.

Timeout test dùng APISIX phụ và upstream HTTP chậm riêng trên network backend,
không mount IAM/DB credentials và không publish host port. Nó chứng minh proxy
timeout504; không thay nghiệm thu Identity với IAM/DB thật. Cleanup fixture được
kiểm bằng kết nối DB mới sau commit, chỉ unlink manifest khi các UUID đã biến mất.

Mặc định API nội bộ18092, IAM19454 và gateway host19453. API/IAM có tham số
`--api-port`/`--iam-port`; cổng host gateway qua `FLOWBRIDGE_GATEWAY_PORT`.
Backend subnet mặc định172.29.240.0/24 và IP gateway172.29.240.2. Nếu trùng mạng,
đặt `FLOWBRIDGE_GATEWAY_BACKEND_SUBNET`, `FLOWBRIDGE_GATEWAY_BACKEND_RANGE` và
`FLOWBRIDGE_GATEWAY_PROXY_IP` cùng nhau. Dải cấp động mặc định172.29.240.128/25
không chứa IP gateway để tránh Docker cấp trùng địa chỉ. Cấu hình được kiểm
trước chạy. Không đổi network/trust giữa một lần test và recovery.

Run record nằm ở `.cache/dev-stack/<project>.json`, chỉ lưu metadata không secret.
Nếu cleanup thất bại, runner dừng container và giữ volumes, trả lỗi. Khôi phục
đúng project đã lưu, với cùng DB env/port/network:

```powershell
.venv/Scripts/python.exe scripts/gateway_stack.py recover --env-file .env --project flowbridge-gateway-test-012345abcdef
```

Không chạy recovery với tên mẫu trên; thay bằng project của lần lỗi. Không xóa
volumes khi chưa đối soát fixture. Private Data/Files/Workflow routes cần receiver thật trước khi mở thêm; profile
hiện tại chỉ công bố Identity core.

## Nguồn upstream

- [Release Apache APISIX](https://apisix.apache.org/downloads/):3.19.0,2026-09-28.
- [Standalone deployment](https://apisix.apache.org/docs/apisix/deployment-modes/).
- [OIDC plugin](https://apisix.apache.org/docs/apisix/plugins/openid-connect/).
- [Proxy rewrite](https://apisix.apache.org/docs/apisix/plugins/proxy-rewrite/).
- [Nginx configuration](https://apisix.apache.org/docs/apisix/customize-nginx-configuration/).
- [Upstream license](https://github.com/apache/apisix/blob/3.19.0/LICENSE) và
  [NOTICE](https://github.com/apache/apisix/blob/3.19.0/NOTICE); giữ notices trong
  image/distribution upstream, không phân phối lại source/binary dependency.
