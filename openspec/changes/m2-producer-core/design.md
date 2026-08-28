# M2 Producer 基础 — 技术设计

## Context

业务规则已由 [docs/compute-loop-design.md](../../../docs/compute-loop-design.md) 全文钉死（cycle/来源/warm start/发布顺序/清理均"已拍板"），本设计只补文档未覆盖的工程决策。M2 的 oracle 是本地测试（design.md §10：阶段门禁 = compute-loop §13.1 七项全绿）；direct-grid/SHUD/T+12/Slurm 的最终 oracle 在 M4，本阶段一律以合成 fixture 与注入式假执行器验证。

## Goals / Non-Goals

**Goals:**

- proposal 所列 8 个 capability 的可实现设计与测试策略；
- compute-loop §13.1 七项本地测试逐项有归属；
- 每个 NWM 快照模块可追溯（来源 commit + 相对路径）。

**Non-Goals:**

- 不做 node-22 现场执行、cron、真实 Slurm 提交、SHUD 二进制运行（M4）；
- 不做 viewer（M3）与部署（M5）；
- 不实现 compute-loop §2 排除项（degraded、registry、血缘、退避、watchdog 等）。

## Decisions

**D1 仓库布局**（grill 用户拍板）：`producer/`（uv 项目，包 `yd_producer`）与 `viewer/` 双顶层，各自独立环境与 lockfile。与部署形态对应：node-22 只用 `producer/`，node-27 只构建 `viewer/`。M2 代码全部落 `producer/src/yd_producer/`。

**D2 NWM 快照锚点**（事实核查）：pin 公开仓 DankerMu/SHUD-NWM 的 `origin/master` HEAD `8ae9b8f29c8b72c574e8cbd95f2994160bd42832`。本地 checkout 领先的 2 个 commit 只改 scheduler/orchestrator（yd 禁止复制的部分），对快照模块无差异。每个快照模块头部注释记录 `NWM@8ae9b8f2 <原路径>`。精确文件清单由 forcing-chain 首个勘察任务在该 commit 上确定，勘察只读、不改 NWM 工作区（agent-ops §5）。

**D3 Slurm 注入式执行器**：控制器依赖 `JobExecutor` 协议（submit/poll 语义），生产实现封装 `sbatch`/`sacct`，测试注入进程内 fake（同步完成/失败/超时可编排）。备选是 subprocess mock，弃——mock 命令行参数脆弱且测不到状态机。真实提交行为属 M4 oracle；Slurm 生产实现在 M2 不做行为测试，本地判据 = 协议一致性 + fake 三态可编排 + `local.toml` 参数装配的纯函数级检查。

**D4 配置装载**：stdlib `tomllib` + dataclass 显式校验；`config.toml` 版本化业务规则、`local.toml` gitignored 现场值（字段清单见 compute-loop §5）。任何必需字段缺失即 fail closed 报错，不设默认猜测（design.md §11：现场值不得在代码中猜测）。

**D5 依赖策略**：骨架 `dependencies = []` 为刻意留空；numpy/xarray/cfgrib 随 forcing-chain 依赖任务加入并 `uv lock`；cfgrib 的 eccodes 运行时库经 `eccodeslib`（ECMWF 官方二进制 wheel，含 manylinux_2_28）显式引入，不依赖系统 `libeccodes`（组 6 已落地）。几何选轻量组合 pyshp + pyproj + shapely（读 shp/dbf、重投影、合并边界），不引入 GDAL/geopandas——viewer 契约本就禁 GDAL 运行时，producer 侧同样够用且 CI 安装面小；随 prepare-variants 任务加入。CLI 用 stdlib `argparse`，零框架依赖（KISS；三个子命令不值 click/typer）。

**D6 NWM 解释器薄外壳 fail-closed**：`prepare` 中 mapping-builder 调用封装为独立函数——接受 `local.toml` 的解释器路径与 NWM checkout 上下文（cwd/`PYTHONPATH`）、以及 `config.toml` 的 `nwm_mapping_builder_module` 作为 module 名（module 名是版本化快照事实而非现场值，归属裁决见 #32；原文笼统写作"接受 `local.toml` 的解释器路径与 module 名"，其中 module 名的出处经 #32 更正 5 裁定为 `config.toml`，issue #3 按文档优先原则同步），路径不存在或非可执行即报错退出；绝不回退到 `uv run`、`--active` 或系统 Python（agent-ops §7.2）。本地测试用假解释器脚本验证调用形态与 fail-closed 分支。

**D7 init 的测试归属**：compute-loop §13.1 表无 init 行——init 的"已有状态/DONE 即拒绝"“7 天窗最早完整 cycle”“率定末态重戳到首轮 T"场景由 `init-bootstrap` spec 自带 Scenario 覆盖；重戳纯函数复用 state 行测试。此处显式声明以免被读作测试缺口。

**D8 基建在 change 外**（grill 用户拍板）：仓库 public、CI（ruff+pytest、Py3.12、openspec validate、stage-pipeline-log 锚）、双骨架已随 commit `3f18de8` 落地，不进本 change 的 spec/tasks。

## Sketch seams under test

测试行使的公共边界，从高到低（每 seam 一行理由）：

1. `controller.run_once(cfg, executor) -> RunReport`（对 tmp 目录树 + fake executor）——最高可本地行使的 seam，一次覆盖 §13.1 控制器行与发布行（前沿、双源并行、raw 缺口、单源失败、无 DONE 崩溃恢复、DONE 最后写、状态保留两份）。
2. `rawscan.judge(raw_root, source, cycle, config) -> ScanVerdict`（目录 fixture）——完整性规则与 f000 特例的判定边界，独立于控制器演进。（本行于 issue #6 修订：原写作 `raw_scan.scan(raw_root, source, cycle) -> Manifest | Incomplete`。三处修订理由——模块名以 issue #6 的 `yd_producer.rawscan` 为准；规则全集在 `Config` 内，故 `config` 必须是显式形参而非隐式全局；返回 `Manifest` 与 tasks.md 组 3 的切分冲突——manifest 结构归 3.2，3.1 只返回判定结果 `ScanVerdict`。复制与 manifest 生成的 seam 由 3.2 另行钉定。）
2b. `rawcopy.stage_raw(verdict, raw_root, work_dir, source, cycle, config) -> StagedRaw`（目录 fixture + 合成源 manifest）——issue #7 按上一行的交接钉定：只读复制与临时 `raw-manifest.json` 生成的边界。独立成 seam 而不并入 `judge` 的三个理由——判定是纯函数、staging 是写面，两者的失败语义不同（不完整不是异常 vs 写面失败即异常）；staging 需要 `work_dir` 与源 manifest 两个 `judge` 不需要的入参；produce→converter 的产物契约（entry 逐变量扇出、`idx_selector` 累积语义、manifest 级 forecast hours）只在此边界可断言。编号取 2b 而非重排后续各行，避免与既有引用（本文件与 tasks.md 多处按序号引用 seam 3–7）产生第二份编号。
3. `state` 模块文件级纯函数（parse/restamp/negative-residual/check，file→file）——格式正确性是状态链安全的根，必须在最细边界钉死。
4. `forcing.build(work, manifest) -> ForcingPackage` 与 `assemble(work, variant, forcing, state_path) -> RunDir`（合成 canonical/变体 fixture）——§13.1 "DB-free 链"行的验收边界；`state_path` 显式入参保证 warm-start 初态覆盖变体自带率定末态可被断言；快照模块另带 NWM 来源最小测试。
5. `tracker.capture(shud_dir, target_minute) -> CheckpointResult`（模拟 `cfg.ic.update` 覆写序列）——轮询竞态只能在此边界确定性重放。

6. `cli.main(argv, env) -> int`（进程内调用，不起子进程）——入口层**自身的契约**（三入口枚举、未知子命令拒绝、`DATABASE_URL` 守卫、`run` 状态目录守卫）由 spec cli-config 的 Scenario 逐条钉死，必须在此边界行使；委托目标以注入的假实现替换，故不牵连业务模块。
7. `nwm.invoke_mapping_builder(local, config, args, runner) -> CompletedProcess`（假解释器脚本 + 注入 runner）——D6 薄外壳的调用形态（精确解释器路径、module 名、cwd/`PYTHONPATH`）与 fail-closed 只能在此边界断言。

CLI 入口层**不做业务行为测试**（薄委托）：`prepare`/`init`/`run` 的业务行为经各自模块 seam（1–5）验证。这不豁免 seam 6——入口层自己的枚举/拒绝/守卫契约是 spec 明文 Scenario，属入口层而非业务模块，必须在入口层测。（原文写作"CLI 入口层不做行为测试"，与 spec cli-config 的 `--help`/未知子命令/`DATABASE_URL`/`run` 守卫四条 Scenario 直接冲突，issue #3 按文档优先原则更正。）

## Risks / Trade-offs

- [NWM 快照模块存在隐藏 DB/registry 耦合] → 勘察任务先行定清单；确有耦合时在快照内最小改写为文件后端并在模块头注明偏离，不引入运行时 NWM import。
- [cfgrib/eccodes 在 CI ubuntu 上安装失败] → 已按「优先二进制 wheel」分支解决：显式加 `eccodeslib` 依赖（PyPI 上 `eccodes` 仅发 win_amd64 wheel，linux/macOS 不自带库），CI producer job 无需 `apt-get libeccodes0`。
- [pyproj/shapely 在 CI 安装失败] → 已随 prepare-variants 依赖任务（10.1）解决并验证：三者均走 cp312 manylinux wheel（pyproj `manylinux_2_28`、shapely `manylinux_2_17`、pyshp 纯 py3），CI producer job 无需额外 apt 包。
- [无真实 GRIB/canonical 数据可本地验证数值] → 合成 fixture 验证结构与管线正确性；数值正确性显式归 M4 receipt，不在 M2 声明。
- [flock 语义测试跨平台脆弱（macOS/Linux 差异）] → 锁封装为小模块，单元测试进程内验证非阻塞跳过语义；真实 cron+flock 行为归 M4。

## Migration Plan

全新代码，无迁移；回滚 = revert 对应 PR。不触碰 `YD_ROOT` 契约与现有文档。

## Open Questions

无——grill 门禁 5 分支全部拍板，0 开放项。
