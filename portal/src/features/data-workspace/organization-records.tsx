// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ApiError } from '@/lib/api';
import { Field, Panel, Select, fmt } from './components';
import { orderDateTime } from './order-readiness';
import type { DecisionRequest } from './decision-dossiers';

type Collection = 'warehouses' | 'products' | 'orders';
type Row = Record<string, string | number | null>;
type Page = { items: Row[]; offset: number; page_size: number; has_more: boolean; read_at: string };
const statuses: Record<string, string> = { ACTIVE:'Đang hoạt động',INACTIVE:'Ngừng hoạt động',DISCONTINUED:'Ngừng kinh doanh',DRAFT:'Nháp',SUBMITTED:'Đã gửi',CONFIRMED:'Đã xác nhận',WAITING_STOCK:'Chờ hàng',RESERVED:'Đã giữ hàng',FULFILLING:'Đang chuẩn bị',PARTIALLY_HANDED_OVER:'Đã bàn giao một phần',COMPLETED:'Đã bàn giao đủ',CANCEL_PENDING:'Chờ hủy',CANCELLED:'Đã hủy' };
const text = (value: unknown) => value == null ? '—' : String(value);
const at = (value: unknown) => value ? orderDateTime(String(value)) : '—';

export function OrganizationRecords({ request, onDenied }: { request: DecisionRequest; onDenied: (error: unknown) => void }) {
  const [collection,setCollection]=useState<Collection>('orders');
  const [code,setCode]=useState(''),[status,setStatus]=useState('');
  const [applied,setApplied]=useState({code:'',status:''});
  const [page,setPage]=useState<Page|null>(null),[lines,setLines]=useState<Page|null>(null);
  const [selected,setSelected]=useState<Row|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const generation=useRef(0);
  useEffect(()=>()=>{generation.current++;},[]);
  function failure(e:unknown){
    setPage(null);setLines(null);setSelected(null);
    if(e instanceof ApiError&&[401,403].includes(e.status))onDenied(e);
    setError(e instanceof ApiError?e.message:'Không đọc được dữ liệu hiện tại. Thử tải lại; không dùng danh sách cũ để kết luận.');
  }
  async function load(offset=0, filters=applied, kind=collection){
    const ticket=++generation.current;
    setBusy(true);setError('');setPage(null);setSelected(null);setLines(null);
    try{
      const result=await request<Page>('/records',{collection:kind,offset,...(filters.code?{code:filters.code}:{}),...(filters.status?{status:filters.status}:{})});
      if(ticket===generation.current){setPage(result);setApplied(filters);}
    }catch(e){if(ticket===generation.current)failure(e);}
    finally{if(ticket===generation.current)setBusy(false);}
  }
  async function detail(row:Row,offset=0){
    const ticket=++generation.current;setBusy(true);setError('');setLines(null);setSelected(row);
    try{
      const result=await request<Page>('/records',{collection:'order_lines',order_id:row.order_id,offset});
      if(ticket===generation.current)setLines(result);
    }catch(e){if(ticket===generation.current)failure(e);}
    finally{if(ticket===generation.current)setBusy(false);}
  }
  useEffect(()=>{void load(0,{code:'',status:''},collection);},[collection]);
  const dirty=code.trim()!==applied.code||status!==applied.status;
  return <section aria-label="Dữ liệu tổ chức">
    <p>Dữ liệu đang lưu tại hệ thống nguồn của tổ chức đang đăng nhập. Danh sách này không giới hạn theo kỳ dự báo hoặc chỉ các đơn đang mở.</p>
    <div className="dw-actions">{([['orders','Tất cả đơn hàng'],['warehouses','Tất cả kho'],['products','Tất cả mặt hàng']] as const).map(([key,label])=><Button key={key} variant={collection===key?'default':'outline'} disabled={busy} onClick={()=>{setCode('');setStatus('');setCollection(key);}}>{label}</Button>)}</div>
    <form className="dw-filterbar" onSubmit={e=>{e.preventDefault();void load(0,{code:code.trim(),status});}}>
      <Field title={collection==='orders'?'Mã đơn chính xác':collection==='warehouses'?'Mã kho chính xác':'Mã mặt hàng chính xác'}><Input value={code} disabled={busy} maxLength={80} onChange={e=>setCode(e.target.value)} placeholder="Để trống để xem tất cả"/></Field>
      <Field title="Trạng thái dữ liệu"><Select value={status} disabled={busy} onChange={setStatus}><option value="">Tất cả trạng thái</option>{(collection==='orders'?['DRAFT','SUBMITTED','CONFIRMED','WAITING_STOCK','RESERVED','FULFILLING','PARTIALLY_HANDED_OVER','COMPLETED','CANCEL_PENDING','CANCELLED']:['ACTIVE','INACTIVE',...(collection==='products'?['DISCONTINUED']:[])]).map(key=><option key={key} value={key}>{statuses[key]}</option>)}</Select></Field>
      <Button type="submit" disabled={busy}>Lọc và tải dữ liệu</Button>
      <Button type="button" variant="outline" disabled={busy} onClick={()=>{setCode('');setStatus('');void load(0,{code:'',status:''});}}>Bỏ bộ lọc</Button>
    </form>
    {dirty&&<p>Thay đổi bộ lọc chưa áp dụng. Bấm Lọc và tải dữ liệu.</p>}
    {busy&&<p role="status">Đang đọc dữ liệu từ nguồn…</p>}{error&&<p role="alert">{error}</p>}
    {page&&<Panel title={collection==='orders'?'Danh sách đơn hàng':collection==='warehouses'?'Danh sách kho':'Danh mục mặt hàng'} subtitle={`Đọc lúc ${at(page.read_at)} · mỗi trang tối đa ${page.page_size} bản ghi. Dữ liệu có thể thay đổi giữa các lần đọc.`}>
      <div className="dw-table-scroll"><table aria-label="Danh sách dữ liệu tổ chức"><thead><tr>{(collection==='orders'?['Mã đơn','Trạng thái','Ngày tạo','Hạn bàn giao','Đã bàn giao đủ lúc','Số dòng hàng','Chi tiết']:collection==='warehouses'?['Mã kho','Tên kho','Trạng thái']:['Mã mặt hàng','Tên mặt hàng','Đơn vị','Trạng thái']).map(label=><th key={label}>{label}</th>)}</tr></thead><tbody>
        {page.items.map(row=><tr key={text(row.order_id||row.warehouse_id||row.product_id)}>{collection==='orders'?<><td>{text(row.order_number)}</td><td>{statuses[text(row.status)]||text(row.status)}</td><td>{at(row.created_at)}</td><td>{at(row.requested_handoff_at)}</td><td>{at(row.completed_at)}</td><td>{fmt(Number(row.line_count))}</td><td><Button variant="outline" disabled={busy||dirty} onClick={()=>void detail(row)}>Xem hàng của {row.order_number}</Button></td></>:collection==='warehouses'?<><td>{text(row.warehouse_code)}</td><td>{text(row.warehouse_name)}</td><td>{statuses[text(row.status)]||text(row.status)}</td></>:<><td>{text(row.sku)}</td><td>{text(row.product_name)}</td><td>{text(row.uom)}</td><td>{statuses[text(row.status)]||text(row.status)}</td></>}</tr>)}
      </tbody></table></div>
      {!page.items.length&&<p>Không có bản ghi khớp bộ lọc trong phạm vi được cấp quyền.</p>}
      <div className="dw-actions"><Button variant="outline" disabled={busy||dirty||page.offset===0} onClick={()=>void load(Math.max(0,page.offset-page.page_size))}>Trang trước</Button><span>Trang {Math.floor(page.offset/page.page_size)+1} · {page.items.length} bản ghi{page.has_more?' · còn trang sau':''}</span><Button variant="outline" disabled={busy||dirty||!page.has_more} onClick={()=>void load(page.offset+page.page_size)}>Trang sau</Button></div>
    </Panel>}
    {selected&&lines&&<Panel title={`Các mặt hàng của ${selected.order_number}`} subtitle="Tên, số lượng và đơn vị theo từng dòng hàng đã lưu tại Sales; không cộng chung các đơn vị khác nhau.">
      <div className="dw-table-scroll"><table aria-label="Các dòng hàng của đơn"><thead><tr><th>Dòng</th><th>Mã</th><th>Tên mặt hàng</th><th>Số lượng</th><th>Đơn vị</th></tr></thead><tbody>{lines.items.map(row=><tr key={text(row.order_line_id)}><td>{text(row.line_no)}</td><td>{text(row.sku)}</td><td>{text(row.product_name)}</td><td>{text(row.ordered_quantity)}</td><td>{text(row.uom)}</td></tr>)}</tbody></table></div>
      <div className="dw-actions"><Button variant="outline" disabled={busy||lines.offset===0} onClick={()=>void detail(selected,Math.max(0,lines.offset-50))}>Dòng hàng trước</Button><Button variant="outline" disabled={busy||!lines.has_more} onClick={()=>void detail(selected,lines.offset+50)}>Dòng hàng tiếp</Button></div>
    </Panel>}
  </section>;
}
