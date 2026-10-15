SET standard_conforming_strings=on;
-- Supplier Assistant v0.4.1 - demo baseline PostgreSQL schema (signed monitoring lead patch)
-- Supabase: run in the target project database postgres, as postgres.
-- 27 tables, 293 columns, 56 FKs. Helper functions and seed data are installed by script.py.
-- Source: agreed schema_v041.json and validated DBML; original files preserved.
-- This is a fresh-schema installer, not an incremental migration.
-- Do not run the older integrity supplement afterward: its constraints overlap.
-- No DROP/TRUNCATE, no credential storage and no new Markdown document store.
-- Transaction rolls back on any schema conflict or creation error.
SET LOCAL client_encoding = 'UTF8';
SET LOCAL search_path = public, pg_catalog;
SET LOCAL TIME ZONE 'Asia/Ho_Chi_Minh';
CREATE TABLE "suppliers" (
  "supplier_id" uuid PRIMARY KEY NOT NULL,
  "supplier_code" varchar(50) UNIQUE NOT NULL,
  "supplier_name" varchar(255) NOT NULL,
  "email" varchar(255),
  "phone" varchar(50),
  "address" text,
  "city" varchar(120),
  "postal_code" varchar(30),
  "country_of_origin" char(2),
  "status" varchar(20) NOT NULL,
  "created_at" timestamptz NOT NULL,
  "updated_at" timestamptz NOT NULL,
  CONSTRAINT "ck_suppliers_status_domain" CHECK (status IN ('active', 'inactive', 'blocked'))
);

CREATE TABLE "products" (
  "product_id" uuid PRIMARY KEY NOT NULL,
  "product_code" varchar(80) UNIQUE NOT NULL,
  "product_name" varchar(255) NOT NULL,
  "product_category" varchar(120) NOT NULL,
  "unit" varchar(30) NOT NULL,
  "description" text,
  "status" varchar(20) NOT NULL,
  "created_at" timestamptz NOT NULL,
  "updated_at" timestamptz NOT NULL,
  CONSTRAINT "ck_products_status_domain" CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE "supplier_products" (
  "supplier_product_id" uuid PRIMARY KEY NOT NULL,
  "supplier_id" uuid NOT NULL,
  "product_id" uuid NOT NULL,
  "supplier_part_number" varchar(100),
  "current_unit_price" numeric(18,4) NOT NULL,
  "currency" char(3) NOT NULL,
  "min_order_qty" numeric(18,4),
  "quoted_lead_days" integer,
  "is_available" boolean NOT NULL,
  "updated_at" timestamptz NOT NULL,
  CONSTRAINT "ck_supplier_products_current_unit_price_nonnegative" CHECK (current_unit_price >= 0),
  CONSTRAINT "ck_supplier_products_min_order_qty_positive" CHECK (min_order_qty > 0),
  CONSTRAINT "ck_supplier_products_quoted_lead_days_nonnegative" CHECK (quoted_lead_days >= 0)
);

CREATE TABLE "purchase_requests" (
  "request_id" uuid PRIMARY KEY NOT NULL,
  "request_code" varchar(50) UNIQUE NOT NULL,
  "department_id" uuid NOT NULL,
  "requested_by" uuid NOT NULL,
  "source" varchar(20) NOT NULL,
  "status" varchar(30) NOT NULL,
  "input_revision" integer NOT NULL DEFAULT 1,
  "submitted_at" timestamptz,
  "notes" text,
  "created_at" timestamptz NOT NULL,
  "updated_at" timestamptz NOT NULL,
  CONSTRAINT "ck_purchase_requests_source_domain" CHECK (source IN ('web', 'chat', 'api')),
  CONSTRAINT "ck_purchase_requests_status_domain" CHECK (status IN ('draft', 'analysing', 'needs_review', 'analysis_failed', 'pending_approval', 'approved', 'rejected', 'ordered', 'cancelled')),
  CONSTRAINT "ck_purchase_requests_input_revision_positive" CHECK (input_revision > 0)
);

CREATE TABLE "purchase_request_items" (
  "request_item_id" uuid PRIMARY KEY NOT NULL,
  "request_id" uuid NOT NULL,
  "line_number" integer NOT NULL,
  "product_id" uuid NOT NULL,
  "preferred_supplier_id" uuid,
  "quantity" numeric(18,4) NOT NULL,
  "requested_delivery_date" date NOT NULL,
  "unit_price_snapshot" numeric(18,4),
  "currency" char(3) NOT NULL,
  "line_total" numeric(18,4),
  "notes" text,
  CONSTRAINT "ck_purchase_request_items_line_number_positive" CHECK (line_number > 0),
  CONSTRAINT "ck_purchase_request_items_quantity_positive" CHECK (quantity > 0),
  CONSTRAINT "ck_purchase_request_items_unit_price_snapshot_nonnegative" CHECK (unit_price_snapshot >= 0),
  CONSTRAINT "ck_purchase_request_items_line_total_nonnegative" CHECK (line_total >= 0)
);

CREATE TABLE "analysis_tasks" (
  "task_id" uuid PRIMARY KEY NOT NULL,
  "request_id" uuid NOT NULL,
  "po_item_id" uuid,
  "task_type" varchar(30) NOT NULL,
  "input_revision" integer NOT NULL,
  "feature_asof" timestamptz NOT NULL,
  "input_snapshot" jsonb NOT NULL,
  "idempotency_key" varchar(160) UNIQUE NOT NULL,
  "status" varchar(20) NOT NULL,
  "started_at" timestamptz,
  "completed_at" timestamptz,
  "error_code" varchar(80),
  "error_message" text,
  "created_at" timestamptz NOT NULL,
  CONSTRAINT "ck_task_monitor_scope" CHECK ((task_type IN ('initial_analysis','reanalysis') AND po_item_id IS NULL) OR (task_type='po_monitoring' AND po_item_id IS NOT NULL)),
  CONSTRAINT "ck_analysis_tasks_task_type_domain" CHECK (task_type IN ('initial_analysis', 'reanalysis', 'po_monitoring')),
  CONSTRAINT "ck_analysis_tasks_input_revision_positive" CHECK (input_revision > 0),
  CONSTRAINT "ck_analysis_tasks_input_snapshot_object" CHECK (jsonb_typeof(input_snapshot) = 'object'),
  CONSTRAINT "ck_analysis_tasks_status_domain" CHECK (status IN ('queued', 'running', 'succeeded', 'failed'))
);

CREATE TABLE "risk_predictions" (
  "prediction_id" uuid PRIMARY KEY NOT NULL,
  "task_id" uuid,
  "request_item_id" uuid,
  "request_id" uuid,
  "input_revision" integer,
  "source_type" varchar(30) NOT NULL,
  "prediction_stage" varchar(24) NOT NULL,
  "po_item_id" uuid,
  "idempotency_key" varchar(180) UNIQUE NOT NULL,
  "supplier_id" uuid NOT NULL,
  "product_id" uuid NOT NULL,
  "quantity" numeric(18,4) NOT NULL,
  "requested_delivery_date" date NOT NULL,
  "feature_asof" timestamptz NOT NULL,
  "requested_lead_days" integer NOT NULL,
  "feature_schema_version" varchar(80) NOT NULL,
  "preprocessing_version" varchar(80) NOT NULL,
  "model_name" varchar(120),
  "model_version" varchar(120),
  "context_dataset_version" varchar(120),
  "context_metadata" jsonb,
  "risk_score" numeric(7,6),
  "risk_threshold" numeric(7,6),
  "risk_level" varchar(25),
  "feature_snapshot" jsonb NOT NULL,
  "status" varchar(24) NOT NULL,
  "error_code" varchar(80),
  "error_message" text,
  "predicted_at" timestamptz NOT NULL,
  CONSTRAINT "ck_prediction_scope" CHECK ((request_item_id IS NULL AND request_id IS NULL AND input_revision IS NULL AND task_id IS NULL) OR (request_item_id IS NOT NULL AND request_id IS NOT NULL AND input_revision IS NOT NULL AND input_revision>0)),
  CONSTRAINT "ck_prediction_lead_anchor" CHECK (requested_lead_days = requested_delivery_date - (feature_asof AT TIME ZONE 'Asia/Ho_Chi_Minh')::date),
  CONSTRAINT "ck_prediction_stage_scope" CHECK ((prediction_stage='PR_CREATED' AND po_item_id IS NULL) OR (prediction_stage='PO_MONITORING' AND po_item_id IS NOT NULL AND request_id IS NOT NULL AND request_item_id IS NOT NULL AND input_revision IS NOT NULL)),
  CONSTRAINT "ck_prediction_stage_snapshot" CHECK (feature_snapshot->'x'->>'prediction_stage' = prediction_stage),
  CONSTRAINT "ck_prediction_item_match_full" CHECK ((request_item_id IS NULL) = (request_id IS NULL)),
  CONSTRAINT "ck_risk_predictions_input_revision_positive" CHECK (input_revision > 0),
  CONSTRAINT "ck_risk_predictions_source_type_domain" CHECK (source_type IN ('pr_initial', 'pr_reanalysis', 'chat_ad_hoc', 'comparison', 'proposal_selection', 'po_monitoring', 'historical_import')),
  CONSTRAINT "ck_risk_predictions_prediction_stage_domain" CHECK (prediction_stage IN ('PR_CREATED', 'PO_MONITORING')),
  CONSTRAINT "ck_risk_predictions_quantity_positive" CHECK (quantity > 0),
  CONSTRAINT "ck_prediction_initial_lead_nonnegative" CHECK (prediction_stage <> 'PR_CREATED' OR requested_lead_days >= 0),
  CONSTRAINT "ck_risk_predictions_risk_score_range" CHECK (risk_score BETWEEN 0 AND 1),
  CONSTRAINT "ck_risk_predictions_risk_threshold_range" CHECK (risk_threshold BETWEEN 0 AND 1),
  CONSTRAINT "ck_risk_predictions_risk_level_domain" CHECK (risk_level IN ('low', 'high')),
  CONSTRAINT "ck_risk_predictions_feature_snapshot_object" CHECK (jsonb_typeof(feature_snapshot) = 'object'),
  CONSTRAINT "ck_risk_predictions_status_domain" CHECK (status IN ('success', 'insufficient_data', 'failed', 'snapshot_only'))
);

CREATE TABLE "risk_analysis_reports" (
  "report_id" uuid PRIMARY KEY NOT NULL,
  "task_id" uuid NOT NULL,
  "request_id" uuid NOT NULL,
  "primary_prediction_id" uuid,
  "risk_level" varchar(25),
  "summary" text NOT NULL,
  "report_data" jsonb NOT NULL,
  "created_at" timestamptz NOT NULL,
  CONSTRAINT "ck_risk_analysis_reports_report_data_object" CHECK (jsonb_typeof(report_data) = 'object')
);

CREATE TABLE "analysis_supplier_candidates" (
  "candidate_id" uuid PRIMARY KEY NOT NULL,
  "report_id" uuid NOT NULL,
  "task_id" uuid NOT NULL,
  "request_id" uuid NOT NULL,
  "request_item_id" uuid NOT NULL,
  "supplier_id" uuid NOT NULL,
  "prediction_id" uuid,
  "unit_price_snapshot" numeric(18,4),
  "currency" char(3),
  "rank" integer,
  "is_eligible" boolean NOT NULL,
  "reason" text,
  "evidence_refs" jsonb,
  CONSTRAINT "ck_analysis_supplier_candidates_unit_price_snapshot_nonnegative" CHECK (unit_price_snapshot >= 0),
  CONSTRAINT "ck_analysis_supplier_candidates_rank_positive" CHECK (rank > 0)
);

CREATE TABLE "proposals" (
  "proposal_id" uuid PRIMARY KEY NOT NULL,
  "proposal_code" varchar(50) UNIQUE NOT NULL,
  "request_id" uuid NOT NULL,
  "version_no" integer NOT NULL,
  "input_revision" integer NOT NULL,
  "supersedes_proposal_id" uuid,
  "is_current" boolean NOT NULL DEFAULT true,
  "analysis_report_id" uuid NOT NULL,
  "generation_task_id" uuid NOT NULL,
  "selection_policy_version" varchar(80) NOT NULL,
  "workflow_instance_id" varchar(160),
  "reason" text,
  "status" varchar(30) NOT NULL,
  "created_by" uuid NOT NULL,
  "submitted_at" timestamptz NOT NULL,
  "created_at" timestamptz NOT NULL,
  CONSTRAINT "ck_proposal_version_positive" CHECK (version_no > 0 AND input_revision > 0),
  CONSTRAINT "ck_proposal_approved_current" CHECK (status <> 'approved' OR is_current),
  CONSTRAINT "ck_proposal_not_self" CHECK (supersedes_proposal_id IS NULL OR supersedes_proposal_id <> proposal_id),
  CONSTRAINT "ck_auto_proposal_status" CHECK (status IN ('pending','approved','rejected','superseded','cancelled')),
  CONSTRAINT "ck_proposals_version_no_positive" CHECK (version_no > 0),
  CONSTRAINT "ck_proposals_input_revision_positive" CHECK (input_revision > 0),
  CONSTRAINT "ck_proposals_status_domain" CHECK (status IN ('pending', 'approved', 'rejected', 'superseded', 'cancelled'))
);

CREATE TABLE "proposal_items" (
  "proposal_item_id" uuid PRIMARY KEY NOT NULL,
  "proposal_id" uuid NOT NULL,
  "request_id" uuid NOT NULL,
  "request_item_id" uuid NOT NULL,
  "selected_supplier_id" uuid NOT NULL,
  "prediction_id" uuid NOT NULL,
  "quantity" numeric(18,4) NOT NULL,
  "unit_price_snapshot" numeric(18,4) NOT NULL,
  "currency" char(3) NOT NULL,
  "proposed_delivery_date" date NOT NULL,
  "reason" text,
  CONSTRAINT "ck_proposal_items_quantity_positive" CHECK (quantity > 0),
  CONSTRAINT "ck_proposal_items_unit_price_snapshot_nonnegative" CHECK (unit_price_snapshot >= 0)
);

CREATE TABLE "approval_history" (
  "approval_id" uuid PRIMARY KEY NOT NULL,
  "proposal_id" uuid NOT NULL,
  "workflow_task_id" varchar(120),
  "approver_id" uuid NOT NULL,
  "decision" varchar(20) NOT NULL,
  "reason" text,
  "decided_at" timestamptz NOT NULL,
  CONSTRAINT "ck_approval_history_decision_domain" CHECK (decision IN ('approved', 'rejected'))
);

CREATE TABLE "purchase_orders" (
  "purchase_order_id" uuid PRIMARY KEY NOT NULL,
  "po_number" varchar(50) UNIQUE NOT NULL,
  "request_id" uuid NOT NULL,
  "proposal_id" uuid NOT NULL,
  "supplier_id" uuid NOT NULL,
  "order_date" timestamptz NOT NULL,
  "status" varchar(35) NOT NULL,
  "currency" char(3) NOT NULL,
  "total_amount" numeric(18,4) NOT NULL,
  "pdf_file_id" varchar(255),
  "created_by" uuid NOT NULL,
  "created_at" timestamptz NOT NULL,
  "updated_at" timestamptz NOT NULL,
  CONSTRAINT "ck_purchase_orders_status_domain" CHECK (status IN ('issued', 'waiting_confirmation', 'confirmed', 'rejected', 'partially_received', 'delivered', 'cancelled')),
  CONSTRAINT "ck_purchase_orders_total_amount_nonnegative" CHECK (total_amount >= 0)
);

CREATE TABLE "purchase_order_items" (
  "po_item_id" uuid PRIMARY KEY NOT NULL,
  "purchase_order_id" uuid NOT NULL,
  "proposal_item_id" uuid UNIQUE NOT NULL,
  "proposal_id" uuid NOT NULL,
  "request_id" uuid NOT NULL,
  "request_item_id" uuid NOT NULL,
  "line_number" integer NOT NULL,
  "product_id" uuid NOT NULL,
  "product_category_snapshot" varchar(120) NOT NULL,
  "ordered_quantity" numeric(18,4) NOT NULL,
  "unit" varchar(30) NOT NULL,
  "unit_price" numeric(18,4) NOT NULL,
  "line_total" numeric(18,4) NOT NULL,
  "item_status" varchar(35) NOT NULL,
  CONSTRAINT "ck_purchase_order_items_line_number_positive" CHECK (line_number > 0),
  CONSTRAINT "ck_purchase_order_items_ordered_quantity_positive" CHECK (ordered_quantity > 0),
  CONSTRAINT "ck_purchase_order_items_unit_price_nonnegative" CHECK (unit_price >= 0),
  CONSTRAINT "ck_purchase_order_items_line_total_nonnegative" CHECK (line_total >= 0),
  CONSTRAINT "ck_purchase_order_items_item_status_domain" CHECK (item_status IN ('open', 'confirmed', 'partially_received', 'completed', 'cancelled'))
);

CREATE TABLE "purchase_order_schedules" (
  "schedule_id" uuid PRIMARY KEY NOT NULL,
  "po_item_id" uuid NOT NULL,
  "schedule_number" integer NOT NULL,
  "scheduled_quantity" numeric(18,4) NOT NULL,
  "requested_delivery_date" date NOT NULL,
  "promised_delivery_date" date,
  "status" varchar(30) NOT NULL,
  "created_at" timestamptz NOT NULL,
  CONSTRAINT "ck_purchase_order_schedules_schedule_number_positive" CHECK (schedule_number > 0),
  CONSTRAINT "ck_purchase_order_schedules_scheduled_quantity_positive" CHECK (scheduled_quantity > 0),
  CONSTRAINT "ck_purchase_order_schedules_status_domain" CHECK (status IN ('open', 'confirmed', 'partially_received', 'completed', 'cancelled'))
);

CREATE TABLE "goods_receipts" (
  "receipt_id" uuid PRIMARY KEY NOT NULL,
  "schedule_id" uuid NOT NULL,
  "received_quantity" numeric(18,4) NOT NULL,
  "actual_delivery_date" date NOT NULL,
  "received_at" timestamptz NOT NULL,
  "recorded_by" uuid,
  "notes" text,
  CONSTRAINT "ck_goods_receipts_received_quantity_positive" CHECK (received_quantity > 0)
);

CREATE TABLE "confirmation_tokens" (
  "token_id" uuid PRIMARY KEY NOT NULL,
  "purchase_order_id" uuid NOT NULL,
  "token_hash" char(64) UNIQUE NOT NULL,
  "expires_at" timestamptz NOT NULL,
  "issued_at" timestamptz NOT NULL,
  "consumed_at" timestamptz,
  "revoked_at" timestamptz
);

CREATE TABLE "supplier_confirmations" (
  "confirmation_id" uuid PRIMARY KEY NOT NULL,
  "purchase_order_id" uuid UNIQUE NOT NULL,
  "token_id" uuid UNIQUE NOT NULL,
  "decision" varchar(20) NOT NULL,
  "reason" text,
  "responded_at" timestamptz NOT NULL,
  CONSTRAINT "ck_supplier_confirmations_decision_domain" CHECK (decision IN ('confirmed', 'rejected'))
);

CREATE TABLE "supplier_confirmation_items" (
  "confirmation_item_id" uuid PRIMARY KEY NOT NULL,
  "confirmation_id" uuid NOT NULL,
  "schedule_id" uuid NOT NULL,
  "revision_no" integer NOT NULL DEFAULT 1,
  "confirmed_quantity" numeric(18,4),
  "promised_delivery_date" date,
  "recorded_at" timestamptz NOT NULL,
  "notes" text,
  CONSTRAINT "ck_detail_positive_revision" CHECK (revision_no > 0),
  CONSTRAINT "ck_detail_quantity_or_promise" CHECK ((confirmed_quantity IS NOT NULL OR promised_delivery_date IS NOT NULL) AND (confirmed_quantity IS NULL OR confirmed_quantity > 0)),
  CONSTRAINT "ck_supplier_confirmation_items_revision_no_positive" CHECK (revision_no > 0),
  CONSTRAINT "ck_supplier_confirmation_items_confirmed_quantity_positive" CHECK (confirmed_quantity > 0)
);

CREATE TABLE "email_logs" (
  "email_log_id" uuid PRIMARY KEY NOT NULL,
  "purchase_order_id" uuid,
  "supplier_id" uuid NOT NULL,
  "recipient_email" varchar(255) NOT NULL,
  "email_type" varchar(40) NOT NULL,
  "template_code" varchar(80),
  "subject" varchar(500),
  "idempotency_key" varchar(160) UNIQUE NOT NULL,
  "provider_idempotency_key" varchar(180),
  "payload_snapshot" jsonb NOT NULL,
  "status" varchar(20) NOT NULL,
  "attempt_count" integer NOT NULL,
  "last_attempt_at" timestamptz,
  "next_retry_at" timestamptz,
  "provider_message_id" varchar(255),
  "sent_at" timestamptz,
  "error_message" text,
  "created_at" timestamptz NOT NULL,
  CONSTRAINT "ck_email_payload_object" CHECK (jsonb_typeof(payload_snapshot) = 'object'),
  CONSTRAINT "ck_email_unknown_no_timer" CHECK (status <> 'delivery_unknown' OR next_retry_at IS NULL),
  CONSTRAINT "ck_email_logs_payload_snapshot_object" CHECK (jsonb_typeof(payload_snapshot) = 'object'),
  CONSTRAINT "ck_email_logs_status_domain" CHECK (status IN ('queued', 'sending', 'sent', 'failed', 'delivery_unknown')),
  CONSTRAINT "ck_email_logs_attempt_count_nonnegative" CHECK (attempt_count >= 0)
);

CREATE TABLE "outbox_events" (
  "event_id" uuid PRIMARY KEY NOT NULL,
  "aggregate_type" varchar(50) NOT NULL,
  "aggregate_id" uuid NOT NULL,
  "event_type" varchar(100) NOT NULL,
  "payload" jsonb NOT NULL,
  "idempotency_key" varchar(180) UNIQUE NOT NULL,
  "status" varchar(20) NOT NULL,
  "attempt_count" integer NOT NULL,
  "available_at" timestamptz NOT NULL,
  "published_at" timestamptz,
  "last_error" text,
  "created_at" timestamptz NOT NULL,
  CONSTRAINT "ck_outbox_events_status_domain" CHECK (status IN ('pending', 'published', 'failed')),
  CONSTRAINT "ck_outbox_events_attempt_count_nonnegative" CHECK (attempt_count >= 0)
);

CREATE TABLE "departments" (
  "department_id" uuid PRIMARY KEY NOT NULL,
  "department_code" varchar(80) UNIQUE NOT NULL,
  "department_name" varchar(255) NOT NULL,
  "status" varchar(20) NOT NULL,
  "created_at" timestamptz NOT NULL,
  "updated_at" timestamptz NOT NULL,
  CONSTRAINT "ck_departments_status_domain" CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE "notifications" (
  "notification_id" uuid PRIMARY KEY NOT NULL,
  "recipient_user_id" uuid NOT NULL,
  "type" varchar(60) NOT NULL,
  "entity_type" varchar(60),
  "entity_id" uuid,
  "title" varchar(255) NOT NULL,
  "message" text,
  "read_at" timestamptz,
  "created_at" timestamptz NOT NULL
);

CREATE TABLE "inventory_snapshots" (
  "inventory_snapshot_id" uuid PRIMARY KEY NOT NULL,
  "product_id" uuid NOT NULL,
  "location_code" varchar(80),
  "on_hand_qty" numeric(18,4) NOT NULL,
  "safety_stock_qty" numeric(18,4) NOT NULL,
  "snapshot_at" timestamptz NOT NULL,
  CONSTRAINT "ck_inventory_snapshots_on_hand_qty_nonnegative" CHECK (on_hand_qty >= 0),
  CONSTRAINT "ck_inventory_snapshots_safety_stock_qty_nonnegative" CHECK (safety_stock_qty >= 0)
);

CREATE TABLE "users" (
  "user_id" uuid PRIMARY KEY NOT NULL,
  "external_issuer" text NOT NULL,
  "external_subject" text NOT NULL,
  "display_name" varchar(255) NOT NULL,
  "email" varchar(255),
  "department_id" uuid,
  "account_type" varchar(16) NOT NULL,
  "status" varchar(16) NOT NULL,
  "created_at" timestamptz NOT NULL,
  "updated_at" timestamptz NOT NULL,
  CONSTRAINT "ck_user_identity_nonblank" CHECK (length(btrim(external_issuer)) > 0 AND length(btrim(external_subject)) > 0),
  CONSTRAINT "ck_user_type_status" CHECK (account_type IN ('human','service') AND status IN ('active','disabled')),
  CONSTRAINT "ck_users_account_type_domain" CHECK (account_type IN ('human', 'service')),
  CONSTRAINT "ck_users_status_domain" CHECK (status IN ('active', 'disabled'))
);

CREATE TABLE "roles" (
  "role_id" uuid PRIMARY KEY NOT NULL,
  "role_code" varchar(60) NOT NULL,
  "role_name" varchar(255) NOT NULL,
  "description" text,
  "allowed_account_type" varchar(16) NOT NULL,
  "scope_type" varchar(16) NOT NULL,
  "status" varchar(16) NOT NULL,
  "created_at" timestamptz NOT NULL,
  "updated_at" timestamptz NOT NULL,
  CONSTRAINT "ck_role_type_status" CHECK (allowed_account_type IN ('human','service') AND scope_type IN ('global','department') AND status IN ('active','disabled')),
  CONSTRAINT "ck_roles_allowed_account_type_domain" CHECK (allowed_account_type IN ('human', 'service')),
  CONSTRAINT "ck_roles_scope_type_domain" CHECK (scope_type IN ('global', 'department')),
  CONSTRAINT "ck_roles_status_domain" CHECK (status IN ('active', 'disabled'))
);

CREATE TABLE "user_roles" (
  "user_role_id" uuid PRIMARY KEY NOT NULL,
  "user_id" uuid NOT NULL,
  "role_id" uuid NOT NULL,
  "scope_type" varchar(16) NOT NULL,
  "department_id" uuid,
  "granted_by" uuid NOT NULL,
  "granted_at" timestamptz NOT NULL,
  "revoked_by" uuid,
  "revoked_at" timestamptz,
  CONSTRAINT "ck_grant_scope" CHECK ((scope_type='global' AND department_id IS NULL) OR (scope_type='department' AND department_id IS NOT NULL)),
  CONSTRAINT "ck_grant_revoke" CHECK ((revoked_at IS NULL AND revoked_by IS NULL) OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_at >= granted_at)),
  CONSTRAINT "ck_user_roles_scope_type_domain" CHECK (scope_type IN ('global', 'department'))
);

CREATE UNIQUE INDEX "uq_supplier_product" ON "supplier_products" ("supplier_id", "product_id");

CREATE INDEX "ix_pr_owner_status" ON "purchase_requests" ("requested_by", "status");

CREATE INDEX "ix_pr_department_status" ON "purchase_requests" ("department_id", "status");

CREATE UNIQUE INDEX "uq_pr_item_scope" ON "purchase_request_items" ("request_item_id", "request_id");

CREATE UNIQUE INDEX "uq_pr_line_number" ON "purchase_request_items" ("request_id", "line_number");

CREATE UNIQUE INDEX "uq_task_request" ON "analysis_tasks" ("task_id", "request_id");

CREATE UNIQUE INDEX "uq_task_revision_scope" ON "analysis_tasks" ("task_id", "request_id", "input_revision");

CREATE UNIQUE INDEX "uq_task_monitor_scope" ON "analysis_tasks" ("task_id", "request_id", "po_item_id");

CREATE UNIQUE INDEX "uq_prediction_item_scope" ON "risk_predictions" ("prediction_id", "request_item_id", "request_id");

CREATE UNIQUE INDEX "uq_prediction_task_scope" ON "risk_predictions" ("prediction_id", "task_id", "request_id");

CREATE UNIQUE INDEX "uq_prediction_candidate_scope" ON "risk_predictions" ("prediction_id", "task_id", "request_item_id", "request_id", "supplier_id");

CREATE INDEX "ix_prediction_po_asof" ON "risk_predictions" ("po_item_id", "feature_asof");

CREATE UNIQUE INDEX "uq_report_request" ON "risk_analysis_reports" ("report_id", "request_id");

CREATE UNIQUE INDEX "uq_report_task_scope" ON "risk_analysis_reports" ("report_id", "task_id", "request_id");

CREATE UNIQUE INDEX "uq_proposal_request" ON "proposals" ("proposal_id", "request_id");

CREATE UNIQUE INDEX "uq_proposal_version" ON "proposals" ("request_id", "version_no");

CREATE UNIQUE INDEX "uq_proposal_generation_task" ON "proposals" ("generation_task_id");

CREATE UNIQUE INDEX "uq_proposal_item_scope" ON "proposal_items" ("proposal_item_id", "proposal_id", "request_id");

CREATE UNIQUE INDEX "uq_proposal_item_full_scope" ON "proposal_items" ("proposal_item_id", "proposal_id", "request_id", "request_item_id");

CREATE UNIQUE INDEX "uq_po_scope" ON "purchase_orders" ("purchase_order_id", "proposal_id", "request_id");

CREATE UNIQUE INDEX "uq_po_issue_group" ON "purchase_orders" ("proposal_id", "supplier_id", "currency");

CREATE UNIQUE INDEX "uq_po_item_pr_scope" ON "purchase_order_items" ("po_item_id", "request_id", "request_item_id");

CREATE UNIQUE INDEX "uq_po_item_request" ON "purchase_order_items" ("po_item_id", "request_id");

CREATE UNIQUE INDEX "uq_po_line_number" ON "purchase_order_items" ("purchase_order_id", "line_number");

CREATE UNIQUE INDEX "uq_schedule_number" ON "purchase_order_schedules" ("po_item_id", "schedule_number");

CREATE INDEX "ix_receipt_knowledge" ON "goods_receipts" ("schedule_id", "received_at");

CREATE UNIQUE INDEX "uq_confirmation_detail_version" ON "supplier_confirmation_items" ("confirmation_id", "schedule_id", "revision_no");

CREATE INDEX "ix_promise_knowledge" ON "supplier_confirmation_items" ("schedule_id", "recorded_at", "revision_no");

CREATE UNIQUE INDEX "uq_user_external_identity" ON "users" ("external_issuer", "external_subject");

CREATE UNIQUE INDEX "uq_role_code" ON "roles" ("role_code");

CREATE UNIQUE INDEX "uq_role_scope" ON "roles" ("role_id", "scope_type");

COMMENT ON TABLE "suppliers" IS 'C01 | suppliers [P0] - Thông tin nhà cung cấp và định danh tham chiếu. ';

COMMENT ON COLUMN "suppliers"."supplier_id" IS 'Khóa nội bộ | PK, NN';

COMMENT ON COLUMN "suppliers"."supplier_code" IS 'Mã hiển thị/API | UNIQUE, NN';

COMMENT ON COLUMN "suppliers"."supplier_name" IS 'Tên nhà cung cấp | NN';

COMMENT ON COLUMN "suppliers"."email" IS 'Email liên hệ | NULL';

COMMENT ON COLUMN "suppliers"."phone" IS 'Điện thoại | NULL';

COMMENT ON COLUMN "suppliers"."address" IS 'Địa chỉ | NULL';

COMMENT ON COLUMN "suppliers"."city" IS 'Vị trí supplier | NULL';

COMMENT ON COLUMN "suppliers"."postal_code" IS 'Mã bưu chính | NULL';

COMMENT ON COLUMN "suppliers"."country_of_origin" IS 'Mã quốc gia ISO alpha-2 | NULL';

COMMENT ON COLUMN "suppliers"."status" IS 'active / inactive / blocked | NN, CHECK';

COMMENT ON COLUMN "suppliers"."created_at" IS 'Thời điểm tạo | NN';

COMMENT ON COLUMN "suppliers"."updated_at" IS 'Thời điểm cập nhật | NN';

COMMENT ON TABLE "products" IS 'C02 | products [P0] - Danh mục sản phẩm và feature loại sản phẩm. ';

COMMENT ON COLUMN "products"."product_id" IS 'ID sản phẩm | PK, NN';

COMMENT ON COLUMN "products"."product_code" IS 'Part number nội bộ | UNIQUE, NN';

COMMENT ON COLUMN "products"."product_name" IS 'Tên sản phẩm | NN';

COMMENT ON COLUMN "products"."product_category" IS 'Feature categorical | NN';

COMMENT ON COLUMN "products"."unit" IS 'Đơn vị tính | NN';

COMMENT ON COLUMN "products"."description" IS 'Mô tả | NULL';

COMMENT ON COLUMN "products"."status" IS 'active / inactive | NN, CHECK';

COMMENT ON COLUMN "products"."created_at" IS 'Ngày tạo | NN';

COMMENT ON COLUMN "products"."updated_at" IS 'Ngày sửa | NN';

COMMENT ON TABLE "supplier_products" IS 'C03 | supplier_products [P0] - Quan hệ N-N supplier-product và báo giá hiện hành. ';

COMMENT ON COLUMN "supplier_products"."supplier_product_id" IS 'ID báo giá hiện hành | PK, NN';

COMMENT ON COLUMN "supplier_products"."supplier_id" IS '-> suppliers | FK, NN';

COMMENT ON COLUMN "supplier_products"."product_id" IS '-> products | FK, NN';

COMMENT ON COLUMN "supplier_products"."supplier_part_number" IS 'SKU phía supplier | NULL';

COMMENT ON COLUMN "supplier_products"."current_unit_price" IS 'Đơn giá hiện hành, không thay giá PO | NN, >=0';

COMMENT ON COLUMN "supplier_products"."currency" IS 'ISO 4217 | NN';

COMMENT ON COLUMN "supplier_products"."min_order_qty" IS 'Ngưỡng tối thiểu nếu có | NULL, >0';

COMMENT ON COLUMN "supplier_products"."quoted_lead_days" IS 'Lead time được báo giá; không phải feature lịch sử | NULL, >=0';

COMMENT ON COLUMN "supplier_products"."is_available" IS 'Khả năng cung ứng | NN';

COMMENT ON COLUMN "supplier_products"."updated_at" IS 'Ngày cập nhật; UNIQUE(supplier_id,product_id) trong MVP | NN';

COMMENT ON TABLE "purchase_requests" IS 'C04 | purchase_requests [P0] - PR cá nhân do nhân viên tạo. Mỗi nhân viên tự tạo PR cá nhân. requested_by lấy từ danh tính đã xác thực. department_id là snapshot lúc tạo, không đổi theo việc chuyển phòng của user. Quyền EMPLOYEE chỉ cho PR của mình.';

COMMENT ON COLUMN "purchase_requests"."request_id" IS 'ID PR | PK, NN';

COMMENT ON COLUMN "purchase_requests"."request_code" IS 'PR-001 | UNIQUE, NN';

COMMENT ON COLUMN "purchase_requests"."department_id" IS '-> departments. Snapshot phòng ban của nhân viên tại lúc lập PR; không gom PR qua trưởng phòng và không đổi theo việc user chuyển phòng. | FK, NN';

COMMENT ON COLUMN "purchase_requests"."requested_by" IS '-> users.user_id của cá nhân nhân viên tạo/gửi PR. Server lấy từ danh tính đã xác thực; cần active human và grant EMPLOYEE. | FK, NN';

COMMENT ON COLUMN "purchase_requests"."source" IS 'web / chat / api | NN, CHECK';

COMMENT ON COLUMN "purchase_requests"."status" IS 'draft / analysing / needs_review / analysis_failed / pending_approval / approved / rejected / ordered / cancelled. | NN, CHECK';

COMMENT ON COLUMN "purchase_requests"."input_revision" IS 'Tăng khi chủ PR sửa product/quantity/deadline/supplier preference trong trạng thái cho phép; sinh snapshot mới. Không sửa input của PR có PO chưa hủy hợp lệ. | NN, DEFAULT 1, >0';

COMMENT ON COLUMN "purchase_requests"."submitted_at" IS 'Nhân viên hoàn tất/gửi PR cá nhân. Tạo đầy đủ có thể lập+gửi cùng hành động; draft thiếu dữ liệu không tự sinh proposal. | NULL';

COMMENT ON COLUMN "purchase_requests"."notes" IS 'Ghi chú chung | NULL';

COMMENT ON COLUMN "purchase_requests"."created_at" IS 'Thời điểm lập PR/draft; feature_asof dùng event tạo/gửi PR đầy đủ theo quy tắc ở E. | NN';

COMMENT ON COLUMN "purchase_requests"."updated_at" IS 'Thời điểm cập nhật | NN';

COMMENT ON TABLE "purchase_request_items" IS 'C05 | purchase_request_items [P0] - Các dòng sản phẩm của PR cá nhân. ';

COMMENT ON COLUMN "purchase_request_items"."request_item_id" IS 'ID dòng PR | PK, NN';

COMMENT ON COLUMN "purchase_request_items"."request_id" IS '-> purchase_requests | FK, NN';

COMMENT ON COLUMN "purchase_request_items"."line_number" IS 'UNIQUE(request_id,line_number) | NN, >0';

COMMENT ON COLUMN "purchase_request_items"."product_id" IS '-> products | FK, NN';

COMMENT ON COLUMN "purchase_request_items"."preferred_supplier_id" IS 'Supplier mong muốn; NULL nếu chưa chọn. Không phải supplier thực giao của nhãn training. | FK, NULL';

COMMENT ON COLUMN "purchase_request_items"."quantity" IS 'Lượng cá nhân yêu cầu mua trên dòng sản phẩm/đơn vị/deadline của PR. Cùng sản phẩm nhưng deadline khác có thể là các dòng riêng. | NN, >0';

COMMENT ON COLUMN "purchase_request_items"."requested_delivery_date" IS 'Hạn giao mong muốn | NN';

COMMENT ON COLUMN "purchase_request_items"."unit_price_snapshot" IS 'Giá snapshot; null nếu chưa báo giá | NULL, >=0';

COMMENT ON COLUMN "purchase_request_items"."currency" IS 'ISO 4217 | NN';

COMMENT ON COLUMN "purchase_request_items"."line_total" IS 'Server tính; có thể null nếu giá chưa có | NULL, >=0';

COMMENT ON COLUMN "purchase_request_items"."notes" IS 'Ghi chú dòng PR | NULL';

COMMENT ON TABLE "analysis_tasks" IS 'C06 | analysis_tasks [P0] - AI phân tích PR hoặc theo dõi PO theo snapshot. Input snapshot và cutoff bất biến. Task po_monitoring gắn một PO item, không sinh proposal. request_id luôn có FK trực tiếp kể cả khi po_item_id NULL.';

COMMENT ON COLUMN "analysis_tasks"."task_id" IS 'ID tác vụ | PK, NN';

COMMENT ON COLUMN "analysis_tasks"."request_id" IS '-> purchase_requests | FK, NN';

COMMENT ON COLUMN "analysis_tasks"."po_item_id" IS 'NULL với initial_analysis/reanalysis; bắt buộc với po_monitoring và thuộc cùng request_id. | FK ghép, NULL theo loại task';

COMMENT ON COLUMN "analysis_tasks"."task_type" IS 'initial_analysis / reanalysis / po_monitoring. Chỉ initial_analysis hoặc reanalysis của PR đủ điều kiện được sinh proposal. | NN, CHECK';

COMMENT ON COLUMN "analysis_tasks"."input_revision" IS 'Revision từ event. Kết quả revision cũ không được thay kết quả hiện hành. | NN, >0';

COMMENT ON COLUMN "analysis_tasks"."feature_asof" IS 'Mốc event hoặc request_at cố định; worker retry giữ nguyên. | NN';

COMMENT ON COLUMN "analysis_tasks"."input_snapshot" IS 'Bất biến: revision, PR items hoặc PO item, category/unit/quantity/deadline và nguồn biết tại feature_asof. Retry không đọc lại input đang thay đổi. | NN, object';

COMMENT ON COLUMN "analysis_tasks"."idempotency_key" IS 'UNIQUE theo event_id + scope + revision. Task monitoring gồm po_item_id và cutoff; cùng event retry trả task cũ. | UNIQUE, NN';

COMMENT ON COLUMN "analysis_tasks"."status" IS 'queued / running / succeeded / failed | NN, CHECK';

COMMENT ON COLUMN "analysis_tasks"."started_at" IS 'Thời điểm chạy | NULL';

COMMENT ON COLUMN "analysis_tasks"."completed_at" IS 'Thời điểm hoàn tất | NULL';

COMMENT ON COLUMN "analysis_tasks"."error_code" IS 'Mã lỗi | NULL';

COMMENT ON COLUMN "analysis_tasks"."error_message" IS 'Chi tiết lỗi | NULL';

COMMENT ON COLUMN "analysis_tasks"."created_at" IS 'Ngày tạo tác vụ | NN';

COMMENT ON TABLE "risk_predictions" IS 'C07 | risk_predictions [P0] - Snapshot 19 thuộc tính cố định A B và kết quả TabICLv2. Một hợp đồng 19 X cho PR_CREATED và PO_MONITORING; x luôn đủ key, giá trị chưa biết là JSON null, không thêm cờ thiếu dữ liệu. Không dùng y hoặc thời điểm hoàn thành thực tế làm X. Điều kiện success, provenance, đồng nhất x/raw và numeric domains được adapter kiểm tra. CHECK ck_fixed_x_shape dùng toán tử JSONB trực tiếp, không tạo hàm riêng.';

COMMENT ON COLUMN "risk_predictions"."prediction_id" IS 'ID lần dự đoán | PK, NN';

COMMENT ON COLUMN "risk_predictions"."task_id" IS '-> analysis_tasks. Nếu có task thì bắt buộc có PR/item/revision cùng task; chat độc lập task=NULL. | FK, NULL';

COMMENT ON COLUMN "risk_predictions"."request_item_id" IS '-> PR item. request_item_id và request_id cùng NULL hoặc cùng có; ghép FK để bảo đảm cùng PR. | FK, NULL';

COMMENT ON COLUMN "risk_predictions"."request_id" IS 'PR của request_item_id và task_id. NULL chỉ khi kịch bản độc lập không gắn PR. | FK, NULL theo scope';

COMMENT ON COLUMN "risk_predictions"."input_revision" IS 'Revision đầu vào; task có mặt thì khớp analysis_tasks.input_revision. | NULL theo scope; >0 nếu có PR';

COMMENT ON COLUMN "risk_predictions"."source_type" IS 'pr_initial / pr_reanalysis / chat_ad_hoc / comparison / proposal_selection / po_monitoring / historical_import. | NN, CHECK';

COMMENT ON COLUMN "risk_predictions"."prediction_stage" IS 'PR_CREATED / PO_MONITORING; phải bằng feature_snapshot.x.prediction_stage. | NN, CHECK';

COMMENT ON COLUMN "risk_predictions"."po_item_id" IS 'NULL ở PR_CREATED; bắt buộc ở PO_MONITORING, cùng request_id/request_item_id với PO item. | FK ghép, NULL theo stage';

COMMENT ON COLUMN "risk_predictions"."idempotency_key" IS 'Khóa event/task + stage + scenario supplier/product/quantity/deadline + cutoff; retry không sinh dự đoán logic mới. | UNIQUE, NN';

COMMENT ON COLUMN "risk_predictions"."supplier_id" IS 'Supplier được score | FK, NN';

COMMENT ON COLUMN "risk_predictions"."product_id" IS 'Product được score | FK, NN';

COMMENT ON COLUMN "risk_predictions"."quantity" IS 'Q gốc của phương án ở A hoặc ordered_quantity của PO item ở B; không thay bằng lượng chưa nhận. | NN, >0';

COMMENT ON COLUMN "risk_predictions"."requested_delivery_date" IS 'Hạn giao của kịch bản | NN';

COMMENT ON COLUMN "risk_predictions"."feature_asof" IS 'Mốc snapshot A hoặc B cố định theo event. Phân biệt với predicted_at là lúc worker ghi kết quả. | NN';

COMMENT ON COLUMN "risk_predictions"."requested_lead_days" IS 'Audit mirror of signed X.days_to_deadline; nonnegative at PR_CREATED, may be negative at PO_MONITORING.';

COMMENT ON COLUMN "risk_predictions"."feature_schema_version" IS 'tabicl_supplier_fixed_v1: đúng 19 key X ở E, cùng schema cho A/B, không missing indicators. | NN';

COMMENT ON COLUMN "risk_predictions"."preprocessing_version" IS 'Phiên bản adapter, dtype, NULL->NaN và tính lịch sử; giữ cùng train/serve. | NN';

COMMENT ON COLUMN "risk_predictions"."model_name" IS 'TabICLv2; bắt buộc khi success. | NULL';

COMMENT ON COLUMN "risk_predictions"."model_version" IS 'tabicl-classifier-v2-20260212.ckpt; bắt buộc khi success. | NULL';

COMMENT ON COLUMN "risk_predictions"."context_dataset_version" IS 'Phiên bản context chung có các snapshot A/B và nhãn đã biết trước cutoff; khác checkpoint. Cùng PO item không nằm cả context và test. | NULL; NN khi success';

COMMENT ON COLUMN "risk_predictions"."context_metadata" IS 'Manifest: hash dữ liệu/checkpoint, label cutoff, split, cấu hình estimator, thư viện và fitted artifact. | NULL; object khi success';

COMMENT ON COLUMN "risk_predictions"."risk_score" IS 'Score; không gọi là xác suất nếu chưa hiệu chuẩn | NULL, 0..1';

COMMENT ON COLUMN "risk_predictions"."risk_threshold" IS 'Ngưỡng tại lần chạy; bắt buộc khi success | NULL, 0..1';

COMMENT ON COLUMN "risk_predictions"."risk_level" IS 'low / high; chỉ khi score hợp lệ | NULL, CHECK';

COMMENT ON COLUMN "risk_predictions"."feature_snapshot" IS 'JSONB gồm x đủ 19 key, metadata q_ref/unit/deadline/asof/history/calendar/source freshness và provenance. NULL trong x được giữ; không đưa y hoặc actual completion vào x. | NN, object';

COMMENT ON COLUMN "risk_predictions"."status" IS 'success / insufficient_data / failed / snapshot_only. snapshot_only chỉ cho historical_import có nguồn xác thực. | NN, CHECK';

COMMENT ON COLUMN "risk_predictions"."error_code" IS 'Mã lỗi khi không thành công | NULL';

COMMENT ON COLUMN "risk_predictions"."error_message" IS 'Lỗi/thiếu dữ liệu | NULL';

COMMENT ON COLUMN "risk_predictions"."predicted_at" IS 'Mốc ghi kết quả/snapshot; có thể sau feature_asof. Không dùng làm mốc xây lại X. | NN';

COMMENT ON TABLE "risk_analysis_reports" IS 'C08 | risk_analysis_reports [P0] - Báo cáo theo task; các phương án cụ thể lưu bảng candidate. report_data chứa decision_status, policy version và document_refs/citations tới kho Markdown hiện có; không nhân bản bảng document. Các quan hệ ghép giữ report cùng task và PR.';

COMMENT ON COLUMN "risk_analysis_reports"."report_id" IS 'ID báo cáo | PK, NN';

COMMENT ON COLUMN "risk_analysis_reports"."task_id" IS '-> analysis_tasks | FK, NN';

COMMENT ON COLUMN "risk_analysis_reports"."request_id" IS '-> purchase_requests | FK, NN';

COMMENT ON COLUMN "risk_analysis_reports"."primary_prediction_id" IS '-> risk_predictions theo prediction_id + task_id + request_id. Không được lấy prediction của task khác. | FK, NULL';

COMMENT ON COLUMN "risk_analysis_reports"."risk_level" IS 'Mức rủi ro tổng hợp | NULL';

COMMENT ON COLUMN "risk_analysis_reports"."summary" IS 'Tóm tắt hiển thị | NN';

COMMENT ON COLUMN "risk_analysis_reports"."report_data" IS 'decision_status ready / needs_review / failed; selection_policy_version, mục tiêu/điều kiện, ứng viên/chọn phương án và document_refs từ kho Markdown hiện có. Lưu nguồn theo version/chunk, không nhân bản nội dung document. | NN, object';

COMMENT ON COLUMN "risk_analysis_reports"."created_at" IS 'Không ghi đè report cũ | NN';

COMMENT ON TABLE "analysis_supplier_candidates" IS 'C09 | analysis_supplier_candidates [P0] - Ứng viên và bằng chứng để AI tự chọn phương án. Prediction nếu có phải cùng task/PR/item/supplier. supplier_id vẫn cần FK trực tiếp khi prediction_id NULL.';

COMMENT ON COLUMN "analysis_supplier_candidates"."candidate_id" IS 'ID ứng viên | PK, NN';

COMMENT ON COLUMN "analysis_supplier_candidates"."report_id" IS '-> risk_analysis_reports | FK, NN';

COMMENT ON COLUMN "analysis_supplier_candidates"."task_id" IS 'Task của report; prediction nếu có phải thuộc cùng task. | FK ghép, NN';

COMMENT ON COLUMN "analysis_supplier_candidates"."request_id" IS 'PR của report và request_item_id; không chỉ FK từng ID riêng lẻ. | FK ghép, NN';

COMMENT ON COLUMN "analysis_supplier_candidates"."request_item_id" IS 'Dòng cần tìm phương án | FK, NN';

COMMENT ON COLUMN "analysis_supplier_candidates"."supplier_id" IS 'Supplier ứng viên | FK, NN';

COMMENT ON COLUMN "analysis_supplier_candidates"."prediction_id" IS '-> risk_predictions; cùng task/request/item/supplier với candidate. NULL nếu chưa có kịch bản chấm. | FK, NULL';

COMMENT ON COLUMN "analysis_supplier_candidates"."unit_price_snapshot" IS 'Giá dùng lúc so sánh | NULL, >=0';

COMMENT ON COLUMN "analysis_supplier_candidates"."currency" IS 'Tiền tệ của giá | NULL';

COMMENT ON COLUMN "analysis_supplier_candidates"."rank" IS 'Hạng theo từng report/item nếu có | NULL, >0';

COMMENT ON COLUMN "analysis_supplier_candidates"."is_eligible" IS 'Có qua bộ lọc nghiệp vụ không | NN';

COMMENT ON COLUMN "analysis_supplier_candidates"."reason" IS 'Lý do, thiếu dữ liệu, cảnh báo | NULL';

COMMENT ON COLUMN "analysis_supplier_candidates"."evidence_refs" IS 'Document ID/version/chunk/source URI/checksum và scope quyền từ kho Markdown PostgreSQL hiện có; reference tích hợp, không FK tới bảng document trong schema này. | NULL';

COMMENT ON TABLE "proposals" IS 'C10 | proposals [P0] - AI tự tạo và gửi proposal phiên bản hiện hành. AI_SERVICE tự tạo proposal pending và gửi duyệt qua outbox; không có người lập proposal. Khóa PR, kiểm tra current revision, report ready và task initial/reanalysis. Chỉ một proposal is_current cho mỗi PR: áp dụng partial UNIQUE ghi ở cuối file, không dùng UNIQUE(request_id) toàn bảng.';

COMMENT ON COLUMN "proposals"."proposal_id" IS 'ID phiếu | PK, NN';

COMMENT ON COLUMN "proposals"."proposal_code" IS 'Mã đề xuất | UNIQUE, NN';

COMMENT ON COLUMN "proposals"."request_id" IS '-> purchase_requests | FK, NN';

COMMENT ON COLUMN "proposals"."version_no" IS 'UNIQUE(request_id, version_no); cấp phiên bản trong transaction khóa PR. | NN, >0';

COMMENT ON COLUMN "proposals"."input_revision" IS 'Revision PR được đề xuất; phải khớp hiện hành khi submit/approve/issue. | NN, >0';

COMMENT ON COLUMN "proposals"."supersedes_proposal_id" IS 'Phiên bản trước cùng request_id; không tự tham chiếu; version_no tăng. | FK self, NULL';

COMMENT ON COLUMN "proposals"."is_current" IS 'Chỉ một proposal hiện hành/PR bằng partial unique index. approved bắt buộc current. | NN, DEFAULT TRUE';

COMMENT ON COLUMN "proposals"."analysis_report_id" IS '-> report của generation_task_id và cùng request_id. Report phải chứng minh phương án đủ điều kiện trước khi AI gửi. | FK ghép, NN';

COMMENT ON COLUMN "proposals"."generation_task_id" IS 'Task AI tạo proposal; cùng request_id/input_revision và đúng report. Một task tạo tối đa một proposal. | FK ghép, NN, UNIQUE';

COMMENT ON COLUMN "proposals"."selection_policy_version" IS 'Phiên bản chính sách chọn phương án từ ứng viên hợp lệ; lưu trọng số/điều kiện thực tế trong report_data. | NN';

COMMENT ON COLUMN "proposals"."workflow_instance_id" IS 'ID luồng duyệt do workflow trả về khi xử lý outbox; callback phải khớp proposal/version. | NULL';

COMMENT ON COLUMN "proposals"."reason" IS 'Lý do chung | NULL';

COMMENT ON COLUMN "proposals"."status" IS 'pending / approved / rejected / superseded / cancelled. Proposal mới do AI tạo ở pending và tự gửi duyệt; không có draft do người lập proposal. | NN, CHECK';

COMMENT ON COLUMN "proposals"."created_by" IS '-> users.user_id tài khoản service có AI_SERVICE. AI tạo/gửi proposal; không có role người lập proposal thủ công. | FK, NN';

COMMENT ON COLUMN "proposals"."submitted_at" IS 'Mốc AI tạo logical request gửi duyệt trong transaction proposal/items/outbox. Khác submitted_at của nhân viên trên PR; chưa khẳng định workflow đã nhận. | NN';

COMMENT ON COLUMN "proposals"."created_at" IS 'Tạo phiếu | NN';

COMMENT ON TABLE "proposal_items" IS 'C11 | proposal_items [P0] - Dòng được chọn và prediction cùng hồ sơ. Chỉ dùng prediction success PR_CREATED, cùng PR/item/revision và supplier/product/quantity/deadline. Quan hệ FK không tự kiểm tra toàn bộ điều kiện nghiệp vụ này.';

COMMENT ON COLUMN "proposal_items"."proposal_item_id" IS 'ID dòng phiếu | PK, NN';

COMMENT ON COLUMN "proposal_items"."proposal_id" IS '-> proposals | FK, NN';

COMMENT ON COLUMN "proposal_items"."request_id" IS 'Scope từ proposal; phải cùng PR với request_item_id và prediction_id. | FK ghép, NN';

COMMENT ON COLUMN "proposal_items"."request_item_id" IS '-> purchase_request_items | FK, NN';

COMMENT ON COLUMN "proposal_items"."selected_supplier_id" IS '-> suppliers; có thể khác giữa các dòng | FK, NN';

COMMENT ON COLUMN "proposal_items"."prediction_id" IS 'Prediction success PR_CREATED cùng PR/item/revision và supplier/product/quantity/deadline của phương án. Không dùng prediction monitoring hoặc snapshot_only để lập proposal. | FK, NN';

COMMENT ON COLUMN "proposal_items"."quantity" IS 'Số lượng cho supplier cụ thể. Đổi quantity phải tạo snapshot/prediction mới trước submit proposal. | NN, >0';

COMMENT ON COLUMN "proposal_items"."unit_price_snapshot" IS 'Đơn giá lúc duyệt | NN, >=0';

COMMENT ON COLUMN "proposal_items"."currency" IS 'Tiền tệ | NN';

COMMENT ON COLUMN "proposal_items"."proposed_delivery_date" IS 'Hạn cần đáp ứng; phải bằng deadline prediction được gắn, không thay bằng ngày supplier hứa. | NN';

COMMENT ON COLUMN "proposal_items"."reason" IS 'Lý do tại dòng | NULL';

COMMENT ON TABLE "approval_history" IS 'C12 | approval_history [P0] - Lịch sử quyết định do workflow quản lý. Active human có APPROVER đúng department grant và được workflow giao mới được duyệt current proposal/version. ADMIN, AI_SERVICE và PO_SERVICE không tự có quyền duyệt. Rejected cần lý do theo workflow.';

COMMENT ON COLUMN "approval_history"."approval_id" IS 'ID quyết định | PK, NN';

COMMENT ON COLUMN "approval_history"."proposal_id" IS '-> proposals | FK, NN';

COMMENT ON COLUMN "approval_history"."workflow_task_id" IS 'Task từ workflow bên ngoài | NULL';

COMMENT ON COLUMN "approval_history"."approver_id" IS '-> users.user_id người active human có APPROVER trong phạm vi phòng ban của PR. Quyết định còn phải đúng workflow/proposal/version. | FK, NN';

COMMENT ON COLUMN "approval_history"."decision" IS 'approved / rejected | NN, CHECK';

COMMENT ON COLUMN "approval_history"."reason" IS 'Bắt buộc nếu rejected | NULL';

COMMENT ON COLUMN "approval_history"."decided_at" IS 'Thời gian quyết định | NN';

COMMENT ON TABLE "purchase_orders" IS 'C13 | purchase_orders [P0] - PO theo supplier và currency của proposal. PO_SERVICE phát PO từ proposal current+approved đúng revision, nhóm theo supplier+currency. Transaction khóa PR và cộng số lượng cam kết trên mọi PO chưa hủy qua các proposal versions. MVP chỉ hủy hợp lệ khi chưa có receipts.';

COMMENT ON COLUMN "purchase_orders"."purchase_order_id" IS 'ID PO | PK, NN';

COMMENT ON COLUMN "purchase_orders"."po_number" IS 'Mã PO | UNIQUE, NN';

COMMENT ON COLUMN "purchase_orders"."request_id" IS '-> purchase_requests | FK, NN';

COMMENT ON COLUMN "purchase_orders"."proposal_id" IS '-> proposals cùng request_id; chỉ current+approved+revision hợp lệ mới issue. UNIQUE(proposal_id,supplier_id,currency). | FK, NN';

COMMENT ON COLUMN "purchase_orders"."supplier_id" IS '-> suppliers | FK, NN';

COMMENT ON COLUMN "purchase_orders"."order_date" IS 'Mốc phát hành PO (khác submitted_at PR) | NN';

COMMENT ON COLUMN "purchase_orders"."status" IS 'issued / waiting_confirmation / confirmed / rejected / partially_received / delivered / cancelled. cancelled chỉ sau hủy hợp lệ và không có receipts trong MVP. | NN, CHECK';

COMMENT ON COLUMN "purchase_orders"."currency" IS 'Một ISO 4217 trên PO. Tách PO theo supplier_id + currency; MVP không tự quy đổi FX. | NN';

COMMENT ON COLUMN "purchase_orders"."total_amount" IS 'Tổng snapshot của PO items | NN, >=0';

COMMENT ON COLUMN "purchase_orders"."pdf_file_id" IS 'Tham chiếu PDF đã tạo | NULL';

COMMENT ON COLUMN "purchase_orders"."created_by" IS '-> users.user_id tài khoản active service có PO_SERVICE; chỉ phát từ proposal đã được duyệt hợp lệ. | FK, NN';

COMMENT ON COLUMN "purchase_orders"."created_at" IS 'Ngày tạo | NN';

COMMENT ON COLUMN "purchase_orders"."updated_at" IS 'Ngày cập nhật | NN';

COMMENT ON TABLE "purchase_order_items" IS 'C14 | purchase_order_items [P0] - PO line; truy ngược PR qua proposal_item_id. Snapshot sản phẩm/category/unit/giá/lượng bất biến. Một proposal item chỉ phát một PO item. Budget lượng phải kiểm tra trong transaction.';

COMMENT ON COLUMN "purchase_order_items"."po_item_id" IS 'ID dòng PO | PK, NN';

COMMENT ON COLUMN "purchase_order_items"."purchase_order_id" IS '-> purchase_orders | FK, NN';

COMMENT ON COLUMN "purchase_order_items"."proposal_item_id" IS '-> proposal_items; lấy prediction_id qua dòng này. Một PO item là một mẫu outcome training. | FK, NN, UNIQUE';

COMMENT ON COLUMN "purchase_order_items"."proposal_id" IS 'Cùng proposal với PO header và proposal_item_id. | FK ghép, NN';

COMMENT ON COLUMN "purchase_order_items"."request_id" IS 'Cùng PR với PO header và proposal item. Dùng scope ổn định cho kiểm tra lượng. | FK ghép, NN';

COMMENT ON COLUMN "purchase_order_items"."request_item_id" IS 'PR item nguồn; khớp cả proposal_item và request_id. Phục vụ FK monitoring và budget. | FK ghép, NN';

COMMENT ON COLUMN "purchase_order_items"."line_number" IS 'UNIQUE(purchase_order_id,line_number) | NN, >0';

COMMENT ON COLUMN "purchase_order_items"."product_id" IS '-> products; phải trùng sản phẩm của proposal_item | FK, NN';

COMMENT ON COLUMN "purchase_order_items"."product_category_snapshot" IS 'Category tại phát hành từ snapshot được duyệt; không lấy category hiện tại để sửa lịch sử. | NN';

COMMENT ON COLUMN "purchase_order_items"."ordered_quantity" IS 'Số lượng đã issue; cộng trên mọi proposal/PO chưa cancelled của PR item không vượt lượng PR. | NN, >0';

COMMENT ON COLUMN "purchase_order_items"."unit" IS 'Đơn vị Q đóng băng khi issue; q_ref của lịch sử phải cùng sản phẩm và đơn vị. | NN';

COMMENT ON COLUMN "purchase_order_items"."unit_price" IS 'Giá snapshot từ phiếu đã duyệt | NN, >=0';

COMMENT ON COLUMN "purchase_order_items"."line_total" IS 'Server tính quantity × price | NN, >=0';

COMMENT ON COLUMN "purchase_order_items"."item_status" IS 'open / confirmed / partially_received / completed / cancelled | NN, CHECK';

COMMENT ON TABLE "purchase_order_schedules" IS 'C15 | purchase_order_schedules [P0] - Lịch giao từng item; granular dataset Dataiku-compatible. SUM scheduled_quantity phải bằng ordered_quantity; ngày yêu cầu gốc cố định cùng PO item. promised_delivery_date là cache hiển thị, lịch sử dự đoán đọc confirmation detail revisions biết trước cutoff.';

COMMENT ON COLUMN "purchase_order_schedules"."schedule_id" IS 'Transaction ID ở mức schedule của hệ thống | PK, NN';

COMMENT ON COLUMN "purchase_order_schedules"."po_item_id" IS '-> purchase_order_items | FK, NN';

COMMENT ON COLUMN "purchase_order_schedules"."schedule_number" IS 'UNIQUE(po_item_id,schedule_number) | NN, >0';

COMMENT ON COLUMN "purchase_order_schedules"."scheduled_quantity" IS 'Lượng theo đợt; tổng bằng ordered_quantity. Muốn nhiều deadline phải tách proposal/PO item. | NN, >0';

COMMENT ON COLUMN "purchase_order_schedules"."requested_delivery_date" IS 'Deadline snapshot = proposed_delivery_date của proposal item; tất cả schedule cùng PO item dùng cùng deadline. | NN';

COMMENT ON COLUMN "purchase_order_schedules"."promised_delivery_date" IS 'Cache ngày hứa hiện hành để hiển thị. Dựng snapshot lịch sử phải đọc phiên bản supplier_confirmation_items đã biết tại cutoff, không đọc cache hiện tại. | NULL';

COMMENT ON COLUMN "purchase_order_schedules"."status" IS 'open / confirmed / partially_received / completed / cancelled | NN, CHECK';

COMMENT ON COLUMN "purchase_order_schedules"."created_at" IS 'Thời điểm tạo schedule | NN';

COMMENT ON TABLE "goods_receipts" IS 'C16 | goods_receipts [P0] - Ghi nhận nhận hàng từng phần; nguồn nhãn ML lịch sử. received_quantity là lượng hàng đạt yêu cầu. Append-only, tổng lượng không vượt schedule. received_at là knowledge timestamp do server ghi, không backdate. recorded_by chỉ nullable cho legacy import có provenance.';

COMMENT ON COLUMN "goods_receipts"."receipt_id" IS 'ID lần nhận | PK, NN';

COMMENT ON COLUMN "goods_receipts"."schedule_id" IS '-> purchase_order_schedules | FK, NN';

COMMENT ON COLUMN "goods_receipts"."received_quantity" IS 'Lượng nhận đạt yêu cầu; không tính hàng lỗi/chưa được chấp nhận vào completion. Tổng không vượt schedule trong MVP. | NN, >0';

COMMENT ON COLUMN "goods_receipts"."actual_delivery_date" IS 'Ngày thực nhận hàng; có thể khác ngày ghi nhận. Receipt append-only trong MVP. | NN';

COMMENT ON COLUMN "goods_receipts"."received_at" IS 'Thời điểm hệ thống biết lần nhận hàng; không backdate. Dùng cutoff lịch sử và nhãn/context. | NN';

COMMENT ON COLUMN "goods_receipts"."recorded_by" IS '-> users.user_id người ghi nhận; thao tác mới qua UI cần active human có WAREHOUSE cho phòng ban của PR. Import legacy có provenance có thể NULL. | FK, NULL với legacy';

COMMENT ON COLUMN "goods_receipts"."notes" IS 'Ghi chú | NULL';

COMMENT ON TABLE "confirmation_tokens" IS 'C17 | confirmation_tokens [P0] - Token độc lập được cấp trước khi supplier phản hồi. Token nghiệp vụ supplier một lần, chỉ lưu hash SHA-256; không phải mật khẩu hay token đăng nhập user. Xác nhận phải dùng token chưa consumed/revoked/expired và thuộc cùng PO.';

COMMENT ON COLUMN "confirmation_tokens"."token_id" IS 'ID token | PK, NN';

COMMENT ON COLUMN "confirmation_tokens"."purchase_order_id" IS '-> purchase_orders | FK, NN';

COMMENT ON COLUMN "confirmation_tokens"."token_hash" IS 'Hash SHA-256 hex của token ngẫu nhiên | UNIQUE, NN';

COMMENT ON COLUMN "confirmation_tokens"."expires_at" IS 'Phải > issued_at | NN';

COMMENT ON COLUMN "confirmation_tokens"."issued_at" IS 'Thời điểm cấp | NN';

COMMENT ON COLUMN "confirmation_tokens"."consumed_at" IS 'Chỉ set sau POST quyết định hợp lệ | NULL';

COMMENT ON COLUMN "confirmation_tokens"."revoked_at" IS 'Hủy token cũ khi cấp token mới nếu cần | NULL';

COMMENT ON TABLE "supplier_confirmations" IS 'C18 | supplier_confirmations [P0] - Phản hồi cấp PO; chỉ chốt một quyết định cho mỗi PO. Một quyết định cho mỗi PO/token. token_id phải thuộc purchase_order_id trong transaction; FK đơn lẻ chưa bảo đảm điều kiện này. responded_at là server knowledge timestamp bất biến.';

COMMENT ON COLUMN "supplier_confirmations"."confirmation_id" IS 'ID phản hồi | PK, NN';

COMMENT ON COLUMN "supplier_confirmations"."purchase_order_id" IS '-> purchase_orders; chặn ghi quyết định lặp | FK, NN, UNIQUE';

COMMENT ON COLUMN "supplier_confirmations"."token_id" IS '-> confirmation_tokens; token thuộc đúng PO | FK, NN, UNIQUE';

COMMENT ON COLUMN "supplier_confirmations"."decision" IS 'confirmed / rejected | NN, CHECK';

COMMENT ON COLUMN "supplier_confirmations"."reason" IS 'Bắt buộc nếu rejected | NULL';

COMMENT ON COLUMN "supplier_confirmations"."responded_at" IS 'Timestamp server ghi nhận quyết định được biết; không backdate từ giờ phía NCC. Header decision bất biến sau phản hồi hợp lệ. | NN';

COMMENT ON TABLE "supplier_confirmation_items" IS 'C19 | supplier_confirmation_items [P0] - Phiên bản chi tiết lượng và ngày hứa đã biết. Append-only revisions. Schedule phải thuộc cùng PO với confirmation, kiểm tra bằng transaction/API. Cập nhật cam kết tạo revision mới, không dùng lại consumed token. Lấy revision cuối đã biết tại T cho từng schedule còn thiếu hàng.';

COMMENT ON COLUMN "supplier_confirmation_items"."confirmation_item_id" IS 'ID chi tiết phản hồi | PK, NN';

COMMENT ON COLUMN "supplier_confirmation_items"."confirmation_id" IS '-> supplier_confirmations | FK, NN';

COMMENT ON COLUMN "supplier_confirmation_items"."schedule_id" IS '-> purchase_order_schedules của cùng PO | FK, NN';

COMMENT ON COLUMN "supplier_confirmation_items"."revision_no" IS 'Phiên bản chi tiết cam kết theo confirmation+schedule. Lưu append-only, cấp số khi khóa confirmation. | NN, DEFAULT 1, >0';

COMMENT ON COLUMN "supplier_confirmation_items"."confirmed_quantity" IS 'Chỉ ghi lượng NCC xác nhận rõ, không vượt schedule. Cho NULL khi chỉ có ngày hứa giao. | NULL; >0 nếu có';

COMMENT ON COLUMN "supplier_confirmation_items"."promised_delivery_date" IS 'Ngày supplier cam kết | NULL';

COMMENT ON COLUMN "supplier_confirmation_items"."recorded_at" IS 'Timestamp server biết phiên bản chi tiết; snapshot chỉ đọc recorded_at < feature_asof. | NN';

COMMENT ON COLUMN "supplier_confirmation_items"."notes" IS 'Ghi chú | NULL';

COMMENT ON TABLE "email_logs" IS 'C20 | email_logs [P0] - Gửi email PO có chống retry trùng. Payload bất biến. delivery_unknown cần đối soát hoặc provider idempotency trước khi retry. UNIQUE idempotency_key trong DB không bảo đảm SMTP gửi đúng một lần.';

COMMENT ON COLUMN "email_logs"."email_log_id" IS 'ID hoạt động gửi | PK, NN';

COMMENT ON COLUMN "email_logs"."purchase_order_id" IS '-> purchase_orders | FK, NULL';

COMMENT ON COLUMN "email_logs"."supplier_id" IS '-> suppliers | FK, NN';

COMMENT ON COLUMN "email_logs"."recipient_email" IS 'Email được dùng khi gửi | NN';

COMMENT ON COLUMN "email_logs"."email_type" IS 'po_issued / reminder / notification | NN';

COMMENT ON COLUMN "email_logs"."template_code" IS 'Mã template | NULL';

COMMENT ON COLUMN "email_logs"."subject" IS 'Tiêu đề | NULL';

COMMENT ON COLUMN "email_logs"."idempotency_key" IS 'UNIQUE cho một email logic trong DB; không tự bảo đảm provider/SMTP chỉ gửi một lần. | UNIQUE, NN';

COMMENT ON COLUMN "email_logs"."provider_idempotency_key" IS 'Khóa chuyển tới provider nếu hỗ trợ idempotency thực; không tự coi Message-ID là bảo đảm chống trùng. | NULL';

COMMENT ON COLUMN "email_logs"."payload_snapshot" IS 'Recipient, subject/body, template và file refs bất biến cho mọi lượt gửi của một logical email. | NN, object';

COMMENT ON COLUMN "email_logs"."status" IS 'queued / sending / sent / failed / delivery_unknown. Timeout sau khi có thể đã gửi => delivery_unknown. | NN, CHECK';

COMMENT ON COLUMN "email_logs"."attempt_count" IS 'Số lần thử | NN, >=0';

COMMENT ON COLUMN "email_logs"."last_attempt_at" IS 'Ghi trước khi gọi provider; lưu dấu vết khi worker ngắt hoặc timeout. | NULL';

COMMENT ON COLUMN "email_logs"."next_retry_at" IS 'Chỉ lên lịch khi chắc chắn chưa được nhận, hoặc dùng idempotency provider đã xác minh. NULL khi delivery_unknown chưa đối soát. | NULL';

COMMENT ON COLUMN "email_logs"."provider_message_id" IS 'ID phía nhà cung cấp email | NULL';

COMMENT ON COLUMN "email_logs"."sent_at" IS 'Mốc thành công | NULL';

COMMENT ON COLUMN "email_logs"."error_message" IS 'Lỗi lần gần nhất | NULL';

COMMENT ON COLUMN "email_logs"."created_at" IS 'Thời điểm tạo | NN';

COMMENT ON TABLE "outbox_events" IS 'C21 | outbox_events [P0] - Ghi sự kiện trong cùng transaction tạo PR/approval/PO, phát sau commit. Ghi sự kiện cùng transaction nghiệp vụ, phát sau commit với xử lý retry/idempotency. aggregate_type+aggregate_id là tham chiếu đa loại, không tạo FK giả tới một bảng. Không lưu credential/Authorization header trong payload.';

COMMENT ON COLUMN "outbox_events"."event_id" IS 'ID event | PK, NN';

COMMENT ON COLUMN "outbox_events"."aggregate_type" IS 'purchase_request / proposal / purchase_order / user / user_role theo sự kiện. | NN';

COMMENT ON COLUMN "outbox_events"."aggregate_id" IS 'ID nghiệp vụ liên quan | NN';

COMMENT ON COLUMN "outbox_events"."event_type" IS 'Các event PR/proposal/PO/receipt/monitoring như v0.4; thêm user.provisioned / .disabled / .department_changed và user_role.granted / .revoked để audit. | NN';

COMMENT ON COLUMN "outbox_events"."payload" IS 'Snapshot scope/revision/asof và actor_user_id nội bộ, sự kiện cấp/thu hồi quyền có grant/role/scope. Không lưu password, access/refresh token, session hoặc Authorization header. | NN';

COMMENT ON COLUMN "outbox_events"."idempotency_key" IS 'Chặn consumer xử lý trùng | UNIQUE, NN';

COMMENT ON COLUMN "outbox_events"."status" IS 'pending / published / failed | NN, CHECK';

COMMENT ON COLUMN "outbox_events"."attempt_count" IS 'Lượt publish | NN, >=0';

COMMENT ON COLUMN "outbox_events"."available_at" IS 'Thời điểm có thể gửi | NN';

COMMENT ON COLUMN "outbox_events"."published_at" IS 'Mốc gửi thành công | NULL';

COMMENT ON COLUMN "outbox_events"."last_error" IS 'Lỗi publish | NULL';

COMMENT ON COLUMN "outbox_events"."created_at" IS 'Mốc commit event với bản ghi business | NN';

COMMENT ON TABLE "departments" IS 'C22 | departments [P0] - Phòng ban có nhu cầu mua hàng. ';

COMMENT ON COLUMN "departments"."department_id" IS 'ID phòng ban của PR | PK, NN';

COMMENT ON COLUMN "departments"."department_code" IS 'Mã phòng ban ổn định để tổ chức hồ sơ và xác định phạm vi role. | UNIQUE, NN';

COMMENT ON COLUMN "departments"."department_name" IS 'Tên phòng ban | NN';

COMMENT ON COLUMN "departments"."status" IS 'active / inactive; không xóa phòng ban đã có PR | NN, CHECK';

COMMENT ON COLUMN "departments"."created_at" IS 'Thời điểm tạo | NN';

COMMENT ON COLUMN "departments"."updated_at" IS 'Thời điểm sửa | NN';

COMMENT ON TABLE "notifications" IS 'C23 | notifications [P2] - Chỉ dùng nếu OpenCore không cung cấp read/unread persistence. P2, chỉ dùng khi OpenCore không cung cấp read/unread persistence. recipient_user_id là UUID local user; entity_type+entity_id là tham chiếu đa loại.';

COMMENT ON COLUMN "notifications"."notification_id" IS 'ID | PK, NN';

COMMENT ON COLUMN "notifications"."recipient_user_id" IS '-> users.user_id người nhận. Service creator của proposal không thay người nhận/owner PR. | FK, NN';

COMMENT ON COLUMN "notifications"."type" IS 'risk_alert / approval / result | NN';

COMMENT ON COLUMN "notifications"."entity_type" IS 'PR / PO / proposal | NULL';

COMMENT ON COLUMN "notifications"."entity_id" IS 'ID liên quan | NULL';

COMMENT ON COLUMN "notifications"."title" IS 'Tiêu đề | NN';

COMMENT ON COLUMN "notifications"."message" IS 'Nội dung | NULL';

COMMENT ON COLUMN "notifications"."read_at" IS 'Mốc đã đọc | NULL';

COMMENT ON COLUMN "notifications"."created_at" IS 'Ngày tạo | NN';

COMMENT ON TABLE "inventory_snapshots" IS 'C24 | inventory_snapshots [P2] - Chỉ cần nếu muốn Dataiku inventory fields. P2. Tồn kho chỉ là nguồn tham khảo theo snapshot_at, không tự thêm vào 19 X đã chốt.';

COMMENT ON COLUMN "inventory_snapshots"."inventory_snapshot_id" IS 'ID snapshot | PK, NN';

COMMENT ON COLUMN "inventory_snapshots"."product_id" IS '-> products | FK, NN';

COMMENT ON COLUMN "inventory_snapshots"."location_code" IS 'Kho | NULL';

COMMENT ON COLUMN "inventory_snapshots"."on_hand_qty" IS 'Tồn hiện hành tại snapshot | NN, >=0';

COMMENT ON COLUMN "inventory_snapshots"."safety_stock_qty" IS 'Mức tồn an toàn | NN, >=0';

COMMENT ON COLUMN "inventory_snapshots"."snapshot_at" IS 'Snapshot không được lấy từ tương lai | NN';

COMMENT ON TABLE "users" IS 'C25 | users [P0] - Hồ sơ người dùng và tài khoản dịch vụ không có mật khẩu. IdP bên ngoài xác thực. Map bằng external_issuer+external_subject sang UUID local; email không phải identity key và không UNIQUE. Không lưu mật khẩu, access/refresh/session tokens. Soft disable để giữ actor lịch sử; user.department_id là phòng hiện tại, khác PR snapshot.';

COMMENT ON COLUMN "users"."user_id" IS 'ID nội bộ ứng dụng; không giả định bằng subject của IdP. | PK, NN';

COMMENT ON COLUMN "users"."external_issuer" IS 'Issuer đã được server xác thực và cho phép; không lấy từ payload tùy ý. | NN, nonblank';

COMMENT ON COLUMN "users"."external_subject" IS 'Subject ổn định của IdP. UNIQUE(external_issuer,external_subject). Không dùng email làm khóa đăng nhập. | NN, nonblank';

COMMENT ON COLUMN "users"."display_name" IS 'Tên hiển thị; không dùng để phân quyền. | NN';

COMMENT ON COLUMN "users"."email" IS 'Thông tin liên hệ/hiển thị; không mặc định UNIQUE và không là khóa định danh. | NULL';

COMMENT ON COLUMN "users"."department_id" IS 'Phòng ban hiện tại -> departments; có thể NULL với service/admin. Nhân viên lập PR phải có phòng ban. | FK, NULL';

COMMENT ON COLUMN "users"."account_type" IS 'human / service. PR creator/approver là human; AI/PO worker là service. Loại tài khoản ổn định. | NN, CHECK';

COMMENT ON COLUMN "users"."status" IS 'active / disabled. Disabled chặn thao tác mới, không xóa actor lịch sử. | NN, CHECK';

COMMENT ON COLUMN "users"."created_at" IS 'Thời điểm provision hồ sơ. | NN';

COMMENT ON COLUMN "users"."updated_at" IS 'Thời điểm thay đổi hồ sơ/trạng thái. | NN';

COMMENT ON TABLE "roles" IS 'C26 | roles [P0] - Danh mục vai trò và phạm vi quyền ứng dụng. Sáu role ban đầu: EMPLOYEE(human/global/own PR), APPROVER(human/department/workflow), WAREHOUSE(human/department), ADMIN(human/global/profiles+grants+config), AI_SERVICE(service/global/analysis+proposal), PO_SERVICE(service/global/issue sau approval). FK không thay middleware phân quyền. Không đổi role_code/scope_type khi role đã được cấp.';

COMMENT ON COLUMN "roles"."role_id" IS 'ID role. | PK, NN';

COMMENT ON COLUMN "roles"."role_code" IS 'Mã ổn định: EMPLOYEE / APPROVER / WAREHOUSE / ADMIN / AI_SERVICE / PO_SERVICE. Quyền hành động theo policy ở API. | UNIQUE, NN';

COMMENT ON COLUMN "roles"."role_name" IS 'Tên vai trò hiển thị. | NN';

COMMENT ON COLUMN "roles"."description" IS 'Mô tả trách nhiệm. | NULL';

COMMENT ON COLUMN "roles"."allowed_account_type" IS 'human / service. API kiểm tra phù hợp users.account_type khi cấp và dùng role. | NN, CHECK';

COMMENT ON COLUMN "roles"."scope_type" IS 'global / department. Không đổi scope/role_code của role đã được cấp; tạo policy/role mới khi đổi ý nghĩa. | NN, CHECK';

COMMENT ON COLUMN "roles"."status" IS 'active / disabled. Role disabled không cấp quyền thao tác mới. | NN, CHECK';

COMMENT ON COLUMN "roles"."created_at" IS 'Ngày tạo danh mục. | NN';

COMMENT ON COLUMN "roles"."updated_at" IS 'Ngày cập nhật. | NN';

COMMENT ON TABLE "user_roles" IS 'C27 | user_roles [P0] - Cấp và thu hồi role theo phạm vi có lịch sử. Grant/revoke có lịch sử: thu hồi ghi revoked_at+revoked_by, cấp lại tạo UUID mới. Active user+active role+grant chưa revoke, account type và scope phải phù hợp. Department grant độc lập phòng hiện tại của user. Hai partial UNIQUE chặn grant hiệu lực trùng, không thay bằng UNIQUE nullable department_id. Bootstrap ADMIN có audit riêng.';

COMMENT ON COLUMN "user_roles"."user_role_id" IS 'ID một lần cấp quyền; cấp lại sau thu hồi tạo dòng mới. | PK, NN';

COMMENT ON COLUMN "user_roles"."user_id" IS '-> users người được cấp. | FK, NN';

COMMENT ON COLUMN "user_roles"."role_id" IS '-> roles; scope_type phải khớp role. | FK ghép, NN';

COMMENT ON COLUMN "user_roles"."scope_type" IS 'global / department. Scope global vẫn tuân thủ quyền sở hữu PR của role EMPLOYEE, không cho đọc toàn bộ PR. | NN, CHECK';

COMMENT ON COLUMN "user_roles"."department_id" IS 'NN nếu department, NULL nếu global. Đây là phòng ban được cấp quyền, độc lập phòng ban hiện tại của user. | FK, NULL theo scope';

COMMENT ON COLUMN "user_roles"."granted_by" IS '-> users actor quản trị cấp quyền. Bootstrap quản trị được thực hiện riêng, có audit; không tự cấp từ login payload. | FK, NN';

COMMENT ON COLUMN "user_roles"."granted_at" IS 'Timestamp server cấp quyền. | NN';

COMMENT ON COLUMN "user_roles"."revoked_by" IS '-> users quản trị thu hồi, có khi revoked_at có. | FK, NULL';

COMMENT ON COLUMN "user_roles"."revoked_at" IS 'NULL là grant chưa thu hồi. Thu hồi giữ nguyên dòng; user/role phải active mới được dùng. | NULL';

ALTER TABLE "risk_predictions" ADD CONSTRAINT "fk_prediction_item_scope" FOREIGN KEY ("request_item_id", "request_id") REFERENCES "purchase_request_items" ("request_item_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "risk_predictions" ADD CONSTRAINT "fk_prediction_task_revision" FOREIGN KEY ("task_id", "request_id", "input_revision") REFERENCES "analysis_tasks" ("task_id", "request_id", "input_revision") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "risk_analysis_reports" ADD CONSTRAINT "fk_report_task_scope" FOREIGN KEY ("task_id", "request_id") REFERENCES "analysis_tasks" ("task_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "risk_analysis_reports" ADD CONSTRAINT "fk_report_primary_scope" FOREIGN KEY ("primary_prediction_id", "task_id", "request_id") REFERENCES "risk_predictions" ("prediction_id", "task_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "analysis_supplier_candidates" ADD CONSTRAINT "fk_candidate_report_scope" FOREIGN KEY ("report_id", "task_id", "request_id") REFERENCES "risk_analysis_reports" ("report_id", "task_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "analysis_supplier_candidates" ADD CONSTRAINT "fk_candidate_item_scope" FOREIGN KEY ("request_item_id", "request_id") REFERENCES "purchase_request_items" ("request_item_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "analysis_supplier_candidates" ADD CONSTRAINT "fk_candidate_prediction_scope" FOREIGN KEY ("prediction_id", "task_id", "request_item_id", "request_id", "supplier_id") REFERENCES "risk_predictions" ("prediction_id", "task_id", "request_item_id", "request_id", "supplier_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "proposals" ADD CONSTRAINT "fk_proposal_predecessor_scope" FOREIGN KEY ("supersedes_proposal_id", "request_id") REFERENCES "proposals" ("proposal_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "proposal_items" ADD CONSTRAINT "fk_proposal_item_header_scope" FOREIGN KEY ("proposal_id", "request_id") REFERENCES "proposals" ("proposal_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "proposal_items" ADD CONSTRAINT "fk_proposal_item_pr_scope" FOREIGN KEY ("request_item_id", "request_id") REFERENCES "purchase_request_items" ("request_item_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "proposal_items" ADD CONSTRAINT "fk_proposal_item_prediction_scope" FOREIGN KEY ("prediction_id", "request_item_id", "request_id") REFERENCES "risk_predictions" ("prediction_id", "request_item_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_orders" ADD CONSTRAINT "fk_po_proposal_scope" FOREIGN KEY ("proposal_id", "request_id") REFERENCES "proposals" ("proposal_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_order_items" ADD CONSTRAINT "fk_po_item_header_scope" FOREIGN KEY ("purchase_order_id", "proposal_id", "request_id") REFERENCES "purchase_orders" ("purchase_order_id", "proposal_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_requests" ADD CONSTRAINT "fk_pr_department" FOREIGN KEY ("department_id") REFERENCES "departments" ("department_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "analysis_tasks" ADD CONSTRAINT "fk_task_po_request" FOREIGN KEY ("po_item_id", "request_id") REFERENCES "purchase_order_items" ("po_item_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "risk_predictions" ADD CONSTRAINT "fk_prediction_po_item_scope" FOREIGN KEY ("po_item_id", "request_id", "request_item_id") REFERENCES "purchase_order_items" ("po_item_id", "request_id", "request_item_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "risk_predictions" ADD CONSTRAINT "fk_prediction_monitor_task" FOREIGN KEY ("task_id", "request_id", "po_item_id") REFERENCES "analysis_tasks" ("task_id", "request_id", "po_item_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "proposals" ADD CONSTRAINT "fk_proposal_generation_revision" FOREIGN KEY ("generation_task_id", "request_id", "input_revision") REFERENCES "analysis_tasks" ("task_id", "request_id", "input_revision") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "proposals" ADD CONSTRAINT "fk_proposal_generation_report" FOREIGN KEY ("analysis_report_id", "generation_task_id", "request_id") REFERENCES "risk_analysis_reports" ("report_id", "task_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_order_items" ADD CONSTRAINT "fk_po_item_proposal_scope" FOREIGN KEY ("proposal_item_id", "proposal_id", "request_id", "request_item_id") REFERENCES "proposal_items" ("proposal_item_id", "proposal_id", "request_id", "request_item_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_order_items" ADD CONSTRAINT "fk_po_item_pr_scope" FOREIGN KEY ("request_item_id", "request_id") REFERENCES "purchase_request_items" ("request_item_id", "request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "users" ADD CONSTRAINT "fk_user_department" FOREIGN KEY ("department_id") REFERENCES "departments" ("department_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "user_roles" ADD CONSTRAINT "fk_user_role_user" FOREIGN KEY ("user_id") REFERENCES "users" ("user_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "user_roles" ADD CONSTRAINT "fk_user_role_scope" FOREIGN KEY ("role_id", "scope_type") REFERENCES "roles" ("role_id", "scope_type") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "user_roles" ADD CONSTRAINT "fk_user_role_department" FOREIGN KEY ("department_id") REFERENCES "departments" ("department_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "user_roles" ADD CONSTRAINT "fk_user_role_granter" FOREIGN KEY ("granted_by") REFERENCES "users" ("user_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "user_roles" ADD CONSTRAINT "fk_user_role_revoker" FOREIGN KEY ("revoked_by") REFERENCES "users" ("user_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_requests" ADD CONSTRAINT "fk_pr_requester_user" FOREIGN KEY ("requested_by") REFERENCES "users" ("user_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "proposals" ADD CONSTRAINT "fk_proposal_creator_user" FOREIGN KEY ("created_by") REFERENCES "users" ("user_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "approval_history" ADD CONSTRAINT "fk_approval_user" FOREIGN KEY ("approver_id") REFERENCES "users" ("user_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_orders" ADD CONSTRAINT "fk_po_creator_user" FOREIGN KEY ("created_by") REFERENCES "users" ("user_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "goods_receipts" ADD CONSTRAINT "fk_receipt_user" FOREIGN KEY ("recorded_by") REFERENCES "users" ("user_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "notifications" ADD CONSTRAINT "fk_notification_user" FOREIGN KEY ("recipient_user_id") REFERENCES "users" ("user_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "supplier_products" ADD CONSTRAINT "fk_supplier_products_supplier_id" FOREIGN KEY ("supplier_id") REFERENCES "suppliers" ("supplier_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "supplier_products" ADD CONSTRAINT "fk_supplier_products_product_id" FOREIGN KEY ("product_id") REFERENCES "products" ("product_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_request_items" ADD CONSTRAINT "fk_purchase_request_items_request_id" FOREIGN KEY ("request_id") REFERENCES "purchase_requests" ("request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_request_items" ADD CONSTRAINT "fk_purchase_request_items_product_id" FOREIGN KEY ("product_id") REFERENCES "products" ("product_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_request_items" ADD CONSTRAINT "fk_purchase_request_items_preferred_supplier_id" FOREIGN KEY ("preferred_supplier_id") REFERENCES "suppliers" ("supplier_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "analysis_tasks" ADD CONSTRAINT "fk_analysis_tasks_request_id" FOREIGN KEY ("request_id") REFERENCES "purchase_requests" ("request_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "risk_predictions" ADD CONSTRAINT "fk_risk_predictions_supplier_id" FOREIGN KEY ("supplier_id") REFERENCES "suppliers" ("supplier_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "risk_predictions" ADD CONSTRAINT "fk_risk_predictions_product_id" FOREIGN KEY ("product_id") REFERENCES "products" ("product_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "analysis_supplier_candidates" ADD CONSTRAINT "fk_analysis_supplier_candidates_supplier_id" FOREIGN KEY ("supplier_id") REFERENCES "suppliers" ("supplier_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "proposal_items" ADD CONSTRAINT "fk_proposal_items_selected_supplier_id" FOREIGN KEY ("selected_supplier_id") REFERENCES "suppliers" ("supplier_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "approval_history" ADD CONSTRAINT "fk_approval_history_proposal_id" FOREIGN KEY ("proposal_id") REFERENCES "proposals" ("proposal_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_orders" ADD CONSTRAINT "fk_purchase_orders_supplier_id" FOREIGN KEY ("supplier_id") REFERENCES "suppliers" ("supplier_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_order_items" ADD CONSTRAINT "fk_purchase_order_items_product_id" FOREIGN KEY ("product_id") REFERENCES "products" ("product_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "purchase_order_schedules" ADD CONSTRAINT "fk_purchase_order_schedules_po_item_id" FOREIGN KEY ("po_item_id") REFERENCES "purchase_order_items" ("po_item_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "goods_receipts" ADD CONSTRAINT "fk_goods_receipts_schedule_id" FOREIGN KEY ("schedule_id") REFERENCES "purchase_order_schedules" ("schedule_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "confirmation_tokens" ADD CONSTRAINT "fk_confirmation_tokens_purchase_order_id" FOREIGN KEY ("purchase_order_id") REFERENCES "purchase_orders" ("purchase_order_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "supplier_confirmations" ADD CONSTRAINT "fk_supplier_confirmations_purchase_order_id" FOREIGN KEY ("purchase_order_id") REFERENCES "purchase_orders" ("purchase_order_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "supplier_confirmations" ADD CONSTRAINT "fk_supplier_confirmations_token_id" FOREIGN KEY ("token_id") REFERENCES "confirmation_tokens" ("token_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "supplier_confirmation_items" ADD CONSTRAINT "fk_supplier_confirmation_items_confirmation_id" FOREIGN KEY ("confirmation_id") REFERENCES "supplier_confirmations" ("confirmation_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "supplier_confirmation_items" ADD CONSTRAINT "fk_supplier_confirmation_items_schedule_id" FOREIGN KEY ("schedule_id") REFERENCES "purchase_order_schedules" ("schedule_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "email_logs" ADD CONSTRAINT "fk_email_logs_purchase_order_id" FOREIGN KEY ("purchase_order_id") REFERENCES "purchase_orders" ("purchase_order_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "email_logs" ADD CONSTRAINT "fk_email_logs_supplier_id" FOREIGN KEY ("supplier_id") REFERENCES "suppliers" ("supplier_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "inventory_snapshots" ADD CONSTRAINT "fk_inventory_snapshots_product_id" FOREIGN KEY ("product_id") REFERENCES "products" ("product_id") ON DELETE RESTRICT ON UPDATE NO ACTION DEFERRABLE INITIALLY IMMEDIATE;


-- Conditional indexes required by the agreed proposal and RBAC design.
CREATE UNIQUE INDEX uq_current_proposal_per_pr ON proposals(request_id) WHERE is_current;
CREATE UNIQUE INDEX uq_active_global_grant ON user_roles(user_id,role_id) WHERE scope_type='global' AND revoked_at IS NULL;
CREATE UNIQUE INDEX uq_active_department_grant ON user_roles(user_id,role_id,department_id) WHERE scope_type='department' AND revoked_at IS NULL;
CREATE INDEX ix_active_user_roles ON user_roles(user_id,role_id,department_id) WHERE revoked_at IS NULL;

-- Exactly 19 named X keys and compatible JSON types; JSON null is allowed.
-- Core availability, provenance and cross-table workflow policy belong to the application.
ALTER TABLE risk_predictions ADD CONSTRAINT ck_fixed_x_shape CHECK (
  feature_snapshot ? 'x'
  AND jsonb_typeof(feature_snapshot->'x') = 'object'
  AND (feature_snapshot->'x') ?& ARRAY['prediction_stage', 'supplier_id', 'product_category', 'order_size_ratio', 'days_to_deadline', 'non_working_days_to_deadline', 'hist_lead_p50_days', 'hist_lead_p90_days', 'hist_late_rate_smoothed', 'hist_sample_count', 'other_open_order_count', 'expected_issue_wait_days', 'remaining_size_ratio', 'elapsed_days_since_issue', 'confirmation_state', 'confirmation_wait_days', 'received_fraction', 'days_since_last_receipt', 'promise_margin_days']::text[]
  AND ((feature_snapshot->'x') - ARRAY['prediction_stage', 'supplier_id', 'product_category', 'order_size_ratio', 'days_to_deadline', 'non_working_days_to_deadline', 'hist_lead_p50_days', 'hist_lead_p90_days', 'hist_late_rate_smoothed', 'hist_sample_count', 'other_open_order_count', 'expected_issue_wait_days', 'remaining_size_ratio', 'elapsed_days_since_issue', 'confirmation_state', 'confirmation_wait_days', 'received_fraction', 'days_since_last_receipt', 'promise_margin_days']::text[]) = '{}'::jsonb
  AND jsonb_typeof(feature_snapshot->'x'->'prediction_stage') IN ('null', 'string')
  AND jsonb_typeof(feature_snapshot->'x'->'supplier_id') IN ('null', 'string')
  AND jsonb_typeof(feature_snapshot->'x'->'product_category') IN ('null', 'string')
  AND jsonb_typeof(feature_snapshot->'x'->'order_size_ratio') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'days_to_deadline') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'non_working_days_to_deadline') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'hist_lead_p50_days') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'hist_lead_p90_days') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'hist_late_rate_smoothed') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'hist_sample_count') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'other_open_order_count') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'expected_issue_wait_days') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'remaining_size_ratio') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'elapsed_days_since_issue') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'confirmation_state') IN ('null', 'string')
  AND jsonb_typeof(feature_snapshot->'x'->'confirmation_wait_days') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'received_fraction') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'days_since_last_receipt') IN ('null', 'number')
  AND jsonb_typeof(feature_snapshot->'x'->'promise_margin_days') IN ('null', 'number')
  AND ((feature_snapshot->'x'->>'prediction_stage') IN ('PR_CREATED', 'PO_MONITORING')) IS TRUE
);


-- Backend-only baseline: no client grants and no RLS policies.
ALTER TABLE public."suppliers" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."suppliers" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."products" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."products" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."supplier_products" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."supplier_products" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."purchase_requests" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."purchase_requests" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."purchase_request_items" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."purchase_request_items" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."analysis_tasks" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."analysis_tasks" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."risk_predictions" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."risk_predictions" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."risk_analysis_reports" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."risk_analysis_reports" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."analysis_supplier_candidates" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."analysis_supplier_candidates" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."proposals" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."proposals" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."proposal_items" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."proposal_items" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."approval_history" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."approval_history" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."purchase_orders" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."purchase_orders" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."purchase_order_items" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."purchase_order_items" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."purchase_order_schedules" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."purchase_order_schedules" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."goods_receipts" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."goods_receipts" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."confirmation_tokens" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."confirmation_tokens" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."supplier_confirmations" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."supplier_confirmations" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."supplier_confirmation_items" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."supplier_confirmation_items" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."email_logs" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."email_logs" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."outbox_events" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."outbox_events" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."departments" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."departments" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."notifications" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."notifications" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."inventory_snapshots" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."inventory_snapshots" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."users" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."users" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."roles" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."roles" FROM PUBLIC, anon, authenticated;
ALTER TABLE public."user_roles" ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public."user_roles" FROM PUBLIC, anon, authenticated;

