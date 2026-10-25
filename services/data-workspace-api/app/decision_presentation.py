# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic read-only adapter to the existing Portal RunResult contract."""

import unicodedata
from uuid import uuid4

from .scenario_decision import METRIC_SEMANTICS_VERSION, measures

CHOICES = {
    "evidence": "Vì sao đơn này cần phối hợp?",
    "comparison": "So sánh phương án trước và sau",
    "tasks": "Hồ sơ đang chờ ai?",
    "outcome": "Đã bàn giao cho đơn vị vận chuyển chưa?",
}


def resolve_intent(question):
    text = (
        "".join(
            c
            for c in unicodedata.normalize("NFD", question or "")
            if unicodedata.category(c) != "Mn"
        )
        .lower()
        .replace("đ", "d")
    )
    if any(
        word in text for word in ("duyet", "xoa", "thuc hien", "ghi du lieu", "sql", "javascript")
    ):
        return None
    for intent, phrases in (
        ("comparison", ("so sanh", "bieu do", "truoc va sau", "truoc sau")),
        ("tasks", ("cho ai", "nhiem vu", "den luot", "dang o buoc")),
        ("outcome", ("ban giao", "giao chua", "hoan tat", "ket qua")),
        ("evidence", ("vi sao", "tai sao", "thieu", "rui ro", "can phoi hop")),
    ):
        if any(phrase in text for phrase in phrases):
            return intent
    return None


def presentation(case, intent, workflow=None):
    result = {
        "schema_version": "1.0",
        "status": "completed",
        "run_id": str(uuid4()),
        "answer": "",
        "datasets": [],
        "tables": [],
        "charts": [],
        "sources": [],
        "warnings": [],
        "questions": [],
        "error": None,
    }
    if intent not in CHOICES:
        result.update(
            status="needs_input",
            answer="Chọn nội dung cần xem. Phần này chỉ đọc, không duyệt hoặc ghi nghiệp vụ.",
            questions=[
                {
                    "id": "workspace-intent",
                    "field": "intent",
                    "kind": "single_choice",
                    "prompt": "Chọn nội dung cần xem",
                    "required": True,
                    "options": [{"value": k, "label": v} for k, v in CHOICES.items()],
                }
            ],
        )
        return result
    revision = case["revision"]
    payload = revision["payload"]
    scenario = payload["scenario"]
    evidence = scenario["evidence"]
    source_id = "decision-evidence"
    result["sources"] = [
        {
            "id": source_id,
            "kind": "tool",
            "tool": "workspace.decision.read",
            "title": "Hồ sơ quyết định · phiên bản " + str(revision["number"]),
        }
    ]
    result["warnings"].append(
        {
            "code": "SAVED_EVIDENCE",
            "message": (
                "Số liệu mô phỏng là bằng chứng lúc lập phiên bản; "
                "kiểm tra hiệu lực trước khi quyết định. "
                "Hoàn tất phản hồi không có nghĩa hồ sơ đã bàn giao."
            ),
        }
    )
    result["answer"] = f"{case['title']} · phiên bản {revision['number']}. "
    if intent in {"comparison", "evidence"}:
        if evidence.get("metric_semantics_version") != METRIC_SEMANTICS_VERSION:
            result["answer"] += "Bằng chứng cũ cần tính lại trước khi so sánh."
            result["warnings"].append(
                {
                    "code": "RECOMPUTE_REQUIRED",
                    "message": "Chưa có chỉ tiêu thiếu nguồn hàng theo thời điểm.",
                }
            )
            return result
        unit = evidence["unit"]
        rows = []
        for name, side in (
            ("Kế hoạch gốc", scenario.get("reference", scenario["baseline"])),
            ("Sau giả định", scenario["baseline"]),
            ("Có bổ sung", scenario["proposed"]),
        ):
            value = measures(side)
            rows.append(
                {
                    "scenario": name,
                    "peak_shortage": value["peak_shortage"],
                    "shortage_days": value["shortage_days"],
                    "ending_inventory": value["ending_inventory"],
                }
            )
        context = {
            "as_of": evidence.get("snapshot_at"),
            "complete": True,
            "metric_definition": (
                "Thiếu nguồn hàng vật lý lớn nhất theo sự kiện "
                "của SKU đang chọn, trong toàn kho; "
                "không phải xác suất trễ hoặc tỷ lệ toàn đơn."
            ),
            "filters": {"warehouse_id": case["warehouse_id"], "sku": payload["scope"]["sku"]},
        }
        columns = [
            {"field": "scenario", "label": "Phương án", "type": "string"},
            {
                "field": "peak_shortage",
                "label": "Thiếu nguồn nhiều nhất",
                "type": "number",
                "unit": unit,
            },
            {
                "field": "shortage_days",
                "label": "Ngày có lúc thiếu",
                "type": "integer",
                "unit": "ngày",
            },
            {"field": "ending_inventory", "label": "Tồn cuối kỳ", "type": "number", "unit": unit},
        ]
        result["datasets"] = [
            {
                "id": "scenario-supply",
                "title": "So sánh nguồn hàng của SKU trong kho",
                "columns": columns,
                "rows": rows,
                "context": context,
                "source_ids": [source_id],
            }
        ]
        result["tables"] = [
            {
                "id": "scenario-table",
                "title": "Nguồn hàng trước và sau",
                "dataset_id": "scenario-supply",
                "fields": [c["field"] for c in columns],
            }
        ]
        result["charts"] = [
            {
                "id": "scenario-shortage",
                "title": "Thiếu nguồn theo thời điểm · cùng SKU và kho",
                "dataset_id": "scenario-supply",
                "type": "bar",
                "orientation": "vertical",
                "stacking": "none",
                "x_field": "scenario",
                "x_label": "Phương án",
                "y_label": "Thiếu nguồn nhiều nhất",
                "y_unit": unit,
                "series": [{"field": "peak_shortage", "label": "Thiếu nguồn nhiều nhất"}],
            }
        ]
        result["answer"] += (
            "So sánh dưới đây dùng cùng dữ liệu nguồn và phiên bản tính. "
            "Đủ nguồn theo mô phỏng vẫn cần xác nhận chuẩn bị, lịch nhận "
            "và đối chiếu bàn giao thực tế."
        )
        original = [
            o for o in scenario["baseline"].get("orders", []) if o["order_id"] == case["order_id"]
        ]
        proposed = [
            o for o in scenario["proposed"].get("orders", []) if o["order_id"] == case["order_id"]
        ]
        if len(original) == 1:

            def missing(order, line):
                if len(order) != 1:
                    return None
                return sum(
                    row["missing_quantity"]
                    for row in order[0]["shortages_at_deadline"]
                    if (row["sku"], row["uom"]) == (line["sku"], line["uom"])
                )

            columns = [
                {"field": "sku", "label": "Sản phẩm", "type": "string"},
                {"field": "uom", "label": "Đơn vị", "type": "string"},
                {"field": "quantity", "label": "Chờ phân bổ khi lập phương án", "type": "number"},
                {
                    "field": "before_shortage",
                    "label": "Thiếu trước hạn · trước",
                    "type": "number",
                    "nullable": True,
                },
                {
                    "field": "after_shortage",
                    "label": "Thiếu trước hạn · sau",
                    "type": "number",
                    "nullable": True,
                },
            ]
            result["datasets"].append(
                {
                    "id": "order-obligations",
                    "title": "Các mặt hàng của đơn đang xét",
                    "columns": columns,
                    "source_ids": [source_id],
                    "context": {
                        **context,
                        "metric_definition": (
                            "Từng SKU/đơn vị của đúng đơn; lượng chưa phân bổ "
                            "và thiếu tại hạn chuẩn bị khi lập phiên bản. "
                            "Không cộng các đơn vị khác nhau."
                        ),
                        "filters": {
                            "order_id": case["order_id"],
                            "warehouse_id": case["warehouse_id"],
                        },
                    },
                    "rows": [
                        {
                            "sku": line["sku"],
                            "uom": line["uom"],
                            "quantity": line["unallocated_quantity"],
                            "before_shortage": missing(original, line),
                            "after_shortage": missing(proposed, line),
                        }
                        for line in original[0]["lines"]
                    ],
                }
            )
            result["tables"].append(
                {
                    "id": "order-lines",
                    "title": "Cùng đơn · từng mặt hàng và đơn vị",
                    "dataset_id": "order-obligations",
                    "fields": [c["field"] for c in columns],
                }
            )
    elif intent == "tasks":
        if workflow is None:
            result["answer"] += (
                "Chưa có lượt quy trình được xác nhận; chưa thể kết luận đang chờ người nào."
            )
        else:
            names = [task["name"] for task in workflow["tasks"]]
            result["answer"] += (
                "Đang chờ: " + "; ".join(names)
                if names
                else "Không có nhiệm vụ người dùng đang mở; "
                "vẫn cần xem trạng thái hồ sơ và bằng chứng bàn giao."
            )
    else:
        result["answer"] += (
            "Hồ sơ đã đối chiếu đủ bằng chứng Kho bàn giao cho đơn vị vận chuyển. "
            "Quy trình kết thúc ở mốc này."
            if case["status"] == "COMPLETED"
            else "Hồ sơ chưa xác minh bàn giao đủ cho đơn vị vận chuyển. "
            "Được duyệt hoặc đã đặt lịch chưa chứng minh hàng đã bàn giao."
        )
    return result
