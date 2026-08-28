# issue #7 / 任务 3.2 闸门审计（`producer/src/yd_producer/rawcopy.py`）

枚举方式：**AST 遍历全部可执行语句**（`ast.parse` + 自定义 `NodeVisitor`，采集
`If`/`IfExp`/`For`/`While`/`ExceptHandler`/`BoolOp`/`Compare`/`Assert` 与**值传播闸门**
`os.lstat`/`open`/`os.open`/`os.fdopen`/`mkdir`/`unlink`/`rmdir`/`lexists`/`exists`/
`dict.get`/`Subscript`/`isinstance`/`relative_to`/`render_bundle_filename`/`zip`/
`json.load`/`S_ISLNK`/`sorted`/`len`），并剔除纯类型注解子树。MUST NOT 用 grep：
`status = os.lstat(path)`、`SOURCE_DIR_NAMES[source]`、`metadata.get(...)` 这类闸门在
关键字扫描下不可见（project-profile「Orchestration hazards」实测漏约 10 个）。

原始枚举：函数体内命中 **157** 个节点，按「行 + 语义」归并为下表 **58** 条闸门；每条
落在「表内（有杀手变异体）」或「死腿登记（附不可达/无判别力的理由）」两桶之一，无第三桶。

**round-1 修复后重修（PR #65）**：复合闸门按**分量**重新展开（见文末「复合闸门逐分量
重修」），表内由 38 条细化为 71 条，死腿登记 20 条不变。

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

## 表内闸门（38 条，各有杀手变异体，全部实测变红）

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
| `expected_variables[lead] is None`（键相等仍需判值） | `_reconstruct_sources` | MF5（去掉该判值）+ `test_verdict_lead_with_a_null_variable_set_is_rejected` | RED |
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
| `_render_manifest` 的 `.encode("utf-8")`（序列化前置到准入期） | `_render_manifest` | MF1b 改成 `errors="surrogatepass"` + `test_non_utf8_encodable_carried_value_is_refused_before_any_write` | RED |
| `for variable in verdict.expected_variables[lead]`（逐变量扇出，集合相等两方向） | `_build_entries` | M24 漏一条 / M25 多一条 | RED / RED |
| `local_key=local_key`（entry 的 key 由 yd **自己算**，不照抄源 manifest） | `_build_entries` | MX1（`local_key=source_entry.local_key`）+ `test_full_cycle_copies_files_and_manifest_triples_match` / `test_manifest_json_matches_the_producer_consumer_contract`（源 fixture 带 `nwm-bucket/` 发散前缀） | RED |
| `work_dir` 在 `raw_root` 之下（析取左半） | `stage_raw` | MF3 去掉该闸门 + `test_work_dir_under_raw_root_is_a_config_error` | RED |
| `raw_root` 在 `work_dir` 之下（析取右半） | `stage_raw` | MF3 + `test_raw_root_under_work_dir_is_a_config_error` | RED |
| `source_id=SOURCE_DIR_NAMES[source]`（存储身份逐源非对称） | `_write_manifest` | M26 用小写入参 | RED |
| `expected_checksum`/`expected_size_bytes` 留 None | `_build_entries` | M28 写 0 | RED |
| `manifest_uri=None` | `_write_manifest` | M29 写 `file://` | RED |
| manifest 级四键 | `_manifest_metadata` | M30 少写 `requested_forecast_hours` | RED |
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

## 死腿登记（20 条，本轮无判别器；逐条附理由）

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
| `except OSError` ×2 | `_Written.rollback` | 尽力而为清理：吞掉异常是有意的，红/绿都不改变外抛的 staging 失败 |
| `if probe.parent == probe` | `_ensure_dir` | 不可达：文件系统根恒存在，`while` 先退出 |
| `zip(..., strict=True)` | `stage_raw` | 不可达：`targets` 由 `rebuilt` 同一推导式构造，长度恒等 |
| `rebuilt[0][1].parent if rebuilt else raw_path` 的 else 支 | `stage_raw` | 不可达：空 `lead_hours` 已在 `_validate_params` 以 `ConfigError` 拒 |
| `if not source_config.lead_hours` | `_validate_params` | 上游 `judge` 先以 `ConfigError` 拒同一输入；此处是本模块自己的兜底 |
| `if kind not in ERROR_KINDS` | `RawStagingError.__init__` | 自检式断言：本模块内所有 raise 点都用字面量 kind，越域取值只可能来自将来的改动 |
| `hours[0]` / `hours[-1]` | `_manifest_metadata` | 与上面空 `lead_hours` 同一前置，非空后恒有定义 |
| `sorted(...)` ×3（消息拼装、回滚排序、`uncovered`） | 多处 | 前两处是展示/顺序，无判定语义；第三处的判定由 `if uncovered` 承担（表内） |


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
