// SPDX-License-Identifier: GPL-3.0-or-later
import { useState } from 'react';
import { InteractiveFlow, WorkspaceDialog } from './interactive-workspace';
type Observation = { id: string; event_type: string; created_at: string; payload: { event_type?: string; producer?: string; correlation_id?: string; occurred_at?: string } };
const labels: Record<string, string> = {
  'warehouse.order_reserved': 'Kho ghi nhận giữ hàng',
  'warehouse.dispatch_prepared': 'Kho ghi nhận chuẩn bị kiện hàng',
  'logistics.shipment_accepted': 'Vận chuyển xác nhận lịch nhận',
  'warehouse.dispatch_handed_over': 'Kho ghi nhận bàn giao cho đơn vị vận chuyển',
  'logistics.handoff_verified': 'Vận chuyển đã đối chiếu bằng chứng bàn giao',
  'sales.handoff_partial': 'Bán hàng ghi nhận bàn giao một phần',
  'sales.handoff_completed': 'Bán hàng ghi nhận bàn giao đủ',
};
export function DecisionProgress({ events, orderId }: { events: Observation[]; orderId: string }) {
  const [step, setStep] = useState<string | null>(null);
  const recorded = events.filter(event => event.event_type === 'SOURCE_OBSERVED' && event.payload.correlation_id === orderId && labels[event.payload.event_type || '']);
  const selected = recorded.filter(event => event.payload.event_type === step);
  return <section aria-label="Bước thực hiện đã ghi nhận">
    <h3>Bước thực hiện đã ghi nhận</h3>
    <p>Các bước Kho, Bán hàng và Vận chuyển đã ghi nhận vẫn được giữ lại khi sửa phương án. Hồ sơ chỉ hoàn tất khi dữ liệu xác nhận đã bàn giao đủ hàng cho đơn vị vận chuyển.</p>
    {!recorded.length && <p>Chưa tải được bước đã thực hiện cho đơn này. Bấm Tải các bước đã thực hiện để kiểm tra. Nếu đã thao tác ở bộ phận khác nhưng chưa thấy kết quả, cần kiểm tra lại ở bộ phận đó.</p>}
    <p>Chọn một ô để xem bằng chứng. Các ô là loại bản ghi, không phải các bước bắt buộc theo thứ tự; bàn giao đủ có thể không có bản ghi bàn giao một phần.</p>
    <InteractiveFlow connected={false} label="Sơ đồ bằng chứng bàn giao" onSelect={setStep} selected={step || undefined} nodes={Object.entries(labels).map(([id,title])=>{
      const count=recorded.filter(e=>e.payload.event_type===id).length;
      return {id,title,description:count?`${count} bản ghi · bấm để xem thời điểm`:'Chưa có bản ghi trong lịch sử đã tải',state:count?'recorded':'unknown'};
    })}/>
    <WorkspaceDialog open={!!step} onClose={()=>setStep(null)} title={labels[step || ''] || 'Bằng chứng thực hiện'} description="Chỉ hiển thị sự kiện đã tải và khớp đúng đơn. Không suy ra bước hoàn tất từ bước khác.">
      {selected.length?<ol className="iw-progress-events">{selected.map(event=><li key={event.id}>{new Date(event.payload.occurred_at || event.created_at).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'})}<p>Nguồn: {{warehouse:'Kho',sales:'Bán hàng',logistics:'Vận chuyển'}[event.payload.producer || ''] || 'Bộ phận ghi nhận'}</p></li>)}</ol>:<p>Chưa có bằng chứng được tải cho bước này. Kiểm tra tại bộ phận phụ trách hoặc tải lại các bước đã thực hiện.</p>}
    </WorkspaceDialog>
  </section>;
}
