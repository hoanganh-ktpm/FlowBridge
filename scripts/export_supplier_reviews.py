# SPDX-License-Identifier: GPL-3.0-or-later
"""Export one human-style supplier review per supplier from actual stored seed facts."""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from sqlalchemy import text

from flowbridge.db import create_engine
from flowbridge.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
ASOF = datetime.fromisoformat("2026-10-15T18:00:00+07:00")


def number(value):
    return f"{value:,.0f}".replace(",", ".")


def day(value):
    if isinstance(value, datetime):
        value = value.astimezone(ASOF.tzinfo)
    return value.strftime("%d/%m/%Y")


def amount(value):
    return number(value) + " đồng"


def po_facts(row):
    receipts = sorted(row["receipts"], key=lambda receipt: receipt["received_at"])
    total = sum(receipt["received_quantity"] for receipt in receipts)
    completed_at = None
    running = 0
    for receipt in receipts:
        running += receipt["received_quantity"]
        if running >= row["ordered_quantity"]:
            completed_at = max(r["actual_delivery_date"] for r in receipts)
            break
    return {
        **row,
        "receipts": receipts,
        "received": total,
        "completed_at": completed_at,
        "late_days": (completed_at - row["requested_delivery_date"]).days if completed_at else None,
        "overdue_days": max(0, (ASOF.date() - row["requested_delivery_date"]).days),
    }


def render_case(order):
    lines = [
        f"Đơn {order['po_number']} đặt {number(order['ordered_quantity'])} {order['unit']} "
        f"{order['product_name']} ({order['product_code']}), phát hành ngày "
        f"{day(order['order_date'])}, hạn nhận đủ ban đầu là "
        f"{day(order['requested_delivery_date'])}.",
        "",
    ]
    if order["receipts"]:
        for i, receipt in enumerate(order["receipts"], 1):
            lines.append(
                f"- Đợt {i}: nhận {number(receipt['received_quantity'])} {order['unit']} "
                f"ngày {day(receipt['actual_delivery_date'])}; "
                f"phiếu nhận {str(receipt['receipt_id'])[:8]}."
            )
        lines.append("")
    else:
        lines += ["Đến mốc dữ liệu, kho chưa có phiếu ghi nhận nhận hàng cho đơn này.", ""]
    if order["completed_at"]:
        if order["late_days"] > 0:
            lines += [
                f"Kho đã nhận đủ ngày {day(order['completed_at'])}. Tính theo hạn gốc, "
                f"đơn hoàn tất trễ {order['late_days']} ngày. Việc nhận một phần trước đó "
                "chưa thay thế yêu cầu nhận đủ số lượng.",
                "",
            ]
        else:
            lines += [
                f"Kho đã nhận đủ ngày {day(order['completed_at'])}, đáp ứng hạn giao "
                "ban đầu. Đây là kết quả của đơn cụ thể này.",
                "",
            ]
    else:
        remaining = order["ordered_quantity"] - order["received"]
        lines += [
            f"Còn {number(remaining)} {order['unit']} chưa được ghi nhận nhận đủ. "
            + (
                f"Đơn đang quá hạn {order['overdue_days']} ngày tại mốc dữ liệu."
                if order["overdue_days"]
                else "Đơn chưa đến hạn tại mốc dữ liệu."
            ),
            "",
        ]
    confirmations = order["confirmations"]
    if confirmations:
        latest = max(confirmations, key=lambda item: item["recorded_at"])
        lines += [
            f"Cam kết mới nhất trong hồ sơ là ngày {day(latest['promised_delivery_date'])}, "
            f"được ghi nhận ngày {day(latest['recorded_at'])}. "
            "Mốc này dùng để theo dõi phản hồi; đánh giá giao trễ vẫn đối chiếu với "
            "hạn ban đầu trên đơn.",
            "",
        ]
    else:
        lines += [
            "Hồ sơ chưa có bản ghi xác nhận lịch giao của nhà cung cấp. Cần kiểm tra "
            "lại đầu mối phụ trách trước khi coi lịch giao là đã được xác nhận.",
            "",
        ]
    lines += [
        f"Đơn giá trên PO: {amount(order['unit_price'])}/{order['unit']}; "
        f"giá trị dòng hàng: {amount(order['line_total'])}, chưa bao gồm VAT."
    ]
    return "\n".join(lines)


def render_review(supplier, reviewer, orders, products, focus, generated):
    complete = [order for order in orders if order["completed_at"]]
    late = [order for order in complete if order["late_days"] > 0]
    ontime = [order for order in complete if order["late_days"] <= 0]
    opened = [order for order in orders if not order["completed_at"]]
    overdue = [order for order in opened if order["overdue_days"] > 0]
    pending = [order for order in opened if not order["overdue_days"]]
    partial = [order for order in opened if order["received"] > 0]
    confirmed = [order for order in orders if order["confirmations"]]
    revised = [order for order in confirmed if len(order["confirmations"]) > 1]
    waits = [
        (
            min(c["recorded_at"] for c in order["confirmations"]) - order["order_date"]
        ).total_seconds()
        / 86400
        for order in confirmed
    ]
    late_rate = len(late) / len(complete) * 100 if complete else None
    rate = f"{late_rate:.2f}%".replace(".", ",") if late_rate is not None else "chưa tính"
    receipt_count = sum(len(order["receipts"]) for order in orders)
    code = supplier["supplier_code"]
    intro = [
        "Qua đối chiếu đơn đặt hàng và phiếu nhận, điểm cần theo dõi nhiều nhất là thời điểm "
        "nhận đủ hàng. Một số đơn đã có hàng về trước nhưng phần còn lại đến sau hạn.",
        "Hồ sơ cho thấy nhà cung cấp có cả đơn đáp ứng hạn giao và đơn hoàn tất muộn. "
        "Khi đặt tiếp, nên xem nhu cầu của từng mặt hàng và lượng hàng còn đang chờ nhận.",
        "Bộ phận kho đã rà soát các lần nhận hàng với hạn giao trên PO. "
        "Nhận xét dưới đây tập trung vào kết quả đã ghi nhận, cùng các đơn còn cần theo dõi.",
    ][int(code[-2:]) % 3]
    lines = [
        f"* Đánh giá nhà cung cấp {supplier['supplier_name']}",
        "",
        f"Mã nhà cung cấp: {code}",
        f"Người nhận xét: {reviewer['display_name']} — Nhân viên kho",
        f"Bộ phận: {reviewer['department_name']}",
        f"Ngày lập: {day(generated)}",
        f"Mốc dữ liệu giao nhận: {day(ASOF)}, 18:00 (UTC+07:00)",
        f"Phạm vi: {len(orders)} PO phát hành từ "
        f"{day(min(order['order_date'] for order in orders))} đến {day(ASOF)}.",
        "",
        "** 1. Nhận xét chung",
        "",
        f"{supplier['supplier_name']} tham gia cung ứng các nhóm hàng văn phòng, thiết bị "
        f"ngoại vi và vật tư đóng gói. Danh mục giới thiệu hiện tập trung vào {focus.lower()}.",
        "",
        intro,
        "",
        f"Trong {len(complete)} PO đã nhận đủ, có {len(late)} PO hoàn tất sau hạn gốc "
        f"({rate}). Ngoài ra còn {len(overdue)} PO đã qua hạn nhưng chưa có đủ phiếu nhận. "
        "Bộ phận mua hàng nên rà soát các đơn còn thiếu trước khi chốt thêm nhu cầu gấp.",
        "",
        "** 2. Mặt hàng và điều kiện mua",
        "",
        "*** 2.1. Những mặt hàng đã có giao dịch",
        "",
        "Các mặt hàng dưới đây đã xuất hiện trong PO của kỳ theo dõi. Số lượng được tính "
        "riêng theo đơn vị mua; không cộng ram giấy với cuộn băng keo hoặc thiết bị.",
        "",
        "| Mặt hàng | Mã | Đơn vị | Số PO |",
        "|---|---|---|---|",
    ]
    counts = Counter(order["product_code"] for order in orders)
    for product in products:
        if counts[product["product_code"]]:
            lines.append(
                f"| {product['product_name']} | {product['product_code']} | "
                f"{product['unit']} | {counts[product['product_code']]} |"
            )
    lines += [
        "",
        "*** 2.2. Danh mục chào hàng mở rộng",
        "",
        f"Danh mục được cập nhật ngày {day(generated)} có {len(products)} mặt hàng. "
        f"Trong đó {len(products) - len(counts)} mặt hàng mới chưa có PO trong phạm vi "
        "lịch sử này. Chưa dùng kết quả của mặt hàng cũ để khẳng định khả năng giao hàng "
        "hay chất lượng của các mặt hàng mới.",
        "",
        "| Mặt hàng bổ sung | Mã | Đơn vị mua |",
        "|---|---|---|",
    ]
    for product in products:
        if not counts[product["product_code"]]:
            lines.append(
                f"| {product['product_name']} | {product['product_code']} | {product['unit']} |"
            )
    lines += [
        "",
        "Khi đặt mua cần ghi đầy đủ mã, quy cách và đơn vị. Với giấy, giá tính theo ram; "
        "với bút, theo cây; với băng keo và màng quấn, theo cuộn. Quy cách đóng hộp "
        "không tự thay đổi đơn vị đã thống nhất trên PO.",
        "",
        "** 3. Tiến độ và hồ sơ đáng chú ý",
        "",
        "*** 3.1. Tổng hợp tại mốc dữ liệu",
        "",
        f"- Đã nhận đủ: {len(complete)} PO.",
        f"- Nhận đủ đúng hoặc trước hạn ban đầu: {len(ontime)} PO.",
        f"- Nhận đủ sau hạn: {len(late)} PO, chiếm {rate} trong nhóm đã hoàn tất.",
        f"- Chưa nhận đủ: {len(opened)} PO; {len(partial)} PO đã nhận một phần.",
        f"- Đã quá hạn nhưng chưa nhận đủ: {len(overdue)} PO.",
        f"- Chưa nhận đủ và chưa đến hạn: {len(pending)} PO.",
        "",
        "Tỷ lệ giao trễ ở trên chỉ tính trên các đơn đã nhận đủ. Các đơn còn mở được "
        "theo dõi riêng, chưa gán là giao đúng hạn.",
        "",
    ]
    cases = []
    if late:
        anchor = next((order for order in late if order["po_number"] == "DEMO-PO-00212"), None)
        cases.append(
            ("Đơn hoàn tất muộn", anchor or max(late, key=lambda order: order["late_days"]))
        )
    if ontime:
        cases.append(("Đơn đáp ứng hạn giao", max(ontime, key=lambda order: order["completed_at"])))
    if overdue:
        cases.append(
            ("Đơn quá hạn còn thiếu hàng", max(overdue, key=lambda order: order["overdue_days"]))
        )
    if pending:
        cases.append(
            (
                "Đơn chưa đến hạn cần tiếp tục theo dõi",
                min(pending, key=lambda order: order["requested_delivery_date"]),
            )
        )
    for i, (title, order) in enumerate(cases, 2):
        lines += [f"*** 3.{i}. {order['po_number']} — {title}", "", render_case(order), ""]
    lines += [
        "** 4. Chất lượng hàng hóa và tiếp nhận",
        "",
        "*** 4.1. Phạm vi đã đối chiếu",
        "",
        f"Kho có {receipt_count} phiếu ghi nhận tiếp nhận hàng liên quan các PO trong "
        "báo cáo. Các phiếu này dùng để đối chiếu số lượng đã nhận và thời điểm hoàn "
        "tất từng đơn.",
        "",
        "Phiếu nhận chưa có trường ghi kết quả thử nghiệm, số lượng kiểm mẫu hoặc số "
        "lượng bị loại. Vì vậy, việc đã nhận đủ không đồng nghĩa toàn bộ hàng đã qua "
        "kiểm tra chất lượng chi tiết.",
        "",
        "*** 4.2. Bao bì và phản ánh từ bộ phận sử dụng",
        "",
        "Tập hồ sơ được đối chiếu lần này chưa có biên bản riêng về bao bì hư hỏng, "
        "ảnh kiểm tra hay phiếu phản ánh chất lượng. Chưa đủ căn cứ nhận xét về "
        "độ nguyên vẹn của bao bì, độ bám dính của băng keo hoặc độ bền thiết bị.",
        "",
        "Nếu bộ phận sử dụng ghi nhận vấn đề, cần bổ sung mã PO, mặt hàng, số lượng "
        "bị ảnh hưởng, ngày phát hiện và người kiểm tra để có thể theo dõi xử lý.",
        "",
        "** 5. Phản hồi và phối hợp",
        "",
        "*** 5.1. Xác nhận đơn và cập nhật cam kết",
        "",
        f"Có {len(confirmed)}/{len(orders)} PO có bản ghi xác nhận lịch giao; "
        f"{len(orders) - len(confirmed)} PO chưa có bản ghi xác nhận tại mốc dữ liệu.",
        "",
    ]
    if waits:
        lines += [
            f"Trung vị thời gian từ phát hành PO đến bản ghi xác nhận đầu tiên là "
            f"{median(waits):.2f} ngày. Có {len(revised)} PO ghi nhận thay đổi cam kết "
            "qua nhiều phiên bản. Cần đọc thời điểm từng lần cập nhật khi đánh giá "
            "nhà cung cấp có báo sớm hay chỉ thay đổi lịch sau hạn.",
            "",
        ]
    lines += [
        "Hồ sơ xác nhận chưa lưu đầy đủ nội dung trao đổi về nguyên nhân thiếu hàng. "
        "Do đó chưa quy nguyên nhân giao trễ cho tồn kho, vận chuyển hay bộ phận mua hàng "
        "nếu không có căn cứ bổ sung.",
        "",
        "*** 5.2. Xử lý khiếu nại và khắc phục",
        "",
        "Chưa có hồ sơ khiếu nại, biên bản đổi trả hoặc xác nhận đóng sự cố trong tập "
        "dữ liệu này. Tốc độ xác nhận PO không được dùng thay cho thời gian giải quyết "
        "khiếu nại. Nếu có cam kết khắc phục, cần theo dõi đến khi có kết quả kiểm tra.",
        "",
        "** 6. Giá và chứng từ",
        "",
        "*** 6.1. Giá trên các đơn đã đặt",
        "",
        "Đơn giá được lưu theo từng dòng PO, tính bằng VND theo đơn vị mua và chưa "
        "bao gồm VAT. Giá trị dòng bằng đơn giá nhân số lượng; tổng PO bằng tổng "
        "các dòng hàng. Không so trực tiếp tổng tiền của hai đơn khác quy cách hoặc "
        "khác khối lượng mua.",
        "",
        "Các mức giá thuộc bộ dữ liệu mô phỏng, không phải báo giá giao dịch thực tế. "
        "Chưa có hóa đơn để kết luận mức giá đã thanh toán hay mức độ cạnh tranh "
        "so với thị trường.",
        "",
        "*** 6.2. Chứng từ cần đối chiếu thêm",
        "",
        "Đã có PO, lịch giao, bản ghi xác nhận và phiếu nhận theo từng đợt. Chưa có "
        "hóa đơn VAT, chứng từ thanh toán hoặc hồ sơ bảo hành trong phạm vi đánh giá. "
        "Cần bổ sung các chứng từ này khi kiểm tra thanh toán hoặc bảo hành; "
        "không coi email phát hành PO là bằng chứng hàng đã được nhận.",
        "",
        "** 7. Đề xuất hợp tác và việc cần theo dõi",
        "",
        "*** 7.1. Điều kiện xem xét đơn mua tiếp",
        "",
        f"Trước khi chọn {supplier['supplier_name']} cho đơn mới, bộ phận mua hàng "
        "nên yêu cầu xác nhận hàng sẵn có và thời điểm giao đủ. Với đơn cần nhận tập "
        "trung hoặc thời hạn gấp, nên đối chiếu thêm phương án cung ứng khác.",
        "",
        "Nếu thống nhất giao nhiều đợt, cần ghi rõ số lượng và lịch của từng đợt. "
        "Việc đồng ý nhận từng phần không tự xóa kết quả trễ so với hạn ban đầu. "
        "Báo cáo này không tự thay đổi trạng thái hợp tác hay phê duyệt mua hàng.",
        "",
        "*** 7.2. Danh sách theo dõi",
        "",
    ]
    if overdue:
        order = max(overdue, key=lambda item: item["overdue_days"])
        lines += [
            f"- Bộ phận mua hàng: rà soát {order['po_number']}, xác nhận lại kế hoạch "
            f"cho {number(order['ordered_quantity'] - order['received'])} {order['unit']} "
            "chưa ghi nhận nhận đủ. Kiểm tra cả khả năng chậm cập nhật phiếu nhận."
        ]
    if pending:
        order = min(pending, key=lambda item: item["requested_delivery_date"])
        lines += [
            f"- Bộ phận mua hàng: theo dõi {order['po_number']} theo hạn "
            f"{day(order['requested_delivery_date'])}; lưu lại mọi thay đổi cam kết."
        ]
    lines += [
        f"- {reviewer['display_name']}: cập nhật phiếu nhận khi hàng về và đối chiếu "
        "số lượng còn thiếu trước khi ghi nhận hoàn tất.",
        "- Bộ phận sử dụng: bổ sung phản ánh có mã PO và kết quả kiểm tra nếu phát "
        "sinh vấn đề chất lượng; chưa đánh dấu đóng khi mới chỉ có lời hứa xử lý.",
        "",
        "*** 7.3. Mốc rà soát tiếp theo",
        "",
        "Rà soát ngay khi có đợt giao mới hoặc có phản hồi thay đổi lịch giao. "
        f"Các kết quả phát sinh sau {day(ASOF)} chưa nằm trong thống kê trên; "
        "cần cập nhật bằng phiếu nhận và bản ghi phản hồi thực tế trước khi kết luận.",
        "",
    ]
    stats = {
        "total_po": len(orders),
        "completed_po": len(complete),
        "late_completed_po": len(late),
        "ontime_completed_po": len(ontime),
        "open_po": len(opened),
        "overdue_open_po": len(overdue),
        "confirmed_po": len(confirmed),
        "promise_revised_po": len(revised),
        "case_po_numbers": [order["po_number"] for _, order in cases],
    }
    return "\n".join(lines), stats


async def export(mirror):
    config = json.loads((ROOT / "examples/rag/realistic_catalog.json").read_text(encoding="utf-8"))
    path = ROOT / "examples/rag/catalog.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    catalog["dataset_id"] = config["version"]
    generated = datetime.now(UTC)
    provenance = {
        "format_version": "supplier-review-export-v2",
        "synthetic_source": True,
        "exported_from_database": True,
        "exported_at": generated.isoformat(),
        "report_asof": ASOF.isoformat(),
        "catalog_reference": "realistic_catalog.json",
        "reviewer_attribution": "Synthetic assignment of existing warehouse identities; "
        "not a claim that a human has authored or signed these generated reviews.",
        "missing_source_fields": [
            "quality tests",
            "rejected quantities",
            "complaints",
            "corrective-action closure",
            "invoices",
            "payments",
        ],
        "documents": [],
    }
    engine = create_engine(Settings(_env_file=ROOT / ".env"), use_null_pool=True)
    try:
        async with engine.connect() as c:

            async def fetch(sql, **parameters):
                return [dict(row) for row in (await c.execute(text(sql), parameters)).mappings()]

            for spec, profile in zip(catalog["documents"], config["suppliers"], strict=True):
                supplier = (
                    await fetch(
                        "SELECT * FROM suppliers WHERE supplier_id=CAST(:id AS uuid)",
                        id=spec["supplier_id"],
                    )
                )[0]
                if supplier["supplier_name"] != profile["name"]:
                    raise RuntimeError("Apply the realistic catalog before exporting reviews")
                reviewer = (
                    await fetch(
                        """
                    SELECT u.*,d.department_name FROM users u
                    JOIN departments d USING(department_id)
                    WHERE user_id=CAST(:id AS uuid)
                """,
                        id=spec["reviewer_user_id"],
                    )
                )[0]
                products = await fetch(
                    """
                    SELECT p.* FROM products p JOIN supplier_products sp USING(product_id)
                    WHERE sp.supplier_id=CAST(:id AS uuid) AND sp.is_available
                    ORDER BY product_code
                """,
                    id=spec["supplier_id"],
                )
                orders = await fetch(
                    """
                    SELECT o.purchase_order_id,o.po_number,o.order_date,
                      i.po_item_id,i.ordered_quantity,
                      i.unit,i.unit_price,i.line_total,p.product_name,p.product_code,
                      s.schedule_id,s.requested_delivery_date
                    FROM purchase_orders o JOIN purchase_order_items i USING(purchase_order_id)
                    JOIN products p USING(product_id)
                    JOIN purchase_order_schedules s USING(po_item_id)
                    WHERE o.supplier_id=CAST(:id AS uuid) AND o.po_number LIKE 'DEMO-PO-%'
                      AND o.order_date<=:cutoff ORDER BY o.order_date
                """,
                    id=spec["supplier_id"],
                    cutoff=ASOF,
                )
                receipts = await fetch(
                    """
                    SELECT r.* FROM goods_receipts r
                    JOIN purchase_order_schedules s USING(schedule_id)
                    JOIN purchase_order_items i USING(po_item_id)
                    JOIN purchase_orders o USING(purchase_order_id)
                    WHERE o.supplier_id=CAST(:id AS uuid) AND r.received_at<=:cutoff
                """,
                    id=spec["supplier_id"],
                    cutoff=ASOF,
                )
                confirmations = await fetch(
                    """
                    SELECT ci.* FROM supplier_confirmation_items ci
                    JOIN supplier_confirmations c USING(confirmation_id)
                    JOIN purchase_orders o USING(purchase_order_id)
                    WHERE o.supplier_id=CAST(:id AS uuid) AND ci.recorded_at<=:cutoff
                """,
                    id=spec["supplier_id"],
                    cutoff=ASOF,
                )
                for order in orders:
                    order["receipts"] = [
                        r for r in receipts if r["schedule_id"] == order["schedule_id"]
                    ]
                    order["confirmations"] = [
                        item
                        for item in confirmations
                        if item["schedule_id"] == order["schedule_id"]
                    ]
                orders = [po_facts(order) for order in orders]
                content, stats = render_review(
                    supplier, reviewer, orders, products, profile["focus"], generated
                )
                destination = path.parent / spec["path"]
                destination.write_text(content, encoding="utf-8", newline="\n")
                if mirror:
                    mirror.mkdir(parents=True, exist_ok=True)
                    (mirror / destination.name).write_text(content, encoding="utf-8", newline="\n")
                spec.update(
                    supplier_name=supplier["supplier_name"],
                    reviewer_name=reviewer["display_name"],
                    sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    known_from=generated.isoformat(),
                )
                provenance["documents"].append(
                    {
                        "file_name": destination.name,
                        "supplier_id": spec["supplier_id"],
                        "reviewer_user_id": spec["reviewer_user_id"],
                        "sha256": spec["sha256"],
                        **stats,
                    }
                )
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (path.parent / "source_provenance.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if mirror:
            for filename in ("catalog.json", "source_provenance.json", "realistic_catalog.json"):
                if filename == "catalog.json":
                    mirror_catalog = json.loads(path.read_text(encoding="utf-8"))
                    for spec in mirror_catalog["documents"]:
                        spec["path"] = Path(spec["path"]).name
                    (mirror / filename).write_text(
                        json.dumps(mirror_catalog, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                else:
                    (mirror / filename).write_bytes((path.parent / filename).read_bytes())
        print("Exported 6 supplier reviews from database facts; local catalog hashes updated.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mirror", type=Path)
    arguments = parser.parse_args()
    try:
        asyncio.run(export(arguments.mirror))
    except Exception as error:
        print(f"Export stopped ({type(error).__name__}); inspect source bindings locally.")
        raise SystemExit(1) from None
