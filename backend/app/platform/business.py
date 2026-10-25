"""Technical HTTP/auth/transaction helpers; no Core or business schema imports."""

import asyncio
import hashlib
import json
import logging
import ssl
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import SQLAlchemyError

from app.platform.http.correlation import CorrelationMiddleware
from app.platform.http.errors import CoreError, ErrorCode, register_exception_handlers
from app.platform.http.request_policy import PrivateJsonMiddleware
from app.platform.identity.settings import IdentitySettings


class BusinessSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLOWBRIDGE_BUSINESS_", env_file=None, hide_input_in_errors=True
    )
    database_url: SecretStr
    actor_bindings: dict[str, dict] = Field(default_factory=dict)
    service_bases: dict[str, str] = Field(default_factory=dict)
    request_timeout: float = Field(default=8, gt=0, le=30)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BusinessRuntime:
    def __init__(self, domain, receipts):
        self.settings = BusinessSettings()
        url = sa.engine.make_url(self.settings.database_url.get_secret_value())
        if url.database != f"{domain}_db" or url.username != f"{domain}_owner":
            raise ValueError("Business API requires its own database/owner")
        self.domain, self.receipts = domain, receipts
        self.engine = sa.create_engine(
            url, hide_parameters=True, pool_pre_ping=True, connect_args={"connect_timeout": 5}
        )
        self.http = httpx.Client(timeout=self.settings.request_timeout, follow_redirects=False)

    def allow(self, actor, org_id, permission):
        binding = self.settings.actor_bindings.get(actor.subject, {})
        if str(org_id) != binding.get("org_id") or permission not in binding.get("permissions", []):
            raise CoreError(ErrorCode.FORBIDDEN)

    def remote(self, service, method, path, token, payload=None, key=None):
        base = self.settings.service_bases.get(service, "")
        if not base.startswith("http://127.0.0.1:") or not path.startswith("/api/v1/"):
            raise CoreError(ErrorCode.FORBIDDEN)
        headers = {"Authorization": "Bearer " + token}
        if key:
            headers["Idempotency-Key"] = key
        try:
            response = self.http.request(method, base + path, json=payload, headers=headers)
        except httpx.TimeoutException:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
        except httpx.HTTPError:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
        if response.status_code not in (200, 201):
            raise CoreError(
                ErrorCode.INVALID_STATE
                if response.status_code in (404, 409)
                else ErrorCode.UPSTREAM_UNAVAILABLE
            )
        return response.json()

    def command(
        self,
        actor,
        body,
        action,
        key,
        operation,
        *,
        response_status=200,
        resource_type=None,
        resource_id=None,
    ):
        self.allow(actor, body.org_id, action)  # Also before cached-result access.
        if not key or not 1 <= len(key) <= 128:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        digest = hashlib.sha256(
            json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        scope = f"workflow:{self.domain}:{action}"
        namespace = f"{body.org_id}:{scope}:{key}"
        lock = int.from_bytes(hashlib.sha256(namespace.encode()).digest()[:8], signed=True)
        try:
            with self.engine.begin() as conn:
                conn.execute(sa.text("SET LOCAL lock_timeout='5s'"))
                conn.execute(sa.select(sa.func.pg_advisory_xact_lock(lock)))
                previous = (
                    conn.execute(
                        sa.select(self.receipts).where(
                            self.receipts.c.org_id == body.org_id,
                            self.receipts.c.scope == scope,
                            self.receipts.c.idempotency_key == key,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if previous:
                    if previous["request_hash"] != digest:
                        raise CoreError(ErrorCode.IDEMPOTENCY_CONFLICT)
                    return previous["response_body"]
                result = jsonable_encoder(operation(conn))
                now = datetime.now(UTC)
                conn.execute(
                    self.receipts.insert().values(
                        id=uuid4(),
                        org_id=body.org_id,
                        scope=scope,
                        idempotency_key=key,
                        actor_subject=actor.subject,
                        request_hash=digest,
                        status="SUCCEEDED",
                        response_status=response_status,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        response_body=result,
                        created_at=now,
                        completed_at=now,
                    )
                )
                return result
        except SQLAlchemyError as error:
            code = getattr(getattr(error, "orig", None), "sqlstate", None)
            logging.getLogger(__name__).warning("Business transaction rejected: SQLSTATE=%s", code)
            raise CoreError(
                ErrorCode.VERSION_CONFLICT
                if code in {"23505", "40001", "40P01"}
                else ErrorCode.UPSTREAM_UNAVAILABLE
            ) from None

    def fetch(self, table, org_id, **filters):
        with self.engine.connect() as conn:
            return self.one(conn, table, org_id, **filters)

    @staticmethod
    def one(conn, table, org_id, *, lock=False, **filters):
        query = sa.select(table).where(
            table.c.org_id == org_id, *(table.c[k] == value for k, value in filters.items())
        )
        row = conn.execute(query.with_for_update() if lock else query).mappings().one_or_none()
        if row is None:
            raise CoreError(ErrorCode.NOT_FOUND)
        return dict(row)

    def application(self):
        identity = IdentitySettings()
        tls = (
            ssl.create_default_context(cafile=str(identity.oidc_ca_bundle))
            if identity.oidc_ca_bundle
            else True
        )

        @asynccontextmanager
        async def lifespan(app):
            app.state.resources = SimpleNamespace(http=httpx.AsyncClient(verify=tls, timeout=10))
            try:
                for attempt in range(5):
                    try:
                        with self.engine.connect() as conn:
                            conn.execute(sa.text("SELECT 1"))
                        break
                    except SQLAlchemyError:
                        if attempt == 4:
                            raise RuntimeError("Business database is not ready") from None
                        await asyncio.sleep(0.5)
                yield
            finally:
                await app.state.resources.http.aclose()
                self.http.close()
                self.engine.dispose()

        app = FastAPI(title=f"FlowBridge {self.domain} workflow slice", lifespan=lifespan)
        app.state.settings = identity
        register_exception_handlers(app)
        app.add_middleware(PrivateJsonMiddleware)
        app.add_middleware(CorrelationMiddleware)

        @app.get("/healthz")
        def health():
            with self.engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            return {"ready": True, "domain": self.domain}

        return app
