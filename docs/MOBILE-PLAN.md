# Kế hoạch app mobile FlowBridge — React Native

<!-- SPDX-License-Identifier: GPL-3.0-or-later; FlowBridge, see ../LICENSE. -->

Cập nhật **20/10/2026**. Theo yêu cầu hiện tại: lập kế hoạch, chưa tạo project app,
chưa cài dependency hoặc thay cấu hình SSO/Docker. Nền backend đã commit tại
`4934bf2` trên nhánh `huuhuandev_features`.

## 1. Mục tiêu bản đầu

App dành cho nhân viên: đăng nhập SSO, xem sản phẩm/nhà cung cấp, xem và tạo yêu
cầu mua hàng, trò chuyện với trợ lý và xem lịch sử. Ưu tiên Android để thử trên
môi trường Windows hiện có; iOS là mốc sau khi có thiết bị và môi trường build.
Đây là đề xuất phạm vi để bắt đầu, chưa phải chức năng mobile đã triển khai.

Các màn hình dự kiến:

| Màn hình | Chức năng |
|---|---|
| Đăng nhập | Mở Keycloak, hoàn thành OTP, quay lại app |
| Trang chủ | Hồ sơ, quyền và trạng thái kết nối |
| Danh mục | Tìm/xem sản phẩm, nhà cung cấp |
| Yêu cầu mua hàng | Danh sách, chi tiết, tạo yêu cầu theo quyền EMPLOYEE |
| Trợ lý AI | Hội thoại, chọn agent khả dụng, câu trả lời và trạng thái run |
| Tài khoản | Thông tin phiên và đăng xuất |

Quản lý tài liệu RAG, trang quản trị, push notification và offline ghi dữ liệu
để sau MVP. Quy trình duyệt/email chỉ mở khi backend đã có adapter hoạt động.

## 2. Công nghệ đề xuất

React Native + Expo + TypeScript, Expo Router cho điều hướng. React Native
khuyến nghị dùng framework cho app mới; Expo phù hợp để bắt đầu bằng một bộ
toolchain thống nhất. [Tài liệu React Native](https://reactnative.dev/docs/environment-setup).

Dùng development build cho luồng OAuth có callback riêng. Expo Go không phù
hợp để nghiệm thu đăng nhập này. Build Android local cần Android Studio/SDK;
iOS local cần Xcode/macOS. EAS là lựa chọn sau, không phải điều kiện bắt buộc
của kế hoạch. [Expo authentication](https://docs.expo.dev/guides/authentication/),
[build local](https://docs.expo.dev/guides/local-app-development/).

Khi bắt đầu code mới chọn bộ Expo SDK/React Native/React/Node tương thích theo
tài liệu tại thời điểm đó, pin manifest/lockfile và kiểm license/OSS. Không lấy
nguyên phiên bản dependency của web để áp sang native. Chưa thêm package trong
lượt lập kế hoạch này. Mã mobile của nhóm theo GPL-3.0-or-later hiện có; assets,
font, icon và dữ liệu bên ngoài cần provenance/license riêng.

## 3. Cấu trúc dự kiến

Đặt app tại `clients/mobile/`, bên cạnh `clients/sso/`. Đây chỉ là sơ đồ, các
thư mục dưới chưa được tạo:

```text
clients/mobile/
  app/                   Routes đăng nhập, tabs và chi tiết
  src/config/            API origin, issuer, client ID, callback
  src/auth/              Phiên SSO, refresh, logout
  src/api/               HTTP client, DTO và xử lý lỗi
  src/features/          catalog, purchase-requests, agent-hub
  src/components/        Thành phần giao diện native dùng chung
  app.config.ts          Cấu hình native và deep link
```

Luồng gọi: màn hình → feature/service → API client → backend. Backend tiếp tục
sở hữu phân quyền, dữ liệu, workflow và kết nối Langflow. App không truy cập
Supabase trực tiếp hoặc giữ secret của Keycloak, Langflow hay nhà cung cấp model.
UI dùng component native; tái sử dụng kiến thức DTO/nghiệp vụ từ web, không chép
DOM/CSS hoặc `keycloak-js` sang React Native.

## 4. Việc cần giải quyết trước khi làm màn hình

### Kết nối thiết bị với Docker

Hiện Docker công bố cổng trên loopback; issuer là
`https://localhost:19444/realms/flowbridge`, API là `http://127.0.0.1:18082`.
`localhost` trên điện thoại không tự trỏ tới máy chạy Docker.

Mốc đầu thử Android kết nối bằng ADB reverse cho cổng API và IAM để giữ nguyên
issuer trên máy phát triển. Cần kiểm cả trình duyệt đăng nhập và HTTP trong app.
Đây là phương án dự kiến, chưa được nghiệm thu trên thiết bị.

Phải kiểm CA/TLS trong app và trình duyệt. CA debug của Android không tự làm
trình duyệt tin chứng chỉ; cần thiết lập tin cậy riêng. API hiện dùng HTTP nên
debug build có thể cần ngoại lệ cleartext đúng origin; release dùng HTTPS.
[Android network security](https://developer.android.com/privacy-and-security/security-config).

Nếu dùng điện thoại qua Wi-Fi hoặc iOS, chuẩn bị hostname/HTTPS mà cả thiết bị,
trình duyệt và container đều truy cập được. Đổi IP trong app không đủ: issuer,
certificate, Keycloak hostname và backend phải khớp. Không sửa issuer của volume
đang dùng để thử nhanh; tạo profile phát triển riêng khi cần đổi endpoint.

### Đăng nhập SSO native

Hiện realm chỉ có public clients `flowbridge-web-a`/`flowbridge-web-b` với callback
HTTP của UI. Giá trị `mobile_client_id` trong profile core đang trỏ web-b; chưa
có client dành riêng cho app native.

Mốc tích hợp cần:

1. Đăng ký public client riêng, dự kiến `flowbridge-mobile`, Code Flow + PKCE S256,
   audience API hiện có, callback native allowlist chính xác. Chốt app ID/scheme
   trước khi build; không thêm wildcard hoặc client secret vào app.
2. Thêm client vào allowlist backend và cấu hình profile/generator tương ứng.
   Với realm đã tồn tại, cập nhật bằng Keycloak Admin API/Console; sửa template
   import đơn thuần không cập nhật realm đang chạy.
3. MVP dùng `/api/v1/me` và cấu hình mobile công khai riêng. Chưa thay binding
   core web-b: core hiện chỉ nhận hai human client ID; muốn dùng core identity
   từ native cần quyết định binding riêng và regression cho web A/B.
4. Mở trình duyệt hệ thống để login/OTP, xác minh state/PKCE và callback, đổi code
   theo public-client flow. Người dùng nhập mật khẩu tại Keycloak.
5. Access token giữ trong bộ nhớ; refresh token dự kiến lưu bằng SecureStore.
   Chỉ một tác vụ refresh chạy tại một thời điểm vì realm bật refresh-token
   rotation. Mất phiên/hết hạn thì về đăng nhập; logout xóa phiên local và kết
   thúc phiên IdP theo endpoint được hỗ trợ.

Ưu tiên thư viện OAuth có sẵn, không tự viết engine xác thực.
[OAuth cho native apps](https://www.rfc-editor.org/rfc/rfc8252),
[Expo AuthSession](https://docs.expo.dev/versions/latest/sdk/auth-session/),
[SecureStore](https://docs.expo.dev/versions/latest/sdk/securestore/).

## 5. API sử dụng

Hợp đồng chuẩn: [API ứng dụng](../backend/flowbridge/contracts/app-api.yaml),
[Agent Hub OpenAPI](../contracts/agent-hub-api.yaml),
[hướng dẫn Agent Hub](AGENT-HUB.md). DTO mobile tách khỏi ORM và bám contract;
giữ tiền/số lượng dạng decimal string, phân trang và revision theo backend.

| Chức năng | API |
|---|---|
| Hồ sơ/quyền | `GET /api/v1/me` |
| Danh mục | `GET /api/v1/products`, `GET /api/v1/suppliers` |
| Yêu cầu mua hàng | `GET/POST /api/v1/purchase-requests`, `GET /api/v1/purchase-requests/{request_id}` |
| Agent khả dụng | `GET /api/v1/agent-hub/agents` |
| Hội thoại | `GET/POST /api/v1/agent-hub/conversations`, đọc chi tiết/messages theo contract |
| Gửi tin | `POST /api/v1/agent-hub/conversations/{conversation_id}/runs` |
| Kết quả/trạng thái | `GET /api/v1/agent-hub/runs/{run_id}` và `/metadata` |
| Timeline | `GET /api/v1/agent-hub/runs/{run_id}/events` |
| Hủy | `POST /api/v1/agent-hub/runs/{run_id}/cancel` |

Đề xuất mobile dùng Agent Hub cho hội thoại bất đồng bộ; web chat cũ vẫn giữ
`/api/v1/chat/*`. Hai loại ID/lịch sử khác nhau, không trộn dữ liệu giữa chúng.
Trước tích hợp cần chứng minh run thật thành công: lần smoke gần nhất trong
checkpoint có `EXECUTION_TIMEOUT`, chưa đủ bằng chứng agent trả lời ổn định.

Quy tắc API client cần kiểm thử:

- HTTP 401: refresh có điều phối; HTTP 403: hiển thị thiếu quyền, không lặp refresh.
- POST ghi dữ liệu giữ nguyên body/Idempotency-Key khi chưa rõ kết quả; không
  tự tạo request/run mới sau lỗi mạng. Nếu khôi phục lượt gửi qua restart app,
  cần thiết kế pending-operation store riêng theo user và đối soát trước gửi lại.
- Hub result trả HTTP200 vẫn có thể `failed`/`cancelled`; xem trạng thái nghiệp vụ.
  Cancel được nhận chưa đồng nghĩa đã hủy xong. Agent không khả dụng phải thể
  hiện đúng từ catalog; ML/RAG chỉ bật khi artifact, quyền và PR/job context hợp lệ.
- MVP có thể poll run đang hoạt động khi app ở foreground. SSE là bước tiếp:
  kiểm transport native có header Authorization/Last-Event-ID, parser chịu
  chunk split và chống event trùng; chọn dependency sau khi xác minh tương thích.
  Đóng stream khi background, reconnect/đối soát cùng run khi quay lại.
- Không đưa bearer vào URL/log. UI có loading, rỗng, offline, timeout và retry;
  đổi tài khoản phải xóa cache và trạng thái pending của user trước.

## 6. Thứ tự triển khai và điều kiện hoàn tất

Các task dưới **chưa bắt đầu**. Không tick chỉ vì đã có kế hoạch hoặc file.

| ID | Đầu ra cần có | Bằng chứng hoàn tất |
|---|---|---|
| CODE-MOBILE-01 | Chốt Android-first, app ID, bộ SDK/license và khung Expo/TypeScript | Development build mở được trên emulator, lockfile và lệnh build tái hiện được |
| CODE-MOBILE-02 | Profile kết nối thiết bị, CA/TLS, client SSO native | Discovery, callback, login/OTP/refresh/logout và `/me` thật; web A/B không hỏng |
| CODE-MOBILE-03 | API client + màn hình hồ sơ/danh mục | Kiểm 401/403/offline/phân trang; đọc danh mục backend thật |
| CODE-MOBILE-04 | Danh sách/chi tiết/tạo PR | Ghi test PR đúng owner; retry không trùng; user khác không đọc được |
| CODE-MOBILE-05 | Hội thoại/run Agent Hub | Một run Data thật thành công; hiển thị timeout/failed/cancelled đúng, không giả thành công |
| CODE-MOBILE-06 | SSE/timeline và khôi phục khi gián đoạn | Replay không trùng, đóng/mở foreground đúng, hết token và mất mạng không tạo run mới |
| CODE-MOBILE-07 | Hoàn thiện UI và APK test | Test component có assertion, smoke Android thật, README, license và artifact có version |
| CODE-MOBILE-08 | iOS khi nhóm có môi trường | Build và test SSO/network trên iOS thật; không suy pass từ Android |

Đi lần lượt 01→02→03 trước khi làm form/chat. Riêng05 có điều kiện backend chạy
model thật và có thể phát sinh phí; tách kiểm thử giả lập khỏi bằng chứng tích
hợp thật. Sau mỗi task cập nhật checkpoint theo kết quả, không tự reset DB/IAM.

## 7. Trạng thái chuẩn bị hiện tại

- Đã rà API, binding SSO, Compose và giới hạn kết nối thiết bị từ source hiện có.
- Đã xác định phạm vi, thứ tự task, nguồn contract và tiêu chí kiểm thử.
- Chưa tạo `clients/mobile/`, chưa cài SDK/dependency, chưa đăng ký client native
  hoặc đổi issuer/cổng. Chưa build APK/iOS hay kiểm mobile trên thiết bị.
- Bước tiếp theo khi bắt đầu triển khai: `CODE-MOBILE-01`, chốt app ID và bộ SDK
  rồi dựng khung; không bắt đầu bằng việc sao chép UI web hoặc thay auth backend.
