-- Read-only verification. All counts apply only to the 27 application tables.
WITH expected(name) AS (VALUES ('suppliers'),
('products'),
('supplier_products'),
('purchase_requests'),
('purchase_request_items'),
('analysis_tasks'),
('risk_predictions'),
('risk_analysis_reports'),
('analysis_supplier_candidates'),
('proposals'),
('proposal_items'),
('approval_history'),
('purchase_orders'),
('purchase_order_items'),
('purchase_order_schedules'),
('goods_receipts'),
('confirmation_tokens'),
('supplier_confirmations'),
('supplier_confirmation_items'),
('email_logs'),
('outbox_events'),
('departments'),
('notifications'),
('inventory_snapshots'),
('users'),
('roles'),
('user_roles')),
app AS (
 SELECT c.oid, c.relname, c.relrowsecurity
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 JOIN expected e ON e.name=c.relname
 WHERE n.nspname='public' AND c.relkind='r'
), cons AS (SELECT c.* FROM pg_constraint c JOIN app a ON a.oid=c.conrelid),
idx AS (SELECT i.* FROM pg_index i JOIN app a ON a.oid=i.indrelid),
client_access AS (
 SELECT a.relname, r.role_name FROM app a CROSS JOIN (VALUES ('anon'), ('authenticated')) r(role_name)
 WHERE has_table_privilege(r.role_name,a.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
)
SELECT current_database() AS database_name,
 (SELECT count(*) FROM app) AS tables,
 (SELECT count(*) FROM pg_attribute x JOIN app a ON a.oid=x.attrelid WHERE x.attnum>0 AND NOT x.attisdropped) AS columns,
 (SELECT count(*) FROM cons WHERE contype='p') AS primary_keys,
 (SELECT count(*) FROM cons WHERE contype='f') AS foreign_keys,
 (SELECT count(*) FROM cons WHERE contype='c') AS checks,
 (SELECT count(*) FROM idx WHERE indisunique AND NOT indisprimary AND indpred IS NULL) AS ordinary_uniques,
 (SELECT count(*) FROM idx WHERE indisunique AND indpred IS NOT NULL) AS partial_uniques,
 (SELECT count(*) FROM app WHERE relrowsecurity) AS rls_tables,
 (SELECT count(*) FROM client_access) AS client_table_grants,
 (SELECT count(*) FROM pg_policies p JOIN expected e ON e.name=p.tablename WHERE p.schemaname='public') AS policies,
 (SELECT count(*) FROM pg_trigger t JOIN app a ON a.oid=t.tgrelid WHERE NOT t.tgisinternal) AS user_triggers,
 (SELECT sum(n) FROM (SELECT count(*) AS n FROM public."suppliers" UNION ALL SELECT count(*) AS n FROM public."products" UNION ALL SELECT count(*) AS n FROM public."supplier_products" UNION ALL SELECT count(*) AS n FROM public."purchase_requests" UNION ALL SELECT count(*) AS n FROM public."purchase_request_items" UNION ALL SELECT count(*) AS n FROM public."analysis_tasks" UNION ALL SELECT count(*) AS n FROM public."risk_predictions" UNION ALL SELECT count(*) AS n FROM public."risk_analysis_reports" UNION ALL SELECT count(*) AS n FROM public."analysis_supplier_candidates" UNION ALL SELECT count(*) AS n FROM public."proposals" UNION ALL SELECT count(*) AS n FROM public."proposal_items" UNION ALL SELECT count(*) AS n FROM public."approval_history" UNION ALL SELECT count(*) AS n FROM public."purchase_orders" UNION ALL SELECT count(*) AS n FROM public."purchase_order_items" UNION ALL SELECT count(*) AS n FROM public."purchase_order_schedules" UNION ALL SELECT count(*) AS n FROM public."goods_receipts" UNION ALL SELECT count(*) AS n FROM public."confirmation_tokens" UNION ALL SELECT count(*) AS n FROM public."supplier_confirmations" UNION ALL SELECT count(*) AS n FROM public."supplier_confirmation_items" UNION ALL SELECT count(*) AS n FROM public."email_logs" UNION ALL SELECT count(*) AS n FROM public."outbox_events" UNION ALL SELECT count(*) AS n FROM public."departments" UNION ALL SELECT count(*) AS n FROM public."notifications" UNION ALL SELECT count(*) AS n FROM public."inventory_snapshots" UNION ALL SELECT count(*) AS n FROM public."users" UNION ALL SELECT count(*) AS n FROM public."roles" UNION ALL SELECT count(*) AS n FROM public."user_roles") rows_in_app) AS application_rows;
