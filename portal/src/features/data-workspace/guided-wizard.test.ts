// SPDX-License-Identifier: GPL-3.0-or-later
import { describe,it,expect } from 'vitest';
import { enterableStep } from './guided-wizard';
describe('dependent workflow steps',()=>{
  const steps=[{id:'source',signature:'warehouse-A',valid:true},{id:'quantity',signature:'20',valid:true},{id:'review',signature:'ready',valid:true}];
  it('requires explicit completion even when default inputs are valid',()=>{
    expect(enterableStep(steps,{},0)).toBe(true);
    expect(enterableStep(steps,{},1)).toBe(false);
    expect(enterableStep(steps,{source:'warehouse-A'},2)).toBe(false);
    expect(enterableStep(steps,{source:'warehouse-A',quantity:'20'},2)).toBe(true);
  });
  it('locks downstream steps after an upstream edit, including a valid edit',()=>{
    const confirmed={source:'warehouse-A',quantity:'20'};
    expect(enterableStep([{...steps[0],signature:'warehouse-B'},...steps.slice(1)],confirmed,2)).toBe(false);
    expect(enterableStep([steps[0],{...steps[1],valid:false},steps[2]],confirmed,2)).toBe(false);
    expect(enterableStep(steps,confirmed,3)).toBe(false);
  });
});
