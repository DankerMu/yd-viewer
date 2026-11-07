## 0. Risk packs

- Schema / columns / units — 选中：s 与 S 的公式（m→mm）、anomaly 新增 `tolerance_mm` 键 → 1.2、1.3、2.1–2.4。
- Legacy compatibility — 选中：缺 S 时在 pinned 输入上与现状一致，pinned oracle 不改 → 2.3、3.1；新增 anomaly 键会改变产品 checksum，属已知差异（design Must preserve）。
- Error handling — 选中：缺键、非 int、bits 0、gcd 0、NetCDF 回退都视为没有 S；非 ready 错误消息 → 1.2、2.1、2.5。
- Documentation / migration notes — 选中：`_types.py` 注释；compute-loop §7.3 与 inventory 已先行修改（PR #371 与本分支的 docs 提交）→ 1.6。
- Public API / CLI / script entry — 不选：新增参数是可选关键字，签名向后兼容；全仓没有地方解析 `ProductionAttemptError` 的消息。
- Release / packaging / dependency compatibility — 不选：uv.lock 锁定 cfgrib 0.9.15.1，已支持 `read_keys`（`dataset.py:477-485` 从变量首条消息读取，写成 `GRIB_<key>`）。
- File IO / path safety — 不选：读取仍走既有 no-follow staged 路径，不新增 IO。
- Concurrency、Config、Permissions、Resource limits — 不选：无相关改动。gcd 是 O(n) 的 numpy 归约，n ≈ 7.4 万。

## 1. 实现

- [x] 1.1 `_common.py:_cfgrib_backend_kwargs`：cfgrib backend_kwargs 增加 `read_keys=["binaryScaleFactor","decimalScaleFactor","bitsPerValue"]`，与既有 `filter_by_keys`/`indexpath` 并存（三个分支都要加）
- [x] 1.2 `_read_record_with_xarray`：从 `data_array.attrs` 读 `GRIB_binaryScaleFactor`、`GRIB_decimalScaleFactor`、`GRIB_bitsPerValue`，三者都是 int（bool 不算）时写入 `RawRecord.metadata`，键名在一处常量中定义；NetCDF 回退时不写
- [x] 1.3 `_units.py`：新增纯函数 `effective_quantization_step_mm(values, binary_scale_factor, decimal_scale_factor, bits_per_value) -> float | None`（参数可为 None），按 design 计算 S
- [x] 1.4 `convert_ifs_precipitation_with_metadata`：新增可选关键字参数（例如 `quantization_step_mm`、`previous_quantization_step_mm`），按 design 计算 tol 和判据；两类 anomaly 都记 `tolerance_mm`
- [x] 1.5 `converter.py` IFS 降水调用点：分别用 precipitation 与 previous_precipitation 的 values 和 metadata 调 1.3 的函数，把结果传入 1.4
- [x] 1.6 `_types.py:18-29` 注释：0.1 是下限，实际容差取有效量化格距（#369：ECMWF 2026102012 起改为 12 bit；NWM 裁剪重打包保留全球粗格）
- [x] 1.7 `nwm._convert_canonical`：非 ready 时消息附有界的 evidence 摘要（形状见 design）；`nwm.py` < 1000 行

## 2. 测试（场景驱动；新用例放 `producer/tests/test_ifs_canonical.py` 或同级新文件，单文件 < 1000 行；新 helper 在测试函数内 import，保证红跑时 pinned 用例仍能收集）

- [x] 2.1 helper（值以 m 构造，E=−13、D=0，s_m = 2^-13 m）：值 = R + 4·s_m·k（k 取多种整数）→ 返回 0.48828125 mm；偏移只有 {0, 24}（单位 s_m）→ 返回 8·s_m·1000 = 0.9765625 mm（2 的幂因子）；常量值 → None；`bits_per_value=0` → None；任一键为 None → None
- [x] 2.2 纯函数：S=0.1221，delta −0.1221 → `ok`，anomaly 为 `small_negative_ifs_precipitation_delta`，`tolerance_mm` 等于 S，输出值 0；delta −0.2442 → `warning_negative_precip`；连续 3 次仍升级为 `error_precip_accumulation`
- [x] 2.3 纯函数（红跑时应通过）：不传 S，delta −0.12 → `warning_negative_precip`；delta −0.05 → `ok` 加 small anomaly；delta −0.5 → `warning_negative_precip`
- [x] 2.4 端到端：真实 GRIB2 编码两个时效的 tp，`bitsPerValue=12`、非常量，值落在 4 倍文件步长的格上；部分格点 f(n) = f(n−1) − 3 个文件步长 → `convert_manifest` 返回 `canonical_ready`，该产品 `ok`。断言 S == 4 × 回读文件步长 × 1000（编码后用 eccodes 回读实际 E/D，不硬编码 E）。注意每条消息以自己的 R（最小值）为原点，要让两条消息都落在 4s 格上又出现 −3s 的 delta，两条消息的最小值须相差 s（模 4s），例如 R_B = R_A − 3s（≡ +s mod 4s）。变体：lead 0 全零常量场接 lead 3 含 −0.5 mm 且 S < 0.5 → `warning_negative_precip`
- [x] 2.5 `_convert_canonical`：canonical_incomplete（真实缺时效转换或注入）→ `ProductionAttemptError` 消息含 `missing_leads` 和具体 lead
- [x] 2.6 红跑：还原 src 后 2.1、2.2、2.4 主用例、2.5 失败（2.1、2.2 因 helper 或参数不存在而失败，不得连累 pinned 用例收集）；2.3 与 2.4 常量场变体通过

## 3. 验证（输入 → 预期）

- [x] 3.1 `cd producer && uv run ruff check . && uv run ruff format --check . && uv run pytest` → 全绿（pinned `test_negative_precipitation_handling_all_cases` 断言未改）
- [x] 3.2 `uv run python scripts/large_file_guard.py`（仓库根）→ passed
- [x] 3.3 `openspec validate ifs-tp-quantization-tolerance --strict --no-interactive` → valid
- [x] 3.4 现场（orchestrator）：node-22 用合并后的代码跑 `diag_ifs_canonical.py convert`，2026102012 与缺口 cycle 全部 `canonical_ready`；2026102000 对照组产品值不变（job 113084，代码 f2aeed9 = PR 代码头，其后提交仅 docs/tasks：36/36 `canonical_ready`、prcp ok=53；2026102000 的 424 个 NetCDF 数组逐一相同）
