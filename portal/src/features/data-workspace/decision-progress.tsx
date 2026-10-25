// SPDX-License-Identifier: GPL-3.0-or-later
type Observation = { id: string; event_type: string; created_at: string; payload: { event_type?: string; producer?: string; correlation_id?: string; occurred_at?: string } };
const labels: Record<string, string> = {
  'warehouse.order_reserved': 'Kho ghi nhận giữ hàng',
  'warehouse.dispatch_prepared': 'Kho ghi nhận chuẩn bị kiện hàng',
  'logistics.shipment_accepted': 'Vận chuyển xác nhận lịch nhận',
  'warehouse.dispatch_handed_over': 'Kho ghi nhận bàn giao cho đơn vị vận chuyển',
  'logistics.handoff_verified': 'Vận chuyển đã đối chiếu bằng chứng bàn giao',
  'sales.handoff_partial': 'Sales ghi nhận bàn giao một phần',
  'sales.handoff_completed': 'Sales ghi nhận bàn giao đủ',
};
export function DecisionProgress({ events, orderId }: { events: Observation[]; orderId: string }) {
  const recorded = events.filter(event => event.event_type === 'SOURCE_OBSERVED' && event.payload.correlation_id === orderId && labels[event.payload.event_type || '']);
  return <section aria-label="Bước thực hiện đã ghi nhận">
    <h3>Bước thực hiện đã ghi nhận</h3>
    <p>Lịch sử giữ nguyên các thao tác nguồn đã ghi nhận, kể cả khi phương án cần sửa. Chỉ đối chiếu đủ bằng chứng bàn giao mới hoàn tất hồ sơ.</p>
    {!recorded.length && <p>Chưa có sự kiện thực hiện của đơn này trong phần lịch sử đã đối soát. Điều này không chứng minh thao tác chưa chạy; cần kiểm tra nguồn hoặc kết quả lệnh nếu mất phản hồi.</p>}
    <ol>{recorded.map(event => <li key={event.id}>{labels[event.payload.event_type!]} · {new Date(event.payload.occurred_at || event.created_at).toLocaleString('vi-VN')}</li>)}</ol>
  </section>;
}
