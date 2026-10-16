# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Xuất mapping từ registry API; không chạy proxy hoặc gọi Admin API.

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Literal
from urllib.parse import urlsplit

from flowbridge.supplier_assistant.contract import application_operations


@dataclass(frozen=True, slots=True)
class GatewayRoute:
    # Một route gateway suy từ operation contract và upstream chung.
    method: str
    path: str
    operation_id: str
    visibility: Literal["public", "internal", "admin"]
    upstream: str


def route_map(
    upstream: str = "http://127.0.0.1:8000",
    *,
    include_ml: bool = False,
    include_identity: bool = False,
) -> tuple[GatewayRoute, ...]:
    # Kiểm tra base URL rồi phân loại public/internal theo path.
    parts = urlsplit(upstream)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("upstream must be an HTTP base URL")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path.strip("/"):
        raise ValueError("upstream must have no credentials, path, query or fragment")
    operations = list(application_operations())
    if include_identity:
        from flowbridge.identity.router import router as identity_router

        operations.extend(
            (method, route.path, {"operationId": route.operation_id})
            for route in identity_router.routes
            for method in route.methods
        )
    if include_ml:
        from flowbridge.supplier_assistant.prediction.router import router

        operations.extend(
            (method, route.path, {"operationId": route.operation_id})
            for route in router.routes
            for method in route.methods
        )
    return tuple(
        GatewayRoute(
            method=method,
            path=path,
            operation_id=spec["operationId"],
            visibility="admin"
            if path.startswith("/admin/")
            else "internal"
            if path.startswith("/internal/")
            else "public",
            upstream=upstream.rstrip("/"),
        )
        for method, path, spec in operations
    )


def main() -> None:
    # CLI xuất mapping JSON để cấu hình gateway, không chạy proxy.
    parser = argparse.ArgumentParser(
        description="Export PostgreSQL API gateway mapping; no proxy starts"
    )
    parser.add_argument("--upstream", default="http://127.0.0.1:8000")
    parser.add_argument("--include-ml", action="store_true")
    parser.add_argument("--include-identity", action="store_true")
    args = parser.parse_args()
    routes = route_map(
        args.upstream,
        include_ml=args.include_ml,
        include_identity=args.include_identity,
    )
    print(json.dumps([asdict(route) for route in routes], indent=2))


if __name__ == "__main__":
    main()
