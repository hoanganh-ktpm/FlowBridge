// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef, useState, type ComponentProps, type ReactNode } from 'react';
import { ArrowRight, CheckCircle2, Circle, Expand, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { workspaceStaleEvent } from '@/lib/workspace-cache';
import './interactive-workspace.css';

export interface FlowNode {
  id: string; title: string; description: string; value?: string; state?: 'recorded' | 'attention' | 'unknown';
  disabled?: boolean;
}
export function WorkspaceModal(props: ComponentProps<typeof Dialog>) {
  useEffect(()=>{
    const close=()=>{if(props.open)props.onOpenChange?.(false);};
    window.addEventListener(workspaceStaleEvent,close);
    return()=>window.removeEventListener(workspaceStaleEvent,close);
  },[props.open,props.onOpenChange]);
  return <Dialog {...props}/>;
}
export function InteractiveFlow({ label, nodes, selected, onSelect, connected = true }: {
  label: string; nodes: FlowNode[]; selected?: string; onSelect: (id: string) => void; connected?: boolean;
}) {
  return <nav className="iw-flow" aria-label={label}><ol>{nodes.map((node, index) => <li key={node.id}>
    {connected && index > 0 && <ArrowRight className="iw-connector" size={18} aria-hidden="true"/>}
    <button type="button" aria-label={node.title} aria-current={selected === node.id ? 'step' : undefined} disabled={node.disabled} className={`iw-node iw-node-${node.state || 'unknown'}${selected === node.id ? ' iw-node-selected' : ''}`} onClick={() => onSelect(node.id)}>
      <span className="iw-node-top"><span className="iw-node-index">{node.state === 'recorded' ? <CheckCircle2 size={17}/> : <Circle size={17}/>}<small>{connected ? index + 1 : ''}</small></span><strong>{node.title}</strong><ArrowRight size={14}/></span>
      {node.value && <b className="iw-node-value">{node.value}</b>}
      <span className="iw-node-description">{node.description}</span>
    </button>
  </li>)}</ol></nav>;
}
export function WorkspaceDialog({ open, onClose, title, description, children, wide = false }: {
  open: boolean; onClose: () => void; title: string; description: string; children: ReactNode; wide?: boolean;
}) {
  const returnFocus = useRef<HTMLElement | null>(null);
  return <WorkspaceModal open={open} onOpenChange={value => { if (!value) onClose(); }}>{open&&<DialogContent showCloseButton={false} className={`iw-dialog${wide ? ' iw-dialog-wide' : ''}`}
    onOpenAutoFocus={()=>{returnFocus.current=document.activeElement instanceof HTMLElement?document.activeElement:null;}}
    onCloseAutoFocus={event=>{event.preventDefault();const target=returnFocus.current;if(target?.isConnected&&!target.closest('[inert]'))target.focus();}}>
    <DialogHeader className="iw-dialog-heading"><div><DialogTitle>{title}</DialogTitle><DialogDescription>{description}</DialogDescription></div><Button type="button" variant="ghost" size="icon" aria-label="Đóng hộp thoại" onClick={onClose}><X size={18}/></Button></DialogHeader>
    <div className="iw-dialog-body">{children}</div>
  </DialogContent>}</WorkspaceModal>;
}
export function InspectChart({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <><div className="iw-chart-preview"><div className="iw-chart-preview-heading"><strong>{title}</strong><Button size="sm" variant="outline" onClick={() => setOpen(true)} aria-label={`Mở biểu đồ ${title}`}><Expand size={14}/>Mở rộng</Button></div><div className="iw-chart-preview-content">{!open && children}</div></div>
    <WorkspaceDialog open={open} onClose={() => setOpen(false)} title={title} description={description} wide>{open && children}</WorkspaceDialog>
  </>;
}
export function WorkspaceFocus({ focused, onClose, title, description, children }: {
  focused: boolean; onClose: () => void; title: string; description: string; children: ReactNode;
}) {
  return focused ? <WorkspaceDialog open onClose={onClose} title={title} description={description} wide>{children}</WorkspaceDialog> : <>{children}</>;
}
