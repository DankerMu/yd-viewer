"""状态链工具包：`cfg.ic` 格式保真解析/回写、读侧 header 时间判定、结构检查、重戳与负残差归零。

判定语义同源于 `NWM@8ae9b8f2 packages/common/state_qc.py`，按落地次序：

- `cfg_ic`（任务 4.1，issue #8）：字节保真的解析/回写层，本包的**格式根**；分段识别辅助
  整体移植自该 pin（回写侧为本仓自有，pin 无 writer）。
- `header_time`（任务 12.1，issue #22）：同一 pin 移植的读侧 header 时间判定原语
  （`cfg_ic_header_minute_index`、`cfg_ic_header_minute_time`、`cfg_ic_header_shape`、
  `CfgIcHeaderShape`、`_VALID_CFG_IC_HEADER_TOKEN_COUNTS`），先于 #9 落地，因为
  `controller.py` 的严格前沿闸门需要「时间头是否对应绝对 T」这一判定；其 `_as_float`
  从 `cfg_ic` 导入，不重复定义。
- `state_qc`（任务 4.2 / 4.4，issue #9）：结构检查、负残差归零与域均阈值。
- `restamp`（任务 4.3，issue #9）：把状态时间头重戳到目标 cycle 的绝对时间。

`state_qc` / `restamp` MUST 从 `cfg_ic` 复用分段识别辅助与有界读、从 `header_time` 复用上述
五个 header 符号，不得再移植一份 NWM pin 的判定逻辑（`nwm-snapshot-inventory.md` §1 中
`packages/common/state_qc.py` 行的双权威副本禁令）。

本包不写任何文件：改写返回新的 `CfgIcDocument`，落盘归 #21 init 首态与 #24 发布器。
"""

from yd_producer.state.cfg_ic import (
    MAX_STATE_IC_BYTES,
    CfgIcDocument,
    LineRole,
    Section,
    parse,
    render,
)
from yd_producer.state.restamp import (
    STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID,
    restamp_to_absolute_time,
)
from yd_producer.state.state_qc import (
    MAX_RIVER_MEAN_CORRECTION_M,
    MAX_UNSAT_MEAN_CORRECTION_M,
    CfgIcHeaderShape,
    StateQCResult,
    StateResidualNormalization,
    StateResidualRejected,
    cfg_ic_header_minute_index,
    cfg_ic_header_minute_time,
    cfg_ic_header_shape,
    normalize_negative_residuals,
    run_state_variable_qc,
    state_ic_structure_complete,
)

__all__ = [
    "MAX_RIVER_MEAN_CORRECTION_M",
    "MAX_STATE_IC_BYTES",
    "MAX_UNSAT_MEAN_CORRECTION_M",
    "STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID",
    "CfgIcDocument",
    "CfgIcHeaderShape",
    "LineRole",
    "Section",
    "StateQCResult",
    "StateResidualNormalization",
    "StateResidualRejected",
    "cfg_ic_header_minute_index",
    "cfg_ic_header_minute_time",
    "cfg_ic_header_shape",
    "normalize_negative_residuals",
    "parse",
    "render",
    "restamp_to_absolute_time",
    "run_state_variable_qc",
    "state_ic_structure_complete",
]
