// SPDX-License-Identifier: GPL-3.0-or-later
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Field, Select, fmt, unitLabel } from './components';

export interface Proposal { quantity: number; arrival: string; arrivalTime?:string; multiplier: number; delay: number; quantile: string }
export function ScenarioAssumptions({ value, bases, unit, disabled, onApply }: {
  value: Proposal; bases: {p50: number;p90: number}; unit: string; disabled: boolean; onApply: (v: Proposal) => void;
}) {
  const [open, setOpen] = useState(false), [draft, setDraft] = useState(value);
  const base = draft.quantile === 'p90' ? bases.p90 : bases.p50;
  const valid = Number.isFinite(draft.multiplier) && draft.multiplier >= 0 && draft.multiplier <= 500
    && Number.isInteger(draft.delay) && draft.delay >= 0 && draft.delay <= 90;
  return <div className="pw-assumptions-control"><Sheet open={open} onOpenChange={next => { if (next) setDraft(value); setOpen(next); }}>
    <SheetTrigger asChild><Button type="button" variant="outline" disabled={disabled}>Điều chỉnh giả định</Button></SheetTrigger>
    <p className="dw-muted">Nhu cầu {value.multiplier === 100 ? 'giữ nguyên' : `${value.multiplier > 100 ? 'tăng' : 'giảm'} ${fmt(Math.abs(value.multiplier - 100))}%`} · hàng về trễ thêm {value.delay} ngày.</p>
    <SheetContent className="pw-settings-sheet w-full sm:max-w-md" showCloseButton={false}>
      <SheetHeader><SheetTitle>Giả định nhu cầu và hàng về</SheetTitle><SheetDescription>Điều chỉnh để thử phương án. Chỉ tính lại khi bấm So sánh phương án.</SheetDescription></SheetHeader>
      <form className="pw-settings-form" onSubmit={e => { e.preventDefault(); if (valid) { onApply({ ...value, multiplier: draft.multiplier, delay: draft.delay, quantile: draft.quantile }); setOpen(false); } }}>
        <div className="pw-settings-fields">
          <Field title="Mức nhu cầu để chuẩn bị"><Select value={draft.quantile} onChange={quantile => setDraft(v => ({ ...v, quantile }))}><option value="p50">Theo lượng dự kiến</option><option value="p90">Chuẩn bị cho mức đặt cao hơn</option></Select></Field>
          <p className="dw-muted">Mức cao hơn lấy từ lịch sử. Thực tế vẫn có thể vượt mức này.</p>
          <Field title="Giả sử lượng khách đặt tăng/giảm (%)"><Input type="number" min={-100} max={400} value={draft.multiplier - 100} onChange={e => setDraft(v => ({ ...v, multiplier: 100 + Number(e.target.value) }))}/></Field>
          <div className="pw-assumption-example"><strong>{draft.multiplier === 100 ? 'Giữ nguyên lượng dự kiến' : `${draft.multiplier > 100 ? 'Tăng' : 'Giảm'} ${fmt(Math.abs(draft.multiplier - 100))}% lượng khách đặt`}</strong><p>Ví dụ: dự kiến 100, tăng 20% thì tính với 120 sản phẩm.</p><p>Trong kỳ này: {fmt(base)} → <strong>{fmt(base * draft.multiplier / 100)} {unitLabel(unit)}</strong>.</p><p>Chỉ đổi lượng đặt mới dự kiến, không đổi đơn đã xác nhận.</p></div>
          <Field title="Hàng đang chờ về trễ thêm (ngày)"><Input type="number" min={0} max={90} step={1} value={draft.delay} onChange={e => setDraft(v => ({ ...v, delay: Number(e.target.value) }))}/></Field>
          <p className="dw-muted">Lùi ngày về của kế hoạch hiện có. Không đổi ngày hàng nhập bổ sung.</p>
        </div>
        <SheetFooter><Button type="submit" disabled={!valid}>Áp dụng giả định</Button><Button type="button" variant="outline" onClick={() => setOpen(false)}>Hủy</Button></SheetFooter>
      </form>
    </SheetContent>
  </Sheet></div>;
}
