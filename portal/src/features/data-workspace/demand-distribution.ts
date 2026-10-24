// SPDX-License-Identifier: GPL-3.0-or-later
export function demandDistribution(values: number[]) {
  if (!values.length || values.some(v => !Number.isFinite(v) || v < 0)) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const low = sorted[0], high = sorted.at(-1)!;
  const niceStep = (value: number) => {
    const scale = 10 ** Math.floor(Math.log10(value));
    return ([1, 2, 5, 10].find(factor => factor * scale >= value) || 10) * scale;
  };
  const target = Math.min(12, Math.ceil(Math.log2(sorted.length) + 1));
  let width = high === low ? 0 : niceStep((high - low) / target);
  if (width && sorted.every(Number.isInteger)) width = Math.max(1, width);
  let lower = width ? Math.floor(low / width) * width : low;
  let count = width ? Math.floor((high - lower) / width) + 1 : 1;
  while (count > 12) {
    width = niceStep(width * 1.01); lower = Math.floor(low / width) * width;
    count = Math.floor((high - lower) / width) + 1;
  }
  const bins = Array.from({ length: count }, (_, i) => ({
    index: i, lower: lower + i * width, upper: width ? lower + (i + 1) * width : high,
    last: i === count - 1, count: 0, cumulativePercent: 0,
  }));
  for (const value of sorted) bins[width === 0 ? 0 : Math.min(count - 1, Math.floor((value - lower) / width))].count++;
  let cumulative = 0;
  for (const bin of bins) { cumulative += bin.count; bin.cumulativePercent = 100 * cumulative / sorted.length; }
  const p50 = sorted[Math.ceil(sorted.length * .5) - 1];
  const p90 = sorted[Math.ceil(sorted.length * .9) - 1];
  return { bins, days: sorted.length, p50, p90, atMostP90: sorted.filter(v => v <= p90).length, zeroDays: sorted.filter(v => v === 0).length };
}
