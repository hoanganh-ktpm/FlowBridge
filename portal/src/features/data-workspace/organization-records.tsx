// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef, useState } from 'react';
import { Database, Package, Search, ShoppingBag, Warehouse } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cell } from '@/lib/format';
import { ApiError } from '@/lib/api';
import { WorkspaceDialog } from './interactive-workspace';
import { Field, Select, unitLabel } from './components';
import { orderDateTime } from './order-readiness';
import type { DecisionRequest } from './decision-dossiers';
import './organization-records.css';

type Collection = 'warehouses' | 'products' | 'orders' | 'inventory';
type Row = Record<string, string | number | null>;
type RecordPage = { items: Row[]; offset: number; page_size: number; has_more: boolean; read_at: string; source?: {name:string} };
type Scope = { collection: Collection; code?: string; warehouseId?: string; warehouseName?: string; productName?: string };
const definitions = {
  warehouses: { title:'Danh mục kho', description:'Tên, mã và trạng thái của các kho. Từ một kho, mở tồn kho hoặc đơn hàng liên quan.', code:'Mã kho chính xác', icon:Warehouse },
  products: { title:'Danh mục mặt hàng', description:'Tên, mã và đơn vị tính của sản phẩm. Mở từng mặt hàng để xem số lượng tại các kho.', code:'Mã mặt hàng chính xác', icon:Package },
  inventory: { title:'Tồn kho thực tế', description:'Xem trong kho có bao nhiêu, đã giữ cho đơn bao nhiêu và còn có thể dùng bao nhiêu.', code:'Mã mặt hàng chính xác', icon:Database },
  orders: { title:'Đơn hàng và dòng hàng', description:'Tra cả đơn đang xử lý, đã bàn giao và đã hủy. Mở đơn để xem số lượng đặt từng món.', code:'Mã đơn chính xác', icon:ShoppingBag },
};
const statuses: Record<string,string> = { ACTIVE:'Đang hoạt động',INACTIVE:'Ngừng hoạt động',DISCONTINUED:'Ngừng kinh doanh',DRAFT:'Nháp',SUBMITTED:'Đã gửi',CONFIRMED:'Đã xác nhận',WAITING_STOCK:'Chờ hàng',RESERVED:'Đã giữ hàng',FULFILLING:'Đang chuẩn bị',PARTIALLY_HANDED_OVER:'Đã bàn giao một phần',COMPLETED:'Đã bàn giao đủ',CANCEL_PENDING:'Chờ hủy',CANCELLED:'Đã hủy' };
const text = (value: unknown) => value == null ? 'Chưa được công bố' : String(value);
const at = (value: unknown) => value ? orderDateTime(String(value)) : 'Chưa ghi nhận';
const quantityColumn = {field:'quantity',label:'Số lượng',type:'decimal',scale:3};
const quantity = (value: Row[string], uom: Row[string]) => value == null ? 'Chưa có số liệu' : `${cell(value,quantityColumn)} ${uom ? unitLabel(String(uom)) : '(chưa có đơn vị)'}`;
const normalized = (value:string) => value.toLocaleLowerCase('vi').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/đ/g,'d');
const isAbort = (e:unknown) => e instanceof DOMException && e.name==='AbortError';

export function OrganizationRecords({request,onDenied}:{request:DecisionRequest;onDenied:(error:unknown)=>void}) {
  const [browser,setBrowser]=useState<Scope|null>(null),[quickOpen,setQuickOpen]=useState(false),[helpOpen,setHelpOpen]=useState(false);
  const [quickKind,setQuickKind]=useState<Collection>('orders'),[quickCode,setQuickCode]=useState('');
  return <section className="or-workspace" aria-label="Tra cứu bản ghi nguồn">
    <div className="or-intro"><span className="or-eyebrow">SỔ TRA CỨU NGHIỆP VỤ</span><h2>Tìm đúng bản ghi, xem rõ số lượng</h2>
      <p>Đọc dữ liệu đã ghi nhận của tổ chức đang đăng nhập. Tra đơn cũ, kiểm tra sản phẩm và đối chiếu tồn thực tế tại từng kho.</p>
    </div>
    <Button variant="outline" onClick={()=>setQuickOpen(true)}><Search size={16}/>Tra cứu theo mã</Button>
    <WorkspaceDialog open={quickOpen} onClose={()=>setQuickOpen(false)} title="Tra cứu theo mã" description="Nhập mã chính xác để tìm trực tiếp, hoặc để trống để mở danh sách.">    <form className="or-quick-search" onSubmit={e=>{e.preventDefault();setQuickOpen(false);setBrowser({collection:quickKind,code:quickCode.trim()});}}>
      <Field title="Loại dữ liệu"><Select value={quickKind} onChange={v=>setQuickKind(v as Collection)}>{(Object.keys(definitions) as Collection[]).map(key=><option key={key} value={key}>{definitions[key].title}</option>)}</Select></Field>
      <Field title={definitions[quickKind].code}><Input value={quickCode} maxLength={80} placeholder="Nhập mã hoặc để trống để xem danh sách" onChange={e=>setQuickCode(e.target.value)}/></Field>
      <Button type="submit"><Search size={16}/>Tra cứu</Button>
    </form>
</WorkspaceDialog>
    <div className="or-collections" role="group" aria-label="Chọn sổ tra cứu">{(Object.keys(definitions) as Collection[]).map(key=>{
      const item=definitions[key],Icon=item.icon;
      return <button type="button" key={key} className="or-collection" aria-label={item.title} onClick={()=>setBrowser({collection:key})}><Icon size={22}/><strong>{item.title}</strong><span>{item.description}</span><b>Mở danh sách →</b></button>;
    })}</div>
    <Button variant="ghost" onClick={()=>setHelpOpen(true)}>Hướng dẫn đọc số lượng</Button><WorkspaceDialog open={helpOpen} onClose={()=>setHelpOpen(false)} title="Cách đọc số lượng" description="Phân biệt lượng khách đặt, tồn thực tế và đơn vị tính.">    <div className="or-guide"><h3>Cần xem số lượng nào?</h3><p><strong>Khách đặt bao nhiêu:</strong> mở Đơn hàng và dòng hàng, chọn đơn để xem từng món.</p><p><strong>Kho còn bao nhiêu:</strong> mở Tồn kho thực tế. Mỗi dòng là một mặt hàng tại một kho, có số lượng đi kèm đơn vị.</p><p><strong>Tên, mã, đơn vị tính:</strong> nằm trong Danh mục mặt hàng, kể cả mặt hàng chưa có bản ghi tồn kho.</p></div>
</WorkspaceDialog>
    {browser&&<RecordsBrowser scope={browser} request={request} onDenied={onDenied} onClose={()=>setBrowser(null)}/>}
  </section>;
}

function RecordsBrowser({scope,request,onDenied,onClose}:{scope:Scope;request:DecisionRequest;onDenied:(e:unknown)=>void;onClose:()=>void}) {
  const kind=scope.collection,definition=definitions[kind];
  const [code,setCode]=useState(scope.code||''),[status,setStatus]=useState('');
  const [applied,setApplied]=useState({code:scope.code||'',status:''});
  const [page,setPage]=useState<RecordPage|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [pageSearch,setPageSearch]=useState('');
  const [selected,setSelected]=useState<Row|null>(null),[lines,setLines]=useState<RecordPage|null>(null),[lineBusy,setLineBusy]=useState(false),[lineError,setLineError]=useState('');
  const [related,setRelated]=useState<Scope|null>(null);
  const listSeq=useRef(0),detailSeq=useRef(0),listAbort=useRef<AbortController|null>(null),detailAbort=useRef<AbortController|null>(null);
  useEffect(()=>()=>{listSeq.current++;detailSeq.current++;listAbort.current?.abort();detailAbort.current?.abort();},[]);
  function failure(e:unknown,detail=false) {
    if(isAbort(e))return;
    const message=e instanceof ApiError&&[401,403].includes(e.status)?'Phiên đăng nhập hoặc quyền đọc nguồn tra cứu đã thay đổi. Kiểm tra quyền với quản trị viên rồi tải lại.':e instanceof ApiError?e.message:'Chưa đọc được dữ liệu. Thử lại khi kết nối ổn định.';
    if(detail)setLineError(message);else {setError(message);setPage(null);}
    if(e instanceof ApiError&&[401,403].includes(e.status)){setPage(null);setLines(null);setSelected(null);onDenied(e);}
  }
  function closeDetail(){detailSeq.current++;detailAbort.current?.abort();setSelected(null);setLines(null);setLineBusy(false);setLineError('');}
  async function load(offset=0,filters=applied,fresh=false){
    const ticket=++listSeq.current;listAbort.current?.abort();const controller=new AbortController();listAbort.current=controller;
    setBusy(true);setError('');setPage(null);setPageSearch('');closeDetail();
    try {
      const result=await request<RecordPage>('/records',{collection:kind,offset,...(filters.code?{code:filters.code}:{}),...(filters.status?{status:filters.status}:{}),...(scope.warehouseId?{warehouse_id:scope.warehouseId}:{})},controller.signal,undefined,fresh);
      if(ticket===listSeq.current){setPage(result);setApplied(filters);}
    }catch(e){if(ticket===listSeq.current)failure(e);}finally{if(ticket===listSeq.current)setBusy(false);}
  }
  async function detail(row:Row,offset=0){
    const ticket=++detailSeq.current;detailAbort.current?.abort();const controller=new AbortController();detailAbort.current=controller;
    setSelected(row);setLines(null);setLineBusy(true);setLineError('');
    try {const result=await request<RecordPage>('/records',{collection:'order_lines',order_id:row.order_id,offset},controller.signal);if(ticket===detailSeq.current)setLines(result);}
    catch(e){if(ticket===detailSeq.current)failure(e,true);}finally{if(ticket===detailSeq.current)setLineBusy(false);}
  }
  useEffect(()=>{void load(0,{code:scope.code||'',status:''});},[]);
  const dirty=code.trim()!==applied.code||status!==applied.status;
  const rows=page?.items.filter(row=>normalized(Object.values(row).map(v=>v??'').join(' ')).includes(normalized(pageSearch.trim())))||[];
  const statusChoices=kind==='orders'?['DRAFT','SUBMITTED','CONFIRMED','WAITING_STOCK','RESERVED','FULFILLING','PARTIALLY_HANDED_OVER','COMPLETED','CANCEL_PENDING','CANCELLED']:['ACTIVE','INACTIVE',...(kind==='products'?['DISCONTINUED']:[])];
  const columns=kind==='orders'?['Mã đơn','Kho xử lý','Trạng thái','Hạn bàn giao','Số dòng hàng','Chi tiết']:kind==='warehouses'?['Mã kho','Tên kho','Trạng thái','Dữ liệu liên quan']:kind==='products'?['Mã mặt hàng','Tên mặt hàng','Đơn vị tính','Trạng thái','Số lượng theo kho']:['Kho','Mặt hàng','Trong kho','Đã giữ cho đơn','Có thể dùng'];
  return <>
    <WorkspaceDialog open onClose={onClose} title={definition.title} description={`${scope.warehouseName?'Kho: '+scope.warehouseName+'. ':''}${scope.productName?'Mặt hàng: '+scope.productName+'. ':''}${kind==='inventory'?'Số lượng đã ghi nhận theo từng kho; không cộng gộp các đơn vị khác nhau.':kind==='products'?'Đây là danh mục định danh sản phẩm. Chọn Xem tồn theo kho để đọc số lượng.':kind==='orders'?'Bao gồm các trạng thái đơn và mọi hạn bàn giao, kể cả đơn cũ đã hoàn tất hoặc hủy.':'Bao gồm cả kho chưa phát sinh tồn kho hoặc đơn hàng.'}`} wide>
      <form className="or-list-filters" onSubmit={e=>{e.preventDefault();void load(0,{code:code.trim(),status},true);}}>
        <Field title={definition.code}><Input value={code} maxLength={80} disabled={busy} placeholder="Để trống để xem tất cả" onChange={e=>setCode(e.target.value)}/></Field>
        {kind!=='inventory'&&<Field title="Trạng thái"><Select value={status} disabled={busy} onChange={setStatus}><option value="">Tất cả trạng thái</option>{statusChoices.map(k=><option key={k} value={k}>{statuses[k]}</option>)}</Select></Field>}
        <Button type="submit" disabled={busy}>Lọc và tải dữ liệu</Button><Button type="button" variant="outline" disabled={busy} onClick={()=>{setCode('');setStatus('');void load(0,{code:'',status:''});}}>Bỏ bộ lọc</Button>
      </form>
      {scope.warehouseId&&<p className="or-scope">Đang giới hạn trong kho {scope.warehouseName||scope.warehouseId}. Đóng hộp thoại để trở về danh sách trước đó.</p>}
      {dirty&&<p role="status">Bộ lọc đã thay đổi. Bấm Lọc và tải dữ liệu để áp dụng.</p>}
      {busy&&<p role="status">Đang đọc bản ghi từ hệ thống nguồn…</p>}{error&&<p role="alert">{error}</p>}
      {page&&<><div className="or-page-info"><span>{page.items.length} bản ghi trong trang {Math.floor(page.offset/page.page_size)+1} · đọc lúc {at(page.read_at)}</span><Input aria-label="Tìm trong trang đang xem" placeholder="Tìm tên hoặc mã trong trang này…" value={pageSearch} onChange={e=>setPageSearch(e.target.value)}/></div>
        <div className={`dw-table-scroll iw-list-table or-table${kind==='inventory'?' or-inventory-table':''}`} tabIndex={0} role="region" aria-label="Bảng bản ghi nguồn"><table aria-label="Danh sách dữ liệu tổ chức"><thead><tr>{columns.map(label=><th key={label}>{label}</th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={kind==='inventory'?`${row.warehouse_id}:${row.sku}:${row.uom}`:text(row.order_id||row.warehouse_id||row.product_id||i)}>
          {kind==='orders'?<><td><strong>{text(row.order_number)}</strong></td><td>{text(row.warehouse_name||row.warehouse_code)}{!row.warehouse_name&&!row.warehouse_code&&row.warehouse_id&&<small>{text(row.warehouse_id)}</small>}</td><td>{statuses[text(row.status)]||text(row.status)}</td><td>{at(row.requested_handoff_at)}</td><td>{row.line_count==null?'Chưa có số liệu':cell(row.line_count)}</td><td><Button variant="outline" disabled={busy||dirty} onClick={()=>void detail(row)}>Xem hàng của {row.order_number}</Button></td></>
            :kind==='warehouses'?<><td><strong>{text(row.warehouse_code)}</strong></td><td>{text(row.warehouse_name)}</td><td>{statuses[text(row.status)]||text(row.status)}</td><td><div className="or-row-actions"><Button variant="outline" size="sm" disabled={dirty} onClick={()=>setRelated({collection:'inventory',warehouseId:String(row.warehouse_id),warehouseName:text(row.warehouse_name)})}>Xem hàng trong kho</Button><Button variant="outline" size="sm" disabled={dirty} onClick={()=>setRelated({collection:'orders',warehouseId:String(row.warehouse_id),warehouseName:text(row.warehouse_name)})}>Xem đơn của kho</Button></div></td></>
            :kind==='products'?<><td><strong>{text(row.sku)}</strong></td><td>{text(row.product_name)}</td><td>{unitLabel(String(row.uom||''))||'Chưa có đơn vị'}</td><td>{statuses[text(row.status)]||text(row.status)}</td><td><Button variant="outline" size="sm" disabled={dirty} onClick={()=>setRelated({collection:'inventory',code:String(row.sku),productName:text(row.product_name)})}>Xem tồn theo kho</Button></td></>
            :<><td><strong>{text(row.warehouse_name||row.warehouse_code)}</strong><small>{text(row.warehouse_code||row.warehouse_id)}</small></td><td><strong>{text(row.product_name||row.sku)}</strong><small>{text(row.sku)}</small></td><td>{quantity(row.on_hand_quantity,row.uom)}</td><td>{quantity(row.reserved_quantity,row.uom)}</td><td><strong>{quantity(row.available_quantity,row.uom)}</strong></td></>}
        </tr>)}</tbody></table></div>
        {kind==='inventory'&&<div className="or-stock-cards" aria-label="Số lượng tồn theo kho">{rows.map(row=><article key={`${row.warehouse_id}:${row.sku}:${row.uom}`}><strong>{text(row.product_name||row.sku)}</strong><span>{text(row.sku)} · {text(row.warehouse_name||row.warehouse_code)}</span><dl><div><dt>Trong kho</dt><dd>{quantity(row.on_hand_quantity,row.uom)}</dd></div><div><dt>Đã giữ cho đơn</dt><dd>{quantity(row.reserved_quantity,row.uom)}</dd></div><div><dt>Có thể dùng</dt><dd>{quantity(row.available_quantity,row.uom)}</dd></div></dl></article>)}</div>}
        {!rows.length&&<p className="or-empty" role="status">{pageSearch?'Không có kết quả trong trang này. Xóa tìm kiếm hoặc chuyển sang trang khác.':kind==='inventory'?'Chưa có bản ghi tồn kho khớp bộ lọc. Điều này chưa xác nhận số lượng bằng 0.':'Không có bản ghi khớp bộ lọc trong phạm vi được cấp quyền.'}</p>}
        <div className="iw-list-pagination"><span>Trang {Math.floor(page.offset/page.page_size)+1} · {page.items.length} bản ghi{page.has_more?' · còn trang sau':''}</span><div><Button variant="outline" disabled={busy||dirty||page.offset===0} onClick={()=>void load(Math.max(0,page.offset-page.page_size))}>Trang trước</Button><Button variant="outline" disabled={busy||dirty||!page.has_more} onClick={()=>void load(page.offset+page.page_size)}>Trang sau</Button></div></div>
        <p className="or-note">Tìm nhanh chỉ lọc trang đang xem; dùng mã chính xác ở trên để tra toàn bộ nguồn. {kind==='inventory'?'Có thể dùng = trong kho − đã giữ cho đơn. Các số này là số liệu hiện có; chưa đánh giá đủ hàng cho những đơn tương lai.':kind==='products'?'Đơn vị tính cho biết cách đếm sản phẩm; số lượng nằm trong Tồn kho thực tế.':''}</p>
        {page.source&&<small className="or-source">Nguồn: {page.source.name}</small>}
      </>}
    </WorkspaceDialog>
    <WorkspaceDialog open={!!selected} onClose={closeDetail} title={`Các mặt hàng của ${selected?.order_number||'đơn hàng'}`} description="Số lượng khách đặt tại Bán hàng theo từng dòng. Không cộng chung các đơn vị khác nhau." wide>
      {selected&&<div className="or-order-facts"><span>Trạng thái<strong>{statuses[text(selected.status)]||text(selected.status)}</strong></span><span>Ngày tạo<strong>{at(selected.created_at)}</strong></span><span>Hạn bàn giao<strong>{at(selected.requested_handoff_at)}</strong></span><span>Đã bàn giao đủ lúc<strong>{at(selected.completed_at)}</strong></span></div>}
      {lineBusy&&<p role="status">Đang tải các mặt hàng…</p>}{lineError&&<p role="alert">{lineError}</p>}
      {lines&&<><div className="dw-table-scroll iw-list-table or-order-lines"><table aria-label="Các dòng hàng của đơn"><thead><tr><th>Dòng</th><th>Mặt hàng</th><th>Số lượng khách đặt</th></tr></thead><tbody>{lines.items.map(row=><tr key={text(row.order_line_id)}><td>{text(row.line_no)}</td><td>{text(row.product_name)}<small>{text(row.sku)}</small></td><td><strong>{quantity(row.ordered_quantity,row.uom)}</strong></td></tr>)}</tbody></table></div>
        {!lines.items.length&&<p role="status">Đơn này chưa có dòng hàng được chia sẻ.</p>}
        <div className="iw-list-pagination"><span>Đọc lúc {at(lines.read_at)} · trang dòng hàng {Math.floor(lines.offset/lines.page_size)+1} · {lines.items.length} dòng</span><div><Button variant="outline" disabled={lineBusy||lines.offset===0} onClick={()=>selected&&void detail(selected,Math.max(0,lines.offset-lines.page_size))}>Dòng hàng trước</Button><Button variant="outline" disabled={lineBusy||!lines.has_more} onClick={()=>selected&&void detail(selected,lines.offset+lines.page_size)}>Dòng hàng tiếp</Button></div></div>
      </>}
    </WorkspaceDialog>
    {related&&<RecordsBrowser scope={related} request={request} onDenied={onDenied} onClose={()=>setRelated(null)}/>}
  </>;
}
