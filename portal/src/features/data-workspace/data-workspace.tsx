// SPDX-License-Identifier: GPL-3.0-or-later
import { useCallback, useEffect, useRef, useState } from 'react';
import { BarChart3, CalendarDays, ChartLine, Info, LoaderCircle, Package, Settings2, Truck } from 'lucide-react';
import { useSession } from '@/app/session';
import { Brand } from '@/components/brand';
import { Button } from '@/components/ui/button';
import { WorkspaceModal as Sheet } from './interactive-workspace';
import { DialogContent as SheetContent, DialogHeader as SheetHeader, DialogTitle as SheetTitle, DialogDescription as SheetDescription } from '@/components/ui/dialog';
import { ApiError } from '@/lib/api';
import { Configuration } from './configuration';
import { BusinessAnalytics } from './business-analytics';
import { BusinessEvidence, type Explanation } from './business-evidence';
import { Field, Panel, Plot, Select, dateLabel, fmt, unitLabel } from './components';
import { ProductSelector } from './product-selector';
import { OperationsDashboard } from './operations-dashboard';
import { HandoffWorkspace } from './handoff-workspace';
import { OrganizationRecords } from './organization-records';
import { FulfillmentInsights } from './fulfillment-insights';
import { AllWarehouses } from './all-warehouses';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { warehouseLabel, type PlanningDirectory, type Planning, type PlanProduct } from './planning-types';
import type { Proposal } from './scenario-assumptions';
import { PlanningWorkspace } from './planning-workspace';
import { GuidedWizard } from './guided-wizard';
import { scenarioSummary } from './scenario-summary';
import { ScenarioShortage } from './scenario-shortage';
import { DecisionDossiers, type DecisionCapabilities } from './decision-dossiers';
import { Card, CardContent } from '@/components/ui/card';
import type { Forecast, Overview, Product, Scenario } from './types';
import './workspace.css';
import './workspace-navigation.css';
import { WorkspaceDialog } from './interactive-workspace';
import { WorkspaceSync, useWorkspaceState, useWorkspaceSync } from './workspace-sync';

type View = 'insights' | 'analytics' | 'forecast' | 'dashboard' | 'plan' | 'orders' | 'configuration' | 'decisions' | 'tasks' | 'outcomes' | 'records' | 'all-warehouses';
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

export function DataWorkspace() {
  const session = useSession();
  return <WorkspaceSync key={session.mode + ':' + session.profile?.user.user_id}><DataWorkspaceContent/></WorkspaceSync>;
}
function DataWorkspaceContent() {
  const session = useSession();
  const sync = useWorkspaceSync();
  const refreshRequested = useRef(!!sync?.saved.states['ui:reload-current']);
  const [view, setView] = useWorkspaceState<View>('view','dashboard');
  const [scopeOpen,setScopeOpen]=useState(false);
  const [overviewWarehouseId,setOverviewWarehouseId]=useWorkspaceState('warehouse','');
  const [orderQuery,setOrderQuery]=useState('');
  const [openHandoffList,setOpenHandoffList]=useState(false);
  const [allWarehousesEnabled,setAllWarehousesEnabled]=useState(false);
  const [products, setProducts] = useState<Product[]>([]);
  const [sourceNames, setSourceNames] = useState<Record<string, string>>({});
  const [directory, setDirectory] = useState<PlanningDirectory>({ warehouses: [], products: [] });
  const [directoryLoaded, setDirectoryLoaded] = useState(false);
  const [admin, setAdmin] = useState(false), [catalogLoaded, setCatalogLoaded] = useState(false);
  const [draft, setDraft] = useWorkspaceState('draft',initialDraft), [applied, setApplied] = useWorkspaceState<Draft | null>('applied',null,true);
  const [plan, setPlan] = useWorkspaceState<Planning | null>('plan',null,true);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [inputsOpen, setInputsOpen] = useState(false);
  const [filter, setFilter] = useState('all');
  const [proposal, setProposal] = useWorkspaceState<Proposal>('proposal',{ quantity: 0, arrival: '', multiplier: 100, delay: 0, quantile: 'p50' });
  const [scenario, setScenario] = useState<Scenario | null>(null), [calculated, setCalculated] = useState<Proposal | null>(null);
  const [saved, setSaved] = useState<Scenario[]>([]), [savedOpen, setSavedOpen] = useState(false);
  const [savedForecasts, setSavedForecasts] = useState<Forecast[]>([]), [savedForecast, setSavedForecast] = useState<Forecast | null>(null);
  const [custom, setCustom] = useState<Overview | null>(null),[customOpen,setCustomOpen]=useState(false);
  const [decisionCapabilities, setDecisionCapabilities] = useState<DecisionCapabilities | null>(null);
  const snapshot = useRef<string | null>(null), generation = useRef(0), operation = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  const mainRef = useRef<HTMLElement | null>(null);
  useEffect(() => { mainRef.current?.scrollTo({ top: 0 }); }, [view, plan?.selected_sku]);
  const request = useCallback(async <T,>(path: string, body?: unknown, signal?: AbortSignal, key?: string, fresh = false): Promise<T> => {
    const ticket = generation.current, controller = new AbortController();
    controllers.current.add(controller);
    try {
      const work = () => session.api.request<T>('/api/v1/data-workspace' + path, { method: body === undefined ? 'GET' : 'POST', body, key, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(210000), ...(signal ? [signal] : [])]) });
      const result = sync ? await sync.read(path, body, work, fresh) : await work();
      if (ticket !== generation.current) throw new DOMException('Selection changed', 'AbortError');
      return result;
    } catch (e) { if (e instanceof ApiError && e.status === 401) session.invalidate(); throw e; }
    finally { controllers.current.delete(controller); }
  }, [session.api, session.invalidate, sync]);
  function report(e: unknown) {
    const retryAction = view === 'plan' ? 'Tính lại dự báo' : view === 'forecast' ? 'Cập nhật dữ liệu' : view === 'analytics' ? 'Cập nhật biểu đồ' : 'Tải dữ liệu và tính lại';
    if (e instanceof DOMException && e.name === 'AbortError') return;
    setExplanation(null);
    if (e instanceof ApiError && [401, 403].includes(e.status)) {
      setScopeOpen(false);setCustomOpen(false);setDecisionCapabilities(null);
      generation.current++; controllers.current.forEach(c => c.abort());
      setInputsOpen(false); setDirectory({ warehouses: [], products: [] }); setDirectoryLoaded(false); setPlan(null); setScenario(null); setSaved([]); setSavedForecasts([]); setSavedForecast(null); setCustom(null); setProducts([]); setAdmin(false); setSavedOpen(false);
    }
    setError(e instanceof ApiError && e.status === 403 ? 'Quyền dữ liệu đã thay đổi. Liên hệ quản trị viên để kiểm tra rồi tải lại trang.'
      : e instanceof ApiError && e.status === 409 ? 'Dữ liệu dùng cho lần so sánh trước đã thay đổi hoặc quá cũ. Bấm So sánh phương án để tính lại trước khi lưu.'
      : e instanceof ApiError && (e.status === 422 && ['NO_DATA', 'STALE_HISTORY', 'INSUFFICIENT_HISTORY', 'DATASET_TOO_LARGE', 'ML_HISTORY_SHORT', 'ML_SOURCE_REQUIRED', 'FORECAST_WINDOW_INVALID'].includes(e.code || '') || e.status === 504 && e.code === 'PLANNING_TIMEOUT' || e.status === 503 && e.code === 'CHRONOS_UNAVAILABLE') ? e.message
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

  useEffect(() => {
    const controller=new AbortController();
    void request<{all_warehouses_enabled:boolean}>('/warehouse-coverage',undefined,controller.signal)
      .then(value=>{if(!controller.signal.aborted){setAllWarehousesEnabled(value.all_warehouses_enabled);if(value.all_warehouses_enabled)setView(current=>current==='dashboard'?'all-warehouses':current);}})
      .catch(()=>{if(!controller.signal.aborted)setAllWarehousesEnabled(false);});
    return()=>controller.abort();
  },[request]);

  const sources = Object.fromEntries(Object.entries(sourceRoles).map(([key, [name]]) => [key, products.find(p => p.name === (sourceNames[key] || name))]));
  const missing = required.filter(k => !sources[k]);
  const selected = plan?.products.find(p => p.sku === plan.selected_sku);
  const dirty = !!plan && JSON.stringify(draft) !== JSON.stringify(applied);
  const scenarioDirty = JSON.stringify(proposal) !== JSON.stringify(calculated);
  const scopeValid=!missing.length&&catalogLoaded&&directoryLoaded&&!!draft.warehouse&&directory.products.some(p=>p.warehouse_id===draft.warehouse&&p.sku===draft.sku);
  const blocked = !!busy || !scopeValid;
  const title = view === 'insights' ? 'Đơn hàng và tác động' : view === 'all-warehouses' ? 'Tổng quan kho và đơn hàng' : view === 'records' ? 'Tra cứu dữ liệu' : view === 'tasks' ? 'Việc của tôi' : view === 'outcomes' ? 'Theo dõi kết quả' : view === 'decisions' ? 'Hồ sơ phối hợp' : view === 'analytics' ? 'Phân tích lịch sử' : view === 'forecast' ? 'Dự báo nhu cầu' : view === 'plan' ? 'Thử phương án nhập hàng' : view === 'orders' ? 'Đơn hàng và bàn giao' : view === 'configuration' ? 'Thiết lập cho IT' : 'Tổng quan';
  function patch<K extends keyof Draft>(key: K, value: Draft[K]) { setDraft(d => ({ ...d, [key]: value })); }
  useEffect(() => {
    if (!refreshRequested.current || !catalogLoaded || !directoryLoaded) return;
    refreshRequested.current = false;
    if (sync) { delete sync.saved.states['ui:reload-current']; sync.save(); }
    if (draft.warehouse && !missing.length && ['dashboard','plan','orders'].includes(view)) {
      void perform('Đang cập nhật dữ liệu liên quan và tính lại kế hoạch', () => load(draft, undefined, true));
    }
  }, [catalogLoaded, directoryLoaded]);
  async function load(next = draft, nextView?: View, preserveProposal = false) {
    generation.current++; controllers.current.forEach(c => c.abort());
    setInputsOpen(false); setPlan(null); setScenario(null); setCalculated(null); setExplanation(null); snapshot.current = null;
    const result = await request<Planning>('/planning', {
      ...Object.fromEntries(Object.entries(sources).filter(([, p]) => !!p).map(([k, p]) => [k, productRef(p!)])),
      warehouse_id: next.warehouse || null, sku: next.sku || null, horizon: Number(next.horizon), history_days: Number(next.history), history_frequency: next.frequency, forecast_model: 'chronos2', training_days: Number(next.training),
    }, undefined, undefined, true);
    setPlan(result);
    const resolved = { ...next, warehouse: result.warehouse_id, sku: result.selected_sku };
    setDraft(resolved); setApplied(resolved);
    if (!preserveProposal) setProposal({ quantity: 0, arrival: result.start, multiplier: 100, delay: 0, quantile: 'p50' });
    setScopeOpen(false);
    if (nextView) setView(nextView);
  }
  function inspect(p: PlanProduct) { void perform('Đang tính dự báo và kiểm tra hàng trong kho', () => load({ ...applied!, sku: p.sku }, 'plan')); }
  function showEvidence(key: string) {
    if (key === 'scenario' && plan) {
      setExplanation({title:'So sánh ba phương án đủ hàng', meaning:'Tách tác động của nhu cầu/lịch hàng thay đổi với tác động của bổ sung hàng, với cùng dữ liệu đã đọc ở lần so sánh này.', applications:plan.explanations.balance?.applications,
        formula:'Phương án ban đầu dùng mức nhu cầu dự kiến thông thường và lịch hàng về hiện có. Hai phương án còn lại cùng mức nhu cầu và số ngày hàng về trễ. Chỉ phương án bổ sung nhận thêm hàng.',
        notes:['Chronos-2 huấn luyện sẵn dùng lịch sử của đúng kho, mã mặt hàng và đơn vị tính làm ngữ cảnh dự báo. Không chuyển dự báo toàn doanh nghiệp vào một kho.', 'Mức tăng/giảm áp vào lượng đặt mới dự báo. Không đổi đơn đã nhận và không suy ra số khách hàng riêng biệt.', 'Mức bổ sung tối thiểu bù phần thiếu hàng lớn nhất tại từng thời điểm cần hàng. Hàng bổ sung được giả định sẵn sàng từ đầu ngày, hoặc lúc chụp dữ liệu nếu là hôm nay.', 'Ngày thiếu hàng được tính cả khi chỉ thiếu trước giờ chuẩn bị của một đơn trong ngày. Nhận hàng sau hạn không xóa đơn đã trễ.', 'Mức nhu cầu cao dùng phân vị do Chronos-2 dự báo, chưa phải xác suất đảm bảo.', 'Mức nhận thêm nhu cầu được tính từ lượng hàng còn sau khi đáp ứng các đơn đã nhận, xét từng thời điểm. Nếu ngay các đơn đã nhận đã thiếu hàng thì chưa thể tính mức nhận thêm.', 'Chỉ mặt hàng đang chọn có lượng đặt mới dự báo. Các mặt hàng khác chỉ xét phần đơn đã nhận.', 'Chưa xét giá mua, ngân sách, hạn sử dụng, số lượng mua tối thiểu hay thời gian nhà cung cấp giao hàng. Đây không phải tối ưu chi phí.', 'Độ trễ đang xét là hàng nhập về kho. Lịch giao nhận với khách và nhà vận chuyển cần đối chiếu riêng.'],
        start:plan.start,end:plan.end,read_at:typeof scenario?.evidence.snapshot_at==='string' ? scenario.evidence.snapshot_at : plan.as_of,last_observation:plan.source_end,technical:scenario?.evidence || plan.forecast.evidence});
      return;
    }
    if (key === 'sensitivity' && scenario?.sensitivity && plan) {
      setExplanation({
        title: 'Bảng thử lượng nhập và ngày hàng về',
        meaning: 'Tính thử từng cặp lượng nhập và ngày về trên cùng nguồn hàng, đơn đang chờ và giả định nhu cầu đã so sánh.',
        applications: plan.explanations.balance?.applications,
        items: [
          { label: 'Lượng đặt mới trong giả định đã tính', value: scenario.baseline.metrics.forecast_demand, unit: scenario.sensitivity.unit },
          { label: 'Số lượng nhập thử', value: scenario.sensitivity.quantities.length },
          { label: 'Số ngày về thử', value: scenario.sensitivity.dates.length },
          { label: 'Số phương án độc lập', value: scenario.sensitivity.cells.length },
        ],
        formula: 'Mỗi ô tính lại tiến độ hàng về và hạn từng đơn. Lấy mức thiếu hàng lớn nhất tại các thời điểm và đếm đơn không đủ hàng trước hạn.',
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
      'SO SÁNH BA PHƯƠNG ÁN ĐỦ HÀNG',
      ...([['Dự báo ban đầu', decision.reference], ['Sau thay đổi nhu cầu/hàng về', decision.stressed], ['Có bổ sung hàng', decision.proposed]] as const).map(([label, value]) => `${label}: thiếu nhiều nhất ${fmt(value.peak_shortage)}; ${value.shortage_days} ngày có lúc thiếu. Hàng còn cuối kỳ ${fmt(value.ending_inventory)}. Đơn có nguy cơ thiếu hàng trước giờ chuẩn bị ${fmt(value.known_orders_at_risk)}.`),
      `Mức bổ sung để hết thiếu hàng: ${new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 3 }).format(Math.ceil(decision.minimum_additional_quantity))} ${unitLabel(selected.uom)}. Từ đầu ngày ${dateLabel(decision.latest_arrival_date)}.`,
      'Kết quả chưa xét giá mua, số lượng mua tối thiểu, ngân sách hay cam kết nhà cung cấp. Độ trễ là hàng nhập về kho.',
      '',
    ] : [];
    const text = ['ĐỀ XUẤT PHỐI HỢP NHẬP HÀNG', ...decisionLines, `Sản phẩm: ${selected.name || selected.sku} (${selected.sku})`, `Kho: ${plan.warehouses.find(w => w.id === plan.warehouse_id)?.name || plan.warehouse_id}`, `Kỳ: ${dateLabel(plan.start)} – ${dateLabel(plan.end)}. Dữ liệu đọc lúc ${plan.as_of}`, `Nhập thêm: ${fmt(calculated.quantity)} ${unitLabel(selected.uom)}. Hàng về ${dateLabel(calculated.arrival)}${calculated.arrivalTime ? ` lúc ${calculated.arrivalTime} (giờ Việt Nam)` : ' (giả định đầu ngày)'}`, `Lượng khách đặt thêm: ${calculated.multiplier}% mức dự báo ${calculated.quantile === 'p90' ? 'cao hơn để chuẩn bị' : 'thông thường'}. Hàng dự kiến về theo lịch hiện có được giả định trễ thêm ${calculated.delay} ngày.`, `Trước khi bổ sung: thiếu lớn nhất ${fmt(canonical.before)} ${unitLabel(selected.uom)}. Sau khi bổ sung: còn thiếu lớn nhất ${fmt(canonical.after)} ${unitLabel(selected.uom)}. Đây là mức thiếu của mặt hàng trong toàn kho, không phải riêng một đơn.`, `Số đơn có nguy cơ thiếu hàng trước giờ chuẩn bị trong toàn kho: trước bổ sung ${fmt(scenario.baseline.metrics.known_orders_at_risk)}, sau bổ sung ${fmt(scenario.proposed.metrics.known_orders_at_risk)}.`, '', 'Kho/mua hàng: xác nhận nhà cung cấp, lượng và giờ hàng về.', 'Bán hàng: rà các đơn có hạn gần, thống nhất lại cam kết với khách nếu cần.', 'Vận chuyển: chốt lịch nhận hàng phù hợp hạn giao.', 'Đề xuất từ mô phỏng, chưa được phê duyệt và chưa tạo giao dịch. Ước tính có thể khác thực tế.'].join('\n');
    const url = URL.createObjectURL(new Blob(['\uFEFF' + text], { type: 'text/plain;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = `de-xuat-${selected.sku.replace(/[^a-zA-Z0-9_-]/g, '_')}.txt`; a.click(); URL.revokeObjectURL(url);
  }
  const forecastPoints = plan?.forecast.points.filter(p => p.item_id === plan.selected_sku).map(p => ({ day: p.timestamp, p50: p.p50, band: [p.p10, p.p90], known: plan.baseline.series.find(s => s.day === p.timestamp)?.known_demand_due ?? null, inbound: plan.baseline.series.find(s => s.day === p.timestamp)?.inbound ?? null })) || [];
  const hasPlannedInbound = forecastPoints.some(p => (p.inbound || 0) > 0);
  const hasKnownDue = forecastPoints.some(p => (p.known || 0) > 0);
  const selectedInbound = plan?.inbound.filter(p => p.sku === plan.selected_sku) || [];

  return <div className="dw-shell pw-shell">
    <aside className="dw-sidebar"><Brand/>
      <p className="dw-nav-label">Lập kế hoạch và phối hợp</p>
      <nav aria-label="Lập kế hoạch và phối hợp">{([
        ['dashboard', BarChart3, 'Tổng quan kho', ['dashboard','all-warehouses','orders']],
        ['forecast', ChartLine, 'Dự báo nhu cầu', ['forecast']],
        ['insights', CalendarDays, 'Đơn hàng và tác động', ['insights']],
        ['plan', Package, 'Thử phương án nhập hàng', ['plan']],
        ...(decisionCapabilities?.enabled ? [['tasks', Truck, 'Phối hợp và bàn giao', ['tasks','decisions','outcomes']]] : []),
      ] as [View, typeof Package, string, View[]][]).map(([id, Icon, label, children]) => <button key={String(id)} aria-current={(children as readonly string[]).includes(view)?'page':undefined} className={(children as readonly string[]).includes(view)?'active':''} onClick={()=>setView(id==='dashboard'&&allWarehousesEnabled?'all-warehouses':id as View)}><Icon size={18}/><span>{String(label)}</span></button>)}</nav>
      <p className="dw-nav-label">Dữ liệu và công cụ</p>
      <nav aria-label="Dữ liệu và công cụ">{([
        ['records', Package, 'Tra cứu dữ liệu', ['records']],
        ['analytics', ChartLine, 'Phân tích lịch sử', ['analytics']],
        ...(admin ? [['configuration', Settings2, 'Thiết lập cho IT', ['configuration']]] : []),
      ] as [View, typeof Package, string, View[]][]).map(([id, Icon, label, children]) => <button key={String(id)} aria-current={(children as readonly string[]).includes(view)?'page':undefined} className={(children as readonly string[]).includes(view)?'active':''} onClick={()=>setView(id as View)}><Icon size={18}/><span>{String(label)}</span></button>)}</nav>
      <div className="dw-user"><span className="dw-avatar">{session.profile?.user.display_name?.[0] || 'F'}</span><span>{session.profile?.user.display_name || 'Người dùng'}<small>{admin ? 'Quản trị dữ liệu' : 'Điều hành doanh nghiệp'}</small></span></div><button className="pw-logout" onClick={() => void session.logout()}>Đăng xuất</button>
    </aside>
    <main className="dw-main" ref={mainRef}>
      <div className="dw-content"><div className="dw-page-heading"><div><h1>{title}</h1></div>{!['configuration','records','tasks','decisions','outcomes','insights'].includes(view) && <Button variant="outline" disabled={!!busy} onClick={() => void perform('Đang mở phương án đã lưu', async () => { const [s, f] = await Promise.all([request<{ items: Scenario[] }>('/scenarios/saved'), request<{ items: Forecast[] }>('/forecasts/saved')]); setSaved(s.items); setSavedForecasts(f.items); setSavedForecast(null); setSavedOpen(true); })}>Kết quả đã lưu</Button>}</div>
        {error && <div className="dw-alert" role="alert">{view !== 'forecast' && <Info size={19}/>}<p>{error}</p></div>}{notice && <div className="dw-notice" role="status">{notice}</div>}
        {catalogLoaded && !products.length && !error && <div className="pw-source-setup"><Info size={24}/><div><h2>Chưa có dữ liệu được chia sẻ</h2><p>Liên hệ IT để được cấp quyền xem dữ liệu Bán hàng, Kho hoặc Vận chuyển.</p></div></div>}
        {['dashboard','all-warehouses','orders'].includes(view) && <Tabs value={view==='orders'?'orders':'overview'} onValueChange={value=>{const target=value==='orders'?'orders':allWarehousesEnabled?'all-warehouses':'dashboard';setOpenHandoffList(false);setView(target);}} className="pw-section-tabs"><TabsList variant="line" aria-label="Theo dõi kho và đơn"><TabsTrigger value="overview">Tồn kho và đơn đang chờ</TabsTrigger><TabsTrigger value="orders" disabled={allWarehousesEnabled&&!!overviewWarehouseId&&!directory.warehouses.some(w=>w.id===overviewWarehouseId)} title={allWarehousesEnabled&&!!overviewWarehouseId&&!directory.warehouses.some(w=>w.id===overviewWarehouseId)?'Kho này hiện chỉ có quyền xem tổng quan; chưa có nguồn để kiểm tra lịch bàn giao.':undefined}>Đơn hàng và bàn giao</TabsTrigger></TabsList></Tabs>}
        {['tasks','decisions','outcomes'].includes(view) && decisionCapabilities?.enabled && <Tabs value={view} onValueChange={value=>setView(value as View)} className="pw-section-tabs"><TabsList variant="line" aria-label="Phối hợp và bàn giao"><TabsTrigger value="tasks">Việc của tôi</TabsTrigger><TabsTrigger value="decisions">Hồ sơ phối hợp</TabsTrigger><TabsTrigger value="outcomes">Theo dõi kết quả</TabsTrigger></TabsList></Tabs>}
        {products.length > 0 && <div hidden={view !== 'analytics' && view !== 'forecast'}><BusinessAnalytics error={error} mode={view === 'forecast' ? 'forecast' : 'history'} products={products} busy={!!busy} request={request} perform={perform} onEvidence={setExplanation} canPlan={!missing.length} names={sourceNames} onPlan={(sku, horizon, training = 180) => { const nextView = sku ? 'plan' : 'orders'; if (!draft.warehouse) { setDraft(d => ({ ...d, sku, horizon: String(horizon), training: String(training) })); setView(nextView); return; } void perform('Đang đối chiếu kế hoạch', () => load({ ...draft, sku, horizon: String(horizon), training: String(training) }, nextView)); }}/></div>}
        {view === 'all-warehouses' && allWarehousesEnabled && <AllWarehouses request={request} onEvidence={setExplanation} onDenied={report} initialWarehouseId={overviewWarehouseId} onSelectWarehouse={warehouseId=>{
          setOverviewWarehouseId(warehouseId);
          if(directory.warehouses.some(w=>w.id===warehouseId))setDraft(previous=>previous.warehouse===warehouseId?previous:{...previous,warehouse:warehouseId,sku:directory.products.find(p=>p.warehouse_id===warehouseId)?.sku||''});
        }} onReviewOrder={(warehouseId,orderNumber,horizon)=>{
          if(!directory.warehouses.some(w=>w.id===warehouseId))return;
          const next={...draft,warehouse:warehouseId,horizon:String(horizon),sku:directory.products.find(p=>p.warehouse_id===warehouseId)?.sku||''};
          setOrderQuery(orderNumber);setDraft(next);setView('orders');void perform('Đang kiểm tra đơn và lịch bàn giao',()=>load(next,'orders'));
        }} analysisWarehouseIds={!missing.length?directory.warehouses.map(w=>w.id):[]} onLocal={(warehouseId,sku,horizon)=>{
          if(!directory.warehouses.some(w=>w.id===warehouseId))return;
          const next={...draft,warehouse:warehouseId,horizon:String(horizon||draft.horizon),sku:sku||directory.products.find(p=>p.warehouse_id===warehouseId)?.sku||''};
          setDraft(next);setView('plan');void perform('Đang tính dự báo và kiểm tra hàng trong kho',()=>load(next,'plan'));
        }}/>}
        {view==='insights'&&<FulfillmentInsights directory={directory} sources={sources} request={request} onDenied={report} onReview={(warehouse,orderNumber,horizon)=>{const next={...draft,warehouse,horizon:String(horizon),sku:directory.products.find(p=>p.warehouse_id===warehouse)?.sku||''};setOrderQuery(orderNumber);setOpenHandoffList(true);setDraft(next);setView('orders');void perform('Đang đối chiếu đơn',()=>load(next,'orders'));}} onSupply={(warehouse,sku,horizon)=>{const next={...draft,warehouse,sku,horizon:String(horizon)};setDraft(next);setView('plan');void perform('Đang dự báo và kiểm tra nguồn hàng',()=>load(next,'plan'));}}/>}
        {view === 'records' && <OrganizationRecords request={request} onDenied={report}/>}
        {['decisions','tasks','outcomes'].includes(view) && decisionCapabilities?.enabled && <DecisionDossiers handoffOptions={plan?.capacity_calendar||[]} key={view} mode={view==='tasks'?'tasks':view==='outcomes'?'outcomes':'all'} request={request} capabilities={decisionCapabilities} scenario={scenario} scenarioDirty={dirty || scenarioDirty}/>}
        {!['decisions','tasks','outcomes','records','all-warehouses','insights'].includes(view) && view !== 'configuration' && view !== 'analytics' && view !== 'forecast' && <>
          <div className="iw-page-toolbar"><span>{directory.warehouses.find(w=>w.id===draft.warehouse)?.name||'Chưa chọn kho'} · {draft.horizon} ngày tới{dirty?' · Có lựa chọn chưa áp dụng':''}</span><Button variant="outline" disabled={!!busy} onClick={()=>setScopeOpen(true)}>Chọn kho và phạm vi</Button></div>
          <WorkspaceDialog open={scopeOpen} onClose={()=>{if(busy)cancel();setScopeOpen(false);}} title="Chọn kho và phạm vi" description="Chọn phạm vi, xác nhận rồi mới đọc dữ liệu và tính kế hoạch." wide>
            {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{busy&&<><p role="status">{busy}…</p><Button variant="outline" onClick={cancel}>Dừng chờ</Button></>}<GuidedWizard label="Các bước chọn phạm vi" busy={!!busy} finishLabel={view==='plan'?'Tính lại dự báo':'Tải dữ liệu và tính lại'} onFinish={()=>{if(!blocked)void perform('Đang đối chiếu dữ liệu các phòng ban',()=>load());}} steps={[
              {id:'scope',title:'Chọn phạm vi',description:'Kho, mặt hàng và khoảng kiểm tra',signature:JSON.stringify(draft),valid:scopeValid,issue:'Chọn kho và mặt hàng có trong danh sách được cấp quyền.',content:<fieldset disabled={!!busy}><section className={`dw-filterbar pw-filters${view === 'orders' ? ' pw-handoff-filters' : ''}${view === 'plan' ? ' pw-ml-filters' : ''}`}><Field title="Kho phân tích" hint="Chỉ đối chiếu đơn hàng và nguồn hàng của kho đã chọn trong phạm vi bạn được cấp quyền."><Select value={draft.warehouse} onChange={warehouse => setDraft({ ...draft, warehouse, sku: directory.products.find(p => p.warehouse_id === warehouse)?.sku || '' })} disabled={!!busy || !directoryLoaded}><option value="" disabled>{!directoryLoaded ? 'Đang tải danh sách kho…' : 'Chọn kho'}</option>{directory.warehouses.map(w => <option key={w.id} value={w.id}>{warehouseLabel(w)}</option>)}</Select></Field><Field title="Khoảng thời gian cần kiểm tra" hint="Khoảng thời gian từ hôm nay để đối chiếu đơn đến hạn, hàng sẽ về và lượng đặt mới dự báo. Chọn kỳ dài hơn để chuẩn bị sớm."><Select value={draft.horizon} onChange={v => patch('horizon', v)} disabled={!!busy}>{[...new Set(['7', '14', '30', '90', draft.horizon])].sort((a, b) => Number(a) - Number(b)).map(n => <option key={n} value={n}>{n} ngày tới</option>)}</Select></Field>{view === 'dashboard' && <Field title="Xem đặt hàng và nhập kho trong" hint="Khoảng dữ liệu đã ghi nhận dùng cho biểu đồ đặt hàng và thực nhập. Đây là lịch sử, không phải kỳ dự báo."><Select value={draft.history} onChange={v => patch('history', v)} disabled={!!busy}>{['7', '14', '30', '90', '365'].map(n => <option key={n} value={n}>{n === '365' ? '12 tháng' : n + ' ngày gần nhất'}</option>)}</Select></Field>}{view === 'dashboard' && <Field title="Gộp số liệu theo" hint="Gộp số liệu theo từng ngày hoặc tổng từng tuần. Tuần đầu và cuối có thể chưa đủ bảy ngày."><Select value={draft.frequency} onChange={v => patch('frequency', v)} disabled={!!busy}><option value="day">Ngày</option><option value="week">Tuần</option></Select></Field>}{view === 'plan' && <Field title="Lịch sử dùng để dự báo" hint="Lịch sử của đúng sản phẩm và kho làm ngữ cảnh cho Chronos-2 huấn luyện sẵn. Không huấn luyện lại mô hình."><Select value={draft.training} disabled={!!busy} onChange={v => patch('training', v)}><option value="90">3 tháng</option><option value="180">6 tháng</option><option value="365">12 tháng</option></Select></Field>}<Field title="Sản phẩm" hint="Tìm và chọn theo mã hoặc tên. Các kết quả dùng cùng sản phẩm và đơn vị của kho đã chọn."><ProductSelector items={directory.products.filter(p => p.warehouse_id === draft.warehouse)} value={draft.sku} onChange={v => patch('sku', v)} disabled={!draft.warehouse || !directoryLoaded || !!busy}/></Field></section></fieldset>},
              {id:'confirm',title:'Xác nhận và cập nhật',description:'Đọc dữ liệu từ Bán hàng, Kho và Vận chuyển',signature:JSON.stringify(draft),valid:scopeValid,content:<dl className="iw-summary"><div><dt>Kho</dt><dd>{directory.warehouses.find(w=>w.id===draft.warehouse)?.name}</dd></div><div><dt>Mặt hàng</dt><dd>{directory.products.find(p=>p.warehouse_id===draft.warehouse&&p.sku===draft.sku)?.name||draft.sku}</dd></div><div><dt>Khoảng kiểm tra</dt><dd>{draft.horizon} ngày tới</dd></div><div><dt>Kết quả</dt><dd>Đối chiếu đơn đang chờ, tồn kho, hàng về và dự báo Chronos-2. Chưa tạo giao dịch.</dd></div></dl>},
            ]}/>
            {!directoryLoaded&&!busy&&catalogLoaded&&sources.stock&&<Button variant="outline" onClick={()=>void perform('Đang tải danh sách kho',()=>loadDirectory())}>Tải lại danh sách kho</Button>}
          </WorkspaceDialog>
          {catalogLoaded && !!missing.length && <div className="pw-source-setup" role="status"><Info size={24}/><div><h2>Cần bổ sung nguồn để phân tích đúng kho và từng đơn hàng</h2><p>Nhờ IT công bố hoặc cấp quyền các nguồn sau: {missing.map(k => sourceRoles[k][1]).join(', ')}. Báo cáo chỉ tính khi có đủ các nguồn bắt buộc.</p>{admin && <details><summary>Tên giao diện dữ liệu cần kết nối</summary><ul>{missing.map(k => <li key={k}>{sourceRoles[k][0]}</li>)}</ul></details>}</div></div>}
          {!plan && !busy && !error && !missing.length && <Card className="pw-empty-state"><CardContent><h2>Chưa có kết quả kiểm tra</h2><p>Chọn kho và kỳ kế hoạch, sau đó bấm {view === 'plan' ? 'Tính lại dự báo' : 'Tải dữ liệu và tính lại'} để đối chiếu đơn hàng và nguồn hàng.</p></CardContent></Card>}
          {plan && <><div className="pw-applied-context"><span>{plan.warehouses.find(w => w.id === plan.warehouse_id)?.name || 'Kho đang phân tích'}</span><span>Khoảng kiểm tra {dateLabel(plan.start)} – {dateLabel(plan.end)}</span></div>
            {!!plan.missing_sources.length && <p className="pw-data-warning">Chưa có {plan.missing_sources.map(k => sourceRoles[k as keyof typeof sourceRoles]?.[1] || k).join(', ')}. Phần chưa có dữ liệu được để trống, không tính là 0.</p>}
            {view === 'dashboard' && <OperationsDashboard plan={plan} selected={selected} filter={filter} onFilter={setFilter} onInspect={inspect} onOrders={orderNumber=>{setOrderQuery(orderNumber||'');setOpenHandoffList(true);setView('orders');}} disabled={blocked || dirty} evidence={evidenceButton} onTasks={decisionCapabilities?.enabled?()=>setView('tasks'):undefined} onOutcomes={decisionCapabilities?.enabled?()=>setView('outcomes'):undefined}/>}
            {view==='plan'&&selected&&<PlanningWorkspace key={plan.warehouse_id+':'+plan.as_of} plan={plan} selected={selected} proposal={proposal} onProposal={setProposal} calculated={calculated} scenario={scenario} dirty={dirty} scenarioDirty={scenarioDirty} busy={!!busy} error={error} request={request} capabilities={decisionCapabilities} onCreated={()=>{setView('decisions');setNotice('Đã lưu hồ sơ. Mở hồ sơ để xem trạng thái và các bước tiếp theo.');}} onInputs={()=>setInputsOpen(true)} onScope={()=>setScopeOpen(true)} onCompare={()=>compare()} onSave={()=>compare(true)} onExport={exportProposal} onCancel={cancel} perform={perform} evidence={evidenceButton}/>}
            {view === 'orders' && <HandoffWorkspace key={plan.warehouse_id+':'+plan.as_of+':'+orderQuery} plan={plan} initialOrder={orderQuery} initialOpen={openHandoffList}/>}
          </>}
        </>}
        {view === 'configuration' && admin && products.length > 0 && <><Configuration products={products} request={request} onError={report} onEvidence={e => setExplanation({ title: 'Kiểm tra cấu hình', meaning: 'Chi tiết cấu hình và dữ liệu thử dành cho IT.', technical: e })} onUse={r => void perform('Đang đọc báo cáo tùy chỉnh', async () => { const p = products.find(p => p.id === r.config.data_product_id)!; setCustom(await request<Overview>('/overview', { ...productRef(p), configuration_id: r.id, days: 30 }));setCustomOpen(true); })}/>{custom && <><Button variant="outline" onClick={()=>setCustomOpen(true)}>Mở báo cáo tùy chỉnh</Button><WorkspaceDialog open={customOpen} onClose={()=>setCustomOpen(false)} title={custom.config.name} description="Báo cáo dùng cấu hình đã lưu, trong quyền đọc nguồn hiện tại." wide><Panel title={custom.config.name} subtitle="Báo cáo tùy chỉnh · cấu hình của IT"><Plot data={custom.analytics.series.map(p => ({ day: p.timestamp, target: p.target }))} unit={unitLabel(custom.config.unit)} lines={[{ key: 'target', name: 'Giá trị theo cấu hình', color: blue }]}/></Panel></WorkspaceDialog></>}</>}
        {busy && <div className="dw-progress pw-progress" role="status"><LoaderCircle className="animate-spin" size={16}/>{busy}…{catalogLoaded && <button className="pw-text-button" onClick={cancel}>Dừng chờ</button>}</div>}
      </div>
    </main>
    {plan && selected && <Sheet open={inputsOpen} onOpenChange={setInputsOpen}><SheetContent className="pw-inputs-sheet w-full sm:max-w-2xl"><SheetHeader><SheetTitle>Số liệu dùng để kiểm tra đủ hàng</SheetTitle><SheetDescription>Dự báo ban đầu, đơn đang chờ và lịch hàng về trước khi áp dụng giả định.</SheetDescription></SheetHeader><div className="pw-inputs-body"><Panel title="Hàng dự kiến về và lượng hàng cần theo ngày" subtitle="Cùng sản phẩm, cùng kho · đối chiếu từng ngày để chọn thời điểm bổ sung"><Plot data={forecastPoints} unit={unitLabel(selected.uom)} lines={[{ key: 'band', name: 'Vùng dao động', color: blue }, { key: 'p50', name: 'Lượng đặt dự kiến mỗi ngày', color: blue, dash: '5 4' }, ...(hasPlannedInbound ? [{ key: 'inbound', name: 'Hàng sẽ về theo kế hoạch', color: cyan, kind: 'bar' as const }] : []), ...(hasKnownDue ? [{ key: 'known', name: 'Hàng còn cần cho đơn đến hạn', color: purple, kind: 'bar' as const }] : [])]}/><p className="pw-panel-note">{!hasPlannedInbound && <>Không có hàng dự kiến về trong kỳ được đưa vào tính toán. </>}Cột hàng về và đơn đến hạn là lượng trong từng ngày. Đường xanh là lượng khách có thể đặt thêm. Tồn đầu kỳ đã được tính trong biểu đồ đủ/thiếu bên dưới. Vùng nhạt cho thấy mức thấp–cao từ lịch sử. Lượng đặt thực tế có thể nằm ngoài vùng này. Khuyến mại hoặc khách đặt đột biến cần thử thêm ở phần giả định bên dưới.</p></Panel><Panel title="Các đợt hàng dự kiến về" subtitle="Chỉ tính vào nguồn cung từ đúng thời điểm dự kiến nhận"><div className="pw-arrivals">{selectedInbound.length ? selectedInbound.map(p => <div key={p.plan_id}><CalendarDays size={18}/><span><strong>{fmt(p.quantity)} {unitLabel(p.uom)}</strong><small>Dự kiến {dateLabel(p.expected_at)}</small></span><span className={p.overdue ? 'pw-danger-text' : ''}>{p.overdue ? 'Quá ngày về · cần xác nhận' : 'Theo kế hoạch'}</span></div>) : <p>Chưa có kế hoạch hàng về được chia sẻ cho sản phẩm này.</p>}</div><p className="pw-panel-note">Kế hoạch chưa phải hàng đã nhận. Kế hoạch quá ngày về không tự được cộng vào hàng sẵn có.</p></Panel></div></SheetContent></Sheet>}

    <BusinessEvidence value={explanation} admin={admin} onClose={() => setExplanation(null)}/>
    <Sheet open={savedOpen} onOpenChange={setSavedOpen}><SheetContent className="iw-saved-dialog"><SheetHeader><SheetTitle>Dự báo và phương án đã lưu</SheetTitle><SheetDescription>Kết quả phản ánh dữ liệu tại thời điểm lưu. Cập nhật dữ liệu trước khi sử dụng cho kế hoạch mới.</SheetDescription></SheetHeader><div className="dw-saved">{!saved.length && !savedForecasts.length && <p>Chưa có kết quả đã lưu.</p>}{savedForecasts.map(f => <button key={f.id} disabled={!!busy} onClick={() => void perform('Đang kiểm tra quyền kết quả', async () => setSavedForecast(await request<Forecast>(`/forecasts/${f.id}`)))}><span>{f.config.entities[0] || f.config.name}<small>{dateLabel(f.created_at)} · {f.config.forecast_horizon} ngày</small></span></button>)}{savedForecast && <div><h3>Dự báo đã lưu</h3><Plot data={savedForecast.points.filter(p => p.item_id === savedForecast.config.entities[0]).map(p => ({ day: p.timestamp, predicted: p.p50, band: [p.p10, p.p90] }))} unit={unitLabel(savedForecast.config.unit)} lines={[{ key: 'band', name: 'Vùng dao động', color: blue }, { key: 'predicted', name: 'Lượng dự kiến', color: purple, dash: '5 4' }]}/><p className="dw-muted">Kết quả cũ dùng để tham khảo. Tạo dự báo mới để lập kế hoạch hiện tại.</p></div>}{saved.map(s => <div className="pw-saved-card" key={s.id}><strong>{String((s.evidence.assumptions as Record<string, unknown>)?.sku || 'Phương án nhập hàng')}</strong><ScenarioShortage value={s}/><small>{s.id?.slice(0, 8)}</small></div>)}</div></SheetContent></Sheet>
  </div>;
}
