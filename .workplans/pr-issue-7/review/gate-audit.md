# issue #7 / 任务 3.2 闸门审计（`producer/src/yd_producer/rawcopy.py`）

枚举方式：**AST 遍历全部可执行语句**（`ast.parse` + 自定义 `NodeVisitor`，采集
`If`/`IfExp`/`For`/`While`/`ExceptHandler`/`BoolOp`/`Compare`/`Assert` 与**值传播闸门**
`os.lstat`/`open`/`os.open`/`os.fdopen`/`mkdir`/`unlink`/`rmdir`/`lexists`/`exists`/
`dict.get`/`Subscript`/`isinstance`/`relative_to`/`render_bundle_filename`/`zip`/
`json.load`/`S_ISLNK`/`sorted`/`len`），并剔除纯类型注解子树。MUST NOT 用 grep：
`status = os.lstat(path)`、`SOURCE_DIR_NAMES[source]`、`metadata.get(...)` 这类闸门在
关键字扫描下不可见（project-profile「Orchestration hazards」实测漏约 10 个）。

原始枚举（**round-4 重新导出，并把匹配规则写死使其可复核**）：脚本
`mut/count_nodes.py`（随本轮变异实验的 scratch 一并留存，路径见下）逐字规定匹配规则——
语句/表达式类取 `If`/`IfExp`/`For`/`While`/`ExceptHandler`/`BoolOp`/`Compare`/`Assert`/
`Subscript`；值传播闸门取 `ast.Call` 且 `func.attr`（方法调用）或 `func.id`（裸名）落在
`{lstat, open, fdopen, mkdir, unlink, rmdir, lexists, exists, get, isinstance, relative_to,
render_bundle_filename, zip, load, S_ISLNK, sorted, len}`；只数函数体（含类内方法），跳过
`ast.arg` 与 `AnnAssign` 的注解子树。实跑结果：head `b11138e` 上 **172** 个节点，
round-4 修复后的 head 上 **191** 个。

**「157」这个数字无法复现，故不再沿用。** 我按上述规则以及若干条变体规则（方法调用按
点号全链匹配 / 不数 `Subscript` / 只数 `except` 与 `if`）都试过，没有一组能在
`b11138e` 上得出 157——原审计只写了「采集哪些节点类型」，没有写死方法调用的匹配方式
与注解剔除范围，故该数字不可复核。本轮把规则连同脚本一并钉死，代价是数字与旧记录不
连续；这是有意的：一个不可复核的数字比一个变化的数字更糟。

归并为下表 **103 + 24 = 127** 条闸门。**「无第三桶」这条完整性断言是假的，已作废**：
round-3 verifier 用三个**存活**变异体（E2/E4/E6）加两处穷举 grep 证明有五条闸门既不在
表内、也不在死腿登记内。本轮把这五条各自归位（其中 tier-3 的 `add_note` 现已有杀手
变异体、进表；其余四条是防御腿，进死腿登记并附理由——**MUST NOT 为它们编造覆盖**）。
本轮不重新推导旧有的 88 条表内行；改动是「补上被证明缺席的行 + 纠正被证明错分的行 +
登记本轮新增闸门」，因此 88 -> 103、20 -> 24 的增量逐条可核（见下两表的「round-4」标记
行），而「归并总数 vs 原始节点数」这条映射仍有人工成分，不作完整性断言。

**round-1 修复后重修（PR #65）**：复合闸门按**分量**重新展开（见文末「复合闸门逐分量
重修」），表内由 38 条细化为 71 条，死腿登记 20 条不变。

**round-2 修复后重修（PR #65）**：表内 71 -> **88** 条（新增承接/自算七条、containment
的归一与 inode 身份三条、`rollback` 三条、外部值形态两条、symlink 顺序一条、`sorted(lead_hours)`
与 `tuple | list` 的 `list` 分量各一条）；死腿登记仍 **20** 条（`_Written.rollback` 的两条 `except OSError`
出表——它已不是「尽力而为」而是「保证不抛 + 失败带进外抛异常」，有 R1a/R1b/R1c/R1d 四个杀手变异体；
新入表的死腿是 `O_NOFOLLOW`）。

变异实验环境：私有 scratch 副本 `/private/tmp/claude-501/mutate-issue7-rawcopy`（含
`issue7` 标识、非共用路径），`rsync --exclude='.venv' --exclude='__pycache__'
--exclude='.pytest_cache'`，副本内 `env -u VIRTUAL_ENV uv sync --frozen`，
`PYTHONDONTWRITEBYTECODE=1` 且逐变异体清 `__pycache__`，每个变异体先断言
`yd_producer.__file__` 落在副本内、再对**已 import** 的 `yd_producer.rawcopy` 做
`inspect.getsource` 变异标记断言，最后跑 `tests/test_rawcopy.py`。
控制变异 **M00**（`local_key` 前缀 `raw/` -> `rawX/`）已实测变红，校准通过。
round-1 修复轮在私有副本 `scratchpad/pr65-fix1` 内重跑全套 producer 用例（不只
`test_rawcopy.py`）复核：**基线 749 passed / 16 skipped**（747 条仓内用例 + 2 条副本
卫生用例），**M00 = 13 failed / 736 passed / 16 skipped**。（原轮记录的 7 failed /
37 passed 是 `test_rawcopy.py` 单文件、且用例集尚未扩充时的数字，与本轮不可比。）
副本还需把仓根的 `openspec/` 与 `docs/` 一并 rsync 进去，否则
`tests/test_snapshot_provenance.py` 在收集期就 `FileNotFoundError`，整轮变异实验会
以「1 error」收场而不是给出红/绿。

**round-2 修复轮的变异实验**：私有副本
`/private/tmp/claude-501/mutate-r2fix-issue7-rawcopy-12303`（同一套 profile 措施：三个
rsync exclude + 仓根 `openspec/`/`docs/`、副本内 `env -u VIRTUAL_ENV uv sync --frozen`、
每轮 pytest 均带 `env -u VIRTUAL_ENV` 与 `PYTHONDONTWRITEBYTECODE=1`、逐变异体清
`__pycache__`、每个变异体先跑 `hygiene.py` 断言 `yd_producer.__file__` 与
`rawcopy.__file__` 都落在副本内并对**已 import** 的模块做 `inspect.getsource` 变异 token
断言，每轮跑**全 producer 套件** `pytest tests/`）。基线 **762 passed / 16 skipped**；
控制变异 **M00**（`_local_key` 前缀 `raw/` -> `rawX00MUT/`）= **17 failed / 745 passed /
16 skipped**（RED，校准通过；较 round-1 的 13 failed 多 4 条，是本轮新增的四条依赖复制
路径的用例）。本轮 19 个候选变异体（P1/P2/P3/N1/N2/N3/N4/N5/N6/N8、R1a/R1b/R1c/R1d、
A2a/A2b、C3a/C3b/C3d）**全部变红**，逐条见上表「杀手变异体」列；控制变异 M00 在**十九个
变异体之后**再跑一次，仍是 17 failed / 745 passed / 16 skipped（首尾两次校准一致）。

**round-4 修复轮的变异实验**：私有副本
`/private/tmp/claude-501/mutate-issue7-pr65-round4-corrective`（同一套 profile 措施：三个
rsync exclude + 仓根 `openspec/`/`docs/`、副本内 `env -u VIRTUAL_ENV uv sync --frozen`、
`PYTHONDONTWRITEBYTECODE=1` 且逐变异体清 `__pycache__`、逐变异体断言
`yd_producer.__file__`/`rawcopy.__file__` 落在副本内并对**已 import** 的模块做
`inspect.getsource` token 断言、**恢复一律 `cp` 自工作树**（副本无 `.git`，`git checkout`
是静默 no-op，round-3 有人因此作废过四次运行））。基线 **799 passed / 16 skipped**，
在 **darwin/APFS（大小写不敏感）卷**上测得。本轮共 **15 个变异体**（1 个控制 + 14 个候选：PROBECAL / G1 / G2 / M3 / M4 / M12 /
MBUNDLE / MCFGRIB / MCOUNT / MORDER / E1 / E4 / E3 / REPR），全部对**最终源**跑过一遍。
控制变异 **CTL**（`_local_key` 前缀 `raw/` -> `rawCTLMUT/`）在十四个候选变异体
**之前与之后**各跑一次，两次都是 **18 failed / 781 passed / 16 skipped**，首尾校准一致，
故下表的红/绿不是陈旧字节码或 venv 重绑定的产物。这 14 个候选没有一个走大小写条件
路径，故卷类别不影响本批结论。

**一条本轮才发现的环境陷阱，profile 应吸收**：仓内没有 `.python-version`，
`producer/pyproject.toml` 只写 `requires-python = ">=3.12"`。工作树的 `.venv` 是既有的
3.12；而**全新的 scratch 副本**里 `uv sync --frozen` 会选到当时最新的解释器（本次选到
3.14.2），于是副本与 CI（`.github/workflows/ci.yml` 钉 `python-version: "3.12"`）不同源。
症状是一条依赖 `json` 递归上限的用例在副本上「莫名其妙」变红（CPython 3.14 起 json 的 C
扫描器不再按 Python 递归上限计数）。补救：副本内一律
`uv sync --frozen --python 3.12`。本轮的所有数字都是在 3.12 副本上测得的。

**沿用结论的边界（本轮被改写的函数不沿用）**：round-1 的 71 条表内结论跑在 round-1 源上。
本轮改写了三处闸门所在的代码块（containment 闸门、tier-1 回滚腿、tier-3 回滚腿），故其
原变异体 MF3/M23/MF6 在**最终源**上重跑，逐条仍 RED：MF3-r2（整条 containment 闸门失效）
= 5 failed、M23-r2（tier-1 不回滚）= 7 failed、MF6-r2（tier-3 不回滚）= 1 failed。其余
legacy 行所指的闸门语句在本轮 diff 中**逐字节未变**（`_carried_metadata` 的 selector 取法、
`_check_accumulation` 的取值域三闸门、`_reconstruct_sources` 的两处比对、`_render_manifest`
的字段赋值），结论沿用。

## 表内闸门（103 条，各有杀手变异体，全部实测变红）

| 闸门（行/语义） | 所在函数 | 杀手变异体 | 结果 |
|---|---|---|---|
| `if verdict.complete is not True` | `stage_raw` | M01 放行 | RED |
| `if len(source_config.bundles) != 1`（以 `len(bundles)` 判，非间接判） | `stage_raw` | M02 放行 | RED |
| 重构路径 vs `verdict.expected_files` 逐字比对 | `_reconstruct_sources` | M03 自比自 | RED |
| `Path.cwd() / path`（与 `judge` 同法提升） | `_absolute` | M04 不提升 | RED |
| `render_bundle_filename(...)`（复用 rawscan 渲染面） | `_reconstruct_sources` | M04/M03 + `test_bundle_pattern_validation_is_reused_from_rawscan` | RED |
| `SOURCE_DIR_NAMES[source]`（重构目录段） | `_reconstruct_sources` | M27 改用小写入参 | RED |
| lead 集合**相等**（缺一个 lead 的方向） | `_reconstruct_sources` | `test_verdict_missing_a_lead_variable_set_is_rejected` + M03 | RED |
| lead 集合**相等**（多一个 lead 的方向） | `_reconstruct_sources` | MF4（相等退化成包含）+ `test_verdict_with_an_extra_lead_is_rejected` | RED |
| `not isinstance(variables, tuple \| list)`（键相等仍需判值形态） | `_reconstruct_sources` | MF5（去掉该判值）+ `test_verdict_lead_with_a_malformed_variable_set_is_rejected`（`None`/`5`/`"tmp2m"`） | RED |
| 同一闸门的 `list` 分量（`list` 值集是**合法**输入） | `_reconstruct_sources` | N4（收窄成 `isinstance(variables, tuple)`）+ `test_verdict_lead_variable_set_as_a_list_stages_normally` | RED（反向：误拒即红） |
| `for segment in segments`（祖先段逐段查） | `_reject_symlinks` | M06 只查叶子 | RED |
| `os.lstat(current)` + `S_ISLNK(mode)` | `_reject_symlinks` | M05 放行 | RED |
| `open(<cycle>/manifest.json)`（OSError 腿） | `_load_source_manifest` | `test_absent_source_manifest_fails_closed`（缺文件即红） | RED（M07 同族） |
| `json.load` 的 `JSONDecodeError` 分量 | `_load_source_manifest` | MS8（只留 `UnicodeDecodeError`）+ `test_unparsable_source_manifest_fails_closed` | RED |
| `json.load` 的解码（`UnicodeDecodeError`）分量 | `_load_source_manifest` | MS7（只留 `JSONDecodeError`）+ `test_non_utf8_source_manifest_bytes_fail_closed` | RED |
| `isinstance(payload, Mapping)` | `_load_source_manifest` | `test_no_bare_stdlib_exception_escapes_for_each_failure_shape`（`[]`） | RED |
| `from_dict` except 元组的 `KeyError` 分量 | `_load_source_manifest` | `test_source_manifest_without_entries_key_fails_closed` | RED |
| `from_dict` except 元组的 `TypeError` 分量 | `_load_source_manifest` | MS5（去掉该分量）+ `..._never_leaks_a_bare_exception[entries-5-TypeError]` | RED |
| `from_dict` except 元组的 `ValueError` 分量 | `_load_source_manifest` | MS4 + `...[cycle_time-nope-ValueError]` | RED |
| `from_dict` except 元组的 `AttributeError` 分量 | `_load_source_manifest` | MS6 + `...[cycle_time-5-AttributeError]` | RED |
| `SOURCE_FORECAST_HOURS_KEY not in metadata` | `_source_forecast_hours` | M07 放行 | RED |
| `isinstance(declared, list)` | `_source_forecast_hours` | M08b 放宽成 `list \| str` | RED |
| `isinstance(value, bool)`（析取左半） | `_source_forecast_hours` | MX3 去掉该析取分支 + `test_bool_forecast_hours_entry_fails_closed`（`[0, 3, 6, True]`） | RED |
| `not isinstance(value, int \| str)`（析取右半） | `_source_forecast_hours` | `test_non_integer_typed_forecast_hours_entry_fails_closed`（`[0, 3, None]`） | RED |
| `int \| str` 联合的 `str` 分量（数字字符串是合法取值） | `_source_forecast_hours` | `test_numeric_string_forecast_hours_are_accepted`（`[0, 3, "6"]`） | RED（反向：误拒即红） |
| `int(value)` 的 ValueError 腿 | `_source_forecast_hours` | `test_non_numeric_forecast_hours_entry_fails_closed` | RED |
| `if uncovered`（源侧小时表 ⊇ yd 小时表） | `stage_raw` | M09 放行 | RED |
| `source_index.get((lead, variable)) is None` | `_build_entries` | M10 放行 | RED |
| `for key in ENTRY_METADATA_KEYS` / `key not in metadata` | `_carried_metadata` | M11 放行 | RED |
| `metadata.get(IDX_SELECTORS_KEY)` + `isinstance(selectors, Mapping)` | `_carried_metadata` | M33 空 Mapping 也落盘 | RED |
| `selectors.get(variable)` + `isinstance(selector, Mapping)` | `_carried_metadata` | M31 塞整个复数 Mapping / `test_variable_absent_from_idx_selectors_omits_the_singular_key` | RED |
| `carried[IDX_SELECTORS_KEY] = dict(selectors)` | `_carried_metadata` | M32 丢复数键 | RED |
| `variable not in ACCUMULATION_VARIABLES`（按变量名判，非按 source 分支） | `_check_accumulation` | M12/M13 + IFS 用例 | RED |
| `isinstance(selector, Mapping)`（R4B2 子 Mapping 必在） | `_check_accumulation` | M12 放行 | RED |
| `for key in ACCUMULATION_TYPE_KEYS` / `selector.get(key) is not None` | `_check_accumulation` | M16 丢 `accumulation_policy` 别名 | RED |
| `accumulation_type is None` | `_check_accumulation` | M15 注入 `or "cumulative_since_cycle"` | RED |
| `accumulation_type not in ACCUMULATION_TYPES` | `_check_accumulation` | M13 放行 | RED |
| `accumulation_type == INTERVAL_BUCKET and not any(step_range)` | `_check_accumulation` | M14 放行 | RED |
| `STEP_RANGE_KEYS` 的 `stepRange` 别名 | `_check_accumulation` | M17 丢别名 | RED |
| `SOURCE_FORECAST_HOURS_KEY`（源侧只强制 `forecast_hours`） | 模块常量 | M18 改成强制 `requested_forecast_hours` | RED |
| `os.path.lexists(candidate)` + `_copy_one` 的 `O_EXCL` | `stage_raw`/`_copy_one` | M19 两处同时去掉 | RED |
| `os.path.lexists(manifest_path)` + `_write_manifest` 的 `O_EXCL` | `stage_raw`/`_write_manifest` | M20 两处同时去掉 | RED |
| `os.open(target, ...)` 的非 FileExists OSError 腿 | `_copy_one` | M36b 收窄成 FileNotFoundError | RED |
| `if before != after`（复制前后 lstat 比对） | `_copy_one` | M21 放行 | RED |
| `_identity` 的 `st_size` 分量 | `_identity` | MX2c（`st_size`→0）+ `test_source_appended_during_copy_is_caught_by_size` | RED |
| `_identity` 的 `st_mtime_ns` 分量 | `_identity` | M22 退化成只比 size + `test_source_mutated_during_copy_leaves_no_partial_copies` | RED |
| `_identity` 的 `st_ino` 分量 | `_identity` | MX2a（`st_ino`→0）+ `test_source_replaced_by_an_equal_stat_file_is_caught_by_inode` | RED |
| `_identity` 的 `st_mode` 分量 | `_identity` | MX2b（`st_mode`→0）+ `test_source_chmodded_during_copy_is_caught_by_mode` | RED |
| `written.rollback()`（`RawStagingError` 腿） | `stage_raw` | M23 不回滚 | RED |
| `written.rollback()`（**非** `RawStagingError` 腿，并收敛成 `copy-failed`） | `stage_raw` | MF1a 收窄回 `except RawStagingError` + `test_bare_exception_inside_the_write_block_still_rolls_back` | RED |
| `written.rollback()`（`BaseException` 腿，且**不**改写异常类型） | `stage_raw` | MF6 该腿不回滚 + `test_keyboard_interrupt_mid_copy_still_rolls_back` | RED |
| `rollback` 的 `except Exception`（**保证不抛**：它抛出的异常会替换正在外抛的失败） | `_Written._remove` | R1a（收窄回 `except OSError`）+ `test_rollback_failure_is_reported_and_never_replaces_the_staging_error` | RED |
| 回滚失败进入外抛异常（tier-1 的 `add_note`） | `stage_raw` | R1d（不挂 note）+ 同上用例 | RED |
| tier-2 的清理声明**条件化**（清理失败时不得宣称「已清理」） | `stage_raw` | R1b（无条件宣称已清理）+ `test_tier2_message_stops_claiming_cleanup_when_rollback_failed` / R1c（无条件宣称残留）+ `test_successful_rollback_still_reports_a_clean_cleanup` | RED |
| `_render_manifest` 的 `.encode("utf-8")`（序列化前置到准入期） | `_render_manifest` | MF1b 改成 `errors="surrogatepass"` + `test_non_utf8_encodable_carried_value_is_refused_before_any_write` | RED |
| `for variable in verdict.expected_variables[lead]`（逐变量扇出，集合相等两方向） | `_build_entries` | M24 漏一条 / M25 多一条 | RED / RED |
| `local_key=local_key`（entry 的 key 由 yd **自己算**，不照抄源 manifest） | `_build_entries` | MX1（`local_key=source_entry.local_key`）+ `test_full_cycle_copies_files_and_manifest_triples_match` / `test_manifest_json_matches_the_producer_consumer_contract`（源 fixture 带 `nwm-bucket/` 发散前缀） | RED |
| `work_dir` 在 `raw_root` 之下（析取左半） | `stage_raw` | MF3 去掉该闸门 + `test_work_dir_under_raw_root_is_a_config_error` | RED |
| `raw_root` 在 `work_dir` 之下（析取右半） | `stage_raw` | MF3 + `test_raw_root_under_work_dir_is_a_config_error` | RED |
| `_normalized` 的 `resolve()`（折叠 symlink 与 `..` 后再判包含） | `stage_raw` | C3b（回到未归一的纯词法闸门）+ `test_symlinked_work_dir_pointing_into_raw_root_is_refused` / `test_dotdot_aliased_work_dir_inside_raw_root_is_refused` / `test_work_dir_reached_through_dotdot_outside_raw_root_stages_normally`（无误拒判别器） | RED |
| `_contains_by_identity`（inode 身份，抓 `resolve()` 关不掉的大小写别名） | `stage_raw` | C3a（只留 `resolve()` 比较）+ `test_case_aliased_work_dir_inside_raw_root_is_refused` | RED |
| `_normalized` 的 `ValueError` 腿（NUL 字节路径） | `_normalized` | C3d（只接 `OSError`）+ `test_null_byte_work_dir_is_refused_without_leaking_a_bare_value_error` | RED |
| `source_id=SOURCE_DIR_NAMES[source]`（存储身份逐源非对称；**自算**不照抄源） | `_render_manifest` | M26 用小写入参 / N2（照抄源 `source_id`，源侧已带 `mirror-` 前缀） | RED |
| `expected_checksum`/`expected_size_bytes` 留 None | `_build_entries` | M28 写 0 | RED |
| `manifest_uri=None` | `_render_manifest` | M29 写 `file://` | RED |
| manifest 级四键 | `_manifest_metadata` | M30 少写 `requested_forecast_hours` | RED |
| `FIRST_/LAST_FORECAST_HOUR_KEY` 由本轮 lead **自算**（不照抄源 manifest） | `_manifest_metadata` | N3 + `test_manifest_level_hour_keys_are_self_computed_not_copied`（源侧小时表两端都比本轮宽） | RED |
| `cycle_time=cycle.astimezone(UTC)`（manifest 级**自算**） | `_render_manifest` | N1（照抄源 manifest 的 `cycle_time`，源侧已偏移到另一个 cycle） | RED |
| `remote_url=source_entry.remote_url`（取源 entry 的**同名字段**，不取承接来的 `logical_remote_url`） | `_build_entries` | N6（源侧两个 URL 已发散到不同 host） | RED |
| entry 级 `cycle_time` **逐字承接**（不自算） | `_carried_metadata` | P2（改为 `cycle.astimezone(UTC).isoformat()`；源侧写 `Z`、自算出 `+00:00`） | RED |
| entry 级 `valid_time` **逐字承接**（不自算） | `_carried_metadata` | P3（改为 `cycle + timedelta(hours=lead)`） | RED |
| `selectors.get(variable)`（单数键由**复数键按变量**取，不照抄源侧单数键） | `_carried_metadata` | P1（改读 `metadata.get(IDX_SELECTOR_KEY)`；4 变量 bundle 上源侧按 §3.1 根本不写单数键） | RED |
| `tuple(sorted(source_config.lead_hours))`（无序 `lead_hours` 是合法配置） | `_reconstruct_sources` | N5（去 `sorted`）+ `test_unsorted_lead_hours_stage_normally`（`(6, 0, 3)` 被误判 `verdict-mismatch` 即红） | RED（反向：误拒即红） |
| `isinstance(accumulation_type, str)`（求哈希前先判形态） | `_check_accumulation` | A2a + `test_unhashable_accumulation_type_fails_closed`（`list`/`dict`） | RED |
| `isinstance(entry.variable, str)`（当字典键前先判形态） | `_index_source_entries` | A2b + `test_unhashable_entry_variable_fails_closed` | RED |
| symlink 拒绝**先于**读源 manifest 的顺序 | `stage_raw` | N8（把读 manifest 提到走查之前）+ `test_symlinked_cycle_directory_is_refused_before_the_manifest_is_read`（链目标里放畸形 manifest，两种顺序按 kind 分开） | RED |
| `source not in SOURCE_DIR_NAMES`（形参守卫） | `_validate_params` | M34 放行 | RED |
| `isinstance(cycle, datetime)` | `_validate_params` | M34 同族 | RED |
| `utcoffset() != timedelta(0)` 的 naive 腿（`utcoffset()` 为 `None`） | `_validate_params` | 参数化行 naive `datetime` | RED |
| `utcoffset() != timedelta(0)` 的非零偏移腿 | `_validate_params` | MS3（改成 `is None`）+ 参数化行 `UTC+8` | RED |
| 整点三元组的 `minute` 分量 | `_validate_params` | 参数化行 `00:30` | RED |
| 整点三元组的 `second` 分量 | `_validate_params` | MS1（`second`→0）+ 参数化行 `00:00:30` | RED |
| 整点三元组的 `microsecond` 分量 | `_validate_params` | MS2（`microsecond`→0）+ 参数化行 `00:00:00.000030` | RED |
| 「零写入」时序（任何检查失败前不建目录） | `stage_raw` | M35 提前建目录 | RED |
| `_ensure_dir` 的 `while probe.exists()` 账本 | `_ensure_dir` | M23/M35（回滚后 work 必须回到空） | RED |
| `_ensure_dir` 的账本**登记时序**（先记账再 mkdir） | `_ensure_dir` | MF2 把 `written.dirs.extend` 移回 mkdir 之后 + `test_mkdir_failing_midway_leaves_no_directories_behind` | RED |
| `_ensure_dir` 的 `mkdir` OSError 腿 | `_ensure_dir` | `test_unwritable_work_directory_reports_copy_failed` | RED |
| `_copy_one` 的分块读写 `if not chunk` | `_copy_one` | M00 控制变异 + 内容逐字节断言 | RED |
| **round-4** 准入期收口块（floor）：`stage_raw` 体内第一条语句即 `try`，其后紧接写入期起点 | `stage_raw` | PROBECAL（`except Exception` 只重抛不收敛）+ `test_admission_phase_is_structurally_enclosed_by_one_floor` 与 **21 条** AST 派生的参数化逃逸探针（19 条注入点变红；`ConfigError`/`RawStagingError` 两条按 happy-path 不可达登记在 `ADMISSION_UNREACHED_ON_HAPPY_PATH`，故不参与判别） | RED（21 failed：19 条探针 + 结构断言 + 递归形态回归） |
| **round-4** entry 级 `forecast_hour` 形态闸门（拒 `int()` 有损归一：`3.9`/`3.0`/`"3"`/`True`） | `_reject_lossy_forecast_hours` | G1（不调用该闸门）+ `test_lossy_forecast_hour_shape_is_refused_before_any_write` 四行 | RED（4 failed；**重复键用例不红** -> 与下一行可分离） |
| **round-4** `(forecast_hour, variable)` 索引的 injectivity 守卫（拒重复键，不后写覆盖） | `_index_source_entries` | G2（守卫短路）+ `test_duplicate_source_entry_key_is_refused` / `..._on_a_lead_this_round_does_not_request...` | RED（2 failed；**形态用例不红** -> 与上一行可分离） |
| **round-4** `_remove` 的 `{exc!r}` 改走不抛的 `_safe_repr`（`rollback`「保证不抛」在 repr 自身抛异常时也成立） | `_Written._remove` | REPR（改回 `{exc!r}`）+ `test_rollback_does_not_raise_when_the_exception_repr_itself_raises` | RED |
| **round-4（原第三桶，现进表）** tier-3 的 `if failures: exc.add_note(...)` | `stage_raw` | E4（删掉该分支）+ `test_keyboard_interrupt_with_a_failing_rollback_carries_the_residue_note`（中断复制 **与** 回滚原语失败两处同时注入） | RED（此前 E4 存活） |
| **round-4（原误记为死腿，现进表）** `rollback` 的目录**逆序**（深者先 `rmdir`） | `_Written.rollback` | E3（`reverse=True` -> `False`）| RED（9 failed，全部是残留 oracle） |
| **round-4** `_contains_by_identity` 的 `inner` **自身**分量（`inner` 本身是指向 `outer` 的链） | `_contains_by_identity` | E1（收窄成只走 `inner.parents`）+ `test_contains_by_identity_catches_inner_being_a_link_to_outer`（函数级、无特权、与卷的大小写敏感性无关） | RED |
| **round-4** `os.path.samestat` 判据本身（inode 身份） | `_is_same_dir` | C3a（只留 `resolve()` 比较）+ E1 | RED |
| **round-4** entry `expected_checksum` 落 `None`（**不承接**源侧取值） | `_build_entries` | M3（改为 `source_entry.expected_checksum`；源侧已偏移成非 `None`） | RED |
| **round-4** entry `expected_size_bytes` 落 `None`（**不承接**源侧取值） | `_build_entries` | M4（改为 `source_entry.expected_size_bytes`） | RED |
| **round-4** entry metadata 白名单的「**仅含**」半边 | `_carried_metadata` | M12（`carried = dict(metadata)` 整份照抄；源侧已多一个非承接键） | RED |
| **round-4** `bundle` **逐字**承接（不按已知三键重建） | `_carried_metadata` | MBUNDLE（按 `layout`/`variables`/`physical_file_count` 重建；源侧多一个不可推导分量） | RED |
| **round-4** `cfgrib_filter_by_keys` **逐字**承接（不由 `grib_short_name` 现造） | `_carried_metadata` | MCFGRIB（现造单键 Mapping；源侧多一个不可推导分量） | RED |
| **round-4** entry **集合**由 verdict 定（不照搬源 entry 列表） | `_build_entries` | MCOUNT（把源侧多出的 entry 一并落盘；源侧已多出一个变量与两个 lead） | RED（10 failed） |
| **round-4** entry **顺序**为 lead 升序 × variables 声明序（不照抄源侧顺序） | `_build_entries` | MORDER（按 `source_index` 的插入序重排；源侧 entry 顺序已整体反转） | RED |

## 死腿登记（24 条，本轮无判别器；逐条附理由）

| 闸门 | 所在函数 | 为什么没有判别器 |
|---|---|---|
| `except TypeError`（`os.fspath` 非路径入参） | `_absolute` | 防御性：形参类型错属调用方；`ConfigError` 面保留但本轮不构造该输入 |
| `except OSError`（`Path.cwd()` 不可用） | `_absolute` | 需删除进程 cwd，测试内不可靠复现；与 `judge` 同形分支 |
| `except ValueError`（`relative_to`） | `_reject_symlinks` | 不可达：路径由 `raw_root` 拼出，恒在其下 |
| `except FileNotFoundError`（祖先段缺失） | `_reject_symlinks` | 调用序上 `judge` 刚判过存在；残余窗口按 Non-goals 归组 12 |
| `except OSError`（祖先段不可 lstat） | `_reject_symlinks` | 该权限形态下 `judge` 已判 `unreadable` -> `complete=False`，走不到 staging |
| `isinstance(metadata, Mapping)` | `_carried_metadata` | 无判别力：`DownloadManifest.from_dict` 恒产出 `dict`；保留为形态防御 |
| `isinstance(metadata, Mapping)`（BoolOp 左半） | `_source_forecast_hours` | 同上（右半 `not in metadata` 在表内） |
| `except FileExistsError` | `_copy_one` | 预检已拒；仅覆盖预检→`O_EXCL` 之间的 TOCTOU 窗口 |
| `except FileExistsError` | `_write_manifest` | 同上 |
| `except OSError`（读写循环中途，如写到一半 ENOSPC） | `_copy_one` | 需真实填满文件系统；注入点选在 `os.open`（M36b/用例已覆盖同一 kind） |
| `except OSError`（复制后 `_identity`） | `_copy_one` | 需在复制后瞬间让源不可 lstat，测试内不可靠 |
| `except OSError`（manifest 写入中途） | `_write_manifest` | 同上（创建腿已由 M36b 同族覆盖） |
| `if probe.parent == probe` | `_ensure_dir` | 不可达：文件系统根恒存在，`while` 先退出。该支一旦成立会把根登记进账本，`rollback` 的 `rmdir("/")` 只会以 EBUSY/EACCES 落进失败清单、不删任何东西（round-2 verifier 批次外记录，理由已写进源码注释） |
| `zip(..., strict=True)` | `stage_raw` | 不可达：`targets` 由 `rebuilt` 同一推导式构造，长度恒等 |
| `rebuilt[0][1].parent if rebuilt else raw_path` 的 else 支 | `stage_raw` | 不可达：空 `lead_hours` 已在 `_validate_params` 以 `ConfigError` 拒 |
| `if not source_config.lead_hours` | `_validate_params` | 上游 `judge` 先以 `ConfigError` 拒同一输入；此处是本模块自己的兜底 |
| `if kind not in ERROR_KINDS` | `RawStagingError.__init__` | 自检式断言：本模块内所有 raise 点都用字面量 kind，越域取值只可能来自将来的改动 |
| `hours[0]` / `hours[-1]` | `_manifest_metadata` | 与上面空 `lead_hours` 同一前置，非空后恒有定义 |
| `sorted(...)`（消息拼装与 `uncovered` 的展示排序；**不含** `_reconstruct_sources` 的 `sorted(lead_hours)` 与 `rollback` 的目录逆序，两条均已在表内） | 多处 | 消息拼装是展示、无判定语义；`uncovered` 的判定由 `if uncovered` 承担（表内）。**round-4 更正**：原行按**语句形态**（都是 `sorted`）把三处并成一条，于是把 `rollback` 的深者优先 `rmdir` 这条**活闸门**错记成死腿（E3 变异体实测红 9 条）。归并 MUST 按语义、不按语句形态 |
| `O_NOFOLLOW`（常量定义与 `os.open(source_path, O_RDONLY \| O_NOFOLLOW)`） | 模块常量 / `_copy_one` | 纵深防御的**第二道闩**（`rawcopy.py:126-128` 自述）：叶子与祖先段的链已由 `_reject_symlinks` 在任何 open 之前逐段 `lstat` 拒绝，单摘该标志不变红（N7 变异体存活于全套件——**round-2 verifier C 实测**，非本轮自跑）。本仓既有范式：`producer/tests/test_safe_fs_refusals.py:19-27` |
| **round-4（原第三桶）** `except FileNotFoundError: pass`（账本反向多记的祖先段本轮可能根本没建出来） | `_Written._remove` | 无判别力：E2 变异体（删掉该腿、让 FNF 落进 `except Exception`）在全套件下**存活**。它是防御腿而不是缺陷——把「本轮没建过的路径」记成清理失败会让残留消息反向说谎，但今天没有一条用例区分「记进 failures」与「跳过」。补覆盖需要构造「账本里有、磁盘上从未存在」的路径，而这正是 `_ensure_dir` 反向多记的正常形态；**MUST NOT 为它编造覆盖** |
| **round-4（原第三桶）** `except (OSError, ValueError)` 的 **`OSError`** 分量 | `_normalized` | 无判别力：E6 变异体（收窄成只接 `ValueError`）**存活**。`ValueError` 分量在表内（C3d + NUL 字节用例）；`OSError` 分量需要一条 `resolve()` 抛 `OSError` 的路径（ELOOP/权限），在 tmp 下不可靠复现 |
| **round-4（原第三桶）** `except (OSError, ValueError): return False` 腿 | `_is_same_dir` | 防御腿：不存在/不可 stat 的段不可能与另一侧同 inode。走到该腿需要 `os.stat` 在两个已 `resolve()` 的根上失败，seam 级不可靠构造。判据本身（`samestat`）已在表内 |
| **round-4（原第三桶）** `getattr(config.raw, source)` | `_validate_params` | 不可达：同函数上一条闸门已保证 `source in SOURCE_DIR_NAMES`，而 `RawConfig` 恰有这两个属性，故 `AttributeError` 走不到。函数 docstring 把它列为「要挡住的裸异常」之一，此处登记它今天由**前置闸门**而非自身兜底 |


## 复合闸门逐分量重修（PR #65 round 1）

原表把若干**多分量闸门**（元组比对、析取谓词、多类型 except 元组）记成一行，并用一个
**单分量**输入为整行背书。这类记法会把「某一分量根本没有判别器」记成 RED。逐条更正：

- `_identity` 四元组（原第 61 行）：原记「M22 退化成只比 size / RED」。M22 确实红，但
  它只判 `st_mtime_ns` 这一个分量；PR #65 的 batch-C 复核在 head `6482ff2` 上实测
  `st_ino`→0 与 `st_mode`→0 两个变异体**存活**（全仓 724 passed / 16 skipped，与基线
  逐字节相同）。现拆成四行，逐分量各有杀手变异体（MX2a/b/c + M22）。
- `forecast_hours` 逐项类型闸门（原第 41 行）：原记整条复合谓词、却只由 `[0, 3, None]`
  背书，该输入只走右析取分支；去掉 `isinstance(value, bool)` 左分支的变异体（MX3）
  在 head `6482ff2` 上**存活**（同样是 724 passed / 16 skipped）。现拆成三行（左析取 / 右析取 / `int | str` 联合的 `str` 分量）。
- 整点与时区闸门：原记「整点三闸门 + 参数化用例」一行，实际只有 `minute` 分量与 naive
  腿被行使。现拆成六行，`second`/`microsecond`/非零偏移各自补了参数化行与变异体。
- `json.load` 与 `from_dict` 的多类型 except 元组：原各记一行。现按分量拆开；注意
  `UnicodeDecodeError` 与 `json.JSONDecodeError` **都是** `ValueError` 的子类，故
  「只去掉其中一个名字、仍留 `ValueError`」是等价变异体——有判别力的变异体是
  MS7/MS8（把元组收窄到只剩另一个分量）。

**原表完全缺席的一行**：`_build_entries` 里的 `local_key=local_key` 赋值。原表唯一与
`local_key` 相关的条目是控制变异 M00，而 M00 变红走的是 `_local_key` 在**复制目标**
路径上的那次共享调用（`stage_raw` 的 `targets` 推导式），不是 manifest 的 entry 字段：
把 entry 的 key 改成照抄源 manifest（MX1）时，head `6482ff2` 上全仓 724 passed /
16 skipped、与基线逐字节相同——**无一条用例判别**。根因在
fixture 上游——`entry_payload()` 用 yd 应当独立算出的那个值去喂输入侧，输入与期望输出
是同一个值，「自己算」与「照抄源」不可区分。修法是把输入侧偏移一个 `nwm-bucket/`
前缀（源 manifest 是不受信的外部 JSON，没有任何东西强制两者重合），断言维持字面构造。

- 本轮 MS1–MS8 这批分量变异跑在加入 `expected_variables` 值面形态守卫**之前**的源上；
  被变异的两个函数（`_validate_params`、`_load_source_manifest`）及其杀手用例在那份源
  与最终源之间逐字节相同，故结论沿用。MX/MF 两批（13 个变异体，含 M00 控制变异与一个
  卫生负控制）是对**最终源**跑的。
- 等价变异体一则：`_load_source_manifest` 的 `except (json.JSONDecodeError,
  UnicodeDecodeError, ValueError)` 里前两个名字都是 `ValueError` 的子类，单独去掉任一
  个都不改变行为。有判别力的做法是把元组**收窄到只剩另一个分量**（MS7/MS8），否则会
  把一个等价变异体的存活误读成覆盖缺口。`_render_manifest` 的
  `except (UnicodeEncodeError, TypeError, ValueError)` 同理（`UnicodeEncodeError`
  冗余于 `ValueError`；`TypeError` 腿不可达——承接值全部来自 `json.load`，恒可序列化，
  登记为死腿）。
