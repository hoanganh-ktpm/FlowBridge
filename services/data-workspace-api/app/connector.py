# SPDX-License-Identifier: GPL-3.0-or-later
"""Core HTTP only. No Hasura credential, SQL, or source connection fallback."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import HTTPException


@dataclass(frozen=True)
class Context:
    token: str
    org_id: UUID
    owner_id: UUID
    admin: bool


class CoreConnector:
    def __init__(self, http: httpx.AsyncClient, base_url: str, org_id: UUID):
        self.http, self.base_url, self.org_id = http, base_url.rstrip("/"), org_id
        self.query_slots = asyncio.Semaphore(4)

    async def request(self, token, path, params=None, body=None):
        try:
            for attempt in range(2):
                response = await self.http.request(
                    "GET" if body is None else "POST",
                    self.base_url + "/api/v1/core" + path,
                    params=params,
                    json=body,
                    headers={"Authorization": "Bearer " + token},
                    follow_redirects=False,
                    timeout=30,
                )
                # These are read-only Core operations, including POST /query.
                # Retry once at the same governed boundary; never retry permission denial.
                if response.status_code < 500 or attempt == 1:
                    break
                await asyncio.sleep(0.15)
        except httpx.HTTPError:
            raise HTTPException(503, "FlowBridge Core unavailable; no source fallback") from None
        if response.status_code != 200:
            status = response.status_code if response.status_code in {401, 403, 404} else 503
            raise HTTPException(status, "Core denied access or upstream unavailable")
        try:
            return response.json()
        except ValueError:
            raise HTTPException(502, "Invalid Core response") from None

    async def context(self, token):
        me = await self.request(token, "/me")
        if me["principal_status"] != "ACTIVE":
            raise HTTPException(403, "Inactive principal")
        return Context(
            token, self.org_id, UUID(me["principal_id"]), me["platform_role"] == "PLATFORM_ADMIN"
        )

    async def catalog(self, ctx):
        return (await self.request(ctx.token, "/authorized-data-products"))["items"]

    async def product(self, ctx, ref):
        for item in await self.catalog(ctx):
            if item["id"] == str(ref.data_product_id) and item["contract_version_id"] == str(
                ref.contract_version_id
            ):
                return item
        raise HTTPException(403, "Data Product/contract is not currently authorized")

    async def rows(self, ctx, ref, columns=None, where=None):
        product = await self.product(ctx, ref)
        allowed = {f["name"] for f in product["fields"]}
        columns = sorted(set(columns or allowed) | set(product["business_key"]) | {"org_id"})
        if not set(columns) <= allowed:
            raise HTTPException(403, "Required columns are not granted")
        first = await self.query(ctx, ref, columns, 100, 0, where)
        rows = first["rows"]
        if len(rows) > 100:
            raise HTTPException(422, "Inconsistent Core pagination")
        complete = len(rows) < 100
        for start in range(100, 10001, 400):
            if complete:
                break
            offsets = list(range(start, min(start + 400, 10001), 100))
            pages = await asyncio.gather(
                *(self.query(ctx, ref, columns, 100, offset, where) for offset in offsets)
            )
            for page in pages:
                batch = page["rows"]
                if len(batch) > 100 or (complete and batch):
                    raise HTTPException(422, "Inconsistent Core pagination")
                rows.extend(batch)
                if len(batch) < 100:
                    complete = True
            if complete:
                break
        if not complete:
            raise HTTPException(
                422,
                detail={
                    "code": "DATASET_TOO_LARGE",
                    "message": "Nguồn dữ liệu vượt giới hạn đọc an toàn. "
                    "Hãy thu hẹp kho hoặc khoảng ngày; hệ thống chưa trả kết quả từ dữ liệu thiếu.",
                },
            )
        # Revalidate all columns via a new authorized read after pagination, including revocations.
        await self.check(ctx, ref, columns)
        return product, rows

    async def check(self, ctx, ref, columns=None):
        product = await self.product(ctx, ref)
        selected = sorted(
            set(columns or [f["name"] for f in product["fields"]])
            | set(product["business_key"])
            | {"org_id"}
        )
        await self.query(ctx, ref, selected, 1, 0)

    async def query(self, ctx, ref, columns, limit, offset, where=None):
        async with self.query_slots:
            page = await self.request(
                ctx.token,
                f"/data-products/{ref.data_product_id}/query",
                body={
                    "contract_version_id": str(ref.contract_version_id),
                    "select": columns,
                    "where": {**(where or {}), "org_id": {"_eq": str(ctx.org_id)}},
                    "limit": limit,
                    "offset": offset,
                },
            )
        if any(str(row.get("org_id")) != str(ctx.org_id) for row in page["rows"]):
            raise HTTPException(422, "Mixed/foreign organization data rejected")
        return page
