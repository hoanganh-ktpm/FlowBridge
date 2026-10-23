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

Identity adapters, HTTP utilities và [Hasura Client/services](app/core/hasura_provisioner/README.md) được tái sử dụng bởi Core P0. `app/main.py` cung cấp FastAPI composition qua dependency injection; HTTP handlers, application services, read policies và worker đã có offline tests. Chưa có PostgreSQL persistence adapters hoặc runtime/E2E hoàn chỉnh. Không dùng memory persistence production; xem [Core P0](../docs/CORE-P0.md) để ghép adapters và lifecycle thật.
