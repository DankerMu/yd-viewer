## 0. Risk packs

- Schema / columns / units — 选中：step 公式（m→mm）、anomaly 新增 `tolerance_mm` 键 → 1.2、2.1、2.2。
- Legacy compatibility — 选中：缺 step 时在 pinned 输入上与现状一致，pinned oracle 不改 → 2.3、3.1；新增 anomaly 键会改变产品 checksum，属已知差异（design Must preserve）。
- Error handling — 选中：缺键或非 int 视为缺失；NetCDF 回退；非 ready 错误消息 → 1.4、2.4、2.5。
- Documentation / migration notes — 选中：`_types.py` 注释；compute-loop §7.3 与 inventory 已由 PR #371 先行 → 1.5。
- Public API / CLI / script entry — 不选：新增参数为可选关键字，签名向后兼容；全仓没有地方解析 `ProductionAttemptError` 的消息。
- Release / packaging / dependency compatibility — 不选：uv.lock 锁定 cfgrib 0.9.15.1，已支持 `read_keys`（`dataset.py:477-485` 从变量首条消息读取并写成 `GRIB_<key>`）。
- File IO / path safety — 不选：读取仍走既有 no-follow staged 路径，不新增 IO。
- Concurrency、Config、Permissions、Resource limits — 不选：无相关改动。

## 1. 实现

- [ ] 1.1 `_common.py:_cfgrib_backend_kwargs`（或 `converter.py:_read_record_with_xarray` 调用处）：cfgrib backend_kwargs 增加 `read_keys=["binaryScaleFactor","decimalScaleFactor","bitsPerValue"]`，与既有 `filter_by_keys`/`indexpath` 并存
- [ ] 1.2 `_read_record_with_xarray`：从 `data_array.attrs` 读取 `GRIB_binaryScaleFactor`/`GRIB_decimalScaleFactor`/`GRIB_bitsPerValue`，三者都是 int 时写入 `RawRecord.metadata`（键名自定，在一处常量中定义）；NetCDF 回退时不写
- [ ] 1.3 `_units.convert_ifs_precipitation_with_metadata`：新增关键字参数（例如 `quantization_step_mm: float | None`、`previous_quantization_step_mm: float | None`），按 design 计算 tol 与判据；两条 anomaly 记 `tolerance_mm`
- [ ] 1.4 `converter.py` IFS 降水调用点：从 precipitation 与 previous_precipitation 的 metadata 推出 step 并传入；`bitsPerValue == 0` 或任一键缺失或非 int 时该侧为 None
- [ ] 1.5 `_types.py:18-29` 注释：说明 0.1 是下限，实际容差取 GRIB 量化步长（#369，ECMWF 2026102012 改 12 bit）
- [ ] 1.6 `nwm._convert_canonical`：非 ready 时消息附有界的 evidence 摘要（design 所述形状）；`nwm.py` 保持 < 1000 行

## 2. 测试（场景驱动；新用例放在 `producer/tests/test_ifs_canonical.py` 或同级新文件，单文件 < 1000 行）

- [ ] 2.1 纯函数：step=0.1221，delta=−0.1221 → `ok`，anomaly 类型 `small_negative_ifs_precipitation_delta`，`tolerance_mm` 记录为该 step；输出值为 0
- [ ] 2.2 纯函数：step=0.1221，delta=−0.2442 → `warning_negative_precip`，`negative_ifs_precipitation_delta`；连续 3 次仍升级为 `error_precip_accumulation`
- [ ] 2.3 纯函数：不传 step，delta=−0.12 → `warning_negative_precip`（与改动前一致）；delta=−0.05 → `ok` 加 small anomaly
- [ ] 2.3b 纯函数：两侧都无 step（模拟常量场 `bitsPerValue=0`），delta −0.5 mm → `warning_negative_precip`；以及 helper 层面：metadata 为 `bitsPerValue=0, E=0, D=0` 时推出 None，而不是 1000
- [ ] 2.4 端到端：用真实 GRIB2 编码两时效 tp，值为非常量场并 `codes_set(handle, "bitsPerValue", 12)`（现有 `_encode_grib2` 写的是常量场，E=0，不能直接用）。f(n)=f(n-1)−1 个步长的格点 → `convert_manifest` 返回 `canonical_ready`、该产品 `ok`；断言推出的 step == `1000 × 2^-13 = 0.1220703125` mm，anomaly 的 `tolerance_mm` 等于它。再加一个 lead 0 全零（常量）消息接 lead 3 含真实显著负值的变体，结果为 `warning_negative_precip`
- [ ] 2.5 `_convert_canonical`：注入 canonical_incomplete 结果（或真实缺时效的转换）→ `ProductionAttemptError` 消息含 `missing_leads` 与具体 lead
- [ ] 2.6 红跑：还原 src 后 2.1、2.4、2.5 失败，2.3、2.3b 通过

## 3. 验证（输入 → 预期）

- [ ] 3.1 `cd producer && uv run ruff check . && uv run ruff format --check . && uv run pytest` → 全绿（pinned `test_negative_precipitation_handling_all_cases` 断言未改）
- [ ] 3.2 `uv run python scripts/large_file_guard.py`（仓库根）→ passed
- [ ] 3.3 `openspec validate ifs-tp-quantization-tolerance --strict --no-interactive` → valid
- [ ] 3.4 现场（orchestrator）：node-22 `diag_ifs_canonical.py convert` 对 2026102012 与扫描中被标记的 cycle → `canonical_ready`；2026102000 对照产品值不变
