# M2 Producer 基础 — 任务分解

任务组按依赖排序；全部落 `producer/`，本地测试是唯一门禁（compute-loop §13.1 映射见各组）。

## 1. cli-config：配置装载与 CLI 骨架

- [ ] 1.1 实现 `config.toml` 类型化装载与 fail-closed 校验（业务规则字段全集，spec cli-config）
- [ ] 1.2 实现 `local.toml` 现场值装载，缺失即报错、零内置默认
- [ ] 1.3 实现 argparse 三入口骨架（prepare/init/run 薄委托）、未知子命令拒绝、`DATABASE_URL` 环境守卫
- [ ] 1.4 实现 NWM 解释器薄外壳（精确路径调用、fail-closed），以假解释器脚本测试调用形态

Suggested fixture level: compact - 内联 TOML 与假解释器小脚本即可覆盖全部场景
Minimal mergeable slice: 配置装载器（1.1–1.2）——纯函数加测试，可独立合并保绿，CLI 入口为后继

## 2. forcing-chain（一）：NWM 快照勘察与基础结构

- [ ] 2.1 只读勘察 NWM@`8ae9b8f2`，产出精确快照文件清单（模块 → 原路径 → 目标路径），随 PR 提交为 change 附录
- [ ] 2.2 快照 object-store/path 基础函数与 IFS/GFS source、raw manifest 数据结构，含其最小测试；剥离 DB/scheduler 分支
- [ ] 2.3 增加溯源头部检查测试：所有快照模块头部含 `NWM@8ae9b8f2 <原路径>`

Suggested fixture level: compact - 结构与路径函数用内存对象与 tmp 目录即可
Minimal mergeable slice: 勘察清单（2.1）——纯文档产物独立合并，快照代码为后继

## 3. raw-scan：完整性扫描与临时 manifest

- [ ] 3.1 实现 IFS/GFS 完整性规则判定（00/12 限定、0–168h、变量/bundle 模式、GFS f000 特例、逐文件检查）
- [ ] 3.2 实现 raw 只读复制到 `work/raw/`（源不可变断言）与临时 `raw-manifest.json` 生成（entry 只引用副本）

Suggested fixture level: compact - tmp 目录树按文件模式生成空壳文件即可覆盖判定与复制
Minimal mergeable slice: 完整性判定纯函数（3.1）——不含复制与 manifest，可独立合并保绿

## 4. state-tools：cfg.ic 工具链

- [ ] 4.1 快照并适配 `cfg.ic` 原生分段解析与回写（mesh/river/lake），字节级 roundtrip 测试
- [ ] 4.2 实现结构检查（缺段、行数与 header 不符、数值区损坏）
- [ ] 4.3 实现重戳到目标 cycle（只改 header、数据不变）
- [ ] 4.4 快照负残差归零与域均修正阈值检查纯函数

Suggested fixture level: compact - 合成分段状态文件（小规模 mesh/river/lake）覆盖全场景
Minimal mergeable slice: 分段解析与 roundtrip（4.1）——格式层独立合并保绿，重戳/残差为后继

## 5. forcing-chain（二）：canonical 转换

- [ ] 5.1 快照 DB-free canonical converter，引入 numpy/xarray/cfgrib 依赖并 `uv lock`，确保 CI producer job 绿（必要时补 eccodes 系统依赖）
- [ ] 5.2 合成 raw fixture → canonical NetCDF + catalog 端到端测试（无数据库连接断言）

Suggested fixture level: expanded - 需构造可被 cfgrib 读取的合成 GRIB 样本，fixture 制作本身有分量
Minimal mergeable slice: atomic - converter 与其端到端测试互为验证，先合无测试的 converter 或无 converter 的 fixture 都不构成独立绿

## 6. forcing-chain（三）：direct-grid forcing 与 SHUD 输入组装

- [ ] 6.1 快照 file-backend direct-grid forcing 生产（格点即站点、binding 权重 1、`Time_Day=0` 锚 cycle）
- [ ] 6.2 实现 work 内临时 registry/model manifest 生成与随 work 清理
- [ ] 6.3 实现 SHUD 输入组装：变体 + forcing → 运行目录，固定覆盖六项参数（START=0/END=7/DT_QR_DOWN=60/Update_IC_STEP=720/BINARY_OUTPUT=1/ASCII_OUTPUT=0），00Z/12Z 同参数测试

Suggested fixture level: compact - 合成 canonical NetCDF 与合成变体目录即可
Minimal mergeable slice: direct-grid forcing 生产（6.1）——对合成 canonical 独立可验证；registry 与组装为后继

## 7. checkpoint-tracker：T+12 捕获与补跑

- [ ] 7.1 实现 `cfg.ic.update` 轮询捕获（命中 720 分钟复制 + 分段格式校验），以模拟覆写序列测试正常/漏采/副本损坏三态
- [ ] 7.2 实现漏采补跑编排（同初态同 forcing、END=0.5、末态采纳；补跑失败传导整轮失败）

Suggested fixture level: compact - 模拟覆写序列与假执行器即可确定性重放竞态
Minimal mergeable slice: 捕获轮询（7.1）——独立于补跑编排可合并保绿

## 8. prepare-variants：变体与几何

- [ ] 8.1 实现 GeoJSON 生成：合成基线 GIS（shp/dbf/prj）→ `rivers.geojson`（`reach_id`=DBF Index、数量一致）与 `boundary.geojson`（合并边界），自定义 Albers → EPSG:4326 重投影；引入几何依赖并 `uv lock`
- [ ] 8.2 实现 prepare 编排：拒绝覆盖检查 → 薄外壳调用 builder（测试注入假 builder）→ 双变体提交（binding 不共用断言）→ scratch 清理

Suggested fixture level: expanded - 需构造带自定义投影的合成 shapefile 基线包
Minimal mergeable slice: GeoJSON 生成（8.1）——纯函数路径独立合并保绿，编排为后继

## 9. init-bootstrap：首态建链

- [ ] 9.1 实现 init 编排：非全新根拒绝守卫、7 天扫描窗定各源首轮（复用 raw-scan）、率定末态重戳写首态（复用 state-tools）、单源无完整 raw 时另一源不受影响

Suggested fixture level: compact - 复用 raw 目录树与合成状态 fixture
Minimal mergeable slice: atomic - 单一编排函数，拒绝守卫/扫描窗/首态写入共享同一条 init 验证路径，无独立可交付子集

## 10. run-controller（一）：前沿发现与锁

- [ ] 10.1 实现严格前沿纯函数：`DONE`/状态文件集合 → 每源待跑 T 或停止原因（全新链、D+12h、状态缺失、raw 缺口、缺轮阻塞）
- [ ] 10.2 实现未提交残留识别与清理重跑判定（保留 T 状态、删更晚状态与半成品）
- [ ] 10.3 实现非阻塞 flock 封装（持有即跳过、覆盖全生命周期），进程内测试跳过语义

Suggested fixture level: compact - tmp 目录树表达 DONE/状态组合即可
Minimal mergeable slice: 前沿确定纯函数（10.1）——判定逻辑独立合并保绿，残留清理与锁为后继

## 11. run-controller（二）：执行、发布与清理

- [ ] 11.1 定义 `JobExecutor` 协议与 Slurm 生产实现（`sbatch`/`sacct` 封装，参数全部来自 `local.toml`）及进程内 fake
- [ ] 11.2 实现发布器：DONE 前自身契约检查（v2 行数/reach 数/T+12 可读）、DAT→状态→`DONE` 固定顺序原子提交、`DONE` 后删旧状态只留两份，用记录型文件操作测试顺序
- [ ] 11.3 实现失败处理（合并日志、删 work、不推进）与崩溃恢复重跑路径
- [ ] 11.4 实现 14 天保留清理（`realpath` 圈定 yd 根、symlink 越界拒删）
- [ ] 11.5 控制器主循环集成：双源并行、单源失败不阻塞、多轮追赶端到端（fake executor 下 §13.1 控制器/发布行全场景）

Suggested fixture level: expanded - 多轮端到端目录树、记录型发布器与可编排 fake executor
Minimal mergeable slice: 发布器（11.2）——发布顺序与契约检查对记录型文件操作独立可验证；executor/主循环为后继
