// SPDX-License-Identifier: GPL-3.0-or-later
import { Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { dateLabel, fmt, unitLabel } from './components';
import type { Planning, PlanProduct } from './planning-types';

/** Compare shortage quantities only within one UOM; never imply a financial ranking. */
export function ProductRiskChart({ products, unit, onSelect }: { products: PlanProduct[]; unit: string; onSelect?: (p: PlanProduct) => void }) {
  const rows = products.filter(p => p.uom === unit)
    .sort((a, b) => (a.first_risk_date || '9999').localeCompare(b.first_risk_date || '9999') || (b.shortage || 0) - (a.shortage || 0)).slice(0, 8)
    .map(p => ({ ...p, label: p.name ? `${p.sku} - ${p.name}` : p.sku }));
  if (!rows.length) return <p className="pw-empty-inline">Chưa có sản phẩm cùng đơn vị để đối chiếu.</p>;
  return <><div className="pw-risk-chart" role="img" aria-label={`Nguy cơ thiếu hàng theo sản phẩm. Trục X: sản phẩm. Trục Y: số lượng thiếu, ${unitLabel(unit)}`}>
    <ResponsiveContainer width="100%" height="100%"><BarChart data={rows} margin={{ top: 24, right: 15, bottom: 5, left: 0 }}>
      <CartesianGrid vertical={false} stroke="var(--fb-border)" strokeDasharray="3 5"/>
      <XAxis dataKey="sku" tick={{ fontSize: 11, fill: 'var(--fb-text-muted)' }} axisLine={false} tickLine={false}/>
      <YAxis width={50} tick={{ fontSize: 11, fill: 'var(--fb-text-muted)' }} axisLine={false} tickLine={false} label={{ value: 'Số lượng thiếu', position: 'insideTopLeft', offset: 0, dx: 8, dy: -18, fontSize: 10, fill: 'var(--fb-text-muted)' }}/>
      <Tooltip cursor={{ fill: 'var(--fb-info-bg)' }} content={({ active, payload }) => {
        const item = payload?.[0]?.payload as typeof rows[number] | undefined;
        return active && item ? <div className="pw-chart-tooltip"><strong>{item.label}</strong><p>Hàng chưa giữ cho đơn khác: {fmt(item.available)}</p><p>Đơn chưa được giữ đủ hàng: {fmt(item.known_quantity)}</p><p>Hàng dự kiến về: {fmt(item.incoming_quantity)}</p><p>Lượng đặt mới dự báo: {fmt(item.forecast_quantity)}</p><hr/><p>Thiếu cho đơn đã nhận: {fmt(item.known_order_shortage)}</p><p>Thiếu tăng thêm khi xét dự báo: {fmt(item.forecast_added_shortage)}</p><p>Ngày cần xử lý: {dateLabel(item.first_risk_date)}</p></div> : null;
      }}/>
      <Legend wrapperStyle={{ fontSize: 10, paddingTop: 16 }}/>
      <Bar dataKey="known_order_shortage" name="Thiếu cho đơn đã nhận" stackId="risk" fill="var(--fb-danger)" maxBarSize={38} isAnimationActive={false} onClick={p => onSelect?.(p.payload as PlanProduct)} cursor={onSelect ? 'pointer' : 'default'}/>
      <Bar dataKey="forecast_added_shortage" name="Thiếu tăng thêm khi xét dự báo" stackId="risk" fill="var(--fb-brand-purple)" radius={[4, 4, 0, 0]} maxBarSize={38} isAnimationActive={false} onClick={p => onSelect?.(p.payload as PlanProduct)} cursor={onSelect ? 'pointer' : 'default'}/>
    </BarChart></ResponsiveContainer>
  </div>{rows.some(p => p.forecast_added_shortage == null) && <p className="pw-panel-note">Chưa đủ dự báo: {rows.filter(p => p.forecast_added_shortage == null).map(p => p.sku).join(', ')}. Phần chưa biết không tính là 0.</p>}{products.filter(p => p.uom === unit).length > 8 && <p className="pw-panel-note">Hiển thị 8 sản phẩm có ngày cần xử lý gần nhất. Danh sách bên dưới có đủ sản phẩm.</p>}</>;
}

export function DemandReceiptsChart({ history, unit }: { history: Planning['history']; unit: string }) {
  return <><div className="pw-flow-chart" role="img" aria-label={`Lượng đặt xác nhận và thực nhập theo ${history.frequency === 'week' ? 'tuần' : 'ngày'}. Đơn vị: ${unitLabel(unit)}`}>
    <ResponsiveContainer width="100%" height="100%"><ComposedChart data={history.series} margin={{ top: 24, right: 15, bottom: 5, left: 0 }}>
      <CartesianGrid vertical={false} stroke="var(--fb-border)" strokeDasharray="3 5"/>
      <XAxis dataKey="day" tickFormatter={v => dateLabel(String(v)).replace(/\/\d{4}$/, '')} minTickGap={24} tick={{ fontSize: 10 }} axisLine={false} tickLine={false}/>
      <YAxis width={50} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} label={{ value: unitLabel(unit), position: 'insideTopLeft', offset: 0, dx: 8, dy: -18, fontSize: 10, fill: 'var(--fb-text-muted)' }}/>
      <Tooltip labelFormatter={v => `${history.frequency === 'week' ? 'Tuần từ ' : ''}${dateLabel(String(v))}`} formatter={v => fmt(typeof v === 'number' ? v : null)} contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid var(--fb-border)' }}/>
      <Legend wrapperStyle={{ fontSize: 10, paddingTop: 16 }}/>
      <Line dataKey="confirmed" name="Lượng đặt xác nhận" stroke="var(--fb-brand-blue)" strokeWidth={2} dot={history.series.length === 1} isAnimationActive={false}/>
      {history.receipts_available && <Line dataKey="received" name="Thực nhập kho" stroke="var(--fb-brand-cyan)" strokeWidth={2} dot={history.series.length === 1} connectNulls={false} isAnimationActive={false}/>}
    </ComposedChart></ResponsiveContainer>
  </div>{!history.receipts_available && <p className="pw-panel-note">Chưa có nguồn thực nhập được chia sẻ. Chưa thể đối chiếu hai luồng.</p>}</>;
}
