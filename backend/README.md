# FlowBridge Backend

**Data Core và Platform APIs cho nền tảng DX-OS.**

Source nằm trong package `app`, chia theo hai khối:

| Khối | Trách nhiệm | Tài liệu |
|---|---|---|
| app/core | Metadata, Registry, Data Products, Governance, Hasura và Agent/Tools | [Core](app/core/README.md) |
| app/platform | Identity, Gateway, Workflow, Audit và HTTP utilities | [Platform](app/platform/README.md) |

Import qua `app.core` và `app.platform`. Root `pyproject.toml`/`uv.lock` là nguồn chuẩn cho dependency Python. Các Business APIs nằm riêng dưới `services/`.

## Kiểm tra

Chạy từ gốc repository:

```sh
uv sync --locked --group dev --group build
uv run --locked pytest tests/platform -q
uv run --locked ruff check backend tests
uv build --no-build-isolation
```

Identity adapters và HTTP utilities đã có implementation. Chưa có app server entrypoint hoặc Core/Domain API runtime hoàn chỉnh. Khi phát triển endpoint mới, thêm composition/lifecycle tại app boundary và giữ logic trong module sở hữu.
