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

变异实验环境：私有 scratch 副本 `/private/tmp/claude-501/mutate-issue7-rawcopy`（含
`issue7` 标识、非共用路径），`rsync --exclude='.venv' --exclude='__pycache__'
--exclude='.pytest_cache'`，副本内 `env -u VIRTUAL_ENV uv sync --frozen`，
`PYTHONDONTWRITEBYTECODE=1` 且逐变异体清 `__pycache__`，每个变异体先断言
`yd_producer.__file__` 落在副本内、再对**已 import** 的 `yd_producer.rawcopy` 做
`inspect.getsource` 变异标记断言，最后跑 `tests/test_rawcopy.py`。
控制变异 **M00**（`local_key` 前缀 `raw/` -> `rawX/`）已实测变红，校准通过。

## 表内闸门（38 条，各有杀手变异体，全部实测变红）

| 闸门（行/语义） | 所在函数 | 杀手变异体 | 结果 |
|---|---|---|---|
| `if verdict.complete is not True` | `stage_raw` | M01 放行 | RED |
| `if len(source_config.bundles) != 1`（以 `len(bundles)` 判，非间接判） | `stage_raw` | M02 放行 | RED |
| 重构路径 vs `verdict.expected_files` 逐字比对 | `_reconstruct_sources` | M03 自比自 | RED |
| `Path.cwd() / path`（与 `judge` 同法提升） | `_absolute` | M04 不提升 | RED |
| `render_bundle_filename(...)`（复用 rawscan 渲染面） | `_reconstruct_sources` | M04/M03 + `test_bundle_pattern_validation_is_reused_from_rawscan` | RED |
| `SOURCE_DIR_NAMES[source]`（重构目录段） | `_reconstruct_sources` | M27 改用小写入参 | RED |
| `verdict.expected_variables.get(lead) is None` | `_reconstruct_sources` | `test_verdict_missing_a_lead_variable_set_is_rejected` + M03 | RED |
| `for segment in segments`（祖先段逐段查） | `_reject_symlinks` | M06 只查叶子 | RED |
| `os.lstat(current)` + `S_ISLNK(mode)` | `_reject_symlinks` | M05 放行 | RED |
| `open(<cycle>/manifest.json)`（OSError 腿） | `_load_source_manifest` | `test_absent_source_manifest_fails_closed`（缺文件即红） | RED（M07 同族） |
| `json.load` 解析腿 | `_load_source_manifest` | `test_unparsable_source_manifest_fails_closed` | RED |
| `isinstance(payload, Mapping)` | `_load_source_manifest` | `test_no_bare_stdlib_exception_escapes_for_each_failure_shape`（`[]`） | RED |
| `DownloadManifest.from_dict` 的 KeyError/TypeError 腿 | `_load_source_manifest` | `test_source_manifest_without_entries_key_fails_closed` | RED |
| `SOURCE_FORECAST_HOURS_KEY not in metadata` | `_source_forecast_hours` | M07 放行 | RED |
| `isinstance(declared, list)` | `_source_forecast_hours` | M08b 放宽成 `list \| str` | RED |
| `isinstance(value, bool) or not isinstance(value, int \| str)` | `_source_forecast_hours` | `test_non_integer_typed_forecast_hours_entry_fails_closed` | RED |
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
| `_identity` 的四元组（size/mtime_ns/ino/mode） | `_identity` | M22 退化成只比 size | RED |
| `written.rollback()`（失败清理） | `stage_raw` | M23 不回滚 | RED |
| `for variable in verdict.expected_variables[lead]`（逐变量扇出，集合相等两方向） | `_build_entries` | M24 漏一条 / M25 多一条 | RED / RED |
| `source_id=SOURCE_DIR_NAMES[source]`（存储身份逐源非对称） | `_write_manifest` | M26 用小写入参 | RED |
| `expected_checksum`/`expected_size_bytes` 留 None | `_build_entries` | M28 写 0 | RED |
| `manifest_uri=None` | `_write_manifest` | M29 写 `file://` | RED |
| manifest 级四键 | `_manifest_metadata` | M30 少写 `requested_forecast_hours` | RED |
| `source not in SOURCE_DIR_NAMES`（形参守卫） | `_validate_params` | M34 放行 | RED |
| `isinstance(cycle, datetime)` / `utcoffset()` / 整点三闸门 | `_validate_params` | M34 同族 + 参数化用例（naive、非整点各一行） | RED |
| 「零写入」时序（任何检查失败前不建目录） | `stage_raw` | M35 提前建目录 | RED |
| `_ensure_dir` 的 `while probe.exists()` 账本 | `_ensure_dir` | M23/M35（回滚后 work 必须回到空） | RED |
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
