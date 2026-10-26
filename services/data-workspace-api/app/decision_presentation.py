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
            answer=(
                "Chọn một câu hỏi gợi ý bên dưới. Phần này giải thích số liệu đã lưu "
                "và không thực hiện thao tác thay bạn."
            ),
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
            "title": "Hồ sơ phối hợp · phiên bản " + str(revision["number"]),
        }
    ]
    result["warnings"].append(
        {
            "code": "SAVED_EVIDENCE",
            "message": (
                "Số liệu được tính lúc lưu phiên bản này; "
                "hãy kiểm tra dữ liệu hiện tại trước khi quyết định. "
                "Có câu trả lời không có nghĩa là hàng đã được bàn giao."
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
                    "message": (
                        "Kết quả cũ chưa xác định được lượng thiếu ở từng thời điểm. Cần tính lại."
                    ),
                }
            )
            return result
        unit = evidence["unit"]
        rows = []
        for name, side in (
            ("1. Kế hoạch ban đầu", scenario.get("reference", scenario["baseline"])),
            ("2. Đổi nhu cầu hoặc ngày về", scenario["baseline"]),
            ("3. Nhập thêm hàng", scenario["proposed"]),
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
                "Lượng hàng thiếu lớn nhất tại một thời điểm trong kỳ của mặt hàng "
                + payload["scope"]["sku"]
                + " tại kho, tính cả đơn đang chờ và lượng khách có thể đặt thêm. "
                "Không cộng lượng thiếu của các ngày. "
                "Số lẻ xuất hiện vì nhu cầu dự báo là ước tính; "
                "đây không phải lượng thiếu riêng của một đơn hoặc xác suất giao trễ."
            ),
            "filters": {"warehouse_id": case["warehouse_id"], "sku": payload["scope"]["sku"]},
        }
        columns = [
            {"field": "scenario", "label": "Phương án", "type": "string"},
            {
                "field": "peak_shortage",
                "label": "Lượng hàng thiếu lớn nhất trong kỳ",
                "type": "number",
                "unit": unit,
            },
            {
                "field": "shortage_days",
                "label": "Số ngày có thời điểm thiếu hàng",
                "type": "integer",
                "unit": "ngày",
            },
            {
                "field": "ending_inventory",
                "label": "Hàng còn lại cuối kỳ",
                "type": "number",
                "unit": unit,
            },
        ]
        result["datasets"] = [
            {
                "id": "scenario-supply",
                "title": "So sánh lượng thiếu và hàng còn lại của mặt hàng trong kho",
                "columns": columns,
                "rows": rows,
                "context": context,
                "source_ids": [source_id],
            }
        ]
        result["tables"] = [
            {
                "id": "scenario-table",
                "title": "So sánh ba phương án đủ hàng",
                "dataset_id": "scenario-supply",
                "fields": [c["field"] for c in columns],
            }
        ]
        result["charts"] = [
            {
                "id": "scenario-shortage",
                "title": "Lượng hàng thiếu của " + payload["scope"]["sku"] + " tại kho trong kỳ",
                "dataset_id": "scenario-supply",
                "type": "bar",
                "orientation": "vertical",
                "stacking": "none",
                "x_field": "scenario",
                "x_label": "Phương án",
                "y_label": "Lượng hàng thiếu lớn nhất trong kỳ",
                "y_unit": unit,
                "series": [
                    {"field": "peak_shortage", "label": "Lượng hàng thiếu lớn nhất trong kỳ"}
                ],
            }
        ]
        result["answer"] += (
            "Bảng dưới so sánh lượng thiếu của mặt hàng trong toàn kho, "
            "không phải riêng đơn của hồ sơ. "
            "Phương án 1 dùng nhu cầu và lịch hàng về ban đầu; phương án 2 áp dụng thay đổi "
            "nhu cầu hoặc ngày hàng về; phương án 3 thêm lượng hàng bạn đề xuất. "
            "Nếu chưa đổi giả định nhu cầu và ngày về thì phương án 1 và 2 sẽ giống nhau. "
            "Số lẻ là kết quả ước tính nhu cầu, không phải số hàng đã kiểm đếm. "
            "Dù tính toán đủ hàng, Kho và Vận chuyển vẫn phải xác nhận thời gian chuẩn bị "
            "và lịch lấy hàng trước khi thực hiện."
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
                {
                    "field": "quantity",
                    "label": "Lượng chưa được giữ đủ khi lập phương án",
                    "type": "number",
                },
                {
                    "field": "before_shortage",
                    "label": "Thiếu trước giờ chuẩn bị · trước bổ sung",
                    "type": "number",
                    "nullable": True,
                },
                {
                    "field": "after_shortage",
                    "label": "Thiếu trước giờ chuẩn bị · sau bổ sung",
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
                            "Từng mặt hàng của đúng đơn này: lượng chưa được giữ đủ "
                            "và lượng thiếu trước giờ Kho cần chuẩn bị tại lần tính đã lưu. "
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
            result["answer"] += "Chưa đọc được tiến độ quy trình; chưa biết đang chờ người nào."
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
