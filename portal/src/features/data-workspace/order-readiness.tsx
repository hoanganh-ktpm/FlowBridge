// SPDX-License-Identifier: GPL-3.0-or-later
import type { PlanOrder } from './planning-types';
import { fmt, unitLabel } from './components';

export const orderDateTime = (value: string) => new Intl.DateTimeFormat('vi-VN', {
  timeZone: 'Asia/Ho_Chi_Minh', hour: '2-digit', minute: '2-digit',
  day: '2-digit', month: '2-digit', year: 'numeric',
}).format(new Date(value));

export function OrderReadinessReasons({ order }: { order: PlanOrder }) {
  const reasons: string[] = [];
  const conditions = order.readiness?.conditions;
  const shortages = order.shortages_at_deadline.filter(item => item.missing_quantity > 0);
  for (const item of shortages) {
    const product = order.products.find(product => product.sku === item.sku);
    reasons.push(`Thiếu ${fmt(item.missing_quantity)} ${unitLabel(item.uom)} ${product?.name || item.sku} (${item.sku}) trước mốc đủ hàng để chuẩn bị.`);
  }
  if (!shortages.length && (conditions?.supply === 'not_met' || order.at_risk))
    reasons.push('Nguồn Kho chưa chứng minh đủ hàng trước mốc chuẩn bị; chưa có số lượng thiếu chi tiết.');
  if (conditions?.supply === 'unknown') reasons.push('Chưa xác minh được nguồn hàng cho toàn đơn.');

  const deadline = Date.parse(order.deadline);
  const late = order.shipments.filter(shipment => shipment.scheduled_handoff_at
    && Date.parse(shipment.scheduled_handoff_at) > deadline);
  for (const shipment of late)
    reasons.push(`${order.already_overdue ? 'Đã quá hạn bàn giao cho đơn vị vận chuyển.' : 'Lịch nhận hàng không kịp hạn bàn giao.'} Hạn bàn giao: ${orderDateTime(order.deadline)}. ${shipment.carrier_name || shipment.carrier_code} dự kiến đến nhận lúc ${orderDateTime(shipment.scheduled_handoff_at!)}; lịch này muộn hơn hạn yêu cầu.`);
  if (!late.length && (conditions?.schedule === 'not_met' || order.schedule_status === 'late'))
    reasons.push('Nguồn Vận chuyển báo lịch sau hạn; chưa có giờ nhận cụ thể để đối chiếu.');
  if (order.schedule_status === 'unbooked') reasons.push('Chưa có lịch nhận được đơn vị vận chuyển xác nhận.');
  else if (order.schedule_status === 'unknown' || conditions?.schedule === 'unknown')
    reasons.push('Chưa đủ dữ liệu ngày giờ nhận để kiểm tra lịch có kịp hạn.');

  if (conditions?.preparation === 'unknown') reasons.push('Kho chưa xác nhận cần bao lâu để chuẩn bị hàng.');
  else if (conditions?.preparation === 'not_met') reasons.push('Chưa đáp ứng thời gian chuẩn bị hàng trước lịch nhận.');
  if (conditions?.capacity === 'not_met') reasons.push('Năng lực nhận hàng của đơn vị vận chuyển không đáp ứng lịch đã chọn.');
  else if (conditions?.capacity === 'unknown') reasons.push('Chưa xác minh được năng lực nhận hàng cho lịch của đơn này.');

  if (!reasons.length) return <small>{order.readiness?.status === 'conditionally_feasible'
    ? 'Các điều kiện kế hoạch đã đáp ứng; chưa phải bằng chứng đã bàn giao.'
    : 'Chưa có kết quả kiểm tra đầy đủ điều kiện bàn giao.'}</small>;
  return <ul aria-label={`Lý do cần xử lý ${order.order_number}`} style={{paddingLeft:18,margin:'8px 0',minWidth:240}}>
    {reasons.map(reason => <li key={reason}><small>{reason}</small></li>)}
  </ul>;
}
