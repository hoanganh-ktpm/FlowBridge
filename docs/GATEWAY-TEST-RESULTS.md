# PR02 Gateway — evidence

Ngày17/10/2026, branch `huuhuandev_features`, base HEAD
`fc93e97038cb9bb382940d538dc0d32cd364fd5e` cùng working-tree PR02.
Không commit/push, migration/stamp/seed hoặc sửa `.env`. DB chỉ ghi fixture UUID
riêng do runner quản lý; cleanup commit được xác minh qua NullPool connection mới
trước khi unlink manifest. Dev state/volumes khác được giữ nguyên.

## Đã chạy

| Kiểm tra | Kết quả |
|---|---|
| APISIX upstream |3.19.0-debian, OCI index `sha256:9a7e45dc943fbf10ec916d1232bae4cda9302ae28a4696cfb83c2da01d261141`, inspect/pull đạt |
| Compose config/Ruff/format/JS/sync/diff | Đạt; Core thêm413 cho hai Identity GET, schema ErrorResponse giữ nguyên và provenance đã sync |
| Full unit |296pass19.19s, session51370exit0 |
| Docker runner trong repo | Session26658exit0, project `flowbridge-gateway-test-9f36744e4c10` |
| External network HTTP |21pass10.12s: route/method allowlist, public internal/admin/ops/docs block kể cả encoding, không DNS/TCP tới backend, Admin9180 đóng, TLS CA, CORS/preflight, correlation,413/429/504 |
| Browser + IAM/API thật |1pass4.3s: PKCE/MFA, human principal qua gateway khớp UUID/tenant, header giả không đổi actor, gọi receiver trực tiếp giữ auth, invalid token401/service token403 |
| Log boundary | Gateway và receiver cùng correlation; các route public bị chặn không xuất hiện ở log receiver; log không chứa fixture tokens/passwords; query bị lọc bằng safe Uvicorn access filter |
| Cleanup | Fixture và project test down/volumes thành công; không còn session của lượt này |

Timeout dùng APISIX phụ nối HTTP upstream chậm trong test: bằng chứng proxy504,
không phải chứng nhận backend hoặc Workflow xử lý timeout. External client chỉ
mount CA công khai, không IAM/DB credential hoặc backend network. Backend vẫn tự
xác thực bearer/grant/policy; introspection gateway không thay resource authorization.

Policy rate/OIDC/timeout/health cuối ở YAML native đã được nghiệm thu lại từ
archive sạch ngoài repo: session89538exit0, project
`flowbridge-gateway-test-28f1943224d1`:21HTTP pass10.14s,1browser pass4.3s,
correlation/denied-route absence/secret redaction và fresh-connection cleanup đạt.
Build unit image từ cùng archive và chạy296unit pass20.49s, session35943exit0;
không skip. Hai project đều down và xóa đúng volumes/network test.

Artifact196source members/66wheel members, một root flowbridge-0.1.0; kiểm exact
source bytes, không cache/actual.env/dependency directories, không chứa credential
đang cấu hình (chỉ scan RAM, không ghi giá trị). Clean archive rebuild ngoài repo
và Python-I installed wheel probe48routes/Identity413/safe logger đạt. Không thay
venv hiện có: wheel chỉ cài vào target verification riêng. Source/wheel194/65
trước đó là snapshot giữa task. Cập nhật evidence sau nghiệm thu chỉ đổi tài liệu;
source runtime/config cuối được giữ đúng byte của archive đã chạy.

## Trạng thái task

| Task | Kết luận |
|---|---|
| CODE-02-01.01 | Đạt public Identity allowlist |
| CODE-02-01.02 | Một phần: public block/network isolation đạt; private listener/allowlist Data/Files/Workflow chờ receiver thật |
| CODE-02-01.03 | Đạt TLS/CA/OIDC/CORS trong profile Identity |
| CODE-02-01.04 | Đạt rate/body/timeout trong profile Identity; upload riêng khi có Files API |
| CODE-02-02.01/.02 | Đạt proxy trust/header boundary/correlation/log trong scope Identity |
| CODE-02-03.01 | Đạt hai network, public/private boundary và Admin API trong scope hiện có |
| CODE-02-03.02 | Một phần: receiver invalid token401/service403 đạt; resource tenant404 và lặp Data/Files/Workflow chờ API thật |

Giữ PR02/CP-003 mở theo các điều kiện còn lại; không tự đóng CP-001/002 hoặc
CODE-00-04.02/05.02 từ suite Gateway này. Image package/license audit toàn phần,
production/HA, private APIs tương lai và release/public access chưa nghiệm thu.

## Lỗi đã xử lý trong task

- Xung đột IP gateway với dải cấp động: tách ip_range, validate trước prepare.
- Fixture localhost guard chặn hostname Docker: chỉ mở alias identity với exact
  issuer, test-mode và marker/metadata của profile riêng; không nhận issuer arbitrary.
- Cert/SNI thiếu gateway-timeout: thêm SAN/SNI của gateway phụ.
- Assertion log trước flush: chờ upstream Nginx flush3s; không nhận lượt lỗi đó pass.
- Unit/build sandbox bị chặn ASGI/cache: đã dừng và chạy ngoài sandbox; không nhận
  kết quả chưa hoàn tất. Không nới auth/schema/fixture guard để vượt lỗi.

Lệnh tái hiện và cách recovery: [GATEWAY.md](GATEWAY.md).
