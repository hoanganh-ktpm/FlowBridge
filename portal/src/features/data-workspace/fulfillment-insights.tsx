// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef, useState } from 'react';
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ApiError } from '@/lib/api';
import { Field, Select, dateLabel, fmt, unitLabel } from './components';
import { GuidedWizard } from './guided-wizard';
import { InteractiveFlow, WorkspaceDialog } from './interactive-workspace';
import { orderDateTime } from './order-readiness';
import { useWorkspaceState } from './workspace-sync';
import { estimateNames, normalizeInsight, preparationBasis, type FulfillmentOrder, type FulfillmentReport } from './fulfillment-types';
import { FulfillmentTimeline } from './fulfillment-timeline';
import type { PlanningDirectory } from './planning-types';
import type { DecisionRequest } from './decision-dossiers';
import type { Product } from './types';
import './fulfillment-insights.css';

type Parameters={warehouse:string;horizon:string;delay:string;planId:string};
type View='timeline'|'impact'|'bottlenecks'|'capacity';
const when=(value?:string|null)=>value&&Number.isFinite(Date.parse(value))?orderDateTime(value):'Chưa xác định';
const pageSize=3;
const required=['demand','stock','inbound','open_orders','commitments'];
export function FulfillmentInsights({directory,sources,request,onDenied,onReview,onSupply}: {
  directory:PlanningDirectory;sources:Record<string,Product|undefined>;request:DecisionRequest;onDenied:(error:unknown)=>void;
  onReview:(warehouse:string,orderNumber:string,horizon:number)=>void;onSupply:(warehouse:string,sku:string,horizon:number)=>void;
}) {
  const [parameters,setParameters]=useWorkspaceState<Parameters>('insight-parameters',{warehouse:directory.warehouses.length===1?directory.warehouses[0].id:'',horizon:'7',delay:'2',planId:''});
  const [saved,setSaved]=useWorkspaceState<{parameters:Parameters;report:FulfillmentReport}|null>('fulfillment-insights-v1',null,true);
  const [setup,setSetup]=useState(false),[view,setView]=useState<View|null>(null),[detail,setDetail]=useState<FulfillmentOrder|null>(null);
  const [query,setQuery]=useState(''),[filter,setFilter]=useState('all'),[page,setPage]=useState(0),[skuFilter,setSkuFilter]=useState('');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[capacityDetail,setCapacityDetail]=useState<string|null>(null);
  const controller=useRef<AbortController|null>(null),generation=useRef(0);
  useEffect(()=>()=>{generation.current++;controller.current?.abort();},[]);
  const report=saved?.report||null,dirty=!!saved&&JSON.stringify(parameters)!==JSON.stringify(saved.parameters);
  const ready=!!report&&!dirty;
  const valid=required.every(key=>sources[key])&&directory.warehouses.some(w=>w.id===parameters.warehouse)&&[7,14,30,90].includes(Number(parameters.horizon))&&Number.isInteger(Number(parameters.delay))&&Number(parameters.delay)>=0&&Number(parameters.delay)<=14&&parameters.delay!=='';
  const selectedInbound=report?.inbound_options.find(r=>r.plan_id===parameters.planId);
  const activeWarehouse=directory.warehouses.find(w=>w.id===parameters.warehouse);
  function patch(key:keyof Parameters,value:string){setParameters(p=>({...p,[key]:value,...(key==='warehouse'||key==='horizon'?{planId:''}:{})}));}
  function closeSetup(){generation.current++;controller.current?.abort();setBusy(false);setSetup(false);}
  async function calculate(){
    if(!valid||busy)return;
    const ticket=++generation.current;controller.current?.abort();controller.current=new AbortController();setBusy(true);setError('');
    try{
      const result=await request<{fulfillment_insights?:FulfillmentReport|{available:false;message:string}}>('/planning',{
        ...Object.fromEntries(Object.entries(sources).filter(([,p])=>p).map(([key,p])=>[key,{data_product_id:p!.id,contract_version_id:p!.contract_version_id}])),
        warehouse_id:parameters.warehouse,horizon:Number(parameters.horizon),forecast_model:'chronos2',training_days:180,
        include_fulfillment_insights:true,insight_delay_days:Number(parameters.delay),insight_plan_id:parameters.planId||null,
      },controller.current.signal,undefined,true);
      if(ticket!==generation.current)return;
      if(!result.fulfillment_insights?.available){setSaved(null);setError(result.fulfillment_insights?.message||'Chưa đủ dữ liệu để ước tính kết quả đơn.');return;}
      if(result.fulfillment_insights.forecast_method!=='chronos2')throw new Error('Unexpected forecast method');
      setCapacityDetail(null);setSaved({parameters:{...parameters},report:result.fulfillment_insights});setSetup(false);setView('timeline');setPage(0);
    }catch(e){
      if(ticket!==generation.current||controller.current?.signal.aborted)return;
      setSaved(null);setError(e instanceof ApiError?e.message:'Chưa ước tính được kết quả đơn. Kiểm tra nguồn và thử lại.');
      if(e instanceof ApiError&&[401,403].includes(e.status)){setSetup(false);setView(null);setDetail(null);onDenied(e);}
    }finally{if(ticket===generation.current)setBusy(false);}
  }
  function open(value:View){setView(value);setPage(0);setFilter('all');setSkuFilter('');setQuery('');}
  const matches=(o:FulfillmentOrder)=>normalizeInsight(`${o.order_number} ${o.lines.map(l=>`${l.sku} ${l.name||''}`).join(' ')}`).includes(normalizeInsight(query));
  const rows=(report?.orders||[]).filter(matches).filter(o=>view!=='impact'||o.affected_by_delay)
    .filter(o=>filter==='all'||(filter==='overdue'?o.already_overdue:filter==='exposed'?o.base.state==='on_time'&&['late','beyond_window'].includes(o.high_demand.state):(view==='impact'?o.delayed:o.base).state===filter))
    .filter(o=>!skuFilter||o.shortages.some(s=>s.sku===skuFilter));
  const actualPage=Math.min(page,Math.max(0,Math.ceil(rows.length/pageSize)-1)),visible=rows.slice(actualPage*pageSize,(actualPage+1)*pageSize);
  const sourceLinkNames={microsoft:'https://learn.microsoft.com/en-us/dynamics365/business-central/sales-how-to-calculate-order-promising-dates',oracle:'https://docs.oracle.com/en/cloud/saas/readiness/scm/24a/scp24a/24A-sc-planning-wn-f30535.htm',paper:'https://arxiv.org/abs/2510.15821'};
  return <section className="fi-home" aria-label="Ước tính đơn hàng và tác động">
    <p className="iw-page-intro">Kết hợp dự báo Chronos-2 với hàng trong kho, các món cùng đơn và lịch hàng về. Xem đơn nào có thể chuẩn bị kịp, hoặc bị ảnh hưởng khi hàng về trễ.</p>
    <div className="iw-page-toolbar"><span>{saved?`${directory.warehouses.find(w=>w.id===saved.parameters.warehouse)?.name||'Kho đã chọn'} · ${saved.parameters.horizon} ngày · đọc lúc ${when(report?.as_of)}`:'Chưa đọc dữ liệu cho tính năng này.'}</span><Button disabled={busy} onClick={()=>{setError('');setSetup(true);}}>{report?'Đổi giả định hoặc cập nhật':'Chọn kho và tạo phân tích'}</Button></div>
    {error&&!setup&&<p role="alert">{error}</p>}{dirty&&<p role="status">Giả định đã đổi. Cập nhật phân tích để xem kết quả phù hợp.</p>}
    <InteractiveFlow connected={false} label="Công cụ phân tích kết quả đơn" onSelect={id=>open(id as View)} nodes={[
      {id:'timeline',title:'Mốc chuẩn bị từng đơn',description:'So hạn đơn với mốc dự kiến ở nhu cầu thường và cao',value:report?`${report.summary.on_time}/${report.summary.total_orders} đơn kịp theo giả định`:undefined,disabled:!ready},
      {id:'impact',title:'Hàng về trễ ảnh hưởng đơn nào?',description:'Đổi lịch hàng về rồi xem thay đổi từng đơn',value:report?`${report.summary.affected} đơn đổi mốc chuẩn bị`:undefined,disabled:!ready},
      {id:'bottlenecks',title:'Mặt hàng chặn nhiều đơn',description:'Xếp hạng số đơn có cùng mặt hàng thiếu',value:report?`${report.bottlenecks.length} mặt hàng`:undefined,disabled:!ready},
      {id:'capacity',title:'Ngày còn lượt lấy hàng',description:'Bản đồ lịch công bố của nhà vận chuyển',disabled:!ready},
    ]}/>
    <details className="fi-notes"><summary>Phạm vi, phương pháp và tài liệu tham khảo</summary><p>Mốc ước tính kết thúc khi Kho có thể chuẩn bị xong để giao cho đơn vị vận chuyển. Không dự đoán thời điểm giao đến khách hoặc nguyên nhân trễ trong thực tế.</p><p>Học nghiệp vụ từ <a href={sourceLinkNames.microsoft} target="_blank" rel="noreferrer">Microsoft Order Promising</a>, <a href={sourceLinkNames.oracle} target="_blank" rel="noreferrer">Oracle Supply/Demand Gantt</a> và <a href={sourceLinkNames.paper} target="_blank" rel="noreferrer">bài báo Chronos-2</a>. Đây là cách triển khai cho dữ liệu FlowBridge, không sao chép toàn bộ năng lực sản phẩm đó.</p>{report?.limitations.map(note=><p key={note}>{note}</p>)}</details>
    <WorkspaceDialog open={setup} onClose={closeSetup} title="Thiết lập phân tích kết quả đơn" description="Chọn phạm vi và giả định. Chỉ đọc dữ liệu và tính khi xác nhận bước cuối." wide>
      {error&&<p role="alert">{error}</p>}{busy&&<p role="status">Đang dự báo và đối chiếu đơn, Kho, lịch lấy hàng…</p>}
      <GuidedWizard label="Các bước phân tích kết quả đơn" busy={busy} finishLabel="Tạo phân tích" onFinish={()=>void calculate()} steps={[
        {id:'scope',title:'Kho và kỳ kiểm tra',description:'Xét toàn bộ mặt hàng của từng đơn',signature:JSON.stringify([parameters.warehouse,parameters.horizon]),valid:required.every(k=>sources[k])&&!!activeWarehouse,issue:'Cần có đủ nguồn Bán hàng, tồn kho, hàng về và hàng đã giữ. Chọn một kho được cấp quyền.',content:<fieldset disabled={busy}><Field title="Kho cần phân tích"><Select value={parameters.warehouse} onChange={v=>patch('warehouse',v)}><option value="">Chọn kho</option>{directory.warehouses.map(w=><option value={w.id} key={w.id}>{w.name||w.code}</option>)}</Select></Field><Field title="Kỳ ước tính"><Select value={parameters.horizon} onChange={v=>patch('horizon',v)}>{[7,14,30,90].map(n=><option value={n} key={n}>{n} ngày tới</option>)}</Select></Field></fieldset>},
        {id:'delay',title:'Giả lập hàng về trễ',description:'Chọn số ngày trễ và đợt hàng cần thử',signature:JSON.stringify([parameters.delay,parameters.planId]),valid,issue:'Nhập số ngày trễ là số nguyên từ 0 đến 14.',content:<fieldset disabled={busy}><Field title="Hàng về trễ thêm (ngày)"><Input inputMode="numeric" value={parameters.delay} onChange={e=>patch('delay',e.target.value)}/></Field><Field title="Đợt hàng giả lập trễ"><Select value={parameters.planId} onChange={v=>patch('planId',v)}><option value="">Tất cả đợt hàng chưa quá hạn trong kỳ</option>{report&&saved?.parameters.warehouse===parameters.warehouse&&report.inbound_options.map(r=><option value={r.plan_id} key={r.plan_id}>{r.sku} · {fmt(r.quantity)} {unitLabel(r.uom)} · {when(r.expected_at)}</option>)}</Select></Field><p>Lần đầu tính cho toàn bộ lịch hàng về. Có kết quả rồi có thể chọn một đợt cụ thể để phân tích tác động.</p></fieldset>},
        {id:'confirm',title:'Xác nhận',description:'Dùng mô hình hiện có và ba kịch bản chung nguồn',signature:JSON.stringify(parameters),valid,content:<dl className="iw-summary"><div><dt>Kho</dt><dd>{activeWarehouse?.name||activeWarehouse?.code}</dd></div><div><dt>Kỳ kiểm tra</dt><dd>{parameters.horizon} ngày</dd></div><div><dt>Trễ lịch hàng về</dt><dd>{parameters.delay} ngày · {selectedInbound?selectedInbound.sku:'toàn bộ đợt hàng chưa quá hạn trong kỳ'}</dd></div><div><dt>Cách tính</dt><dd>Dự báo thường P50 / nhu cầu cao P90 / P50 và hàng về trễ. Không đổi đơn, không giữ hàng, không đặt lịch.</dd></div></dl>},
      ]}/>
    </WorkspaceDialog>
    <WorkspaceDialog open={!!view} onClose={()=>{setView(null);setDetail(null);}} title={view==='timeline'?'Mốc chuẩn bị dự kiến của đơn':view==='impact'?'Tác động khi hàng về trễ':view==='bottlenecks'?'Mặt hàng chặn nhiều đơn nhất':'Bản đồ lượt lấy hàng'} description="Các kịch bản dùng chung một lần đọc nguồn. Chọn biểu đồ hoặc đơn để xem bằng chứng chi tiết." wide>
      {!ready?<p role="status">Cần tạo lại phân tích cho giả định hiện tại.</p>:report&&<>
        <p className="fi-note">Dữ liệu đọc lúc <strong>{when(report.as_of)}</strong>. {report.preparation_minutes===null?'Kho chưa xác nhận thời gian chuẩn bị, nên chưa có mốc chuẩn bị xong.':`Mốc ước tính = lúc giả lập đủ hàng + ${report.preparation_minutes} phút chuẩn bị.`} Chưa lập lịch nhân sự soạn từng đơn hoặc xác nhận đã bàn giao.</p>
        {(view==='timeline'||view==='impact')&&<><div className="iw-list-toolbar"><Input aria-label="Tìm đơn trong phân tích" value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}} placeholder="Tìm mã đơn, tên hoặc mã mặt hàng…"/><Select label="Lọc ước tính đơn" value={filter} onChange={v=>{setFilter(v);setPage(0);}}><option value="all">Tất cả</option><option value="overdue">Đã quá hạn lúc tra cứu</option>{Object.entries(estimateNames).map(([key,name])=><option value={key} key={key}>{name}</option>)}<option value="exposed">Chỉ kịp ở nhu cầu thông thường</option></Select></div>
          {view==='impact'&&<p className="fi-note">Giả lập {report.delayed_plan_id?'một đợt hàng':'các đợt hàng'} trễ thêm {report.delay_days} ngày: <strong>{report.summary.affected} đơn đổi mốc chuẩn bị; {report.summary.newly_late} đơn từ kịp thành không kịp.</strong> Các đơn đã trễ sẵn không tính là mới trễ.</p>}
          {view==='impact'&&!report.inbound_options.length&&<p className="fi-same-note">Không có đợt hàng chưa quá hạn trong kỳ để dịch lịch. Giả định hàng về trễ chưa có nguồn áp dụng; không có nghĩa mọi đơn đều an toàn.</p>}
          <div className="fi-legend"><span><i className="fi-base"/>Nhu cầu thường · P50</span><span><i className="fi-high"/>Nhu cầu cao · P90</span><span><i className="fi-delayed"/>Hàng về trễ</span><span><i className="fi-deadline"/>Hạn bàn giao</span><span><i className="fi-now-legend"/>Lúc đọc dữ liệu</span></div>
          <FulfillmentTimeline orders={visible} report={report} impact={view==='impact'} onSelect={setDetail}/>
          {!rows.length&&<p>Không có đơn phù hợp với bộ lọc hoặc chưa có đơn đổi mốc trong kịch bản này.</p>}<div className="iw-list-pagination"><span>{rows.length} đơn · trang {actualPage+1}</span><div><Button variant="outline" disabled={!actualPage} onClick={()=>setPage(actualPage-1)}>Trang trước</Button><Button variant="outline" disabled={(actualPage+1)*pageSize>=rows.length} onClick={()=>setPage(actualPage+1)}>Trang sau</Button></div></div>
        </>}
        {view==='bottlenecks'&&<><p>Đếm đơn thiếu từng món tại giờ cần chuẩn bị. {report.preparation_minutes===null?'Kho chưa công bố thời gian chuẩn bị, nên tạm xét tại hạn bàn giao. ':''} Cùng một đơn có thể nằm ở nhiều cột; đường tích lũy tính trên liên hệ đơn–mặt hàng, không phải tỷ lệ đơn duy nhất.</p>{report.bottlenecks.length?<div className="fi-pareto" role="img" aria-label="Pareto mặt hàng thiếu và số đơn bị chặn"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={report.bottlenecks.slice(0,20)} margin={{top:20,right:5,bottom:15,left:0}}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="sku" tick={{fontSize:11}}/><YAxis yAxisId="orders" allowDecimals={false} width={35}/><YAxis yAxisId="percent" orientation="right" domain={[0,100]} width={35} unit="%"/><Tooltip/><Bar yAxisId="orders" dataKey="order_count" name="Liên hệ đơn thiếu món" fill="#3377c7" onClick={(_,index)=>{setSkuFilter(report.bottlenecks[index].sku);setView('timeline');setQuery('');setFilter('all');setPage(0);}}/><Line yAxisId="percent" dataKey="cumulative_percent" name="Tích lũy liên hệ (%)" stroke="#a65b22"/></ComposedChart></ResponsiveContainer></div>:<p>Chưa thấy mặt hàng chặn đơn tại giờ cần chuẩn bị trong kịch bản này.</p>}
          {report.bottlenecks.length>20&&<p>Biểu đồ hiển thị 20 mặt hàng đứng đầu; bảng có đầy đủ các mặt hàng.</p>}<div className="dw-table-scroll"><table className="fi-table"><thead><tr><th>Mặt hàng</th><th>Số đơn thiếu món</th><th>Hành động</th></tr></thead><tbody>{report.bottlenecks.map(item=><tr key={item.sku+item.uom}><td>{item.name||item.sku}<small>{item.sku} · {unitLabel(item.uom)}</small></td><td>{item.order_count}</td><td><Button variant="outline" onClick={()=>{setSkuFilter(item.sku);setView('timeline');setQuery('');setFilter('all');setPage(0);}}>Xem đơn thiếu {item.sku}</Button><Button variant="ghost" onClick={()=>{setView(null);onSupply(saved!.parameters.warehouse,item.sku,Number(saved!.parameters.horizon));}}>Thử bổ sung {item.sku}</Button></td></tr>)}</tbody></table></div>
        </>}
        {view==='capacity'&&<><p>Màu thể hiện số lượt lấy hàng đã dùng so với số lượt công bố; chưa phản ánh tải trọng xe hoặc đơn đã đóng gói. Ô vắng nghĩa là chưa có lịch công bố.</p>{!report.capacity_available?<p>Chưa có nguồn công bố số lượt lấy hàng.</p>:!report.capacity.length?<p>Chưa có lịch công bố trong kỳ. Không kết luận không còn năng lực.</p>:<div className="fi-capacity-grid" role="group" aria-label="Bản đồ lượt lấy hàng">{[...new Set(report.capacity.map(c=>c.carrier_code))].map(code=><section key={code}><h3>{code}</h3><div>{report.capacity.filter(c=>c.carrier_code===code).map(c=><button type="button" key={c.day} className={c.used>c.maximum?'fi-capacity-over':c.maximum<=0?'fi-capacity-off':c.remaining===0?'fi-capacity-full':'fi-capacity-free'} onClick={()=>setCapacityDetail(`${code} · ${dateLabel(c.day)}: ${c.used}/${c.maximum} lượt đã dùng, còn ${c.remaining} lượt.`)}><span>{dateLabel(c.day)}</span><strong>{c.used}/{c.maximum}</strong><small>{c.remaining} lượt còn lại</small></button>)}</div></section>)}</div>}{capacityDetail&&<p role="status">{capacityDetail}</p>}</>}
      </>}
    </WorkspaceDialog>
    <WorkspaceDialog open={!!detail} onClose={()=>setDetail(null)} title={`Ước tính ${detail?.order_number||'đơn hàng'}`} description="Đối chiếu mốc tính toán, nguồn hàng được phân bổ giả lập và lịch đã nhận." wide>{detail&&report&&<>
      <p><strong>Hạn bàn giao: {when(detail.deadline)}</strong>{detail.already_overdue?' · Hạn đã qua; không dùng số liệu hiện tại để kết luận kết quả quá khứ.':''}</p>
      <p className="fi-note">{preparationBasis(detail,detail.base,report.as_of)} {report.preparation_minutes!==null&&`Thời gian chuẩn bị: ${report.preparation_minutes} phút, chưa xét số đơn Kho có thể soạn đồng thời.`}</p>
      <div className="dw-table-scroll"><table className="fi-table"><thead><tr><th>Kịch bản</th><th>Đủ mọi món</th><th>Dự kiến chuẩn bị xong</th><th>Đánh giá</th></tr></thead><tbody>{([['base','Nhu cầu thông thường'],['high_demand','Nhu cầu cao P90'],['delayed',`Hàng về trễ ${report.delay_days} ngày`]] as const).map(([key,name])=><tr key={key}><td>{name}</td><td>{when(detail[key].material_ready_at)}</td><td>{when(detail[key].preparation_complete_at)}</td><td>{estimateNames[detail[key].state]}</td></tr>)}</tbody></table></div>
      {!!detail.missing_forecast_skus.length&&<p role="status">Chưa có lịch sử dự báo cho {detail.missing_forecast_skus.join(', ')}. Chưa xét hết nhu cầu mới của các món này.</p>}
      <h3>Hàng còn cần phân bổ cho đơn</h3>{detail.all_lines_held_or_dispatched?<p>Không còn lượng chưa phân bổ theo bản ghi hàng đã giữ/đã bàn giao. Chưa xác nhận thao tác chuẩn bị đã hoàn tất.</p>:<ul>{detail.lines.map(l=><li key={l.sku+l.uom}>{l.name||l.sku} · {fmt(l.quantity)} {unitLabel(l.uom)}</li>)}</ul>}
      <h3>Nguồn giả lập dùng cho đơn</h3>{!detail.supply_links.length?<p>Chưa phân bổ được đầy đủ nguồn hoặc hàng đã được giữ từ trước.</p>:<ul>{detail.supply_links.map((link,i)=>{const source=report.inbound_options.find(r=>r.plan_id===link.source_id);return <li key={i}>{link.source_id==='stock'?'Hàng chưa giữ tại thời điểm đọc':`Đợt hàng ${link.sku} về ${when(source?.expected_at)}`} · {fmt(link.quantity)} {unitLabel(link.uom)}</li>;})}</ul>}
      <h3>Đối chiếu đủ ba bộ phận</h3>{detail.pickup_checks&&<ul>{(['base','high_demand','delayed'] as const).map(key=><li key={key}><strong>{key==='base'?'Theo nhu cầu thường':key==='high_demand'?'Theo nhu cầu cao':'Khi hàng về trễ'}:</strong> {detail.pickup_checks![key].reason}</li>)}</ul>}<h3>Lịch lấy hàng đã xác nhận</h3>{detail.schedules.length?detail.schedules.map((s,i)=><p key={i}>{s.carrier_name||s.carrier_code} · {when(s.scheduled_handoff_at)}. Cần đối chiếu lịch này với mốc chuẩn bị và số lượt công bố.</p>):<p>Chưa có lịch lấy hàng đã xác nhận trong dữ liệu được đọc.</p>}
      <Button variant="outline" onClick={()=>{setDetail(null);setView(null);onReview(saved!.parameters.warehouse,detail.order_number,Number(saved!.parameters.horizon));}}>Mở điều kiện bàn giao của {detail.order_number}</Button>
    </>}</WorkspaceDialog>
  </section>;
}
