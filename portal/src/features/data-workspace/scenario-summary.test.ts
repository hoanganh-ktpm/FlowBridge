// SPDX-License-Identifier: GPL-3.0-or-later
import { describe, expect, it } from 'vitest';
import type { Scenario } from './types';
import { scenarioSummary } from './scenario-summary';

const old = { baseline: { metrics: { peak_shortage: 0 } }, proposed: { metrics: { peak_shortage: 0 } }, evidence: { unit: 'EA' } } as unknown as Scenario;

describe('canonical saved scenario measures', () => {
  it('keeps a missed morning shortage even when both day-end values are zero', () => {
    const value = { ...old, decision: { stressed: { peak_shortage: 30 }, proposed: { peak_shortage: 0, first_shortage_date: null } } } as Scenario;
    expect(scenarioSummary(value)).toEqual({ before: 30, after: 0, firstShortageDate: null, unit: 'EA', legacy: false });
  });
  it('never relabels ambiguous old unmet/order-blocked quantities as physical shortage', () => {
    expect(scenarioSummary(old)).toMatchObject({ before: null, after: null, legacy: true });
  });
  it('keeps physical zero for a SKU blocked only by another SKU', () => {
    const value = { ...old, baseline: { metrics: { peak_shortage: 20, peak_supply_shortage: 0 } }, proposed: { metrics: { peak_shortage: 20, peak_supply_shortage: 0 } } } as Scenario;
    expect(scenarioSummary(value)).toMatchObject({ before: 0, after: 0, legacy: false });
  });
  it('allows explicitly versioned forecast-only day-end deficit', () => {
    const value = { ...old, evidence: { metric_semantics_version: 'physical_supply_deficit_v1', shortage_basis: 'day_end_supply' } };
    expect(scenarioSummary(value)).toMatchObject({ before: 0, after: 0, unit: null, legacy: false });
  });
  it('does not coerce null/non-finite data to zero', () => {
    const value = { ...old, decision: { stressed: { peak_shortage: null }, proposed: { peak_shortage: Number.NaN } } } as unknown as Scenario;
    expect(scenarioSummary(value)).toMatchObject({ before: null, after: null, legacy: true });
  });
});
