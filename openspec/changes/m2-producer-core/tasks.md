# M2 Producer 基础 — 任务分解

任务组按依赖排序（每组的"依赖"行给出真实前置，未列即可与前面各组并行）；全部落 `producer/`，本地测试是唯一门禁。每组尾部标注 compute-loop §13.1 归属行。

## 1. cli-config：配置装载与 CLI 骨架

- [ ] 1.1 实现 `config.toml` 类型化装载与 fail-closed 校验（业务规则字段全集含 `reach_count`，spec cli-config）
- [ ] 1.2 实现 `local.toml` 现场值装载（含 NWM checkout 根），缺失即报错、零内置默认
- [ ] 1.3 实现 argparse 三入口骨架（prepare/init/run 薄委托，注册 `[project.scripts]` 入口点）、未知子命令拒绝、`DATABASE_URL` 环境守卫、run 入口状态目录缺失/为空即报错停止且不触发 init 逻辑
- [ ] 1.4 实现 NWM 解释器薄外壳（精确路径调用、cwd/`PYTHONPATH` 取自 checkout 字段、fail-closed），以假解释器脚本测试调用形态

依赖：无
§13.1 归属：无直接行（基础设施，支撑全部行）
Suggested fixture level: compact - 内联 TOML 与假解释器小脚本即可覆盖全部场景
Minimal mergeable slice: 配置装载器（1.1–1.2）——纯函数加测试，可独立合并保绿，CLI 入口为后继

## 2. forcing-chain（一）：NWM 快照勘察与基础结构

- [ ] 2.1 只读勘察 NWM@`8ae9b8f2`，产出精确快照文件清单（模块 → 原路径 → 目标路径，含 tracker 与补跑），落为 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md`
- [ ] 2.2 快照 object-store/path 基础函数与 IFS/GFS source、raw manifest 数据结构，含其最小测试；剥离 DB/scheduler 分支
- [ ] 2.3 增加溯源头部检查测试：所有快照模块（含后续组落地的 converter/forcing/tracker）头部含 `NWM@8ae9b8f2 <原路径>`

依赖：无
§13.1 归属：DB-free 链（基础结构部分）
Suggested fixture level: compact - 结构与路径函数用内存对象与 tmp 目录即可
Minimal mergeable slice: 勘察清单（2.1）——纯文档产物独立合并，快照代码为后继

## 3. raw-scan：完整性扫描与临时 manifest

- [ ] 3.1 实现 IFS/GFS 完整性规则判定（00/12 限定、0–168h、变量/bundle 模式、GFS f000 特例、逐文件检查）
- [ ] 3.2 实现 raw 只读复制到 `work/raw/`（源不可变断言）与临时 `raw-manifest.json` 生成（entry 只引用副本）

依赖：组 1（规则来自 config）、组 2（manifest 结构）
§13.1 归属：raw 扫描
Suggested fixture level: compact - tmp 目录树按文件模式生成空壳文件即可覆盖判定与复制
Minimal mergeable slice: 完整性判定纯函数（3.1）——不含复制与 manifest，可独立合并保绿

## 4. state-tools：cfg.ic 工具链

- [ ] 4.1 快照并适配 `cfg.ic` 原生分段解析与回写（mesh/river/lake），字节级 roundtrip 测试
- [ ] 4.2 实现结构检查（缺段、行数与 header 不符、数值区损坏）
- [ ] 4.3 实现重戳到目标 cycle 绝对时间（只改 header、数据不变；服务 init 首态与发布前 T+12 定戳两条路径）
- [ ] 4.4 快照负残差归零与域均修正阈值检查纯函数

依赖：组 2（勘察清单定原路径）
§13.1 归属：state
Suggested fixture level: compact - 合成分段状态文件（小规模 mesh/river/lake）覆盖全场景
Minimal mergeable slice: 分段解析与 roundtrip（4.1）——格式层独立合并保绿，重戳/残差为后继

## 5. 执行器抽象：JobExecutor 协议与 fake

- [ ] 5.1 定义 `JobExecutor` 协议（submit/poll、job ID/partition/终态/起止时间语义）与进程内 fake（成功/失败/超时可编排），接口契约测试
- [ ] 5.2 实现 Slurm 生产执行器（`sbatch`/`sacct` 封装，参数全部装配自 `local.toml`、零内置默认）；本阶段不做行为测试（M4 oracle），本地判据 = 参数装配纯函数检查 + 协议一致性

依赖：组 1（Slurm 字段结构）
§13.1 归属：控制器（支撑）
Suggested fixture level: compact - 进程内接口契约与参数装配测试，无文件 fixture
Minimal mergeable slice: 协议与 fake（5.1）——接口层独立合并保绿，生产执行器为后继

## 6. forcing-chain（二）：科学计算依赖引入

- [x] 6.1 引入 numpy/xarray/cfgrib 并 `uv lock`，加 import 冒烟测试，确保 CI producer job 绿（必要时 CI 补 eccodes 系统依赖，作为依赖引入的伴生动作显式提交）

依赖：无
§13.1 归属：DB-free 链（支撑）
Suggested fixture level: none - import 冒烟即验证目标，无业务 fixture
Minimal mergeable slice: atomic - 依赖+lock+冒烟+CI 绿是一条验证路径上的原子提交，子项无独立价值

## 7. forcing-chain（三）：canonical 转换

- [ ] 7.1 快照 DB-free canonical converter 并以合成 raw fixture → canonical NetCDF + catalog 端到端测试（无数据库连接断言）

依赖：组 2、组 3（manifest）、组 6（依赖）
§13.1 归属：DB-free 链
Suggested fixture level: expanded - 需构造可被 cfgrib 读取的合成 GRIB 样本，fixture 制作本身有分量
Minimal mergeable slice: atomic - converter 与其端到端测试互为验证，先合无测试的 converter 或无 converter 的 fixture 都不构成独立绿（依赖引入已剥离到组 6）

## 8. forcing-chain（四）：direct-grid forcing 与 SHUD 输入组装

- [ ] 8.1 快照 file-backend direct-grid forcing 生产（格点即站点、binding 权重 1、`Time_Day=0` 锚 cycle）
- [ ] 8.2 实现 work 内临时 registry/model manifest 生成与随 work 清理
- [ ] 8.3 实现 SHUD 输入组装：变体 + forcing + `states/<source>/<T>.cfg.ic` → 运行目录；warm-start 状态 MUST 覆盖变体自带率定末态（可区分 IC fixture 断言）；固定覆盖六项参数（START=0/END=7/DT_QR_DOWN=60/Update_IC_STEP=720/BINARY_OUTPUT=1/ASCII_OUTPUT=0），00Z/12Z 同参数测试

依赖：组 7（canonical 结构）、组 4（状态文件）
§13.1 归属：DB-free 链
Suggested fixture level: compact - 合成 canonical NetCDF、合成变体目录与可区分状态文件即可
Minimal mergeable slice: direct-grid forcing 生产（8.1）——对合成 canonical 独立可验证；registry 与组装为后继

## 9. checkpoint-tracker：T+12 捕获与补跑

- [ ] 9.1 快照并适配 `cfg.ic.update` 轮询捕获（命中 720 分钟复制 + 分段格式校验；产物保持相对时间头），以模拟覆写序列测试正常/漏采/副本损坏三态
- [ ] 9.2 快照并适配漏采补跑（同一 Slurm 作业内、同初态同 forcing、END=0.5、末态采纳；注入假 SHUD 调用测试；补跑失败传导整轮失败；控制器提交计数不变）

依赖：组 4（分段校验）、组 8（运行目录形态）
§13.1 归属：tracker
Suggested fixture level: compact - 模拟覆写序列与假 SHUD 调用即可确定性重放竞态
Minimal mergeable slice: 捕获轮询（9.1）——独立于补跑可合并保绿

## 10. prepare-variants：变体与几何

- [ ] 10.1 引入几何依赖（pyshp/pyproj/shapely）并 `uv lock`，构造带自定义 Albers `.prj` 的合成 shapefile 基线 fixture，实现 `.prj` 解析与重投影工具，CI 绿
- [ ] 10.2 实现 `rivers.geojson`（`reach_id`=DBF Index、数量一致）与 `boundary.geojson`（单元合并边界）生成，落点 `input/viewer/`
- [ ] 10.3 实现 prepare 编排：拒绝覆盖检查 → 薄外壳按源两次调用 builder（记录型假 builder 断言两次入参 source/grid 不同、输出分别落 `yd_gfs`/`yd_ifs`）→ 变体 reach 数等于 `reach_count` 校验 → 提交到 `input/models/` 与 `input/viewer/` → scratch 清理

依赖：组 1（薄外壳、`reach_count`）
§13.1 归属：prepare
Suggested fixture level: expanded - 需构造带自定义投影的合成 shapefile 基线包
Minimal mergeable slice: 几何依赖与重投影工具（10.1）——依赖+fixture+纯函数独立合并保绿，产物生成与编排为后继

## 11. init-bootstrap：首态建链

- [ ] 11.1 实现 init 编排：非全新根拒绝守卫、7 天扫描窗定各源首轮（复用 raw-scan）、任一源窗内无完整 cycle 即整体拒绝不写状态（fail closed）、率定末态重戳写首态（复用 state-tools）

依赖：组 3（扫描）、组 4（重戳）
§13.1 归属：无直接行（测试归属见 change design D7）
Suggested fixture level: compact - 复用 raw 目录树与合成状态 fixture
Minimal mergeable slice: atomic - 单一编排函数，拒绝守卫/扫描窗/首态写入共享同一条 init 验证路径，无独立可交付子集

## 12. run-controller（一）：前沿发现与锁

- [ ] 12.1 实现严格前沿纯函数：`DONE`/状态文件集合 → 每源待跑 T 或停止原因（全新链、D+12h、状态缺失、时间头不对应 T、raw 缺口、缺轮阻塞）
- [ ] 12.2 实现未提交残留识别与清理重跑判定（保留 T 状态、删更晚状态与半成品）
- [ ] 12.3 实现非阻塞 flock 封装（持有即跳过、覆盖全生命周期），进程内测试跳过语义

依赖：组 1、组 4（12.1 时间头校验读分段 header）
§13.1 归属：控制器（前沿/flock 幂等/raw 缺口）
Suggested fixture level: compact - tmp 目录树表达 DONE/状态组合即可
Minimal mergeable slice: 前沿确定纯函数（12.1）——判定逻辑独立合并保绿，残留清理与锁为后继

## 13. run-controller（二）：发布、失败与清理

- [ ] 13.1 实现发布器：T+12 checkpoint 重戳到绝对 T+12（复用 4.3）→ DONE 前契约检查（v2、`forecast_days*24` 行、数据列数等于 `reach_count` 且等于变体 reach 数、T+12 可读、合并日志可用）→ DAT 原子 rename 为 `yd.rivqdown.dat` → 状态 rename → `DONE` 最后写 → 删旧状态只留两份 → 删本轮 work；正式文件不继承 scratch uid/gid/mode；记录型文件操作测试顺序与终名
- [ ] 13.2 实现失败处理（合并日志、删 work、不推进；复用 12.2 判定，仅接入失败/重跑路径）
- [ ] 13.3 实现 14 天保留清理（`realpath` 圈定 yd 根、symlink 越界拒删）

依赖：组 4（重戳）、组 12
§13.1 归属：发布（无 DONE 崩溃恢复/DONE 最后写/状态只留两份）
Suggested fixture level: expanded - 多状态目录树与记录型发布器
Minimal mergeable slice: 发布器（13.1）——发布顺序与契约检查对记录型文件操作独立可验证；失败与清理为后继

## 14. run-controller（三）：主循环集成

- [ ] 14.1 单源单轮 `run_once` 骨架打通：发现 → 组装 → 提交 fake → 发布 → work 清理；job ID/partition/终态/起止时间进运行报告；`local.toml` 缺 Slurm 字段即停
- [ ] 14.2 多轮追赶与缺口停等：raw 一次补齐 T/T+12h/T+24h 时序推进、每源在途提交计数 ≤1、缺轮停在缺口（§13.1：同源顺序/raw 缺口）
- [ ] 14.3 双源并行、单源失败隔离与崩溃恢复端到端：IFS 失败 GFS 继续、失败日志与 work 清理、无 DONE 残留下次重跑（§13.1：双源并行/单源失败/无 DONE 崩溃恢复）

依赖：组 5、组 8、组 9、组 12、组 13
§13.1 归属：控制器/发布（逐 task 标注场景）
Suggested fixture level: expanded - 多轮端到端目录树与可编排 fake executor
Minimal mergeable slice: 单源单轮骨架（14.1）——一条端到端路径独立合并保绿，追赶与双源为后继
