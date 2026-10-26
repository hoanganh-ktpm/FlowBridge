// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ApiError } from '@/lib/api';
import type { DecisionRequest } from './decision-dossiers';
import type { Explanation } from './business-evidence';
import { DemandReceiptsChart, ProductRiskChart } from './decision-charts';
import { Field, Panel, Plot, Select, dateLabel, unitLabel } from './components';
import { orderDateTime } from './order-readiness';
import type { Warehouse, WarehouseAnalysisData, WarehouseOverview } from './warehouse-overview-types';
import { InspectChart } from './interactive-workspace';
import { useWorkspaceState } from './workspace-sync';
import { OrderAnalysis } from './order-analysis';

type Settings = { sku: string; days: string; frequency: string };

type AnalysisProps = {
  warehouse: Warehouse; horizon: number; readAt: string; active: boolean; request: DecisionRequest;
  onDenied: (error: unknown) => void; onEvidence?: (value: Explanation) => void;
  onAnalyze?: (sku: string) => void; onReviewOrder?: (orderNumber: string) => void;
};
export function WarehouseAnalysis(props: AnalysisProps) {
  const [mode, setMode] = useWorkspaceState('warehouse-chart-mode:'+props.warehouse.warehouse_id,'products');
  return <div className="wo-analysis-modes">
    <Tabs value={mode} onValueChange={setMode}><TabsList variant="line" aria-label="Góc nhìn phân tích"><TabsTrigger value="products">Theo mặt hàng</TabsTrigger><TabsTrigger value="orders">Theo đơn hàng</TabsTrigger></TabsList></Tabs>
    <div hidden={mode !== 'products'}><ProductAnalysis {...props} active={props.active && mode === 'products'}/></div>
    <div hidden={mode !== 'orders'}><OrderAnalysis {...props} active={props.active && mode === 'orders'}/></div>
  </div>;
}
function ProductAnalysis({ warehouse, horizon, readAt, active, request, onDenied, onEvidence, onAnalyze }: AnalysisProps) {
  const context = `${warehouse.organization_id}:${warehouse.warehouse_id}:${horizon}:${readAt}`;
  const defaultSku = warehouse.products.find(p => (p.shortage || 0) > 0)?.sku || warehouse.products[0]?.sku || '';
  const [settings, setSettings] = useWorkspaceState<Settings>('product-chart-settings:'+warehouse.warehouse_id,{ sku: defaultSku, days: '30', frequency: 'day' });
  const [loaded, setLoaded] = useWorkspaceState<{ context: string; data: WarehouseAnalysisData; settings: Settings } | null>('product-chart-data:'+warehouse.warehouse_id,null,true);
  const [chart, setChart] = useState('history'), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const generation = useRef(0), started = useRef('');
  const controller = useRef<AbortController | null>(null);
  const current = loaded?.context === context ? loaded : null;
  const dirty = !!current && JSON.stringify(settings) !== JSON.stringify(current.settings);
  const selected = current?.data.products.find(p => p.sku === current.data.selected_sku);

  async function load(next = settings, fresh = false) {
    controller.current?.abort();
    const pending = new AbortController(); controller.current = pending;
    const ticket = ++generation.current;
    setSettings(next); setLoaded(null); setError('');
    if (!next.sku) { setBusy(false); setError('Kho chưa có mặt hàng được chia sẻ để phân tích.'); return; }
    setBusy(true);
    try {
      const result = await request<WarehouseOverview>('/all-warehouses', {
        warehouse_id: warehouse.warehouse_id, horizon, include_planning: true, include_analysis: true,
        analysis_sku: next.sku, history_days: Number(next.days), history_frequency: next.frequency,
      }, pending.signal, undefined, fresh);
      if (pending.signal.aborted || ticket !== generation.current) return;
      const item = result.items.find(item => item.warehouse_id === warehouse.warehouse_id && item.organization_id === warehouse.organization_id);
      if (result.items.length !== 1 || !item || result.horizon !== horizon) throw new Error('Unexpected scope');
      if (!item.analysis) { setError(item.planning_issue?.message || 'Chưa đủ dữ liệu để dựng biểu đồ của kho này.'); return; }
      const data = item.analysis;
      if (data.warehouse_id !== warehouse.warehouse_id || data.horizon !== horizon || data.selected_sku !== next.sku || data.history_days !== Number(next.days) || data.history.frequency !== next.frequency) throw new Error('Unexpected analysis');
      setLoaded({ context, data, settings: next });
    } catch (e) {
      if (pending.signal.aborted || ticket !== generation.current) return;
      setLoaded(null);
      if (e instanceof ApiError && [401, 403].includes(e.status)) { onDenied(e); return; }
      setError('Chưa tải được biểu đồ. Tồn kho và đơn hàng vẫn xem được; bấm Cập nhật biểu đồ để thử lại.');
    } finally { if (ticket === generation.current) setBusy(false); }
  }

  useEffect(() => () => { generation.current++; controller.current?.abort(); }, [context]);
  useEffect(() => {
    if (!active || started.current === context) return;
    started.current = context;
    if(current)return;
    void load(settings, !!loaded && loaded.context !== context);
  }, [active, context]);

  const evidence = (kind: string) => current?.data.explanations[kind] && onEvidence
    ? <Button variant="ghost" size="sm" onClick={() => onEvidence(current.data.explanations[kind])}>Nguồn và cách tính</Button> : undefined;
  return <section className="wo-analysis" aria-label={`Biểu đồ phân tích ${warehouse.warehouse_code}`}>
    <div className="wo-analysis-filters">
      <Field title="Mặt hàng phân tích"><Select label="Mặt hàng phân tích" value={settings.sku} disabled={busy} onChange={sku => setSettings(s => ({ ...s, sku }))}>{warehouse.products.map(p => <option key={`${p.sku}:${p.uom}`} value={p.sku}>{p.name || p.sku} · {p.sku}</option>)}</Select></Field>
      <Field title="Lịch sử hiển thị"><Select label="Lịch sử hiển thị" value={settings.days} disabled={busy} onChange={days => setSettings(s => ({ ...s, days }))}>{[7, 14, 30, 90, 365].map(days => <option key={days} value={days}>{days === 365 ? '12 tháng' : `${days} ngày`}</option>)}</Select></Field>
      <Field title="Gộp biểu đồ theo"><Select label="Gộp biểu đồ theo" value={settings.frequency} disabled={busy} onChange={frequency => setSettings(s => ({ ...s, frequency }))}><option value="day">Ngày</option><option value="week">Tuần</option></Select></Field>
      <Button disabled={busy || !settings.sku} onClick={() => void load(settings, true)}>{busy ? 'Đang tải biểu đồ…' : 'Cập nhật biểu đồ'}</Button>
    </div>
    {busy && <p role="status">Đang đối chiếu lịch sử đặt hàng và phiếu nhập của kho đang chọn…</p>}
    {error && <p role="alert">{error}</p>}
    {dirty && <p role="status">Lựa chọn đã thay đổi. Bấm Cập nhật biểu đồ; kết quả bên dưới vẫn thuộc lần tính trước.</p>}
    {current && selected && <>
      <p className="wo-analysis-context">{warehouse.warehouse_name} · {selected.name || selected.sku} · {unitLabel(selected.uom)} · Đọc lúc {orderDateTime(current.data.as_of)}</p>
      <Tabs value={chart} onValueChange={setChart} className="pw-section-tabs"><TabsList variant="line" aria-label="Chọn biểu đồ kho"><TabsTrigger value="history">Lượng đặt và thực nhập</TabsTrigger><TabsTrigger value="risk">Nguy cơ thiếu hàng</TabsTrigger><TabsTrigger value="balance">Diễn biến đủ và thiếu</TabsTrigger></TabsList></Tabs>
      {chart === 'history' && <Panel title="Lượng đặt và thực nhập" subtitle={`${dateLabel(current.data.history.start)} – ${dateLabel(current.data.history.end)}`} action={evidence('history')}><InspectChart title="Lượng đặt và thực nhập" description="Cùng mặt hàng, cùng kho, cùng đơn vị và khoảng lịch sử đã áp dụng."><DemandReceiptsChart history={current.data.history} unit={selected.uom}/></InspectChart><p className="pw-panel-note">Lượng khách đặt và lượng đã nhập của cùng mặt hàng, cùng kho. Hai luồng này chưa phải biến động tồn kho.</p></Panel>}
      {chart === 'risk' && <Panel title="Nguy cơ thiếu hàng theo sản phẩm" subtitle={`Trong ${horizon} ngày tới · chỉ so sánh mặt hàng cùng đơn vị ${unitLabel(selected.uom)}`} action={evidence('risk')}><InspectChart title="Nguy cơ thiếu hàng theo sản phẩm" description="Chọn một cột để mở phương án của mặt hàng đó."><ProductRiskChart products={current.data.products} unit={selected.uom} onSelect={dirty || !onAnalyze ? undefined : p => onAnalyze(p.sku)}/></InspectChart><p className="pw-panel-note">Tách lượng thiếu cho đơn đã nhận và phần thiếu tăng thêm khi xét nhu cầu dự báo. Chọn cột để thử bổ sung mặt hàng đó.</p></Panel>}
      {chart === 'balance' && <Panel title="Diễn biến đủ và thiếu hàng" subtitle={`Cùng mặt hàng, cùng kho · ${horizon} ngày tới`} action={evidence('balance')}><InspectChart title="Diễn biến đủ và thiếu hàng" description="Đối chiếu tồn và mức thiếu tại các thời điểm trong kỳ."><Plot data={current.data.baseline.series} unit={unitLabel(selected.uom)} lines={[{ key: 'physical_inventory', name: 'Tồn cuối ngày', color: 'var(--fb-brand-blue)' }, { key: 'daily_peak_supply_shortage', name: 'Lượng hàng thiếu lớn nhất trong ngày', color: 'var(--fb-danger)' }]}/></InspectChart><p className="pw-panel-note">Cuối ngày có hàng vẫn có thể thiếu trước giờ cần chuẩn bị. Lượng thiếu không được cộng các ngày với nhau.</p></Panel>}
    </>}
  </section>;
}
