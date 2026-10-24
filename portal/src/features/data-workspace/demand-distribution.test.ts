// SPDX-License-Identifier: GPL-3.0-or-later
import { describe, expect, it } from 'vitest';
import { demandDistribution } from './demand-distribution';

describe('historical daily demand distribution', () => {
  it('preserves every day including zeros and the maximum at the last boundary', () => {
    const values = Array.from({ length: 30 }, (_, i) => i);
    const result = demandDistribution(values)!;
    expect(result.bins.reduce((n, b) => n + b.count, 0)).toBe(30);
    expect(result.bins.at(-1)!.cumulativePercent).toBe(100);
    expect(result.p50).toBe(14); expect(result.p90).toBe(26);
    expect(result.atMostP90).toBe(27); expect(result.zeroDays).toBe(1);
    expect(values[0]).toBe(0); // input is not sorted/mutated in place
  });
  it('keeps constant and intermittent series rather than inventing a normal curve', () => {
    const constant = demandDistribution(Array(28).fill(0))!;
    expect(constant.bins).toHaveLength(1); expect(constant.bins[0].count).toBe(28);
    expect(constant.p90).toBe(0); expect(constant.atMostP90).toBe(28);
    const intermittent = demandDistribution([...Array(27).fill(0), 120])!;
    expect(intermittent.p90).toBe(0); expect(intermittent.atMostP90).toBe(27);
    expect(intermittent.bins.at(-1)!.count).toBe(1);
  });
  it('rejects unknown/invalid measures instead of silently replacing them by zero', () => {
    expect(demandDistribution([])).toBeNull(); expect(demandDistribution([1, NaN])).toBeNull();
    expect(demandDistribution([1, Infinity])).toBeNull(); expect(demandDistribution([-1, 2])).toBeNull();
  });
  it('uses readable equal-width groups and keeps distinct integer levels separate', () => {
    const result = demandDistribution([...Array(14).fill(0), ...Array(14).fill(1)])!;
    expect(result.bins.map(b => b.count)).toEqual([14, 14]);
    expect(result.bins.every(b => Number.isInteger(b.lower) && Number.isInteger(b.upper))).toBe(true);
    const wide = demandDistribution(Array.from({ length: 365 }, (_, i) => i * 7))!;
    expect(wide.bins.length).toBeLessThanOrEqual(12);
    expect(wide.bins.reduce((n, b) => n + b.count, 0)).toBe(365);
  });
});
