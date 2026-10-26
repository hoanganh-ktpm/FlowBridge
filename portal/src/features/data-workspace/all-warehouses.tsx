// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef, useState } from 'react';
import { useWorkspaceState } from './workspace-sync';
import { ApiError } from '@/lib/api';
import type { Explanation } from './business-evidence';
import type { DecisionRequest } from './decision-dossiers';
import type { Warehouse, WarehouseOverview as Overview } from './warehouse-overview-types';
import { WarehouseOverviewView } from './warehouse-overview-view';

export function AllWarehouses({request,onDenied,onLocal,analysisWarehouseIds=[],initialWarehouseId='',onSelectWarehouse,onReviewOrder,onEvidence}: {request:DecisionRequest;onDenied:(e:unknown)=>void;onLocal:(warehouseId:string,sku?:string,horizon?:number)=>void;analysisWarehouseIds?:string[];initialWarehouseId?:string;onSelectWarehouse?:(warehouseId:string)=>void;onReviewOrder?:(warehouseId:string,orderNumber:string,horizon:number)=>void;onEvidence?:(value:Explanation)=>void}) {
  const [horizon,setHorizon]=useWorkspaceState('overview-horizon','7'),[result,setResult]=useState<Overview|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const generation=useRef(0);
  const controller=useRef<AbortController|null>(null);
  async function calculate(warehouse:Warehouse,ticket:number,signal:AbortSignal,days:number,fresh=false){
    try{
      const value=await request<Overview>('/all-warehouses',{horizon:days,include_planning:true,warehouse_id:warehouse.warehouse_id},signal,undefined,fresh);
      if(ticket!==generation.current||signal.aborted)return;
      const item=value.items.find(w=>w.warehouse_id===warehouse.warehouse_id&&w.organization_id===warehouse.organization_id);
      if(value.items.length!==1||!item||value.horizon!==days)throw new Error('Unexpected warehouse result');
      setResult(current=>current?{...current,items:current.items.map(w=>w.warehouse_id===item.warehouse_id?item:w)}:null);
    }catch(e){
      if(ticket!==generation.current||signal.aborted)return;
      if(e instanceof ApiError&&[401,403].includes(e.status)){
        controller.current?.abort();setResult(null);setError('Quyền xem dữ liệu đã thay đổi. Tải lại trang sau khi quản trị viên kiểm tra.');onDenied(e);return;
      }
      const modelUnavailable=e instanceof ApiError&&e.code==='CHRONOS_UNAVAILABLE';
      setResult(current=>current?{...current,items:current.items.map(w=>w.warehouse_id===warehouse.warehouse_id?{...w,planning_status:'unavailable',planning_issue:{code:modelUnavailable?'CHRONOS_UNAVAILABLE':'PLANNING_UNAVAILABLE',message:modelUnavailable?'Chưa chạy được mô hình Chronos-2. Bạn vẫn có thể xem tồn kho và đơn hàng; liên hệ IT để kiểm tra mô hình.':'Chưa tính được lượng thiếu. Bạn vẫn có thể xem tồn kho và đơn hàng, hoặc bấm Thử tính lại.'}}:w)}:null);
    }
  }
  async function calculateAll(value:Overview,ticket:number,signal:AbortSignal,fresh=false){
    const queue=value.items.filter(w=>w.planning_status==='pending');
    // Limit background work even when the reviewed directory contains many warehouses.
    async function worker(){
      while(queue.length&&ticket===generation.current&&!signal.aborted){
        const item=queue.shift();if(item)await calculate(item,ticket,signal,value.horizon,fresh);
      }
    }
    await Promise.all([worker(),worker()]);
  }
  async function load(fresh=false){
    controller.current?.abort();
    const nextController=new AbortController();controller.current=nextController;
    const ticket=++generation.current;setBusy(true);setError('');setResult(null);
    try{
      const value=await request<Overview>('/all-warehouses',{horizon:Number(horizon),include_planning:false},nextController.signal,undefined,fresh);
      if(ticket===generation.current&&!nextController.signal.aborted){setResult(value);setBusy(false);void calculateAll(value,ticket,nextController.signal,fresh);}
    }catch(e){if(ticket===generation.current&&!nextController.signal.aborted){setResult(null);if(e instanceof ApiError&&[401,403].includes(e.status))onDenied(e);setError(e instanceof ApiError&&[401,403].includes(e.status)?'Phiên đăng nhập hoặc quyền xem dữ liệu đã thay đổi. Nhờ quản trị viên kiểm tra rồi tải lại trang.':'Chưa tải được dữ liệu kho từ các bộ phận. Kiểm tra kết nối rồi bấm Làm mới dữ liệu để thử lại.');}}
    finally{if(ticket===generation.current)setBusy(false);}
  }
  useEffect(()=>{void load();return()=>{generation.current++;controller.current?.abort();};},[]);
  function retry(warehouse:Warehouse){
    const signal=controller.current?.signal;if(!signal||signal.aborted||!result)return;
    setResult(current=>current?{...current,items:current.items.map(w=>w.warehouse_id===warehouse.warehouse_id?{...w,planning_status:'pending',planning_issue:null}:w)}:null);
    void calculate(warehouse,generation.current,signal,result.horizon,true);
  }
  return <WarehouseOverviewView request={request} onEvidence={onEvidence} onDenied={e=>{controller.current?.abort();setResult(null);setError('Quyền xem dữ liệu đã thay đổi. Tải lại trang sau khi quản trị viên kiểm tra.');onDenied(e);}} result={result} busy={busy} error={error} horizon={horizon} onHorizon={setHorizon}
    initialWarehouseId={initialWarehouseId} onSelectWarehouse={onSelectWarehouse} onReviewOrder={onReviewOrder} onReload={()=>void load(true)} onRetry={retry} onAnalyze={onLocal} analysisWarehouseIds={analysisWarehouseIds}/>;
}
