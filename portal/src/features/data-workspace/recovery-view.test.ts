// SPDX-License-Identifier: GPL-3.0-or-later
import { expect,it } from 'vitest';
import { recoveryFill } from './recovery-view';
import type { RecoveryOption } from './types';

const option:RecoveryOption={kind:'additional_supply',title:'Bổ sung trước hạn',quantity:30,
  arrival_at:'2026-10-27T01:00:00+00:00',can_fill_simulation:true,requires_confirmation:['Kho'],assumptions:[]};
it('converts an exact UTC cutoff into the business date and time without executing an action',()=>{
  expect(recoveryFill(option)).toEqual({quantity:30,arrival:'2026-10-27',arrivalTime:'08:00'});
});
it('never prefills a commitment change or a malformed/unknown supply option',()=>{
  for(const value of [{...option,kind:'review_commitment'}, {...option,quantity:NaN},
    {...option,arrival_at:'2026-10-27T08:00:00'}, {...option,can_fill_simulation:false}]) {
    expect(recoveryFill(value as RecoveryOption)).toBeNull();
  }
});
