r"""合成 SHUD v2 `yd.rivqdown.dat` 生成器（程序化，零二进制入库）。

布局权威（`docs/products-contract.md` §5.1，由 `rSHUD/R/readout.R:26-31` 与
`SHUD/src/classes/Model_Control.cpp:254-259` 双向核对）：

```text
[0, 1024)          文本头：可打印 ASCII 前缀 + 其后全 NUL（C 侧 `char header[1024] = {}`）
[1024, 1032)       st  —— float64 起始日期
[1032, 1040)       nc  —— float64 列数
[1040, 1040+8*nc)  列编号表 —— nc 个 float64
其后                数据区 —— 每行 nc+1 个 float64（首列相对分钟）
```

独立性硬约束：本模块 MUST NOT 从 `yd_producer` import 任何东西（含
`publish.DAT_FIXED_HEADER_BYTES`）——期望偏移量由本模块**独立**登记，否则
「布局判定是否正确」退化成「被测模块与自己一致」的永真式。

反例形态由同一构造器的参数产出：`layout="v1"`（无文本头、`nc` 落在 offset 0）、
`extra_bytes`（残行尾巴）、`rows` 增减（多/少一行）、`header_text` 的越界字节形态。
"""

from __future__ import annotations

import struct
from pathlib import Path

#: 文本头字节数（v2 的固定前缀，独立登记，不从被测模块导入）。
TEXT_HEADER_BYTES = 1024
#: 一个 float64 的字节数。
FLOAT64_BYTES = 8
#: 文本头 + `st` + `nc` 的定长前缀。
FIXED_HEADER_BYTES = TEXT_HEADER_BYTES + 2 * FLOAT64_BYTES
#: `docs/products-contract.md` §5.2：数据区第 0 列的步长（分钟）。
MINUTE_STEP = 60
#: SHUD 侧写出的默认文本头文本（形态：可打印 ASCII，随后补 NUL）。
DEFAULT_HEADER_TEXT = "SHUD v2 rivqdown yd"


def _pack(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}d", *values)


def build_text_header(text: str) -> bytes:
    """把描述文本补成 1024 字节：可打印 ASCII 前缀 + 其后全 NUL。"""
    raw = text.encode("ascii")
    if len(raw) > TEXT_HEADER_BYTES:
        raise ValueError("header text 超过 1024 字节")
    return raw + b"\x00" * (TEXT_HEADER_BYTES - len(raw))


def build_dat_bytes(
    *,
    nc: int,
    rows: int,
    header_text: str = DEFAULT_HEADER_TEXT,
    header_bytes: bytes | None = None,
    start_date: float = 20260826.0,
    extra_bytes: int = 0,
    layout: str = "v2",
    column_ids: list[float] | None = None,
    truncate_column_table: int = 0,
) -> bytes:
    """合成一份 DAT 的字节。

    `layout="v1"` 产出 rSHUD 的旧布局（无 1024 文本头，`nc` 直接落在 offset 0），
    用作「v2 判据是否有判别力」的反例：`nc == 3988` 时它的前 8 字节正是
    `3988.0` 的 little-endian 表示，「文件够大」这类判据放行它。

    `extra_bytes` 在数据区尾部追加若干字节（残行）；`truncate_column_table` 从列编号
    表尾部砍掉若干个 float64（列编号表不完整）。
    """
    ids = column_ids if column_ids is not None else [float(i + 1) for i in range(nc)]
    table = _pack(ids)
    if truncate_column_table:
        table = table[: len(table) - truncate_column_table * FLOAT64_BYTES]

    data = bytearray()
    for row in range(rows):
        values = [float(row * MINUTE_STEP)]
        values.extend(float(row + 1) for _ in range(nc))
        data.extend(_pack(values))
    data.extend(b"\x00" * extra_bytes)

    if layout == "v1":
        # rSHUD 的 v1 分支：文件开头直接是 `nc`，没有文本头，也没有 `st`。
        return _pack([float(nc)]) + table + bytes(data)
    if layout != "v2":  # pragma: no cover - 构造自检
        raise ValueError(f"未知 layout: {layout!r}")

    head = build_text_header(header_text) if header_bytes is None else header_bytes
    if len(head) != TEXT_HEADER_BYTES:
        raise ValueError("文本头必须恰好 1024 字节")
    return head + _pack([start_date, float(nc)]) + table + bytes(data)


def expected_v2_size(*, nc: int, rows: int) -> int:
    """手算的合法 v2 文件大小（独立于被测模块的算术）。"""
    return FIXED_HEADER_BYTES + FLOAT64_BYTES * nc + rows * (nc + 1) * FLOAT64_BYTES


def write_sparse_dat(
    path: Path, *, nc: int, rows: int, header_text: str = DEFAULT_HEADER_TEXT
) -> Path:
    """写一份**稀疏**的合法 v2 DAT：只落真实头部，数据区靠 `truncate` 撑到合法大小。

    用于「契约检查阶段的读是有界的」这条断言：一个 `st_size` 巨大但头部合法的 DAT，
    检查阶段若整读就会把峰值内存抬到文件大小量级。
    """
    head = (
        build_text_header(header_text)
        + _pack([20260826.0, float(nc)])
        + _pack([float(i + 1) for i in range(nc)])
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head)
    with path.open("r+b") as handle:
        handle.truncate(expected_v2_size(nc=nc, rows=rows))
    return path
