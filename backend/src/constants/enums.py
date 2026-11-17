from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
class ClaimStatus(str, Enum):
    committed = 'committed'   # 已认领，实际扣量
    rejected = 'rejected'     # 超额或同键冲突被拒，不扣量
class ForecastStatus(str, Enum):
    draft = 'draft'           # 基于当前最新计划版本
    stale = 'stale'           # 计划已出新版，旧计算不得批准
    approved = 'approved'     # 已批准，结果与简报冻结
    rejected = 'rejected'     # 批准时版本已过期
class WorkOrderLineState(str, Enum):
    pending = 'pending'       # 待认领：尚无认领
    partial = 'partial'       # 部分领用：已认领但未结清
    settled = 'settled'       # 结清：认领合计达到计划量
