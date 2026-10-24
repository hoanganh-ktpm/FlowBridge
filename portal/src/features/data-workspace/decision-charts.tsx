// SPDX-License-Identifier: GPL-3.0-or-later
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { dateLabel, fmt, unitLabel } from './components';
import type { PlanProduct } from './planning-types';

/** Compare shortage quantities only within one UOM; never imply a financial ranking. */
export function ReplenishmentPriorities({ products, unit }: { products: PlanProduct[]; unit: string }) {
  const rows = products.filter(p => p.uom === unit && p.shortage != null && p.shortage > 0)
    .sort((a, b) => (a.first_risk_date || '9999').localeCompare(b.first_risk_date || '9999') || (b.shortage || 0) - (a.shortage || 0)).slice(0, 8)
    .map(p => ({ ...p, label: p.name ? `${p.sku} - ${p.name}` : p.sku }));
  if (!rows.length) return <p className="pw-empty-inline">Chưa thấy thiếu hàng với nhóm đơn vị {unitLabel(unit)} trong kỳ này. Xem thêm các kế hoạch quá ngày về và lịch giao nhận trước khi chốt.</p>;
  return <div className="pw-priority-chart" style={{ height: Math.max(190, rows.length * 48 + 55) }} role="img" aria-label={`Mức thiếu hàng cao nhất theo sản phẩm. Đơn vị: ${unitLabel(unit)}`}>
    <ResponsiveContainer width="100%" height="100%"><BarChart data={rows} layout="vertical" margin={{ top: 12, right: 30, bottom: 10, left: 10 }}>
      <CartesianGrid horizontal={false} stroke="var(--fb-border)" strokeDasharray="3 5"/>
      <XAxis type="number" tick={{ fontSize: 11, fill: 'var(--fb-text-muted)' }} axisLine={false} tickLine={false}/>
      <YAxis type="category" dataKey="label" width={155} tick={{ fontSize: 11, fill: 'var(--fb-text-muted)' }} axisLine={false} tickLine={false}/>
      <Tooltip cursor={{ fill: 'var(--fb-info-bg)' }} content={({ active, payload }) => {
        const item = payload?.[0]?.payload as typeof rows[number] | undefined;
        return active && item ? <div className="pw-chart-tooltip"><strong>{item.label}</strong><p>Thiếu nhiều nhất: {fmt(item.shortage)} {unitLabel(unit)}</p><p>Bắt đầu cần xử lý: {dateLabel(item.first_risk_date)}</p><p>{item.orders_at_risk} đơn có nguy cơ thiếu hàng trước hạn</p></div> : null;
      }}/>
      <Bar dataKey="shortage" name="Thiếu nhiều nhất trong kỳ" fill="var(--fb-brand-purple)" radius={[0, 4, 4, 0]} maxBarSize={23} isAnimationActive={false}/>
    </BarChart></ResponsiveContainer>
  </div>;
}
