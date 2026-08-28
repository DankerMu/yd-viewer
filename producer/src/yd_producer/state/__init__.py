"""状态链工具包：`cfg.ic` 格式保真解析/回写，以及读侧 header 时间判定。

本包现有两处落地，判定语义同源于 `NWM@8ae9b8f2 packages/common/state_qc.py`：
`cfg_ic`（任务 4.1）是字节保真的解析/回写层，其分段识别辅助整体移植自该 pin（回写侧
为本仓自有，pin 无 writer）；`header_time`（任务 12.1）是同一 pin 移植的读侧
header 时间判定原语（`cfg_ic_header_minute_index`、`cfg_ic_header_minute_time`、
`cfg_ic_header_shape`、`CfgIcHeaderShape`、`_VALID_CFG_IC_HEADER_TOKEN_COUNTS`），
之所以先行落地，是因为 `controller.py` 的严格前沿闸门需要「时间头是否对应绝对 T」这
一判定。`header_time` 的 `_as_float` 从 `cfg_ic` 导入，不重复定义。

结构检查、重戳、负残差归零（任务 4.2–4.4，issue #9）另行落地，届时 MUST 从 `cfg_ic`
导入分段识别辅助、并从 `header_time` 导入上述五个符号，两者都不得再从 NWM pin 移植一
份副本。该禁令的权威表述见 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md`
中 `packages/common/state_qc.py` 一行的备注。
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
