import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const maintenance={
  requisitions:part=>api('/maintenance/requisitions'+(part?('?part='+encodeURIComponent(part)):'')),
  importRequisitions:data=>api('/maintenance/requisitions/import',{method:'POST',body:JSON.stringify(data)}),
  workOrders:()=>api('/maintenance/work-orders'),
  registerWorkOrder:data=>api('/maintenance/work-orders',{method:'POST',body:JSON.stringify(data)}),
  reviseWorkOrder:(id,data)=>api('/maintenance/work-orders/'+id+'/revisions',{method:'POST',body:JSON.stringify(data)}),
  workOrder:id=>api('/maintenance/work-orders/'+id),
  submitClaim:data=>api('/maintenance/claims',{method:'POST',body:JSON.stringify(data)}),
  claim:key=>api('/maintenance/claims/'+encodeURIComponent(key)),
  claims:()=>api('/maintenance/claims'),
  forecasts:()=>api('/maintenance/forecasts'),
  createForecast:data=>api('/maintenance/forecasts',{method:'POST',body:JSON.stringify(data)}),
  forecast:id=>api('/maintenance/forecasts/'+id),
  approveForecast:id=>api('/maintenance/forecasts/'+id+'/approve',{method:'POST'}),
};
