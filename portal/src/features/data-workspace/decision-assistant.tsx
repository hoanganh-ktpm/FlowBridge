// SPDX-License-Identifier: GPL-3.0-or-later
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { RunResult } from '@/features/results/run-result';
import type { RunResult as Result } from '@/lib/types';
import { ApiError } from '@/lib/api';
import type { DecisionRequest } from './decision-dossiers';

const choices = [ ['evidence', 'Vì sao cần phối hợp?'], ['comparison', 'So sánh trước và sau'], ['tasks', 'Đang chờ ai?'], ['outcome', 'Đã bàn giao chưa?'] ] as const;
export function DecisionAssistant({ request, caseId, revisionId, onDenied }: { request: DecisionRequest; caseId: string; revisionId: string; onDenied: (e: unknown) => void }) {
  const [result, setResult] = useState<Result | null>(null), [busy, setBusy] = useState(false), [question, setQuestion] = useState(''), [error, setError] = useState('');
  async function ask(intent?: string) {
    setBusy(true); setError(''); setResult(null);
    try {
      setResult(await request<Result>('/decisions/' + caseId + '/assistant', { revision_id: revisionId, ...(intent ? { intent } : { question: question.trim() }) }));
    } catch (e) {
      setResult(null);
      if (e instanceof ApiError && [401,403].includes(e.status)) onDenied(e);
      else setError('Chưa đọc được bằng chứng hoặc phiên bản đã đổi. Tải lại hồ sơ và thử lại; không dùng số liệu cũ để kết luận.');
    } finally { setBusy(false); }
  }
  return <section aria-label="Trợ lý hồ sơ">
    <h3>Hỏi dữ liệu hồ sơ</h3>
    <p>Chọn một câu hỏi gợi ý hoặc hỏi về lý do thiếu hàng, so sánh phương án, người đang xử lý hay kết quả bàn giao. Câu trả lời dùng số liệu đã lưu của phiên bản đang mở.</p>
    <div className="dw-actions">{choices.map(([intent, label]) => <Button key={intent} variant="outline" disabled={busy} onClick={() => void ask(intent)}>{label}</Button>)}</div>
    <form className="dw-actions" onSubmit={event => { event.preventDefault(); if (question.trim()) void ask(); }}>
      <Input aria-label="Câu hỏi về hồ sơ" maxLength={500} value={question} disabled={busy} onChange={event => setQuestion(event.target.value)} placeholder="Ví dụ: Hồ sơ đang chờ ai?"/>
      <Button type="submit" disabled={busy || !question.trim()}>Hỏi dữ liệu</Button>
    </form>
    {busy && <p role="status">Đang đọc bằng chứng được cấp quyền…</p>}{error && <p role="alert">{error}</p>}
    {result && <RunResult result={result}/>}
  </section>;
}
