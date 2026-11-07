Change surface: `IFSCanonicalConverter` 的 GRIB 读取（`converter.py:_read_record_with_xarray`、`_common.py:_cfgrib_backend_kwargs`）→ `RawRecord.metadata` → IFS 降水调用点（`converter.py` 约 L456-471）→ `_units.convert_ifs_precipitation_with_metadata`；`nwm._convert_canonical` 的错误消息。

Must preserve:
- 拿不到 S 时 tol 退回 0.1。S 缺失的情形：NetCDF 回退、cfgrib 未返回键、键非 int、值非有限、`bitsPerValue == 0` 的常量场、gcd 为 0。在 pinned 输入上（delta −0.001、−1.0 mm，`test_ifs_canonical.py:290-316`）结果与现状完全一致；只有区间 (−0.1·(1+1e-6), −0.1] 的判定由显著改为噪声。pinned oracle `test_negative_precipitation_handling_all_cases` 不改断言即可通过。
- 输出值仍为 `max(0, delta)·24/step_hours`。容差只影响 flag 和 anomaly，不改数值。
- `filter_by_keys`/`indexpath` 约定、no-follow staged 读取、GFS 路径、shortwave 路径都不变。
- `FORCING_USABLE_CANONICAL_QUALITY_FLAGS`、连续计数与 `error_precip_accumulation` 升级规则不变。
- 新增的 `tolerance_mm` 进入 lineage_json，带 anomaly 的产品 checksum 会因此改变；重新转换旧 cycle 时会重写这些产品，数值不变。canonical 是 work 内的本轮工件，不跨轮复用，下游不受影响。

Must add/change:
- 文件步长 s（mm）= `1000 × 2**binaryScaleFactor × 10**(−decimalScaleFactor)`。`read_keys` 取 `binaryScaleFactor`、`decimalScaleFactor`、`bitsPerValue` 三个键，三者都必须是 int（bool 不算）且 `bitsPerValue > 0`，否则视为没有 s。
- **有效量化格距** S = s × g，g = `gcd(rint((v − min(v)) / s))`，在该消息全部有限格点上计算。g == 0 时（常量场）视为没有 S。理由：raw bundle 是 NWM 下载器从全球场裁剪后重新打包的，区域值保留全球 12 bit 的粗格，现场实测 g 为 1、2、4、8。只用 s 会漏判，缺口内多数 cycle 仍会 `canonical_incomplete`。
- 计算放在一个纯函数 helper 里（例如 `_units.effective_quantization_step_mm(values, binary_scale_factor, decimal_scale_factor, bits_per_value) -> float | None`），在 IFS 降水调用点用 record 的 values 和 metadata 调用。读取层只负责把三个键放进 metadata。
- tol = `max(PRECIP_NEGATIVE_NOISE_TOLERANCE_MM, S_prev, S_curr)`，缺失的一侧不参与；第一个时效（previous 为 None）只用 S_curr。
- 判据：噪声为 `-tol·(1+1e-6) ≤ delta < 0`，显著为 `delta < -tol·(1+1e-6)`。有没有 S 都用这一判据（docs §7.3）。两类 anomaly 都记 `tolerance_mm`。
- 非 ready 时的异常为单行：`canonical conversion did not succeed: canonical_incomplete; missing_variables=[...]; missing_leads=[(51,['prcp_rate_or_amount']),...]; rejected_quality_flags={...}`，有长度上界（例如最多列 20 个 lead），类型仍是 `ProductionAttemptError`。`ConversionResult` 里没有 evidence，所以 nwm 内部重调 `evaluate_canonical_readiness`；不新增公共 API。`nwm.py` 保持 < 1000 行。

Governing invariant: 一个负 delta 当且仅当其幅度超过 `max(0.1 mm, 两条消息中较粗的有效量化格距)` 时，才被标为显著负降水。

Sibling surfaces:
- GFS APCP（`_units.py:58/108`）：显式不改。NOAA APCP 打包未变，0.1 已覆盖 1/16 mm。
- IFS shortwave：显式不改。超阈值是 `warn`，forcing 仍可用，不阻塞。
- NetCDF 回退：没有键，退回 0.1。
- `_readiness.py`：不改。

Seams under test:
- helper 纯函数；
- `convert_ifs_precipitation_with_metadata` 纯函数；
- `IFSCanonicalConverter.convert_manifest` 端到端，用真实 GRIB2 编码（参照 `test_canonical_db_free.py:_encode_grib2`，但必须是非常量值并显式设置 `bitsPerValue`）；
- `nwm._convert_canonical` 的错误消息。

Required evidence:
- helper：值落在 4s 格上（s = 0.1220703125）→ 返回 4s；常量场或 bits 0 → None；缺键 → None。
- 纯函数：S=0.1221，delta −0.1221 → `ok` 加 small anomaly，`tolerance_mm` ≈ 0.1221；delta −0.2442 → `warning_negative_precip`；不传 S、delta −0.12 → `warning_negative_precip`（与现状一致）；不传 S、delta −0.5 → `warning_negative_precip`。
- 端到端：两个时效的 tp，以 `bitsPerValue=12` 真实编码，值落在 4 倍文件步长的格上，存在 delta = −3 个文件步长的格点 → `canonical_ready`、该产品 `ok`。断言推出的文件步长为 `0.1220703125` mm、S 为其 4 倍。改动前这个用例是 `canonical_incomplete`（红）。再加一个变体：lead 0 全零（常量场）接 lead 3，含 −0.5 mm 的真实负值且 S < 0.5 → `warning_negative_precip`。
- `_convert_canonical`：canonical_incomplete 时消息含 `missing_leads` 和 lead 号。
- 现场 oracle（orchestrator 在 node-22 执行）：2026092700–2026110612 全部 IFS cycle、每对时效越界 0 对（`lattice_check.py`，已跑过）；复现脚本对 2026102012 和缺口 cycle 转换后为 `canonical_ready`。

Non-goals: GFS 与 shortwave 的容差、阈值入 config、retry 上限（#370）、NWM 上游修复、历史 cycle 重算。

Review focus:
1. 缺 S 时的 legacy 等价性；
2. s 公式与 E/D 的符号，gcd 只在有限值上计算、使用整数 dtype；
3. cfgrib `read_keys` 与 `filter_by_keys` 并用时确实写入 attrs，且取到的是该变量对应的消息；
4. `bitsPerValue == 0` 和 g == 0 都不产生 S；
5. `nwm.py` < 1000 行，错误消息有界。
