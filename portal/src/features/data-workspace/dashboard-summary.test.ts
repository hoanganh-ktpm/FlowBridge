// SPDX-License-Identifier: GPL-3.0-or-later
import { expect, it } from 'vitest';
import { priorityOrders } from './dashboard-summary';
import type { PlanOrder } from './planning-types';

it('prioritizes the nearest handoff and treats an unconfirmed schedule as needing review', () => {
  const orders = [
    { order_id: 'later', deadline: '2026-10-27T10:00:00+07:00', at_risk: true, schedule_status: 'scheduled' },
    { order_id: 'early', deadline: '2026-10-27T08:00:00+07:00', at_risk: false, schedule_status: 'unconfirmed' },
    { order_id: 'clear', deadline: '2026-10-27T07:00:00+07:00', at_risk: false, schedule_status: 'scheduled' },
  ] as PlanOrder[];
  expect(priorityOrders(orders).map(order => order.order_id)).toEqual(['early', 'later']);
  expect(orders[0].order_id).toBe('later');
  expect(priorityOrders(orders, 1)).toHaveLength(1);
});
