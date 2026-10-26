// SPDX-License-Identifier: GPL-3.0-or-later
import { describe,it,expect } from 'vitest';
import { timelinePosition,normalizeInsight } from './fulfillment-types';
describe('order insight chart coordinates',()=>{
  it('bounds dates without turning unknown dates into ready now',()=>{
    expect(timelinePosition(null,'2026-10-26','2026-11-02')).toBeNull();
    expect(timelinePosition('invalid','2026-10-26','2026-11-02')).toBeNull();
    expect(timelinePosition('2026-10-25','2026-10-26','2026-11-02')).toBe(0);
    expect(timelinePosition('2026-11-05','2026-10-26','2026-11-02')).toBe(100);
    expect(timelinePosition('2026-10-29T12:00:00Z','2026-10-26','2026-11-02')).toBe(50);
  });
  it('finds Vietnamese product names with plain text',()=>{
    expect(normalizeInsight('Đơn Bàn phím')).toBe('don ban phim');
  });
});
