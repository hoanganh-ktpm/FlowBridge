// SPDX-License-Identifier: GPL-3.0-or-later
// Local, interactive acceptance tool. Not part of the production application.
import { useState } from 'react';
import { useSession } from '@/app/session';
import { ApiError } from '@/lib/api';

const actions = [
  ['sales.operations.order_read', 'Bán hàng · Đọc đơn gốc'],
  ['warehouse.operations.inventory_read', 'Kho · Đọc tồn và phiên bản'],
  ['warehouse.operations.inbound_read', 'Kho · Đọc kế hoạch hàng về'],
  ['warehouse.operations.post_receipt', 'Kho · Ghi phiếu nhận hàng'],
  ['warehouse.operations.reserve_order', 'Kho · Giữ đủ hàng cho đơn'],
  ['warehouse.operations.reservation_read', 'Kho · Đọc lượng đã giữ'],
  ['warehouse.operations.prepare_dispatch', 'Kho · Chuẩn bị phiếu xuất'],
  ['warehouse.operations.dispatch_read', 'Kho · Đọc bản kê xuất hàng'],
  ['logistics.operations.capacity_read', 'Vận chuyển · Đọc năng lực'],
  ['logistics.operations.book_carrier', 'Vận chuyển · Xác nhận lịch tiếp nhận'],
  ['logistics.operations.shipment_read', 'Vận chuyển · Đọc vận đơn'],
  ['warehouse.operations.record_carrier_handoff', 'Kho · Ghi nhận bàn giao cho đơn vị vận chuyển'],
  ['logistics.operations.reconcile_handoff', 'Vận chuyển · Đối chiếu bàn giao'],
  ['sales.operations.reconcile_handoff', 'Bán hàng · Đối chiếu hoàn tất đơn'],
  ['warehouse.operations.handoff_proof_read', 'Kho · Đọc bằng chứng bàn giao'],
] as const;

export default function NativeOwner({ onExit }: { onExit: () => void }) {
  const session = useSession();
  const [action,setAction]=useState<string>(actions[0][0]);
  const [payload,setPayload]=useState('{}');
  const [key,setKey]=useState<string>(()=>crypto.randomUUID());
  const [attested,setAttested]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const [result,setResult]=useState<unknown>(null);
  const physical=action==='warehouse.operations.record_carrier_handoff';
  if (!import.meta.env.DEV || !['localhost','127.0.0.1','[::1]'].includes(location.hostname)
      || session.mode!=='live' || !session.authenticated) return <p>Công cụ chỉ dùng trên local với SSO thật.</p>;
  async function execute() {
    setResult(null);setError('');
    if(payload.length>65536){setError('Nội dung vượt giới hạn kiểm thử.');return;}
    let body:unknown;
    try{body=JSON.parse(payload);}catch{setError('Nội dung JSON chưa hợp lệ.');return;}
    if(!body||typeof body!=='object'||Array.isArray(body)||!key.trim()||(physical&&!attested)){
      setError('Cần object JSON, mã lệnh và xác nhận cho bước bàn giao.');return;
    }
    setBusy(true);
    try {
      const value=await session.api.request('/api/v1/workflows/actions/'+action+'/execute',{
        method:'POST',body,key:key.trim(),signal:AbortSignal.timeout(45000),
      });
      setResult(value);
    } catch(e) {
      if(e instanceof ApiError && e.status===401)session.invalidate();
      setError(e instanceof ApiError?`HTTP ${e.status}: ${e.message}`:'Chưa xác minh được kết quả. Giữ mã lệnh để đối soát trước khi thử lại.');
    } finally{setBusy(false);}
  }
  return <main style={{padding:24,maxWidth:1050,margin:'auto',fontFamily:'sans-serif'}}>
    <h1>Kiểm thử nghiệp vụ local bằng SSO</h1>
    <p>Chỉ dùng dữ liệu thử nghiệm đã thống nhất. Đây là thao tác của người kiểm thử;
      không phải công cụ tự ghi dữ liệu của Agent. API chủ sở hữu quyết định quyền thực hiện.</p>
    <p>Tài khoản: <strong>{session.profile?.user.display_name}</strong></p>
    <div style={{display:'flex',gap:16,marginBottom:20}}>
      <button onClick={onExit} disabled={busy}>Về Workspace</button>
      <button onClick={()=>void session.logout()} disabled={busy}>Đổi tài khoản SSO</button>
    </div>
    <label>Thao tác nghiệp vụ<select aria-label="Thao tác nghiệp vụ" value={action} disabled={busy}
      style={{display:'block',width:'100%',padding:10,margin:'8px 0'}}
      onChange={e=>{setAction(e.target.value);setKey(crypto.randomUUID());setResult(null);setError('');setAttested(false);}}>
      {actions.map(([id,label])=><option key={id} value={id}>{label}</option>)}
    </select></label>
    <label>Mã lệnh để đối soát<input aria-label="Mã lệnh để đối soát" value={key} disabled={busy}
      style={{display:'block',width:'100%',padding:10,margin:'8px 0'}} onChange={e=>setKey(e.target.value)}/></label>
    <p>Giữ nguyên mã và nội dung khi thử lại một lệnh mất phản hồi. Đọc kết quả lệnh ở owner
      trước khi tạo lệnh mới nếu chưa rõ giao dịch đã chạy hay chưa.</p>
    <label>Nội dung lệnh JSON<textarea aria-label="Nội dung lệnh JSON" value={payload} disabled={busy}
      style={{display:'block',width:'100%',minHeight:250,padding:12,fontFamily:'monospace'}}
      onChange={e=>setPayload(e.target.value)}/></label>
    {physical&&<label style={{display:'block',margin:'16px 0'}}><input type="checkbox"
      checked={attested} onChange={e=>setAttested(e.target.checked)} disabled={busy}/>
      Xác nhận đây là tình huống bàn giao giả lập để kiểm thử; đã kiểm tra người nhận,
      số biên nhận và thời điểm hợp lệ trong nội dung lệnh.</label>}
    <button onClick={()=>void execute()} disabled={busy||(physical&&!attested)} style={{marginTop:16,padding:12}}>
      {busy?'Đang kiểm tra và gửi lệnh…':'Thực hiện bước kiểm thử'}
    </button>
    {error&&<p role="alert">{error}</p>}
    {result!==null&&<><h2>Kết quả API chủ sở hữu</h2><pre aria-label="Kết quả nghiệp vụ"
      style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',background:'#f1f5f9',color:'#111',padding:16}}>{JSON.stringify(result,null,2)}</pre></>}
  </main>;
}
