// SPDX-License-Identifier: GPL-3.0-or-later
import { expect, it } from 'vitest';
import { conditionCounts, deadlineBuckets } from './order-chart-data';
import type { OrderAnalysisData, OrderAssessment } from './warehouse-overview-types';
const row = (id: string, deadline: string, classification: string, conditions = {}) => ({ order_id: id, deadline, classification, readiness: { conditions } }) as OrderAssessment;
it('counts an order once at its local handoff date, including already overdue today', () => {
  const data = { start:'2026-10-26',end:'2026-11-01',horizon:7,as_of:'2026-10-26T10:00:00+07:00',orders:[
    row('old','2026-10-26T02:00:00Z','blocked'), // 09:00 local, already overdue.
    row('today','2026-10-26T17:00:00+07:00','unknown'),
    row('tomorrow','2026-10-26T18:30:00Z','ready'), // 01:30 next local day.
  ] } as OrderAnalysisData;
  const buckets=deadlineBuckets(data);
  expect(buckets[0].key).toBe('overdue'); expect(buckets[0].blocked).toBe(1);
  expect(buckets[1].unknown).toBe(1); expect(buckets[2].ready).toBe(1);
  expect(buckets.reduce((n,b)=>n+b.blocked+b.unknown+b.ready,0)).toBe(3);
});
it('groups long horizons into bounded weeks and counts overlapping conditions separately', () => {
  const order=row('A','2026-11-01T10:00:00+07:00','blocked',{supply:'not_met',schedule:'not_met',preparation:'met',capacity:'unknown'});
  const data={start:'2026-10-26',end:'2027-01-23',horizon:90,as_of:'2026-10-26T10:00:00+07:00',orders:[order]} as OrderAnalysisData;
  const buckets=deadlineBuckets(data);
  expect(buckets).toHaveLength(13); expect(buckets[0].blocked).toBe(1);
  const counts=conditionCounts([order]);
  expect(counts.find(c=>c.key==='supply')?.blocked).toBe(1);
  expect(counts.find(c=>c.key==='schedule')?.blocked).toBe(1);
  expect(counts.find(c=>c.key==='capacity')?.unknown).toBe(1);
});
