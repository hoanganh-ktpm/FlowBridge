// SPDX-License-Identifier: GPL-3.0-or-later
import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Panel, Plot, dateLabel, fmt, unitLabel } from './components';
import type { Forecast, Scenario, DecisionMeasure } from './types';
import type { Proposal } from './scenario-assumptions';
import { RecoveryOptions } from './recovery-options';
import { ScenarioShortage } from './scenario-shortage';
import { scenarioSummary } from './scenario-summary';

const blue='var(--fb-brand-blue)', purple='var(--fb-brand-purple)', cyan='var(--fb-brand-cyan)';
const quantity = (v:number) => new Intl.NumberFormat('vi-VN',{maximumFractionDigits:3}).format(v);
export function ScenarioComparison({ value, forecast, sku, unit, proposal, dirty, busy, onSuggest, onSave, onExport, evidence }: {
  value:Scenario|null;forecast:Forecast;sku:string;unit:string;proposal:Proposal|null;dirty:boolean;busy:boolean;
  onSuggest:(quantity:number,arrival:string,time?:string)=>void;onSave:()=>void;onExport:()=>void;evidence:ReactNode;
}) {
  const decision=value?.decision;
  const shortage=value?scenarioSummary(value):null;
  const model=forecast.model.entities?.[sku];
  const score=model?.validation[model.method];
  const p50Score=model?.p50_holdout?.[model.method];
  const balance=(value?.baseline.series || []).map((p,i)=>({day:p.day, original:value?.reference?.series[i]?.net_position, stressed:p.net_position, supplied:value?.proposed.series[i]?.net_position}));
  const flows=(value?.baseline.series || []).map((p,i)=>({day:p.day, original:value?.reference?.series[i]?.demand, adjusted:p.demand, due:p.known_demand_due, incoming:p.inbound, added:(value?.proposed.series[i]?.inbound || 0)-(p.inbound || 0)}));
  const rows:{key:Exclude<keyof DecisionMeasure,'shortage_basis'|'first_shortage_date'>;label:string;suffix?:string}[]=[
    {key:'new_demand',label:'Lượng khách có thể đặt thêm',suffix:unitLabel(unit)},
    {key:'peak_shortage',label:'Lượng hàng thiếu lớn nhất trong kỳ',suffix:unitLabel(unit)},
    {key:'shortage_days',label:'Số ngày có thời điểm thiếu hàng',suffix:'ngày'},
    {key:'ending_inventory',label:'Hàng còn lại cuối kỳ',suffix:unitLabel(unit)},
    {key:'known_orders_at_risk',label:'Số đơn có nguy cơ thiếu trước giờ chuẩn bị · toàn kho',suffix:'đơn'},
    {key:'demand_headroom_percent',label:'Có thể nhận thêm bao nhiêu nhu cầu mới?',suffix:'Tỷ lệ so với dự báo ban đầu, giữ nguyên cách phân bố theo ngày'},
  ];
  const verdict=decision?.verdict;
  const extraAtEnd=decision ? decision.proposed.ending_inventory-decision.stressed.ending_inventory : 0;
  const summary = !decision ? '' : verdict==='extra_without_risk_reduction'
    ? `Kế hoạch hiện tại đã đáp ứng nhu cầu sau điều chỉnh. Lượng nhập thêm chưa giảm rủi ro trong kỳ này. Nếu không nhập thêm, cuối kỳ còn ${fmt(decision.stressed.ending_inventory)} ${unitLabel(unit)}; nếu nhập thêm, còn ${fmt(decision.proposed.ending_inventory)} ${unitLabel(unit)}${extraAtEnd>0 ? `, tăng ${fmt(extraAtEnd)} ${unitLabel(unit)}` : ''}.`
    : verdict==='no_extra_needed' ? 'Chưa cần bổ sung để tránh thiếu hàng trong kỳ đang xét. Có thể thử nhu cầu cao hơn hoặc hàng về trễ để xem khi nào kế hoạch bắt đầu thiếu hàng.'
    : verdict==='covered' ? 'Phương án đã loại bỏ lượng thiếu theo giả định và chưa thấy đơn thiếu hàng trước hạn trong kỳ.'
    : verdict==='partial' ? 'Phương án giảm được một phần rủi ro. Lượng nhập hoặc ngày hàng về vẫn chưa đáp ứng hết nhu cầu và hạn đơn.'
    : 'Phương án chưa giải quyết được rủi ro đang có. Kiểm tra thời điểm hàng về, lượng bổ sung và các dòng sản phẩm khác cùng đơn.';
  return <Panel title="So sánh ba phương án đủ hàng" subtitle="1. Kế hoạch ban đầu · 2. Thay đổi nhu cầu hoặc ngày hàng về · 3. Nhập thêm hàng" action={evidence}>
    <div className="pw-scenario-model"><Badge variant="secondary">{forecast.model.method==='chronos2' || model?.method==='chronos2' ? 'Chronos-2 · huấn luyện sẵn' : forecast.model.method==='ridge_lag' || model?.method==='ridge_lag' ? 'Ridge · kết quả cũ' : 'Cần cập nhật dự báo từ lịch sử'}</Badge>{model && <span>{model.observations} ngày lịch sử dùng để dự báo{p50Score ? ` · Sai lệch trung bình khi kiểm tra dự báo trên dữ liệu chưa dùng để học: ${fmt(p50Score.mae)} ${unitLabel(unit)}/ngày (${p50Score.observations} ngày)` : score && model.method==='chronos2' ? ` · Sai lệch trung bình khi kiểm tra trên dữ liệu lịch sử: ${fmt(score.mae)} ${unitLabel(unit)}/ngày` : score ? ` · Sai lệch trung bình của mô hình trước điều chỉnh: ${fmt(score.mae)} ${unitLabel(unit)}/ngày; chưa có kết quả kiểm tra riêng cho dự báo sau điều chỉnh` : ''}</span>}</div>
    {!value && <div className="pw-scenario-start"><h3>Kiểm tra kế hoạch khi nhu cầu hoặc lịch hàng thay đổi</h3><p>Điều chỉnh giả định rồi bấm So sánh phương án. Kết quả tách tác động của biến động và tác động của lượng nhập thêm.</p></div>}
    {value && decision && <>
      <details className="iw-shortage-details"><summary><strong>Lượng thiếu lớn nhất trong kỳ</strong><span>Trước bổ sung: {shortage?.before==null?'Chưa xác định':`${fmt(shortage.before)} ${unitLabel(unit)}`} · Sau bổ sung: {shortage?.after==null?'Chưa xác định':`${fmt(shortage.after)} ${unitLabel(unit)}`}</span><small>Xem phạm vi và cách tính</small></summary><ScenarioShortage value={value}/></details>
      {proposal?.multiplier === 100 && proposal.delay === 0 && proposal.quantile === 'p50' && <p className="pw-panel-note">Bạn chưa thay đổi nhu cầu hoặc ngày hàng về, nên phương án 1 và 2 có cùng kết quả. Phương án 3 cho thấy tác động của lượng hàng nhập thêm.</p>}
      <div className={`pw-scenario-verdict${dirty ? ' pw-scenario-outdated' : ''}`} aria-live="polite"><strong>{dirty ? 'Kết quả đang thuộc phương án trước' : verdict==='extra_without_risk_reduction' || verdict==='no_extra_needed' ? 'Lượng bổ sung cần được cân nhắc' : verdict==='covered' ? 'Đủ hàng theo các giả định đã chọn' : 'Còn rủi ro cần xử lý'}</strong><p>{summary}</p></div>
      <div className="pw-scenario-suggestion"><div><strong>{decision.minimum_additional_quantity>0 ? `Bổ sung ${quantity(Math.ceil(decision.minimum_additional_quantity))} ${unitLabel(unit)} từ đầu ngày ${dateLabel(decision.latest_arrival_date)}` : 'Mức bổ sung để hết thiếu trong kỳ: 0'}</strong><p>{decision.minimum_additional_quantity>0 ? 'Làm tròn lên số nguyên từ mức thiếu lớn nhất tại từng thời điểm, với giả định đang dùng. Cần xác nhận nhà cung cấp có thể giao kịp.' : 'Hãy cân đối hàng còn cuối kỳ trước khi tăng lượng nhập. Kết quả chỉ áp dụng cho khoảng ngày đã chọn.'}{(decision.remaining_late_orders_after_suggestion || 0)>0 && ' Vẫn còn đơn trễ do hạn đã qua hoặc sản phẩm khác. Thêm mặt hàng này chưa xử lý hết.'}</p></div>{decision.minimum_additional_quantity>0 && decision.latest_arrival_date && <Button variant="outline" disabled={busy || dirty || decision.minimum_additional_quantity>1e9} onClick={()=>onSuggest(decision.minimum_additional_quantity,decision.latest_arrival_date!)}>Điền mức bổ sung</Button>}</div>
      <Tabs defaultValue="balance" className="pw-scenario-tabs"><TabsList><TabsTrigger value="balance">Khả năng đáp ứng</TabsTrigger><TabsTrigger value="flows">Nhu cầu & hàng về</TabsTrigger><TabsTrigger value="metrics">Chỉ tiêu so sánh</TabsTrigger></TabsList>
        <TabsContent value="balance"><Plot data={balance} unit={unitLabel(unit)} lines={[{key:'original',name:'Dự báo ban đầu',color:'var(--fb-text-muted)',dash:'4 4'},{key:'stressed',name:'Sau khi đổi nhu cầu hoặc ngày hàng về',color:purple},{key:'supplied',name:'Sau bổ sung hàng',color:blue}]}/><p className="pw-panel-note">Biểu đồ lấy hàng có thể dùng cộng hàng dự kiến về, rồi trừ lượng hàng cần đến từng ngày. Số âm nghĩa là còn thiếu. Hệ thống kiểm tra thêm giờ cần chuẩn bị của từng đơn, vì hàng về cuối ngày có thể không kịp cho đơn buổi sáng.</p></TabsContent>
        <TabsContent value="flows"><Plot data={flows} unit={unitLabel(unit)} lines={[{key:'original',name:'Lượng khách có thể đặt thêm · dự báo ban đầu',color:'var(--fb-text-muted)',dash:'4 4'},{key:'adjusted',name:'Lượng đặt mới sau điều chỉnh',color:purple},{key:'due',name:'Đơn đã nhận đến hạn',color:'var(--fb-border-strong)',kind:'bar'},{key:'incoming',name:'Hàng về theo lịch đã điều chỉnh',color:cyan,kind:'bar'},{key:'added',name:'Nhập bổ sung',color:blue,kind:'bar'}]}/><p className="pw-panel-note">Dự báo chỉ là lượng đặt mới. Đơn đã xác nhận được giữ nguyên. Hàng nhập hiện có được lùi theo số ngày trễ đã chọn.</p></TabsContent>
        <TabsContent value="metrics"><div className="pw-scenario-table"><Table aria-label="Đối chiếu ba kịch bản"><TableHeader><TableRow><TableHead>Chỉ tiêu</TableHead><TableHead>1. Kế hoạch ban đầu</TableHead><TableHead>2. Đổi nhu cầu hoặc ngày về</TableHead><TableHead>3. Nhập thêm hàng</TableHead></TableRow></TableHeader><TableBody>{rows.map(row=><TableRow key={row.key}><TableCell>{row.label}<small>{row.suffix}</small></TableCell>{[decision.reference,decision.stressed,decision.proposed].map((measure,i)=>{const amount=measure[row.key];return <TableCell key={i}>{row.key==='demand_headroom_percent' ? typeof amount==='number' ? amount>=0 ? `Tới +${fmt(amount)}%` : `Cần giảm ${fmt(-amount)}%` : 'Chưa có ngưỡng' : fmt(amount)}</TableCell>;})}</TableRow>)}</TableBody></Table></div></TabsContent>
      </Tabs>
      <div className="pw-scenario-assumptions"><span>Thay đổi lượng đặt mới {proposal ? `${proposal.multiplier>=100 ? '+' : ''}${fmt(proposal.multiplier-100)}% so với ${proposal.quantile==='p90' ? 'mức cao tham khảo' : 'Dự báo ban đầu'}` : 'theo giả định đã tính'}</span><span>Hàng nhập trễ {proposal?.delay || 0} ngày</span><span>Nhập thêm {fmt(proposal?.quantity)} {unitLabel(unit)} · {dateLabel(proposal?.arrival)}{proposal?.arrivalTime ? ` lúc ${proposal.arrivalTime}` : ' (đầu ngày)'}</span></div>


      <div className="pw-proposal-actions"><Button variant="outline" disabled={busy || dirty} onClick={onSave}>Lưu phương án</Button><Button disabled={busy || dirty} onClick={onExport}>Tải đề xuất phối hợp</Button></div>
      <details className="pw-recovery-more"><summary>Xem các lựa chọn bổ sung khác</summary><RecoveryOptions options={value.recovery_options || []} disabled={busy || dirty} onFill={onSuggest}/></details>
    </>}
  </Panel>;
}
