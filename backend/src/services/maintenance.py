"""检修口径核心服务：工单版本、按日认领、日常/检修分离试算。

并发与恢复约定：
1. 认领在写事务内执行，先对领用日历行与工单计划行加锁（PG: SELECT ... FOR UPDATE；
   SQLite: 引擎 begin 事件统一发出 BEGIN IMMEDIATE 串行化写事务），再显式计算
   “当日领用余额”和“工单计划余额”，任一明细超额即整批拒绝、不写明细不扣量；
2. request_key 唯一：同键重试返回首次结果，不重复扣量；中断后凭同键恢复；
3. 试算固化完整同版计划与内容指纹，批准时复核指纹，计划改版后的旧计算一律拒绝；
4. 已批准结果及其简报字段为不可变快照，计划变更不回写。
"""
from __future__ import annotations

import math
import statistics
import time
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from src.config.database import engine
from src.constants.enums import ClaimStatus, ForecastStatus, WorkOrderLineState
from src.models import (
    MaintenanceWorkOrder, PlanVersion, WorkOrderLine,
    MaintenanceRequisition, ClaimRequest, ClaimItem, MaintenanceForecast,
)
from src.utils.serialization import checksum


# ---------- 基础校验 ----------

def parse_day(value: str, field='日期') -> date:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise HTTPException(422, f'{field}须为 ISO 日期（YYYY-MM-DD）') from None


def check_finite_qty(value, field='数量'):
    if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
        raise HTTPException(422, f'{field}必须是非负有限数值')
    return float(value)


def begin_immediate(db):
    """确保写事务已开始。

    引擎的 begin 事件已让每个新事务以 BEGIN IMMEDIATE 开始（SQLite），
    并发写事务在第一条语句即串行化；这里仅确保连接/事务已建立，
    不主动回滚，以免在同一事务内组合调用时丢弃调用方已 flush 的待提交数据。
    PostgreSQL 的行级并发由 with_for_update 承担。
    """
    db.connection()


# ---------- 领用日历 ----------

def import_requisitions(db, data):
    seen = set()
    clean = []
    for i, row in enumerate(data.rows):
        day = parse_day(row.use_date, f'第 {i + 1} 行日期')
        qty = check_finite_qty(row.total_qty, f'第 {i + 1} 行领用量')
        key = (str(day), row.part)
        if key in seen:
            raise HTTPException(422, f'第 {i + 1} 行与同批次其他行日期/备件重复：{key}')
        seen.add(key)
        clean.append((str(day), row.part, qty))
    begin_immediate(db)
    upserted = 0
    for use_date, part, qty in clean:
        existing = db.scalar(select(MaintenanceRequisition).where(
            MaintenanceRequisition.use_date == use_date,
            MaintenanceRequisition.part == part,
        ))
        if existing:
            # 已发生认领的日期不允许把总量改到认领额之下
            claimed = db.scalar(select(func.coalesce(func.sum(ClaimItem.qty), 0.0)).join(
                ClaimRequest, ClaimRequest.id == ClaimItem.claim_request_id
            ).where(ClaimItem.requisition_id == existing.id,
                    ClaimRequest.status == ClaimStatus.committed.value))
            if qty < claimed - 1e-9:
                raise HTTPException(422, f'{use_date} {part} 已认领 {claimed:g} 件，领用总量不得低于该值')
            existing.total_qty = qty
            existing.source = data.source
            existing.updated_at = time.time()
        else:
            db.add(MaintenanceRequisition(
                part=part, use_date=use_date, total_qty=qty,
                source=data.source, updated_at=time.time(),
            ))
        upserted += 1
    return upserted


def list_requisitions(db, part: str | None = None, limit=500):
    stmt = select(MaintenanceRequisition).order_by(
        MaintenanceRequisition.use_date.desc(), MaintenanceRequisition.part
    ).limit(limit)
    if part:
        stmt = select(MaintenanceRequisition).where(MaintenanceRequisition.part == part).order_by(
            MaintenanceRequisition.use_date.desc()).limit(limit)
    return list(db.scalars(stmt))


# ---------- 工单登记与版本 ----------

def _validate_lines(lines):
    clean, seen = [], set()
    for i, line in enumerate(lines):
        day = parse_day(line.plan_date, f'第 {i + 1} 行计划日期')
        qty = check_finite_qty(line.planned_qty, f'第 {i + 1} 行计划量')
        if qty <= 0:
            raise HTTPException(422, f'第 {i + 1} 行计划量必须为正数')
        key = (line.part, str(day))
        if key in seen:
            raise HTTPException(422, f'第 {i + 1} 行备件/日期重复：{key}')
        seen.add(key)
        clean.append({'part': line.part, 'plan_date': str(day), 'planned_qty': qty})
    return clean


def _snapshot_version(db, work_order, lines, user, note):
    version = PlanVersion(
        work_order_id=work_order.id, version=work_order.current_version,
        note=note, created_by=user.id, created_at=time.time(),
    )
    db.add(version)
    db.flush()
    for line in lines:
        db.add(WorkOrderLine(
            plan_version_id=version.id, work_order_id=work_order.id,
            part=line['part'], plan_date=line['plan_date'], planned_qty=line['planned_qty'],
        ))
    db.flush()
    return version


def register_work_order(db, data, user):
    lines = _validate_lines(data.lines)
    begin_immediate(db)
    if db.scalar(select(MaintenanceWorkOrder).where(MaintenanceWorkOrder.code == data.code)):
        raise HTTPException(409, f'工单编号 {data.code} 已存在，请改用修订入口')
    now = time.time()
    wo = MaintenanceWorkOrder(
        code=data.code, title=data.title, owner=data.owner,
        current_version=1, active=True, created_at=now, updated_at=now,
    )
    db.add(wo)
    db.flush()
    version = _snapshot_version(db, wo, lines, user, data.note or '初次登记')
    return wo, version


def revise_work_order(db, work_order_id, data, user):
    lines = _validate_lines(data.lines)
    begin_immediate(db)
    wo = db.get(MaintenanceWorkOrder, work_order_id)
    if not wo or not wo.active:
        raise HTTPException(404, '工单不存在或已停用')
    wo.current_version += 1
    wo.updated_at = time.time()
    version = _snapshot_version(db, wo, lines, user, data.note or f'第 {wo.current_version} 版修订')
    return wo, version


def current_version(db, work_order_id):
    wo = db.get(MaintenanceWorkOrder, work_order_id)
    if not wo:
        raise HTTPException(404, '工单不存在')
    return db.scalar(select(PlanVersion).where(
        PlanVersion.work_order_id == wo.id,
        PlanVersion.version == wo.current_version,
    ))


def line_state(line, claimed_qty):
    if claimed_qty <= 1e-9:
        return WorkOrderLineState.pending.value
    if claimed_qty + 1e-9 >= line.planned_qty:
        return WorkOrderLineState.settled.value
    return WorkOrderLineState.partial.value


def work_order_board(db, work_order_id):
    """当前版本工单行 + 认领进度（待认领/部分领用/结清）。"""
    version = current_version(db, work_order_id)
    lines = list(db.scalars(select(WorkOrderLine).where(
        WorkOrderLine.plan_version_id == version.id).order_by(
        WorkOrderLine.plan_date, WorkOrderLine.part)))
    board = []
    for line in lines:
        claimed = db.scalar(select(func.coalesce(func.sum(ClaimItem.qty), 0.0)).join(
            ClaimRequest, ClaimRequest.id == ClaimItem.claim_request_id
        ).where(ClaimItem.work_order_line_id == line.id,
                ClaimRequest.status == ClaimStatus.committed.value))
        req = db.scalar(select(MaintenanceRequisition).where(
            MaintenanceRequisition.use_date == line.plan_date,
            MaintenanceRequisition.part == line.part,
        ))
        day_total = req.total_qty if req else 0.0
        day_claimed = db.scalar(select(func.coalesce(func.sum(ClaimItem.qty), 0.0)).join(
            ClaimRequest, ClaimRequest.id == ClaimItem.claim_request_id
        ).where(ClaimItem.requisition_id == req.id,
                ClaimRequest.status == ClaimStatus.committed.value)) if req else 0.0
        board.append({
            'line_id': line.id, 'part': line.part, 'plan_date': line.plan_date,
            'planned_qty': line.planned_qty, 'claimed_qty': round(claimed, 6),
            'state': line_state(line, claimed),
            'day_total_qty': day_total,
            'day_remaining': round(day_total - day_claimed, 6),
        })
    return version, board


# ---------- 按日认领 ----------

def _lock_requisition(db, use_date, part):
    stmt = select(MaintenanceRequisition).where(
        MaintenanceRequisition.use_date == use_date,
        MaintenanceRequisition.part == part,
    )
    if engine.dialect.name != 'sqlite':
        stmt = stmt.with_for_update()
    req = db.scalar(stmt)
    if not req:
        raise HTTPException(422, f'{use_date} {part} 在领用日历中不存在，无法认领（零需求日也需先登记为 0）')
    return req


def _lock_line(db, work_order_id, use_date, part):
    version = current_version(db, work_order_id)
    stmt = select(WorkOrderLine).where(
        WorkOrderLine.plan_version_id == version.id,
        WorkOrderLine.part == part,
        WorkOrderLine.plan_date == use_date,
    )
    if engine.dialect.name != 'sqlite':
        stmt = stmt.with_for_update()
    line = db.scalar(stmt)
    if not line:
        raise HTTPException(422, f'工单当前版本第 {use_date} {part} 没有计划行，不能认领')
    return line


def _committed_sum(db, column, value):
    return float(db.scalar(select(func.coalesce(func.sum(ClaimItem.qty), 0.0)).join(
        ClaimRequest, ClaimRequest.id == ClaimItem.claim_request_id
    ).where(ClaimRequest.status == ClaimStatus.committed.value, column == value)))


def submit_claim(db, data, user):
    """整批原子认领：任一明细超额即整体拒绝（不写明细、不扣量）；同 request_key 重试不重复扣量。

    并发安全：SQLite 由 BEGIN IMMEDIATE 串行化写事务；PostgreSQL 先对日历行与计划行
    加 FOR UPDATE 行锁，再计算余额，保证并发认领合计不超额。
    """
    for i, item in enumerate(data.lines):
        parse_day(item.use_date, f'第 {i + 1} 行认领日期')
        check_finite_qty(item.qty, f'第 {i + 1} 行认领量')
        if item.qty <= 0:
            raise HTTPException(422, f'第 {i + 1} 行认领量必须为正数')
    # 同一请求内可能出现多行指向同一 (工单, 日期, 备件)，先合并再核额
    merged = {}
    for item in data.lines:
        key = (item.work_order_id, item.use_date, item.part)
        merged[key] = merged.get(key, 0.0) + item.qty

    begin_immediate(db)
    existing = db.scalar(select(ClaimRequest).where(ClaimRequest.request_key == data.request_key))
    if existing:
        return _claim_snapshot(db, existing)

    prepared = []
    for (work_order_id, use_date, part), qty in merged.items():
        req = _lock_requisition(db, use_date, part)
        line = _lock_line(db, work_order_id, use_date, part)
        day_used = _committed_sum(db, ClaimItem.requisition_id, req.id)
        line_used = _committed_sum(db, ClaimItem.work_order_line_id, line.id)
        if req.total_qty - day_used + 1e-9 < qty:
            return _reject(db, data, user,
                           f'{use_date} {part} 本次认领 {qty:g} 件后将超过当天领用总量 '
                           f'（当天 {req.total_qty:g}，已认领 {day_used:g}）')
        if line.planned_qty - line_used + 1e-9 < qty:
            return _reject(db, data, user,
                           f'{use_date} {part} 本次认领 {qty:g} 件后将超过工单计划量 '
                           f'（计划 {line.planned_qty:g}，已认领 {line_used:g}）')
        prepared.append((req, line, qty))

    try:
        request = ClaimRequest(
            request_key=data.request_key, status=ClaimStatus.committed.value,
            created_by=user.id, created_at=time.time(),
        )
        db.add(request)
        db.flush()
        now = time.time()
        for req, line, qty in prepared:
            db.add(ClaimItem(
                claim_request_id=request.id, requisition_id=req.id,
                work_order_line_id=line.id, part=line.part,
                use_date=line.plan_date, qty=qty, created_at=now,
            ))
        db.flush()
    except IntegrityError:
        # 极端并发下另一事务已占用同一 request_key：回放既有结果，绝不重复扣量
        db.rollback()
        begin_immediate(db)
        other = db.scalar(select(ClaimRequest).where(ClaimRequest.request_key == data.request_key))
        if other:
            return _claim_snapshot(db, other)
        raise
    return _claim_snapshot(db, request)


def _reject(db, data, user, reason):
    request = ClaimRequest(
        request_key=data.request_key, status=ClaimStatus.rejected.value,
        reject_reason=reason, created_by=user.id, created_at=time.time(),
    )
    db.add(request)
    db.flush()
    return _claim_snapshot(db, request)


def _claim_snapshot(db, request: ClaimRequest):
    items = list(db.scalars(select(ClaimItem).where(ClaimItem.claim_request_id == request.id)))
    return {
        'request_key': request.request_key,
        'status': request.status,
        'reject_reason': request.reject_reason,
        'lines': [{'part': it.part, 'use_date': it.use_date, 'qty': it.qty} for it in items],
    }


def claim_status(db, request_key):
    request = db.scalar(select(ClaimRequest).where(ClaimRequest.request_key == request_key))
    if not request:
        raise HTTPException(404, '该认领键尚无记录，可能在提交前中断，可用同键安全重试')
    return _claim_snapshot(db, request)


# ---------- 试算：未认领量估计日常需求 + 未来检修量 ----------

def _claimed_map(db):
    """返回 {(date, part): 已认领合计}，只计 committed。"""
    rows = db.execute(select(
        ClaimItem.use_date, ClaimItem.part, func.sum(ClaimItem.qty)
    ).join(ClaimRequest, ClaimItem.claim_request_id == ClaimRequest.id).where(
        ClaimRequest.status == ClaimStatus.committed.value
    ).group_by(ClaimItem.use_date, ClaimItem.part)).all()
    return {(r[0], r[1]): float(r[2]) for r in rows}


def _current_plan_snapshot(db):
    """完整同版计划：所有生效工单的当前版本及其全部计划行，连同内容指纹。

    试算固定该快照；任何一张工单修订/新增都会改变指纹，旧试算据此判定失效。
    """
    current_versions = list(db.scalars(select(PlanVersion).join(
        MaintenanceWorkOrder, MaintenanceWorkOrder.id == PlanVersion.work_order_id
    ).where(
        PlanVersion.version == MaintenanceWorkOrder.current_version,
        MaintenanceWorkOrder.active.is_(True),
    )))
    plan_lines = list(db.scalars(select(WorkOrderLine).where(
        WorkOrderLine.plan_version_id.in_([v.id for v in current_versions])))) \
        if current_versions else []
    fingerprint = checksum([{
        'version_id': v.id, 'work_order_id': v.work_order_id, 'version': v.version,
    } for v in current_versions] + [{
        'part': l.part, 'plan_date': l.plan_date, 'planned_qty': l.planned_qty,
    } for l in plan_lines])
    return current_versions, plan_lines, fingerprint


def run_forecast(db, data, user):
    horizon = int(data.horizon_days)
    history_days = int(data.history_days)
    # 完整同版计划：试算开始即固定当前最新版本，期间计划变更不影响本次计算
    begin_immediate(db)
    current_versions, plan_lines, fingerprint = _current_plan_snapshot(db)
    if not current_versions:
        raise HTTPException(422, '尚无生效检修工单，请先登记工单与备件计划量')

    requisitions = list(db.scalars(select(MaintenanceRequisition)))
    if not requisitions:
        raise HTTPException(422, '尚无领用日历，无法估计日常需求')
    last_day = max(parse_day(r.use_date) for r in requisitions)
    window_start = last_day - timedelta(days=history_days - 1)
    claimed = _claimed_map(db)

    # 未认领量 = 当天领用总量 - 当天已认领检修量（下限 0），按备件汇总为日均日常需求
    parts = sorted({r.part for r in requisitions} | {l.part for l in plan_lines})
    by_part = {p: {} for p in parts}
    for r in requisitions:
        by_part.setdefault(r.part, {})[r.use_date] = r.total_qty
    per_part, routine_total = [], 0.0
    daily_rows = []
    for offset in range(1, horizon + 1):
        daily_rows.append({'day': str(last_day + timedelta(days=offset)), 'routine_qty': 0.0,
                           'maintenance_qty': 0.0, 'total_qty': 0.0})
    for part in parts:
        unclaimed_series = []
        for index in range(history_days):
            day = window_start + timedelta(days=index)
            total = by_part.get(part, {}).get(str(day), 0.0)
            maintenance = claimed.get((str(day), part), 0.0)
            unclaimed_series.append(max(0.0, total - maintenance))
        routine_rate = statistics.fmean(unclaimed_series)
        future_maintenance = {}
        for line in plan_lines:
            if line.part != part:
                continue
            day = parse_day(line.plan_date)
            delta = (day - last_day).days
            if 1 <= delta <= horizon:
                future_maintenance[str(day)] = future_maintenance.get(str(day), 0.0) + line.planned_qty
        part_routine_total = 0.0
        for row in daily_rows:
            routine_today = round(routine_rate, 6)
            maint_today = round(future_maintenance.get(row['day'], 0.0), 6)
            row['routine_qty'] += routine_today
            row['maintenance_qty'] += maint_today
            row['total_qty'] += routine_today + maint_today
            part_routine_total += routine_today
        part_maintenance_total = sum(future_maintenance.values())
        routine_total += part_routine_total
        per_part.append({
            'part': part,
            'history_days': history_days,
            'routine_daily_rate': round(routine_rate, 4),
            'routine_horizon_qty': round(part_routine_total, 3),
            'maintenance_horizon_qty': round(part_maintenance_total, 3),
            'forecast_horizon_qty': round(part_routine_total + part_maintenance_total, 3),
        })
    for row in daily_rows:
        for key in ('routine_qty', 'maintenance_qty', 'total_qty'):
            row[key] = round(row[key], 3)
    maintenance_total = round(sum(r['maintenance_qty'] for r in daily_rows), 3)
    routine_total = round(routine_total, 3)
    version_refs = [{'work_order_id': v.work_order_id, 'version': v.version, 'plan_version_id': v.id}
                    for v in current_versions]
    result = {
        'summary': (f'以最近 {history_days} 天未认领量估计日常需求 '
                    f'{routine_total:g} 件，叠加预测期尚未领用的检修计划 {maintenance_total:g} 件，'
                    f'未来 {horizon} 天合计 {round(routine_total + maintenance_total, 3):g} 件。'),
        'basis': {
            'last_observed_day': str(last_day),
            'history_window': [str(window_start), str(last_day)],
            'horizon': [str(last_day + timedelta(days=1)), str(last_day + timedelta(days=horizon))],
            'routine_rule': '日均日常需求 = 最近窗口内（当日领用总量 − 当日已认领检修量）的均值，零需求日保留',
            'maintenance_rule': '预测期内当前版本工单计划量逐日相加；已认领/已过期的检修量不再计入',
        },
        'plan_versions': version_refs,
        'plan_fingerprint': fingerprint,
        'per_part': per_part,
        'daily': daily_rows,
        'totals': {
            'routine_qty': routine_total,
            'maintenance_qty': maintenance_total,
            'total_qty': round(routine_total + maintenance_total, 3),
        },
        'findings': [
            '集中检修领用经认领后从日常需求中剔除，避免检修脉冲污染均值。',
            '未来检修量只取预测期内、当前计划版本中尚未领用的部分，不与历史领用重复计算。',
            '本结果为备料估计，不是安全库存或采购指令；计划修订后旧版本结果自动失效，不可批准。',
        ],
    }
    forecast = MaintenanceForecast(
        name=data.name, horizon_days=horizon,
        plan_version_id=current_versions[0].id,
        plan_fingerprint=fingerprint, result=result,
        status=ForecastStatus.draft.value, created_by=user.id, created_at=time.time(),
    )
    db.add(forecast)
    db.flush()
    return forecast


def _refresh_stale(db, forecast):
    """计划指纹比对：试算固化的是“所有生效工单当前版本”的完整计划，
    任意工单修订或新增导致当前指纹变化，未批准的旧试算即失效。"""
    if forecast.status != ForecastStatus.draft.value:
        return forecast
    _, _, current_fingerprint = _current_plan_snapshot(db)
    if current_fingerprint != forecast.plan_fingerprint:
        forecast.status = ForecastStatus.stale.value
    return forecast


def list_forecasts(db):
    items = list(db.scalars(select(MaintenanceForecast).order_by(MaintenanceForecast.id.desc()).limit(100)))
    for f in items:
        _refresh_stale(db, f)
    return items


def get_forecast(db, forecast_id):
    forecast = db.get(MaintenanceForecast, forecast_id)
    if not forecast:
        raise HTTPException(404, '试算结果不存在')
    return _refresh_stale(db, forecast)


def approve_forecast(db, forecast_id, user):
    begin_immediate(db)
    forecast = db.get(MaintenanceForecast, forecast_id)
    if not forecast:
        raise HTTPException(404, '试算结果不存在')
    if forecast.status == ForecastStatus.approved.value:
        return forecast  # 幂等：既有批准结果保持原值
    forecast = _refresh_stale(db, forecast)
    if forecast.status == ForecastStatus.stale.value:
        forecast.status = ForecastStatus.rejected.value
        forecast.reject_reason = '计划已变更为新版本，旧计算晚于计划完成，不得批准；请按新版重新试算'
        db.flush()
        db.commit()  # 拒绝结论须留痕，不能随异常回滚
        raise HTTPException(409, forecast.reject_reason)
    forecast.status = ForecastStatus.approved.value
    forecast.approved_at = time.time()
    db.flush()
    return forecast
