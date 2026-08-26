# yd 循环预报设计方案（node-22 极简计算环）

状态：方案（已与需求方对齐结论，未开始实现）
日期：2026-08-26

## 1. 目标与边界

在 node-22 上只为 yd 一个流域跑滚动预报，参考 NWM 但大幅简化：

- **warm start 语义与 NWM 完全一致**（快照 valid_time 精确等于下轮起点、精确匹配 + 血缘、
  负残差归零），实现方式取 NWM 自身的确定性路径（见 §5），去掉 watcher/轮询等复杂件；
- 每轮跑完**只保留 SHUD 产物与状态链**，run 目录整体删除；
- 无 DB、无 orchestrator、无 registry 服务——一个 cron 驱动的单脚本 + Slurm 作业；
- 产物按 [products-contract.md](products-contract.md) 落 `/ghdc/data/yd/output/`，供 27 的
  yd-viewer 与最终客户侧同款服务消费。

## 2. 已拍板决策

| 分支 | 结论 |
|---|---|
| 驱动源 | **双源 IFS + GFS**，各自独立成环（独立 warm-start 状态链、独立产物） |
| 预报时效 | **7 天（168h）**，两源同窗 |
| 节律 | **每 12h**，00/12 UTC cycle，与 NWM raw 数据到达节奏对齐 |
| 失败兜底 | **绝不冷启动**：降级梯度到底后停更 + 告警（yd 无 spin-up 机制，冷启动即垃圾数据） |
| 代码归属 | 本仓 `compute/` 目录；node-22 上 checkout 本仓运行 |

双源对展示端的影响：viewer 每河段过程线展示 IFS/GFS 两条曲线（图例区分），
产物契约的 cycle 目录增加 source 维度——已同步改入 `design.md` 与 `products-contract.md`。

## 3. 事实基线（已查证，路径见 NWM 仓）

- **forcing 三段 CLI 全部 DB-free 可独立调用**：`nhms-{ifs,gfs} download` →
  `nhms-canonical convert` → `nhms-forcing produce`（`workers/data_adapters/cli.py`、
  `workers/canonical_converter/cli.py`、`workers/forcing_producer/cli.py`）。
  node-22 生产 `.venv` 已装齐（`.venv/bin/nhms-forcing` 等）。
- **raw grib 现成**：node-22 生产调度已在下载 IFS/GFS（每日 4 cycle，覆盖 00/12），
  私有根 `/scratch/frd_muziyao/nhms-prod/object-store/raw/`，NFS 镜像
  `/ghdc/data/nwm/object-store/raw/`（实测两源均新鲜到当天，GFS 留 ~16 天）。
  canonical 转换产物同在私有根下，可直接复用。
- **站点表即契约**：`nhms-forcing produce` 的 file 后端只需一个 `.tsd.forc`
  （`ID Lon Lat X Y Z Filename` 表）+ 手写最小 file-registry/model manifest 两个 JSON，
  不需要 Basins 注册；站点任意经纬度，legacy IDW（k=4 最近格点 1/d² 加权）插值，
  与 yd 的 105 站 0.1° 表直接兼容（`workers/forcing_producer/file_store.py:1027-1068`）。
- **CSV 口径**：`X<lon>Y<lat>.csv` = `Time_Day Precip Temp RH Wind RN`
  （mm/day、°C、[0,1]、m/s、W/m²），时间基准 **UTC**、相对起始日的日序——SHUD 直接读，
  与 rSHUD 逐字节同构（NWM `docs/forcing数据处理流程与rSHUD一致性说明.md`）。
- **NWM warm start 精确语义**（`workers/shud_runtime/runtime.py`、
  `packages/common/state_cli.py`、`state_manager.py`、`state_qc.py`）：
  1. 快照 **valid_time 精确等于下一轮 cycle 起点**（本轮在 lead=cycle 间隔处产出的
     checkpoint），不是"跑到哪算哪"的末态；
  2. 下一轮按 `(model, source, valid_time, 反推 producer cycle, lead_hours)` 精确匹配 +
     血缘（模型包版本/checksum）校验选取，而非"取最新"；
  3. 候选损坏/时间不符 → 逐级降级到更旧可用状态；同因批量降级 → 系统性熔断硬失败；
  4. 保存前**负残差归零**：负值一律清零，但 mesh 域均修正量 > 2.0e-4 m 或 river 域均
     > 2.0e-3 m 则整体拒绝该快照；
  5. `.cfg.ic` header `<mesh_count> <列数6> <minute-time>`，重启时把 minute-time 重戳到
     新 run 的时间基准（NWM `_shift_cfg_ic_time` 同义操作）；
  6. NWM **没有 SPINUPDAY 机制**，靠 12h 接力替代 spin-up——这正是"绝不冷启动"决策的依据。
- **yd 模型包**：7891 单元 / 3988 河段；`yd.cfg.para` 为 `INIT_MODE=3, BINARY_OUTPUT=1,
  CRYOSPHERE=1`；交付的 `yd.cfg.ic` 是 25 年率定长跑末态（minute 13150080 =
  2000-01-01 起 9132 天 ≈ 2025-01-01 有效），作为 bootstrap 底座。
- **算力**：CPU 分区 24 节点 × 40 核；yd 规模的 7 天预报单跑预计分钟级。
- **node-22 环境纪律**：维护窗口前禁止裸 `uv run`/`uv sync`；一律用精确解释器
  `/scratch/frd_muziyao/NWM/.venv/bin/python -m <entry>` 调 nhms CLI（CLAUDE.md 约束）。

## 4. 每轮流程（cycle T ∈ {00,12}UTC × source ∈ {ifs,gfs}，相互独立）

```
cron(每小时) → flock 防重入 → 对每个 source：
  1. 发现：NWM store 里最新的 raw/<source>/<T>/manifest.json 完整、且
     /ghdc/data/yd/output/<T>/<source>/DONE 不存在 → 该 (T, source) 为待跑目标
  2. forcing：canonical/<source>/<T> 缺则 nhms-canonical convert；
     nhms-forcing produce --source-id <source> --cycle-time <T> --model-id yd_<source>
     → 105 个 X*.csv + yd.tsd.forc（0–168h）
  3. 选态：states/<source>/ 里 valid_time == T 的快照；缺则降级（§5）
  4. 组装 run 目录（/scratch/frd_muziyao/yd-loop/runs/<source>/<T>/）：
     input 模板（几何/参数/calib，随本仓固定版本）+ 新 forcing + 重戳后的 cfg.ic
  5. sbatch 单作业，作业内顺序两次 SHUD：
     a. 状态短跑：START=0, END=0.5（12h）→ 末态 = valid_time T+12h 的快照
     b. 预报长跑：START=0, END=7.0 → yd.rivqdown.dat（DT_QR_DOWN=180，3h 步长）
  6. 收尾（作业成功后）：
     - 短跑末态负残差归零 + 阈值 QC → states/<source>/<T+12h>.cfg.ic + 元数据
       （producer_cycle、模型包 checksum——供下轮血缘校验）
     - yd.rivqdown.dat → /ghdc/data/yd/output/<T>/<source>/，最后 touch DONE
     - 整个 run 目录删除；states/<source>/ 保留最近 30 个快照 + bootstrap；
       output/ 清理 >14 天的 cycle 目录
  7. 失败：run 目录删除（保留该轮一份错误日志），不写 DONE、不产状态；
     下一轮自然走降级梯度
```

两次 SHUD 同源同 IC 同 forcing，仅 END 不同——短跑末态即"lead=12h checkpoint"，
与 NWM watcher 采样 + 确定性补算（`runtime.py:784-937`）产出的对象**语义等价**，
但实现是纯确定性的，无轮询、无补算分支。代价是每轮多跑 12h/168h ≈ 7% 计算量，yd 规模下可忽略。

## 5. Warm-start 选态与降级（复刻 NWM 三层语义，去掉不需要的层）

1. **精确命中**：`states/<source>/<T>.cfg.ic` 且元数据血缘（模型包 checksum）一致 → fresh。
2. **降级梯度**：缺失/损坏（checksum 不符、header 时间与文件名不符）→ 取 `<T` 最新可用
   快照，header minute 重戳到 T，产物标记 `degraded`（写入该轮 `meta.json`，viewer 可见性
   后续再议）；候选逐个验、坏的标记跳过。
3. **停更熔断**：可用快照距 T 超过 **7 天** → 本轮拒跑，写告警状态（§7），绝不 INIT_MODE=1。
   同 NWM 系统性熔断精神：熔断不消耗候选，修复后自然恢复。
4. **bootstrap**：首启用 zhaochen 25 年末态（valid ≈ 2025-01-01）重戳为首轮 T ——
   有效性距今 ~1.5 年，头几周状态偏离需向用户/计算方说明（开放项 3 给出补救选项）。

血缘：状态元数据记录 `{source, producer_cycle, model_package_sha256, valid_time}`；
模型包（input 模板 + calib + SHUD 二进制版本号）变更时 checksum 变 → 旧状态整体失配 →
触发停更告警，人工决定重新 bootstrap。这等价于 NWM 的 packaged-IC fail-closed 门。

## 6. Forcing 的最小接线

- yd 自有 object-store 根 `/scratch/frd_muziyao/yd-loop/object-store/`：
  `raw/`、`canonical/` 为指向 NWM 生产私有根同名目录的**符号链接**（同属 frd_muziyao，
  只读复用，零拷贝零污染）；`forcing/` 为实目录，yd 自己的产出落这里。
- 手写两个 JSON（进本仓 `compute/registry/`）：file-registry manifest
  （`yd_ifs`/`yd_gfs` 两个 model 条目）+ model manifest（`model_package_uri` 指向
  本仓 checkout 内的 yd 输入模板目录，`shud_input_name=yd`）。
- 环境：`NHMS_FORCING_REPOSITORY_BACKEND=file`、`NHMS_SCHEDULER_REGISTRY_MANIFEST=<上述
  registry 路径>`、`OBJECT_STORE_ROOT=/scratch/frd_muziyao/yd-loop/object-store`。
- IFS 168h 内步长 3h；GFS 全程 3h——两源产物时间轴一致，viewer 无需特判。

## 7. 调度、日志与告警（极简）

- **cron**（frd_muziyao 账户）每小时执行 `compute/run_cycle.sh`（flock 单实例）；
  脚本只做发现/组装/提交/收尾，重活全部在 Slurm 作业内。
- 幂等：以 `output/<T>/<source>/DONE` 为唯一完成判据，重复执行天然 no-op；
  作业中断残留的 run 目录由下次执行清理重建。
- 日志：单一滚动日志 `/scratch/frd_muziyao/yd-loop/loop.log`（logrotate 或按大小自截），
  失败轮另存一份该轮错误摘要至 `states/<source>/failures/`（薄记，非产物）。
- 告警 = 状态外显：每次执行后写 `/ghdc/data/yd/status.json`
  （各 source 最新成功 cycle、连续失败次数、最后错误摘要、是否处于停更熔断）。
  yd-viewer 读它在页面上显示"数据更新时间/停更提示"（viewer 侧小增量，已记入其开放项）；
  不建邮件/IM 通道，保持极简。

## 8. 保留策略（"只留 SHUD 产物"的精确化）

| 对象 | 保留 |
|---|---|
| `output/<T>/<source>/yd.rivqdown.dat` + `DONE` + `meta.json` | 14 天，之后删除 cycle 目录 |
| `states/<source>/*.cfg.ic`（+元数据） | 最近 30 个 + bootstrap 永久 |
| run 目录（forcing csv、其余 SHUD 输出、日志） | 跑完即删，失败仅留错误摘要 |
| yd object-store `forcing/<source>/<cycle>/` | 跟随 output 同窗清理 |

## 9. 验证计划

| 项 | 手段（oracle：node-22 实机） |
|---|---|
| forcing 最小接线 | 对一个历史 cycle 跑三段 CLI，抽 3 站 CSV 与 canonical 格点值对照 IDW 权重手算 |
| 两段跑 warm-start 接力 | 连续两轮实跑：断言第二轮消费的快照 valid_time == 其 T、血缘匹配；比对第二轮 [0,12h] 流量与第一轮长跑同窗段一致性 |
| 负残差 QC | 用真实短跑末态验证清零/阈值行为（含构造超阈值拒绝样例） |
| 降级/熔断 | 删除精确快照 → 断言取旧+degraded 标记；清空 states → 断言拒跑+status.json 告警位 |
| 幂等 | DONE 存在时重复执行 no-op；作业中断后重入自愈 |
| 产物契约 | 27 侧 yd-viewer 读实产 output/ 出曲线（与 viewer M4/M5 合并验收） |

## 10. 开放项

1. **SHUD 二进制版本**：必须与 zhaochen 率定所用版本一致（CRYOSPHERE 等特性开关影响状态
   语义）——向 zhaochen 确认其编译版本/commit，钉进模型包 checksum。
2. **契约确认**：双源目录布局（`output/<T>/<source>/`）与时间基准（forcing/产物均为 UTC，
   viewer 展示转北京时间）需与 zhaochen / viewer 侧三方对齐——viewer 契约文档已同步改。
3. **bootstrap 状态过旧（~2025-01）**：可选补救——向 zhaochen 要 2025-01 至今的历史
   forcing，先跑一次追赶模拟把状态推进到当前再开环；不做则接受头几周 degraded。
4. **对 NWM 生产的依赖**：raw/canonical 搭便车，NWM 下载停摆则 yd 停更（status.json 可见）。
   接受此依赖以换极简；如需独立，后备方案是 yd 环内自跑 `nhms-ifs/gfs download`（CLI 现成）。
5. node-22 `.venv` 3.11 切换维护窗口（NWM #1831）落地后，本环调用的精确解释器路径复核一次。

## 11. 风险

- 两源 raw 到达时间不一：按源独立成环已消化（一源晚到只影响该源该轮）。
- NWM raw 保留 14 天、canonical 清理窗口更短的可能：本环紧跟最新 cycle（滞后 ≤ 12h），
  实际风险极低；缺 canonical 时自行 convert 兜底。
- Slurm 队列拥堵导致 12h 内没跑完：下轮发现上轮无状态 → 降级梯度自然消化；
  作业请求资源刻意小（单节点 8 核）降低排队概率。
