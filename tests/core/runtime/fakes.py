import asyncio
import json
import re
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from time import time
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import SecretStr

from app.core.applications.manifest import source_name
from app.core.data_products.reading import HasuraDataReader
from app.core.data_sources.discovery import SourceSnapshot
from app.core.data_sources.service import ApprovedSourcePolicy, SourceDiscoveryService
from app.core.db.ports import StoredResource
from app.core.hasura_provisioner.adapters import EnvironmentSecretResolver
from app.core.hasura_provisioner.client import HasuraMetadataClient
from app.core.hasura_provisioner.schemas import TableRef
from app.core.hasura_provisioner.settings import HasuraSettings
from app.core.hasura_provisioner.worker import PublicationWorker
from app.main import CoreDependencies, create_app
from app.platform.http.errors import CoreError, ErrorCode
from app.platform.identity.authentication import VerifiedIdentity
from app.platform.identity.settings import IdentitySettings

WAREHOUSE = str(UUID(int=100))
SALES = str(UUID(int=101))
ADMIN = str(UUID(int=1))
CONSUMER = str(UUID(int=2))
OWNER = str(UUID(int=3))
ISSUER = "https://identity.example.test/realms/dxos"
ROOT = Path(__file__).resolve().parents[3]


class MemoryStore:
    durable = False

    def __init__(self):
        self.data = {
            kind: {}
            for kind in [
                "department",
                "application",
                "source",
                "product",
                "contract",
                "request",
                "grant",
                "job",
            ]
        }
        self.data["department"][WAREHOUSE] = StoredResource(
            {"id": WAREHOUSE, "code": "warehouse", "status": "ACTIVE"}, {}
        )
        self.data["department"][SALES] = StoredResource(
            {"id": SALES, "code": "sales", "status": "ACTIVE"}, {}
        )
        self.principals = {
            "admin": self.principal(ADMIN, "PLATFORM_ADMIN", []),
            "consumer": self.principal(CONSUMER, "NONE", [self.membership(SALES)]),
            "owner": self.principal(OWNER, "NONE", [self.membership(WAREHOUSE, "OWNER")]),
        }
        self.bindings = {
            subject: {
                "issuer": ISSUER,
                "subject": subject,
                "kind": "human",
                "service_client_id": None,
            }
            for subject in self.principals
        }
        self.receipts, self.audits, self.locks = {}, [], {}
        self.mutex = asyncio.Lock()
        self.clock = datetime.now(UTC)
        self.audit_failure = False

    @staticmethod
    def membership(department_id, role="STAFF"):
        return {
            "department_id": department_id,
            "membership_role": role,
            "status": "ACTIVE",
            "expires_at": None,
            "department_status": "ACTIVE",
        }

    @staticmethod
    def principal(principal_id, role, memberships):
        return {
            "principal_id": principal_id,
            "principal_status": "ACTIVE",
            "platform_role": role,
            "application_id": None,
            "memberships": memberships,
        }

    @asynccontextmanager
    async def transaction(self):
        async with self.mutex:
            snapshot = deepcopy((self.data, self.receipts, self.audits))
            try:
                yield self
            except BaseException:
                self.data, self.receipts, self.audits = snapshot
                raise

    @asynccontextmanager
    async def permission_lock(self, product_id, department_id):
        async with self.locks.setdefault((product_id, department_id), asyncio.Lock()):
            yield

    async def now(self):
        return self.clock

    async def get(self, kind, resource_id):
        try:
            return deepcopy(self.data[kind][str(resource_id)])
        except KeyError:
            raise CoreError(ErrorCode.NOT_FOUND) from None

    async def find(self, kind, **equal):
        return deepcopy(
            [
                item
                for item in self.data[kind].values()
                if all(item.public.get(key) == value for key, value in equal.items())
            ]
        )

    async def insert(self, kind, resource):
        if resource.public["id"] in self.data[kind]:
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        self.data[kind][resource.public["id"]] = deepcopy(resource)

    async def claimable_jobs(self, now, *, limit):
        from app.core.governance.service import instant

        return deepcopy(
            sorted(
                [
                    item
                    for item in self.data["job"].values()
                    if item.public["status"] in {"QUEUED", "RETRY"}
                    and instant(item.public["next_attempt_at"]) <= now
                    or item.public["status"] == "RUNNING"
                    and instant(item.private["lease_expires_at"]) <= now
                ],
                key=lambda item: (item.public["next_attempt_at"], item.public["id"]),
            )[:limit]
        )

    async def replace(self, kind, resource):
        assert resource.public["id"] in self.data[kind]
        self.data[kind][resource.public["id"]] = deepcopy(resource)

    async def receipt(self, namespace):
        return deepcopy(self.receipts.get(namespace))

    async def save_receipt(self, namespace, receipt):
        assert namespace not in self.receipts
        self.receipts[namespace] = deepcopy(receipt)

    async def resolve_identity(self, identity):
        if identity.issuer != ISSUER or identity.subject not in self.principals:
            raise CoreError(ErrorCode.FORBIDDEN)
        return {
            **deepcopy(self.bindings[identity.subject]),
            "context": deepcopy(self.principals[identity.subject]),
        }

    async def identity_for_principal(self, principal_id):
        for subject, principal in self.principals.items():
            if principal["principal_id"] == str(principal_id):
                binding = self.bindings[subject]
                return VerifiedIdentity(
                    ISSUER,
                    subject,
                    client_id=binding["service_client_id"] or "portal",
                    account_type=binding["kind"],
                )
        raise CoreError(ErrorCode.FORBIDDEN)

    async def audit(self, actor_id, correlation_id, action, resource_id, **details):
        if self.audit_failure:
            raise RuntimeError("SYNTHETIC_PRIVATE_DATABASE_ERROR")
        self.audits.append((str(actor_id), correlation_id, action, str(resource_id), details))


class Discovery:
    def __init__(self):
        self.calls = 0
        self.kind, self.primary = "table", ["sku"]
        self.nullable = False

    async def discover(self, source):
        self.calls += 1
        return SourceSnapshot.model_validate(
            {
                "source_key": source.key,
                "schemas": ["public"],
                "objects": [
                    {
                        "table": {"schema": "public", "name": "stock_available"},
                        "kind": self.kind,
                        "columns": [
                            {"name": "sku", "pg_type": "TEXT", "nullable": False},
                            {
                                "name": "available_quantity",
                                "pg_type": "INTEGER",
                                "nullable": self.nullable,
                            },
                        ],
                        "primary_key": self.primary,
                    }
                ],
            }
        )


class Credentials:
    def __init__(self):
        self.contexts = {}

    async def issue(self, context):
        token = str(uuid4())
        self.contexts[token] = context
        return SecretStr(token)


class MetadataEngine:
    def __init__(self, credentials):
        self.credentials = credentials
        self.metadata = {"version": 3, "sources": []}
        self.version = 1
        self.rows = [{"sku": "SKU-1", "available_quantity": 5}]
        self.data_requests = []
        self.partial_error = False
        self.timeout = False
        self.source_timeout_after_commit = False
        self.timeout_after = None
        self.mutations = []

    def request(self, request):
        if request.url.path == "/introspect":
            token = parse_qs(request.content.decode()).get("token", [""])[0]
            return httpx.Response(
                200,
                json={
                    "active": token in {"admin", "consumer", "owner", "unknown", "service"},
                    "iss": ISSUER,
                    "sub": token,
                    "aud": ["core"],
                    "exp": time() + 600,
                    "client_id": "service-client" if token == "service" else "portal",
                    "token_type": "Bearer",
                },
            )
        payload = json.loads(request.content)
        if request.url.path == "/v1/graphql":
            assert "x-hasura-admin-secret" not in request.headers
            assert not any(key.startswith("x-hasura-") for key in request.headers)
            self.data_requests.append(payload)
            if self.timeout:
                raise httpx.ReadTimeout("SYNTHETIC_TIMEOUT")
            token = request.headers["Authorization"].removeprefix("Bearer ")
            context = self.credentials.contexts[token]
            query = payload["query"]
            namespace, root, selection = re.search(
                r"\{(\w+)\{rows:(\w+)\(.*?\)\{(.*?)\}", query
            ).groups()
            source = next(
                source for source in self.metadata["sources"] if source["name"] == namespace
            )
            table = next(table for table in source["tables"] if table["table"]["name"] == root)
            permission = next(
                (
                    item
                    for item in table.get("select_permissions", [])
                    if item["role"] == context.enforcement_role
                ),
                None,
            )
            selected = {item.split(":")[0] for item in selection.split()}
            if permission is None or not selected <= set(permission["permission"]["columns"]):
                return httpx.Response(200, json={"errors": [{"message": "denied"}]})
            variables = payload["variables"]
            rows = [
                {key: row.get(key) for key in selected}
                for row in sorted(self.rows, key=lambda row: row["sku"])
            ][variables["offset"] : variables["offset"] + variables["limit"]]
            result = {"data": {namespace: {"rows": rows}}}
            if self.partial_error:
                result["errors"] = [{"message": "SYNTHETIC_PRIVATE_ERROR"}]
            return httpx.Response(200, json=result)
        assert request.url.path == "/v1/metadata"
        assert request.headers["x-hasura-admin-secret"] == "SYNTHETIC_ADMIN_ONLY"
        operation, args = payload["type"], payload["args"]
        if operation == "export_metadata":
            return httpx.Response(
                200, json={"resource_version": self.version, "metadata": self.metadata}
            )
        if operation == "get_inconsistent_metadata":
            return httpx.Response(200, json={"is_consistent": True, "inconsistent_objects": []})
        assert payload["resource_version"] == self.version
        self.mutations.append(operation)
        if operation == "pg_add_source":
            self.metadata["sources"].append(
                {
                    "name": args["name"],
                    "kind": "postgres",
                    "configuration": args["configuration"],
                    "customization": args["customization"],
                    "tables": [],
                }
            )
            self.version += 1
            if self.source_timeout_after_commit:
                self.source_timeout_after_commit = False
                raise httpx.ReadTimeout("SYNTHETIC_AMBIGUOUS_COMMIT")
            return httpx.Response(200, json={"message": "success"})
        source = next(
            source for source in self.metadata["sources"] if source["name"] == args["source"]
        )
        if operation == "pg_track_table":
            source["tables"].append({"table": args["table"]})
        else:
            table = next(table for table in source["tables"] if table["table"] == args["table"])
            if operation == "pg_set_table_customization":
                table["configuration"] = args["configuration"]
            elif operation == "pg_create_select_permission":
                table.setdefault("select_permissions", []).append(
                    {
                        "role": args["role"],
                        "permission": args["permission"],
                        "comment": args["comment"],
                    }
                )
            elif operation == "pg_drop_select_permission":
                table["select_permissions"] = [
                    permission
                    for permission in table["select_permissions"]
                    if permission["role"] != args["role"]
                ]
            else:
                raise AssertionError(operation)
        self.version += 1
        if operation == self.timeout_after:
            self.timeout_after = None
            raise httpx.ReadTimeout("SYNTHETIC_LOST_REPLY_AFTER_COMMIT")
        return httpx.Response(200, json={"message": "success"})


class ContractAssertions:
    def __init__(self):
        self.validators = {}
        self.documents = {}
        for path in (ROOT / "contracts").glob("*.yaml"):
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.documents[path.resolve().as_uri()] = document
        identity = ROOT / "contracts/identity-context.schema.json"
        self.documents[identity.resolve().as_uri()] = json.loads(
            identity.read_text(encoding="utf-8")
        )
        self.operations = []
        for filename in [
            "core-api.openapi.yaml",
            "data-governance.yaml",
            "application-onboarding.yaml",
        ]:
            uri = (ROOT / "contracts" / filename).resolve().as_uri()
            for path, item in self.documents[uri]["paths"].items():
                for method in ["get", "post", "patch"]:
                    if method in item:
                        self.operations.append((method, path, item[method], uri))

    def validate(self, response):
        from urllib.parse import urldefrag, urljoin

        method, path = response.request.method.lower(), response.request.url.path
        match = next(
            (
                item
                for item in self.operations
                if item[0] == method and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", item[1]), path)
            ),
            None,
        )
        assert match, (method, path)
        _, _, operation, base = match

        def expand(value, uri):
            if isinstance(value, list):
                return [expand(item, uri) for item in value]
            if not isinstance(value, dict):
                return value
            if "$ref" in value:
                target_uri, fragment = urldefrag(urljoin(uri, value["$ref"]))
                document = self.documents[target_uri]
                for part in fragment.strip("/").split("/") if fragment else ():
                    document = document[part.replace("~1", "/").replace("~0", "~")]
                return expand(document, target_uri)
            return {key: expand(item, uri) for key, item in value.items()}

        cache_key = (method, match[1], response.status_code)
        if cache_key not in self.validators:
            spec = expand(operation["responses"][str(response.status_code)], base)
            self.validators[cache_key] = Draft202012Validator(
                spec["content"]["application/json"]["schema"], format_checker=FormatChecker()
            )
        self.validators[cache_key].validate(response.json())
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Correlation-Id"]
        if response.status_code == 202:
            assert response.headers["Location"]
            assert int(response.headers["Retry-After"]) > 0


@lru_cache
def contract_assertions():
    return ContractAssertions()


class Harness:
    def __init__(self):
        self.store, self.discovery, self.credentials = MemoryStore(), Discovery(), Credentials()
        self.engine = MetadataEngine(self.credentials)
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.engine.request))
        self.settings = HasuraSettings(
            _env_file=None,
            endpoint="http://127.0.0.1:8080",
            admin_secret="SYNTHETIC_ADMIN_ONLY",
            allowed_sources={source_name("warehouse", "pg")},
            allowed_database_hosts={"warehouse.example.test"},
        )
        self.metadata = HasuraMetadataClient(self.settings, self.http)
        secrets = EnvironmentSecretResolver(
            {"warehouse-reader": "WAREHOUSE_URL"},
            {
                "WAREHOUSE_URL": "postgresql://reader:SYNTHETIC@warehouse.example.test/warehouse_db?sslmode=verify-full"
            },
        )
        policy = ApprovedSourcePolicy(
            secrets,
            frozenset({"warehouse.example.test"}),
            {"warehouse-reader": (TableRef(schema="public", name="stock_available"),)},
        )
        self.dependencies = CoreDependencies(
            self.store,
            SourceDiscoveryService(self.discovery, policy),
            HasuraDataReader(
                self.http, self.settings, self.credentials, verified_auth_profile=True
            ),
            self.http,
            IdentitySettings(
                _env_file=None,
                oidc_issuer=ISSUER,
                oidc_audience="core",
                oidc_introspection_url="https://identity.example.test/introspect",
                oidc_client_id="core",
                oidc_client_secret="SYNTHETIC_IDP",
            ),
            metadata=self.metadata,
        )
        self.app = create_app(self.dependencies, testing=True)
        self.api = httpx.AsyncClient(
            transport=httpx.ASGITransport(self.app), base_url="http://core.test"
        )
        self.worker = PublicationWorker(self.app.state.core, self.metadata, self.settings)
        self.contracts = contract_assertions()

    async def request(self, method, path, *, actor="admin", key=None, **kwargs):
        headers = {"Authorization": "Bearer " + actor, "X-Correlation-Id": "runtime-check"}
        if method.upper() in {"POST", "PATCH"}:
            headers["Idempotency-Key"] = key or str(uuid4())
        headers.update(kwargs.pop("headers", {}))
        response = await self.api.request(method, "/api/v1/core" + path, headers=headers, **kwargs)
        self.contracts.validate(response)
        return response

    async def close(self):
        await self.api.aclose()
        await self.http.aclose()

    def record(self, kind, resource_id):
        return self.store.data[kind][str(resource_id)].public


def contract_document():
    return {
        "document_version": "1.0",
        "grain": "One row per SKU",
        "business_key": ["sku"],
        "classification": "INTERNAL",
        "description": "Synthetic inventory",
        "relationships": [],
        "fields": [
            {
                "column": "sku",
                "name": "sku",
                "physical_type": "TEXT",
                "wire_type": "string",
                "nullable": False,
                "description": None,
            },
            {
                "column": "available_quantity",
                "name": "available",
                "physical_type": "INTEGER",
                "wire_type": "integer",
                "nullable": False,
                "description": None,
                "constraints": {"minimum": 0},
            },
        ],
    }


async def published(harness):
    response = await harness.request(
        "POST",
        "/applications",
        json={"department_id": WAREHOUSE, "app_key": "warehouse", "name": "Warehouse"},
    )
    assert response.status_code == 201, response.text
    application = response.json()
    response = await harness.request(
        "PATCH",
        "/applications/" + application["id"],
        json={"expected_revision": 1, "status": "ACTIVE"},
    )
    assert response.status_code == 200, response.text
    response = await harness.request(
        "POST",
        "/data-sources",
        json={
            "application_id": application["id"],
            "source_key": "warehouse.pg",
            "db_type": "POSTGRESQL",
            "host": "warehouse.example.test",
            "port": 5432,
            "database_name": "warehouse_db",
            "db_user": "reader",
            "secret_ref": "warehouse-reader",
        },
    )
    assert response.status_code == 201, response.text
    source = response.json()
    response = await harness.request(
        "POST", f"/data-sources/{source['id']}/connect", json={"expected_revision": 1}
    )
    assert response.status_code == 202, response.text
    assert await harness.worker.run_once("worker-one")
    assert harness.record("job", response.json()["job_id"])["status"] == "SUCCEEDED"
    response = await harness.request(
        "POST",
        "/data-products",
        json={
            "data_source_id": source["id"],
            "product_key": "warehouse.inventory",
            "name": "Available stock",
            "schema_name": "public",
            "object_name": "stock_available",
            "object_type": "TABLE",
            "classification": "INTERNAL",
        },
    )
    assert response.status_code == 201, response.text
    product = response.json()
    response = await harness.request(
        "POST",
        f"/data-products/{product['id']}/contracts",
        json={"version": "1.0.0", "contract_json": contract_document()},
    )
    assert response.status_code == 201, response.text
    contract = response.json()
    response = await harness.request(
        "POST", f"/data-contracts/{contract['id']}/publish", json={"expected_revision": 1}
    )
    assert response.status_code == 200, response.text
    response = await harness.request(
        "POST",
        f"/data-products/{product['id']}/publish",
        json={"expected_revision": 1, "contract_version_id": contract["id"]},
    )
    assert response.status_code == 202, response.text
    assert await harness.worker.run_once("worker-one")
    job = harness.record("job", response.json()["job_id"])
    assert job["status"] == "SUCCEEDED", job
    return application, source, product, contract


async def granted(harness):
    application, source, product, contract = await published(harness)
    scope = {
        "action": "SELECT",
        "columns": ["sku", "available_quantity"],
        "row_scope": {"kind": "ALL"},
    }
    response = await harness.request(
        "POST",
        "/access-requests",
        json={
            "data_product_id": product["id"],
            "contract_version_id": contract["id"],
            "consumer_department_id": SALES,
            "consumer_principal_id": None,
            "requested_scope_json": scope,
            "reason": "Stock lookup",
        },
    )
    assert response.status_code == 201, response.text
    access_request = response.json()
    response = await harness.request(
        "POST",
        f"/access-requests/{access_request['id']}/approve",
        json={"expected_revision": 1, "scope_json": scope, "expires_at": None},
    )
    assert response.status_code == 202, response.text
    grant = response.json()["grant"]
    return application, source, product, contract, grant


async def read(harness, product, contract, **kwargs):
    return await harness.request(
        "GET",
        f"/data-products/{product['id']}/rows",
        actor=kwargs.pop("actor", "consumer"),
        params=kwargs.pop(
            "params", {"contract_version_id": contract["id"], "columns": "sku,available"}
        ),
        **kwargs,
    )
