# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

"""Additive protocol nodes around existing ver2 tools; no model or transport of their own."""

import json
from copy import deepcopy
from typing import Any

from langchain_core.tools import BaseTool
from lfx.custom import Component
from lfx.field_typing import Tool
from lfx.io import DropdownInput, HandleInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message
from pydantic import BaseModel, Field

from flowbridge_agent.ver2_coordination import (
    CURRENT,
    EDGES,
    TASKS,
    Reply,
    Request,
    collect,
    loads,
    resolved_from,
    stamp,
)
from flowbridge_agent.ver2_entity_dependency import complete_entity_dependency, scope_entity_request

NAMES = {"database": "hoi_du_lieu", "prediction": "hoi_du_doan", "knowledge": "hoi_tri_thuc"}


class ProtocolInput(BaseModel):
    input_value: str = Field(
        description="JSON A2A task/question/business_date/known; task chỉ dùng tên trong "
        "danh sách tool.description, không tự đặt. Evidence do runtime giữ."
    )


def matching_receipts(scope, known: dict) -> list[dict]:
    """Only forward receipts matching the requested identities, within this execution."""
    matches = []
    for receipt in scope.receipts:
        resolved = resolved_from([receipt])
        if not resolved:
            continue
        relevant = False
        for stem in ("product", "supplier", "order", "request"):
            selector = known.get(stem + "_id") or known.get(stem)
            if not selector:
                continue
            candidates = [receipt["input"].get(stem), receipt["input"].get("identifier")]
            candidates += [resolved.get(stem + suffix) for suffix in ("_id", "_code", "_name")]
            found = any(
                str(selector).strip().casefold() == str(candidate).strip().casefold()
                for candidate in candidates
                if candidate is not None
            )
            if not found:
                relevant = False
                break
            relevant = True
        if relevant:
            matches.append(deepcopy(receipt))
    return matches[-5:]


def normalize_request(value: str, target: str, scope) -> Request:
    raw = deepcopy(loads(value))
    # Serialized evidence authored by a model is never the source of truth.
    raw["evidence"] = []
    request = Request.model_validate(raw)
    if target == "database" and scope.role == "central":
        request.task = {
            "find_multi_supplier_orders": "find_multi_supplier_requests",
            "multi_supplier_orders": "find_multi_supplier_requests",
        }.get(request.task, request.task)
    parent = scope.request
    if scope.role in {"prediction", "knowledge"} and target == "database":
        if parent is None:
            raise ValueError("PROTOCOL_PARENT_REQUIRED")
        request.business_date = parent.business_date
        request.known = deepcopy(parent.known)
        # A dependency uses a Data task, never the parent's ML/RAG task.
        if parent.task == "order_risk":
            request.task = "order"
        elif parent.task in {"rank_suppliers", "score_supplier", "retrieve"}:
            request.task = "resolve_entities"
        else:
            raise ValueError("PROTOCOL_TASK_NOT_ALLOWED")
    if request.task not in TASKS[target]:
        raise ValueError("PROTOCOL_TASK_NOT_ALLOWED")
    scope_entity_request(request, target, scope)
    receipts = matching_receipts(scope, request.known)
    request.evidence = Reply(service=target, status="partial", results=receipts).results
    resolved = resolved_from(receipts)
    if request.task in {"history_samples", "delivery_orders"}:
        # Verified catalog display labels are not extra query parameters. Preserve
        # explicit model/user filters so the strict task DTO can reject unsupported ones.
        resolved = {k: v for k, v in resolved.items() if k in {"product_id", "supplier_id"}}
    request.known.update(resolved)
    return request


class ProtocolTool(BaseTool):
    args_schema: type[BaseModel] = ProtocolInput
    original: BaseTool = Field(exclude=True, repr=False)
    caller_service: str
    target_service: str

    @property
    def action(self) -> str:
        scope = CURRENT.get()
        if (
            scope
            and scope.role in {"prediction", "knowledge"}
            and self.target_service == "database"
        ):
            # The existing task middleware consults action before each model call.
            # After a dependency reply, let the model finish or call its own API.
            if scope.child_replies:
                return "dependency_completed"
            if scope.request:
                resolved = resolved_from(matching_receipts(scope, scope.request.known))
                required = {
                    "rank_suppliers": ("product_id",),
                    "score_supplier": ("product_id", "supplier_id"),
                    "order_risk": ("order_id",),
                    "retrieve": ("supplier_id",),
                }.get(scope.request.task, ())
                if required and all(resolved.get(key) for key in required):
                    return "dependency_completed"
        return self.name

    def _run(self, **kwargs: Any):
        raise NotImplementedError("Use asynchronous tools")

    async def _arun(self, input_value: str):
        scope = CURRENT.get()
        try:
            if scope is None or scope.role != self.caller_service:
                raise ValueError("PROTOCOL_SCOPE_REQUIRED")
            if self.target_service not in EDGES[scope.role]:
                raise ValueError("PROTOCOL_ROUTE_BLOCKED")
            request = normalize_request(input_value, self.target_service, scope)
            if (
                self.target_service == "database"
                and request.task == "request"
                and scope.role == "central"
            ):
                from flowbridge_agent.ver2_request_delivery import request_reply

                raw = await request_reply(request, scope)
            elif self.target_service == "database" and request.task == "delivery_orders":
                from flowbridge_agent.ver2_delivery_orders import delivery_reply

                raw = await delivery_reply(request, scope)
            elif self.target_service == "database" and request.task == "history_samples":
                from flowbridge_agent.ver2_history import history_reply

                raw = await history_reply(request, scope)
            elif self.target_service == "database" and request.task == "find_multi_item_orders":
                from flowbridge_agent.ver2_multi_item_orders import multi_item_reply

                raw = await multi_item_reply(request, scope)
            elif (
                self.target_service == "database" and request.task == "find_multi_supplier_requests"
            ):
                from flowbridge_agent.ver2_multi_supplier import multi_supplier_reply

                raw = await multi_supplier_reply(request, scope)
            else:
                raw = await self.original.ainvoke(
                    {"input_value": json.dumps(request.model_dump(mode="json"), ensure_ascii=False)}
                )
            if isinstance(raw, Message):
                raw = raw.text
            reply = Reply.model_validate(loads(raw))
            if reply.service != self.target_service:
                raise ValueError("PROTOCOL_REPLY_SERVICE_MISMATCH")
            if (reply.results or reply.resolved or reply.status == "success") and (
                reply.trace.get("request_id") != scope.run.identifier
            ):
                raise ValueError("PROTOCOL_REPLY_UNCORRELATED")
            observed = scope.run.evidence
            for result in reply.results:
                receipt = result.model_dump(mode="json")
                if json.dumps(receipt, sort_keys=True, ensure_ascii=False) not in observed:
                    raise ValueError("PROTOCOL_REPLY_UNTRUSTED_EVIDENCE")
                collect(scope, receipt)
            reply = complete_entity_dependency(reply, request, scope)
            if scope.role in {"prediction", "knowledge"} and self.target_service == "database":
                # Keep the complete receipts in runtime. The model only needs IDs,
                # names, missing inputs and (for ambiguity) the actual choices.
                if reply.status == "success":
                    reply.results = []
            return reply.model_dump(mode="json")
        except Exception as exc:
            allowed = {
                "PROTOCOL_SCOPE_REQUIRED",
                "PROTOCOL_ROUTE_BLOCKED",
                "PROTOCOL_PARENT_REQUIRED",
                "PROTOCOL_TASK_NOT_ALLOWED",
                "PROTOCOL_REPLY_SERVICE_MISMATCH",
                "PROTOCOL_REPLY_UNCORRELATED",
                "PROTOCOL_REPLY_UNTRUSTED_EVIDENCE",
            }
            code = (
                str(exc)
                if isinstance(exc, ValueError) and str(exc) in allowed
                else "PROTOCOL_INVALID_MESSAGE"
            )
            reply = Reply(service=self.target_service, status="error", warnings=[code])
            return stamp(reply, scope) if scope else reply.model_dump(mode="json")


class A2AProtocolAdapter(Component):
    name = "A2AProtocolAdapter"
    display_name = "Chuẩn hóa giao tiếp A2A"
    description = (
        "Chuẩn hóa task/JSON, giữ chứng cứ bằng runtime và đóng nhánh Data đã xong; "
        "không gọi model."
    )
    icon = "Braces"
    inputs = [
        HandleInput(
            name="source_tools",
            display_name="A2A hiện có",
            input_types=["Tool"],
            is_list=True,
            required=True,
        ),
        DropdownInput(
            name="caller_service",
            display_name="Bên gọi",
            options=["central", "prediction", "knowledge"],
            value="central",
        ),
        DropdownInput(
            name="target_service", display_name="Bên nhận", options=list(NAMES), value="database"
        ),
    ]
    outputs = [Output(name="tools", display_name="Công cụ đã chuẩn hóa", method="build_tools")]

    def build_tools(self) -> list[Tool]:
        sources = self.source_tools or []
        if len(sources) != 1 or not isinstance(sources[0], BaseTool):
            raise ValueError("Expected exactly one existing A2A tool")
        source = sources[0]
        description = (
            "Task hợp lệ hiện tại: "
            + ", ".join(sorted(TASKS[self.target_service]))
            + ".\n"
            + source.description
            + "\nRuntime chuẩn hóa JSON và giữ evidence; không chép lại ToolResult."
        )
        if self.caller_service == "central" and self.target_service == "database":
            description += (
                "\nHỏi nhóm/yêu cầu này đã giao chưa: task=request, known.identifier là mã "
                "yêu cầu mua chung. Công cụ PR tự đọc các PO con và phiếu nhận trong "
                "request_delivery_evidence; không cần tách thành nhiều lượt hỏi hay xin "
                "xác nhận đọc từng PO. Không đưa mã PO vào task=request; mã PO dùng task=order. "
                "PURCHASE_RESOURCE_MISMATCH là gọi sai công cụ, không phải đơn không tồn tại "
                "hay thiếu quyền. Với tỷ lệ trễ thực tế, chỉ dùng summary của nhóm đã xác minh: "
                "số đơn nhận đủ trễ / số đơn đã nhận đủ. Nếu chưa đơn nào nhận đủ, tỷ lệ null "
                "(chưa xác định); đếm đơn đang quá hạn riêng. Không gọi 0% khi đơn chưa đến hạn. "
                "Chưa có phiếu nhận nghĩa là chưa ghi nhận nhận hàng tại mốc tra cứu; "
                "không tự xác nhận tình trạng ngoài thực tế chưa được nhập. "
                "Nếu 'tỷ lệ trễ' có thể là dự đoán rủi ro, trả tiến độ đã xác minh và hỏi rõ "
                "người dùng có muốn chấm rủi ro; điểm ML chưa hiệu chuẩn không phải xác suất."
            )
            description += (
                "\nTìm 'đơn hàng nhiều nhà cung cấp': dùng task=find_multi_supplier_requests, "
                "known={min_distinct_suppliers:2,page:1,page_size:1}. Mỗi đơn đặt hàng (PO) "
                "gắn một NCC; ý phù hợp là một yêu cầu mua (PR) đã tách ra các PO của nhiều "
                "NCC. Công cụ trả mã yêu cầu chung + các mã đơn con + tên từng NCC. Giải thích "
                "ngắn mô hình này rồi đưa nhóm thật; không dừng ở 'không hỗ trợ' hay bắt chọn "
                "mã để dò. Có thể known.request_code là mã yêu cầu đầy đủ để tìm đúng nhóm. "
                "Không lẫn với task=find_multi_item_orders (nhiều mặt hàng trong cùng PO), "
                "không gom các PO khác yêu cầu thành một nhóm. Lỗi PROTOCOL_TASK_NOT_ALLOWED "
                "là sai tên task, không chứng minh database không có dữ liệu; dùng đúng task."
            )
            description += (
                "\nTìm một/vài đơn gồm nhiều mặt hàng khác nhau: dùng task=find_multi_item_orders, "
                "known={min_distinct_products:2,page:1,page_size:1} nếu chỉ cần một mã. "
                "Backend lọc COUNT(DISTINCT product_id) trước phân trang, không dò 500 đơn, "
                "không bắt người dùng chọn từng mã. Nhiều dòng cùng sản phẩm chưa phải nhiều "
                "loại hàng. Nếu total_matches=0 thì nói chưa có đơn thỏa trong phạm vi quyền "
                "đọc hiện tại; không bịa ví dụ hay nói quét chưa đủ. Còn trang dùng next_page. "
                "Không tự chọn một đơn tìm được làm đơn người dùng đang muốn chấm ML. "
                "Hỏi 'mặt hàng nào số lượng nhiều nhất' chưa nói rõ đại lượng thì hỏi lại "
                "muốn tồn kho, tổng lượng đã đặt hay lượng chưa nhận; không tự chọn. Danh mục "
                "products chỉ là tên/quy cách, phân trang 20 không phải nguyên nhân thiếu số "
                "lượng. Chỉ so lượng trong cùng đơn vị; không xếp hạng cây/cái/ram/cuộn chung "
                "như cùng một đại lượng. Chưa có công cụ đọc số lượng tương ứng thì nói rõ "
                "giới hạn sau khi chốt câu hỏi, không suy từ danh mục hay số nhận thành tồn kho."
            )
            description += (
                "\nHỏi nhà cung cấp của một mã đơn: dùng task=order và mã đơn; kết quả "
                "supplier_id/supplier_code/supplier_name đã đối chiếu với NCC trên đơn. "
                "Không nói thiếu tên khi công cụ đã trả tên. promised_delivery_date là "
                "ngày cam kết, KHÔNG phải ngày nhận thực tế; không gộp thành 'cam kết/hoàn tất'. "
                "status=delivered/completed không chứng minh ngày nhận đủ. Chỉ tính trễ thực "
                "tế từ phiếu nhận/lịch sử giao đã kiểm chứng. Công cụ xem đơn tự trả "
                "delivery_evidence khi tra được phiếu nhận của đúng đơn: "
                "orders.items[].schedules[] có completion_date, received_quantity, days_late, "
                "receipt_ids; as_of và scope thuộc snapshot đó. Nếu có bằng chứng này thì "
                "PHẢI dùng ngày nhận đủ thực tế, không nói chưa có phiếu nhận. Nếu unavailable "
                "chỉ nói lượt tra chưa lấy được bằng chứng, không kết luận DB không có. "
                "Biết NCC và thấy đơn trễ chưa "
                "chứng minh nguyên nhân hay quy lỗi; giải thích nguyên nhân cần tài liệu nguồn."
            )
            description += (
                "\nXem N mẫu/bản ghi lịch sử của kết quả ML, hoặc kiểm chứng hist_sample_count: "
                "dùng task=history_samples, KHÔNG dùng RAG. known gồm product/supplier bằng "
                "tên/mã hoặc product_id/supplier_id, reference=result.history_reference nếu "
                "kết quả ML có; bản cũ dùng feature_asof=trace.feature_asof và expected_count=N. "
                "Giữ sản phẩm/NCC của kịch bản đang bàn từ lịch sử. page=1/page_size=20. "
                "Đây là dòng lịch sử đã nhận đủ dùng tính đặc trưng, không phải toàn bộ "
                "context/training TabICL. Không chạy lại dự đoán chỉ để xem lịch sử. "
                "Nếu không còn reference/cutoff thì có thể đọc lịch sử hiện tại nhưng PHẢI "
                "nói chưa xác minh đó là N mẫu cũ; số lượng trùng không chứng minh đúng mẫu. "
                "Không yêu cầu UUID, không đoán ngày chấm, không nói thiếu quyền trừ lỗi 403. "
                "Trả bảng mã đơn, sản phẩm, lượng, hạn gốc, ngày nhận đủ, số ngày trễ. "
                "Giữ reference_matches/expected_count_matches/warnings/next_page. "
                "Trạng thái changed không được mô tả là đã kiểm chứng mẫu cũ. "
                "Thiếu sản phẩm/NCC thì hỏi đúng thông tin thiếu, không dùng danh mục toàn NCC."
            )
            description += (
                "\nĐơn nào của NCC giao trễ/đang quá hạn: dùng task=delivery_orders, "
                "known={supplier: tên/mã NCC hoặc supplier_id: UUID, "
                "delivery_filter: late|completed_late|open_overdue|all, page:1, page_size:5}. "
                "Đây là tra cứu đơn/lịch giao/phiếu nhận thực trong DB, không dùng RAG để "
                "liệt kê mã đơn. supplier cũng có thể lấy từ NCC đã chốt ở lịch sử hội thoại. "
                "Trả mã đơn, tên hàng, hạn gốc, ngày nhận đủ, số ngày trễ, as_of và next_page. "
                "late gồm cả đã nhận trễ và phần chưa nhận đang quá hạn; "
                "completed_late chỉ đơn đã nhận đủ trễ. Đưa bảng các đơn thực, ghi rõ trang/"
                "tổng số, phân biệt chưa nhận đủ với đã nhận đủ trễ, không bịa nguyên nhân. "
                "Muốn xem tiếp dùng page=next_page và giữ nguyên NCC/filter. "
                "Trang rỗng nhưng total_matches>0 là ngoài phạm vi trang, không phải NCC "
                "không có đơn trễ. "
                "Ngày nhận đủ là null thì nói chưa nhận đủ; không thay bằng hôm nay. "
                "Chỉ các đơn người hỏi có quyền xem, chốt tại thời điểm API đọc; nêu as_of "
                "và scope, không nhận là toàn bộ lịch sử NCC hay cutoff của RAG. "
                "Nếu cần giới hạn kỳ/ngày hoặc mặt hàng mà công cụ chưa hỗ trợ, hỏi/giải thích "
                "giới hạn, không âm thầm bỏ điều kiện. Không dùng kết quả này để tạo đơn."
            )
        if self.target_service == "prediction":
            description += (
                "\nKhi đọc rủi ro đơn, diễn giải theo aggregation_policy API thực trả về. "
                "remaining_value_weighted_v2: representative_risk_score là trung bình theo "
                "giá trị hàng còn chưa nhận, KHÔNG phải MAX; highest_item_risk_score là điểm "
                "dòng cao nhất riêng. Luôn nêu dòng có điểm cao, high_risk_item_count và "
                "scored_value_coverage. Thiếu điểm toàn đơn thì không thay bằng điểm observed "
                "hoặc MAX. Chỉ bản legacy max_open_item_v1 dùng MAX làm điểm đại diện. "
                "Tất cả là điểm chưa hiệu chuẩn, không gọi là tỷ lệ/xác suất trễ."
                " hist_sample_count là số dòng đã nhận đủ cùng NCC/sản phẩm/đơn vị trong "
                "cửa sổ lịch sử dùng tính đặc trưng; không phải toàn bộ dữ liệu model. "
                "API mới có history_reference để Data task=history_samples truy xuất "
                "kiểm chứng mà không chạy lại model. Giữ reference/cutoff khi trình bày."
            )
        if self.caller_service == "central" and self.target_service == "knowledge":
            description += (
                "\nKhông dùng RAG khi người dùng đòi xem N dòng/bản ghi lịch sử đã dùng "
                "tính đặc trưng ML (hist_sample_count). Hãy gọi hoi_du_lieu với "
                "task=history_samples. Không có công cụ liệt kê không có nghĩa thiếu quyền."
            )
        if self.caller_service != "central":
            description += (
                " Chỉ giải quyết định danh; nhận resolved xong thì dùng API chuyên môn, "
                "không gọi lại Data."
            )
        return [
            ProtocolTool(
                name=NAMES[self.target_service],
                description=description,
                original=source,
                caller_service=self.caller_service,
                target_service=self.target_service,
            )
        ]


class A2AReplyEnvelope(Component):
    name = "A2AReplyEnvelope"
    display_name = "Kiểm tra JSON trả về A2A"
    description = (
        "Bỏ khung JSON, kiểm schema/service/handoff, giữ nguyên lỗi và chứng cứ; không gọi model."
    )
    icon = "ShieldCheck"
    inputs = [
        HandleInput(
            name="input_value",
            display_name="Phản hồi từ Parser cũ",
            input_types=["Message", "Data"],
            required=True,
        ),
        DropdownInput(
            name="service", display_name="Service", options=list(NAMES), value="database"
        ),
    ]
    outputs = [Output(name="envelope", display_name="JSON đã kiểm tra", method="validate_reply")]

    def validate_reply(self) -> Data:
        try:
            raw = (
                self.input_value.text
                if isinstance(self.input_value, Message)
                else self.input_value.data
            )
            reply = Reply.model_validate(loads(raw))
            if reply.service != self.service:
                raise ValueError("Service mismatch")
            if any(
                h.service == self.service or h.task not in TASKS[h.service] for h in reply.handoffs
            ):
                raise ValueError("Invalid handoff")
        except (ValueError, TypeError, AttributeError):
            reply = Reply(
                service=self.service, status="error", warnings=["PROTOCOL_INVALID_REPLY_ENVELOPE"]
            )
        # Type Convert (native) will emit this exact JSON string as a Message.
        return Data(data={"text": json.dumps(reply.model_dump(mode="json"), ensure_ascii=False)})
