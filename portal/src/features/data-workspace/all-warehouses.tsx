// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api';
import { Field, Panel, Select, fmt, unitLabel } from './components';
import { orderDateTime } from './order-readiness';
import type { DecisionRequest } from './decision-dossiers';

interface Warehouse {
  organization_id:string;warehouse_id:string;warehouse_name:string;warehouse_code:string;
  products:{sku:string;name:string|null;uom:string;on_hand:number|string;reserved:number|string;available:number|string;shortage:number|null}[];
  open_orders:{order_id:string;order_number:string;deadline:string;status:string;lines:{sku:string;uom:string;ordered_quantity:number|string}[]}[];
  planning_status:'calculated'|'needs_data';planning_issue:{code:string;message:string}|null;plan_read_at:string|null;
}
interface Overview {items:Warehouse[];read_at:string;warehouse_count:number;horizon:number}

export function AllWarehouses({request,onDenied,onLocal}: {request:DecisionRequest;onDenied:(e:unknown)=>void;onLocal:()=>void}) {
  const [horizon,setHorizon]=useState('7'),[result,setResult]=useState<Overview|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const generation=useRef(0);
  async function load(){
    const ticket=++generation.current;setBusy(true);setError('');setResult(null);
    try{
      const value=await request<Overview>('/all-warehouses',{horizon:Number(horizon)});
      if(ticket===generation.current)setResult(value);
    }catch(e){if(ticket===generation.current){setResult(null);if(e instanceof ApiError&&[401,403].includes(e.status))onDenied(e);setError(e instanceof ApiError?e.message:'Chưa đọc đủ dữ liệu các kho. Thử cập nhật lại; không dùng kết quả cũ.');}}
    finally{if(ticket===generation.current)setBusy(false);}
  }
  useEffect(()=>{void load();return()=>{generation.current++;};},[]);
  return <section aria-label="Tổng quan tất cả kho">
    <p>Tất cả kho trong các phạm vi quản trị đã cấu hình và được cấp quyền đọc. Mỗi kho tính riêng, giữ đúng mã hàng và đơn vị; không trộn dữ liệu giữa các tổ chức.</p>
    <div className="dw-filterbar"><Field title="Kỳ tổng quan tất cả kho"><Select value={horizon} disabled={busy} onChange={setHorizon}>{[7,14,30,90].map(day=><option key={day} value={day}>{day} ngày tới</option>)}</Select></Field><Button disabled={busy} onClick={()=>void load()}>Cập nhật toàn bộ kho</Button><Button variant="outline" disabled={busy} onClick={onLocal}>Phân tích chi tiết kho đang chọn</Button></div>
    {busy&&<p role="status">Đang đọc dữ liệu và kiểm tra kế hoạch của tất cả kho…</p>}
    {error&&<p role="alert">{error}</p>}
    {result&&<>
      <p>Đã đọc {result.warehouse_count} kho · {result.items.reduce((n,w)=>n+w.products.length,0)} mặt hàng theo kho · {result.items.reduce((n,w)=>n+w.open_orders.length,0)} đơn đang mở. Dữ liệu đọc lúc {orderDateTime(result.read_at)}.</p>
      {Number(horizon)!==result.horizon&&<p>Đã đổi kỳ nhưng chưa áp dụng. Bấm Cập nhật toàn bộ kho.</p>}
      {result.items.map(warehouse=><Panel key={warehouse.warehouse_id} title={`${warehouse.warehouse_name} (${warehouse.warehouse_code})`} subtitle={`${warehouse.products.length} mặt hàng có số liệu tồn · ${warehouse.open_orders.length} đơn đang mở`}>
        {warehouse.planning_issue?<p role="status">Chưa tính được dự báo/kế hoạch: {warehouse.planning_issue.message} Số tồn và đơn hàng bên dưới vẫn là dữ liệu đang lưu tại nguồn.</p>:<p>Đã tính lại kế hoạch {result.horizon} ngày cho kho này.</p>}
        <div className="dw-table-scroll"><table aria-label={`Tồn và kế hoạch ${warehouse.warehouse_code}`}><thead><tr><th>Mặt hàng</th><th>Đang trong kho</th><th>Đã giữ</th><th>Còn phân bổ</th><th>Thiếu theo kế hoạch</th></tr></thead><tbody>{warehouse.products.map(product=><tr key={product.sku}><td>{product.name||product.sku}<small>{product.sku} · {unitLabel(product.uom)}</small></td><td>{fmt(Number(product.on_hand))}</td><td>{fmt(Number(product.reserved))}</td><td>{fmt(Number(product.available))}</td><td>{product.shortage==null?'Chưa đủ dữ liệu để tính':fmt(product.shortage)}</td></tr>)}</tbody></table></div>
        {!warehouse.products.length&&<p>Chưa có số liệu tồn kho được chia sẻ; không coi là kho không có hàng.</p>}
        <details><summary>{warehouse.open_orders.length} đơn đang mở của {warehouse.warehouse_name}</summary><div className="dw-table-scroll"><table><thead><tr><th>Đơn</th><th>Hạn bàn giao</th><th>Mặt hàng</th></tr></thead><tbody>{warehouse.open_orders.map(order=><tr key={order.order_id}><td>{order.order_number}</td><td>{orderDateTime(order.deadline)}</td><td>{order.lines.map((line,i)=><div key={i}>{line.sku}: {line.ordered_quantity} {unitLabel(line.uom)}</div>)}</td></tr>)}</tbody></table></div></details>
      </Panel>)}
    </>}
  </section>;
}
