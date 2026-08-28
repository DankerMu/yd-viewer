"""状态链工具包：`cfg.ic` 格式保真解析/回写。

本包当前只含格式层（任务 4.1）。结构检查、重戳、负残差归零（任务 4.2–4.4）另行落地，
届时 MUST 复用 `cfg_ic` 里的分段识别辅助，不得再移植一份 NWM pin 的分段逻辑。
"""

from yd_producer.state.cfg_ic import (
    MAX_STATE_IC_BYTES,
    CfgIcDocument,
    LineRole,
    Section,
    parse,
    render,
)

__all__ = [
    "MAX_STATE_IC_BYTES",
    "CfgIcDocument",
    "LineRole",
    "Section",
    "parse",
    "render",
]
