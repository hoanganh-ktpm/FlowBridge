# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError

from .analytics import analyze, local_day, numeric, prepare
from .changes import change_manifest
from .connector import CoreConnector
from .decision_api import install_decision_routes
from .decision_freshness import digest, input_digest, ordered
from .decision_impact import dependency_evidence
from .decision_workflow import DecisionWorkflowClient
from .forecasting import Forecaster
from .handoff_plan import assess_slot
from .handoff_readiness import preparation_policy
from .operations import planning_report
from .planning import SnapshotCache
from .planning_options import planning_options
from .portfolio import carrier_summary, supply_reference
from .records import RecordsRequest, records_page
from .reports import overview, recipe
from .repository import PostgresRepository
from .request_lifetime import bounded_overview
from .schemas import (
    AnalysisConfig,
    AnalyticsResult,
    CapacityRequest,
    ForecastRequest,
    ForecastResult,
    OverviewRequest,
    OverviewResult,
    PlanningOptionsRequest,
    PlanningRequest,
    ProductRef,
    ProfileRequest,
    ScenarioResult,
    SimulationRequest,
)
from .simulation import sensitivity, simulate
from .source_roles import configured_source_names
from .warehouse_overview import WarehouseOverviewRequest, warehouse_overview

PREFIX = "/api/v1/data-workspace"


def forecast_config_at_request(body, instant):
    if body.start_date is not None:
        today = instant.astimezone(ZoneInfo(body.config.timezone)).date()
        if (
            body.config.frequency != "D"
            or body.start_date < today
            or body.end_date > today + timedelta(days=89)
        ):
            raise HTTPException(
                422,
                detail={
                    "code": "FORECAST_WINDOW_INVALID",
                    "message": "Chọn ngày bắt đầu từ hôm nay và ngày kết thúc trong 90 ngày tới.",
                },
            )
        try:
            return AnalysisConfig.model_validate(
                {
                    **body.config.model_dump(),
                    "forecast_start": body.start_date,
                    "forecast_horizon": (body.end_date - body.start_date).days + 1,
                }
            )
        except ValueError:
            raise HTTPException(
                422,
                detail={
                    "code": "STALE_HISTORY",
                    "message": "Lịch sử quá cũ cho khoảng dự báo đã chọn. Hãy cập nhật dữ liệu.",
                },
            ) from None
    if not body.start_from_request_date:
        return body.config
    if body.config.frequency != "D":
        raise HTTPException(422, "Operational request-date forecasts require daily frequency")
    today = instant.astimezone(ZoneInfo(body.config.timezone)).date()
    try:
        return AnalysisConfig.model_validate({**body.config.model_dump(), "forecast_start": today})
    except ValueError:
        raise HTTPException(
            422,
            detail={
                "code": "STALE_HISTORY",
                "message": "Lịch sử quá cũ hoặc chưa hoàn tất. Hãy áp dụng lại báo cáo.",
            },
        ) from None


def configured_columns(config):
    return [
        config.time_column,
        config.entity_column,
        config.target_column,
        *([config.unit_column] if config.unit_column else []),
        *config.filters.keys(),
    ]


def create_app(
    connector=None,
    repository=None,
    forecaster=None,
    *,
    case_repository=None,
    decision_bindings=None,
    decision_workflow=None,
):
    http = None
    if connector is None:
        http = httpx.AsyncClient()
        connector = CoreConnector(
            http,
            os.environ["FLOWBRIDGE_WORKSPACE_CORE_URL"],
            UUID(os.environ["FLOWBRIDGE_WORKSPACE_ORG_ID"]),
            scan_pages=int(os.environ.get("FLOWBRIDGE_WORKSPACE_SCAN_PAGES", "1")),
        )
    if repository is None:
        repository = PostgresRepository(os.environ["FLOWBRIDGE_WORKSPACE_DATABASE_URL"])
    forecaster = forecaster or Forecaster(
        os.environ.get("FLOWBRIDGE_WORKSPACE_CHRONOS_PATH"),
        os.environ.get("FLOWBRIDGE_WORKSPACE_CHRONOS_REVISION"),
    )
    snapshots = SnapshotCache()
    source_names = configured_source_names()
    authorization_slots = asyncio.Semaphore(4)
    owned_workflow = decision_workflow is None and isinstance(connector, CoreConnector)
    if owned_workflow:
        decision_workflow = DecisionWorkflowClient.configured(connector.base_url)

    @asynccontextmanager
    async def lifespan(application):
        yield
        if http:
            await http.aclose()
        if owned_workflow and decision_workflow:
            await decision_workflow.close()
        if hasattr(repository, "close"):
            await repository.close()

    app = FastAPI(title="FlowBridge Data Workspace", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def private(request, call_next):
        if isinstance(connector, CoreConnector):
            async with connector.request_scope():
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(request, error):
        return JSONResponse({"detail": "Workspace persistence unavailable; check migrations"}, 503)

    bearer_dependency = Security(HTTPBearer(auto_error=False))

    async def actor(
        request: Request, credentials: HTTPAuthorizationCredentials | None = bearer_dependency
    ):
        values = request.headers.getlist("authorization")
        if len(values) != 1 or not values[0].startswith("Bearer ") or len(values[0]) < 8:
            raise HTTPException(
                401, "Sign in through FlowBridge SSO", headers={"WWW-Authenticate": "Bearer"}
            )
        return await connector.context(values[0][7:])

    actor_dependency = Depends(actor)

    @app.get(PREFIX + "/changes")
    async def changes(ctx=actor_dependency):
        bindings = decision_bindings if decision_bindings is not None else json.loads(
            os.environ.get("FLOWBRIDGE_WORKSPACE_DECISION_BINDINGS", "{}")
        )
        return await change_manifest(ctx, connector, repository, bindings)

    async def dataset(ctx, config):
        product, rows = await connector.rows(
            ctx,
            config,
            configured_columns(config),
            where={key: {"_eq": value} for key, value in config.filters.items()},
        )
        return prepare(config, rows, product)

    async def reauthorize(ctx, payload):
        # Cached results are never a replacement for current Data Product permission checks.
        async def check(source):
            async with authorization_slots:
                await connector.check(
                    ctx, ProductRef.model_validate(source["ref"]), source["columns"]
                )

        await asyncio.gather(*(check(source) for source in payload["sources"]))

    def source(config):
        return {
            "ref": {
                "data_product_id": str(config.data_product_id),
                "contract_version_id": str(config.contract_version_id),
            },
            "columns": configured_columns(config),
        }

    async def inspect_decision(ctx, payload):
        """Never uses SnapshotCache: every decision command re-reads governed APIs."""
        scenario, forecast = payload["scenario"], payload["forecast"]
        evidence = scenario["evidence"]
        if evidence.get("input_digest_version") != "warehouse_operational_v2" or not forecast[
            "evidence"
        ].get("history_sha256"):
            raise HTTPException(409, "Recompute this scenario and forecast before submitting")
        body = SimulationRequest.model_validate(
            {
                **evidence["assumptions"],
                "snapshot_id": None,
                "include_sensitivity": False,
                "save": False,
            }
        )
        config = AnalysisConfig.model_validate(forecast["config"])
        history_product, history_rows = await connector.rows(
            ctx,
            config,
            configured_columns(config),
            where={key: {"_eq": value} for key, value in config.filters.items()},
        )
        historical = prepare(
            config,
            history_rows,
            history_product,
            known_event_entities={str(row[config.entity_column]) for row in history_rows},
        )
        if (
            not payload.get("validation_baseline")
            and historical["evidence"]["history_sha256"] != forecast["evidence"]["history_sha256"]
        ):
            raise HTTPException(409, "Forecast history changed; recompute before submitting")
        inputs = await operational_inputs(body, ctx)
        current_digest = input_digest(
            body.warehouse_id,
            body.sku,
            inputs["stocks"],
            inputs["inbounds"],
            inputs["open_rows"],
            inputs["commitment_rows"],
            inputs["preparation_rows"],
            forecast["points"],
        )
        if not payload.get("validation_baseline") and current_digest != evidence["input_sha256"]:
            raise HTTPException(
                409, "Operational source facts changed; create a new scenario revision"
            )
        fresh = await asyncio.to_thread(
            simulate,
            body,
            forecast,
            inputs["stocks"],
            inputs["inbounds"],
            inputs["captured_at"].astimezone(ZoneInfo(config.timezone)).date(),
            inputs["open_rows"],
            inputs["commitment_rows"],
            snapshot_at=inputs["captured_at"],
            source_names=source_names,
            preparation_rows=inputs["preparation_rows"],
        )
        selected = [
            row
            for row in fresh["proposed"].get("orders", [])
            if row["order_id"] == payload["scope"]["order_id"]
        ]
        if len(selected) != 1:
            raise HTTPException(409, "Selected order is no longer eligible for this proposal")
        catalog = await connector.catalog(ctx)
        supplementary, facts = [], {}
        for role, columns in {
            "schedule": [
                "order_id",
                "warehouse_id",
                "carrier_code",
                "scheduled_handoff_at",
                "status",
            ],
            "capacity": [
                "carrier_code",
                "capacity_date",
                "max_dispatches",
                "used_dispatches",
                "status",
            ],
        }.items():
            matches = [p for p in catalog if p["name"] == source_names[role]]
            if len(matches) != 1:
                raise HTTPException(
                    409, "Grant current Logistics schedule and capacity sources before submitting"
                )
            ref = ProductRef(
                data_product_id=matches[0]["id"],
                contract_version_id=matches[0]["contract_version_id"],
            )
            where = (
                {
                    "order_id": {"_eq": payload["scope"]["order_id"]},
                    "warehouse_id": {"_eq": str(body.warehouse_id)},
                }
                if role == "schedule"
                else None
            )
            _, rows = await connector.rows(ctx, ref, columns, where=where)
            facts[role] = rows
            supplementary.append({"ref": ref.model_dump(mode="json"), "columns": columns})
        policy = preparation_policy(inputs["preparation_rows"], body.warehouse_id)
        slot = assess_slot(
            selected[0],
            facts["schedule"],
            facts["capacity"],
            policy,
            config.timezone,
            payload.get("handoff_plan"),
        )
        capacities = slot["capacities"]
        warehouse_ok = policy is not None and not selected[0]["at_risk"]
        logistics_ok = slot["confirmable"]
        proof = {
            "input_sha256": digest(
                {
                    "operational": current_digest,
                    "history": historical["evidence"]["history_sha256"],
                    "schedule": ordered(facts["schedule"]),
                    "capacity": ordered(capacities),
                    **(
                        {"handoff_plan": payload["handoff_plan"]}
                        if payload.get("handoff_plan")
                        else {}
                    ),
                }
            ),
            "warehouse_confirmable": warehouse_ok,
            "logistics_confirmable": bool(logistics_ok),
            "logistics_reasons": slot["reasons"],
            "booking_status": slot["booking_status"],
            "order_source_id": str(body.open_orders.data_product_id),
            "order_contract_id": str(body.open_orders.contract_version_id),
            "sources": supplementary,
            "checked_at": datetime.now(UTC).isoformat(),
            "impact_evidence": dependency_evidence(
                payload,
                inputs,
                selected[0],
                historical["evidence"]["history_sha256"],
                slot["contract_schedule"],
                capacities,
                policy,
            ),
        }
        await reauthorize(ctx, {"sources": [*payload["sources"], *supplementary]})
        return proof

    install_decision_routes(
        app,
        PREFIX,
        actor_dependency,
        repository,
        reauthorize,
        case_repository,
        decision_bindings,
        inspector=inspect_decision if decision_workflow else None,
        event_reader=getattr(connector, "decision_events", None),
        workflow=decision_workflow,
    )

    @app.get(PREFIX + "/products")
    async def products(ctx=actor_dependency):
        return {
            "items": await connector.catalog(ctx),
            "org_id": str(ctx.org_id),
            "can_configure": ctx.admin,
            "source_names": source_names,
        }

    @app.post(PREFIX + "/preview")
    async def preview(body: AnalysisConfig, ctx=actor_dependency):
        if not ctx.admin:
            raise HTTPException(403, "IT role required to configure analytical mappings")
        result = await dataset(ctx, body)
        return {key: value for key, value in result.items() if key != "points"}

    @app.post(PREFIX + "/profile")
    async def profile(body: ProfileRequest, ctx=actor_dependency):
        product, rows = await connector.rows(ctx, body, [body.time_column, body.entity_column])
        days = [local_day(r[body.time_column], body.timezone) for r in rows]
        return {
            "start": min(days).isoformat() if days else None,
            "end": max(days).isoformat() if days else None,
            "entities": sorted({str(r[body.entity_column]) for r in rows}),
            "product": product,
            "rows": len(rows),
        }

    @app.post(PREFIX + "/analytics", response_model=AnalyticsResult)
    async def analytics(body: AnalysisConfig, ctx=actor_dependency):
        return analyze(await dataset(ctx, body))

    @app.post(PREFIX + "/overview", response_model=OverviewResult)
    async def operational_overview(body: OverviewRequest, ctx=actor_dependency):
        instant = datetime.now(UTC)
        mapping = None
        if body.configuration_id:
            saved = await repository.get("config", body.configuration_id, ctx)
            await reauthorize(ctx, saved)
            mapping = AnalysisConfig.model_validate(saved["config"])
            if (mapping.data_product_id, mapping.contract_version_id) != (
                body.data_product_id,
                body.contract_version_id,
            ):
                raise HTTPException(403, "Configuration/source binding mismatch")
        product = await connector.product(ctx, body)
        if mapping:
            columns = configured_columns(mapping)
        else:
            _, entity_column, target_column, _ = recipe(product, source_names)
            columns = ["day", entity_column, target_column]
            if any(f["name"] == "uom" for f in product["fields"]):
                columns.append("uom")
        product, rows = await connector.rows(
            ctx,
            body,
            columns,
            where={key: {"_eq": value} for key, value in mapping.filters.items()}
            if mapping
            else None,
        )
        result = overview(product, rows, body, mapping=mapping, source_names=source_names)
        issues, related, sources = (
            {},
            {},
            [
                {
                    "ref": body.model_dump(
                        include={"data_product_id", "contract_version_id"}, mode="json"
                    ),
                    "columns": columns,
                }
            ],
        )

        async def read_related(name, reference, expected):
            if reference is None:
                related[name] = None
                return
            try:
                metadata, data = await connector.rows(ctx, reference)
                if metadata["name"] != source_names.get(name, expected):
                    raise HTTPException(422, "Related report source does not match its domain role")
                related[name] = data
                sources.append(
                    {
                        "ref": reference.model_dump(mode="json"),
                        "columns": [f["name"] for f in metadata["fields"]],
                    }
                )
                if name == "stock":
                    result["stocks"] = data
                    result["stock_evidence"] = {
                        "data_products": [metadata],
                        "executed_at": instant.isoformat(),
                        "formulas": {"available_quantity": "on_hand_quantity - reserved_quantity"},
                    }
            except HTTPException as error:
                if error.status_code not in {403, 404, 502, 503}:
                    raise
                related[name] = None
                issues[name] = error.status_code

        await asyncio.gather(
            read_related("stock", body.stock, "warehouse.stock_availability"),
            read_related("inbound", body.inbound, "warehouse.inbound_schedule"),
            read_related("capacity", body.capacity, "logistics.handoff_capacity"),
            read_related("catalog", body.catalog, "warehouse.planning_catalog"),
        )
        if related.get("catalog") is not None:
            labels = {}
            for row in related["catalog"]:
                if row.get("product_name"):
                    labels.setdefault(row["sku"], set()).add(row["product_name"])
            # Conflicting names across warehouses remain codes rather than an arbitrary label.
            result["entity_labels"] = {
                sku: next(iter(names)) for sku, names in labels.items() if len(names) == 1
            }
        if (body.stock or body.inbound) and product["name"] == source_names["sales_history"]:
            result["portfolio"] = supply_reference(
                result, related["stock"], related["inbound"], body.planning_days, instant
            )
        if related["capacity"] is not None:
            result["capacity_summary"] = carrier_summary(
                related["capacity"], body.planning_days, instant
            )
        result["related_issues"] = issues
        result["report_sources"] = sources
        result["analytics"]["evidence"]["sources"] = sources[:1]
        result["comparison"]["evidence"]["sources"] = sources[:1]
        if result.get("portfolio"):
            result["portfolio"]["evidence"]["sources"] = [
                s
                for s in sources
                if not body.capacity
                or s["ref"]["data_product_id"] != str(body.capacity.data_product_id)
            ]
        if result.get("capacity_summary"):
            result["capacity_summary"]["evidence"]["sources"] = [
                s
                for s in sources
                if body.capacity
                and s["ref"]["data_product_id"] == str(body.capacity.data_product_id)
            ]
        # No authorization result survives a cross-source read. Catch revocation
        # that occurred while another application's facts were being retrieved.
        await reauthorize(ctx, {"sources": sources})
        return result

    @app.post(PREFIX + "/planning")
    async def merchandise_plan(body: PlanningRequest, ctx=actor_dependency):
        try:
            async with asyncio.timeout(180):
                result, sources = await planning_report(
                    body, ctx, connector, repository, source_names, forecaster
                )
                await reauthorize(ctx, {"sources": sources})
        except TimeoutError:
            raise HTTPException(
                504,
                detail={
                    "code": "PLANNING_TIMEOUT",
                    "message": "Đối chiếu dữ liệu mất quá lâu. Hãy thử lại sau hoặc chọn kho khác.",
                },
            ) from None
        return result

    @app.post(PREFIX + "/planning-options")
    async def available_planning_options(body: PlanningOptionsRequest, ctx=actor_dependency):
        result, sources = await planning_options(body, ctx, connector, source_names)
        await reauthorize(ctx, {"sources": sources})
        return result

    @app.post(PREFIX + "/records")
    async def organization_records(body: RecordsRequest, ctx=actor_dependency):
        return await records_page(body, ctx, connector, source_names)

    @app.get(PREFIX + "/warehouse-coverage")
    async def warehouse_coverage(ctx=actor_dependency):
        return {"all_warehouses_enabled": ctx.admin}

    @app.post(PREFIX + "/all-warehouses")
    async def all_warehouses(
        body: WarehouseOverviewRequest, request: Request, ctx=actor_dependency
    ):
        return await bounded_overview(
            request,
            warehouse_overview(body, ctx, connector, repository, source_names, forecaster),
            timeout=180,
        )

    @app.post(PREFIX + "/configurations", status_code=201)
    async def save_configuration(body: AnalysisConfig, ctx=actor_dependency):
        if not ctx.admin:
            raise HTTPException(403, "IT role required")
        await dataset(ctx, body)
        return await repository.save(
            "config",
            ctx,
            {
                "config": body.model_dump(mode="json"),
                "version": 1,
                "sources": [source(body)],
            },
        )

    @app.get(PREFIX + "/{kind}/saved")
    async def saved(kind: str, ctx=actor_dependency):
        mapping = {"configurations": "config", "forecasts": "forecast", "scenarios": "simulation"}
        if kind not in mapping:
            raise HTTPException(404)
        items = []
        for payload in await repository.list(mapping[kind], ctx):
            try:
                await reauthorize(ctx, payload)
            except HTTPException as error:
                if error.status_code in {403, 404}:
                    continue
                raise
            items.append(payload)
        return {"items": items}

    @app.post(PREFIX + "/forecasts", status_code=201, response_model=ForecastResult)
    async def forecast(body: ForecastRequest, ctx=actor_dependency):
        requested_at = datetime.now(UTC)
        config = forecast_config_at_request(body, requested_at)
        today = requested_at.astimezone(ZoneInfo(config.timezone)).date()
        if config.end >= today:
            raise HTTPException(422, "Forecast history must end before today's incomplete period")
        if body.training_days:
            if config.frequency != "D":
                raise HTTPException(422, "Training-day lookback requires daily frequency")
            product, rows = await connector.rows(
                ctx,
                config,
                configured_columns(config),
                where={key: {"_eq": value} for key, value in config.filters.items()},
            )
            observed = [
                local_day(r[config.time_column], config.timezone)
                for r in rows
                if (not config.entities or str(r[config.entity_column]) in config.entities)
                and local_day(r[config.time_column], config.timezone) <= config.end
            ]
            if not observed:
                raise HTTPException(
                    422, detail={"code": "NO_DATA", "message": "Chưa có lịch sử để dự đoán."}
                )
            config = config.model_copy(
                update={
                    "start": max(min(observed), config.end - timedelta(days=body.training_days - 1))
                }
            )
            known = None
            if product["name"] in {
                source_names["sales_history"],
                source_names["transport_history"],
            }:
                _, entity_col, target_col, _ = recipe(product, source_names)
                if (
                    config.time_column == "day"
                    and config.entity_column == entity_col
                    and config.target_column == target_col
                    and config.missing_policy == "zero"
                ):
                    known = {str(r[entity_col]) for r in rows}
            data = prepare(config, rows, product, known_event_entities=known)
        else:
            data = await dataset(ctx, config)
        points, model = await asyncio.to_thread(
            forecaster.run,
            data,
            config,
            body.allow_statistical_fallback,
        )
        payload = {
            "status": "SUCCEEDED",
            "config": config.model_dump(mode="json"),
            "points": points,
            "model": model,
            "evidence": {
                **data["evidence"],
                "model": model,
                "requested_at": requested_at.isoformat(),
                "origin_policy": "selected_calendar_window"
                if body.start_date is not None
                else "request_date"
                if body.start_from_request_date
                else "explicit_or_history",
            },
            "sources": [source(config)],
            "quality": data["quality"],
            "created_at": datetime.now(UTC).isoformat(),
            "metrics": [
                {
                    "item_id": entity,
                    "forecast_total": (
                        sum(p["p50"] for p in points if p["item_id"] == entity)
                        if config.metric_type == "FLOW"
                        else None
                    ),
                }
                for entity in sorted({p["item_id"] for p in points})
            ],
        }
        await reauthorize(ctx, payload)
        return await repository.save("forecast", ctx, payload)

    @app.get(PREFIX + "/forecasts/{run_id}", response_model=ForecastResult)
    async def get_forecast(run_id: UUID, ctx=actor_dependency):
        payload = await repository.get("forecast", run_id, ctx)
        await reauthorize(ctx, payload)
        return payload

    async def operational_inputs(body, ctx):
        warehouse_filter = (
            {"warehouse_id": {"_eq": str(body.warehouse_id)}} if body.warehouse_id else None
        )
        stock_columns = ["warehouse_id", "sku", "uom", "on_hand_quantity", "reserved_quantity"]
        stock_metadata = await connector.product(ctx, body.stock)
        if any(f["name"] == "updated_at" for f in stock_metadata["fields"]):
            stock_columns.append("updated_at")
        inbound_columns = [
            "warehouse_id",
            "sku",
            "uom",
            "status",
            "expected_at",
            "remaining_quantity",
        ]
        (stock_product, stocks), (inbound_product, inbounds) = await asyncio.gather(
            connector.rows(ctx, body.stock, stock_columns, where=warehouse_filter),
            connector.rows(ctx, body.inbound, inbound_columns, where=warehouse_filter),
        )
        if (
            stock_product["name"] != source_names["stock"]
            or inbound_product["name"] != source_names["inbound"]
        ):
            raise HTTPException(422, "Select the published stock and inbound planning products")
        open_rows = commitment_rows = None
        planning_sources = []
        if body.open_orders:
            order_columns = [
                "warehouse_id",
                "order_id",
                "order_line_id",
                "sku",
                "uom",
                "ordered_quantity",
                "status",
                "requested_handoff_at",
            ]
            order_metadata = await connector.product(ctx, body.open_orders)
            if any(f["name"] == "order_number" for f in order_metadata["fields"]):
                order_columns.append("order_number")
            commitment_columns = [
                "warehouse_id",
                "order_id",
                "order_line_id",
                "sku",
                "uom",
                "dispatched_quantity",
                "held_quantity",
            ]
            open_product, open_rows = await connector.rows(
                ctx, body.open_orders, order_columns, where=warehouse_filter
            )
            commitment_product = await connector.product(ctx, body.commitments)
            if (
                open_product["name"] != source_names["open_orders"]
                or commitment_product["name"] != source_names["commitments"]
            ):
                raise HTTPException(422, "Select the published order/commitment products")
            order_ids = sorted({str(row["order_id"]) for row in open_rows})
            batch_slots = asyncio.Semaphore(4)

            async def read_commitments(ids):
                async with batch_slots:
                    _, rows = await connector.rows(
                        ctx,
                        body.commitments,
                        commitment_columns,
                        where={**(warehouse_filter or {}), "order_id": {"_in": ids}},
                    )
                    return rows

            pending = [
                asyncio.create_task(read_commitments(order_ids[index : index + 100]))
                for index in range(0, len(order_ids), 100)
            ]
            try:
                commitment_rows = [row for batch in await asyncio.gather(*pending) for row in batch]
            except BaseException:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                raise
            planning_sources = [
                {
                    "ref": body.open_orders.model_dump(mode="json"),
                    "columns": order_columns,
                },
                {
                    "ref": body.commitments.model_dump(mode="json"),
                    "columns": commitment_columns,
                },
            ]
        preparation_rows = None
        if body.preparation:
            columns = [
                "warehouse_id",
                "preparation_minutes",
                "preparation_policy_revision",
                "preparation_confirmed_at",
                "preparation_confirmed_by",
            ]
            product, preparation_rows = await connector.rows(
                ctx,
                body.preparation,
                columns,
                where={"warehouse_id": {"_eq": str(body.warehouse_id)}},
            )
            if product["name"] != source_names["preparation"]:
                raise HTTPException(422, "Select the published warehouse preparation policy")
            planning_sources.append(
                {"ref": body.preparation.model_dump(mode="json"), "columns": columns}
            )
        return {
            "stocks": stocks,
            "preparation_rows": preparation_rows,
            "inbounds": inbounds,
            "open_rows": open_rows,
            "commitment_rows": commitment_rows,
            "captured_at": datetime.now(UTC),
            "sources": [
                *planning_sources,
                {
                    "ref": body.stock.model_dump(mode="json"),
                    "columns": stock_columns,
                },
                {
                    "ref": body.inbound.model_dump(mode="json"),
                    "columns": inbound_columns,
                },
            ],
        }

    @app.post(PREFIX + "/scenarios", response_model=ScenarioResult)
    async def scenario(body: SimulationRequest, ctx=actor_dependency):
        forecast = await repository.get("forecast", body.forecast_run_id, ctx)
        await reauthorize(ctx, forecast)
        if body.snapshot_id:
            inputs = snapshots.get(ctx, body)
            await reauthorize(ctx, inputs)
            snapshot_id = str(body.snapshot_id)
        else:
            inputs = await operational_inputs(body, ctx)
            await reauthorize(ctx, inputs)
            snapshot_id = snapshots.put(ctx, body, inputs)
        result = await asyncio.to_thread(
            simulate,
            body,
            forecast,
            inputs["stocks"],
            inputs["inbounds"],
            inputs["captured_at"].astimezone(ZoneInfo(forecast["config"]["timezone"])).date(),
            inputs["open_rows"],
            inputs["commitment_rows"],
            snapshot_at=inputs["captured_at"],
            source_names=source_names,
            preparation_rows=inputs.get("preparation_rows"),
        )
        if body.include_sensitivity:
            result["sensitivity"] = await asyncio.to_thread(
                sensitivity,
                body,
                forecast,
                inputs["stocks"],
                inputs["inbounds"],
                inputs["captured_at"].astimezone(ZoneInfo(forecast["config"]["timezone"])).date(),
                inputs["open_rows"],
                inputs["commitment_rows"],
                snapshot_at=inputs["captured_at"],
                reference_result=result,
                source_names=source_names,
                preparation_rows=inputs.get("preparation_rows"),
            )
        result["evidence"].update(
            {
                "snapshot_id": snapshot_id,
                "snapshot_ttl_seconds": snapshots.ttl,
                "snapshot_freshness": (
                    "Edits reuse inputs; refresh reads APIs. "
                    "Source grants are checked on every use."
                ),
            }
        )
        payload = {
            **result,
            "created_at": datetime.now(UTC).isoformat(),
            "sources": [*forecast["sources"], *inputs["sources"]],
        }
        await reauthorize(ctx, payload)
        if body.save:
            return await repository.save("simulation", ctx, payload)
        return payload

    @app.get(PREFIX + "/scenarios/{scenario_id}", response_model=ScenarioResult)
    async def get_scenario(scenario_id: UUID, ctx=actor_dependency):
        payload = await repository.get("simulation", scenario_id, ctx)
        await reauthorize(ctx, payload)
        return payload

    @app.post(PREFIX + "/stock-options")
    async def stock_options(body: ProductRef, ctx=actor_dependency):
        product, rows = await connector.rows(ctx, body)
        if product["name"] != source_names["stock"]:
            raise HTTPException(422, "Select stock availability")
        return {
            "items": rows,
            "evidence": {
                "data_products": [product],
                "executed_at": datetime.now(UTC).isoformat(),
                "formulas": {"available_quantity": "on_hand_quantity − reserved_quantity"},
                "freshness": "Current published snapshot, fetched on request",
                "source_updated_at": max(
                    (r["updated_at"] for r in rows if r.get("updated_at")), default=None
                ),
            },
        }

    @app.post(PREFIX + "/capacity")
    async def capacity(body: CapacityRequest, ctx=actor_dependency):
        product, rows = await connector.rows(ctx, body)
        if not product["name"].endswith("handoff_capacity") or body.end < body.start:
            raise HTTPException(422, "Select capacity product and valid period")
        series = []
        for row in rows:
            day = local_day(row["capacity_date"], "Asia/Ho_Chi_Minh")
            if row["carrier_code"] != body.carrier_code or not body.start <= day <= body.end:
                continue
            used = numeric(row["used_dispatches"])
            baseline = numeric(row["max_dispatches"]) if row["status"] == "ACTIVE" else 0
            proposed = baseline + body.additional_dispatches if row["status"] == "ACTIVE" else 0
            series.append(
                {
                    "day": day.isoformat(),
                    "booked_dispatches": used,
                    "baseline_capacity": baseline,
                    "proposed_capacity": proposed,
                    "remaining_dispatches": max(0, baseline - used),
                    "utilization_percent": 100 * used / baseline if baseline else None,
                }
            )
        return {
            "series": sorted(series, key=lambda r: r["day"]),
            "evidence": {
                "data_products": [product],
                "unit": "dispatches",
                "assumptions": body.model_dump(mode="json"),
                "formula": "remaining = max(0, ACTIVE max_dispatches − used_or_booked_dispatches)",
                "limitations": [
                    "Known bookings only; SKU demand is never converted into dispatches",
                    "UNAVAILABLE carrier days remain unavailable",
                ],
            },
        }

    return app
