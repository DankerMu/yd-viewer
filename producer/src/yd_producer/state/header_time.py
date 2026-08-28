r"""SHUD `cfg.ic` header 行的**时间语义与形状**原语（读侧，任务 12.1 落地）。

溯源：`NWM@8ae9b8f2 packages/common/state_qc.py`。本模块的五个公开/半公开符号
（`cfg_ic_header_minute_index`:609、`cfg_ic_header_minute_time`:629、
`_VALID_CFG_IC_HEADER_TOKEN_COUNTS`:646、`CfgIcHeaderShape`:650、
`cfg_ic_header_shape`:664）逐符号自 pin 移植，各带自己的溯源注释。

`_as_float` **从 `yd_producer.state.cfg_ic` 导入，绝不在此重新定义**：pin 的 docstring
逐字声明这几个符号与 `_header_counts` 共享「最后一个数值 token 即 minute-time」规则，
两份 `_as_float` 定义就是两个权威，一旦漂移，「什么算数值 token」在读、移位、校验三侧
会各说各话。

对 pin 的**刻意偏离**（两条，此处即全集）：

1. **刻意不移植 `_valid_time_from_header_minute`**（pin `packages/common/state_cli.py:359`，
   归 issue #9）。该函数在 `0 <= m <= horizon` 时把 minute token 解释为**相对** cycle 的
   分钟（`cycle_time + m`）——对 checkpoint 重戳是对的，对严格前沿闸门是**错的**：
   `docs/compute-loop-design.md` §8 与 `specs/run-controller/spec.md` 的判据逐字是「以绝对
   时间判定」。宽容读法会让一份未重戳的残留 header（如 `720.000000`）在 T=cycle+12h 时被
   判为「对应 T」放行，正是断链的入口。绝对时间比较由 `yd_producer.controller` 自有实现，
   本模块只提供 shape 门与 minute token 提取，**不做任何相对分钟解释**。
2. **行长换行**：pin 的两处单行列表推导超出本仓 ruff 行宽，按 `state/cfg_ic.py` 的移植
   先例折行。判定语义逐字不变。

本模块 stdlib-only：零 NWM 运行时 import、零数据库/scheduler 依赖、零文件系统写入，
且**不做任何 IO**（调用方有界读出 header 行并传入已切分的 token）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from yd_producer.state.cfg_ic import _as_float

__all__ = [
    "CfgIcHeaderShape",
    "cfg_ic_header_minute_index",
    "cfg_ic_header_minute_time",
    "cfg_ic_header_shape",
]


def cfg_ic_header_minute_index(header_tokens: Sequence[str]) -> int | None:
    """Return the position of the SHUD IC header minute-time token, or None.

    Shares the "LAST numeric token is the minute-time" rule with ``_header_counts``
    so every consumer (state QC, runtime header read, runtime time shift) interprets
    native 3-token ``<mesh> <mesh-state-columns> <minute-time>`` and compatibility
    4-token ``<mesh> <river> <lake> <minute-time>`` headers consistently. Returns
    the index into ``header_tokens`` of that trailing numeric token. None when there
    are fewer than two numeric tokens (no count + minute-time pair) or none at all.
    """
    # NWM@8ae9b8f2 packages/common/state_qc.py:609-626（逐字移植）
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
    # NWM@8ae9b8f2 packages/common/state_qc.py:629-643（逐字移植）
    index = cfg_ic_header_minute_index(header_tokens)
    if index is None:
        return None
    return _as_float(header_tokens[index])


# NWM@8ae9b8f2 packages/common/state_qc.py:642-646（逐字移植）
# The only two ``.cfg.ic`` header layouts SHUD's ``Model_Data::read_ic`` and this
# repository's writers ever produce: the native 3-token
# ``<mesh> <mesh-state-columns> <minute-time>`` and the compatibility 4-token
# ``<mesh> <river> <lake> <minute-time>``. Anything else is a malformed delivery.
_VALID_CFG_IC_HEADER_TOKEN_COUNTS = (3, 4)


@dataclass(frozen=True)
class CfgIcHeaderShape:
    """Verdict of the shared ``.cfg.ic`` header content-shape check.

    ``mesh_count`` is the integer value of the FIRST numeric token (the mesh
    element count in both accepted layouts), or None when that token is absent
    or not an integer. ``reason`` is None exactly when ``valid`` is True.
    """

    # NWM@8ae9b8f2 packages/common/state_qc.py:649-661（逐字移植）
    numeric_token_count: int
    mesh_count: int | None
    valid: bool
    reason: str | None


def cfg_ic_header_shape(
    header_tokens: Sequence[str],
    *,
    expected_mesh_count: int | None = None,
) -> CfgIcHeaderShape:
    r"""Validate the content shape of a SHUD ``.cfg.ic`` header line.

    This is the SINGLE source of the header-shape rule shared by the baseline
    registration gate, the direct-grid provision gate and the packaged-IC
    qualification gates. It is pure: the caller reads the header line (bounded)
    and passes the already-split tokens, mirroring this module's existing
    ``expected_*_count`` convention of taking model metadata from the caller.

    A header is valid when it carries exactly three or four numeric tokens
    (:data:`_VALID_CFG_IC_HEADER_TOKEN_COUNTS`) -- and, when
    ``expected_mesh_count`` is supplied, when its leading numeric token is an
    integer equal to that count. Two numeric tokens (the ``23106\t6`` shape from
    issue #1197) is exactly the case the gates must refuse: the runtime's
    "LAST numeric token is the minute-time" rule would overwrite the column
    count with an epoch-minute value.

    Note this is deliberately STRICTER than the runtime injector, which shifts
    any header with three or more numeric tokens: the gates refuse unknown
    (>= 5 token) layouts fail-closed, while the injector keeps its existing
    behaviour there rather than silently flipping on an unknown live layout.

    "Numeric" uses the same :func:`_as_float` rule as
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
