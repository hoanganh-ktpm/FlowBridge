// SPDX-License-Identifier: GPL-3.0-or-later
import type { ReactNode } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Panel, Select, dateLabel, fmt, unitLabel } from './components';
import { DemandReceiptsChart, ProductRiskChart } from './decision-charts';
import type { Planning, PlanProduct } from './planning-types';

const status = { urgent: 'Cần bổ sung', watch: 'Cần xác nhận hàng về', clear: 'Đủ theo ước tính', unknown: 'Thiếu dữ liệu dự báo' };
export function OperationsDashboard({ plan, selected, filter, onFilter, onInspect, onOrders, disabled, evidence }: {
  plan: Planning; selected: PlanProduct | undefined; filter: string; onFilter: (v: string) => void;
  onInspect: (p: PlanProduct) => void; onOrders: () => void; disabled: boolean; evidence: (key: string) => ReactNode;
}) {
  const rows = plan.products.filter(p => filter === 'all' || filter === 'urgent' && p.priority === 'urgent' || filter === 'watch' && p.late_plan_ids.length > 0);
  const productLabel = `${plan.selected_sku}${selected?.name ? ' - ' + selected.name : ''}`;
  function focus(value: string) { onFilter(value); document.getElementById('priority-list')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  return <div className="pw-overview">
    <div className="pw-summary-grid">
      <Card><Button variant="ghost" onClick={() => focus('urgent')}><span>Mặt hàng cần bổ sung</span><strong>{plan.kpis.risk_products}<small>/{plan.products.length}</small></strong></Button></Card>
      <Card><Button variant="ghost" onClick={onOrders}><span>Đơn cần phối hợp</span><strong>{plan.kpis.risk_orders}</strong></Button></Card>
      <Card><Button variant="ghost" onClick={() => focus('watch')}><span>Lịch hàng về cần rà</span><strong>{plan.kpis.plans_attention}</strong></Button></Card>
    </div>
    <div className="pw-business-charts">
      <Panel title="Lượng đặt và thực nhập" subtitle={`${productLabel} · ${dateLabel(plan.history.start)} – ${dateLabel(plan.history.end)}`} action={evidence('history')}>
        <DemandReceiptsChart history={plan.history} unit={selected?.uom || ''}/>
        <p className="pw-panel-note">Hai luồng của cùng sản phẩm, cùng kho · không phải biến động tồn kho.</p>
      </Panel>
      <Panel title="Nguy cơ thiếu hàng theo sản phẩm" subtitle={`Đến ${dateLabel(plan.end)} · ${unitLabel(selected?.uom)}`} action={evidence('risk')}>
        <ProductRiskChart products={plan.products} unit={selected?.uom || ''} onSelect={disabled ? undefined : onInspect}/>
        <p className="pw-panel-note">Cột càng cao, lượng thiếu càng lớn. Chọn sản phẩm để thử bổ sung hàng đúng hạn.</p>
      </Panel>
    </div>
    <Panel title="Danh sách cần theo dõi" action={<Select label="Lọc mức ưu tiên" value={filter} onChange={onFilter}><option value="all">Tất cả sản phẩm</option><option value="urgent">Cần bổ sung</option><option value="watch">Cần rà lịch hàng về</option></Select>}>
      <div id="priority-list" className="pw-compact-table"><Table><TableHeader><TableRow><TableHead>Sản phẩm</TableHead><TableHead>Hàng còn phân bổ</TableHead><TableHead>Đơn chờ phân bổ</TableHead><TableHead>Thiếu nhiều nhất</TableHead><TableHead className="text-right">Xử lý</TableHead></TableRow></TableHeader><TableBody>{rows.map(p => <TableRow key={p.sku}>
        <TableCell><strong>{p.sku} - {p.name || 'Chưa có tên được chia sẻ'}</strong><div><Badge variant={p.priority === 'urgent' ? 'destructive' : 'secondary'}>{status[p.priority]}</Badge></div></TableCell>
        <TableCell>{fmt(p.available)}<small>{unitLabel(p.uom)}</small></TableCell><TableCell>{fmt(p.known_quantity)}</TableCell>
        <TableCell><strong className={p.shortage ? 'pw-danger-text' : ''}>{fmt(p.shortage)}</strong><small>{p.first_risk_date ? 'Từ ' + dateLabel(p.first_risk_date) : 'Trong kỳ đang xét'}</small></TableCell>
        <TableCell className="text-right"><Button variant="ghost" size="sm" disabled={disabled || p.priority === 'unknown'} onClick={() => onInspect(p)}>{p.priority === 'urgent' ? 'Thử bổ sung hàng' : 'Xem chi tiết'}</Button></TableCell>
      </TableRow>)}</TableBody></Table>{!rows.length && <p className="pw-empty-inline">Không có sản phẩm thuộc mức này.</p>}</div>
    </Panel>
  </div>;
}
