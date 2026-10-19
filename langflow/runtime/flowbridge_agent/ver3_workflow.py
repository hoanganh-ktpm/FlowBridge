# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from flowbridge_agent.tools import ToolResult, normalized, tool_result
from flowbridge_agent.ver2_data import selector_words, words
from flowbridge_agent.ver3_contracts import (
    ROUTES,
    Decision,
    Intent,
    ServiceRequest,
    normalize_slots,
)


@dataclass
class Boundary:
    request: ServiceRequest
    receipts: dict = field(default_factory=dict)


BOUNDARY: ContextVar[Boundary | None] = ContextVar("flowbridge_ver3_boundary", default=None)
ServiceCall = Callable[[ServiceRequest], Awaitable[dict]]


def new_state() -> dict:
    return {
        "task_id": uuid4().hex,
        "slots": {},
        "verified": {},
        "choices": {},
        "evidence": {},
        "plan": [],
        "stage": "needs_entity",
    }


def fast_reply(question: str) -> str | None:
    # Full-message match only; a greeting followed by procurement must reach the gateway.
    text = re.sub(r"[!?.,;]+$", "", normalized(question)).strip()
    if text in {"chao", "xin chao", "chao ban", "hello", "hi", "alo"}:
        return "Chào bạn! Mình là FlowBridge, trợ lý tra cứu và đánh giá nhà cung cấp."
    if text in {"ban la ai", "ban ten gi", "gioi thieu ban than"}:
        return (
            "Mình là FlowBridge, hỗ trợ tra cứu sản phẩm, nhà cung cấp, PR/PO và rủi ro mua hàng."
        )
    if text in {"ban lam duoc gi", "ban co the lam gi", "chuc nang cua ban", "ban giup duoc gi"}:
        return (
            "Mình hỗ trợ tra cứu sản phẩm, báo giá, PR/PO, đánh giá nhà cung cấp và tìm tài liệu. "
            "Mình có thể tư vấn chuẩn bị mua hàng; hiện chưa tạo hay gửi đơn đặt hàng."
        )
    if text in {"ban khoe khong", "ban co khoe khong"}:
        return "Mình sẵn sàng hỗ trợ bạn. Hôm nay bạn cần tìm hiểu mặt hàng nào?"
    if text in {"cam on", "cam on ban", "thanks", "thank you"}:
        return "Rất vui được hỗ trợ bạn!"
    return None


def select_choice(choices: dict, selection: str) -> dict | None:
    items = choices.get("items", [])
    text = normalized(selection).strip(" .!?")
    ordinal = re.fullmatch(r"(?:(?:cai|loai|dong|ncc|ung vien) )?(?:thu )?(\d+)", text)
    numbers = {"dau tien": 1, "thu nhat": 1, "thu hai": 2, "thu ba": 3}
    index = (
        int(ordinal[1])
        if ordinal
        else next((v for k, v in numbers.items() if text == k or text.endswith(" " + k)), None)
    )
    if index is not None:
        return items[index - 1] if 1 <= index <= len(items) else None
    stem = choices.get("kind", "product")
    tokens = set(selector_words(selection, "supplier" if stem == "supplier" else "product"))
    if not tokens:
        return None
    matches = []
    for item in items:
        label = " ".join(
            str(item.get(k, ""))
            for k in (stem + "_code", stem + "_name", "request_code", "po_number", "label")
        )
        if tokens <= set(words(label)):
            matches.append(item)
    return matches[0] if len(matches) == 1 else None


def merge(state: dict, decision: Decision, business_date: str) -> dict:
    patch = normalize_slots(decision.slots, business_date)
    if decision.mode == "new" or not state:
        state = new_state()
    else:
        state = deepcopy(state)
    for stem in ("product", "supplier", "request", "order"):
        if stem in patch and normalized(patch[stem]) != normalized(state["slots"].get(stem, "")):
            if stem in {"product", "supplier"}:
                for name in list(state["verified"]):
                    if name.startswith(stem + "_") or (stem == "product" and name == "unit"):
                        state["verified"].pop(name)
            else:
                state.pop("record", None)
                state.pop("request_line", None)
                if stem == "request":
                    state["slots"].pop("product", None)
                    state["verified"] = {}
            state["choices"] = {}
            state["evidence"] = {}
    if decision.clear:
        state["evidence"] = {}
    state["cleared"] = list(set(state.get("cleared", [])) | set(decision.clear))
    for name in patch:
        if name in state["cleared"]:
            state["cleared"].remove(name)
    for name in decision.clear:
        state["slots"].pop(name, None)
        if name == "supplier":
            state["verified"] = {
                k: v for k, v in state["verified"].items() if not k.startswith("supplier_")
            }
    if any(state["slots"].get(k) != v for k, v in patch.items()):
        # Old business results are never reused after a constraint changes.
        state.pop("result", None)
    state["slots"].update(patch)
    if decision.tasks:
        if len(decision.tasks) > 3:
            raise ValueError("PLAN_TOO_LONG")
        state["plan"] = list(dict.fromkeys(decision.tasks))
    if "supplier" in patch and state["plan"] == [Intent.RANK]:
        state["plan"] = [Intent.SCORE]
    if "supplier" in decision.clear and state["plan"] == [Intent.SCORE]:
        state["plan"] = [Intent.RANK]
    if decision.selection:
        choice = select_choice(state["choices"], decision.selection)
        if choice is None:
            state["selection_error"] = True
        else:
            kind = state["choices"]["kind"]
            if kind in {"product", "supplier"}:
                state["slots"][kind] = choice.get(kind + "_code") or choice[kind + "_id"]
                state["verified"] = {
                    k: v
                    for k, v in state["verified"].items()
                    if not k.startswith(kind + "_") and not (kind == "product" and k == "unit")
                }
                if kind == "supplier" and state["plan"] == [Intent.RANK]:
                    state["plan"] = [Intent.SCORE]
            elif kind == "line":
                state["request_line"] = choice
            else:
                state["slots"][kind] = choice.get("request_code") or choice.get("po_number")
                state.pop("record", None)
            state["choices"] = {}
            state.pop("result", None)
    return state


class Workflow:
    def __init__(self, call: ServiceCall, max_calls: int = 6) -> None:
        self.call, self.max_calls = call, max_calls
        self.run_id = uuid4().hex
        self.events: list[dict] = []
        self.receipts: list[dict] = []

    async def execute(self, state: dict, task: Intent, arguments: dict) -> dict:
        if len(self.events) >= self.max_calls:
            raise ValueError("SERVICE_BUDGET_EXHAUSTED")
        service, action = ROUTES[task]
        clearable = {
            Intent.RANK: {"currency", "max_unit_price"},
            Intent.OFFERS: {"supplier"},
        }.get(task, set())
        cleared = sorted(clearable & set(state.get("cleared", [])))
        if cleared:
            arguments = {**arguments, "clear_defaults": cleared}
        request = ServiceRequest(
            run_id=self.run_id,
            task_id=state["task_id"],
            service=service,
            task=task,
            arguments=arguments,
        )
        self.events.append({"service": service, "task": task})
        receipt = ToolResult.model_validate(await self.call(request)).model_dump(mode="json")
        if receipt["tool"] != action:
            raise ValueError("SERVICE_ACTION_MISMATCH")
        self.receipts.append(receipt)
        reference = uuid4().hex
        state["evidence"][reference] = {"task_id": state["task_id"], "result": receipt}
        # Bounded task-local evidence, never sent through model-authored JSON.
        state["evidence"] = dict(list(state["evidence"].items())[-8:])
        return receipt

    def output(
        self, state: dict, status: str, *, text: str = "", result: dict | None = None
    ) -> dict:
        state["stage"] = status
        return {
            "state": state,
            "status": status,
            "text": text,
            "result": result,
            "trace": {
                "run_id": self.run_id,
                "service_calls": len(self.events),
                "events": self.events,
                "receipts": self.receipts,
                "outcomes": [
                    {
                        "tool": r["tool"],
                        "status": r["status"],
                        "error_code": (r["error"] or {}).get("code"),
                    }
                    for r in self.receipts
                ],
            },
        }

    def failed(self, state: dict, result: dict) -> dict | None:
        if result["error"] or result["status"] in {"error", "unavailable"}:
            return self.output(
                state,
                "error",
                result=result,
                text="Chưa hoàn tất vì dịch vụ gặp lỗi. Không thể kết luận từ lượt này.",
            )
        return None

    async def entities(self, state: dict, need_product: bool, need_supplier: bool) -> dict | None:
        missing = [
            stem
            for stem, needed in (("product", need_product), ("supplier", need_supplier))
            if needed and not state["verified"].get(stem + "_id")
        ]
        if not missing:
            return None
        if state["choices"].get("kind") in missing:
            return self.output(
                state,
                "needs_choice",
                text=(
                    "Bạn chọn đối tượng theo số thứ tự hoặc mã nhé."
                    if state["choices"].get("items")
                    else "Bạn cho tên hoặc mã cụ thể hơn nhé."
                ),
            )
        labels = {"product": "tên hoặc mã sản phẩm", "supplier": "tên hoặc mã nhà cung cấp"}
        arguments = {s: state["slots"][s] for s in missing if state["slots"].get(s)}
        if not arguments:
            return self.output(
                state,
                "needs_input",
                text="Bạn cho biết " + ", ".join(labels[s] for s in missing) + " nhé.",
            )
        result = await self.execute(state, Intent.RESOLVE, arguments)
        failed = self.failed(state, result)
        if failed:
            return failed
        data = result["data"] or {}
        for stem, entity in data.get("entities", {}).items():
            # Only the adapter may certify a unique, complete match.
            if entity.get("status") == "success" and not entity.get("truncated"):
                verified = data.get("resolved", {})
                identifier = verified.get(stem + "_id")
                if identifier:
                    UUID(identifier)
                    state["verified"].update(
                        {
                            k: v
                            for k, v in verified.items()
                            if k.startswith(stem + "_") or (stem == "product" and k == "unit")
                        }
                    )
            else:
                state["choices"] = {
                    "kind": stem,
                    "items": entity.get("items") or entity.get("suggestions", []),
                    "truncated": entity.get("truncated", False),
                }
                return self.output(state, "needs_choice", text="; ".join(data.get("questions", [])))
        remaining = [s for s in missing if not state["verified"].get(s + "_id")]
        if remaining:
            return self.output(
                state,
                "needs_input",
                text="Bạn cho biết " + ", ".join(labels[s] for s in remaining) + " nhé.",
            )
        return None

    async def purchase(self, state: dict, kind: str) -> dict | None:
        if state["choices"].get("kind") == kind:
            return self.output(
                state, "needs_choice", text="Bạn chọn mã PR/PO hoặc bổ sung ngày/từ khóa nhé."
            )
        record = state.get("record", {})
        if record.get("kind") != kind:
            result = await self.execute(
                state,
                Intent.REQUEST if kind == "request" else Intent.ORDER,
                {"identifier": state["slots"].get(kind)},
            )
            failed = self.failed(state, result)
            if failed:
                return failed
            data = result["data"] or {}
            key = "request_id" if kind == "request" else "purchase_order_id"
            if not data.get(key) or data.get("truncated"):
                state["choices"] = {
                    "kind": kind,
                    "items": data.get("items", []),
                    "truncated": data.get("truncated", False),
                }
                return self.output(state, "needs_choice", text="; ".join(data.get("questions", [])))
            record = {"kind": kind, "data": data}
            state["record"] = record
        return None

    async def request_line(self, state: dict) -> dict | None:
        stopped = await self.purchase(state, "request")
        if stopped:
            return stopped
        items = state["record"]["data"].get("items", [])
        if not state.get("request_line"):
            if len(items) != 1:
                state["choices"] = {
                    "kind": "line",
                    "items": [
                        {
                            **item,
                            "label": f"Dòng {i}: "
                            + str(item.get("product_name") or item.get("product_id")),
                        }
                        for i, item in enumerate(items, 1)
                    ],
                    "truncated": False,
                }
                return self.output(state, "needs_choice", text="Bạn chọn dòng hàng nào của PR?")
            state["request_line"] = items[0]
        line = state["request_line"]
        if "product" not in state["slots"]:
            state["slots"]["product"] = line["product_id"]
        for source, target in (
            ("quantity", "quantity"),
            ("unit", "unit"),
            ("requested_delivery_date", "delivery_date"),
        ):
            if line.get(source) is not None:
                state["slots"].setdefault(target, str(line[source]))
        return None

    async def run(
        self, previous: dict, decision: Decision, question: str, business_date: str
    ) -> dict:
        if decision.mode == "chat":
            return self.output(
                previous or new_state(),
                "chat",
                text=decision.reply or "Mình có thể giúp bạn tra cứu và đánh giá nhà cung cấp.",
            )
        if decision.mode in {"clarify", "complex"}:
            return self.output(
                previous or new_state(),
                "needs_input",
                text=decision.reply or "Bạn muốn ưu tiên tra cứu, xếp hạng hay đọc tài liệu trước?",
            )
        state = merge(previous, decision, business_date)
        if state.pop("selection_error", False):
            return self.output(
                state,
                "needs_choice",
                text="Chưa xác định được lựa chọn duy nhất. "
                "Bạn chọn theo số thứ tự hoặc mã trong danh sách nhé.",
            )
        if not state["plan"]:
            return self.output(
                state,
                "needs_input",
                text="Bạn muốn tra cứu giá, xếp hạng NCC hay xem rủi ro đơn hàng?",
            )
        outputs = []
        for intent in state["plan"]:
            intent = Intent(intent)
            slots = state["slots"]
            if slots.get("request") and intent in {Intent.RANK, Intent.SCORE}:
                stopped = await self.request_line(state)
                if stopped:
                    return stopped
            if intent in {Intent.RANK, Intent.SCORE, Intent.RESOLVE, Intent.RETRIEVE}:
                stopped = await self.entities(
                    state,
                    intent in {Intent.RANK, Intent.SCORE} or bool(slots.get("product")),
                    intent == Intent.SCORE or bool(slots.get("supplier")),
                )
                if stopped:
                    return stopped
            if intent in {Intent.RANK, Intent.SCORE}:
                missing = [k for k in ("quantity", "unit", "delivery_date") if not slots.get(k)]
                if missing:
                    labels = {
                        "quantity": "số lượng",
                        "unit": "đơn vị",
                        "delivery_date": "ngày nhận",
                    }
                    return self.output(
                        state,
                        "needs_input",
                        text="Bạn bổ sung " + ", ".join(labels[k] for k in missing) + " nhé.",
                    )
                if normalized(slots["unit"]) != normalized(state["verified"].get("unit", "")):
                    return self.output(
                        state,
                        "needs_input",
                        text="Đơn vị sản phẩm là "
                        + str(state["verified"].get("unit"))
                        + ". Bạn xác nhận số lượng theo đơn vị này nhé.",
                    )
                if slots["delivery_date"] < business_date:
                    return self.output(
                        state, "needs_input", text="Ngày nhận đã qua; bạn cho ngày mới nhé."
                    )
                args = {
                    "product_id": state["verified"]["product_id"],
                    "quantity": slots["quantity"],
                    "requested_delivery_date": slots["delivery_date"],
                }
                if intent == Intent.RANK:
                    if slots.get("top_k", 3) > 10:
                        return self.output(
                            state, "needs_input", text="Xếp hạng hỗ trợ top-k từ 1 đến 10 nhé."
                        )
                    args.update(
                        {k: slots[k] for k in ("top_k", "currency", "max_unit_price") if k in slots}
                    )
                    if args.get("max_unit_price") is not None and not args.get("currency"):
                        return self.output(
                            state, "needs_input", text="Giá trần này dùng tiền tệ nào?"
                        )
                else:
                    args["supplier_id"] = state["verified"]["supplier_id"]
            elif intent == Intent.RESOLVE:
                if not state["verified"]:
                    return self.output(
                        state, "needs_input", text="Bạn cho tên hoặc mã sản phẩm/NCC cần tra nhé."
                    )
                return self.output(
                    state,
                    "success",
                    text="Đã xác minh: "
                    + "; ".join(
                        str(state["verified"][k])
                        for k in ("product_name", "supplier_name")
                        if state["verified"].get(k)
                    ),
                )
            elif intent in {Intent.PRODUCTS, Intent.SUPPLIERS}:
                args = {
                    "resource": "products" if intent == Intent.PRODUCTS else "suppliers",
                    "query": slots.get("query"),
                    "page": slots.get("page", 1),
                }
            elif intent == Intent.OFFERS:
                if not slots.get("product"):
                    return self.output(state, "needs_input", text="Bạn cần báo giá sản phẩm nào?")
                args = {k: slots[k] for k in ("product", "supplier") if slots.get(k)}
            elif intent in {Intent.REQUEST, Intent.ORDER, Intent.RISK}:
                kind = "request" if intent == Intent.REQUEST else "order"
                stopped = await self.purchase(state, kind)
                if stopped:
                    return stopped
                if intent != Intent.RISK:
                    result = tool_result(
                        ROUTES[intent][1], {"identifier": slots.get(kind)}, state["record"]["data"]
                    )
                    outputs.append(result)
                    continue
                args = {"order_id": state["record"]["data"]["purchase_order_id"]}
            elif intent == Intent.RESULT:
                if not slots.get("prediction"):
                    return self.output(
                        state, "needs_input", text="Bạn cho mã kết quả dự đoán đã lưu nhé."
                    )
                try:
                    args = {"prediction_id": str(UUID(slots["prediction"]))}
                except ValueError:
                    return self.output(state, "needs_input", text="Mã kết quả dự đoán chưa hợp lệ.")
            else:
                args = {"query": slots.get("query") or question, "top_k": slots.get("top_k", 3)}
                if state["verified"].get("supplier_id"):
                    args["supplier_id"] = state["verified"]["supplier_id"]
            result = await self.execute(state, intent, args)
            failed = self.failed(state, result)
            if failed:
                return failed
            data = result["data"] or {}
            if intent == Intent.OFFERS and data.get("resolved"):
                for key in ("product_id", "supplier_id"):
                    if data["resolved"].get(key):
                        UUID(data["resolved"][key])
                state["verified"].update(data["resolved"])
            if intent == Intent.OFFERS and data.get("entities") and data.get("status") != "success":
                for stem, entity in data["entities"].items():
                    if entity["status"] != "success":
                        state["choices"] = {
                            "kind": stem,
                            "items": entity.get("items") or entity.get("suggestions", []),
                            "truncated": entity.get("truncated", False),
                        }
                        return self.output(
                            state, "needs_choice", text="; ".join(data.get("questions", []))
                        )
            if intent in {Intent.PRODUCTS, Intent.SUPPLIERS}:
                state["choices"] = {
                    "kind": "product" if intent == Intent.PRODUCTS else "supplier",
                    "items": data.get("items", []),
                    "truncated": data.get("truncated", False),
                }
            if intent == Intent.RANK:
                state["choices"] = {
                    "kind": "supplier",
                    "items": data.get("candidates", []),
                    "truncated": False,
                }
            outputs.append(result)
        state["result"] = outputs[-1] if outputs else None
        return {**self.output(state, "completed", result=state["result"]), "results": outputs}


def safe_cell(value: object) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")[:700]


def table(items: list[dict], columns: list[tuple[str, str]]) -> str:
    if not items:
        return "Không có dòng dữ liệu phù hợp trong kết quả trả về."
    return "\n".join(
        [
            "| " + " | ".join(label for _, label in columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
            *[
                "| " + " | ".join(safe_cell(item.get(key)) for key, _ in columns) + " |"
                for item in items[:20]
            ],
        ]
    )


def render_result(result: dict) -> str:
    data, action = result.get("data") or {}, result["tool"]
    if result.get("error"):
        return "Mã lỗi: " + safe_cell(result["error"]["code"])
    if action == "prediction_rank_suppliers":
        candidates = [
            {
                **r,
                "risk_score": r.get("result", {}).get("risk_score"),
                "score_status": r.get("result", {}).get("status"),
            }
            for r in data.get("candidates", [])
        ]
        content = "Kết quả xếp hạng từ API (rủi ro tăng dần):\n\n" + table(
            candidates,
            [
                ("rank", "Hạng"),
                ("supplier_name", "Nhà cung cấp"),
                ("unit_price", "Đơn giá"),
                ("currency", "Tiền tệ"),
                ("risk_score", "Điểm rủi ro"),
                ("score_status", "Trạng thái"),
            ],
        )
        if data.get("unscored") or data.get("status") == "insufficient_data":
            if not candidates:
                content = "API chưa trả được danh sách nhà cung cấp có điểm để xếp hạng."
            content += "\n\nCó ứng viên chưa đủ dữ liệu để chấm; chưa thể kết luận xếp hạng đầy đủ."
            unscored = [
                {
                    **r,
                    "missing": ", ".join(r.get("result", {}).get("missing_fields", [])),
                    "reason": ", ".join(r.get("reason_codes", [])),
                }
                for r in data.get("unscored", [])
            ]
            if unscored:
                content += "\n\n" + table(
                    unscored,
                    [
                        ("supplier_name", "Nhà cung cấp"),
                        ("unit_price", "Đơn giá"),
                        ("currency", "Tiền tệ"),
                        ("missing", "Dữ liệu còn thiếu"),
                        ("reason", "Lý do"),
                    ],
                )
        if data.get("status") == "no_eligible_suppliers":
            content += "\n\nKhông có NCC đủ điều kiện theo bộ lọc đã yêu cầu."
        return content + "\n\nĐiểm rủi ro chưa được hiệu chuẩn thành xác suất giao trễ."
    if action == "prediction_score_supplier":
        risk = data.get("result", {})
        return (
            "Kết quả đánh giá NCC: "
            + safe_cell(risk.get("status"))
            + "; điểm rủi ro: "
            + safe_cell(risk.get("risk_score"))
            + ".\n\nThiếu: "
            + safe_cell(", ".join(risk.get("missing_fields", [])) or "không")
            + ". Điểm này chưa được hiệu chuẩn thành xác suất."
        )
    if action == "knowledge_search_supplier":
        chunks = data.get("chunks", [])
        return "Bằng chứng tài liệu:\n\n" + (
            "\n\n".join(
                str(c.get("text", ""))[:1800]
                + "\nNguồn: "
                + " · ".join(
                    str(c.get("reference", {}).get(k, ""))
                    for k in ("source_uri", "document_version", "chunk_id")
                    if c.get("reference", {}).get(k)
                )
                for c in chunks[:5]
            )
            or "Chưa có đoạn tài liệu phù hợp."
        )
    if action == "prediction_get_order_risk":
        return (
            "Rủi ro đơn hàng: "
            + safe_cell(data.get("status"))
            + (
                "; điểm theo giá trị còn chưa nhận: "
                if data.get("aggregation_policy") == "remaining_value_weighted_v2"
                else "; điểm MAX: "
            )
            + safe_cell(data.get("representative_risk_score"))
            + "; đầy đủ: "
            + safe_cell(data.get("is_complete"))
            + ". Điểm chưa được hiệu chuẩn thành xác suất."
        )
    if action == "prediction_get_result":
        return table(
            [data],
            [
                ("prediction_id", "Mã kết quả"),
                ("status", "Trạng thái"),
                ("risk_score", "Điểm"),
                ("predicted_at", "Thời điểm"),
            ],
        )
    items = data.get("items", [])
    columns = [
        (k, label)
        for k, label in [
            ("product_code", "Mã SP"),
            ("product_name", "Sản phẩm"),
            ("unit", "Đơn vị"),
            ("supplier_code", "Mã NCC"),
            ("supplier_name", "Nhà cung cấp"),
            ("current_unit_price", "Đơn giá"),
            ("currency", "Tiền tệ"),
            ("quoted_lead_days", "Ngày giao dự kiến"),
            ("quantity", "Số lượng"),
            ("requested_delivery_date", "Ngày cần nhận"),
        ]
        if any(k in item for item in items)
    ]
    if not columns and items:
        columns = [(k, k) for k in list(items[0])[:6]]
    prefix = "\n".join(str(data[k]) for k in ("request_code", "po_number", "status") if data.get(k))
    suffix = "\n\nKết quả quét chưa đầy đủ." if data.get("truncated") else ""
    if data.get("has_more"):
        suffix += f"\n\nCòn trang tiếp theo: {data.get('next_page')}."
    if action == "catalog_read_offers":
        suffix += "\n\nBáo giá không xác nhận tồn kho thực tế."
    return prefix + "\n\n" + table(items, columns) + suffix


def render(output: dict) -> str:
    parts = [output.get("text", "")]
    choices = output.get("state", {}).get("choices", {})
    if output["status"] == "needs_choice":
        for index, item in enumerate(choices.get("items", []), 1):
            label = item.get("label") or " — ".join(
                str(item[k])
                for k in (
                    "product_code",
                    "product_name",
                    "unit",
                    "supplier_code",
                    "supplier_name",
                    "request_code",
                    "po_number",
                )
                if item.get(k)
            )
            parts.append(f"{index}. {label}")
        if choices.get("truncated"):
            parts.append("Danh sách quét chưa đầy đủ; bạn cho mã hoặc mô tả cụ thể hơn nhé.")
    for result in output.get("results") or ([output["result"]] if output.get("result") else []):
        parts.append(render_result(result))
    return "\n\n".join(p for p in parts if p).strip()
