// SPDX-License-Identifier: GPL-3.0-or-later
import { expect, test, type Page } from '@playwright/test';
const names = ['sales.planning_demand_daily','warehouse.stock_availability','warehouse.inbound_schedule','sales.open_orders','warehouse.order_commitments','warehouse.planning_catalog','warehouse.receipts_daily','logistics.handoff_schedule','logistics.handoff_capacity'];
const products = [...names, 'sales.confirmed_demand_daily', 'logistics.accepted_shipments_daily'].map((name, i) => ({ id: `00000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}`, contract_version_id: '00000000-0000-4000-8000-000000000020', name, business_key: ['org_id','sku','day'], fields: ['org_id','sku','day','confirmed_quantity'].map(name => ({ name, column: name, wire_type: name === 'day' ? 'date' : name === 'confirmed_quantity' ? 'number' : 'string' })) }));
const warehouse = '00000000-0000-4000-8000-000000000030';
const config = { name: 'Kế hoạch hàng hóa', data_product_id: products[0].id, contract_version_id: products[0].contract_version_id, time_column: 'day', entity_column: 'sku', target_column: 'confirmed_quantity', unit: 'EA', unit_column: 'uom', metric_type: 'FLOW', aggregation: 'SUM', frequency: 'D', timezone: 'Asia/Ho_Chi_Minh', missing_policy: 'zero', duplicate_policy: 'error', start: '2026-10-01', end: '2026-10-24', entities: ['SKU-A'], forecast_horizon: 7, forecast_start: '2026-10-25', forecast_model: 'seasonal_mean', filters: { warehouse_id: warehouse } };
function side(quantity = 0, arrival = '', daily = 10, delay = 0, opening = 20) {
  let balance = opening, atRisk = 0, supply = opening, capacity = Infinity, impossible = false;
  const series = Array.from({ length: 7 }, (_, i) => {
    const day = `2026-10-${25 + i}`;
    const inbound = (i === 2 + delay ? 15 : 0) + (day === arrival ? quantity : 0);
    balance += inbound; supply += inbound;
    const room = supply - (i >= 3 ? 30 : 0); if (room < 0) impossible = true;
    if (i === 3 && i > 0) capacity = Math.min(capacity, room / (i * 10));
    capacity = Math.min(capacity, room / ((i + 1) * 10));
    if (i === 3) { atRisk = balance < 30 ? 1 : 0; balance -= 30; }
    balance -= daily;
    return { day, net_position: balance, physical_inventory: Math.max(0, balance), unmet_cumulative: Math.max(0, -balance), demand: daily, inbound, known_demand_due: i === 3 ? 30 : 0 };
  });
  const shortage = Math.max(...series.map(p => p.unmet_cumulative));
  return { series, metrics: { first_risk_date: series.find(p => p.unmet_cumulative > 0)?.day || null, peak_shortage: shortage, forecast_demand: daily * 7, fulfilled_quantity: Math.max(0, daily * 7 - shortage), fulfillment_percent: null, known_orders_at_risk: atRisk, demand_headroom_percent: Number.isFinite(capacity) && !impossible ? 100 * (capacity - 1) : null } };
}
const baseline = side(), proposed = side(80, '2026-10-26');
const evidence = { executed_at: '2026-10-25T02:00:00Z', sources: [{ ref: { data_product_id: products[0].id }, columns: ['org_id','sku'] }], assumptions: { sku: 'SKU-A' }, snapshot_id: '00000000-0000-4000-8000-000000000040' };
const forecast = { id: '00000000-0000-4000-8000-000000000050', config, model: { method: 'seasonal_mean', kind: 'statistical' }, evidence, created_at: evidence.executed_at, metrics: [], points: baseline.series.map(p => ({ item_id: 'SKU-A', timestamp: p.day, p10: 8, p50: 10, p90: 12 })) };
const explanation = { title: 'Nguy cơ thiếu hàng', meaning: 'Đối chiếu hàng có thể phân bổ, đơn đang chờ và hàng sẽ về.', applications: [{ name: 'Bán hàng', description: 'Đơn đã xác nhận' }, { name: 'Kho hàng', description: 'Hàng thực tế và kế hoạch nhập đã duyệt' }], items: [{ label: 'Đang có trong kho', value: 25, unit: 'EA' }, { label: 'Đã giữ cho đơn', value: 5, unit: 'EA' }], formula: 'Hàng đầu kỳ + hàng về đúng thời điểm − đơn đang chờ − nhu cầu mới dự kiến', notes: ['Kế hoạch nhập chưa phải hàng đã nhận.'], read_at: evidence.executed_at, last_observation: '2026-10-24', start: '2026-10-25', end: '2026-10-31' };
const plan = {
  as_of: evidence.executed_at, source_end: '2026-10-24', source_age_days: 1, start: '2026-10-25', end: '2026-10-31', horizon: 7, warehouse_id: warehouse, warehouses: [{ id: warehouse, name: 'Kho Hà Nội', code: 'HN' }],
  products: [{ sku: 'SKU-A', name: 'Cà phê rang', uom: 'EA', on_hand: 25, reserved: 5, available: 20, forecast_quantity: 70, known_quantity: 30, incoming_quantity: 15, known_order_shortage: 10, forecast_added_shortage: 55, shortage: 65, first_risk_date: '2026-10-28', orders_at_risk: 1, next_arrival: '2026-10-27', priority: 'urgent', late_plan_ids: ['plan-1'], forecast_id: forecast.id }, { sku: 'SKU-B', name: 'Trà xanh', uom: 'EA', on_hand: 1000, reserved: 0, available: 1000, forecast_quantity: 20, known_quantity: 0, incoming_quantity: 0, known_order_shortage: 0, forecast_added_shortage: 0, shortage: 0, first_risk_date: null, orders_at_risk: 0, next_arrival: null, priority: 'clear', late_plan_ids: [], forecast_id: forecast.id }],
  selected_sku: 'SKU-A', baseline, forecast,
  orders: [{ order_id: 'order-1', order_number: 'SO-001', deadline: '2026-10-28T09:00:00+07:00', at_risk: true, already_overdue: false, reason: 'stock', schedule_status: 'late', fulfilled_at: null, lines: [], shortages_at_deadline: [{ sku: 'SKU-A', uom: 'EA', missing_quantity: 10 }], products: [{ sku: 'SKU-A', name: 'Cà phê rang', uom: 'EA' }], shipments: [{ shipment_number: 'SHP-001', carrier_code: 'C1', carrier_name: 'Giao hàng Miền Bắc', scheduled_handoff_at: '2026-10-29', status: 'ACCEPTED' }] }],
  inbound: [{ plan_id: 'plan-1', sku: 'SKU-A', quantity: 15, uom: 'EA', expected_at: '2026-10-27T09:00:00+07:00', overdue: false, status: 'APPROVED' }],
  capacity_calendar: [{ day: '2026-10-27', carrier_code: 'C1', carrier_name: 'Giao hàng Miền Bắc', maximum: 10, booked: 8, remaining: 2 }],
  history: { start: '2026-10-18', end: '2026-10-24', frequency: 'day', receipts_available: true, series: Array.from({ length: 7 }, (_, i) => ({ day: `2026-10-${18 + i}`, confirmed: 10, received: i === 2 ? 50 : 0, difference: i === 2 ? 40 : -10 })) }, kpis: { risk_products: 1, risk_orders: 1, needs_planning: 1, plans_attention: 1 }, missing_sources: [], evidence, explanations: { stock: explanation, balance: explanation, risk: explanation, history: { ...explanation, title: 'Đặt hàng và nhập kho' } },
};
function scenarioResult(body: any, opening = 20) {
  const daily = (body.demand_quantile === 'p90' ? 12 : 10) * body.demand_multiplier;
  const reference = side(0, '', 10, 0, opening), baseline = side(0, '', daily, body.inbound_delay_days, opening), proposed = side(body.additional_quantity, body.arrival_date, daily, body.inbound_delay_days, opening);
  const measure = (v: ReturnType<typeof side>) => ({ peak_shortage: v.metrics.peak_shortage, shortage_days: v.series.filter(p => p.unmet_cumulative > 0).length, ending_inventory: v.series.at(-1)!.physical_inventory, new_demand: v.metrics.forecast_demand, known_orders_at_risk: v.metrics.known_orders_at_risk, demand_headroom_percent: v.metrics.demand_headroom_percent });
  const peak = baseline.metrics.peak_shortage, first = baseline.metrics.first_risk_date;
  return { ...(body.save ? { id: 'saved-scenario' } : {}), reference, baseline, proposed, evidence,
    decision: { reference: measure(reference), stressed: measure(baseline), proposed: measure(proposed), minimum_additional_quantity: Math.ceil(peak * 1000) / 1000, latest_arrival_date: first, remaining_late_orders_after_suggestion: 0, model_method: 'chronos2', verdict: peak === 0 && baseline.metrics.known_orders_at_risk === 0 ? body.additional_quantity > 0 ? 'extra_without_risk_reduction' : 'no_extra_needed' : proposed.metrics.peak_shortage === 0 && proposed.metrics.known_orders_at_risk === 0 ? 'covered' : 'partial' },
  };
}

type State = { calls: { path: string; body: any }[]; status: number; missing: boolean; optionalMissing: boolean; detail?: { code: string; message: string } };
async function mock(page: Page, admin = false) {
  const state: State = { calls: [], status: 200, missing: false, optionalMissing: false };
  await page.route('**/config.json', r => r.fulfill({ json: { apiUrl: 'http://127.0.0.1:18000', issuer: 'https://identity.example.test/realms/flowbridge', clientId: 'flowbridge-web-a' } }));
  await page.route('**/node_modules/.vite/deps/keycloak-js.js*', r => r.fulfill({ contentType: 'application/javascript', body: `export default class Keycloak { authenticated=true;token='test-caller';async init(){return true}async updateToken(){return true}async logout(){} }` }));
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname, body = route.request().postDataJSON();
    expect(route.request().headers().authorization).toBe('Bearer test-caller'); state.calls.push({ path, body });
    if (path.endsWith('/core/me')) return route.fulfill({ json: { principal_id: 'test-user', platform_role: admin ? 'PLATFORM_ADMIN' : 'NONE', verified_identity: {} } });
    if (path.endsWith('/products')) return route.fulfill({ json: { items: state.missing ? products.slice(1) : products, can_configure: admin } });
    if (path.endsWith('/planning-options')) return route.fulfill({ json: { warehouses: plan.warehouses, products: plan.products.map(p => ({ warehouse_id: warehouse, sku: p.sku, name: p.name, uom: p.uom })) } });
    if (path.endsWith('/planning')) {
      if (state.status !== 200) return route.fulfill({ status: state.status, json: { detail: state.detail || { code: 'NO_DATA', message: 'Chưa đủ dữ liệu tại nguồn đang chọn.' } } });
      const result = structuredClone(plan); result.selected_sku = body.sku || 'SKU-A'; result.history.frequency = body.history_frequency || 'day';
      if (body.forecast_model === 'chronos2') result.forecast = { ...result.forecast, config: { ...result.forecast.config, forecast_model: 'chronos2' }, model: { method: 'chronos2', kind: 'machine_learning', entities: { [result.selected_sku]: { method: 'chronos2', observations: body.training_days, validation: { chronos2: { mae: 2.4 } } } } } as any };
      if (state.optionalMissing) { result.history.receipts_available = false; result.history.series = result.history.series.map(p => ({ ...p, received: null as any, difference: null as any })); result.missing_sources = ['receipts'] as any; }
      return route.fulfill({ json: result });
    }
    if (path.endsWith('/scenarios')) {
      const daily = (body.demand_quantile === 'p90' ? 12 : 10) * body.demand_multiplier;
      const quantities = [0, 50, 100], dates = ['2026-10-25', '2026-10-28', '2026-10-31'];
      const sensitivity = body.include_sensitivity ? { quantities, dates, unit: 'EA', cells: quantities.flatMap(quantity => dates.map(arrival_date => ({ quantity, arrival_date, ...side(quantity, arrival_date, daily, body.inbound_delay_days).metrics }))) } : null;
      return route.fulfill({ json: { ...scenarioResult(body), sensitivity } });
    }
    if (path.endsWith('/forecasts/saved')) return route.fulfill({ json: { items: [forecast] } });
    if (path.endsWith('/forecasts/' + forecast.id)) return route.fulfill({ json: forecast });
    if (path.endsWith('/overview')) {
      const logistics = body.data_product_id === products[10].id;
      const entity = body.entity || (logistics ? 'C1' : 'SKU-A');
      const length = Math.min(56, body.days); const series = Array.from({ length }, (_, i) => ({ item_id: entity, timestamp: new Date(Date.UTC(2026, 9, 25 - length + i)).toISOString().slice(0, 10), target: i % 17 === 0 ? 40 : 10 + i % 7, moving_average: 12 })); const total = series.reduce((n, p) => n + p.target, 0);
      const analytics = { series, metrics: [{ item_id: entity, total, latest: series.at(-1)!.target, recent_7_periods: series.slice(-7).reduce((n, p) => n + p.target, 0), period_growth_percent: body.days === 7 ? null : 12.5, average_per_period: total / series.length, standard_deviation: 2, coefficient_of_variation_percent: 16, peak_weekday: 4, day_of_week_average: { '0': 10, '1': 12, '2': 9, '3': 11, '4': 20, '5': 15, '6': 8 } }], quality: {}, evidence: { ...evidence, data_products: [{ name: logistics ? 'logistics.accepted_shipments_daily' : 'sales.confirmed_demand_daily' }] } };
      return route.fulfill({ json: { config: { ...config, start: series[0].timestamp, end: series.at(-1)!.timestamp, data_product_id: body.data_product_id, entities: [entity], entity_column: logistics ? 'carrier_code' : 'sku', unit: logistics ? 'shipments' : 'EA' }, entity, entities: logistics ? ['C1', 'C2'] : ['SKU-A', 'SKU-B'], entity_labels: logistics ? {} : { 'SKU-A': 'Cà phê rang', 'SKU-B': 'Trà xanh' }, source_start: '2026-08-30', source_end: '2026-10-24', source_age_days: 1, analytics, comparison: analytics, comparison_entities: [entity] } });
    }
    if (path.endsWith('/forecasts')) return route.fulfill({ status: 201, json: { ...forecast, model: { method: 'chronos2', kind: 'machine_learning', entities: { [body.config.entities[0]]: { method: 'chronos2', kind: 'machine_learning', observations: body.training_days, validation: { chronos2: { mae: 2.4, wape_percent: 12, windows: [] } } } } }, config: { ...body.config, forecast_start: body.start_date, forecast_horizon: Math.round((Date.parse(body.end_date) - Date.parse(body.start_date)) / 86400000) + 1 }, points: Array.from({ length: Math.round((Date.parse(body.end_date) - Date.parse(body.start_date)) / 86400000) + 1 }, (_, i) => ({ item_id: body.config.entities[0], timestamp: new Date(Date.parse(body.start_date) + i * 86400000).toISOString().slice(0, 10), p10: 8, p50: 10, p90: 12 })) } });
    if (path.endsWith('/scenarios/saved')) return route.fulfill({ json: { items: [{ id: 'saved-scenario', baseline, proposed, evidence }] } });
    if (path.endsWith('/preview')) return route.fulfill({ json: { quality: {}, preview_before: [{ sku: 'SKU-A' }], preview_after: [], evidence } });
    if (path.endsWith('/configurations')) return route.fulfill({ status: 201, json: { id: 'config-1', config: body } });
    return route.fulfill({ status: 404, json: {} });
  });
  return state;
}
const count = (s: State, path: string) => s.calls.filter(c => c.path.endsWith(path)).length;
async function apply(page: Page) { if (await page.getByRole('button', { name: 'Cập nhật kế hoạch', exact: true }).count() === 0) await page.getByRole('button', { name: 'Tổng quan', exact: true }).click(); await page.getByRole('button', { name: 'Cập nhật kế hoạch', exact: true }).click(); await expect(page.locator('.pw-applied-context')).toBeVisible(); }

test('compact overview shows two governed business charts without the removed noise', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data'); await apply(page);
  await expect(page.getByText('PHỐI HỢP ĐỂ RA QUYẾT ĐỊNH', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Lượng khách đặt trong kỳ', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Bình quân mỗi ngày', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Phân bố lượng đặt theo ngày' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Lượng đặt và thực nhập', exact: true })).toBeVisible();
  const risk = page.getByRole('img', { name: /Nguy cơ thiếu hàng theo sản phẩm. Trục X: sản phẩm/ });
  await expect(risk).toBeVisible(); await expect(risk).toContainText('Thiếu cho đơn đã nhận');
  await expect(risk).toContainText('Thiếu tăng thêm khi xét dự báo');
  await expect(page.getByRole('heading', { name: 'Dự báo nhu cầu và kế hoạch chuẩn bị' })).toHaveCount(0);
  await page.getByLabel('Gộp theo', { exact: true }).selectOption('week');
  expect(count(s, '/planning')).toBe(1); await apply(page);
  expect(s.calls.filter(c => c.path.endsWith('/planning')).at(-1)!.body.history_frequency).toBe('week');
  await expect(page.getByRole('img', { name: /Lượng đặt xác nhận và thực nhập theo tuần/ })).toBeVisible();
  expect(await page.getByRole('heading').allTextContents()).not.toEqual(expect.arrayContaining([expect.stringMatching(/\?$/)]));
  await page.mouse.move(20, 20); await page.screenshot({ animations: 'disabled', path: 'ref/qa/compact-dashboard.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/compact-dashboard-mobile.png', fullPage: true });
});

test('sensitivity grid selects quantity and date without silently calculating or saving', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data'); await apply(page);
  await page.getByRole('button', { name: 'Thử bổ sung hàng', exact: true }).click();
  await page.getByRole('button', { name: 'So sánh phương án', exact: true }).click();
  const matrix = page.getByRole('table', { name: 'Ma trận lượng nhập và ngày hàng về' });
  await expect(matrix).toBeVisible();
  expect(s.calls.find(c => c.path.endsWith('/scenarios'))!.body.include_sensitivity).toBe(true);
  const early = matrix.getByRole('button', { name: 'Nhập 100 sản phẩm, về 25/10/2026: thiếu 0, 0 đơn thiếu hàng trước hạn', exact: true });
  await early.click();
  await expect(page.getByLabel('Số lượng nhập thêm')).toHaveValue('100');
  await expect(page.getByLabel('Ngày dự kiến hàng về', { exact: true })).toHaveValue('2026-10-25');
  expect(count(s, '/scenarios')).toBe(1);
  await expect(page.getByRole('button', { name: 'Lưu phương án', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'So sánh phương án', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Lưu phương án', exact: true })).toBeEnabled();
  await expect(matrix.getByRole('button', { name: /Nhập 100 sản phẩm, về 31\/10\/2026/ })).toContainText('1 đơn có nguy cơ trễ');
  const matrixPanel = page.locator('.dw-panel').filter({ has: page.getByRole('heading', { name: 'Ma trận lượng nhập và ngày hàng về', exact: true }) });
  await matrixPanel.getByRole('button', { name: 'Nguồn & cách tính', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('Số phương án độc lập');
  await expect(page.getByRole('dialog')).toContainText('Không cộng lượng nhập từ các ô');
  await page.keyboard.press('Escape'); await expect(page.locator('[data-slot="sheet-overlay"]')).toHaveCount(0);
  await matrix.scrollIntoViewIfNeeded(); await page.mouse.move(20, 20);
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/replenishment-sensitivity.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('history remains independent of planning sources and keeps details out of the overview', async ({ page }) => {
  const s = await mock(page); s.missing = true; await page.goto('/#data');
  await page.getByRole('tab', { name: 'Phân tích lịch sử', exact: true }).click();
  await page.getByLabel('Xem số liệu đã ghi nhận trong', { exact: true }).selectOption('7');
  expect(count(s, '/overview')).toBe(0); expect(count(s, '/planning')).toBe(0);
  await page.getByRole('button', { name: 'Cập nhật biểu đồ', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Diễn biến lượng đặt hàng' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Lượng đặt hàng theo thứ trong tuần' })).toBeVisible();
  await page.getByRole('button', { name: 'Tùy chọn biểu đồ', exact: true }).click();
  await page.getByRole('menuitemcheckbox', { name: 'Làm mượt theo bình quân 7 ngày', exact: true }).click(); await page.keyboard.press('Escape'); expect(count(s, '/overview')).toBe(1);
  await expect(page.locator('.dw-metrics')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Tạo dự báo', exact: true })).not.toBeVisible();
});

test('dedicated forecast uses only ML and keeps stale settings from triggering a decision', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data');
  await page.getByRole('button', { name: 'Dự báo nhu cầu', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Bước 1. Cập nhật dữ liệu', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Bước 2. Tạo dự báo', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Cập nhật dữ liệu', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Bước 2. Tạo dự báo', exact: true })).toBeVisible();
  await expect(page.getByLabel('Dữ liệu tham chiếu', { exact: true })).toHaveValue('180');
  await expect(page.getByLabel('Mô hình', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Giải thích cách tạo dự báo', exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText('Chronos-2 đã được huấn luyện sẵn');
  await page.keyboard.press('Escape');
  expect(count(s, '/forecasts')).toBe(0);
  await page.getByLabel('Ngày bắt đầu dự báo', { exact: true }).fill('2026-10-27');
  await page.getByLabel('Ngày kết thúc dự báo', { exact: true }).fill('2026-11-03');
  await page.getByRole('button', { name: 'Tạo dự báo', exact: true }).click();
  const body = s.calls.find(c => c.path.endsWith('/forecasts'))!.body;
  expect(body.config.forecast_model).toBe('chronos2'); expect(body.allow_statistical_fallback).toBe(false);
  expect(body.start_date).toBe('2026-10-27'); expect(body.end_date).toBe('2026-11-03');
  await expect(page.getByRole('heading', { name: 'Lượng đặt dự báo', exact: true })).toBeVisible();
  await page.getByLabel('Ngày kết thúc dự báo', { exact: true }).fill('2026-11-04');
  await expect(page.getByRole('button', { name: 'Đối chiếu nguồn hàng', exact: true })).toBeDisabled();
  await page.getByLabel('Ngày kết thúc dự báo', { exact: true }).fill('2026-11-03');
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/compact-forecast.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/compact-forecast-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'Đối chiếu nguồn hàng', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Thử phương án nhập hàng', exact: true })).toBeVisible();
  expect(s.calls.find(c => c.path.endsWith('/planning'))!.body.sku).toBe('SKU-A');
  expect(s.calls.find(c => c.path.endsWith('/planning'))!.body.horizon).toBe(10);
  await page.getByRole('button', { name: 'Dự báo nhu cầu', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Lượng đặt dự báo', exact: true })).toBeVisible();
  expect(count(s, '/overview')).toBe(1);
  await page.getByLabel('Khoảng xem lịch sử', { exact: true }).selectOption('90');
  await expect(page.getByRole('heading', { name: 'Bước 2. Tạo dự báo', exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Lượng đặt dự báo', exact: true })).toHaveCount(0);
  expect(count(s, '/overview')).toBe(1);
  await page.route('**/api/v1/data-workspace/overview', route => route.fulfill({ status: 503, json: { detail: 'Chưa cập nhật được dữ liệu. Vui lòng thử lại.' } }));
  await page.getByRole('button', { name: 'Cập nhật dữ liệu', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Chưa tải được nguồn dữ liệu');
  await expect(page.getByRole('alert')).toContainText('Cập nhật dữ liệu');
  await expect(page.getByRole('heading', { name: 'Bước 2. Tạo dự báo', exact: true })).toHaveCount(0);
});

test('ML history errors are readable and never silently fall back', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data');
  await page.getByRole('button', { name: 'Dự báo nhu cầu', exact: true }).click();
  await page.getByRole('button', { name: 'Cập nhật dữ liệu', exact: true }).click();
  await page.route('**/api/v1/data-workspace/forecasts', r => r.fulfill({ status: 422, json: { detail: { code: 'ML_HISTORY_SHORT', message: 'Chưa đủ lịch sử để học và kiểm chứng Machine Learning.' } } }));
  await page.getByRole('button', { name: 'Tạo dự báo', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Chưa đủ lịch sử để học');
  await expect(page.getByRole('heading', { name: 'Lượng đặt dự báo', exact: true })).toHaveCount(0);
  expect(count(s, '/planning')).toBe(0);
});

test('transport charts keep shipment units and saved forecasts remain viewable', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data');
  await page.getByRole('tab', { name: 'Phân tích lịch sử', exact: true }).click();
  await page.getByLabel('Nội dung', { exact: true }).selectOption(products[10].id);
  await page.getByRole('button', { name: 'Cập nhật biểu đồ', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Diễn biến lượt vận chuyển tiếp nhận' })).toBeVisible();
  await expect(page.getByRole('img', { name: /Biểu đồ C1.*lượt vận chuyển/ })).toBeVisible();
  await page.getByRole('button', { name: 'Đã lưu', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: /SKU-A/ }).click();
  await expect(page.getByRole('dialog').getByRole('heading', { name: 'Dự báo đã lưu' })).toBeVisible();
  expect(count(s, '/forecasts/' + forecast.id)).toBe(1);
});

test('weekly decisions use explicit Apply; search by product name or code keeps applied context', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data');
  await expect(page.getByRole('heading', { name: 'Tổng quan', exact: true })).toBeVisible(); await page.getByRole('button', { name: 'Tổng quan', exact: true }).click();
  await page.getByLabel('Xem đặt hàng và nhập kho trong', { exact: true }).selectOption('7');
  expect(count(s, '/planning')).toBe(0); await apply(page);
  expect(s.calls.find(c => c.path.endsWith('/planning'))!.body.history_days).toBe(7);
  expect(s.calls.find(c => c.path.endsWith('/planning'))!.body.horizon).toBe(7);
  await expect(page.getByRole('img', { name: /Lượng đặt xác nhận và thực nhập/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Phiên đăng nhập', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Đã xác thực', exact: true })).toHaveCount(0);
  const selector = page.getByRole('combobox', { name: 'Sản phẩm', exact: true });
  await expect(selector.locator('option')).toHaveText(['SKU-A - Cà phê rang', 'SKU-B - Trà xanh']);
  await selector.selectOption({ label: 'SKU-B - Trà xanh' });
  expect(count(s, '/planning')).toBe(1); await expect(page.getByText('Có thay đổi chưa áp dụng', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Thử bổ sung hàng', exact: true })).toBeDisabled();
  await apply(page); expect(s.calls.filter(c => c.path.endsWith('/planning')).at(-1)!.body.sku).toBe('SKU-B');
  await selector.selectOption({ label: 'SKU-A - Cà phê rang' }); await apply(page);
  await page.getByRole('button', { name: 'Nguồn & cách tính', exact: true }).first().click();
  await expect(page.getByRole('dialog')).toContainText('Hàng đầu kỳ + hàng về đúng thời điểm');
  await expect(page.getByRole('dialog')).toContainText('Đang có trong kho');
  await expect(page.getByRole('dialog')).not.toContainText('data_product_id');
  await expect(page.getByRole('dialog')).not.toContainText('Chi tiết kỹ thuật');
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/decision-evidence.png', fullPage: true });
  await page.keyboard.press('Escape'); await expect(page.locator('[data-slot="sheet-overlay"]')).toHaveCount(0);
  await page.setViewportSize({ width: 1600, height: 1500 });
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/decision-dashboard.png', fullPage: true });
});

test('a forecast leads to a tested proposal; editing never recalculates or exports stale results', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data'); await apply(page);
  await page.getByRole('button', { name: 'Thử bổ sung hàng', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Thử phương án nhập hàng', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Xem dữ liệu đầu vào', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Lịch hàng về và nhu cầu theo ngày', exact: true })).toBeVisible();
  const supplyChart = page.getByRole('img', { name: /Biểu đồ Vùng dao động, Lượng đặt dự kiến mỗi ngày, Hàng sẽ về theo kế hoạch, Đơn đã nhận đến hạn, chưa phân bổ/ });
  await expect(supplyChart).toBeVisible();
  await expect(supplyChart).toContainText('Hàng sẽ về theo kế hoạch');
  await page.setViewportSize({ width: 1600, height: 1300 });
  await page.locator('.dw-main').evaluate(e => { e.scrollTop = 0; }); await page.mouse.move(20, 20);
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/decision-supply.png', fullPage: true });
  await page.keyboard.press('Escape'); await expect(page.locator('[data-slot=sheet-overlay]')).toHaveCount(0);
  await page.getByLabel('Số lượng nhập thêm').fill('50'); await page.getByLabel('Ngày dự kiến hàng về', { exact: true }).fill('2026-10-26');
  expect(count(s, '/scenarios')).toBe(0);
  await page.getByRole('button', { name: 'So sánh phương án', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Lưu phương án', exact: true })).toBeEnabled(); expect(count(s, '/scenarios')).toBe(1);
  await page.getByLabel('Ngày dự kiến hàng về', { exact: true }).fill('2026-10-30');
  await expect(page.getByRole('button', { name: 'Lưu phương án', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Tải đề xuất phối hợp', exact: true })).toBeDisabled(); expect(count(s, '/scenarios')).toBe(1);
  await page.getByRole('button', { name: 'Điều chỉnh giả định', exact: true }).click(); await page.getByLabel('Giả sử lượng khách đặt tăng/giảm (%)', { exact: true }).fill('20'); await expect(page.locator('.pw-assumption-example')).toContainText('Tăng 20%'); await expect(page.locator('.pw-assumption-example')).toContainText('84 sản phẩm'); await page.getByLabel('Mức nhu cầu để chuẩn bị', { exact: true }).selectOption('p90'); await page.getByRole('button', { name: 'Áp dụng giả định', exact: true }).click();
  await page.getByRole('button', { name: 'So sánh phương án', exact: true }).click();
  const body = s.calls.filter(c => c.path.endsWith('/scenarios')).at(-1)!.body;
  expect(body.snapshot_id).toBe(evidence.snapshot_id); expect(body.demand_quantile).toBe('p90'); expect(body.demand_multiplier).toBe(1.2); expect(body.open_orders).toBeTruthy(); expect(body.commitments).toBeTruthy();
  const download = page.waitForEvent('download'); await page.getByRole('button', { name: 'Tải đề xuất phối hợp', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('de-xuat-SKU-A.txt');
  await page.getByRole('button', { name: 'Lưu phương án', exact: true }).click(); await expect(page.getByRole('status')).toContainText('Đã lưu phương án');
  expect(s.calls.filter(c => c.path.endsWith('/scenarios')).at(-1)!.body.save).toBe(true);
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/decision-proposal.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Điều chỉnh giả định', exact: true }).click(); await page.getByLabel('Giả sử lượng khách đặt tăng/giảm (%)', { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/decision-mobile.png', fullPage: true });
});

test('leadership KPI navigates to order deadlines and named carrier calendar', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data'); await apply(page);
  await page.getByRole('button', { name: /Đơn cần phối hợp/ }).click();
  await expect(page.getByRole('heading', { name: 'Đơn hàng và tiến độ đáp ứng' })).toBeVisible();
  await expect(page.getByText('SO-001', { exact: true })).toBeVisible(); await expect(page.getByText('Chưa đủ hàng trước hạn', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Lịch tiếp nhận của nhà vận chuyển' })).toBeVisible();
  await expect(page.getByText('Giao hàng Miền Bắc', { exact: true }).first()).toBeVisible(); expect(count(s, '/planning')).toBe(1);
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/decision-orders.png', fullPage: true });
});

test('missing optional receipts never become zero or a misleading chart legend', async ({ page }) => {
  const s = await mock(page); s.optionalMissing = true; await page.goto('/#data'); await apply(page);
  const chart = page.getByRole('img', { name: /Lượng đặt xác nhận và thực nhập/ });
  await expect(chart).not.toContainText('Thực nhập kho'); await expect(page.locator('.pw-data-warning')).toContainText('không tính là 0');
});

test('missing required per-warehouse demand explains the setup and does not call facts', async ({ page }) => {
  const s = await mock(page); s.missing = true; await page.goto('/#data'); await page.getByRole('button', { name: 'Tổng quan', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Lượng đặt hàng theo kho');
  await expect(page.getByRole('button', { name: 'Cập nhật kế hoạch', exact: true })).toBeDisabled(); expect(count(s, '/planning')).toBe(0);
});

test('IT mapping remains functional and technical evidence is limited to IT', async ({ page }) => {
  await mock(page, true); await page.goto('/#data'); await apply(page);
  await page.getByRole('button', { name: 'Nguồn & cách tính', exact: true }).first().click();
  await page.getByText('Chi tiết kỹ thuật · chỉ dành cho IT', { exact: true }).click(); await expect(page.getByRole('dialog')).toContainText('data_product_id'); await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Thiết lập cho IT', exact: true }).click();
  await page.getByRole('button', { name: 'Kiểm tra & xem trước', exact: true }).click();
  await expect(page.getByText('preview_before', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Lưu cấu hình', exact: true }).click(); await expect(page.getByRole('status')).toContainText('Đã lưu cấu hình');
});

for (const status of [401, 403, 503, 422]) test(`source failure ${status} clears old facts and never invents decisions`, async ({ page }) => {
  const s = await mock(page); await page.goto('/#data'); await apply(page); s.status = status;
  await page.getByRole('button', { name: 'Cập nhật kế hoạch', exact: true }).click();
  if (status === 401) { await expect(page.getByText('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.', { exact: true })).toBeVisible(); await expect(page.locator('.dw-shell')).toHaveCount(0); }
  else { await expect(page.getByRole('alert')).toContainText(status === 403 ? 'Quyền dữ liệu đã thay đổi' : status === 422 ? 'Chưa đủ dữ liệu' : 'Chưa tải được nguồn'); await expect(page.locator('.dw-chart,.pw-chart')).toHaveCount(0); }
  await expect(page.locator('.pw-summary-grid')).toHaveCount(0);
});

for (const [code, message, status] of [
  ['DATASET_TOO_LARGE', 'Nguồn dữ liệu vượt giới hạn đọc an toàn. Hãy thu hẹp kho hoặc khoảng ngày.', 422],
  ['STALE_HISTORY', 'Lịch sử đặt hàng quá cũ. Cần cập nhật nguồn trước khi lập kế hoạch.', 422],
  ['PLANNING_TIMEOUT', 'Đối chiếu dữ liệu mất quá lâu. Hãy thử lại sau hoặc chọn kho khác.', 504],
] as const) test(`planning reports ${code} accurately and releases the loading state`, async ({ page }) => {
  const s = await mock(page); s.status = status; s.detail = { code, message };
  await page.goto('/#data'); await page.getByRole('button', { name: 'Tổng quan', exact: true }).click();
  await page.getByRole('button', { name: 'Cập nhật kế hoạch', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText(message);
  await expect(page.locator('.dw-progress')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Cập nhật kế hoạch', exact: true })).toBeEnabled();
  await expect(page.getByRole('alert')).not.toContainText('14 ngày');
  s.status = 200; await apply(page); expect(count(s, '/planning')).toBe(2);
});

test('a slow planning read can be stopped and retried without stale results', async ({ page }) => {
  await mock(page);
  let release!: () => void;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/data-workspace/planning', async route => {
    await waiting;
    await route.fulfill({ json: plan }).catch(() => {});
  });
  await page.goto('/#data'); await page.getByRole('button', { name: 'Tổng quan', exact: true }).click();
  await page.getByRole('button', { name: 'Cập nhật kế hoạch', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Đang đối chiếu dữ liệu');
  await page.getByRole('button', { name: 'Dừng chờ', exact: true }).click();
  await expect(page.locator('.dw-progress')).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('Đã dừng chờ');
  await page.unroute('**/api/v1/data-workspace/planning'); release();
  await expect(page.locator('.pw-summary-grid')).toHaveCount(0);
  await apply(page); await expect(page.getByRole('alert')).toHaveCount(0);
});


test('warehouse choices show real names and multiple warehouses require a deliberate choice', async ({ page }) => {
  const s = await mock(page);
  const second = '00000000-0000-4000-8000-000000000031';
  await page.route('**/api/v1/data-workspace/planning-options', route => route.fulfill({ json: { warehouses: [...plan.warehouses, { id: second, name: 'Kho Hồ Chí Minh', code: 'HCM' }], products: [ ...plan.products.map(p => ({ warehouse_id: warehouse, sku: p.sku, name: p.name, uom: p.uom })), { warehouse_id: second, sku: 'M08', name: 'Bút bi', uom: 'EA' } ] } }));
  await page.goto('/#data');
  const picker = page.getByRole('combobox', { name: 'Kho phân tích', exact: true });
  await expect(picker).toBeEnabled(); await expect(picker).toHaveValue('');
  await expect(picker.locator('option')).toHaveText(['Chọn kho', 'Kho Hà Nội (HN)', 'Kho Hồ Chí Minh (HCM)']);
  await expect(page.getByText('Kho đầu tiên được chia sẻ', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Cập nhật kế hoạch', exact: true })).toBeDisabled();
  const empty = page.locator('.pw-empty-state').filter({ has: page.getByRole('heading', { name: 'Chưa có kế hoạch được tải', exact: true }) }); await expect(empty).toBeVisible();
  await expect(empty.locator('svg')).toHaveCount(0); expect((await empty.boundingBox())!.height).toBeLessThan(180);
  await expect(page.locator('.dw-topbar')).toHaveCount(0);
  await picker.selectOption(second);
  await expect(page.getByRole('button', { name: 'Cập nhật kế hoạch', exact: true })).toBeEnabled();
  await expect(page.getByRole('combobox', { name: 'Sản phẩm', exact: true })).toHaveValue('M08');
  await expect(page.getByRole('combobox', { name: 'Sản phẩm', exact: true }).locator('option')).toHaveText(['M08 - Bút bi']);
  expect(count(s, '/planning')).toBe(0);
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/warehouse-choice.png', fullPage: true });
});

test('chart options float above the page and assumptions only apply on confirmation', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data');
  await page.getByRole('button', { name: 'Giải thích Kỳ kế hoạch', exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText('Khoảng thời gian từ hôm nay');
  await page.keyboard.press('Escape');
  expect(await page.locator('.dw-content').innerText()).not.toContain(';');
  await page.getByRole('tab', { name: 'Phân tích lịch sử', exact: true }).click();
  await page.getByRole('button', { name: 'Cập nhật biểu đồ', exact: true }).click();
  const historyHeight = await page.locator('.dw-main').evaluate(e => e.scrollHeight);
  await page.getByRole('button', { name: 'Tùy chọn biểu đồ', exact: true }).click();
  await expect(page.getByRole('menu')).toBeVisible();
  expect(await page.locator('.dw-main').evaluate(e => e.scrollHeight)).toBe(historyHeight);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Tổng quan', exact: true }).click(); await apply(page);
  await page.getByRole('button', { name: 'Thử bổ sung hàng', exact: true }).click();
  const controls = page.locator('.dw-controls');
  expect(await controls.evaluate(e => parseFloat(getComputedStyle(e).paddingLeft))).toBeGreaterThanOrEqual(16);
  await page.getByRole('button', { name: 'Điều chỉnh giả định', exact: true }).scrollIntoViewIfNeeded();
  const before = await page.locator('.dw-main').evaluate(e => e.scrollHeight);
  await page.getByRole('button', { name: 'Điều chỉnh giả định', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeInViewport({ ratio: 1 });
  await page.getByLabel('Giả sử lượng khách đặt tăng/giảm (%)', { exact: true }).fill('20');
  expect(await page.locator('.dw-main').evaluate(e => e.scrollHeight)).toBe(before);
  expect(count(s, '/scenarios')).toBe(0);
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/assumptions-sheet.png', fullPage: true });
  await page.getByRole('button', { name: 'Hủy', exact: true }).click();
  await expect(page.locator('.pw-assumptions-control')).toContainText('Nhu cầu giữ nguyên');
  await page.getByRole('button', { name: 'Điều chỉnh giả định', exact: true }).click();
  await expect(page.getByLabel('Giả sử lượng khách đặt tăng/giảm (%)', { exact: true })).toHaveValue('0');
  await page.getByLabel('Giả sử lượng khách đặt tăng/giảm (%)', { exact: true }).fill('20');
  await page.getByRole('button', { name: 'Áp dụng giả định', exact: true }).click();
  await expect(page.locator('.pw-assumptions-control')).toContainText('Nhu cầu tăng 20%');
  expect(count(s, '/scenarios')).toBe(0);
});


test('ML scenario separates the shock, shows a quantity/date response, and compares real decision measures', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data'); await apply(page);
  await page.getByRole('button', { name: 'Thử bổ sung hàng', exact: true }).click();
  const loading = s.calls.filter(c => c.path.endsWith('/planning')).at(-1)!.body;
  expect(loading.forecast_model).toBe('chronos2'); expect(loading.training_days).toBe(180);
  await page.getByRole('button', { name: 'Điều chỉnh giả định', exact: true }).click();
  await page.getByLabel('Giả sử lượng khách đặt tăng/giảm (%)', { exact: true }).fill('20');
  await page.getByLabel('Hàng đang chờ về trễ thêm (ngày)', { exact: true }).fill('2');
  await page.getByRole('button', { name: 'Áp dụng giả định', exact: true }).click();
  await page.getByLabel('Số lượng nhập thêm', { exact: true }).fill('50');
  await page.getByLabel('Ngày dự kiến hàng về', { exact: true }).fill('2026-10-26');
  await page.getByRole('button', { name: 'So sánh phương án', exact: true }).click();
  const body = s.calls.filter(c => c.path.endsWith('/scenarios')).at(-1)!.body;
  expect(body.require_ml).toBe(true); expect(body.demand_multiplier).toBe(1.2); expect(body.inbound_delay_days).toBe(2);
  await page.getByRole('tab', { name: 'Chỉ tiêu so sánh', exact: true }).click();
  const comparison = page.getByRole('table', { name: 'Đối chiếu ba kịch bản', exact: true });
  await expect(comparison).toBeVisible(); await expect(comparison).toContainText('ML gốc');
  await expect(comparison).toContainText('Hàng còn phân bổ cuối kỳ');
  await expect(comparison).toContainText('Lượng đặt mới còn có thể tăng');
  const demand = comparison.getByRole('row').filter({ hasText: 'Lượng đặt mới dự báo' });
  await expect(demand.getByRole('cell')).toHaveText(['Lượng đặt mới dự báosản phẩm', '70', '84', '84']);
  await page.getByRole('tab', { name: 'Khả năng đáp ứng', exact: true }).click();
  await page.getByRole('heading', { name: 'So sánh kịch bản từ dự báo ML', exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/ml-scenario-comparison.png', fullPage: true });
  await page.getByRole('tab', { name: 'Nhu cầu & hàng về', exact: true }).click();
  await expect(page.getByRole('img', { name: /Lượng đặt mới · ML gốc, Lượng đặt mới sau điều chỉnh/ })).toBeVisible();
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/ml-scenario-flows.png', fullPage: true });
  const before = count(s, '/scenarios');
  await page.getByRole('button', { name: 'Điền mức bổ sung', exact: true }).click();
  await expect(page.getByLabel('Số lượng nhập thêm', { exact: true })).toHaveValue('79');
  await expect(page.getByLabel('Ngày dự kiến hàng về', { exact: true })).toHaveValue('2026-10-26');
  expect(count(s, '/scenarios')).toBe(before);
  await expect(page.getByRole('button', { name: 'Lưu phương án', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'So sánh phương án', exact: true }).click();
  await expect(page.locator('.pw-scenario-verdict')).toContainText('Đủ nguồn hàng theo kịch bản');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('tab', { name: 'Chỉ tiêu so sánh', exact: true }).click();
  await page.getByRole('table', { name: 'Đối chiếu ba kịch bản', exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/ml-scenario-mobile.png', fullPage: true });
});

test('an already sufficient ML plan exposes extra ending inventory instead of celebrating zero to zero', async ({ page }) => {
  await mock(page); await page.goto('/#data'); await apply(page);
  await page.getByRole('button', { name: 'Thử bổ sung hàng', exact: true }).click();
  await page.route('**/api/v1/data-workspace/scenarios', route => route.fulfill({ json: scenarioResult(route.request().postDataJSON(), 200) }));
  await page.getByLabel('Số lượng nhập thêm', { exact: true }).fill('20');
  await page.getByLabel('Ngày dự kiến hàng về', { exact: true }).fill('2026-10-25');
  await page.getByRole('button', { name: 'So sánh phương án', exact: true }).click();
  await expect(page.locator('.pw-scenario-verdict')).toContainText('chưa giảm rủi ro');
  await expect(page.locator('.pw-scenario-verdict')).toContainText('tăng 20 sản phẩm');
  await expect(page.getByText('Mức bổ sung để hết thiếu trong kỳ: 0', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Điền mức bổ sung', exact: true })).toHaveCount(0);
  await page.getByRole('heading', { name: 'So sánh kịch bản từ dự báo ML', exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/ml-scenario-extra-inventory.png', fullPage: true });
});
