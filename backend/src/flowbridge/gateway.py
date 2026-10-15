# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Xuất mapping từ registry API; không chạy proxy hoặc gọi Admin API.

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Literal
from urllib.parse import urlsplit

from flowbridge.shared.contract import operations


@dataclass(frozen=True, slots=True)
class GatewayRoute:
    # Một route gateway suy từ operation contract và upstream chung.
    method: str
    path: str
    operation_id: str
    visibility: Literal["public", "internal"]
    upstream: str


def route_map(upstream: str = "http://127.0.0.1:8000") -> tuple[GatewayRoute, ...]:
    # Kiểm tra base URL rồi phân loại public/internal theo path.
    parts = urlsplit(upstream)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("upstream must be an HTTP base URL")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path.strip("/"):
        raise ValueError("upstream must have no credentials, path, query or fragment")
    return tuple(
        GatewayRoute(
            method=operation.method,
            path=operation.path,
            operation_id=operation.operation_id,
            visibility="internal" if operation.path.startswith("/internal/") else "public",
            upstream=upstream.rstrip("/"),
        )
        for operation in operations()
    )


def main() -> None:
    # CLI xuất mapping JSON để cấu hình gateway, không chạy proxy.
    parser = argparse.ArgumentParser(
        description="Export sample API gateway mapping; no proxy starts"
    )
    parser.add_argument("--upstream", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    print(json.dumps([asdict(route) for route in route_map(args.upstream)], indent=2))


if __name__ == "__main__":
    main()
