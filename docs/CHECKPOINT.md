# Checkpoint FlowBridge

Nguồn liên quan: [roadmap](../../.ai-local/FlowBridge/PLAN-OPENCORE.md), [task/test](../../.ai-local/FlowBridge/TASK-API-TEST.md), [SSO](../../.ai-local/FlowBridge/PLAN-SSO.md), [bản xuất](../../05-bao-cao/Ke-hoach-Tang1-OpenCore-2026-10-16.md).

<!-- BEGIN CURRENT SYNC-2026-10-16-OPENCORE-03 -->
## Trạng thái hiện hành sau tích hợp origin và chuyển SSO

Đồng bộ **SYNC-2026-10-16-OPENCORE-03**, ngày 16/10/2026 Asia/Saigon. Phần này là nguồn bàn giao hiện tại; các log, branch và evidence phía dưới giữ lịch sử tại thời điểm ghi.

### Yêu cầu, Git và trạng thái thay đổi

- Người dùng yêu cầu kéo 5 commit origin, giải conflict và chuyển SSO sang nhánh của mình. Tên thực tế đã xác minh là `huuhuandev_features`; không tạo nhánh theo lỗi gõ `huuhuandev_fearture`.
- Repo `E:/CTU/CLB_Tin_hoc/OLP/FlowBridge`, branch hiện tại **huuhuandev_features**, HEAD **13d0fff0594325894bd991b27baf750a2d41f893**, SSO đã commit local. Origin source aebfe85; origin target d396441; main ae20b28 là ancestor. Nhánh target ahead origin/huuhuandev_features11 commit trước push (10 commit upstream +1 SSO). Commit mới 13d0fff gồm42 file; chưa xác nhận push thành công.
- Source hoanganhdev_features đã fast-forward a945f1c→aebfe85 và không nhận diff SSO local. Target fast-forward d396441→aebfe85 rồi apply stash SSO. Người dùng xác nhận fetch thành công; đã đối chiếu refs/FETCH_HEAD sau fetch, không có commit source/target/main còn thiếu trong HEAD tích hợp.
- Fetch sandbox từng lỗi DNS; fetch ngoài sandbox đầu chờ credential rồi dừng, fetch không tương tác xác nhận cần login. Không tính các lệnh lỗi là fetch thành công, không thu thập token/password. Việc xác thực được người dùng hoàn tất trong terminal.
- Backup43 file/diff/SHA256 tại `OLP/.ai-local/FlowBridge/git-backups/20261016-020607-sso-transfer`; giữ stash **f034e3e8502fe50f6634cfa8ab1c01ddaf1563c6**. Không drop/pop stash, không reset/clean hoặc ghi đè thay đổi khác của người dùng. Backup không sao chép nội dung .env/credential/state; chỉ giữ hash để đối chiếu.

### Conflict và bảo toàn code

- Đã resolve 3 conflict: CHANGELOG, pyproject và checkpoint; giữ nội dung SSO và upstream RAG/ML. Main/settings auto-merge đã review để giữ CA/CORS/introspection cùng ML/RAG router/lifecycle. Không còn unmerged path hoặc marker conflict trong file hiện hành.
- Đủ43 file local ban đầu; 36 file giống backup sau LF normalization,7 file được ghép. 47 file added upstream ngoài diff local giữ nguyên byte sau chuẩn hóa LF. .env và `.cache/identity-final/fixture.json` của người dùng giữ nguyên SHA256.
- Runtime core0.2.0 vẫn là28 operation thiết kế chưa mount; app/AI mặc định46 operation (38 có đường DB/directory,8 adapter cũ fail closed). ML optional bổ sung5 route thành51 khi bật; RAG_DEV chỉ chạy profile loopback riêng không DSN, không bật cùng DB/ML. Không bỏ guard hoặc coi RAG dev là ACL/tệp production.
- SSO app (PKCE A/B, introspection, registry human/service, policy DB, verified-link, bootstrap) được giữ nguyên. Core principal/binding,2 API identity core, UserContext/JobContext, MFA/expiry thực, provisioning ghi IdP hai chiều còn mở. CODE-ML-01/RAG upstream nằm tầng ứng dụng, không đóng task cha core/CP-001..CP-012.

### Kiểm tra mới trên bản tích hợp

- Ruff check + format72 file đạt; uv lock --check --offline graph142 đạt. Manifest/uv.lock kế thừa pins optional ML/RAG upstream; không thêm graph dependency mới để resolve SSO.
- Unit lần đầu208 pass/13 skip do thiếu rank_bm25. Đã cài numpy2.5.4/rank-bm25 0.2.2 đúng version+hash uv.lock, không cài lại môi trường hoặc tải model. Lượt cuối **221 passed in8.71s**, session51775 exit0, không skip.
- Browser SSO E2E mới **3 passed in25.2s**, session16432 exit0: Keycloak26.8.1 native import realm sạch, API TCP18081, IAM HTTPS19443, browserA/B8761/8762, Supabase thật, không override authentication. Đạt login/profile/PR/idempotency/ownership, ID/refresh-token misuse, SSO/refresh/logout, ADMIN verified-link và AI/PO service quyền đúng.
- Source/wheel build offline đạt **143 source members /55 wheel members**; giữ SSO/RAG/ML/docs/component. Audit actual secret trong config/private state (chỉ so trong bộ nhớ), cache/node_modules/weights/dependency binaries đạt. Giải nén ngoài checkout tại visualizations/sso-origin-clean-verification và rebuild source+wheel offline đạt.
- Python -I import wheel rebuilt, kiểm DB/SSO46 routes và ML51 routes có security/unique operation IDs; RAG_DEV một route development khi DSN trống, config RAG_DEV+DB/ML bị từ chối. Smoke đầu dùng sai tổ hợp flag bị validation guard chặn; đã sửa phép kiểm theo profile upstream, không nới guard. Không gọi model hoặc claim inference/Chroma production đã kiểm tại máy này.
- SQL18 test của lượt SSO trước và model/RAG engine results upstream là lịch sử; không chạy lại toàn integration engine chỉ vì merge. Hướng dẫn SQL regression đã chọn rõ `tests/integration/test_supplier_database.py`, tránh vô tình gọi suite ML/Chroma cần engine riêng.

### Dữ liệu, tiến trình và công việc còn lại

- Fixture mới `.cache/identity-merge-verification` tạo riêng5 user/grant/department và PR UUID, cleanup hoàn tất theo script; fixture.json mới đã gỡ sau cleanup. API70724 và IAM35625 đã dừng Ctrl+C sau test, Playwright dọn client servers. Không dừng server/process của người dùng.
- `.cache/identity-final/fixture.json` có sẵn vẫn được giữ nguyên, không query/cleanup hoặc tái tạo profile này. Đối soát state/DB theo UUID trước retry test thủ công. Không migrate/seed/reset, không sửa user/grant cũ hoặc .env, không đổi Java/trust store hệ thống.
- SSO,3 file merge và hướng dẫn/evidence đã commit13d0fff (42 file). docs/CHECKPOINT.md đang tracked upstream nhưng được chỉ định local AI-support trong info/exclude nên giữ ngoài commit; working tree chỉ còn checkpoint này sửa. Backup/stash vẫn tồn tại cho đến khi người dùng chủ động bỏ. Không có task code core mới hoặc tiến trình test/build/migration do agent còn dở.
- Đã cập nhật PLAN-OPENCORE, PLAN-SSO, TASK-API-TEST, hai checkpoint và bản xuất `05-bao-cao/Ke-hoach-Tang1-OpenCore-2026-10-16.md` cùng syncID02. Snapshot và lịch sử trước merge giữ nguyên; branch/HEAD trong báo cáo lịch sử không bị thay thành bản mới.
- Bước code tiếp theo giữ **CODE-01-02.00 → CODE-01-01.01 + phần core CODE-01-02.01/.02 → CODE-01-03.01/.02**, rồi Gateway. Người dùng đã yêu cầu commit và xác nhận rõ push lên origin/huuhuandev_features; commit hoàn tất, push còn chờ credential. Không force/publish release/deploy.
- Kiểm cuối bàn giao: source archive cuối vẫn143/55 và audit secret/bundling đạt; Git không unmerged/staged path, đủ43 file SSO, tất cả origin source/target/main là ancestor HEAD. Link tài liệu/sync hai checkpoint/git diff --check đạt; 0 listener test19443/18081/8761/8762. Branch hiện tại huuhuandev_features; không còn session agent chạy.

### Commit và push theo yêu cầu mới

- Người dùng đã yêu cầu commit lên origin. Preflight xác nhận42 file SSO/project review được, không actual secret/cache/dependency binary/marker conflict; giữ docs/CHECKPOINT.md và .ai-local AI-support ngoài commit theo local exclude. Test/runtime không đổi sau221 unit/3 E2E đã đạt ở lượt tích hợp, không chạy lại engine chỉ để commit.
- Fetch trước push ngoài sandbox không tương tác vẫn thiếu credential (không thể prompt password). Chưa coi fetch/push thành công; sẽ đã tạo commit local được yêu cầu rồi thử push bình thường, không force. Nếu credential tiếp tục chặn thì cần người dùng đăng nhập GitHub qua terminal của máy.

- Commit mới **13d0fff0594325894bd991b27baf750a2d41f893**, message `feat(identity): integrate Keycloak SSO with Supabase APIs`,42 file/2041 insertions/28 deletions. Preflight secret scan và staged diff check đạt; không commit checkpoint/plan AI local hoặc secret/cache. Backup/stash giữ nguyên.
- Push đầu bị auto-review chặn vì cho rằng “commit lên” chưa rõ cho phép push/đích remote chưa xác minh. Đã hỏi và nhận user xác nhận rõ `Cho phép push lên origin/huuhuandev_features`; retry đúng lệnh push thường được duyệt, nhưng Git báo thiếu credential, không thể prompt password. Không bypass review, không dùng force hoặc chuyển remote.
- Đã nhờ người dùng chạy `git push origin huuhuandev_features` trong terminal workspace và hoàn tất đăng nhập. Chưa nhận outcome mới; cần kiểm refs/reflog sau phản hồi trước kết luận đã push. Không yêu cầu token/password; không ghi credential vào repo/checkpoint.
<!-- END CURRENT SYNC-2026-10-16-OPENCORE-03 -->

## Nhật ký trước khi chốt tích hợp origin

Phần sau là lịch sử, gồm trạng thái plan-only, session đang chạy hoặc bước dự kiến trước khi có outcome cuối. Không dùng các chỉ dẫn cũ để tiếp tục.

### Trạng thái hiện hành sau đồng bộ kế hoạch

Đồng bộ **SYNC-2026-10-16-OPENCORE-01**, ngày 16/10/2026 Asia/Saigon. Đây là phần bàn giao hiện tại; toàn bộ nhật ký phía dưới là lịch sử và không thay thế phần này.

#### Mục tiêu, quyền và Git

- Yêu cầu hiện hành: xuất kế hoạch tiếp Tầng 1 và đồng bộ toàn bộ plan/checkpoint. Lượt này chỉ sửa tài liệu; không bắt đầu code các API core mới, không commit/push/deploy, không sửa .env, cài lại môi trường, migrate/seed/reset hoặc tự cleanup dữ liệu test.
- Repo `E:/CTU/CLB_Tin_hoc/OLP/FlowBridge`, branch `hoanganhdev_features`, HEAD `a945f1ccaccf7391157aae626dfcb93c36bc8b2e` + working-tree diff SSO có sẵn. Git status đã kiểm lại; giữ nguyên code/config/test chưa commit của người dùng. Các checkout/HEAD cũ trong nhật ký không phải hiện trạng.
- Nguồn roadmap: `OLP/.ai-local/FlowBridge/PLAN-OPENCORE.md`; test/task con: `TASK-API-TEST.md`; chi tiết SSO: `PLAN-SSO.md`; bản xuất review: `OLP/05-bao-cao/Ke-hoach-Tang1-OpenCore-2026-10-16.md`. Phần hiện hành của hai checkpoint dùng cùng sync ID/nội dung; nhật ký lịch sử mỗi file giữ riêng.

#### Hiện trạng code và evidence

- CODE-DB-01/02 đã hoàn tất scope persistence Supabase/bỏ sample. Runtime có 46 operation app/AI v0.4.1: 38 có đường DB/directory nếu đủ cấu hình, 8 chờ adapter. 28 operation core0.2.0 là thiết kế, chưa mount; số đường xử lý không là bằng chứng nghiệm thu từng operation.
- SSO app đã có Keycloak PKCE A/B, introspection RFC7662, registry human/service, policy DB, directory verified-link và bootstrap dry-run/apply. CODE-01-01.02 adapter đạt scope Supabase; CODE-01-02.01/.02 và CODE-01-04.01/.02 chỉ đạt một phần core. Còn hai Identity API core, principal tenant/app/scope, delegated/job context, MFA/expiry thực; provisioning chỉ link user IdP có sẵn, chưa ghi IdP/password/reconcile hai chiều.
- PR 00 còn môi trường tái tạo/CI/CONTRIBUTING/spike engine; PR 02 chưa có APISIX runtime; PR 03–09 chưa có core registry/Data/Files/workflow/broker/dispatcher dùng chung; PR 10/11 chưa nghiệm thu demo bốn lõi/production/release. Không tick task cha hoặc CP-001..CP-012 từ evidence ứng dụng.
- Evidence lượt code SSO: 129 unit (5.49s), 18 Supabase SQL (160.54s), 3 browser E2E IAM sạch (24.5s), 10 contract/route (2.81s); Ruff44/lock44, archive96 source/34 wheel, audit secret/bundling và clean rebuild/isolated import/OpenAPI46 đạt. Chi tiết tại `FlowBridge/docs/SSO-TEST-RESULTS.md`; đây là kết quả lịch sử trên diff SSO, không chạy lại hoặc nhận thêm pass trong lượt đồng bộ tài liệu.
- Migration head gần nhất đã xác minh trong lượt SSO là `0002_api_idempotency`; lượt này không query DB live hoặc kiểm drift. Không schema/dependency/runtime thay đổi do đồng bộ plan. ADR hiện hành `FlowBridge/docs/SSO-ADR.md` chọn introspection thay JWT/JWKS cũ; engine khác vẫn là ứng viên cần version/license/OSS/spike trước dùng.

#### File đã đồng bộ và quyết định

- Sửa PLAN-OPENCORE, PLAN-SSO, TASK-API-TEST ở `.ai-local/FlowBridge`; cập nhật hai checkpoint và xuất `05-bao-cao/Ke-hoach-Tang1-OpenCore-2026-10-16.md`. Không sửa ngược snapshot/evidence/đánh giá cũ. Chỉ dẫn sample trong các phần lịch sử được đánh dấu hết hiệu lực; sample runtime không khôi phục.
- Giữ ID cũ; thêm duy nhất task mới `CODE-01-02.00` để chốt tenant/application binding và mapping quyền app → core. Không dùng tenant giả để đáp ứng DTO. Nếu cần registry tối thiểu có migration, làm phần CODE-03-01 liên quan trước `/identity/me`; full multi-tenant chỉ nhận sau policy/test hai tenant thật.
- Thứ tự: binding + Identity core → Gateway → registry/Data core → Files/scanner/search → Workflow + Events → demo; ingestion/lineage/archive sau Data và nghiệm thu theo scope; ops/restore/release làm xuyên suốt rồi chốt cuối. Chia CODE-09 thành nền dispatch/inbox và bridge để retry không đi sau tác động ngoài.

#### Tiến trình và state cần giữ

- Kiểm local listener tại thời điểm đồng bộ: lệnh trong sandbox không đọc được TCP state (MethodInvocationException); kiểm lại read-only ngoài sandbox thành công, 0 listener test trên 18443/18080/8761/8762. Đây không chứng minh không có tiến trình khác hoặc dịch vụ remote; không dừng server/process của người dùng.
- **Profile `.cache/identity-final` hiện có `fixture.json` trở lại.** Evidence cleanup SSO trước đây vẫn là lịch sử; fixture mới/chưa rõ outcome phải đối soát với DB theo UUID trước retry setup hoặc cleanup. Lượt này chỉ kiểm file tồn tại, không đọc secret, không query/ghi DB hoặc xóa state. `.cache/identity-verification` không có fixture state; profile mặc định `.cache/identity` chưa có operator config.
- Không có command build/test/migration do agent khởi chạy đang dở trong lượt đồng bộ. Kết quả chạy thử thủ công của người dùng chưa được thu thập; không gán pass/fail từ sự tồn tại fixture.json.

#### Kiểm tra lượt tài liệu và bước tiếp

- Static validation đã đạt: liên kết Markdown của plan/bản xuất và phần checkpoint hiện hành, sync ID và hai block checkpoint giống nhau, bảo toàn toàn bộ ID tham chiếu cũ/63 checkbox cha (11 tick lịch sử), số core/app/unavailable=28/46/8 từ contract/source, 83 file runtime byte-hash không đổi và git diff --check. Lưu script/report riêng bị ignore tại `.cache/check_plan_sync.py` và `.cache/plan-sync-validation.json`; không đóng gói vào source. Không chạy pytest/E2E/build/engine vì thay đổi chỉ là plan/checkpoint.
- Bước tiếp cụ thể khi triển khai: đọc phần hiện hành này, Git status/branch/HEAD và file thực tế; làm CODE-01-02.00 rồi CODE-01-01.01 + phần core CODE-01-02.01/.02 + CODE-01-03.01/.02. Dùng TASK-API-TEST cho expected; không chạy lại migration/seed hoặc sample. Trước test có DB writes phải đối soát fixture đang tồn tại.
- Giữ CP snapshots cũ; chưa tạo CP mới vì chưa đạt mốc runtime. Các mục thiếu production/MFA/provisioning hai chiều vẫn mở theo plan, không dùng giới hạn của lượt tài liệu để ngăn công việc người dùng yêu cầu sau này.

#### Chuyển nhánh và tích hợp origin theo yêu cầu mới

- Người dùng yêu cầu kéo 5 commit origin, giải conflict và chuyển SSO sang nhánh của mình. Đã xác minh tên thực tế là `huuhuandev_features` (không có nhánh `huuhuandev_fearture`). Quyền mới cho phép fetch/switch/integrate/resolve cần thiết, thay giới hạn doc-only ở đầu checkpoint; chưa yêu cầu push hoặc publish.
- Đầu vào: source `hoanganhdev_features` / a945f1c; target `huuhuandev_features` / d396441. Cached origin source aebfe85 có 5 commit mới (RAG/ML + merge); target hiện là ancestor của cached source, không có local-only commit cần viết lại.
- Đã backup 43 file modified/untracked và tracked binary diff, kèm SHA-256 manifest tại OLP/.ai-local/FlowBridge/git-backups/20261016-020607-sso-transfer. Không sao chép nội dung .env/credential/state local; chỉ hash để đối chiếu. Không xóa stash cũ.
- Fetch trong sandbox lỗi DNS; fetch ngoài sandbox chờ credential, đã dừng session8599. Fetch không tương tác xác nhận thiếu đăng nhập GitHub; đang chờ người dùng chạy git fetch origin trong terminal và đăng nhập, không yêu cầu gửi secret. Không coi remote-tracking cache là fetch mới thành công.
- Trước thao tác tiếp: lưu toàn bộ diff/untracked bằng stash riêng, ghi OID; cập nhật source/target theo cached refs an toàn rồi apply SSO trên target để resolve/review. Nếu origin có ref mới sau đăng nhập phải đối chiếu/integrate thêm trước kết luận. Không reset/clean, không migrate/seed hoặc tác động fixture hiện có.

#### Tiến độ tích hợp SSO vào huuhuandev_features

- Người dùng xác nhận đã fetch thành công. Đã kiểm refs/FETCH_HEAD sau xác nhận: origin/hoanganhdev_features=aebfe85, origin/huuhuandev_features=d396441, không có commit mới của origin source/target/main chưa nằm trong HEAD tích hợp. Fetch mặc định ban đầu thiếu credential không tính là thành công; không yêu cầu người dùng gửi token.
- Đã stash toàn bộ 43 file modified/untracked, OID `f034e3e8502fe50f6634cfa8ab1c01ddaf1563c6`, giữ stash để khôi phục. Source hoanganhdev_features fast-forward a945f1c→aebfe85, target huuhuandev_features fast-forward d396441→aebfe85, sau đó apply stash lên target. Target ahead origin/huuhuandev_features 10 commit (các commit đã có từ upstream), chưa push hoặc tạo commit SSO.
- Resolve 3 conflict CHANGELOG/pyproject/checkpoint bằng giữ cả RAG/ML upstream và SSO local; không còn unmerged path. Auto-merge main/settings được review để giữ ML/RAG lifecycle/router và CA/CORS/SSO. 43 file SSO có đủ, 36 file giống bản backup sau chuẩn hóa LF, 7 file được ghép; 47 file added upstream ngoài diff local giữ nguyên byte sau LF normalization. .env và fixture hiện có giữ nguyên SHA-256.
- Ruff check và format72 đạt, uv lock --check --offline graph142 đạt; build offline source+wheel vào .cache/sso-origin-merged-build thành công. Unit lần đầu 208 pass/13 skip vì thiếu optional rank_bm25. Đã cài đúng numpy2.5.4/rank-bm25 0.2.2 theo pin+hash uv.lock, không đổi manifest/lock hoặc tải model; sắp chạy lại unit để có outcome đủ13case, chưa nhận pass lần mới.
- Trước kiểm SSO thật: dùng profile test mới `.cache/identity-merge-verification`, IAM HTTPS19443 và API18081, cùng client8761/8762. Profile/namespace riêng với `.cache/identity-final/fixture.json` của người dùng; không sửa/xóa fixture cũ. Sẽ tạo và cleanup đúng UUID do suite mới tạo, không migration/seed/reset. Chưa khởi động các server/suite mới ở thời điểm ghi dòng này; cần ghi session/outcome và cleanup trước kết thúc.


## Nhật ký lịch sử trước lần đồng bộ này

Các trạng thái như sample, branch cũ, chưa có SSO, session đang chạy hoặc bước tiếp trong phần này chỉ có giá trị tại thời điểm ghi. Đọc phần hiện hành phía trên để tiếp tục; không thực hiện các prompt/lệnh lịch sử đã bị thay thế. Giữ nguyên nội dung diễn tiến và snapshot, không nhận lại test cũ là vừa chạy.

### Lịch sử từ origin đã tích hợp (RAG/ML)

Các kết quả bên dưới do upstream ghi trên môi trường khác, chưa chạy lại tại máy này.

### 2026-10-16 — CODE-ML-01: Prediction Service (hoàn tất code và kiểm thử)

- Yêu cầu: triển khai hai luồng ML cho custom node sau khi pull code mới; rủi ro PO
  đại diện bằng MAX điểm các PO item còn phải giao, kèm item/deadline quyết định.
- Repo `D:\OLP MNM 2025\FlowBridge`, branch `hoanganhdev_features`, HEAD đầu vào
  `ae20b282f4009634f97ddede1396f1e53397732c`; Git sạch khi bắt đầu. Không có plan/task
  được dẫn chiếu trong checkout; dùng ID bổ sung CODE-ML-01.
- Đã đọc hướng dẫn `D:\OLP MNM 2025\AGENTS.md`, checkpoint và auth/repository hiện có.
  Tích hợp cùng package và chính sách DB/IdP; không tạo microservice hoặc giả danh.
- Đã thêm `supplier_assistant/prediction/`: 19 features, SQL repository có giới hạn,
  context có provenance/cutoff, TabICLv2 adapter, API scenario/rank, lưu/đọc PO risk.
  Runtime bật bằng ML_ENABLED, giữ hợp đồng AI 0.4.1 cũ và RAG hiện có.
- Không đổi schema/migration, `.env`, dữ liệu nghiệp vụ, không commit/push/deploy.
  Dependency optional `ml`: tabicl 2.2.0 (BSD-3-Clause), pandas 2.3.3 (BSD-3-Clause);
  checkpoint upstream BSD-3-Clause, tải riêng ngoài source. uv lock cập nhật 142 packages.
- `uv lock` và `uv sync --locked --extra ml --inexact` hoàn tất ngoài sandbox; mạng
  sandbox chặn registry. Lock 142 packages, thêm 6 package vào graph đã có RAG. Không
  cài lại môi trường hoặc đổi pin dependency RAG.
- Đã tải checkpoint chính thức vào `.cache/models`, SHA-256
  `bdc7dbd5e4ff21f8f0456fcf90c6b7cdf72dbea960f2d05b19bec19f9b3d4ed0`.
  Context `.cache/ml/context.json` là 192 snapshot A/B giả lập riêng, có source_kind;
  không lấy CSV simulator lỗi làm context. Chỉ dùng smoke/integration; không có bằng
  chứng calibration/chất lượng nghiệp vụ, production chặn context synthetic.
- Model smoke chạy fit + predict_proba TabICLv2 thật thành công; startup thử riêng A
  categorical toàn NULL. Model/context nạp một lần, inference ở thread có serialization
  và giới hạn queue. Không tải model từ HTTP/startup, không đọc pickle do client cung cấp.
- HTTP: scenario/rank read-only; PO POST synchronous trả 200 sau commit (dependency
  function scope), giữ risk_predictions + durable retry atomically; GET theo quyền/
  freshness/context hash/checkpoint/policy. Không tạo report/proposal/analysis task giả.
- Thêm typed HTTP client, component Langflow ranking chỉ đọc, CLI download/demo/
  validate/smoke, docs/ML-SERVICE.md, ví dụ request, env template, gateway --include-ml,
  README/CHANGELOG. API AI 0.4.1 cũ vẫn fail closed, không thay wire contract cũ.
- `pytest tests/unit -q -rs`: **186 passed, 13 skipped in 8.45s**. 41 test ML mới đều
  qua; 13 skip nằm ở test_knowledge.py do thiếu optional rank_bm25 có sẵn từ code RAG.
  Không coi skip là test RAG đã qua. Ruff check + format (18 file thay đổi/code ML)
  và git diff --check đạt; uv lock --check --offline đạt.
- `pytest tests/integration/test_prediction_database.py -m integration`: **2 passed
  in 27.94s** ngoài sandbox. SQL và TabICL thật: HTTP rank/scenario qua lifecycle thật,
  read-only không tăng rows, PO persistence, cùng key không ghi trùng, ACL và rollback
  khi inference lỗi. Test tiêm Identity/approval, không chứng minh SSO/workflow thật.
  Fixture write rollback; không sửa/nạp lại dữ liệu demo. Không chạy migration/seed.
- Build offline wheel + source tar.gz đạt `.cache/ml-build/`; audit 118 source/52 wheel
  members, một source root, có ML docs/component, không secret/cache/weights/dependency
  binary. Import wheel bằng Python -I và validate OpenAPI đạt (45 paths/51 operations).
- Không còn session kiểm thử/download đang chạy. Chưa khởi động server lâu dài,
  sửa .env, cấu hình IdP, test trong Langflow UI, calibration/evaluation hoặc scheduler.
  Kiểm tra Settings cuối (chỉ boolean): DB đã cấu hình; ML_ENABLED=false,
  DOCS_ENABLED=false, 5 trường OIDC chưa đầy đủ. Không đưa credential vào log.
  Để dùng: cài extra ml, bật ML_ENABLED/DOCS_ENABLED, cấu hình IdP và mapping/grants
  theo docs, restart backend; dùng context observed đã review cho nghiệp vụ thật.

### 2026-10-16 — Giải quyết conflict PR #4 (RAG development)

- Yêu cầu: sửa conflict của PR #4 hiện có, cập nhật nhánh minnhungdev_features; không merge PR vào main.
- Head đầu vào: d3ce7e7; main được gộp: f24a2c9. Làm trong worktree riêng; giữ nguyên checkout hoanganhdev_features của người dùng.
- Đã xử lý 6 file conflict: .env.example, CHANGELOG.md, README.md, main.py, settings.py, pyproject.toml; bỏ cả marker cũ đã tồn tại trong CHANGELOG của main.
- Giữ API PostgreSQL, bearer introspection và OpenAPI từ main. RAG development giữ lifecycle, loopback guard và DSN trống; kiểm tra guard sau khi Settings ghép DSN từ host/user/password.
- Không khôi phục API/sample modules đã bị main loại bỏ. Router RAG dùng Header của FastAPI thay import shared.http đã bị xóa; bỏ nhánh Swagger Core sample.
- Cập nhật tests/unit/test_knowledge_http.py và examples/rag/README.md theo cách chạy riêng; giữ nguyên bằng chứng thử Qwen trước merge và ghi rõ thời điểm.
- Kiểm tra: 158 unit test pass, không skip; Ruff check/format pass; uv lock --check --offline pass. Hatchling build wheel và source tar.gz pass; kiểm nội dung archive và import wheel ngoài source, bootstrap/database/RAG OpenAPI pass.
- Sandbox Windows làm asyncio treo khi tạo socket nội bộ; đã dừng các lượt đó và chạy lại unit suite ngoài sandbox thành công. rank-bm25 dùng từ thư mục test tạm, không sửa môi trường đang dùng.
- Chưa chạy Supabase integration, migration/seed, Chroma thật hoặc tải/chạy Qwen; không thay dependency pin hoặc schema. Lockfile được Git tự gộp và kiểm tra đạt.
- Không có PLAN-OPENCORE.md/TASK-API-TEST.md trong snapshot để gắn ID CODE; phạm vi theo PR #4. Bước cập nhật Git: tạo merge commit từ bản đã kiểm tra, push thường vào minnhungdev_features rồi xác minh PR/remote SHA; không force-push.

### 2026-10-16 — SSO: hoàn tất phạm vi cơ bản và kiểm build

- Người dùng yêu cầu code SSO và bỏ sample identity; plan không còn là giới hạn chỉ đọc. Đã đọc plan/checkpoint/rules, xác minh checkout `huuhuandev_features` / `d396441` sạch và chuyển sang baseline Supabase sẵn có `hoanganhdev_features` / `a945f1ccaccf7391157aae626dfcb93c36bc8b2e`. Không merge/reset/commit/push. HEAD được commit bởi thao tác ngoài agent trước lượt này; thông tin conflict/staged của checkpoint cũ cần đối chiếu hiện trạng.
- CODE-01-00: baseline đúng API Supabase, sample identity/runtime đã được bỏ ở commit này; sẽ kiểm regression trên code mới. CODE-01-04.00 và CODE-01-01.02 bắt đầu: chuẩn bị ADR/config Keycloak, review adapter introspection, bootstrap/link user/grant, browser clients và test thật. Giữ ranh giới headless và không tự viết engine xác thực.
- Môi trường: có Node/npm/OpenSSL và Java launcher; không tìm thấy Docker/Podman trên PATH. Lượt kiểm Java trong sandbox session `47898` không trả output, đã dừng Ctrl+C. Kiểm metadata/version ngoài sandbox session `69644` đang chạy: Java hiện có 26.0.2.1, Node 24.19.0, npm metadata chưa có outcome. Kiểm outcome trước retry, không tự cài Docker hoặc thay Java hệ thống.
- Chưa sửa dependency/schema/migration hoặc `.env`, chưa chạy migration/seed/IdP/DB writes. Chỉ kiểm công cụ, đọc source và ghi checkpoint. Bước tiếp: chọn version/license upstream, thêm config/adapters/bootstrap và tests, ghi chính xác phần có/không có evidence engine thật.
- CODE-01-04.00: đã pin Keycloak 26.8.1 (Apache-2.0), Keycloak JS 26.2.4 và Playwright 1.64.1 (Apache-2.0), Temurin 25.0.4.1+1 để nghiệm thu native. Metadata upstream và SHA-256 tại `infra/identity/dependencies.json`; tải/kiểm hash thành công, package ở `.cache/sso-downloads`, giải nén `.tools/sso` (đều Git ignore). Không thay Java hệ thống; không có Docker. Session metadata `69644` và download `71615` đã kết thúc exit 0.
- Code đang làm: authentication kiểm access-token type/client/nbf và phân biệt human/service; Settings thêm registry/CA/admin/CORS; main dùng CA qua SSL chuẩn và CORS allowlist. Thêm directory adapter chỉ đọc user đã có tại Keycloak, accounts link/grant và bootstrap có rollback mặc định. Provision API chỉ liên kết identity đã xác minh, không tạo mật khẩu hoặc ghi IdP; retry DB giữ nguyên profile/grant và idempotency transaction hiện có. Không có schema/migration mới.
- Infra/client: một realm template không chứa credential thật, local TLS preparation và launcher chuẩn upstream, client A/B dùng SDK chính thức. Chưa chạy test mới hoặc nhận mốc A/B/C. Trước lệnh dài: chuẩn bị npm lock/install trong client riêng, tạo profile test bị ignore và khởi động Keycloak native TLS local để kiểm integration thật; không sửa `.env` hoặc seed DB.
- IAM local đang chạy session `25372`: Keycloak 26.8.1/Temurin25, import realm thành công, HTTPS loopback 18443; chế độ dev cũng mở HTTP loopback 8080 theo log upstream, không coi đây là cấu hình production. Profile/CA/credential riêng ở `.cache/identity-verification`, không ghi giá trị vào checkpoint. npm session `72194` đã hoàn tất; 4 package JS cài theo lock, license Apache-2.0.
- Sắp chạy unit mới và fixture browser: fixture xác minh 3 tài khoản test thật qua directory client rồi tạo riêng department/users/grants UUID; có `fixture.json` để cleanup đúng ID nếu gián đoạn. Không override authentication, không seed/reset/migration, không cấp quyền hoặc sửa các user cũ. Suite E2E sẽ gọi listener API thật trên port 18080, hai client 8761/8762, profile TLS tin CA riêng; trace/video/screenshot tắt để không lưu token.
- Unit mới **128 passed in 4.69s**, lint đạt. Fixture E2E đã tạo thành công sau khi xác minh metadata IdP thật; cần cleanup bằng `tests/e2e/identity_fixture.py --identity-dir .cache/identity-verification --cleanup` khi dừng test. API session `4373` đang chạy trên 18080; IAM session `25372` vẫn chạy. Không dừng server 8000 của người dùng.
- Browser lần đầu bị required action VERIFY_PROFILE do fixture thiếu email/name, đã sửa profile generation và test hoàn tất form upstream. Các lần tiếp phát hiện user token active nhưng thiếu sub do custom client scope không chứa subject mapper. Backend từ chối 401 đúng; đã thêm protocol mapper `oidc-sub-mapper` chuẩn vào realm template và áp dụng bằng Admin API lên đúng realm local, không bỏ guard. Admin credential chỉ dùng cấu hình IAM local, không dùng password grant thay browser SSO. Các lần lỗi không tính nghiệm thu.
- Sắp chạy lại E2E sau fix config và suite SQL integration **18 cases dự kiến** (15 baseline + 3 bootstrap): chưa có kết quả các lượt này. Test SQL bootstrap chỉ chứng minh transaction/policy, còn E2E dùng IdP/token/listener/DB thật và không override auth. Cần kiểm outcome và cleanup fixture trước retry/đóng lượt.
- SQL regression session `89163`: **18 passed in 160.54s**. Browser phiên bản có human/service: **3 passed in 23.3s**, session `36954`; gồm PKCE, API persistence/idempotency/ownership, SSO A→B, refresh/logout, ADMIN verified link, service client đúng role và không giả user. Unit sau service-binding fix: **128 passed in 5.04s**. Đã bổ sung thêm guard typ Refresh và test dùng refresh token làm bearer; chưa kiểm lượt cuối cho guard này.
- Directory GET /users thực tế không có serviceAccountClientId; đã sửa dùng endpoint client/service-account-user chuẩn, cần view-users + view-clients read-only. Native realm local đã cập nhật đúng quyền; nguồn template có subject mapper và quyền đọc. Lượt chuẩn bị service fixture đầu fail trước ghi DB; sau fix đã tạo 5 user fixture riêng (3 human + AI/PO), chưa cleanup lượt hiện tại.
- Sắp kiểm tái lập từ state IAM sạch: cleanup fixture cũ, dừng API session `97138` và IAM `25372`, tạo `.cache/identity-final` từ generator hiện tại, chạy native với H2 đặt riêng theo IdentityDir (tránh profile khác dùng lẫn DB/secret), rồi E2E lần cuối. Không reset/xóa IAM cũ hoặc dữ liệu ứng dụng có sẵn; không sửa `.env`, không migration/seed. Cần ghi outcome/session mới và cleanup đúng fixture cuối.

- Kết quả lượt cuối trên `.cache/identity-final` sinh từ template sạch: E2E **3 passed in 24.5s** (session 77154), unit **129 passed in 5.49s** (67521), SQL regression giữ kết quả **18 passed in 160.54s** (89163). Contract/route sau đổi example UUID **10 passed in 2.81s**. Directory binding dùng API chuẩn, view-users/view-clients; native H2 qua env JDBC để tránh .bat tách tham số.
- Cleanup đúng 5 user fixture đã hoàn tất, SQL read-only xác nhận không còn human test identities và counts suppliers=6/products=6/purchase_requests=1000. API 72419 và IAM 12989 đã dừng Ctrl+C; client servers do Playwright dọn. Không còn session test/server đang chạy, không dừng server 8000 của người dùng.
- Ruff lint/format đạt 44 file; uv lock offline đạt 44 package. Không Python dependency/schema/migration mới, không sửa .env, không seed/reset. Bỏ đúng 3 marker merge đã commit sẵn trong CHANGELOG, giữ cả hai phần lịch sử. Canonical contract example UUID được sync/checksum, không đổi schema/wire API.
- ADR và evidence triển khai được đặt tại `docs/SSO-ADR.md`, `docs/SSO-TEST-RESULTS.md`, tránh các thư mục AI-support bị local Git exclude. Sdist chỉ include đúng tài liệu này thay vì cả docs/adr/evidence local. Trước lệnh dài: build cuối offline vào .cache/sso-build, audit secret/bundling, giải nén vào workspace visualizations mới và rebuild ngoài source; chưa nhận kết quả audit/rebuild cuối.

- Build offline cuối đạt **96 source members / 34 wheel members**; audit secret/bundling/GPL notice 14 code file mới đạt. Giải nén ngoài repo tại visualizations/sso-clean-verification, rebuild source+wheel offline đạt; Python -I import wheel rebuilt và tạo OpenAPI đủ 46 operation có security, không sample context. Kết quả/tài liệu/task được cập nhật trong docs/SSO*.md và .ai-local/FlowBridge; chỉ refresh archive do thay tài liệu, không chạy lại engine suites không bị ảnh hưởng.
- Phạm vi hoàn tất: SSO PKCE hai client, introspection/registry, policy DB human/service, directory verified-link và bootstrap CLI. Chưa nhận MFA/expiry policy thực, provisioning ghi IdP hai chiều, production hoặc core DTO/delegated/job context/CP-002. Không có tác vụ runtime dở; bước tiếp review diff/hướng dẫn chạy, không tự commit/release.

- Kiểm cuối sau cập nhật tài liệu: archive vẫn 96/34 members và audit secret/bundling đạt; clean rebuild tại `visualizations/.../sso-final-clean-verification` + isolated wheel import/OpenAPI46 đạt, exit 0. `git diff --check` và local Markdown links đạt. Không listener test trên 18443/18080/8761/8762, không fixture.json còn chờ cleanup. Tất cả kiểm tra cần cho scope này hoàn tất; code sẵn review, chưa commit/push/deploy.

### 2026-10-16 — Chẩn đoán 404 khi mở Swagger

- Người dùng xác nhận mở `http://127.0.0.1:8000/docs` bằng trình duyệt và nhận NOT_FOUND. Repo/branch/HEAD vẫn là FlowBridge / `hoanganhdev_features` / `928ebfa91a2f7ca2b94d4bd366f95acc3411f008`; giữ nguyên staged changes và conflict CHANGELOG.
- Settings trong tiến trình kiểm tra: database đã cấu hình, docs_enabled=False, IdP chưa cấu hình; chỉ đọc boolean, không hiển thị credential. `main.py` đặt docs_url/openapi_url=None khi docs tắt; đây là nguyên nhân phù hợp với 404 được báo. Biến môi trường của server người dùng có thể khác tiến trình kiểm tra.
- Hướng xử lý: dừng server cũ, đặt `FLOWBRIDGE_DOCS_ENABLED=true` trong PowerShell hoặc `.env`, khởi động lại rồi mở `/docs`. Không đổi source/runtime, `.env`, migration hoặc dữ liệu DB.
- Smoke ASGI offline trong sandbox session `60874` không trả output, đã dừng Ctrl+C (exit 1); không nhận test đã pass, không còn session chẩn đoán chạy. Không chạy lại suite/integration cho việc hướng dẫn cấu hình này. Bước tiếp theo: người dùng khởi động server với docs bật và kiểm tra trình duyệt; bearer/IdP vẫn cần cho API bảo vệ.

### 2026-10-15 — CODE-DB-02: Hoàn tất bỏ API sample, chỉ giữ API Supabase

- Yêu cầu mới thay phạm vi rà soát: người dùng yêu cầu chỉ giữ API dùng Supabase và giải thích phần cần bổ sung để API không bị chặn. Xóa runtime/fixture/config/sample tooling cũ; giữ kiểm quyền, migration, ORM và seed database. Không triển khai IdP/workflow/model khi chưa có cấu hình hoặc adapter thực tế.
- Repo `E:\CTU\CLB_Tin_hoc\OLP\FlowBridge`, branch `hoanganhdev_features`, HEAD `928ebfa91a2f7ca2b94d4bd366f95acc3411f008`. Giữ staged changes và conflict CHANGELOG có sẵn; không reset/commit/push. Plan/task/rules nằm ở `.ai-local/FlowBridge/` ngoài checkout; đã đọc mục sample CODE-00-07..14 và checkpoint, không tick nghiệm thu tích hợp.
- Rà AST 75 file Python và references: sample chỉ mount qua `main.py`; test Supabase có phụ thuộc `SampleSupplierAdapter` cho 4 payload. Bước tiếp theo: chuyển 4 payload cần thiết vào tests, bỏ sample runtime/test/tooling, giữ và bổ sung test contract/auth/route cho database.
- Chưa sửa runtime/dependency/migration hoặc chạy test trong task này. Trước kiểm tra dài sẽ chạy Ruff, unit không mạng, build/archive sạch; integration chỉ khi cần kiểm phần thay đổi với database, không chạy seed/migration lại. Chưa có tiến trình kiểm thử đang chạy.
- Thiếu để mở API: 5 biến OIDC RFC7662 và issuer+subject/user/grant thật; workflow authoritative, IdP provisioning, chat/model/document adapters cho 9 operation trả 503; durable outbox/email/analysis workers cho tác động bên ngoài. Không thay bằng HTTP impersonation.
- Đã xóa 45 file sample runtime/test/example/tooling và snapshot core khỏi package; giữ contract core ở `contracts/` làm thiết kế. Main chỉ ghép router database; Settings bỏ 2 trường sample. Gateway map app/AI, tách public/internal/admin. Chuyển đúng 4 payload còn dùng sang `tests/integration/analysis-payloads.json` và notice riêng, sửa integration để không import sample adapter.
- Dependency: bỏ `python-multipart` khỏi manifest, `uv lock --offline` với `.cache/uv` thành công (44 package). Thử `.cache/uv-build` trước đó fail vì thiếu cache index; không cài lại môi trường hoặc truy cập registry. Không thay migration/schema.
- Tài liệu README/Supabase/Alembic/CHANGELOG đang cập nhật. Giữ nguyên conflict marker CHANGELOG. Sửa mô tả sample đã lỗi thời trong nguồn `03-api/contracts/{app-api,ai-tools-api}.yaml`, đồng bộ snapshot/checksum bằng script; không đổi wire DTO/path/operation ID.
- Xác nhận bằng Settings: DB đã được cấu hình; cả 5 biến OIDC chưa cấu hình (chỉ kiểm boolean, không ghi giá trị). Chưa truy cập hoặc sửa dữ liệu Supabase trong task này.
- Trước kiểm tra dài: chuẩn bị chạy Ruff và toàn bộ unit offline, default collection loại integration. Sau đó kiểm archive/build sạch, và suite Supabase nếu thay đổi fixture cần kiểm chứng. Chưa có kết quả test mới ở checkpoint này.
- Ruff lint/format hiện đạt 40 file; `uv lock --check --offline` đạt 44 package. Lượt unit trong sandbox session `57607` không trả output, đã dừng Ctrl+C (exit 1); chưa có kết quả test, không chạy đồng thời lại. Sắp chạy lại unit với timeout chẩn đoán ngoài sandbox; không đổi môi trường hoặc chạy seed/migration.
- Unit ngoài sandbox hoàn tất **107 passed in 4.33s**, exit 0. Trước lệnh integration dài: chạy `pytest tests/integration -m integration` để xác minh payload vừa chuyển; phần lớn transaction rollback, case commit/concurrency có cleanup UUID riêng. Không chạy seed/migration hoặc sửa dữ liệu demo; đầu vào vẫn revision 0002 đã xác nhận ở CODE-DB-01. Sắp build vào `.cache/supabase-only-build` và kiểm archive sạch ngoài source tree.
- Build offline/no-build-isolation thành công source tar.gz và wheel tại `.cache/supabase-only-build/`; audit **71 sdist members**, **31 wheel members**, một root `flowbridge-0.1.0`, không sample runtime/cache/venv/dependency binary/secret. Wheel không có test fixtures hoặc `python-multipart` trong METADATA. Giải nén sạch tại workspace visualizations `flowbridge-supabase-only-verification/flowbridge-0.1.0`, rebuild offline thành công; Python `-I` import trực tiếp rebuilt wheel và tạo app/Swagger đủ 46 operation, giữ security, không sample context. Đây là build verification, không release/publish; CHANGELOG vẫn có conflict cũ.
- So sánh snapshot với Git index ban đầu: components/DTO, path, operationId, request/response/status/security/required role/parameter schema không đổi; chỉ mô tả trạng thái và ví dụ key được sửa. Sync check nguồn chuẩn/checksum đạt. Không còn import/tham chiếu module sample trong backend/tests/scripts.
- Integration session `65115` đang chạy ngoài sandbox, đã trả 13 dấu pass, chưa có tổng kết/exit; kiểm outcome trước retry. Chưa có session build/unit còn chạy.
- Kết quả cuối integration session `65115`: **15 passed in 162.09s**, exit 0, không skipped. Unit **107 passed**, Ruff lint/format **40 file** đạt, lock check offline **44 package** đạt. Test Supabase chỉ tiêm danh tính, không phải bằng chứng SSO/workflow/ML. Test commit/concurrency đã chạy cleanup của suite; không chạy lại migration/seed/reset.
- Hoàn tất cập nhật tài liệu gateway/contract và `03-api/contracts/README.md`; bỏ dòng sample config còn sót trong Alembic README. Build/audit cuối sau sửa tài liệu đạt **71 source members / 31 wheel members**; không sample runtime/secret/cache/dependency nhúng. `git diff --check` ngoài CHANGELOG đạt; conflict CHANGELOG có sẵn vẫn chưa xử lý.
- CODE-DB-02 hoàn tất trong phạm vi dọn API sample: runtime chỉ còn chế độ database, 46 operation theo contract (37 có đường xử lý DB, 9 chờ engine/adapter). Giữ scripts seed/generator vì chúng nạp dữ liệu vào Supabase và vẫn được test schema/synthetic data sử dụng; payload test nằm riêng trong tests, không trong wheel. Không thay wire DTO hoặc schema/migration database, không sửa `.env`, không commit/push/deploy. Không còn session kiểm thử/build đang chạy.
- Bước tiếp theo: cấu hình IdP RFC7662 với 5 biến OIDC hiện còn thiếu, bootstrap/map exact issuer+subject và grant vào users/user_roles để dùng API bảo vệ. Muốn hoàn tất nghiệp vụ xuyên engine cần adapter provisioning/workflow/chat/model/document và durable outbox/analysis/email worker; xem mục “Cần bổ sung để API không bị chặn” trong `docs/SUPABASE-API.md`. Không dùng danh tính mẫu hoặc bỏ policy để mở API.

### 2026-10-15 — CODE-DB-01: Hoàn tất phần database của API nghiệp vụ

- Yêu cầu hiện tại: chuyển API nghiệp vụ sang Supabase. Người dùng xác nhận chưa có Identity/SSO; sẽ tiêm danh tính trong test, runtime không giả danh hoặc bỏ kiểm quyền.
- Repo `E:\CTU\CLB_Tin_hoc\OLP\FlowBridge`, branch `hoanganhdev_features`, HEAD `928ebfa91a2f7ca2b94d4bd366f95acc3411f008`. Không có plan/task/rules được dẫn chiếu trong checkout; dùng task bổ sung ổn định `CODE-DB-01`, chưa đánh dấu hoàn tất. Giữ thay đổi staged và conflict CHANGELOG có sẵn; không commit/push.
- Đã thêm repository SQLAlchemy async, database service/policy và router cho contract app/AI 0.4.1; giữ sample mode độc lập. Thêm adapter introspection upstream RFC 7662 và Settings OIDC, không tự viết engine JWT/SSO. Model/migration `0002_api_idempotency` thêm riêng bảng retry response, bật RLS và revoke client grants.
- Các chức năng cần workflow approval, identity provisioning, chat/model/document engine vẫn fail closed 503; lệnh DB sinh outbox pending, chưa có worker xử lý event. Không coi queue/outbox là engine đã chạy.
- File mới: `identity/authentication.py`, `models/api_idempotency.py`, `supplier_assistant/{repository,policy,database_service}.py`, revision `alembic/versions/20261015_0002_api_idempotency.py`. File sửa: settings/main/models init/router/contract, pyproject (mặc định loại integration, chọn rõ bằng `-m integration`), baseline seed test tách metadata operational.
- Checkpoint trước lệnh dài/migration: Supabase đã xác nhận revision 0001 và 27 bảng bằng lượt kiểm thử bên dưới. Chuẩn bị áp dụng duy nhất revision 0002 thêm bảng operational; không seed/reset/drop dữ liệu cũ. Chưa chạy migration này hoặc test integration ở thời điểm ghi mục này.
- Ruff check backend/tests/revision mới pass. Regression đang chạy ngoài sandbox, session `94865`; phải kiểm outcome trước retry. Bước tiếp theo: viết integration có assertions và rollback fixture, review SQL 0002 offline, chạy migration mới, test HTTP persistence/quyền/idempotency trên Supabase, cập nhật tài liệu và evidence.
- Cập nhật giữa task: regression session `94865` hoàn tất **187 passed in 15.02s**. Đã review SQL offline riêng 0001→0002: chỉ CREATE bảng idempotency, notice, RLS/revoke và cập nhật alembic_version; không DROP. Đã áp dụng `0002_api_idempotency` lên Supabase thật thành công, exit 0 (5.45s), không seed hoặc sửa dữ liệu ứng dụng cũ. Không chạy lại migration nếu bị gián đoạn; tiếp theo chỉ kiểm current/check và HTTP integration.
- Integration lần đầu: 8 pass/3 fail (95.83s), fixture prediction dùng lead_days cũ gây constraint `ck_prediction_lead_anchor`; đã sửa fixture và kiểm invariant anchor ở service. Lần tiếp: 11 pass/1 fail (142.15s), negative token ngắn sai schema (422 thay 401); sau sửa negative token, nhóm bổ sung đạt 3 pass/1 fail (69.10s), expectation confirmation 200 khác contract 201; đã sửa test theo contract, không đổi status API để làm test pass.
- Test auth adapter HTTP mock: **9 passed in 1.08s**; đây không phải integration SSO. Supabase `alembic current` xác nhận `0002_api_idempotency (head)`, `alembic check` đạt không drift. Integration đã chứng minh commit request và chống tạo trùng đồng thời qua hai request + kết nối đọc độc lập; fixture của test được cleanup.
- Tài liệu đã cập nhật README, CHANGELOG (giữ conflict có sẵn), .env.example, contracts mapping, alembic README và `docs/SUPABASE-API.md`. Sdist bổ sung hướng dẫn mới. Sắp chạy suite integration cuối, regression và build archive cục bộ; chưa xác nhận hoàn tất các lượt cuối.
- Kết quả cuối: **15 integration passed in 180.31s**, session `24526`, không skip; **201 unit/sample passed, 15 integration deselected in 15.87s**, session `46013`. Có thêm 5 assertion cases cho NUMERIC(18,4) overflow/rounding. Không dùng test mock để thay bằng chứng SQL thật.
- HTTP smoke cuối với lifespan thật, không override auth: live/ready 200; business/admin thiếu bearer đều 401; OpenAPI đủ 46 operations. SQL read-only xác nhận revision 0002, RLS bảng mới bật, anon/authenticated không có SELECT/INSERT/UPDATE/DELETE; không còn identity fixture. Counts giữ `suppliers=6`, `products=6`, `purchase_requests=1000`, `api_idempotency=0` sau cleanup. Không chỉnh .env hoặc credential.
- Build: lần đầu uv cache mặc định bị lỗi quyền sandbox (session `85152`, đã kết thúc exit 1), đổi UV_CACHE_DIR vào `.cache/uv-build` thì build offline/no-build-isolation đạt. Archive `.cache/api-build/flowbridge-0.1.0.tar.gz` bản cuối có 113 members, một root đúng version, không `.env`/cache/dependency binaries. Giải nén sạch ngoài repo tại workspace visualizations, build wheel và import/app construction từ wheel bằng Python -I đạt. Chỉ là build verification, không chuẩn bị/publish release; artifact vẫn mang CHANGELOG có conflict cũ.
- Ruff lint và format check backend/tests/revision mới đạt; notice GPL/no standalone string comment của các file code mới đã kiểm. `git diff --check` ngoài CHANGELOG đạt; conflict marker CHANGELOG tồn tại từ đầu task và chưa giải quyết. Không thêm dependency, không đổi contract snapshot hoặc chạy seed. Không còn session kiểm thử/build đang chạy.
- Trạng thái: CODE-DB-01 hoàn tất phần persistence theo yêu cầu: 37 operation có đường xử lý database, 9 operation cần engine/adapter ngoài trả 503 như tài liệu. Không claim nghiệm thu 37 operation riêng lẻ hoặc tích hợp SSO/workflow/email/ML; test coverage là các luồng/invariants nêu trong suite. Chưa commit/push/deploy. Bước tiếp theo: cấu hình IdP thật và mapping issuer+subject; triển khai durable outbox dispatcher/workflow/email/model theo yêu cầu riêng, giải quyết conflict Git trước khi commit/release.

### 2026-10-15 — Kiểm thử API với cấu hình Supabase mới

- Mục tiêu: theo yêu cầu người dùng, dùng `.env` vừa bổ sung để kiểm thử kết nối và API với Supabase thật. Chỉ đọc database; không chạy migration/seed hoặc thay đổi dữ liệu.
- Repo thực tế: `E:\CTU\CLB_Tin_hoc\OLP\FlowBridge`; branch `hoanganhdev_features`; HEAD `928ebfa91a2f7ca2b94d4bd366f95acc3411f008`. Checkpoint phía dưới có lịch sử từ checkout/HEAD khác, không dùng thay bằng chứng hiện tại.
- Task: kiểm thử runtime/database, hoàn tất trong phạm vi đã có. Không tự gán CODE/CP: checkout không có `docs/PLAN-OPENCORE.md`, `docs/TASK-API-TEST.md` hoặc `docs/PROJECT-RULES.md` để đối chiếu.
- Đầu vào: giữ nguyên mọi thay đổi staged và conflict `UU CHANGELOG.md`; không giải quyết conflict, commit hoặc publish. Không sửa `.env`, code runtime, dependency hay migration.
- Lệnh `./.venv/Scripts/python.exe scripts/check_db.py` trong sandbox không trả kết quả, session `96617` đã dừng. Chạy lại với quyền mạng ngoài sandbox: exit 0, `SELECT 1` thành công (5.70s).
- Tạo script kiểm tra cục bộ `.cache/supabase_api_smoke.py` (Git ignore, GPL notice, không chứa credential). Lệnh `./.venv/Scripts/python.exe .cache/supabase_api_smoke.py` ngoài sandbox: exit 0 (6.36s). Dùng lifespan thật và HTTPX ASGITransport để gọi app trong tiến trình; không khởi động TCP server/gateway, không mock DB.
- Startup đọc PostgreSQL thật thành công; `GET /_ops/live` và `GET /_ops/ready` đều HTTP 200 và payload đúng. Shutdown đã giải phóng resources.
- Catalog thật có đủ 27/27 bảng nghiệp vụ trong ORM và revision `0001_supplier_schema`. Trong transaction READ ONLY, xác nhận cột ORM đọc được và COUNT: `suppliers=6`, `products=6`, `purchase_requests=1000`. Không đọc/ghi nội dung record hoặc hiển thị credential.
- `GET /api/v1/suppliers`, `/api/v1/products`, `/api/v1/purchase-requests` đều HTTP 404 với sample mode tắt. Đây là giới hạn triển khai hiện tại: router nghiệp vụ chỉ mount trong sample mode, chưa có đường API persistence thật. Không coi các assertion 404 hay test sample là nghiệm thu CRUD Supabase thành công.
- Regression: `./.venv/Scripts/python.exe -m pytest tests/unit/test_settings.py tests/unit/test_operations.py tests/unit/test_lifespan.py tests/unit/test_database_tooling.py -q --tb=no` ngoài sandbox: **47 passed in 4.01s**, exit 0. Không chạy lại full suite hoặc `alembic check` vì phạm vi là runtime/read-only, không thay đổi code/schema.
- File thay đổi trong lượt: checkpoint này và script tạm `.cache/supabase_api_smoke.py`; không còn tiến trình kiểm thử đang chạy. Bước tiếp theo nếu được yêu cầu triển khai: nối router → service/policy → repository dùng request-scoped AsyncSession và kiểm thử CRUD với DB thật; Supabase connectivity đã được xác nhận, nghiệp vụ chưa đạt.
- Rà diff cuối: `git diff --check` báo conflict marker có sẵn tại `CHANGELOG.md:5,8,14`; chưa xử lý vì ngoài yêu cầu test. `git check-ignore` xác nhận `.env` và script `.cache/` bị ignore; checkpoint hiện được Git theo dõi.

### 2026-10-15 — Tích hợp schema và chạy dữ liệu từ script của nhóm

- Yêu cầu trực tiếp: thiết lập và chạy dữ liệu từ script đã được thêm vào repo.
- File đầu vào thực tế là `alembic/script.py`: standalone installer gồm schema,
  3 hàm SQL và bộ sinh dữ liệu demo. Đã chuyển nguyên nội dung sang
  `scripts/supplier_demo_source.py`; không dùng installer để tạo schema ngoài Alembic.
- Kiểm tra catalog trước khi chạy: `public` không có bảng và không có 3 helper trùng tên.
- Tạo revision `0001_supplier_schema` và các asset schema/helper/verify cố định trong
  `alembic/sql/`; SQL trích nguyên văn từ script và được kiểm tra bằng test.
- Migration đã commit: 27 bảng ứng dụng, 293 cột, 56 FK, 83 CHECK, 27 PK,
  40 unique index thông thường, 3 unique index có điều kiện; RLS bật cả 27 bảng.
  Quyền bảng cho anon/authenticated bằng 0, không có policy/user trigger theo script.
- Thêm 27 ORM mapping trong `models/supplier_assistant.py`, đăng ký trong `models/__init__.py`.
  Đủ tên constraint, index và comment; `alembic check` sau cùng không phát hiện drift.
- Thêm `scripts/seed.py` dùng cấu hình asyncpg/SSL hiện có, không thêm driver.
  Seed từ chối DB có dữ liệu, dùng advisory lock và một transaction, kiểm tra schema,
  count từng bảng, FK/CHECK và hàm training trước commit. Không reset/xóa dữ liệu.
- Đã nạp mặc định 1.000 PR: tổng 25.422 dòng trong 27 bảng, 6 supplier, 6 product,
  998 PO, 3.863 prediction mô phỏng. Đọc lại tất cả bảng qua SQLAlchemy sau commit khớp.
- Hàm training trả 1.780 dòng có nhãn; CSV train 1.420, test 279, loại 81 dòng biên;
  queries 83. Không gửi email, không phát outbox thật, không chạy model AI/ML.
- CSV/report tại `demo_output/`, bị Git ignore. `.env` hiện có được dùng nhưng không
  đưa credential vào source, log hoặc checkpoint. Hướng dẫn cập nhật tại `alembic/README.md`.
- Test: 109 pass, 1 fail cũ `test_contract_snapshot_matches_provenance_checksum`.
  Lỗi checksum tương tự lượt thiết lập trước; không sửa contract/API ngoài yêu cầu.
- Các file contract tầng 2 và nội dung checkpoint có sẵn được giữ nguyên.
  Chưa nối API sample với database thật; chưa commit/push thay đổi Git.

### 2026-10-15 — Xóa các bảng tạo nhầm trên Supabase

- Yêu cầu hiện tại: xóa các bảng ứng dụng đã tạo trên Supabase được cấu hình cho dự án.
- Repo: `D:\OLP MNM 2025\FlowBridge`; branch `hoanganhdev_features`; HEAD `a94ad47`.
- Trạng thái: hoàn tất thao tác bảo trì theo yêu cầu trực tiếp của người dùng; không gắn ID CODE vì plan và checkpoint cũ không tồn tại trong checkout này.
- Đã kiểm tra catalog PostgreSQL thật: 26 bảng ứng dụng trong `public`; các schema còn lại thuộc dịch vụ Supabase hoặc extension.
- Đã chạy một câu `DROP TABLE` liệt kê đủ 26 bảng với `RESTRICT` trong một transaction; lock timeout 5 giây, statement timeout 30 giây. Không dùng `CASCADE` hoặc xóa schema.
- Giao dịch đã commit. Truy vấn lại sau commit xác nhận `public` còn 0 bảng; danh sách bảng ngoài `public` không đổi.
- Các bảng đã xóa: `analysis_tasks`, `approval_history`, `confirmation_tokens`, `departments`, `email_logs`, `goods_receipts`, `inventory_snapshots`, `notifications`, `outbox_events`, `products`, `proposal_items`, `proposals`, `purchase_order_items`, `purchase_order_schedules`, `purchase_orders`, `purchase_request_items`, `purchase_requests`, `risk_analysis_reports`, `risk_predictions`, `roles`, `supplier_confirmation_items`, `supplier_confirmations`, `supplier_products`, `suppliers`, `user_roles`, `users`.
- Kết nối dùng Settings/SQLAlchemy hiện có; không lưu thông tin đăng nhập. Kiểm tra catalog ban đầu trong sandbox bị treo và đã dừng trước khi thực hiện bất kỳ thay đổi nào. Các lệnh mạng sau đó chạy với quyền được phê duyệt.
- Không thêm dependency hoặc Alembic revision; đây là thao tác dọn bảng tạo nhầm, không triển khai schema ứng dụng mới. Chưa chạy migration tạo bảng hoặc seed.
- Không chạy unit test vì không sửa mã ứng dụng; bằng chứng kiểm tra là truy vấn trực tiếp sau commit.
- Thay đổi file trong lượt này: tạo checkpoint này. Các thay đổi có sẵn trong `.env.example`, `CHANGELOG.md`, `README.md`, `backend/src/flowbridge/db.py`, `backend/src/flowbridge/settings.py`, `pyproject.toml`, `tests/unit/test_settings.py` và các file Alembic/model/script/test chưa được Git theo dõi được giữ nguyên.
- Không còn việc cần thực hiện trong yêu cầu xóa bảng này. Khi có yêu cầu dựng schema mới, kiểm tra lại catalog và chuẩn bị migration trước khi chạy.

### 2026-10-15 — Hợp đồng tầng 02 Supplier Assistant

- Yêu cầu: chỉ viết hợp đồng YAML tầng API Tools dựa trên OpenCore tầng 1 và tài liệu Word/sơ đồ người dùng cung cấp; chưa triển khai mã.
- Repo: `D:\OLP MNM 2025\FlowBridge`; branch `hoanganhdev_features`; HEAD `a94ad4744f18c3a249780d4c52cfe4eba40727bc`.
- Trạng thái: hoàn tất bản thiết kế để review, không đánh dấu task triển khai CODE hoặc mốc CP. `docs/PLAN-OPENCORE.md` và `docs/TASK-API-TEST.md` không có trong checkout để đối chiếu ID.
- File mới: `contracts/supplier-assistant-layer2-api.yaml`, OpenAPI 3.1, version 0.1.0; 27 paths, 29 operations, 104 schemas, 11 event types. Chỉ bổ sung mục bàn giao này vào checkpoint, giữ mục bảo trì trước đó.
- Nguồn tầng 1: `contracts/opencore-api.yaml` version 0.2.0; khóa SHA-256 trong `x-upstream`. Các schema chung Id/Timestamp/Money/ErrorResponse/Evidence/Event giữ nguyên định nghĩa và đã đối chiếu cấu trúc.
- Quyết định: tách draft khỏi snapshot runtime 0.4.1; DTO nhiều dòng, tool dự đoán/so sánh theo dòng; manifest/cutoff/version dùng chung khi fanout; chat async và action intent có cấu trúc; worker có lease/fencing; RAG có ACL/provenance/generation; phê duyệt authoritative và outbox/saga khi khác engine; email unknown phải đối soát.
- Điểm cần thống nhất khi triển khai: Core Money hai chữ số và schema DB/sample bốn chữ số chưa tương thích mọi giá trị; draft yêu cầu adapter hoặc nâng đồng bộ contract, không làm tròn ngầm. Draft chưa tương thích wire với snapshot runtime; không tự sync hoặc coi endpoint đã tồn tại.
- Kiểm tra đã chạy bằng `.venv/Scripts/python.exe`: YAML không trùng key; `FastAPI OpenAPI.model_validate`; `Draft202012Validator.check_schema` cho mọi schema; resolve toàn bộ local refs; đối chiếu Core operationIds/shared schemas và consumer map; kiểm path/security/idempotency; validate 15 request examples, 8 response examples, 10 standard error examples và 9 negative schema cases. Tất cả pass. Hash hai snapshot app/AI runtime vẫn khớp manifest hiện có.
- Chưa chạy integration/runtime acceptance vì chỉ viết contract; không thêm dependency, migration, router, service, model hoặc sửa snapshot runtime. Các kịch bản `x-acceptance-scenarios` là yêu cầu nghiệm thu tương lai, không phải test runtime đã pass.
- Rà soát cuối: Core Files không có field version; đã quy định version nguồn tệp/PDF bằng `File.sha256` hiện có, không yêu cầu API revision tệp giả định. Sau chỉnh mapping, OpenAPI model và mọi JSON Schema vẫn pass; `git diff --check` pass.
- Bước tiếp theo khi có yêu cầu triển khai: review ranh giới/scope và precision, thống nhất nguồn contract, ánh xạ schema DB và đăng ký projection/policy/workflow với Core, rồi mới lập task triển khai. Không tự chạy migration, commit hoặc publish.
