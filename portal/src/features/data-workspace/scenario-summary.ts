// SPDX-License-Identifier: GPL-3.0-or-later
import type { Scenario } from './types';

const finite = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;

/** Saved/API/text views must use physical supply deficit, not end-of-day unmet. */
export function scenarioSummary(value: Scenario) {
  const canonical = (side: 'baseline' | 'proposed') => {
    const decision = value.decision?.[side === 'baseline' ? 'stressed' : 'proposed'];
    if (decision) return finite(decision.peak_shortage);
    const metrics = value[side].metrics;
    if (finite(metrics.peak_supply_shortage) !== null) return finite(metrics.peak_supply_shortage);
    // Only explicitly versioned daily projections may label the daily metric as supply deficit.
    if (value.evidence.metric_semantics_version === 'physical_supply_deficit_v1' && value.evidence.shortage_basis === 'day_end_supply') return finite(metrics.peak_shortage);
    return null;
  };
  const before = canonical('baseline'), after = canonical('proposed');
  const first = value.decision?.proposed.first_shortage_date;
  return {
    before, after,
    firstShortageDate: first === undefined ? null : first,
    unit: typeof value.evidence.unit === 'string' ? value.evidence.unit : null,
    legacy: before === null || after === null,
  };
}
