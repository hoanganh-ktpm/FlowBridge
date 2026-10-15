# Evidence SSO — 16/10/2026

## 16/10/2026 — Sau tích hợp origin và chuyển nhánh

Repo hiện hành `huuhuandev_features` / `aebfe852ea3843b8c52aa050e5527301ad0b27f2` + diff SSO chưa commit. Đã tích hợp 5 commit source origin (RAG/ML và merge), target chứa toàn bộ refs source/target/main đã fetch; chưa push. Source hoanganhdev_features fast-forward cùng HEAD. Backup43 file và stash f034e3e8502fe50f6634cfa8ab1c01ddaf1563c6 được giữ; 3 conflict CHANGELOG/pyproject/checkpoint đã resolve, không mất package/config hai phía.

| Kiểm mới sau merge | Kết quả |
|---|---|
| Unit toàn bộ | **221 passed in 8.71s**, không skip; lượt đầu 208/13 skip do thiếu optional dependency không tính đủ RAG |
| Optional unit dependency | numpy2.5.4 và rank-bm25 0.2.2 từ pin+SHA256 uv.lock; không thêm graph mới hoặc tải model |
| SSO browser E2E thật | **3 passed in 25.2s**; profile mới `.cache/identity-merge-verification`, Keycloak HTTPS19443/API18081, browser A/B8761/8762, Supabase thật, không override auth |
| Ruff / lock | Check + format72 đạt; uv lock --check --offline142 packages đạt |
| Build | Source tar.gz/wheel offline đạt, **143 source members / 55 wheel members**; giữ SSO+RAG+ML, không state/weights/dependency binary; audit secret/bundling + rebuild ngoài checkout và isolated wheel import đạt: DB/SSO46, ML51 operation có security; RAG dev riêng1 route và config xung đột bị từ chối |
| Bảo toàn code/state | Đủ43 file SSO; 36 giống backup sau LF normalization và7 file được ghép. 47 file added upstream ngoài diff local giữ nguyên; .env/fixture identity-final không đổi hash |
| Cleanup | Fixture mới được dọn đúng UUID, API70724 và IAM35625 đã dừng; fixture cũ của người dùng giữ nguyên |

Nội dung mới upstream RAG dev/ML được giữ; test unit RAG/ML sử dụng fixture/mock theo suite, không tự nhận model inference/Chroma production hoặc tích hợp ML↔SSO live đã được kiểm hết. Full Supabase SQL18 của lượt trước chưa chạy lại; 3 E2E mới có persistence/ownership/link/service token thật trong scope SSO.

## Lịch sử nghiệm thu SSO trước khi tích hợp origin

Các kết quả bên dưới thuộc baseline cũ và profile test ở thời điểm đó; không thay thế bảng mới phía trên.

Repo FlowBridge, branch `hoanganhdev_features`, baseline HEAD `a945f1ccaccf7391157aae626dfcb93c36bc8b2e` cộng working-tree diff của task. Chưa commit/push/deploy. Code/runtime đã bỏ sample identity từ baseline; task này bổ sung SSO thật, không phục dựng actor fixture trong API.

## Môi trường và phạm vi

Keycloak **26.8.1**, Temurin **25.0.4.1+1**, native Windows local/H2; OpenSSL sinh CA local; Node **24.19.0**, SDK **26.2.4**, Playwright **1.64.1**, Edge headless có sẵn. Distribution được kiểm SHA-256 từ upstream metadata trước giải nén; manifest/license tại infra/identity/dependencies.json. Không cài Docker hoặc đổi Java/trust store hệ thống. Chưa chạy hoặc audit OCI image.

API TCP loopback port 18080 dùng Supabase thật với revision `0002_api_idempotency`; IdP HTTPS localhost 18443 và hai browser client 8761/8762. Profile cuối `.cache/identity-final` được sinh lại từ source, H2 riêng trong IdentityDir, realm import sạch; không dùng realm đã sửa thủ công làm bằng chứng cuối. Backend/Node tin CA riêng, browser test pin SPKI của certificate đó. Không có auth override trong E2E, trace/video/screenshot tắt; credential/token không đưa vào evidence.

## Kết quả

| Kiểm tra | Kết quả |
|---|---|
| Unit toàn bộ | **129 passed in 5.49s**; auth/directory mocks chỉ là unit evidence |
| SQL regression Supabase | **18 passed in 160.54s**; 15 baseline + 3 bootstrap transaction/idempotency/permission/rollback |
| Browser E2E trên IAM state sạch | **3 passed in 24.5s**, không skip |
| Ruff lint/format | Đạt, 44 file Python được format-check tại thời điểm kiểm |
| Cleanup E2E cuối | Script cleanup hoàn tất; kiểm read-only không còn human test identities; counts suppliers=6, products=6, purchase_requests=1000 |

Ba E2E chứng minh:

1. Browser đăng nhập code+PKCE, access token có subject/client/audience hợp lệ, gọi API và lưu/đọc PR thật; cùng idempotency key không tạo PR trùng. Thiếu bearer, token giả, ID token và refresh token dùng làm bearer bị 401. User khác không đọc được PR dù tự thêm header role/user. Client B lấy token riêng qua phiên SSO mà không nhập lại mật khẩu; refresh hoạt động; logout làm token của phiên đó bị từ chối trong môi trường đã đo.
2. ADMIN thật liên kết UUID identity đã xác minh qua directory, retry không sửa profile/grant đang có; khai account_type sai bị 403. Đây là link user đã tồn tại tại IdP, không phải tạo tài khoản/password IdP hoặc provisioning hai chiều.
3. Client credentials của AI/PO tạo danh tính service thật, metadata đối chiếu bằng endpoint service-account-user. AI_SERVICE gọi lookup đúng quyền; human tự khai X-Role không nhận quyền đó; PO không tự có AI_SERVICE; service token không giả human bằng X-User-Id.

Fixture chuẩn bị 3 human và 2 service user ứng dụng cùng department riêng, chỉ gắn với identity IdP local. Cleanup theo UUID/resource do test tạo, xóa đúng idempotency/outbox/PR/items/grants/users/department của fixture. Các case SQL khác dùng rollback hoặc cleanup UUID riêng. Không chạy migration, seed, reset hoặc sửa user/dữ liệu demo cũ.

## Lỗi đã gặp và xử lý

- Fixture IdP đầu thiếu profile bắt buộc: Keycloak hiển thị VERIFY_PROFILE. Generator đã bổ sung email/name; test cũ hỗ trợ hoàn tất form upstream. Không bỏ required-action guard.
- Custom scope đầu thiếu sub: backend trả 401 đúng. Đã thêm SubMapper chuẩn cho access token/introspection, không tự suy ra subject hoặc nới xác thực. Lượt cuối import sạch đã chứng minh template có đủ claim.
- GET /users không trả serviceAccountClientId: adapter hiện kiểm binding qua endpoint service-account-user, directory có view-users/view-clients read-only; không phân loại bằng username.
- Windows .bat tách tham số JDBC có dấu chấm phẩy: launcher dùng KC_DB/KC_DB_URL chuẩn qua environment. Lượt cuối H2 được tạo tại IdentityDir và khởi động thành công.

Các lượt fail/chẩn đoán không được tính là pass. Không có test mang tên SSO chỉ in HTTP hoặc dùng actor tự khai để giả engine.

## Giới hạn

Chưa nghiệm thu MFA, delegated/job context, core identity APIs, production TLS/persistence/network boundary hoặc image. Không tick CP-002 hay các task cha core từ 3 E2E này. Tạo user/password tại IdP, provisioning hai chiều, workflow/email/AI/document engine vẫn là phạm vi riêng. Hiệu lực logout ở trên là kết quả đo cho cấu hình này, không khẳng định chung cho mọi IdP/token/cache policy.

Hướng dẫn tái hiện: [SSO.md](SSO.md). Cấu hình dev/H2 chỉ dùng local; source release không chứa state, secret, SDK/engine binaries hoặc node_modules. Build offline/no-build-isolation đạt source tar.gz và wheel: **96 source members / 34 wheel members**, một root `flowbridge-0.1.0`. Audit không có secret đã sinh/cấu hình, `.env`, state, cache, node_modules hoặc dependency binaries; 14 file code mới có GPL notice và Python không dùng docstring làm comment. Giải nén ngoài checkout tại workspace visualizations, rebuild offline thành công; Python `-I` import wheel rebuild và tạo app/OpenAPI đủ **46 operation có security**, không sample context. Đây là kiểm build cục bộ, chưa phải release/publish. Đã loại marker merge cũ khỏi CHANGELOG, giữ nội dung hai phần lịch sử.
