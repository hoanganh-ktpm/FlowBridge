// SPDX-License-Identifier: GPL-3.0-or-later
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { InteractiveFlow, WorkspaceDialog } from './interactive-workspace';
import { Select, dateLabel, fmt, unitLabel } from './components';
import { OrderReadinessReasons, orderDateTime } from './order-readiness';
import type { Planning, PlanOrder } from './planning-types';

type Section = 'orders' | 'supply' | 'capacity';
const size = 20;
const normalize = (v: string) => v.toLocaleLowerCase('vi').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd');
const supplyState = (o: PlanOrder) => o.at_risk || o.shortages_at_deadline.some(s => s.missing_quantity > 0) || o.readiness?.conditions.supply === 'not_met'
  ? 'missing' : o.readiness?.conditions.supply === 'met' ? 'met' : 'unknown';
const supplyLabels = { missing: 'Có thể thiếu hàng', met: 'Đủ theo kế hoạch', unknown: 'Chưa đủ dữ liệu' };

export function HandoffWorkspace({ plan, initialOrder = '', initialOpen = false }: { plan: Planning; initialOrder?: string; initialOpen?: boolean }) {
  const [section, setSection] = useState<Section | null>(initialOrder || initialOpen ? 'orders' : null);
  const [queries, setQueries] = useState({ orders: initialOrder, supply: '', capacity: '' });
  const [filters, setFilters] = useState({ orders: 'all', supply: 'all', capacity: 'all' });
  const [pages, setPages] = useState({ orders: 0, supply: 0, capacity: 0 });
  const [orderId, setOrderId] = useState<string | null>(null);
  const active = section || 'orders', query = queries[active], filter = filters[active];
  const orders = plan.orders.filter(o => normalize(`${o.order_number} ${o.products.map(p => `${p.name || ''} ${p.sku}`).join(' ')}`).includes(normalize(query.trim()))
    && (filter === 'all' || (active === 'supply' ? supplyState(o) === filter
      : filter === 'risk' ? o.at_risk || o.readiness?.status === 'not_met'
      : filter === 'late' ? o.already_overdue
      : filter === 'ready' ? o.readiness?.status === 'conditionally_feasible'
      : !o.readiness || o.readiness.status === 'unknown')))
    .sort((a,b) => Date.parse(a.deadline) - Date.parse(b.deadline) || a.order_number.localeCompare(b.order_number));
  const calendar = plan.capacity_calendar.filter(r => normalize(`${r.carrier_name || ''} ${r.carrier_code} ${dateLabel(r.day)} ${r.day}`).includes(normalize(query.trim()))
    && (filter === 'all' || (filter === 'available' ? r.remaining > 0 && r.booked <= r.maximum : filter === 'over' ? r.booked > r.maximum : r.remaining <= 0 && r.booked <= r.maximum)))
    .sort((a,b) => a.day.localeCompare(b.day) || a.carrier_code.localeCompare(b.carrier_code));
  const total = active === 'capacity' ? calendar.length : orders.length;
  const page = Math.min(pages[active], Math.max(0, Math.ceil(total / size) - 1));
  const visible = orders.slice(page * size, (page + 1) * size);
  const selected = plan.orders.find(o => o.order_id === orderId);
  function open(value: Section) { setSection(value); setOrderId(null); }
  function resetPage() { setPages(p => ({ ...p, [active]: 0 })); }
  const titles = { orders: 'Đơn đến hạn bàn giao', supply: 'Đối chiếu nguồn hàng theo đơn', capacity: 'Lịch tiếp nhận của nhà vận chuyển' };
  return <section aria-label="Kiểm tra trước khi bàn giao">
    <InteractiveFlow label="Đối chiếu đơn trước khi bàn giao" selected={section || undefined} onSelect={id => open(id as Section)} nodes={[
      { id: 'orders', title: 'Đơn đến hạn bàn giao', value: `${plan.orders.length} đơn`, description: 'Mở danh sách để tìm đơn, lọc điều kiện và xem chi tiết' },
      { id: 'supply', title: 'Đối chiếu nguồn hàng', value: `${plan.orders.filter(o => supplyState(o) === 'missing').length} đơn có thể thiếu`, description: 'Xem thiếu mặt hàng nào, bao nhiêu và cần đủ lúc nào' },
      { id: 'capacity', title: 'Kiểm tra lịch lấy hàng', value: `${plan.capacity_calendar.length} lịch công bố`, description: 'Tìm nhà vận chuyển và xem ngày còn lượt nhận hàng' },
    ]}/>
    <p className="pw-panel-note">Bấm một ô để mở danh sách trong hộp thoại. Đây là kiểm tra kế hoạch; chưa đặt lịch hoặc ghi nhận bàn giao.</p>
    <WorkspaceDialog open={!!section} onClose={() => { setSection(null); setOrderId(null); }} title={titles[active]}
      description={`${plan.warehouses.find(w => w.id === plan.warehouse_id)?.name || 'Kho đang xem'} · ${dateLabel(plan.start)} – ${dateLabel(plan.end)}. Kết quả theo dữ liệu đã đọc; các mốc giờ là giờ Việt Nam.`} wide>
      <div className="iw-list-toolbar">
        <Input aria-label={active === 'capacity' ? 'Tìm lịch nhà vận chuyển' : 'Tìm đơn cần bàn giao'} placeholder={active === 'capacity' ? 'Tìm tên, mã nhà vận chuyển hoặc ngày…' : 'Tìm mã đơn, tên hoặc mã mặt hàng…'} value={query} onChange={e => { setQueries(q => ({ ...q, [active]: e.target.value })); resetPage(); }}/>
        <Select label={active === 'capacity' ? 'Lọc lượt nhận hàng' : active === 'supply' ? 'Lọc nguồn hàng' : 'Lọc điều kiện bàn giao'} value={filter} onChange={v => { setFilters(f => ({ ...f, [active]: v })); resetPage(); }}>
          <option value="all">Tất cả</option>
          {active === 'capacity' ? <><option value="available">Còn lượt nhận</option><option value="full">Hết lượt nhận</option><option value="over">Vượt lịch công bố</option></>
            : active === 'supply' ? <><option value="missing">Có thể thiếu hàng</option><option value="met">Đủ theo kế hoạch</option><option value="unknown">Chưa đủ dữ liệu</option></>
            : <><option value="risk">Còn điều kiện chưa đáp ứng</option><option value="late">Đã qua hạn bàn giao</option><option value="ready">Đáp ứng theo kế hoạch</option><option value="unknown">Chưa đủ dữ liệu</option></>}
        </Select>
      </div>
      {section && <div className="dw-table-scroll iw-list-table" tabIndex={0} role="region" aria-label="Danh sách kiểm tra bàn giao">
        {active === 'capacity' ? <table aria-label="Lịch nhà vận chuyển"><thead><tr><th>Ngày</th><th>Nhà vận chuyển</th><th>Số lượt công bố</th><th>Đã đặt</th><th>Còn lượt nhận</th><th>Gợi ý phối hợp</th></tr></thead><tbody>{calendar.slice(page*size,(page+1)*size).map(r => <tr key={r.day+r.carrier_code}><td>{dateLabel(r.day)}</td><td>{r.carrier_name || r.carrier_code}<small>{r.carrier_code}</small></td><td>{fmt(r.maximum)}</td><td>{fmt(r.booked)}</td><td>{fmt(r.remaining)}</td><td>{r.booked > r.maximum ? 'Vượt lịch công bố · cần điều phối lại' : r.remaining > 0 ? 'Có thể trao đổi thêm lịch nhận' : 'Cần hỏi lịch khác hoặc đơn vị khác'}</td></tr>)}</tbody></table>
          : <table aria-label={active === 'supply' ? 'Nguồn hàng theo đơn' : 'Đơn và điều kiện bàn giao'}><thead><tr><th>Đơn / sản phẩm</th><th>Hạn bàn giao</th><th>{active === 'supply' ? 'Hàng có đủ không?' : 'Điều kiện bàn giao'}</th><th>{active === 'supply' ? 'Cần đủ hàng trước' : 'Lịch lấy hàng dự kiến'}</th><th>Chi tiết</th></tr></thead><tbody>{visible.map(o => <tr key={o.order_id}>
            <td><strong>{o.order_number}</strong>{o.products.map(p => <small key={p.sku}>{p.name || p.sku} · {p.sku}</small>)}</td>
            <td>{orderDateTime(o.deadline)}{o.already_overdue && <small>Đã qua hạn bàn giao</small>}</td>
            <td>{active === 'supply' ? <><strong>{supplyLabels[supplyState(o)]}</strong>{o.shortages_at_deadline.filter(s => s.missing_quantity > 0).map(s => <small key={s.sku}>Thiếu {fmt(s.missing_quantity)} {unitLabel(s.uom)} · {o.products.find(p => p.sku === s.sku)?.name || s.sku}</small>)}</>
              : <span className={`pw-status ${o.readiness?.status === 'not_met' || o.at_risk ? 'pw-urgent' : 'pw-watch'}`}>{o.readiness?.status === 'conditionally_feasible' ? 'Đáp ứng theo kế hoạch' : o.readiness?.status === 'not_met' || o.at_risk ? 'Còn điều kiện chưa đáp ứng' : 'Chưa đủ dữ liệu'}</span>}</td>
            <td>{active === 'supply' ? o.readiness?.stock_ready_by ? orderDateTime(o.readiness.stock_ready_by) : 'Kho chưa xác nhận thời gian chuẩn bị'
              : o.shipments.length ? o.shipments.map(s => <div key={s.shipment_number}>{s.carrier_name || s.carrier_code}<small>{s.shipment_number} · {s.scheduled_handoff_at ? orderDateTime(s.scheduled_handoff_at) : 'Chưa xác nhận ngày giờ nhận'}</small></div>) : 'Chưa có lịch được chia sẻ'}</td>
            <td><Button variant="outline" size="sm" aria-label={`Mở điều kiện ${o.order_number}`} onClick={() => setOrderId(o.order_id)}>Xem chi tiết</Button></td>
          </tr>)}</tbody></table>}
      </div>}
      {!total && <p className="pw-empty-inline" role="status">{query || filter !== 'all' ? 'Không có kết quả khớp tìm kiếm và bộ lọc.' : active === 'capacity' ? 'Chưa có lịch công bố trong kỳ. Chưa thể kết luận còn lượt nhận hàng.' : 'Không có đơn đang chờ trong kỳ được chia sẻ.'}</p>}
      <div className="iw-list-pagination"><span>{total ? `Hiển thị ${page*size+1}–${Math.min((page+1)*size,total)} / ${total} ${active === 'capacity' ? 'lịch' : 'đơn'}` : '0 kết quả'}</span><div>
        <Button variant="outline" size="sm" disabled={page===0} onClick={() => setPages(p => ({ ...p, [active]: page-1 }))}>Trang trước</Button>
        <Button variant="outline" size="sm" disabled={(page+1)*size>=total} onClick={() => setPages(p => ({ ...p, [active]: page+1 }))}>Trang sau</Button>
      </div></div>
      {active === 'capacity' && <p className="pw-panel-note">Một lượt là một lần đến nhận bàn giao, không phải một sản phẩm. Lịch còn lượt chưa có nghĩa đơn đã được đặt lịch.</p>}
    </WorkspaceDialog>
    <WorkspaceDialog open={!!selected && !!section} onClose={() => setOrderId(null)} title={`Điều kiện bàn giao ${selected?.order_number || ''}`} description="Đối chiếu nguồn hàng và lịch nhận trên cùng đơn. Điều kiện kế hoạch chưa phải bằng chứng đã bàn giao." wide>
      {selected && <><p>Hạn bàn giao: <strong>{orderDateTime(selected.deadline)}</strong></p>
        {selected.readiness?.stock_ready_by && <p>Cần đủ hàng trước <strong>{orderDateTime(selected.readiness.stock_ready_by)}</strong>{selected.readiness.preparation_minutes != null ? ` để Kho chuẩn bị trong ${fmt(selected.readiness.preparation_minutes)} phút.` : '.'}</p>}
        <OrderReadinessReasons order={selected}/>
        <h3>Lịch lấy hàng dự kiến</h3>
        {selected.shipments.length ? selected.shipments.map(s => <p key={s.shipment_number}><strong>{s.carrier_name || s.carrier_code}</strong><small className="iw-detail-line">{s.shipment_number} · {s.scheduled_handoff_at ? orderDateTime(s.scheduled_handoff_at) : 'Chưa xác nhận ngày giờ nhận'}</small></p>) : <p>Chưa có lịch được chia sẻ.</p>}
        <h3>Các mặt hàng trong đơn</h3><div className="dw-table-scroll"><table><thead><tr><th>Mặt hàng</th><th>Còn cần phân bổ</th><th>Có thể thiếu trước giờ chuẩn bị</th></tr></thead><tbody>{selected.products.map(p => {
          const line=selected.lines.find(l => l.sku===p.sku && l.uom===p.uom), shortage=selected.shortages_at_deadline.find(s => s.sku===p.sku && s.uom===p.uom);
          return <tr key={p.sku+p.uom}><td>{p.name || p.sku}<small>{p.sku}</small></td><td>{line ? `${fmt(line.unallocated_quantity)} ${unitLabel(p.uom)}` : 'Chưa có số lượng được chia sẻ'}</td><td>{shortage ? `${fmt(shortage.missing_quantity)} ${unitLabel(p.uom)}` : supplyState(selected)==='met' ? `0 ${unitLabel(p.uom)}` : 'Chưa có số lượng thiếu chi tiết'}</td></tr>;
        })}</tbody></table></div><Button variant="outline" onClick={() => { setOrderId(null); setSection('capacity'); }}>Đối chiếu lịch nhà vận chuyển</Button></>}
    </WorkspaceDialog>
  </section>;
}
