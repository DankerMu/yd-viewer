## Design

Change surface: `producer/src/yd_producer/state/cfg_ic.py`（`LineRole`、`CfgIcDocument`、`parse`、`with_replaced_lines`）；`producer/src/yd_producer/state/__init__.py` 若需导出；`producer/tests/test_cfg_ic.py`；`docs/compute-loop-design.md` §8 一句。

Must preserve:
- 字节等价回写：`render(parse(b)) == b`，前导行作为原始行原样保留。
- 偏离 1 的实质：mesh 已满后任何**不是** river 前导的数值行仍报 `surplus sectioned IC mesh row`；偏离 2/4/6/7/8 不变。
- lake 前导行为不变：`_native_lake_section_preamble` 的判据、`LAKE_PREAMBLE` 角色、`declared_lake_count` 校验与既有用例全部保持。
- 下游消费者：`state_qc._row_counts`/`_check_missing_sections` 读 `doc.river.rows`/`row_count`，前导行 MUST NOT 进入 `river.data_line_indices`/`rows`；`restamp` 只改 header，不受影响；tracker 经 `parse` 走同一路径。
- `state/__init__.py` 的公开名单只增不改。

Must add/change:
- `LineRole.RIVER_PREAMBLE = "river_preamble"`。
- `CfgIcDocument.river_preamble_index: int | None`、`declared_river_count: int | None`；`__post_init__` 对 `river_preamble_index` 新增一条与 `lake_preamble_index` 同构的范围校验 `raise`，因此 `DOCUMENT_API_RAISE_COUNTS["CfgIcDocument.__post_init__"]` 由 5 改为 6（注释同步说明第 6 条是 river 前导范围检查）。
- `parse()` 在 `section == "mesh"` 分支：`len(mesh_rows) == declared_mesh_count` 且 `_native_river_section_preamble(text, next_line=…, stage_section_count=…)` 返回非 None → 记 `RIVER_PREAMBLE`；该 helper 与 lake 版同构（两 token、整数、count ≥ 0、cols > 0、下一有效行是 river 列头）。
- 收尾校验：`declared_river_count is not None and len(river_rows) != declared_river_count` → `ValueError("truncated sectioned IC river body: have river=…; section declares river=…")`。
- `with_replaced_lines`：与既有 `LAKE_PREAMBLE` 行为对齐——前导行不在任何 `data_line_indices` 内，替换它不被拒绝、`rows` 不受影响（现有 API 对 header/列头/lake 前导即如此；不新增拒绝分支，保持对称）。
- 模块头偏离清单：偏离 1 的表述更新为「mesh 已满后出现的非 river 前导数值行」；新增**第 9 条偏离**「river 前导声明的 count 与实际 river 行数不符时抛 `truncated sectioned IC river body`」（pin 从不识别 river 前导，无对应物），docstring 的「八条」**两处**（偏离清单引言与「模型扩展」节的「上面的八条偏离」）都改「九条」，`test_module_documents_the_deliberate_deviations` 的 `declared == 8` 与 `test_deviation_list_is_closed_against_the_actual_raise_count` 的计数随之改为 9。这是确定项，不是条件句。
- 新 helper `_native_river_section_preamble` 是**独立新函数**：逻辑与 lake 版同构，但 pin 无对应物，MUST NOT 贴 `NWM@8ae9b8f2 … 逐字移植` 溯源注释、MUST NOT 加入测试的 `PORTED_HELPERS`；`_native_lake_section_preamble` 本体与溯源注释保持原样。

Governing invariant: 每一行恰好一个 `LineRole`，状态数据行只归 mesh/river/lake 三段之一，段元数据行（river/lake 前导）不进任何段的行区间，且回写字节等价。

Sibling surfaces:
- `_native_lake_section_preamble`（同构判据的来源；river 版复用其规则，不复制第二套语义）。
- `state_qc` 行数比对（消费 `doc.river.rows`；前导误归为数据行会让 river 行数 +1）。
- `restamp`（消费 `header_index`；无变化，用例回归即可）。
- tracker 的 checkpoint 采纳（`cfg_ic.parse` 同一入口；无独立逻辑）。
- 计数式兼容布局拒绝（偏离 6）：前导行识别只在存在分段列头的路径内生效，不得为无列头文件开后门。

Seams under test: `cfg_ic.parse` / `cfg_ic.render` / `CfgIcDocument.with_replaced_lines` / `state_qc` 的行数检查入口。

Required evidence:
- 现场同构 fixture（header `N 6 t` / mesh 列头 / N 行 / `M 2` / `Index Stage` / M 行，无 lake）→ `parse` 成功；`river_preamble_index` 指向 `M 2` 行、角色 `river_preamble`、`declared_river_count == M`、`mesh.rows` N 行、`river.rows` M 行、`lake is None`；`render` 字节等价。
- 同布局 + lake 段（`K 2` / `Index LakeStage` / K 行）→ 两个前导各归各位，两个 count 都校验。
- 负例：mesh 已满后三 token 数值行 → `surplus sectioned IC mesh row`；两整数行但下一行不是 river 列头 → 同上；river 行数 ≠ M → `truncated sectioned IC river body`。
- `with_replaced_lines` 指向前导行：不拒绝，`rows` 与各段 `data_line_indices` 不变（与 lake 前导现有行为一致）。
- fixture 接入：river 前导以可选参数 `river_preamble: bool = False` 接入 `cfg_ic_fixtures.build_cfg_ic`（默认关，既有无前导 fixture 与全部现有用例逐字节不变）；`blank_lines=True` 时空行插在前导行**之前**（对齐 lake 前导现有写法 `cfg_ic_fixtures.py:186-188`），前导与列头之间不插空行。覆盖不是自动的：`DIRTY_CASES` 新增至少三条显式条目（`river_preamble`、`river_preamble+blank_lines`、`river_preamble+lake`），使 `test_dirty_inputs_roundtrip_byte_identical` 与 `test_every_line_has_exactly_one_role` 实际跑到含前导的输入。
- `__post_init__` 负例：构造 `river_preamble_index` 越界的 `CfgIcDocument` → `ValueError`（镜像既有 `lake_preamble_index` 越界用例 `test_cfg_ic.py:1009-1011`）。
- 新用例对改动前源码红、改动后绿（实现者报告须给出红跑证据）。
- `state_qc` 对含前导 fixture 的 river 行数 == M。

Non-goals: 计数式布局；parser 内校验 `reach_count`；改 pin 其它静默丢行面；现场 prepare 重跑（归 #202）。

Review focus:
1. 前导识别只在 mesh 已满且下一行是 river 列头时触发，`M 2` 形态的 river 数据行（如 river id 2、stage 整数）不会被误判（stage_section_count 与「紧邻列头」判据）。
2. 前导行不进任何 `data_line_indices`，`state_qc` 行数不变。
3. 字节等价；`with_replaced_lines` 对前导行与 lake 前导行为对称（不拒绝、不进 rows）。
4. 偏离清单改为九条并与两个 `ast`/计数测试同步，无遗漏的 `raise`。
5. `_native_river_section_preamble` 无溯源标签、不在 `PORTED_HELPERS`；`_native_lake_section_preamble` 未被改动。
6. docs 一句在代码 commit 之前。
