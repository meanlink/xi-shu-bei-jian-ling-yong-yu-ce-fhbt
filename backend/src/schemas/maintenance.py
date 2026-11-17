"""检修口径模块的入参契约。所有数量在服务端重新校验，前端禁用不替代校验。"""
from pydantic import BaseModel, Field


class RequisitionRow(BaseModel):
    use_date: str = Field(min_length=8, max_length=10)
    part: str = Field(min_length=1, max_length=40)
    total_qty: float = Field(ge=0, le=1_000_000_000)


class RequisitionImport(BaseModel):
    source: str = Field(default='manual.json', max_length=120)
    rows: list[RequisitionRow] = Field(min_length=1, max_length=10000)


class WorkOrderLineInput(BaseModel):
    part: str = Field(min_length=1, max_length=40)
    plan_date: str = Field(min_length=8, max_length=10)
    planned_qty: float = Field(gt=0, le=1_000_000_000)


class WorkOrderRegister(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    title: str = Field(min_length=2, max_length=160)
    owner: str = Field(min_length=2, max_length=80)
    note: str = Field(default='', max_length=500)
    lines: list[WorkOrderLineInput] = Field(min_length=1, max_length=2000)


class WorkOrderRevision(BaseModel):
    note: str = Field(default='', max_length=500)
    lines: list[WorkOrderLineInput] = Field(min_length=1, max_length=2000)


class ClaimLineInput(BaseModel):
    part: str = Field(min_length=1, max_length=40)
    use_date: str = Field(min_length=8, max_length=10)
    qty: float = Field(gt=0, le=1_000_000_000)
    work_order_id: int


class ClaimInput(BaseModel):
    request_key: str = Field(min_length=8, max_length=80)
    lines: list[ClaimLineInput] = Field(min_length=1, max_length=1000)


class ForecastInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    horizon_days: int = Field(default=14, ge=1, le=120)
    history_days: int = Field(default=56, ge=7, le=365)
