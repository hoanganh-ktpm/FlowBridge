// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ApiError } from '@/lib/api';
import { Field, Panel, Select, fmt } from './components';
import { scenarioSummary } from './scenario-summary';
import type { Scenario } from './types';

export type DecisionRequest = <T>(path: string, body?: unknown, signal?: AbortSignal, key?: string) => Promise<T>;
export type DecisionCapabilities = { enabled: boolean; roles: string[]; warehouse_ids: string[]; workflow_enabled: boolean };
type Revision = { id: string; number: number; created_at: string; payload: { scenario: Scenario; requires_revalidation: boolean; scope: { sku: string; order_id: string; warehouse_id: string } } };
type Dossier = { id: string; title: string; warehouse_id: string; order_id: string; coordinator_id: string; status: string; version: number; current_revision: number; updated_at: string; revision: Revision; participants: { principal_id: string; role: string }[]; workflow_instance_ref?: string | null; actions?: {can_revise:boolean;can_submit:boolean} };
type Summary = Omit<Dossier, 'revision' | 'participants'>;
type Event = { id: string; event_type: string; created_at: string; payload: { number?: number; note?:string|null;role?:string;outcome?:string } };
const status: Record<string, string> = { DRAFT: 'Bản nháp', VALIDATING: 'Đang xác minh', NEEDS_REVISION: 'Cần sửa phương án', PENDING_APPROVAL: 'Chờ duyệt', APPROVED: 'Đã duyệt', EXECUTING: 'Đang thực hiện', COMPLETED: 'Đã bàn giao', STOPPED: 'Đã dừng' };
const eventNames: Record<string, string> = { CASE_CREATED: 'Lập hồ sơ', REVISION_CREATED: 'Thêm phiên bản', SUBMISSION_REQUESTED:'Gửi xác minh', SUBMISSION_RESUMED:'Đối soát lần gửi', WORKFLOW_OBSERVED:'Cập nhật tiến độ quy trình', TASK_COMPLETED:'Hoàn tất nhiệm vụ',CARRIER_HANDOFF_VERIFIED:'Đã đối chiếu đủ bằng chứng bàn giao cho đơn vị vận chuyển' };
const roleNames: Record<string,string> = {coordinator:'Người điều phối',warehouse:'Kho',logistics:'Vận chuyển',manager:'Quản lý'};
const outcomeNames: Record<string,string> = {confirmed:'Xác nhận điều kiện',changes_required:'Yêu cầu sửa',approved:'Duyệt',rejected:'Không chấp thuận',acknowledged:'Đã nhắc người phụ trách'};
type WorkflowTask = {id:string;name:string;task_key:string;role:string;mine:boolean};
type Workflow = {instance_id:string;revision_id:string;status:string;tasks:WorkflowTask[]};
type WorkflowState = {case:Dossier;workflow:Workflow|null;warnings:string[]};

function message(error: unknown) {
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

export function CreateDossier({ request, scenario, warehouse, capabilities, disabled, onCreated }: {
  request: DecisionRequest; scenario: Scenario; warehouse: string; capabilities: DecisionCapabilities;
  disabled: boolean; onCreated: () => void;
}) {
  const orders = scenario.baseline.orders || [];
  const [order, setOrder] = useState(''), [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const key = useCommandKey();
  const allowed = capabilities.roles.includes('coordinator') && capabilities.warehouse_ids.includes(warehouse);
  async function create() {
    if (!scenario.id || disabled || !allowed) return;
    const body = { simulation_run_id: scenario.id, order_id: order, title: title.trim() };
    setBusy(true); setError('');
    try { await request('/decisions', body, undefined, key('/decisions', body)); onCreated(); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  if (!allowed) return null;
  return <Panel title="Lập hồ sơ quyết định" subtitle="Lưu bằng chứng và phiên bản cho một đơn cụ thể để phối hợp giữa các phòng ban.">
    {!scenario.id && <p>Lưu phương án đã so sánh ở trên trước khi lập hồ sơ.</p>}
    <Field title="Đơn cần phối hợp"><Select value={order} onChange={value => { setOrder(value); setTitle(`Phối hợp chuẩn bị ${orders.find(o => o.order_id === value)?.order_number || 'đơn hàng'}`); }} disabled={busy || disabled}>
      <option value="">Chọn đơn trong phương án</option>{orders.map(o => <option key={o.order_id} value={o.order_id}>{o.order_number || o.order_id}</option>)}
    </Select></Field>
    {!orders.length && <p>Phương án này chưa có đơn chưa phân bổ phù hợp để lập hồ sơ.</p>}
    <Field title="Tên hồ sơ"><Input value={title} maxLength={160} onChange={e => setTitle(e.target.value)} disabled={busy || disabled}/></Field>
    {error && <p role="alert">{error}</p>}
    <Button disabled={busy || disabled || !scenario.id || !order || !title.trim()} onClick={() => void create()}>{busy ? 'Đang lưu hồ sơ…' : 'Lưu hồ sơ quyết định'}</Button>
    <p className="dw-muted">Người phối hợp lấy từ phân công của doanh nghiệp. Bản nháp chưa được duyệt và chưa tạo giao dịch.</p>
  </Panel>;
}

export function DecisionDossiers({ request, capabilities, scenario, scenarioDirty }: {
  request: DecisionRequest; capabilities: DecisionCapabilities; scenario: Scenario | null; scenarioDirty: boolean;
}) {
  const [items, setItems] = useState<Summary[]>([]), [selected, setSelected] = useState<Dossier | null>(null);
  const [history, setHistory] = useState<Revision[]>([]), [events, setEvents] = useState<Event[]>([]);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [comment,setComment] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const key = useCommandKey();
  async function listing() {
    setBusy(true); setError(''); setSelected(null); setHistory([]); setEvents([]); setWorkflow(null);setComment('');
    try {
      const page = await request<{ items: Summary[]; has_more: boolean; warnings: string[] }>('/decisions');
      setItems(page.items); setNotice([...(page.warnings.length ? ['Một số hồ sơ không còn quyền truy cập.'] : []), ...(page.has_more ? ['Đang hiển thị 100 hồ sơ cập nhật gần nhất.'] : [])].join(' '));
    } catch (e) { setItems([]); setError(message(e)); } finally { setBusy(false); }
  }
  useEffect(() => { void listing(); }, [request]);
  async function open(id: string) {
    setBusy(true); setError(''); setSelected(null); setHistory([]); setEvents([]); setWorkflow(null);setComment('');
    try {
      const [detail, revisions, timeline] = await Promise.all([
        request<Dossier>('/decisions/' + id), request<{ items: Revision[]; has_more: boolean }>('/decisions/' + id + '/revisions'), request<{ items: Event[]; has_more: boolean }>('/decisions/' + id + '/timeline'),
      ]);
      setSelected(detail); setHistory(revisions.items); setEvents(timeline.items);
      if (capabilities.workflow_enabled) {
        const current = await request<WorkflowState>('/decisions/' + id + '/workflow');
        setSelected(current.case); setWorkflow(current.workflow);
        if (current.warnings.length && current.workflow) setNotice(current.warnings.join(' '));
      }
      if (revisions.has_more || timeline.has_more) setNotice('Lịch sử dài; đang hiển thị một phần.');
    } catch (e) { setSelected(null); setWorkflow(null); setError(message(e)); } finally { setBusy(false); }
  }
  async function revise() {
    if (!selected || !scenario?.id || scenarioDirty) return;
    const path = '/decisions/' + selected.id + '/revisions';
    const body = { simulation_run_id: scenario.id, expected_version: selected.version };
    setBusy(true); setError('');
    try { await request(path, body, undefined, key(path, body)); await open(selected.id); }
    catch (e) { setSelected(null); setError(message(e)); } finally { setBusy(false); }
  }
  async function submit() {
    if (!selected?.actions?.can_submit) return;
    const path = '/decisions/' + selected.id + '/submit';
    const body = {revision_id:selected.revision.id,expected_version:selected.version};
    setBusy(true); setError('');
    try { await request<WorkflowState>(path,body,undefined,key(path,body)); await open(selected.id); }
    catch (e) { failedCommand(e); } finally { setBusy(false); }
  }
  function failedCommand(e:unknown) {
    if(e instanceof ApiError && [401,403].includes(e.status)){setSelected(null);setHistory([]);setEvents([]);setWorkflow(null);}
    setError(message(e));
  }
  async function complete(task:WorkflowTask,outcome:string) {
    if (!selected || !task.mine) return;
    const path='/decisions/'+selected.id+'/tasks/'+task.id+'/complete';
    const body={revision_id:selected.revision.id,outcome,note:comment.trim()||null};
    setBusy(true); setError('');
    try {await request<WorkflowState>(path,body,undefined,key(path,body));await open(selected.id);}
    catch(e){failedCommand(e);} finally{setBusy(false);}
  }
  async function verifyHandoff(){
    if(!selected)return;
    const path='/decisions/'+selected.id+'/verify-handoff';
    const body={revision_id:selected.revision.id};
    setBusy(true);setError('');
    try{await request<WorkflowState>(path,body,undefined,key(path,body));await open(selected.id);}
    catch(e){if(e instanceof ApiError&&e.status===409)setError('Chưa xác minh được bàn giao đầy đủ từ Kho hoặc quy trình chưa được duyệt. Hồ sơ vẫn chưa hoàn tất.');else failedCommand(e);}
    finally{setBusy(false);}
  }
  const canRevise = selected && (selected.actions?.can_revise ?? capabilities.roles.includes('coordinator')) && ['DRAFT', 'NEEDS_REVISION'].includes(selected.status)
    && !!scenario?.id && !scenarioDirty && scenario.baseline.orders?.some(o => o.order_id === selected.order_id);
  return <div className="pw-dossiers">
    <Button variant="outline" disabled={busy} onClick={() => void listing()}>Tải lại hồ sơ</Button>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <Panel title="Hồ sơ được phân công" subtitle="Chỉ hiện hồ sơ trong tổ chức, phân công và quyền đọc nguồn còn hiệu lực.">
      {items.map(item => <div key={item.id} className="pw-saved-card"><h3>{item.title}</h3><p>{status[item.status] || item.status} · Phiên bản {item.current_revision}</p><Button disabled={busy} variant="outline" onClick={() => void open(item.id)}>Mở hồ sơ {item.title}</Button></div>)}
      {!busy && !items.length && <p>Chưa có hồ sơ được phân công cho bạn.</p>}
    </Panel>
    {selected && <Panel title={selected.title} subtitle={`${status[selected.status] || selected.status} · Phiên bản ${selected.current_revision}`}>
      <p>Đơn: {selected.revision.payload.scenario.baseline.orders?.[0]?.order_number || selected.order_id}</p>
      <p>Thiếu nguồn hàng nhiều nhất: {fmt(scenarioSummary(selected.revision.payload.scenario).before)} → {fmt(scenarioSummary(selected.revision.payload.scenario).after)}</p>
      <p>Hồ sơ lưu bằng chứng tại thời điểm tính. Cần xác minh lại dữ liệu trước khi trình duyệt hoặc thực hiện.</p>
      <p>{selected.participants.length} phân công phối hợp trong hồ sơ.</p>
      {capabilities.workflow_enabled && <>
        <p>Nguồn dữ liệu và quyền truy cập được kiểm tra lại khi gửi, xác nhận và duyệt. Mọi thao tác gắn với phiên bản {selected.current_revision}.</p>
        {selected.actions?.can_submit && <Button disabled={busy} onClick={()=>void submit()}>{selected.status==='VALIDATING'?'Đối soát lần gửi trước':'Gửi Kho và Vận chuyển xác minh'}</Button>}
        {workflow?.tasks.some(task=>task.mine)&&<Field title="Nhận xét hoặc lý do phản hồi"><Input value={comment} maxLength={2000} disabled={busy} onChange={event=>setComment(event.target.value)} placeholder="Bắt buộc khi yêu cầu sửa hoặc không chấp thuận"/></Field>}
        {workflow?.tasks.map(task=><div key={task.id} className="pw-saved-card"><h3>{task.name}</h3><p>{task.mine?'Đến lượt bạn xử lý':'Đang chờ người được phân công'}</p>{task.mine && <>
          {(task.role==='warehouse'||task.role==='logistics')&&<><Button disabled={busy} onClick={()=>void complete(task,'confirmed')}>Xác nhận điều kiện {task.role==='warehouse'?'Kho':'Vận chuyển'}</Button><Button variant="outline" disabled={busy||!comment.trim()} onClick={()=>void complete(task,'changes_required')}>Yêu cầu sửa phương án</Button></>}
          {task.role==='manager'&&<><Button disabled={busy} onClick={()=>void complete(task,'approved')}>Duyệt phiên bản {selected.current_revision}</Button><Button variant="outline" disabled={busy||!comment.trim()} onClick={()=>void complete(task,'changes_required')}>Trả lại để sửa</Button><Button variant="outline" disabled={busy||!comment.trim()} onClick={()=>void complete(task,'rejected')}>Không chấp thuận</Button></>}
          {task.role==='coordinator'&&<Button disabled={busy} onClick={()=>void complete(task,'acknowledged')}>Đã nhắc người phụ trách</Button>}
        </>}</div>)}
        {selected.status==='APPROVED'&&<p>Đã duyệt phương án. Vẫn cần thực hiện nghiệp vụ và xác minh bàn giao thực tế; hồ sơ chưa hoàn tất.</p>}
        {selected.status!=='COMPLETED'&&(['APPROVED','EXECUTING'].includes(selected.status)||workflow?.status==='COMPLETED')&&<Button variant="outline" disabled={busy} onClick={()=>void verifyHandoff()}>Kiểm tra bàn giao thực tế</Button>}
        {selected.status==='COMPLETED'&&<p>Đã đối chiếu đủ các dòng hàng với bằng chứng Kho bàn giao cho đơn vị vận chuyển. Đây là mốc kết thúc quy trình.</p>}
      </>}
      {capabilities.roles.includes('coordinator') && <><Button disabled={busy || !canRevise} onClick={() => void revise()}>Thêm phương án đang xem thành phiên bản mới</Button><p className="dw-muted">So sánh và lưu phương án mới cho cùng đơn trước khi thêm. Phiên bản trước vẫn được giữ lại.</p></>}
      <h3>Lịch sử phiên bản</h3>{history.map(revision => <div key={revision.id}><strong>Phiên bản {revision.number}</strong><p>{new Date(revision.created_at).toLocaleString('vi-VN')} · Thiếu nguồn {fmt(scenarioSummary(revision.payload.scenario).after)}</p></div>)}
      <h3>Diễn biến</h3>{events.map(event => <div key={event.id}><p>{new Date(event.created_at).toLocaleString('vi-VN')} · {eventNames[event.event_type] || 'Cập nhật hồ sơ'}{event.payload.number ? ` · Phiên bản ${event.payload.number}` : ''}{event.payload.role?` · ${roleNames[event.payload.role]||event.payload.role}`:''}{event.payload.outcome?` · ${outcomeNames[event.payload.outcome]||event.payload.outcome}`:''}</p>{event.payload.note&&<p>{event.payload.note}</p>}</div>)}
      {!capabilities.workflow_enabled && <p className="dw-muted">Quy trình xác minh và phê duyệt chưa được kết nối. Hồ sơ đang ở bước lưu bản nháp.</p>}
    </Panel>}
  </div>;
}
