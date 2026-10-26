// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { InteractiveFlow } from './interactive-workspace';

export type WizardStep = { id: string; title: string; description: string; signature: string; valid: boolean; issue?: string; content: ReactNode };
export function enterableStep(steps: Pick<WizardStep, 'id' | 'valid' | 'signature'>[], confirmed: Record<string,string>, index: number) {
  return index >= 0 && index < steps.length && steps.slice(0,index).every(step=>step.valid && confirmed[step.id]===step.signature);
}
export function GuidedWizard({ label, steps, busy=false, finishLabel, onFinish }: {
  label: string; steps: WizardStep[]; busy?: boolean; finishLabel: string; onFinish: ()=>void;
}) {
  const [index,setIndex]=useState(0),[confirmed,setConfirmed]=useState<Record<string,string>>({});
  const active=enterableStep(steps,confirmed,index)?index:Math.max(0,steps.findIndex((_,i)=>!enterableStep(steps,confirmed,i))-1);
  const current=steps[active], last=active===steps.length-1;
  const heading=useRef<HTMLHeadingElement|null>(null);
  useEffect(()=>{heading.current?.focus();},[active]);
  function next(){
    if(busy||!current.valid||!enterableStep(steps,confirmed,active))return;
    if(last){onFinish();return;}
    setConfirmed(previous=>({...Object.fromEntries(steps.slice(0,active).map(s=>[s.id,previous[s.id]])),[current.id]:current.signature}));
    setIndex(active+1);
  }
  return <section className="gw-wizard" aria-label={label}>
    <InteractiveFlow label={label+' · tiến độ'} selected={current.id} onSelect={id=>{
      const target=steps.findIndex(s=>s.id===id);if(!busy&&enterableStep(steps,confirmed,target))setIndex(target);
    }} nodes={steps.map((s,i)=>({id:s.id,title:s.title,description:s.description,disabled:busy||!enterableStep(steps,confirmed,i),state:confirmed[s.id]===s.signature&&s.valid?'recorded':'unknown'}))}/>
    <div className="gw-step" key={current.id}><h3 ref={heading} tabIndex={-1}>Bước {active+1}/{steps.length}: {current.title}</h3>{current.content}</div>
    {!current.valid&&current.issue&&<p className="gw-issue" role="status">{current.issue}</p>}
    <footer className="gw-footer"><span>{last?'Kiểm tra thông tin trước khi xác nhận.':'Hoàn tất bước này để mở bước tiếp theo.'}</span><div>
      {active>0&&<Button type="button" variant="outline" disabled={busy} onClick={()=>setIndex(active-1)}>Bước trước</Button>}
      <Button type="button" disabled={busy||!current.valid} onClick={next}>{busy?'Đang xử lý…':last?finishLabel:'Tiếp tục'}</Button>
    </div></footer>
  </section>;
}
