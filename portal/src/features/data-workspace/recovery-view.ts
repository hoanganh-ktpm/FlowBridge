// SPDX-License-Identifier: GPL-3.0-or-later
import type { RecoveryOption } from './types';

export function recoveryFill(option: RecoveryOption) {
  if (!option.can_fill_simulation || option.kind !== 'additional_supply' ||
      typeof option.quantity !== 'number' || !Number.isFinite(option.quantity) ||
      option.quantity <= 0 || option.quantity > 1e9 || !option.arrival_at ||
      !/(Z|[+-]\d{2}:\d{2})$/.test(option.arrival_at)) return null;
  const at = new Date(option.arrival_at);
  if (!Number.isFinite(at.getTime())) return null;
  return { quantity: option.quantity,
    arrival: new Intl.DateTimeFormat('sv-SE', { timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit',day:'2-digit' }).format(at),
    arrivalTime: new Intl.DateTimeFormat('sv-SE', { timeZone:'Asia/Ho_Chi_Minh',hour:'2-digit',minute:'2-digit',hourCycle:'h23' }).format(at) };
}
