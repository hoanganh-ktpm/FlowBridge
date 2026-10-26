// SPDX-License-Identifier: GPL-3.0-or-later
import { fmt } from './components';
import { orderDateTime } from './order-readiness';
import { estimateNames, preparationBasis, samePreparationTimes, timelinePosition, timelineWindow, type FulfillmentOrder, type FulfillmentReport } from './fulfillment-types';

const when=(value:string|null)=>value?orderDateTime(value):'Chưa xác định';
const scenarios=[['base','Nhu cầu thường'],['high_demand','Nhu cầu cao'],['delayed','Hàng về trễ']] as const;

export function FulfillmentTimeline({orders,report,impact,onSelect}:{
  orders:FulfillmentOrder[];report:FulfillmentReport;impact:boolean;onSelect:(order:FulfillmentOrder)=>void;
}){
  if(!orders.length)return null;
  const {start,end}=timelineWindow(orders,report.as_of);
  const nowPosition=timelinePosition(report.as_of,start,end);
  const sameCount=orders.filter(samePreparationTimes).length;
  return <div className="fi-timeline">
    <details className="fi-chart-explanation"><summary>Cách đọc biểu đồ</summary><p>Trục thời gian tự co giãn theo các đơn trên trang này; đường đứt là lúc đọc dữ liệu. Mỗi chấm là một mốc tính toán, không phải tiến độ giao hàng thực tế. Các chấm nằm thẳng hàng nghĩa là ba kịch bản chưa làm đổi mốc chuẩn bị.</p></details>
    {sameCount>0&&<p className="fi-same-note">{sameCount}/{orders.length} đơn đang xem có cùng mốc ở cả ba kịch bản.</p>}
    <div className="fi-timeline-head" aria-hidden="true"><span>Đơn và hạn bàn giao</span><div className="fi-time-axis">{[0,0.5,1].map(f=><span key={f}>{when(new Date(Date.parse(start)+(Date.parse(end)-Date.parse(start))*f).toISOString())}</span>)}</div><span>Kết quả theo giả định</span></div>
    <div role="group" aria-label="Dòng thời gian đơn hàng">
      {orders.map(order=>{
        const estimate=impact?order.delayed:order.base;
        const deadlinePosition=timelinePosition(order.deadline,start,end);
        const slack=estimate.slack_hours;
        return <button type="button" key={order.order_id} className="fi-order-row" onClick={()=>onSelect(order)}>
          <span className="fi-order-identity"><strong>{order.order_number}</strong><small>Hạn bàn giao<br/>{when(order.deadline)}</small><span className={`fi-state ${order.already_overdue?'fi-state-overdue':estimate.state==='on_time'?'fi-state-ready':'fi-state-attention'}`}>{order.already_overdue?'Đã quá hạn lúc tra cứu':estimateNames[estimate.state]}</span></span>
          <span className="fi-row-chart">
            <span className="fi-track" role="img" aria-label={`${order.order_number}: hạn ${when(order.deadline)}; đọc dữ liệu ${when(report.as_of)}; ${scenarios.map(([key,name])=>`${name}: ${when(order[key].preparation_complete_at)}`).join('; ')}`}>
              {nowPosition!==null&&<i className="fi-now" style={{left:`${nowPosition}%`}} title={`Đọc dữ liệu: ${when(report.as_of)}`}/>}
              {deadlinePosition!==null&&<i className="fi-mark fi-deadline" style={{left:`${deadlinePosition}%`}} title={`Hạn bàn giao: ${when(order.deadline)}`}/>}
              {scenarios.map(([key,name])=>{
                const pos=timelinePosition(order[key].preparation_complete_at,start,end);
                return pos!==null?<i key={key} className={`fi-mark fi-${key==='high_demand'?'high':key}`} style={{left:`${pos}%`}} title={`${name}: ${when(order[key].preparation_complete_at)}`}/>:null;
              })}
            </span>
            <small>{samePreparationTimes(order)?'Ba kịch bản cùng mốc':impact?`Ban đầu: ${when(order.base.preparation_complete_at)}`:'Ba mốc tương ứng ba kịch bản'}</small>
          </span>
          <span className="fi-order-time"><small>{impact?'Chuẩn bị xong nếu hàng về trễ':'Ước tính chuẩn bị xong'}</small><strong>{when(estimate.preparation_complete_at)}</strong><small>{slack===null?'Chưa tính được chênh lệch với hạn':slack>=0?`Sớm hơn hạn ${fmt(slack)} giờ`:`Sau hạn ${fmt(-slack)} giờ (ước tính)`}</small>{order.already_overdue&&<small>Không phải số giờ trễ thực tế.</small>}<small className="fi-row-action">Xem cách tính và nguồn hàng →</small></span>
          <span className="fi-order-basis">{preparationBasis(order,estimate,report.as_of)}</span>
        </button>;
      })}
    </div>
  </div>;
}
