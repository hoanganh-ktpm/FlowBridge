# Portal

Khối giao diện độc lập: React/TypeScript, design tokens, shadcn/ui, chat, bảng/biểu đồ, Activities, đăng nhập PKCE và Keycloak theme. Giữ dependency/lockfile và attribution hiện có.

Source ở src; SSO/session ở src/app; theme ở src/keycloak-theme; UI primitives ở src/components/ui; HTTP/API boundary ở src/lib. Fixtures hiện có nằm ở src/fixtures. JSON presentation schemas ở contracts/portal/schemas.

```powershell
npm ci
npm run build
npm test
npm run dev
```

Preview: http://127.0.0.1:5174/?preview=1. start.ps1 chỉ chạy Vite, không gọi launcher/backend/ML/RAG cũ. Có thể phát triển UI bằng fixtures mà không chờ các khối backend.

Đăng nhập giữ Keycloak SDK/PKCE. Config công khai lấy qua FLOWBRIDGE_SSO_PUBLIC_CONFIG hoặc FLOWBRIDGE_WEB_API_URL/ISSUER/CLIENT_ID và VITE_*; không đưa secret vào browser. Fallback hiện trỏ local v1, cần cấu hình deployment mới khi backend có API.

API client, Agent IDs và dữ liệu demo vẫn có semantics mua hàng v1. Chưa chuyển sang Data Registry/Workflow Tasks; đây là phần UI cần làm tiếp sau khi contract mới có thật. Build/unit tests không chứng minh API/SSO mới đã chạy E2E.
