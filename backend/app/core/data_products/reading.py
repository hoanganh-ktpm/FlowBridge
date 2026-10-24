import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import SecretStr

from app.core.hasura_provisioner.schemas import department_role
from app.core.hasura_provisioner.settings import HasuraSettings
from app.platform.http.errors import CoreError, ErrorCode

from .schemas import DataContractDocument
from .validation import supported_pattern


@dataclass(frozen=True)
class TrustedHasuraContext:
    principal_id: UUID
    department_id: UUID
    data_product_id: UUID
    contract_version_id: UUID
    grant_id: UUID
    desired_revision: int
    applied_revision: int
    validated_at: datetime
    expires_at: datetime | None

    @property
    def enforcement_role(self) -> str:
        return department_role(self.department_id)


@dataclass(frozen=True)
class ReadTarget:
    namespace: str
    root: str
    aliases: tuple[tuple[str, str], ...] = ()


class HasuraCredentialProvider(Protocol):
    async def issue(self, context: TrustedHasuraContext) -> SecretStr: ...


class DataReader(Protocol):
    async def read(
        self,
        target: ReadTarget,
        context: TrustedHasuraContext,
        columns: tuple[str, ...],
        order: tuple[str, ...],
        limit: int,
        offset: int,
    ) -> list[dict]: ...


class HasuraDataReader:
    def __init__(
        self,
        http: httpx.AsyncClient,
        settings: HasuraSettings,
        credentials: HasuraCredentialProvider,
        *,
        verified_auth_profile: bool = False,
    ) -> None:
        if not verified_auth_profile or settings.select_limit < 101:
            raise ValueError("Verified non-admin Hasura v2 auth/naming profile is required")
        self.http = http
        self.settings = settings
        self.credentials = credentials

    async def read(self, target, context, columns, order, limit, offset):
        aliases = dict(target.aliases)
        identifiers = (target.namespace, target.root, *columns, *order, *aliases.values())
        if (
            not columns
            or not order
            or not 2 <= limit <= 101
            or not 0 <= offset <= 10000
            or any(not re.fullmatch(r"(?!__)[a-z_][a-z0-9_]{0,62}", item) for item in identifiers)
            or context.desired_revision != context.applied_revision
        ):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        ordering = ",".join("{" + aliases.get(key, key) + ":asc}" for key in order)
        selection = " ".join(
            column + ":" + aliases[column]
            if column in aliases and aliases[column] != column
            else column
            for column in columns
        )
        query = (
            "query CoreRead($limit:Int!,$offset:Int!){"
            + target.namespace
            + "{rows:"
            + target.root
            + "(limit:$limit,offset:$offset,order_by:["
            + ordering
            + "]){"
            + selection
            + "}}}"
        )
        result = await self.execute_graphql(context, query, {"limit": limit, "offset": offset})
        namespace = result["data"].get(target.namespace)
        rows = namespace.get("rows") if isinstance(namespace, dict) else None
        if (
            not isinstance(rows, list)
            or len(rows) > limit
            or any(not isinstance(row, dict) for row in rows)
        ):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return rows

    async def execute_graphql(self, context, query: str, variables: dict):
        token = (await self.credentials.issue(context)).get_secret_value()
        now = datetime.now(UTC)
        if (
            not token
            or "\r" in token
            or "\n" in token
            or token == self.settings.admin_secret.get_secret_value()
            or context.desired_revision != context.applied_revision
            or not -1 <= (now - context.validated_at).total_seconds() <= 10
            or (context.expires_at is not None and context.expires_at <= now)
        ):
            raise CoreError(ErrorCode.FORBIDDEN)
        request = self.http.build_request(
            "POST",
            self.settings.endpoint + "/v1/graphql",
            json={"query": query, "variables": variables},
            timeout=self.settings.timeout_seconds,
        )
        for header in list(request.headers):
            if header.lower().startswith("x-hasura-") or header.lower() in {
                "authorization",
                "cookie",
            }:
                del request.headers[header]
        request.headers["Authorization"] = "Bearer " + token
        try:
            response = await self.http.send(request, auth=None, follow_redirects=False, stream=True)
            try:
                if response.status_code != 200:
                    raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 8 * 1024 * 1024:
                        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
                    chunks.append(chunk)
                result = json.loads(b"".join(chunks))
            finally:
                await response.aclose()
        except httpx.TimeoutException:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
        except (httpx.HTTPError, ValueError, RecursionError):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
        if (
            not isinstance(result, dict)
            or "errors" in result
            or not isinstance(result.get("data"), dict)
        ):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return result


def validate_rows(
    document: DataContractDocument, columns: tuple[str, ...], rows: list[dict]
) -> list[dict]:
    fields = {field.name: field for field in document.fields}
    projection = {name: fields[name] for name in columns}
    properties = {}
    for name, field in projection.items():
        wire_type = "string" if field.wire_type in {"date", "date-time"} else field.wire_type
        if wire_type in {"object", "array"}:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        schema = {"type": [wire_type, "null"] if field.nullable else wire_type}
        if field.wire_type in {"date", "date-time"}:
            schema["format"] = field.wire_type
        if field.constraints:
            if field.constraints.pattern is not None and not supported_pattern(
                field.constraints.pattern
            ):
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            mapping = {"min_length": "minLength", "max_length": "maxLength"}
            schema.update(
                {
                    mapping.get(key, key): value
                    for key, value in field.constraints.model_dump(exclude_none=True).items()
                }
            )
        properties[name] = schema
    validator = Draft202012Validator(
        {
            "type": "object",
            "properties": properties,
            "required": list(columns),
            "additionalProperties": False,
        },
        format_checker=FormatChecker(),
    )
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {field.column for field in projection.values()}:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        public = {name: row[field.column] for name, field in projection.items()}
        for name, field in projection.items():
            value = public[name]
            if field.wire_type == "date-time" and value is not None:
                if (
                    not isinstance(value, str)
                    or re.search(r"\.\d{7,}", value)
                    or not FormatChecker().conforms(value, "date-time")
                ):
                    raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
                try:
                    public[name] = (
                        datetime.fromisoformat(value.replace("Z", "+00:00"))
                        .astimezone(UTC)
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                except (ValueError, OverflowError):
                    raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
        if not validator.is_valid(public):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        for name, value in public.items():
            field = projection[name]
            if (
                (isinstance(value, float) and not math.isfinite(value))
                or (type(value) is int and abs(value) > 2**53 - 1)
                or (
                    field.wire_type == "integer"
                    and value is not None
                    and (type(value) is not int or abs(value) > 2**53 - 1)
                )
                or (
                    field.wire_type == "date-time" and value is not None and not value.endswith("Z")
                )
            ):
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        result.append(public)
    return result
