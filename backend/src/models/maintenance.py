"""检修工单、领用日历、按日认领与检修口径试算的持久化模型。

口径约定：
- 领用日历按 (日期, 备件) 唯一，包含零需求日；
- 工单每次修订生成一个完整的 plan_version 快照版本；
- 认领明细的双重上限（当日领用余额、工单计划余额）在服务层事务内条件更新；
- 试算结果固化 plan_version，计划变更后旧版本结果不得批准。
"""
from sqlalchemy import String, Double, ForeignKey, Boolean, Integer, UniqueConstraint, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class MaintenanceWorkOrder(Base):
    __tablename__ = 'maintenance_work_orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    owner: Mapped[str] = mapped_column(String(80))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)


class PlanVersion(Base):
    __tablename__ = 'plan_versions'
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey('maintenance_work_orders.id'))
    version: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(500), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('work_order_id', 'version', name='uq_plan_version'),)


class WorkOrderLine(Base):
    __tablename__ = 'work_order_lines'
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_version_id: Mapped[int] = mapped_column(ForeignKey('plan_versions.id'))
    work_order_id: Mapped[int] = mapped_column(ForeignKey('maintenance_work_orders.id'))
    part: Mapped[str] = mapped_column(String(40), index=True)
    plan_date: Mapped[str] = mapped_column(String(10), index=True)
    planned_qty: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('plan_version_id', 'part', 'plan_date', name='uq_plan_line'),
                      Index('ix_line_current', 'work_order_id', 'part', 'plan_date'))


class MaintenanceRequisition(Base):
    __tablename__ = 'maintenance_requisitions'
    id: Mapped[int] = mapped_column(primary_key=True)
    part: Mapped[str] = mapped_column(String(40), index=True)
    use_date: Mapped[str] = mapped_column(String(10), index=True)
    total_qty: Mapped[float] = mapped_column(Double)
    source: Mapped[str] = mapped_column(String(120), default='manual')
    updated_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('use_date', 'part', name='uq_requisition_day_part'),)


class ClaimRequest(Base):
    """一次按日历批次的认领请求；request_key 保证中断重试不重复扣量。"""
    __tablename__ = 'claim_requests'
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(20), default='committed')  # committed / rejected
    reject_reason: Mapped[str] = mapped_column(String(500), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)


class ClaimItem(Base):
    __tablename__ = 'claim_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_request_id: Mapped[int] = mapped_column(ForeignKey('claim_requests.id'), index=True)
    requisition_id: Mapped[int] = mapped_column(ForeignKey('maintenance_requisitions.id'))
    work_order_line_id: Mapped[int] = mapped_column(ForeignKey('work_order_lines.id'))
    part: Mapped[str] = mapped_column(String(40), index=True)
    use_date: Mapped[str] = mapped_column(String(10), index=True)
    qty: Mapped[float] = mapped_column(Double)
    created_at: Mapped[float] = mapped_column(Double)


class MaintenanceForecast(Base):
    __tablename__ = 'maintenance_forecasts'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    horizon_days: Mapped[int] = mapped_column(Integer)
    plan_version_id: Mapped[int] = mapped_column(ForeignKey('plan_versions.id'))
    plan_fingerprint: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON, default=dict)  # 完整同版计划快照，批准后不再改写
    status: Mapped[str] = mapped_column(String(20), default='draft')  # draft / stale / approved / rejected
    reject_reason: Mapped[str] = mapped_column(String(500), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    approved_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    __table_args__ = (Index('ix_forecast_version', 'plan_version_id'),)
