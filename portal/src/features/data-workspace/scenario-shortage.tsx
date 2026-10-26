// SPDX-License-Identifier: GPL-3.0-or-later
import { dateLabel, fmt, unitLabel } from './components';
import { scenarioSummary } from './scenario-summary';
import type { Scenario } from './types';

/** The warehouse-wide forecast deficit must not read as one order's shortage. */
export function ScenarioShortage({ value }: { value: Scenario }) {
  const summary = scenarioSummary(value);
  const assumptions = value.evidence.assumptions as { sku?: string } | undefined;
  const sku = assumptions?.sku;
  const unit = unitLabel(summary.unit || '');
  return <section className="pw-shortage-summary" aria-label="Lượng hàng thiếu trước và sau khi bổ sung">
    <p><strong>Lượng hàng còn thiếu theo tính toán{sku ? ` · ${sku}` : ''}</strong></p>
    <p>Trước khi bổ sung hàng: <strong>{summary.before === null ? 'Chưa xác định' : `${fmt(summary.before)} ${unit}`}</strong>.</p>
    <p>Sau khi bổ sung theo phương án: <strong>{summary.after === null ? 'Chưa xác định' : `${fmt(summary.after)} ${unit}`}</strong>.</p>
    <p className="dw-muted">Đây là mức thiếu lớn nhất tại một thời điểm trong kỳ của mặt hàng này tại kho, tính cả đơn đang chờ và lượng khách có thể đặt thêm. Không phải số lượng thiếu riêng của đơn trong hồ sơ; không cộng các ngày thiếu với nhau.</p>
    <p className="dw-muted">Số lẻ có thể xuất hiện vì nhu cầu tương lai là ước tính. Khi đề xuất nhập hàng, hệ thống làm tròn lượng cần bổ sung lên số nguyên.</p>
    {summary.legacy ? <p role="status">Kết quả cũ chưa đủ dữ liệu để giải thích lượng thiếu. Cần tính lại trước khi dùng.</p>
      : summary.firstShortageDate ? <p>Sau khi bổ sung, vẫn có thể thiếu hàng từ ngày {dateLabel(summary.firstShortageDate)}.</p>
      : summary.after === 0 ? <p>Theo phương án này, mặt hàng không còn thiếu trong kỳ. Việc chuẩn bị và lịch nhận của đơn vị vận chuyển vẫn cần được kiểm tra riêng.</p>
      : <p>Chưa xác định ngày bắt đầu thiếu hàng sau khi bổ sung.</p>}
  </section>;
}
