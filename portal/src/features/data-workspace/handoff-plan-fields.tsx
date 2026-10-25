// SPDX-License-Identifier: GPL-3.0-or-later
import { Input } from '@/components/ui/input';
import { Field, Select } from './components';

export type CarrierPlan = { carrier_code: string; scheduled_handoff_at: string };
export type CarrierDraft = { carrier: string; at: string } | null;
export type CarrierOption = { carrier_code: string; carrier_name?: string | null };
export const validCarrierDraft = (value: CarrierDraft) => !value || !!(value.carrier && value.at);
export const carrierBody = (value: CarrierDraft): CarrierPlan | null => value ? { carrier_code: value.carrier, scheduled_handoff_at: value.at + ':00+07:00' } : null;
export function carrierDraft(value?: CarrierPlan | null): CarrierDraft {
  return value ? { carrier: value.carrier_code, at: new Date(new Date(value.scheduled_handoff_at).getTime() + 7 * 3600000).toISOString().slice(0, 16) } : null;
}
export function HandoffPlanFields({ value, options, onChange, disabled }: { value: CarrierDraft; options: CarrierOption[]; onChange: (value: CarrierDraft) => void; disabled: boolean }) {
  const carriers = new Map(options.map(item => [item.carrier_code, item.carrier_name || item.carrier_code]));
  if (value?.carrier && !carriers.has(value.carrier)) carriers.set(value.carrier, value.carrier);
  return <fieldset disabled={disabled} className="pw-saved-card">
    <legend>Lịch dự kiến bàn giao</legend>
    <label><input type="checkbox" checked={!!value} onChange={event => onChange(event.target.checked ? { carrier: '', at: '' } : null)}/> Chọn lịch để Kho và Vận chuyển xác minh trước khi đặt thật</label>
    {value && <>
      <Field title="Đơn vị vận chuyển dự kiến"><Select value={value.carrier} onChange={carrier => onChange({ ...value, carrier })}><option value="">Chọn đơn vị có lịch công bố</option>{[...carriers].map(([code, name]) => <option key={code} value={code}>{name}</option>)}</Select></Field>
      <Field title="Thời điểm bàn giao dự kiến (giờ Việt Nam)"><Input type="datetime-local" value={value.at} onChange={event => onChange({ ...value, at: event.target.value })}/></Field>
      <p>Đây là điều kiện của phương án, chưa giữ chỗ hay tạo vận đơn. Hệ thống đọc lại năng lực công bố; sau khi duyệt, đặt lịch thật vẫn phải kiểm tra chỗ còn lại.</p>
    </>}
    {!value && <p>Nếu không chọn lịch dự kiến, hồ sơ cần có lịch đã được xác nhận trong nguồn để xác minh điều kiện Vận chuyển.</p>}
  </fieldset>;
}
