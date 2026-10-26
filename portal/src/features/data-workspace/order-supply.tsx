// SPDX-License-Identifier: GPL-3.0-or-later
import { AlertCircle, Check, Info, LoaderCircle } from 'lucide-react';
import { cell } from '@/lib/format';
import { unitLabel } from './components';
import { orderDateTime } from './order-readiness';
import type { Warehouse } from './warehouse-overview-types';

type Order = Warehouse['open_orders'][number];
const quantityColumn = { field: 'missing_quantity', label: 'Thiếu dự kiến', type: 'decimal', scale: 3 };

function supplyState(warehouse: Warehouse, order: Order) {
  if (warehouse.planning_status === 'pending') return 'pending';
  if (warehouse.planning_status !== 'calculated') return 'unknown';
  return order.supply_check?.status || 'unknown';
}

const labels = {
  risk: 'Có thể thiếu hàng', clear: 'Chưa thấy thiếu hàng', pending: 'Đang kiểm tra…',
  unknown: 'Chưa đủ dữ liệu', outside_horizon: 'Ngoài kỳ dự báo',
};

export function OrderSupplyBadge({ warehouse, order, onClick }: {
  warehouse: Warehouse; order: Order; onClick?: () => void;
}) {
  const state = supplyState(warehouse, order);
  const Icon = state === 'risk' ? AlertCircle : state === 'clear' ? Check : state === 'pending' ? LoaderCircle : Info;
  const content = <><Icon size={14} className={state === 'pending' ? 'animate-spin' : undefined}/>{labels[state]}</>;
  const className = `wo-badge wo-${state === 'outside_horizon' ? 'unknown' : state} wo-supply-badge`;
  return onClick
    ? <button type="button" className={className} onClick={onClick} aria-label={`${labels[state]} của ${order.order_number}`} aria-haspopup="dialog">{content}</button>
    : <span className={className}>{content}</span>;
}

export function OrderSupplyDetails({ warehouse, order, productName, onAnalyze }: {
  warehouse: Warehouse; order: Order; productName: (sku: string, uom: string) => string;
  onAnalyze?: (sku: string) => void;
}) {
  const state = supplyState(warehouse, order);
  const check = warehouse.planning_status === 'calculated' ? order.supply_check : null;
  return <section className="wo-order-supply" aria-label={`Kiểm tra thiếu hàng của ${order.order_number}`}>
    <div className="wo-supply-heading"><h3>Hàng có đủ cho đơn này không?</h3><OrderSupplyBadge warehouse={warehouse} order={order}/></div>
    {state === 'pending' ? <p>Đang kiểm tra từng mặt hàng. Bạn vẫn có thể xem số lượng đã đặt bên dưới.</p>
      : !check ? <p>Chưa kiểm tra được lượng thiếu của đơn này. {warehouse.planning_issue?.message || 'Chưa có kết quả dự báo đầy đủ; không thể kết luận đủ hàng.'}</p>
      : <>
        {check.stock_ready_by && state !== 'outside_horizon' && <p>Cần đủ hàng trước <strong>{orderDateTime(check.stock_ready_by)}</strong> để Kho có {check.preparation_minutes} phút chuẩn bị.</p>}
        {state === 'risk' && <div className="wo-table-scroll"><table aria-label={`Mặt hàng có thể thiếu của ${order.order_number}`}><thead><tr><th>Mặt hàng</th><th>Thiếu dự kiến</th></tr></thead><tbody>{check.shortages.map(item => <tr key={`${item.sku}:${item.uom}`}><td><strong>{productName(item.sku, item.uom)}</strong><small>{item.sku}</small></td><td><strong className="wo-shortage">{cell(item.missing_quantity, quantityColumn)} {unitLabel(item.uom)}</strong>{onAnalyze&&<button className="wo-supply-next" onClick={()=>onAnalyze(item.sku)}>Thử bổ sung</button>}</td></tr>)}</tbody></table></div>}
        {state === 'clear' && <p>Chưa thấy mặt hàng nào thiếu theo kế hoạch hiện tại. Vẫn cần Kho xác nhận giữ đủ hàng và Vận chuyển xác nhận lịch lấy hàng.</p>}
        {!!check.notes.length && <ul className="wo-supply-notes">{check.notes.map(note => <li key={note}>{note}</li>)}</ul>}
        {state !== 'outside_horizon' && <details><summary>Căn cứ tính lượng thiếu</summary><p>Đối chiếu phần hàng chưa giữ hoặc chưa xuất của đơn với tồn có thể dùng, hàng dự kiến về và nhu cầu dự báo trước hạn của từng mặt hàng. Kết quả là ước tính, không phải số lượng đặt hay lượng thiếu chung của cả kho. Các đơn dùng chung nguồn hàng nên không cộng lượng thiếu của từng đơn để ra tổng lượng cần nhập.</p>{warehouse.plan_read_at && <p>Tính lúc {orderDateTime(warehouse.plan_read_at)}.</p>}</details>}
      </>}
  </section>;
}
