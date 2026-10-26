// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { InteractiveFlow, WorkspaceDialog } from './interactive-workspace';
import { GuidedWizard } from './guided-wizard';
import { ApiError } from '@/lib/api';
import { Field, Panel, Select, fmt, unitLabel } from './components';
import { scenarioSummary } from './scenario-summary';
import { ScenarioShortage } from './scenario-shortage';
import type { Scenario } from './types';
import { DecisionChecks } from './decision-checks';
import { DecisionProgress } from './decision-progress';
import { DecisionAssistant } from './decision-assistant';
import { HandoffPlanFields, carrierBody, carrierDraft, validCarrierDraft, type CarrierDraft, type CarrierPlan, type CarrierOption } from './handoff-plan-fields';

export type DecisionRequest = <T>(path: string, body?: unknown, signal?: AbortSignal, key?: string, fresh?: boolean) => Promise<T>;
export type DecisionCapabilities = { enabled: boolean; roles: string[]; warehouse_ids: string[]; workflow_enabled: boolean };
type Revision = { id: string; number: number; created_at: string; payload: { scenario: Scenario; requires_revalidation: boolean; handoff_plan?:CarrierPlan|null; scope: { sku: string; order_id: string; warehouse_id: string } } };
type Dossier = { id: string; title: string; warehouse_id: string; order_id: string; coordinator_id: string; status: string; version: number; current_revision: number; updated_at: string; revision: Revision; participants: { principal_id: string; role: string }[]; workflow_instance_ref?: string | null; actions?: {can_revise:boolean;can_submit:boolean} };
type Summary = Omit<Dossier, 'revision' | 'participants'>;
type Event = { id: string; event_type: string; created_at: string; payload: { number?: number; note?:string|null;role?:string;outcome?:string;event_type?:string;producer?:string;correlation_id?:string;occurred_at?:string } };
const status: Record<string, string> = { DRAFT: 'Bản nháp', VALIDATING: 'Đang xác minh', NEEDS_REVISION: 'Cần sửa phương án', PENDING_APPROVAL: 'Chờ duyệt', APPROVED: 'Đã duyệt', EXECUTING: 'Đang thực hiện', COMPLETED: 'Đã bàn giao', STOPPED: 'Đã dừng' };
const eventNames: Record<string, string> = { CASE_CREATED: 'Lập hồ sơ', REVISION_CREATED: 'Thêm phiên bản', SUBMISSION_REQUESTED:'Gửi xác minh', SUBMISSION_RESUMED:'Kiểm tra kết quả gửi', WORKFLOW_OBSERVED:'Cập nhật tiến độ quy trình', TASK_COMPLETED:'Hoàn tất nhiệm vụ',SOURCE_OBSERVED:'Tải các bước đã thực hiện',CARRIER_HANDOFF_VERIFIED:'Đã đối chiếu đủ bằng chứng bàn giao cho đơn vị vận chuyển' };
const roleNames: Record<string,string> = {coordinator:'Người điều phối',warehouse:'Kho',logistics:'Vận chuyển',manager:'Quản lý'};
const outcomeNames: Record<string,string> = {confirmed:'Xác nhận điều kiện',changes_required:'Yêu cầu sửa',approved:'Duyệt',rejected:'Không chấp thuận',acknowledged:'Đã nhắc người phụ trách'};
type WorkflowTask = {id:string;name:string;task_key:string;role:string;mine:boolean};
type Workflow = {instance_id:string;revision_id:string;status:string;tasks:WorkflowTask[]};
type WorkflowState = {case:Dossier;workflow:Workflow|null;warnings:string[]};

function message(error: unknown) {
  if (error instanceof ApiError && error.code === 'DECISION_CONDITIONS_UNMET') return error.message;
  if (error instanceof ApiError && error.code === 'DECISION_REQUIRES_REVISION') return 'Dữ liệu làm thay đổi điều kiện hoặc nội dung phương án. Xem mục Phương án còn phù hợp không?, tính lại và gửi xác minh phiên bản mới.';
  if (error instanceof ApiError && error.status === 409) return 'Hồ sơ đã thay đổi hoặc đơn này đã có hồ sơ đang xử lý. Tải lại danh sách trước khi sửa tiếp.';
  if (error instanceof ApiError && error.status === 403) return 'Phân công hoặc quyền đọc nguồn đã thay đổi. Không thể mở hồ sơ này.';
  if (error instanceof ApiError && error.status === 503) return 'Chưa đủ cấu hình người lập, Kho, Vận chuyển và người duyệt độc lập, hoặc dịch vụ chưa sẵn sàng. Nhờ IT kiểm tra.';
  return error instanceof ApiError ? error.message : 'Chưa tải được hồ sơ. Kiểm tra kết nối rồi thử lại.';
}
function useCommandKey() {
  // Keep a command key across a lost response/retry; changed content gets a new key.
  const keys = useRef(new Map<string, string>());
  return (path: string, body: unknown) => {
    const signature = JSON.stringify([path, body]);
    if (!keys.current.has(signature)) keys.current.set(signature, crypto.randomUUID());
    return keys.current.get(signature)!;
  };
}

export function CreateDossier({ request, scenario, warehouse, capabilities, disabled, onCreated, handoffOptions=[] }: {
  request: DecisionRequest; scenario: Scenario; warehouse: string; capabilities: DecisionCapabilities;
  disabled: boolean; onCreated: () => void;
  handoffOptions?:CarrierOption[];
}) {
  const orders = scenario.baseline.orders || [];
  const [order, setOrder] = useState(''), [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [handoff,setHandoff]=useState<CarrierDraft>(null);
  const key = useCommandKey();
  const allowed = capabilities.roles.includes('coordinator') && capabilities.warehouse_ids.includes(warehouse);
  async function create() {
    if (!scenario.id || disabled || !allowed || !orders.some(o=>o.order_id===order) || !title.trim() || title.trim().length>160 || !validCarrierDraft(handoff)) return;
    const body = { simulation_run_id: scenario.id, order_id: order, title: title.trim(),...(handoff?{handoff_plan:carrierBody(handoff)}:{}) };
    setBusy(true); setError('');
    try { await request('/decisions', body, undefined, key('/decisions', body)); onCreated(); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  if (!allowed) return null;
  const identityValid=orders.some(o=>o.order_id===order)&&!!title.trim()&&title.trim().length<=160;
  const contextValid=!!scenario.id&&!disabled&&allowed;
  return <section>
    {error&&<p role="alert">{error}</p>}
    {!scenario.id&&<p role="status">Lưu phương án đã so sánh trước khi lập hồ sơ.</p>}
    <fieldset disabled={busy||disabled}><GuidedWizard label="Các bước lập hồ sơ phối hợp" busy={busy} finishLabel="Lưu hồ sơ phối hợp" onFinish={()=>{if(identityValid&&contextValid&&validCarrierDraft(handoff))void create();}} steps={[
      {id:'order',title:'Chọn đơn cần phối hợp',description:'Một hồ sơ gắn với một đơn trong phương án',valid:identityValid&&contextValid,signature:JSON.stringify([scenario.id,order,title]),issue:'Chọn một đơn thuộc phương án đã lưu và nhập tên hồ sơ.',content:<><Field title="Đơn cần phối hợp"><Select value={order} onChange={value=>{setOrder(value);setTitle(`Phối hợp chuẩn bị ${orders.find(o=>o.order_id===value)?.order_number||'đơn hàng'}`);}}><option value="">Chọn đơn trong phương án</option>{orders.map(o=><option key={o.order_id} value={o.order_id}>{o.order_number||o.order_id}</option>)}</Select></Field><Field title="Tên hồ sơ"><Input value={title} maxLength={160} onChange={e=>setTitle(e.target.value)}/></Field>{!orders.length&&<p>Chưa có đơn phù hợp trong phương án này.</p>}</>},
      {id:'handoff',title:'Điều kiện bàn giao',description:'Chọn lịch dự kiến hoặc dùng lịch đã xác nhận',valid:validCarrierDraft(handoff),signature:JSON.stringify(handoff),issue:'Chọn nhà vận chuyển và nhập thời điểm hợp lệ, hoặc bỏ chọn lịch dự kiến.',content:<HandoffPlanFields value={handoff} options={handoffOptions} onChange={setHandoff} disabled={busy||disabled}/>},
      {id:'confirm',title:'Xác nhận hồ sơ',description:'Kiểm tra đơn, tên hồ sơ và lịch dự kiến',valid:identityValid&&contextValid&&validCarrierDraft(handoff),signature:JSON.stringify([scenario.id,order,title,handoff]),content:<><dl className="iw-summary"><div><dt>Hồ sơ</dt><dd>{title}</dd></div><div><dt>Đơn hàng</dt><dd>{orders.find(o=>o.order_id===order)?.order_number||order}</dd></div><div><dt>Lịch dự kiến</dt><dd>{handoff?`${handoff.carrier} · ${handoff.at.replace('T',' ')}`:'Dùng lịch lấy hàng đã xác nhận khi kiểm tra điều kiện'}</dd></div></dl><p>Người phối hợp lấy từ phân công của doanh nghiệp. Lưu bản nháp chưa gửi duyệt hoặc tạo giao dịch.</p></>},
    ]}/></fieldset>
  </section>;

}

export function DecisionDossiers({ request, capabilities, scenario, scenarioDirty, mode = 'all', handoffOptions=[] }: {
  request: DecisionRequest; capabilities: DecisionCapabilities; scenario: Scenario | null; scenarioDirty: boolean;
  mode?: 'all' | 'tasks' | 'outcomes';
  handoffOptions?:CarrierOption[];
}) {
  const [items, setItems] = useState<Summary[]>([]), [selected, setSelected] = useState<Dossier | null>(null);
  const [history, setHistory] = useState<Revision[]>([]), [events, setEvents] = useState<Event[]>([]);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [queue, setQueue] = useState<{case_id:string;title:string;revision_id:string;task:WorkflowTask}[]>([]);
  const [comment,setComment] = useState('');
  const [listOpen,setListOpen]=useState(false),[listSearch,setListSearch]=useState(''),[listStatus,setListStatus]=useState('all'),[listPage,setListPage]=useState(0);
  const detailEpoch=useRef(0);
  const [detailTab,setDetailTab] = useState('action');
  useEffect(()=>{document.querySelector('.iw-case-content')?.closest('.iw-dialog-body')?.scrollTo({top:0});},[selected?.id,detailTab]);
  const [handoff,setHandoff]=useState<CarrierDraft>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const key = useCommandKey();
  async function listing(fresh = false) {
    detailEpoch.current++;setBusy(true); setError(''); setSelected(null); setHistory([]); setEvents([]); setWorkflow(null);setComment('');
    try {
      if(mode==='tasks'){
        const result=await request<{items:{case_id:string;title:string;revision_id:string;task:WorkflowTask}[];has_more:boolean;warnings:string[]}>('/decision-tasks',undefined,undefined,undefined,fresh);
        setQueue(result.items);setItems([]);
        setNotice(result.warnings.length||result.has_more?'Danh sách nhiệm vụ chưa xác minh đầy đủ. Một số bộ phận chưa trả đủ dữ liệu hoặc chưa cập nhật tiến độ; chưa thể kết luận đã hết việc.':'');
        return;
      }
      const page = await request<{ items: Summary[]; has_more: boolean; warnings: string[] }>('/decisions',undefined,undefined,undefined,fresh);
      setItems(mode==='outcomes'?page.items.filter(item=>item.status!=='DRAFT'):page.items); setNotice([...(page.warnings.length ? ['Một số hồ sơ không còn quyền truy cập.'] : []), ...(page.has_more ? ['Đang hiển thị 100 hồ sơ cập nhật gần nhất.'] : [])].join(' '));
    } catch (e) { setItems([]); setQueue([]); setError(message(e)); } finally { setBusy(false); }
  }
  useEffect(() => { setQueue([]);void listing(); }, [request,mode]);
  function applyDetail(detail:Dossier) {
    setSelected(detail);
    setItems(previous=>previous.map(item=>item.id===detail.id?{
      ...item,status:detail.status,version:detail.version,current_revision:detail.current_revision,
      updated_at:detail.updated_at,workflow_instance_ref:detail.workflow_instance_ref,
    }:item));
  }
  async function open(id: string) {
    const ticket=++detailEpoch.current;setBusy(true); setError(''); setSelected(null); setHistory([]); setEvents([]); setWorkflow(null);setComment('');
    try {
      const [detail, revisions, timeline] = await Promise.all([
        request<Dossier>('/decisions/' + id), request<{ items: Revision[]; has_more: boolean }>('/decisions/' + id + '/revisions'), request<{ items: Event[]; has_more: boolean }>('/decisions/' + id + '/timeline'),
      ]);
      if(ticket!==detailEpoch.current)return;
      applyDetail(detail);setHandoff(carrierDraft(detail.revision.payload.handoff_plan)); setHistory(revisions.items); setEvents(timeline.items);
      if (capabilities.workflow_enabled) {
        const current = await request<WorkflowState>('/decisions/' + id + '/workflow');
        if(ticket!==detailEpoch.current)return;
        applyDetail(current.case); setWorkflow(current.workflow);
        if (current.warnings.length && current.workflow) setNotice(current.warnings.join(' '));
      }
      if (revisions.has_more || timeline.has_more) setNotice('Lịch sử dài; đang hiển thị một phần.');
    } catch (e) { if(ticket!==detailEpoch.current)return;setSelected(null);setHistory([]);setEvents([]);setWorkflow(null);if(e instanceof ApiError && [401,403].includes(e.status)){setItems([]);setQueue([]);} setError(message(e)); } finally { setBusy(false); }
  }
  async function revise() {
    if (!selected || !scenario?.id || scenarioDirty) return;
    const path = '/decisions/' + selected.id + '/revisions';
    const body = { simulation_run_id: scenario.id, expected_version: selected.version,handoff_plan:carrierBody(handoff) };
    const commandTicket=detailEpoch.current;setBusy(true); setError('');
    try { await request(path, body, undefined, key(path, body)); if(commandTicket!==detailEpoch.current)return;if(commandTicket!==detailEpoch.current)return;await open(selected.id); }
    catch (e) { setSelected(null); setError(message(e)); } finally { setBusy(false); }
  }
  async function submit() {
    if (!selected?.actions?.can_submit) return;
    const pending = selected.status === 'VALIDATING' && !selected.workflow_instance_ref;
    const path = '/decisions/' + selected.id + (pending ? '/reconcile-start' : '/submit');
    const body = pending ? {} : {revision_id:selected.revision.id,expected_version:selected.version};
    const commandTicket=detailEpoch.current;setBusy(true); setError('');
    try { await request<WorkflowState>(path,body,undefined,key(path,body)); if(commandTicket!==detailEpoch.current)return;if(commandTicket!==detailEpoch.current)return;await open(selected.id); }
    catch (e) { failedCommand(e); } finally { setBusy(false); }
  }
  function failedCommand(e:unknown) {
    if(e instanceof ApiError && [401,403].includes(e.status)){setSelected(null);setHistory([]);setEvents([]);setWorkflow(null);setItems([]);setQueue([]);}
    setError(message(e));
  }
  async function complete(task:WorkflowTask,outcome:string) {
    if (!selected || !task.mine) return;
    const path='/decisions/'+selected.id+'/tasks/'+task.id+'/complete';
    const body={revision_id:selected.revision.id,outcome,note:comment.trim()||null};
    const commandTicket=detailEpoch.current;setBusy(true); setError('');
    try {
      await request<WorkflowState>(path,body,undefined,key(path,body));if(commandTicket!==detailEpoch.current)return;await open(selected.id);
      if(mode==='tasks'){
        setQueue([]);
        try{
          const result=await request<{items:typeof queue;warnings:string[];has_more:boolean}>('/decision-tasks');
          setQueue(result.items);
          if(result.warnings.length||result.has_more)setNotice('Nhiệm vụ đã phản hồi. Danh sách còn một số hồ sơ cần kiểm tra lại.');
        }catch(e){failedCommand(e);}
      }
    }
    catch(e){failedCommand(e);} finally{setBusy(false);}
  }
  async function verifyHandoff(){
    if(!selected)return;
    const path='/decisions/'+selected.id+'/verify-handoff';
    const body={revision_id:selected.revision.id};
    const commandTicket=detailEpoch.current;setBusy(true);setError('');
    try{await request<WorkflowState>(path,body,undefined,key(path,body));if(commandTicket!==detailEpoch.current)return;await open(selected.id);}
    catch(e){if(e instanceof ApiError&&e.status===409)setError('Chưa xác minh được bàn giao đầy đủ từ Kho hoặc quy trình chưa được duyệt. Hồ sơ vẫn chưa hoàn tất.');else failedCommand(e);}
    finally{setBusy(false);}
  }
  async function replan(){
    if(!selected||!comment.trim())return;
    const path='/decisions/'+selected.id+'/replan';
    const body={revision_id:selected.revision.id,note:comment.trim()};
    const commandTicket=detailEpoch.current;setBusy(true);setError('');
    try{await request<WorkflowState>(path,body,undefined,key(path,body));if(commandTicket!==detailEpoch.current)return;await open(selected.id);}
    catch(e){failedCommand(e);}finally{setBusy(false);}
  }
  async function refreshTimeline(){
    if(!selected)return;
    const result=await request<{items:Event[];has_more:boolean}>('/decisions/'+selected.id+'/timeline');
    setEvents(result.items);
    if(result.has_more)setNotice('Lịch sử dài; đang hiển thị một phần. Cần đối chiếu dữ liệu các bộ phận để xác định đầy đủ bước đã thực hiện.');
  }
  const canRevise = selected && (selected.actions?.can_revise ?? capabilities.roles.includes('coordinator')) && ['DRAFT', 'NEEDS_REVISION'].includes(selected.status)
    && !!scenario?.id && !scenarioDirty && validCarrierDraft(handoff) && scenario.baseline.orders?.some(o => o.order_id === selected.order_id);
  const searchKey=listSearch.trim().toLocaleLowerCase('vi').normalize('NFD').replace(/[\u0300-\u036f]/g,'');
  const matches=(value:string)=>value.toLocaleLowerCase('vi').normalize('NFD').replace(/[\u0300-\u036f]/g,'').includes(searchKey);
  const filteredItems=items.filter(item=>matches(item.title)&&(listStatus==='all'||item.status===listStatus));
  const filteredQueue=queue.filter(item=>matches(item.title+' '+item.task.name));
  const listTotal=mode==='tasks'?filteredQueue.length:filteredItems.length;
  const page=Math.min(listPage,Math.max(0,Math.ceil(listTotal/12)-1));
  function closeDetail(){detailEpoch.current++;setSelected(null);}
  return <div className="pw-dossiers">
    <p className="iw-page-intro">{mode==='tasks'?'Mở nhiệm vụ đến lượt bạn, kiểm tra đúng phiên bản rồi phản hồi.':mode==='outcomes'?'Theo dõi tiến độ và bằng chứng bàn giao của các hồ sơ được phân công.':'Tra hồ sơ phối hợp theo trạng thái. Mỗi hồ sơ giữ phương án, phân công và lịch sử riêng.'}</p>
    <InteractiveFlow connected={false} label="Chức năng hồ sơ phối hợp" onSelect={id=>{setListStatus(id==='list'?'all':id);setListPage(0);setListOpen(true);}} nodes={[
      {id:'list',title:mode==='tasks'?'Mở việc đến lượt bạn':mode==='outcomes'?'Xem tiến độ hồ sơ':'Mở danh sách hồ sơ',description:'Tìm kiếm, lọc và mở từng hồ sơ',value:busy?'Đang tải…':`${mode==='tasks'?queue.length:items.length} ${mode==='tasks'?'nhiệm vụ':'hồ sơ'}`},
      ...(mode==='tasks'?[]:[{id:'PENDING_APPROVAL',title:'Hồ sơ chờ duyệt',description:'Đã qua bước xác minh, đang chờ quản lý',value:`${items.filter(i=>i.status==='PENDING_APPROVAL').length} hồ sơ`},{id:'COMPLETED',title:'Hồ sơ đã bàn giao',description:'Xem bằng chứng hoàn tất bàn giao',value:`${items.filter(i=>i.status==='COMPLETED').length} hồ sơ`}]),
    ]}/>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <WorkspaceDialog open={listOpen} onClose={()=>setListOpen(false)} title={mode==='tasks'?'Việc đến lượt bạn':mode==='outcomes'?'Tiến độ và kết quả hồ sơ':'Danh sách hồ sơ phối hợp'} description="Chọn hồ sơ để xem chi tiết, đóng chi tiết sẽ trở về danh sách đang lọc." wide>
      <div className="iw-list-toolbar"><Input aria-label="Tìm hồ sơ" placeholder="Tìm tên hồ sơ hoặc nhiệm vụ…" value={listSearch} onChange={e=>{setListSearch(e.target.value);setListPage(0);}}/>{mode!=='tasks'&&<Select label="Lọc trạng thái hồ sơ" value={listStatus} onChange={v=>{setListStatus(v);setListPage(0);}}><option value="all">Tất cả trạng thái</option>{Object.entries(status).map(([key,name])=><option key={key} value={key}>{name}</option>)}</Select>}<Button variant="outline" disabled={busy} onClick={()=>void listing(true)}>Tải lại hồ sơ</Button></div>
      <Panel title={mode==='tasks'?'Việc đến lượt bạn':mode==='outcomes'?'Tiến độ và kết quả hồ sơ':'Hồ sơ được phân công'} subtitle="Chỉ hiển thị hồ sơ của doanh nghiệp mà bạn được phân công và còn quyền xem dữ liệu.">
      <div className="pw-dossier-body pw-dossier-list">
      {mode==='tasks'&&filteredQueue.slice(page*12,(page+1)*12).map(item=><div key={item.task.id} className="pw-saved-card"><h3>{item.task.name}</h3><p>{item.title}</p><Button disabled={busy} variant="outline" onClick={()=>{setDetailTab('action');void open(item.case_id);}}>Mở nhiệm vụ {item.title}</Button></div>)}
      {filteredItems.slice(page*12,(page+1)*12).map(item => <div key={item.id} className="pw-saved-card"><h3>{item.title}</h3><p>{status[item.status] || item.status} · Phiên bản {item.current_revision}</p><Button disabled={busy} variant="outline" onClick={() => {setDetailTab('action');void open(item.id);}}>Mở hồ sơ {item.title}</Button></div>)}
      {!busy && !listTotal && <p>{(listSearch.trim()||listStatus!=='all')?'Không có kết quả phù hợp với bộ lọc.':mode==='tasks'?(queue.length?'Chọn một nhiệm vụ để kiểm tra đúng phiên bản và phản hồi.':'Chưa tìm thấy việc đến lượt bạn xử lý trong danh sách vừa tải. Xem cảnh báo nếu dữ liệu chưa đầy đủ.'):mode==='outcomes'?'Chưa có hồ sơ đã gửi trong phần dữ liệu được phép xem.':'Chưa có hồ sơ được phân công cho bạn.'}</p>}
      </div>
    </Panel><div className="iw-list-pagination"><span>{listTotal} kết quả · trang {page+1}</span><div><Button variant="outline" disabled={!page} onClick={()=>setListPage(page-1)}>Trang trước</Button><Button variant="outline" disabled={(page+1)*12>=listTotal} onClick={()=>setListPage(page+1)}>Trang sau</Button></div></div>
    </WorkspaceDialog>
    {selected && <WorkspaceDialog open={!!selected} onClose={closeDetail} title={selected.title} description={`${status[selected.status] || selected.status} · Phiên bản ${selected.current_revision}`} wide>{error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{busy&&<p role="status">Đang cập nhật hồ sơ…</p>}<div className="iw-case-content">
      <div className="pw-dossier-body">
      <p>Đơn: {selected.revision.payload.scenario.baseline.orders?.find(order => order.order_id === selected.order_id)?.order_number || selected.order_id}</p>
      <details className="pw-dossier-summary"><summary>Phương án đã lưu và phân công</summary><ScenarioShortage value={selected.revision.payload.scenario}/>
      <p>Hồ sơ giữ số liệu của lần tính đã lưu. Trước khi gửi duyệt hoặc thực hiện, bấm Kiểm tra dữ liệu hiện tại để xem số liệu có thay đổi không.</p>
      <p>{selected.participants.length} người được phân công: {selected.participants.map(person => roleNames[person.role] || 'Người phối hợp').join(', ')}.</p>
      </details>
      {selected.revision.payload.handoff_plan&&<p>Lịch dự kiến của phiên bản này: {selected.revision.payload.handoff_plan.carrier_code} · {new Date(selected.revision.payload.handoff_plan.scheduled_handoff_at).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'})}. {selected.status==='COMPLETED'?'Đây là lịch trong phương án đã duyệt; kết quả bàn giao được xác minh riêng bằng bằng chứng Kho.':'Đây là điều kiện để xác minh, chưa chứng minh đã đặt lịch hoặc đã bàn giao.'}</p>}
      <InteractiveFlow label="Các bước xem và xử lý hồ sơ" selected={detailTab} onSelect={setDetailTab} nodes={[{id:"checks",title:"Đối chiếu điều kiện",description:"Kiểm tra nguồn trước khi phản hồi"},{id:"action",title:"Phản hồi đúng phiên bản",description:"Xác nhận, trả lại hoặc duyệt theo quyền"},{id:"progress",title:"Theo dõi bàn giao",description:"Xem bằng chứng Kho và Vận chuyển"},{id:"history",title:"Xem các lần thay đổi",description:"Phiên bản và lịch sử xử lý"}]}/>
      <Tabs value={detailTab} onValueChange={setDetailTab} className="pw-section-tabs"><TabsList variant="line" aria-label="Nội dung hồ sơ"><TabsTrigger value="action">Xử lý và duyệt</TabsTrigger><TabsTrigger value="checks">Kiểm tra dữ liệu</TabsTrigger><TabsTrigger value="progress">Tiến độ bàn giao</TabsTrigger><TabsTrigger value="assistant">Hỏi dữ liệu</TabsTrigger><TabsTrigger value="history">Lịch sử</TabsTrigger></TabsList></Tabs>
      <div hidden={detailTab!=='assistant'}>
      <DecisionAssistant key={'assistant-'+selected.revision.id} request={request} caseId={selected.id} revisionId={selected.revision.id} onDenied={failedCommand}/></div>
      {capabilities.workflow_enabled && <>
        <div hidden={detailTab!=='checks'}><DecisionChecks key={selected.revision.id} request={request} caseId={selected.id} disabled={busy} onDenied={failedCommand} onSynced={refreshTimeline}/></div>
        <div hidden={detailTab!=='action'}>{selected.status==='COMPLETED'&&<div className="pw-next-step"><span>Hồ sơ đã hoàn tất. Xem bằng chứng của các bộ phận tại bước bàn giao.</span><Button variant="outline" onClick={()=>setDetailTab('progress')}>Xem bằng chứng bàn giao</Button></div>}<p>Nguồn dữ liệu và quyền truy cập được kiểm tra lại khi gửi, xác nhận và duyệt. Mọi thao tác gắn với phiên bản {selected.current_revision}.</p>
        {selected.actions?.can_submit && <Button disabled={busy} onClick={()=>void submit()}>{selected.status==='VALIDATING'?'Kiểm tra kết quả gửi trước':'Gửi Kho và Vận chuyển xác minh'}</Button>}
        {workflow?.tasks.some(task=>task.mine)&&<Field title="Nhận xét hoặc lý do phản hồi"><Input value={comment} maxLength={2000} disabled={busy} onChange={event=>setComment(event.target.value)} placeholder="Bắt buộc khi yêu cầu sửa hoặc không chấp thuận"/></Field>}
        {workflow?.tasks.map(task=><div key={task.id} className="pw-saved-card"><h3>{task.name}</h3><p>{task.mine?'Đến lượt bạn xử lý':'Đang chờ người được phân công'}</p>{task.mine && <>
          {(task.role==='warehouse'||task.role==='logistics')&&<><Button disabled={busy} onClick={()=>void complete(task,'confirmed')}>Xác nhận điều kiện {task.role==='warehouse'?'Kho':'Vận chuyển'}</Button><Button variant="outline" disabled={busy||!comment.trim()} onClick={()=>void complete(task,'changes_required')}>Yêu cầu sửa phương án</Button></>}
          {task.role==='manager'&&<><Button disabled={busy} onClick={()=>void complete(task,'approved')}>Duyệt phiên bản {selected.current_revision}</Button><Button variant="outline" disabled={busy||!comment.trim()} onClick={()=>void complete(task,'changes_required')}>Trả lại để sửa</Button><Button variant="outline" disabled={busy||!comment.trim()} onClick={()=>void complete(task,'rejected')}>Không chấp thuận</Button></>}
          {task.role==='coordinator'&&<Button disabled={busy} onClick={()=>void complete(task,'acknowledged')}>Đã nhắc người phụ trách</Button>}
        </>}</div>)}
        {selected.status==='APPROVED'&&<p>Đã duyệt phương án. Vẫn cần thực hiện nghiệp vụ và xác minh bàn giao thực tế; hồ sơ chưa hoàn tất.</p>}
        {['APPROVED','EXECUTING'].includes(selected.status)&&capabilities.roles.some(role=>['coordinator','manager'].includes(role))&&<>
          <Field title="Lý do cần lập lại phương án"><Input maxLength={2000} disabled={busy} value={comment} onChange={event=>setComment(event.target.value)}/></Field>
          <Button variant="outline" disabled={busy||!comment.trim()} onClick={()=>void replan()}>Dừng chờ bàn giao để lập phiên bản mới</Button>
          <p>Yêu cầu này kết thúc lượt duyệt cũ để lập lại phương án. Hàng đã giữ, phiếu nhập và giao dịch đã thực hiện vẫn còn; cần đối chiếu dữ liệu các bộ phận trước khi gửi phiên bản mới.</p>
        </>}
        </div><div hidden={detailTab!=='progress'}>
        <DecisionProgress events={events} orderId={selected.order_id}/>
        {selected.status!=='COMPLETED'&&(['APPROVED','EXECUTING'].includes(selected.status)||workflow?.status==='COMPLETED')&&<Button variant="outline" disabled={busy} onClick={()=>void verifyHandoff()}>Kiểm tra bàn giao thực tế</Button>}
        {selected.status==='COMPLETED'&&<p>Đã đối chiếu đủ các dòng hàng với bằng chứng Kho bàn giao cho đơn vị vận chuyển. Đây là mốc kết thúc quy trình.</p>}</div>
      </>}
      <div hidden={detailTab!=='action'}>{capabilities.roles.includes('coordinator') && (selected.actions?.can_revise ?? ['DRAFT','NEEDS_REVISION'].includes(selected.status)) && <>{selected.actions?.can_revise&&<HandoffPlanFields value={handoff} options={handoffOptions} onChange={setHandoff} disabled={busy}/>}<Button disabled={busy || !canRevise} onClick={() => void revise()}>Dùng phương án đã lưu để lập phiên bản mới</Button><p className="dw-muted">So sánh và lưu phương án mới cho cùng đơn trước khi thêm. Phiên bản trước vẫn được giữ lại.</p></>}
      </div><div hidden={detailTab!=='history'}><h3>Lịch sử phiên bản</h3>{history.map(revision => <div key={revision.id}><strong>Phiên bản {revision.number}</strong><p>{new Date(revision.created_at).toLocaleString('vi-VN')} · Sau bổ sung còn thiếu lớn nhất {fmt(scenarioSummary(revision.payload.scenario).after)} {unitLabel(scenarioSummary(revision.payload.scenario).unit || '')} trong kỳ</p></div>)}
      <h3>Diễn biến</h3>{events.map(event => <div key={event.id}><p>{new Date(event.created_at).toLocaleString('vi-VN')} · {eventNames[event.event_type] || 'Cập nhật hồ sơ'}{event.payload.number ? ` · Phiên bản ${event.payload.number}` : ''}{event.payload.role?` · ${roleNames[event.payload.role]||event.payload.role}`:''}{event.payload.outcome?` · ${outcomeNames[event.payload.outcome]||event.payload.outcome}`:''}</p>{event.payload.note&&<p>{event.payload.note}</p>}</div>)}
      </div>
      {!capabilities.workflow_enabled && <p className="dw-muted">Quy trình xác minh và phê duyệt chưa được kết nối. Hồ sơ đang ở bước lưu bản nháp.</p>}
      </div>
    <Button variant="outline" onClick={closeDetail}>Về danh sách hồ sơ</Button></div></WorkspaceDialog>}
  </div>;
}
