// SPDX-License-Identifier: GPL-3.0-or-later
import { useState, type ReactNode } from 'react';
import { InteractiveFlow, WorkspaceDialog } from './interactive-workspace';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Panel, Plot, Select, dateLabel, fmt, unitLabel } from './components';
import { DemandReceiptsChart, ProductRiskChart } from './decision-charts';
import type { Planning, PlanProduct } from './planning-types';
import { priorityOrders } from './dashboard-summary';

const status = { urgent: 'Cần bổ sung', watch: 'Cần xác nhận hàng về', clear: 'Đủ theo ước tính', unknown: 'Thiếu dữ liệu dự báo' };
export function OperationsDashboard({ plan, selected, filter, onFilter, onInspect, onOrders, disabled, evidence, onTasks, onOutcomes }: {
  plan: Planning; selected: PlanProduct | undefined; filter: string; onFilter: (v: string) => void;
  onInspect: (p: PlanProduct) => void; onOrders: (orderNumber?: string) => void; disabled: boolean; evidence: (key: string) => ReactNode;
  onTasks?: () => void; onOutcomes?: () => void;
}) {
  const [tab,setTab]=useState<string|null>(null),[search,setSearch]=useState('');
  const rows = plan.products.filter(p => `${p.sku} ${p.name||''}`.toLocaleLowerCase('vi').includes(search.toLocaleLowerCase('vi'))).filter(p => filter === 'all' || filter === 'urgent' && p.priority === 'urgent' || filter === 'watch' && p.late_plan_ids.length > 0);
  const productLabel = `${plan.selected_sku}${selected?.name ? ' - ' + selected.name : ''}`;
  const attention = priorityOrders(plan.orders);
  function focus(value: string) { onFilter(value);setTab('priority'); }
  return <div className="pw-overview">
    <h2>Ưu tiên xử lý</h2><p className="iw-page-intro">Dữ liệu đã cập nhật. Chọn một chức năng để mở danh sách hoặc biểu đồ.</p>
    <InteractiveFlow connected={false} label="Chức năng tổng quan kho" onSelect={setTab} nodes={[
      {id:'priority',title:'Mặt hàng cần chú ý',description:'Tìm mặt hàng và thử bổ sung'},
      {id:'charts',title:'Biểu đồ đối chiếu',description:'Lượng đặt, thực nhập và khả năng đủ hàng'},
    ]}/>
    <div className="pw-summary-grid">
      <Card><Button variant="ghost" onClick={() => focus('urgent')}><span>Mặt hàng cần bổ sung</span><strong>{plan.kpis.risk_products}<small>/{plan.products.length}</small></strong></Button></Card>
      <Card><Button variant="ghost" onClick={()=>onOrders()}><span>Đơn cần phối hợp</span><strong>{plan.kpis.risk_orders}</strong></Button></Card>
      <Card><Button variant="ghost" onClick={() => focus('watch')}><span>Đợt hàng về cần kiểm tra</span><strong>{plan.kpis.plans_attention}</strong></Button></Card>
    </div>
    <WorkspaceDialog open={tab==='priority'} onClose={()=>setTab(null)} title="Mặt hàng và đơn cần chú ý" description="Lọc mặt hàng cần xử lý hoặc mở đơn liên quan." wide>    <Panel title="Ưu tiên xử lý" subtitle={`Dữ liệu đọc lúc ${new Date(plan.as_of).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })} · giờ Việt Nam`}>
      {attention.length ? <ol>{attention.map(order => <li className="flex flex-wrap items-center" key={order.order_id}><Button className="h-auto max-w-full whitespace-normal text-left" variant="link" onClick={()=>onOrders(order.order_number)} disabled={disabled}>{order.order_number} · hạn {new Date(order.deadline).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })}</Button><span>{order.at_risk ? 'Có nguy cơ thiếu hàng trước giờ chuẩn bị' : order.schedule_status === 'late' ? 'Lịch lấy hàng muộn hơn hạn bàn giao' : 'Cần xác minh lịch nhận'}</span></li>)}</ol> : <p>Chưa có đơn cần phối hợp trong dữ liệu đã đọc. Dự báo nhu cầu mới vẫn được đánh giá riêng.</p>}
      {plan.missing_sources.length > 0 && <p role="status">Một số nguồn chưa được chia sẻ; cần xem giới hạn trước khi kết luận kế hoạch đã đáp ứng đầy đủ.</p>}
    </Panel>
<Input aria-label="Tìm mặt hàng cần chú ý" placeholder="Tìm tên hoặc mã mặt hàng…" value={search} onChange={e=>setSearch(e.target.value)}/><Panel title="Danh sách cần theo dõi" action={<Select label="Lọc mức ưu tiên" value={filter} onChange={onFilter}><option value="all">Tất cả sản phẩm</option><option value="urgent">Cần bổ sung</option><option value="watch">Cần rà lịch hàng về</option></Select>}>
      <div id="priority-list" className="pw-compact-table"><Table><TableHeader><TableRow><TableHead>Sản phẩm</TableHead><TableHead>Hàng chưa giữ cho đơn khác</TableHead><TableHead>Số lượng đơn còn cần hàng</TableHead><TableHead>Lượng thiếu lớn nhất trong kỳ</TableHead><TableHead className="text-right">Xử lý</TableHead></TableRow></TableHeader><TableBody>{rows.map(p => <TableRow key={p.sku}>
        <TableCell><strong>{p.sku} - {p.name || 'Chưa có tên được chia sẻ'}</strong><div><Badge variant={p.priority === 'urgent' ? 'destructive' : 'secondary'}>{status[p.priority]}</Badge></div></TableCell>
        <TableCell>{fmt(p.available)}<small>{unitLabel(p.uom)}</small></TableCell><TableCell>{fmt(p.known_quantity)}</TableCell>
        <TableCell><strong className={p.shortage ? 'pw-danger-text' : ''}>{fmt(p.shortage)}</strong><small>{p.first_risk_date ? 'Từ ' + dateLabel(p.first_risk_date) : 'Trong kỳ đang xét'}</small></TableCell>
        <TableCell className="text-right"><Button variant="ghost" size="sm" disabled={disabled || p.priority === 'unknown'} onClick={() => onInspect(p)}>{p.priority === 'urgent' ? 'Thử bổ sung hàng' : 'Xem chi tiết'}</Button></TableCell>
      </TableRow>)}</TableBody></Table>{!rows.length && <p className="pw-empty-inline">Không có sản phẩm thuộc mức này.</p>}</div>
    </Panel>
    </WorkspaceDialog><WorkspaceDialog open={tab==='charts'} onClose={()=>setTab(null)} title="Biểu đồ đối chiếu" description="Đối chiếu lượng đặt, thực nhập và khả năng đủ hàng của cùng kho." wide>    <div className="pw-business-charts">
      <Panel title="Lượng đặt và thực nhập" subtitle={`${productLabel} · ${dateLabel(plan.history.start)} – ${dateLabel(plan.history.end)}`} action={evidence('history')}>
        <DemandReceiptsChart history={plan.history} unit={selected?.uom || ''}/>
        <p className="pw-panel-note">So sánh số lượng khách đặt và số lượng đã nhập kho của cùng mặt hàng. Hai số này không thể dùng trực tiếp để tính tồn kho vì chưa tính hàng xuất và hàng đã giữ.</p>
      </Panel>
      <Panel title="Nguy cơ thiếu hàng theo sản phẩm" subtitle={`Đến ${dateLabel(plan.end)} · ${unitLabel(selected?.uom)}`} action={evidence('risk')}>
        <ProductRiskChart products={plan.products} unit={selected?.uom || ''} onSelect={disabled ? undefined : onInspect}/>
        <p className="pw-panel-note">Cột càng cao, lượng thiếu càng lớn. Chọn sản phẩm để thử bổ sung hàng đúng hạn.</p>
      </Panel>
    </div>

    <Panel title="Diễn biến đủ và thiếu hàng" subtitle={`${productLabel} · cùng kho và kỳ kế hoạch`} action={evidence('balance')}>
      <Plot data={plan.baseline.series} unit={unitLabel(selected?.uom || '')} lines={[{key:'physical_inventory',name:'Tồn cuối ngày',color:'var(--fb-brand-blue)'},{key:'daily_peak_supply_shortage',name:'Lượng hàng thiếu lớn nhất trong ngày',color:'var(--fb-danger)'}]}/>
      <p>Tồn cuối ngày có thể đủ nhưng hàng vẫn thiếu trước giờ cần chuẩn bị. Mức thiếu xét thời điểm của đơn và hàng về; lịch nhà vận chuyển được kiểm tra riêng.</p>
    </Panel>
    </WorkspaceDialog><div className="pw-next-step"><span>Chọn mặt hàng cần bổ sung để thử phương án, sau đó gửi phối hợp và theo dõi bàn giao.</span>{onTasks&&<Button variant="outline" onClick={onTasks}>Mở việc của tôi</Button>}{onOutcomes&&<Button variant="outline" onClick={onOutcomes}>Xem tiến độ hồ sơ</Button>}</div>
  </div>;
}
