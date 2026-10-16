# Backend/IAM development containers

Hướng dẫn: [DEV-CONTAINERS.md](../../docs/DEV-CONTAINERS.md).
Task source `CODE-00-04.01` / `CODE-00-05.01`; kiểm engine thực thuộc `.02`, đang nghiệm thu.
Base branch `huuhuandev_features`, HEAD `70ed9da643c9f7beb0c67f6068d11d4e96d18566`.
Người dùng đã cài Docker và yêu cầu setup thực tế ngày 17/10/2026.

## Gỡ lệch migration ngày 17/10/2026

Đã khôi phục migration0003/SQL gốc từ commit nhóm `99c9b69`, không migrate/stamp/seed
DB. Alembic current khớp head, check đạt; Core catalog đọc riêng đạt, counts6/6/96
giữ nguyên. 19 SQL business test trên host và 8 unit tooling/migration đạt.
Rebuild tooling check-db/tools và require-head trong container đạt, không restart
stack dev. Full business/SSO container, fixture recovery, image audit và clean archive
Engine build vẫn mở. Xem [evidence](../../docs/MIGRATION-0003-RECONCILIATION.md).

## Kiểm Engine ngày 17/10/2026

- Docker CLI 29.8.2, Compose 5.6.0, Linux Engine/context desktop-linux. Doctor/config
  interpolation đạt. Host dùng bản Desktop per-user; PATH chỉ bổ sung trong phiên lệnh.
- Build runtime/tooling/unit và client/browser Dockerfile đạt; Keycloak image đã pull.
  Build thật phát hiện `.dockerignore` thiếu Alembic, compose.yaml và chính
  .dockerignore; đã sửa allowlist. Không thay migration/dependency để sửa build.
- `up --env-file .env --clients`: IAM/API healthy, client A/B HTTP200. Docs/readiness/
  Identity config200, protected me401; IAM discovery200 với CA verification và đúng issuer.
- Unit trong Linux container readonly/non-root: **279 passed in 22.56s**.
  Sau sửa down bao gồm mọi profile và thêm regression: **280 passed in 22.25s**;
  Ruff lint/format hai file Python và git diff check đạt.
  SQL Identity thật từ tooling container: **1 passed in 8.11s**, fixture rollback.
  Check DB connectivity read-only đạt; require-head fail đúng vì DB/source mismatch.
  Không chạy full SSO/business, migrate/seed hoặc tạo SSO fixture.
- Restart đồng thời IAM/API: API exit3 PostgreSQL startup check failed. Recreate
  API/client sau IAM healthy phục hồi thành công; không xoay state/secret. Hướng dẫn
  dùng down/up giữ volumes, tránh restart đồng thời các container chung namespace.
- Down/up đã đạt và fingerprint runtime.json không đổi. Bản sửa down bật mọi
  profile: kiểm thực tế dừng/xóa đủ API/IAM/client A/B, không còn container project;
  giữ nguyên volumes. Stack được bật lại để dùng tiếp.
- Image audit đầy đủ và clean archive container build chưa chạy. Không đóng
  CODE-00-04.02/CODE-00-05.02/CP-001 bằng các kết quả health/unit này.

## Dependency và provenance

`images.env` là manifest chuẩn cho image references, pin tag + OCI manifest digest.
Đã đọc metadata bốn registry upstream (HTTP200); sau đó pull/build/chạy trên Engine.
Việc chạy thành công chưa thay audit license/notice toàn bộ packages trong image.
Phiên bản đồng bộ bằng unit test với `.python-version`, `pyproject.toml`, manifest
Keycloak native và package Playwright. Docker Compose >=2.35.0 là tool yêu cầu,
không được đóng gói trong repo. Các tag/digest sau khi override phải kiểm lại.

| Thành phần | Phiên bản / nguồn chính | License / giới hạn kiểm |
|---|---|---|
| Python image | 3.12.15 slim-bookworm, Docker Official Image `library/python` | Python PSF; Dockerfile upstream MIT; Debian và package OS có license riêng |
| uv builder | 0.13.1, ghcr.io/astral-sh/uv | MIT OR Apache-2.0; chỉ copy binary upstream qua stage build/tooling |
| Keycloak | 26.8.1, quay.io/keycloak/keycloak | Apache-2.0 code upstream; UBI/JVM/package trong image cần audit riêng |
| Playwright browser | 1.64.1 noble, mcr.microsoft.com/playwright | Apache-2.0 tooling; Chromium/Ubuntu và dependencies có license/notice riêng |
| OpenSSL / CA certificates | Package manager của Python Debian base | Bản OSS upstream; version/package/license thực tế phải ghi khi build/audit image |
| NumPy dev | 2.5.4, giữ node/version/hash đã có trong uv.lock | BSD-3-Clause; wheel bundled libraries có notices riêng |
| rank-bm25 dev | 0.2.2, giữ node/version/hash đã có trong uv.lock | Apache-2.0; dùng BM25 upstream, không viết lại algorithm |

NumPy/rank-bm25 chỉ thêm quan hệ nhóm dev trong manifest/lock, không đổi runtime
dependencies hoặc phiên bản các package đã có. GPL-3.0-or-later của code nhóm
tương thích với các license code nêu trên; không suy ra toàn OCI image đã đạt
audit, không đổi attribution/notice upstream hoặc license dữ liệu/model.
Không phát hành source/binary engine/dependency trong source tar.gz; Dockerfiles
chỉ khai báo cách lấy qua upstream và package managers. Image chứa dependency là
artifact riêng, chưa publish hoặc chứng nhận OSS/image compliance.

Nguồn upstream:

- [Keycloak container/import/TLS](https://www.keycloak.org/server/containers)
- [Compose service network namespaces](https://docs.docker.com/reference/compose-file/services/#network_mode)
- [Compose secrets](https://docs.docker.com/reference/compose-file/secrets/)
- [uv Docker integration](https://docs.astral.sh/uv/guides/integration/docker/)
- [Python Official Image source/license](https://github.com/docker-library/python)
- [uv license](https://github.com/astral-sh/uv/blob/main/LICENSE-MIT)
- [Playwright license](https://github.com/microsoft/playwright/blob/main/LICENSE)
- [NumPy license](https://numpy.org/doc/stable/license.html)
- [rank-bm25 license](https://github.com/dorianbrown/rank_bm25/blob/master/LICENSE)

## Kiểm source trước khi cài Docker (lịch sử)

- YAML parse và JSON Schema chính thức Compose: đạt về cấu trúc; không thay
  `docker compose config`/interpolation hoặc build/runtime.
- Đã kiểm dry-run, từ chối Docker thiếu, syntax Node và unit lifecycle/config.
- Full unit bằng môi trường dev riêng: **279 passed in37.05s**, session74056 exit0.
  `uv sync --locked --no-default-groups --group dev --group build --no-editable`
  cài46 packages vào visualizations/container-source-minimal-dev, giữ .venv cũ.
  Assert không có torch/pandas/chromadb/tabicl/sentence_transformers đạt.
- Ruff check và format91 file, lock142, Node syntax, git diff --check đạt.
- Build source/wheel đạt178/65 members, có đủ Dockerfiles/Compose/image manifest,
  shell/Python runners và test source; audit actual env secret/cache/dependency
  binary trong source/wheel đạt. Giải nén ngoài checkout tại
  `visualizations/dev-container-clean-ac40a4aa/flowbridge-0.1.0`, rebuild source/wheel đạt.
- `python -I` import wheel rebuild rồi chạy unit từ source archive: **279 passed
  in18.89s**, session44967. Không cài model extras hoặc đọc .env checkout.
- Check helper cuối lượt đầu gọi đường dẫn script tương đối từ ngoài source nên
  exit2 sau khi279 unit đã pass; đã sửa phép kiểm sang absolute script path và
  dry-run từ ngoài checkout đạt. Không tính lượt helper exit1 là pass toàn bộ.
- `doctor` thực tế trả exit1 với thông báo Docker chưa cài; đây là kiểm fail-closed,
  không phải engine acceptance. Config/schema/LF/GPL/comment style và index trống đạt.

Source tasks CODE-00-04.01/CODE-00-05.01 hoàn tất theo phạm vi chuẩn bị source;
CODE-00-04.02/CODE-00-05.02 và các task cha/CP giữ mở tới khi kiểm Engine thật.
- Lượt unit đầu 13 pass/1 fail do separator Windows trong assertion test; sửa
  `relative_to(...).as_posix()`, lượt sau14 pass. Đây là test portability, không
  phải container acceptance. Ruff lượt đầu còn dòng dài; đã format/sửa.
- `uv lock --offline` khi đổi nhóm dev thiếu metadata cache nên fail; cập nhật
  lock bằng metadata upstream đạt graph142, diff4 dòng, không cài lại `.venv`.
  Metadata rank-bm25 có specifier quote cũ được uv chuẩn hóa; version không đổi.

## Phần còn lại

- Audit toàn bộ image package inventory/licenses/secrets, clean archive Engine build,
  browser E2E và recovery sau fixture thật còn mở.
- Revision blocker đã được gỡ bằng source gốc; full browser/container SQL acceptance
  vẫn cần chạy và có fixture prerequisites, không suy ra pass từ require-head.
