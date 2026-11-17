"""检修工单 / 领用日历 / 按日认领 / 检修口径试算的 HTTP 入口。

权限：admin 与 analyst 可登记、修订、导入与认领、试算；仅 admin 可批准；viewer 只读。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from src.config.database import get_db
from src.middlewares.auth import current_user, can_write, admin_only
from src.models import (
    MaintenanceWorkOrder, PlanVersion,
    ClaimRequest, ClaimItem,
)
from src.constants.enums import ClaimStatus
from src.schemas.maintenance import (
    RequisitionImport, WorkOrderRegister, WorkOrderRevision, ClaimInput, ForecastInput,
)
from src.services import maintenance as svc
from src.services.audit import record
from src.utils.serialization import (
    requisition_dict, work_order_dict, plan_version_dict, forecast_dict,
)

router = APIRouter(prefix='/maintenance')


# ---------- 领用日历 ----------

@router.get('/requisitions')
def requisitions(part: str | None = None, db=Depends(get_db), u=Depends(current_user)):
    rows = svc.list_requisitions(db, part)
    out = []
    for r in rows:
        claimed = db.scalar(select(func.coalesce(func.sum(ClaimItem.qty), 0.0)).join(
            ClaimRequest, ClaimRequest.id == ClaimItem.claim_request_id
        ).where(ClaimItem.requisition_id == r.id,
                ClaimRequest.status == ClaimStatus.committed.value))
        out.append(requisition_dict(r, claimed))
    return out


@router.post('/requisitions/import', status_code=201)
def import_requisitions(data: RequisitionImport, db=Depends(get_db), u=Depends(can_write)):
    count = svc.import_requisitions(db, data)
    record(db, u.email, 'maintenance.requisition.import', {'rows': count, 'source': data.source})
    db.commit()
    return {'imported': count}


# ---------- 工单与版本 ----------

@router.get('/work-orders')
def work_orders(db=Depends(get_db), u=Depends(current_user)):
    items = db.scalars(select(MaintenanceWorkOrder).order_by(MaintenanceWorkOrder.id.desc()))
    return [work_order_dict(w) for w in items]


@router.post('/work-orders', status_code=201)
def register(data: WorkOrderRegister, db=Depends(get_db), u=Depends(can_write)):
    wo, version = svc.register_work_order(db, data, u)
    record(db, u.email, 'maintenance.workorder.register',
           {'id': wo.id, 'code': wo.code, 'version': version.version, 'lines': len(data.lines)})
    db.commit()
    return {'work_order': work_order_dict(wo), 'plan_version': plan_version_dict(version)}


@router.post('/work-orders/{id}/revisions', status_code=201)
def revise(id: int, data: WorkOrderRevision, db=Depends(get_db), u=Depends(can_write)):
    wo, version = svc.revise_work_order(db, id, data, u)
    record(db, u.email, 'maintenance.workorder.revise',
           {'id': wo.id, 'code': wo.code, 'version': version.version, 'lines': len(data.lines)})
    db.commit()
    return {'work_order': work_order_dict(wo), 'plan_version': plan_version_dict(version)}


@router.get('/work-orders/{id}')
def work_order_detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    wo = db.get(MaintenanceWorkOrder, id)
    if not wo:
        raise HTTPException(404, '工单不存在')
    version, board = svc.work_order_board(db, id)
    versions = list(db.scalars(select(PlanVersion).where(
        PlanVersion.work_order_id == id).order_by(PlanVersion.version.desc())))
    return {
        'work_order': work_order_dict(wo),
        'current_version': plan_version_dict(version),
        'board': board,
        'versions': [plan_version_dict(v) for v in versions],
    }


# ---------- 认领 ----------

@router.post('/claims', status_code=201)
def claim(data: ClaimInput, db=Depends(get_db), u=Depends(can_write)):
    snapshot = svc.submit_claim(db, data, u)
    record(db, u.email, 'maintenance.claim.' + snapshot['status'],
           {'request_key': snapshot['request_key'], 'lines': len(data.lines),
            'reason': snapshot.get('reject_reason', '')})
    db.commit()
    return snapshot


@router.get('/claims/{request_key}')
def claim_lookup(request_key: str, db=Depends(get_db), u=Depends(current_user)):
    return svc.claim_status(db, request_key)


@router.get('/claims')
def claim_listing(db=Depends(get_db), u=Depends(current_user)):
    requests = db.scalars(select(ClaimRequest).order_by(ClaimRequest.id.desc()).limit(100))
    out = []
    for req in requests:
        snap = svc.claim_status(db, req.request_key)
        snap['id'] = req.id
        snap['created_at'] = req.created_at
        snap['created_by'] = req.created_by
        out.append(snap)
    return out


# ---------- 试算与批准 ----------

@router.get('/forecasts')
def forecasts(db=Depends(get_db), u=Depends(current_user)):
    return [forecast_dict(f) for f in svc.list_forecasts(db)]


@router.post('/forecasts', status_code=201)
def create_forecast(data: ForecastInput, db=Depends(get_db), u=Depends(can_write)):
    f = svc.run_forecast(db, data, u)
    record(db, u.email, 'maintenance.forecast.create',
           {'id': f.id, 'plan_version_id': f.plan_version_id, 'horizon': f.horizon_days})
    db.commit()
    return forecast_dict(f)


@router.get('/forecasts/{id}')
def forecast_detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    return forecast_dict(svc.get_forecast(db, id))


@router.post('/forecasts/{id}/approve')
def approve(id: int, db=Depends(get_db), u=Depends(admin_only)):
    try:
        f = svc.approve_forecast(db, id, u)
    except HTTPException as exc:
        # 服务层对 stale 拒绝已自行提交留痕；其余情况回滚后透传错误
        if exc.status_code != 409:
            db.rollback()
        raise
    record(db, u.email, 'maintenance.forecast.approve',
           {'id': id, 'plan_version_id': f.plan_version_id, 'fingerprint': f.plan_fingerprint})
    db.commit()
    return forecast_dict(f)
