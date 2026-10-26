// SPDX-License-Identifier: GPL-3.0-or-later
import { WorkspaceModal as Sheet } from './interactive-workspace';
import { useEffect, useId, useRef, useState } from 'react';
import { AlertCircle, ArrowRight, Check, ChevronLeft, ChevronRight, Clock3, Info, LoaderCircle, Package, RefreshCw, Search, Warehouse as WarehouseIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cell } from '@/lib/format';
import { DialogContent as SheetContent, DialogDescription as SheetDescription, DialogHeader as SheetHeader, DialogTitle as SheetTitle } from '@/components/ui/dialog';
import { Select, fmt, unitLabel } from './components';
import { orderDateTime } from './order-readiness';
import { WarehouseAnalysis } from './warehouse-analysis';
import type { DecisionRequest } from './decision-dossiers';
import type { Explanation } from './business-evidence';
import { OrderSupplyBadge, OrderSupplyDetails } from './order-supply';
import { stockState, warehouseCounts, type StockState, type Warehouse, type WarehouseOverview } from './warehouse-overview-types';
import './warehouse-overview.css';
import { InteractiveFlow, WorkspaceDialog } from './interactive-workspace';

const stateNames: Record<StockState, string> = { risk: 'Có thể thiếu', clear: 'Chưa thấy thiếu', unknown: 'Chưa có dự báo', pending: 'Đang tính…' };
const orderStates: Record<string, string> = { CONFIRMED: 'Đã xác nhận', WAITING_STOCK: 'Chờ hàng', RESERVED: 'Đã giữ hàng', FULFILLING: 'Đang chuẩn bị', PARTIALLY_HANDED_OVER: 'Đã bàn giao một phần', CANCEL_PENDING: 'Chờ hủy' };
const normalize = (value: string) => value.toLocaleLowerCase('vi').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd');
const pageSize = 20;
const orderQuantityColumn = { field: 'ordered_quantity', label: 'Số lượng đặt', type: 'decimal', scale: 3 };

function StockBadge({ state }: { state: StockState }) {
  const Icon = state === 'pending' ? LoaderCircle : state === 'risk' ? AlertCircle : state === 'clear' ? Check : Info;
  return <span className={`wo-badge wo-${state}`}><Icon size={13} className={state === 'pending' ? 'animate-spin' : undefined}/>{stateNames[state]}</span>;
}

function Coverage({ warehouse }: { warehouse: Warehouse }) {
  const counts = warehouseCounts(warehouse), total = warehouse.products.length;
  return <div className="wo-coverage">
    <div className="wo-coverage-bar" role="img" aria-label={`Khả năng đủ hàng của ${warehouse.warehouse_name}: ${counts.risk} mặt hàng có thể thiếu, ${counts.clear} chưa thấy thiếu, ${counts.unknown} chưa có dự báo, ${counts.pending} đang tính.`}>
      {(['risk', 'clear', 'unknown', 'pending'] as const).filter(state => counts[state]).map(state => <span key={state} className={`wo-segment-${state}`} style={{ width: `${counts[state] / total * 100}%` }}/>) }
    </div>
    <div className="wo-coverage-legend">{(['risk', 'clear', 'unknown', 'pending'] as const).filter(state => counts[state]).map(state => <span key={state}><i className={`wo-segment-${state}`}/>{counts[state]} {state === 'risk' ? 'có thể thiếu' : state === 'clear' ? 'chưa thấy thiếu' : state === 'pending' ? 'đang tính' : 'chưa có dự báo'}</span>)}</div>
  </div>;
}

export function WarehouseOverviewView({ result, busy, error, horizon, onHorizon, onReload, onRetry, onAnalyze, analysisWarehouseIds = [], initialWarehouseId = '', onSelectWarehouse, onReviewOrder, request, onDenied, onEvidence }: {
  request?: DecisionRequest; onDenied?: (e:unknown)=>void; onEvidence?: (e:Explanation)=>void;
  result: WarehouseOverview | null; busy: boolean; error: string; horizon: string;
  onHorizon: (value: string) => void; onReload: () => void; onRetry: (warehouse: Warehouse) => void;
  onAnalyze: (warehouseId: string, sku?: string, horizon?: number) => void; analysisWarehouseIds?: string[];
  initialWarehouseId?: string; onSelectWarehouse?: (warehouseId:string)=>void;
  onReviewOrder?: (warehouseId:string,orderNumber:string,horizon:number)=>void;
}) {
  const [selectedId, setSelectedId] = useState(initialWarehouseId), [tab, setTab] = useState('summary');
  const [search, setSearch] = useState(''), [filter, setFilter] = useState('all'), [page, setPage] = useState(0);
  const [expandedOrder, setExpandedOrder] = useState<string | null>(null);
  const [focusNode,setFocusNode]=useState<string|null>(null);
  const [allOrdersOpen,setAllOrdersOpen]=useState(false);
  const [orderFilter,setOrderFilter]=useState('all');
  const [orderSearch,setOrderSearch]=useState('');
  const [orderPage,setOrderPage]=useState(0);
  const horizonId = useId();
  const [directoryOpen,setDirectoryOpen]=useState(false);
  const detailRef = useRef<HTMLElement | null>(null);
  const items = result?.items || [];
  const selected = items.find(w => w.warehouse_id === selectedId) || items[0];
  const totals = items.reduce((sum, w) => {
    const counts = warehouseCounts(w);
    return { products: sum.products + w.products.length, orders: sum.orders + w.open_orders.length, risk: sum.risk + counts.risk, clear: sum.clear + counts.clear, unknown: sum.unknown + counts.unknown, pending: sum.pending + counts.pending };
  }, { products: 0, orders: 0, risk: 0, clear: 0, unknown: 0, pending: 0 });
  const dirty = !!result && Number(horizon) !== result.horizon;
  const canAnalyze = !!selected && analysisWarehouseIds.includes(selected.warehouse_id);
  const analysisDisabled = dirty || busy;
  const counts = selected ? warehouseCounts(selected) : null;
  const term = normalize(search.trim());
  const products = selected?.products.filter(product => (filter === 'all' || filter === 'unknown' && ['unknown', 'pending'].includes(stockState(selected, product)) || stockState(selected, product) === filter) && normalize(`${product.name || ''} ${product.sku}`).includes(term)).sort((a, b) => {
    const rank = { risk: 0, clear: 1, pending: 2, unknown: 3 };
    return rank[stockState(selected, a)] - rank[stockState(selected, b)] || (a.name || a.sku).localeCompare(b.name || b.sku, 'vi');
  }) || [];
  const productName = (sku: string, uom: string) => selected?.products.find(p => p.sku === sku && p.uom === uom)?.name || sku;
  const orderTerm=normalize(orderSearch.trim());
  const orders = selected?.open_orders.filter(order => (orderFilter==='all'||(order.supply_check?.status||'unknown')===orderFilter) && normalize(`${order.order_number} ${order.lines.map(line => `${line.sku} ${productName(line.sku, line.uom)}`).join(' ')}`).includes(orderTerm)).sort((a, b) => Date.parse(a.deadline) - Date.parse(b.deadline) || a.order_number.localeCompare(b.order_number)) || [];
  const activePage = Math.min(orderPage, Math.max(0, Math.ceil(orders.length / pageSize) - 1));
  const visibleOrders = orders.slice(activePage * pageSize, (activePage + 1) * pageSize);
  const expanded = orders.find(order => order.order_id === expandedOrder);
  useEffect(()=>{if(selected)onSelectWarehouse?.(selected.warehouse_id);},[selected?.warehouse_id]);
  useEffect(() => { setSearch(''); setFilter('all'); setOrderFilter('all'); setOrderSearch(''); setOrderPage(0); setPage(0); setExpandedOrder(null); }, [result?.read_at]);
  function choose(warehouse: Warehouse, nextTab: string, scroll = true) {
    setDirectoryOpen(false); setAllOrdersOpen(nextTab==='orders'); setFocusNode(nextTab==='inventory'?'warehouse':null); setSelectedId(warehouse.warehouse_id); setTab(previous=>nextTab==='summary'?previous:'summary'); setOrderFilter('all'); setOrderSearch(''); setOrderPage(0); setSearch(''); setFilter('all'); setPage(0); setExpandedOrder(null);
    if (scroll) detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function chooseTab(value: string) {
    if(value==='inventory'){setFocusNode('warehouse');setSearch('');setFilter('all');setPage(0);}
    else if(value==='orders'){setAllOrdersOpen(true);setOrderSearch('');setOrderFilter('all');setOrderPage(0);}
    else {setTab(tab==='analysis'?'summary':'analysis');}
    setExpandedOrder(null);
  }
  const inventoryContent = selected && result ? <div className="wo-orders-dialog wo-inventory-dialog">
            <div className="wo-stock-context"><div><strong>Tồn kho và dự báo theo mặt hàng</strong><p>Thiếu dự kiến tính cả đơn đang chờ và lượng khách có thể đặt thêm.</p></div><button type="button" onClick={()=>chooseTab('orders')}>Xem số lượng từng đơn<ArrowRight size={14}/></button></div>
            <div className="wo-inventory-toolbar"><div className="wo-filters" role="group" aria-label="Lọc mặt hàng">{[['all', 'Tất cả', selected.products.length], ['risk', 'Có thể thiếu', counts?.risk], ['unknown', 'Chưa có dự báo', (counts?.unknown || 0) + (counts?.pending || 0)]] .map(([key, label, count]) => <button key={key} type="button" aria-pressed={filter === key} onClick={() => {setFilter(String(key));setPage(0);}}>{label}<span>{count}</span></button>)}</div><label className="wo-search"><Search size={16}/><Input aria-label="Tìm mặt hàng trong kho" placeholder="Tìm tên hoặc mã hàng…" value={search} onChange={event => {setSearch(event.target.value);setPage(0);}}/></label></div>
            {selected.planning_status === 'pending' ? <div className="wo-notice wo-notice-pending" role="status"><LoaderCircle size={16} className="animate-spin"/><p>Tồn kho đã sẵn sàng. <strong>Đang tính lượng thiếu</strong> cho {result.horizon} ngày tới; bạn vẫn có thể tìm hàng hoặc xem đơn.</p></div>
              : selected.planning_issue ? <div className="wo-notice" role="status"><Info size={17}/><div><strong>{selected.planning_status === 'unavailable' ? 'Chưa tính được dự báo' : 'Chưa đủ dữ liệu để dự báo'}</strong><p>Tồn kho và đơn hàng vẫn xem được. Chưa thể kết luận kho đủ hay thiếu hàng.</p><details><summary>Xem lý do</summary><p>{selected.planning_issue.message}</p></details></div>{selected.planning_status === 'unavailable' && <Button variant="outline" size="sm" disabled={dirty} onClick={() => onRetry(selected)}>Thử tính lại</Button>}</div>
              : <div className="wo-result-note"><span><i className="wo-segment-risk"/>{counts?.risk || 0} mặt hàng có thể thiếu trong {result.horizon} ngày tới</span>{selected.plan_read_at && <span>Dự báo tính lúc {orderDateTime(selected.plan_read_at)}</span>}</div>}
            <div className="wo-table-scroll" tabIndex={0} role="region" aria-label="Bảng dữ liệu kho"><table className="wo-stock-table" aria-label={`Tồn và kế hoạch ${selected.warehouse_code}`}><thead><tr><th>Mặt hàng</th><th>Trong kho</th><th>Đã giữ cho đơn</th><th>Có thể dùng</th><th>Thiếu dự kiến</th><th>Tiếp theo</th></tr></thead><tbody>{products.slice(page * pageSize, (page + 1) * pageSize).map(product => {
              const state = stockState(selected, product);
              return <tr key={product.sku} className={state === 'risk' ? 'wo-row-risk' : undefined}><td><strong>{product.name || product.sku}</strong><small>{product.sku} · {unitLabel(product.uom)}</small></td><td>{fmt(Number(product.on_hand))}</td><td>{fmt(Number(product.reserved))}</td><td><strong>{fmt(Number(product.available))}</strong></td><td>{state === 'pending' ? <span className="wo-muted">Đang tính…</span> : state === 'unknown' ? <span className="wo-muted">Chưa có dự báo</span> : <span className={state === 'risk' ? 'wo-shortage' : 'wo-muted'}>{fmt(product.shortage)} <small>{unitLabel(product.uom)}</small></span>}</td><td>{state === 'risk' && canAnalyze ? <Button size="sm" variant="ghost" disabled={analysisDisabled} onClick={() => onAnalyze(selected.warehouse_id, product.sku, result.horizon)}>Thử bổ sung<ArrowRight size={13}/></Button> : <StockBadge state={state}/>}</td></tr>;
            })}</tbody></table></div>
            {!products.length && <div className="wo-empty"><Search size={24}/><strong>{selected.products.length ? 'Không có mặt hàng khớp bộ lọc' : 'Chưa có số liệu tồn kho'}</strong><p>{selected.products.length ? 'Thử tên, mã hàng khác hoặc chọn Tất cả.' : 'Chưa có số liệu không có nghĩa kho không có hàng.'}</p>{(search || filter !== 'all') && <Button size="sm" variant="outline" onClick={() => { setSearch(''); setFilter('all'); }}>Bỏ bộ lọc</Button>}</div>}
            <details className="wo-explanation"><summary><Info size={15}/>Cách đọc số liệu tồn kho và dự báo</summary><div><p><strong>Có thể dùng</strong> là hàng trong kho trừ phần đã giữ cho các đơn khác.</p><p><strong>Thiếu dự kiến</strong> là lượng thiếu lớn nhất của mặt hàng này tại một thời điểm trong {result.horizon} ngày tới, tính cả đơn đang chờ và lượng khách có thể đặt thêm. Không cộng các ngày thiếu với nhau. Dự báo có thể có số lẻ.</p><p>Biểu đồ trên thẻ kho đếm số mặt hàng, không cộng chung số lượng của các đơn vị khác nhau. Kết quả đủ hàng vẫn cần kiểm tra thời gian chuẩn bị và lịch đơn vị vận chuyển đến lấy.</p><p>Làm mới dữ liệu chỉ tải và tính lại báo cáo; không sửa tồn kho hoặc tạo đơn mua.</p></div></details>

            {!!products.length && <div className="wo-pagination"><span>Hiển thị {page * pageSize + 1}–{Math.min((page + 1) * pageSize, products.length)} / {products.length} mặt hàng</span><div><Button variant="outline" size="sm" disabled={page===0} onClick={()=>setPage(page-1)}>Trang trước</Button><Button variant="outline" size="sm" disabled={(page+1)*pageSize>=products.length} onClick={()=>setPage(page+1)}>Trang sau</Button></div></div>}

  </div> : null;
  const orderContent = selected && result ? <>
            <div className="wo-orders-toolbar"><Select label="Lọc tình trạng hàng của đơn" value={orderFilter} onChange={value=>{setOrderFilter(value);setOrderPage(0);setExpandedOrder(null);}}><option value="all">Tất cả đơn</option><option value="risk">Có thể thiếu hàng</option><option value="clear">Chưa thấy thiếu</option><option value="unknown">Chưa đủ dữ liệu</option><option value="outside_horizon">Ngoài kỳ dự báo</option></Select><p>{selected.open_orders.length} đơn chưa hoàn tất · gồm cả đơn ngoài {result.horizon} ngày đang dự báo</p><label className="wo-search"><Search size={16}/><Input aria-label="Tìm đơn hàng trong kho" placeholder="Tìm mã đơn hoặc mặt hàng…" value={orderSearch} onChange={event => { setOrderSearch(event.target.value); setOrderPage(0); setExpandedOrder(null); }}/></label></div>
            <div className="wo-table-scroll" tabIndex={0} role="region" aria-label="Bảng dữ liệu kho"><table aria-label={`Đơn hàng ${selected.warehouse_code}`}><thead><tr><th>Mã đơn</th><th>Hạn giao cho đơn vị vận chuyển</th><th>Trạng thái đơn</th><th>Hàng có đủ không?</th><th>Mặt hàng và số lượng đặt</th><th>Chi tiết</th></tr></thead><tbody>{visibleOrders.map(order => <tr key={order.order_id}><td><strong>{order.order_number}</strong></td><td>{orderDateTime(order.deadline)}{Date.parse(order.deadline) < Date.now() && <small className="wo-warning-text">Đã qua hạn bàn giao</small>}</td><td>{orderStates[order.status] || 'Đang xử lý'}</td><td><OrderSupplyBadge warehouse={selected} order={order} onClick={() => setExpandedOrder(order.order_id)}/></td><td><ul className="wo-order-quantities" aria-label={`Số lượng đặt trong ${order.order_number}`}>{order.lines.map((line,index)=><li key={index}><span>{productName(line.sku,line.uom)}<small>{line.sku}</small></span><strong>{cell(line.ordered_quantity,orderQuantityColumn)} {unitLabel(line.uom)}</strong></li>)}</ul></td><td><Button size="sm" variant="ghost" aria-label={`Xem hàng của ${order.order_number}`} aria-expanded={expandedOrder === order.order_id} onClick={() => setExpandedOrder(expandedOrder === order.order_id ? null : order.order_id)}>Xem chi tiết</Button></td></tr>)}</tbody></table></div>
            {!orders.length && <div className="wo-empty"><Package size={24}/><strong>{orderSearch || orderFilter!=='all' ? 'Không tìm thấy đơn phù hợp' : 'Không có đơn chưa hoàn tất ở kho này'}</strong><p>{orderSearch || orderFilter!=='all' ? 'Thử tìm kiếm khác hoặc chọn Tất cả đơn.' : 'Bạn có thể kiểm tra tồn kho ở thẻ bên cạnh.'}</p></div>}
            <Sheet open={!!expanded} onOpenChange={open=>{if(!open)setExpandedOrder(null);}}><SheetContent className="wo-order-sheet" showCloseButton={false}>
              <SheetHeader><div className="wo-sheet-title"><SheetTitle>{expanded?.order_number || 'Chi tiết đơn hàng'}</SheetTitle><Button variant="outline" size="sm" onClick={()=>setExpandedOrder(null)}>Đóng chi tiết</Button></div><SheetDescription>{selected.warehouse_name} · {expanded ? orderStates[expanded.status] || 'Đang xử lý' : ''}</SheetDescription></SheetHeader>
              {expanded&&<><div className="wo-order-meta"><span>Hạn bàn giao cho đơn vị vận chuyển</span><strong>{orderDateTime(expanded.deadline)}</strong></div><OrderSupplyDetails warehouse={selected} order={expanded} productName={productName} onAnalyze={canAnalyze&&!analysisDisabled ? sku=>onAnalyze(selected.warehouse_id,sku,result!.horizon) : undefined}/><div className="wo-drawer-actions">{canAnalyze&&onReviewOrder&&<Button variant="outline" disabled={analysisDisabled} onClick={()=>onReviewOrder(selected.warehouse_id,expanded.order_number,result!.horizon)}>Kiểm tra lịch bàn giao của đơn</Button>}</div><section className="wo-order-lines" aria-label={`Mặt hàng của ${expanded.order_number}`}><h3>Các mặt hàng trong đơn</h3><ul>{expanded.lines.map((line,index)=><li key={index}><span>{productName(line.sku,line.uom)}<small>{line.sku}</small></span><strong>{cell(line.ordered_quantity,orderQuantityColumn)} {unitLabel(line.uom)}</strong></li>)}</ul></section></>}
            </SheetContent></Sheet>
            {!!orders.length && <div className="wo-pagination"><span>Hiển thị {activePage * pageSize + 1}–{Math.min((activePage + 1) * pageSize, orders.length)} / {orders.length} đơn</span><div><Button variant="outline" size="sm" disabled={activePage === 0} onClick={() => { setOrderPage(activePage - 1); setExpandedOrder(null); }}><ChevronLeft size={15}/>Trang trước</Button><Button variant="outline" size="sm" disabled={(activePage + 1) * pageSize >= orders.length} onClick={() => { setOrderPage(activePage + 1); setExpandedOrder(null); }}>Trang sau<ChevronRight size={15}/></Button></div></div>}

  </> : null;
  return <section className="wo-overview" aria-label="Tổng quan tất cả kho">
    <div className="wo-toolbar"><p>Xem kho nào cần chú ý, kiểm tra hàng và theo dõi các đơn chưa hoàn tất.</p><div className="wo-toolbar-actions">
      <div className="wo-horizon"><label htmlFor={horizonId}>Khoảng thời gian dự báo</label><Select id={horizonId} label="Khoảng thời gian dự báo" value={horizon} onChange={onHorizon} disabled={busy}>{[7, 14, 30, 90].map(days => <option key={days} value={days}>{days} ngày tới</option>)}</Select></div>
      <Button disabled={busy} onClick={onReload}><RefreshCw size={15} className={busy ? 'animate-spin' : undefined}/>{busy ? 'Đang tải…' : 'Làm mới dữ liệu'}</Button>
    </div></div>
    {dirty && <div className="wo-notice" role="status"><Info size={16}/><p>Chưa áp dụng khoảng thời gian mới. Bấm <strong>Làm mới dữ liệu</strong> để tính lại. Kết quả dưới đây vẫn thuộc {result!.horizon} ngày.</p></div>}
    {error && <div className="wo-notice wo-notice-error" role="alert"><AlertCircle size={18}/><p>{error}</p></div>}
    {busy && <div className="wo-loading" role="status"><LoaderCircle size={20} className="animate-spin"/><div><strong>Đang tải tồn kho và đơn hàng…</strong><p>Các kho sẽ hiện trước, dự báo được tính tiếp ở nền.</p></div></div>}
    {result && <>
      <div className="wo-summary" aria-label="Số liệu tổng quan">
        <article className="wo-summary-card"><span className="wo-summary-icon"><WarehouseIcon size={19}/></span><div><span>Kho đang theo dõi</span><strong>{items.length}</strong><small>{totals.products} mặt hàng theo kho</small></div></article>
        <article className="wo-summary-card"><span className="wo-summary-icon wo-icon-purple"><Package size={19}/></span><div><span>Đơn chưa hoàn tất</span><strong>{totals.orders}</strong><small>Bao gồm mọi hạn bàn giao</small></div></article>
        <article className="wo-summary-card"><span className="wo-summary-icon wo-icon-amber"><AlertCircle size={19}/></span><div><span>Mặt hàng có thể thiếu</span><strong>{!totals.risk && (totals.pending || totals.unknown && !totals.clear) ? '—' : totals.risk}<em>mặt hàng</em></strong><small>{totals.risk ? `Trong ${result.horizon} ngày tới` : totals.pending ? 'Dự báo đang cập nhật' : totals.unknown ? 'Còn mặt hàng chưa có dự báo' : 'Chưa thấy thiếu theo dự báo'}</small></div></article>
        <article className="wo-summary-card"><span className="wo-summary-icon wo-icon-gray"><Info size={19}/></span><div><span>Chưa có kết quả dự báo</span><strong>{totals.unknown + totals.pending}<em>mặt hàng</em></strong><small>{totals.pending ? `${totals.pending} đang tính${totals.unknown ? ` · ${totals.unknown} chưa đủ dữ liệu` : ''}` : totals.unknown ? 'Xem lý do ở từng kho' : 'Đã tính cho mọi mặt hàng'}</small></div></article>
      </div>
      <div className="wo-directory-toggle"><button type="button" onClick={()=>setDirectoryOpen(true)}><WarehouseIcon size={15}/>Xem tổng quan {items.length} kho<ArrowRight size={14}/></button><span className="wo-read-at"><Clock3 size={13}/>Tải lúc {orderDateTime(result.read_at)}</span></div>
      <Sheet open={directoryOpen} onOpenChange={setDirectoryOpen}><SheetContent className="wo-directory-sheet w-full sm:max-w-xl"><SheetHeader><SheetTitle>Các kho được phép xem</SheetTitle><SheetDescription>Chọn tồn kho hoặc đơn hàng để mở đúng kho.</SheetDescription></SheetHeader><div className="wo-warehouses">{items.map(warehouse => {
        const c = warehouseCounts(warehouse);
        const state: StockState = c.risk ? 'risk' : c.pending ? 'pending' : c.unknown || !warehouse.products.length ? 'unknown' : 'clear';
        const active = selected?.warehouse_id === warehouse.warehouse_id;
        return <article key={warehouse.warehouse_id} className={`wo-warehouse${active ? ' wo-warehouse-selected' : ''}`} aria-label={warehouse.warehouse_name}>
          <div className="wo-warehouse-heading"><span className="wo-warehouse-icon"><WarehouseIcon size={23}/></span><div><h3>{warehouse.warehouse_name}</h3><span className="wo-code">{warehouse.warehouse_code}</span></div><StockBadge state={state}/></div>
          <div className="wo-warehouse-numbers"><div><strong>{warehouse.products.length}</strong><span>Mặt hàng</span></div><div><strong>{warehouse.open_orders.length}</strong><span>Đơn chưa hoàn tất</span></div><div><strong className={c.risk ? 'wo-warning-text' : ''}>{c.pending && !c.risk ? '…' : c.risk || (c.unknown ? '—' : 0)}</strong><span>Mặt hàng có thể thiếu</span></div></div>
          <Coverage warehouse={warehouse}/>
          <div className="wo-warehouse-actions"><Button variant={active && tab === 'inventory' ? 'secondary' : 'ghost'} size="sm" aria-pressed={active && tab === 'inventory'} onClick={() => choose(warehouse, 'inventory')}>Xem tồn kho<ArrowRight size={14}/></Button><Button variant={active && tab === 'orders' ? 'secondary' : 'ghost'} size="sm" aria-pressed={active && tab === 'orders'} onClick={() => choose(warehouse, 'orders')}>Xem đơn hàng<ArrowRight size={14}/></Button></div>
        </article>;
      })}</div></SheetContent></Sheet>
      {!items.length && <div className="wo-empty"><WarehouseIcon size={30}/><h2>Chưa có kho được phép xem</h2><p>Nhờ quản trị viên kiểm tra kết nối và quyền xem dữ liệu kho.</p></div>}
      {selected && <section ref={detailRef} className="wo-detail" aria-label={`Chi tiết ${selected.warehouse_name}`}>
        <div className="wo-detail-heading"><div className="wo-current-warehouse"><label htmlFor={`${horizonId}-warehouse`}>Kho đang xem</label><Select id={`${horizonId}-warehouse`} label="Kho đang xem" value={selected.warehouse_id} onChange={id=>{const warehouse=items.find(w=>w.warehouse_id===id);if(warehouse)choose(warehouse,'summary',false);}}>{items.map(w=><option key={w.warehouse_id} value={w.warehouse_id}>{w.warehouse_name} ({w.warehouse_code})</option>)}</Select></div>{canAnalyze && <Button variant="outline" disabled={analysisDisabled} onClick={() => onAnalyze(selected.warehouse_id, undefined, result.horizon)}>Thử phương án nhập hàng<ArrowRight size={15}/></Button>}</div>
        <div className="iw-overview-flow"><InteractiveFlow label="Liên thông Bán hàng, Kho và Vận chuyển" onSelect={id=>{setSearch('');setFilter('all');setPage(0);setFocusNode(id);}} nodes={[
          {id:'sales',title:'Bán hàng',value:`${selected.open_orders.length} đơn đang mở`,description:'Xem đơn đến hạn và lượng khách đã đặt'},
          {id:'warehouse',title:'Kho',value:`${selected.products.length} mặt hàng`,description:'Đối chiếu hàng có thể dùng và hàng có thể thiếu',state:counts?.risk?'attention':'unknown'},
          {id:'logistics',title:'Vận chuyển',description:'Chọn đơn để kiểm tra lịch lấy hàng và hạn bàn giao'},
        ]}/></div>
        <WorkspaceDialog open={!!focusNode} onClose={()=>setFocusNode(null)} title={`${focusNode==='sales'?'Đơn từ Bán hàng':focusNode==='warehouse'?'Nguồn hàng trong Kho':'Kiểm tra lịch bàn giao'} · ${selected.warehouse_name}`} description="Dữ liệu thuộc kho đang chọn. Chọn bản ghi để đi tiếp; mở sơ đồ không tạo giao dịch." wide>
          {focusNode==='warehouse'?inventoryContent:<>
            <p>{focusNode==='logistics'?'Chọn một đơn để đọc lịch đã xác nhận từ Vận chuyển. Danh sách dưới đây chỉ có hạn của đơn, chưa kết luận lịch lấy hàng đáp ứng.':'10 đơn có hạn sớm nhất trong dữ liệu đã tải. Mở một đơn để đối chiếu món hàng và lượng cần.'}</p>
            <div className="iw-records-links">{selected.open_orders.slice().sort((a,b)=>Date.parse(a.deadline)-Date.parse(b.deadline)).slice(0,10).map(order=><Button variant="outline" key={order.order_id} disabled={focusNode==='logistics'&&(!canAnalyze||!onReviewOrder||analysisDisabled)} onClick={()=>{
              setFocusNode(null);
              if(focusNode==='logistics'&&onReviewOrder)onReviewOrder(selected.warehouse_id,order.order_number,result.horizon);
              else{setAllOrdersOpen(true);setOrderSearch('');setOrderPage(0);setOrderFilter('all');setExpandedOrder(order.order_id);}
            }}>{order.order_number} · {orderDateTime(order.deadline)}</Button>)}</div>
            {!selected.open_orders.length&&<p>Kho này không có đơn đang mở trong dữ liệu đã tải.</p>}
            <Button variant="outline" onClick={()=>{setOrderSearch('');setOrderPage(0);setExpandedOrder(null);setOrderFilter('all');setAllOrdersOpen(true);}}>Xem đầy đủ đơn của kho</Button>
          </>}
        </WorkspaceDialog>
        <WorkspaceDialog open={allOrdersOpen} onClose={()=>{setAllOrdersOpen(false);setExpandedOrder(null);}} title={`Toàn bộ đơn đang mở · ${selected.warehouse_name}`} description={`${selected.open_orders.length} đơn chưa hoàn tất trong dữ liệu đã tải của kho này, gồm cả đơn ngoài kỳ dự báo. Tìm kiếm, chuyển trang hoặc chọn đơn để xem chi tiết.`} wide>{allOrdersOpen&&<div className="wo-orders-dialog">{orderContent}</div>}</WorkspaceDialog>
        <div className="iw-overview-actions" aria-label="Mở chi tiết kho">
          <Button variant="outline" onClick={()=>chooseTab('inventory')}>Xem tồn kho ({selected.products.length})</Button>
          <Button variant="outline" onClick={()=>chooseTab('orders')}>Xem đơn hàng ({selected.open_orders.length})</Button>
          {request&&<Button variant="outline" aria-expanded={tab==='analysis'} onClick={()=>chooseTab('analysis')}>Xem biểu đồ phân tích</Button>}
        </div>
        {request&&<WorkspaceDialog open={tab==='analysis'} onClose={()=>setTab('summary')} title={`Biểu đồ phân tích · ${selected.warehouse_name}`} description="Chọn góc nhìn theo mặt hàng hoặc đơn hàng. Mọi biểu đồ dùng dữ liệu của kho đang chọn." wide><WarehouseAnalysis key={selected.warehouse_id} warehouse={selected} horizon={result.horizon} readAt={result.read_at} active={tab==='analysis'} request={request} onDenied={onDenied||(()=>{})} onEvidence={onEvidence} onReviewOrder={canAnalyze&&!analysisDisabled&&onReviewOrder?number=>onReviewOrder(selected.warehouse_id,number,result.horizon):undefined} onAnalyze={canAnalyze&&!analysisDisabled?sku=>{setTab('summary');onAnalyze(selected.warehouse_id,sku,result.horizon);}:undefined}/></WorkspaceDialog>}

      </section>}
    </>}
  </section>;
}
