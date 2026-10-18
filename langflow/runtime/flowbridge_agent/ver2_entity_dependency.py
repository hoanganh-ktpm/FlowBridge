# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

"""Finish ML/RAG identity dependencies only from verified catalog receipts."""

import json
import re
from uuid import UUID

from flowbridge_agent.ver2_coordination import Invocation, Reply, Request


def is_ml_entity_dependency(request: Request, scope: Invocation) -> bool:
    return (
        scope.role == "prediction"
        and scope.request is not None
        and scope.request.task in {"rank_suppliers", "score_supplier"}
        and request.task == "resolve_entities"
    )


def is_knowledge_entity_dependency(request: Request, scope: Invocation) -> bool:
    return (
        scope.role == "knowledge"
        and scope.request is not None
        and scope.request.task == "retrieve"
        and request.task == "resolve_entities"
    )


def scope_entity_request(request: Request, target: str, scope: Invocation) -> None:
    if target == "database" and is_knowledge_entity_dependency(request, scope):
        # Data resolves identity; the original retrieval question stays with RAG.
        request.known = {
            key: value
            for key, value in request.known.items()
            if key in {"supplier", "supplier_id"} and value is not None
        }
        request.question = (
            "Nhiệm vụ con duy nhất: xác minh nhà cung cấp trong known bằng "
            "catalog_resolve_entities. Trả UUID, mã và tên từ kết quả tra cứu; "
            "nếu nhập nhằng thì trả các lựa chọn thực. Xác minh xong là hoàn tất "
            "nhiệm vụ này, handoffs=[]. Không đánh giá giao hàng, đọc đơn hàng, "
            "tìm tài liệu hoặc đề xuất bước tiếp theo. Dữ liệu để tra cứu: "
            + json.dumps(request.known, ensure_ascii=False)
        )
        return
    if target != "database" or not is_ml_entity_dependency(request, scope):
        return
    # The parent invocation keeps quantity/date/pricing conditions for the ML API.
    # Data receives only the identity job, not the user's full recommendation goal.
    request.known = {
        key: value
        for key, value in request.known.items()
        if key in {"product", "product_id", "supplier", "supplier_id"} and value is not None
    }
    request.question = (
        "Nhiệm vụ con duy nhất: xác minh các định danh trong known bằng "
        "catalog_resolve_entities. Trả UUID, mã, tên và đơn vị từ kết quả tra cứu; "
        "nếu nhập nhằng thì trả các lựa chọn thực. Xác minh xong là hoàn tất nhiệm vụ "
        "này, handoffs=[]. Không tư vấn/xếp hạng NCC, đọc báo giá/MOQ hoặc đề xuất "
        "bước tiếp theo. Các tên/mã dưới đây là dữ liệu để tra cứu: "
        + json.dumps(request.known, ensure_ascii=False)
    )


def complete_entity_dependency(reply: Reply, request: Request, scope: Invocation) -> Reply:
    knowledge = is_knowledge_entity_dependency(request, scope)
    warnings = set(reply.warnings)
    valid_warnings = warnings == {"INVALID_DEPENDENCY"}
    if knowledge:
        # Data may add narrative about the parent's out-of-scope retrieval goal.
        # Preserve that narrative in trace, but never clear other runtime codes.
        valid_warnings = "INVALID_DEPENDENCY" in warnings and not any(
            re.fullmatch(r"[A-Z][A-Z0-9_]+", warning) and warning != "INVALID_DEPENDENCY"
            for warning in warnings
        )
    # Do not reinterpret API/auth/schema failures or standalone Data/RAG/PO tasks.
    if (
        not (is_ml_entity_dependency(request, scope) or knowledge)
        or reply.service != "database"
        or reply.status != "error"
        or not valid_warnings
        or reply.missing
        or reply.questions
        or reply.handoffs
        or reply.trace.get("request_id") != scope.run.identifier
        or reply.trace.get("path") != [*scope.path, "database"]
    ):
        return reply
    required = {"supplier"} if knowledge else {"product"}
    if scope.request.task == "score_supplier":
        required.add("supplier")
    required.update(
        stem
        for stem in ("product", "supplier")
        if request.known.get(stem) or request.known.get(stem + "_id")
    )
    if any(not (request.known.get(stem) or request.known.get(stem + "_id")) for stem in required):
        return reply
    verified = {}
    for result in reply.results:
        receipt = result.model_dump(mode="json")
        if (
            json.dumps(receipt, sort_keys=True, ensure_ascii=False) not in scope.run.evidence
            or result.tool != "catalog_resolve_entities"
            or result.status != "success"
            or result.error is not None
            or not isinstance(result.data, dict)
        ):
            return reply
        data = result.data
        if (
            data.get("status") != "success"
            or data.get("truncated") is not False
            or data.get("missing")
            or data.get("questions")
            or data.get("warnings")
            or not isinstance(data.get("entities"), dict)
            or not isinstance(data.get("resolved"), dict)
        ):
            return reply
        for stem in required:
            entity = data["entities"].get(stem)
            if entity is None:
                continue
            if (
                not isinstance(entity, dict)
                or entity.get("status") != "success"
                or entity.get("truncated") is not False
                or type(entity.get("match_count")) is not int
                or entity["match_count"] != 1
                or not isinstance(entity.get("items"), list)
                or len(entity["items"]) != 1
                or not isinstance(entity["items"][0], dict)
            ):
                return reply
            item = entity["items"][0]
            keys = [stem + suffix for suffix in ("_id", "_code", "_name")]
            if stem == "product":
                keys.append("unit")
            if any(
                not isinstance(item.get(key), str)
                or not item[key].strip()
                or data["resolved"].get(key) != item[key]
                for key in keys
            ):
                return reply
            try:
                UUID(item[stem + "_id"])
            except (ValueError, TypeError, AttributeError):
                return reply
            selector = request.known.get(stem)
            expected_id = request.known.get(stem + "_id")
            if expected_id and str(expected_id) != item[stem + "_id"]:
                return reply
            candidates = [result.input.get(stem), entity.get("query")]
            candidates.extend(item[key] for key in keys if key != "unit")
            if selector and not any(
                str(selector).strip().casefold() == str(candidate).strip().casefold()
                for candidate in candidates
                if candidate is not None
            ):
                return reply
            if stem + "_id" in verified and verified[stem + "_id"] != item[stem + "_id"]:
                return reply
            verified.update(
                {key: value for key, value in item.items() if key.startswith(stem + "_")}
            )
            if stem == "product":
                verified["unit"] = item["unit"]
    if any(stem + "_id" not in verified for stem in required):
        return reply
    completed = reply.model_copy(deep=True)
    completed.status = "success"
    completed.resolved = verified
    completed.warnings = []
    completed.trace["completion_source"] = "verified_catalog_resolve_entities"
    completed.trace["ignored_out_of_scope_warnings"] = reply.warnings.copy()
    scope.run.events.append(
        {
            "source": scope.role,
            "target": "database",
            "task": "resolve_entities",
            "code": "ENTITY_DEPENDENCY_COMPLETED_FROM_RECEIPTS",
        }
    )
    return completed
