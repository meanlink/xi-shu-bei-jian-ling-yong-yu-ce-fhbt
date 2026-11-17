import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
def requisition_dict(r, claimed_qty=0.0):
    return {'id':r.id,'part':r.part,'use_date':r.use_date,'total_qty':r.total_qty,
            'claimed_qty':round(claimed_qty,6),'routine_remaining':round(max(0.0,r.total_qty-claimed_qty),6),
            'source':r.source,'updated_at':r.updated_at}
def work_order_dict(wo):
    return {'id':wo.id,'code':wo.code,'title':wo.title,'owner':wo.owner,
            'current_version':wo.current_version,'active':wo.active,
            'created_at':wo.created_at,'updated_at':wo.updated_at}
def plan_version_dict(v):
    return {'id':v.id,'work_order_id':v.work_order_id,'version':v.version,
            'note':v.note,'created_by':v.created_by,'created_at':v.created_at}
def forecast_dict(f):
    return {'id':f.id,'name':f.name,'horizon_days':f.horizon_days,
            'plan_version_id':f.plan_version_id,'plan_fingerprint':f.plan_fingerprint,
            'result':f.result,'status':f.status,'reject_reason':f.reject_reason,
            'created_by':f.created_by,'created_at':f.created_at,'approved_at':f.approved_at}
