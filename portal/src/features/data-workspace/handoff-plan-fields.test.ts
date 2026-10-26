// SPDX-License-Identifier: GPL-3.0-or-later
import { describe,it,expect } from 'vitest';
import { validCarrierDraft } from './handoff-plan-fields';
describe('optional handoff schedule',()=>{
  it('allows using an existing confirmed schedule without supplying a draft',()=>{
    expect(validCarrierDraft(null)).toBe(true);
    expect(validCarrierDraft({carrier:'C1',at:'2026-10-27T10:00'})).toBe(true);
  });
  it('rejects an incomplete schedule and impossible calendar/time values',()=>{
    for(const draft of [{carrier:'',at:'2026-10-27T10:00'},{carrier:'C1',at:''},{carrier:'C1',at:'2026-02-30T10:00'},{carrier:'C1',at:'2026-10-27T25:10'}])expect(validCarrierDraft(draft)).toBe(false);
  });
});
