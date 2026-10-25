// SPDX-License-Identifier: GPL-3.0-or-later
import type { PlanOrder } from './planning-types';

/** Only supplied facts; no LLM, quantity total across units, or invented risk score. */
export function priorityOrders(orders: PlanOrder[], limit = 3) {
  return orders.filter(order => order.at_risk || ['late', 'unconfirmed', 'unbooked', 'unknown'].includes(order.schedule_status))
    .slice().sort((a, b) => Date.parse(a.deadline) - Date.parse(b.deadline) || a.order_id.localeCompare(b.order_id))
    .slice(0, limit);
}
