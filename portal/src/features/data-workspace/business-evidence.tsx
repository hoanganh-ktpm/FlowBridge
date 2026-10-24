// SPDX-License-Identifier: GPL-3.0-or-later
import { Sheet,SheetContent,SheetHeader,SheetTitle,SheetDescription } from '@/components/ui/sheet';
import { Database,Clock,Calculator } from 'lucide-react';
import { dateLabel,fmt,unitLabel } from './components';
import type { Evidence } from './types';
export interface Explanation {title:string;meaning:string;applications?:{name:string;description:string}[];items?:{label:string;value:number|null;unit?:string}[];formula?:string;notes?:string[];read_at?:string;last_observation?:string;start?:string;end?:string;technical?:Evidence}
export function sourceNames(e:Evidence):{name:string;description:string}[]{
 const list=(e.data_products||[]) as {name?:string}[];
 const names=new Set(list.map(p=>p.name?.split('.')[0]).filter(Boolean));
 return [...names].map(n=>({name:({sales:'Bán hàng',warehouse:'Kho hàng',logistics:'Vận chuyển'} as Record<string,string>)[n!]||'Nguồn được cấp quyền',description:n==='sales'?'Đơn đặt hàng đã xác nhận':n==='warehouse'?'Tồn kho, hàng đã giữ và hàng sẽ về':'Lịch bàn giao đã tiếp nhận'}));
}
export function BusinessEvidence({value,admin,onClose}:{value:Explanation|null;admin:boolean;onClose:()=>void}) {
 return <Sheet open={!!value} onOpenChange={open=>{if(!open)onClose();}}><SheetContent className="pw-evidence" style={{width:'min(560px,95vw)',maxWidth:560}}><SheetHeader><SheetTitle>Số liệu này được tính như thế nào?</SheetTitle><SheetDescription>{value?.title}</SheetDescription></SheetHeader>{value&&<div className="pw-evidence-body"><p className="pw-evidence-lead">{value.meaning}</p>
 {!!value.applications?.length&&<section><h3><Database size={16}/>Lấy số liệu từ đâu?</h3>{value.applications.map((a,i)=><div className="pw-source" key={i}><strong>{a.name}</strong><p>{a.description}</p></div>)}</section>}
 {!!value.items?.length&&<section><h3><Calculator size={16}/>Các số được sử dụng</h3><dl>{value.items.map((v,i)=><div key={i}><dt>{v.label}</dt><dd>{fmt(v.value)}{v.unit&&<small> {unitLabel(v.unit)}</small>}</dd></div>)}</dl></section>}
 {value.formula&&<section><h3>Tính như thế nào?</h3><p className="pw-equation">{value.formula}</p></section>}
 <section><h3><Clock size={16}/>Thời điểm số liệu</h3><dl>{value.start&&<div><dt>Khoảng đang xét</dt><dd>{dateLabel(value.start)} – {dateLabel(value.end)}</dd></div>}{value.read_at&&<div><dt>Đọc dữ liệu lúc</dt><dd>{new Date(value.read_at).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'})}</dd></div>}{value.last_observation&&<div><dt>Đặt hàng ghi nhận đến</dt><dd>{dateLabel(value.last_observation)}</dd></div>}</dl></section>
 {!!value.notes?.length&&<section><h3>Điều cần lưu ý</h3><ul>{value.notes.map((n,i)=><li key={i}>{n}</li>)}</ul></section>}
 {admin&&value.technical&&<details className="pw-technical"><summary>Chi tiết kỹ thuật · chỉ dành cho IT</summary><pre>{JSON.stringify(value.technical,null,2)}</pre></details>}
 </div>}</SheetContent></Sheet>;
}
