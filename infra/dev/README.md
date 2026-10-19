# Container cho phát triển

Hướng dẫn vận hành và kiểm thử: [DEV-CONTAINERS.md](../../docs/DEV-CONTAINERS.md).
Chạy ứng dụng đầy đủ: [Hướng dẫn chạy](../../HUONG-DAN-CHAY.md).

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
