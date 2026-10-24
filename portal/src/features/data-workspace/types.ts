// SPDX-License-Identifier: GPL-3.0-or-later
export interface Product {
  id: string; name: string; contract_version_id: string; business_key: string[];
  fields: { name: string; wire_type: string; column: string }[];
}
export interface Config {
  name: string; data_product_id: string; contract_version_id: string;
  time_column: string; entity_column: string; target_column: string;
  unit: string; unit_column: string | null; metric_type: "FLOW" | "STOCK";
  aggregation: string; frequency: string; timezone: string;
  missing_policy: string; duplicate_policy: string; start: string; end: string;
  entities: string[]; forecast_horizon: number; forecast_start: string | null;
  forecast_model: string;
  filters: Record<string, string | number | boolean>;
}
export type Evidence = Record<string, unknown>;
export interface Analytics {
  series: { item_id: string; timestamp: string; target: number; moving_average: number }[];
  metrics: { item_id: string; total: number | null; latest: number; recent_7_periods: number;
    period_growth_percent: number | null; trend_per_period: number; average_per_period?: number; standard_deviation?: number; coefficient_of_variation_percent?: number | null; peak_weekday?: number | null; day_of_week_average?: Record<string,number> }[];
  quality: Record<string, number>; evidence: Evidence;
}
export interface Overview {
  config: Config; analytics: Analytics; entities: string[]; entity: string;
  source_start: string; source_end: string; source_age_days: number;
  comparison?: Analytics; comparison_entities?: string[];
  entity_labels?: Record<string, string>;
  stocks?: Stock[]; stock_evidence?: Evidence | null;
  portfolio?: { rows: {item_id:string;available:number|null;planned_inbound:number|null;reference_demand:number;cover_days:number|null;unit:string}[];planning_days:number;start:string;end:string;evidence:Evidence } | null;
  capacity_summary?: {rows:{carrier:string;booked_slots:number;remaining_slots:number;published_days:number;overbooked_days:number}[];planning_days:number;start:string;end:string;evidence:Evidence}|null;
  related_issues?: Record<string,number>;
}
export interface Forecast {
  id: string; config: Config; evidence: Evidence; created_at: string;
  points: { item_id: string; timestamp: string; p10: number; p50: number; p90: number }[];
  metrics: { item_id: string; forecast_total: number }[];
  model: { method: string; kind: string; fallback_reason?: string; entities?: Record<string,{method:string;kind:string;reason:string;observations:number;gap_days:number;validation:Record<string,{mae:number;wape_percent:number|null;windows:object[]}>}> };
}
export interface ScenarioSide {
  series: { day: string; net_position: number; physical_inventory: number; unmet_cumulative: number; inbound?: number; known_demand_due?: number }[];
  metrics: { first_risk_date: string | null; peak_shortage: number; forecast_demand: number;
    fulfilled_quantity: number; fulfillment_percent: number | null; known_orders_at_risk: number | null };
}
export interface Scenario {
  id?: string; baseline: ScenarioSide; proposed: ScenarioSide;
  comparison: { metrics_delta: Record<string, number> }; evidence: Evidence;
  sensitivity?: {
    quantities: number[]; dates: string[]; unit: string;
    cells: { quantity: number; arrival_date: string; peak_shortage: number; known_orders_at_risk: number | null; first_risk_date: string | null }[];
  } | null;
}
export interface Stock { warehouse_id: string; sku: string; available_quantity: number; uom: string }
export const localToday = () => new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Ho_Chi_Minh", year: "numeric", month: "2-digit", day: "2-digit",
}).format(new Date());
export function initialConfig(product: Product): Config {
  const names = new Set(product.fields.map(f => f.name));
  const logistics = names.has("accepted_shipments");
  return {
    name: product.name, data_product_id: product.id, contract_version_id: product.contract_version_id,
    time_column: names.has("day") ? "day" : names.has("updated_at") ? "updated_at" : product.fields.find(f => ["date", "date-time"].includes(f.wire_type))?.name || product.fields[0].name,
    entity_column: names.has("sku") ? "sku" : names.has("carrier_code") ? "carrier_code" : product.business_key[0],
    target_column: names.has("confirmed_quantity") ? "confirmed_quantity" : logistics ? "accepted_shipments" : names.has("available_quantity") ? "available_quantity" : product.fields.find(f => ["number", "integer"].includes(f.wire_type))?.name || "",
    unit: logistics ? "shipments" : "EA", unit_column: names.has("uom") ? "uom" : null,
    metric_type: names.has("available_quantity") ? "STOCK" : "FLOW",
    aggregation: names.has("available_quantity") ? "LAST" : "SUM", frequency: "D",
    timezone: "Asia/Ho_Chi_Minh", missing_policy: "error", duplicate_policy: "error",
    start: localToday(), end: localToday(), entities: [], forecast_horizon: 14,
    forecast_start: null, forecast_model: "auto",
    filters: {},
  };
}
