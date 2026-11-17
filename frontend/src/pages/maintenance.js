import {maintenance} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {parseCSV} from '../utils/csv.js';
import {ClaimStatus,ForecastStatus,LineState} from '../constants/enums.js';

const canWrite=()=>session.user.role!=='viewer';
const isAdmin=()=>session.user.role==='admin';

export async function maintenancePage(){
 workspace.maintenance=workspace.maintenance||{tab:'requisition'};
 const [requisitions,workOrders,claims,forecasts]=await Promise.all([
   maintenance.requisitions(),maintenance.workOrders(),maintenance.claims(),maintenance.forecasts()]);
 workspace.mt={requisitions,workOrders,claims,forecasts};
 const tab=workspace.maintenance.tab;
 const tabs=[['requisition','领用日历'],['workorder','工单与版本'],['claim','按日认领'],['forecast','检修试算']];
 return heading('06 / MAINTENANCE LEDGER','检修工单与备料认领','集中检修领用按工单逐批认领，从日常需求中剔除；试算只叠加预测期内尚未领用的检修计划，避免备料高估。')
  +'<div class="tabs">'+tabs.map(([k,label])=>'<button class="tab '+(tab===k?'active':'')+'" data-tab="'+k+'">'+esc(label)+'</button>').join('')+'</div>'
  +(tab==='requisition'?requisitionView(requisitions):tab==='workorder'?workOrderView(workOrders):tab==='claim'?claimView(workOrders,claims):forecastView(forecasts));
}

function requisitionView(rows){
 const parts=[...new Set(rows.map(r=>r.part))];
 return '<div class="mt-grid"><section class="panel"><div class="section-title"><h2>登记领用日历</h2><span>按日期 × 备件</span></div>'
  +'<form id="req-form"><label>上传 CSV（use_date,part,total_qty）<input type="file" id="req-file" accept=".csv,.json"></label>'
  +'<label>记录 JSON<textarea name="rows" rows="6" placeholder="[{&quot;use_date&quot;:&quot;2026-11-16&quot;,&quot;part&quot;:&quot;R17&quot;,&quot;total_qty&quot;:18}]" required></textarea></label>'
  +'<label>来源<input name="source" value="manual.json" required></label>'
  +'<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>导入/更新日历（含零需求日）</button>'
  +'<p class="hint">同一日期+备件再次导入即为更新；总量不得低于该日已认领量。零需求日请登记 total_qty=0。</p></form></section>'
  +'<section class="panel"><div class="section-title"><h2>最近领用日</h2><span>'+rows.length+' 行 · 备件 '+parts.join('、')+'</span></div>'
  +table([{key:'use_date',label:'领用日'},{key:'part',label:'备件'},{key:'total_qty',label:'当天领用总量'},{key:'claimed_qty',label:'已认领检修'},{key:'routine_remaining',label:'未认领（日常口径）'}],rows,'暂无日历，请先导入。')+'</section></div>';
}

function workOrderView(workOrders){
 return '<div class="mt-grid"><section class="panel"><div class="section-title"><h2>登记检修工单</h2><span>备件 · 日期 · 计划量</span></div>'
  +'<form id="wo-form"><label>工单编号<input name="code" required minlength="2" placeholder="WO-2026-014"></label>'
  +'<label>工单名称<input name="title" required minlength="2" placeholder="主轴集中检修"></label>'
  +'<label>负责人<input name="owner" required minlength="2" placeholder="维保负责人姓名"></label>'
  +'<label>计划行 JSON<textarea name="lines" rows="6" required placeholder="[{&quot;part&quot;:&quot;R17&quot;,&quot;plan_date&quot;:&quot;2026-11-25&quot;,&quot;planned_qty&quot;:8}]"></textarea></label>'
  +'<label>备注<input name="note" placeholder="初次登记"></label>'
  +'<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>登记工单（生成计划版本）</button>'
  +'<p class="hint">每次修订都会生成完整新版本快照；认领与试算始终绑定具体版本，计划改版后旧试算自动失效。</p></form></section>'
  +'<section class="panel"><div class="section-title"><h2>工单清单</h2><span>'+workOrders.length+' 张</span></div>'
  +table([{key:'code',label:'工单编号'},{key:'title',label:'名称'},{key:'owner',label:'负责人'},{key:'current_version',label:'当前版本'},{key:'active',label:'状态'}],
    workOrders.map(w=>({...w,active:w.active?'生效':'停用'})),'暂无工单。')+'</section></div>'
  +'<section class="panel"><div class="section-title"><h2>工单看板（待认领 / 部分领用 / 结清）</h2><span>选择工单查看当前版本逐行进度</span></div>'
  +'<label>选择工单<select id="wo-select">'+workOrders.map(w=>'<option value="'+w.id+'">'+esc(w.code)+' · v'+w.current_version+' · '+esc(w.title)+'</option>').join('')+'</select></label>'
  +'<div id="wo-board"><p class="empty">选择工单后加载逐行认领进度。</p></div>'
  +'<form id="rev-form" class="revise" style="margin-top:22px"><div class="section-title"><h2>计划修订（生成新版本）</h2></div>'
  +'<label>修订备注<input name="note" placeholder="例如：计划量上调"></label>'
  +'<label>完整计划行 JSON（整版替换）<textarea name="lines" rows="4" required></textarea></label>'
  +'<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>提交修订并生成新版本</button></form></section>';
}

function claimView(workOrders,claims){
 return '<div class="mt-grid"><section class="panel"><div class="section-title"><h2>按日历批次认领</h2><span>双重上限校验</span></div>'
  +'<form id="claim-form"><label>认领请求键 request_key（中断后用同键重试不重复扣量）<input name="request_key" required minlength="8" value="claim-'+Date.now()+'"></label>'
  +'<label>认领明细 JSON<textarea name="lines" rows="6" required placeholder="[{&quot;part&quot;:&quot;R17&quot;,&quot;use_date&quot;:&quot;2026-11-16&quot;,&quot;qty&quot;:3,&quot;work_order_id&quot;:1}]"></textarea></label>'
  +'<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>提交整批认领</button>'
  +'<p class="hint">合计不得超过当天领用总量，也不得超过工单计划量；任一明细超额则整批拒绝、不扣任何量。</p></form>'
  +'<form id="claim-lookup" class="revise"><label>按 request_key 恢复/查询认领结果<input name="request_key" required minlength="8" placeholder="claim-..."></label><button class="button">查询认领结果</button></form></section>'
  +'<section class="panel"><div class="section-title"><h2>认领请求记录</h2><span>'+claims.length+' 条</span></div>'
  +table([{key:'request_key',label:'请求键'},{key:'status',label:'结果'},{key:'reject_reason',label:'拒绝原因'},{key:'created_at',label:'时间'}],
    claims.map(c=>({...c,status:ClaimStatus[c.status]||c.status,created_at:date(c.created_at)})),'暂无认领请求。')+'</section></div>';
}

function forecastView(forecasts){
 return '<div class="mt-grid"><section class="panel"><div class="section-title"><h2>检修口径试算</h2><span>未认领估日常 + 未来检修</span></div>'
  +'<form id="fc-form"><label>试算名称<input name="name" required minlength="2" value="检修口径备料试算"></label>'
  +'<div class="parameter-grid"><label>预测天数<input type="number" name="horizon_days" value="14" min="1" max="120" required></label>'
  +'<label>历史窗口天数<input type="number" name="history_days" value="56" min="7" max="365" required></label></div>'
  +'<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>按当前计划版本试算</button>'
  +'<p class="hint">试算开始即固定完整同版计划并留指纹；计划改版后，旧结果转为“不可批准”，已批准结果与简报保持原值。</p></form></section>'
  +'<section class="panel"><div class="section-title"><h2>试算记录与版本追查</h2><span>'+forecasts.length+' 条</span></div>'
  +forecasts.map(f=>forecastCard(f)).join('')+'</section></div>';
}

function forecastCard(f){
 const r=f.result||{},t=r.totals||{};
 const versions=(r.plan_versions||[]).map(v=>'工单#'+v.work_order_id+' v'+v.version).join('、');
 const statusClass=f.status==='approved'?'ok':(f.status==='stale'||f.status==='rejected'?'bad':'');
 return '<article class="fc-card '+statusClass+'"><div class="fc-head"><strong>'+esc(f.name)+'</strong>'
  +'<span class="fc-status '+esc(f.status)+'">'+esc(ForecastStatus[f.status]||f.status)+'</span></div>'
  +'<p class="hint">'+esc(r.summary||'')+'</p>'
  +'<div class="metrics">'+[['日常需求估计',t.routine_qty],['未来检修量',t.maintenance_qty],['备料合计',t.total_qty]].map(([k,v])=>'<div><span>'+k+'</span><strong>'+esc(fmt(v))+'</strong></div>').join('')+'</div>'
  +'<p class="hint">采用计划版本：'+esc(versions||'—')+' · 指纹 '+esc((f.plan_fingerprint||'').slice(0,18))+'… · '+date(f.created_at)+'</p>'
  +(f.reject_reason?'<p class="warning">'+esc(f.reject_reason)+'</p>':'')
  +(f.status==='draft'&&isAdmin()?'<button class="primary small" data-approve="'+f.id+'">批准此试算</button>':'')
  +(f.status==='draft'&&!isAdmin()?'<p class="hint">待业务负责人批准。</p>':'')
  +(r.daily?'<details><summary>查看未来逐日表（'+r.daily.length+' 天）</summary>'
   +table([{key:'day',label:'日期'},{key:'routine_qty',label:'日常需求'},{key:'maintenance_qty',label:'检修计划'},{key:'total_qty',label:'合计'}],r.daily)+'</details>':'')+'</article>';
}

export function bindMaintenance(refresh){
 document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{workspace.maintenance.tab=b.dataset.tab;refresh();});
 // 日历导入
 const reqForm=document.querySelector('#req-form');
 if(reqForm){
  document.querySelector('#req-file').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;try{const text=await file.text();const data=file.name.endsWith('.csv')?parseCSV(text):JSON.parse(text);reqForm.querySelector('[name=rows]').value=JSON.stringify(data);reqForm.querySelector('[name=source]').value=file.name;}catch(err){notify(err.message,true);}});
  submit(reqForm,async f=>{await maintenance.importRequisitions({source:f.get('source'),rows:JSON.parse(f.get('rows'))});notify('领用日历已保存');await refresh();});
 }
 // 工单登记
 const woForm=document.querySelector('#wo-form');
 if(woForm)submit(woForm,async f=>{await maintenance.registerWorkOrder({code:f.get('code'),title:f.get('title'),owner:f.get('owner'),note:f.get('note'),lines:JSON.parse(f.get('lines'))});notify('工单已登记，生成计划版本');workspace.maintenance.tab='workorder';await refresh();});
 // 工单选择 + 看板
 const sel=document.querySelector('#wo-select');
 if(sel){sel.onchange=()=>loadBoard(sel.value);loadBoard(sel.value);}
 const revForm=document.querySelector('#rev-form');
 if(revForm)submit(revForm,async f=>{const id=sel.value;await maintenance.reviseWorkOrder(id,{note:f.get('note'),lines:JSON.parse(f.get('lines'))});notify('已生成新计划版本');await refresh();document.querySelector('#wo-select').value=id;loadBoard(id);});
 // 认领
 const claimForm=document.querySelector('#claim-form');
 if(claimForm)submit(claimForm,async f=>{const snap=await maintenance.submitClaim({request_key:f.get('request_key'),lines:JSON.parse(f.get('lines'))});if(snap.status==='committed'){notify('整批认领成功');}else{notify('认领被拒：'+snap.reject_reason,true);}workspace.maintenance.tab='claim';await refresh();});
 const lookup=document.querySelector('#claim-lookup');
 if(lookup)submit(lookup,async f=>{const snap=await maintenance.claim(f.get('request_key'));notify(snap.status==='committed'?('认领成功，明细 '+snap.lines.length+' 行'):('该键结果：'+(snap.reject_reason||'已拒绝')));});
 // 试算
 const fcForm=document.querySelector('#fc-form');
 if(fcForm)submit(fcForm,async f=>{await maintenance.createForecast({name:f.get('name'),horizon_days:Number(f.get('horizon_days')),history_days:Number(f.get('history_days'))});notify('试算完成，已固化计划版本');workspace.maintenance.tab='forecast';await refresh();});
 document.querySelectorAll('[data-approve]').forEach(b=>b.onclick=async()=>{try{await maintenance.approveForecast(b.dataset.approve);notify('已批准，结果与简报冻结');await refresh();}catch(e){notify(e.message,true);await refresh();}});
}

async function loadBoard(id){
 const box=document.querySelector('#wo-board');
 try{
  const detail=await maintenance.workOrder(id);
  const rows=detail.board.map(b=>({...b,state:LineState[b.state]||b.state}));
  box.innerHTML=table([{key:'plan_date',label:'计划日期'},{key:'part',label:'备件'},{key:'planned_qty',label:'计划量'},{key:'claimed_qty',label:'已认领'},{key:'state',label:'状态'},{key:'day_total_qty',label:'当天领用'},{key:'day_remaining',label:'当天剩余'}],rows);
  const rev=document.querySelector('#rev-form [name=lines]');
  if(rev)rev.value=JSON.stringify(detail.board.map(b=>({part:b.part,plan_date:b.plan_date,planned_qty:b.planned_qty})),null,2);
 }catch(e){box.innerHTML='<p class="warning">'+esc(e.message)+'</p>';}
}
