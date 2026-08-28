# NWM@8ae9b8f2 packages/common/state_qc.py
"""SHUD `cfg.ic` 状态变量 QC：结构检查（任务 4.2）、负残差归零（任务 4.4）与 header 判定基座。

溯源：`NWM@8ae9b8f2 packages/common/state_qc.py`。判定语义与数值常量整体移植自该 pin，
逐函数带 `NWM@8ae9b8f2 packages/common/state_qc.py` 注释。

**格式根单一权威**：分段识别与有界读**一律从 `yd_producer.state.cfg_ic` 导入**
（`parse` / `MAX_STATE_IC_BYTES` / `_as_float` 等），本模块 MUST NOT 再移植一份——那会造成
pin 分段逻辑的双权威副本（`nwm-snapshot-inventory.md` §1 中 `packages/common/state_qc.py` 行的禁令）。

**列语义的两层定位方式不同，不得互相污染**（pin 模块 docstring `:24-25` 明写
"Column semantics are applied by position"）：

- 4.2 的 `_check_block_range` 逐字沿用 pin 的**按位置**语义（`_MESH_STATE_COLUMNS` /
  `_RIVER_STATE_COLUMNS` / `_LAKE_STATE_COLUMNS` 按位置命名）；
- 4.4 的**投影列定位**由**段列头文本**查找（pin `:210-222` 的 `current_columns.index("unsat")`
  语义），MUST NOT 写死索引 4。列名就地重切 `doc.lines[section.column_header_index]`，
  不给 `cfg_ic.Section` 加字段。

对 pin 的**刻意偏离**（五条，此处即全集）：

1. **序列化法则：改写一律经 `CfgIcDocument.with_replaced_lines`，只重新序列化被刻意改动的
   那几行**。pin 的 `normalize_state_negative_residuals`(:154) 以 `content.splitlines()`
   拆开、改完后 `"\\n".join(lines)` 重拼**整个文件**，于是每一条**未被改动**的行的 CRLF 被
   归一为 LF、行尾空格与 Tab/空格混排被整体重写。spec state-tools 要求重戳「数据区 MUST
   保持不变」，故 yd 侧的改动集 = **仅真正含负值的数据行**，其余行逐字节保持原样（含各自
   的行尾符）。
2. **改行的内部形态：只替换目标 token 的字节切片，保留行内其余空白布局与行尾符**。pin 改
   行后 `"\\t".join(tokens)` 会把**未被改动**的 token 之间的原始分隔一律重写成单 Tab；元素
   id 与未改动的状态列的字节不该动。故本模块用 `token_spans` / `replace_tokens` 就地
   splice（二者是**公开**名字：`restamp.py` 跨模块 import 它们做 header 行的同款 splice）。
   （这两条偏离**只在脏输入上可证伪**——canonical 化的写法在干净输入上恒绿。）
3. **非有限值 MUST 在任何投影之前被拒**（`normalize_negative_residuals` 抛 `ValueError`）。
   pin 的残差函数**没有** finiteness 门，实测其后果：`nan >= 0.0` 为 False 故 NaN 被**静默
   归零**，`correction=-nan` 与 `nan > 1e-2` 皆为 False 故不计入 `over_tolerance_clamp_count`，
   域均为 `nan` 而 `nan > cap` 为 False，两条阈值门全部放行 → **accepted**；`+inf >= 0.0`
   为 True 故 `+inf` **原样存活**到输出 → accepted；`-inf` 只在落到 **unsat 列或 river
   stage 列**（两条域均和的唯一累加来源，pin `:248-255`）时才因域均 inf 被拦，落在 canopy /
   snow / surface / gw / lake 列时只计入 `max_correction_m` 与 `over_tolerance_clamp_count`
   → 照样 accepted。即 pin 的残差层对非有限值是四条真 fail-open 路径（issue #54 第 1 条）。
   4.2 侧不构成偏离——pin 的 `_check_block_range`(:827) 本就有 isfinite 门，且**次序是
   finiteness 先于负值**（`:827` 早于 `:832`），本模块逐字保持该次序。
4. **超阈值的错误契约：抛 `ValueError` 而非返回 `accepted=False` 的 dataclass**。spec
   state-tools 明写「超阈值 MUST 报错」「处理报错，不产出修正后状态」；承 #8 确立的「本模块
   族的结构性/语义性拒绝一律 `ValueError`」约定，本模块抛 `StateResidualRejected`
   （`ValueError` 子类），异常实例携带 `.evidence`——载荷即 pin
   `StateResidualNormalization.evidence()` 的**完整**字典（含 `accepted=False` 与 `reason`），
   故 receipt 侧证据零丢失，而「不产出修正后状态」由「不返回文档」结构性保证。成功路径仍
   返回 pin 形状的 `StateResidualNormalization`，`accepted` 字段保留以对齐 pin 的证据形状。
5. **段缺席无条件判失败：与 pin 在同一输入上判定反转。** pin 的 `_check_row_counts`
   （pin `state_qc.py:791-798`）对每个 `expected is None` 逐类跳过，`_check_block_range`
   对空 list（`river_rows == []`）返回 `None`，于是「mesh 段完整而 river **列头整段缺席**
   （连列头都没有）、且 `expected_*` 全为 `None`」这份负载在 pin 上得 `passed=True` /
   `structure_complete=True`，在本模块上得 `passed=False` / `False`（round-2 verifier 把
   pin 模块拷出**直接执行**，在同一份字节负载上取到这组对照）。**反转的触发条件是列头
   缺席，不是行数为零**：river 列头在场而其下零行时，该段对本模块而言「存在」，走
   `_check_row_counts` 的行数消息、而该门对 `expected is None` 跳过，两侧同为
   `passed=True`，不构成反转，MUST NOT 拿这种负载做本条的用例。同一输入上的判定反转是
   偏离，不是扩展；扩展的只是**报错措辞**（点名段名而非行数消息，见下）。依据：spec
   state-tools 的第一条 Requirement 独立要求 `cfg.ic`「至少包含 mesh 状态段与 river
   `Stage` 段」，且「缺 river 段被拒」Scenario 不带任何前置条件，故 `doc.river is None`
   在零 `expected_*` 下即可判。lake 段的不对称是刻意的：原生格式里 lake 段本就可选，
   只有调用方声明了**非零** lake 计数、段却不存在时才失败，那一支与 pin 无判定反转。
   段**存在**但行数不符仍走 `_check_row_counts` 的 pin 行数消息（那道门对
   `expected is None` 仍逐类跳过，不受本条影响）。连带后果：`run_state_variable_qc` 与
   `state_ic_structure_complete` 的判定路径含这道**非 pin 闸门**（`_check_missing_sections`），
   二者的逐函数溯源注释因此不得单挂「逐字移植」。

对 pin 的**扩展**（非偏离，pin 无对应面）：

- `StateResidualNormalization` 的 `content: str` 字段（pin `:111`）改名为
  `document: CfgIcDocument`——偏离 1 的连带后果（本层不再持有整文件字符串）。这是**唯一**
  登记的字段改名；`evidence()` 的键集不受影响（pin `:126-151` 本就不含 `content`）。
- **段缺席的报错措辞**：pin 没有「段缺席」概念，`doc.river is None` 在 pin 那里表现为
  `river row count 0 != expected N`。spec state-tools 的 Scenario「缺 river 段被拒 → 指明
  缺失段」要求点名该段，故 `_check_missing_sections` 在段缺席时给出 `missing river section`。
  **措辞**这一面 pin 无对应物，属扩展；该判定的**无条件性**是刻意偏离 5（见上），因为它
  在 `expected_*` 全为 `None` 的同一输入上与 pin 判定反转。
- QC 入口的 `max_bytes` 形参：一路传到 `cfg_ic.parse`，默认值即 `MAX_STATE_IC_BYTES`（未变）。
  本模块**不新增任何读取面**，有界读完全由 `cfg_ic.parse` 承担。

**已知面（不收窄）**：`cfg_ic._as_float` 用裸 `float()`，接受 Python 的下划线数字字面量
（`1_0` → `10.0`），词法宽于 C/Fortran 读者。issue #54 明文「记在第 1 条内作为已知面，不单独
立项」；收窄即偏离 pin 的词法且无 pin 对应物，故本模块不改。

**non-goal**：`_check_water_balance`(pin `:843`) 与 `water_balance` 形参不移植——pin 自标
Lane 2 TODO、恒 `skipped`，数值正确性在本项目显式归 M4，落地即死参数。

本模块 stdlib-only：零 NWM 运行时 import、零数据库/scheduler 依赖，**不写任何文件**
（改写返回新的 `CfgIcDocument`，落盘归 #21 init 首态与 #24 发布器）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yd_producer.state.cfg_ic import (
    MAX_STATE_IC_BYTES,
    CfgIcDocument,
    Section,
    _as_float,
    parse,
)

__all__ = [
    "MAX_RIVER_MEAN_CORRECTION_M",
    "MAX_STATE_IC_BYTES",
    "MAX_UNSAT_MEAN_CORRECTION_M",
    "CfgIcHeaderShape",
    "StateQCResult",
    "StateResidualNormalization",
    "StateResidualRejected",
    "cfg_ic_header_minute_index",
    "cfg_ic_header_minute_time",
    "cfg_ic_header_shape",
    "normalize_negative_residuals",
    "run_state_variable_qc",
    "state_ic_structure_complete",
]

# NWM@8ae9b8f2 packages/common/state_qc.py:45-49（逐字移植）
# Mesh state-variable column layout (SHUD element IC columns, by position).
# Index 0 is the element id; storage/stage state columns follow. These are the
# columns that must be finite and non-negative (depths/storages cannot be < 0).
# Canopy(interception), Snow, Surface(overland), Unsat(soil moisture), Groundwater.
_MESH_STATE_COLUMNS = ("canopy", "snow", "surface", "unsat", "groundwater")

# NWM@8ae9b8f2 packages/common/state_qc.py:51-52（逐字移植）
# River state columns (by position after the id): river stage / channel storage.
_RIVER_STATE_COLUMNS = ("river_stage",)

# NWM@8ae9b8f2 packages/common/state_qc.py:54-55（逐字移植）
# Lake state columns (by position after the id): lake stage.
_LAKE_STATE_COLUMNS = ("lake_stage",)

# NWM@8ae9b8f2 packages/common/state_qc.py:57-60（逐字移植）
# Physically plausible upper bound (metres) for any single storage/stage column.
# Values above this are treated as corrupt (range failure). Generous on purpose;
# real SHUD depths are << 1000 m.
_MAX_STATE_VALUE_M = 1.0e6

# NWM@8ae9b8f2 packages/common/state_qc.py:62-65（逐字移植）
# Native SHUD restart writers can emit small negative depths/stages from numeric
# residuals. Treat sub-centimetre negatives as numeric zero for QC; larger
# negatives remain fatal because they are no longer harmless roundoff.
_NEGATIVE_ZERO_TOLERANCE = 1.0e-2

# NWM@8ae9b8f2 packages/common/state_qc.py:67-106（owner directive 与总体数据，逐字保留实质）
# Negative restart residuals are projected to the physical zero floor
# UNCONDITIONALLY: there is no per-cell repair ceiling. SHUD maps a negative
# unsaturated-zone depth and a negative channel stage to its dry constitutive
# branch, but the restart writer still serializes the raw ODE residual, so the
# published artifact has to be projected before QC sees it.
#
# The per-cell ceiling was removed by owner directive after production falsified
# two successive attempts to size one. The population supports the removal: a
# scan of all 4327 published ``state.cfg.ic`` files found 122,070 negative river
# values across 539 files, and the distribution is bimodal -- 122,057 (99.99%)
# below 1 cm, and every value above 1 cm belonging to two ``basins_dth_yj``
# models at the same reach (max 0.216031 m, seen twice). Above 0.216 m the data
# constrains nothing, so any ceiling in [0.25, 1.0] would accept and reject
# exactly the same observed set; a ceiling sized off the worst observation just
# re-stalls production on the next one.
#
# The domain-mean caps are therefore the ONLY rejection criteria, and they are
# what still fails closed on a basin-wide solver collapse. Their denominators
# are the mesh row count and the RIVER row count respectively. The river cap
# cannot mirror the unsat cap: production river-row counts span 319..43799, so
# 2.0e-4 would give the smallest basin a 0.064 m total budget and fail closed on
# a single decimetre residual. 2.0e-3 leaves a 2 mm domain-average stage error,
# hydrologically negligible, while a routing failure exceeds it by orders of
# magnitude.
#
# Recorded trade: a lone insane value (one -17 m stage among 8622 reaches
# averages ~2e-3) is now published rather than blocked. Availability was chosen
# over strictness by owner directive; ``max_correction_m`` and
# ``over_tolerance_clamp_count`` in the evidence keep it visible in the receipt.
# ``_NEGATIVE_ZERO_TOLERANCE`` survives only as the line between routine cleanup
# and a flagged clamp in that evidence, and as the raw-content range check in
# :func:`run_state_variable_qc`.
MAX_UNSAT_MEAN_CORRECTION_M = 2.0e-4
MAX_RIVER_MEAN_CORRECTION_M = 2.0e-3

# NWM@8ae9b8f2 packages/common/state_qc.py:642-646（逐字移植）
# The only two ``.cfg.ic`` header layouts SHUD's ``Model_Data::read_ic`` and this
# project's writers ever produce: the native 3-token
# ``<mesh> <mesh-state-columns> <minute-time>`` and the compatibility 4-token
# ``<mesh> <river> <lake> <minute-time>``. Anything else is a malformed delivery.
_VALID_CFG_IC_HEADER_TOKEN_COUNTS = (3, 4)


# --- header 判定基座（任务 4.3 重戳的输入，pin 同文件） ---


def cfg_ic_header_minute_index(header_tokens: Sequence[str]) -> int | None:
    """Return the position of the SHUD IC header minute-time token, or None.

    Shares the "LAST numeric token is the minute-time" rule with ``_header_counts``
    so every consumer (state QC, runtime header read, runtime time shift) interprets
    native 3-token ``<mesh> <mesh-state-columns> <minute-time>`` and compatibility
    4-token ``<mesh> <river> <lake> <minute-time>`` headers consistently. Returns
    the index into ``header_tokens`` of that trailing numeric token. None when there
    are fewer than two numeric tokens (no count + minute-time pair) or none at all.
    """
    # NWM@8ae9b8f2 packages/common/state_qc.py:609-627（逐字移植）
    numeric_indices = [
        index
        for index, token in enumerate(header_tokens)
        if _as_float(token) is not None
    ]
    if len(numeric_indices) < 2:
        # Need at least one count token plus the trailing minute-time.
        return None
    return numeric_indices[-1]


def cfg_ic_header_minute_time(header_tokens: Sequence[str]) -> float | None:
    """Return the SHUD IC header minute-time value, or None.

    Uses :func:`cfg_ic_header_minute_index` so the minute-time is read from the
    LAST numeric token regardless of whether a lake count is present.
    """
    # NWM@8ae9b8f2 packages/common/state_qc.py:629-639（逐字移植）
    index = cfg_ic_header_minute_index(header_tokens)
    if index is None:
        return None
    return _as_float(header_tokens[index])


@dataclass(frozen=True)
class CfgIcHeaderShape:
    """Verdict of the shared ``.cfg.ic`` header content-shape check.

    ``mesh_count`` is the integer value of the FIRST numeric token (the mesh
    element count in both accepted layouts), or None when that token is absent
    or not an integer. ``reason`` is None exactly when ``valid`` is True.
    """

    # NWM@8ae9b8f2 packages/common/state_qc.py:649-662（逐字移植）
    numeric_token_count: int
    mesh_count: int | None
    valid: bool
    reason: str | None


def cfg_ic_header_shape(
    header_tokens: Sequence[str],
    *,
    expected_mesh_count: int | None = None,
) -> CfgIcHeaderShape:
    """Validate the content shape of a SHUD ``.cfg.ic`` header line.

    This is the SINGLE source of the header-shape rule. It is pure: the caller
    reads the header line (bounded) and passes the already-split tokens,
    mirroring this module's existing ``expected_*_count`` convention of taking
    model metadata from the caller.

    A header is valid when it carries exactly three or four numeric tokens
    (:data:`_VALID_CFG_IC_HEADER_TOKEN_COUNTS`) -- and, when
    ``expected_mesh_count`` is supplied, when its leading numeric token is an
    integer equal to that count. Two numeric tokens (the ``23106\\t6`` shape from
    issue #1197) is exactly the case the gates must refuse: the "LAST numeric
    token is the minute-time" rule would overwrite the column count with an
    epoch-minute value.

    Note this is deliberately STRICTER than the runtime injector, which shifts
    any header with three or more numeric tokens: the gates refuse unknown
    (>= 5 token) layouts fail-closed, while the injector keeps its existing
    behaviour there rather than silently flipping on an unknown live layout.

    "Numeric" uses the same ``_as_float`` rule as
    :func:`cfg_ic_header_minute_index`, so there is one definition of what
    counts as a token across read, shift and validation.
    """
    # NWM@8ae9b8f2 packages/common/state_qc.py:664-727（逐字移植）
    numeric_values = [
        value
        for value in (_as_float(token) for token in header_tokens)
        if value is not None
    ]
    numeric_token_count = len(numeric_values)
    mesh_value = numeric_values[0] if numeric_values else None
    mesh_count = (
        int(mesh_value)
        if mesh_value is not None and float(mesh_value).is_integer()
        else None
    )

    if numeric_token_count not in _VALID_CFG_IC_HEADER_TOKEN_COUNTS:
        return CfgIcHeaderShape(
            numeric_token_count=numeric_token_count,
            mesh_count=mesh_count,
            valid=False,
            reason=(
                f"IC header carries {numeric_token_count} numeric token(s); "
                f"expected 3 (<mesh> <mesh-state-columns> <minute-time>) "
                f"or 4 (<mesh> <river> <lake> <minute-time>)"
            ),
        )
    if expected_mesh_count is not None and mesh_count != expected_mesh_count:
        return CfgIcHeaderShape(
            numeric_token_count=numeric_token_count,
            mesh_count=mesh_count,
            valid=False,
            reason=(
                f"IC header mesh count {mesh_count} does not match the expected "
                f"mesh element count {expected_mesh_count} "
                f"({numeric_token_count} numeric token(s) in the header)"
            ),
        )
    return CfgIcHeaderShape(
        numeric_token_count=numeric_token_count,
        mesh_count=mesh_count,
        valid=True,
        reason=None,
    )


# --- 行内 token 的字节级 splice（偏离 2 的执行点；pin 无对应面） ---


def line_body(line: str) -> str:
    """返回一行**去掉行尾符**的行体。`with_replaced_lines` 的替换值就是这个形态。"""
    parts = line.splitlines()
    return parts[0] if parts else ""


def token_spans(text: str) -> tuple[tuple[int, int], ...]:
    """按 `str.split()` 的同一套空白规则切出每个 token 的 `[start, end)` 字节切片。

    与 `text.split()` 逐 token 对齐，故 `text.split()[i]` 恰为 `text[start_i:end_i]`。
    存在的理由：`str.split()` 丢掉了 token 之间的原始分隔（空格 / 多空格 / Tab 混排），
    `"\\t".join(...)` 式的回写会把**未改动** token 之间的字节一并重写。
    """
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, char in enumerate(text):
        if char.isspace():
            if start is not None:
                spans.append((start, index))
                start = None
        elif start is None:
            start = index
    if start is not None:
        spans.append((start, len(text)))
    return tuple(spans)


def replace_tokens(text: str, replacements: Mapping[int, str]) -> str:
    """就地替换指定 token 的字节切片，行内其余字节（含分隔空白）逐字保持原样。"""
    spans = token_spans(text)
    result = text
    for index in sorted(replacements, reverse=True):
        if (
            not 0 <= index < len(spans)
        ):  # pragma: no cover - 调用方按行内 token 数取索引
            raise ValueError(
                f"token index {index} out of range [0, {len(spans)}) for {text!r}"
            )
        start, end = spans[index]
        result = result[:start] + replacements[index] + result[end:]
    return result


# --- 任务 4.2 结构检查 ---


@dataclass(frozen=True)
class StateQCResult:
    passed: bool
    checks: dict[str, Any]
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        # NWM@8ae9b8f2 packages/common/state_qc.py:316-321（逐字移植）
        return {
            "passed": self.passed,
            "checks": dict(self.checks),
            "reason": self.reason,
        }


def _source_label(source: Path | str | bytes) -> str:
    """`checks['ic_path']` 的值。pin 只收路径（`str(ic_path)`）；bytes 入口是本仓扩展。"""
    if isinstance(source, bytes):
        return f"<bytes:{len(source)}>"
    return str(source)


def _row_counts(
    doc: CfgIcDocument,
    *,
    expected_mesh_count: int | None,
    expected_river_count: int | None,
    expected_lake_count: int | None,
) -> dict[str, Any]:
    # NWM@8ae9b8f2 packages/common/state_qc.py:357-364（逐字移植的键集）
    return {
        "mesh": doc.mesh.row_count,
        "river": doc.river.row_count if doc.river is not None else 0,
        "lake": doc.lake.row_count if doc.lake is not None else 0,
        "expected_mesh": expected_mesh_count,
        "expected_river": expected_river_count,
        "expected_lake": expected_lake_count,
    }


def _check_missing_sections(
    doc: CfgIcDocument, row_counts: Mapping[str, Any]
) -> str | None:
    """段缺席时点名该段。pin 无此面（见模块头「对 pin 的扩展」），由 spec Scenario 强制。

    **段缺席的判定不依赖调用方计数**：spec state-tools 的第一条 Requirement 独立要求
    `cfg.ic`「至少包含 mesh 状态段与 river `Stage` 段」，故 `doc.river is None` 在零
    `expected_*` 下就已可判，且 `结构检查` Requirement 的「缺 river 段被拒」Scenario 不带
    任何前置条件。`expected_river_count` 只参与**行数比对**（那一半仍按 pin 在 `None` 时
    跳过，见 `_check_row_counts`），不再充当段缺席的开关。

    **river 与 lake 的不对称是刻意的，不是疏漏**：lake 段在原生 `cfg.ic` 格式里**本就
    可选**（`doc.lake is None` 是合法形态，spec 写的是「可能含 lake 段」），故 lake 段缺席
    MUST NOT 无条件失败——只有调用方**声明了**非零 lake 计数、段却不存在时才失败。

    段**存在**但行数不符走 `_check_row_counts` 的 pin 行数消息。
    """
    if doc.river is None:
        expected_river = row_counts.get("expected_river")
        detail = "" if expected_river is None else f" (expected {expected_river} rows)"
        return f"missing river section{detail}"
    expected_lake = row_counts.get("expected_lake")
    if expected_lake is not None and int(expected_lake) != 0 and doc.lake is None:
        return f"missing lake section (expected {expected_lake} rows)"
    return None


def _check_row_counts(row_counts: Mapping[str, Any]) -> str | None:
    # NWM@8ae9b8f2 packages/common/state_qc.py:791-799（逐字移植）
    for kind in ("mesh", "river", "lake"):
        expected = row_counts.get(f"expected_{kind}")
        if expected is None:
            continue
        actual = row_counts.get(kind, 0)
        if int(actual) != int(expected):
            return f"{kind} row count {actual} != expected {expected}"
    return None


def _check_block_range(
    kind: str,
    rows: Sequence[Sequence[float]],
    columns: Sequence[str],
    report: dict[str, Any],
) -> str | None:
    """Validate finiteness, non-negativity, and bounds of state columns.

    Column 0 is treated as the element id (ignored for non-negativity bounds beyond
    finiteness). Named state columns plus any extra trailing storage columns must be
    finite, non-negative, and within ``_MAX_STATE_VALUE_M``.

    判定次序 MUST 保持 pin 的**行内逐列单遍**形态：同一列先 isfinite（`:827`）、再负值
    （`:832`）、再上界（`:836`）。写成「先扫全块非有限、再扫全块负值」的两遍式会在
    「前列为负值、后列为 NaN」的行上报错到另一道门。
    """
    # NWM@8ae9b8f2 packages/common/state_qc.py:802-841（逐字移植，含 isfinite 门与其次序）
    block_report: dict[str, Any] = {"rows": len(rows), "violations": 0}
    report[kind] = block_report
    # Each row must carry the element id (column 0) plus every expected state column.
    # A short row means missing state variables -- a structural QC failure, not a row
    # to be silently range-checked on whatever columns happen to be present.
    min_columns = 1 + len(columns)
    for index, row in enumerate(rows):
        if len(row) < min_columns:
            block_report["violations"] += 1
            return (
                f"{kind} row {index} missing state columns "
                f"(have {len(row)}, need >= {min_columns})"
            )
        # Validate all columns are finite; element id is column 0.
        for col_index, value in enumerate(row):
            if not math.isfinite(value):
                block_report["violations"] += 1
                return f"{kind} row {index} column {col_index} is not finite ({value})"
            # State columns (everything after the id) must be non-negative & bounded.
            if col_index >= 1:
                if value < -_NEGATIVE_ZERO_TOLERANCE:
                    block_report["violations"] += 1
                    name = (
                        columns[col_index - 1]
                        if col_index - 1 < len(columns)
                        else f"col{col_index}"
                    )
                    return f"{kind} row {index} {name} is negative ({value})"
                if value > _MAX_STATE_VALUE_M:
                    block_report["violations"] += 1
                    name = (
                        columns[col_index - 1]
                        if col_index - 1 < len(columns)
                        else f"col{col_index}"
                    )
                    return (
                        f"{kind} row {index} {name} exceeds bound "
                        f"({value} > {_MAX_STATE_VALUE_M})"
                    )
    return None


def run_state_variable_qc(
    source: Path | str | bytes,
    *,
    expected_mesh_count: int | None = None,
    expected_river_count: int | None = None,
    expected_lake_count: int | None = None,
    max_bytes: int = MAX_STATE_IC_BYTES,
) -> StateQCResult:
    """Parse and QC a SHUD ``.cfg.ic`` file.

    ``expected_*`` counts come from the model manifest / accompanying metadata
    (**由调用方传入**：把权威 `reach_count` 接进来是 #21 init / #24 发布器的领域，本层
    不读 `config.toml`). When ``None`` the corresponding row-count check is skipped
    (structure is still parsed).

    Parsing failure is itself a QC failure (never a crash): a malformed or truncated
    IC file returns ``passed=False`` with a reason rather than raising.
    """
    # NWM@8ae9b8f2 packages/common/state_qc.py:324-388（判定次序与消息移植；
    # 判定路径含非 pin 闸门 `_check_missing_sections`（模块头偏离 5），
    # water_balance 见模块头 non-goal，故不是逐字移植）
    checks: dict[str, Any] = {
        "ic_path": _source_label(source),
        "parsed": False,
        "row_counts": None,
        "range": None,
    }
    try:
        doc = parse(source, max_bytes=max_bytes)
    except ValueError as error:
        checks["parse_error"] = str(error)
        return StateQCResult(
            passed=False, checks=checks, reason=f"IC parse failed: {error}"
        )

    checks["parsed"] = True

    # Row-count check against expected element counts.
    row_counts = _row_counts(
        doc,
        expected_mesh_count=expected_mesh_count,
        expected_river_count=expected_river_count,
        expected_lake_count=expected_lake_count,
    )
    checks["row_counts"] = row_counts
    missing_reason = _check_missing_sections(doc, row_counts)
    if missing_reason is not None:
        return StateQCResult(passed=False, checks=checks, reason=missing_reason)
    count_reason = _check_row_counts(row_counts)
    if count_reason is not None:
        return StateQCResult(passed=False, checks=checks, reason=count_reason)

    # Range / non-negative checks per block.
    range_report: dict[str, Any] = {}
    range_reason = _check_block_range(
        "mesh", doc.mesh.rows, _MESH_STATE_COLUMNS, range_report
    )
    if range_reason is None:
        river_rows = doc.river.rows if doc.river is not None else ()
        range_reason = _check_block_range(
            "river", river_rows, _RIVER_STATE_COLUMNS, range_report
        )
    lake_rows = doc.lake.rows if doc.lake is not None else ()
    if range_reason is None and lake_rows:
        range_reason = _check_block_range(
            "lake", lake_rows, _LAKE_STATE_COLUMNS, range_report
        )
    checks["range"] = range_report
    if range_reason is not None:
        return StateQCResult(passed=False, checks=checks, reason=range_reason)

    return StateQCResult(passed=True, checks=checks, reason=None)


def state_ic_structure_complete(
    source: Path | str | bytes,
    *,
    expected_mesh_count: int | None = None,
    expected_river_count: int | None = None,
    expected_lake_count: int | None = None,
    max_bytes: int = MAX_STATE_IC_BYTES,
) -> bool:
    """Return whether an IC file contains every required state row.

    This deliberately checks structure only.  The full physical range checks
    remain the responsibility of :func:`run_state_variable_qc` at state-save
    time.  The tracker (#16) uses this narrower predicate while watching a
    non-atomically rewritten ``cfg.ic.update`` file so it never preserves a
    header-matching but only partially written checkpoint. That promise holds
    **without** caller-supplied counts for the river section: a payload whose
    river ``Stage`` section has not been written yet returns ``False`` even when
    every ``expected_*`` is ``None`` (see :func:`_check_missing_sections`).
    Native SHUD headers do not contain the river count, so callers must still
    pass the model's expected count for a strict **row-count** decision.
    """
    # NWM@8ae9b8f2 packages/common/state_qc.py:391-421（判定次序移植；判定路径含非 pin
    # 闸门 `_check_missing_sections`（模块头偏离 5），故不是逐字移植）
    try:
        doc = parse(source, max_bytes=max_bytes)
    except ValueError:
        return False
    row_counts = _row_counts(
        doc,
        expected_mesh_count=expected_mesh_count,
        expected_river_count=expected_river_count,
        expected_lake_count=expected_lake_count,
    )
    if _check_missing_sections(doc, row_counts) is not None:
        return False
    return _check_row_counts(row_counts) is None


# --- 任务 4.4 负残差归零与域均阈值 ---


@dataclass(frozen=True)
class StateResidualNormalization:
    """pin `StateResidualNormalization`(:109-151) 的形状，`content` 改名为 `document`。"""

    # NWM@8ae9b8f2 packages/common/state_qc.py:109-151（字段与 evidence 键名逐字移植）
    document: CfgIcDocument
    accepted: bool
    reason: str | None
    normalized_value_count: int
    normalized_unsat_row_count: int
    mesh_row_count: int
    max_unsat_correction_m: float
    mean_unsat_correction_m: float
    normalized_river_row_count: int = 0
    river_row_count: int = 0
    max_river_correction_m: float = 0.0
    mean_river_correction_m: float = 0.0
    over_tolerance_clamp_count: int = 0
    max_correction_m: float = 0.0

    def evidence(self) -> dict[str, Any]:
        return {
            "policy": "unbounded_physical_zero_projection_v4",
            "accepted": self.accepted,
            "reason": self.reason,
            "normalized_value_count": self.normalized_value_count,
            "normalized_unsat_row_count": self.normalized_unsat_row_count,
            "mesh_row_count": self.mesh_row_count,
            "normalized_unsat_row_fraction": (
                self.normalized_unsat_row_count / self.mesh_row_count
                if self.mesh_row_count
                else 0.0
            ),
            "max_unsat_correction_m": self.max_unsat_correction_m,
            "mean_unsat_correction_m": self.mean_unsat_correction_m,
            "max_unsat_mean_correction_m": MAX_UNSAT_MEAN_CORRECTION_M,
            "normalized_river_row_count": self.normalized_river_row_count,
            "river_row_count": self.river_row_count,
            "normalized_river_row_fraction": (
                self.normalized_river_row_count / self.river_row_count
                if self.river_row_count
                else 0.0
            ),
            "max_river_correction_m": self.max_river_correction_m,
            "mean_river_correction_m": self.mean_river_correction_m,
            "max_river_mean_correction_m": MAX_RIVER_MEAN_CORRECTION_M,
            "negative_zero_tolerance_m": _NEGATIVE_ZERO_TOLERANCE,
            "over_tolerance_clamp_count": self.over_tolerance_clamp_count,
            "max_correction_m": self.max_correction_m,
        }


class StateResidualRejected(ValueError):
    """域均修正超阈值。`.evidence` 是 pin `evidence()` 的完整载荷（见模块头偏离 4）。"""

    def __init__(self, reason: str, evidence: dict[str, Any]) -> None:
        super().__init__(reason)
        self.evidence = evidence


def _section_column_names(doc: CfgIcDocument, section: Section) -> tuple[str, ...]:
    """就地重切段列头取列名。pin `:197` 的 `[token.strip().lower() for ...]` 语义。"""
    header_line = doc.lines[section.column_header_index]
    return tuple(token.strip().lower() for token in header_line.split())


def _require_finite(doc: CfgIcDocument) -> None:
    """非有限值 MUST 在任何投影之前被拒（见模块头偏离 3）。"""
    for section in (doc.mesh, doc.river, doc.lake):
        if section is None:
            continue
        for row_index, row in enumerate(section.rows):
            for col_index, value in enumerate(row):
                if not math.isfinite(value):
                    raise ValueError(
                        f"{section.name} row {row_index} column {col_index} "
                        f"is not finite ({value}); negative-residual normalization "
                        "refuses to project a non-finite state file"
                    )


def normalize_negative_residuals(doc: CfgIcDocument) -> StateResidualNormalization:
    """Project every negative restart residual to the physical zero floor.

    There is no per-cell repair ceiling: any negative value in any state column,
    of any magnitude, is written as ``0``. Rejection is decided solely by the two
    domain-mean correction caps, which is what still fails closed on a
    basin-wide solver collapse. ``_NEGATIVE_ZERO_TOLERANCE`` does **not** gate the
    projection; it only splits the evidence into routine sub-centimetre cleanup
    and ``over_tolerance_clamp_count`` flagged clamps. See the module-level
    comment on the caps for the population data and the recorded trade.

    改动集 = **仅真正含负值的数据行**，且改动行内只有负值 token 的字节变化（偏离 1/2）；
    非有限值在**任何投影之前**即被拒（偏离 3，pin 的残差函数没有这道门）；超阈值抛
    :class:`StateResidualRejected` 且**不产出修正后文档**（偏离 4）。
    """
    # NWM@8ae9b8f2 packages/common/state_qc.py:154-313（投影语义、阈值判定与证据形状移植；
    # 下一行的 `_require_finite` 是 pin 残差函数**没有**的前置闸门（模块头偏离 3），且 pin
    # 在超阈值时返回 `_rejected(...)` 而本模块抛异常（偏离 4），故不是逐字移植）
    _require_finite(doc)

    mesh_columns = _section_column_names(doc, doc.mesh)
    unsat_index = mesh_columns.index("unsat") if "unsat" in mesh_columns else None
    stage_index: int | None = None
    if doc.river is not None:
        river_columns = _section_column_names(doc, doc.river)
        for candidate in ("stage", "river_stage"):
            if candidate in river_columns:
                stage_index = river_columns.index(candidate)
                break

    mesh_row_count = doc.mesh.row_count
    river_row_count = doc.river.row_count if doc.river is not None else 0

    normalized_value_count = 0
    normalized_unsat_rows = 0
    unsat_correction_sum = 0.0
    max_unsat_correction = 0.0
    normalized_river_rows = 0
    river_correction_sum = 0.0
    max_river_correction = 0.0
    max_correction = 0.0
    over_tolerance_clamps = 0
    replacements: dict[int, str] = {}

    for section in (doc.mesh, doc.river, doc.lake):
        if section is None:
            continue
        is_mesh = section is doc.mesh
        is_river = doc.river is not None and section is doc.river
        for line_index, row in zip(
            section.data_line_indices, section.rows, strict=True
        ):
            body = line_body(doc.lines[line_index])
            token_replacements: dict[int, str] = {}
            unsat_repaired = False
            river_row_repaired = False
            for value_index in range(1, len(row)):
                value = row[value_index]
                if value >= 0.0:
                    continue
                token_replacements[value_index] = "0"
                normalized_value_count += 1
                correction = -value
                max_correction = max(max_correction, correction)
                if correction > _NEGATIVE_ZERO_TOLERANCE:
                    over_tolerance_clamps += 1
                if is_mesh and unsat_index == value_index:
                    unsat_repaired = True
                    unsat_correction_sum += correction
                    max_unsat_correction = max(max_unsat_correction, correction)
                elif is_river and stage_index == value_index:
                    river_row_repaired = True
                    river_correction_sum += correction
                    max_river_correction = max(max_river_correction, correction)
            if unsat_repaired:
                normalized_unsat_rows += 1
            if river_row_repaired:
                normalized_river_rows += 1
            if token_replacements:
                replacements[line_index] = replace_tokens(body, token_replacements)

    mean_correction = unsat_correction_sum / mesh_row_count if mesh_row_count else 0.0
    mean_river_correction = (
        river_correction_sum / river_row_count if river_row_count else 0.0
    )

    def _reject(reason: str) -> StateResidualRejected:
        rejected = StateResidualNormalization(
            document=doc,
            accepted=False,
            reason=reason,
            normalized_value_count=normalized_value_count,
            normalized_unsat_row_count=normalized_unsat_rows,
            mesh_row_count=mesh_row_count,
            max_unsat_correction_m=max_unsat_correction,
            mean_unsat_correction_m=mean_correction,
            normalized_river_row_count=normalized_river_rows,
            river_row_count=river_row_count,
            max_river_correction_m=max_river_correction,
            mean_river_correction_m=mean_river_correction,
            over_tolerance_clamp_count=over_tolerance_clamps,
            max_correction_m=max_correction,
        )
        return StateResidualRejected(reason, rejected.evidence())

    if mean_correction > MAX_UNSAT_MEAN_CORRECTION_M:
        raise _reject(
            "unsat negative-residual domain-mean correction is "
            f"{mean_correction:.9f} m, above "
            f"{MAX_UNSAT_MEAN_CORRECTION_M:.9f} m"
        )
    if mean_river_correction > MAX_RIVER_MEAN_CORRECTION_M:
        raise _reject(
            "river-stage negative-residual domain-mean correction is "
            f"{mean_river_correction:.9f} m, above "
            f"{MAX_RIVER_MEAN_CORRECTION_M:.9f} m"
        )

    normalized = doc.with_replaced_lines(replacements) if replacements else doc
    return StateResidualNormalization(
        document=normalized,
        accepted=True,
        reason=None,
        normalized_value_count=normalized_value_count,
        normalized_unsat_row_count=normalized_unsat_rows,
        mesh_row_count=mesh_row_count,
        max_unsat_correction_m=max_unsat_correction,
        mean_unsat_correction_m=mean_correction,
        normalized_river_row_count=normalized_river_rows,
        river_row_count=river_row_count,
        max_river_correction_m=max_river_correction,
        mean_river_correction_m=mean_river_correction,
        over_tolerance_clamp_count=over_tolerance_clamps,
        max_correction_m=max_correction,
    )
