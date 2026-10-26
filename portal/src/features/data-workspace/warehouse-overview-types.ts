// SPDX-License-Identifier: GPL-3.0-or-later
import type { Planning } from './planning-types';
import type { PlanOrder } from './planning-types';

export type OrderAssessment = Omit<PlanOrder, 'lines'> & {
  lines: { sku: string; uom: string; ordered_quantity: number | string }[];
  classification: 'blocked' | 'unknown' | 'ready'; supply_notes: string[];
};
export interface OrderAnalysisData {
  as_of: string; start: string; end: string; horizon: number; outside_horizon_count: number;
  orders: OrderAssessment[]; missing_sources: string[]; assessment_available: boolean;
}

export type WarehouseAnalysisData = Pick<Planning, 'warehouse_id' | 'as_of' | 'selected_sku' | 'history' | 'products' | 'baseline' | 'explanations' | 'horizon'> & { history_days: number };
export interface OrderSupplyCheck {
  status: 'risk' | 'clear' | 'unknown' | 'outside_horizon';
  shortages: { sku: string; uom: string; missing_quantity: number }[];
  stock_ready_by: string | null;
  preparation_minutes: number | null;
  notes: string[];
}

export interface Warehouse {
  organization_id: string;
  warehouse_id: string;
  warehouse_name: string;
  warehouse_code: string;
  products: {
    sku: string; name: string | null; uom: string;
    on_hand: number | string; reserved: number | string; available: number | string;
    shortage: number | null;
  }[];
  open_orders: {
    order_id: string; order_number: string; deadline: string; status: string;
    lines: { sku: string; uom: string; ordered_quantity: number | string }[];
    supply_check?: OrderSupplyCheck | null;
  }[];
  planning_status: 'calculated' | 'needs_data' | 'pending' | 'unavailable';
  planning_issue: { code: string; message: string } | null;
  plan_read_at: string | null;
  analysis?: WarehouseAnalysisData | null;
  order_analysis?: OrderAnalysisData | null;
}

export interface WarehouseOverview {
  items: Warehouse[]; read_at: string; warehouse_count: number; horizon: number;
}

export type StockState = 'risk' | 'clear' | 'unknown' | 'pending';

export function stockState(warehouse: Warehouse, product: Warehouse['products'][number]): StockState {
  if (warehouse.planning_status === 'pending') return 'pending';
  if (warehouse.planning_status !== 'calculated' || product.shortage === null || !Number.isFinite(product.shortage)) return 'unknown';
  return product.shortage > 1e-9 ? 'risk' : 'clear';
}

export function warehouseCounts(warehouse: Warehouse) {
  const counts = { risk: 0, clear: 0, unknown: 0, pending: 0 };
  for (const product of warehouse.products) counts[stockState(warehouse, product)]++;
  return counts;
}
