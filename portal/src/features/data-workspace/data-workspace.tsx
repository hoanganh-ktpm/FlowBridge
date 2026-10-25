// SPDX-License-Identifier: GPL-3.0-or-later
import { useCallback, useEffect, useRef, useState } from 'react';
import { BarChart3, CalendarDays, ChartLine, Info, LoaderCircle, Package, Settings2, Truck } from 'lucide-react';
import { useSession } from '@/app/session';
import { Brand } from '@/components/brand';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { ApiError } from '@/lib/api';
import { Configuration } from './configuration';
import { BusinessAnalytics } from './business-analytics';
import { BusinessEvidence, type Explanation } from './business-evidence';
import { Field, Panel, Plot, Select, dateLabel, fmt, unitLabel } from './components';
import { ProductSelector } from './product-selector';
import { OperationsDashboard } from './operations-dashboard';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { SensitivityChart } from './sensitivity-chart';
import { warehouseLabel, type PlanningDirectory, type Planning, type PlanProduct } from './planning-types';
import { ScenarioAssumptions, type Proposal } from './scenario-assumptions';
import { ScenarioComparison } from './scenario-comparison';
import { scenarioSummary } from './scenario-summary';
import { CreateDossier, DecisionDossiers, type DecisionCapabilities } from './decision-dossiers';
import { Card, CardContent } from '@/components/ui/card';
import type { Forecast, Overview, Product, Scenario } from './types';
import './workspace.css';

type View = 'analytics' | 'forecast' | 'dashboard' | 'plan' | 'orders' | 'configuration' | 'decisions' | 'tasks' | 'outcomes';
type Draft = { warehouse: string; sku: string; horizon: string; history: string; frequency: string; training: string };
const initialDraft: Draft = { warehouse: '', sku: '', horizon: '7', history: '30', frequency: 'day', training: '180' };
const sourceRoles = {
  demand: ['sales.planning_demand_daily', 'Lượng đặt hàng theo kho'],
  stock: ['warehouse.stock_availability', 'Tồn kho và hàng đã giữ'],
  inbound: ['warehouse.inbound_schedule', 'Kế hoạch hàng về'],
  open_orders: ['sales.open_orders', 'Đơn hàng đang chờ'],
  commitments: ['warehouse.order_commitments', 'Hàng đã giữ hoặc bàn giao theo đơn'],
  catalog: ['warehouse.planning_catalog', 'Tên sản phẩm và tên kho'],
  receipts: ['warehouse.receipts_daily', 'Hàng nhập thực tế'],
  schedule: ['logistics.handoff_schedule', 'Lịch giao nhận theo đơn'],
  capacity: ['logistics.handoff_capacity', 'Số lượt nhận hàng của nhà vận chuyển'],
  preparation: ['warehouse.handoff_preparation_policy', 'Thời gian chuẩn bị do Kho xác nhận'],
} as const;
const required = ['demand', 'stock', 'inbound', 'open_orders', 'commitments'] as const;
const blue = 'var(--fb-brand-blue)', cyan = 'var(--fb-brand-cyan)', purple = 'var(--fb-brand-purple)';
const productRef = (p: Product) => ({ data_product_id: p.id, contract_version_id: p.contract_version_id });
const priorityLabel = { urgent: 'Cần xử lý', watch: 'Cần xác nhận hàng về', clear: 'Đủ theo ước tính', unknown: 'Chưa đủ dữ liệu' };
const orderReason: Record<string, string> = { stock: 'Chưa đủ hàng trước hạn', schedule_late: 'Lịch dự kiến sau hạn cần đáp ứng', schedule_unconfirmed: 'Cần chốt lịch với vận chuyển', schedule_unknown: 'Chưa có dữ liệu lịch giao nhận', scheduled: 'Đã có lịch bàn giao dự kiến' };

export function DataWorkspace() {
  const session = useSession();
  const [view, setView] = useState<View>('dashboard');
  const [products, setProducts] = useState<Product[]>([]);
  const [sourceNames, setSourceNames] = useState<Record<string, string>>({});
  const [directory, setDirectory] = useState<PlanningDirectory>({ warehouses: [], products: [] });
  const [directoryLoaded, setDirectoryLoaded] = useState(false);
  const [admin, setAdmin] = useState(false), [catalogLoaded, setCatalogLoaded] = useState(false);
  const [draft, setDraft] = useState(initialDraft), [applied, setApplied] = useState<Draft | null>(null);
  const [plan, setPlan] = useState<Planning | null>(null);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [inputsOpen, setInputsOpen] = useState(false);
  const [filter, setFilter] = useState('all');
  const [proposal, setProposal] = useState<Proposal>({ quantity: 0, arrival: '', multiplier: 100, delay: 0, quantile: 'p50' });
  const [scenario, setScenario] = useState<Scenario | null>(null), [calculated, setCalculated] = useState<Proposal | null>(null);
  const [saved, setSaved] = useState<Scenario[]>([]), [savedOpen, setSavedOpen] = useState(false);
  const [savedForecasts, setSavedForecasts] = useState<Forecast[]>([]), [savedForecast, setSavedForecast] = useState<Forecast | null>(null);
  const [custom, setCustom] = useState<Overview | null>(null);
  const [decisionCapabilities, setDecisionCapabilities] = useState<DecisionCapabilities | null>(null);
  const snapshot = useRef<string | null>(null), generation = useRef(0), operation = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  const mainRef = useRef<HTMLElement | null>(null);
  useEffect(() => { mainRef.current?.scrollTo({ top: 0 }); }, [view, plan?.selected_sku]);
  const request = useCallback(async <T,>(path: string, body?: unknown, signal?: AbortSignal, key?: string): Promise<T> => {
    const ticket = generation.current, controller = new AbortController();
    controllers.current.add(controller);
    try {
      const result = await session.api.request<T>('/api/v1/data-workspace' + path, { method: body === undefined ? 'GET' : 'POST', body, key, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(90000), ...(signal ? [signal] : [])]) });
      if (ticket !== generation.current) throw new DOMException('Selection changed', 'AbortError');
      return result;
    } catch (e) { if (e instanceof ApiError && e.status === 401) session.invalidate(); throw e; }
    finally { controllers.current.delete(controller); }
  }, [session.api, session.invalidate]);
  function report(e: unknown) {
    const retryAction = view === 'plan' ? 'Cập nhật dự báo ML' : view === 'forecast' ? 'Cập nhật dữ liệu' : view === 'analytics' ? 'Cập nhật biểu đồ' : 'Cập nhật kế hoạch';
    if (e instanceof DOMException && e.name === 'AbortError') return;
    setExplanation(null);
    if (e instanceof ApiError && [401, 403].includes(e.status)) {
      setDecisionCapabilities(null);
      generation.current++; controllers.current.forEach(c => c.abort());
      setInputsOpen(false); setDirectory({ warehouses: [], products: [] }); setDirectoryLoaded(false); setPlan(null); setScenario(null); setSaved([]); setSavedForecasts([]); setSavedForecast(null); setCustom(null); setProducts([]); setAdmin(false); setSavedOpen(false);
    }
    setError(e instanceof ApiError && e.status === 403 ? 'Quyền dữ liệu đã thay đổi. Liên hệ quản trị viên để kiểm tra rồi tải lại trang.'
      : e instanceof ApiError && e.status === 409 ? 'Số liệu so sánh đã hết hạn. Bấm So sánh phương án để tính lại trước khi lưu.'
      : e instanceof ApiError && (e.status === 422 && ['NO_DATA', 'STALE_HISTORY', 'INSUFFICIENT_HISTORY', 'DATASET_TOO_LARGE', 'ML_HISTORY_SHORT', 'ML_SOURCE_REQUIRED', 'FORECAST_WINDOW_INVALID'].includes(e.code || '') || e.status === 504 && e.code === 'PLANNING_TIMEOUT') ? e.message
      : e instanceof DOMException && e.name === 'TimeoutError' ? `Đọc dữ liệu mất quá lâu. Chọn ${retryAction} để thử lại.`
      : e instanceof ApiError && e.status === 422 ? 'Dữ liệu hoặc lựa chọn chưa phù hợp để tính toán. Kiểm tra kho, sản phẩm, đơn vị và khoảng ngày rồi thử lại.'
      : `Chưa tải được nguồn dữ liệu. Kiểm tra kết nối rồi chọn ${retryAction} để thử lại.`);
  }
  async function perform(label: string, work: () => Promise<void>) {
    const ticket = ++operation.current;
    setBusy(label); setError(''); setNotice('');
    try { await work(); } catch (e) { if (ticket === operation.current) report(e); } finally { if (ticket === operation.current) setBusy(''); }
  }
  function cancel() {
    operation.current++; generation.current++;
    controllers.current.forEach(c => c.abort());
    setBusy(''); setError(''); setNotice('Đã dừng chờ. Bạn có thể đổi lựa chọn rồi cập nhật lại.');
  }
  async function loadDirectory(items = products, names = sourceNames) {
    const stock = items.find(p => p.name === (names.stock || sourceRoles.stock[0]));
    const catalog = items.find(p => p.name === (names.catalog || sourceRoles.catalog[0]));
    if (!stock) { setDirectoryLoaded(true); return; }
    const result = await request<PlanningDirectory>('/planning-options', { stock: productRef(stock), ...(catalog ? { catalog: productRef(catalog) } : {}) });
    setDirectory(result); setDirectoryLoaded(true);
    setDraft(previous => {
      const warehouse = result.warehouses.some(w => w.id === previous.warehouse) ? previous.warehouse : result.warehouses.length === 1 ? result.warehouses[0].id : '';
      const choices = result.products.filter(p => p.warehouse_id === warehouse);
      return { ...previous, warehouse, sku: choices.some(p => p.sku === previous.sku) ? previous.sku : choices[0]?.sku || '' };
    });
  }
  useEffect(() => {
    let active = true;
    void perform('Đang kiểm tra nguồn được chia sẻ', async () => {
      const result = await request<{ items: Product[]; can_configure: boolean; source_names?: Record<string, string> }>('/products');
      if (active) { setSourceNames(result.source_names || {}); setProducts(result.items); setAdmin(result.can_configure); setCatalogLoaded(true); await loadDirectory(result.items, result.source_names || {}); }
    });
    return () => { active = false; generation.current++; controllers.current.forEach(c => c.abort()); };
  }, [request]);

  useEffect(() => {
    const controller = new AbortController();
    void request<DecisionCapabilities>('/decision-capabilities', undefined, controller.signal)
      .then(value => { if (!controller.signal.aborted) setDecisionCapabilities(value); })
      .catch(() => { if (!controller.signal.aborted) setDecisionCapabilities(null); });
    return () => controller.abort();
  }, [request]);

  const sources = Object.fromEntries(Object.entries(sourceRoles).map(([key, [name]]) => [key, products.find(p => p.name === (sourceNames[key] || name))]));
  const missing = required.filter(k => !sources[k]);
  const selected = plan?.products.find(p => p.sku === plan.selected_sku);
  const dirty = !!plan && JSON.stringify(draft) !== JSON.stringify(applied);
  const scenarioDirty = JSON.stringify(proposal) !== JSON.stringify(calculated);
  const hasML = (plan?.forecast.model.entities?.[plan.selected_sku]?.method || plan?.forecast.model.method) === 'ridge_lag';
  const blocked = !!busy || !!missing.length || !catalogLoaded || !directoryLoaded || !draft.warehouse;
  const title = view === 'tasks' ? 'Việc của tôi' : view === 'outcomes' ? 'Theo dõi kết quả' : view === 'decisions' ? 'Hồ sơ quyết định' : view === 'analytics' ? 'Tổng quan' : view === 'forecast' ? 'Dự báo nhu cầu' : view === 'plan' ? 'Thử phương án nhập hàng' : view === 'orders' ? 'Đơn hàng & giao nhận' : view === 'configuration' ? 'Thiết lập cho IT' : 'Tổng quan';
  function patch<K extends keyof Draft>(key: K, value: Draft[K]) { setDraft(d => ({ ...d, [key]: value })); }
  async function load(next = draft, nextView?: View) {
    generation.current++; controllers.current.forEach(c => c.abort());
    setInputsOpen(false); setPlan(null); setScenario(null); setCalculated(null); setExplanation(null); snapshot.current = null;
    const result = await request<Planning>('/planning', {
      ...Object.fromEntries(Object.entries(sources).filter(([, p]) => !!p).map(([k, p]) => [k, productRef(p!)])),
      warehouse_id: next.warehouse || null, sku: next.sku || null, horizon: Number(next.horizon), history_days: Number(next.history), history_frequency: next.frequency, forecast_model: (nextView || view) === 'plan' ? 'ridge_lag' : 'seasonal_mean', training_days: Number(next.training),
    });
    setPlan(result);
    const resolved = { ...next, warehouse: result.warehouse_id, sku: result.selected_sku };
    setDraft(resolved); setApplied(resolved);
    setProposal({ quantity: 0, arrival: result.start, multiplier: 100, delay: 0, quantile: 'p50' });
    if (nextView) setView(nextView);
  }
  function inspect(p: PlanProduct) { void perform('Đang học ML và đối chiếu nguồn hàng', () => load({ ...applied!, sku: p.sku }, 'plan')); }
  function showEvidence(key: string) {
    if (key === 'scenario' && plan) {
      setExplanation({title:'So sánh kịch bản từ dự báo ML', meaning:'Tách tác động của nhu cầu/lịch hàng thay đổi với tác động của bổ sung hàng, trên cùng một dữ liệu chụp tại thời điểm so sánh.', applications:plan.explanations.balance?.applications,
        formula:'Dự báo gốc dùng P50 ML và lịch nhập gốc. Hai phương án còn lại cùng mức nhu cầu và số ngày hàng về trễ. Chỉ phương án bổ sung nhận thêm hàng.',
        notes:['ML được học từ lịch sử của đúng kho, SKU và đơn vị. Không chuyển dự báo toàn doanh nghiệp vào một kho.', 'Mức tăng/giảm áp vào lượng đặt mới dự báo. Không đổi đơn đã nhận và không suy ra số khách hàng riêng biệt.', 'Mức bổ sung tối thiểu bù phần thiếu nguồn cung lớn nhất tại từng sự kiện. Hàng bổ sung được giả định sẵn sàng từ đầu ngày, hoặc lúc chụp dữ liệu nếu là hôm nay.', 'Ngày có thời điểm thiếu tính cả hạn đơn trong ngày. Nhận hàng sau hạn không xóa đơn đã trễ.', 'Mức nhu cầu cao là giả định từ sai lệch lịch sử, chưa phải xác suất đảm bảo.', 'Biên lượng đặt lấy mức nhỏ nhất qua các thời điểm: (nguồn hàng lũy kế trừ đơn đã nhận đến hạn) chia lượng ML gốc lũy kế. Không có ngưỡng nếu đơn đã nhận đã thiếu nguồn cung hoặc ML gốc không có lượng đặt.', 'Chỉ mặt hàng đang chọn có lượng đặt mới dự báo. Các mặt hàng khác chỉ xét phần đơn đã nhận.', 'Chưa xét giá mua, ngân sách, hạn sử dụng, MOQ hay thời gian cung ứng. Đây không phải tối ưu chi phí.', 'Độ trễ đang xét là hàng nhập về kho. Lịch giao nhận với khách và nhà vận chuyển cần đối chiếu riêng.'],
        start:plan.start,end:plan.end,read_at:typeof scenario?.evidence.snapshot_at==='string' ? scenario.evidence.snapshot_at : plan.as_of,last_observation:plan.source_end,technical:scenario?.evidence || plan.forecast.evidence});
      return;
    }
    if (key === 'sensitivity' && scenario?.sensitivity && plan) {
      setExplanation({
        title: 'Ma trận lượng nhập và ngày hàng về',
        meaning: 'Tính thử từng cặp lượng nhập và ngày về trên cùng nguồn hàng, đơn đang chờ và giả định nhu cầu đã so sánh.',
        applications: plan.explanations.balance?.applications,
        items: [
          { label: 'Lượng đặt mới trong giả định đã tính', value: scenario.baseline.metrics.forecast_demand, unit: scenario.sensitivity.unit },
          { label: 'Số lượng nhập thử', value: scenario.sensitivity.quantities.length },
          { label: 'Số ngày về thử', value: scenario.sensitivity.dates.length },
          { label: 'Số phương án độc lập', value: scenario.sensitivity.cells.length },
        ],
        formula: 'Mỗi ô tính lại tiến độ hàng về và hạn từng đơn. Lấy mức thiếu nguồn hàng lớn nhất tại các thời điểm và đếm đơn không đủ hàng trước hạn.',
        notes: ['Mỗi ô là một phương án riêng. Không cộng lượng nhập từ các ô với nhau.', 'Các ô giữ cùng nhu cầu, độ trễ và dữ liệu của lần so sánh. Không học lại dự đoán hay đọc thêm nguồn cho từng ô.', 'Mức lượng thử được lấy quanh mức thiếu và lượng bạn nhập. Không tối ưu chi phí mua hàng.', 'Hàng về sau hạn không xóa việc đơn đã trễ. Lịch vận chuyển cần kiểm tra riêng.'],
        start: scenario.baseline.series[0]?.day, end: scenario.baseline.series.at(-1)?.day,
        read_at: typeof scenario.evidence.snapshot_at === 'string' ? scenario.evidence.snapshot_at : undefined,
        last_observation: plan.source_end, technical: scenario.evidence,
      });
    } else if (plan?.explanations[key]) setExplanation({ ...plan.explanations[key], technical: plan.evidence });
  }
  const evidenceButton = (key: string) => <Button variant="ghost" size="sm" className="pw-text-button" onClick={() => showEvidence(key)}>Nguồn & cách tính</Button>;
  async function compare(save = false) {
    if (!plan) return;
    const body = { forecast_run_id: plan.forecast.id, sku: plan.selected_sku, warehouse_id: plan.warehouse_id, stock: productRef(sources.stock!), inbound: productRef(sources.inbound!), open_orders: productRef(sources.open_orders!), commitments: productRef(sources.commitments!), ...(sources.preparation ? { preparation: productRef(sources.preparation) } : {}), additional_quantity: proposal.quantity, arrival_date: proposal.arrival, ...(proposal.arrivalTime ? { arrival_at: `${proposal.arrival}T${proposal.arrivalTime}:00+07:00` } : {}), demand_multiplier: proposal.multiplier / 100, demand_quantile: proposal.quantile, inbound_delay_days: proposal.delay, include_sensitivity: true, require_ml: true, save };
    let result: Scenario;
    try { result = await request<Scenario>('/scenarios', { ...body, ...(snapshot.current ? { snapshot_id: snapshot.current } : {}) }); }
    catch (e) {
      if (save || !(e instanceof ApiError) || e.status !== 409 || !snapshot.current) throw e;
      snapshot.current = null; result = await request<Scenario>('/scenarios', body);
    }
    snapshot.current = typeof result.evidence.snapshot_id === 'string' ? result.evidence.snapshot_id : null;
    setScenario(result); setCalculated({ ...proposal });
    if (save) setNotice('Đã lưu phương án để xem lại. Đề xuất chưa tạo đơn mua hoặc phiếu nhập.');
  }
  function exportProposal() {
    if (!scenario || !calculated || !plan || !selected) return;
    const decision = scenario.decision;
    const canonical = scenarioSummary(scenario);
    const decisionLines = decision ? [
      'DỰ BÁO ML VÀ BA KỊCH BẢN',
      ...([['ML gốc', decision.reference], ['Sau thay đổi nhu cầu/hàng về', decision.stressed], ['Có bổ sung hàng', decision.proposed]] as const).map(([label, value]) => `${label}: thiếu nhiều nhất ${fmt(value.peak_shortage)}; ${value.shortage_days} ngày có lúc thiếu. Hàng còn cuối kỳ ${fmt(value.ending_inventory)}. Đơn thiếu trước hạn ${fmt(value.known_orders_at_risk)}.`),
      `Mức bổ sung để hết thiếu nguồn hàng: ${new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 3 }).format(Math.ceil(decision.minimum_additional_quantity))} ${unitLabel(selected.uom)}. Từ đầu ngày ${dateLabel(decision.latest_arrival_date)}.`,
      'Kết quả chưa xét giá mua, MOQ, ngân sách hay cam kết nhà cung cấp. Độ trễ là hàng nhập về kho.',
      '',
    ] : [];
    const text = ['ĐỀ XUẤT PHỐI HỢP NHẬP HÀNG', ...decisionLines, `Sản phẩm: ${selected.name || selected.sku} (${selected.sku})`, `Kho: ${plan.warehouses.find(w => w.id === plan.warehouse_id)?.name || plan.warehouse_id}`, `Kỳ: ${dateLabel(plan.start)} – ${dateLabel(plan.end)}. Dữ liệu đọc lúc ${plan.as_of}`, `Nhập thêm: ${fmt(calculated.quantity)} ${unitLabel(selected.uom)}. Hàng về ${dateLabel(calculated.arrival)}${calculated.arrivalTime ? ` lúc ${calculated.arrivalTime} (giờ Việt Nam)` : ' (giả định đầu ngày)'}`, `Nhu cầu: ${calculated.multiplier}% ước tính. Mức ${calculated.quantile === 'p90' ? 'cao hơn' : 'thông thường'}. Kế hoạch cũ trễ ${calculated.delay} ngày`, `Thiếu nhiều nhất: ${fmt(canonical.before)} → ${fmt(canonical.after)} ${unitLabel(selected.uom)}`, `Đơn thiếu hàng trước hạn (toàn kho): ${fmt(scenario.baseline.metrics.known_orders_at_risk)} → ${fmt(scenario.proposed.metrics.known_orders_at_risk)}`, '', 'Kho/mua hàng: xác nhận nhà cung cấp, lượng và giờ hàng về.', 'Bán hàng: rà các đơn có hạn gần, thống nhất lại cam kết với khách nếu cần.', 'Vận chuyển: chốt lịch nhận hàng phù hợp hạn giao.', 'Đề xuất từ mô phỏng, chưa được phê duyệt và chưa tạo giao dịch. Ước tính có thể khác thực tế.'].join('\n');
    const url = URL.createObjectURL(new Blob(['\uFEFF' + text], { type: 'text/plain;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = `de-xuat-${selected.sku.replace(/[^a-zA-Z0-9_-]/g, '_')}.txt`; a.click(); URL.revokeObjectURL(url);
  }
  const forecastPoints = plan?.forecast.points.filter(p => p.item_id === plan.selected_sku).map(p => ({ day: p.timestamp, p50: p.p50, band: [p.p10, p.p90], known: plan.baseline.series.find(s => s.day === p.timestamp)?.known_demand_due ?? null, inbound: plan.baseline.series.find(s => s.day === p.timestamp)?.inbound ?? null })) || [];
  const hasPlannedInbound = forecastPoints.some(p => (p.inbound || 0) > 0);
  const hasKnownDue = forecastPoints.some(p => (p.known || 0) > 0);
  const selectedInbound = plan?.inbound.filter(p => p.sku === plan.selected_sku) || [];
  const assumptionBases = { p50: plan?.forecast.points.filter(p => p.item_id === plan.selected_sku).reduce((n, p) => n + p.p50, 0) || 0, p90: plan?.forecast.points.filter(p => p.item_id === plan.selected_sku).reduce((n, p) => n + p.p90, 0) || 0 };

  return <div className="dw-shell pw-shell">
    <aside className="dw-sidebar"><Brand/>
      <p className="dw-nav-label">Quy trình quyết định</p>
      <nav aria-label="Quy trình quyết định">{([[ 'dashboard', BarChart3, 'Cần xử lý' ], [ 'plan', Package, 'Chi tiết & phương án' ], ...(decisionCapabilities?.enabled ? [[ 'tasks', Package, 'Việc của tôi' ], [ 'outcomes', Truck, 'Theo dõi kết quả' ]] : [])] as const).map(([id, Icon, label]) => <button key={String(id)} aria-current={view===id?'page':undefined} className={view===id?'active':''} onClick={()=>setView(id as View)}><Icon size={18}/><span>{String(label)}</span></button>)}</nav>
      <p className="dw-nav-label">Dữ liệu và công cụ</p>
      <nav aria-label="Dữ liệu và công cụ">{([[ 'forecast', ChartLine, 'Dự báo nhu cầu' ], [ 'orders', Truck, 'Đơn hàng & giao nhận' ], ...(decisionCapabilities?.enabled ? [[ 'decisions', Package, 'Hồ sơ quyết định' ]] : []), ...(admin ? [[ 'configuration', Settings2, 'Thiết lập cho IT' ]] : [])] as const).map(([id, Icon, label]) => <button key={String(id)} aria-current={view===id?'page':undefined} className={view===id?'active':''} onClick={()=>setView(id as View)}><Icon size={18}/><span>{String(label)}</span></button>)}</nav>
      <div className="dw-user"><span className="dw-avatar">{session.profile?.user.display_name?.[0] || 'F'}</span><span>{session.profile?.user.display_name || 'Người dùng'}<small>{admin ? 'Quản trị dữ liệu' : 'Điều hành doanh nghiệp'}</small></span></div><button className="pw-logout" onClick={() => void session.logout()}>Đăng xuất</button>
    </aside>
    <main className="dw-main" ref={mainRef}>
      <div className="dw-content"><div className="dw-page-heading"><div><h1>{title}</h1></div>{view !== 'configuration' && <Button variant="outline" disabled={!!busy} onClick={() => void perform('Đang mở phương án đã lưu', async () => { const [s, f] = await Promise.all([request<{ items: Scenario[] }>('/scenarios/saved'), request<{ items: Forecast[] }>('/forecasts/saved')]); setSaved(s.items); setSavedForecasts(f.items); setSavedForecast(null); setSavedOpen(true); })}>Đã lưu</Button>}</div>
        {error && <div className="dw-alert" role="alert">{view !== 'forecast' && <Info size={19}/>}<p>{error}</p></div>}{notice && <div className="dw-notice" role="status">{notice}</div>}
        {catalogLoaded && !products.length && !error && <div className="pw-source-setup"><Info size={24}/><div><h2>Chưa có dữ liệu được chia sẻ</h2><p>Liên hệ IT để được cấp quyền xem dữ liệu Bán hàng, Kho hoặc Vận chuyển.</p></div></div>}
        {(view === 'dashboard' || view === 'analytics') && <Tabs value={view} onValueChange={v => setView(v as View)} className="pw-overview-tabs"><TabsList><TabsTrigger value="dashboard">Đặt hàng & nguồn hàng</TabsTrigger><TabsTrigger value="analytics">Phân tích lịch sử</TabsTrigger></TabsList></Tabs>}
        {products.length > 0 && <div hidden={view !== 'analytics' && view !== 'forecast'}><BusinessAnalytics mode={view === 'forecast' ? 'forecast' : 'history'} products={products} busy={!!busy} request={request} perform={perform} onEvidence={setExplanation} canPlan={!missing.length} names={sourceNames} onPlan={(sku, horizon, training = 180) => { const nextView = sku ? 'plan' : 'orders'; if (!draft.warehouse) { setDraft(d => ({ ...d, sku, horizon: String(horizon), training: String(training) })); setView(nextView); return; } void perform('Đang đối chiếu kế hoạch', () => load({ ...draft, sku, horizon: String(horizon), training: String(training) }, nextView)); }}/></div>}
        {['decisions','tasks','outcomes'].includes(view) && decisionCapabilities?.enabled && <DecisionDossiers handoffOptions={plan?.capacity_calendar||[]} key={view} mode={view==='tasks'?'tasks':view==='outcomes'?'outcomes':'all'} request={request} capabilities={decisionCapabilities} scenario={scenario} scenarioDirty={dirty || scenarioDirty}/>}
        {!['decisions','tasks','outcomes'].includes(view) && view !== 'configuration' && view !== 'analytics' && view !== 'forecast' && <>
          <section className={`dw-filterbar pw-filters${view === 'plan' ? ' pw-ml-filters' : ''}`}><Field title="Kho phân tích" hint="Chỉ đối chiếu đơn hàng và nguồn hàng của kho đã chọn trong phạm vi bạn được cấp quyền."><Select value={draft.warehouse} onChange={warehouse => setDraft({ ...draft, warehouse, sku: directory.products.find(p => p.warehouse_id === warehouse)?.sku || '' })} disabled={!!busy || !directoryLoaded}><option value="" disabled>{!directoryLoaded ? 'Đang tải danh sách kho…' : 'Chọn kho'}</option>{directory.warehouses.map(w => <option key={w.id} value={w.id}>{warehouseLabel(w)}</option>)}</Select></Field><Field title="Kỳ kế hoạch" hint="Khoảng thời gian từ hôm nay để đối chiếu đơn đến hạn, hàng sẽ về và lượng đặt mới dự báo. Chọn kỳ dài hơn để chuẩn bị sớm."><Select value={draft.horizon} onChange={v => patch('horizon', v)} disabled={!!busy}>{[...new Set(['7', '14', '30', '90', draft.horizon])].sort((a, b) => Number(a) - Number(b)).map(n => <option key={n} value={n}>{n} ngày tới</option>)}</Select></Field>{view === 'dashboard' && <Field title="Xem đặt hàng và nhập kho trong" hint="Khoảng dữ liệu đã ghi nhận dùng cho biểu đồ đặt hàng và thực nhập. Đây là lịch sử, không phải kỳ dự báo."><Select value={draft.history} onChange={v => patch('history', v)} disabled={!!busy}>{['7', '14', '30', '90', '365'].map(n => <option key={n} value={n}>{n === '365' ? '12 tháng' : n + ' ngày gần nhất'}</option>)}</Select></Field>}{view === 'dashboard' && <Field title="Gộp theo" hint="Gộp số liệu theo từng ngày hoặc tổng từng tuần. Tuần đầu và cuối có thể chưa đủ bảy ngày."><Select value={draft.frequency} onChange={v => patch('frequency', v)} disabled={!!busy}><option value="day">Ngày</option><option value="week">Tuần</option></Select></Field>}{view === 'plan' && <Field title="Dữ liệu học ML" hint="Lịch sử của đúng sản phẩm và kho dùng để học dự báo. Kỳ dự báo dài cần nhiều dữ liệu hơn để kiểm chứng."><Select value={draft.training} disabled={!!busy} onChange={v => patch('training', v)}><option value="90">3 tháng</option><option value="180">6 tháng</option><option value="365">12 tháng</option></Select></Field>}<Field title="Sản phẩm" hint="Tìm và chọn theo mã hoặc tên. Các kết quả dùng cùng sản phẩm và đơn vị của kho đã chọn."><ProductSelector items={directory.products.filter(p => p.warehouse_id === draft.warehouse)} value={draft.sku} onChange={v => patch('sku', v)} disabled={!draft.warehouse || !directoryLoaded || !!busy}/></Field><Button disabled={blocked} onClick={() => void perform('Đang đối chiếu dữ liệu các phòng ban', () => load())}>{view === 'plan' ? 'Cập nhật dự báo ML' : 'Cập nhật kế hoạch'}</Button>{dirty && <span className="pw-draft-note">Có thay đổi chưa áp dụng</span>}{!directoryLoaded && !busy && catalogLoaded && sources.stock && <Button variant="outline" onClick={() => void perform('Đang tải danh sách kho', () => loadDirectory())}>Tải lại danh sách kho</Button>}</section>
          {catalogLoaded && !!missing.length && <div className="pw-source-setup" role="status"><Info size={24}/><div><h2>Cần bổ sung nguồn để phân tích đúng kho và từng đơn hàng</h2><p>Nhờ IT công bố hoặc cấp quyền các nguồn sau: {missing.map(k => sourceRoles[k][1]).join(', ')}. Báo cáo chỉ tính khi có đủ các nguồn bắt buộc.</p>{admin && <details><summary>Tên giao diện dữ liệu cần kết nối</summary><ul>{missing.map(k => <li key={k}>{sourceRoles[k][0]}</li>)}</ul></details>}</div></div>}
          {!plan && !busy && !error && !missing.length && <Card className="pw-empty-state"><CardContent><h2>Chưa có kế hoạch được tải</h2><p>Chọn kho và kỳ kế hoạch, sau đó bấm {view === 'plan' ? 'Cập nhật dự báo ML' : 'Cập nhật kế hoạch'} để đối chiếu đơn hàng và nguồn hàng.</p></CardContent></Card>}
          {plan && <><div className="pw-applied-context"><span>{plan.warehouses.find(w => w.id === plan.warehouse_id)?.name || 'Kho đang phân tích'}</span><span>Kế hoạch {dateLabel(plan.start)} – {dateLabel(plan.end)}</span></div>
            {!!plan.missing_sources.length && <p className="pw-data-warning">Chưa có {plan.missing_sources.map(k => sourceRoles[k as keyof typeof sourceRoles]?.[1] || k).join(', ')}. Phần chưa có dữ liệu được để trống, không tính là 0.</p>}
            {view === 'dashboard' && <OperationsDashboard plan={plan} selected={selected} filter={filter} onFilter={setFilter} onInspect={inspect} onOrders={() => setView('orders')} disabled={blocked || dirty} evidence={evidenceButton} onTasks={decisionCapabilities?.enabled?()=>setView('tasks'):undefined} onOutcomes={decisionCapabilities?.enabled?()=>setView('outcomes'):undefined}/>}
            {view === 'plan' && selected && <>
              {!hasML && <p className="pw-data-warning">Chọn Cập nhật dự báo ML để học từ lịch sử của kho này trước khi so sánh.</p>}
              <div className="pw-product-heading"><div><span className={`pw-status pw-${selected.priority}`}>{priorityLabel[selected.priority]}</span><h2>{selected.sku}{selected.name ? " - " + selected.name : ""}<small>{selected.sku} · {unitLabel(selected.uom)}</small></h2></div><div className="pw-input-actions"><Button variant="outline" size="sm" onClick={() => setInputsOpen(true)}>Xem dữ liệu đầu vào</Button>{evidenceButton('balance')}</div></div>
              <div className="pw-number-strip"><div><span>Chưa giữ cho đơn khác</span><strong>{fmt(selected.available)}</strong><button onClick={() => showEvidence('stock')}>Cách tính</button></div><div><span>Đơn đang chờ phân bổ</span><strong>{fmt(selected.known_quantity)}</strong></div><div><span>Lượng đặt mới dự báo</span><strong>{fmt(selected.forecast_quantity)}</strong></div><div><span>Hàng sẽ về trong kỳ</span><strong>{fmt(selected.incoming_quantity)}</strong></div></div>

              <div className="dw-plan-layout pw-plan-layout"><section className="dw-controls dw-panel"><h2>Phương án bổ sung</h2><p className="dw-muted">Thử lượng nhập và thời điểm hàng về trên cùng dự báo ML của kho.</p><Field title={`Nhập thêm (${unitLabel(selected.uom)})`}><Input aria-label="Số lượng nhập thêm" type="text" inputMode="numeric" pattern="[0-9]*" autoComplete="off" value={Number.isFinite(proposal.quantity) ? proposal.quantity : ''} onChange={e => { if (/^[0-9]*$/.test(e.target.value)) setProposal({ ...proposal, quantity: e.target.value === '' ? Number.NaN : Number(e.target.value) }); }}/></Field><Field title="Ngày dự kiến hàng về" hint="Ngày lượng nhập thêm có thể sẵn sàng trong kho. Đây là giả định để tính thử, chưa phải xác nhận từ nhà cung cấp."><Input type="date" min={plan.start} max={plan.end} value={proposal.arrival} onChange={e => setProposal({ ...proposal, arrival: e.target.value })}/></Field><Field title="Giờ dự kiến hàng về (giờ Việt Nam)"><Input type="time" value={proposal.arrivalTime || ""} onChange={e => setProposal({ ...proposal, arrivalTime: e.target.value || undefined })}/></Field><p className="dw-muted">Nếu để trống giờ, mô phỏng giả định hàng sẵn sàng từ đầu ngày, hoặc lúc đọc dữ liệu nếu là hôm nay. Giờ nhập là giả định, chưa phải xác nhận của Kho.</p><ScenarioAssumptions value={proposal} bases={assumptionBases} unit={selected.uom} disabled={!!busy || dirty || !hasML} onApply={setProposal}/><Button disabled={!!busy || dirty || !hasML || !Number.isInteger(proposal.quantity) || proposal.quantity < 0 || proposal.quantity > 1e9 || !proposal.arrival || proposal.arrival < plan.start || proposal.arrival > plan.end || !Number.isFinite(proposal.multiplier) || proposal.multiplier < 0 || proposal.multiplier > 500 || !Number.isInteger(proposal.delay) || proposal.delay < 0 || proposal.delay > 90} onClick={() => void perform('Đang so sánh phương án', () => compare())}>So sánh phương án</Button><p className="dw-muted">Chỉ tính khi bấm. Chưa tạo đơn mua hoặc thay đổi tồn kho.</p>{scenario && scenarioDirty && <p className="pw-data-warning">Phương án đã sửa. So sánh lại trước khi lưu hoặc tải đề xuất.</p>}</section><ScenarioComparison value={scenario} forecast={plan.forecast} sku={plan.selected_sku} unit={selected.uom} proposal={calculated} dirty={dirty || scenarioDirty} busy={!!busy} onSuggest={(quantity, arrival, arrivalTime) => setProposal(p => ({ ...p, quantity, arrival, arrivalTime }))} onSave={() => void perform('Đang lưu phương án', () => compare(true))} onExport={exportProposal} evidence={evidenceButton('scenario')}/></div>
            </>}
            {view === 'plan' && scenario && decisionCapabilities?.enabled && <CreateDossier handoffOptions={plan.capacity_calendar} request={request} scenario={scenario} warehouse={plan.warehouse_id} capabilities={decisionCapabilities} disabled={!!busy || dirty || scenarioDirty} onCreated={() => { setView('decisions'); setNotice('Đã lưu hồ sơ. Mở hồ sơ để xem trạng thái hiện tại và các bước cần thực hiện.'); }}/>}
            {view === 'plan' && scenario?.sensitivity && <Panel title="Ma trận lượng nhập và ngày hàng về" action={evidenceButton('sensitivity')} subtitle="So sánh đồng thời lượng bổ sung, thời điểm hàng về, lượng còn thiếu và số đơn có nguy cơ trễ"><SensitivityChart value={scenario.sensitivity} baselineShortage={(scenario.decision?.stressed.peak_shortage ?? scenario.baseline.metrics.peak_shortage)} quantity={proposal.quantity} arrival={proposal.arrival} disabled={!!busy || dirty || !!calculated && (proposal.multiplier !== calculated.multiplier || proposal.quantile !== calculated.quantile || proposal.delay !== calculated.delay)} onSelect={(quantity, arrival) => { setProposal(p => ({ ...p, quantity, arrival, arrivalTime: undefined })); setNotice('Đã điền từ ma trận. Bấm So sánh phương án để cập nhật kết quả trước khi lưu.'); }}/><p className="pw-panel-note">Ma trận thuộc lần so sánh đã tính: {calculated?.multiplier}% mức {calculated?.quantile === 'p90' ? 'chuẩn bị cao hơn' : 'dự kiến'}, hàng đang chờ trễ {calculated?.delay || 0} ngày. Đổi giả định nhu cầu hoặc độ trễ thì bấm So sánh phương án để tạo ma trận mới.</p></Panel>}
            {view === 'orders' && <>
              <Panel title="Đơn hàng và tiến độ đáp ứng" subtitle="Theo dõi khả năng đáp ứng và lịch bàn giao dự kiến. Kết quả tương lai là nguy cơ theo kế hoạch, chưa phải giao sớm hay trễ thực tế."><div className="dw-table-scroll"><table><thead><tr><th>Đơn / sản phẩm</th><th>Hạn bàn giao cho vận chuyển</th><th>Vấn đề cần xử lý</th><th>Lịch bàn giao dự kiến</th><th>Phòng ban phối hợp</th></tr></thead><tbody>{plan.orders.map(o => <tr key={o.order_id}><td><strong>{o.order_number}</strong>{o.products.map(p => <small key={p.sku}>{p.name || p.sku} · {p.sku}</small>)}</td><td>{dateLabel(o.deadline)}<small>{new Date(o.deadline).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Ho_Chi_Minh' })}{o.already_overdue ? ' · Đã qua hạn' : ''}</small>{o.readiness && <small>{o.readiness.stock_ready_by ? 'Hàng sẵn sàng trước ' + new Date(o.readiness.stock_ready_by).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' }) : 'Chưa có thời gian chuẩn bị được Kho xác nhận'}</small>}</td><td><span className={`pw-status ${o.at_risk || o.schedule_status === 'late' ? 'pw-urgent' : 'pw-watch'}`}>{orderReason[o.reason] || 'Cần kiểm tra'}</span>{o.readiness && <small>{o.readiness.status === 'unknown' ? 'Chưa xác minh đủ điều kiện bàn giao' : o.readiness.status === 'not_met' ? 'Còn điều kiện chưa đáp ứng' : 'Có thể đáp ứng theo điều kiện kế hoạch; chưa phải đã bàn giao'}</small>}{o.shortages_at_deadline.map(p => <small key={p.sku}>{p.sku}: thiếu {fmt(p.missing_quantity)} {unitLabel(p.uom)}</small>)}</td><td>{o.shipments.length ? o.shipments.map(s => <div key={s.shipment_number}><strong>{s.carrier_name || s.carrier_code}</strong><small>{s.shipment_number} · {dateLabel(s.scheduled_handoff_at)}</small></div>) : 'Chưa có lịch được chia sẻ'}</td><td>{o.at_risk ? 'Kho/mua hàng xác nhận bổ sung. Bán hàng rà cam kết' : o.schedule_status === 'booked' ? 'Kho chuẩn bị hàng theo lịch' : 'Bán hàng & Vận chuyển chốt lịch nhận'}</td></tr>)}</tbody></table>{!plan.orders.length && <p className="pw-empty-inline">Không có đơn đang chờ trong kỳ được chia sẻ.</p>}</div></Panel>
              <Panel title="Lịch tiếp nhận của nhà vận chuyển" subtitle="Lượt nhận hàng công bố cho toàn doanh nghiệp · không đổi số sản phẩm thành số chuyến"><div className="dw-table-scroll"><table><thead><tr><th>Ngày</th><th>Nhà vận chuyển</th><th>Tối đa (lượt)</th><th>Đã đặt (lượt)</th><th>Còn nhận (lượt)</th><th>Gợi ý phối hợp</th></tr></thead><tbody>{plan.capacity_calendar.map(r => <tr key={r.day + r.carrier_code}><td>{dateLabel(r.day)}</td><td>{r.carrier_name || r.carrier_code}<small>{r.carrier_code}</small></td><td>{fmt(r.maximum)}</td><td>{fmt(r.booked)}</td><td>{fmt(r.remaining)}</td><td>{r.booked > r.maximum ? 'Vượt lịch công bố · cần điều phối lại' : r.remaining > 0 ? 'Có thể trao đổi thêm lịch nhận' : 'Cần hỏi lịch khác hoặc đơn vị khác'}</td></tr>)}</tbody></table>{!plan.capacity_calendar.length && <p className="pw-empty-inline">Chưa có lịch công bố trong kỳ. Chưa thể kết luận còn nhận thêm hay không.</p>}</div><p className="pw-panel-note">Một lượt là một lần tiếp nhận bàn giao, không phải một sản phẩm. Ngày chưa công bố lịch có năng lực chưa biết.</p></Panel>
            </>}
          </>}
        </>}
        {view === 'configuration' && admin && products.length > 0 && <><Configuration products={products} request={request} onError={report} onEvidence={e => setExplanation({ title: 'Kiểm tra cấu hình', meaning: 'Chi tiết cấu hình và dữ liệu thử dành cho IT.', technical: e })} onUse={r => void perform('Đang đọc báo cáo tùy chỉnh', async () => { const p = products.find(p => p.id === r.config.data_product_id)!; setCustom(await request<Overview>('/overview', { ...productRef(p), configuration_id: r.id, days: 30 })); })}/>{custom && <Panel title={custom.config.name} subtitle="Báo cáo tùy chỉnh · cấu hình của IT"><Plot data={custom.analytics.series.map(p => ({ day: p.timestamp, target: p.target }))} unit={unitLabel(custom.config.unit)} lines={[{ key: 'target', name: 'Giá trị theo cấu hình', color: blue }]}/></Panel>}</>}
        {busy && <div className="dw-progress pw-progress" role="status"><LoaderCircle className="animate-spin" size={16}/>{busy}…{catalogLoaded && <button className="pw-text-button" onClick={cancel}>Dừng chờ</button>}</div>}
      </div>
    </main>
    {plan && selected && <Sheet open={inputsOpen} onOpenChange={setInputsOpen}><SheetContent className="pw-inputs-sheet w-full sm:max-w-2xl"><SheetHeader><SheetTitle>Nguồn hàng và nhu cầu đầu vào</SheetTitle><SheetDescription>Dự báo ML gốc, đơn đang chờ và lịch hàng về trước khi áp dụng giả định.</SheetDescription></SheetHeader><div className="pw-inputs-body"><Panel title="Lịch hàng về và nhu cầu theo ngày" subtitle="Cùng sản phẩm, cùng kho · đối chiếu từng ngày để chọn thời điểm bổ sung"><Plot data={forecastPoints} unit={unitLabel(selected.uom)} lines={[{ key: 'band', name: 'Vùng dao động', color: blue }, { key: 'p50', name: 'Lượng đặt dự kiến mỗi ngày', color: blue, dash: '5 4' }, ...(hasPlannedInbound ? [{ key: 'inbound', name: 'Hàng sẽ về theo kế hoạch', color: cyan, kind: 'bar' as const }] : []), ...(hasKnownDue ? [{ key: 'known', name: 'Đơn đã nhận đến hạn, chưa phân bổ', color: purple, kind: 'bar' as const }] : [])]}/><p className="pw-panel-note">{!hasPlannedInbound && <>Không có hàng dự kiến về trong kỳ được đưa vào tính toán. </>}Cột hàng về và đơn đến hạn là lượng trong từng ngày. Đường xanh là lượng khách có thể đặt thêm. Tồn đầu kỳ đã được tính trong biểu đồ đủ/thiếu bên dưới. Vùng nhạt cho thấy mức thấp–cao từ lịch sử. Lượng đặt thực tế có thể nằm ngoài vùng này. Khuyến mại hoặc khách đặt đột biến cần thử thêm ở phần giả định bên dưới.</p></Panel><Panel title="Các đợt hàng dự kiến về" subtitle="Chỉ tính vào nguồn cung từ đúng thời điểm dự kiến nhận"><div className="pw-arrivals">{selectedInbound.length ? selectedInbound.map(p => <div key={p.plan_id}><CalendarDays size={18}/><span><strong>{fmt(p.quantity)} {unitLabel(p.uom)}</strong><small>Dự kiến {dateLabel(p.expected_at)}</small></span><span className={p.overdue ? 'pw-danger-text' : ''}>{p.overdue ? 'Quá ngày về · cần xác nhận' : 'Theo kế hoạch'}</span></div>) : <p>Chưa có kế hoạch hàng về được chia sẻ cho sản phẩm này.</p>}</div><p className="pw-panel-note">Kế hoạch chưa phải hàng đã nhận. Kế hoạch quá ngày về không tự được cộng vào hàng sẵn có.</p></Panel></div></SheetContent></Sheet>}
    <BusinessEvidence value={explanation} admin={admin} onClose={() => setExplanation(null)}/>
    <Sheet open={savedOpen} onOpenChange={setSavedOpen}><SheetContent><SheetHeader><SheetTitle>Dự báo và phương án đã lưu</SheetTitle><SheetDescription>Kết quả phản ánh dữ liệu tại thời điểm lưu. Cập nhật dữ liệu trước khi sử dụng cho kế hoạch mới.</SheetDescription></SheetHeader><div className="dw-saved">{!saved.length && !savedForecasts.length && <p>Chưa có kết quả đã lưu.</p>}{savedForecasts.map(f => <button key={f.id} disabled={!!busy} onClick={() => void perform('Đang kiểm tra quyền kết quả', async () => setSavedForecast(await request<Forecast>(`/forecasts/${f.id}`)))}><span>{f.config.entities[0] || f.config.name}<small>{dateLabel(f.created_at)} · {f.config.forecast_horizon} ngày</small></span></button>)}{savedForecast && <div><h3>Dự báo đã lưu</h3><Plot data={savedForecast.points.filter(p => p.item_id === savedForecast.config.entities[0]).map(p => ({ day: p.timestamp, predicted: p.p50, band: [p.p10, p.p90] }))} unit={unitLabel(savedForecast.config.unit)} lines={[{ key: 'band', name: 'Vùng dao động', color: blue }, { key: 'predicted', name: 'Lượng dự kiến', color: purple, dash: '5 4' }]}/><p className="dw-muted">Kết quả cũ dùng để tham khảo. Tạo dự báo mới để lập kế hoạch hiện tại.</p></div>}{saved.map(s => <div className="pw-saved-card" key={s.id}><strong>{String((s.evidence.assumptions as Record<string, unknown>)?.sku || 'Phương án nhập hàng')}</strong><p>Thiếu nguồn hàng nhiều nhất: {fmt(scenarioSummary(s).before)} → {fmt(scenarioSummary(s).after)} {scenarioSummary(s).unit ? unitLabel(scenarioSummary(s).unit!) : ""}</p>{scenarioSummary(s).legacy && <p role="status">Kết quả cũ chưa có chỉ tiêu thiếu nguồn theo giờ. Cần tính lại trước khi dùng.</p>}<p>Ngày thiếu nguồn: {dateLabel(scenarioSummary(s).firstShortageDate)}</p><small>{s.id?.slice(0, 8)}</small></div>)}</div></SheetContent></Sheet>
  </div>;
}
