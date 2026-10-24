// SPDX-License-Identifier: GPL-3.0-or-later
import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, BarChart3, CalendarDays, ChevronRight, ClipboardList, Download, History, Info, LoaderCircle, LogOut, Package, Save, Settings2, Truck } from 'lucide-react';
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
import { PlanningChart } from './planning-chart';
import { ReplenishmentPriorities } from './decision-charts';
import { SensitivityChart } from './sensitivity-chart';
import type { Planning, PlanProduct } from './planning-types';
import type { Forecast, Overview, Product, Scenario } from './types';
import './workspace.css';

type View = 'analytics' | 'dashboard' | 'plan' | 'orders' | 'configuration';
type Draft = { warehouse: string; sku: string; horizon: string; history: string };
type Proposal = { quantity: number; arrival: string; multiplier: number; delay: number; quantile: string };
const initialDraft: Draft = { warehouse: '', sku: '', horizon: '7', history: '30' };
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
} as const;
const required = ['demand', 'stock', 'inbound', 'open_orders', 'commitments'] as const;
const blue = 'var(--fb-brand-blue)', cyan = 'var(--fb-brand-cyan)', purple = 'var(--fb-brand-purple)';
const productRef = (p: Product) => ({ data_product_id: p.id, contract_version_id: p.contract_version_id });
const priorityLabel = { urgent: 'Cần xử lý', watch: 'Cần xác nhận hàng về', clear: 'Đủ theo ước tính', unknown: 'Chưa đủ dữ liệu' };
const orderReason: Record<string, string> = { stock: 'Chưa đủ hàng trước hạn', schedule_late: 'Lịch nhận hàng sau hạn giao', schedule_unconfirmed: 'Cần chốt lịch với vận chuyển', schedule_unknown: 'Chưa có dữ liệu lịch giao nhận', scheduled: 'Đã có lịch nhận hàng' };

export function DataWorkspace() {
  const session = useSession();
  const [view, setView] = useState<View>('analytics');
  const [products, setProducts] = useState<Product[]>([]);
  const [admin, setAdmin] = useState(false), [catalogLoaded, setCatalogLoaded] = useState(false);
  const [draft, setDraft] = useState(initialDraft), [applied, setApplied] = useState<Draft | null>(null);
  const [plan, setPlan] = useState<Planning | null>(null);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [filter, setFilter] = useState('all');
  const [proposal, setProposal] = useState<Proposal>({ quantity: 0, arrival: '', multiplier: 100, delay: 0, quantile: 'p50' });
  const [scenario, setScenario] = useState<Scenario | null>(null), [calculated, setCalculated] = useState<Proposal | null>(null);
  const [saved, setSaved] = useState<Scenario[]>([]), [savedOpen, setSavedOpen] = useState(false);
  const [savedForecasts, setSavedForecasts] = useState<Forecast[]>([]), [savedForecast, setSavedForecast] = useState<Forecast | null>(null);
  const [custom, setCustom] = useState<Overview | null>(null);
  const snapshot = useRef<string | null>(null), generation = useRef(0), operation = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  const mainRef = useRef<HTMLElement | null>(null);
  useEffect(() => { mainRef.current?.scrollTo({ top: 0 }); }, [view, plan?.selected_sku]);
  const request = useCallback(async <T,>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> => {
    const ticket = generation.current, controller = new AbortController();
    controllers.current.add(controller);
    try {
      const result = await session.api.request<T>('/api/v1/data-workspace' + path, { method: body === undefined ? 'GET' : 'POST', body, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(90000), ...(signal ? [signal] : [])]) });
      if (ticket !== generation.current) throw new DOMException('Selection changed', 'AbortError');
      return result;
    } catch (e) { if (e instanceof ApiError && e.status === 401) session.invalidate(); throw e; }
    finally { controllers.current.delete(controller); }
  }, [session.api, session.invalidate]);
  function report(e: unknown) {
    if (e instanceof DOMException && e.name === 'AbortError') return;
    setExplanation(null);
    if (e instanceof ApiError && [401, 403].includes(e.status)) {
      generation.current++; controllers.current.forEach(c => c.abort());
      setPlan(null); setScenario(null); setSaved([]); setSavedForecasts([]); setSavedForecast(null); setCustom(null); setProducts([]); setAdmin(false); setSavedOpen(false);
    }
    setError(e instanceof ApiError && e.status === 403 ? 'Quyền dữ liệu đã thay đổi. Liên hệ quản trị viên để kiểm tra rồi tải lại trang.'
      : e instanceof ApiError && e.status === 409 ? 'Số liệu so sánh đã hết hạn. Bấm So sánh phương án để tính lại trước khi lưu.'
      : e instanceof ApiError && (e.status === 422 && ['NO_DATA', 'STALE_HISTORY', 'INSUFFICIENT_HISTORY', 'DATASET_TOO_LARGE'].includes(e.code || '') || e.status === 504 && e.code === 'PLANNING_TIMEOUT') ? e.message
      : e instanceof DOMException && e.name === 'TimeoutError' ? 'Đối chiếu dữ liệu mất quá lâu. Bấm Cập nhật kế hoạch để thử lại.'
      : e instanceof ApiError && e.status === 422 ? 'Dữ liệu hoặc lựa chọn chưa phù hợp để tính toán. Kiểm tra kho, sản phẩm, đơn vị và khoảng ngày rồi thử lại.'
      : 'Chưa tải được nguồn dữ liệu. Kiểm tra kết nối rồi bấm Cập nhật kế hoạch để thử lại.');
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
  useEffect(() => {
    let active = true;
    void perform('Đang kiểm tra nguồn được chia sẻ', async () => {
      const result = await request<{ items: Product[]; can_configure: boolean }>('/products');
      if (active) { setProducts(result.items); setAdmin(result.can_configure); setCatalogLoaded(true); }
    });
    return () => { active = false; generation.current++; controllers.current.forEach(c => c.abort()); };
  }, [request]);

  const sources = Object.fromEntries(Object.entries(sourceRoles).map(([key, [name]]) => [key, products.find(p => p.name === name)]));
  const missing = required.filter(k => !sources[k]);
  const selected = plan?.products.find(p => p.sku === plan.selected_sku);
  const dirty = !!plan && JSON.stringify(draft) !== JSON.stringify(applied);
  const scenarioDirty = JSON.stringify(proposal) !== JSON.stringify(calculated);
  const shown = plan?.products.filter(p => filter === 'all' || filter === 'urgent' && p.priority === 'urgent' || filter === 'watch' && p.late_plan_ids.length > 0) || [];
  const blocked = !!busy || !!missing.length || !catalogLoaded;
  const title = view === 'analytics' ? 'Phân tích & dự đoán' : view === 'plan' ? 'Thử phương án nhập hàng' : view === 'orders' ? 'Đơn hàng & giao nhận' : view === 'configuration' ? 'Thiết lập cho IT' : 'Kế hoạch hàng hóa';
  function patch<K extends keyof Draft>(key: K, value: Draft[K]) { setDraft(d => ({ ...d, [key]: value })); }
  async function load(next = draft, nextView?: View) {
    generation.current++; controllers.current.forEach(c => c.abort());
    setPlan(null); setScenario(null); setCalculated(null); setExplanation(null); snapshot.current = null;
    const result = await request<Planning>('/planning', {
      ...Object.fromEntries(Object.entries(sources).filter(([, p]) => !!p).map(([k, p]) => [k, productRef(p!)])),
      warehouse_id: next.warehouse || null, sku: next.sku || null, horizon: Number(next.horizon), history_days: Number(next.history),
    });
    setPlan(result);
    const resolved = { ...next, warehouse: result.warehouse_id, sku: result.selected_sku };
    setDraft(resolved); setApplied(resolved);
    setProposal({ quantity: 0, arrival: result.start, multiplier: 100, delay: 0, quantile: 'p50' });
    if (nextView) setView(nextView);
  }
  function inspect(p: PlanProduct) { void perform('Đang phân tích sản phẩm', () => load({ ...applied!, sku: p.sku }, 'plan')); }
  function showEvidence(key: string) {
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
        formula: 'Mỗi ô tính lại tiến độ hàng về và hạn từng đơn; lấy lượng chưa đáp ứng lớn nhất qua các ngày và đếm đơn không đủ hàng trước hạn.',
        notes: ['Mỗi ô là một phương án riêng; không cộng lượng nhập từ các ô với nhau.', 'Các ô giữ cùng nhu cầu, độ trễ và dữ liệu của lần so sánh. Không học lại dự đoán hay đọc thêm nguồn cho từng ô.', 'Mức lượng thử được lấy quanh mức thiếu và lượng bạn nhập; không tối ưu chi phí mua hàng.', 'Hàng về sau hạn không xóa việc đơn đã trễ. Lịch vận chuyển cần kiểm tra riêng.'],
        start: scenario.baseline.series[0]?.day, end: scenario.baseline.series.at(-1)?.day,
        read_at: typeof scenario.evidence.snapshot_at === 'string' ? scenario.evidence.snapshot_at : undefined,
        last_observation: plan.source_end, technical: scenario.evidence,
      });
    } else if (plan?.explanations[key]) setExplanation({ ...plan.explanations[key], technical: plan.evidence });
  }
  const evidenceButton = (key: string) => <button className="pw-text-button" onClick={() => showEvidence(key)}><Info size={14}/>Nguồn & cách tính</button>;
  async function compare(save = false) {
    if (!plan) return;
    const body = { forecast_run_id: plan.forecast.id, sku: plan.selected_sku, warehouse_id: plan.warehouse_id, stock: productRef(sources.stock!), inbound: productRef(sources.inbound!), open_orders: productRef(sources.open_orders!), commitments: productRef(sources.commitments!), additional_quantity: proposal.quantity, arrival_date: proposal.arrival, demand_multiplier: proposal.multiplier / 100, demand_quantile: proposal.quantile, inbound_delay_days: proposal.delay, include_sensitivity: true, save };
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
    const text = ['ĐỀ XUẤT PHỐI HỢP NHẬP HÀNG', `Sản phẩm: ${selected.name || selected.sku} (${selected.sku})`, `Kho: ${plan.warehouses.find(w => w.id === plan.warehouse_id)?.name || plan.warehouse_id}`, `Kỳ: ${dateLabel(plan.start)} – ${dateLabel(plan.end)}; dữ liệu đọc lúc ${plan.as_of}`, `Nhập thêm: ${fmt(calculated.quantity)} ${unitLabel(selected.uom)}; hàng về ${dateLabel(calculated.arrival)}`, `Nhu cầu: ${calculated.multiplier}% ước tính; mức ${calculated.quantile === 'p90' ? 'cao hơn' : 'thông thường'}; kế hoạch cũ trễ ${calculated.delay} ngày`, `Thiếu nhiều nhất: ${fmt(scenario.baseline.metrics.peak_shortage)} → ${fmt(scenario.proposed.metrics.peak_shortage)} ${unitLabel(selected.uom)}`, `Đơn thiếu hàng trước hạn (toàn kho): ${fmt(scenario.baseline.metrics.known_orders_at_risk)} → ${fmt(scenario.proposed.metrics.known_orders_at_risk)}`, '', 'Kho/mua hàng: xác nhận nhà cung cấp, lượng và giờ hàng về.', 'Bán hàng: rà các đơn có hạn gần, thống nhất lại cam kết với khách nếu cần.', 'Vận chuyển: chốt lịch nhận hàng phù hợp hạn giao.', 'Đề xuất từ mô phỏng, chưa được phê duyệt và chưa tạo giao dịch. Ước tính có thể khác thực tế.'].join('\n');
    const url = URL.createObjectURL(new Blob(['\uFEFF' + text], { type: 'text/plain;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = `de-xuat-${selected.sku.replace(/[^a-zA-Z0-9_-]/g, '_')}.txt`; a.click(); URL.revokeObjectURL(url);
  }
  const forecastPoints = plan?.forecast.points.filter(p => p.item_id === plan.selected_sku).map(p => ({ day: p.timestamp, p50: p.p50, band: [p.p10, p.p90], known: plan.baseline.series.find(s => s.day === p.timestamp)?.known_demand_due ?? null, inbound: plan.baseline.series.find(s => s.day === p.timestamp)?.inbound ?? null })) || [];
  const hasPlannedInbound = forecastPoints.some(p => (p.inbound || 0) > 0);
  const hasKnownDue = forecastPoints.some(p => (p.known || 0) > 0);
  const balancePoints = (scenario?.baseline || plan?.baseline)?.series.map((p, i) => ({ day: p.day, baseline: p.net_position, ...(scenario ? { proposed: scenario.proposed.series[i]?.net_position } : {}) })) || [];
  const selectedInbound = plan?.inbound.filter(p => p.sku === plan.selected_sku) || [];
  const demandTotal = plan?.history.series.reduce((n, p) => n + p.confirmed, 0) || 0;
  const receiptsTotal = plan?.history.receipts_available ? plan.history.series.reduce((n, p) => n + (p.received || 0), 0) : null;
  const assumptionBase = plan?.forecast.points.filter(p => p.item_id === plan.selected_sku).reduce((n, p) => n + (proposal.quantile === 'p90' ? p.p90 : p.p50), 0) || 0;

  return <div className="dw-shell pw-shell">
    <aside className="dw-sidebar"><Brand/><div className="dw-workspace-label">PHỐI HỢP ĐỂ RA QUYẾT ĐỊNH</div><nav aria-label="Điều hướng">{([[ 'analytics', BarChart3, 'Biểu đồ & dự đoán' ], [ 'dashboard', ClipboardList, 'Kế hoạch hàng hóa' ], [ 'plan', Package, 'Phương án nhập hàng' ], [ 'orders', Truck, 'Đơn hàng & giao nhận' ], ...(admin ? [[ 'configuration', Settings2, 'Thiết lập cho IT' ]] : [])] as const).map(([id, Icon, label]) => <button key={String(id)} aria-current={view === id ? 'page' : undefined} className={view === id ? 'active' : ''} onClick={() => setView(id as View)}><Icon size={18}/><span>{String(label)}</span></button>)}</nav>
      <div className="pw-sidebar-note"><span>Bán hàng</span><ArrowRight size={12}/><span>Kho</span><ArrowRight size={12}/><span>Vận chuyển</span><p>Cùng dữ liệu. Rõ việc cần làm.</p></div>
      <div className="dw-user"><span className="dw-avatar">{session.profile?.user.display_name?.[0] || 'F'}</span><span>{session.profile?.user.display_name || 'Người dùng'}<small>{admin ? 'Quản trị dữ liệu' : 'Điều hành doanh nghiệp'}</small></span></div><button className="pw-logout" onClick={() => void session.logout()}><LogOut size={16}/>Đăng xuất</button>
    </aside>
    <main className="dw-main" ref={mainRef}><header className="dw-topbar"><span>Không gian quyết định<ChevronRight size={14}/>{title}</span><span className="pw-company-context">Bán hàng · Kho · Vận chuyển</span></header>
      <div className="dw-content"><div className="dw-page-heading"><div><span className="pw-eyebrow">KẾ HOẠCH & ĐÁP ỨNG ĐƠN HÀNG</span><h1>{title}</h1><p>{view === 'analytics' ? 'Hiểu diễn biến, ước tính ngày tới và đối chiếu kế hoạch để ra quyết định.' : view === 'dashboard' ? 'Biết nơi cần ưu tiên, hiểu nguyên nhân và chọn phương án trước khi cam kết.' : view === 'plan' ? 'Nếu nhập thêm hoặc hàng về trễ, khả năng đáp ứng khách hàng thay đổi ra sao?' : view === 'orders' ? 'Đối chiếu hàng sẵn có, hạn giao và lịch nhận hàng để phối hợp xử lý.' : 'Khai báo nguồn và kiểm tra dữ liệu dành cho người quản trị.'}</p></div>{view !== 'configuration' && <Button variant="outline" disabled={!!busy} onClick={() => void perform('Đang mở phương án đã lưu', async () => { const [s, f] = await Promise.all([request<{ items: Scenario[] }>('/scenarios/saved'), request<{ items: Forecast[] }>('/forecasts/saved')]); setSaved(s.items); setSavedForecasts(f.items); setSavedForecast(null); setSavedOpen(true); })}><History size={15}/>Đã lưu</Button>}</div>
        {error && <div className="dw-alert" role="alert"><Info size={19}/><p>{error}</p></div>}{notice && <div className="dw-notice" role="status">{notice}</div>}
        {catalogLoaded && !products.length && !error && <div className="pw-source-setup"><Info size={24}/><div><h2>Chưa có dữ liệu được chia sẻ</h2><p>Liên hệ IT để được cấp quyền xem dữ liệu Bán hàng, Kho hoặc Vận chuyển.</p></div></div>}
        {products.length > 0 && <div hidden={view !== 'analytics'}><BusinessAnalytics products={products} busy={!!busy} request={request} perform={perform} onEvidence={setExplanation} canPlan={!missing.length} onPlan={(sku, horizon) => void perform('Đang đối chiếu kế hoạch', () => load({ ...draft, sku, horizon: String(horizon) }, sku ? 'plan' : 'orders'))}/></div>}
        {view !== 'configuration' && view !== 'analytics' && <>
          <section className="dw-filterbar pw-filters"><Field title="Kho phân tích"><Select value={draft.warehouse} onChange={v => setDraft({ ...draft, warehouse: v, sku: '' })} disabled={!!busy}><option value="">Kho đầu tiên được chia sẻ</option>{plan?.warehouses.map(w => <option key={w.id} value={w.id}>{w.name || w.code || w.id}</option>)}</Select></Field><Field title="Chuẩn bị cho"><Select value={draft.horizon} onChange={v => patch('horizon', v)} disabled={!!busy}>{['7', '14', '30', '90'].map(n => <option key={n} value={n}>{n} ngày tới</option>)}</Select></Field>{view === 'dashboard' && <Field title="Xem đặt hàng và nhập kho trong"><Select value={draft.history} onChange={v => patch('history', v)} disabled={!!busy}>{['7', '14', '30', '90', '365'].map(n => <option key={n} value={n}>{n === '365' ? '12 tháng' : n + ' ngày gần nhất'}</option>)}</Select></Field>}<Field title="Sản phẩm"><ProductSelector items={plan?.products || []} value={draft.sku} onChange={v => patch('sku', v)} disabled={!plan || !!busy}/></Field><Button disabled={blocked} onClick={() => void perform('Đang đối chiếu dữ liệu các phòng ban', () => load())}>Cập nhật kế hoạch</Button>{dirty && <span className="pw-draft-note">Có thay đổi chưa áp dụng</span>}<p className="pw-filter-explainer">“Chuẩn bị cho” là những ngày sắp tới cần đủ hàng và giao đúng hẹn.{view === 'dashboard' && <> Khoảng đặt hàng và nhập kho chỉ dùng để nhìn lại việc đã ghi nhận, không phải số ngày dự đoán.</>}</p></section>
          {catalogLoaded && !!missing.length && <div className="pw-source-setup" role="status"><Info size={24}/><div><h2>Cần bổ sung nguồn để phân tích đúng kho và từng đơn hàng</h2><p>Nhờ IT công bố hoặc cấp quyền các nguồn sau: {missing.map(k => sourceRoles[k][1]).join('; ')}. Báo cáo chỉ tính khi có đủ các nguồn bắt buộc.</p>{admin && <details><summary>Tên giao diện dữ liệu cần kết nối</summary><ul>{missing.map(k => <li key={k}>{sourceRoles[k][0]}</li>)}</ul></details>}</div></div>}
          {!plan && !busy && !error && !missing.length && <div className="dw-welcome"><div className="dw-welcome-icon"><ClipboardList size={32}/></div><h2>Bắt đầu từ việc doanh nghiệp cần quyết định</h2><p>Chọn kho, sản phẩm và số ngày cần chuẩn bị, rồi bấm Cập nhật kế hoạch.<br/>Đối chiếu đơn hàng, tồn kho, hàng sẽ về và lịch giao nhận.</p><div className="pw-welcome-questions"><span>Hàng nào cần nhập thêm?</span><span>Đơn nào cần ưu tiên?</span><span>Nhập lúc nào mới kịp?</span></div></div>}
          {plan && <><div className="dw-context"><strong>{plan.warehouses.find(w => w.id === plan.warehouse_id)?.name || 'Kho đang phân tích'}</strong><span><CalendarDays size={14}/>{dateLabel(plan.start)} – {dateLabel(plan.end)}</span><span>Lịch sử đặt hàng đến {dateLabel(plan.source_end)}</span><span className="dw-source-age">Cập nhật lúc {new Date(plan.as_of).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Ho_Chi_Minh' })}</span></div>
            {plan.source_age_days > 1 && <p className="pw-data-warning">Lịch sử đặt hàng chậm {plan.source_age_days} ngày. Kiểm tra nguồn trước khi chốt lượng nhập.</p>}
            {!!plan.missing_sources.length && <p className="pw-data-warning">Chưa có {plan.missing_sources.map(k => sourceRoles[k as keyof typeof sourceRoles]?.[1] || k).join(', ')}. Phần chưa có dữ liệu được để trống, không tính là 0.</p>}
            {view === 'dashboard' && <>
              <div className="pw-kpis"><button onClick={() => { setFilter('urgent'); document.getElementById('priority-list')?.scrollIntoView({ behavior: 'smooth' }); }}><span><Package size={17}/>Mặt hàng cần xử lý</span><strong>{plan.kpis.risk_products}<small> / {plan.products.length}</small></strong><p>Thiếu theo ước tính hoặc ảnh hưởng đơn đang chờ<ArrowRight size={15}/></p></button><button onClick={() => setView('orders')}><span><Truck size={17}/>Đơn có nguy cơ trễ</span><strong>{plan.kpis.risk_orders}</strong><p>Thiếu hàng trước hạn hoặc lịch nhận quá muộn<ArrowRight size={15}/></p></button><button onClick={() => { setFilter('watch'); document.getElementById('priority-list')?.scrollIntoView({ behavior: 'smooth' }); }}><span><CalendarDays size={17}/>Kế hoạch nhập cần rà</span><strong>{plan.kpis.plans_attention}</strong><p>Quá ngày về hoặc về sau lúc cần hàng<ArrowRight size={15}/></p></button></div>
              <Panel title="Mức thiếu hàng theo sản phẩm" subtitle={`Ưu tiên ngày thiếu gần nhất · tối đa 8 sản phẩm cùng đơn vị ${unitLabel(selected?.uom)}`} action={evidenceButton('balance')}><ReplenishmentPriorities products={plan.products} unit={selected?.uom || ''}/><p className="pw-panel-note">Thanh dài cho biết lượng thiếu nhiều nhất trong kỳ, không phải số cần mua chắc chắn. Xem ngày bắt đầu thiếu trong danh sách bên dưới rồi thử lượng nhập và ngày về phù hợp.</p></Panel>
              <section className="pw-priority dw-panel" id="priority-list"><div className="dw-panel-heading"><div><h2>Ưu tiên xử lý trong kỳ</h2><p>Cùng kho, cùng đơn vị từng sản phẩm · chọn một dòng để xem nguyên nhân và thử cách xử lý</p></div><Select label="Lọc mức ưu tiên" value={filter} onChange={setFilter}><option value="all">Tất cả sản phẩm</option><option value="urgent">Cần xử lý</option><option value="watch">Cần rà kế hoạch nhập</option></Select></div><div className="dw-table-scroll"><table><thead><tr><th>Sản phẩm</th><th>Chưa giữ cho đơn</th><th>Đơn chờ phân bổ</th><th>Khách dự kiến đặt thêm</th><th>Hàng sẽ về trong kỳ</th><th>Thiếu nhiều nhất</th><th>Việc cần làm</th></tr></thead><tbody>{shown.map(p => <tr key={p.sku}><td><strong>{p.name || p.sku}</strong><small>{p.sku} · {unitLabel(p.uom)}</small><span className={`pw-status pw-${p.priority}`}>{priorityLabel[p.priority]}</span></td><td>{fmt(p.available)}</td><td>{fmt(p.known_quantity)}</td><td>{fmt(p.forecast_quantity)}</td><td>{fmt(p.incoming_quantity)}<small>{p.next_arrival ? 'Gần nhất ' + dateLabel(p.next_arrival) : 'Chưa có lịch hàng về'}</small></td><td className={p.shortage ? 'pw-danger-text' : ''}>{fmt(p.shortage)}<small>{p.first_risk_date ? 'Từ ' + dateLabel(p.first_risk_date) : 'Trong kỳ đang xét'}</small></td><td><button className="pw-row-action" disabled={blocked || dirty || p.priority === 'unknown'} onClick={() => inspect(p)}>{p.priority === 'urgent' ? 'Thử bổ sung hàng' : 'Xem phân tích'}<ArrowRight size={14}/></button></td></tr>)}</tbody></table>{!shown.length && <p className="pw-empty-inline">Không có sản phẩm thuộc mức ưu tiên này.</p>}</div><div className="pw-panel-note">Thiếu nhiều nhất xét từng thời điểm hàng về và hạn đơn, không chỉ so tổng cuối kỳ. {evidenceButton('balance')}</div></section>
              <div className="pw-two-columns"><Panel title="Đặt hàng, nhập kho và chênh lệch" subtitle={`${plan.selected_sku}${selected?.name ? " - " + selected.name : ""} · ${dateLabel(plan.history.start)} – ${dateLabel(plan.history.end)} · theo ${plan.history.frequency === 'week' ? 'tuần' : 'ngày'} · ${unitLabel(selected?.uom)}`} action={evidenceButton('history')}><div className="pw-inline-stats"><span>Khách đặt <strong>{fmt(demandTotal)}</strong></span><span>Đã nhập kho <strong>{fmt(receiptsTotal)}</strong></span><span>Nhập − đặt <strong>{fmt(receiptsTotal === null ? null : receiptsTotal - demandTotal)}</strong></span></div><PlanningChart label="Lượng đặt hàng, nhập kho thực tế và chênh lệch" data={plan.history.series} series={[{ key: 'confirmed', name: 'Khách đặt đã xác nhận', color: blue }, ...(plan.history.receipts_available ? [{ key: 'received', name: 'Đã nhập kho thực tế', color: cyan }, { key: 'difference', name: 'Nhập trừ đặt', color: purple, kind: 'line' as const }] : [])]}/><p className="pw-panel-note">Dùng để rà nhịp bổ sung hàng. Lượng khách đặt chưa phải số đã xuất kho; chênh lệch này không phải tăng/giảm tồn kho.</p></Panel><Panel title="Cân đối nguồn hàng theo ngày" subtitle={`${plan.selected_sku}${selected?.name ? " - " + selected.name : ""} · ${unitLabel(selected?.uom)}`} action={evidenceButton('balance')}><Plot data={balancePoints} unit={unitLabel(selected?.uom)} lines={[{ key: 'baseline', name: 'Hàng còn lại sau khi đáp ứng nhu cầu', color: blue }]}/><p className="pw-panel-note">Dưới 0: nhu cầu chưa được đáp ứng. Đã xét hàng sẽ về và đơn đang chờ; hàng về muộn vẫn có thể gây thiếu trước đó.</p><Button variant="outline" disabled={!!busy || dirty} onClick={() => setView('plan')}>Thử cách bổ sung hàng<ArrowRight size={15}/></Button></Panel></div>
              <div className="pw-action-brief"><ClipboardList size={22}/><div><h2>Nội dung phối hợp kế hoạch tuần</h2><p><strong>Kho & mua hàng:</strong> xác nhận lượng và ngày hàng về. <strong>Bán hàng:</strong> ưu tiên hạn gần, rà cam kết với khách. <strong>Vận chuyển:</strong> chốt lịch nhận theo ngày.</p></div><Button variant="outline" onClick={() => setView('orders')}>Xem đơn cần phối hợp<ArrowRight size={15}/></Button></div>
            </>}
            {view === 'plan' && selected && <>
              <div className="pw-product-heading"><div><span className={`pw-status pw-${selected.priority}`}>{priorityLabel[selected.priority]}</span><h2>{selected.sku}{selected.name ? " - " + selected.name : ""}<small>{selected.sku} · {unitLabel(selected.uom)}</small></h2></div>{evidenceButton('balance')}</div>
              <div className="pw-number-strip"><div><span>Chưa giữ cho đơn khác</span><strong>{fmt(selected.available)}</strong><button onClick={() => showEvidence('stock')}>Cách tính</button></div><div><span>Đơn đang chờ phân bổ</span><strong>{fmt(selected.known_quantity)}</strong></div><div><span>Khách dự kiến đặt thêm</span><strong>{fmt(selected.forecast_quantity)}</strong></div><div><span>Hàng sẽ về trong kỳ</span><strong>{fmt(selected.incoming_quantity)}</strong></div></div>
              <div className="pw-two-columns"><Panel title="Lịch hàng về và nhu cầu theo ngày" subtitle="Cùng sản phẩm, cùng kho · đối chiếu từng ngày để chọn thời điểm bổ sung"><Plot data={forecastPoints} unit={unitLabel(selected.uom)} lines={[{ key: 'band', name: 'Vùng dao động', color: blue }, { key: 'p50', name: 'Lượng đặt dự kiến mỗi ngày', color: blue, dash: '5 4' }, ...(hasPlannedInbound ? [{ key: 'inbound', name: 'Hàng sẽ về theo kế hoạch', color: cyan, kind: 'bar' as const }] : []), ...(hasKnownDue ? [{ key: 'known', name: 'Đơn đã nhận đến hạn, chưa phân bổ', color: purple, kind: 'bar' as const }] : [])]}/><p className="pw-panel-note">{!hasPlannedInbound && <>Không có hàng dự kiến về trong kỳ được đưa vào tính toán. </>}Cột hàng về và đơn đến hạn là lượng trong từng ngày; đường xanh là lượng khách có thể đặt thêm. Tồn đầu kỳ đã được tính trong biểu đồ đủ/thiếu bên dưới. Vùng nhạt cho thấy mức thấp–cao từ lịch sử; lượng đặt thực tế có thể nằm ngoài vùng này. Khuyến mại hoặc khách đặt đột biến cần thử thêm ở phần giả định bên dưới.</p></Panel><Panel title="Các đợt hàng dự kiến về" subtitle="Chỉ tính vào nguồn cung từ đúng thời điểm dự kiến nhận"><div className="pw-arrivals">{selectedInbound.length ? selectedInbound.map(p => <div key={p.plan_id}><CalendarDays size={18}/><span><strong>{fmt(p.quantity)} {unitLabel(p.uom)}</strong><small>Dự kiến {dateLabel(p.expected_at)}</small></span><span className={p.overdue ? 'pw-danger-text' : ''}>{p.overdue ? 'Quá ngày về · cần xác nhận' : 'Theo kế hoạch'}</span></div>) : <p>Chưa có kế hoạch hàng về được chia sẻ cho sản phẩm này.</p>}</div><p className="pw-panel-note">Kế hoạch chưa phải hàng đã nhận. Kế hoạch quá ngày về không tự được cộng vào hàng sẵn có.</p></Panel></div>
              <div className="dw-plan-layout pw-plan-layout"><section className="dw-controls dw-panel"><h2>Thử một cách xử lý</h2><p className="dw-muted">Nhập lượng bổ sung và ngày có thể nhận để so sánh tác động.</p>{!!selected.shortage && <button className="pw-suggestion" disabled={dirty || !!busy} onClick={() => setProposal(p => ({ ...p, quantity: Math.ceil(selected.shortage!), arrival: selected.first_risk_date && selected.first_risk_date >= plan.start ? selected.first_risk_date : plan.start }))}>Điền thử {fmt(Math.ceil(selected.shortage))} {unitLabel(selected.uom)} trước ngày thiếu<ArrowRight size={14}/></button>}<Field title={`Nhập thêm (${unitLabel(selected.uom)})`}><Input aria-label="Số lượng nhập thêm" type="number" min={0} max={1000000000} value={proposal.quantity} onChange={e => setProposal({ ...proposal, quantity: Number(e.target.value) })}/></Field><Field title="Ngày dự kiến hàng về"><Input type="date" min={plan.start} max={plan.end} value={proposal.arrival} onChange={e => setProposal({ ...proposal, arrival: e.target.value })}/></Field><p className="dw-muted">Giả định nhận hàng bổ sung vào đầu ngày. Nếu đơn có hạn trong cùng ngày, cần xác nhận lại giờ hàng về.</p><details className="dw-advanced"><summary>Nếu nhu cầu tăng hoặc hàng về trễ</summary><Field title="Mức nhu cầu để chuẩn bị"><Select value={proposal.quantile} onChange={v => setProposal({ ...proposal, quantile: v })}><option value="p50">Theo lượng dự kiến</option><option value="p90">Chuẩn bị cho mức đặt cao hơn</option></Select></Field><p className="dw-muted">Dùng mức đặt cao hơn từng ngày đã tính từ lịch sử. Thực tế vẫn có thể cao hơn; đây không phải cam kết đủ hàng.</p><Field title="Giả sử lượng khách đặt tăng/giảm (%)"><Input type="number" min={-100} max={400} value={proposal.multiplier - 100} onChange={e => setProposal({ ...proposal, multiplier: 100 + Number(e.target.value) })}/></Field><div className="pw-assumption-example"><strong>{proposal.multiplier === 100 ? "Giữ nguyên lượng dự kiến" : proposal.multiplier > 100 ? `Tăng ${fmt(proposal.multiplier - 100)}% lượng khách đặt` : `Giảm ${fmt(100 - proposal.multiplier)}% lượng khách đặt`}</strong><p>Ví dụ: dự kiến 100 sản phẩm, tăng 20% thì tính với 120 sản phẩm. Nhập 0% để giữ nguyên.</p><p>Trong kỳ này: {fmt(assumptionBase)} → <strong>{fmt(assumptionBase * proposal.multiplier / 100)} {unitLabel(selected.uom)}</strong> đơn mới dự kiến{proposal.quantile === "p90" ? " ở mức chuẩn bị cao hơn" : ""}.</p><p>Chỉ thay lượng khách có thể đặt thêm; không đổi số lượng đơn đã xác nhận. Dùng khi dự kiến có khuyến mại, khách lớn đặt thêm hoặc thị trường giảm. Đây là giả định để thử, không phải độ chính xác dự đoán.</p></div><Field title="Hàng đang chờ về trễ thêm (ngày)"><Input type="number" min={0} max={90} value={proposal.delay} onChange={e => setProposal({ ...proposal, delay: Number(e.target.value) })}/></Field><p className="dw-muted">Ví dụ nhập 2: lùi ngày về của các kế hoạch hiện có thêm 2 ngày. Không đổi ngày của lượng nhập bổ sung ở trên.</p></details><Button disabled={!!busy || dirty || !Number.isFinite(proposal.quantity) || proposal.quantity < 0 || proposal.quantity > 1e9 || !proposal.arrival || proposal.arrival < plan.start || proposal.arrival > plan.end || !Number.isFinite(proposal.multiplier) || proposal.multiplier < 0 || proposal.multiplier > 500 || !Number.isInteger(proposal.delay) || proposal.delay < 0 || proposal.delay > 90} onClick={() => void perform('Đang so sánh phương án', () => compare())}>So sánh phương án</Button><p className="dw-muted">Chỉ tính khi bấm. Chưa tạo đơn mua hoặc thay đổi tồn kho.</p>{scenario && scenarioDirty && <p className="pw-data-warning">Phương án đã sửa. So sánh lại trước khi lưu hoặc tải đề xuất.</p>}</section><Panel title="Tác động của phương án bổ sung" subtitle="Số dư sau khi đáp ứng nhu cầu · dưới 0 là phần chưa đáp ứng"><Plot data={balancePoints} unit={unitLabel(selected.uom)} lines={[{ key: 'baseline', name: 'Giữ kế hoạch hiện tại', color: 'var(--fb-text-muted)', dash: '5 4' }, ...(scenario ? [{ key: 'proposed', name: 'Nếu áp dụng phương án mới', color: blue }] : [])]}/>{scenario ? <><p className="pw-panel-note">Cả hai phương án dùng cùng giả định đã tính: lượng khách đặt {calculated ? fmt(calculated.multiplier) : "—"}% mức {calculated?.quantile === "p90" ? "chuẩn bị cao hơn" : "dự kiến"}, hàng đang chờ về trễ thêm {calculated?.delay || 0} ngày. Phương án mới bổ sung {fmt(calculated?.quantity)} {unitLabel(selected.uom)} vào {dateLabel(calculated?.arrival)}.</p><div className="pw-outcomes"><div><span>Thiếu nhiều nhất</span><strong>{fmt(scenario.baseline.metrics.peak_shortage)}<ArrowRight size={15}/>{fmt(scenario.proposed.metrics.peak_shortage)} <small>{unitLabel(selected.uom)}</small></strong></div><div><span>Đơn thiếu hàng trước hạn · toàn kho</span><strong>{fmt(scenario.baseline.metrics.known_orders_at_risk)}<ArrowRight size={15}/>{fmt(scenario.proposed.metrics.known_orders_at_risk)}</strong></div><div><span>Ngày đầu có nguy cơ thiếu / trễ</span><strong>{scenario.proposed.metrics.first_risk_date ? dateLabel(scenario.proposed.metrics.first_risk_date) : "Chưa thấy trong kỳ"}</strong></div></div><div className="pw-decision-result"><Info size={19}/><p>{scenario.proposed.metrics.peak_shortage > 0 || (scenario.proposed.metrics.known_orders_at_risk || 0) > 0 ? 'Vẫn còn rủi ro. Rà ngày hàng về, các sản phẩm khác cùng đơn và hạn giao trước khi chốt.' : 'Theo giả định này, chưa thấy thiếu hàng. Xác nhận lại nhà cung cấp và lịch giao nhận trước khi chốt.'}</p></div><div className="pw-proposal-actions"><Button variant="outline" disabled={!!busy || dirty || scenarioDirty} onClick={() => void perform('Đang lưu phương án', () => compare(true))}><Save size={15}/>Lưu phương án</Button><Button disabled={!!busy || dirty || scenarioDirty} onClick={exportProposal}><Download size={15}/>Tải đề xuất phối hợp</Button></div></> : <p className="pw-panel-note">Nhập phương án bên trái để thấy lượng thiếu giảm bao nhiêu và còn đơn nào có nguy cơ trễ.</p>}</Panel></div>
            </>}
            {view === 'plan' && scenario?.sensitivity && <Panel title="Ma trận lượng nhập và ngày hàng về" action={evidenceButton('sensitivity')} subtitle="So sánh đồng thời lượng bổ sung, thời điểm hàng về, lượng còn thiếu và số đơn có nguy cơ trễ"><SensitivityChart value={scenario.sensitivity} baselineShortage={scenario.baseline.metrics.peak_shortage} quantity={proposal.quantity} arrival={proposal.arrival} disabled={!!busy || dirty || !!calculated && (proposal.multiplier !== calculated.multiplier || proposal.quantile !== calculated.quantile || proposal.delay !== calculated.delay)} onSelect={(quantity, arrival) => { setProposal(p => ({ ...p, quantity, arrival })); setNotice('Đã điền từ ma trận. Bấm So sánh phương án để cập nhật kết quả trước khi lưu.'); }}/><p className="pw-panel-note">Ma trận thuộc lần so sánh đã tính: {calculated?.multiplier}% mức {calculated?.quantile === 'p90' ? 'chuẩn bị cao hơn' : 'dự kiến'}, hàng đang chờ trễ {calculated?.delay || 0} ngày. Đổi giả định nhu cầu hoặc độ trễ thì bấm So sánh phương án để tạo ma trận mới.</p></Panel>}
            {view === 'orders' && <>
              <Panel title="Đơn hàng và tiến độ đáp ứng" subtitle="Ưu tiên thiếu hàng và lịch nhận muộn; chưa chốt lịch không đồng nghĩa đã trễ"><div className="dw-table-scroll"><table><thead><tr><th>Đơn / sản phẩm</th><th>Hạn bàn giao cho vận chuyển</th><th>Vấn đề cần xử lý</th><th>Lịch đã nhận</th><th>Phòng ban phối hợp</th></tr></thead><tbody>{plan.orders.map(o => <tr key={o.order_id}><td><strong>{o.order_number}</strong>{o.products.map(p => <small key={p.sku}>{p.name || p.sku} · {p.sku}</small>)}</td><td>{dateLabel(o.deadline)}<small>{new Date(o.deadline).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Ho_Chi_Minh' })}{o.already_overdue ? ' · Đã qua hạn' : ''}</small></td><td><span className={`pw-status ${o.at_risk || o.schedule_status === 'late' ? 'pw-urgent' : 'pw-watch'}`}>{orderReason[o.reason] || 'Cần kiểm tra'}</span>{o.shortages_at_deadline.map(p => <small key={p.sku}>{p.sku}: thiếu {fmt(p.missing_quantity)} {unitLabel(p.uom)}</small>)}</td><td>{o.shipments.length ? o.shipments.map(s => <div key={s.shipment_number}><strong>{s.carrier_name || s.carrier_code}</strong><small>{s.shipment_number} · {dateLabel(s.scheduled_handoff_at)}</small></div>) : 'Chưa có lịch được chia sẻ'}</td><td>{o.at_risk ? 'Kho/mua hàng xác nhận bổ sung; Bán hàng rà cam kết' : o.schedule_status === 'booked' ? 'Kho chuẩn bị hàng theo lịch' : 'Bán hàng & Vận chuyển chốt lịch nhận'}</td></tr>)}</tbody></table>{!plan.orders.length && <p className="pw-empty-inline">Không có đơn đang chờ trong kỳ được chia sẻ.</p>}</div></Panel>
              <Panel title="Lịch tiếp nhận của nhà vận chuyển" subtitle="Lượt nhận hàng công bố cho toàn doanh nghiệp · không đổi số sản phẩm thành số chuyến"><div className="dw-table-scroll"><table><thead><tr><th>Ngày</th><th>Nhà vận chuyển</th><th>Tối đa (lượt)</th><th>Đã đặt (lượt)</th><th>Còn nhận (lượt)</th><th>Gợi ý phối hợp</th></tr></thead><tbody>{plan.capacity_calendar.map(r => <tr key={r.day + r.carrier_code}><td>{dateLabel(r.day)}</td><td>{r.carrier_name || r.carrier_code}<small>{r.carrier_code}</small></td><td>{fmt(r.maximum)}</td><td>{fmt(r.booked)}</td><td>{fmt(r.remaining)}</td><td>{r.booked > r.maximum ? 'Vượt lịch công bố · cần điều phối lại' : r.remaining > 0 ? 'Có thể trao đổi thêm lịch nhận' : 'Cần hỏi lịch khác hoặc đơn vị khác'}</td></tr>)}</tbody></table>{!plan.capacity_calendar.length && <p className="pw-empty-inline">Chưa có lịch công bố trong kỳ. Chưa thể kết luận còn nhận thêm hay không.</p>}</div><p className="pw-panel-note">Một lượt là một lần tiếp nhận bàn giao, không phải một sản phẩm. Ngày chưa công bố lịch có năng lực chưa biết.</p></Panel>
            </>}
          </>}
        </>}
        {view === 'configuration' && admin && products.length > 0 && <><Configuration products={products} request={request} onError={report} onEvidence={e => setExplanation({ title: 'Kiểm tra cấu hình', meaning: 'Chi tiết cấu hình và dữ liệu thử dành cho IT.', technical: e })} onUse={r => void perform('Đang đọc báo cáo tùy chỉnh', async () => { const p = products.find(p => p.id === r.config.data_product_id)!; setCustom(await request<Overview>('/overview', { ...productRef(p), configuration_id: r.id, days: 30 })); })}/>{custom && <Panel title={custom.config.name} subtitle="Báo cáo tùy chỉnh · cấu hình của IT"><Plot data={custom.analytics.series.map(p => ({ day: p.timestamp, target: p.target }))} unit={unitLabel(custom.config.unit)} lines={[{ key: 'target', name: 'Giá trị theo cấu hình', color: blue }]}/></Panel>}</>}
        {busy && <div className="dw-progress pw-progress" role="status"><LoaderCircle className="animate-spin" size={16}/>{busy}…{catalogLoaded && <button className="pw-text-button" onClick={cancel}>Dừng chờ</button>}</div>}
      </div>
    </main>
    <BusinessEvidence value={explanation} admin={admin} onClose={() => setExplanation(null)}/>
    <Sheet open={savedOpen} onOpenChange={setSavedOpen}><SheetContent><SheetHeader><SheetTitle>Dự đoán & phương án đã lưu</SheetTitle><SheetDescription>Kết quả tại thời điểm lưu; cần cập nhật phân tích trước khi quyết định hôm nay.</SheetDescription></SheetHeader><div className="dw-saved">{!saved.length && !savedForecasts.length && <p>Chưa có kết quả đã lưu.</p>}{savedForecasts.map(f => <button key={f.id} disabled={!!busy} onClick={() => void perform('Đang kiểm tra quyền kết quả', async () => setSavedForecast(await request<Forecast>(`/forecasts/${f.id}`)))}><History size={16}/><span>{f.config.entities[0] || f.config.name}<small>{dateLabel(f.created_at)} · {f.config.forecast_horizon} ngày</small></span></button>)}{savedForecast && <div><h3>Dự đoán tại thời điểm lưu</h3><Plot data={savedForecast.points.filter(p => p.item_id === savedForecast.config.entities[0]).map(p => ({ day: p.timestamp, predicted: p.p50, band: [p.p10, p.p90] }))} unit={unitLabel(savedForecast.config.unit)} lines={[{ key: 'band', name: 'Vùng dao động', color: blue }, { key: 'predicted', name: 'Lượng dự kiến', color: purple, dash: '5 4' }]}/><p className="dw-muted">Kết quả cũ để tham khảo. Tạo dự đoán mới để lập kế hoạch hôm nay.</p></div>}{saved.map(s => <div className="pw-saved-card" key={s.id}><strong>{String((s.evidence.assumptions as Record<string, unknown>)?.sku || 'Phương án nhập hàng')}</strong><p>Thiếu nhiều nhất: {fmt(s.baseline.metrics.peak_shortage)} → {fmt(s.proposed.metrics.peak_shortage)}</p><p>Ngày có nguy cơ: {dateLabel(s.proposed.metrics.first_risk_date)}</p><small>{s.id?.slice(0, 8)}</small></div>)}</div></SheetContent></Sheet>
  </div>;
}
