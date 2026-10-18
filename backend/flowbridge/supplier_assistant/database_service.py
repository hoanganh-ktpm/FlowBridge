# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Database use cases; commands and their retry responses commit in one transaction.
import hashlib
import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.identity.directory import KeycloakDirectory
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.contract import dto_validator
from flowbridge.supplier_assistant.policy import SupplierPolicy, require
from flowbridge.supplier_assistant.repository import SupplierRepository, dto, table

CATALOGS = {
    "listSuppliers": "suppliers",
    "listProducts": "products",
    "listSupplierProducts": "supplier_products",
    "listDepartments": "departments",
    "listApplicationUsers": "users",
    "listApplicationRoles": "roles",
}
AGGREGATES = {
    "listPurchaseRequests": "purchase_requests",
    "listPurchaseProposals": "proposals",
    "listPurchaseOrders": "purchase_orders",
}
DETAILS = {
    "getPurchaseRequest": ("purchase_requests", "request_id"),
    "getAnalysisJob": ("analysis_tasks", "task_id"),
    "getPurchaseProposal": ("proposals", "proposal_id"),
    "getPurchaseOrder": ("purchase_orders", "order_id"),
}
UNAVAILABLE = {
    "decidePurchaseProposal",
    "sendChatMessage",
    "getChatHistory",
    "retrieveSupplierKnowledge",
    "indexPreparedMarkdown",
    "compareSupplierOptions",
    "runAutomatedRiskAnalysis",
    "predictDeliveryRisk",
}


BUSINESS_TIMEZONE = timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")


def now() -> datetime:
    return datetime.now(UTC)


def amount(value: Decimal) -> Decimal:
    require(
        value.is_finite() and Decimal(0) <= value <= Decimal("99999999999999.9999"),
        ErrorCode.VALIDATION_ERROR,
    )
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class SupplierDatabaseService:
    def __init__(
        self, repository: SupplierRepository, directory: KeycloakDirectory | None = None
    ) -> None:
        self.repo = repository
        self.policy = SupplierPolicy(repository)
        self.directory = directory

    async def execute(
        self,
        operation: str,
        role: str,
        payload: dict[str, Any],
        path: dict[str, str],
        query: dict[str, Any],
        response_schema: str,
        identity: VerifiedIdentity | None,
        idempotency_key: str | None = None,
        confirmation_token: str | None = None,
        *,
        read: bool = False,
    ) -> dict[str, Any]:
        if role != "SUPPLIER_TOKEN":
            require(identity is not None, ErrorCode.UNAUTHENTICATED)
            await self.policy.authenticate(identity)
            if operation == "getApplicationProfile" and read:
                self.policy.authorize_profile()
            else:
                self.policy.authorize(role, read=read)
            await self.authorize_resource(path, payload)
            actor_key = str(self.policy.user["user_id"])
        else:
            # Token-bound scope prevents replay under another supplier's credential.
            require(bool(confirmation_token), ErrorCode.TOKEN_INVALID)
            token_hash = hashlib.sha256(confirmation_token.encode()).hexdigest()
            tokens = await self.repo.rows(
                "confirmation_tokens",
                table("confirmation_tokens").c.token_hash == token_hash,
            )
            require(bool(tokens), ErrorCode.TOKEN_INVALID)
            token = tokens[0]
            require(str(token["purchase_order_id"]) == path["order_id"], ErrorCode.TOKEN_INVALID)
            require(token["revoked_at"] is None, ErrorCode.TOKEN_INVALID)
            require(token["expires_at"] > now(), ErrorCode.TOKEN_EXPIRED)
            actor_key = "supplier:" + str(token["token_id"])
        scope = actor_key + ":" + operation
        digest = hashlib.sha256(
            json.dumps(
                {"operation": operation, "path": path, "query": query, "payload": payload},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if not read:
            require(bool(idempotency_key), ErrorCode.VALIDATION_ERROR)
            lock_key = int.from_bytes(
                hashlib.sha256((scope + ":" + idempotency_key).encode()).digest()[:8],
                "big",
                signed=True,
            )
            await self.repo.session.execute(sa.select(sa.func.pg_advisory_xact_lock(lock_key)))
            retries = table("api_idempotency")
            cached = await self.repo.rows(
                "api_idempotency",
                retries.c.scope == scope,
                retries.c.key == idempotency_key,
            )
            if cached:
                require(cached[0]["request_hash"] == digest, ErrorCode.IDEMPOTENCY_CONFLICT)
                # Permissions are checked above on every replay, including revoked grants.
                return cached[0]["response"]
        result = await self.dispatch(
            operation, payload, path, query, idempotency_key, confirmation_token
        )
        dto_validator(response_schema).validate(result)
        if not read:
            await self.repo.insert(
                "api_idempotency",
                {
                    "scope": scope,
                    "key": idempotency_key,
                    "request_hash": digest,
                    "response": result,
                    "created_at": now(),
                },
            )
        return result

    async def authorize_resource(self, path: dict[str, str], body: dict[str, Any]) -> None:
        resources = {
            "request_id": "purchase_requests",
            "task_id": "analysis_tasks",
            "proposal_id": "proposals",
            "order_id": "purchase_orders",
        }
        for key, name in resources.items():
            if key in path:
                row = await self.repo.get(name, path[key])
                await self.policy.request(row["request_id"])
        if "confirmation_id" in path:
            confirmation = await self.repo.get("supplier_confirmations", path["confirmation_id"])
            order = await self.repo.get("purchase_orders", confirmation["purchase_order_id"])
            await self.policy.request(order["request_id"])
        if body.get("request_id"):
            await self.policy.request(body["request_id"])

    async def dispatch(
        self,
        op: str,
        body: dict[str, Any],
        path: dict[str, str],
        query: dict[str, Any],
        key: str | None,
        token: str | None,
    ) -> dict[str, Any]:
        page, size = query.get("page", 1), query.get("page_size", 20)
        if op in CATALOGS:
            return await self.repo.page(CATALOGS[op], [], page, size)
        if op in AGGREGATES:
            name = AGGREGATES[op]
            conditions = self.policy.request_conditions()
            if name != "purchase_requests":
                requests = sa.select(table("purchase_requests").c.request_id).where(*conditions)
                conditions = [table(name).c.request_id.in_(requests)]
            return await self.repo.page(name, conditions, page, size)
        if op in DETAILS:
            name, pk = DETAILS[op]
            row = await self.repo.get(name, path[pk])
            await self.policy.request(row["request_id"])
            return await self.repo.aggregate(name, row)
        if op in UNAVAILABLE:
            model_ops = {
                "sendChatMessage",
                "compareSupplierOptions",
                "runAutomatedRiskAnalysis",
                "predictDeliveryRisk",
            }
            raise CoreError(
                ErrorCode.MODEL_UNAVAILABLE if op in model_ops else ErrorCode.UPSTREAM_UNAVAILABLE
            )
        match op:
            case "provisionApplicationUser":
                return await self.provision_user(body)
            case "getApplicationProfile":
                grants = [g for rows in self.policy.grants.values() for g in rows]
                ids = {g["role_id"] for g in grants}
                roles = await self.repo.rows("roles", table("roles").c.role_id.in_(ids))
                return {
                    "user": dto("users", self.policy.user),
                    "grants": [dto("user_roles", g) for g in grants],
                    "roles": [dto("roles", r) for r in roles],
                }
            case "createPurchaseRequest" | "updatePurchaseRequest":
                return await self.purchase_request(body, path.get("request_id"))
            case "submitPurchaseRequest" | "cancelPurchaseRequest":
                request = await self.editable_request(path["request_id"], body)
                if op == "submitPurchaseRequest":
                    require(
                        await self.repo.count(
                            "purchase_request_items",
                            table("purchase_request_items").c.request_id == request["request_id"],
                        )
                        > 0
                    )
                request = await self.repo.update(
                    "purchase_requests",
                    request["request_id"],
                    {
                        "status": "analysing" if op == "submitPurchaseRequest" else "cancelled",
                        "submitted_at": now()
                        if op == "submitPurchaseRequest"
                        else request["submitted_at"],
                        "updated_at": now(),
                    },
                )
                await self.event(
                    "purchase_request",
                    request["request_id"],
                    "purchase_request.submitted"
                    if op == "submitPurchaseRequest"
                    else "purchase_request.cancelled",
                    {"input_revision": request["input_revision"]},
                )
                return await self.repo.aggregate("purchase_requests", request)
            case "createAnalysisJob" | "registerAutomaticAnalysis":
                return await self.create_analysis(
                    body, key, automatic=op == "registerAutomaticAnalysis"
                )
            case "claimAnalysisJob" | "completeAnalysisJob" | "failAnalysisJob":
                return await self.change_analysis(op, path["task_id"], body)
            case "createPurchaseProposal":
                return await self.create_proposal(body)
            case "issueApprovedPurchaseOrder":
                return await self.issue_orders(body)
            case "listGoodsReceipts" | "recordGoodsReceipt":
                order = await self.repo.get("purchase_orders", path["order_id"])
                await self.policy.request(order["request_id"], roles={"WAREHOUSE"})
                if op == "recordGoodsReceipt":
                    return await self.receipt(order, body)
                items = sa.select(table("purchase_order_items").c.po_item_id).where(
                    table("purchase_order_items").c.purchase_order_id == order["purchase_order_id"]
                )
                schedules = sa.select(table("purchase_order_schedules").c.schedule_id).where(
                    table("purchase_order_schedules").c.po_item_id.in_(items)
                )
                return await self.repo.page(
                    "goods_receipts",
                    [table("goods_receipts").c.schedule_id.in_(schedules)],
                    page,
                    size,
                )
            case "previewSupplierConfirmation" | "recordSupplierConfirmation":
                return await self.confirmation(op, path["order_id"], body, token)
            case "appendConfirmationDetail":
                confirmation = await self.repo.get(
                    "supplier_confirmations", path["confirmation_id"]
                )
                order = await self.repo.get("purchase_orders", confirmation["purchase_order_id"])
                await self.policy.request(order["request_id"], lock=True)
                return await self.confirmation_item(
                    confirmation, body["item"], body["expected_revision_no"]
                )
            case "requestEmailRetry" | "recordOrderDispatch":
                return await self.email(op, path["order_id"], body)
            case "listApplicationUserRoles":
                user = await self.repo.get("users", path["user_id"])
                return await self.repo.page(
                    "user_roles", [table("user_roles").c.user_id == user["user_id"]], page, size
                )
            case "updateApplicationUser" | "grantApplicationRole" | "revokeApplicationRole":
                return await self.admin(op, path, body)
            case "lookupSupplierData":
                return await self.lookup(body)
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)

    async def event(
        self,
        aggregate: str,
        identifier: UUID,
        kind: str,
        payload: dict[str, Any],
    ) -> UUID:
        identifier_event = uuid4()
        await self.repo.insert(
            "outbox_events",
            {
                "event_id": identifier_event,
                "aggregate_type": aggregate,
                "aggregate_id": identifier,
                "event_type": kind,
                "payload": payload,
                "idempotency_key": str(identifier_event),
                "status": "pending",
                "attempt_count": 0,
                "available_at": now(),
                "published_at": None,
                "last_error": None,
                "created_at": now(),
            },
        )
        return identifier_event

    async def editable_request(self, identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        request = await self.policy.request(identifier, lock=True, roles={"EMPLOYEE"})
        require(
            request["input_revision"] == body["expected_input_revision"], ErrorCode.REQUEST_CHANGED
        )
        require(request["status"] in {"draft", "needs_review", "analysis_failed"})
        require(
            await self.repo.count(
                "purchase_orders",
                table("purchase_orders").c.request_id == request["request_id"],
                table("purchase_orders").c.status != "cancelled",
            )
            == 0
        )
        return request

    async def purchase_request(
        self, body: dict[str, Any], identifier: str | None
    ) -> dict[str, Any]:
        if identifier:
            request = await self.editable_request(identifier, body)
            revision = request["input_revision"] + 1
        else:
            actor = self.policy.user
            require(actor["department_id"] is not None, ErrorCode.FORBIDDEN)
            department = await self.repo.get("departments", actor["department_id"])
            require(department["status"] == "active", ErrorCode.FORBIDDEN)
            request_id = uuid4()
            request = await self.repo.insert(
                "purchase_requests",
                {
                    "request_id": request_id,
                    "request_code": "PR-" + str(request_id),
                    "department_id": actor["department_id"],
                    "requested_by": actor["user_id"],
                    "source": body["source"],
                    "status": "analysing",
                    "input_revision": 1,
                    "submitted_at": now(),
                    "notes": body.get("notes"),
                    "created_at": now(),
                    "updated_at": now(),
                },
            )
            revision = 1
        existing = await self.repo.rows(
            "purchase_request_items",
            table("purchase_request_items").c.request_id == request["request_id"],
        )
        # Keep retained line IDs stable; FK constraints reject removing referenced history.
        if len(body["items"]) < len(existing):
            await self.repo.session.execute(
                sa.delete(table("purchase_request_items")).where(
                    table("purchase_request_items").c.request_id == request["request_id"],
                    table("purchase_request_items").c.line_number > len(body["items"]),
                )
            )
        by_line = {row["line_number"]: row for row in existing}
        for index, item in enumerate(body["items"], 1):
            product = await self.repo.get("products", item["product_id"])
            require(product["status"] == "active", ErrorCode.VALIDATION_ERROR)
            quantity = Decimal(item["quantity"])
            price = None
            preferred = item.get("preferred_supplier_id")
            if preferred:
                supplier = await self.repo.get("suppliers", preferred)
                require(supplier["status"] == "active", ErrorCode.VALIDATION_ERROR)
                quote = await self.repo.one(
                    "supplier_products",
                    table("supplier_products").c.supplier_id == UUID(preferred),
                    table("supplier_products").c.product_id == UUID(item["product_id"]),
                )
                require(
                    quote["is_available"] and quantity >= quote["min_order_qty"],
                    ErrorCode.VALIDATION_ERROR,
                )
                if quote["currency"] == item["currency"]:
                    price = quote["current_unit_price"]
            values = {
                **item,
                "request_id": request["request_id"],
                "line_number": index,
                "preferred_supplier_id": preferred,
                "notes": item.get("notes"),
                "unit_price_snapshot": price,
                "line_total": amount(quantity * price) if price is not None else None,
            }
            if index in by_line:
                await self.repo.update(
                    "purchase_request_items", by_line[index]["request_item_id"], values
                )
            else:
                await self.repo.insert(
                    "purchase_request_items", {"request_item_id": uuid4(), **values}
                )
        request = await self.repo.update(
            "purchase_requests",
            request["request_id"],
            {
                "source": body.get("source", request["source"]),
                "notes": body.get("notes"),
                "input_revision": revision,
                "status": "analysing",
                "submitted_at": now(),
                "updated_at": now(),
            },
        )
        snapshot = await self.repo.aggregate("purchase_requests", request)
        await self.event(
            "purchase_request",
            request["request_id"],
            "purchase_request.created" if identifier is None else "purchase_request.updated",
            {"input_revision": revision, "request": snapshot},
        )
        return snapshot

    async def create_analysis(
        self, body: dict[str, Any], key: str, *, automatic: bool
    ) -> dict[str, Any]:
        request = await self.policy.request(body["request_id"], lock=True)
        require(request["input_revision"] == body["input_revision"], ErrorCode.REQUEST_CHANGED)
        require(request["status"] not in {"cancelled", "draft"})
        po_item = body.get("po_item_id")
        require((body["task_type"] == "po_monitoring") == bool(po_item), ErrorCode.VALIDATION_ERROR)
        if po_item:
            item = await self.repo.get("purchase_order_items", po_item)
            require(item["request_id"] == request["request_id"], ErrorCode.NOT_FOUND)
        require(
            await self.repo.count(
                "analysis_tasks",
                table("analysis_tasks").c.request_id == request["request_id"],
                table("analysis_tasks").c.status.in_(["queued", "running"]),
            )
            == 0,
            ErrorCode.ANALYSIS_IN_PROGRESS,
        )
        snapshot = await self.repo.aggregate("purchase_requests", request)
        cutoff = now()
        if automatic:
            source = await self.repo.get("outbox_events", body["source_event_id"])
            require(source["aggregate_id"] == request["request_id"], ErrorCode.REPORT_MISMATCH)
            require(
                source["event_type"]
                in {
                    "purchase_request.created",
                    "purchase_request.updated",
                    "purchase_request.submitted",
                },
                ErrorCode.REPORT_MISMATCH,
            )
            require(
                source["payload"].get("input_revision") == request["input_revision"],
                ErrorCode.REQUEST_CHANGED,
            )
            cutoff = datetime.fromisoformat(body["feature_asof"].replace("Z", "+00:00"))
            require(
                cutoff <= now() and body["input_snapshot"] == snapshot, ErrorCode.REQUEST_CHANGED
            )
            # The outbox event is the authoritative knowledge cutoff.
            require(cutoff == source["created_at"], ErrorCode.REQUEST_CHANGED)
        row = await self.repo.insert(
            "analysis_tasks",
            {
                "task_id": uuid4(),
                "request_id": request["request_id"],
                "po_item_id": po_item,
                "task_type": body["task_type"],
                "input_revision": request["input_revision"],
                "feature_asof": cutoff,
                "input_snapshot": snapshot,
                "idempotency_key": (
                    "event:"
                    + body["source_event_id"]
                    + ":"
                    + body["task_type"]
                    + ":"
                    + str(po_item or "")
                )
                if automatic
                else "api:" + str(uuid4()),
                "status": "queued",
                "started_at": None,
                "completed_at": None,
                "error_code": None,
                "error_message": None,
                "created_at": now(),
            },
        )
        await self.event(
            "analysis_task", row["task_id"], "analysis.queued", {"task_id": str(row["task_id"])}
        )
        return dto("analysis_tasks", row)

    async def change_analysis(
        self, op: str, identifier: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        task = await self.repo.get("analysis_tasks", identifier)
        request = await self.policy.request(task["request_id"], lock=True)
        task = await self.repo.get("analysis_tasks", identifier, lock=True)
        require(request["status"] != "cancelled")
        require(task["input_revision"] == request["input_revision"], ErrorCode.REQUEST_CHANGED)
        if op == "claimAnalysisJob":
            require(task["status"] == body["expected_status"] == "queued")
            updates = {"status": "running", "started_at": now()}
        else:
            require(task["status"] == "running")
            require(body["input_revision"] == task["input_revision"], ErrorCode.REQUEST_CHANGED)
            if op == "completeAnalysisJob":
                await self.analysis_results(task, body)
                updates = {"status": "succeeded", "completed_at": now()}
            else:
                updates = {
                    "status": "failed",
                    "completed_at": now(),
                    "error_code": body["error_code"],
                    "error_message": body["error_message"],
                }
            if task["task_type"] != "po_monitoring":
                await self.repo.update(
                    "purchase_requests",
                    request["request_id"],
                    {
                        "status": "needs_review"
                        if op == "completeAnalysisJob"
                        else "analysis_failed",
                        "updated_at": now(),
                    },
                )
        return dto(
            "analysis_tasks", await self.repo.update("analysis_tasks", task["task_id"], updates)
        )

    async def analysis_results(self, task: dict[str, Any], body: dict[str, Any]) -> None:
        report = body["report"]
        require(
            report["task_id"] == str(task["task_id"])
            and report["request_id"] == str(task["request_id"]),
            ErrorCode.REPORT_MISMATCH,
        )
        lines = {
            str(row["request_item_id"]): row
            for row in await self.repo.rows(
                "purchase_request_items",
                table("purchase_request_items").c.request_id == task["request_id"],
            )
        }
        predictions = {p["prediction_id"]: p for p in body["predictions"]}
        require(len(predictions) == len(body["predictions"]), ErrorCode.REPORT_MISMATCH)
        require(
            report["primary_prediction_id"] is None
            or report["primary_prediction_id"] in predictions,
            ErrorCode.REPORT_MISMATCH,
        )
        for prediction in predictions.values():
            line = lines.get(prediction["request_item_id"])
            require(line is not None, ErrorCode.REPORT_MISMATCH)
            require(
                prediction["task_id"] == str(task["task_id"])
                and prediction["request_id"] == str(task["request_id"])
                and prediction["input_revision"] == task["input_revision"]
                and prediction["product_id"] == str(line["product_id"]),
                ErrorCode.REPORT_MISMATCH,
            )
            require(
                datetime.fromisoformat(prediction["feature_asof"].replace("Z", "+00:00"))
                == task["feature_asof"],
                ErrorCode.REPORT_MISMATCH,
            )
            expected_stage = "PO_MONITORING" if task["po_item_id"] else "PR_CREATED"
            require(
                prediction["prediction_stage"] == expected_stage
                and prediction["po_item_id"]
                == (str(task["po_item_id"]) if task["po_item_id"] else None),
                ErrorCode.REPORT_MISMATCH,
            )
            require(
                prediction["feature_snapshot"]["x"]["supplier_id"] == prediction["supplier_id"],
                ErrorCode.REPORT_MISMATCH,
            )
            anchor = task["feature_asof"].astimezone(BUSINESS_TIMEZONE).date()
            lead_days = (date.fromisoformat(prediction["requested_delivery_date"]) - anchor).days
            require(
                prediction["requested_lead_days"] == lead_days
                and prediction["feature_snapshot"]["x"]["days_to_deadline"] == lead_days,
                ErrorCode.REPORT_MISMATCH,
            )
            if task["po_item_id"] is None:
                require(
                    Decimal(prediction["quantity"]) == line["quantity"]
                    and date.fromisoformat(prediction["requested_delivery_date"])
                    == line["requested_delivery_date"],
                    ErrorCode.REQUEST_CHANGED,
                )
            # Knowledge timestamps are server-owned; callbacks cannot backdate observations.
            prediction = {**prediction, "predicted_at": now()}
            await self.repo.insert("risk_predictions", prediction)
        await self.repo.insert("risk_analysis_reports", {**report, "created_at": now()})
        for candidate in body["candidates"]:
            prediction = predictions.get(candidate["prediction_id"])
            require(
                candidate["report_id"] == report["report_id"]
                and candidate["task_id"] == str(task["task_id"])
                and candidate["request_id"] == str(task["request_id"])
                and candidate["request_item_id"] in lines,
                ErrorCode.REPORT_MISMATCH,
            )
            if candidate["prediction_id"] is not None:
                require(
                    prediction is not None
                    and all(
                        candidate[k] == prediction[k]
                        for k in ("task_id", "request_id", "request_item_id", "supplier_id")
                    ),
                    ErrorCode.REPORT_MISMATCH,
                )
            await self.repo.insert("analysis_supplier_candidates", candidate)

    async def create_proposal(self, body: dict[str, Any]) -> dict[str, Any]:
        request = await self.policy.request(body["request_id"], lock=True)
        require(request["input_revision"] == body["input_revision"], ErrorCode.REQUEST_CHANGED)
        require(request["status"] in {"analysing", "needs_review", "analysis_failed", "rejected"})
        task = await self.repo.get("analysis_tasks", body["generation_task_id"])
        report = await self.repo.get("risk_analysis_reports", body["analysis_report_id"])
        require(
            task["request_id"] == request["request_id"]
            and task["input_revision"] == request["input_revision"]
            and task["status"] == "succeeded"
            and task["task_type"] != "po_monitoring"
            and report["task_id"] == task["task_id"]
            and report["request_id"] == request["request_id"],
            ErrorCode.REPORT_MISMATCH,
        )
        require(
            report["report_data"].get("decision_status") == "ready"
            and report["report_data"].get("selection_policy_version")
            == body["selection_policy_version"],
            ErrorCode.REPORT_MISMATCH,
        )
        require(
            await self.repo.count(
                "proposals", table("proposals").c.generation_task_id == task["task_id"]
            )
            == 0
        )
        require(
            await self.repo.count(
                "purchase_orders",
                table("purchase_orders").c.request_id == request["request_id"],
                table("purchase_orders").c.status != "cancelled",
            )
            == 0
        )
        candidates = await self.repo.rows(
            "analysis_supplier_candidates",
            table("analysis_supplier_candidates").c.report_id == report["report_id"],
            table("analysis_supplier_candidates").c.is_eligible.is_(True),
        )
        lines = await self.repo.rows(
            "purchase_request_items",
            table("purchase_request_items").c.request_id == request["request_id"],
        )
        require(bool(lines))
        selected = []
        for line in lines:
            options = [
                c
                for c in candidates
                if c["request_item_id"] == line["request_item_id"]
                and c["rank"] is not None
                and c["prediction_id"] is not None
                and c["unit_price_snapshot"] is not None
                and c["currency"] == line["currency"]
            ]
            require(bool(options), ErrorCode.INSUFFICIENT_DATA)
            candidate = min(options, key=lambda c: (c["rank"], str(c["candidate_id"])))
            prediction = await self.repo.get("risk_predictions", candidate["prediction_id"])
            require(
                prediction["task_id"] == task["task_id"]
                and prediction["request_item_id"] == line["request_item_id"]
                and prediction["supplier_id"] == candidate["supplier_id"]
                and prediction["status"] == "success"
                and prediction["prediction_stage"] == "PR_CREATED",
                ErrorCode.REPORT_MISMATCH,
            )
            selected.append((line, candidate))
        proposals = await self.repo.rows(
            "proposals", table("proposals").c.request_id == request["request_id"]
        )
        previous = next((p for p in proposals if p["is_current"]), None)
        if previous:
            await self.repo.update(
                "proposals", previous["proposal_id"], {"is_current": False, "status": "superseded"}
            )
        identifier = uuid4()
        proposal = await self.repo.insert(
            "proposals",
            {
                "proposal_id": identifier,
                "proposal_code": "PRO-" + str(identifier),
                "request_id": request["request_id"],
                "version_no": max((p["version_no"] for p in proposals), default=0) + 1,
                "input_revision": request["input_revision"],
                "supersedes_proposal_id": previous["proposal_id"] if previous else None,
                "is_current": True,
                "analysis_report_id": report["report_id"],
                "generation_task_id": task["task_id"],
                "selection_policy_version": body["selection_policy_version"],
                "workflow_instance_id": None,
                "reason": None,
                "status": "pending",
                "created_by": self.policy.user["user_id"],
                "submitted_at": now(),
                "created_at": now(),
            },
        )
        for line, candidate in selected:
            await self.repo.insert(
                "proposal_items",
                {
                    "proposal_item_id": uuid4(),
                    "proposal_id": identifier,
                    "request_id": request["request_id"],
                    "request_item_id": line["request_item_id"],
                    "selected_supplier_id": candidate["supplier_id"],
                    "prediction_id": candidate["prediction_id"],
                    "quantity": line["quantity"],
                    "unit_price_snapshot": candidate["unit_price_snapshot"],
                    "currency": candidate["currency"],
                    "proposed_delivery_date": line["requested_delivery_date"],
                    "reason": candidate["reason"],
                },
            )
        await self.repo.update(
            "purchase_requests",
            request["request_id"],
            {"status": "pending_approval", "updated_at": now()},
        )
        await self.event(
            "proposal",
            identifier,
            "proposal.submitted",
            {
                "proposal_id": str(identifier),
                "version_no": proposal["version_no"],
                "input_revision": request["input_revision"],
            },
        )
        return await self.repo.aggregate("proposals", proposal)

    async def issue_orders(self, body: dict[str, Any]) -> dict[str, Any]:
        proposal = await self.repo.get("proposals", body["approved_proposal_id"])
        request = await self.policy.request(proposal["request_id"], lock=True)
        proposal = await self.repo.get("proposals", proposal["proposal_id"], lock=True)
        require(proposal["status"] == "approved" and proposal["is_current"])
        require(proposal["version_no"] == body["expected_version_no"], ErrorCode.VERSION_CONFLICT)
        require(
            proposal["input_revision"] == body["input_revision"] == request["input_revision"],
            ErrorCode.REQUEST_CHANGED,
        )
        require(request["status"] == "approved")
        existing = await self.repo.rows(
            "purchase_orders", table("purchase_orders").c.proposal_id == proposal["proposal_id"]
        )
        if existing:
            return {"purchase_orders": await self.repo.aggregates("purchase_orders", existing)}
        lines = await self.repo.rows(
            "proposal_items", table("proposal_items").c.proposal_id == proposal["proposal_id"]
        )
        require(bool(lines))
        groups: dict[tuple[Any, str], list[dict[str, Any]]] = {}
        for item in lines:
            groups.setdefault((item["selected_supplier_id"], item["currency"]), []).append(item)
        orders = []
        for (supplier, currency), items in groups.items():
            identifier = uuid4()
            order = await self.repo.insert(
                "purchase_orders",
                {
                    "purchase_order_id": identifier,
                    "po_number": "PO-" + str(identifier),
                    "request_id": request["request_id"],
                    "proposal_id": proposal["proposal_id"],
                    "supplier_id": supplier,
                    "order_date": now(),
                    "status": "issued",
                    "currency": currency,
                    "total_amount": amount(
                        sum(
                            (
                                amount(item["quantity"] * item["unit_price_snapshot"])
                                for item in items
                            ),
                            Decimal(0),
                        )
                    ),
                    "pdf_file_id": None,
                    "created_by": self.policy.user["user_id"],
                    "created_at": now(),
                    "updated_at": now(),
                },
            )
            for index, item in enumerate(items, 1):
                line = await self.repo.get("purchase_request_items", item["request_item_id"])
                product = await self.repo.get("products", line["product_id"])
                item_id = uuid4()
                await self.repo.insert(
                    "purchase_order_items",
                    {
                        "po_item_id": item_id,
                        "purchase_order_id": identifier,
                        "proposal_item_id": item["proposal_item_id"],
                        "proposal_id": proposal["proposal_id"],
                        "request_id": request["request_id"],
                        "request_item_id": line["request_item_id"],
                        "line_number": index,
                        "product_id": product["product_id"],
                        "product_category_snapshot": product["product_category"],
                        "ordered_quantity": item["quantity"],
                        "unit": product["unit"],
                        "unit_price": item["unit_price_snapshot"],
                        "line_total": amount(item["quantity"] * item["unit_price_snapshot"]),
                        "item_status": "open",
                    },
                )
                await self.repo.insert(
                    "purchase_order_schedules",
                    {
                        "schedule_id": uuid4(),
                        "po_item_id": item_id,
                        "schedule_number": 1,
                        "scheduled_quantity": item["quantity"],
                        "requested_delivery_date": item["proposed_delivery_date"],
                        "promised_delivery_date": None,
                        "status": "open",
                        "created_at": now(),
                    },
                )
            await self.event(
                "purchase_order",
                identifier,
                "purchase_order.issued",
                {
                    "purchase_order_id": str(identifier),
                    "supplier_id": str(supplier),
                },
            )
            orders.append(order)
        await self.repo.update(
            "purchase_requests", request["request_id"], {"status": "ordered", "updated_at": now()}
        )
        return {"purchase_orders": await self.repo.aggregates("purchase_orders", orders)}

    async def receipt(self, order: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        await self.policy.request(order["request_id"], lock=True, roles={"WAREHOUSE"})
        order = await self.repo.get("purchase_orders", order["purchase_order_id"], lock=True)
        require(
            order["status"] in {"issued", "waiting_confirmation", "confirmed", "partially_received"}
        )
        schedule = await self.repo.get("purchase_order_schedules", body["schedule_id"], lock=True)
        item = await self.repo.get("purchase_order_items", schedule["po_item_id"])
        require(item["purchase_order_id"] == order["purchase_order_id"], ErrorCode.NOT_FOUND)
        require(schedule["status"] != "cancelled" and item["item_status"] != "cancelled")
        receipts = await self.repo.rows(
            "goods_receipts", table("goods_receipts").c.schedule_id == schedule["schedule_id"]
        )
        total = sum((r["received_quantity"] for r in receipts), Decimal(0)) + Decimal(
            body["received_quantity"]
        )
        require(total <= schedule["scheduled_quantity"])
        require(
            date.fromisoformat(body["actual_delivery_date"])
            <= now().astimezone(BUSINESS_TIMEZONE).date(),
            ErrorCode.VALIDATION_ERROR,
        )
        receipt = await self.repo.insert(
            "goods_receipts",
            {
                "receipt_id": uuid4(),
                **body,
                "notes": body.get("notes"),
                "received_at": now(),
                "recorded_by": self.policy.user["user_id"],
            },
        )
        await self.repo.update(
            "purchase_order_schedules",
            schedule["schedule_id"],
            {
                "status": "completed"
                if total == schedule["scheduled_quantity"]
                else "partially_received",
            },
        )
        schedules = await self.repo.rows(
            "purchase_order_schedules",
            table("purchase_order_schedules").c.po_item_id == item["po_item_id"],
        )
        completed = all(s["status"] == "completed" for s in schedules)
        await self.repo.update(
            "purchase_order_items",
            item["po_item_id"],
            {"item_status": "completed" if completed else "partially_received"},
        )
        items = await self.repo.rows(
            "purchase_order_items",
            table("purchase_order_items").c.purchase_order_id == order["purchase_order_id"],
        )
        await self.repo.update(
            "purchase_orders",
            order["purchase_order_id"],
            {
                "status": "delivered"
                if all(i["item_status"] == "completed" for i in items)
                else "partially_received",
                "updated_at": now(),
            },
        )
        await self.event(
            "goods_receipt",
            receipt["receipt_id"],
            "goods_receipt.recorded",
            {"receipt_id": str(receipt["receipt_id"]), "schedule_id": str(schedule["schedule_id"])},
        )
        return dto("goods_receipts", receipt)

    async def confirmation(
        self,
        op: str,
        identifier: str,
        body: dict[str, Any],
        secret: str,
    ) -> dict[str, Any]:
        token_hash = hashlib.sha256(secret.encode()).hexdigest()
        tokens = table("confirmation_tokens")
        # Lock order before token for consistent ordering with other order commands.
        order = await self.repo.get(
            "purchase_orders", identifier, lock=op != "previewSupplierConfirmation"
        )
        token = await self.repo.one(
            "confirmation_tokens",
            tokens.c.token_hash == token_hash,
            lock=op != "previewSupplierConfirmation",
        )
        require(
            token["consumed_at"] is None and token["revoked_at"] is None, ErrorCode.TOKEN_INVALID
        )
        require(token["expires_at"] > now(), ErrorCode.TOKEN_EXPIRED)
        require(token["purchase_order_id"] == order["purchase_order_id"], ErrorCode.TOKEN_INVALID)
        require(order["status"] in {"issued", "waiting_confirmation"})
        if op == "previewSupplierConfirmation":
            result = await self.repo.aggregate("purchase_orders", order)
            return {
                k: result[k]
                for k in (
                    "purchase_order_id",
                    "po_number",
                    "status",
                    "currency",
                    "total_amount",
                    "items",
                    "schedules",
                )
            } | {"can_decide": True}
        require(
            body["decision"] == "confirmed" or not body.get("items"), ErrorCode.VALIDATION_ERROR
        )
        items = body.get("items", [])
        require(len({i["schedule_id"] for i in items}) == len(items), ErrorCode.VALIDATION_ERROR)
        confirmation = await self.repo.insert(
            "supplier_confirmations",
            {
                "confirmation_id": uuid4(),
                "purchase_order_id": order["purchase_order_id"],
                "token_id": token["token_id"],
                "decision": body["decision"],
                "reason": body.get("reason"),
                "responded_at": now(),
            },
        )
        for item in items:
            await self.confirmation_item(confirmation, item, 0)
        await self.repo.update("confirmation_tokens", token["token_id"], {"consumed_at": now()})
        await self.repo.update(
            "purchase_orders",
            order["purchase_order_id"],
            {"status": body["decision"], "updated_at": now()},
        )
        return await self.repo.aggregate("supplier_confirmations", confirmation)

    async def confirmation_item(
        self,
        confirmation: dict[str, Any],
        body: dict[str, Any],
        revision: int,
    ) -> dict[str, Any]:
        require(confirmation["decision"] == "confirmed")
        order = await self.repo.get("purchase_orders", confirmation["purchase_order_id"], lock=True)
        require(order["status"] not in {"cancelled", "rejected", "delivered"})
        schedule = await self.repo.get("purchase_order_schedules", body["schedule_id"], lock=True)
        item = await self.repo.get("purchase_order_items", schedule["po_item_id"])
        require(item["purchase_order_id"] == confirmation["purchase_order_id"], ErrorCode.NOT_FOUND)
        require(schedule["status"] != "cancelled")
        if body["confirmed_quantity"] is not None:
            require(Decimal(body["confirmed_quantity"]) <= schedule["scheduled_quantity"])
        versions = await self.repo.rows(
            "supplier_confirmation_items",
            table("supplier_confirmation_items").c.confirmation_id
            == confirmation["confirmation_id"],
            table("supplier_confirmation_items").c.schedule_id == schedule["schedule_id"],
        )
        latest = max((v["revision_no"] for v in versions), default=0)
        require(latest == revision, ErrorCode.VERSION_CONFLICT)
        result = await self.repo.insert(
            "supplier_confirmation_items",
            {
                "confirmation_item_id": uuid4(),
                "confirmation_id": confirmation["confirmation_id"],
                **body,
                "revision_no": latest + 1,
                "recorded_at": now(),
                "notes": body.get("notes"),
            },
        )
        await self.repo.update(
            "purchase_order_schedules",
            schedule["schedule_id"],
            {
                "promised_delivery_date": body["promised_delivery_date"],
            },
        )
        return dto("supplier_confirmation_items", result)

    async def email(self, op: str, identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        order = await self.repo.get("purchase_orders", identifier)
        await self.policy.request(order["request_id"], lock=True)
        require(order["status"] != "cancelled")
        if op == "requestEmailRetry":
            logs = await self.repo.rows(
                "email_logs",
                table("email_logs").c.purchase_order_id == order["purchase_order_id"],
                order_by=table("email_logs").c.created_at.desc(),
                limit=1,
                lock=True,
            )
            require(bool(logs), ErrorCode.NOT_FOUND)
            log = logs[0]
            require(log["status"] == "failed")
            await self.repo.update(
                "email_logs", log["email_log_id"], {"status": "queued", "next_retry_at": now()}
            )
            event = await self.event(
                "purchase_order",
                order["purchase_order_id"],
                "email.retry_requested",
                {"email_log_id": str(log["email_log_id"]), "reason": body["reason"]},
            )
            return {
                "action_id": str(event),
                "status": "queued",
                "resource_id": str(order["purchase_order_id"]),
            }
        log = await self.repo.get("email_logs", body["email_log_id"], lock=True)
        require(log["purchase_order_id"] == order["purchase_order_id"], ErrorCode.NOT_FOUND)
        require(log["status"] == "sending")
        log = await self.repo.update(
            "email_logs",
            log["email_log_id"],
            {
                "status": body["status"],
                "provider_message_id": body.get("provider_message_id"),
                "error_message": body.get("error_message"),
                "last_attempt_at": now(),
                "sent_at": now() if body["status"] == "sent" else None,
                "next_retry_at": None,
                "attempt_count": max(1, log["attempt_count"]),
            },
        )
        return dto("email_logs", log)

    async def admin(self, op: str, path: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if op == "revokeApplicationRole":
            grant = await self.repo.get("user_roles", path["user_role_id"])
            user = await self.repo.get("users", grant["user_id"], lock=True)
            grant = await self.repo.get("user_roles", grant["user_role_id"], lock=True)
            require(grant["revoked_at"] is None)
            row = await self.repo.update(
                "user_roles",
                grant["user_role_id"],
                {
                    "revoked_at": now(),
                    "revoked_by": self.policy.user["user_id"],
                },
            )
            name = "user_roles"
        else:
            user = await self.repo.get("users", path["user_id"], lock=True)
            if body.get("department_id"):
                department = await self.repo.get("departments", body["department_id"])
                require(department["status"] == "active", ErrorCode.VALIDATION_ERROR)
            if op == "updateApplicationUser":
                require(bool(body), ErrorCode.VALIDATION_ERROR)
                require(
                    user["user_id"] != self.policy.user["user_id"]
                    or body.get("status") != "disabled"
                )
                row = await self.repo.update(
                    "users", user["user_id"], {**body, "updated_at": now()}
                )
                name = "users"
            else:
                role = await self.repo.get("roles", body["role_id"])
                code = role["role_code"]
                expected_account = "service" if code in {"AI_SERVICE", "PO_SERVICE"} else "human"
                expected_scope = "department" if code in {"APPROVER", "WAREHOUSE"} else "global"
                require(role["status"] == user["status"] == "active", ErrorCode.FORBIDDEN)
                require(
                    user["account_type"] == role["allowed_account_type"] == expected_account,
                    ErrorCode.FORBIDDEN,
                )
                require(
                    body["scope_type"] == role["scope_type"] == expected_scope
                    and (body["department_id"] is not None) == (expected_scope == "department"),
                    ErrorCode.VALIDATION_ERROR,
                )
                grants = table("user_roles")
                scope = (
                    grants.c.department_id == UUID(body["department_id"])
                    if body["department_id"]
                    else grants.c.department_id.is_(None)
                )
                require(
                    await self.repo.count(
                        "user_roles",
                        grants.c.user_id == user["user_id"],
                        grants.c.role_id == role["role_id"],
                        grants.c.revoked_at.is_(None),
                        scope,
                    )
                    == 0
                )
                row = await self.repo.insert(
                    "user_roles",
                    {
                        "user_role_id": uuid4(),
                        **body,
                        "user_id": user["user_id"],
                        "granted_by": self.policy.user["user_id"],
                        "granted_at": now(),
                        "revoked_by": None,
                        "revoked_at": None,
                    },
                )
                name = "user_roles"
        await self.event(
            "user",
            user["user_id"],
            "identity." + op,
            {
                "actor_id": str(self.policy.user["user_id"]),
                "user_id": str(user["user_id"]),
                "reason": body.get("reason"),
            },
        )
        return dto(name, row)

    async def provision_user(self, body: dict[str, Any]) -> dict[str, Any]:
        # The contract links an existing IdP identity; passwords/users are managed by Keycloak.
        from flowbridge.identity.account_repository import IdentityAccountRepository
        from flowbridge.identity.accounts import IdentityAccounts

        if self.directory is None:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        identity = await self.directory.resolve(body["identity_reference"])
        require(identity.account_type == body["account_type"], ErrorCode.FORBIDDEN)
        user, created = await IdentityAccounts(IdentityAccountRepository(self.repo.session)).link(
            identity,
            display_name=body["display_name"],
            email=body.get("email", identity.email),
            department_id=UUID(body["department_id"]) if body.get("department_id") else None,
        )
        if created:
            await self.event(
                "user",
                user["user_id"],
                "identity.user_linked",
                {
                    "user_id": str(user["user_id"]),
                    "actor_id": str(self.policy.user["user_id"]),
                },
            )
        return dto("users", user)

    async def lookup(self, body: dict[str, Any]) -> dict[str, Any]:
        name = body["resource"]
        target = table(name)
        conditions = []
        if body.get("request_id"):
            request = await self.policy.request(body["request_id"])
            if "request_id" in target.c:
                conditions.append(target.c.request_id == request["request_id"])
            elif name in {"purchase_order_schedules", "goods_receipts"}:
                items = sa.select(table("purchase_order_items").c.po_item_id).where(
                    table("purchase_order_items").c.request_id == request["request_id"]
                )
                if name == "purchase_order_schedules":
                    conditions.append(target.c.po_item_id.in_(items))
                else:
                    schedules = sa.select(table("purchase_order_schedules").c.schedule_id).where(
                        table("purchase_order_schedules").c.po_item_id.in_(items)
                    )
                    conditions.append(target.c.schedule_id.in_(schedules))
            elif name in {"suppliers", "products", "supplier_products"}:
                # Request scope alone cannot identify a supplier; require an explicit filter.
                require(
                    bool(body.get("supplier_id") or body.get("product_id")),
                    ErrorCode.VALIDATION_ERROR,
                )
        for field in ("supplier_id", "product_id"):
            if body.get(field):
                if field not in target.c:
                    raise CoreError(ErrorCode.VALIDATION_ERROR)
                conditions.append(target.c[field] == UUID(body[field]))
        if body.get("feature_asof"):
            timestamps = {
                "goods_receipts": "received_at",
                "risk_predictions": "predicted_at",
                "risk_analysis_reports": "created_at",
            }
            require(name in timestamps, ErrorCode.UPSTREAM_UNAVAILABLE)
            cutoff = datetime.fromisoformat(body["feature_asof"].replace("Z", "+00:00"))
            conditions.append(target.c[timestamps[name]] < cutoff)
            if name == "goods_receipts":
                conditions.append(
                    target.c.actual_delivery_date <= cutoff.astimezone(BUSINESS_TIMEZONE).date()
                )
        result = await self.repo.page(
            name, conditions, body.get("page", 1), body.get("page_size", 20)
        )
        # Lookup rows follow table DTOs; aggregate children belong to dedicated API responses.
        result["items"] = [
            {column.name: row[column.name] for column in target.c} for row in result["items"]
        ]
        return {"resource": name, **result, "warnings": []}
