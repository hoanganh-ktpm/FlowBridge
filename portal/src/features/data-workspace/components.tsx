// SPDX-License-Identifier: GPL-3.0-or-later
import { Children, cloneElement, isValidElement, useId, useState, type ReactElement, type ReactNode } from "react";
import { Info } from "lucide-react";
import { CartesianGrid, Legend, Line, ComposedChart, ReferenceLine, ResponsiveContainer,
  Tooltip as ChartTooltip, XAxis, YAxis, Area, Bar } from "recharts";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";

export const fmt = (value: number | null | undefined) => value == null ? "—" : new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 }).format(value);
export const dateLabel = (value?: string | null) => value ? new Date(value.length === 10 ? value + "T00:00:00+07:00" : value).toLocaleDateString("vi-VN", { timeZone: 'Asia/Ho_Chi_Minh' }) : "—";
export const unitLabel = (unit?: string) => unit === "EA" ? "sản phẩm" : unit === "shipments" ? "lượt vận chuyển" : unit === "dispatches" ? "lượt bàn giao" : unit || "";
export function withCalendarGap(history: {day:string}[], future: {day:string}[]) {
  const gap:{day:string}[]=[];
  if(history.length && future.length) {
    const day=new Date(history.at(-1)!.day+"T00:00:00Z");day.setUTCDate(day.getUTCDate()+1);
    while(day.toISOString().slice(0,10)<future[0].day && gap.length<90) {
      gap.push({day:day.toISOString().slice(0,10)});day.setUTCDate(day.getUTCDate()+1);
    }
  }
  return [...history,...gap,...future];
}
export function Hint({ label, children, onDetail }: { label: string; children: ReactNode; onDetail?: () => void }) {
  const [open, setOpen] = useState(false);
  return <Tooltip open={open} onOpenChange={setOpen}><TooltipTrigger asChild>
    <button type="button" className="dw-hint" aria-label={label} onClick={() => onDetail ? onDetail() : setOpen(v => !v)}><Info size={15}/></button>
  </TooltipTrigger><TooltipContent sideOffset={7} className="max-w-72 text-xs leading-6">{children}</TooltipContent></Tooltip>;
}
export function Field({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  const id = useId();
  const controls = Children.map(children, (child, index) => {
    if (!isValidElement(child)) return child;
    const element = child as ReactElement<{ id?: string; label?: string; "aria-label"?: string }>;
    const props = { id: element.props.id || `${id}-${index}`, "aria-label": element.props["aria-label"] || title };
    return cloneElement(element, child.type === Select ? { ...props, label: element.props.label || title } : props);
  });
  return <div className="dw-field"><div className="dw-field-label"><label htmlFor={`${id}-0`}>{title}</label>{hint && <Hint label={`Giải thích ${title}`}>{hint}</Hint>}</div>{controls}</div>;
}
export function Select({ value, onChange, children, label, disabled = false, id }: { value: string; onChange: (value: string) => void; children: ReactNode; label?: string; disabled?: boolean; id?: string }) {
  return <select id={id} aria-label={label} disabled={disabled} value={value} onChange={e => onChange(e.target.value)}>{children}</select>;
}
export function Panel({ title, subtitle, children, action }: { title: string; subtitle?: string; children: ReactNode; action?: ReactNode }) {
  return <section className="dw-panel"><div className="dw-panel-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</div>{children}</section>;
}
export function Metric({ title, value, note, help, onDetail }: { title: string; value: string; note: string; help: string; onDetail?: () => void }) {
  return <article className="dw-metric"><div>{title}<Hint label={`Giải thích ${title}`} onDetail={onDetail}>{help}</Hint></div><strong>{value}</strong><span>{note}</span></article>;
}
export function Plot({ data, lines, unit, future, bars = false }: { data: object[]; lines: { key: string; name: string; color: string; dash?: string; kind?: 'bar' }[]; unit: string; future?: string; bars?: boolean }) {
  if (!data.length) return <div className="dw-chart-empty">Chưa có kết quả để hiển thị.</div>;
  return <div className="dw-chart" role="img" aria-label={`Biểu đồ ${lines.map(l => l.name).join(", ")}. Đơn vị: ${unit}`}>
    <ResponsiveContainer width="100%" height="100%"><ComposedChart data={data} margin={{ top: 12, right: 18, left: 0, bottom: 4 }}>
      <CartesianGrid stroke="var(--fb-border)" vertical={false} strokeDasharray="3 5"/>
      <XAxis dataKey="day" tick={{ fontSize: 11, fill: "var(--fb-text-muted)" }} tickFormatter={v => dateLabel(String(v)).replace(/\/\d{4}$/, "")} minTickGap={28} axisLine={false} tickLine={false}/>
      <YAxis width={56} tick={{ fontSize: 11, fill: "var(--fb-text-muted)" }} tickLine={false} axisLine={false}/>
      <ChartTooltip labelFormatter={v => dateLabel(String(v))} contentStyle={{ borderRadius: 10, border: "1px solid var(--fb-border)", background: "var(--fb-surface)", fontSize: 12 }} formatter={(v: unknown) => Array.isArray(v) ? v.map(n => fmt(Number(n))).join(" – ") : fmt(typeof v === "number" ? v : null)}/>
      <Legend wrapperStyle={{ fontSize: 11, paddingTop: 16 }}/><ReferenceLine y={0} stroke="var(--fb-border-strong)"/>
      {future && <ReferenceLine x={future} stroke="var(--fb-brand-purple)" strokeDasharray="4 4"/>}
      {lines.map(line => bars || line.kind === 'bar' ? <Bar key={line.key} dataKey={line.key} name={line.name} fill={line.color} radius={[3,3,0,0]} maxBarSize={28} isAnimationActive={false}/> : line.key === "band" ? <Area key="band" dataKey="band" name={line.name} stroke="none" fill="var(--fb-brand-blue)" fillOpacity={0.1} connectNulls={false} isAnimationActive={false}/> : <Line key={line.key} dataKey={line.key} name={line.name} stroke={line.color} strokeWidth={2} strokeDasharray={line.dash} dot={data.length===1?{r:3}:false} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive={false}/>)}
    </ComposedChart></ResponsiveContainer>
  </div>;
}

export function Bars({ data, category, lines, unit }: {data:object[];category:string;lines:{key:string;name:string;color:string}[];unit:string}) {
  if (!data.length) return <div className="dw-chart-empty">Chưa có dữ liệu công bố trong khoảng này.</div>;
  return <div className="dw-chart dw-category-chart" role="img" aria-label={`Biểu đồ ${lines.map(l=>l.name).join(", ")}. Đơn vị: ${unit}`}><ResponsiveContainer width="100%" height="100%"><ComposedChart data={data} margin={{top:15,right:20,left:0,bottom:4}}>
    <CartesianGrid stroke="var(--fb-border)" vertical={false} strokeDasharray="3 5"/>
    <XAxis dataKey={category} tick={{fontSize:11,fill:"var(--fb-text-muted)"}} axisLine={false} tickLine={false}/>
    <YAxis width={48} tick={{fontSize:10,fill:"var(--fb-text-muted)"}} axisLine={false} tickLine={false}/>
    <ChartTooltip contentStyle={{borderRadius:10,fontSize:11,border:"1px solid var(--fb-border)"}} formatter={(value:unknown)=>fmt(typeof value==="number"?value:null)}/>
    <Legend wrapperStyle={{fontSize:10,paddingTop:15}}/>
    {lines.map(l=><Bar key={l.key} dataKey={l.key} name={l.name} fill={l.color} radius={[3,3,0,0]} maxBarSize={28} isAnimationActive={false}/>)}
  </ComposedChart></ResponsiveContainer></div>;
}
