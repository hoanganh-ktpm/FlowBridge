// SPDX-License-Identifier: GPL-3.0-or-later
import { WorkspaceModal as Sheet } from './interactive-workspace';
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DialogContent as SheetContent, DialogHeader as SheetHeader, DialogTitle as SheetTitle, DialogDescription as SheetDescription } from '@/components/ui/dialog';
import { ApiError } from '@/lib/api';
import { cell } from '@/lib/format';
import { Select, dateLabel, fmt, unitLabel } from './components';
import { OrderReadinessReasons, orderDateTime } from './order-readiness';
import { assessmentLabels, conditionCounts, conditionLabels, conditionNames, deadlineBuckets } from './order-chart-data';
import type { DecisionRequest } from './decision-dossiers';
import type { OrderAnalysisData, OrderAssessment, Warehouse, WarehouseOverview } from './warehouse-overview-types';
import './order-analysis.css';
import { useWorkspaceState } from './workspace-sync';
import { WorkspaceFocus } from './interactive-workspace';

const classes = ['blocked', 'unknown', 'ready'] as const;
const pageSize = 10;
const quantityColumn = { field: 'ordered_quantity', label: 'Số lượng đặt', type: 'decimal', scale: 3 };
function remaining(order: OrderAssessment, asOf: string) {
  const hours = (Date.parse(order.deadline) - Date.parse(asOf)) / 3600000;
  if (hours < 0) return `Quá hạn ${fmt(-hours / 24)} ngày`;
  return hours < 24 ? `Còn ${fmt(hours)} giờ` : `Còn ${fmt(hours / 24)} ngày`;
}
export function OrderAnalysis({ warehouse, horizon, readAt, active, request, onDenied, onReviewOrder }: {
  warehouse: Warehouse; horizon: number; readAt: string; active: boolean; request: DecisionRequest;
  onDenied: (error: unknown) => void; onReviewOrder?: (orderNumber: string) => void;
}) {
  const context = `${warehouse.organization_id}:${warehouse.warehouse_id}:${horizon}:${readAt}`;
  const [loaded, setLoaded] = useWorkspaceState<{ context: string; data: OrderAnalysisData } | null>('order-chart-data:'+warehouse.warehouse_id,null,true);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [chart, setChart] = useState('deadlines');
  const [filter, setFilter] = useState('all'), [bucketKey, setBucketKey] = useState('all'), [page, setPage] = useState(0);
  const [detail, setDetail] = useState<string | null>(null);
  const [bucketOpen,setBucketOpen]=useState(false);
  const started = useRef(''), generation = useRef(0), controller = useRef<AbortController | null>(null);
  const data = loaded?.context === context ? loaded.data : null;
  async function load(fresh = false) {
    controller.current?.abort(); const pending = new AbortController(); controller.current = pending;
    const ticket = ++generation.current; setBusy(true); setError(''); setLoaded(null); setDetail(null);
    try {
      const result = await request<WarehouseOverview>('/all-warehouses', { warehouse_id: warehouse.warehouse_id, horizon, include_planning: true, include_order_analysis: true }, pending.signal, undefined, fresh);
      if (pending.signal.aborted || ticket !== generation.current) return;
      const item = result.items.find(w => w.warehouse_id === warehouse.warehouse_id && w.organization_id === warehouse.organization_id);
      const analysis = item?.order_analysis;
      if (result.items.length !== 1 || result.horizon !== horizon || !analysis || analysis.horizon !== horizon || !Number.isFinite(Date.parse(analysis.as_of)) || !Array.isArray(analysis.orders)
        || new Set(analysis.orders.map(o => o.order_id)).size !== analysis.orders.length
        || analysis.orders.some(o => !Number.isFinite(Date.parse(o.deadline)) || !classes.includes(o.classification))) throw new Error('Invalid order scope');
      setLoaded({ context, data: analysis }); setBucketKey('all'); setFilter('all'); setPage(0);
    } catch (e) {
      if (pending.signal.aborted || ticket !== generation.current) return;
      setLoaded(null);
      if (e instanceof ApiError && [401, 403].includes(e.status)) { onDenied(e); return; }
      setError('Chưa tải được phân tích đơn hàng. Bấm Cập nhật biểu đồ đơn hàng để thử lại.');
    } finally { if (ticket === generation.current) setBusy(false); }
  }
  useEffect(() => () => { generation.current++; controller.current?.abort(); }, [context]);
  useEffect(() => {
    if (!active || started.current === context) return;
    started.current = context;if(data)return; void load(!!loaded && loaded.context !== context);
  }, [active, context]);
  const buckets = data ? deadlineBuckets(data) : [];
  const bucket = buckets.find(b => b.key === bucketKey);
  const rows = (bucket?.orders || data?.orders || []).filter(o => filter === 'all' || o.classification === filter)
    .slice().sort((a, b) => Date.parse(a.deadline) - Date.parse(b.deadline) || a.order_number.localeCompare(b.order_number));
  const shown = rows.slice(page * pageSize, (page + 1) * pageSize);
  const selected = data?.orders.find(o => o.order_id === detail);
  const max = Math.max(1, ...buckets.map(b => b.orders.length));
  const label = (b: typeof buckets[number]) => b.key === 'overdue' ? 'Đã quá hạn' : b.start === b.end ? dateLabel(b.start) : `${dateLabel(b.start)} – ${dateLabel(b.end)}`;
  function choose(key: string, classification = 'all') { setBucketKey(key); setFilter(classification); setPage(0); setChart('conditions'); setBucketOpen(true); }
  return <section className="oa-analysis" aria-label={`Phân tích đơn hàng ${warehouse.warehouse_code}`}>
    <div className="oa-heading"><p>Đơn nào cần làm trước, và đang vướng ở bước nào?</p><Button variant="outline" disabled={busy} onClick={() => void load(true)}>{busy ? 'Đang kiểm tra đơn hàng…' : 'Cập nhật biểu đồ đơn hàng'}</Button></div>
    {busy && <p role="status">Đang đối chiếu đơn hàng với Kho và lịch nhận của đơn vị vận chuyển…</p>}
    {error && <p role="alert">{error}</p>}
    {data && <>
      <p className="oa-context">{warehouse.warehouse_name} · {data.orders.length} đơn chưa hoàn tất đến hết {dateLabel(data.end)} · Đọc lúc {orderDateTime(data.as_of)}</p>
      <div className="oa-summary" aria-label="Điều kiện các đơn hàng">{classes.map(key => <button type="button" key={key} className={`oa-state-${key}`} onClick={() => choose('all', key)}><strong>{data.orders.filter(o => o.classification === key).length}</strong><span>{assessmentLabels[key]}</span></button>)}</div>
      {!data.assessment_available && <p className="wo-notice">Chưa đủ dữ liệu để kiểm tra điều kiện. Vẫn hiển thị hạn của các đơn đã đọc; phần chưa biết được giữ là Chưa xác minh.</p>}
      <p className="oa-note">Chỉ xét đơn đang mở đến hết kỳ, gồm cả đơn đã quá hạn. {data.outside_horizon_count} đơn có hạn sau kỳ chưa nằm trong biểu đồ. Đây là đánh giá kế hoạch, chưa phải kết quả bàn giao thực tế.</p>
      <details className="oa-note"><summary>Nguồn và cách đọc biểu đồ đơn hàng</summary><p>Bán hàng cung cấp đơn và hạn bàn giao. Kho cung cấp hàng còn dùng được, hàng đã giữ, hàng về và thời gian chuẩn bị. Vận chuyển cung cấp lịch lấy hàng đã nhận và số lượt nhận theo lịch.</p><p>“Có vướng mắc” khi ít nhất một điều kiện chưa đáp ứng. Nếu chưa thấy vướng nhưng còn điều kiện chưa xác minh, xếp vào “Chưa xác minh đủ”. Chỉ khi bốn điều kiện cùng đáp ứng mới xếp “Đáp ứng theo kế hoạch”. Thời gian còn lại tính tại lúc đọc dữ liệu; các nguồn không được đọc trong cùng một giao dịch xuyên database.</p><p>Dự báo lượng đặt mới có thể làm tăng nguy cơ thiếu hàng cho đơn đang mở. Số lượng thiếu là ước tính theo từng mã hàng và đơn vị, không phải hàng đã thiếu thực tế hoặc xác suất giao trễ.</p></details>
      <Tabs value={chart} onValueChange={setChart} className="pw-section-tabs"><TabsList variant="line" aria-label="Chọn biểu đồ đơn hàng"><TabsTrigger value="deadlines">Hạn bàn giao</TabsTrigger><TabsTrigger value="conditions">Điều kiện từng đơn</TabsTrigger></TabsList></Tabs>
      {chart === 'deadlines' && <section aria-label="Áp lực theo hạn bàn giao" className="oa-deadlines">
        <h3>Ngày nào có nhiều đơn cần phối hợp?</h3><p className="oa-note">Độ dài thanh là số đơn, mỗi đơn chỉ tính một lần. Chọn một ngày để xem các đơn thuộc ngày đó.</p>
        <div className="oa-legend">{classes.map(key => <span key={key}><i className={`oa-fill-${key}`}/>{assessmentLabels[key]}</span>)}</div>
        <div className="oa-axis"><span>Số đơn</span><span>0 → {max}</span></div>
        {buckets.map(b => <button type="button" key={b.key} className="oa-deadline-row" aria-label={`Xem các đơn ${label(b)}: ${b.orders.length} đơn, ${b.blocked} có vướng mắc, ${b.unknown} chưa xác minh đủ, ${b.ready} đáp ứng theo kế hoạch`} onClick={() => choose(b.key)} disabled={!b.orders.length}>
          <span>{label(b)}</span><span className="oa-bar-track">{classes.filter(key => b[key]).map(key => <span key={key} className={`oa-fill-${key}`} style={{ width: `${b[key] / max * 100}%` }}>{b[key]}</span>)}</span><strong>{b.orders.length}</strong>
        </button>)}
        <p className="oa-note">Nhóm “Đã quá hạn” chỉ nói rằng hạn của đơn đang mở đã qua tại thời điểm đọc, không suy ra lỗi thuộc bộ phận nào.</p>
      </section>}
      {chart === 'conditions' && <WorkspaceFocus focused={bucketOpen} onClose={()=>setBucketOpen(false)} title="Các đơn cần đối chiếu" description={`${warehouse.warehouse_name} · Chọn mã đơn hoặc một ô màu để xem chi tiết.`}><section aria-label="Ma trận điều kiện đơn hàng">
        <h3>Đơn nào đang vướng ở bước nào?</h3>
        <div className="oa-filters"><label>Hạn bàn giao<Select label="Lọc hạn bàn giao của đơn" value={bucketKey} onChange={v => { setBucketKey(v); setPage(0); }}><option value="all">Tất cả trong kỳ</option>{buckets.filter(b => b.orders.length).map(b => <option key={b.key} value={b.key}>{label(b)}</option>)}</Select></label><label>Điều kiện<Select label="Lọc điều kiện của đơn" value={filter} onChange={v => { setFilter(v); setPage(0); }}><option value="all">Tất cả điều kiện</option>{classes.map(key => <option key={key} value={key}>{assessmentLabels[key]}</option>)}</Select></label></div>
        <div className="oa-bottlenecks">{conditionCounts(rows).map(c => <span key={c.key}>{conditionLabels[c.key as keyof typeof conditionLabels]}: <b>{c.blocked} vướng</b> · {c.unknown} chưa xác minh</span>)}</div>
        <p className="oa-note">Đếm trên {rows.length} đơn khớp bộ lọc. Một đơn có thể vướng nhiều bước; không cộng các cột thành tổng số đơn. Danh sách ưu tiên hạn sớm nhất.</p>
        <div className="oa-matrix-scroll" tabIndex={0}><table className="oa-matrix" aria-label="Điều kiện bàn giao theo từng đơn"><thead><tr><th>Đơn / thời gian còn lại</th>{Object.values(conditionLabels).map(value => <th key={value}>{value}</th>)}</tr></thead><tbody>{shown.map(order => <tr key={order.order_id}><td><button type="button" onClick={() => setDetail(order.order_id)} aria-label={`Xem phân tích ${order.order_number}`}><strong>{order.order_number}</strong><span>{orderDateTime(order.deadline)}</span><small>{remaining(order, data.as_of)}</small></button></td>{Object.keys(conditionLabels).map(key => {
          const state = order.readiness?.conditions[key] || 'unknown';
          return <td key={key}><button type="button" className={`oa-condition oa-condition-${state}`} onClick={() => setDetail(order.order_id)} aria-label={`${order.order_number} · ${conditionLabels[key as keyof typeof conditionLabels]} · ${conditionNames[state] || 'Chưa xác minh'}`}><span aria-hidden="true">{state === 'met' ? '✓' : state === 'not_met' ? '!' : '?'}</span>{conditionNames[state] || 'Chưa xác minh'}</button></td>;
        })}</tr>)}</tbody></table></div>
        {!rows.length && <p className="oa-empty">Không có đơn khớp bộ lọc.</p>}
        {rows.length > pageSize && <div className="oa-pagination"><span>{page * pageSize + 1}–{Math.min(rows.length, (page + 1) * pageSize)} / {rows.length} đơn</span><Button size="sm" variant="outline" disabled={!page} onClick={() => setPage(p => p - 1)}>Đơn trước</Button><Button size="sm" variant="outline" disabled={(page + 1) * pageSize >= rows.length} onClick={() => setPage(p => p + 1)}>Đơn tiếp</Button></div>}
      </section></WorkspaceFocus>}
      <Sheet open={!!selected} onOpenChange={open => { if (!open) setDetail(null); }}><SheetContent className="oa-detail-sheet"><SheetHeader><SheetTitle>Phân tích {selected?.order_number}</SheetTitle><SheetDescription>{warehouse.warehouse_name} · Đối chiếu ba bộ phận ở thời điểm đọc dữ liệu.</SheetDescription></SheetHeader>{selected && <>
        <p><strong>Hạn bàn giao:</strong> {orderDateTime(selected.deadline)}</p>
        {selected.readiness?.stock_ready_by && <p><strong>Kho cần đủ hàng trước:</strong> {orderDateTime(selected.readiness.stock_ready_by)}</p>}
        <OrderReadinessReasons order={selected}/>
        {selected.supply_notes.map(note => <p className="oa-note" key={note}>{note}</p>)}
        <h3>Mặt hàng và số lượng đặt</h3><ul>{selected.lines.map((line, i) => <li key={i}>{selected.products.find(p => p.sku === line.sku && p.uom === line.uom)?.name || line.sku} ({line.sku}): <strong>{cell(line.ordered_quantity,quantityColumn)} {unitLabel(line.uom)}</strong></li>)}</ul>
        <p className="oa-note">Đáp ứng theo kế hoạch chưa xác nhận đã giữ hàng, đóng gói hay bàn giao. Không cộng số lượng khác đơn vị thành một chỉ số.</p>
        {onReviewOrder && <Button variant="outline" onClick={() => onReviewOrder(selected.order_number)}>Mở kế hoạch của {selected.order_number}</Button>}
      </>}</SheetContent></Sheet>
    </>}
  </section>;
}
