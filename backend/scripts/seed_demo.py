import time, json
from datetime import date, timedelta
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, MaintenanceWorkOrder
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.services import maintenance as mt
from src.schemas.maintenance import (
    RequisitionImport, RequisitionRow, WorkOrderRegister, WorkOrderLineInput,
    ClaimInput, ClaimLineInput, ForecastInput,
)
from src.utils.security import hash_password
from src.utils.serialization import checksum


def _seed_maintenance(db, admin):
    """幂等播种检修口径演示数据：日历、工单版本、认领与试算样例。"""
    if db.scalar(select(MaintenanceWorkOrder).where(MaintenanceWorkOrder.code == 'WO-DEMO-01')):
        return
    start = date(2026, 11, 10)
    req_rows = []
    for i in range(11):  # 11-10 .. 11-20
        day = str(start + timedelta(days=i))
        qty_r17 = 20 if day == '2026-11-16' else (0 if day == '2026-11-12' else 2 + i % 3)
        req_rows.append(RequisitionRow(use_date=day, part='R17', total_qty=qty_r17))
        req_rows.append(RequisitionRow(use_date=day, part='G22', total_qty=1 if i % 2 == 0 else 0))
    mt.import_requisitions(db, RequisitionImport(source='demo-maintenance.csv', rows=req_rows))
    wo, _ = mt.register_work_order(db, WorkOrderRegister(
        code='WO-DEMO-01', title='三号车间主轴集中检修', owner='林知 · 业务负责人',
        note='演示工单：含历史检修日与未来计划',
        lines=[
            WorkOrderLineInput(part='R17', plan_date='2026-11-16', planned_qty=12),
            WorkOrderLineInput(part='R17', plan_date='2026-11-25', planned_qty=8),
            WorkOrderLineInput(part='R17', plan_date='2026-11-28', planned_qty=6),
        ]), admin)
    # 历史检修日已认领 12 件（结清），把检修脉冲从日常需求中剔除
    mt.submit_claim(db, ClaimInput(request_key='seed-claim-demo-01', lines=[
        ClaimLineInput(part='R17', use_date='2026-11-16', qty=12, work_order_id=wo.id)]), admin)
    approved = mt.run_forecast(db, ForecastInput(
        name='检修口径备料试算 · 已批准基线', horizon_days=14, history_days=11), admin)
    mt.approve_forecast(db, approved.id, admin)
    mt.run_forecast(db, ForecastInput(
        name='检修口径备料试算 · 待复核', horizon_days=14, history_days=11), admin)
    db.add(Audit(actor='seed', action='demo.maintenance', detail={'work_order': wo.id}, at=time.time()))


def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        admin=db.scalar(select(User).where(User.role=='admin'))
        _seed_maintenance(db, admin)
        db.commit()
        models=[User,Dataset,Job,Report,Delivery,Audit,MaintenanceWorkOrder]
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in models}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
