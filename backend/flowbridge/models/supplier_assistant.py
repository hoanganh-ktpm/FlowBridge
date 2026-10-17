# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""ORM mappings for the agreed Supplier Assistant baseline (27 tables).

Columns, named constraints and indexes match migration 0001_supplier_schema.
RLS, privileges and SQL helpers remain explicitly managed by migrations.
Change these definitions and generate a reviewed migration for future schema changes.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import conv

from flowbridge.models.base import Base


class Supplier(Base):
    __table__ = sa.Table(
        "suppliers",
        Base.metadata,
        sa.Column("supplier_id", sa.UUID(), nullable=False, comment="Khóa nội bộ | PK, NN"),
        sa.Column(
            "supplier_code",
            sa.VARCHAR(length=50),
            nullable=False,
            comment="Mã hiển thị/API | UNIQUE, NN",
        ),
        sa.Column(
            "supplier_name", sa.VARCHAR(length=255), nullable=False, comment="Tên nhà cung cấp | NN"
        ),
        sa.Column("email", sa.VARCHAR(length=255), nullable=True, comment="Email liên hệ | NULL"),
        sa.Column("phone", sa.VARCHAR(length=50), nullable=True, comment="Điện thoại | NULL"),
        sa.Column("address", sa.TEXT(), nullable=True, comment="Địa chỉ | NULL"),
        sa.Column("city", sa.VARCHAR(length=120), nullable=True, comment="Vị trí supplier | NULL"),
        sa.Column(
            "postal_code", sa.VARCHAR(length=30), nullable=True, comment="Mã bưu chính | NULL"
        ),
        sa.Column(
            "country_of_origin",
            sa.CHAR(length=2),
            nullable=True,
            comment="Mã quốc gia ISO alpha-2 | NULL",
        ),
        sa.Column(
            "status",
            sa.VARCHAR(length=20),
            nullable=False,
            comment="active / inactive / blocked | NN, CHECK",
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Thời điểm tạo | NN"
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Thời điểm cập nhật | NN",
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['active'::character varying, "
                "'inactive'::character varying, 'blocked'::character "
                "varying]::text[])"
            ),
            name=conv("ck_suppliers_status_domain"),
        ),
        sa.PrimaryKeyConstraint("supplier_id", name=conv("suppliers_pkey")),
        sa.UniqueConstraint("supplier_code", name=conv("suppliers_supplier_code_key")),
        comment=("C01 | suppliers [P0] - Thông tin nhà cung cấp và định danh tham chiếu. "),
    )


class Product(Base):
    __table__ = sa.Table(
        "products",
        Base.metadata,
        sa.Column("product_id", sa.UUID(), nullable=False, comment="ID sản phẩm | PK, NN"),
        sa.Column(
            "product_code",
            sa.VARCHAR(length=80),
            nullable=False,
            comment="Part number nội bộ | UNIQUE, NN",
        ),
        sa.Column(
            "product_name", sa.VARCHAR(length=255), nullable=False, comment="Tên sản phẩm | NN"
        ),
        sa.Column(
            "product_category",
            sa.VARCHAR(length=120),
            nullable=False,
            comment="Feature categorical | NN",
        ),
        sa.Column("unit", sa.VARCHAR(length=30), nullable=False, comment="Đơn vị tính | NN"),
        sa.Column("description", sa.TEXT(), nullable=True, comment="Mô tả | NULL"),
        sa.Column(
            "status", sa.VARCHAR(length=20), nullable=False, comment="active / inactive | NN, CHECK"
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Ngày tạo | NN"
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Ngày sửa | NN"
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['active'::character varying, "
                "'inactive'::character varying]::text[])"
            ),
            name=conv("ck_products_status_domain"),
        ),
        sa.PrimaryKeyConstraint("product_id", name=conv("products_pkey")),
        sa.UniqueConstraint("product_code", name=conv("products_product_code_key")),
        comment="C02 | products [P0] - Danh mục sản phẩm và feature loại sản phẩm. ",
    )


class SupplierProduct(Base):
    __table__ = sa.Table(
        "supplier_products",
        Base.metadata,
        sa.Column(
            "supplier_product_id",
            sa.UUID(),
            nullable=False,
            comment="ID báo giá hiện hành | PK, NN",
        ),
        sa.Column("supplier_id", sa.UUID(), nullable=False, comment="-> suppliers | FK, NN"),
        sa.Column("product_id", sa.UUID(), nullable=False, comment="-> products | FK, NN"),
        sa.Column(
            "supplier_part_number",
            sa.VARCHAR(length=100),
            nullable=True,
            comment="SKU phía supplier | NULL",
        ),
        sa.Column(
            "current_unit_price",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment="Đơn giá hiện hành, không thay giá PO | NN, >=0",
        ),
        sa.Column("currency", sa.CHAR(length=3), nullable=False, comment="ISO 4217 | NN"),
        sa.Column(
            "min_order_qty",
            sa.NUMERIC(precision=18, scale=4),
            nullable=True,
            comment="Ngưỡng tối thiểu nếu có | NULL, >0",
        ),
        sa.Column(
            "quoted_lead_days",
            sa.INTEGER(),
            nullable=True,
            comment="Lead time được báo giá; không phải feature lịch sử | NULL, >=0",
        ),
        sa.Column("is_available", sa.BOOLEAN(), nullable=False, comment="Khả năng cung ứng | NN"),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Ngày cập nhật; UNIQUE(supplier_id,product_id) trong MVP | NN",
        ),
        sa.CheckConstraint(
            sa.text("current_unit_price >= 0::numeric"),
            name=conv("ck_supplier_products_current_unit_price_nonnegative"),
        ),
        sa.CheckConstraint(
            sa.text("min_order_qty > 0::numeric"),
            name=conv("ck_supplier_products_min_order_qty_positive"),
        ),
        sa.CheckConstraint(
            sa.text("quoted_lead_days >= 0"),
            name=conv("ck_supplier_products_quoted_lead_days_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.product_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_supplier_products_product_id"),
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.supplier_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_supplier_products_supplier_id"),
        ),
        sa.PrimaryKeyConstraint("supplier_product_id", name=conv("supplier_products_pkey")),
        comment=(
            "C03 | supplier_products [P0] - Quan hệ N-N supplier-product và báo giá hiện hành. "
        ),
    )


sa.Index(
    "uq_supplier_product",
    SupplierProduct.__table__.c["supplier_id"],
    SupplierProduct.__table__.c["product_id"],
    unique=True,
)


class PurchaseRequest(Base):
    __table__ = sa.Table(
        "purchase_requests",
        Base.metadata,
        sa.Column("request_id", sa.UUID(), nullable=False, comment="ID PR | PK, NN"),
        sa.Column(
            "request_code", sa.VARCHAR(length=50), nullable=False, comment="PR-001 | UNIQUE, NN"
        ),
        sa.Column(
            "department_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "-> departments. Snapshot phòng ban của nhân viên tại lúc lập PR; "
                "không gom PR qua trưởng phòng và không đổi theo việc user chuyển "
                "phòng. | FK, NN"
            ),
        ),
        sa.Column(
            "requested_by",
            sa.UUID(),
            nullable=False,
            comment=(
                "-> users.user_id của cá nhân nhân viên tạo/gửi PR. Server lấy từ "
                "danh tính đã xác thực; cần active human và grant EMPLOYEE. | FK, "
                "NN"
            ),
        ),
        sa.Column(
            "source", sa.VARCHAR(length=20), nullable=False, comment="web / chat / api | NN, CHECK"
        ),
        sa.Column(
            "status",
            sa.VARCHAR(length=30),
            nullable=False,
            comment=(
                "draft / analysing / needs_review / analysis_failed / "
                "pending_approval / approved / rejected / ordered / cancelled. | "
                "NN, CHECK"
            ),
        ),
        sa.Column(
            "input_revision",
            sa.INTEGER(),
            nullable=False,
            server_default=sa.text("1"),
            comment=(
                "Tăng khi chủ PR sửa product/quantity/deadline/supplier preference "
                "trong trạng thái cho phép; sinh snapshot mới. Không sửa input của "
                "PR có PO chưa hủy hợp lệ. | NN, DEFAULT 1, >0"
            ),
        ),
        sa.Column(
            "submitted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment=(
                "Nhân viên hoàn tất/gửi PR cá nhân. Tạo đầy đủ có thể lập+gửi cùng "
                "hành động; draft thiếu dữ liệu không tự sinh proposal. | NULL"
            ),
        ),
        sa.Column("notes", sa.TEXT(), nullable=True, comment="Ghi chú chung | NULL"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment=(
                "Thời điểm lập PR/draft; feature_asof dùng event tạo/gửi PR đầy đủ "
                "theo quy tắc ở E. | NN"
            ),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Thời điểm cập nhật | NN",
        ),
        sa.CheckConstraint(
            sa.text("input_revision > 0"), name=conv("ck_purchase_requests_input_revision_positive")
        ),
        sa.CheckConstraint(
            sa.text(
                "source::text = ANY (ARRAY['web'::character varying, "
                "'chat'::character varying, 'api'::character varying]::text[])"
            ),
            name=conv("ck_purchase_requests_source_domain"),
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['draft'::character varying, "
                "'analysing'::character varying, 'needs_review'::character varying, "
                "'analysis_failed'::character varying, "
                "'pending_approval'::character varying, 'approved'::character "
                "varying, 'rejected'::character varying, 'ordered'::character "
                "varying, 'cancelled'::character varying]::text[])"
            ),
            name=conv("ck_purchase_requests_status_domain"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.department_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_pr_department"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.user_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_pr_requester_user"),
        ),
        sa.PrimaryKeyConstraint("request_id", name=conv("purchase_requests_pkey")),
        sa.UniqueConstraint("request_code", name=conv("purchase_requests_request_code_key")),
        comment=(
            "C04 | purchase_requests [P0] - PR cá nhân do nhân viên tạo. Mỗi "
            "nhân viên tự tạo PR cá nhân. requested_by lấy từ danh tính đã xác "
            "thực. department_id là snapshot lúc tạo, không đổi theo việc "
            "chuyển phòng của user. Quyền EMPLOYEE chỉ cho PR của mình."
        ),
    )


sa.Index(
    "ix_pr_department_status",
    PurchaseRequest.__table__.c["department_id"],
    PurchaseRequest.__table__.c["status"],
    unique=False,
)


sa.Index(
    "ix_pr_owner_status",
    PurchaseRequest.__table__.c["requested_by"],
    PurchaseRequest.__table__.c["status"],
    unique=False,
)


class PurchaseRequestItem(Base):
    __table__ = sa.Table(
        "purchase_request_items",
        Base.metadata,
        sa.Column("request_item_id", sa.UUID(), nullable=False, comment="ID dòng PR | PK, NN"),
        sa.Column("request_id", sa.UUID(), nullable=False, comment="-> purchase_requests | FK, NN"),
        sa.Column(
            "line_number",
            sa.INTEGER(),
            nullable=False,
            comment="UNIQUE(request_id,line_number) | NN, >0",
        ),
        sa.Column("product_id", sa.UUID(), nullable=False, comment="-> products | FK, NN"),
        sa.Column(
            "preferred_supplier_id",
            sa.UUID(),
            nullable=True,
            comment=(
                "Supplier mong muốn; NULL nếu chưa chọn. Không phải supplier thực "
                "giao của nhãn training. | FK, NULL"
            ),
        ),
        sa.Column(
            "quantity",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment=(
                "Lượng cá nhân yêu cầu mua trên dòng sản phẩm/đơn vị/deadline của "
                "PR. Cùng sản phẩm nhưng deadline khác có thể là các dòng riêng. | "
                "NN, >0"
            ),
        ),
        sa.Column(
            "requested_delivery_date", sa.DATE(), nullable=False, comment="Hạn giao mong muốn | NN"
        ),
        sa.Column(
            "unit_price_snapshot",
            sa.NUMERIC(precision=18, scale=4),
            nullable=True,
            comment="Giá snapshot; null nếu chưa báo giá | NULL, >=0",
        ),
        sa.Column("currency", sa.CHAR(length=3), nullable=False, comment="ISO 4217 | NN"),
        sa.Column(
            "line_total",
            sa.NUMERIC(precision=18, scale=4),
            nullable=True,
            comment="Server tính; có thể null nếu giá chưa có | NULL, >=0",
        ),
        sa.Column("notes", sa.TEXT(), nullable=True, comment="Ghi chú dòng PR | NULL"),
        sa.CheckConstraint(
            sa.text("line_number > 0"), name=conv("ck_purchase_request_items_line_number_positive")
        ),
        sa.CheckConstraint(
            sa.text("line_total >= 0::numeric"),
            name=conv("ck_purchase_request_items_line_total_nonnegative"),
        ),
        sa.CheckConstraint(
            sa.text("quantity > 0::numeric"),
            name=conv("ck_purchase_request_items_quantity_positive"),
        ),
        sa.CheckConstraint(
            sa.text("unit_price_snapshot >= 0::numeric"),
            name=conv("ck_purchase_request_items_unit_price_snapshot_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["preferred_supplier_id"],
            ["suppliers.supplier_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_purchase_request_items_preferred_supplier_id"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.product_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_purchase_request_items_product_id"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["purchase_requests.request_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_purchase_request_items_request_id"),
        ),
        sa.PrimaryKeyConstraint("request_item_id", name=conv("purchase_request_items_pkey")),
        comment=("C05 | purchase_request_items [P0] - Các dòng sản phẩm của PR cá nhân. "),
    )


sa.Index(
    "uq_pr_item_scope",
    PurchaseRequestItem.__table__.c["request_item_id"],
    PurchaseRequestItem.__table__.c["request_id"],
    unique=True,
)


sa.Index(
    "uq_pr_line_number",
    PurchaseRequestItem.__table__.c["request_id"],
    PurchaseRequestItem.__table__.c["line_number"],
    unique=True,
)


class AnalysisTask(Base):
    __table__ = sa.Table(
        "analysis_tasks",
        Base.metadata,
        sa.Column("task_id", sa.UUID(), nullable=False, comment="ID tác vụ | PK, NN"),
        sa.Column("request_id", sa.UUID(), nullable=False, comment="-> purchase_requests | FK, NN"),
        sa.Column(
            "po_item_id",
            sa.UUID(),
            nullable=True,
            comment=(
                "NULL với initial_analysis/reanalysis; bắt buộc với po_monitoring "
                "và thuộc cùng request_id. | FK ghép, NULL theo loại task"
            ),
        ),
        sa.Column(
            "task_type",
            sa.VARCHAR(length=30),
            nullable=False,
            comment=(
                "initial_analysis / reanalysis / po_monitoring. Chỉ "
                "initial_analysis hoặc reanalysis của PR đủ điều kiện được sinh "
                "proposal. | NN, CHECK"
            ),
        ),
        sa.Column(
            "input_revision",
            sa.INTEGER(),
            nullable=False,
            comment=(
                "Revision từ event. Kết quả revision cũ không được thay kết quả hiện hành. | NN, >0"
            ),
        ),
        sa.Column(
            "feature_asof",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Mốc event hoặc request_at cố định; worker retry giữ nguyên. | NN",
        ),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "Bất biến: revision, PR items hoặc PO item, "
                "category/unit/quantity/deadline và nguồn biết tại feature_asof. "
                "Retry không đọc lại input đang thay đổi. | NN, object"
            ),
        ),
        sa.Column(
            "idempotency_key",
            sa.VARCHAR(length=160),
            nullable=False,
            comment=(
                "UNIQUE theo event_id + scope + revision. Task monitoring gồm "
                "po_item_id và cutoff; cùng event retry trả task cũ. | UNIQUE, NN"
            ),
        ),
        sa.Column(
            "status",
            sa.VARCHAR(length=20),
            nullable=False,
            comment="queued / running / succeeded / failed | NN, CHECK",
        ),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Thời điểm chạy | NULL",
        ),
        sa.Column(
            "completed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Thời điểm hoàn tất | NULL",
        ),
        sa.Column("error_code", sa.VARCHAR(length=80), nullable=True, comment="Mã lỗi | NULL"),
        sa.Column("error_message", sa.TEXT(), nullable=True, comment="Chi tiết lỗi | NULL"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Ngày tạo tác vụ | NN",
        ),
        sa.CheckConstraint(
            sa.text("input_revision > 0"), name=conv("ck_analysis_tasks_input_revision_positive")
        ),
        sa.CheckConstraint(
            sa.text("jsonb_typeof(input_snapshot) = 'object'::text"),
            name=conv("ck_analysis_tasks_input_snapshot_object"),
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['queued'::character varying, "
                "'running'::character varying, 'succeeded'::character varying, "
                "'failed'::character varying]::text[])"
            ),
            name=conv("ck_analysis_tasks_status_domain"),
        ),
        sa.CheckConstraint(
            sa.text(
                "task_type::text = ANY (ARRAY['initial_analysis'::character "
                "varying, 'reanalysis'::character varying, "
                "'po_monitoring'::character varying]::text[])"
            ),
            name=conv("ck_analysis_tasks_task_type_domain"),
        ),
        sa.CheckConstraint(
            sa.text(
                "(task_type::text = ANY (ARRAY['initial_analysis'::character "
                "varying, 'reanalysis'::character varying]::text[])) AND po_item_id "
                "IS NULL OR task_type::text = 'po_monitoring'::text AND po_item_id "
                "IS NOT NULL"
            ),
            name=conv("ck_task_monitor_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["purchase_requests.request_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_analysis_tasks_request_id"),
        ),
        sa.ForeignKeyConstraint(
            ["po_item_id", "request_id"],
            ["purchase_order_items.po_item_id", "purchase_order_items.request_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_task_po_request"),
        ),
        sa.PrimaryKeyConstraint("task_id", name=conv("analysis_tasks_pkey")),
        sa.UniqueConstraint("idempotency_key", name=conv("analysis_tasks_idempotency_key_key")),
        comment=(
            "C06 | analysis_tasks [P0] - AI phân tích PR hoặc theo dõi PO theo "
            "snapshot. Input snapshot và cutoff bất biến. Task po_monitoring "
            "gắn một PO item, không sinh proposal. request_id luôn có FK trực "
            "tiếp kể cả khi po_item_id NULL."
        ),
    )


sa.Index(
    "uq_task_monitor_scope",
    AnalysisTask.__table__.c["task_id"],
    AnalysisTask.__table__.c["request_id"],
    AnalysisTask.__table__.c["po_item_id"],
    unique=True,
)


sa.Index(
    "uq_task_request",
    AnalysisTask.__table__.c["task_id"],
    AnalysisTask.__table__.c["request_id"],
    unique=True,
)


sa.Index(
    "uq_task_revision_scope",
    AnalysisTask.__table__.c["task_id"],
    AnalysisTask.__table__.c["request_id"],
    AnalysisTask.__table__.c["input_revision"],
    unique=True,
)


class RiskPrediction(Base):
    __table__ = sa.Table(
        "risk_predictions",
        Base.metadata,
        sa.Column("prediction_id", sa.UUID(), nullable=False, comment="ID lần dự đoán | PK, NN"),
        sa.Column(
            "task_id",
            sa.UUID(),
            nullable=True,
            comment=(
                "-> analysis_tasks. Nếu có task thì bắt buộc có PR/item/revision "
                "cùng task; chat độc lập task=NULL. | FK, NULL"
            ),
        ),
        sa.Column(
            "request_item_id",
            sa.UUID(),
            nullable=True,
            comment=(
                "-> PR item. request_item_id và request_id cùng NULL hoặc cùng có; "
                "ghép FK để bảo đảm cùng PR. | FK, NULL"
            ),
        ),
        sa.Column(
            "request_id",
            sa.UUID(),
            nullable=True,
            comment=(
                "PR của request_item_id và task_id. NULL chỉ khi kịch bản độc lập "
                "không gắn PR. | FK, NULL theo scope"
            ),
        ),
        sa.Column(
            "input_revision",
            sa.INTEGER(),
            nullable=True,
            comment=(
                "Revision đầu vào; task có mặt thì khớp "
                "analysis_tasks.input_revision. | NULL theo scope; >0 nếu có PR"
            ),
        ),
        sa.Column(
            "source_type",
            sa.VARCHAR(length=30),
            nullable=False,
            comment=(
                "pr_initial / pr_reanalysis / chat_ad_hoc / comparison / "
                "proposal_selection / po_monitoring / historical_import. | NN, "
                "CHECK"
            ),
        ),
        sa.Column(
            "prediction_stage",
            sa.VARCHAR(length=24),
            nullable=False,
            comment=(
                "PR_CREATED / PO_MONITORING; phải bằng "
                "feature_snapshot.x.prediction_stage. | NN, CHECK"
            ),
        ),
        sa.Column(
            "po_item_id",
            sa.UUID(),
            nullable=True,
            comment=(
                "NULL ở PR_CREATED; bắt buộc ở PO_MONITORING, cùng "
                "request_id/request_item_id với PO item. | FK ghép, NULL theo stage"
            ),
        ),
        sa.Column(
            "idempotency_key",
            sa.VARCHAR(length=180),
            nullable=False,
            comment=(
                "Khóa event/task + stage + scenario "
                "supplier/product/quantity/deadline + cutoff; retry không sinh dự "
                "đoán logic mới. | UNIQUE, NN"
            ),
        ),
        sa.Column("supplier_id", sa.UUID(), nullable=False, comment="Supplier được score | FK, NN"),
        sa.Column("product_id", sa.UUID(), nullable=False, comment="Product được score | FK, NN"),
        sa.Column(
            "quantity",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment=(
                "Q gốc của phương án ở A hoặc ordered_quantity của PO item ở B; "
                "không thay bằng lượng chưa nhận. | NN, >0"
            ),
        ),
        sa.Column(
            "requested_delivery_date",
            sa.DATE(),
            nullable=False,
            comment="Hạn giao của kịch bản | NN",
        ),
        sa.Column(
            "feature_asof",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment=(
                "Mốc snapshot A hoặc B cố định theo event. Phân biệt với "
                "predicted_at là lúc worker ghi kết quả. | NN"
            ),
        ),
        sa.Column(
            "requested_lead_days",
            sa.INTEGER(),
            nullable=False,
            comment=(
                "Audit mirror of signed X.days_to_deadline; nonnegative at "
                "PR_CREATED, may be negative at PO_MONITORING."
            ),
        ),
        sa.Column(
            "feature_schema_version",
            sa.VARCHAR(length=80),
            nullable=False,
            comment=(
                "tabicl_supplier_fixed_v1: đúng 19 key X ở E, cùng schema cho A/B, "
                "không missing indicators. | NN"
            ),
        ),
        sa.Column(
            "preprocessing_version",
            sa.VARCHAR(length=80),
            nullable=False,
            comment=(
                "Phiên bản adapter, dtype, NULL->NaN và tính lịch sử; giữ cùng train/serve. | NN"
            ),
        ),
        sa.Column(
            "model_name",
            sa.VARCHAR(length=120),
            nullable=True,
            comment="TabICLv2; bắt buộc khi success. | NULL",
        ),
        sa.Column(
            "model_version",
            sa.VARCHAR(length=120),
            nullable=True,
            comment="tabicl-classifier-v2-20260212.ckpt; bắt buộc khi success. | NULL",
        ),
        sa.Column(
            "context_dataset_version",
            sa.VARCHAR(length=120),
            nullable=True,
            comment=(
                "Phiên bản context chung có các snapshot A/B và nhãn đã biết trước "
                "cutoff; khác checkpoint. Cùng PO item không nằm cả context và "
                "test. | NULL; NN khi success"
            ),
        ),
        sa.Column(
            "context_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Manifest: hash dữ liệu/checkpoint, label cutoff, split, cấu hình "
                "estimator, thư viện và fitted artifact. | NULL; object khi success"
            ),
        ),
        sa.Column(
            "risk_score",
            sa.NUMERIC(precision=7, scale=6),
            nullable=True,
            comment="Score; không gọi là xác suất nếu chưa hiệu chuẩn | NULL, 0..1",
        ),
        sa.Column(
            "risk_threshold",
            sa.NUMERIC(precision=7, scale=6),
            nullable=True,
            comment="Ngưỡng tại lần chạy; bắt buộc khi success | NULL, 0..1",
        ),
        sa.Column(
            "risk_level",
            sa.VARCHAR(length=25),
            nullable=True,
            comment="low / high; chỉ khi score hợp lệ | NULL, CHECK",
        ),
        sa.Column(
            "feature_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "JSONB gồm x đủ 19 key, metadata "
                "q_ref/unit/deadline/asof/history/calendar/source freshness và "
                "provenance. NULL trong x được giữ; không đưa y hoặc actual "
                "completion vào x. | NN, object"
            ),
        ),
        sa.Column(
            "status",
            sa.VARCHAR(length=24),
            nullable=False,
            comment=(
                "success / insufficient_data / failed / snapshot_only. "
                "snapshot_only chỉ cho historical_import có nguồn xác thực. | NN, "
                "CHECK"
            ),
        ),
        sa.Column(
            "error_code",
            sa.VARCHAR(length=80),
            nullable=True,
            comment="Mã lỗi khi không thành công | NULL",
        ),
        sa.Column("error_message", sa.TEXT(), nullable=True, comment="Lỗi/thiếu dữ liệu | NULL"),
        sa.Column(
            "predicted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment=(
                "Mốc ghi kết quả/snapshot; có thể sau feature_asof. Không dùng làm "
                "mốc xây lại X. | NN"
            ),
        ),
        sa.CheckConstraint(
            sa.text(
                "feature_snapshot ? 'x'::text AND jsonb_typeof(feature_snapshot -> "
                "'x'::text) = 'object'::text AND (feature_snapshot -> 'x'::text) ?& "
                "ARRAY['prediction_stage'::text, 'supplier_id'::text, "
                "'product_category'::text, 'order_size_ratio'::text, "
                "'days_to_deadline'::text, 'non_working_days_to_deadline'::text, "
                "'hist_lead_p50_days'::text, 'hist_lead_p90_days'::text, "
                "'hist_late_rate_smoothed'::text, 'hist_sample_count'::text, "
                "'other_open_order_count'::text, 'expected_issue_wait_days'::text, "
                "'remaining_size_ratio'::text, 'elapsed_days_since_issue'::text, "
                "'confirmation_state'::text, 'confirmation_wait_days'::text, "
                "'received_fraction'::text, 'days_since_last_receipt'::text, "
                "'promise_margin_days'::text] AND ((feature_snapshot -> 'x'::text) "
                "- ARRAY['prediction_stage'::text, 'supplier_id'::text, "
                "'product_category'::text, 'order_size_ratio'::text, "
                "'days_to_deadline'::text, 'non_working_days_to_deadline'::text, "
                "'hist_lead_p50_days'::text, 'hist_lead_p90_days'::text, "
                "'hist_late_rate_smoothed'::text, 'hist_sample_count'::text, "
                "'other_open_order_count'::text, 'expected_issue_wait_days'::text, "
                "'remaining_size_ratio'::text, 'elapsed_days_since_issue'::text, "
                "'confirmation_state'::text, 'confirmation_wait_days'::text, "
                "'received_fraction'::text, 'days_since_last_receipt'::text, "
                "'promise_margin_days'::text]) = '{}'::jsonb AND "
                "(jsonb_typeof((feature_snapshot -> 'x'::text) -> "
                "'prediction_stage'::text) = ANY (ARRAY['null'::text, "
                "'string'::text])) AND (jsonb_typeof((feature_snapshot -> "
                "'x'::text) -> 'supplier_id'::text) = ANY (ARRAY['null'::text, "
                "'string'::text])) AND (jsonb_typeof((feature_snapshot -> "
                "'x'::text) -> 'product_category'::text) = ANY (ARRAY['null'::text, "
                "'string'::text])) AND (jsonb_typeof((feature_snapshot -> "
                "'x'::text) -> 'order_size_ratio'::text) = ANY (ARRAY['null'::text, "
                "'number'::text])) AND (jsonb_typeof((feature_snapshot -> "
                "'x'::text) -> 'days_to_deadline'::text) = ANY (ARRAY['null'::text, "
                "'number'::text])) AND (jsonb_typeof((feature_snapshot -> "
                "'x'::text) -> 'non_working_days_to_deadline'::text) = ANY "
                "(ARRAY['null'::text, 'number'::text])) AND "
                "(jsonb_typeof((feature_snapshot -> 'x'::text) -> "
                "'hist_lead_p50_days'::text) = ANY (ARRAY['null'::text, "
                "'number'::text])) AND (jsonb_typeof((feature_snapshot -> "
                "'x'::text) -> 'hist_lead_p90_days'::text) = ANY "
                "(ARRAY['null'::text, 'number'::text])) AND "
                "(jsonb_typeof((feature_snapshot -> 'x'::text) -> "
                "'hist_late_rate_smoothed'::text) = ANY (ARRAY['null'::text, "
                "'number'::text])) AND (jsonb_typeof((feature_snapshot -> "
                "'x'::text) -> 'hist_sample_count'::text) = ANY "
                "(ARRAY['null'::text, 'number'::text])) AND "
                "(jsonb_typeof((feature_snapshot -> 'x'::text) -> "
                "'other_open_order_count'::text) = ANY (ARRAY['null'::text, "
                "'number'::text])) AND (jsonb_typeof((feature_snapshot -> "
                "'x'::text) -> 'expected_issue_wait_days'::text) = ANY "
                "(ARRAY['null'::text, 'number'::text])) AND "
                "(jsonb_typeof((feature_snapshot -> 'x'::text) -> "
                "'remaining_size_ratio'::text) = ANY (ARRAY['null'::text, "
                "'number'::text])) AND (jsonb_typeof((feature_snapshot -> "
                "'x'::text) -> 'elapsed_days_since_issue'::text) = ANY "
                "(ARRAY['null'::text, 'number'::text])) AND "
                "(jsonb_typeof((feature_snapshot -> 'x'::text) -> "
                "'confirmation_state'::text) = ANY (ARRAY['null'::text, "
                "'string'::text])) AND (jsonb_typeof((feature_snapshot -> "
                "'x'::text) -> 'confirmation_wait_days'::text) = ANY "
                "(ARRAY['null'::text, 'number'::text])) AND "
                "(jsonb_typeof((feature_snapshot -> 'x'::text) -> "
                "'received_fraction'::text) = ANY (ARRAY['null'::text, "
                "'number'::text])) AND (jsonb_typeof((feature_snapshot -> "
                "'x'::text) -> 'days_since_last_receipt'::text) = ANY "
                "(ARRAY['null'::text, 'number'::text])) AND "
                "(jsonb_typeof((feature_snapshot -> 'x'::text) -> "
                "'promise_margin_days'::text) = ANY (ARRAY['null'::text, "
                "'number'::text])) AND (((feature_snapshot -> 'x'::text) ->> "
                "'prediction_stage'::text) = ANY (ARRAY['PR_CREATED'::text, "
                "'PO_MONITORING'::text])) IS TRUE"
            ),
            name=conv("ck_fixed_x_shape"),
        ),
        sa.CheckConstraint(
            sa.text("prediction_stage::text <> 'PR_CREATED'::text OR requested_lead_days >= 0"),
            name=conv("ck_prediction_initial_lead_nonnegative"),
        ),
        sa.CheckConstraint(
            sa.text("(request_item_id IS NULL) = (request_id IS NULL)"),
            name=conv("ck_prediction_item_match_full"),
        ),
        sa.CheckConstraint(
            sa.text(
                "requested_lead_days = (requested_delivery_date - (feature_asof AT "
                "TIME ZONE 'Asia/Ho_Chi_Minh'::text)::date)"
            ),
            name=conv("ck_prediction_lead_anchor"),
        ),
        sa.CheckConstraint(
            sa.text(
                "request_item_id IS NULL AND request_id IS NULL AND input_revision "
                "IS NULL AND task_id IS NULL OR request_item_id IS NOT NULL AND "
                "request_id IS NOT NULL AND input_revision IS NOT NULL AND "
                "input_revision > 0"
            ),
            name=conv("ck_prediction_scope"),
        ),
        sa.CheckConstraint(
            sa.text(
                "prediction_stage::text = 'PR_CREATED'::text AND po_item_id IS NULL "
                "OR prediction_stage::text = 'PO_MONITORING'::text AND po_item_id "
                "IS NOT NULL AND request_id IS NOT NULL AND request_item_id IS NOT "
                "NULL AND input_revision IS NOT NULL"
            ),
            name=conv("ck_prediction_stage_scope"),
        ),
        sa.CheckConstraint(
            sa.text(
                "((feature_snapshot -> 'x'::text) ->> 'prediction_stage'::text) = "
                "prediction_stage::text"
            ),
            name=conv("ck_prediction_stage_snapshot"),
        ),
        sa.CheckConstraint(
            sa.text("jsonb_typeof(feature_snapshot) = 'object'::text"),
            name=conv("ck_risk_predictions_feature_snapshot_object"),
        ),
        sa.CheckConstraint(
            sa.text("input_revision > 0"), name=conv("ck_risk_predictions_input_revision_positive")
        ),
        sa.CheckConstraint(
            sa.text(
                "prediction_stage::text = ANY (ARRAY['PR_CREATED'::character "
                "varying, 'PO_MONITORING'::character varying]::text[])"
            ),
            name=conv("ck_risk_predictions_prediction_stage_domain"),
        ),
        sa.CheckConstraint(
            sa.text("quantity > 0::numeric"), name=conv("ck_risk_predictions_quantity_positive")
        ),
        sa.CheckConstraint(
            sa.text(
                "risk_level::text = ANY (ARRAY['low'::character varying, "
                "'high'::character varying]::text[])"
            ),
            name=conv("ck_risk_predictions_risk_level_domain"),
        ),
        sa.CheckConstraint(
            sa.text("risk_score >= 0::numeric AND risk_score <= 1::numeric"),
            name=conv("ck_risk_predictions_risk_score_range"),
        ),
        sa.CheckConstraint(
            sa.text("risk_threshold >= 0::numeric AND risk_threshold <= 1::numeric"),
            name=conv("ck_risk_predictions_risk_threshold_range"),
        ),
        sa.CheckConstraint(
            sa.text(
                "source_type::text = ANY (ARRAY['pr_initial'::character varying, "
                "'pr_reanalysis'::character varying, 'chat_ad_hoc'::character "
                "varying, 'comparison'::character varying, "
                "'proposal_selection'::character varying, "
                "'po_monitoring'::character varying, 'historical_import'::character "
                "varying]::text[])"
            ),
            name=conv("ck_risk_predictions_source_type_domain"),
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['success'::character varying, "
                "'insufficient_data'::character varying, 'failed'::character "
                "varying, 'snapshot_only'::character varying]::text[])"
            ),
            name=conv("ck_risk_predictions_status_domain"),
        ),
        sa.ForeignKeyConstraint(
            ["request_item_id", "request_id"],
            ["purchase_request_items.request_item_id", "purchase_request_items.request_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_prediction_item_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "request_id", "po_item_id"],
            ["analysis_tasks.task_id", "analysis_tasks.request_id", "analysis_tasks.po_item_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_prediction_monitor_task"),
        ),
        sa.ForeignKeyConstraint(
            ["po_item_id", "request_id", "request_item_id"],
            [
                "purchase_order_items.po_item_id",
                "purchase_order_items.request_id",
                "purchase_order_items.request_item_id",
            ],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_prediction_po_item_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "request_id", "input_revision"],
            [
                "analysis_tasks.task_id",
                "analysis_tasks.request_id",
                "analysis_tasks.input_revision",
            ],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_prediction_task_revision"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.product_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_risk_predictions_product_id"),
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.supplier_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_risk_predictions_supplier_id"),
        ),
        sa.PrimaryKeyConstraint("prediction_id", name=conv("risk_predictions_pkey")),
        sa.UniqueConstraint("idempotency_key", name=conv("risk_predictions_idempotency_key_key")),
        comment=(
            "C07 | risk_predictions [P0] - Snapshot 19 thuộc tính cố định A B "
            "và kết quả TabICLv2. Một hợp đồng 19 X cho PR_CREATED và "
            "PO_MONITORING; x luôn đủ key, giá trị chưa biết là JSON null, "
            "không thêm cờ thiếu dữ liệu. Không dùng y hoặc thời điểm hoàn "
            "thành thực tế làm X. Điều kiện success, provenance, đồng nhất "
            "x/raw và numeric domains được adapter kiểm tra. CHECK "
            "ck_fixed_x_shape dùng toán tử JSONB trực tiếp, không tạo hàm "
            "riêng."
        ),
    )


sa.Index(
    "ix_prediction_po_asof",
    RiskPrediction.__table__.c["po_item_id"],
    RiskPrediction.__table__.c["feature_asof"],
    unique=False,
)


sa.Index(
    "uq_prediction_candidate_scope",
    RiskPrediction.__table__.c["prediction_id"],
    RiskPrediction.__table__.c["task_id"],
    RiskPrediction.__table__.c["request_item_id"],
    RiskPrediction.__table__.c["request_id"],
    RiskPrediction.__table__.c["supplier_id"],
    unique=True,
)


sa.Index(
    "uq_prediction_item_scope",
    RiskPrediction.__table__.c["prediction_id"],
    RiskPrediction.__table__.c["request_item_id"],
    RiskPrediction.__table__.c["request_id"],
    unique=True,
)


sa.Index(
    "uq_prediction_task_scope",
    RiskPrediction.__table__.c["prediction_id"],
    RiskPrediction.__table__.c["task_id"],
    RiskPrediction.__table__.c["request_id"],
    unique=True,
)


class RiskAnalysisReport(Base):
    __table__ = sa.Table(
        "risk_analysis_reports",
        Base.metadata,
        sa.Column("report_id", sa.UUID(), nullable=False, comment="ID báo cáo | PK, NN"),
        sa.Column("task_id", sa.UUID(), nullable=False, comment="-> analysis_tasks | FK, NN"),
        sa.Column("request_id", sa.UUID(), nullable=False, comment="-> purchase_requests | FK, NN"),
        sa.Column(
            "primary_prediction_id",
            sa.UUID(),
            nullable=True,
            comment=(
                "-> risk_predictions theo prediction_id + task_id + request_id. "
                "Không được lấy prediction của task khác. | FK, NULL"
            ),
        ),
        sa.Column(
            "risk_level", sa.VARCHAR(length=25), nullable=True, comment="Mức rủi ro tổng hợp | NULL"
        ),
        sa.Column("summary", sa.TEXT(), nullable=False, comment="Tóm tắt hiển thị | NN"),
        sa.Column(
            "report_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "decision_status ready / needs_review / failed; "
                "selection_policy_version, mục tiêu/điều kiện, ứng viên/chọn phương "
                "án và document_refs từ kho Markdown hiện có. Lưu nguồn theo "
                "version/chunk, không nhân bản nội dung document. | NN, object"
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Không ghi đè report cũ | NN",
        ),
        sa.CheckConstraint(
            sa.text("jsonb_typeof(report_data) = 'object'::text"),
            name=conv("ck_risk_analysis_reports_report_data_object"),
        ),
        sa.ForeignKeyConstraint(
            ["primary_prediction_id", "task_id", "request_id"],
            [
                "risk_predictions.prediction_id",
                "risk_predictions.task_id",
                "risk_predictions.request_id",
            ],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_report_primary_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "request_id"],
            ["analysis_tasks.task_id", "analysis_tasks.request_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_report_task_scope"),
        ),
        sa.PrimaryKeyConstraint("report_id", name=conv("risk_analysis_reports_pkey")),
        comment=(
            "C08 | risk_analysis_reports [P0] - Báo cáo theo task; các phương "
            "án cụ thể lưu bảng candidate. report_data chứa decision_status, "
            "policy version và document_refs/citations tới kho Markdown hiện "
            "có; không nhân bản bảng document. Các quan hệ ghép giữ report cùng "
            "task và PR."
        ),
    )


sa.Index(
    "uq_report_request",
    RiskAnalysisReport.__table__.c["report_id"],
    RiskAnalysisReport.__table__.c["request_id"],
    unique=True,
)


sa.Index(
    "uq_report_task_scope",
    RiskAnalysisReport.__table__.c["report_id"],
    RiskAnalysisReport.__table__.c["task_id"],
    RiskAnalysisReport.__table__.c["request_id"],
    unique=True,
)


class AnalysisSupplierCandidate(Base):
    __table__ = sa.Table(
        "analysis_supplier_candidates",
        Base.metadata,
        sa.Column("candidate_id", sa.UUID(), nullable=False, comment="ID ứng viên | PK, NN"),
        sa.Column(
            "report_id", sa.UUID(), nullable=False, comment="-> risk_analysis_reports | FK, NN"
        ),
        sa.Column(
            "task_id",
            sa.UUID(),
            nullable=False,
            comment=("Task của report; prediction nếu có phải thuộc cùng task. | FK ghép, NN"),
        ),
        sa.Column(
            "request_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "PR của report và request_item_id; không chỉ FK từng ID riêng lẻ. | FK ghép, NN"
            ),
        ),
        sa.Column(
            "request_item_id", sa.UUID(), nullable=False, comment="Dòng cần tìm phương án | FK, NN"
        ),
        sa.Column("supplier_id", sa.UUID(), nullable=False, comment="Supplier ứng viên | FK, NN"),
        sa.Column(
            "prediction_id",
            sa.UUID(),
            nullable=True,
            comment=(
                "-> risk_predictions; cùng task/request/item/supplier với "
                "candidate. NULL nếu chưa có kịch bản chấm. | FK, NULL"
            ),
        ),
        sa.Column(
            "unit_price_snapshot",
            sa.NUMERIC(precision=18, scale=4),
            nullable=True,
            comment="Giá dùng lúc so sánh | NULL, >=0",
        ),
        sa.Column("currency", sa.CHAR(length=3), nullable=True, comment="Tiền tệ của giá | NULL"),
        sa.Column(
            "rank",
            sa.INTEGER(),
            nullable=True,
            comment="Hạng theo từng report/item nếu có | NULL, >0",
        ),
        sa.Column(
            "is_eligible",
            sa.BOOLEAN(),
            nullable=False,
            comment="Có qua bộ lọc nghiệp vụ không | NN",
        ),
        sa.Column(
            "reason", sa.TEXT(), nullable=True, comment="Lý do, thiếu dữ liệu, cảnh báo | NULL"
        ),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Document ID/version/chunk/source URI/checksum và scope quyền từ "
                "kho Markdown PostgreSQL hiện có; reference tích hợp, không FK tới "
                "bảng document trong schema này. | NULL"
            ),
        ),
        sa.CheckConstraint(
            sa.text("rank > 0"), name=conv("ck_analysis_supplier_candidates_rank_positive")
        ),
        sa.CheckConstraint(
            sa.text("unit_price_snapshot >= 0::numeric"),
            name=conv("ck_analysis_supplier_candidates_unit_price_snapshot_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.supplier_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_analysis_supplier_candidates_supplier_id"),
        ),
        sa.ForeignKeyConstraint(
            ["request_item_id", "request_id"],
            ["purchase_request_items.request_item_id", "purchase_request_items.request_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_candidate_item_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id", "task_id", "request_item_id", "request_id", "supplier_id"],
            [
                "risk_predictions.prediction_id",
                "risk_predictions.task_id",
                "risk_predictions.request_item_id",
                "risk_predictions.request_id",
                "risk_predictions.supplier_id",
            ],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_candidate_prediction_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "task_id", "request_id"],
            [
                "risk_analysis_reports.report_id",
                "risk_analysis_reports.task_id",
                "risk_analysis_reports.request_id",
            ],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_candidate_report_scope"),
        ),
        sa.PrimaryKeyConstraint("candidate_id", name=conv("analysis_supplier_candidates_pkey")),
        comment=(
            "C09 | analysis_supplier_candidates [P0] - Ứng viên và bằng chứng "
            "để AI tự chọn phương án. Prediction nếu có phải cùng "
            "task/PR/item/supplier. supplier_id vẫn cần FK trực tiếp khi "
            "prediction_id NULL."
        ),
    )


class Proposal(Base):
    __table__ = sa.Table(
        "proposals",
        Base.metadata,
        sa.Column("proposal_id", sa.UUID(), nullable=False, comment="ID phiếu | PK, NN"),
        sa.Column(
            "proposal_code",
            sa.VARCHAR(length=50),
            nullable=False,
            comment="Mã đề xuất | UNIQUE, NN",
        ),
        sa.Column("request_id", sa.UUID(), nullable=False, comment="-> purchase_requests | FK, NN"),
        sa.Column(
            "version_no",
            sa.INTEGER(),
            nullable=False,
            comment=(
                "UNIQUE(request_id, version_no); cấp phiên bản trong transaction khóa PR. | NN, >0"
            ),
        ),
        sa.Column(
            "input_revision",
            sa.INTEGER(),
            nullable=False,
            comment=(
                "Revision PR được đề xuất; phải khớp hiện hành khi submit/approve/issue. | NN, >0"
            ),
        ),
        sa.Column(
            "supersedes_proposal_id",
            sa.UUID(),
            nullable=True,
            comment=(
                "Phiên bản trước cùng request_id; không tự tham chiếu; version_no "
                "tăng. | FK self, NULL"
            ),
        ),
        sa.Column(
            "is_current",
            sa.BOOLEAN(),
            nullable=False,
            server_default=sa.text("true"),
            comment=(
                "Chỉ một proposal hiện hành/PR bằng partial unique index. approved "
                "bắt buộc current. | NN, DEFAULT TRUE"
            ),
        ),
        sa.Column(
            "analysis_report_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "-> report của generation_task_id và cùng request_id. Report phải "
                "chứng minh phương án đủ điều kiện trước khi AI gửi. | FK ghép, NN"
            ),
        ),
        sa.Column(
            "generation_task_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "Task AI tạo proposal; cùng request_id/input_revision và đúng "
                "report. Một task tạo tối đa một proposal. | FK ghép, NN, UNIQUE"
            ),
        ),
        sa.Column(
            "selection_policy_version",
            sa.VARCHAR(length=80),
            nullable=False,
            comment=(
                "Phiên bản chính sách chọn phương án từ ứng viên hợp lệ; lưu trọng "
                "số/điều kiện thực tế trong report_data. | NN"
            ),
        ),
        sa.Column(
            "workflow_instance_id",
            sa.VARCHAR(length=160),
            nullable=True,
            comment=(
                "ID luồng duyệt do workflow trả về khi xử lý outbox; callback phải "
                "khớp proposal/version. | NULL"
            ),
        ),
        sa.Column("reason", sa.TEXT(), nullable=True, comment="Lý do chung | NULL"),
        sa.Column(
            "status",
            sa.VARCHAR(length=30),
            nullable=False,
            comment=(
                "pending / approved / rejected / superseded / cancelled. Proposal "
                "mới do AI tạo ở pending và tự gửi duyệt; không có draft do người "
                "lập proposal. | NN, CHECK"
            ),
        ),
        sa.Column(
            "created_by",
            sa.UUID(),
            nullable=False,
            comment=(
                "-> users.user_id tài khoản service có AI_SERVICE. AI tạo/gửi "
                "proposal; không có role người lập proposal thủ công. | FK, NN"
            ),
        ),
        sa.Column(
            "submitted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment=(
                "Mốc AI tạo logical request gửi duyệt trong transaction "
                "proposal/items/outbox. Khác submitted_at của nhân viên trên PR; "
                "chưa khẳng định workflow đã nhận. | NN"
            ),
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Tạo phiếu | NN"
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['pending'::character varying, "
                "'approved'::character varying, 'rejected'::character varying, "
                "'superseded'::character varying, 'cancelled'::character "
                "varying]::text[])"
            ),
            name=conv("ck_auto_proposal_status"),
        ),
        sa.CheckConstraint(
            sa.text("status::text <> 'approved'::text OR is_current"),
            name=conv("ck_proposal_approved_current"),
        ),
        sa.CheckConstraint(
            sa.text("supersedes_proposal_id IS NULL OR supersedes_proposal_id <> proposal_id"),
            name=conv("ck_proposal_not_self"),
        ),
        sa.CheckConstraint(
            sa.text("version_no > 0 AND input_revision > 0"),
            name=conv("ck_proposal_version_positive"),
        ),
        sa.CheckConstraint(
            sa.text("input_revision > 0"), name=conv("ck_proposals_input_revision_positive")
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['pending'::character varying, "
                "'approved'::character varying, 'rejected'::character varying, "
                "'superseded'::character varying, 'cancelled'::character "
                "varying]::text[])"
            ),
            name=conv("ck_proposals_status_domain"),
        ),
        sa.CheckConstraint(
            sa.text("version_no > 0"), name=conv("ck_proposals_version_no_positive")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.user_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_proposal_creator_user"),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_report_id", "generation_task_id", "request_id"],
            [
                "risk_analysis_reports.report_id",
                "risk_analysis_reports.task_id",
                "risk_analysis_reports.request_id",
            ],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_proposal_generation_report"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_task_id", "request_id", "input_revision"],
            [
                "analysis_tasks.task_id",
                "analysis_tasks.request_id",
                "analysis_tasks.input_revision",
            ],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_proposal_generation_revision"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_proposal_id", "request_id"],
            ["proposals.proposal_id", "proposals.request_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_proposal_predecessor_scope"),
        ),
        sa.PrimaryKeyConstraint("proposal_id", name=conv("proposals_pkey")),
        sa.UniqueConstraint("proposal_code", name=conv("proposals_proposal_code_key")),
        comment=(
            "C10 | proposals [P0] - AI tự tạo và gửi proposal phiên bản hiện "
            "hành. AI_SERVICE tự tạo proposal pending và gửi duyệt qua outbox; "
            "không có người lập proposal. Khóa PR, kiểm tra current revision, "
            "report ready và task initial/reanalysis. Chỉ một proposal "
            "is_current cho mỗi PR: áp dụng partial UNIQUE ghi ở cuối file, "
            "không dùng UNIQUE(request_id) toàn bảng."
        ),
    )


sa.Index(
    "uq_current_proposal_per_pr",
    Proposal.__table__.c["request_id"],
    unique=True,
    postgresql_where=sa.text("is_current"),
)


sa.Index("uq_proposal_generation_task", Proposal.__table__.c["generation_task_id"], unique=True)


sa.Index(
    "uq_proposal_request",
    Proposal.__table__.c["proposal_id"],
    Proposal.__table__.c["request_id"],
    unique=True,
)


sa.Index(
    "uq_proposal_version",
    Proposal.__table__.c["request_id"],
    Proposal.__table__.c["version_no"],
    unique=True,
)


class ProposalItem(Base):
    __table__ = sa.Table(
        "proposal_items",
        Base.metadata,
        sa.Column("proposal_item_id", sa.UUID(), nullable=False, comment="ID dòng phiếu | PK, NN"),
        sa.Column("proposal_id", sa.UUID(), nullable=False, comment="-> proposals | FK, NN"),
        sa.Column(
            "request_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "Scope từ proposal; phải cùng PR với request_item_id và "
                "prediction_id. | FK ghép, NN"
            ),
        ),
        sa.Column(
            "request_item_id",
            sa.UUID(),
            nullable=False,
            comment="-> purchase_request_items | FK, NN",
        ),
        sa.Column(
            "selected_supplier_id",
            sa.UUID(),
            nullable=False,
            comment="-> suppliers; có thể khác giữa các dòng | FK, NN",
        ),
        sa.Column(
            "prediction_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "Prediction success PR_CREATED cùng PR/item/revision và "
                "supplier/product/quantity/deadline của phương án. Không dùng "
                "prediction monitoring hoặc snapshot_only để lập proposal. | FK, NN"
            ),
        ),
        sa.Column(
            "quantity",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment=(
                "Số lượng cho supplier cụ thể. Đổi quantity phải tạo "
                "snapshot/prediction mới trước submit proposal. | NN, >0"
            ),
        ),
        sa.Column(
            "unit_price_snapshot",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment="Đơn giá lúc duyệt | NN, >=0",
        ),
        sa.Column("currency", sa.CHAR(length=3), nullable=False, comment="Tiền tệ | NN"),
        sa.Column(
            "proposed_delivery_date",
            sa.DATE(),
            nullable=False,
            comment=(
                "Hạn cần đáp ứng; phải bằng deadline prediction được gắn, không "
                "thay bằng ngày supplier hứa. | NN"
            ),
        ),
        sa.Column("reason", sa.TEXT(), nullable=True, comment="Lý do tại dòng | NULL"),
        sa.CheckConstraint(
            sa.text("quantity > 0::numeric"), name=conv("ck_proposal_items_quantity_positive")
        ),
        sa.CheckConstraint(
            sa.text("unit_price_snapshot >= 0::numeric"),
            name=conv("ck_proposal_items_unit_price_snapshot_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id", "request_id"],
            ["proposals.proposal_id", "proposals.request_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_proposal_item_header_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["request_item_id", "request_id"],
            ["purchase_request_items.request_item_id", "purchase_request_items.request_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_proposal_item_pr_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id", "request_item_id", "request_id"],
            [
                "risk_predictions.prediction_id",
                "risk_predictions.request_item_id",
                "risk_predictions.request_id",
            ],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_proposal_item_prediction_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["selected_supplier_id"],
            ["suppliers.supplier_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_proposal_items_selected_supplier_id"),
        ),
        sa.PrimaryKeyConstraint("proposal_item_id", name=conv("proposal_items_pkey")),
        comment=(
            "C11 | proposal_items [P0] - Dòng được chọn và prediction cùng hồ "
            "sơ. Chỉ dùng prediction success PR_CREATED, cùng PR/item/revision "
            "và supplier/product/quantity/deadline. Quan hệ FK không tự kiểm "
            "tra toàn bộ điều kiện nghiệp vụ này."
        ),
    )


sa.Index(
    "uq_proposal_item_full_scope",
    ProposalItem.__table__.c["proposal_item_id"],
    ProposalItem.__table__.c["proposal_id"],
    ProposalItem.__table__.c["request_id"],
    ProposalItem.__table__.c["request_item_id"],
    unique=True,
)


sa.Index(
    "uq_proposal_item_scope",
    ProposalItem.__table__.c["proposal_item_id"],
    ProposalItem.__table__.c["proposal_id"],
    ProposalItem.__table__.c["request_id"],
    unique=True,
)


class ApprovalHistory(Base):
    __table__ = sa.Table(
        "approval_history",
        Base.metadata,
        sa.Column("approval_id", sa.UUID(), nullable=False, comment="ID quyết định | PK, NN"),
        sa.Column("proposal_id", sa.UUID(), nullable=False, comment="-> proposals | FK, NN"),
        sa.Column(
            "workflow_task_id",
            sa.VARCHAR(length=120),
            nullable=True,
            comment="Task từ workflow bên ngoài | NULL",
        ),
        sa.Column(
            "approver_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "-> users.user_id người active human có APPROVER trong phạm vi "
                "phòng ban của PR. Quyết định còn phải đúng "
                "workflow/proposal/version. | FK, NN"
            ),
        ),
        sa.Column(
            "decision",
            sa.VARCHAR(length=20),
            nullable=False,
            comment="approved / rejected | NN, CHECK",
        ),
        sa.Column("reason", sa.TEXT(), nullable=True, comment="Bắt buộc nếu rejected | NULL"),
        sa.Column(
            "decided_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Thời gian quyết định | NN",
        ),
        sa.CheckConstraint(
            sa.text(
                "decision::text = ANY (ARRAY['approved'::character varying, "
                "'rejected'::character varying]::text[])"
            ),
            name=conv("ck_approval_history_decision_domain"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.proposal_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_approval_history_proposal_id"),
        ),
        sa.ForeignKeyConstraint(
            ["approver_id"],
            ["users.user_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_approval_user"),
        ),
        sa.PrimaryKeyConstraint("approval_id", name=conv("approval_history_pkey")),
        comment=(
            "C12 | approval_history [P0] - Lịch sử quyết định do workflow quản "
            "lý. Active human có APPROVER đúng department grant và được "
            "workflow giao mới được duyệt current proposal/version. ADMIN, "
            "AI_SERVICE và PO_SERVICE không tự có quyền duyệt. Rejected cần lý "
            "do theo workflow."
        ),
    )


class PurchaseOrder(Base):
    __table__ = sa.Table(
        "purchase_orders",
        Base.metadata,
        sa.Column("purchase_order_id", sa.UUID(), nullable=False, comment="ID PO | PK, NN"),
        sa.Column("po_number", sa.VARCHAR(length=50), nullable=False, comment="Mã PO | UNIQUE, NN"),
        sa.Column("request_id", sa.UUID(), nullable=False, comment="-> purchase_requests | FK, NN"),
        sa.Column(
            "proposal_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "-> proposals cùng request_id; chỉ current+approved+revision hợp lệ "
                "mới issue. UNIQUE(proposal_id,supplier_id,currency). | FK, NN"
            ),
        ),
        sa.Column("supplier_id", sa.UUID(), nullable=False, comment="-> suppliers | FK, NN"),
        sa.Column(
            "order_date",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Mốc phát hành PO (khác submitted_at PR) | NN",
        ),
        sa.Column(
            "status",
            sa.VARCHAR(length=35),
            nullable=False,
            comment=(
                "issued / waiting_confirmation / confirmed / rejected / "
                "partially_received / delivered / cancelled. cancelled chỉ sau hủy "
                "hợp lệ và không có receipts trong MVP. | NN, CHECK"
            ),
        ),
        sa.Column(
            "currency",
            sa.CHAR(length=3),
            nullable=False,
            comment=(
                "Một ISO 4217 trên PO. Tách PO theo supplier_id + currency; MVP "
                "không tự quy đổi FX. | NN"
            ),
        ),
        sa.Column(
            "total_amount",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment="Tổng snapshot của PO items | NN, >=0",
        ),
        sa.Column(
            "pdf_file_id",
            sa.VARCHAR(length=255),
            nullable=True,
            comment="Tham chiếu PDF đã tạo | NULL",
        ),
        sa.Column(
            "created_by",
            sa.UUID(),
            nullable=False,
            comment=(
                "-> users.user_id tài khoản active service có PO_SERVICE; chỉ phát "
                "từ proposal đã được duyệt hợp lệ. | FK, NN"
            ),
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Ngày tạo | NN"
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Ngày cập nhật | NN"
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['issued'::character varying, "
                "'waiting_confirmation'::character varying, 'confirmed'::character "
                "varying, 'rejected'::character varying, "
                "'partially_received'::character varying, 'delivered'::character "
                "varying, 'cancelled'::character varying]::text[])"
            ),
            name=conv("ck_purchase_orders_status_domain"),
        ),
        sa.CheckConstraint(
            sa.text("total_amount >= 0::numeric"),
            name=conv("ck_purchase_orders_total_amount_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.user_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_po_creator_user"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id", "request_id"],
            ["proposals.proposal_id", "proposals.request_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_po_proposal_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.supplier_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_purchase_orders_supplier_id"),
        ),
        sa.PrimaryKeyConstraint("purchase_order_id", name=conv("purchase_orders_pkey")),
        sa.UniqueConstraint("po_number", name=conv("purchase_orders_po_number_key")),
        comment=(
            "C13 | purchase_orders [P0] - PO theo supplier và currency của "
            "proposal. PO_SERVICE phát PO từ proposal current+approved đúng "
            "revision, nhóm theo supplier+currency. Transaction khóa PR và cộng "
            "số lượng cam kết trên mọi PO chưa hủy qua các proposal versions. "
            "MVP chỉ hủy hợp lệ khi chưa có receipts."
        ),
    )


sa.Index(
    "uq_po_issue_group",
    PurchaseOrder.__table__.c["proposal_id"],
    PurchaseOrder.__table__.c["supplier_id"],
    PurchaseOrder.__table__.c["currency"],
    unique=True,
)


sa.Index(
    "uq_po_scope",
    PurchaseOrder.__table__.c["purchase_order_id"],
    PurchaseOrder.__table__.c["proposal_id"],
    PurchaseOrder.__table__.c["request_id"],
    unique=True,
)


class PurchaseOrderItem(Base):
    __table__ = sa.Table(
        "purchase_order_items",
        Base.metadata,
        sa.Column("po_item_id", sa.UUID(), nullable=False, comment="ID dòng PO | PK, NN"),
        sa.Column(
            "purchase_order_id", sa.UUID(), nullable=False, comment="-> purchase_orders | FK, NN"
        ),
        sa.Column(
            "proposal_item_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "-> proposal_items; lấy prediction_id qua dòng này. Một PO item là "
                "một mẫu outcome training. | FK, NN, UNIQUE"
            ),
        ),
        sa.Column(
            "proposal_id",
            sa.UUID(),
            nullable=False,
            comment="Cùng proposal với PO header và proposal_item_id. | FK ghép, NN",
        ),
        sa.Column(
            "request_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "Cùng PR với PO header và proposal item. Dùng scope ổn định cho "
                "kiểm tra lượng. | FK ghép, NN"
            ),
        ),
        sa.Column(
            "request_item_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "PR item nguồn; khớp cả proposal_item và request_id. Phục vụ FK "
                "monitoring và budget. | FK ghép, NN"
            ),
        ),
        sa.Column(
            "line_number",
            sa.INTEGER(),
            nullable=False,
            comment="UNIQUE(purchase_order_id,line_number) | NN, >0",
        ),
        sa.Column(
            "product_id",
            sa.UUID(),
            nullable=False,
            comment="-> products; phải trùng sản phẩm của proposal_item | FK, NN",
        ),
        sa.Column(
            "product_category_snapshot",
            sa.VARCHAR(length=120),
            nullable=False,
            comment=(
                "Category tại phát hành từ snapshot được duyệt; không lấy category "
                "hiện tại để sửa lịch sử. | NN"
            ),
        ),
        sa.Column(
            "ordered_quantity",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment=(
                "Số lượng đã issue; cộng trên mọi proposal/PO chưa cancelled của PR "
                "item không vượt lượng PR. | NN, >0"
            ),
        ),
        sa.Column(
            "unit",
            sa.VARCHAR(length=30),
            nullable=False,
            comment=(
                "Đơn vị Q đóng băng khi issue; q_ref của lịch sử phải cùng sản phẩm và đơn vị. | NN"
            ),
        ),
        sa.Column(
            "unit_price",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment="Giá snapshot từ phiếu đã duyệt | NN, >=0",
        ),
        sa.Column(
            "line_total",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment="Server tính quantity × price | NN, >=0",
        ),
        sa.Column(
            "item_status",
            sa.VARCHAR(length=35),
            nullable=False,
            comment=("open / confirmed / partially_received / completed / cancelled | NN, CHECK"),
        ),
        sa.CheckConstraint(
            sa.text(
                "item_status::text = ANY (ARRAY['open'::character varying, "
                "'confirmed'::character varying, 'partially_received'::character "
                "varying, 'completed'::character varying, 'cancelled'::character "
                "varying]::text[])"
            ),
            name=conv("ck_purchase_order_items_item_status_domain"),
        ),
        sa.CheckConstraint(
            sa.text("line_number > 0"), name=conv("ck_purchase_order_items_line_number_positive")
        ),
        sa.CheckConstraint(
            sa.text("line_total >= 0::numeric"),
            name=conv("ck_purchase_order_items_line_total_nonnegative"),
        ),
        sa.CheckConstraint(
            sa.text("ordered_quantity > 0::numeric"),
            name=conv("ck_purchase_order_items_ordered_quantity_positive"),
        ),
        sa.CheckConstraint(
            sa.text("unit_price >= 0::numeric"),
            name=conv("ck_purchase_order_items_unit_price_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id", "proposal_id", "request_id"],
            [
                "purchase_orders.purchase_order_id",
                "purchase_orders.proposal_id",
                "purchase_orders.request_id",
            ],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_po_item_header_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["request_item_id", "request_id"],
            ["purchase_request_items.request_item_id", "purchase_request_items.request_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_po_item_pr_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_item_id", "proposal_id", "request_id", "request_item_id"],
            [
                "proposal_items.proposal_item_id",
                "proposal_items.proposal_id",
                "proposal_items.request_id",
                "proposal_items.request_item_id",
            ],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_po_item_proposal_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.product_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_purchase_order_items_product_id"),
        ),
        sa.PrimaryKeyConstraint("po_item_id", name=conv("purchase_order_items_pkey")),
        sa.UniqueConstraint(
            "proposal_item_id", name=conv("purchase_order_items_proposal_item_id_key")
        ),
        comment=(
            "C14 | purchase_order_items [P0] - PO line; truy ngược PR qua "
            "proposal_item_id. Snapshot sản phẩm/category/unit/giá/lượng bất "
            "biến. Một proposal item chỉ phát một PO item. Budget lượng phải "
            "kiểm tra trong transaction."
        ),
    )


sa.Index(
    "uq_po_item_pr_scope",
    PurchaseOrderItem.__table__.c["po_item_id"],
    PurchaseOrderItem.__table__.c["request_id"],
    PurchaseOrderItem.__table__.c["request_item_id"],
    unique=True,
)


sa.Index(
    "uq_po_item_request",
    PurchaseOrderItem.__table__.c["po_item_id"],
    PurchaseOrderItem.__table__.c["request_id"],
    unique=True,
)


sa.Index(
    "uq_po_line_number",
    PurchaseOrderItem.__table__.c["purchase_order_id"],
    PurchaseOrderItem.__table__.c["line_number"],
    unique=True,
)


class PurchaseOrderSchedule(Base):
    __table__ = sa.Table(
        "purchase_order_schedules",
        Base.metadata,
        sa.Column(
            "schedule_id",
            sa.UUID(),
            nullable=False,
            comment="Transaction ID ở mức schedule của hệ thống | PK, NN",
        ),
        sa.Column(
            "po_item_id", sa.UUID(), nullable=False, comment="-> purchase_order_items | FK, NN"
        ),
        sa.Column(
            "schedule_number",
            sa.INTEGER(),
            nullable=False,
            comment="UNIQUE(po_item_id,schedule_number) | NN, >0",
        ),
        sa.Column(
            "scheduled_quantity",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment=(
                "Lượng theo đợt; tổng bằng ordered_quantity. Muốn nhiều deadline "
                "phải tách proposal/PO item. | NN, >0"
            ),
        ),
        sa.Column(
            "requested_delivery_date",
            sa.DATE(),
            nullable=False,
            comment=(
                "Deadline snapshot = proposed_delivery_date của proposal item; tất "
                "cả schedule cùng PO item dùng cùng deadline. | NN"
            ),
        ),
        sa.Column(
            "promised_delivery_date",
            sa.DATE(),
            nullable=True,
            comment=(
                "Cache ngày hứa hiện hành để hiển thị. Dựng snapshot lịch sử phải "
                "đọc phiên bản supplier_confirmation_items đã biết tại cutoff, "
                "không đọc cache hiện tại. | NULL"
            ),
        ),
        sa.Column(
            "status",
            sa.VARCHAR(length=30),
            nullable=False,
            comment=("open / confirmed / partially_received / completed / cancelled | NN, CHECK"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Thời điểm tạo schedule | NN",
        ),
        sa.CheckConstraint(
            sa.text("schedule_number > 0"),
            name=conv("ck_purchase_order_schedules_schedule_number_positive"),
        ),
        sa.CheckConstraint(
            sa.text("scheduled_quantity > 0::numeric"),
            name=conv("ck_purchase_order_schedules_scheduled_quantity_positive"),
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['open'::character varying, "
                "'confirmed'::character varying, 'partially_received'::character "
                "varying, 'completed'::character varying, 'cancelled'::character "
                "varying]::text[])"
            ),
            name=conv("ck_purchase_order_schedules_status_domain"),
        ),
        sa.ForeignKeyConstraint(
            ["po_item_id"],
            ["purchase_order_items.po_item_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_purchase_order_schedules_po_item_id"),
        ),
        sa.PrimaryKeyConstraint("schedule_id", name=conv("purchase_order_schedules_pkey")),
        comment=(
            "C15 | purchase_order_schedules [P0] - Lịch giao từng item; "
            "granular dataset Dataiku-compatible. SUM scheduled_quantity phải "
            "bằng ordered_quantity; ngày yêu cầu gốc cố định cùng PO item. "
            "promised_delivery_date là cache hiển thị, lịch sử dự đoán đọc "
            "confirmation detail revisions biết trước cutoff."
        ),
    )


sa.Index(
    "uq_schedule_number",
    PurchaseOrderSchedule.__table__.c["po_item_id"],
    PurchaseOrderSchedule.__table__.c["schedule_number"],
    unique=True,
)


class GoodsReceipt(Base):
    __table__ = sa.Table(
        "goods_receipts",
        Base.metadata,
        sa.Column("receipt_id", sa.UUID(), nullable=False, comment="ID lần nhận | PK, NN"),
        sa.Column(
            "schedule_id", sa.UUID(), nullable=False, comment="-> purchase_order_schedules | FK, NN"
        ),
        sa.Column(
            "received_quantity",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment=(
                "Lượng nhận đạt yêu cầu; không tính hàng lỗi/chưa được chấp nhận "
                "vào completion. Tổng không vượt schedule trong MVP. | NN, >0"
            ),
        ),
        sa.Column(
            "actual_delivery_date",
            sa.DATE(),
            nullable=False,
            comment=(
                "Ngày thực nhận hàng; có thể khác ngày ghi nhận. Receipt "
                "append-only trong MVP. | NN"
            ),
        ),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment=(
                "Thời điểm hệ thống biết lần nhận hàng; không backdate. Dùng cutoff "
                "lịch sử và nhãn/context. | NN"
            ),
        ),
        sa.Column(
            "recorded_by",
            sa.UUID(),
            nullable=True,
            comment=(
                "-> users.user_id người ghi nhận; thao tác mới qua UI cần active "
                "human có WAREHOUSE cho phòng ban của PR. Import legacy có "
                "provenance có thể NULL. | FK, NULL với legacy"
            ),
        ),
        sa.Column("notes", sa.TEXT(), nullable=True, comment="Ghi chú | NULL"),
        sa.CheckConstraint(
            sa.text("received_quantity > 0::numeric"),
            name=conv("ck_goods_receipts_received_quantity_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["purchase_order_schedules.schedule_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_goods_receipts_schedule_id"),
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"],
            ["users.user_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_receipt_user"),
        ),
        sa.PrimaryKeyConstraint("receipt_id", name=conv("goods_receipts_pkey")),
        comment=(
            "C16 | goods_receipts [P0] - Ghi nhận nhận hàng từng phần; nguồn "
            "nhãn ML lịch sử. received_quantity là lượng hàng đạt yêu cầu. "
            "Append-only, tổng lượng không vượt schedule. received_at là "
            "knowledge timestamp do server ghi, không backdate. recorded_by chỉ "
            "nullable cho legacy import có provenance."
        ),
    )


sa.Index(
    "ix_receipt_knowledge",
    GoodsReceipt.__table__.c["schedule_id"],
    GoodsReceipt.__table__.c["received_at"],
    unique=False,
)


class ConfirmationToken(Base):
    __table__ = sa.Table(
        "confirmation_tokens",
        Base.metadata,
        sa.Column("token_id", sa.UUID(), nullable=False, comment="ID token | PK, NN"),
        sa.Column(
            "purchase_order_id", sa.UUID(), nullable=False, comment="-> purchase_orders | FK, NN"
        ),
        sa.Column(
            "token_hash",
            sa.CHAR(length=64),
            nullable=False,
            comment="Hash SHA-256 hex của token ngẫu nhiên | UNIQUE, NN",
        ),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Phải > issued_at | NN",
        ),
        sa.Column(
            "issued_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Thời điểm cấp | NN"
        ),
        sa.Column(
            "consumed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Chỉ set sau POST quyết định hợp lệ | NULL",
        ),
        sa.Column(
            "revoked_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Hủy token cũ khi cấp token mới nếu cần | NULL",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_orders.purchase_order_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_confirmation_tokens_purchase_order_id"),
        ),
        sa.PrimaryKeyConstraint("token_id", name=conv("confirmation_tokens_pkey")),
        sa.UniqueConstraint("token_hash", name=conv("confirmation_tokens_token_hash_key")),
        comment=(
            "C17 | confirmation_tokens [P0] - Token độc lập được cấp trước khi "
            "supplier phản hồi. Token nghiệp vụ supplier một lần, chỉ lưu hash "
            "SHA-256; không phải mật khẩu hay token đăng nhập user. Xác nhận "
            "phải dùng token chưa consumed/revoked/expired và thuộc cùng PO."
        ),
    )


class SupplierConfirmation(Base):
    __table__ = sa.Table(
        "supplier_confirmations",
        Base.metadata,
        sa.Column("confirmation_id", sa.UUID(), nullable=False, comment="ID phản hồi | PK, NN"),
        sa.Column(
            "purchase_order_id",
            sa.UUID(),
            nullable=False,
            comment="-> purchase_orders; chặn ghi quyết định lặp | FK, NN, UNIQUE",
        ),
        sa.Column(
            "token_id",
            sa.UUID(),
            nullable=False,
            comment="-> confirmation_tokens; token thuộc đúng PO | FK, NN, UNIQUE",
        ),
        sa.Column(
            "decision",
            sa.VARCHAR(length=20),
            nullable=False,
            comment="confirmed / rejected | NN, CHECK",
        ),
        sa.Column("reason", sa.TEXT(), nullable=True, comment="Bắt buộc nếu rejected | NULL"),
        sa.Column(
            "responded_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment=(
                "Timestamp server ghi nhận quyết định được biết; không backdate từ "
                "giờ phía NCC. Header decision bất biến sau phản hồi hợp lệ. | NN"
            ),
        ),
        sa.CheckConstraint(
            sa.text(
                "decision::text = ANY (ARRAY['confirmed'::character varying, "
                "'rejected'::character varying]::text[])"
            ),
            name=conv("ck_supplier_confirmations_decision_domain"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_orders.purchase_order_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_supplier_confirmations_purchase_order_id"),
        ),
        sa.ForeignKeyConstraint(
            ["token_id"],
            ["confirmation_tokens.token_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_supplier_confirmations_token_id"),
        ),
        sa.PrimaryKeyConstraint("confirmation_id", name=conv("supplier_confirmations_pkey")),
        sa.UniqueConstraint(
            "purchase_order_id", name=conv("supplier_confirmations_purchase_order_id_key")
        ),
        sa.UniqueConstraint("token_id", name=conv("supplier_confirmations_token_id_key")),
        comment=(
            "C18 | supplier_confirmations [P0] - Phản hồi cấp PO; chỉ chốt một "
            "quyết định cho mỗi PO. Một quyết định cho mỗi PO/token. token_id "
            "phải thuộc purchase_order_id trong transaction; FK đơn lẻ chưa bảo "
            "đảm điều kiện này. responded_at là server knowledge timestamp bất "
            "biến."
        ),
    )


class SupplierConfirmationItem(Base):
    __table__ = sa.Table(
        "supplier_confirmation_items",
        Base.metadata,
        sa.Column(
            "confirmation_item_id",
            sa.UUID(),
            nullable=False,
            comment="ID chi tiết phản hồi | PK, NN",
        ),
        sa.Column(
            "confirmation_id",
            sa.UUID(),
            nullable=False,
            comment="-> supplier_confirmations | FK, NN",
        ),
        sa.Column(
            "schedule_id",
            sa.UUID(),
            nullable=False,
            comment="-> purchase_order_schedules của cùng PO | FK, NN",
        ),
        sa.Column(
            "revision_no",
            sa.INTEGER(),
            nullable=False,
            server_default=sa.text("1"),
            comment=(
                "Phiên bản chi tiết cam kết theo confirmation+schedule. Lưu "
                "append-only, cấp số khi khóa confirmation. | NN, DEFAULT 1, >0"
            ),
        ),
        sa.Column(
            "confirmed_quantity",
            sa.NUMERIC(precision=18, scale=4),
            nullable=True,
            comment=(
                "Chỉ ghi lượng NCC xác nhận rõ, không vượt schedule. Cho NULL khi "
                "chỉ có ngày hứa giao. | NULL; >0 nếu có"
            ),
        ),
        sa.Column(
            "promised_delivery_date",
            sa.DATE(),
            nullable=True,
            comment="Ngày supplier cam kết | NULL",
        ),
        sa.Column(
            "recorded_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment=(
                "Timestamp server biết phiên bản chi tiết; snapshot chỉ đọc "
                "recorded_at < feature_asof. | NN"
            ),
        ),
        sa.Column("notes", sa.TEXT(), nullable=True, comment="Ghi chú | NULL"),
        sa.CheckConstraint(sa.text("revision_no > 0"), name=conv("ck_detail_positive_revision")),
        sa.CheckConstraint(
            sa.text(
                "(confirmed_quantity IS NOT NULL OR promised_delivery_date IS NOT "
                "NULL) AND (confirmed_quantity IS NULL OR confirmed_quantity > "
                "0::numeric)"
            ),
            name=conv("ck_detail_quantity_or_promise"),
        ),
        sa.CheckConstraint(
            sa.text("confirmed_quantity > 0::numeric"),
            name=conv("ck_supplier_confirmation_items_confirmed_quantity_positive"),
        ),
        sa.CheckConstraint(
            sa.text("revision_no > 0"),
            name=conv("ck_supplier_confirmation_items_revision_no_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["confirmation_id"],
            ["supplier_confirmations.confirmation_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_supplier_confirmation_items_confirmation_id"),
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["purchase_order_schedules.schedule_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_supplier_confirmation_items_schedule_id"),
        ),
        sa.PrimaryKeyConstraint(
            "confirmation_item_id", name=conv("supplier_confirmation_items_pkey")
        ),
        comment=(
            "C19 | supplier_confirmation_items [P0] - Phiên bản chi tiết lượng "
            "và ngày hứa đã biết. Append-only revisions. Schedule phải thuộc "
            "cùng PO với confirmation, kiểm tra bằng transaction/API. Cập nhật "
            "cam kết tạo revision mới, không dùng lại consumed token. Lấy "
            "revision cuối đã biết tại T cho từng schedule còn thiếu hàng."
        ),
    )


sa.Index(
    "ix_promise_knowledge",
    SupplierConfirmationItem.__table__.c["schedule_id"],
    SupplierConfirmationItem.__table__.c["recorded_at"],
    SupplierConfirmationItem.__table__.c["revision_no"],
    unique=False,
)


sa.Index(
    "uq_confirmation_detail_version",
    SupplierConfirmationItem.__table__.c["confirmation_id"],
    SupplierConfirmationItem.__table__.c["schedule_id"],
    SupplierConfirmationItem.__table__.c["revision_no"],
    unique=True,
)


class EmailLog(Base):
    __table__ = sa.Table(
        "email_logs",
        Base.metadata,
        sa.Column("email_log_id", sa.UUID(), nullable=False, comment="ID hoạt động gửi | PK, NN"),
        sa.Column(
            "purchase_order_id", sa.UUID(), nullable=True, comment="-> purchase_orders | FK, NULL"
        ),
        sa.Column("supplier_id", sa.UUID(), nullable=False, comment="-> suppliers | FK, NN"),
        sa.Column(
            "recipient_email",
            sa.VARCHAR(length=255),
            nullable=False,
            comment="Email được dùng khi gửi | NN",
        ),
        sa.Column(
            "email_type",
            sa.VARCHAR(length=40),
            nullable=False,
            comment="po_issued / reminder / notification | NN",
        ),
        sa.Column(
            "template_code", sa.VARCHAR(length=80), nullable=True, comment="Mã template | NULL"
        ),
        sa.Column("subject", sa.VARCHAR(length=500), nullable=True, comment="Tiêu đề | NULL"),
        sa.Column(
            "idempotency_key",
            sa.VARCHAR(length=160),
            nullable=False,
            comment=(
                "UNIQUE cho một email logic trong DB; không tự bảo đảm "
                "provider/SMTP chỉ gửi một lần. | UNIQUE, NN"
            ),
        ),
        sa.Column(
            "provider_idempotency_key",
            sa.VARCHAR(length=180),
            nullable=True,
            comment=(
                "Khóa chuyển tới provider nếu hỗ trợ idempotency thực; không tự coi "
                "Message-ID là bảo đảm chống trùng. | NULL"
            ),
        ),
        sa.Column(
            "payload_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "Recipient, subject/body, template và file refs bất biến cho mọi "
                "lượt gửi của một logical email. | NN, object"
            ),
        ),
        sa.Column(
            "status",
            sa.VARCHAR(length=20),
            nullable=False,
            comment=(
                "queued / sending / sent / failed / delivery_unknown. Timeout sau "
                "khi có thể đã gửi => delivery_unknown. | NN, CHECK"
            ),
        ),
        sa.Column("attempt_count", sa.INTEGER(), nullable=False, comment="Số lần thử | NN, >=0"),
        sa.Column(
            "last_attempt_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment=(
                "Ghi trước khi gọi provider; lưu dấu vết khi worker ngắt hoặc timeout. | NULL"
            ),
        ),
        sa.Column(
            "next_retry_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment=(
                "Chỉ lên lịch khi chắc chắn chưa được nhận, hoặc dùng idempotency "
                "provider đã xác minh. NULL khi delivery_unknown chưa đối soát. | "
                "NULL"
            ),
        ),
        sa.Column(
            "provider_message_id",
            sa.VARCHAR(length=255),
            nullable=True,
            comment="ID phía nhà cung cấp email | NULL",
        ),
        sa.Column(
            "sent_at", sa.TIMESTAMP(timezone=True), nullable=True, comment="Mốc thành công | NULL"
        ),
        sa.Column("error_message", sa.TEXT(), nullable=True, comment="Lỗi lần gần nhất | NULL"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Thời điểm tạo | NN"
        ),
        sa.CheckConstraint(
            sa.text("attempt_count >= 0"), name=conv("ck_email_logs_attempt_count_nonnegative")
        ),
        sa.CheckConstraint(
            sa.text("jsonb_typeof(payload_snapshot) = 'object'::text"),
            name=conv("ck_email_logs_payload_snapshot_object"),
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['queued'::character varying, "
                "'sending'::character varying, 'sent'::character varying, "
                "'failed'::character varying, 'delivery_unknown'::character "
                "varying]::text[])"
            ),
            name=conv("ck_email_logs_status_domain"),
        ),
        sa.CheckConstraint(
            sa.text("jsonb_typeof(payload_snapshot) = 'object'::text"),
            name=conv("ck_email_payload_object"),
        ),
        sa.CheckConstraint(
            sa.text("status::text <> 'delivery_unknown'::text OR next_retry_at IS NULL"),
            name=conv("ck_email_unknown_no_timer"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_orders.purchase_order_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_email_logs_purchase_order_id"),
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.supplier_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_email_logs_supplier_id"),
        ),
        sa.PrimaryKeyConstraint("email_log_id", name=conv("email_logs_pkey")),
        sa.UniqueConstraint("idempotency_key", name=conv("email_logs_idempotency_key_key")),
        comment=(
            "C20 | email_logs [P0] - Gửi email PO có chống retry trùng. Payload "
            "bất biến. delivery_unknown cần đối soát hoặc provider idempotency "
            "trước khi retry. UNIQUE idempotency_key trong DB không bảo đảm "
            "SMTP gửi đúng một lần."
        ),
    )


class OutboxEvent(Base):
    __table__ = sa.Table(
        "outbox_events",
        Base.metadata,
        sa.Column("event_id", sa.UUID(), nullable=False, comment="ID event | PK, NN"),
        sa.Column(
            "aggregate_type",
            sa.VARCHAR(length=50),
            nullable=False,
            comment=(
                "purchase_request / proposal / purchase_order / user / user_role theo sự kiện. | NN"
            ),
        ),
        sa.Column("aggregate_id", sa.UUID(), nullable=False, comment="ID nghiệp vụ liên quan | NN"),
        sa.Column(
            "event_type",
            sa.VARCHAR(length=100),
            nullable=False,
            comment=(
                "Các event PR/proposal/PO/receipt/monitoring như v0.4; thêm "
                "user.provisioned / .disabled / .department_changed và "
                "user_role.granted / .revoked để audit. | NN"
            ),
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "Snapshot scope/revision/asof và actor_user_id nội bộ, sự kiện "
                "cấp/thu hồi quyền có grant/role/scope. Không lưu password, "
                "access/refresh token, session hoặc Authorization header. | NN"
            ),
        ),
        sa.Column(
            "idempotency_key",
            sa.VARCHAR(length=180),
            nullable=False,
            comment="Chặn consumer xử lý trùng | UNIQUE, NN",
        ),
        sa.Column(
            "status",
            sa.VARCHAR(length=20),
            nullable=False,
            comment="pending / published / failed | NN, CHECK",
        ),
        sa.Column("attempt_count", sa.INTEGER(), nullable=False, comment="Lượt publish | NN, >=0"),
        sa.Column(
            "available_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Thời điểm có thể gửi | NN",
        ),
        sa.Column(
            "published_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Mốc gửi thành công | NULL",
        ),
        sa.Column("last_error", sa.TEXT(), nullable=True, comment="Lỗi publish | NULL"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Mốc commit event với bản ghi business | NN",
        ),
        sa.CheckConstraint(
            sa.text("attempt_count >= 0"), name=conv("ck_outbox_events_attempt_count_nonnegative")
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['pending'::character varying, "
                "'published'::character varying, 'failed'::character "
                "varying]::text[])"
            ),
            name=conv("ck_outbox_events_status_domain"),
        ),
        sa.PrimaryKeyConstraint("event_id", name=conv("outbox_events_pkey")),
        sa.UniqueConstraint("idempotency_key", name=conv("outbox_events_idempotency_key_key")),
        comment=(
            "C21 | outbox_events [P0] - Ghi sự kiện trong cùng transaction tạo "
            "PR/approval/PO, phát sau commit. Ghi sự kiện cùng transaction "
            "nghiệp vụ, phát sau commit với xử lý retry/idempotency. "
            "aggregate_type+aggregate_id là tham chiếu đa loại, không tạo FK "
            "giả tới một bảng. Không lưu credential/Authorization header trong "
            "payload."
        ),
    )


class Department(Base):
    __table__ = sa.Table(
        "departments",
        Base.metadata,
        sa.Column(
            "department_id", sa.UUID(), nullable=False, comment="ID phòng ban của PR | PK, NN"
        ),
        sa.Column(
            "department_code",
            sa.VARCHAR(length=80),
            nullable=False,
            comment=(
                "Mã phòng ban ổn định để tổ chức hồ sơ và xác định phạm vi role. | UNIQUE, NN"
            ),
        ),
        sa.Column(
            "department_name", sa.VARCHAR(length=255), nullable=False, comment="Tên phòng ban | NN"
        ),
        sa.Column(
            "status",
            sa.VARCHAR(length=20),
            nullable=False,
            comment="active / inactive; không xóa phòng ban đã có PR | NN, CHECK",
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Thời điểm tạo | NN"
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Thời điểm sửa | NN"
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['active'::character varying, "
                "'inactive'::character varying]::text[])"
            ),
            name=conv("ck_departments_status_domain"),
        ),
        sa.PrimaryKeyConstraint("department_id", name=conv("departments_pkey")),
        sa.UniqueConstraint("department_code", name=conv("departments_department_code_key")),
        comment="C22 | departments [P0] - Phòng ban có nhu cầu mua hàng. ",
    )


class Notification(Base):
    __table__ = sa.Table(
        "notifications",
        Base.metadata,
        sa.Column("notification_id", sa.UUID(), nullable=False, comment="ID | PK, NN"),
        sa.Column(
            "recipient_user_id",
            sa.UUID(),
            nullable=False,
            comment=(
                "-> users.user_id người nhận. Service creator của proposal không "
                "thay người nhận/owner PR. | FK, NN"
            ),
        ),
        sa.Column(
            "type",
            sa.VARCHAR(length=60),
            nullable=False,
            comment="risk_alert / approval / result | NN",
        ),
        sa.Column(
            "entity_type", sa.VARCHAR(length=60), nullable=True, comment="PR / PO / proposal | NULL"
        ),
        sa.Column("entity_id", sa.UUID(), nullable=True, comment="ID liên quan | NULL"),
        sa.Column("title", sa.VARCHAR(length=255), nullable=False, comment="Tiêu đề | NN"),
        sa.Column("message", sa.TEXT(), nullable=True, comment="Nội dung | NULL"),
        sa.Column(
            "read_at", sa.TIMESTAMP(timezone=True), nullable=True, comment="Mốc đã đọc | NULL"
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Ngày tạo | NN"
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.user_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_notification_user"),
        ),
        sa.PrimaryKeyConstraint("notification_id", name=conv("notifications_pkey")),
        comment=(
            "C23 | notifications [P2] - Chỉ dùng nếu OpenCore không cung cấp "
            "read/unread persistence. P2, chỉ dùng khi OpenCore không cung cấp "
            "read/unread persistence. recipient_user_id là UUID local user; "
            "entity_type+entity_id là tham chiếu đa loại."
        ),
    )


class InventorySnapshot(Base):
    __table__ = sa.Table(
        "inventory_snapshots",
        Base.metadata,
        sa.Column(
            "inventory_snapshot_id", sa.UUID(), nullable=False, comment="ID snapshot | PK, NN"
        ),
        sa.Column("product_id", sa.UUID(), nullable=False, comment="-> products | FK, NN"),
        sa.Column("location_code", sa.VARCHAR(length=80), nullable=True, comment="Kho | NULL"),
        sa.Column(
            "on_hand_qty",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment="Tồn hiện hành tại snapshot | NN, >=0",
        ),
        sa.Column(
            "safety_stock_qty",
            sa.NUMERIC(precision=18, scale=4),
            nullable=False,
            comment="Mức tồn an toàn | NN, >=0",
        ),
        sa.Column(
            "snapshot_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Snapshot không được lấy từ tương lai | NN",
        ),
        sa.CheckConstraint(
            sa.text("on_hand_qty >= 0::numeric"),
            name=conv("ck_inventory_snapshots_on_hand_qty_nonnegative"),
        ),
        sa.CheckConstraint(
            sa.text("safety_stock_qty >= 0::numeric"),
            name=conv("ck_inventory_snapshots_safety_stock_qty_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.product_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_inventory_snapshots_product_id"),
        ),
        sa.PrimaryKeyConstraint("inventory_snapshot_id", name=conv("inventory_snapshots_pkey")),
        comment=(
            "C24 | inventory_snapshots [P2] - Chỉ cần nếu muốn Dataiku "
            "inventory fields. P2. Tồn kho chỉ là nguồn tham khảo theo "
            "snapshot_at, không tự thêm vào 19 X đã chốt."
        ),
    )


class User(Base):
    __table__ = sa.Table(
        "users",
        Base.metadata,
        sa.Column(
            "user_id",
            sa.UUID(),
            nullable=False,
            comment="ID nội bộ ứng dụng; không giả định bằng subject của IdP. | PK, NN",
        ),
        sa.Column(
            "external_issuer",
            sa.TEXT(),
            nullable=False,
            comment=(
                "Issuer đã được server xác thực và cho phép; không lấy từ payload "
                "tùy ý. | NN, nonblank"
            ),
        ),
        sa.Column(
            "external_subject",
            sa.TEXT(),
            nullable=False,
            comment=(
                "Subject ổn định của IdP. UNIQUE(external_issuer,external_subject). "
                "Không dùng email làm khóa đăng nhập. | NN, nonblank"
            ),
        ),
        sa.Column(
            "display_name",
            sa.VARCHAR(length=255),
            nullable=False,
            comment="Tên hiển thị; không dùng để phân quyền. | NN",
        ),
        sa.Column(
            "email",
            sa.VARCHAR(length=255),
            nullable=True,
            comment=(
                "Thông tin liên hệ/hiển thị; không mặc định UNIQUE và không là khóa "
                "định danh. | NULL"
            ),
        ),
        sa.Column(
            "department_id",
            sa.UUID(),
            nullable=True,
            comment=(
                "Phòng ban hiện tại -> departments; có thể NULL với service/admin. "
                "Nhân viên lập PR phải có phòng ban. | FK, NULL"
            ),
        ),
        sa.Column(
            "account_type",
            sa.VARCHAR(length=16),
            nullable=False,
            comment=(
                "human / service. PR creator/approver là human; AI/PO worker là "
                "service. Loại tài khoản ổn định. | NN, CHECK"
            ),
        ),
        sa.Column(
            "status",
            sa.VARCHAR(length=16),
            nullable=False,
            comment=(
                "active / disabled. Disabled chặn thao tác mới, không xóa actor "
                "lịch sử. | NN, CHECK"
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Thời điểm provision hồ sơ. | NN",
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Thời điểm thay đổi hồ sơ/trạng thái. | NN",
        ),
        sa.CheckConstraint(
            sa.text("length(btrim(external_issuer)) > 0 AND length(btrim(external_subject)) > 0"),
            name=conv("ck_user_identity_nonblank"),
        ),
        sa.CheckConstraint(
            sa.text(
                "(account_type::text = ANY (ARRAY['human'::character varying, "
                "'service'::character varying]::text[])) AND (status::text = ANY "
                "(ARRAY['active'::character varying, 'disabled'::character "
                "varying]::text[]))"
            ),
            name=conv("ck_user_type_status"),
        ),
        sa.CheckConstraint(
            sa.text(
                "account_type::text = ANY (ARRAY['human'::character varying, "
                "'service'::character varying]::text[])"
            ),
            name=conv("ck_users_account_type_domain"),
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['active'::character varying, "
                "'disabled'::character varying]::text[])"
            ),
            name=conv("ck_users_status_domain"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.department_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_user_department"),
        ),
        sa.PrimaryKeyConstraint("user_id", name=conv("users_pkey")),
        comment=(
            "C25 | users [P0] - Hồ sơ người dùng và tài khoản dịch vụ không có "
            "mật khẩu. IdP bên ngoài xác thực. Map bằng "
            "external_issuer+external_subject sang UUID local; email không phải "
            "identity key và không UNIQUE. Không lưu mật khẩu, "
            "access/refresh/session tokens. Soft disable để giữ actor lịch sử; "
            "user.department_id là phòng hiện tại, khác PR snapshot."
        ),
    )


sa.Index(
    "uq_user_external_identity",
    User.__table__.c["external_issuer"],
    User.__table__.c["external_subject"],
    unique=True,
)


class Role(Base):
    __table__ = sa.Table(
        "roles",
        Base.metadata,
        sa.Column("role_id", sa.UUID(), nullable=False, comment="ID role. | PK, NN"),
        sa.Column(
            "role_code",
            sa.VARCHAR(length=60),
            nullable=False,
            comment=(
                "Mã ổn định: EMPLOYEE / APPROVER / WAREHOUSE / ADMIN / AI_SERVICE / "
                "PO_SERVICE. Quyền hành động theo policy ở API. | UNIQUE, NN"
            ),
        ),
        sa.Column(
            "role_name",
            sa.VARCHAR(length=255),
            nullable=False,
            comment="Tên vai trò hiển thị. | NN",
        ),
        sa.Column("description", sa.TEXT(), nullable=True, comment="Mô tả trách nhiệm. | NULL"),
        sa.Column(
            "allowed_account_type",
            sa.VARCHAR(length=16),
            nullable=False,
            comment=(
                "human / service. API kiểm tra phù hợp users.account_type khi cấp "
                "và dùng role. | NN, CHECK"
            ),
        ),
        sa.Column(
            "scope_type",
            sa.VARCHAR(length=16),
            nullable=False,
            comment=(
                "global / department. Không đổi scope/role_code của role đã được "
                "cấp; tạo policy/role mới khi đổi ý nghĩa. | NN, CHECK"
            ),
        ),
        sa.Column(
            "status",
            sa.VARCHAR(length=16),
            nullable=False,
            comment=("active / disabled. Role disabled không cấp quyền thao tác mới. | NN, CHECK"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Ngày tạo danh mục. | NN",
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, comment="Ngày cập nhật. | NN"
        ),
        sa.CheckConstraint(
            sa.text(
                "(allowed_account_type::text = ANY (ARRAY['human'::character "
                "varying, 'service'::character varying]::text[])) AND "
                "(scope_type::text = ANY (ARRAY['global'::character varying, "
                "'department'::character varying]::text[])) AND (status::text = ANY "
                "(ARRAY['active'::character varying, 'disabled'::character "
                "varying]::text[]))"
            ),
            name=conv("ck_role_type_status"),
        ),
        sa.CheckConstraint(
            sa.text(
                "allowed_account_type::text = ANY (ARRAY['human'::character "
                "varying, 'service'::character varying]::text[])"
            ),
            name=conv("ck_roles_allowed_account_type_domain"),
        ),
        sa.CheckConstraint(
            sa.text(
                "scope_type::text = ANY (ARRAY['global'::character varying, "
                "'department'::character varying]::text[])"
            ),
            name=conv("ck_roles_scope_type_domain"),
        ),
        sa.CheckConstraint(
            sa.text(
                "status::text = ANY (ARRAY['active'::character varying, "
                "'disabled'::character varying]::text[])"
            ),
            name=conv("ck_roles_status_domain"),
        ),
        sa.PrimaryKeyConstraint("role_id", name=conv("roles_pkey")),
        comment=(
            "C26 | roles [P0] - Danh mục vai trò và phạm vi quyền ứng dụng. Sáu "
            "role ban đầu: EMPLOYEE(human/global/own PR), "
            "APPROVER(human/department/workflow), WAREHOUSE(human/department), "
            "ADMIN(human/global/profiles+grants+config), "
            "AI_SERVICE(service/global/analysis+proposal), "
            "PO_SERVICE(service/global/issue sau approval). FK không thay "
            "middleware phân quyền. Không đổi role_code/scope_type khi role đã "
            "được cấp."
        ),
    )


sa.Index("uq_role_code", Role.__table__.c["role_code"], unique=True)


sa.Index("uq_role_scope", Role.__table__.c["role_id"], Role.__table__.c["scope_type"], unique=True)


class UserRole(Base):
    __table__ = sa.Table(
        "user_roles",
        Base.metadata,
        sa.Column(
            "user_role_id",
            sa.UUID(),
            nullable=False,
            comment="ID một lần cấp quyền; cấp lại sau thu hồi tạo dòng mới. | PK, NN",
        ),
        sa.Column(
            "user_id", sa.UUID(), nullable=False, comment="-> users người được cấp. | FK, NN"
        ),
        sa.Column(
            "role_id",
            sa.UUID(),
            nullable=False,
            comment="-> roles; scope_type phải khớp role. | FK ghép, NN",
        ),
        sa.Column(
            "scope_type",
            sa.VARCHAR(length=16),
            nullable=False,
            comment=(
                "global / department. Scope global vẫn tuân thủ quyền sở hữu PR của "
                "role EMPLOYEE, không cho đọc toàn bộ PR. | NN, CHECK"
            ),
        ),
        sa.Column(
            "department_id",
            sa.UUID(),
            nullable=True,
            comment=(
                "NN nếu department, NULL nếu global. Đây là phòng ban được cấp "
                "quyền, độc lập phòng ban hiện tại của user. | FK, NULL theo scope"
            ),
        ),
        sa.Column(
            "granted_by",
            sa.UUID(),
            nullable=False,
            comment=(
                "-> users actor quản trị cấp quyền. Bootstrap quản trị được thực "
                "hiện riêng, có audit; không tự cấp từ login payload. | FK, NN"
            ),
        ),
        sa.Column(
            "granted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Timestamp server cấp quyền. | NN",
        ),
        sa.Column(
            "revoked_by",
            sa.UUID(),
            nullable=True,
            comment="-> users quản trị thu hồi, có khi revoked_at có. | FK, NULL",
        ),
        sa.Column(
            "revoked_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment=(
                "NULL là grant chưa thu hồi. Thu hồi giữ nguyên dòng; user/role "
                "phải active mới được dùng. | NULL"
            ),
        ),
        sa.CheckConstraint(
            sa.text(
                "revoked_at IS NULL AND revoked_by IS NULL OR revoked_at IS NOT "
                "NULL AND revoked_by IS NOT NULL AND revoked_at >= granted_at"
            ),
            name=conv("ck_grant_revoke"),
        ),
        sa.CheckConstraint(
            sa.text(
                "scope_type::text = 'global'::text AND department_id IS NULL OR "
                "scope_type::text = 'department'::text AND department_id IS NOT "
                "NULL"
            ),
            name=conv("ck_grant_scope"),
        ),
        sa.CheckConstraint(
            sa.text(
                "scope_type::text = ANY (ARRAY['global'::character varying, "
                "'department'::character varying]::text[])"
            ),
            name=conv("ck_user_roles_scope_type_domain"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.department_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_user_role_department"),
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.user_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_user_role_granter"),
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["users.user_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_user_role_revoker"),
        ),
        sa.ForeignKeyConstraint(
            ["role_id", "scope_type"],
            ["roles.role_id", "roles.scope_type"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_user_role_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            use_alter=True,
            ondelete="RESTRICT",
            deferrable=True,
            name=conv("fk_user_role_user"),
        ),
        sa.PrimaryKeyConstraint("user_role_id", name=conv("user_roles_pkey")),
        comment=(
            "C27 | user_roles [P0] - Cấp và thu hồi role theo phạm vi có lịch "
            "sử. Grant/revoke có lịch sử: thu hồi ghi revoked_at+revoked_by, "
            "cấp lại tạo UUID mới. Active user+active role+grant chưa revoke, "
            "account type và scope phải phù hợp. Department grant độc lập phòng "
            "hiện tại của user. Hai partial UNIQUE chặn grant hiệu lực trùng, "
            "không thay bằng UNIQUE nullable department_id. Bootstrap ADMIN có "
            "audit riêng."
        ),
    )


sa.Index(
    "ix_active_user_roles",
    UserRole.__table__.c["user_id"],
    UserRole.__table__.c["role_id"],
    UserRole.__table__.c["department_id"],
    unique=False,
    postgresql_where=sa.text("(revoked_at IS NULL)"),
)


sa.Index(
    "uq_active_department_grant",
    UserRole.__table__.c["user_id"],
    UserRole.__table__.c["role_id"],
    UserRole.__table__.c["department_id"],
    unique=True,
    postgresql_where=sa.text(
        "(((scope_type)::text = 'department'::text) AND (revoked_at IS NULL))"
    ),
)


sa.Index(
    "uq_active_global_grant",
    UserRole.__table__.c["user_id"],
    UserRole.__table__.c["role_id"],
    unique=True,
    postgresql_where=sa.text("(((scope_type)::text = 'global'::text) AND (revoked_at IS NULL))"),
)
