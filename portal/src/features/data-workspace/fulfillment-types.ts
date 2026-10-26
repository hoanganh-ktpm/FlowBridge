// SPDX-License-Identifier: GPL-3.0-or-later
export type Estimate = {material_ready_at:string|null;preparation_complete_at:string|null;state:'on_time'|'late'|'unknown'|'beyond_window';slack_hours:number|null};
export type FulfillmentOrder = {
  order_id:string;order_number:string;deadline:string;already_overdue:boolean;
  base:Estimate;high_demand:Estimate;delayed:Estimate;affected_by_delay:boolean;newly_late:boolean;
  missing_forecast_skus:string[];shortages:{sku:string;uom:string;missing_quantity:number}[];
  supply_links:{source_id:string;sku:string;uom:string;quantity:number}[];
  lines:{sku:string;uom:string;quantity:number;name:string|null}[];
  schedules:{carrier_code:string;carrier_name?:string;scheduled_handoff_at:string|null;status:string}[];
  all_lines_held_or_dispatched:boolean;
  pickup_checks?:Record<'base'|'high_demand'|'delayed',{state:'blocked'|'unknown'|'conditional';reason:string}>;
};
export type FulfillmentReport = {
  available:true;as_of:string;start:string;end:string;forecast_method:string;preparation_minutes:number|null;
  delay_days:number;delayed_plan_id:string|null;orders:FulfillmentOrder[];
  inbound_options:{plan_id:string;sku:string;uom:string;quantity:number;expected_at:string}[];
  bottlenecks:{sku:string;uom:string;name:string|null;order_ids:string[];order_count:number;cumulative_percent:number}[];
  capacity_available:boolean;capacity:{day:string;carrier_code:string;maximum:number;used:number;remaining:number;utilization_percent:number|null}[];
  summary:{total_orders:number;on_time:number;newly_late:number;affected:number;high_demand_exposed:number};limitations:string[];
};
export const estimateNames:Record<Estimate['state'],string>={on_time:'Kịp chuẩn bị theo giả định',late:'Dự kiến chuẩn bị sau hạn',unknown:'Chưa đủ dữ liệu',beyond_window:'Chưa đủ hàng trong kỳ'};
export const normalizeInsight=(value:string)=>value.toLocaleLowerCase('vi').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/đ/g,'d');
export function timelinePosition(value:string|null,start:string,end:string):number|null{
  if(!value)return null;
  const at=Date.parse(value),a=Date.parse(start),b=Date.parse(end);
  return Number.isFinite(at)&&Number.isFinite(a)&&Number.isFinite(b)&&b>a?Math.max(0,Math.min(100,(at-a)/(b-a)*100)):null;
}

// Zoom to the displayed orders, including expired deadlines rather than clipping them to "now".
export function timelineWindow(orders:FulfillmentOrder[],asOf:string):{start:string;end:string}{
  const times=[asOf,...orders.flatMap(o=>[o.deadline,o.base.preparation_complete_at,o.high_demand.preparation_complete_at,o.delayed.preparation_complete_at])]
    .filter((v):v is string=>!!v).map(Date.parse).filter(Number.isFinite);
  const first=Math.min(...times),last=Math.max(...times);
  if(!times.length)return {start:'',end:''};
  const padding=Math.max(30*60*1000,(last-first)*0.08);
  return {start:new Date(first-padding).toISOString(),end:new Date(last+padding).toISOString()};
}

export function samePreparationTimes(order:FulfillmentOrder):boolean{
  const times=[order.base,order.high_demand,order.delayed].map(e=>e.preparation_complete_at?Date.parse(e.preparation_complete_at):NaN);
  return times.every(Number.isFinite)&&times.every(t=>t===times[0]);
}

export function preparationBasis(order:FulfillmentOrder,estimate:Estimate,asOf:string):string{
  if(order.all_lines_held_or_dispatched)return 'Hàng đã giữ hoặc đã xuất được loại khỏi phần cần phân bổ. Chưa xác nhận đơn đã soạn xong.';
  if(estimate.material_ready_at&&Math.abs(Date.parse(estimate.material_ready_at)-Date.parse(asOf))<1000)
    return 'Giả lập có đủ nguồn từ lúc đọc dữ liệu; mốc này bằng giờ đọc cộng thời gian chuẩn bị của Kho.';
  if(estimate.material_ready_at)return 'Mốc này tính từ lúc giả lập đủ mọi món, cộng thời gian chuẩn bị của Kho.';
  return 'Chưa tìm được thời điểm đủ mọi món trong kỳ kiểm tra.';
}
