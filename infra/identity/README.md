# Keycloak cho FlowBridge

Nguồn cấu hình chuẩn là `realm.template.json`; secret chỉ xuất hiện trong cấu hình sinh ra ở thư mục bị Git ignore. Keycloak sở hữu IdP; backend gọi introspection và directory API, không phát token hoặc lưu mật khẩu.

Phiên bản, nguồn và SHA-256 được pin trong `dependencies.json`. Tải distribution từ URL upstream bằng công cụ chuẩn, đối chiếu SHA-256 trước giải nén vào `.tools/`. Giữ nguyên LICENSE/NOTICE của distribution. Không thêm `.tools/`, `.cache/`, node_modules hoặc binary vào source artifact. SDK browser cài bằng npm/lockfile riêng.

Windows local:

```powershell
& ./.venv/Scripts/python.exe scripts/prepare_identity.py --test-users
& ./infra/identity/start-keycloak.ps1 -KeycloakHome .tools/sso/keycloak-26.8.1 -JavaHome '.tools/sso/jdk-25.0.4.1+1'
```

Lệnh đầu cần OpenSSL trên PATH, tạo CA/certificate riêng, client secrets và tài khoản test thật trong realm import. Nó từ chối ghi đè state đã có để tránh tự xoay secret. Lệnh sau chỉ gọi `kc.bat` upstream bằng Java riêng, không đổi Java/CA hệ thống. Cấu hình dev/H2 chỉ dùng local, có HTTP loopback 8080 và HTTPS 18443; issuer/API luôn dùng HTTPS và `sslRequired=all`. Không coi đây là triển khai production.

Trên hệ khác, dùng distribution/Java tương ứng và gọi `kc.sh` upstream với cùng realm đã render, certificate và hostname. Checksum Windows trong manifest không áp dụng cho binary nền tảng khác: cần lấy và xác minh metadata upstream tương ứng. Không sửa source upstream để chạy.

Production dùng cách cấu hình [Keycloak server](https://www.keycloak.org/server/configuration), [database](https://www.keycloak.org/server/db), [TLS](https://www.keycloak.org/server/enabletls) và [container](https://www.keycloak.org/server/containers) chuẩn. Database IAM phải tách khỏi application schema Supabase; chỉ môi trường local này dùng H2. Realm import không cập nhật realm đã có: quản lý thay đổi trên IdP bằng giao diện/CLI/Admin API được hỗ trợ, không reset realm để đổi config.

Xem [hướng dẫn SSO](../../docs/SSO.md) để chạy API, bootstrap quyền, client A/B và test. Không dùng realm mẫu để giả identity qua HTTP; mọi token do IdP thật cấp và được backend kiểm tra.

Profile container development/test dùng chính generator/realm template này: [DEV-CONTAINERS.md](../../docs/DEV-CONTAINERS.md). Docker Linux build, IAM/API/clients và unit/SQL Identity đã chạy; full SSO bị chặn bởi DB revision, image audit toàn phần còn mở. Không thay hướng dẫn production upstream.
