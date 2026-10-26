// SPDX-License-Identifier: GPL-3.0-or-later
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field, Select, dateLabel, fmt, unitLabel } from './components';
import { GuidedWizard } from './guided-wizard';
import { InteractiveFlow, WorkspaceDialog } from './interactive-workspace';
import { ScenarioComparison } from './scenario-comparison';
import { SensitivityChart } from './sensitivity-chart';
import { CreateDossier, type DecisionCapabilities, type DecisionRequest } from './decision-dossiers';
import type { Proposal } from './scenario-assumptions';
import type { Planning, PlanProduct } from './planning-types';
import type { Scenario } from './types';
import type { ReactNode } from 'react';

export function PlanningWorkspace({plan,selected,proposal,onProposal,calculated,scenario,dirty,scenarioDirty,busy,error,request,capabilities,onCreated,onInputs,onScope,onCompare,onSave,onExport,onCancel,perform,evidence}: {
  plan:Planning;selected:PlanProduct;proposal:Proposal;onProposal:(v:Proposal)=>void;calculated:Proposal|null;scenario:Scenario|null;
  dirty:boolean;scenarioDirty:boolean;busy:boolean;error:string;request:DecisionRequest;capabilities:DecisionCapabilities|null;
  onCreated:()=>void;onInputs:()=>void;onScope:()=>void;onCompare:()=>Promise<void>;onSave:()=>Promise<void>;onExport:()=>void;onCancel:()=>void;
  perform:(label:string,work:()=>Promise<void>)=>Promise<void>;evidence:(key:string)=>ReactNode;
}) {
  const [dialog,setDialog]=useState<'edit'|'result'|'case'|null>(null),[resultTab,setResultTab]=useState('compare');
  const method=plan.forecast.model.entities?.[plan.selected_sku]?.method||plan.forecast.model.method;
  const ready=!dirty&&method==='chronos2',fresh=!!scenario&&!dirty&&!scenarioDirty;
  const canCreate=!!scenario?.id&&fresh&&!!capabilities?.roles.includes('coordinator')&&capabilities.warehouse_ids.includes(plan.warehouse_id);
  const validQuantity=Number.isInteger(proposal.quantity)&&proposal.quantity>=0&&proposal.quantity<=1e9;
  const validArrival=!!proposal.arrival&&proposal.arrival>=plan.start&&proposal.arrival<=plan.end&&(!proposal.arrivalTime||/^([01]\d|2[0-3]):[0-5]\d$/.test(proposal.arrivalTime));
  const validAssumptions=Number.isFinite(proposal.multiplier)&&proposal.multiplier>=0&&proposal.multiplier<=500&&Number.isInteger(proposal.delay)&&proposal.delay>=0&&proposal.delay<=90&&['p50','p90'].includes(proposal.quantile);
  const valid=ready&&validQuantity&&validArrival&&validAssumptions;
  function close(){if(dialog==='edit'&&busy)onCancel();setDialog(null);}
  function suggest(quantity:number,arrival:string,arrivalTime?:string){onProposal({...proposal,quantity,arrival,arrivalTime});setDialog('edit');}
  const review=<dl className="iw-summary"><div><dt>Mặt hàng</dt><dd>{selected.name||selected.sku} · {selected.sku}</dd></div><div><dt>Nhập thêm</dt><dd>{Number.isFinite(proposal.quantity)?fmt(proposal.quantity):'Chưa nhập'} {unitLabel(selected.uom)}</dd></div><div><dt>Hàng sẵn sàng trong kho</dt><dd>{dateLabel(proposal.arrival)} {proposal.arrivalTime||'· từ đầu ngày, hoặc thời điểm đọc dữ liệu nếu là hôm nay'}</dd></div><div><dt>Nhu cầu mới</dt><dd>{proposal.multiplier}% mức {proposal.quantile==='p90'?'chuẩn bị cao hơn':'dự kiến'}</dd></div><div><dt>Hàng đang chờ về trễ thêm</dt><dd>{proposal.delay} ngày</dd></div></dl>;
  return <section className="iw-page-home" aria-label="Lập phương án nhập hàng">
    <div className="iw-page-status"><strong>{selected.name||selected.sku}</strong><span>{selected.sku} · có thể dùng {fmt(selected.available)} {unitLabel(selected.uom)} · đơn còn cần {fmt(selected.known_quantity)} {unitLabel(selected.uom)}</span></div>
    <InteractiveFlow label="Chức năng lập phương án" selected={dialog||undefined} onSelect={id=>{if(id==='inputs')onInputs();else setDialog(id as 'edit'|'result'|'case');}} nodes={[
      {id:'inputs',title:'Xem dữ liệu đầu vào',description:'Đơn đang chờ, dự báo và lịch hàng về'},
      {id:'edit',title:'Thiết lập phương án',description:'Nhập từng bước, xác nhận rồi mới tính',disabled:!ready||busy},
      {id:'result',title:'Xem kết quả so sánh',description:scenario&&!fresh?'Đầu vào đã đổi · cần tính lại':'Biểu đồ, thử lượng nhập và lưu phương án',disabled:!fresh||busy},
      {id:'case',title:'Lập hồ sơ phối hợp',description:scenario&&!scenario.id?'Lưu phương án trong kết quả so sánh trước':'Chỉ mở khi phương án hiện tại đã lưu',disabled:!canCreate||busy},
    ]}/>
    {!ready&&<p role="status">Chọn lại phạm vi và tạo dự báo Chronos-2 trước khi lập phương án. <Button variant="outline" onClick={onScope}>Chọn kho và phạm vi</Button></p>}
    {scenario&&!fresh&&<p role="status">Phương án hoặc phạm vi đã thay đổi. Mở Thiết lập phương án và so sánh lại trước khi lưu hoặc phối hợp.</p>}
    {fresh&&<p className="iw-page-status">{scenario?.id?'Phương án hiện tại đã lưu.':'Đã tính so sánh, chưa lưu phương án.'} Mở kết quả để xem chi tiết.</p>}
    <WorkspaceDialog open={dialog==='edit'} onClose={close} title="Thử lượng nhập và ngày hàng về" description="Chỉ mô phỏng, chưa tạo giao dịch nhập hàng. Hoàn tất từng bước rồi xác nhận để so sánh." wide>
      {error&&<p role="alert">{error}</p>}{busy&&<Button variant="outline" onClick={onCancel}>Dừng chờ</Button>}
      <fieldset disabled={busy||!ready}><GuidedWizard label="Các bước nhập phương án" busy={busy} finishLabel="So sánh phương án" onFinish={()=>{if(valid)void perform('Đang so sánh phương án',async()=>{await onCompare();setResultTab('compare');setDialog('result');});}} steps={[
        {id:'quantity',title:'Nhập số lượng',description:'Số lượng bổ sung cho mặt hàng đang chọn',valid:validQuantity&&ready,signature:String(proposal.quantity),issue:'Nhập số nguyên từ 0 đến 1.000.000.000. Nhập 0 nếu chỉ muốn thử biến động nhu cầu.',content:<Field title={`Nhập thêm (${unitLabel(selected.uom)})`}><Input aria-label="Số lượng nhập thêm" inputMode="numeric" value={Number.isFinite(proposal.quantity)?proposal.quantity:''} onChange={e=>{if(/^[0-9]*$/.test(e.target.value))onProposal({...proposal,quantity:e.target.value===''?NaN:Number(e.target.value)});}}/></Field>},
        {id:'arrival',title:'Chọn thời điểm hàng về',description:'Khi nào hàng bổ sung có thể dùng trong kho',valid:validArrival,signature:JSON.stringify([proposal.arrival,proposal.arrivalTime]),issue:'Chọn ngày trong kỳ kế hoạch và giờ hợp lệ nếu có nhập giờ.',content:<><Field title="Ngày dự kiến hàng về"><Input type="date" min={plan.start} max={plan.end} value={proposal.arrival} onChange={e=>onProposal({...proposal,arrival:e.target.value})}/></Field><Field title="Giờ dự kiến hàng về (giờ Việt Nam)"><Input type="time" value={proposal.arrivalTime||''} onChange={e=>onProposal({...proposal,arrivalTime:e.target.value||undefined})}/></Field><p>Để trống giờ: tính hàng sẵn sàng từ đầu ngày, hoặc lúc đọc dữ liệu nếu là hôm nay. Đây là giả định, chưa phải xác nhận của Kho.</p></>},
        {id:'assumptions',title:'Chọn giả định nhu cầu',description:'Giữ nguyên hoặc thử nhu cầu và lịch hàng khác',valid:validAssumptions,signature:JSON.stringify([proposal.multiplier,proposal.delay,proposal.quantile]),issue:'Mức tăng/giảm từ −100% đến 400%; số ngày trễ là số nguyên từ 0 đến 90.',content:<><Field title="Mức nhu cầu để chuẩn bị"><Select value={proposal.quantile} onChange={quantile=>onProposal({...proposal,quantile})}><option value="p50">Theo lượng dự kiến</option><option value="p90">Chuẩn bị cho mức đặt cao hơn</option></Select></Field><Field title="Giả sử lượng khách đặt tăng/giảm (%)"><Input type="number" min={-100} max={400} value={Number.isFinite(proposal.multiplier)?proposal.multiplier-100:''} onChange={e=>onProposal({...proposal,multiplier:e.target.value===''?NaN:100+Number(e.target.value)})}/></Field><Field title="Hàng đang chờ về trễ thêm (ngày)"><Input type="number" min={0} max={90} step={1} value={Number.isFinite(proposal.delay)?proposal.delay:''} onChange={e=>onProposal({...proposal,delay:e.target.value===''?NaN:Number(e.target.value)})}/></Field><p>Chỉ thay đổi nhu cầu mới dự báo và lịch hàng về hiện có. Các đơn đã xác nhận được giữ nguyên.</p></>},
        {id:'review',title:'Xác nhận phương án',description:'Kiểm tra lại số lượng, thời điểm và giả định',valid,signature:JSON.stringify(proposal),content:review},
      ]}/></fieldset>
    </WorkspaceDialog>
    <WorkspaceDialog open={dialog==='result'} onClose={close} title="Kết quả so sánh phương án" description="Ba phương án dùng cùng dữ liệu. Lưu phương án hiện tại trước khi lập hồ sơ phối hợp." wide>
      {error&&<p role="alert">{error}</p>}{busy&&<p role="status">Đang xử lý…</p>}
      {!fresh?<p role="status">Đầu vào đã thay đổi. Tính lại để sử dụng kết quả.</p>:<>
        <div className="dw-actions"><Button variant={resultTab==='compare'?'default':'outline'} onClick={()=>setResultTab('compare')}>Biểu đồ và chỉ tiêu</Button><Button variant={resultTab==='sensitivity'?'default':'outline'} disabled={!scenario?.sensitivity} onClick={()=>setResultTab('sensitivity')}>Chọn lượng và ngày nhập</Button><Button variant="outline" onClick={()=>setDialog('edit')}>Sửa phương án</Button></div>
        {resultTab==='compare'?<ScenarioComparison value={scenario} forecast={plan.forecast} sku={plan.selected_sku} unit={selected.uom} proposal={calculated} dirty={!fresh} busy={busy} onSuggest={suggest} onSave={()=>void perform('Đang lưu phương án',onSave)} onExport={onExport} evidence={evidence('scenario')}/>:scenario?.sensitivity&&<SensitivityChart value={scenario.sensitivity} baselineShortage={scenario.decision?.stressed.peak_shortage??scenario.baseline.metrics.peak_shortage} quantity={proposal.quantity} arrival={proposal.arrival} disabled={busy||!fresh} onSelect={(quantity,arrival)=>suggest(quantity,arrival)}/>}
        {canCreate&&<Button disabled={busy} onClick={()=>setDialog('case')}>Tiếp tục lập hồ sơ</Button>}
      </>}
    </WorkspaceDialog>
    <WorkspaceDialog open={dialog==='case'} onClose={close} title="Lập hồ sơ phối hợp" description="Hồ sơ sử dụng đúng phương án đã lưu và chỉ lập cho một đơn trong phương án." wide>
      {canCreate&&scenario&&capabilities?<CreateDossier handoffOptions={plan.capacity_calendar} request={request} scenario={scenario} warehouse={plan.warehouse_id} capabilities={capabilities} disabled={busy||!fresh} onCreated={onCreated}/>:<p role="status">Cần tính và lưu phương án hiện tại trước khi lập hồ sơ.</p>}
    </WorkspaceDialog>
  </section>;
}
