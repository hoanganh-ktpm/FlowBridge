
-- These helpers run with the caller's rights. They do not authenticate a user or bypass RLS.
CREATE FUNCTION public.sa_has_role(p_user uuid, p_role text, p_department uuid DEFAULT NULL,
 p_at timestamptz DEFAULT now()) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $fn$
 SELECT EXISTS (
 SELECT 1 FROM public.users u JOIN public.user_roles g ON g.user_id=u.user_id
 JOIN public.roles r ON r.role_id=g.role_id AND r.scope_type=g.scope_type
 WHERE u.user_id=p_user AND u.status='active' AND r.status='active'
 AND r.allowed_account_type=u.account_type AND r.role_code=p_role
 AND g.granted_at<=p_at AND (g.revoked_at IS NULL OR g.revoked_at>p_at)
 AND (g.scope_type='global' OR g.department_id=p_department));
$fn$;

CREATE FUNCTION public.sa_po_progress(p_po uuid, p_asof timestamptz DEFAULT now())
RETURNS TABLE(po_item_id uuid, ordered_qty numeric, received_qty numeric, remaining_qty numeric,
 original_deadline date, last_receipt_known_at timestamptz)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $fn$
 SELECT i.po_item_id, i.ordered_quantity, coalesce(r.qty,0),
 greatest(i.ordered_quantity-coalesce(r.qty,0),0), s.deadline, r.known_at
 FROM public.purchase_order_items i
 JOIN public.purchase_orders o ON o.purchase_order_id=i.purchase_order_id
 LEFT JOIN LATERAL (SELECT max(sc.requested_delivery_date) deadline
 FROM public.purchase_order_schedules sc WHERE sc.po_item_id=i.po_item_id) s ON true
 LEFT JOIN LATERAL (SELECT sum(g.received_quantity) qty, max(g.received_at) known_at
 FROM public.goods_receipts g JOIN public.purchase_order_schedules sc ON sc.schedule_id=g.schedule_id
 WHERE sc.po_item_id=i.po_item_id AND g.received_at<=p_asof
 AND g.actual_delivery_date<=(p_asof AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) r ON true
 WHERE i.purchase_order_id=p_po AND o.order_date<=p_asof;
$fn$;

-- Only the supplier actually ordered gets an observed label. Open orders are excluded.
-- For this demo every item has exactly one frozen schedule/deadline.
CREATE FUNCTION public.sa_training_rows(p_cutoff timestamptz DEFAULT now())
RETURNS TABLE(prediction_id uuid, group_id uuid, feature_asof timestamptz,
 prediction_stage varchar, x jsonb, label integer, label_available_at timestamptz)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $fn$
 SELECT p.prediction_id, i.po_item_id, p.feature_asof, p.prediction_stage,
 p.feature_snapshot->'x', (r.actual_date>p.requested_delivery_date)::integer, r.known_at
 FROM public.risk_predictions p
 JOIN public.purchase_order_items i ON i.request_item_id=p.request_item_id
 JOIN public.purchase_orders o ON o.purchase_order_id=i.purchase_order_id AND o.supplier_id=p.supplier_id
 JOIN LATERAL (SELECT sum(g.received_quantity) qty, max(g.actual_delivery_date) actual_date,
 max(g.received_at) known_at FROM public.goods_receipts g
 JOIN public.purchase_order_schedules s ON s.schedule_id=g.schedule_id
 WHERE s.po_item_id=i.po_item_id AND g.received_at<=p_cutoff) r ON r.qty>=i.ordered_quantity
 WHERE p.feature_asof<=p_cutoff AND p.feature_asof<r.known_at
 AND (p.po_item_id IS NULL OR p.po_item_id=i.po_item_id);
$fn$;
REVOKE ALL ON FUNCTION public.sa_has_role(uuid,text,uuid,timestamptz) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.sa_po_progress(uuid,timestamptz) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.sa_training_rows(timestamptz) FROM PUBLIC,anon,authenticated;
