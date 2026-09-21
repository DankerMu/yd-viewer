## 1. river 段前导行归属（#305）
- [ ] 1.1 docs：`docs/compute-loop-design.md` §8「`cfg.ic` 是原生分段格式」段补一句：river/lake 段各以一行 `<count> <cols>` 前导开头，属段元数据，不计入状态行（独立 docs commit，在代码之前）。
- [ ] 1.2 `cfg_ic.py`：`LineRole.RIVER_PREAMBLE`、`CfgIcDocument.river_preamble_index`/`declared_river_count`（`__post_init__` 范围校验对偶）、独立新函数 `_native_river_section_preamble`（无溯源标签）、`parse` 的 mesh 已满分支、river 行数收尾校验（登记为第 9 条偏离，docstring「八条」→「九条」）、`with_replaced_lines` 对前导行与 lake 对称不拒绝。
- [ ] 1.3 `cfg_ic_fixtures.build_cfg_ic` 增 `river_preamble: bool = False`（空行插在前导行之前，默认关保持既有 fixture 不变）；`DIRTY_CASES` 新增 `river_preamble` / `river_preamble+blank_lines` / `river_preamble+lake` 三条；`test_cfg_ic.py`：现场同构 fixture（无 lake / 有 lake）roundtrip 与角色断言、`declared_river_count`；三条 parse 负例；`river_preamble_index` 越界的 `__post_init__` 负例；`with_replaced_lines` 替换前导行后 `rows`/`data_line_indices` 不变；`test_module_documents_the_deliberate_deviations` 与 `test_deviation_list_is_closed_against_the_actual_raise_count` 改为 9，`DOCUMENT_API_RAISE_COUNTS["CfgIcDocument.__post_init__"]` 改为 6；`PORTED_HELPERS` 不加新函数；`state_qc` 对含前导 fixture 的 river 行数 == M。先红后绿证据写进实现报告。
- [ ] 1.4 验证：`cd producer && uv run pytest`；`uv run ruff check . && uv run ruff format --check .`；`openspec validate cfg-ic-river-preamble --strict --no-interactive`。
Suggested fixture level: expanded
Minimal mergeable slice: atomic - 一个角色 + 一个判据 + 一条校验，拆开任何一半都不能解析真实文件。

## Risk evidence
- Selected Schema / columns / field names：文件格式解析规则变化。证据：1.3 的 roundtrip + 角色断言 + `declared_river_count` 校验。
- Selected Legacy compatibility：既有 lake 前导、无前导合成 fixture、计数式布局拒绝全部保持。证据：现有 `test_cfg_ic.py`/`test_state_qc.py`/`test_restamp.py` 不改断言全绿。
- Selected Error handling：负例保持红且措辞可判（`surplus sectioned IC mesh row` / `truncated sectioned IC river body`）。证据：1.3 三条负例。
- Selected Documentation：compute-loop §8 一句（1.1）；模块头偏离清单同步（1.2）。
- Not selected Public API / CLI：`parse`/`render` 签名不变，只增字段与枚举值。
- Not selected Config / project setup：无涉及。
- Not selected File IO / path safety：不新增读写路径。
- Not selected Auth / Concurrency / Resource limits / Release：无涉及。
