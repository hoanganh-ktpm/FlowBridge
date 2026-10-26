// SPDX-License-Identifier: GPL-3.0-or-later
import { expect, test, type Page } from '@playwright/test';
const names = ['sales.planning_demand_daily','warehouse.stock_availability','warehouse.inbound_schedule','sales.open_orders','warehouse.order_commitments','warehouse.planning_catalog','warehouse.receipts_daily','logistics.handoff_schedule','logistics.handoff_capacity'];
const products = [...names, 'sales.confirmed_demand_daily', 'logistics.accepted_shipments_daily'].map((name, i) => ({ id: `00000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}`, contract_version_id: '00000000-0000-4000-8000-000000000020', name, business_key: ['org_id','sku','day'], fields: ['org_id','sku','day','confirmed_quantity'].map(name => ({ name, column: name, wire_type: name === 'day' ? 'date' : name === 'confirmed_quantity' ? 'number' : 'string' })) }));
const warehouse = '00000000-0000-4000-8000-000000000030';

test('workspace restores authorized reads after navigation and reload, but manual refresh reads again', async ({page})=>{
  const state=await mock(page);
  await page.goto('/#data');
  await expect(page.getByRole('button',{name:'Chọn kho và phạm vi',exact:true})).toBeEnabled();
  await refreshPlan(page);
  await expect(page.getByRole('heading',{name:'Ưu tiên xử lý',exact:true})).toBeVisible();
  const reads=()=>state.calls.filter(c=>c.path.endsWith('/planning')).length;
  expect(reads()).toBe(1);
  await page.getByRole('button',{name:'Tra cứu dữ liệu',exact:true}).click();
  await page.getByRole('button',{name:'Tổng quan kho',exact:true}).click();
  expect(reads()).toBe(1);
  const directory=state.calls.filter(c=>c.path.endsWith('/planning-options')).length;
  await page.reload();
  await expect(page.getByRole('heading',{name:'Ưu tiên xử lý',exact:true})).toBeVisible();
  expect(reads()).toBe(1);
  expect(state.calls.filter(c=>c.path.endsWith('/planning-options')).length).toBe(directory);
  await refreshPlan(page);
  await expect.poll(reads).toBe(2);
});

test('a model upgrade drops old calculations once while keeping the directory and later cache reuse',async({page})=>{
  const state=await mock(page);
  await page.goto('/#data'); await apply(page);
  const reads=()=>count(state,'/planning');
  const directory=count(state,'/planning-options');
  expect(reads()).toBe(1);
  await page.evaluate(()=>{
    for(const key of Object.keys(sessionStorage).filter(k=>k.startsWith('flowbridge.workspace.v1:'))){
      const saved=JSON.parse(sessionStorage.getItem(key)!);
      delete saved.forecastRevision;
      if(saved.states['data:plan'])saved.states['data:plan'].forecast.model={method:'ridge_lag',kind:'machine_learning'};
      sessionStorage.setItem(key,JSON.stringify(saved));
    }
  });
  await page.reload();
  await expect(page.getByText('Chưa có kết quả kiểm tra',{exact:true})).toBeVisible();
  expect(count(state,'/planning-options')).toBe(directory);
  expect(reads()).toBe(1);
  await refreshPlan(page);
  await expect(page.getByRole('heading',{name:'Ưu tiên xử lý',exact:true})).toBeVisible();
  expect(state.calls.filter(c=>c.path.endsWith('/planning')).at(-1)!.body.forecast_model).toBe('chronos2');
  await page.reload();
  await expect(page.getByRole('heading',{name:'Ưu tiên xử lý',exact:true})).toBeVisible();
  expect(reads()).toBe(2);
});

test('a missing Chronos artifact shows the model error without retrying statistics',async({page})=>{
  const state=await mock(page);
  state.status=503;
  state.detail={code:'CHRONOS_UNAVAILABLE',message:'Chưa chạy được mô hình Chronos-2. Cần kiểm tra trọng số mô hình.'};
  await page.goto('/#data');
  await refreshPlan(page);
  await expect(page.getByRole('alert')).toContainText('Chưa chạy được mô hình Chronos-2');
  const calls=state.calls.filter(c=>c.path.endsWith('/planning'));
  expect(calls).toHaveLength(1);
  expect(calls[0].body.forecast_model).toBe('chronos2');
  await expect(page.getByRole('heading',{name:'Ưu tiên xử lý',exact:true})).toHaveCount(0);
});

test('related department changes notify, block old actions and require a fresh read',async({page})=>{
  const state=await mock(page);
  await page.goto('/#data');
  await refreshPlan(page);
  await expect(page.getByRole('heading',{name:'Ưu tiên xử lý',exact:true})).toBeVisible();
  state.revision='c';
  const alert=page.getByRole('alert',{name:'Thông báo thay đổi dữ liệu'});
  await expect(alert).toContainText('Kho vừa cập nhật',{timeout:20000});
  await expect(page.locator('[inert]')).toHaveCount(1);
  await page.screenshot({path:'../.tmp/decision-demo/workspace-sync-notification-test.png'});
  expect(state.calls.filter(c=>c.path.endsWith('/planning')).length).toBe(1);
  await alert.getByRole('button',{name:'Cập nhật dữ liệu ngay'}).click();
  await expect(alert).toHaveCount(0);
  await expect.poll(()=>state.calls.filter(c=>c.path.endsWith('/planning')).length).toBe(2);
  await page.screenshot({path:'../.tmp/decision-demo/workspace-sync-test.png'});
});

test('a new identity or authorization stamp cannot restore another permission snapshot',async({page})=>{
  const state=await mock(page);
  await page.goto('/#data');
  await refreshPlan(page);
  await expect(page.getByRole('heading',{name:'Ưu tiên xử lý',exact:true})).toBeVisible();
  state.stamp='d';
  await page.reload();
  await expect(page.getByText('Chưa có kết quả kiểm tra', {exact:true})).toBeVisible();
  expect(state.calls.filter(c=>c.path.endsWith('/planning-options')).length).toBe(2);
  state.principal='other-user';
  await page.reload();
  await expect(page.getByText('Chưa có kết quả kiểm tra', {exact:true})).toBeVisible();
  expect(state.calls.filter(c=>c.path.endsWith('/planning-options')).length).toBe(3);
});

test('a change found on reopening waits for acknowledgement before loading business data',async({page})=>{
  const state=await mock(page);
  await page.goto('/#data');
  await refreshPlan(page);
  await expect(page.getByRole('heading',{name:'Ưu tiên xử lý',exact:true})).toBeVisible();
  state.revision='e';
  await page.reload();
  const alert=page.getByRole('alert',{name:'Thông báo thay đổi dữ liệu'});
  await expect(alert).toContainText('Kho vừa cập nhật');
  expect(state.calls.filter(c=>c.path.endsWith('/planning-options')).length).toBe(1);
  await alert.getByRole('button',{name:'Cập nhật dữ liệu ngay'}).click();
  await expect(page.getByRole('heading',{name:'Ưu tiên xử lý',exact:true})).toBeVisible();
  expect(state.calls.filter(c=>c.path.endsWith('/planning-options')).length).toBe(2);
  expect(state.calls.filter(c=>c.path.endsWith('/planning')).length).toBe(2);
});

test('a warehouse covered by source revisions reuses its overview until explicitly refreshed',async({page})=>{
  await mock(page); let reads=0;
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  await page.route('**/all-warehouses',r=>{
    reads++;
    return r.fulfill({json:{warehouse_count:1,horizon:7,read_at:evidence.executed_at,items:[{
      organization_id:'test-org',warehouse_id:warehouse,warehouse_name:'Kho kiểm thử',warehouse_code:'KT',
      products:[{sku:'A4',name:'Giấy A4',uom:'ram',on_hand:12,reserved:2,available:10,shortage:0}],
      open_orders:[],planning_status:'calculated',planning_issue:null,plan_read_at:evidence.executed_at,
    }]}});
  });
  await page.goto('/#data');
  await openInventory(page);
  await expect(page.getByRole('table',{name:'Tồn và kế hoạch KT'})).toBeVisible();
  expect(reads).toBe(1);
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:'Tra cứu dữ liệu',exact:true}).click();
  await page.getByRole('button',{name:'Tổng quan kho',exact:true}).click();
  await openInventory(page);
  await expect(page.getByRole('table',{name:'Tồn và kế hoạch KT'})).toBeVisible();
  expect(reads).toBe(1);
  await closeWorkspaceDialogs(page);
  await page.reload();
  await openInventory(page);
  await expect(page.getByRole('table',{name:'Tồn và kế hoạch KT'})).toBeVisible();
  expect(reads).toBe(1);
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:'Làm mới dữ liệu',exact:true}).click();
  await expect.poll(()=>reads).toBe(2);
});
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

type State = { calls: { path: string; body: any }[]; status: number; missing: boolean; optionalMissing: boolean; detail?: { code: string; message: string }; holdout?: number; prepared?: boolean; revision?: string; stamp?: string; principal?: string };
async function mock(page: Page, admin = false) {
  const state: State = { calls: [], status: 200, missing: false, optionalMissing: false };
  await page.route('**/config.json', r => r.fulfill({ json: { apiUrl: 'http://127.0.0.1:18000', issuer: 'https://identity.example.test/realms/flowbridge', clientId: 'flowbridge-web-a' } }));
  await page.route('**/node_modules/.vite/deps/keycloak-js.js*', r => r.fulfill({ contentType: 'application/javascript', body: `export default class Keycloak { authenticated=true;token='test-caller';async init(){return true}async updateToken(){return true}async logout(){} }` }));
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname, body = route.request().postDataJSON();
    expect(route.request().headers().authorization).toBe('Bearer test-caller'); state.calls.push({ path, body });
    if (path.endsWith('/core/me')) return route.fulfill({ json: { principal_id: state.principal || 'test-user', platform_role: admin ? 'PLATFORM_ADMIN' : 'NONE', verified_identity: {} } });
    if (path.endsWith('/changes')) return route.fulfill({json:{schema_version:1,principal_id:state.principal||'test-user',org_id:'test-org',authorization_stamp:(state.stamp||'a').repeat(64),enabled:true,complete:true,coverage:'assigned_warehouses_native_api_events',versions:[{key:'warehouse-revision',org_id:'test-org',warehouse_id:warehouse,producer:'warehouse',token:(state.revision||'b').repeat(64)}]}});
    if (path.endsWith('/products')) return route.fulfill({ json: { items: state.missing ? products.slice(1) : products, can_configure: admin } });
    if (path.endsWith('/planning-options')) return route.fulfill({ json: { warehouses: plan.warehouses, products: plan.products.map(p => ({ warehouse_id: warehouse, sku: p.sku, name: p.name, uom: p.uom })) } });
    if (path.endsWith('/planning')) {
      if (state.status !== 200) return route.fulfill({ status: state.status, json: { detail: state.detail || { code: 'NO_DATA', message: 'Chưa đủ dữ liệu tại nguồn đang chọn.' } } });
      const result = structuredClone(plan); result.selected_sku = body.sku || 'SKU-A'; result.history.frequency = body.history_frequency || 'day';
      (result.orders[0] as any).readiness = { status: 'not_met', conditions: { supply: 'not_met', preparation: state.prepared ? 'met' : 'unknown', schedule: 'not_met', capacity: 'unknown' }, stock_ready_by: state.prepared ? '2026-10-28T07:00:00+07:00' : null, preparation_minutes: state.prepared ? 120 : null, preparation_policy_revision: state.prepared ? 1 : null, actual_handoff_verified: false };
      if (body.forecast_model === 'chronos2') result.forecast = { ...result.forecast, config: { ...result.forecast.config, forecast_model: 'chronos2' }, model: { method: 'chronos2', kind: 'machine_learning', entities: { [result.selected_sku]: { method: 'chronos2', observations: body.training_days, validation: { chronos2: { mae: 2.4 } } } } } as any };
      if (state.holdout !== undefined && body.forecast_model === 'chronos2') (result.forecast.model as any).entities[result.selected_sku].validation.chronos2.mae = state.holdout;
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

test('decision dossier saves one order with retry key and preserves draft semantics', async ({ page }) => {
  const state = await mock(page);
  const order = '00000000-0000-4000-8000-000000000061';
  const keys: string[] = [];
  let dossier: any = null;
  await page.route('**/decision-capabilities', route => route.fulfill({ json: { enabled: true, roles: ['coordinator'], warehouse_ids: [warehouse], workflow_enabled: false } }));
  await page.route('**/scenarios', route => {
    const body = route.request().postDataJSON();
    const result: any = scenarioResult(body);
    result.baseline.orders = [{ order_id: order, order_number: 'SO-001', lines: [{ sku: 'SKU-A' }] }];
    return route.fulfill({ json: result });
  });
  await page.route('**/decisions**', route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'POST') {
      keys.push(route.request().headers()['idempotency-key']);
      if (keys.length === 1) return route.fulfill({ status: 503, json: {} });
      const body = route.request().postDataJSON();
      expect(Object.keys(body).sort()).toEqual(['order_id', 'simulation_run_id', 'title']);
      dossier = { id: 'case-1', ...body, warehouse_id: warehouse, status: 'DRAFT', version: 1, current_revision: 1, participants: [1, 2, 3, 4],
        revision: { id: 'revision-1', number: 1, created_at: evidence.executed_at, payload: { scenario: { ...scenarioResult({ demand_multiplier: 1, inbound_delay_days: 0, additional_quantity: 0, arrival_date: '2026-10-25' }), baseline: { ...baseline, orders: [{ order_id: order, order_number: 'SO-001' }] } }, requires_revalidation: true } } };
      return route.fulfill({ status: 201, json: dossier });
    }
    if (path.endsWith('/timeline')) return route.fulfill({ json: { items: [{ id: 'event-1', event_type: 'CASE_CREATED', created_at: evidence.executed_at, payload: { number: 1 } }], has_more: false } });
    if (path.endsWith('/revisions')) return route.fulfill({ json: { items: [dossier.revision], has_more: false } });
    if (path.endsWith('/case-1')) return route.fulfill({ json: dossier });
    return route.fulfill({ json: { items: dossier ? [dossier] : [], warnings: [], has_more: false } });
  });
  await page.goto('/#data');
  await page.getByRole('button', { name: 'Thử phương án nhập hàng', exact: true }).click();
  await refreshPlan(page);
  await proposalStep(page,'review');
  await page.getByRole('button', { name: 'So sánh phương án', exact: true }).click();
  await closeProposal(page);
  await page.getByRole('button',{name:'Lưu phương án',exact:true}).click();
  await page.getByRole('button',{name:'Tiếp tục lập hồ sơ',exact:true}).click();
  const form=page.getByRole('dialog',{name:'Lập hồ sơ phối hợp',exact:true});
  await expect(form.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();
  await form.getByLabel('Đơn cần phối hợp',{exact:true}).selectOption(order);
  await form.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  await form.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  const create=form.getByRole('button',{name:'Lưu hồ sơ phối hợp',exact:true});
  await create.click();await expect(form.getByRole('alert')).toBeVisible();await create.click();
  await expect(page.getByRole('heading', { name: 'Hồ sơ phối hợp', exact: true })).toBeVisible();
  await openDossierList(page);
  await page.getByRole('button', { name: 'Mở hồ sơ Phối hợp chuẩn bị SO-001', exact: true }).click();
  await expect(page.getByText('Hồ sơ đang ở bước lưu bản nháp.', { exact: false })).toBeVisible();
  expect(keys).toHaveLength(2); expect(keys[0]).toBeTruthy(); expect(keys[0]).toBe(keys[1]);
  expect(state.calls.some(c => /workflow|actions/.test(c.path))).toBe(false);
});

test('manager task stays on its revision, explains unmet conditions and records return reason', async ({ page }) => {
  await mock(page);
  const scenario: any = scenarioResult({ demand_multiplier: 1, inbound_delay_days: 0, additional_quantity: 30, arrival_date: '2026-10-25' });
  scenario.baseline.orders = [{ order_id: 'order-1', order_number: 'SO-001', lines: [] }];
  const dossier = { id:'case-1',title:'Bổ sung cà phê cho SO-001',warehouse_id:warehouse,order_id:'order-1',status:'PENDING_APPROVAL',version:4,current_revision:1,
    revision:{id:'revision-1',number:1,created_at:evidence.executed_at,payload:{scenario,requires_revalidation:true}},participants:[1,2,3,4],actions:{can_submit:false,can_revise:false} };
  let returned = false; const commands:any[] = [];
  await page.route('**/decision-capabilities', r=>r.fulfill({json:{enabled:true,roles:['manager'],warehouse_ids:[warehouse],workflow_enabled:true}}));
  await page.route('**/decisions**', r=>{
    const path = new URL(r.request().url()).pathname;
    if(r.request().method()==='POST') {
      const body=r.request().postDataJSON();commands.push(body);
      expect(body.revision_id).toBe('revision-1');expect(body.actor_principal_id).toBeUndefined();
      if(body.outcome==='approved')return r.fulfill({status:409,json:{detail:{code:'DECISION_CONDITIONS_UNMET',message:'Vận chuyển chưa đủ thời gian chuẩn bị trước lịch đã chọn.'}}});
      expect(body.note).toBe('Tồn kho đã thay đổi, cần Kho xác minh lại.');returned=true;
      return r.fulfill({json:{case:{...dossier,status:'NEEDS_REVISION'},workflow:{tasks:[]},warnings:[]}});
    }
    const detail={...dossier,status:returned?'NEEDS_REVISION':'PENDING_APPROVAL'};
    if(path.endsWith('/workflow'))return r.fulfill({json:{case:detail,workflow:{instance_id:'instance-1',revision_id:'revision-1',status:detail.status,tasks:returned?[]:[{id:'task-1',name:'Quản lý duyệt đúng phiên bản đã xác minh',task_key:'managerReview',role:'manager',mine:true}]},warnings:[]}});
    if(path.endsWith('/timeline'))return r.fulfill({json:{items:returned?[{id:'event-1',event_type:'TASK_COMPLETED',created_at:evidence.executed_at,payload:{note:'Tồn kho đã thay đổi, cần Kho xác minh lại.',role:'manager',outcome:'changes_required'}}]:[],has_more:false}});
    if(path.endsWith('/revisions'))return r.fulfill({json:{items:[dossier.revision],has_more:false}});
    if(path.endsWith('/case-1'))return r.fulfill({json:detail});
    return r.fulfill({json:{items:[detail],warnings:[],has_more:false}});
  });
  await page.goto('/#data');await page.getByRole('button',{name:'Phối hợp và bàn giao',exact:true}).click();await page.getByRole('tab',{name:'Hồ sơ phối hợp',exact:true}).click();
  await openDossierList(page);
  await page.getByRole('button',{name:'Mở hồ sơ Bổ sung cà phê cho SO-001',exact:true}).click();
  await expect(page.getByRole('button',{name:'Trả lại để sửa',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Duyệt phiên bản 1',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('Vận chuyển chưa đủ thời gian chuẩn bị');
  await expect(page.getByRole('alert')).not.toContainText('Hồ sơ đã thay đổi');
  await page.getByLabel('Nhận xét hoặc lý do phản hồi').fill('Tồn kho đã thay đổi, cần Kho xác minh lại.');
  await page.getByRole('button',{name:'Trả lại để sửa',exact:true}).click();
  await page.getByRole('tab',{name:'Lịch sử',exact:true}).click();
  await expect(page.getByText('Tồn kho đã thay đổi, cần Kho xác minh lại.',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Duyệt phiên bản 1',exact:true})).toHaveCount(0);
  expect(commands.map(c=>c.outcome)).toEqual(['approved','changes_required']);
});

test('approved dossier stays open until the server verifies physical handoff evidence',async({page})=>{
  await mock(page);let attempts=0;let complete=false;
  const scenario:any=scenarioResult({demand_multiplier:1,inbound_delay_days:0,additional_quantity:30,arrival_date:'2026-10-25'});
  scenario.baseline.orders=[{order_id:'order-1',order_number:'SO-001',lines:[]}];
  const dossier={id:'case-1',title:'Bàn giao SO-001',warehouse_id:warehouse,order_id:'order-1',status:'APPROVED',version:4,current_revision:1,
    revision:{id:'revision-1',number:1,created_at:evidence.executed_at,payload:{scenario,requires_revalidation:true,handoff_plan:{carrier_code:'CARRIER-1',scheduled_handoff_at:'2026-10-28T10:00:00+07:00'}}},participants:[1,2,3,4],actions:{can_submit:false,can_revise:false}};
  await page.route('**/decision-capabilities',r=>r.fulfill({json:{enabled:true,roles:['warehouse'],warehouse_ids:[warehouse],workflow_enabled:true}}));
  await page.route('**/decisions**',r=>{
    const path=new URL(r.request().url()).pathname;
    if(r.request().method()==='POST'){
      expect(path.endsWith('/verify-handoff')).toBe(true);expect(r.request().postDataJSON()).toEqual({revision_id:'revision-1'});
      attempts++;
      if(attempts===1)return r.fulfill({status:409,json:{detail:'Warehouse has not recorded the full handoff'}});
      complete=true;return r.fulfill({json:{case:{...dossier,status:'COMPLETED'},workflow:{status:'COMPLETED',tasks:[]},warnings:[]}});
    }
    const detail={...dossier,status:complete?'COMPLETED':'APPROVED'};
    if(path.endsWith('/workflow'))return r.fulfill({json:{case:detail,workflow:{instance_id:'instance-1',revision_id:'revision-1',status:detail.status,tasks:[]},warnings:[]}});
    if(path.endsWith('/timeline'))return r.fulfill({json:{items:complete?[{id:'event-1',event_type:'CARRIER_HANDOFF_VERIFIED',created_at:evidence.executed_at,payload:{}}]:[],has_more:false}});
    if(path.endsWith('/revisions'))return r.fulfill({json:{items:[dossier.revision],has_more:false}});
    if(path.endsWith('/case-1'))return r.fulfill({json:detail});
    return r.fulfill({json:{items:[detail],warnings:[],has_more:false}});
  });
  await page.goto('/#data');await page.getByRole('button',{name:'Phối hợp và bàn giao',exact:true}).click();await page.getByRole('tab',{name:'Hồ sơ phối hợp',exact:true}).click();
  await openDossierList(page);
  await page.getByRole('button',{name:'Mở hồ sơ Bàn giao SO-001',exact:true}).click();
  const verify=page.getByRole('button',{name:'Kiểm tra bàn giao thực tế',exact:true});
  await expect(page.getByText('Đã duyệt phương án. Vẫn cần thực hiện nghiệp vụ và xác minh bàn giao thực tế; hồ sơ chưa hoàn tất.',{exact:true})).toBeVisible();
  expect(attempts).toBe(0);await page.getByRole('tab',{name:'Tiến độ bàn giao',exact:true}).click();
  await verify.click();await expect(page.getByRole('alert')).toContainText('Hồ sơ vẫn chưa hoàn tất');
  await verify.click();await expect(page.getByText('Đã đối chiếu đủ các dòng hàng với bằng chứng Kho bàn giao cho đơn vị vận chuyển. Đây là mốc kết thúc quy trình.',{exact:true})).toBeVisible();
  await expect(verify).toHaveCount(0);expect(attempts).toBe(2);
  await expect(page.getByText('Đây là lịch trong phương án đã duyệt; kết quả bàn giao được xác minh riêng bằng bằng chứng Kho.',{exact:false})).toBeVisible();
  await expect(page.getByText('Đây là điều kiện để xác minh, chưa chứng minh đã đặt lịch hoặc đã bàn giao.',{exact:false})).toHaveCount(0);
  // Opening/refetching details also updates the existing list card; do not keep
  // showing APPROVED above a COMPLETED detail until a separate manual refresh.
  await page.getByRole('button',{name:'Về danh sách hồ sơ',exact:true}).click();
  await openDossierList(page);
  const card=page.locator('.pw-saved-card').filter({has:page.getByRole('button',{name:'Mở hồ sơ Bàn giao SO-001',exact:true})});
  await expect(card).toContainText('Đã bàn giao');
  await expect(card).not.toContainText('Đã duyệt');
});

test('saved scenario keeps intraday physical shortage instead of a zero day-end balance', async ({ page }) => {
  await mock(page);
  const saved = scenarioResult({ demand_multiplier: 1, inbound_delay_days: 0, additional_quantity: 30, arrival_date: '2026-10-25', save: true });
  saved.baseline.metrics.peak_shortage = 0;
  saved.proposed.metrics.peak_shortage = 0;
  saved.decision.stressed.peak_shortage = 30;
  saved.decision.proposed.peak_shortage = 0;
  await page.route('**/scenarios/saved', route => route.fulfill({ json: { items: [saved] } }));
  await page.goto('/#data');
  await page.getByRole('button', { name: 'Kết quả đã lưu', exact: true }).click();
  await expect(page.locator('.pw-saved-card')).toContainText('Trước khi bổ sung hàng: 30');
  await expect(page.locator('.pw-saved-card')).toContainText('Sau khi bổ sung theo phương án: 0');
  await expect(page.locator('.pw-saved-card')).toContainText('Không phải số lượng thiếu riêng của đơn');
  await expect(page.locator('.pw-saved-card')).not.toContainText('Kết quả cũ');
});

test('old saved results with ambiguous shortage ask for recalculation', async ({ page }) => {
  await mock(page);
  await page.goto('/#data');
  await page.getByRole('button', { name: 'Kết quả đã lưu', exact: true }).click();
  await expect(page.locator('.pw-saved-card')).toContainText('Cần tính lại trước khi dùng');
});

test('priority briefing links the nearest named order to its source-backed details', async ({ page }) => {
  const state = await mock(page);
  await page.goto('/#data'); await apply(page);
  await expect(page.getByRole('heading', { name: 'Ưu tiên xử lý', exact: true })).toBeVisible();
  await openPriority(page);
  const action = page.getByRole('button', { name: /^SO-001 · hạn/ });
  await expect(action).toBeVisible();
  await action.click();
  await expect(page.getByRole('dialog',{name:'Đơn đến hạn bàn giao',exact:true})).toBeVisible();
  await expect(page.getByRole('table',{name:'Đơn và điều kiện bàn giao',exact:true})).toContainText('SO-001');
  expect(count(state, '/planning')).toBe(1);
});

test('organization records include completed orders, page and filter without forecast or fixture fallback', async ({ page }) => {
  const state=await mock(page);const requests:any[]=[];let denied=false;
  await page.route('**/records', r=>{
    const b=r.request().postDataJSON();requests.push(b);
    if(denied)return r.fulfill({status:403,json:{detail:{message:'Quyền tra cứu đã thu hồi.'}}});
    let items:any[]=[];
    if(b.collection==='orders')items=Array.from({length:b.offset?1:50},(_,i)=>({order_id:'order-'+(i+b.offset),order_number:'SO-'+(i+b.offset),status:b.status||'COMPLETED',created_at:'2026-03-01T10:00:00+07:00',requested_handoff_at:'2026-03-05T10:00:00+07:00',completed_at:'2026-03-04T10:00:00+07:00',line_count:2}));
    if(b.collection==='warehouses')items=[{warehouse_id:'empty-wh',warehouse_code:'KHO-MOI',warehouse_name:'Kho chưa phát sinh tồn',status:'ACTIVE'}];
    if(b.collection==='products')items=[{product_id:'new-product',sku:'CHUA-NHAP',product_name:'Mặt hàng chưa nhập kho',uom:'cái',status:'INACTIVE'}];
    if(b.collection==='order_lines')items=[{order_line_id:'line-1',line_no:1,sku:'SKU-A',product_name:'Cà phê rang',uom:'gói',ordered_quantity:'12.000'}];
    return r.fulfill({json:{items,offset:b.offset||0,page_size:50,has_more:b.collection==='orders'&&!b.offset,read_at:evidence.executed_at}});
  });
  await page.goto('/#data');await page.getByRole('button',{name:'Tra cứu dữ liệu',exact:true}).click();
  await expect(page.getByRole('table')).toHaveCount(0);
  expect(requests).toHaveLength(0);
  await page.getByRole('button',{name:'Đơn hàng và dòng hàng',exact:true}).click();
  await expect(page.getByRole('table',{name:'Danh sách dữ liệu tổ chức'})).toContainText('Đã bàn giao đủ');
  expect(count(state,'/planning')).toBe(0);
  await page.getByRole('button',{name:'Trang sau',exact:true}).click();
  await expect(page.getByRole('table',{name:'Danh sách dữ liệu tổ chức'})).toContainText('SO-50');
  await page.getByRole('button',{name:'Xem hàng của SO-50',exact:true}).click();
  await expect(page.getByRole('table',{name:'Các dòng hàng của đơn'})).toContainText('Cà phê rang');
  await expect(page.getByRole('table',{name:'Các dòng hàng của đơn'}).getByRole('cell',{name:'12 gói',exact:true})).toBeVisible();
  await page.screenshot({path:'../.tmp/decision-demo/interactive-records-dialog.png',animations:'disabled'});
  await page.keyboard.press('Escape');
  await page.getByLabel('Trạng thái',{exact:true}).selectOption('CONFIRMED');
  await expect(page.getByRole('button',{name:'Trang trước',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Lọc và tải dữ liệu',exact:true}).click();
  expect(requests.at(-1)).toMatchObject({collection:'orders',offset:0,status:'CONFIRMED'});
  await expect(page.getByRole('table',{name:'Các dòng hàng của đơn'})).toHaveCount(0);
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:'Danh mục kho',exact:true}).click();
  await expect(page.getByRole('table',{name:'Danh sách dữ liệu tổ chức'})).toContainText('Kho chưa phát sinh tồn');
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:'Danh mục mặt hàng',exact:true}).click();
  await expect(page.getByRole('table',{name:'Danh sách dữ liệu tổ chức'})).toContainText('Mặt hàng chưa nhập kho');
  denied=true;await page.getByRole('button',{name:'Lọc và tải dữ liệu',exact:true}).click();
  await expect(page.getByRole('table',{name:'Danh sách dữ liệu tổ chức'})).toHaveCount(0);
  await expect(page.getByText('Mặt hàng chưa nhập kho',{exact:true})).toHaveCount(0);
});

test('all-warehouse overview includes a second scoped warehouse without inventing a forecast', async ({ page }) => {
  await mock(page);const calls:any[]=[];let denied=false;
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  await page.route('**/all-warehouses',r=>{
    const body=r.request().postDataJSON();calls.push(body);
    if(denied)return r.fulfill({status:403,json:{detail:'Scope revoked'}});
    const common={products:[{sku:'SAME-SKU',name:'Giấy A4',uom:'ram',on_hand:12,reserved:2,available:10,shortage:3}],open_orders:[],planning_status:'calculated',planning_issue:null};
    return r.fulfill({json:{warehouse_count:2,horizon:body.horizon,read_at:evidence.executed_at,items:[
      {...common,organization_id:'org-1',warehouse_id:'wh-1',warehouse_name:'Kho Bình Dương',warehouse_code:'BD'},
      {...common,organization_id:'org-2',warehouse_id:'wh-2',warehouse_name:'Kho Hồ Chí Minh',warehouse_code:'HCM',planning_status:'needs_data',planning_issue:{code:'INSUFFICIENT_HISTORY',message:'Cần ít nhất 14 kỳ lịch sử.'},products:[{...common.products[0],shortage:null}]},
    ]}});
  });
  await page.goto('/#data');
  await expect(page.getByRole('heading',{name:'Tổng quan kho và đơn hàng',exact:true})).toBeVisible();
  await expect(page.getByLabel('Kho đang xem',{exact:true}).locator('option')).toHaveText(['Kho Bình Dương (BD)','Kho Hồ Chí Minh (HCM)']);
  await openInventory(page);
  await expect(page.getByRole('table',{name:'Tồn và kế hoạch BD'})).toContainText('3');
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:/Xem tổng quan \d+ kho/}).click();await page.getByRole('article',{name:'Kho Hồ Chí Minh'}).getByRole('button',{name:'Xem tồn kho'}).click();
  await expect(page.getByRole('table',{name:'Tồn và kế hoạch HCM'})).toContainText('Chưa có dự báo');
  await page.getByText('Xem lý do',{exact:true}).click();
  await expect(page.getByText(/Cần ít nhất 14 kỳ lịch sử/)).toBeVisible();
  await closeWorkspaceDialogs(page);
  await page.getByLabel('Khoảng thời gian dự báo',{exact:true}).selectOption('14');
  await expect(page.getByRole('status').filter({hasText:'Chưa áp dụng khoảng thời gian mới.'})).toBeVisible();
  await page.getByRole('button',{name:'Làm mới dữ liệu',exact:true}).click();
  expect(calls.at(-1)).toEqual({horizon:14,include_planning:false});
  await openInventory(page);
  await expect(page.getByRole('table',{name:'Tồn và kế hoạch HCM'})).toBeVisible();
  await closeWorkspaceDialogs(page);
  denied=true;await page.getByRole('button',{name:'Làm mới dữ liệu',exact:true}).click();
  await expect(page.getByRole('table',{name:'Tồn và kế hoạch BD'})).toHaveCount(0);
  await expect(page.getByRole('table',{name:'Tồn và kế hoạch HCM'})).toHaveCount(0);
});

test('inventory stays usable while warehouse forecasts run separately and a later denial clears it', async ({ page }) => {
  await mock(page);
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  const waiting=new Map<string,()=>void>();let retry=false;
  const common={organization_id:'org-1',products:[{sku:'A4',name:'Giấy A4',uom:'ram',on_hand:12,reserved:2,available:10,shortage:null}],open_orders:[],planning_status:'pending',planning_issue:null,plan_read_at:null};
  const warehouses=[{...common,warehouse_id:'wh-1',warehouse_name:'Kho Bình Dương',warehouse_code:'BD'}, {...common,warehouse_id:'wh-2',warehouse_name:'Kho Hồ Chí Minh',warehouse_code:'HCM'}];
  await page.route('**/all-warehouses',async r=>{
    const body=r.request().postDataJSON();
    if(!body.include_planning)return r.fulfill({json:{items:warehouses,warehouse_count:2,horizon:body.horizon,read_at:evidence.executed_at}});
    if(retry)return r.fulfill({status:403,json:{detail:'Scope revoked'}});
    await new Promise<void>(resolve=>waiting.set(body.warehouse_id,resolve));
    if(body.warehouse_id==='wh-2')return r.fulfill({status:503,json:{detail:'Planning unavailable'}});
    return r.fulfill({json:{items:[{...warehouses[0],planning_status:'calculated',products:[{...common.products[0],shortage:3}]}],warehouse_count:1,horizon:body.horizon,read_at:evidence.executed_at}});
  });
  await page.goto('/#data');
  const bd=page.getByRole('table',{name:'Tồn và kế hoạch BD'}),hcm=page.getByRole('table',{name:'Tồn và kế hoạch HCM'});
  await openInventory(page);
  await expect(bd).toContainText('Giấy A4');
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:/Xem tổng quan \d+ kho/}).click();
  await expect(page.getByRole('article',{name:'Kho Hồ Chí Minh'})).toContainText('Đang tính…');
  await page.keyboard.press('Escape');
  await openInventory(page);
  await expect(bd.getByRole('row').nth(1).getByRole('cell').nth(3)).toHaveText('10');
  await closeWorkspaceDialogs(page);
  await expect(page.getByRole('button',{name:'Làm mới dữ liệu',exact:true})).toBeEnabled();
  await expect(page.getByLabel('Kho đang xem',{exact:true})).toBeEnabled();
  await expect.poll(()=>waiting.size).toBe(2);
  waiting.get('wh-1')!();
  await openInventory(page);
  await expect(bd.getByRole('row').nth(1).getByRole('cell').nth(4)).toHaveText('3 ram');
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:/Xem tổng quan \d+ kho/}).click();await page.getByRole('article',{name:'Kho Hồ Chí Minh'}).getByRole('button',{name:'Xem tồn kho'}).click();
  await expect(hcm).toContainText('Đang tính…');
  waiting.get('wh-2')!();
  await expect(page.getByRole('button',{name:'Thử tính lại',exact:true})).toBeVisible();
  await expect(hcm).toContainText('Giấy A4');
  await expect(hcm.getByRole('row').nth(1).getByRole('cell').nth(4)).toHaveText('Chưa có dự báo');
  retry=true;await page.getByRole('button',{name:'Thử tính lại',exact:true}).click();
  await expect(bd).toHaveCount(0);await expect(hcm).toHaveCount(0);
});

test('refreshing the warehouse overview cancels an old background result instead of overwriting the new period', async ({ page }) => {
  await mock(page);
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  let release:()=>void=()=>{};let oldStarted=false;
  const warehouse={organization_id:'org',warehouse_id:'wh',warehouse_name:'Kho Bình Dương',warehouse_code:'BD',products:[{sku:'A4',name:'Giấy A4',uom:'ram',on_hand:12,reserved:2,available:10,shortage:null}],open_orders:[],planning_status:'pending',planning_issue:null,plan_read_at:null};
  await page.route('**/all-warehouses',async r=>{
    const body=r.request().postDataJSON();
    if(body.include_planning&&body.horizon===7){oldStarted=true;await new Promise<void>(resolve=>{release=resolve;});}
    const item={...warehouse,...(body.include_planning?{planning_status:'calculated',products:[{...warehouse.products[0],shortage:body.horizon===14?5:99}]}:{})};
    // The seven-day HTTP request is deliberately cancelled by the refresh.
    await r.fulfill({json:{items:[item],warehouse_count:1,horizon:body.horizon,read_at:evidence.executed_at}}).catch(()=>{});
  });
  await page.goto('/#data');
  await expect.poll(()=>oldStarted).toBe(true);
  await page.getByLabel('Khoảng thời gian dự báo',{exact:true}).selectOption('14');
  await page.getByRole('button',{name:'Làm mới dữ liệu',exact:true}).click();
  await openInventory(page);
  const amount=page.getByRole('table',{name:'Tồn và kế hoạch BD'}).getByRole('row').nth(1).getByRole('cell').nth(4);
  await expect(amount).toHaveText('5 ram');release();
  await expect(page.getByText(/mặt hàng có thể thiếu trong 14 ngày/)).toBeVisible();
  await expect(amount).toHaveText('5 ram');
});

test('warehouse redesign filters named stock, pages orders and opens the exact scoped analysis', async ({ page }) => {
  const state=await mock(page);
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  const first={organization_id:'org-1',warehouse_id:warehouse,warehouse_name:'Kho Hà Nội',warehouse_code:'HN',products:[
    {sku:'SKU-A',name:'Băng keo đóng thùng',uom:'cuộn',on_hand:30,reserved:20,available:10,shortage:3},
    {sku:'SKU-B',name:'Giấy A4',uom:'ram',on_hand:50,reserved:10,available:40,shortage:0},
  ],open_orders:Array.from({length:25},(_,i)=>({order_id:'order-'+i,order_number:'SO-REVIEW-'+String(i+1).padStart(3,'0'),deadline:'2026-11-'+String(i+1).padStart(2,'0')+'T17:00:00+07:00',status:'CONFIRMED',lines:[{sku:'SKU-A',uom:'cuộn',ordered_quantity:10+i},...(i===0?[{sku:'SKU-B',uom:'ram',ordered_quantity:'2.125'}]:[])]})),planning_status:'calculated',planning_issue:null,plan_read_at:evidence.executed_at};
  const second={organization_id:'org-2',warehouse_id:'foreign-warehouse',warehouse_name:'Kho Hồ Chí Minh',warehouse_code:'HCM',products:[{sku:'SKU-A',name:'Mực in của kho khác',uom:'hộp',on_hand:8,reserved:0,available:8,shortage:null}],open_orders:[],planning_status:'needs_data',planning_issue:{code:'NO_DATA',message:'Chưa đủ lịch sử.'},plan_read_at:null};
  await page.route('**/all-warehouses',r=>r.fulfill({json:{items:[first,second],warehouse_count:2,horizon:r.request().postDataJSON().horizon,read_at:evidence.executed_at}}));
  await page.goto('/#data');
  await expect(page.getByRole('heading',{name:'Tổng quan kho và đơn hàng',exact:true})).toBeVisible();
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:/Xem tổng quan \d+ kho/}).click();
  await expect(page.getByRole('img',{name:/Khả năng đủ hàng của Kho Hà Nội: 1 mặt hàng có thể thiếu, 1 chưa thấy thiếu, 0 chưa có dự báo/})).toBeVisible();
  await page.keyboard.press('Escape');
  await openInventory(page);
  const stock=page.getByRole('table',{name:'Tồn và kế hoạch HN'});
  await page.getByRole('group',{name:'Lọc mặt hàng'}).getByRole('button',{name:/Có thể thiếu/}).click();
  await expect(stock.getByRole('row')).toHaveCount(2);
  await page.getByLabel('Tìm mặt hàng trong kho',{exact:true}).fill('bang keo dong thung');
  await expect(stock).toContainText('Băng keo đóng thùng');
  await expect(stock).not.toContainText('Mực in của kho khác');
  await page.getByLabel('Tìm mặt hàng trong kho',{exact:true}).fill('giay');
  await expect(page.getByText('Không có mặt hàng khớp bộ lọc',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Bỏ bộ lọc',exact:true}).click();
  await expect(stock.getByRole('row')).toHaveCount(3);
  await page.locator('.dw-main').evaluate(element=>{element.scrollTop=0;});
  await page.screenshot({path:'../.tmp/decision-demo/warehouse-redesign-desktop-test.png',fullPage:true,animations:'disabled'});
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:/^Xem đơn hàng \(\d+\)$/}).click();
  const orders=page.getByRole('table',{name:'Đơn hàng HN'});
  await expect(orders.getByRole('row')).toHaveCount(21);
  const quantities=page.getByRole('list',{name:'Số lượng đặt trong SO-REVIEW-001',exact:true});
  await expect(quantities).toContainText('10 cuộn');
  await expect(quantities).toContainText('2,125 ram');
  await expect(quantities).not.toContainText('Mực in của kho khác');
  await page.getByRole('button',{name:'Trang sau',exact:true}).click();
  await expect(orders).toContainText('SO-REVIEW-021');
  await expect(orders.getByRole('row')).toHaveCount(6);
  await page.getByLabel('Tìm đơn hàng trong kho',{exact:true}).fill('SO-REVIEW-025');
  await page.getByRole('button',{name:'Xem hàng của SO-REVIEW-025',exact:true}).click();
  await expect(page.getByRole('region',{name:'Mặt hàng của SO-REVIEW-025'})).toContainText('34 cuộn');
  await expect(page.getByRole('region',{name:'Mặt hàng của SO-REVIEW-025'})).not.toContainText('Mực in của kho khác');
  await expect(page.getByRole('dialog',{name:'SO-REVIEW-025',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Đóng chi tiết',exact:true}).click();
  await closeWorkspaceDialogs(page);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.locator('.dw-main').evaluate(element=>{element.scrollTop=0;});
  await page.screenshot({path:'../.tmp/decision-demo/warehouse-redesign-mobile-test.png',fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1440,height:1000});
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:/Xem tổng quan \d+ kho/}).click();await page.getByRole('article',{name:'Kho Hồ Chí Minh'}).getByRole('button',{name:'Xem tồn kho'}).click();
  await expect(page.getByRole('button',{name:'Thử phương án nhập hàng',exact:true})).toHaveCount(0);
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:/Xem tổng quan \d+ kho/}).click();await page.getByRole('article',{name:'Kho Hà Nội'}).getByRole('button',{name:'Xem tồn kho'}).click();
  await closeWorkspaceDialogs(page);
  await page.getByLabel('Khoảng thời gian dự báo',{exact:true}).selectOption('14');
  await openInventory(page);
  await expect(page.getByRole('button',{name:'Thử bổ sung',exact:true})).toBeDisabled();
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:'Làm mới dữ liệu',exact:true}).click();
  await openInventory(page);
  await page.getByRole('button',{name:'Thử bổ sung',exact:true}).click();
  await expect.poll(()=>state.calls.filter(c=>c.path.endsWith('/planning')).length).toBe(1);
  expect(state.calls.find(c=>c.path.endsWith('/planning'))!.body).toMatchObject({warehouse_id:warehouse,sku:'SKU-A',forecast_model:'chronos2',horizon:14});
});

test('navigation groups related work and shortage leads to a scoped proposal without a long page', async ({page})=>{
  const state=await mock(page,true);
  await page.route('**/decision-capabilities',r=>r.fulfill({json:{enabled:true,roles:['coordinator'],warehouse_ids:[warehouse],workflow_enabled:true}}));
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  const item={organization_id:'org',warehouse_id:warehouse,warehouse_name:'Kho Hà Nội',warehouse_code:'HN',products:[{sku:'SKU-A',name:'Cà phê rang',uom:'EA',on_hand:25,reserved:5,available:20,shortage:65}],open_orders:Array.from({length:25},(_,i)=>({order_id:'order-'+i,order_number:'SO-SCOPE-'+i,deadline:'2026-10-28T09:00:00+07:00',status:'CONFIRMED',lines:[{sku:'SKU-A',uom:'EA',ordered_quantity:30}],supply_check:{status:'risk',shortages:[{sku:'SKU-A',uom:'EA',missing_quantity:10}],stock_ready_by:'2026-10-28T07:00:00+07:00',preparation_minutes:120,notes:[]}})),planning_status:'calculated',planning_issue:null,plan_read_at:evidence.executed_at};
  await page.route('**/all-warehouses',r=>r.fulfill({json:{items:[item],warehouse_count:1,horizon:r.request().postDataJSON().horizon,read_at:evidence.executed_at}}));
  await page.goto('/#data');
  const work=page.getByRole('navigation',{name:'Lập kế hoạch và phối hợp',exact:true}),tools=page.getByRole('navigation',{name:'Dữ liệu và công cụ',exact:true});
  await expect(work.getByRole('button')).toHaveText(['Tổng quan kho','Dự báo nhu cầu','Đơn hàng và tác động','Thử phương án nhập hàng','Phối hợp và bàn giao']);
  await expect(tools.getByRole('button')).toHaveText(['Tra cứu dữ liệu','Phân tích lịch sử','Thiết lập cho IT']);
  await page.getByLabel('Khoảng thời gian dự báo',{exact:true}).selectOption('14');
  await page.getByRole('button',{name:'Làm mới dữ liệu',exact:true}).click();
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:/^Xem đơn hàng \(\d+\)$/}).click();
  const scroll=page.getByRole('region',{name:'Bảng dữ liệu kho',exact:true}).filter({has:page.getByRole('table',{name:'Đơn hàng HN'})});
  const size=await scroll.evaluate(e=>({viewport:e.clientHeight,content:e.scrollHeight}));
  expect(size.viewport).toBeLessThanOrEqual(480);expect(size.content).toBeGreaterThan(size.viewport);
  expect(await page.locator('.dw-main').evaluate(e=>e.scrollHeight)).toBeLessThan(1500);
  await page.getByRole('button',{name:'Có thể thiếu hàng của SO-SCOPE-0',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Thử bổ sung',exact:true}).click();
  await closeProposal(page);
  await expect(page.getByRole('button',{name:'Thiết lập phương án',exact:true})).toBeEnabled();
  expect(state.calls.find(c=>c.path.endsWith('/planning'))!.body).toMatchObject({warehouse_id:warehouse,sku:'SKU-A',horizon:14,forecast_model:'chronos2'});
  await expect(page.getByRole('button',{name:'Lập hồ sơ phối hợp',exact:true})).toBeDisabled();
  await proposalStep(page,'review');await page.getByRole('button',{name:'So sánh phương án',exact:true}).click();
  await expect(page.getByRole('button',{name:'Tiếp tục lập hồ sơ',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Lưu phương án',exact:true}).click();
  await page.getByRole('button',{name:'Tiếp tục lập hồ sơ',exact:true}).click();
  const form=page.getByRole('dialog',{name:'Lập hồ sơ phối hợp',exact:true});
  await expect(form.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();
  await expect(form.getByRole('button',{name:'Lưu hồ sơ phối hợp',exact:true})).toHaveCount(0);
  await closeWorkspaceDialogs(page);
  expect(state.calls.filter(c=>c.path.endsWith('/planning'))).toHaveLength(1);
  expect(state.calls.filter(c=>c.path.endsWith('/scenarios'))).toHaveLength(2);
  expect(state.calls.filter(c=>c.path.endsWith('/decisions'))).toHaveLength(0);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('overview restores lazy warehouse charts, keeps SKU scope and never invents missing receipts', async ({page})=>{
  const state=await mock(page);
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  const calls:any[]=[];let denied=false;let receipts=true;
  const item={organization_id:'org-1',warehouse_id:warehouse,warehouse_name:'Kho Hà Nội',warehouse_code:'HN',products:plan.products.map(p=>({...p,shortage:p.shortage})),open_orders:[],planning_status:'calculated',planning_issue:null,plan_read_at:evidence.executed_at};
  const other={...item,organization_id:'org-2',warehouse_id:'other-wh',warehouse_name:'Kho Hồ Chí Minh',warehouse_code:'HCM',products:[{...item.products[0],name:'Mực in riêng kho HCM'}]};
  await page.route('**/all-warehouses',r=>{
    const body=r.request().postDataJSON();calls.push(body);
    if(body.include_analysis){
      if(denied)return r.fulfill({status:403,json:{detail:'Receipt source revoked'}});
      const chosen=body.warehouse_id===warehouse?item:other;
      const analysis={warehouse_id:chosen.warehouse_id,as_of:evidence.executed_at,selected_sku:body.analysis_sku,horizon:body.horizon,history_days:body.history_days,
        products:chosen.products,baseline,explanations:{history:explanation,risk:explanation,balance:explanation},
        history:{...plan.history,frequency:body.history_frequency,receipts_available:receipts,series:plan.history.series.map(row=>({...row,...(!receipts?{received:null,difference:null}:{})}))}};
      return r.fulfill({json:{items:[{...chosen,analysis}],warehouse_count:1,horizon:body.horizon,read_at:evidence.executed_at}});
    }
    return r.fulfill({json:{items:[item,other],warehouse_count:2,horizon:body.horizon,read_at:evidence.executed_at}});
  });
  await page.goto('/#data');
  await expect(page.getByRole('button',{name:/^Xem tồn kho/})).toBeVisible();
  expect(calls.filter(c=>c.include_analysis)).toHaveLength(0);
  await page.getByRole('button',{name:'Xem biểu đồ phân tích',exact:true}).click();
  const region=page.getByRole('region',{name:'Biểu đồ phân tích HN',exact:true});
  await expect(region.getByRole('img',{name:/Lượng đặt xác nhận và thực nhập theo ngày/})).toBeVisible();
  await expect(region).toContainText('Thực nhập kho');
  expect(calls.filter(c=>c.include_analysis)).toEqual([{warehouse_id:warehouse,horizon:7,include_planning:true,include_analysis:true,analysis_sku:'SKU-A',history_days:30,history_frequency:'day'}]);
  await region.getByRole('button',{name:'Nguồn và cách tính',exact:true}).click();
  await expect(page.getByRole('dialog').last()).toContainText('Đối chiếu hàng có thể phân bổ');
  await page.keyboard.press('Escape');
  await region.getByRole('tab',{name:'Nguy cơ thiếu hàng',exact:true}).click();
  await expect(region.getByRole('img',{name:/Nguy cơ thiếu hàng theo sản phẩm/})).toBeVisible();
  await expect(region.getByRole('img',{name:/Lượng đặt xác nhận/})).toHaveCount(0);
  await region.getByRole('tab',{name:'Diễn biến đủ và thiếu',exact:true}).click();
  await expect(region.getByRole('heading',{name:'Diễn biến đủ và thiếu hàng',exact:true})).toBeVisible();
  expect(calls.filter(c=>c.include_analysis)).toHaveLength(1);
  await page.getByRole('dialog',{name:/^Biểu đồ phân tích ·/}).getByRole('button',{name:'Đóng hộp thoại',exact:true}).click();
  await expect(page.getByRole('button',{name:/^Xem tồn kho/})).toBeVisible();
  await page.getByRole('button',{name:'Xem biểu đồ phân tích',exact:true}).click();
  expect(calls.filter(c=>c.include_analysis)).toHaveLength(1);
  await region.getByRole('tab',{name:'Lượng đặt và thực nhập',exact:true}).click();
  await page.getByLabel('Mặt hàng phân tích',{exact:true}).selectOption('SKU-B');
  await expect(region.getByRole('status')).toContainText('Lựa chọn đã thay đổi');
  expect(calls.filter(c=>c.include_analysis)).toHaveLength(1);
  receipts=false;
  await page.getByLabel('Gộp biểu đồ theo',{exact:true}).selectOption('week');
  await page.getByRole('button',{name:'Cập nhật biểu đồ',exact:true}).click();
  await expect(region.getByRole('img',{name:/Lượng đặt xác nhận và thực nhập theo tuần/})).toBeVisible();
  await expect(region).toContainText('Chưa có nguồn thực nhập được chia sẻ');
  await expect(region.getByRole('img',{name:/Lượng đặt xác nhận và thực nhập theo tuần/})).not.toContainText('Thực nhập kho');
  expect(calls.filter(c=>c.include_analysis).at(-1)).toMatchObject({analysis_sku:'SKU-B',history_frequency:'week'});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.setViewportSize({width:1440,height:1000});
  await closeWorkspaceDialogs(page);
  await page.getByLabel('Kho đang xem',{exact:true}).selectOption('other-wh');
  await page.getByRole('button',{name:'Xem biểu đồ phân tích',exact:true}).click();
  const hcm=page.getByRole('region',{name:'Biểu đồ phân tích HCM',exact:true});
  await expect(hcm).toContainText('Mực in riêng kho HCM');
  await expect(region).toHaveCount(0);
  expect(calls.filter(c=>c.include_analysis).at(-1).warehouse_id).toBe('other-wh');
  denied=true;await hcm.getByRole('button',{name:'Cập nhật biểu đồ',exact:true}).click();
  await expect(page.getByRole('region',{name:'Biểu đồ phân tích HCM',exact:true})).toHaveCount(0);
  await expect(page.getByRole('table',{name:'Tồn và kế hoạch HN'})).toHaveCount(0);
  expect(state.calls.filter(c=>c.path.endsWith('/scenarios'))).toHaveLength(0);
});

test('order charts expose deadline pressure and whole-order conditions with scoped details and no writes',async({page})=>{
  const state=await mock(page); const calls:any[]=[]; let denied=false;
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  const conditions={supply:'met',preparation:'met',schedule:'met',capacity:'met'};
  const order=(id:string,classification:string,patch:Record<string,string>={},deadline='2026-10-26T17:00:00+07:00')=>({
    order_id:id,order_number:id,deadline,classification,at_risk:patch.supply==='not_met',already_overdue:Date.parse(deadline)<Date.parse('2026-10-26T10:00:00+07:00'),
    status:'CONFIRMED',fulfilled_at:null,reason:'stock',schedule_status:patch.schedule==='unknown'?'unbooked':'booked',shipments:[],
    products:[{sku:'SKU-A',name:'Cà phê rang',uom:'EA'},{sku:'PAPER',name:'Giấy A4',uom:'ram'}],
    lines:[{sku:'SKU-A',uom:'EA',ordered_quantity:10},{sku:'PAPER',uom:'ram',ordered_quantity:4}],
    shortages_at_deadline:patch.supply==='not_met'?[{sku:'PAPER',uom:'ram',missing_quantity:2}]:[],supply_notes:[],
    readiness:{status:classification==='ready'?'conditionally_feasible':classification==='blocked'?'not_met':'unknown',conditions:{...conditions,...patch},stock_ready_by:'2026-10-26T15:00:00+07:00',preparation_minutes:120,preparation_policy_revision:1,actual_handoff_verified:false},
  });
  const orders=[order('SO-PAPER','blocked',{supply:'not_met',schedule:'not_met'}),order('SO-UNKNOWN','unknown',{capacity:'unknown'}),order('SO-READY','ready'),order('SO-PAST','unknown',{supply:'unknown'},'2026-10-26T09:00:00+07:00')];
  const item={organization_id:'test-org',warehouse_id:warehouse,warehouse_name:'Kho Hà Nội',warehouse_code:'HN',products:plan.products,open_orders:orders,planning_status:'calculated',planning_issue:null,plan_read_at:evidence.executed_at};
  await page.route('**/all-warehouses',r=>{
    const body=r.request().postDataJSON(); calls.push(body);
    if(body.include_order_analysis&&denied)return r.fulfill({status:403,json:{detail:'Scope revoked'}});
    return r.fulfill({json:{items:[{...item,
      ...(body.include_analysis?{analysis:{warehouse_id:warehouse,as_of:evidence.executed_at,selected_sku:body.analysis_sku,horizon:7,history_days:30,products:plan.products,baseline,history:plan.history,explanations:{}}}:{}),
      ...(body.include_order_analysis?{order_analysis:{as_of:'2026-10-26T10:00:00+07:00',start:'2026-10-26',end:'2026-11-01',horizon:7,outside_horizon_count:3,orders,missing_sources:[],assessment_available:true}}:{}),
    }],warehouse_count:1,horizon:7,read_at:evidence.executed_at}});
  });
  await page.goto('/#data');
  await expect(page.getByRole('button',{name:/^Xem tồn kho/})).toBeVisible();
  expect(calls.filter(c=>c.include_order_analysis)).toHaveLength(0);
  await page.getByRole('button',{name:'Xem biểu đồ phân tích',exact:true}).click();
  await page.getByRole('tab',{name:'Theo đơn hàng',exact:true}).click();
  const region=page.getByRole('region',{name:'Phân tích đơn hàng HN',exact:true});
  await expect(region).toContainText('4 đơn chưa hoàn tất');
  await expect(region).toContainText('3 đơn có hạn sau kỳ');
  await expect(region.getByRole('button',{name:/Xem các đơn Đã quá hạn: 1 đơn/})).toBeVisible();
  expect(calls.filter(c=>c.include_order_analysis)).toEqual([{warehouse_id:warehouse,horizon:7,include_planning:true,include_order_analysis:true}]);
  await region.getByRole('button',{name:/Xem các đơn 26\/10\/2026: 3 đơn, 1 có vướng mắc, 1 chưa xác minh đủ, 1 đáp ứng/}).click();
  const table=page.getByRole('table',{name:'Điều kiện bàn giao theo từng đơn'});
  await expect(table).toContainText('SO-PAPER');await expect(table).not.toContainText('SO-PAST');
  await table.getByRole('button',{name:'Xem phân tích SO-PAPER',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Phân tích SO-PAPER',exact:true});
  await expect(dialog).toContainText('Thiếu 2 ram Giấy A4 (PAPER)');
  await expect(dialog).toContainText('10 sản phẩm'); await expect(dialog).toContainText('4 ram');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await page.getByRole('dialog',{name:'Các đơn cần đối chiếu',exact:true}).getByRole('button',{name:'Đóng hộp thoại',exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await region.evaluate(el=>el.getBoundingClientRect().right<=innerWidth)).toBe(true);
  expect(await region.getByRole('button',{name:'Cập nhật biểu đồ đơn hàng',exact:true}).evaluate(el=>el.getBoundingClientRect().right<=innerWidth)).toBe(true);
  await page.screenshot({path:'../.tmp/decision-demo/order-analysis-mobile-test.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:'../.tmp/decision-demo/order-analysis-matrix-test.png',fullPage:true});
  await page.getByRole('tab',{name:'Hạn bàn giao',exact:true}).click();
  await page.screenshot({path:'../.tmp/decision-demo/order-analysis-deadlines-test.png',fullPage:true});
  await page.getByRole('tab',{name:'Theo mặt hàng',exact:true}).click();
  await page.getByRole('tab',{name:'Theo đơn hàng',exact:true}).click();
  expect(calls.filter(c=>c.include_order_analysis)).toHaveLength(1);
  denied=true;await region.getByRole('button',{name:'Cập nhật biểu đồ đơn hàng',exact:true}).click();
  await expect(region).toHaveCount(0);
  expect(state.calls.filter(c=>c.path.endsWith('/scenarios')||c.path.endsWith('/forecasts'))).toHaveLength(0);
});

test('order supply warnings open exact per-order shortages and keep pending or unknown separate', async ({ page }) => {
  await mock(page);
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  let release:()=>void=()=>{};let started=false;let requests=0;
  const check={status:'risk',shortages:[{sku:'SKU-A',uom:'cái',missing_quantity:10},{sku:'SKU-B',uom:'ram',missing_quantity:1.125}],stock_ready_by:'2026-10-27T08:00:00+07:00',preparation_minutes:120,notes:[]};
  const order={order_id:'risk',order_number:'SO-RISK',deadline:'2026-10-27T10:00:00+07:00',status:'CONFIRMED',lines:[{sku:'SKU-A',uom:'cái',ordered_quantity:168},{sku:'SKU-B',uom:'ram',ordered_quantity:122}],supply_check:check};
  const item={organization_id:'org',warehouse_id:warehouse,warehouse_name:'Kho Bình Dương',warehouse_code:'BD',products:[
    {sku:'SKU-A',name:'Bàn phím Logitech K120',uom:'cái',on_hand:618,reserved:608,available:10,shortage:392},
    {sku:'SKU-B',name:'Giấy A4',uom:'ram',on_hand:50,reserved:20,available:30,shortage:9},
  ],open_orders:[order,
    {...order,order_id:'clear',order_number:'SO-CLEAR',supply_check:{...check,status:'clear',shortages:[]}},
    {...order,order_id:'unknown',order_number:'SO-UNKNOWN',supply_check:{...check,status:'unknown',shortages:[],notes:['Chưa có dự báo đầy đủ cho tất cả mặt hàng của đơn.']}},
    {...order,order_id:'outside',order_number:'SO-OUTSIDE',supply_check:{...check,status:'outside_horizon',shortages:[],notes:['Hạn bàn giao của đơn nằm ngoài khoảng thời gian đang dự báo.']}},
  ],planning_status:'calculated',planning_issue:null,plan_read_at:evidence.executed_at};
  await page.route('**/all-warehouses',async r=>{
    requests++;const body=r.request().postDataJSON();
    if(body.include_planning){started=true;await new Promise<void>(resolve=>{release=resolve;});}
    return r.fulfill({json:{items:[body.include_planning?item:{...item,planning_status:'pending'}],warehouse_count:1,horizon:body.horizon,read_at:evidence.executed_at}});
  });
  await page.goto('/#data');
  await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:/^Xem đơn hàng \(\d+\)$/}).click();
  await expect(page.getByRole('button',{name:'Đang kiểm tra… của SO-RISK',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Chưa thấy thiếu hàng của SO-RISK',exact:true})).toHaveCount(0);
  await expect.poll(()=>started).toBe(true);release();
  await page.getByRole('button',{name:'Có thể thiếu hàng của SO-RISK',exact:true}).click();
  const detail=page.getByRole('region',{name:'Kiểm tra thiếu hàng của SO-RISK',exact:true});
  await expect(detail).toContainText('08:00 27/10/2026');
  const shortages=page.getByRole('table',{name:'Mặt hàng có thể thiếu của SO-RISK',exact:true});
  await expect(shortages).toContainText('Bàn phím Logitech K120');
  await expect(shortages).toContainText('10 cái');
  await expect(shortages).toContainText('Giấy A4');
  await expect(shortages).toContainText('1,125 ram');
  await expect(shortages).not.toContainText('392');
  await page.getByText('Căn cứ tính lượng thiếu',{exact:true}).click();
  await expect(detail).toContainText('không phải số lượng đặt hay lượng thiếu chung của cả kho');
  expect(requests).toBe(2);
  await page.getByRole('button',{name:'Đóng chi tiết',exact:true}).click();
  await page.getByRole('button',{name:'Chưa thấy thiếu hàng của SO-CLEAR',exact:true}).click();
  await expect(page.getByRole('region',{name:'Kiểm tra thiếu hàng của SO-CLEAR',exact:true})).toContainText('Vẫn cần Kho xác nhận');
  await page.getByRole('button',{name:'Đóng chi tiết',exact:true}).click();
  await page.getByRole('button',{name:'Chưa đủ dữ liệu của SO-UNKNOWN',exact:true}).click();
  await expect(page.getByRole('region',{name:'Kiểm tra thiếu hàng của SO-UNKNOWN',exact:true})).toContainText('Chưa có dự báo đầy đủ');
  await expect(page.getByRole('table',{name:'Mặt hàng có thể thiếu của SO-UNKNOWN',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Đóng chi tiết',exact:true}).click();
  await page.getByRole('button',{name:'Ngoài kỳ dự báo của SO-OUTSIDE',exact:true}).click();
  await expect(page.getByRole('region',{name:'Kiểm tra thiếu hàng của SO-OUTSIDE',exact:true})).toContainText('nằm ngoài khoảng thời gian đang dự báo');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('scenario quality shows Chronos historical P50 validation without claiming fitted residual adjustment', async ({ page }) => {
  const state = await mock(page); state.holdout = 1.2;
  await page.goto('/#data'); await apply(page);
  await openPriority(page); await page.getByRole('button', { name: 'Thử bổ sung hàng', exact: true }).click();
  await proposalStep(page,'review');await page.getByRole('button',{name:'So sánh phương án',exact:true}).click();
  await expect(page.getByText(/Sai lệch trung bình khi kiểm tra trên dữ liệu lịch sử: 1,2/)).toBeVisible();
  await expect(page.getByText(/sai lệch kiểm chứng dự báo nền/)).toHaveCount(0);
});

test('handoff details show the warehouse preparation cutoff and never treat missing policy as zero', async ({ page }) => {
  const state = await mock(page);
  await page.goto('/#data'); await apply(page);
  await page.getByRole('button', { name: /Đơn cần phối hợp/ }).click();
  await closeWorkspaceDialogs(page);
  await page.getByRole('navigation',{name:'Đối chiếu đơn trước khi bàn giao'}).getByRole('button',{name:'Đối chiếu nguồn hàng',exact:true}).click();
  await expect(page.getByText('Kho chưa xác nhận thời gian chuẩn bị')).toBeVisible();
  await closeWorkspaceDialogs(page);
  state.prepared = true;
  await page.getByRole('button', { name: 'Tổng quan kho', exact: true }).click();
  await apply(page);
  await page.getByRole('button', { name: /Đơn cần phối hợp/ }).click();
  await page.getByRole('button',{name:'Mở điều kiện SO-001',exact:true}).click();
  await expect(page.getByText(/Cần đủ hàng trước.*07:00.*120 phút/)).toBeVisible();
  await expect(page.getByRole('list',{name:'Lý do cần xử lý SO-001'})).toContainText('Thiếu 10 sản phẩm Cà phê rang (SKU-A)');
  await expect(page.getByRole('dialog',{name:'Điều kiện bàn giao SO-001',exact:true}).getByText('Còn điều kiện chưa đáp ứng')).toHaveCount(0);
});

test('order problems explain a late carrier slot with both timestamps and distinguish unknown capacity', async ({ page }) => {
  await mock(page);
  await page.route('**/planning', route => {
    const result = structuredClone(plan);
    result.orders[0] = { ...result.orders[0], at_risk: false, reason: 'schedule_late',
      shortages_at_deadline: [], schedule_status: 'late', deadline: '2026-10-07T17:00:00+07:00', already_overdue: true,
      shipments: [{ shipment_number: 'SHP-001', carrier_code: 'C1', carrier_name: 'Giao hàng Miền Bắc', scheduled_handoff_at: '2026-10-07T18:00:00+07:00', status: 'ACCEPTED' }] };
    (result.orders[0] as any).readiness = { status: 'not_met', conditions: { supply: 'met', preparation: 'met', schedule: 'not_met', capacity: 'unknown' }, stock_ready_by: '2026-10-07T15:00:00+07:00', preparation_minutes: 120, preparation_policy_revision: 1, actual_handoff_verified: false };
    return route.fulfill({ json: result });
  });
  await page.goto('/#data'); await apply(page);
  await page.getByRole('tab', { name: 'Đơn hàng và bàn giao', exact: true }).click();
  await page.getByRole('navigation',{name:'Đối chiếu đơn trước khi bàn giao'}).getByRole('button',{name:'Đơn đến hạn bàn giao',exact:true}).click();
  await page.getByRole('button',{name:'Mở điều kiện SO-001',exact:true}).click();
  const reasons = page.getByRole('list', { name: 'Lý do cần xử lý SO-001' });
  await expect(reasons).toContainText(/Đã quá hạn bàn giao cho đơn vị vận chuyển\. Hạn bàn giao:.*17:00.*07\/10\/2026\. Giao hàng Miền Bắc dự kiến đến nhận lúc.*18:00.*07\/10\/2026; lịch này muộn hơn hạn yêu cầu\./);
  await expect(reasons).toContainText('Chưa xác minh được đơn vị vận chuyển còn lượt đến lấy hàng');
  await expect(reasons).not.toContainText('Thiếu');
  await expect(page.getByText(/Cần đủ hàng trước.*15:00.*120 phút/)).toBeVisible();
  await page.getByRole('dialog',{name:'Điều kiện bàn giao SO-001',exact:true}).getByRole('button',{name:'Đóng hộp thoại',exact:true}).click();
  const carrierCell = page.getByRole('row').filter({ hasText: 'SO-001' }).getByRole('cell').nth(3);
  await expect(carrierCell).toContainText(/18:00.*07\/10\/2026/);
  await expect(page.getByRole('table',{name:'Đơn và điều kiện bàn giao',exact:true}).getByText('Còn điều kiện chưa đáp ứng')).toBeVisible();
});

test('an unconfirmed carrier time is shown as missing data instead of proving a slot or shortage', async ({ page }) => {
  await mock(page);
  await page.route('**/planning', route => {
    const result = structuredClone(plan);
    result.orders[0] = { ...result.orders[0], at_risk: false, reason: 'scheduled', schedule_status: 'booked', shortages_at_deadline: [],
      shipments: [{ ...result.orders[0].shipments[0], scheduled_handoff_at: null as any }] };
    (result.orders[0] as any).readiness = { status: 'unknown', conditions: { supply: 'met', preparation: 'unknown', schedule: 'unknown', capacity: 'unknown' }, stock_ready_by: null, preparation_minutes: null, preparation_policy_revision: null, actual_handoff_verified: false };
    return route.fulfill({ json: result });
  });
  await page.goto('/#data'); await apply(page);
  await page.getByRole('tab', { name: 'Đơn hàng và bàn giao', exact: true }).click();
  await page.getByRole('navigation',{name:'Đối chiếu đơn trước khi bàn giao'}).getByRole('button',{name:'Đơn đến hạn bàn giao',exact:true}).click();
  await page.getByRole('button',{name:'Mở điều kiện SO-001',exact:true}).click();
  const reasons = page.getByRole('list', { name: 'Lý do cần xử lý SO-001' });
  await expect(reasons).toContainText('Chưa đủ dữ liệu ngày giờ nhận');
  await expect(reasons).toContainText('Kho chưa xác nhận cần bao lâu');
  await expect(reasons).not.toContainText('sau hạn');
  await expect(reasons).not.toContainText('Thiếu');
  await expect(page.getByRole('dialog',{name:'Điều kiện bàn giao SO-001',exact:true}).getByText(/SHP-001 · Chưa xác nhận ngày giờ nhận/)).toBeVisible();
});

test('conditional recovery only fills an exact-hour simulation and never executes business writes', async ({ page }) => {
  await mock(page); const submitted: any[] = [];
  await page.route('**/scenarios', route => {
    const body = route.request().postDataJSON(); submitted.push(body);
    return route.fulfill({ json: { ...scenarioResult(body), recovery_options: [{
      kind:'additional_supply',title:'Bổ sung nguồn hàng trước giờ cần',quantity:30,unit:'EA',
      arrival_at:'2026-10-28T01:00:00+00:00',projected_shortage:0,remaining_at_risk_orders:0,
      can_fill_simulation:true,requires_confirmation:['Kho xác nhận nguồn hàng','Logistics xác nhận lịch'],
      assumptions:['Chưa tạo phiếu nhập hay đơn mua.'],
    }] } });
  });
  await page.goto('/#data'); await apply(page);
  await openPriority(page); await page.getByRole('button', { name: 'Thử bổ sung hàng', exact: true }).click();
  await proposalStep(page,'review');
  await page.getByRole('button', { name: 'So sánh phương án', exact: true }).click();
  await page.getByText('Xem các lựa chọn bổ sung khác',{exact:true}).click();
  await expect(page.getByRole('region', { name: 'Gợi ý phục hồi phương án' })).toBeVisible();
  await page.getByRole('button', { name: 'Điền phương án thử', exact: true }).click();
  await editProposal(page);
  await proposalStep(page,'quantity');
  await expect(page.getByLabel('Số lượng nhập thêm')).toHaveValue('30');
  await proposalStep(page,'arrival');
  await expect(page.getByLabel('Ngày dự kiến hàng về', { exact:true })).toHaveValue('2026-10-28');
  await editProposal(page);
  await expect(page.getByLabel('Giờ dự kiến hàng về (giờ Việt Nam)', { exact:true })).toHaveValue('08:00');
  expect(submitted).toHaveLength(1);
  await closeProposal(page);
  await expect(page.getByRole('button', { name:'Xem kết quả so sánh',exact:true })).toBeDisabled();
  await proposalStep(page,'review');await page.getByRole('button', { name:'So sánh phương án',exact:true }).click();
  expect(submitted[1].arrival_at).toBe('2026-10-28T08:00:00+07:00');
  await closeProposal(page);
  await expect(page.getByRole('button', { name:'Lưu phương án',exact:true })).toBeEnabled();
  const download = page.waitForEvent('download');
  await closeProposal(page);
  await page.getByRole('button', { name:'Tải đề xuất phối hợp',exact:true }).click();
  const file = await (await download).path();
  const { readFile } = await import('node:fs/promises');
  expect(await readFile(file!, 'utf8')).toContain('lúc 08:00 (giờ Việt Nam)');
});

async function editProposal(page: Page) {
  await expect(page.locator('.pw-settings-sheet[data-state=closed]')).toHaveCount(0);
  const dialog=page.locator('.iw-dialog').filter({has:page.locator('[data-slot=dialog-title]').getByText('Thử lượng nhập và ngày hàng về',{exact:true})});
  if(!await dialog.isVisible()){const result=page.getByRole('dialog',{name:'Kết quả so sánh phương án',exact:true});if(await result.isVisible())await result.getByRole('button',{name:'Sửa phương án',exact:true}).click();else await page.getByRole('button',{name:'Thiết lập phương án',exact:true}).click();}
}
async function closeProposal(page:Page){
 await expect(page.locator('.iw-dialog[data-state=closed]')).toHaveCount(0);
 const dialog=page.locator('.iw-dialog').filter({has:page.locator('[data-slot=dialog-title]').getByText('Thử lượng nhập và ngày hàng về',{exact:true})});
 if(await dialog.isVisible()){await dialog.getByRole('button',{name:'Đóng hộp thoại',exact:true}).click();await expect(dialog).toHaveCount(0);}
}

test('interactive warehouse diagram opens scoped dialogs and restores context without new reads',async({page})=>{
  await mock(page);let reads=0;
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  const item={organization_id:'test-org',warehouse_id:warehouse,warehouse_name:'Kho Hà Nội',warehouse_code:'HN',products:plan.products,open_orders:[{order_id:'order-1',order_number:'SO-001',deadline:'2026-10-28T17:00:00+07:00',status:'CONFIRMED',lines:[{sku:'SKU-A',uom:'EA',ordered_quantity:30}],supply_check:{status:'risk',shortages:[{sku:'SKU-A',uom:'EA',missing_quantity:10}],notes:[]}}],planning_status:'calculated',planning_issue:null,plan_read_at:evidence.executed_at};
  await page.route('**/all-warehouses',r=>{reads++;return r.fulfill({json:{items:[item],warehouse_count:1,horizon:7,read_at:evidence.executed_at}});});
  await page.goto('/#data');
  const flow=page.getByRole('navigation',{name:'Liên thông Bán hàng, Kho và Vận chuyển'});
  await flow.getByRole('button',{name:'Kho',exact:true}).click();
  const stock=page.getByRole('dialog',{name:'Nguồn hàng trong Kho · Kho Hà Nội',exact:true});
  await expect(stock).toContainText('Cà phê rang');
  await page.screenshot({path:'../.tmp/decision-demo/interactive-overview-dialog.png',animations:'disabled'});
  await page.keyboard.press('Escape');await expect(stock).toHaveCount(0);
  await expect(flow.getByRole('button',{name:'Kho',exact:true})).toBeFocused();
  await flow.getByRole('button',{name:'Bán hàng',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:/SO-001 ·/}).click();
  await expect(page.getByRole('dialog')).toContainText('30 sản phẩm');
  await page.getByRole('button',{name:'Đóng chi tiết',exact:true}).click();
  expect(reads).toBe(1);
  await page.getByRole('dialog',{name:'Toàn bộ đơn đang mở · Kho Hà Nội',exact:true}).getByRole('button',{name:'Đóng hộp thoại',exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  await flow.getByRole('button',{name:'Kho',exact:true}).click();
  expect(await page.getByRole('dialog').evaluate(el=>el.getBoundingClientRect().right<=innerWidth)).toBe(true);
  await page.screenshot({path:'../.tmp/decision-demo/interactive-overview-mobile.png',animations:'disabled'});
});

test('interactive proposal preserves a draft on closing and closes stale dialogs before another action',async({page})=>{
  const state=await mock(page);await page.goto('/#data');await apply(page);
  await openPriority(page);await page.getByRole('button',{name:'Thử bổ sung hàng',exact:true}).click();
  await proposalStep(page,'quantity');
  await editProposal(page);await page.getByLabel('Số lượng nhập thêm',{exact:true}).fill('55');
  await page.screenshot({path:'../.tmp/decision-demo/interactive-proposal-dialog.png',animations:'disabled'});
  await closeProposal(page);await expect(page.getByRole('button',{name:'Thiết lập phương án',exact:true})).toBeVisible();
  expect(count(state,'/scenarios')).toBe(0);
  await proposalStep(page,'quantity');
  await editProposal(page);await expect(page.getByLabel('Số lượng nhập thêm',{exact:true})).toHaveValue('55');
  state.revision='d';
  await expect(page.getByRole('alert',{name:'Thông báo thay đổi dữ liệu'})).toContainText('Kho vừa cập nhật',{timeout:20000});
  await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.locator('[inert]')).toHaveCount(1);
  expect(count(state,'/scenarios')).toBe(0);
  await page.getByRole('button',{name:'Cập nhật dữ liệu ngay',exact:true}).click();
  await proposalStep(page,'quantity');await expect(page.getByLabel('Số lượng nhập thêm',{exact:true})).toHaveValue('55');
});

test('interactive analytics and IT steps open focused dialogs without implicit calculations or saves', async ({page})=>{
  const state=await mock(page,true);await page.goto('/#data');
  await page.getByRole('button',{name:'Dự báo nhu cầu',exact:true}).click();
  await expect(page.locator('.dw-content input:visible')).toHaveCount(0);
  expect(count(state,'/overview')).toBe(0);expect(count(state,'/forecasts')).toBe(0);
  await beginForecast(page);
  await expect(page.getByLabel('Ngày bắt đầu dự báo',{exact:true})).toBeVisible();
  expect(count(state,'/forecasts')).toBe(0);
  await closeWorkspaceDialogs(page);
  await openIT(page);
  const wizard=page.getByRole('dialog',{name:'Thiết lập cấu hình phân tích',exact:true});
  await expect(wizard.getByRole('button',{name:'Ghép các cột dữ liệu',exact:true})).toBeDisabled();
  await expect(wizard.getByRole('button',{name:'Xem lại và lưu',exact:true})).toBeDisabled();
  await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  await expect(page.getByLabel('Cột thời gian',{exact:true})).toBeVisible();
  await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  expect(count(state,'/preview')).toBe(0);
  await wizard.getByRole('button',{name:'Kiểm tra & xem trước',exact:true}).click();
  await expect(wizard.getByText('Kiểm tra thành công.',{exact:false})).toBeVisible();
  await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  await expect(wizard.getByRole('button',{name:'Lưu cấu hình',exact:true})).toBeEnabled();
  expect(count(state,'/configurations')).toBe(0);
  await page.screenshot({path:'../.tmp/ui-dialog-redesign/it-review-fixture.png',animations:'disabled'});
});

test('interactive dossier progress opens only recorded evidence and keeps missing steps explicit',async({page})=>{
  const state=await mock(page);
  const scenario:any=scenarioResult({demand_multiplier:1,inbound_delay_days:0,additional_quantity:30,arrival_date:'2026-10-25'});
  scenario.baseline.orders=[{order_id:'order-1',order_number:'SO-001',lines:[]}];
  const dossier={id:'case-1',title:'Phối hợp SO-001',warehouse_id:warehouse,order_id:'order-1',status:'APPROVED',version:4,current_revision:1,revision:{id:'revision-1',number:1,created_at:evidence.executed_at,payload:{scenario,requires_revalidation:true}},participants:[],actions:{can_revise:false,can_submit:false}};
  await page.route('**/decision-capabilities',r=>r.fulfill({json:{enabled:true,roles:['warehouse'],warehouse_ids:[warehouse],workflow_enabled:true}}));
  await page.route('**/decision-tasks',r=>r.fulfill({json:{items:[],has_more:false,warnings:[]}}));
  await page.route('**/decisions**',r=>{
    const path=new URL(r.request().url()).pathname;
    expect(r.request().method()).toBe('GET');
    if(path.endsWith('/workflow'))return r.fulfill({json:{case:dossier,workflow:null,warnings:[]}});
    if(path.endsWith('/revisions'))return r.fulfill({json:{items:[dossier.revision],has_more:false}});
    if(path.endsWith('/timeline'))return r.fulfill({json:{items:[{id:'event-1',event_type:'SOURCE_OBSERVED',created_at:evidence.executed_at,payload:{event_type:'warehouse.order_reserved',producer:'warehouse',correlation_id:'order-1',occurred_at:evidence.executed_at}}],has_more:false}});
    if(path.endsWith('/case-1'))return r.fulfill({json:dossier});
    return r.fulfill({json:{items:[dossier],has_more:false,warnings:[]}});
  });
  await page.goto('/#data');await page.getByRole('button',{name:'Phối hợp và bàn giao',exact:true}).click();await page.getByRole('tab',{name:'Hồ sơ phối hợp',exact:true}).click();
  await openDossierList(page);
  await page.getByRole('button',{name:'Mở hồ sơ Phối hợp SO-001',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Phối hợp SO-001',exact:true});
  await expect(dialog).toBeVisible();await dialog.getByRole('button',{name:'Theo dõi bàn giao',exact:true}).click();
  const flow=dialog.getByRole('navigation',{name:'Sơ đồ bằng chứng bàn giao'});
  await expect(flow.getByRole('button',{name:'Vận chuyển xác nhận lịch nhận',exact:true})).toContainText('Chưa có bản ghi');
  await page.screenshot({path:'../.tmp/decision-demo/interactive-dossier-diagram.png',animations:'disabled'});
  await flow.getByRole('button',{name:'Kho ghi nhận giữ hàng',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'Kho ghi nhận giữ hàng',exact:true})).toContainText('Nguồn: Kho');
  expect(state.calls.filter(c=>c.path.endsWith('/scenarios')||c.path.endsWith('/forecasts'))).toHaveLength(0);
});

test('IT wizard discards a preview returned after its configuration was edited', async ({page})=>{
  const state=await mock(page,true);let release=()=>{};let started=false;
  const waiting=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/preview',async r=>{started=true;await waiting;await r.fulfill({json:{quality:{},preview_before:[{sku:'old'}],preview_after:[]}});});
  await page.goto('/#data');await openIT(page);
  const wizard=page.getByRole('dialog',{name:'Thiết lập cấu hình phân tích',exact:true});
  await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  await wizard.getByRole('button',{name:'Kiểm tra & xem trước',exact:true}).click();
  await expect.poll(()=>started).toBe(true);
  await expect(page.getByLabel('Đơn vị',{exact:true})).toBeDisabled();
  await expect(wizard.getByRole('button',{name:'Chọn nguồn và thời gian',exact:true})).toBeDisabled();
  release();await expect(wizard.getByText('Kiểm tra thành công.',{exact:false})).toBeVisible();
  await wizard.getByRole('button',{name:'Chọn nguồn và thời gian',exact:true}).click();
  await page.getByLabel('Tên cấu hình',{exact:true}).fill('Cấu hình đã sửa');
  await expect(wizard.getByRole('button',{name:'Xem lại và lưu',exact:true})).toBeDisabled();
  await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  await expect(wizard.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();
  expect(count(state,'/configurations')).toBe(0);
});
async function scopeInputs(page:Page){
 const dialog=page.getByRole('dialog',{name:'Chọn kho và phạm vi',exact:true});
 if(!await dialog.isVisible()){await closeWorkspaceDialogs(page);await page.getByRole('button',{name:'Chọn kho và phạm vi',exact:true}).click();}
 const back=dialog.getByRole('button',{name:'Bước trước',exact:true});if(await back.isVisible())await back.click();
}
async function refreshPlan(page:Page){
 await scopeInputs(page);
 const dialog=page.getByRole('dialog',{name:'Chọn kho và phạm vi',exact:true});
 await dialog.getByRole('button',{name:'Tiếp tục',exact:true}).click();
 await dialog.getByRole('button',{name:/^(Tải dữ liệu và tính lại|Tính lại dự báo)$/}).click();
}
async function apply(page:Page){
 if(!await page.getByRole('button',{name:'Chọn kho và phạm vi',exact:true}).isVisible()){await closeWorkspaceDialogs(page);await page.getByRole('button',{name:'Tổng quan kho',exact:true}).click();}
 await refreshPlan(page);await expect(page.locator('.pw-applied-context')).toBeVisible();
}
async function openPriority(page:Page){
 if(!await page.getByRole('dialog',{name:'Mặt hàng và đơn cần chú ý',exact:true}).isVisible())await page.getByRole('button',{name:'Mặt hàng cần chú ý',exact:true}).click();
}
async function openDossierList(page:Page){
 if(await page.getByRole('dialog',{name:/^(Danh sách hồ sơ phối hợp|Việc đến lượt bạn|Tiến độ và kết quả hồ sơ)$/}).isVisible())return;
 await page.getByRole('button',{name:/^(Mở danh sách hồ sơ|Mở việc đến lượt bạn|Xem tiến độ hồ sơ)$/}).click();
}
async function proposalStep(page:Page,step:'quantity'|'arrival'|'assumptions'|'review'){
 await editProposal(page);
 const dialog=page.getByRole('dialog',{name:'Thử lượng nhập và ngày hàng về',exact:true});
 const titles={quantity:'Nhập số lượng',arrival:'Chọn thời điểm hàng về',assumptions:'Chọn giả định nhu cầu',review:'Xác nhận phương án'};
 const target=Object.keys(titles).indexOf(step);
 const heading=dialog.locator('.gw-step>h3');
 let current=Number((await heading.innerText()).match(/Bước (\d)/)![1])-1;
 while(current>target){await dialog.getByRole('button',{name:'Bước trước',exact:true}).click();current--;}
 while(current<target){await dialog.getByRole('button',{name:'Tiếp tục',exact:true}).click();current++;}
}


test('compact overview shows two governed business charts without the removed noise', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data'); await apply(page);
  await expect(page.getByText('PHỐI HỢP ĐỂ RA QUYẾT ĐỊNH', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Lượng khách đặt trong kỳ', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Bình quân mỗi ngày', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Phân bố lượng đặt theo ngày' })).toHaveCount(0);
  await page.getByRole('button',{name:'Biểu đồ đối chiếu',exact:true}).click();
  await expect(page.getByRole('heading', { name: 'Lượng đặt và thực nhập', exact: true })).toBeVisible();
  const risk = page.getByRole('img', { name: /Nguy cơ thiếu hàng theo sản phẩm. Trục X: sản phẩm/ });
  await expect(risk).toBeVisible(); await expect(risk).toContainText('Thiếu cho đơn đã nhận');
  await expect(risk).toContainText('Thiếu tăng thêm khi xét dự báo');
  await expect(page.getByRole('heading', { name: 'Dự báo nhu cầu và kế hoạch chuẩn bị' })).toHaveCount(0);
  await scopeInputs(page);
  await page.getByLabel('Gộp số liệu theo', { exact: true }).selectOption('week');
  expect(count(s, '/planning')).toBe(1); await apply(page);
  expect(s.calls.filter(c => c.path.endsWith('/planning')).at(-1)!.body.history_frequency).toBe('week');
  await page.getByRole('button',{name:'Biểu đồ đối chiếu',exact:true}).click();
  await expect(page.getByRole('img', { name: /Lượng đặt xác nhận và thực nhập theo tuần/ })).toBeVisible();
  expect(await page.getByRole('heading').allTextContents()).not.toEqual(expect.arrayContaining([expect.stringMatching(/\?$/)]));
  await page.mouse.move(20, 20); await page.screenshot({ animations: 'disabled', path: 'ref/qa/compact-dashboard.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/compact-dashboard-mobile.png', fullPage: true });
});

test('sensitivity grid selects quantity and date without silently calculating or saving', async ({page})=>{
  const state=await mock(page);await page.goto('/#data');await apply(page);await openPriority(page);
  await page.getByRole('button',{name:'Thử bổ sung hàng',exact:true}).click();await proposalStep(page,'review');await page.getByRole('button',{name:'So sánh phương án',exact:true}).click();
  await page.getByRole('button',{name:'Chọn lượng và ngày nhập',exact:true}).click();
  const grid=page.getByRole('table',{name:'Bảng thử lượng nhập và ngày hàng về',exact:true});await expect(grid).toBeVisible();
  const option=grid.getByRole('button').last();await option.click();
  await expect(page.getByRole('dialog',{name:'Thử lượng nhập và ngày hàng về',exact:true})).toBeVisible();
  expect(count(state,'/scenarios')).toBe(1);
  await closeWorkspaceDialogs(page);await expect(page.getByRole('button',{name:'Xem kết quả so sánh',exact:true})).toBeDisabled();
  await proposalStep(page,'review');await page.getByRole('button',{name:'So sánh phương án',exact:true}).click();expect(count(state,'/scenarios')).toBe(2);
});

test('history remains independent of planning sources and keeps details out of the overview', async ({page})=>{
  const state=await mock(page);state.missing=true;await page.goto('/#data');
  await page.getByRole('button',{name:'Dự báo nhu cầu',exact:true}).click();await page.getByRole('button',{name:'Phân tích lịch sử',exact:true}).click();
  await page.getByRole('button',{name:'Chọn dữ liệu phân tích',exact:true}).click();
  await page.getByRole('button',{name:'Cập nhật biểu đồ',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button',{name:'Xu hướng theo thời gian',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Diễn biến lượng đặt hàng',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Nhịp trong tuần',exact:true}).last().click();
  await expect(page.getByRole('heading',{name:'Lượng đặt hàng theo thứ trong tuần',exact:true})).toBeVisible();
  expect(count(state,'/planning')).toBe(0);expect(count(state,'/forecasts')).toBe(0);
  await closeWorkspaceDialogs(page);
  await expect(page.locator('.dw-content table:visible')).toHaveCount(0);
});

test('dedicated forecast uses only ML and keeps stale settings from triggering a decision', async ({page})=>{
  const state=await mock(page);await page.goto('/#data');await beginForecast(page);
  const wizard=page.getByRole('dialog',{name:'Tạo dự báo nhu cầu',exact:true});
  await expect(page.getByLabel('Dữ liệu tham chiếu',{exact:true})).toHaveValue('180');
  await expect(wizard.getByRole('button',{name:'Xác nhận và tính',exact:true})).toBeDisabled();
  await page.getByLabel('Ngày bắt đầu dự báo',{exact:true}).fill('2026-10-27');await page.getByLabel('Ngày kết thúc dự báo',{exact:true}).fill('2026-10-26');
  await expect(wizard.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();
  await page.getByLabel('Ngày kết thúc dự báo',{exact:true}).fill('2026-11-03');
  expect(count(state,'/forecasts')).toBe(0);await finishForecast(page);
  const body=state.calls.find(c=>c.path.endsWith('/forecasts'))!.body;
  expect(body.config.forecast_model).toBe('chronos2');expect(body.allow_statistical_fallback).toBe(false);
  expect(body.start_date).toBe('2026-10-27');expect(body.end_date).toBe('2026-11-03');
  await expect(page.getByRole('heading',{name:'Lượng đặt dự báo',exact:true})).toBeVisible();
  await closeWorkspaceDialogs(page);await page.getByRole('button',{name:'Tạo dự báo',exact:true}).click();
  await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  await page.getByLabel('Ngày kết thúc dự báo',{exact:true}).fill('2026-11-04');
  await closeWorkspaceDialogs(page);
  await expect(page.getByRole('button',{name:'Xem kết quả dự báo',exact:true})).toBeDisabled();
  expect(count(state,'/forecasts')).toBe(1);
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'../.tmp/ui-dialog-redesign/forecast-home-fixture-mobile.png',animations:'disabled'});
});

test('ML history errors are readable and never silently fall back', async ({page})=>{
  const state=await mock(page);await page.goto('/#data');
  await page.route('**/forecasts',r=>r.fulfill({status:422,json:{detail:{code:'ML_HISTORY_SHORT',message:'Chưa đủ lịch sử để kiểm chứng mô hình.'}}}));
  await beginForecast(page);await finishForecast(page,false);
  await expect(page.getByRole('dialog',{name:'Tạo dự báo nhu cầu',exact:true}).getByRole('alert')).toContainText('Chưa đủ lịch sử');
  expect(count(state,'/planning')).toBe(0);
});

test('transport charts keep shipment units and saved forecasts remain viewable', async ({page})=>{
  await mock(page);await page.goto('/#data');await beginForecast(page,true);await finishForecast(page);
  await expect(page.getByRole('heading',{name:'Lượt tiếp nhận dự báo',exact:true})).toBeVisible();
  await expect(page.getByRole('dialog',{name:'Kết quả dự báo nhu cầu',exact:true})).toContainText('lượt');
  await closeWorkspaceDialogs(page);await page.getByRole('button',{name:'Kết quả đã lưu',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Dự báo và phương án đã lưu',exact:true})).toBeVisible();
});

test('weekly decisions use explicit Apply; search by product name or code keeps applied context',async({page})=>{
  const state=await mock(page);await page.goto('/#data');await apply(page);
  await scopeInputs(page);await page.getByLabel('Gộp số liệu theo',{exact:true}).selectOption('week');
  expect(count(state,'/planning')).toBe(1);await refreshPlan(page);await expect(page.locator('.pw-applied-context')).toBeVisible();
  expect(state.calls.filter(c=>c.path.endsWith('/planning')).at(-1)!.body.history_frequency).toBe('week');
  await scopeInputs(page);await page.getByLabel('Sản phẩm',{exact:true}).selectOption('SKU-B');
  expect(count(state,'/planning')).toBe(2);await refreshPlan(page);await expect(page.locator('.pw-applied-context')).toBeVisible();
  expect(state.calls.filter(c=>c.path.endsWith('/planning')).at(-1)!.body.sku).toBe('SKU-B');
  await page.getByRole('button',{name:'Biểu đồ đối chiếu',exact:true}).click();await page.getByRole('button',{name:'Nguồn & cách tính',exact:true}).first().click();
  const evidence=page.getByRole('dialog').last();await expect(evidence).toContainText('Hàng đầu kỳ + hàng về đúng thời điểm');await expect(evidence).not.toContainText('Chi tiết kỹ thuật');
});

test('a forecast leads to a tested proposal; editing never recalculates or exports stale results',async({page})=>{
  const state=await mock(page);await page.goto('/#data');await apply(page);await openPriority(page);
  await page.getByRole('button',{name:'Thử bổ sung hàng',exact:true}).click();await page.getByRole('button',{name:'Xem dữ liệu đầu vào',exact:true}).click();
  await expect(page.getByRole('img',{name:/Biểu đồ Vùng dao động, Lượng đặt dự kiến mỗi ngày/})).toBeVisible();await closeWorkspaceDialogs(page);
  await proposalStep(page,'quantity');await page.getByLabel('Số lượng nhập thêm',{exact:true}).fill('50');
  await proposalStep(page,'arrival');await page.getByLabel('Ngày dự kiến hàng về',{exact:true}).fill('2026-10-26');
  expect(count(state,'/scenarios')).toBe(0);await proposalStep(page,'review');await page.getByRole('button',{name:'So sánh phương án',exact:true}).click();
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Tải đề xuất phối hợp',exact:true}).click();await download;
  expect(count(state,'/scenarios')).toBe(1);await proposalStep(page,'quantity');await page.getByLabel('Số lượng nhập thêm',{exact:true}).fill('60');
  await closeWorkspaceDialogs(page);await expect(page.getByRole('button',{name:'Xem kết quả so sánh',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'Lập hồ sơ phối hợp',exact:true})).toBeDisabled();
  expect(count(state,'/scenarios')).toBe(1);await proposalStep(page,'quantity');await page.getByLabel('Số lượng nhập thêm',{exact:true}).fill('');await expect(page.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();
});

test('leadership KPI navigates to order deadlines and named carrier calendar', async ({ page }) => {
  const s = await mock(page); await page.goto('/#data'); await apply(page);
  await page.getByRole('button', { name: /Đơn cần phối hợp/ }).click();
  await expect(page.getByRole('dialog',{name:'Đơn đến hạn bàn giao',exact:true})).toBeVisible();
  await expect(page.getByRole('table',{name:'Đơn và điều kiện bàn giao',exact:true})).toContainText('SO-001');
  await expect(page.getByText('SO-001', { exact: true })).toBeVisible();
  await page.getByRole('button',{name:'Mở điều kiện SO-001',exact:true}).click();
  await expect(page.getByRole('list',{name:'Lý do cần xử lý SO-001'})).toContainText('Thiếu 10 sản phẩm');
  await closeWorkspaceDialogs(page);
  await page.getByRole('navigation',{name:'Đối chiếu đơn trước khi bàn giao'}).getByRole('button',{name:'Kiểm tra lịch lấy hàng',exact:true}).click();
  await expect(page.getByRole('heading', { name: 'Lịch tiếp nhận của nhà vận chuyển' })).toBeVisible();
  await expect(page.getByRole('cell',{name:/Giao hàng Miền Bắc/}).first()).toBeVisible(); expect(count(s, '/planning')).toBe(1);
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/decision-orders.png', fullPage: true });
});

test('missing optional receipts never become zero or a misleading chart legend', async ({ page }) => {
  const s = await mock(page); s.optionalMissing = true; await page.goto('/#data'); await apply(page);
  await page.getByRole('button',{name:'Biểu đồ đối chiếu',exact:true}).click();
  const chart = page.getByRole('img', { name: /Lượng đặt xác nhận và thực nhập/ });
  await expect(chart).not.toContainText('Thực nhập kho'); await expect(page.locator('.pw-data-warning')).toContainText('không tính là 0');
});

test('missing required per-warehouse demand explains the setup and does not call facts', async ({ page }) => {
  const s = await mock(page); s.missing = true; await page.goto('/#data'); await page.getByRole('button', { name: 'Tổng quan kho', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Lượng đặt hàng theo kho');
  await scopeInputs(page);await expect(page.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();expect(count(s,'/planning')).toBe(0);
});

test('IT mapping remains functional and technical evidence is limited to IT',async({page})=>{
  const state=await mock(page,true);await page.goto('/#data');await openIT(page);
  const wizard=page.getByRole('dialog',{name:'Thiết lập cấu hình phân tích',exact:true});
  await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  await page.getByLabel('Cột thời gian',{exact:true}).selectOption('day');await page.getByLabel('Cột đối tượng',{exact:true}).selectOption('sku');await page.getByLabel('Cột đo lường',{exact:true}).selectOption('confirmed_quantity');
  await page.getByLabel('Cột lọc',{exact:true}).selectOption('sku');await expect(wizard.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();await page.getByLabel('Giá trị lọc',{exact:true}).fill('SKU-A');
  await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await wizard.getByRole('button',{name:'Kiểm tra & xem trước',exact:true}).click();await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  expect(state.calls.find(c=>c.path.endsWith('/preview'))!.body.filters).toEqual({sku:'SKU-A'});
  await wizard.getByRole('button',{name:'Mở đầy đủ các bước xử lý',exact:true}).click();await expect(page.getByRole('dialog').last()).toContainText('Chi tiết kỹ thuật');await page.keyboard.press('Escape');
  await wizard.getByRole('button',{name:'Lưu cấu hình',exact:true}).click();await expect(wizard).toHaveCount(0);expect(count(state,'/configurations')).toBe(1);
});

for (const status of [401, 403, 503, 422]) test(`source failure ${status} clears old facts and never invents decisions`, async ({ page }) => {
  const s = await mock(page); await page.goto('/#data'); await apply(page); s.status = status;
  await refreshPlan(page);
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
  await page.goto('/#data'); await page.getByRole('button', { name: 'Tổng quan kho', exact: true }).click();
  await refreshPlan(page);
  await expect(page.getByRole('alert')).toHaveText(message);
  await expect(page.locator('.dw-progress')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Tải dữ liệu và tính lại', exact: true })).toBeEnabled();
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
  await page.goto('/#data'); await page.getByRole('button', { name: 'Tổng quan kho', exact: true }).click();
  await refreshPlan(page);
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
  await scopeInputs(page);
  const picker = page.getByRole('combobox', { name: 'Kho phân tích', exact: true });
  await expect(picker).toBeEnabled(); await expect(picker).toHaveValue('');
  await expect(picker.locator('option')).toHaveText(['Chọn kho', 'Kho Hà Nội (HN)', 'Kho Hồ Chí Minh (HCM)']);
  await expect(page.getByText('Kho đầu tiên được chia sẻ', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();
  const empty = page.locator('.pw-empty-state'); await expect(empty).toBeVisible();
  await expect(empty.locator('svg')).toHaveCount(0); expect((await empty.boundingBox())!.height).toBeLessThan(180);
  await expect(page.locator('.dw-topbar')).toHaveCount(0);
  await picker.selectOption(second);
  await expect(page.getByRole('button',{name:'Tiếp tục',exact:true})).toBeEnabled();
  await expect(page.getByRole('combobox', { name: 'Sản phẩm', exact: true })).toHaveValue('M08');
  await expect(page.getByRole('combobox', { name: 'Sản phẩm', exact: true }).locator('option')).toHaveText(['M08 - Bút bi']);
  expect(count(s, '/planning')).toBe(0);
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/warehouse-choice.png', fullPage: true });
});

test('chart options float above the page and assumptions only apply on confirmation',async({page})=>{
  const state=await mock(page);await page.goto('/#data');
  await page.getByRole('button',{name:'Dự báo nhu cầu',exact:true}).click();await page.getByRole('button',{name:'Phân tích lịch sử',exact:true}).click();await page.getByRole('button',{name:'Chọn dữ liệu phân tích',exact:true}).click();await page.getByRole('button',{name:'Cập nhật biểu đồ',exact:true}).click();await page.getByRole('button',{name:'Xu hướng theo thời gian',exact:true}).click();
  const height=await page.locator('.dw-main').evaluate(e=>e.scrollHeight);await page.getByRole('button',{name:'Tùy chọn biểu đồ',exact:true}).click();await expect(page.getByRole('menu')).toBeVisible();expect(await page.locator('.dw-main').evaluate(e=>e.scrollHeight)).toBe(height);await page.keyboard.press('Escape');await closeWorkspaceDialogs(page);
  await page.getByRole('button',{name:'Tổng quan kho',exact:true}).click();await apply(page);await openPriority(page);await page.getByRole('button',{name:'Thử bổ sung hàng',exact:true}).click();await proposalStep(page,'assumptions');await page.getByLabel('Giả sử lượng khách đặt tăng/giảm (%)',{exact:true}).fill('20');
  expect(count(state,'/scenarios')).toBe(0);await closeWorkspaceDialogs(page);await proposalStep(page,'assumptions');await expect(page.getByLabel('Giả sử lượng khách đặt tăng/giảm (%)',{exact:true})).toHaveValue('20');expect(count(state,'/scenarios')).toBe(0);
});


test('ML scenario separates the shock, shows a quantity/date response, and compares real decision measures',async({page})=>{
  const state=await mock(page);await page.goto('/#data');await apply(page);await openPriority(page);await page.getByRole('button',{name:'Thử bổ sung hàng',exact:true}).click();
  await proposalStep(page,'quantity');await page.getByLabel('Số lượng nhập thêm',{exact:true}).fill('50');await proposalStep(page,'arrival');await page.getByLabel('Ngày dự kiến hàng về',{exact:true}).fill('2026-10-26');await proposalStep(page,'assumptions');await page.getByLabel('Giả sử lượng khách đặt tăng/giảm (%)',{exact:true}).fill('20');await page.getByLabel('Hàng đang chờ về trễ thêm (ngày)',{exact:true}).fill('2');await proposalStep(page,'review');await page.getByRole('button',{name:'So sánh phương án',exact:true}).click();
  const body=state.calls.filter(c=>c.path.endsWith('/scenarios')).at(-1)!.body;expect(body.require_ml).toBe(true);expect(body.demand_multiplier).toBe(1.2);expect(body.inbound_delay_days).toBe(2);
  await page.getByRole('tab',{name:'Chỉ tiêu so sánh',exact:true}).click();const table=page.getByRole('table',{name:'Đối chiếu ba kịch bản',exact:true});await expect(table.getByRole('row').filter({hasText:'Lượng khách có thể đặt thêm'}).getByRole('cell')).toHaveText(['Lượng khách có thể đặt thêmsản phẩm','70','84','84']);
  await page.getByRole('button',{name:'Điền mức bổ sung',exact:true}).click();await expect(page.getByLabel('Số lượng nhập thêm',{exact:true})).toHaveValue('79');expect(count(state,'/scenarios')).toBe(1);await proposalStep(page,'review');await page.getByRole('button',{name:'So sánh phương án',exact:true}).click();await expect(page.locator('.pw-scenario-verdict')).toContainText('Đủ hàng theo các giả định đã chọn');
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'../.tmp/ui-dialog-redesign/scenario-result-fixture-mobile.png',animations:'disabled'});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(await page.locator('.iw-dialog-body > .dw-actions button').evaluateAll(buttons=>buttons.every(b=>b.getBoundingClientRect().right<=innerWidth&&b.getBoundingClientRect().left>=0))).toBe(true);
});

test('an already sufficient ML plan exposes extra ending inventory instead of celebrating zero to zero', async ({ page }) => {
  await mock(page); await page.goto('/#data'); await apply(page);
  await openPriority(page); await page.getByRole('button', { name: 'Thử bổ sung hàng', exact: true }).click();
  await page.route('**/api/v1/data-workspace/scenarios', route => route.fulfill({ json: scenarioResult(route.request().postDataJSON(), 200) }));
  await editProposal(page);
  await proposalStep(page,'quantity');
  await page.getByLabel('Số lượng nhập thêm', { exact: true }).fill('20');
  await editProposal(page);
  await proposalStep(page,'arrival');
  await page.getByLabel('Ngày dự kiến hàng về', { exact: true }).fill('2026-10-25');
  await proposalStep(page,'review');
  await page.getByRole('button', { name: 'So sánh phương án', exact: true }).click();
  await closeProposal(page);
  await expect(page.locator('.pw-scenario-verdict')).toContainText('chưa giảm rủi ro');
  await closeProposal(page);
  await expect(page.locator('.pw-scenario-verdict')).toContainText('tăng 20 sản phẩm');
  await expect(page.getByText('Mức bổ sung để hết thiếu trong kỳ: 0', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Điền mức bổ sung', exact: true })).toHaveCount(0);
  await page.getByRole('heading', { name: 'So sánh ba phương án đủ hàng', exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ animations: 'disabled', path: 'ref/qa/ml-scenario-extra-inventory.png', fullPage: true });
});


test('dossier impact report explains harmless and harmful changes without auto approval', async ({ page }) => {
  await mock(page);
  const scenario: any = scenarioResult({ demand_multiplier: 1, inbound_delay_days: 0, additional_quantity: 30, arrival_date: '2026-10-25' });
  scenario.baseline.orders = [{ order_id: 'order-1', order_number: 'SO-001', lines: [] }];
  const dossier = { id: 'case-1', title: 'Đối soát điều kiện SO-001', warehouse_id: warehouse, order_id: 'order-1', status: 'PENDING_APPROVAL', version: 4, current_revision: 1,
    revision: { id: 'revision-1', number: 1, created_at: evidence.executed_at, payload: { scenario, requires_revalidation: true } }, participants: [1,2,3,4], actions: { can_submit: false, can_revise: false } };
  let count = 0;
  const checks: any[] = [];
  await page.route('**/decision-capabilities', r => r.fulfill({ json: { enabled: true, roles: ['manager'], warehouse_ids: [warehouse], workflow_enabled: true } }));
  await page.route('**/decisions**', r => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/revalidate')) {
      count++;
      const impact = count === 1 ? { classification: 'NO_EFFECT', requires_revision: false, diff: [] } : { classification: 'REQUIRES_REVISION', requires_revision: true, diff: [{ path: 'preparation_minutes', before: 60, after: 120 }] };
      checks.unshift({ id: 'check-' + count, created_at: evidence.executed_at, outcome: count === 1 ? 'VALID' : 'STALE', payload: { impact } });
      return r.fulfill({ status: count === 1 ? 200 : 409, json: count === 1 ? { impact } : { detail: { code: 'DECISION_REQUIRES_REVISION', message: 'Source conditions changed', impact } } });
    }
    if (path.endsWith('/checks')) return r.fulfill({ json: { items: checks, has_more: false } });
    if (path.endsWith('/workflow')) return r.fulfill({ json: { case: dossier, workflow: { instance_id: 'i-1', revision_id: 'revision-1', status: 'PENDING_APPROVAL', tasks: [] }, warnings: [] } });
    if (path.endsWith('/revisions')) return r.fulfill({ json: { items: [dossier.revision], has_more: false } });
    if (path.endsWith('/timeline')) return r.fulfill({ json: { items: [], has_more: false } });
    if (path.endsWith('/case-1')) return r.fulfill({ json: dossier });
    return r.fulfill({ json: { items: [dossier], warnings: [], has_more: false } });
  });
  await page.goto('/#data'); await page.getByRole('button',{name:'Phối hợp và bàn giao',exact:true}).click();await page.getByRole('tab',{name:'Hồ sơ phối hợp',exact:true}).click();
  await openDossierList(page);
  await page.getByRole('button', { name: 'Mở hồ sơ Đối soát điều kiện SO-001', exact: true }).click();
  await page.getByRole('tab',{name:'Kiểm tra dữ liệu',exact:true}).click();
  await page.getByRole('button', { name: 'Kiểm tra dữ liệu hiện tại', exact: true }).click();
  await expect(page.getByText(/Có thay đổi nhưng điều kiện và nội dung phương án giữ nguyên/)).toBeVisible();
  await page.getByRole('tab',{name:'Kiểm tra dữ liệu',exact:true}).click();
  await page.getByRole('button', { name: 'Kiểm tra dữ liệu hiện tại', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Điều kiện hoặc nội dung phương án đã đổi');
  await page.getByText(/Cần tính và xác minh lại/, { exact: false }).click();
  await expect(page.getByText('Trước: 60 phút.', { exact: true })).toBeVisible();
  await expect(page.getByText('Sau: 120 phút.', { exact: true })).toBeVisible();
  expect(count).toBe(2);
});


test('my tasks opens the assigned revision and assistant reuses the canonical chart renderer', async ({ page }) => {
  await mock(page);
  const scenario: any = scenarioResult({ demand_multiplier: 1, inbound_delay_days: 0, additional_quantity: 30, arrival_date: '2026-10-25' });
  scenario.baseline.orders = [{ order_id: 'order-1', order_number: 'SO-001', lines: [] }];
  const task = { id: 'task-1', name: 'Kho xác nhận điều kiện', task_key: 'verifyWarehouse', role: 'warehouse', mine: true };
  const dossier = { id: 'case-1', title: 'Giữ đúng phiên bản SO-001', warehouse_id: warehouse, order_id: 'order-1', status: 'VALIDATING', version: 3, current_revision: 1, workflow_instance_ref: 'i-1',
    revision: { id: 'revision-1', number: 1, created_at: evidence.executed_at, payload: { scenario, requires_revalidation: true } }, participants: [1,2,3,4], actions: { can_submit: false, can_revise: false } };
  const writes: string[] = [];
  let revoked = false;
  await page.route('**/decision-capabilities', r => r.fulfill({ json: { enabled: true, roles: ['warehouse'], warehouse_ids: [warehouse], workflow_enabled: true } }));
  await page.route('**/decision-tasks', r => r.fulfill({ json: { items: [{ case_id: 'case-1', title: dossier.title, revision_id: 'revision-1', task }], warnings: [], has_more: false } }));
  await page.route('**/decisions**', r => {
    const path = new URL(r.request().url()).pathname;
    if (r.request().method() === 'POST') {
      writes.push(path); expect(path.endsWith('/assistant')).toBe(true);
      expect(r.request().postDataJSON()).toEqual({ revision_id: 'revision-1', intent: 'comparison' });
      if (revoked) return r.fulfill({ status: 403, json: { detail: 'Source grant revoked' } });
      return r.fulfill({ json: { schema_version: '1.0', status: 'completed', run_id: 'answer-1', answer: 'Đây là bằng chứng phương án, chưa phải đã bàn giao.',
        datasets: [{ id: 'scenario-supply', title: 'So sánh nguồn hàng', columns: [{ field: 'scenario', label: 'Phương án', type: 'string' }, { field: 'shortage', label: 'Thiếu hàng', type: 'number', unit: 'EA' }], rows: [{ scenario: 'Trước', shortage: 30 }, { scenario: 'Sau', shortage: 0 }], context: { as_of: evidence.executed_at, complete: true }, source_ids: ['source-1'] }],
        tables: [{ id: 'table-1', title: 'Nguồn hàng trước và sau', dataset_id: 'scenario-supply', fields: ['scenario', 'shortage'] }],
        charts: [{ id: 'chart-1', title: 'Thiếu hàng của phương án', dataset_id: 'scenario-supply', type: 'bar', orientation: 'vertical', stacking: 'none', x_field: 'scenario', x_label: 'Phương án', y_label: 'Thiếu hàng', y_unit: 'EA', series: [{ field: 'shortage', label: 'Thiếu hàng' }] }], sources: [{ id: 'source-1', kind: 'tool', tool: 'workspace.decision.read' }], warnings: [], questions: [], error: null } });
    }
    if (path.endsWith('/workflow')) return r.fulfill({ json: { case: dossier, workflow: { instance_id: 'i-1', revision_id: 'revision-1', status: 'VALIDATING', tasks: [task] }, warnings: [] } });
    if (path.endsWith('/revisions')) return r.fulfill({ json: { items: [dossier.revision], has_more: false } });
    if (path.endsWith('/timeline')) return r.fulfill({ json: { items: [], has_more: false } });
    if (path.endsWith('/case-1')) return r.fulfill({ json: dossier });
    return r.fulfill({ json: { items: [dossier], warnings: [], has_more: false } });
  });
  await page.goto('/#data');
  await page.getByRole('button',{name:'Phối hợp và bàn giao',exact:true}).click();await page.getByRole('tab',{name:'Việc của tôi',exact:true}).click();
  await openDossierList(page);await page.getByRole('button', { name: 'Mở nhiệm vụ Giữ đúng phiên bản SO-001', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Xác nhận điều kiện Kho', exact: true })).toBeVisible();
  await page.getByRole('tab',{name:'Hỏi dữ liệu',exact:true}).click();
  await page.getByRole('button', { name: 'So sánh trước và sau', exact: true }).click();
  await expect(page.getByRole('img', { name: /Thiếu hàng của phương án/ })).toBeVisible();
  await expect(page.getByText('Đây là bằng chứng phương án, chưa phải đã bàn giao.', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'ref/qa/decision-assistant.png', fullPage: true, animations: 'disabled' });
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'ref/qa/decision-assistant-mobile.png',fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1440,height:1000});
  revoked = true;
  await page.getByRole('tab',{name:'Hỏi dữ liệu',exact:true}).click();
  await page.getByRole('button', { name: 'So sánh trước và sau', exact: true }).click();
  await expect(page.getByRole('img', { name: /Thiếu hàng của phương án/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Xác nhận điều kiện Kho', exact: true })).toHaveCount(0);
  expect(writes.every(path => path.endsWith('/assistant'))).toBe(true);
});


test('coordinator can replan an approved revision with a reason while preserving recorded steps', async ({ page }) => {
  await mock(page);
  const scenario: any = scenarioResult({ demand_multiplier: 1, inbound_delay_days: 0, additional_quantity: 30, arrival_date: '2026-10-25' });
  scenario.baseline.orders = [{ order_id: 'order-1', order_number: 'SO-001', lines: [] }];
  const dossier = { id: 'case-1', title: 'Lập lại SO-001', warehouse_id: warehouse, order_id: 'order-1', status: 'APPROVED', version: 4, current_revision: 1, workflow_instance_ref: 'i-1', revision: { id: 'revision-1', number: 1, created_at: evidence.executed_at, payload: { scenario, requires_revalidation: true } }, participants: [1,2,3,4], actions: { can_submit: false, can_revise: false } };
  let changed = false;
  const commands: unknown[] = [];
  await page.route('**/decision-capabilities', r => r.fulfill({ json: { enabled: true, roles: ['coordinator'], warehouse_ids: [warehouse], workflow_enabled: true } }));
  await page.route('**/decisions**', r => {
    const path = new URL(r.request().url()).pathname;
    const detail = { ...dossier, status: changed ? 'NEEDS_REVISION' : 'APPROVED', actions: { can_submit: false, can_revise: changed } };
    if (r.request().method() === 'POST') {
      expect(path.endsWith('/replan')).toBe(true);
      const body = r.request().postDataJSON();commands.push(body);
      expect(body).toEqual({ revision_id: 'revision-1', note: 'Nhà vận chuyển đổi lịch, cần xác minh lại.' });
      changed = true;return r.fulfill({ json: { case: { ...detail, status: 'NEEDS_REVISION' }, workflow: { tasks: [] }, warnings: [] } });
    }
    if (path.endsWith('/workflow')) return r.fulfill({ json: { case: detail, workflow: { instance_id: 'i-1', revision_id: 'revision-1', status: detail.status, tasks: [] }, warnings: [] } });
    if (path.endsWith('/revisions')) return r.fulfill({ json: { items: [dossier.revision], has_more: false } });
    if (path.endsWith('/timeline')) return r.fulfill({ json: { items: [{ id: 'observed-1', event_type: 'SOURCE_OBSERVED', created_at: evidence.executed_at, payload: { producer: 'warehouse', event_type: 'warehouse.order_reserved', correlation_id: 'order-1', occurred_at: evidence.executed_at } }], has_more: false } });
    if (path.endsWith('/case-1')) return r.fulfill({ json: detail });
    return r.fulfill({ json: { items: [detail], warnings: [], has_more: false } });
  });
  await page.goto('/#data');await page.getByRole('button',{name:'Phối hợp và bàn giao',exact:true}).click();await page.getByRole('tab',{name:'Theo dõi kết quả',exact:true}).click();
  await openDossierList(page);
  await page.getByRole('button', { name: 'Mở hồ sơ Lập lại SO-001', exact: true }).click();
  const replan = page.getByRole('button', { name: 'Dừng chờ bàn giao để lập phiên bản mới', exact: true });
  await expect(replan).toBeDisabled();
  await page.getByLabel('Lý do cần lập lại phương án', { exact: true }).fill('Nhà vận chuyển đổi lịch, cần xác minh lại.');
  await replan.click();
  await page.getByRole('tab',{name:'Tiến độ bàn giao',exact:true}).click();
  await expect(page.getByText('Kho ghi nhận giữ hàng', { exact: false })).toBeVisible();
  await expect(replan).toHaveCount(0);
  await expect(page.getByText('Cần sửa phương án · Phiên bản 1', { exact: true }).first()).toBeVisible();
  expect(commands).toHaveLength(1);
});

test('full warehouse orders open a second dialog with search pagination and nested detail',async({page})=>{
  const state=await mock(page);let reads=0;
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  const item={organization_id:'test-org',warehouse_id:warehouse,warehouse_name:'Kho Hà Nội',warehouse_code:'HN',products:plan.products,
    open_orders:Array.from({length:25},(_,i)=>({order_id:`order-${i+1}`,order_number:`SO-${String(i+1).padStart(3,'0')}`,deadline:'2026-10-28T17:00:00+07:00',status:'CONFIRMED',lines:[{sku:'SKU-A',uom:'EA',ordered_quantity:'30.000'}],supply_check:{status:'risk',shortages:[{sku:'SKU-A',uom:'EA',missing_quantity:10}],notes:[]}})),
    planning_status:'calculated',planning_issue:null,plan_read_at:evidence.executed_at};
  await page.route('**/all-warehouses',r=>{reads++;return r.fulfill({json:{items:[item],warehouse_count:1,horizon:7,read_at:evidence.executed_at}});});
  await page.goto('/#data');
  const sales=page.getByRole('navigation',{name:'Liên thông Bán hàng, Kho và Vận chuyển'}).getByRole('button',{name:'Bán hàng',exact:true});
  await sales.click();
  const quick=page.getByRole('dialog',{name:'Đơn từ Bán hàng · Kho Hà Nội',exact:true});
  await quick.getByRole('button',{name:'Xem đầy đủ đơn của kho',exact:true}).click();
  const list=page.getByRole('dialog',{name:'Toàn bộ đơn đang mở · Kho Hà Nội',exact:true});
  await expect(list).toBeVisible();await expect(list).toContainText('Hiển thị 1–20 / 25 đơn');
  await expect(list.getByRole('table').getByRole('row')).toHaveCount(21);
  await page.screenshot({path:'../.tmp/decision-demo/all-warehouse-orders-dialog-test.png',animations:'disabled'});
  await list.getByRole('button',{name:'Trang sau',exact:true}).click();
  await expect(list).toContainText('Hiển thị 21–25 / 25 đơn');
  await list.getByRole('button',{name:'Xem hàng của SO-021',exact:true}).click();
  const detail=page.getByRole('dialog',{name:'SO-021',exact:true});
  await expect(detail).toContainText('30 sản phẩm');
  await detail.getByRole('button',{name:'Đóng chi tiết',exact:true}).click();
  await expect(list).toBeVisible();await expect(list).toContainText('Hiển thị 21–25 / 25 đơn');
  await list.getByRole('textbox',{name:'Tìm đơn hàng trong kho',exact:true}).fill('SO-025');
  await expect(list).toContainText('Hiển thị 1–1 / 1 đơn');
  await expect(list.getByRole('table').getByRole('row')).toHaveCount(2);
  await list.getByRole('textbox',{name:'Tìm đơn hàng trong kho',exact:true}).fill('không có');
  await expect(list).toContainText('Không tìm thấy đơn phù hợp');
  await list.getByRole('button',{name:'Đóng hộp thoại',exact:true}).click();
  await expect(quick).toBeVisible();await expect(quick.getByRole('button',{name:'Xem đầy đủ đơn của kho',exact:true})).toBeFocused();
  await quick.getByRole('button',{name:'Xem đầy đủ đơn của kho',exact:true}).click();
  await expect(list).toContainText('Hiển thị 1–20 / 25 đơn');
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>list.evaluate(el=>el.getBoundingClientRect().right<=innerWidth)).toBe(true);
  await expect(list.getByRole('button',{name:'Trang sau',exact:true})).toBeInViewport();
  await page.screenshot({path:'../.tmp/decision-demo/all-warehouse-orders-dialog-mobile-test.png',animations:'disabled'});
  expect(reads).toBe(1);
  state.revision='d';
  await expect(page.getByRole('alert',{name:'Thông báo thay đổi dữ liệu'})).toContainText('Kho vừa cập nhật',{timeout:20000});
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('handoff cards open filtered paged dialogs and preserve the list after inspecting an order',async({page})=>{
  const state=await mock(page);let planningReads=0;
  await page.route('**/planning',r=>{planningReads++;return r.fulfill({json:{...structuredClone(plan),orders:Array.from({length:25},(_,i)=>({...structuredClone(plan.orders[0]),order_id:`handoff-${i+1}`,order_number:`SO-${String(i+1).padStart(3,'0')}`,already_overdue:i===0,lines:[{sku:'SKU-A',uom:'EA',unallocated_quantity:30}],at_risk:i<24,shortages_at_deadline:i<24?[{sku:'SKU-A',uom:'EA',missing_quantity:10}]:[],readiness:{status:i<24?'not_met':'unknown',conditions:{supply:i<24?'not_met':'unknown',schedule:'unknown'},stock_ready_by:'2026-10-28T07:00:00+07:00',preparation_minutes:120}})),capacity_calendar:[{day:'2026-10-28',carrier_code:'C1',carrier_name:'Giao hàng Miền Bắc',maximum:5,booked:3,remaining:2},{day:'2026-10-29',carrier_code:'C2',carrier_name:'Vận tải Miền Nam',maximum:2,booked:2,remaining:0}]}});});
  await page.goto('/#data');await apply(page);
  await page.getByRole('tab',{name:'Đơn hàng và bàn giao',exact:true}).click();
  const flow=page.getByRole('navigation',{name:'Đối chiếu đơn trước khi bàn giao',exact:true});
  await expect(page.getByRole('table')).toHaveCount(0);
  await page.screenshot({path:'../.tmp/decision-demo/handoff-compact-page-test.png',animations:'disabled'});
  await flow.getByRole('button',{name:'Đơn đến hạn bàn giao',exact:true}).click();
  const list=page.getByRole('dialog',{name:'Đơn đến hạn bàn giao',exact:true});
  await expect(list).toContainText('Hiển thị 1–20 / 25 đơn');
  await list.getByRole('button',{name:'Trang sau',exact:true}).click();
  await expect(list).toContainText('Hiển thị 21–25 / 25 đơn');
  await list.getByRole('button',{name:'Mở điều kiện SO-021',exact:true}).click();
  const detail=page.getByRole('dialog',{name:'Điều kiện bàn giao SO-021',exact:true});
  await expect(detail).toContainText('Thiếu 10 sản phẩm Cà phê rang');
  await expect(detail.getByRole('table')).toContainText('30 sản phẩm');
  await detail.getByRole('button',{name:'Đóng hộp thoại',exact:true}).click();
  await expect(list).toContainText('Hiển thị 21–25 / 25 đơn');
  await list.getByLabel('Lọc điều kiện bàn giao',{exact:true}).selectOption('late');
  await expect(list).toContainText('Hiển thị 1–1 / 1 đơn');
  await list.getByLabel('Tìm đơn cần bàn giao',{exact:true}).fill('khong co');
  await expect(list).toContainText('Không có kết quả khớp');
  await page.keyboard.press('Escape');await expect(flow.getByRole('button',{name:'Đơn đến hạn bàn giao',exact:true})).toBeFocused();
  await flow.getByRole('button',{name:'Đối chiếu nguồn hàng',exact:true}).click();
  const supply=page.getByRole('dialog',{name:'Đối chiếu nguồn hàng theo đơn',exact:true});
  await supply.getByLabel('Lọc nguồn hàng',{exact:true}).selectOption('unknown');
  await expect(supply).toContainText('Hiển thị 1–1 / 1 đơn');
  await expect(supply).toContainText('SO-025');await expect(supply).not.toContainText('SO-024');
  await supply.getByLabel('Lọc nguồn hàng',{exact:true}).selectOption('missing');
  await expect(supply).toContainText('Thiếu 10 sản phẩm');
  await page.screenshot({path:'../.tmp/decision-demo/handoff-supply-dialog-test.png',animations:'disabled'});
  await page.keyboard.press('Escape');
  await flow.getByRole('button',{name:'Kiểm tra lịch lấy hàng',exact:true}).click();
  const capacity=page.getByRole('dialog',{name:'Lịch tiếp nhận của nhà vận chuyển',exact:true});
  await capacity.getByLabel('Lọc lượt nhận hàng',{exact:true}).selectOption('available');
  await expect(capacity).toContainText('Giao hàng Miền Bắc');await expect(capacity).not.toContainText('Vận tải Miền Nam');
  await capacity.getByLabel('Tìm lịch nhà vận chuyển',{exact:true}).fill('mien bac');
  await expect(capacity).toContainText('Hiển thị 1–1 / 1 lịch');
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>capacity.evaluate(el=>el.getBoundingClientRect().right<=innerWidth)).toBe(true);
  await page.screenshot({path:'../.tmp/decision-demo/handoff-capacity-mobile-test.png',animations:'disabled'});
  expect(planningReads).toBe(1);
  expect(state.calls.filter(c=>/reservations|bookings|handoffs/.test(c.path))).toHaveLength(0);
  state.revision='d';await expect(page.getByRole('alert',{name:'Thông báo thay đổi dữ liệu'})).toBeVisible({timeout:20000});
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('warehouse overview keeps lists inside dialogs with stock and order filters',async({page})=>{
  await mock(page);let reads=0;
  await page.route('**/warehouse-coverage',r=>r.fulfill({json:{all_warehouses_enabled:true}}));
  const item={organization_id:'test-org',warehouse_id:warehouse,warehouse_name:'Kho Hà Nội',warehouse_code:'HN',products:Array.from({length:25},(_,i)=>({...plan.products[0],sku:`SKU-${i}`,name:`Mặt hàng ${i}`,shortage:i===24?0:10})),open_orders:[{order_id:'order-1',order_number:'SO-001',deadline:'2026-10-28T17:00:00+07:00',status:'CONFIRMED',lines:[{sku:'SKU-0',uom:'EA',ordered_quantity:30}],supply_check:{status:'risk',shortages:[{sku:'SKU-0',uom:'EA',missing_quantity:10}],notes:[]}}],planning_status:'calculated',planning_issue:null,plan_read_at:evidence.executed_at};
  await page.route('**/all-warehouses',r=>{reads++;return r.fulfill({json:{items:[item],warehouse_count:1,horizon:7,read_at:evidence.executed_at}});});
  await page.goto('/#data');await expect(page.getByRole('button',{name:'Xem tồn kho (25)',exact:true})).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
  await page.getByRole('button',{name:'Xem tồn kho (25)',exact:true}).click();
  const stock=page.getByRole('dialog',{name:'Nguồn hàng trong Kho · Kho Hà Nội',exact:true});
  await expect(stock.getByRole('table').getByRole('row')).toHaveCount(21);
  await stock.getByRole('button',{name:'Trang sau',exact:true}).click();await expect(stock).toContainText('Hiển thị 21–25 / 25 mặt hàng');
  await stock.getByLabel('Tìm mặt hàng trong kho',{exact:true}).fill('mat hang 24');await expect(stock).toContainText('Hiển thị 1–1 / 1 mặt hàng');
  await stock.getByRole('group',{name:'Lọc mặt hàng'}).getByRole('button',{name:/Có thể thiếu/}).click();await expect(stock).toContainText('Không có mặt hàng khớp bộ lọc');
  await stock.getByRole('button',{name:'Bỏ bộ lọc',exact:true}).click();
  await stock.getByLabel('Tìm mặt hàng trong kho',{exact:true}).fill('mat hang 24');
  await stock.getByRole('button',{name:'Xem số lượng từng đơn',exact:true}).click();
  const orders=page.getByRole('dialog',{name:'Toàn bộ đơn đang mở · Kho Hà Nội',exact:true});
  await orders.getByLabel('Lọc tình trạng hàng của đơn',{exact:true}).selectOption('clear');await expect(orders).toContainText('Không tìm thấy đơn phù hợp');
  await orders.getByLabel('Lọc tình trạng hàng của đơn',{exact:true}).selectOption('risk');await expect(orders).toContainText('SO-001');
  await page.keyboard.press('Escape');
  await expect(stock).toBeVisible();await expect(stock.getByLabel('Tìm mặt hàng trong kho',{exact:true})).toHaveValue('mat hang 24');
  await expect(stock).toContainText('Hiển thị 1–1 / 1 mặt hàng');
  await page.keyboard.press('Escape');await expect(page.getByRole('table')).toHaveCount(0);expect(reads).toBe(1);
});

async function openInventory(page:Page){
  await page.getByRole('button',{name:/^Xem tồn kho \(\d+\)$/}).click();
  await expect(page.getByRole('dialog',{name:/^Nguồn hàng trong Kho ·/})).toBeVisible();
}
async function closeWorkspaceDialogs(page:Page){
  for(let n=0;n<8&&await page.getByRole('dialog').count();n++){
    const dialog=page.getByRole('dialog').last(),id=await dialog.getAttribute('id');
    const close=dialog.getByRole('button',{name:/^(Đóng hộp thoại|Close)$/}).first();
    if(await close.isVisible())await close.click();else await page.keyboard.press('Escape');
    if(id)await expect(page.locator(`[id="${id}"]`)).toHaveCount(0);
  }
}


test('record product catalog explains units and opens exact stock quantities per warehouse without forecasts',async({page})=>{
  const state=await mock(page);const calls:any[]=[];
  await page.route('**/records',r=>{
    const b=r.request().postDataJSON();calls.push(b);
    const items=b.collection==='products'?[{product_id:'p1',sku:'KB-K120',product_name:'Bàn phím Logitech K120',uom:'cái',status:'ACTIVE'}]:[
      {warehouse_id:warehouse,warehouse_code:'HN',warehouse_name:'Kho Hà Nội',sku:'KB-K120',product_name:'Bàn phím Logitech K120',uom:'cái',on_hand_quantity:'618.000',reserved_quantity:'608.000',available_quantity:'10.000'},
      {warehouse_id:'other',warehouse_code:'HCM',warehouse_name:'Kho Hồ Chí Minh',sku:'KB-K120',product_name:'Bàn phím Logitech K120',uom:'cái',on_hand_quantity:'0.000',reserved_quantity:'0.000',available_quantity:null}];
    return r.fulfill({json:{items,offset:b.offset||0,page_size:50,has_more:false,read_at:evidence.executed_at}});
  });
  await page.goto('/#data');await page.getByRole('button',{name:'Tra cứu dữ liệu',exact:true}).click();
  await expect(page.getByRole('table')).toHaveCount(0);expect(calls).toHaveLength(0);
  await page.screenshot({path:'../.tmp/decision-demo/records-lookup-home-test.png',animations:'disabled'});
  await page.getByRole('button',{name:'Danh mục mặt hàng',exact:true}).click();
  const catalog=page.getByRole('dialog',{name:'Danh mục mặt hàng',exact:true});
  await expect(catalog).toContainText('Đơn vị tính cho biết cách đếm sản phẩm');
  await catalog.getByLabel('Tìm trong trang đang xem',{exact:true}).fill('ban phim');
  await catalog.getByRole('button',{name:'Xem tồn theo kho',exact:true}).click();
  const stock=page.getByRole('dialog',{name:'Tồn kho thực tế',exact:true});
  expect(calls.at(-1)).toMatchObject({collection:'inventory',code:'KB-K120',offset:0});
  const first=stock.getByRole('row').filter({hasText:'Kho Hà Nội'});
  await expect(first.getByRole('cell').nth(2)).toHaveText('618 cái');
  await expect(first.getByRole('cell').nth(3)).toHaveText('608 cái');
  await expect(first.getByRole('cell').nth(4)).toHaveText('10 cái');
  const second=stock.getByRole('row').filter({hasText:'Kho Hồ Chí Minh'});
  await expect(second.getByRole('cell').nth(2)).toHaveText('0 cái');
  await expect(second.getByRole('cell').nth(4)).toHaveText('Chưa có số liệu');
  await page.screenshot({path:'../.tmp/decision-demo/records-real-stock-dialog-test.png',animations:'disabled'});
  await page.setViewportSize({width:390,height:844});
  await expect(stock.getByLabel('Số lượng tồn theo kho')).toBeVisible();
  await expect(stock.getByLabel('Số lượng tồn theo kho')).toContainText('618 cái');
  await page.screenshot({path:'../.tmp/decision-demo/records-stock-mobile-test.png',animations:'disabled'});
  await page.setViewportSize({width:1440,height:1000});
  await page.keyboard.press('Escape');await expect(catalog).toBeVisible();
  await expect(catalog.getByLabel('Tìm trong trang đang xem',{exact:true})).toHaveValue('ban phim');
  expect(count(state,'/planning')).toBe(0);expect(count(state,'/all-warehouses')).toBe(0);
});

test('record warehouse links keep the warehouse scope on filters pages and related orders',async({page})=>{
  const state=await mock(page);const calls:any[]=[];
  await page.route('**/records',r=>{
    const b=r.request().postDataJSON();calls.push(b);let items:any[]=[];
    if(b.collection==='warehouses')items=[{warehouse_id:warehouse,warehouse_code:'HN',warehouse_name:'Kho Hà Nội',status:'ACTIVE'}];
    if(b.collection==='inventory')items=b.code?[]:[{warehouse_id:warehouse,warehouse_code:'HN',warehouse_name:'Kho Hà Nội',sku:'A4',product_name:'Giấy A4',uom:'ram',on_hand_quantity:'12.125',reserved_quantity:'2.000',available_quantity:'10.125'}];
    if(b.collection==='orders')items=[{order_id:'o1',order_number:'SO-CANCELLED',warehouse_id:warehouse,warehouse_name:'Kho Hà Nội',status:'CANCELLED',line_count:0}];
    return r.fulfill({json:{items,offset:b.offset||0,page_size:50,has_more:b.collection==='inventory'&&!b.offset&&!b.code,read_at:evidence.executed_at}});
  });
  await page.goto('/#data');await page.getByRole('button',{name:'Tra cứu dữ liệu',exact:true}).click();
  await page.getByRole('button',{name:'Danh mục kho',exact:true}).click();
  const locations=page.getByRole('dialog',{name:'Danh mục kho',exact:true});
  await locations.getByRole('button',{name:'Xem hàng trong kho',exact:true}).click();
  const stock=page.getByRole('dialog',{name:'Tồn kho thực tế',exact:true});
  await expect(stock).toContainText('12,125 ram');
  await stock.getByRole('button',{name:'Trang sau',exact:true}).click();
  expect(calls.at(-1)).toMatchObject({collection:'inventory',warehouse_id:warehouse,offset:50});
  await stock.getByLabel('Mã mặt hàng chính xác',{exact:true}).fill('CHUA-NHAP');
  await expect(stock.getByRole('button',{name:'Trang trước',exact:true})).toBeDisabled();
  await stock.getByRole('button',{name:'Lọc và tải dữ liệu',exact:true}).click();
  await expect(stock).toContainText('Chưa có bản ghi tồn kho khớp bộ lọc. Điều này chưa xác nhận số lượng bằng 0.');
  expect(calls.at(-1)).toMatchObject({code:'CHUA-NHAP',warehouse_id:warehouse,offset:0});
  await page.keyboard.press('Escape');await expect(locations).toBeVisible();
  await locations.getByRole('button',{name:'Xem đơn của kho',exact:true}).click();
  const orders=page.getByRole('dialog',{name:'Đơn hàng và dòng hàng',exact:true});
  await expect(orders).toContainText('SO-CANCELLED');await expect(orders).toContainText('Đã hủy');
  expect(calls.at(-1)).toMatchObject({collection:'orders',warehouse_id:warehouse});
  state.revision='d';await expect(page.getByRole('alert',{name:'Thông báo thay đổi dữ liệu'})).toBeVisible({timeout:20000});
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('record order detail cancels a closed request and never replaces a newer order with a late response',async({page})=>{
  await mock(page);let release:()=>void=()=>{};let started=false;
  await page.route('**/records',async r=>{
    const b=r.request().postDataJSON();let items:any[]=[];
    if(b.collection==='orders')items=[{order_id:'a',order_number:'SO-A',status:'CONFIRMED',line_count:1},{order_id:'b',order_number:'SO-B',status:'COMPLETED',line_count:1}];
    if(b.collection==='order_lines'){
      if(b.order_id==='a'){started=true;await new Promise<void>(resolve=>{release=resolve;});}
      items=[{order_line_id:'line-'+b.order_id,line_no:1,product_name:b.order_id==='a'?'Mặt hàng cũ':'Giấy A4',sku:b.order_id,uom:'ram',ordered_quantity:'1.125'}];
    }
    await r.fulfill({json:{items,offset:0,page_size:50,has_more:false,read_at:evidence.executed_at}}).catch(()=>{});
  });
  await page.goto('/#data');await page.getByRole('button',{name:'Tra cứu dữ liệu',exact:true}).click();
  await page.getByRole('button',{name:'Đơn hàng và dòng hàng',exact:true}).click();
  await page.getByRole('button',{name:'Xem hàng của SO-A',exact:true}).click();await expect.poll(()=>started).toBe(true);
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'Xem hàng của SO-B',exact:true}).click();release();
  const detail=page.getByRole('dialog',{name:'Các mặt hàng của SO-B',exact:true});
  await expect(detail).toContainText('1,125 ram');await expect(detail).not.toContainText('Mặt hàng cũ');
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>detail.evaluate(el=>el.getBoundingClientRect().right<=innerWidth)).toBe(true);
  await page.screenshot({path:'../.tmp/decision-demo/records-order-quantity-mobile-test.png',animations:'disabled'});
});

async function beginForecast(page:Page,transport=false){
 await closeWorkspaceDialogs(page);await page.getByRole('button',{name:'Dự báo nhu cầu',exact:true}).click();
 await page.getByRole('button',{name:'Tạo dự báo',exact:true}).click();
 const wizard=page.getByRole('dialog',{name:'Tạo dự báo nhu cầu',exact:true});
 if(transport)await page.getByLabel('Nội dung',{exact:true}).selectOption(products.at(-1)!.id);
 await wizard.getByRole('button',{name:'Cập nhật dữ liệu',exact:true}).click();
 await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
}
async function finishForecast(page:Page,success=true){
 const wizard=page.getByRole('dialog',{name:'Tạo dự báo nhu cầu',exact:true});await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await wizard.getByRole('button',{name:'Tạo dự báo',exact:true}).click();
 if(success)await expect(page.getByRole('dialog',{name:'Kết quả dự báo nhu cầu',exact:true})).toBeVisible();
}
async function openIT(page:Page){
 await closeWorkspaceDialogs(page);await page.getByRole('button',{name:'Thiết lập cho IT',exact:true}).click();await page.getByRole('button',{name:'Thiết lập cấu hình phân tích',exact:true}).click();
}

test('each workspace page opens compact content and runs tools only on explicit actions',async({page})=>{
  const state=await mock(page,true);
  await page.route('**/decision-capabilities',r=>r.fulfill({json:{enabled:true,roles:['coordinator'],warehouse_ids:[warehouse],workflow_enabled:false}}));
  await page.route('**/decision-tasks',r=>r.fulfill({json:{items:[],has_more:false,warnings:[]}}));
  await page.goto('/#data');
  for(const [index,name] of ['Tổng quan kho','Dự báo nhu cầu','Đơn hàng và tác động','Thử phương án nhập hàng','Phối hợp và bàn giao','Tra cứu dữ liệu','Phân tích lịch sử','Thiết lập cho IT'].entries()){
    await page.getByRole('button',{name,exact:true}).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.locator('.dw-content input:visible,.dw-content textarea:visible,.dw-content table:visible')).toHaveCount(0);
    await page.screenshot({path:`../.tmp/ui-dialog-redesign/home-${index}-fixture.png`,animations:'disabled'});
  }
  expect(count(state,'/planning')).toBe(0);expect(count(state,'/forecasts')).toBe(0);expect(count(state,'/preview')).toBe(0);expect(count(state,'/configurations')).toBe(0);
});

test('wizard blocks skipping, invalid fields and stale downstream confirmations',async({page})=>{
  const state=await mock(page,true);await page.goto('/#data');await openIT(page);
  const wizard=page.getByRole('dialog',{name:'Thiết lập cấu hình phân tích',exact:true});
  await page.getByLabel('Tên cấu hình',{exact:true}).fill('');await expect(wizard.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();await expect(wizard.getByRole('button',{name:'Ghép các cột dữ liệu',exact:true})).toBeDisabled();
  await page.getByLabel('Tên cấu hình',{exact:true}).fill('Đánh giá nhu cầu');await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  await page.getByLabel('Cột đo lường',{exact:true}).selectOption('sku');await expect(wizard.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();
  await page.getByLabel('Cột đo lường',{exact:true}).selectOption('confirmed_quantity');await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  await expect(wizard.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();await wizard.getByRole('button',{name:'Kiểm tra & xem trước',exact:true}).click();await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();
  await expect(wizard.getByRole('button',{name:'Lưu cấu hình',exact:true})).toBeEnabled();
  await wizard.getByRole('button',{name:'Chọn nguồn và thời gian',exact:true}).click();await page.getByLabel('Tên cấu hình',{exact:true}).fill('Đánh giá nhu cầu mới');await expect(wizard.getByRole('button',{name:'Xem lại và lưu',exact:true})).toBeDisabled();
  await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await expect(wizard.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();
  expect(count(state,'/preview')).toBe(1);expect(count(state,'/configurations')).toBe(0);
});

test('closing a running forecast discards a late reply and never reopens its result',async({page})=>{
  await mock(page);let release=()=>{},started=false,completed=false;
  const waiting=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/forecasts',async route=>{started=true;await waiting;await route.fulfill({status:201,json:forecast}).catch(()=>{});completed=true;});
  await page.goto('/#data');await beginForecast(page);await finishForecast(page,false);await expect.poll(()=>started).toBe(true);
  await page.getByRole('dialog',{name:'Tạo dự báo nhu cầu',exact:true}).getByRole('button',{name:'Đóng hộp thoại',exact:true}).click();release();await expect.poll(()=>completed).toBe(true);
  await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.getByRole('button',{name:'Xem kết quả dự báo',exact:true})).toBeDisabled();
});

function insightFixture(){
 const base={material_ready_at:'2026-10-27T08:00:00+07:00',preparation_complete_at:'2026-10-27T10:00:00+07:00',state:'on_time',slack_hours:7};
 const late={material_ready_at:'2026-10-29T08:00:00+07:00',preparation_complete_at:'2026-10-29T10:00:00+07:00',state:'late',slack_hours:-41};
 const orders=Array.from({length:12},(_,i)=>({order_id:`insight-${i}`,order_number:`SO-INS-${i+1}`,deadline:'2026-10-27T17:00:00+07:00',already_overdue:false,base:{...base},high_demand:{...late},delayed:i<2?{...late}:{...base},affected_by_delay:i<2,newly_late:i<2,missing_forecast_skus:[],shortages:[{sku:'SKU-A',uom:'EA',missing_quantity:5}],supply_links:[{source_id:'00000000-0000-4000-8000-000000000080',sku:'SKU-A',uom:'EA',quantity:30}],lines:[{sku:'SKU-A',name:'Cà phê rang',uom:'EA',quantity:30}],schedules:[],all_lines_held_or_dispatched:false}));
 return {available:true,as_of:'2026-10-25T09:00:00+07:00',start:'2026-10-25',end:'2026-10-31',forecast_method:'chronos2',preparation_minutes:120,delay_days:2,delayed_plan_id:null,orders,inbound_options:[{plan_id:'00000000-0000-4000-8000-000000000080',sku:'SKU-A',uom:'EA',quantity:30,expected_at:'2026-10-27T08:00:00+07:00'}],bottlenecks:[{sku:'SKU-A',name:'Cà phê rang',uom:'EA',order_ids:orders.map(o=>o.order_id),order_count:12,cumulative_percent:100}],capacity_available:true,capacity:[{day:'2026-10-27',carrier_code:'C1',maximum:4,used:4,remaining:0,utilization_percent:100},{day:'2026-10-28',carrier_code:'C1',maximum:4,used:2,remaining:2,utilization_percent:50}],summary:{total_orders:12,on_time:12,newly_late:2,affected:2,high_demand_exposed:12},limitations:['Phân bổ giả lập, chưa giữ hàng hoặc tạo giao dịch.']};
}
async function configureInsights(page:Page){
 await page.getByRole('button',{name:'Đơn hàng và tác động',exact:true}).click();await page.getByRole('button',{name:'Chọn kho và tạo phân tích',exact:true}).click();
 const wizard=page.getByRole('dialog',{name:'Thiết lập phân tích kết quả đơn',exact:true});
 await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();return wizard;
}
test('order insights require explicit confirmation and reuse their authorized snapshot across navigation',async({page})=>{
 const state=await mock(page);let reads=0;
 await page.route('**/planning',r=>{reads++;const body=r.request().postDataJSON();expect(body.include_fulfillment_insights).toBe(true);expect(body.forecast_model).toBe('chronos2');expect(body.warehouse_id).toBe(warehouse);return r.fulfill({json:{...plan,fulfillment_insights:insightFixture()}});});
 await page.goto('/#data');await page.getByRole('button',{name:'Đơn hàng và tác động',exact:true}).click();expect(reads).toBe(0);await expect(page.locator('.dw-content input:visible,.dw-content table:visible')).toHaveCount(0);
 const wizard=await configureInsights(page);expect(reads).toBe(0);await wizard.getByRole('button',{name:'Tạo phân tích',exact:true}).click();
 const timeline=page.getByRole('dialog',{name:'Mốc chuẩn bị dự kiến của đơn',exact:true});await expect(timeline.getByRole('button').filter({hasText:'SO-INS-1'}).first()).toBeVisible();await timeline.getByRole('button',{name:'Trang sau',exact:true}).click();await expect(timeline).toContainText('trang 2');await timeline.getByRole('button',{name:'Trang trước',exact:true}).click();
 await timeline.getByRole('button').filter({hasText:'SO-INS-1'}).first().click();const detail=page.getByRole('dialog',{name:'Ước tính SO-INS-1',exact:true});await expect(detail).toContainText('Cà phê rang');await expect(detail).toContainText('30 sản phẩm');await expect(detail).toContainText('Chưa có lịch lấy hàng');await detail.getByRole('button',{name:'Đóng hộp thoại',exact:true}).click();await expect(timeline).toContainText('trang 1');
 await page.screenshot({path:'../.tmp/ui-dialog-redesign/insights-timeline-fixture.png',animations:'disabled'});await closeWorkspaceDialogs(page);
 await page.getByRole('button',{name:'Tra cứu dữ liệu',exact:true}).click();await page.getByRole('button',{name:'Đơn hàng và tác động',exact:true}).click();expect(reads).toBe(1);
 await page.getByRole('button',{name:'Hàng về trễ ảnh hưởng đơn nào?',exact:true}).click();const impact=page.getByRole('dialog',{name:'Tác động khi hàng về trễ',exact:true});await expect(impact).toContainText('2 đơn từ kịp thành không kịp');await expect(impact.locator('.fi-order-row')).toHaveCount(2);await closeWorkspaceDialogs(page);
 await page.getByRole('button',{name:'Mặt hàng chặn nhiều đơn',exact:true}).click();await expect(page.getByRole('img',{name:'Pareto mặt hàng thiếu và số đơn bị chặn',exact:true})).toBeVisible();await page.getByRole('button',{name:'Xem đơn thiếu SKU-A',exact:true}).click();await expect(page.locator('.fi-order-row')).toHaveCount(6);await closeWorkspaceDialogs(page);
 await page.getByRole('button',{name:'Ngày còn lượt lấy hàng',exact:true}).click();const heatmap=page.getByRole('dialog',{name:'Bản đồ lượt lấy hàng',exact:true});await heatmap.getByRole('button').filter({hasText:'2/4'}).click();await expect(heatmap.getByRole('status')).toContainText('còn 2 lượt');await page.setViewportSize({width:390,height:844});await page.screenshot({path:'../.tmp/ui-dialog-redesign/insights-capacity-fixture-mobile.png',animations:'disabled'});expect(await heatmap.evaluate(e=>e.getBoundingClientRect().right<=innerWidth)).toBe(true);
 expect(reads).toBe(1);expect(state.calls.some(c=>/scenarios|decisions/.test(c.path))).toBe(false);
});
test('order insight delay is bounded and a stale or denied reply cannot restore private data',async({page})=>{
 const state=await mock(page);let reads=0;
 await page.route('**/planning',r=>{reads++;return r.fulfill({json:{...plan,fulfillment_insights:insightFixture()}});});
 await page.goto('/#data');await page.getByRole('button',{name:'Đơn hàng và tác động',exact:true}).click();await page.getByRole('button',{name:'Chọn kho và tạo phân tích',exact:true}).click();const wizard=page.getByRole('dialog',{name:'Thiết lập phân tích kết quả đơn',exact:true});
 await expect(wizard.getByRole('button',{name:'Xác nhận',exact:true})).toBeDisabled();await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await page.getByLabel('Hàng về trễ thêm (ngày)',{exact:true}).fill('15');await expect(wizard.getByRole('button',{name:'Tiếp tục',exact:true})).toBeDisabled();await page.getByLabel('Hàng về trễ thêm (ngày)',{exact:true}).fill('2');await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await wizard.getByRole('button',{name:'Tạo phân tích',exact:true}).click();await expect(page.getByRole('dialog',{name:'Mốc chuẩn bị dự kiến của đơn',exact:true})).toBeVisible();await closeWorkspaceDialogs(page);
 await page.getByRole('button',{name:'Đổi giả định hoặc cập nhật',exact:true}).click();await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await page.getByLabel('Đợt hàng giả lập trễ',{exact:true}).selectOption('00000000-0000-4000-8000-000000000080');await closeWorkspaceDialogs(page);await expect(page.getByRole('button',{name:'Mốc chuẩn bị từng đơn',exact:true})).toBeDisabled();expect(reads).toBe(1);
 state.revision='d';await expect(page.getByRole('alert',{name:'Thông báo thay đổi dữ liệu'})).toBeVisible({timeout:20000});await page.getByRole('button',{name:'Cập nhật dữ liệu ngay',exact:true}).click();await expect(page.getByRole('button',{name:'Mốc chuẩn bị từng đơn',exact:true})).toBeDisabled();expect(reads).toBe(1);
});

test('a denied insight refresh clears the previous result and never falls back to a different model',async({page})=>{
 const state=await mock(page);let denied=false,reads=0;
 await page.route('**/planning',route=>{reads++;return denied?route.fulfill({status:403,json:{detail:'Source access revoked'}}):route.fulfill({json:{...plan,fulfillment_insights:insightFixture()}});});
 await page.goto('/#data');const wizard=await configureInsights(page);await wizard.getByRole('button',{name:'Tạo phân tích',exact:true}).click();await expect(page.getByRole('dialog',{name:'Mốc chuẩn bị dự kiến của đơn',exact:true})).toBeVisible();await closeWorkspaceDialogs(page);
 denied=true;await page.getByRole('button',{name:'Đổi giả định hoặc cập nhật',exact:true}).click();await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await wizard.getByRole('button',{name:'Tiếp tục',exact:true}).click();await wizard.getByRole('button',{name:'Tạo phân tích',exact:true}).click();
 await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.getByRole('button',{name:'Mốc chuẩn bị từng đơn',exact:true})).toBeDisabled();expect(reads).toBe(2);expect(state.calls.some(c=>c.path.endsWith('/scenarios'))).toBe(false);
});
