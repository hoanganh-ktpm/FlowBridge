# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa

from flowbridge.identity.authentication import VerifiedIdentity
from flowbridge.settings import Settings
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.database_service import BUSINESS_TIMEZONE
from flowbridge.supplier_assistant.policy import SupplierPolicy, require
from flowbridge.supplier_assistant.repository import json_value, table

from .features import FeatureBuilder, OrderFact, history_samples
from .history import HistoryRequest, HistoryResponse, history_reference, history_row
from .model import TabICLRuntime
from .repository import PredictionRepository
from .schemas import (
    FEATURE_VERSION,
    PREPROCESSING_VERSION,
    Candidate,
    FeatureSnapshot,
    ItemRisk,
    OrderRisk,
    RankRequest,
    RankResponse,
    RiskResult,
    ScenarioRequest,
    ScenarioResponse,
    Trace,
)


def aggregate_order(order_id: UUID, items: list[ItemRisk]) -> OrderRisk:
    valid = [r for r in items if r.result.status == "success" and r.result.risk_score is not None]
    highest = min(valid, key=lambda r: (-r.result.risk_score, str(r.po_item_id))) if valid else None
    complete = len(valid) == len(items)
    currencies = {r.currency for r in items}
    weights_known = bool(items) and all(r.remaining_value is not None for r in items)
    weights_known &= len(currencies) == 1 and None not in currencies
    total = sum((r.remaining_value for r in items), Decimal(0)) if weights_known else Decimal(0)
    scored_value = (
        sum((r.remaining_value for r in valid), Decimal(0)) if weights_known else Decimal(0)
    )
    observed = (
        float(
            sum((Decimal(str(r.result.risk_score)) * r.remaining_value for r in valid), Decimal(0))
            / scored_value
        )
        if scored_value > 0
        else None
    )
    coverage = float(scored_value / total) if total > 0 else None
    status = (
        "no_open_items"
        if not items
        else ("success" if complete and total > 0 else "partial" if valid else "insufficient_data")
    )
    return OrderRisk(
        purchase_order_id=order_id,
        status=status,
        is_complete=complete,
        representative_risk_score=observed if complete and coverage is not None else None,
        aggregation_definition="Điểm trung bình có trọng số theo giá trị hàng còn chưa nhận "
        "(lượng còn thiếu × đơn giá hợp đồng), cùng tiền tệ. Chỉ số ưu tiên theo dõi, "
        "không phải xác suất ít nhất một món trễ. Giữ riêng điểm MAX của dòng và cảnh báo "
        "HIGH_RISK_ITEM_PRESENT. Thiếu điểm hoặc trọng số: điểm toàn đơn là null; "
        "observed_value_weighted_score chỉ mô tả phần đã chấm, xem scored_value_coverage.",
        observed_value_weighted_score=observed,
        scored_value_coverage=coverage,
        highest_item_risk_score=highest.result.risk_score if highest else None,
        high_risk_item_count=sum(r.result.risk_score > r.result.risk_threshold for r in valid),
        unscored_item_count=len(items) - len(valid),
        highest_risk_item_id=highest.po_item_id if highest else None,
        highest_risk_item_deadline=highest.requested_delivery_date if highest else None,
        items=items,
        warnings=([] if complete else ["ORDER_RISK_INCOMPLETE"])
        + (["ORDER_VALUE_WEIGHTS_UNAVAILABLE"] if items and total <= 0 else [])
        + (
            ["HIGH_RISK_ITEM_PRESENT"]
            if any(r.result.risk_score > r.result.risk_threshold for r in valid)
            else []
        ),
    )


def item_exposure(fact: OrderFact, asof: datetime) -> dict:
    """Use outstanding contractual value in one PO currency; never sum mixed units."""
    received = sum((r["received_quantity"] for r in fact.known_receipts(asof)), Decimal(0))
    remaining = max(Decimal(0), fact.item["ordered_quantity"] - received)
    price = fact.item.get("unit_price")
    currency = fact.order.get("currency")
    return {
        "remaining_value": remaining * price if price is not None else None,
        "currency": currency,
    }


class PredictionService:
    def __init__(
        self,
        repository: PredictionRepository,
        runtime: TabICLRuntime,
        settings: Settings,
    ) -> None:
        self.repo, self.runtime, self.settings = repository, runtime, settings
        self.policy = SupplierPolicy(repository.core)

    async def authorize(self, identity: VerifiedIdentity, *, write: bool = False) -> None:
        await self.policy.authenticate(identity)
        if write:
            self.policy.authorize("AI_SERVICE")
        elif self.policy.has("AI_SERVICE"):
            self.policy.authorize("AI_SERVICE")
        else:
            self.policy.authorize("EMPLOYEE", read=True)

    def builder(self) -> FeatureBuilder:
        return FeatureBuilder(
            history_days=self.settings.ml_history_days,
            quantity_references=self.runtime.context.quantity_references,
            receipt_coverage_confirmed=self.settings.ml_receipt_coverage_confirmed,
        )

    def trace(self, asof: datetime, prediction_id: UUID | None = None) -> Trace:
        return Trace(
            feature_asof=asof,
            model_version=self.settings.ml_model_version,
            context_dataset_version=(
                self.runtime.context.version if self.runtime.context else "unavailable"
            ),
            prediction_id=prediction_id,
            persisted=prediction_id is not None,
        )

    def result(self, score: float, snapshot: FeatureSnapshot) -> RiskResult:
        score = float(Decimal(str(score)).quantize(Decimal("0.000001")))
        warnings = list(snapshot.metadata["warnings"])
        if self.runtime.context.source_kind == "synthetic":
            warnings.append("SYNTHETIC_CONTEXT_NOT_VALIDATED_FOR_REAL_PURCHASES")
        return RiskResult(
            status="success",
            risk_score=score,
            risk_level="high" if score > self.settings.ml_risk_threshold else "low",
            risk_threshold=self.settings.ml_risk_threshold,
            hist_sample_count=snapshot.x.hist_sample_count,
            history_reference=snapshot.metadata.get("history_reference"),
            warnings=warnings,
        )

    def insufficient(
        self, error: CoreError | None = None, *, reason: str = "MISSING_DATA"
    ) -> RiskResult:
        return RiskResult(
            status="insufficient_data",
            risk_threshold=self.settings.ml_risk_threshold,
            error_code=reason,
            missing_fields=[d.field for d in error.details] if error else [],
        )

    async def history(self, body: HistoryRequest) -> HistoryResponse:
        # Detailed model-feature evidence is a company-wide purchase-history permission.
        # Plain employees receive neither another user's orders nor a fake empty sample.
        self.policy.authorize("PROCUREMENT_VIEWER")
        reference = body.reference
        now = datetime.now(UTC)
        asof = reference.feature_asof if reference else body.feature_asof or now
        require(asof.tzinfo is not None and asof <= now, ErrorCode.VALIDATION_ERROR)
        if reference:
            require(
                reference.product_id == body.product_id
                and reference.supplier_id == body.supplier_id,
                ErrorCode.VALIDATION_ERROR,
            )
            require(
                body.feature_asof is None or body.feature_asof == asof, ErrorCode.VALIDATION_ERROR
            )
        product = await self.repo.core.get("products", body.product_id)
        await self.repo.core.get("suppliers", body.supplier_id)
        unit = reference.unit if reference else product["unit"]
        category = reference.product_category if reference else product["product_category"]
        days = reference.history_window_days if reference else self.settings.ml_history_days
        exclude = reference.exclude_item_id if reference else None
        facts = await self.repo.facts(
            {**product, "product_category": category}, [body.supplier_id], asof, days
        )
        group = history_samples(
            facts,
            product_id=body.product_id,
            supplier_id=body.supplier_id,
            unit=unit,
            category=category,
            asof=asof,
            history_days=days,
            exclude_item_id=exclude,
        )
        actual = history_reference(
            group,
            product_id=body.product_id,
            supplier_id=body.supplier_id,
            unit=unit,
            category=category,
            asof=asof,
            history_days=days,
            exclude_item_id=exclude,
        )
        matches = actual == reference if reference else None
        count_matches = (
            len(group) == body.expected_count if body.expected_count is not None else None
        )
        warnings = ["CURRENT_STATUS_NOT_HISTORIZED", "PRODUCT_NAME_IS_CURRENT_CATALOG_LABEL"]
        if reference is None:
            warnings.append("HISTORY_RECONSTRUCTED_WITHOUT_ORIGINAL_REFERENCE")
        if reference is None and body.feature_asof is None:
            warnings.append("CURRENT_HISTORY_NOT_PREVIOUS_PREDICTION")
        if matches is False or count_matches is False:
            warnings.append("HISTORY_CHANGED_OR_REFERENCE_MISMATCH")
        more = body.page * body.page_size < len(group)
        rows = group[(body.page - 1) * body.page_size : body.page * body.page_size]
        return HistoryResponse(
            status="changed"
            if matches is False or count_matches is False
            else "partial"
            if more
            else "success"
            if group
            else "not_found",
            reference=actual,
            reference_matches=matches,
            expected_count_matches=count_matches,
            items=[history_row(f, outcome, product["product_name"]) for f, outcome in rows],
            total_matches=len(group),
            page=body.page,
            page_size=body.page_size,
            next_page=body.page + 1 if more else None,
            warnings=warnings,
        )

    @staticmethod
    def eligibility(supplier: dict, offer: dict, quantity: Decimal) -> list[str]:
        reasons = []
        if supplier["status"] != "active":
            reasons.append("SUPPLIER_INACTIVE")
        if not offer["is_available"]:
            reasons.append("UNAVAILABLE")
        if offer["min_order_qty"] is not None and quantity < offer["min_order_qty"]:
            reasons.append("MOQ")
        return reasons

    async def product(self, product_id: UUID) -> dict:
        product = await self.repo.core.get("products", product_id)
        require(product["status"] == "active", ErrorCode.INVALID_STATE)
        return product

    def valid_deadline(self, body: ScenarioRequest | RankRequest, asof: datetime) -> None:
        require(
            body.requested_delivery_date >= asof.astimezone(BUSINESS_TIMEZONE).date(),
            ErrorCode.VALIDATION_ERROR,
        )

    async def scenario(self, body: ScenarioRequest) -> ScenarioResponse:
        asof = datetime.now(UTC)
        self.valid_deadline(body, asof)
        product = await self.product(body.product_id)
        supplier = await self.repo.core.get("suppliers", body.supplier_id)
        offers = await self.repo.core.rows(
            "supplier_products",
            table("supplier_products").c.product_id == body.product_id,
            table("supplier_products").c.supplier_id == body.supplier_id,
        )
        require(bool(offers), ErrorCode.NOT_FOUND)
        require(not self.eligibility(supplier, offers[0], body.quantity), ErrorCode.INVALID_STATE)
        self.runtime.ensure_ready(asof)
        facts = await self.repo.facts(
            product, [body.supplier_id], asof, self.settings.ml_history_days
        )
        try:
            snapshot = self.builder().build(
                product=product,
                supplier_id=body.supplier_id,
                quantity=body.quantity,
                unit=product["unit"],
                deadline=body.requested_delivery_date,
                asof=asof,
                facts=facts,
            )
        except CoreError as exc:
            if exc.code != ErrorCode.INSUFFICIENT_DATA:
                raise
            return ScenarioResponse(
                scenario=body, result=self.insufficient(exc), trace=self.trace(asof)
            )
        score = (await self.runtime.predict([snapshot]))[0]
        return ScenarioResponse(
            scenario=body, result=self.result(score, snapshot), trace=self.trace(asof)
        )

    async def rank(self, body: RankRequest) -> RankResponse:
        asof = datetime.now(UTC)
        self.valid_deadline(body, asof)
        product = await self.product(body.product_id)
        offers = await self.repo.core.rows(
            "supplier_products",
            table("supplier_products").c.product_id == body.product_id,
            limit=self.settings.ml_max_candidates + 1,
        )
        require(len(offers) <= self.settings.ml_max_candidates, ErrorCode.VALIDATION_ERROR)
        suppliers = {
            s["supplier_id"]: s
            for s in await self.repo.core.rows(
                "suppliers",
                table("suppliers").c.supplier_id.in_([o["supplier_id"] for o in offers]),
            )
        }
        eligible, excluded = [], []
        for offer in offers:
            supplier = suppliers[offer["supplier_id"]]
            reasons = self.eligibility(supplier, offer, body.quantity)
            if body.currency and offer["currency"] != body.currency:
                reasons.append("CURRENCY")
            if (
                body.max_unit_price is not None
                and offer["current_unit_price"] > body.max_unit_price
            ):
                reasons.append("MAX_UNIT_PRICE")
            if reasons:
                excluded.append({"supplier_id": str(offer["supplier_id"]), "reason_codes": reasons})
            else:
                eligible.append(offer)
        if not eligible:
            return RankResponse(
                status="no_eligible_suppliers",
                trace=self.trace(asof),
                candidates=[],
                excluded=excluded,
                considered_count=len(offers),
            )
        self.runtime.ensure_ready(asof)
        facts = (
            await self.repo.facts(
                product,
                [o["supplier_id"] for o in eligible],
                asof,
                self.settings.ml_history_days,
            )
            if eligible
            else []
        )
        pending, unscored = [], []
        for offer in eligible:
            candidate = dict(
                supplier_id=offer["supplier_id"],
                supplier_name=suppliers[offer["supplier_id"]]["supplier_name"],
                unit_price=f"{offer['current_unit_price']:.4f}",
                currency=offer["currency"],
                quoted_lead_days=offer["quoted_lead_days"],
            )
            try:
                snapshot = self.builder().build(
                    product=product,
                    supplier_id=offer["supplier_id"],
                    quantity=body.quantity,
                    unit=product["unit"],
                    deadline=body.requested_delivery_date,
                    asof=asof,
                    facts=facts,
                )
                pending.append((candidate, snapshot))
            except CoreError as exc:
                if exc.code != ErrorCode.INSUFFICIENT_DATA:
                    raise
                unscored.append(Candidate(**candidate, result=self.insufficient(exc)))
        scores = await self.runtime.predict([s for _, s in pending])
        ranked = [
            Candidate(**c, result=self.result(score, snapshot), reason_codes=["ML_RISK_ASCENDING"])
            for (c, snapshot), score in zip(pending, scores, strict=True)
        ]
        ranked.sort(key=lambda c: (c.result.risk_score, str(c.supplier_id)))
        ranked = [c.model_copy(update={"rank": i + 1}) for i, c in enumerate(ranked)]
        warnings = ["AVAILABILITY_IS_NOT_A_CAPACITY_COMMITMENT"]
        if ranked and all(c.result.risk_level == "high" for c in ranked):
            warnings.append("ALL_SCORED_SUPPLIERS_HIGH_RISK")
        return RankResponse(
            status="no_eligible_suppliers"
            if not eligible
            else "partial"
            if unscored and ranked
            else "insufficient_data"
            if unscored
            else "success",
            trace=self.trace(asof),
            candidates=ranked[: body.top_k],
            unscored=unscored,
            excluded=excluded,
            considered_count=len(offers),
            warnings=warnings,
        )

    async def order(self, order_id: UUID, *, lock: bool = False) -> dict:
        order = await self.repo.core.get("purchase_orders", order_id, lock=lock)
        await self.policy.request(order["request_id"])
        return order

    @staticmethod
    def open_items(facts: list[OrderFact], asof: datetime) -> list[OrderFact]:
        return [
            f
            for f in facts
            if f.item["item_status"] not in {"completed", "cancelled"}
            and f.order["status"] not in {"delivered", "cancelled", "rejected"}
            and f.outcome(asof) is None
        ]

    async def predict_order(self, order_id: UUID, key: str) -> OrderRisk:
        # The caller owns one transaction: predictions and retry response commit together.
        scope = str(self.policy.user["user_id"]) + ":ml.predict-order:" + str(order_id)
        lock_id = int.from_bytes(
            hashlib.sha256((scope + ":" + key).encode()).digest()[:8], "big", signed=True
        )
        await self.repo.core.session.execute(sa.select(sa.func.pg_advisory_xact_lock(lock_id)))
        order = await self.order(order_id, lock=True)
        cached = await self.repo.core.rows(
            "api_idempotency",
            table("api_idempotency").c.scope == scope,
            table("api_idempotency").c.key == key,
        )
        if cached:
            return OrderRisk.model_validate(cached[0]["response"])
        asof = datetime.now(UTC)
        require(order["order_date"] <= asof, ErrorCode.INVALID_STATE)
        facts = await self.repo.order_facts(order_id, asof)
        active = self.open_items(facts, asof)
        require(len(active) <= self.settings.ml_max_candidates, ErrorCode.VALIDATION_ERROR)
        if active:
            self.runtime.ensure_ready(asof)
        request = await self.policy.request(order["request_id"])
        pending, items = [], []
        history_cache: dict[UUID, list[OrderFact]] = {}
        for fact in active:
            line = fact.item
            require(
                str(line["po_item_id"]) not in self.runtime.context.group_ids,
                ErrorCode.INVALID_STATE,
            )
            product = {
                "product_id": line["product_id"],
                "product_category": line["product_category_snapshot"],
            }
            if line["product_id"] not in history_cache:
                history_cache[line["product_id"]] = await self.repo.facts(
                    product,
                    [order["supplier_id"]],
                    asof,
                    self.settings.ml_history_days,
                )
            try:
                deadline = fact.deadline()
                snapshot = self.builder().build(
                    product=product,
                    supplier_id=order["supplier_id"],
                    quantity=line["ordered_quantity"],
                    unit=line["unit"],
                    deadline=deadline,
                    asof=asof,
                    facts=history_cache[line["product_id"]],
                    current=fact,
                )
                pending.append((fact, snapshot))
            except CoreError as exc:
                if exc.code != ErrorCode.INSUFFICIENT_DATA:
                    raise
                # Invalid schedules cannot provide an honest item deadline; fail the request.
                deadline = fact.deadline()
                items.append(
                    ItemRisk(
                        po_item_id=line["po_item_id"],
                        product_id=line["product_id"],
                        requested_delivery_date=deadline,
                        result=self.insufficient(exc),
                    )
                )
        scores = await self.runtime.predict([s for _, s in pending])
        for (fact, snapshot), score in zip(pending, scores, strict=True):
            result = self.result(score, snapshot)
            prediction_id = uuid4()
            prediction_key = hashlib.sha256(
                (scope + ":" + key + ":" + str(fact.item["po_item_id"])).encode()
            ).hexdigest()
            await self.repo.core.insert(
                "risk_predictions",
                {
                    "prediction_id": prediction_id,
                    "task_id": None,
                    "request_id": order["request_id"],
                    "request_item_id": fact.item["request_item_id"],
                    "input_revision": request["input_revision"],
                    "source_type": "po_monitoring",
                    "prediction_stage": "PO_MONITORING",
                    "po_item_id": fact.item["po_item_id"],
                    "idempotency_key": "ml-po:" + prediction_key,
                    "supplier_id": order["supplier_id"],
                    "product_id": snapshot.product_id,
                    "quantity": snapshot.quantity,
                    "requested_delivery_date": snapshot.requested_delivery_date,
                    "feature_asof": asof,
                    "requested_lead_days": int(snapshot.x.days_to_deadline),
                    "feature_schema_version": FEATURE_VERSION,
                    "preprocessing_version": PREPROCESSING_VERSION,
                    "model_name": "TabICLv2",
                    "model_version": self.settings.ml_model_version,
                    "context_dataset_version": self.runtime.context.version,
                    "context_metadata": self.runtime.metadata(),
                    "risk_score": result.risk_score,
                    "risk_threshold": result.risk_threshold,
                    "risk_level": result.risk_level,
                    "feature_snapshot": snapshot.model_dump(mode="json"),
                    "status": "success",
                    "error_code": None,
                    "error_message": None,
                    "predicted_at": datetime.now(UTC),
                },
            )
            items.append(
                ItemRisk(
                    po_item_id=fact.item["po_item_id"],
                    product_id=snapshot.product_id,
                    requested_delivery_date=snapshot.requested_delivery_date,
                    result=result,
                    trace=self.trace(asof, prediction_id),
                )
            )
        exposures = {f.item["po_item_id"]: item_exposure(f, asof) for f in active}
        items = [item.model_copy(update=exposures[item.po_item_id]) for item in items]
        response = aggregate_order(order_id, sorted(items, key=lambda r: str(r.po_item_id)))
        await self.repo.core.insert(
            "api_idempotency",
            {
                "scope": scope,
                "key": key,
                "request_hash": hashlib.sha256(str(order_id).encode()).hexdigest(),
                "response": response.model_dump(mode="json"),
                "created_at": datetime.now(UTC),
            },
        )
        return response

    def saved_item(self, row: dict) -> ItemRisk:
        snapshot = row["feature_snapshot"]
        result = RiskResult(
            status="success" if row["status"] == "success" else "insufficient_data",
            risk_score=float(row["risk_score"]) if row["risk_score"] is not None else None,
            risk_level=row["risk_level"],
            risk_threshold=float(
                row["risk_threshold"]
                if row["risk_threshold"] is not None
                else self.settings.ml_risk_threshold
            ),
            hist_sample_count=snapshot["x"].get("hist_sample_count"),
            history_reference=snapshot.get("metadata", {}).get("history_reference"),
            error_code=row["error_code"],
            warnings=snapshot.get("metadata", {}).get("warnings", []),
        )
        if (row.get("context_metadata") or {}).get("source_kind") == "synthetic":
            result = result.model_copy(update={"warnings": result.warnings + ["SYNTHETIC_CONTEXT"]})
        return ItemRisk(
            po_item_id=row["po_item_id"],
            product_id=row["product_id"],
            requested_delivery_date=row["requested_delivery_date"],
            result=result,
            trace=Trace(
                feature_asof=row["feature_asof"],
                model_version=row["model_version"] or "unavailable",
                context_dataset_version=row["context_dataset_version"] or "unavailable",
                prediction_id=row["prediction_id"],
                persisted=True,
                feature_schema_version=row["feature_schema_version"],
                preprocessing_version=row["preprocessing_version"],
            ),
        )

    async def current_order_risk(self, order_id: UUID) -> OrderRisk:
        await self.order(order_id)
        asof = datetime.now(UTC)
        active = self.open_items(await self.repo.order_facts(order_id, asof), asof)
        require(len(active) <= self.settings.ml_max_candidates, ErrorCode.VALIDATION_ERROR)
        items = []
        for fact in active:
            rows = await self.repo.core.rows(
                "risk_predictions",
                table("risk_predictions").c.po_item_id == fact.item["po_item_id"],
                table("risk_predictions").c.feature_schema_version == FEATURE_VERSION,
                table("risk_predictions").c.preprocessing_version == PREPROCESSING_VERSION,
                table("risk_predictions").c.model_version == self.settings.ml_model_version,
                table("risk_predictions").c.feature_asof <= asof,
                order_by=(table("risk_predictions").c.feature_asof.desc()),
                limit=1,
            )
            if rows:
                item = self.saved_item(rows[0])
                fresh = (asof - rows[0]["feature_asof"]).total_seconds() <= (
                    self.settings.ml_prediction_max_age_seconds
                )
                context_matches = self.runtime.context is not None and (
                    rows[0]["context_dataset_version"] == self.runtime.context.version
                    and (rows[0].get("context_metadata") or {}).get("context_sha256")
                    == self.runtime.context.sha256
                    and (rows[0].get("context_metadata") or {}).get("checkpoint_sha256")
                    == self.runtime.checkpoint_sha256
                    and (rows[0].get("context_metadata") or {}).get("feature_policy")
                    == {
                        "history_days": self.settings.ml_history_days,
                        "receipt_coverage_confirmed": self.settings.ml_receipt_coverage_confirmed,
                    }
                )
                if not fresh or not context_matches:
                    item = item.model_copy(
                        update={
                            "result": self.insufficient(
                                reason="STALE_PREDICTION"
                                if not fresh
                                else "CONTEXT_VERSION_UNAVAILABLE"
                            )
                        }
                    )
            else:
                item = ItemRisk(
                    po_item_id=fact.item["po_item_id"],
                    product_id=fact.item["product_id"],
                    requested_delivery_date=fact.deadline(),
                    result=self.insufficient(reason="NO_PREDICTION"),
                )
            items.append(item.model_copy(update=item_exposure(fact, asof)))
        return aggregate_order(order_id, items)

    async def prediction(self, prediction_id: UUID) -> dict:
        row = await self.repo.core.get("risk_predictions", prediction_id)
        require(row["request_id"] is not None, ErrorCode.NOT_FOUND)
        await self.policy.request(row["request_id"])
        # Persisted provenance only; neither context data nor arbitrary user features are exposed.
        return json_value(
            {
                key: row[key]
                for key in (
                    "prediction_id",
                    "request_id",
                    "request_item_id",
                    "po_item_id",
                    "supplier_id",
                    "product_id",
                    "quantity",
                    "requested_delivery_date",
                    "feature_asof",
                    "predicted_at",
                    "status",
                    "risk_score",
                    "risk_threshold",
                    "risk_level",
                    "feature_schema_version",
                    "preprocessing_version",
                    "model_version",
                    "context_dataset_version",
                    "error_code",
                )
            }
        ) | {"score_is_calibrated": False, "calibrated_late_probability": None}
