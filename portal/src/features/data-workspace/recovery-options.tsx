// SPDX-License-Identifier: GPL-3.0-or-later
import { Button } from '@/components/ui/button';
import { recoveryFill } from './recovery-view';
import { fmt, unitLabel } from './components';
import type { RecoveryOption } from './types';

export function RecoveryOptions({ options, disabled, onFill }: {
  options:RecoveryOption[];disabled:boolean;onFill:(quantity:number,arrival:string,time?:string)=>void;
}) {
  if (!options.length) return null;
  return <section aria-label="Gợi ý phục hồi phương án" className="space-y-3">
    <h3>Hướng xử lý cần xác minh</h3>
    {options.map((option,index) => {
      const fill = recoveryFill(option);
      return <div className="rounded-lg border p-3 space-y-2" key={`${option.kind}-${index}`}>
        <strong>{option.title}</strong>
        {option.quantity !== undefined && <p>{fmt(option.quantity)} {unitLabel(option.unit)}{option.arrival_at ? ` trước ${new Date(option.arrival_at).toLocaleString('vi-VN', { timeZone:'Asia/Ho_Chi_Minh' })} (giờ Việt Nam)` : ''}</p>}
        {option.projected_shortage !== undefined && <p>Theo mô phỏng: còn thiếu {fmt(option.projected_shortage)} {unitLabel(option.unit)}; {fmt(option.remaining_at_risk_orders)} đơn còn nguy cơ.</p>}
        {option.suggested_handoff_at && <p>Mốc bàn giao tham khảo: {new Date(option.suggested_handoff_at).toLocaleString('vi-VN', { timeZone:'Asia/Ho_Chi_Minh' })}. Cần xác nhận lại cam kết và lịch nhận.</p>}
        <ul>{option.requires_confirmation.map(item => <li key={item}>{item}</li>)}</ul>
        {option.assumptions.map(item => <p className="dw-muted" key={item}>{item}</p>)}
        {fill && <Button variant="outline" disabled={disabled} onClick={() => onFill(fill.quantity,fill.arrival,fill.arrivalTime)}>Điền phương án thử</Button>}
      </div>;
    })}
  </section>;
}
