Change surface: `IFSCanonicalConverter` 的 GRIB 读取（`converter.py:_read_record_with_xarray`、`_common.py:_cfgrib_backend_kwargs`）→ `RawRecord.metadata` → IFS 降水调用点（`converter.py` 约 L456-471）→ `_units.convert_ifs_precipitation_with_metadata`；`nwm._convert_canonical` 的错误消息。

Must preserve:
- 拿不到 step（NetCDF 回退、cfgrib 未返回键、值非有限、`bitsPerValue == 0` 常量场）时 tol 退回 0.1：在 pinned 输入上结果与现状完全一致（delta −0.001、−1.0 mm，`test_ifs_canonical.py:290-316`），只有区间 (−0.1·(1+1e-6), −0.1] 的判定由显著改为噪声。既有 pinned oracle `test_negative_precipitation_handling_all_cases` 不改断言即可通过。
- 新增的 `tolerance_mm` 进入 lineage_json，所以带 anomaly 的产品 checksum 会变；重新转换改动前的 cycle 会重写这些产品（不再是 `already_done`），数值不变。canonical 是 work 内的本轮工件，不跨轮复用，对下游没有影响。
- 输出值仍是 `max(0, delta)·24/step_hours`。容差只影响 flag/anomaly，不改数值。
- `filter_by_keys`/`indexpath` 约定、no-follow staged 读取、GFS 路径与 shortwave 路径不变。
- `FORCING_USABLE_CANONICAL_QUALITY_FLAGS`、连续计数与 `error_precip_accumulation` 升级规则不变。

Must add/change:
- step_mm = `1000 × 2**binaryScaleFactor × 10**(−decimalScaleFactor)`。`read_keys` 取 `binaryScaleFactor`、`decimalScaleFactor`、`bitsPerValue` 三键，三者都必须是 int（bool 不算）且 `bitsPerValue > 0` 才计算，否则视为缺失。常量场（例如 IFS f000 的 tp 全为 0）编码为 `bitsPerValue=0, E=0, D=0`，不加这一条就会算出 1000 mm 的伪步长。
- tol = `max(PRECIP_NEGATIVE_NOISE_TOLERANCE_MM, step_prev, step_curr)`，缺失的一侧不参与取 max；第一时效（previous 为 None）只用 curr。
- 噪声：`-tol·(1+1e-6) ≤ delta < 0`；显著：`delta < -tol·(1+1e-6)`。有没有 step 都用这一判据（docs §7.3），差别只在 tol 的取值。
- 非 ready 异常：`canonical conversion did not succeed: canonical_incomplete; missing_variables=[...]; missing_leads=[(51,['prcp_rate_or_amount']),...]; rejected_quality_flags={...}`，单行，有长度上界（例如前 20 个 lead），保持 `ProductionAttemptError` 类型。evidence 用 `evaluate_canonical_readiness` 重算，或从 converter 已有结果取得，二选一取最简单的方式，不新增公共 API。

Governing invariant: 一个负 delta 当且仅当其幅度超过两条消息中较粗的量化步长（且不小于 0.1 mm）时才被标为显著负降水。

Sibling surfaces: GFS APCP（`_units.py:58/108`），显式不改（NOAA APCP 打包未变，0.1 已覆盖 1/16 mm）；IFS shortwave（`IFS_SHORTWAVE_NEGATIVE_TOLERANCE_W_M2`），显式不改（超阈值为 `warn`，forcing 可用，不阻塞）；NetCDF 回退读取（无 scale 键 → 退回 0.1）；`_readiness.py` 不改。

Seams under test: `convert_ifs_precipitation_with_metadata` 纯函数；`IFSCanonicalConverter.convert_manifest` 端到端（真实 GRIB2 编码，沿用 `test_canonical_input_bounds.py` 的 `_encode_grib2` 方式，使 cfgrib 读出 scale 键）；`nwm._convert_canonical` 错误消息。

Required evidence:
- 纯函数：step 0.1221，delta −0.1221 → `ok` 加 `small_negative_ifs_precipitation_delta`，`tolerance_mm≈0.1221`；delta −0.2442 → `warning_negative_precip`；不传 step → 与现状一致（−0.12 → warn）。
- 纯函数：prev 一侧无 step（常量场），curr 无 step，delta −0.5 mm → `warning_negative_precip`。
- 端到端：两时效真实 GRIB tp，用非常量值并 `codes_set(handle, "bitsPerValue", 12)`，推出的 step 必须等于 `1000 × 2^-13 = 0.1220703125` mm；产生 −1 步长噪声 → catalog 中该产品 `ok`，`convert_manifest` 返回 `canonical_ready`。改动前此用例为 `canonical_incomplete`（红跑）。
- `_convert_canonical`：canonical_incomplete 时消息包含 `missing_leads` 与 lead 号。
- 现场 oracle（orchestrator 在 node-22 执行，不归 implementer）：复现脚本对 2026102012 → ready。

Non-goals: GFS/shortwave 容差、阈值入 config、retry 上限、NWM 上游修复、历史 cycle 重算。

Review focus: (1) 缺 step 时的 legacy 等价性；(2) step 公式与 E/D 符号；(3) cfgrib `read_keys` 与 `filter_by_keys` 并用时确实写入 attrs，且 bundle 内取到的是对应变量的消息；(4) `bitsPerValue == 0` 不产生 step、常量场一侧不放大容差；(5) nwm.py 不超过 1000 行，错误消息有界。
