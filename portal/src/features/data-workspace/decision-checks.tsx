// SPDX-License-Identifier: GPL-3.0-or-later
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api';
import type { DecisionRequest } from './decision-dossiers';

type Impact = { classification: string; requires_revision: boolean; diff: { path: string; before: unknown; after: unknown }[]; observed_changes?: { dependency: string }[] };
type Check = { id: string; created_at: string; outcome: string; payload: { impact?: Impact; warehouse_confirmable?: boolean; logistics_confirmable?: boolean } };
const names: Record<string, string> = { original_order: 'Nội dung đơn gốc', forecast_history: 'Lịch sử dùng dự báo', preparation_minutes: 'Thời gian chuẩn bị (phút)', plan: 'Khả năng đủ hàng trước hạn', schedule: 'Lịch nhận của đơn vị vận chuyển', warehouse_confirmable: 'Điều kiện Kho', logistics_confirmable: 'Điều kiện Vận chuyển', evidence_version: 'Phiên bản bằng chứng' };
const outcomes: Record<string, string> = { VALID: 'Đã kiểm tra', STALE: 'Cần tính và xác minh lại', DENIED: 'Quyền đã thay đổi', UNAVAILABLE: 'Chưa đọc được nguồn' };
function describe(path: string, value: unknown): string {
  if (value === null || value === undefined) return 'Chưa có bằng chứng';
  if (typeof value === 'boolean') return value ? 'Đáp ứng' : 'Chưa đáp ứng';
  if (path === 'forecast_history' || path === 'evidence_version') return 'Bằng chứng cần được tính và xác minh lại';
  if (path === 'preparation_minutes' && typeof value === 'number') return `${value} phút`;
  if (path === 'original_order' && Array.isArray(value)) return value.map(row => `${row.sku} · ${row.ordered_quantity} ${row.uom} · hạn ${new Date(row.requested_handoff_at).toLocaleString('vi-VN')}`).join('; ');
  if (path === 'schedule' && Array.isArray(value)) return value.length ? value.map(row => `${row.carrier_code} · ${new Date(row.scheduled_handoff_at).toLocaleString('vi-VN')} · ${row.status === 'ACCEPTED' ? 'đã xác nhận lịch' : 'chưa xác nhận lịch'}`).join('; ') : 'Chưa có lịch được xác nhận';
  if (path === 'plan' && typeof value === 'object') {
    const plan = value as { at_risk?: boolean; already_overdue?: boolean; shortages_at_deadline?: { sku?: string; quantity?: number }[] };
    return `${plan.at_risk ? 'Có nguy cơ không đáp ứng trước hạn' : 'Đáp ứng điều kiện hàng theo phương án'}${plan.already_overdue ? '; đã qua hạn chuẩn bị' : ''}`;
  }
  return 'Nội dung phương án đã thay đổi';
}

export function DecisionChecks({ request, caseId, disabled, onDenied, onSynced }: { request: DecisionRequest; caseId: string; disabled: boolean; onDenied: (e: unknown) => void; onSynced: () => Promise<void> }) {
  const [checks, setChecks] = useState<Check[]>([]), [busy, setBusy] = useState(false), [notice, setNotice] = useState('');
  async function run(operation: 'revalidate' | 'sync-events' | 'checks') {
    setBusy(true); setNotice('');
    const prefix = '/decisions/' + caseId;
    try {
      if (operation === 'sync-events') {
        const result = await request<{ new_observations: number; warnings: string[] }>(prefix + '/sync-events', {});
        await onSynced();
        setNotice(`Đã tải ${result.new_observations} bước mới từ các bộ phận. ${result.warnings.length ? 'Chưa tải đủ lịch sử; cần kiểm tra lại. ' : ''}Lịch sử giúp biết ai đã làm bước nào. Bấm Kiểm tra dữ liệu hiện tại để kiểm tra lại điều kiện của phương án.`);
      } else {
        if (operation === 'revalidate') await request(prefix + '/revalidate', {});
        const result = await request<{ items: Check[]; has_more: boolean }>(prefix + '/checks');
        setChecks(result.items);
        if (result.has_more) setNotice('Đang hiển thị 100 lần kiểm tra gần nhất của phiên bản này.');
      }
    } catch (e) {
      if (e instanceof ApiError && [401, 403].includes(e.status)) { setChecks([]); onDenied(e); }
      else if (e instanceof ApiError && e.code === 'DECISION_REQUIRES_REVISION') {
        setNotice('Điều kiện hoặc nội dung phương án đã đổi. Tính phương án mới, trả lại hồ sơ và xác minh, duyệt phiên bản mới; bước đã thực hiện vẫn được giữ.');
        try { const result = await request<{ items: Check[] }>(prefix + '/checks'); setChecks(result.items); }
        catch (readError) { setChecks([]); onDenied(readError); }
      } else setNotice('Chưa xác minh được dữ liệu hiện tại. Không thể coi phương án là còn hiệu lực; kiểm tra dịch vụ và quyền nguồn rồi thử lại.');
    } finally { setBusy(false); }
  }
  return <section aria-label="Phương án còn phù hợp không?">
    <h3>Phương án còn phù hợp không?</h3>
    <p>Kiểm tra lại dữ liệu nguồn để biết phương án còn dùng được hay cần lập phiên bản mới. Việc kiểm tra không tự duyệt hoặc thực hiện giao dịch.</p>
    <div className="dw-actions">
      <Button variant="outline" disabled={disabled || busy} onClick={() => void run('revalidate')}>Kiểm tra dữ liệu hiện tại</Button>
      <Button variant="outline" disabled={disabled || busy} onClick={() => void run('checks')}>Xem lịch sử kiểm tra</Button>
      <Button variant="outline" disabled={disabled || busy} onClick={() => void run('sync-events')}>Tải các bước đã thực hiện</Button>
    </div>
    {notice && <p role="status">{notice}</p>}
    {checks.map(check => <details key={check.id} className="pw-saved-card">
      <summary>{new Date(check.created_at).toLocaleString('vi-VN')} · {outcomes[check.outcome] || 'Chưa xác nhận'}{check.payload.impact?.classification === 'NO_EFFECT' ? ' · Có thay đổi nhưng điều kiện và nội dung phương án giữ nguyên' : ''}</summary>
      {check.payload.impact?.diff.map(diff => <div key={diff.path}><p>{names[diff.path] || 'Bằng chứng phương án'} đã thay đổi.</p><p>Trước: {describe(diff.path, diff.before)}.</p><p>Sau: {describe(diff.path, diff.after)}.</p></div>)}
      {check.payload.warehouse_confirmable !== undefined && <p>Kho: {check.payload.warehouse_confirmable ? 'đáp ứng điều kiện tại lần kiểm tra' : 'chưa đáp ứng điều kiện'}. Vận chuyển: {check.payload.logistics_confirmable ? 'đáp ứng điều kiện tại lần kiểm tra' : 'chưa đáp ứng điều kiện'}.</p>}
    </details>)}
  </section>;
}
