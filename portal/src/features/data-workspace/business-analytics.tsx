// SPDX-License-Identifier: GPL-3.0-or-later
import { useState } from 'react';
import { ArrowRight, BarChart3, Info, TrendingUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Bars, Field, Metric, Panel, Plot, Select, dateLabel, fmt, unitLabel, withCalendarGap } from './components';
import { ProductSelector } from './product-selector';
import { DistributionChart } from './distribution-chart';
import { sourceNames, type Explanation } from './business-evidence';
import type { WorkspaceRequest } from './configuration';
import type { Forecast, Overview, Product } from './types';

const blue = 'var(--fb-brand-blue)', cyan = 'var(--fb-brand-cyan)', purple = 'var(--fb-brand-purple)';
const palette = [blue, purple, cyan];
const ref = (p: Product) => ({ data_product_id: p.id, contract_version_id: p.contract_version_id });
const weekdays = ['Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7', 'Chủ nhật'];
const demand = (p: Product) => p.name === 'sales.confirmed_demand_daily';
const methodName = (method: string) => method === 'ridge_lag' ? 'Học nhịp đặt hàng và xu hướng gần đây' : method === 'seasonal_mean' ? 'Dựa vào cùng thứ trong tuần' : 'Tự chọn bằng kết quả thử trên lịch sử';

export function BusinessAnalytics({ products, busy, request, perform, onEvidence, onPlan, canPlan }: {
  products: Product[]; busy: boolean; request: WorkspaceRequest;
  perform: (label: string, work: () => Promise<void>) => Promise<void>;
  onEvidence: (value: Explanation) => void; onPlan: (sku: string, horizon: number) => void; canPlan: boolean;
}) {
  const topics = products.filter(p => demand(p) || p.name === 'logistics.accepted_shipments_daily');
  const [topic, setTopic] = useState(topics.find(demand)?.id || topics[0]?.id || '');
  const [days, setDays] = useState('30'), [entity, setEntity] = useState('');
  const [compared, setCompared] = useState<string[]>([]), [average, setAverage] = useState(false);
  const [result, setResult] = useState<Overview | null>(null);
  const [applied, setApplied] = useState({ topic: '', days: '', entity: '', compared: [] as string[] });
  const [horizon, setHorizon] = useState('7'), [method, setMethod] = useState('auto'), [training, setTraining] = useState('180');
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [forecastSettings, setForecastSettings] = useState('');
  const product = topics.find(p => p.id === topic);
  const activeProduct = topics.find(p => p.id === result?.config.data_product_id);
  const sales = !!activeProduct && demand(activeProduct);
  const metric = result?.analytics.metrics.find(m => m.item_id === result.entity);
  const dirty = !!result && (topic !== applied.topic || days !== applied.days || entity !== applied.entity || JSON.stringify(compared) !== JSON.stringify(applied.compared));
  const forecastDirty = !!forecast && forecastSettings !== JSON.stringify({ horizon, method, training });
  const labels = result?.entity_labels || {};
  const label = (key: string) => labels[key] ? `${key} - ${labels[key]}` : key;
  const unit = unitLabel(result?.config.unit);
  const seriesRows = new Map<string, Record<string, string | number>>();
  for (const point of result?.comparison?.series || result?.analytics.series || []) {
    const i = (result?.comparison_entities || [result?.entity]).indexOf(point.item_id);
    if (i < 0) continue;
    const row = seriesRows.get(point.timestamp) || { day: point.timestamp };
    row[`item${i}`] = average ? point.moving_average : point.target; seriesRows.set(point.timestamp, row);
  }
  const history = [...seriesRows.values()].sort((a, b) => String(a.day).localeCompare(String(b.day)));
  const rhythm = Object.entries(metric?.day_of_week_average || {}).map(([key, value]) => ({ day: weekdays[Number(key)], quantity: value, order: Number(key) })).sort((a, b) => a.order - b.order);
  const forecastHistory = result?.analytics.series.filter(p => p.item_id === result.entity).map(p => ({ day: p.timestamp, actual: p.target })) || [];
  const future = forecast?.points.filter(p => p.item_id === result?.entity).map(p => ({ day: p.timestamp, predicted: p.p50, band: [p.p10, p.p90] })) || [];
  const forecastTotal = forecast?.points.filter(p => p.item_id === result?.entity).reduce((n, p) => n + p.p50, 0);
  const selectedModel = result ? forecast?.model.entities?.[result.entity] : undefined;

  async function apply() {
    if (!product) return;
    setResult(null); setForecast(null);
    const catalog = products.find(p => p.name === 'warehouse.planning_catalog');
    const report = await request<Overview>('/overview', { ...ref(product), days: Number(days), entity: entity || null, compare_entities: compared, ...(demand(product) && catalog ? { catalog: ref(catalog) } : {}) });
    setResult(report); setEntity(report.entity);
    const choices = report.comparison_entities || [report.entity]; setCompared(choices);
    setApplied({ topic, days, entity: report.entity, compared: choices });
  }
  async function predict() {
    if (!result) return;
    setForecast(null);
    const next = await request<Forecast>('/forecasts', { config: { ...result.config, forecast_horizon: Number(horizon), forecast_model: method }, start_from_request_date: true, training_days: Number(training), allow_statistical_fallback: true });
    setForecast(next); setForecastSettings(JSON.stringify({ horizon, method, training }));
  }
  function explain(kind: 'history' | 'rhythm' | 'forecast' | 'distribution') {
    if (!result) return;
    const evidence = kind === 'forecast' && forecast ? forecast.evidence : result.analytics.evidence;
    if (kind === 'distribution') {
      onEvidence({ title: sales ? 'Phân bố lượng đặt theo ngày' : 'Phân bố lượt tiếp nhận theo ngày',
        meaning: 'Tách ngày có mức phát sinh thường gặp và ngày cao đột biến để chọn mức cần kiểm tra khi chuẩn bị nguồn lực.',
        applications: sourceNames(result.analytics.evidence),
        formula: 'Cột = số ngày trong từng nhóm lượng phát sinh. Đường = 100 × số ngày từ nhóm thấp nhất đến nhóm đang xem ÷ tổng số ngày. Ngưỡng 50%/90% lấy theo thứ tự trong dãy số đã sắp tăng.',
        notes: ['Tính từ toàn bộ ngày đã đọc trong kỳ, kể cả ngày xác nhận không phát sinh. Cần ít nhất 28 ngày để hiển thị.', 'Các nhóm có khoảng rộng bằng nhau, tối đa 12 nhóm; độ rộng làm tròn theo các mức dễ đọc 1, 2, 5 và bội số của chúng. Sản phẩm nguyên chiếc dùng cận nguyên. Nhóm cuối bao gồm giá trị lớn nhất; các nhóm trước không bao gồm cận trên.', 'Chỉ mô tả những ngày đã ghi nhận, không giả định phân phối chuẩn hoặc đưa ra xác suất bảo đảm cho tương lai. Nhịp trong tuần, khuyến mại và xu hướng có thể làm thay đổi kết quả.'],
        start: result.config.start, end: result.config.end, last_observation: result.source_end, technical: result.analytics.evidence });
      return;
    }
    onEvidence({
      title: kind === 'forecast' ? 'Dự đoán những ngày tới' : kind === 'rhythm' ? 'Nhịp trong tuần' : 'Lịch sử và mức thay đổi',
      meaning: kind === 'forecast' ? 'Ước tính lượng phát sinh mới sau ngày tạo dự đoán. Dùng để chuẩn bị nguồn hàng hoặc lịch nhận hàng.' : kind === 'rhythm' ? 'So lượng bình quân của từng thứ để bố trí người và lịch bổ sung phù hợp.' : sales ? 'Lượng sản phẩm khách từng đặt và được xác nhận, gồm cả đơn có thể hủy sau đó.' : 'Số lượt vận chuyển đã được tiếp nhận trong kỳ, không phải số sản phẩm hoặc số giao thành công.',
      applications: sourceNames(evidence),
      items: kind === 'forecast' ? [{ label: 'Tổng lượng dự kiến trong kỳ tới', value: forecastTotal ?? null, unit: result.config.unit }] : [{ label: 'Tổng phát sinh trong kỳ', value: metric?.total ?? null, unit: result.config.unit }, { label: 'Bình quân mỗi ngày', value: metric?.average_per_period ?? null, unit: result.config.unit }, { label: '7 ngày cuối kỳ', value: metric?.recent_7_periods ?? null, unit: result.config.unit }],
      formula: kind === 'forecast' ? 'Dùng lịch sử để ước tính từng ngày; so thử cách tính trên các đoạn lịch sử chưa dùng để học khi đủ dữ liệu.' : kind === 'rhythm' ? 'Bình quân mỗi thứ = tổng phát sinh của thứ đó ÷ số ngày thuộc thứ đó trong khoảng xem.' : 'Mức thay đổi = (tổng 7 ngày cuối ÷ tổng 7 ngày liền trước − 1) × 100%.',
      notes: kind === 'forecast' ? ['Đường nét đứt là ước tính. Vùng nhạt mô tả các mức thấp–cao từ lịch sử; lượng thực tế có thể nằm ngoài vùng này.', 'Mức cao từng ngày chỉ giúp thử cách chuẩn bị; cộng chúng lại không tạo thành một mức đảm bảo cho cả kỳ.', 'Đối chiếu với tồn kho, đơn đang chờ và lịch hàng về trước khi quyết định nhập.'] : ['Không gọi lượng khách đặt là số đã bán hoặc đã giao.', 'So sánh tuần cần đủ hai tuần và tuần trước khác 0. Giá trị không tính được được để trống.', 'Nguồn được đọc thành công đầy đủ: ngày không có sự kiện được tính 0; không điền 0 khi nguồn lỗi.'],
      start: kind === 'forecast' ? future[0]?.day : result.config.start,
      end: kind === 'forecast' ? future.at(-1)?.day : result.config.end,
      last_observation: result.source_end, read_at: typeof evidence.executed_at === 'string' ? evidence.executed_at : forecast?.created_at,
      technical: evidence,
    });
  }
  const evidenceButton = (kind: 'history' | 'rhythm' | 'forecast' | 'distribution') => <button className="pw-text-button" onClick={() => explain(kind)}><Info size={14}/>Nguồn & cách tính</button>;

  if (!topics.length) return <div className="pw-source-setup"><Info size={24}/><div><h2>Chưa có nguồn phân tích được chia sẻ</h2><p>Nhờ IT cấp quyền lịch sử đặt hàng hoặc lượt vận chuyển. Các chức năng kế hoạch vẫn nằm trong cùng không gian này.</p></div></div>;
  return <>
    <section className="dw-filterbar pw-analytics-filters"><Field title="Nội dung"><Select value={topic} disabled={busy} onChange={v => { setTopic(v); setEntity(''); setCompared([]); }}>{topics.map(p => <option key={p.id} value={p.id}>{demand(p) ? 'Lượng khách đặt hàng' : 'Lượt vận chuyển tiếp nhận'}</option>)}</Select></Field><Field title="Xem số liệu đã ghi nhận trong"><Select value={days} disabled={busy} onChange={setDays}>{['7', '14', '30', '90', '365'].map(n => <option key={n} value={n}>{n === '365' ? '12 tháng' : n + ' ngày gần nhất'}</option>)}</Select></Field><Field title={product && demand(product) ? 'Sản phẩm' : 'Nhà vận chuyển'}>{product && demand(product) ? <ProductSelector items={(result?.entities || []).map(sku => ({ sku, name: labels[sku] }))} value={entity} onChange={v => { setEntity(v); setCompared([v, ...compared.filter(e => e !== v)].slice(0, 3)); }} disabled={busy || !result || topic !== applied.topic}/> : <Select value={entity} disabled={busy || !result || topic !== applied.topic} onChange={v => { setEntity(v); setCompared([v]); }}><option value="">Tự chọn khi cập nhật</option>{result?.entities.map(e => <option key={e}>{e}</option>)}</Select>}</Field><Button disabled={busy} onClick={() => void perform('Đang đọc số liệu phân tích', apply)}>Cập nhật biểu đồ</Button>{dirty && <span className="pw-draft-note">Có thay đổi chưa áp dụng · biểu đồ vẫn thuộc bộ lọc đã cập nhật trước đó.</span>}<p className="pw-filter-explainer">Chọn khoảng để xem lượng khách đã đặt hoặc lượt vận chuyển đã tiếp nhận. Dự đoán những ngày sắp tới nằm ở phần bên dưới.</p></section>
    {!result && <div className="dw-welcome"><div className="dw-welcome-icon"><BarChart3 size={32}/></div><h2>Từ số liệu đến quyết định</h2><p>Xem xu hướng, so sánh nhịp trong tuần và ước tính những ngày tới.<br/>Bấm Cập nhật biểu đồ để đọc nguồn theo quyền của bạn.</p></div>}
    {result && <>
      <div className="dw-context"><strong>{label(result.entity)}</strong><span>{dateLabel(result.config.start)} – {dateLabel(result.config.end)}</span><span>Phạm vi nguồn: toàn doanh nghiệp được cấp quyền</span><span className="dw-source-age">Số liệu đến {dateLabel(result.source_end)}</span></div>
      {result.source_age_days > 1 && <p className="pw-data-warning">Nguồn chậm {result.source_age_days} ngày. Biểu đồ lịch sử vẫn xem được; kiểm tra độ mới trước khi dùng dự đoán.</p>}
      <div className="dw-metrics"><Metric title={sales ? 'Lượng khách đặt trong kỳ' : 'Lượt vận chuyển tiếp nhận'} value={fmt(metric?.total)} note={`${unit} · ${label(result.entity)}`} help={sales ? 'Lượng từng xác nhận, có thể gồm đơn hủy sau đó; không phải bán ròng.' : 'Lượt đã tiếp nhận, không phải số giao thành công.'} onDetail={() => explain('history')}/><Metric title="7 ngày cuối so với 7 ngày trước" value={metric?.period_growth_percent == null ? 'Chưa đủ mốc so sánh' : `${metric.period_growth_percent > 0 ? '+' : ''}${fmt(metric.period_growth_percent)}%`} note="Hai khoảng 7 ngày liên tiếp ở cuối kỳ" help="Cần đủ hai khoảng và khoảng trước khác 0." onDetail={() => explain('history')}/><Metric title="Bình quân mỗi ngày" value={fmt(metric?.average_per_period)} note={`${unit}/ngày · dùng để chuẩn bị nguồn lực`} help="Tổng lượng phát sinh chia số ngày trong khoảng đang xem." onDetail={() => explain('history')}/></div>
      <Panel title={sales ? 'Diễn biến lượng đặt hàng' : 'Diễn biến lượt vận chuyển tiếp nhận'} subtitle={sales ? 'So nhịp đặt hàng để ưu tiên sản phẩm cần rà nguồn cung' : 'So nhịp tiếp nhận để chuẩn bị nhân sự và trao đổi lịch nhận'} action={evidenceButton('history')}><div className="pw-comparison-tools"><label><input type="checkbox" checked={average} onChange={e => setAverage(e.target.checked)}/>Làm mượt theo bình quân 7 ngày</label><details><summary>Chọn đối tượng so sánh · tối đa 3</summary><div>{result.entities.map(e => <label key={e}><input type="checkbox" disabled={busy || e === entity || !compared.includes(e) && compared.length >= 3} checked={compared.includes(e)} onChange={event => setCompared(c => event.target.checked ? [...c, e] : c.filter(v => v !== e))}/>{label(e)}</label>)}</div></details></div><Plot data={history} unit={unit} lines={(result.comparison_entities || [result.entity]).map((name, i) => ({ key: `item${i}`, name: label(name), color: palette[i] }))}/><p className="pw-panel-note">{average ? 'Đường biểu diễn bình quân tối đa 7 ngày gần nhất, giúp nhìn xu hướng nhưng có thể làm mờ các ngày tăng đột biến.' : 'Mỗi điểm là phát sinh trong một ngày.'} Chọn đối tượng rồi bấm Cập nhật biểu đồ để so sánh.</p></Panel>
      <div className="pw-two-columns"><Panel title={sales ? 'Lượng đặt hàng theo thứ trong tuần' : 'Lượt vận chuyển theo thứ trong tuần'} subtitle={`Bình quân cùng thứ trong khoảng xem · ${label(result.entity)} · ${unit}/ngày`} action={evidenceButton('rhythm')}><Bars data={rhythm} category="day" unit={unit} lines={[{ key: 'quantity', name: sales ? 'Lượng đặt bình quân' : 'Lượt tiếp nhận bình quân', color: blue }]}/><p className="pw-panel-note">Dùng để sắp lịch bổ sung hàng hoặc bố trí người. Khoảng 7 ngày chỉ có một lần mỗi thứ; xem dài hơn trước khi kết luận có quy luật.</p></Panel><Panel title={sales ? 'Phân bố lượng đặt theo ngày' : 'Phân bố lượt tiếp nhận theo ngày'} subtitle={`${label(result.entity)} · cột: số ngày · đường: tỷ lệ ngày từ mức thấp đến cao`} action={evidenceButton('distribution')}><DistributionChart values={result.analytics.series.filter(p => p.item_id === result.entity).map(p => p.target)} unit={result.config.unit} subject={sales ? 'lượng đặt' : 'lượt tiếp nhận'}/></Panel></div>
      <section className="pw-forecast-section"><div className="pw-section-title"><span><TrendingUp size={20}/></span><div><h2>Dự báo nhu cầu và kế hoạch chuẩn bị</h2><p>Xem ước tính, hiểu mức dao động rồi đối chiếu khả năng đáp ứng.</p></div></div><div className="dw-predict-bar"><Field title="Dự đoán cho"><Select value={horizon} onChange={setHorizon} disabled={busy}>{['7', '14', '30'].map(n => <option key={n} value={n}>{n} ngày tới</option>)}</Select></Field><Button disabled={busy || dirty || result.source_age_days > 90} onClick={() => void perform('Đang tính dự đoán', predict)}>Tạo dự đoán</Button><span className="dw-muted">Bắt đầu từ ngày tạo · giờ Việt Nam</span></div><details className="dw-method-options"><summary>Cách ước tính và kiểm tra độ tin cậy</summary><div className="dw-method-fields"><Field title="Cách ước tính"><Select value={method} onChange={setMethod} disabled={busy}><option value="auto">Tự chọn từ kết quả thử trên lịch sử</option><option value="seasonal_mean">Dựa vào cùng thứ trong tuần</option><option value="ridge_lag">Học thêm xu hướng gần đây</option></Select></Field><Field title="Lịch sử dùng để tính"><Select value={training} onChange={setTraining} disabled={busy}><option value="90">3 tháng</option><option value="180">6 tháng</option><option value="365">12 tháng</option></Select></Field></div><p className="pw-panel-note">Ví dụ: để dự đoán thứ Hai tới, hệ thống xem những thứ Hai trước và nhịp gần đây. Khi đủ lịch sử, hệ thống thử dự đoán lại những ngày đã biết kết quả để so cách tính. Lựa chọn tự động chỉ đổi cách tính nếu sai số thử giảm trên 5%.</p>{selectedModel && <div className="dw-model-check"><p>Đang dùng: {methodName(selectedModel.method)}.</p>{Object.keys(selectedModel.validation).length ? <table><thead><tr><th>Cách tính</th><th>Sai lệch bình quân/ngày</th><th>Sai số so với tổng phát sinh</th></tr></thead><tbody>{Object.entries(selectedModel.validation).map(([name, value]) => <tr key={name}><td>{methodName(name)}</td><td>{fmt(value.mae)} {unit}</td><td>{value.wape_percent == null ? 'Chưa tính được' : fmt(value.wape_percent) + '%'}</td></tr>)}</tbody></table> : <p>Chưa đủ lịch sử để so sánh các cách tính; đang dùng nhịp cùng thứ trong tuần.</p>}<p>Sai số thấp hơn là tốt hơn trên lịch sử đã thử; không bảo đảm tương lai có sai số tương tự.</p></div>}</details>
      {forecast && <><Panel title={`${label(result.entity)} · dự kiến ${fmt(forecastTotal)} ${unit} trong ${forecast.config.forecast_horizon} ngày`} subtitle="Đường liền: lịch sử · nét đứt: ước tính · vùng nhạt: dao động từng ngày" action={evidenceButton('forecast')}><Plot data={withCalendarGap(forecastHistory, future)} unit={unit} future={future[0]?.day} lines={[{ key: 'actual', name: 'Đã ghi nhận', color: blue }, { key: 'band', name: 'Vùng dao động từ lịch sử', color: blue }, { key: 'predicted', name: 'Ước tính ngày tới', color: purple, dash: '5 4' }]}/><p className="pw-panel-note">{sales ? 'Ước tính lượng khách đặt mới; chưa gồm việc đối chiếu đơn đang chờ, tồn kho và hàng nhập.' : 'Ước tính lượt tiếp nhận mới; không đổi số sản phẩm thành số lượt vận chuyển.'} Lượng thực tế có thể cao hoặc thấp hơn vùng nhạt; nên thử thêm khi dự kiến khuyến mại hoặc khách đặt đột biến.</p></Panel>{forecastDirty && <p className="pw-data-warning">Bạn đã đổi cách tính hoặc thời gian. Bấm Tạo dự đoán để cập nhật; biểu đồ hiện tại vẫn là kết quả trước.</p>}<div className="pw-action-brief"><Info size={22}/><div><h2>{sales ? 'Đối chiếu dự báo với nguồn hàng' : 'Đối chiếu dự báo với lịch nhận hàng'}</h2><p>{sales ? 'Đối chiếu cùng sản phẩm tại một kho: đơn đang chờ + lượng khách có thể đặt thêm, hàng chưa giữ và lịch hàng về. Phần kế hoạch tính lại dự đoán theo lịch sử riêng của kho, rồi thử nhập thêm để biết có kịp và giảm thiếu bao nhiêu.' : 'Xem lượt đã đặt, mức tối đa công bố từng ngày và hạn bàn giao của đơn; trao đổi lịch với nhà vận chuyển trước khi cam kết.'}</p>{!canPlan && <p>Cần IT chia sẻ thêm nguồn kế hoạch theo kho và đơn hàng để thực hiện bước đối chiếu này.</p>}</div><Button disabled={!canPlan || busy || dirty || forecastDirty} onClick={() => onPlan(sales ? result.entity : '', forecast.config.forecast_horizon)}>{sales ? 'Kiểm tra đủ hàng & thử nhập' : 'Mở kế hoạch giao nhận'}<ArrowRight size={15}/></Button></div></>}
      </section>
    </>}
  </>;
}
