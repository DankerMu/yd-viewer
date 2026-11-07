## Why

自 2026102012 起，ECMWF 把 IFS `tp` 由 16 bit 改为 12 bit 打包，google、aws、ecmwf 三个镜像一致。量化步长随之从 0.008–0.03 mm 升到 0.12–0.24 mm。去累积时，量化噪声产生的负 delta 恰好是 −1 个步长，超过了固定容差 `PRECIP_NEGATIVE_NOISE_TOLERANCE_MM=0.1`（该值按 16 bit 推出，`canonical/_types.py:24-29`），于是被判成 `warning_negative_precip`。这个 flag 不在 forcing 可用集合里，readiness 判定缺时效，结果是 `canonical_incomplete`。node-22 ifs 自此每小时 FAILED，前沿停在 2026102012。2026-11-07 在计算节点上用 yd 自身转换器复现：2026102000 为 `canonical_ready`；2026102012 缺 lead 51/63/138/144 的 `prcp_rate_or_amount`。另外，失败日志只带 `status`，看不出缺的是什么。扫描结果：缺口内 25 个 cycle 全部判为 `canonical_incomplete`；被标记格点的负 delta 是文件步长的 0.5–4 倍，但始终不超过有效格距（2026092700–2026110612 全部 IFS 时效对，越界 0 对）。Closes #369。文档已先行改好（PR #371，compute-loop §7.3 与 snapshot inventory 第 1 行）。

**硬约束：简化项目，不过度设计。**

```text
Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent (expanded: GRIB reader 行为与 canonical QC/readiness 语义，属 parser/reader 与 legacy 兼容触发项)
Blast radius: 改错 → 真负降水被当噪声吞掉（静默降级），或旧 16-bit cycle 产物/flag 漂移，或 NetCDF 回退路径崩溃；ifs 预报链继续停摆
Selected risk packs: Schema / columns / units; Legacy compatibility; Error handling; Documentation
Evidence floor: producer ruff + format + pytest 全绿；新增容差场景测试红→绿；真实 GRIB bundle 读出 scale keys；large_file_guard 通过；node-22 复现命令对 2026102012 转为 ready
```

## What Changes

- `canonical/converter.py` `_read_record_with_xarray`：cfgrib 打开时请求 `read_keys=["binaryScaleFactor","decimalScaleFactor","bitsPerValue"]`，把三键写入 `RawRecord.metadata`（缺键不写；NetCDF 回退不写）。
- `canonical/_units.py`：新增有效量化格距 helper（文件步长 × 整数偏移 gcd，覆盖 NWM 裁剪重打包后残留的全球粗格）；`convert_ifs_precipitation_with_metadata` 新增可选关键字参数，`tol = max(0.1, S_prev, S_curr)`，判据 `delta ≥ −tol·(1+1e-6)`，anomaly 增记 `tolerance_mm`。缺 S 即退回 0.1。
- `canonical/converter.py` IFS 降水调用点从两条 `RawRecord.metadata` 推出 step 后传入。
- `nwm.py` `_convert_canonical`：非 ready 时异常消息附 readiness evidence 摘要。
- `_types.py:18-29` 注释同步更新。

不做：GFS APCP 路径；IFS shortwave 路径；常量值；`FORCING_USABLE_CANONICAL_QUALITY_FLAGS`；连续 3 次升级规则；retry 上限（#370）。

## Capabilities

### New Capabilities
- `ifs-precip-quantization-tolerance`：IFS 降水去累积噪声容差随 GRIB 量化步长。

## Impact

- `producer/src/yd_producer/canonical/{converter.py,_units.py,_types.py}`、`producer/src/yd_producer/nwm.py`；测试见 tasks.md。
