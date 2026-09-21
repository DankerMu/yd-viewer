## Why

M4 阶段 3 的首次现场 `prepare`（2026-09-21）被 `state/cfg_ic.parse` 拒绝：真实 `yd.cfg.ic` 在 mesh 段末尾与 `Index Stage` 列头之间有一行 river 段前导 `3988\t2`（`<river-count> <state-cols>`）。解析器只为 lake 段实现了这类前导行的识别（`_native_lake_section_preamble`），river 的同构行落进「mesh 已满」分支被当作多余 mesh 行 fail closed（模块头「刻意偏离 1」）。NWM pin 在同一位置静默丢行，故应急副本未触发。`prepare`、`init`、tracker 采纳 `.cfg.ic.update` 三条路径共用该解析器，不修则 M4 整体阻塞（issue #305）。

## What Changes

- `LineRole` 新增 `RIVER_PREAMBLE`；`CfgIcDocument` 新增 `river_preamble_index` 与 `declared_river_count`（与 lake 对偶）。
- `parse()`：mesh 段行数已达 header 声明值后，若当前行是两整数（count ≥ 0，cols > 0）且下一有效行是 river 列头，则归 `RIVER_PREAMBLE`；否则维持偏离 1 的拒绝。river 数据行数与声明 count 不符时以 `truncated sectioned IC river body` 拒绝（与 lake 对偶）。
- `render` 字节等价不变；`with_replaced_lines` 不把前导行当作可替换数据行。
- 模块头偏离清单与 `ast` 计数测试同步；`docs/compute-loop-design.md` §8 补一句布局说明（docs commit 先于代码 commit）。
- 不改计数式兼容布局的拒绝（偏离 6）、不在 parser 里校验 `reach_count`、不做任何现场操作。

## Triage

```text
Issue type: bugfix
Fixture level: expanded
Upstream suggested level: expanded (agree)
Blast radius: 三条生产路径（prepare 率定末态、init 首态复制/重戳、tracker 采纳 T+12 checkpoint）对真实 SHUD 状态文件全部不可用；反向风险是把 river 数据行或多余 mesh 行误归为前导，静默丢/错置状态行
Selected risk packs: Schema / columns / field names; Legacy compatibility; Error handling; Documentation
Evidence floor: cd producer && uv run pytest tests/test_cfg_ic.py tests/test_state_qc.py tests/test_restamp.py（新用例先红后绿）；cd producer && uv run pytest；uv run ruff check . && uv run ruff format --check .
```
