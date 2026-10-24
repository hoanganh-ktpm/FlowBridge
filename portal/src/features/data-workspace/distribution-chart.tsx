// SPDX-License-Identifier: GPL-3.0-or-later
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { demandDistribution } from './demand-distribution';
import { fmt, unitLabel } from './components';

export function DistributionChart({ values, unit, subject }: { values: number[]; unit: string; subject: string }) {
  if (values.length < 28) return <div className="pw-empty-inline">Cần ít nhất 28 ngày đã ghi nhận để xem phân bố. Hiện có {values.length} ngày; chọn 90 ngày hoặc 12 tháng rồi cập nhật biểu đồ. Khoảng xem ngắn vẫn dùng được để theo dõi diễn biến.</div>;
  const distribution = demandDistribution(values);
  if (!distribution) return <p className="pw-empty-inline">Chưa thể vẽ phân bố vì có số liệu không hợp lệ. Cần kiểm tra nguồn trước khi sử dụng.</p>;
  const precise = (n: number) => n.toLocaleString('vi-VN', { maximumFractionDigits: 3 });
  const rows = distribution.bins.map(b => ({ ...b, label: b.lower === b.upper ? precise(b.lower) : `${precise(b.lower)}–${precise(b.upper)}` }));
  return <>
    <div className="pw-distribution-summary"><div><span>Ít nhất 50% số ngày không vượt</span><strong>{fmt(distribution.p50)} <small>{unitLabel(unit)}</small></strong></div><div><span>Ít nhất 90% số ngày không vượt</span><strong>{fmt(distribution.p90)} <small>{unitLabel(unit)}</small></strong></div></div>
    <div className="pw-chart pw-distribution-chart" role="img" aria-label={`Phân bố ${subject} theo ngày và tỷ lệ ngày cộng dồn`}><ResponsiveContainer width="100%" height="100%"><ComposedChart data={rows} margin={{ top: 15, right: 5, bottom: 8, left: 0 }}>
      <CartesianGrid vertical={false} stroke="var(--fb-border)" strokeDasharray="3 5"/>
      <XAxis dataKey="label" tick={{ fontSize: 10, fill: 'var(--fb-text-muted)' }} axisLine={false} tickLine={false} minTickGap={15}/>
      <YAxis yAxisId="days" width={38} allowDecimals={false} tick={{ fontSize: 10, fill: 'var(--fb-text-muted)' }} axisLine={false} tickLine={false}/>
      <YAxis yAxisId="percent" orientation="right" domain={[0, 100]} width={40} tickFormatter={v => `${v}%`} tick={{ fontSize: 10, fill: 'var(--fb-brand-purple)' }} axisLine={false} tickLine={false}/>
      <Tooltip content={({ active, payload }) => {
        const bin = payload?.[0]?.payload as typeof rows[number] | undefined;
        return active && bin ? <div className="pw-chart-tooltip"><strong>{precise(bin.lower)} {bin.last ? 'đến' : 'đến dưới'} {precise(bin.upper)} {unitLabel(unit)}/ngày</strong><p>{bin.count}/{distribution.days} ngày nằm trong nhóm này</p><p>{fmt(bin.cumulativePercent)}% số ngày nằm trong nhóm này hoặc các nhóm thấp hơn</p></div> : null;
      }}/>
      <Legend wrapperStyle={{ fontSize: 10, paddingTop: 15 }}/>
      <Bar yAxisId="days" dataKey="count" name="Số ngày (trục trái)" fill="var(--fb-brand-blue)" maxBarSize={45} radius={[3, 3, 0, 0]} isAnimationActive={false}/>
      <Line yAxisId="percent" dataKey="cumulativePercent" type="stepAfter" name="Tỷ lệ ngày cộng dồn (trục phải)" stroke="var(--fb-brand-purple)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false}/>
    </ComposedChart></ResponsiveContainer></div>
    <p className="pw-panel-note">{distribution.atMostP90}/{distribution.days} ngày có {subject} không vượt {fmt(distribution.p90)} {unitLabel(unit)}; {distribution.days - distribution.atMostP90} ngày vượt mức này. Có {distribution.zeroDays} ngày không phát sinh. Đường tím cộng tỷ lệ từ nhóm thấp đến cao. Đây là lịch sử quan sát, không đảm bảo đủ hàng hay xác suất của những ngày tới; cần đối chiếu thêm nhịp trong tuần.</p>
    <details className="pw-distribution-data"><summary>Bảng số ngày theo mức phát sinh</summary><div className="dw-table-scroll"><table><thead><tr><th>Mức phát sinh/ngày</th><th>Số ngày</th><th>Tỷ lệ ngày cộng dồn</th></tr></thead><tbody>{rows.map(b => <tr key={b.index}><td>{precise(b.lower)} {b.last ? 'đến' : 'đến dưới'} {precise(b.upper)}</td><td>{b.count}</td><td>{fmt(b.cumulativePercent)}%</td></tr>)}</tbody></table></div></details>
  </>;
}
