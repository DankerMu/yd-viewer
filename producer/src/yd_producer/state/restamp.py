# NWM@8ae9b8f2 packages/common/state_cli.py
"""把 `cfg.ic` 的状态时间头重戳到目标 cycle 的**绝对**时间（任务 4.3）。

溯源：`NWM@8ae9b8f2 packages/common/state_cli.py`。语义移植自 pin 的
`_normalized_checkpoint_ic_file`(:256) 的 header 覆写段与 `_ensure_utc`(:1186)，
header 判定基座（`cfg_ic_header_shape` / `cfg_ic_header_minute_index`）在
`yd_producer.state.state_qc`（pin 同文件 `state_qc.py`），本模块导入之。

**唯一重戳入口**：`restamp_to_absolute_time(doc, target)`。compute-loop §9.2 的两条定戳
路径——init 首态（率定末态重戳到 T）与发布前 checkpoint 定戳（重戳到 T+12）——只差 `target`
实参，MUST NOT 分裂成两个函数或加 `mode` 开关。

对 pin 的**刻意偏离**（四条，此处即全集）：

1. **闸门次序：`cfg_ic_header_shape` 提到 `cfg_ic_header_minute_index` 之前。** pin 的
   `_normalized_checkpoint_ic_file` 先取 minute-index（`state_cli.py:271`）、`None` 即早退，
   再查 shape（`:283`）。yd 侧反过来：shape 合法（恰 3 或 4 个数值 token）**蕴含** minute-index
   必不为 None（minute-index 为 None ⟺ 数值 token < 2，两集合交空），故 shape 在前时不存在
   「先按不合法布局定位再拒绝」的中间态。连带后果：`cfg_ic_header_minute_index` 返回 `None`
   这条分支在本 seam 上**恒不可达**，故落成带 `pragma: no cover` 的内部不变量自检
   （承 #8 在 `cfg_ic.parse` 末尾全覆盖划分自检上确立的同一手法），**不为它写用例**——
   两条可写法（手工构造文档、喂 bytes）分别红在 `STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID`
   与 `cfg_ic.parse` 的 `unreadable IC header` 上，都不行使本分支。
2. **序列化法则：只重写 header 行，且行内只替换 minute token 的字节。** pin 改完 header 后
   `"\\t".join(header)` 会把 header 行里**未被改动**的 token 之间的原始分隔（空格/多空格/
   Tab 混排）一律重写成单 Tab，随后 `"\\n".join(lines)` 又把整个文件的 CRLF 归一为 LF、抹掉
   行尾空格。spec state-tools 要求「数据区 MUST 保持不变」，故 yd 侧经
   `CfgIcDocument.with_replaced_lines` 只替换 header 一行，行内经
   `state_qc.replace_tokens` 就地 splice minute token 的字节切片，行尾符由 API 贴回原样。
   （这条**只在脏输入上可证伪**：canonical 化的写法在干净输入上恒绿。）
3. **不写任何文件、不做 rekey。** pin 把结果经 `atomic_write_bytes_no_follow` 落
   `.{name}.normalized` 点前缀兄弟文件；该 helper 属 `safe_fs`（issue #5 在途，本仓未落地）。
   本模块的 seam 是 doc→doc，落盘归 #21 / #24。**闭包切点**：pin 的
   `_read_limited_text_no_follow`(:978) / `_read_limited_bytes_no_follow`(:966) 不移植——
   二者委派 `safe_fs`，而 `cfg_ic.parse` 已提供有界读与 `MAX_STATE_IC_BYTES`。
4. **无条件重写 minute token：pin 的 `header_changed` 短路不移植。** pin
   （`state_cli.py:288-296`）先把 header 里现存的 minute token 读成 `observed_minute`，
   再按 `round(observed) != round(expected)` 判 `header_changed`；判为「未变」时**原样返回
   字节未动的产物**。于是 pin 有两种可观测形态：(a) 已是目标分钟的整数写法 `27000000` 被
   保留，不会规范化成 `27000000.000000`；(b) 与目标差 `< 30 s` 的时标（`round()` 相等）被
   **静默保留**为旧值。yd 侧一律经 `replace_tokens` 写入 `f"{expected:.6f}"`：本 seam 的
   契约是「header 时间**对应** target」（spec state-tools「重戳保数据」），秒级残差与记法
   漂移都不该跟着产物走进 warm start 链，故这是**收紧**——重戳后的分钟 token 恒为目标值的
   `%.6f` 规范形。代价是干净输入上的一次无意义 splice，行为上无副作用（数据区与 header
   其余 token 仍逐字节不变，见偏离 2）。
   pin 该段的 `except ValueError` 回退子项**不在此登记**：它只在 minute token 解析不出
   浮点时生效，而 yd 侧的 shape 闸门（偏离 1）已保证该 token 必可被 `_as_float` 解析，故那
   条子分支在本 seam 上无对应物，登记它等于登记一条不存在的差异。

`_ensure_utc` 的 pin 语义（`state_cli.py:1186-1189`）逐字保留：**naive datetime 视为 UTC**，
aware 转 UTC。**不得**改成拒绝 naive——那是无 pin 对应物的收窄。

**non-goal（rekey 面，路由 #16 tracker / #24 发布器）**：`_checkpoint_with_header_time`
(`state_cli.py:305`)、`_checkpoint_header_minute`(:327)、`_valid_time_from_header_minute`(:359)、
`_lead_hours_from_run_valid_time`(:1149)、`StateCheckpoint`(:62)、`StateRunContext`(:50)、
`STATE_CHECKPOINT_IC_HEADER_SHAPE_REKEY_SKIPPED`(:87)、`LOGGER`(:46) 一律不落——本 issue 内
无调用方，落地即死代码。

本模块 stdlib-only：零 NWM 运行时 import、零数据库/scheduler 依赖，不写任何文件。
"""

from __future__ import annotations

from datetime import UTC, datetime

from yd_producer.state.cfg_ic import CfgIcDocument
from yd_producer.state.state_qc import (
    cfg_ic_header_minute_index,
    cfg_ic_header_shape,
    line_body,
    replace_tokens,
)

__all__ = [
    "STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID",
    "restamp_to_absolute_time",
]

# NWM@8ae9b8f2 packages/common/state_cli.py:83-86（逐字移植）
# #1430 checkpoint IC header shape reason. Judges an artifact's CONTENT at
# normalization time rather than the publish source's admissibility.
STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID = (
    "STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID"
)


def _ensure_utc(value: datetime) -> datetime:
    """naive datetime 视为 UTC，aware 转 UTC（pin 语义逐字，见模块头）。"""
    # NWM@8ae9b8f2 packages/common/state_cli.py:1186-1189（逐字移植）
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def restamp_to_absolute_time(doc: CfgIcDocument, target: datetime) -> CfgIcDocument:
    """把 header 的 minute token 重戳到 `target` 对应的 epoch 分钟，返回新文档。

    唯一改动集是 header 行，且行内只有 minute token 的字节变化：其余每一行、以及 header
    行内的 mesh 计数 / 列数 token 与行尾符，全部逐字节保持原样。

    header 形状不合法（数值 token 数不为 3 或 4）时抛 `ValueError`，消息以
    :data:`STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID` 起头，且**不产出任何文档**。
    """
    # NWM@8ae9b8f2 packages/common/state_cli.py:256-304（header 覆写段的判定语义移植；
    # 落盘、rekey 与 `accepted` 早退分支按模块头偏离 1/3 剥离）
    header_line = doc.lines[doc.header_index]
    body = line_body(header_line)
    header_tokens = body.split()

    # #1430: a minute index is about to be OVERWRITTEN, so the header's shape has
    # to hold FIRST (yd 侧把 shape 提到 minute-index 之前，见模块头偏离 1). On the
    # #1197 two-token shape the located "minute" token is the mesh-state column
    # count, and writing an epoch-minute over it mints the same poisoned IC that
    # made SHUD allocate ~183 GB. Refuse instead of producing the document.
    shape = cfg_ic_header_shape(header_tokens)
    if not shape.valid:
        raise ValueError(
            f"{STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID}: cfg.ic header is not "
            f"a publishable SHUD layout: {shape.reason}"
        )

    minute_index = cfg_ic_header_minute_index(header_tokens)
    if minute_index is None:  # pragma: no cover - shape 合法蕴含 minute token 必存在
        raise ValueError(
            f"{STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID}: cfg.ic header passed "
            f"the shape gate but carries no minute token: {body!r}"
        )

    # pin `state_cli.py:293`/`:299`：epoch 分钟 = valid_time.timestamp() / 60，写成 %.6f。
    expected_minute = _ensure_utc(target).timestamp() / 60.0
    new_body = replace_tokens(body, {minute_index: f"{expected_minute:.6f}"})
    return doc.with_replaced_lines({doc.header_index: new_body})
