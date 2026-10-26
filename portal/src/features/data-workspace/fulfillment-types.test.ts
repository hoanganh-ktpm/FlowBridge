// SPDX-License-Identifier: GPL-3.0-or-later
import { describe,it,expect } from 'vitest';
import { timelinePosition,normalizeInsight,timelineWindow,samePreparationTimes,preparationBasis,type FulfillmentOrder } from './fulfillment-types';
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
  const asOf='2026-10-26T18:22:00+07:00';
  const estimate={material_ready_at:asOf,preparation_complete_at:'2026-10-26T20:22:00+07:00',state:'late' as const,slack_hours:-3.3667};
  const order={deadline:'2026-10-26T17:00:00+07:00',base:estimate,high_demand:estimate,delayed:estimate,all_lines_held_or_dispatched:false} as FulfillmentOrder;
  it('keeps expired deadlines visible and spreads a few hours across the chart',()=>{
    const window=timelineWindow([order],asOf);
    expect(Date.parse(window.start)).toBeLessThan(Date.parse(order.deadline));
    expect(Date.parse(window.end)).toBeGreaterThan(Date.parse(estimate.preparation_complete_at));
    const deadline=timelinePosition(order.deadline,window.start,window.end)!;
    const ready=timelinePosition(estimate.preparation_complete_at,window.start,window.end)!;
    expect(deadline).toBeGreaterThan(0);expect(ready).toBeLessThan(100);expect(ready-deadline).toBeGreaterThan(60);
  });
  it('does not treat missing estimates as three equal preparation dates',()=>{
    expect(samePreparationTimes(order)).toBe(true);
    expect(samePreparationTimes({...order,delayed:{...estimate,preparation_complete_at:null}})).toBe(false);
    expect(samePreparationTimes({...order,delayed:{...estimate,preparation_complete_at:'2026-10-27T20:22:00+07:00'}})).toBe(false);
  });
  it('explains policy arithmetic without claiming held orders are already prepared',()=>{
    expect(preparationBasis(order,estimate,asOf)).toContain('giờ đọc cộng');
    expect(preparationBasis({...order,all_lines_held_or_dispatched:true},estimate,asOf)).toContain('Chưa xác nhận đơn đã soạn xong');
    expect(preparationBasis(order,{...estimate,material_ready_at:null},asOf)).toContain('Chưa tìm được');
  });
});
