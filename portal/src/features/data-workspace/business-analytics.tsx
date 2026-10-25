// SPDX-License-Identifier: GPL-3.0-or-later
import { useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Bars, Field, Panel, Plot, Select, dateLabel, fmt, unitLabel, withCalendarGap } from './components';
import { ProductSelector } from './product-selector';
import { sourceNames, type Explanation } from './business-evidence';
import type { WorkspaceRequest } from './configuration';
import type { Forecast, Overview, Product } from './types';

const blue = 'var(--fb-brand-blue)', purple = 'var(--fb-brand-purple)';
const palette = [blue, purple, 'var(--fb-brand-cyan)'];
const ref = (p: Product) => ({ data_product_id: p.id, contract_version_id: p.contract_version_id });
const weekdays = ['Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7', 'Chủ nhật'];
const addDays = (day: string, count: number) => new Date(Date.parse(day + 'T00:00:00Z') + count * 86400000).toISOString().slice(0, 10);
const calendarDays = (start: string, end: string) => Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1;

function ForecastHelp({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <Tooltip open={open} onOpenChange={setOpen}><TooltipTrigger asChild>
    <button type="button" className="pw-forecast-help" aria-label={label} onClick={() => setOpen(value => !value)}>?</button>
  </TooltipTrigger><TooltipContent sideOffset={8} className="max-w-72 text-xs leading-6">{children}</TooltipContent></Tooltip>;
}

export function BusinessAnalytics({ products, busy, request, perform, onEvidence, onPlan, canPlan, mode, names }: {
  products: Product[]; busy: boolean; request: WorkspaceRequest; mode: 'history' | 'forecast';
  names: Record<string, string>;
  perform: (label: string, work: () => Promise<void>) => Promise<void>;
  onEvidence: (value: Explanation) => void; onPlan: (sku: string, horizon: number, training?: number) => void; canPlan: boolean;
}) {
  const salesName = names.sales_history || 'sales.confirmed_demand_daily';
  const topics = products.filter(p => p.name === salesName || p.name === (names.transport_history || 'logistics.accepted_shipments_daily'));
  const [topic, setTopic] = useState(topics.find(p => p.name === salesName)?.id || topics[0]?.id || '');
  const [days, setDays] = useState('30'), [entity, setEntity] = useState('');
  const [compared, setCompared] = useState<string[]>([]), [average, setAverage] = useState(false);
  const [result, setResult] = useState<Overview | null>(null);
  const [applied, setApplied] = useState({ topic: '', days: '', entity: '', compared: [] as string[] });
  const [startDate, setStartDate] = useState(''), [endDate, setEndDate] = useState(''), [training, setTraining] = useState('180');
  const [forecast, setForecast] = useState<Forecast | null>(null), [forecastSettings, setForecastSettings] = useState('');
  const product = topics.find(p => p.id === topic);
  const sales = result?.config.entity_column === 'sku';
  const labels = result?.entity_labels || {};
  const label = (key: string) => labels[key] ? `${key} - ${labels[key]}` : key;
  const unit = unitLabel(result?.config.unit);
  const metric = result?.analytics.metrics.find(m => m.item_id === result.entity);
  const dirty = !!result && (topic !== applied.topic || days !== applied.days || entity !== applied.entity || JSON.stringify(compared) !== JSON.stringify(applied.compared));
  const today = result ? addDays(result.source_end, result.source_age_days) : new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Ho_Chi_Minh' }).format(new Date());
  const lastDate = addDays(today, 89);
  const validWindow = !!startDate && !!endDate && startDate >= today && endDate >= startDate && endDate <= lastDate;
  const forecastDirty = !!forecast && forecastSettings !== JSON.stringify({ startDate, endDate, training });
  const rows = new Map<string, Record<string, string | number>>();
  for (const point of result?.comparison?.series || result?.analytics.series || []) {
    const i = (result?.comparison_entities || [result?.entity]).indexOf(point.item_id);
    if (i < 0) continue;
    const row = rows.get(point.timestamp) || { day: point.timestamp };
    row[`item${i}`] = average ? point.moving_average : point.target; rows.set(point.timestamp, row);
  }
  const history = [...rows.values()].sort((a, b) => String(a.day).localeCompare(String(b.day)));
  const rhythm = Object.entries(metric?.day_of_week_average || {}).map(([key, value]) => ({ day: weekdays[Number(key)], quantity: value, order: Number(key) })).sort((a, b) => a.order - b.order);
  const actual = result?.analytics.series.filter(p => p.item_id === result.entity).map(p => ({ day: p.timestamp, actual: p.target })) || [];
  const future = forecast?.points.filter(p => p.item_id === result?.entity).map(p => ({ day: p.timestamp, predicted: p.p50, band: [p.p10, p.p90] })) || [];
  const forecastTotal = future.reduce((sum, p) => sum + p.predicted, 0);
  const model = result ? forecast?.model.entities?.[result.entity] : undefined;
  const score = model?.validation.ridge_lag;

  async function apply() {
    if (!product) return;
    setResult(null); setForecast(null);
    const catalog = products.find(p => p.name === (names.catalog || 'warehouse.planning_catalog'));
    const report = await request<Overview>('/overview', { ...ref(product), days: Number(days), entity: entity || null, compare_entities: mode === 'history' ? compared : [], ...(product.name === salesName && catalog ? { catalog: ref(catalog) } : {}) });
    setResult(report); setEntity(report.entity);
    const reportToday = addDays(report.source_end, report.source_age_days);
    if (!startDate || startDate < reportToday) { setStartDate(reportToday); setEndDate(addDays(reportToday, 6)); }
    const choices = report.comparison_entities || [report.entity]; setCompared(choices);
    setApplied({ topic, days, entity: report.entity, compared: choices });
  }
  async function predict() {
    if (!result || !validWindow) return;
    setForecast(null);
    const next = await request<Forecast>('/forecasts', { config: { ...result.config, entities: [result.entity], forecast_model: 'ridge_lag' }, start_date: startDate, end_date: endDate, training_days: Number(training), allow_statistical_fallback: false });
    setForecast(next); setForecastSettings(JSON.stringify({ startDate, endDate, training }));
  }
  function explain(kind: 'history' | 'rhythm' | 'forecast') {
    if (!result) return;
    const evidence = kind === 'forecast' && forecast ? forecast.evidence : result.analytics.evidence;
    onEvidence({ title: kind === 'forecast' ? 'Nguồn dữ liệu và phương pháp dự báo' : kind === 'rhythm' ? 'Nhịp trong tuần' : 'Số liệu đã ghi nhận',
      meaning: kind === 'forecast' ? 'Ước tính nhu cầu mới để tham khảo khi lập kế hoạch. Cần đối chiếu với các cam kết đã có.' : sales ? 'Lượng khách đặt đã được xác nhận. Số liệu này chưa phản ánh hàng đã giao hoặc doanh thu ròng.' : 'Số lượt vận chuyển đã tiếp nhận. Số liệu này chưa phản ánh số lượt giao thành công.',
      applications: sourceNames(evidence),
      formula: kind === 'forecast' ? 'ML học từ lượng đặt các ngày trước, nhịp tuần và xu hướng. Kiểm chứng trên hai đoạn lịch sử theo đúng thứ tự thời gian.' : kind === 'rhythm' ? 'Tổng lượng phát sinh cùng thứ ÷ số ngày thuộc thứ đó.' : 'Tổng hợp các sự kiện đã ghi nhận theo ngày, đúng quyền nguồn.',
      notes: [ ...(kind === 'forecast' ? ['Mô hình Ridge chỉ tạo dự báo khi có đủ lịch sử để học và kiểm chứng.', 'Vùng nhạt thể hiện mức dao động tham khảo từ sai lệch trong quá khứ. Kết quả thực tế có thể nằm ngoài vùng này.', 'Dữ liệu học phải đủ 56 ngày và hai kỳ kiểm chứng, cộng khoảng trống đến ngày dự báo.'] : []), `Dữ liệu được ghi nhận đến ${dateLabel(result.source_end)}. Thời điểm này cách ngày tạo kết quả ${result.source_age_days} ngày.`, 'Chỉ điền 0 cho ngày không có sự kiện trong nguồn tổng hợp đầy đủ đã đọc thành công.'],
      start: kind === 'forecast' ? future[0]?.day : result.config.start, end: kind === 'forecast' ? future.at(-1)?.day : result.config.end,
      last_observation: result.source_end, read_at: forecast?.created_at, technical: evidence });
  }
  const evidence = (kind: 'history' | 'rhythm' | 'forecast') => <Button size="sm" variant="ghost" className="pw-text-button" onClick={() => explain(kind)}>Nguồn và cách tính</Button>;
  if (!topics.length) return <div className="pw-source-setup"><p>Chưa có dữ liệu lịch sử được chia sẻ. Liên hệ IT để kết nối nguồn đặt hàng hoặc vận chuyển.</p></div>;
  const filters = <>
    <section className="dw-filterbar pw-analytics-filters">
      <Field title="Nội dung"><Select value={topic} disabled={busy} onChange={v => { setTopic(v); setEntity(''); setCompared([]); }}>{topics.map(p => <option key={p.id} value={p.id}>{p.name === salesName ? 'Lượng khách đặt hàng' : 'Lượt vận chuyển tiếp nhận'}</option>)}</Select></Field>
      <Field title={mode === 'forecast' ? 'Khoảng xem lịch sử' : 'Xem số liệu đã ghi nhận trong'} hint="Khoảng số liệu quá khứ hiển thị trên biểu đồ. Dữ liệu học ML được chọn riêng ở bước tạo dự báo."><Select value={days} disabled={busy} onChange={setDays}>{['7', '14', '30', '90', '365'].map(n => <option key={n} value={n}>{n === '365' ? '12 tháng' : n + ' ngày'}</option>)}</Select></Field>
      <Field title={product?.name === salesName ? 'Sản phẩm' : 'Nhà vận chuyển'}>{product?.name === salesName ? <ProductSelector items={(result?.entities || []).map(sku => ({ sku, name: labels[sku] }))} value={entity} onChange={v => { setEntity(v); setCompared([v, ...compared.filter(e => e !== v)].slice(0, 3)); }} disabled={busy || !result || topic !== applied.topic}/> : <Select value={entity} disabled={busy || !result || topic !== applied.topic} onChange={v => { setEntity(v); setCompared([v]); }}><option value="">Chọn sau khi cập nhật</option>{result?.entities.map(e => <option key={e}>{e}</option>)}</Select>}</Field>
      <Button disabled={busy} onClick={() => void perform('Đang cập nhật dữ liệu', apply)}>{mode === 'history' ? 'Cập nhật biểu đồ' : 'Cập nhật dữ liệu'}</Button>
      {dirty && <span className="pw-draft-note">Lựa chọn đã thay đổi. Cập nhật dữ liệu để tiếp tục.</span>}
    </section>
    {mode === 'forecast' && result && !dirty && <p className="pw-forecast-ready" role="status">Đã cập nhật dữ liệu đến {dateLabel(result.source_end)}.</p>}
  </>;
  return <div className={`pw-analysis-view${mode === 'forecast' ? ' pw-forecast-view' : ''}`}>
    {mode === 'forecast' ? <Panel title="Bước 1. Cập nhật dữ liệu" subtitle="Chọn nội dung và khoảng thời gian cần xem.">{filters}</Panel> : filters}
    {!result && mode === 'history' && <Card className="pw-empty-state"><CardContent><h2>Chưa có biểu đồ</h2><p>Chọn nội dung và khoảng xem, sau đó cập nhật dữ liệu để bắt đầu.</p></CardContent></Card>}
    {result && mode === 'history' && <>
      <Panel title={sales ? 'Diễn biến lượng đặt hàng' : 'Diễn biến lượt vận chuyển tiếp nhận'} action={evidence('history')}>
        <div className="pw-chart-options"><DropdownMenu modal={false}><DropdownMenuTrigger asChild><Button size="sm" variant="outline">Tùy chọn biểu đồ</Button></DropdownMenuTrigger><DropdownMenuContent className="pw-chart-menu" align="start" sideOffset={8} collisionPadding={12}>
          <DropdownMenuCheckboxItem checked={average} onSelect={e => e.preventDefault()} onCheckedChange={setAverage}>Làm mượt theo bình quân 7 ngày</DropdownMenuCheckboxItem>
          <DropdownMenuSeparator/><DropdownMenuLabel>So sánh tối đa 3 đối tượng</DropdownMenuLabel>
          {result.entities.map(e => <DropdownMenuCheckboxItem key={e} disabled={busy || e === entity || !compared.includes(e) && compared.length >= 3} checked={compared.includes(e)} onSelect={e => e.preventDefault()} onCheckedChange={checked => setCompared(c => checked ? [...c, e] : c.filter(v => v !== e))}>{label(e)}</DropdownMenuCheckboxItem>)}
          <DropdownMenuSeparator/><p className="px-2 py-1 text-xs text-muted-foreground">Cập nhật biểu đồ để áp dụng đối tượng so sánh.</p>
        </DropdownMenuContent></DropdownMenu></div>
        <Plot data={history} unit={unit} lines={(result.comparison_entities || [result.entity]).map((name, i) => ({ key: `item${i}`, name: label(name), color: palette[i] }))}/>
      </Panel>
      <Panel title={sales ? 'Lượng đặt hàng theo thứ trong tuần' : 'Lượt vận chuyển theo thứ trong tuần'} subtitle={`${label(result.entity)} · ${unit}/ngày`} action={evidence('rhythm')}><Bars data={rhythm} category="day" unit={unit} lines={[{ key: 'quantity', name: sales ? 'Lượng đặt bình quân' : 'Lượt tiếp nhận bình quân', color: blue }]}/></Panel>
    </>}
    {result && !dirty && mode === 'forecast' && <>
      <Panel title="Bước 2. Tạo dự báo" subtitle="Chọn dữ liệu tham chiếu và thời gian cần dự báo." action={<ForecastHelp label="Giải thích cách tạo dự báo">Mô hình học từ lịch sử và kiểm tra lại trên dữ liệu đã ghi nhận trước khi tạo dự báo. Nếu chưa đủ dữ liệu, hệ thống sẽ thông báo để bạn bổ sung.</ForecastHelp>}>
        <div className="pw-forecast-controls">
          <Field title="Dữ liệu tham chiếu" hint="Khoảng lịch sử dùng để học và kiểm chứng ML. Chọn khoảng dài hơn nếu kỳ dự báo xa hoặc dài."><Select value={training} disabled={busy} onChange={setTraining}><option value="90">3 tháng gần nhất</option><option value="180">6 tháng gần nhất</option><option value="365">12 tháng gần nhất</option></Select></Field>
          <Field title="Ngày bắt đầu dự báo" hint="Ngày đầu tiên cần ước tính lượng đặt mới. Chọn từ hôm nay trở đi."><Input type="date" min={today} max={lastDate} value={startDate} disabled={busy} onChange={e => setStartDate(e.target.value)}/></Field>
          <Field title="Ngày kết thúc dự báo" hint="Tính cả ngày kết thúc. Khoảng dự báo phải nằm trong 90 ngày tới và cần đủ lịch sử để kiểm chứng."><Input type="date" min={startDate || today} max={lastDate} value={endDate} disabled={busy} onChange={e => setEndDate(e.target.value)}/></Field>
          <Button disabled={busy || dirty || !validWindow || result.source_age_days > 90} onClick={() => void perform('Đang học và kiểm chứng dự báo', predict)}>Tạo dự báo</Button>
        </div>
        {!validWindow && <p className="pw-panel-note">Chọn ngày bắt đầu từ hôm nay và ngày kết thúc không trước ngày bắt đầu, trong 90 ngày tới.</p>}
        {result.source_age_days > 90 && <p className="pw-panel-note">Dữ liệu nguồn đã quá cũ. Cần bổ sung dữ liệu mới trước khi tạo dự báo.</p>}
      </Panel>
      {forecast && <Panel title={sales ? 'Lượng đặt dự báo' : 'Lượt tiếp nhận dự báo'} subtitle={`${label(result.entity)} · ${dateLabel(future[0]?.day)} – ${dateLabel(future.at(-1)?.day)}`} action={evidence('forecast')}>
        <div className="pw-forecast-summary"><div><span>{sales ? 'Tổng lượng đặt mới dự kiến' : 'Tổng lượt tiếp nhận dự kiến'} <ForecastHelp label="Giải thích lượng dự kiến">Đây là ước tính cho kỳ đã chọn. Kết quả chưa phải đơn hàng đã nhận hoặc lịch vận chuyển đã xác nhận.</ForecastHelp></span><strong>{fmt(forecastTotal)} <small>{unit}</small></strong></div><div><span>Sai lệch trung bình <ForecastHelp label="Giải thích sai lệch trung bình">Mức chênh lệch bình quân mỗi ngày khi kiểm tra dự báo trên dữ liệu lịch sử. Sai lệch trong tương lai có thể khác.</ForecastHelp></span><strong>{score ? fmt(score.mae) : 'Chưa có kiểm chứng'} <small>{score ? unit + '/ngày' : ''}</small></strong></div></div>
        <Plot data={withCalendarGap(actual, future)} unit={unit} future={future[0]?.day} lines={[{ key: 'actual', name: 'Đã ghi nhận', color: blue }, { key: 'band', name: 'Khoảng dao động tham khảo', color: blue }, { key: 'predicted', name: 'Lượng đặt mới dự báo', color: purple, dash: '5 4' }]}/>
        <div className="pw-forecast-footer"><p>{forecastDirty ? 'Thiết lập đã thay đổi. Tạo lại dự báo để cập nhật kết quả.' : 'Khi đối chiếu kế hoạch, hệ thống tính từ hôm nay đến ngày kết thúc để không bỏ sót đơn và nhu cầu trước kỳ đã chọn.'}</p><Button variant="outline" disabled={!canPlan || busy || dirty || forecastDirty} onClick={() => onPlan(sales ? result.entity : '', calendarDays(today, future.at(-1)!.day), Number(training))}>{sales ? 'Đối chiếu nguồn hàng' : 'Xem kế hoạch giao nhận'}</Button></div>
      </Panel>}
    </>}
  </div>;
}
