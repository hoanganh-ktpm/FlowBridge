// SPDX-License-Identifier: GPL-3.0-or-later
import { dateLabel, fmt, unitLabel } from './components';
import type { Scenario } from './types';

export function SensitivityChart({ value, baselineShortage, disabled, quantity, arrival, onSelect }: {
  value: NonNullable<Scenario['sensitivity']>; baselineShortage: number; disabled: boolean;
  quantity: number; arrival: string; onSelect: (quantity: number, arrival: string) => void;
}) {
  return <>
    <p className="pw-panel-note">Mỗi ô là một phương án nhập bổ sung độc lập: hàng ngang là lượng nhập, cột là ngày về. Số lớn là lượng thiếu cao nhất còn lại của sản phẩm; dòng nhỏ là số đơn thiếu hàng trước hạn trong toàn kho.</p>
    <div className="pw-sensitivity-scroll"><table className="pw-sensitivity-table" aria-label="Ma trận lượng nhập và ngày hàng về"><thead><tr><th scope="col">Nhập thêm<br/><small>{unitLabel(value.unit)}</small></th>{value.dates.map(d => <th scope="col" key={d}>{dateLabel(d)}</th>)}</tr></thead><tbody>{value.quantities.map(q => <tr key={q}><th scope="row">{fmt(q)}</th>{value.dates.map(d => {
      const cell = value.cells.find(c => c.quantity === q && c.arrival_date === d);
      if (!cell) return <td key={d}>Chưa tính</td>;
      const status = cell.peak_shortage > 0 ? cell.peak_shortage >= baselineShortage ? 'high' : 'short' : cell.known_orders_at_risk === null ? 'unknown' : cell.known_orders_at_risk > 0 ? 'late' : 'clear';
      const selected = quantity === q && arrival === d;
      return <td key={d}><button type="button" className={`pw-sensitivity-cell pw-cell-${status}`} aria-pressed={selected} disabled={disabled} aria-label={`Nhập ${fmt(q)} ${unitLabel(value.unit)}, về ${dateLabel(d)}: thiếu ${fmt(cell.peak_shortage)}, ${cell.known_orders_at_risk === null ? 'chưa biết số đơn trễ' : `${cell.known_orders_at_risk} đơn thiếu hàng trước hạn`}`} onClick={() => onSelect(q, d)}><strong>{fmt(cell.peak_shortage)}</strong><small>{cell.known_orders_at_risk === null ? 'Đơn: chưa đủ dữ liệu' : `${cell.known_orders_at_risk} đơn có nguy cơ trễ`}</small></button></td>;
    })}</tr>)}</tbody></table></div>
    <div className="pw-sensitivity-legend"><span><i className="pw-cell-clear"/>Chưa thấy thiếu hàng/đơn trễ do thiếu hàng</span><span><i className="pw-cell-short"/>Còn thiếu hàng</span><span><i className="pw-cell-late"/>Còn đơn trễ dù lượng thiếu về sau bằng 0</span></div>
    <p className="pw-panel-note">Chọn một ô để điền lượng và ngày về, rồi bấm So sánh phương án. Các lượng thử lấy quanh mức thiếu của kế hoạch hiện tại và lượng bạn nhập, không phải đề xuất mua tối ưu. Hàng về sau hạn không xóa việc đơn đã trễ. Lịch vận chuyển và chi phí cần được rà riêng.</p>
  </>;
}
