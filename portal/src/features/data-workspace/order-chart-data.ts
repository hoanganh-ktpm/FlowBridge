// SPDX-License-Identifier: GPL-3.0-or-later
import type { OrderAnalysisData, OrderAssessment } from './warehouse-overview-types';

export const conditionLabels = { supply: 'Hàng trong Kho', preparation: 'Thời gian chuẩn bị', schedule: 'Lịch lấy hàng', capacity: 'Lượt nhận của nhà vận chuyển' };
export const assessmentLabels = { blocked: 'Có vướng mắc', unknown: 'Chưa xác minh đủ', ready: 'Đáp ứng theo kế hoạch' };
export const conditionNames: Record<string, string> = { met: 'Đáp ứng', not_met: 'Có vướng mắc', unknown: 'Chưa xác minh' };
const dateKey = (value: string) => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Ho_Chi_Minh' }).format(new Date(value));
const plus = (value: string, count: number) => new Date(Date.parse(value + 'T00:00:00Z') + count * 86400000).toISOString().slice(0, 10);
export type DeadlineBucket = { key: string; start: string; end: string; orders: OrderAssessment[]; blocked: number; unknown: number; ready: number };
export function deadlineBuckets(data: OrderAnalysisData): DeadlineBucket[] {
  const width = data.horizon > 14 ? 7 : 1;
  const rows: DeadlineBucket[] = [];
  const make = (key: string, start = key, end = key): DeadlineBucket => ({ key, start, end, orders: [], blocked: 0, unknown: 0, ready: 0 });
  const overdue = make('overdue');
  for (let i = 0; i < data.horizon; i += width) {
    const start = plus(data.start, i), end = plus(data.start, Math.min(data.horizon - 1, i + width - 1));
    rows.push(make(start, start, end));
  }
  for (const order of data.orders) {
    const key = dateKey(order.deadline);
    const bucket = Date.parse(order.deadline) < Date.parse(data.as_of) ? overdue : rows.find(row => key >= row.start && key <= row.end);
    if (!bucket) continue;
    bucket.orders.push(order); bucket[order.classification]++;
  }
  return overdue.orders.length ? [overdue, ...rows] : rows;
}
export function conditionCounts(orders: OrderAssessment[]) {
  return Object.keys(conditionLabels).map(key => ({ key, blocked: orders.filter(o => o.readiness?.conditions[key] === 'not_met').length, unknown: orders.filter(o => !o.readiness?.conditions[key] || o.readiness.conditions[key] === 'unknown').length }));
}
