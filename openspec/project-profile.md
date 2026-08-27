# Project Profile: yd-viewer (SHUD Forecast Producer + Viewer)

活文档：Phase 0.0 引导生成，Phase 0.5 在项目长出新风险面时维护。仅记录核心风险包/触发词未覆盖的项目特有面。

Project profile: yd-viewer

Entry surfaces:
- `producer/src/yd_producer/`：`yd-producer` CLI 三入口（prepare/init/run）与其下模块
- `viewer/src/yd_viewer/`：只读 FastAPI over `YD_ROOT`
- `YD_ROOT` 文件契约（producer 写、viewer 读；`docs/products-contract.md`）
- `config.toml`（版本化业务规则）/ `local.toml`（gitignored 现场值）
- `openspec/changes/**` 与 `docs/*.md`（文档优先于实现）

Contracts:
- `YD_ROOT` 产物：`yd.rivqdown.dat`、`states/<source>/<T>.cfg.ic`、`DONE` 语义与写入顺序
- `cfg.ic` 原生分段格式（mesh/river/lake）与绝对时间头
- `raw-manifest.json` / 临时 registry 结构；`reach_count` 与变体 reach 数一致
- NWM 快照模块溯源头 `NWM@8ae9b8f2 <原路径>`
- `JobExecutor` 协议（submit/poll）

Risk axes:
- 状态链连续性：warm-start 初态必须覆盖变体自带率定末态；时间头与 cycle 绝对时间对应；断链即整链失效
- 严格前沿推进：DONE 定位 D → T=D+12h，缺轮停等不跳 cycle，每源在途作业 ≤1
- NFS 发布顺序与崩溃恢复：DAT → 状态 → DONE 最后写；无 DONE 残留必须可干净重跑
- 跨节点/外部系统禁区：禁连 NWM 数据库/scheduler，禁 GDAL 运行时，禁裸 python/pip（`docs/agent-ops.md` 为硬约束）
- 合成 fixture 是 M2 唯一 oracle：数值正确性显式归 M4，不得在本阶段声明

Typical evidence:
- 合成目录树 fixture + 注入式 fake（executor / builder / SHUD 调用）
- 记录型文件操作断言（顺序、终名、uid/gid/mode 不继承 scratch）
- 字节级 roundtrip（cfg.ic 分段解析回写）

Command entry points:
- producer 测试：`cd producer && uv run pytest`
- producer 风格：`cd producer && uv run ruff check . && uv run ruff format --check .`
- producer 依赖：`cd producer && uv sync --frozen`（依赖变更须 `uv lock` 后提交）
- viewer 同上（`cd viewer && ...`）
- OpenSpec：`openspec validate --all`（CI）/ `openspec validate <change> --strict --no-interactive`
- stage-pipeline 锚：`bash scripts/check-stage-pipeline-log.sh origin/master`

Verification matrix:
- `producer/src/yd_producer/**` -> `cd producer && uv run pytest` -> pytest 全绿，新行为有对应用例
- `producer/**` 任意改动 -> `cd producer && uv run ruff check . && uv run ruff format --check .` -> 退出码 0
- `producer/pyproject.toml` / `uv.lock` -> `cd producer && uv sync --frozen` -> 无 lock drift；CI producer job 绿
- `viewer/src/yd_viewer/**` -> `cd viewer && uv run pytest && uv run ruff check . && uv run ruff format --check .` -> 退出码 0
- `openspec/**` -> `openspec validate --all` -> 退出码 0
- `openspec/changes/<name>/**` -> `bash scripts/check-stage-pipeline-log.sh origin/master` -> 该 change 在 `docs/stage-pipeline-log.jsonl` 有条目
- 默认构建+测试 -> 上述 producer + viewer + openspec 三条 -> CI 四个 job 全绿

Mutation-testing hazards（本仓已实测绊倒过多个独立 agent，写进每份要求变异证明的 brief）:
- 复制 `producer/` 到 scratch 做变异时必须 `rsync --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache'`。带 `.venv` 复制会让 `yd_producer` 解析回仓库原文件，变异全部"存活"——假阴性，且看起来像好消息。
- 跑之前先断言 `yd_producer.__file__` 落在 scratch 副本里，再用一个必然变红的控制变异校准。
- scratch 目录名取唯一（含 issue/round 标识），并发 agent 共用通用路径会互相覆写脚本。

Domain risk packs:
- Geospatial / CRS / shapefile sidecars（`.prj` 自定义 Albers、重投影、GeoJSON 产物）
- Time series / forcing / temporal boundaries（cycle 00/12、0–168h、`Time_Day=0` 锚、T+12 相对/绝对时间头）
- 状态链 / warm-start 定戳一致性（cfg.ic 时间头 ↔ cycle 绝对时间；跨轮不断链）
- NWM 快照溯源与 DB-free 隔离（模块头溯源注释；禁运行时 NWM import 与数据库连接）

Domain expanded-triggers:
- `cfg.ic`、`state`/状态链、`warm start`、重戳/restamp
- `cycle`、`T+12`、`checkpoint`、`DONE`、前沿/frontier
- `raw manifest`、`forcing`、`canonical`、GRIB、NetCDF
- `.prj`、CRS、投影、GeoJSON
- `Slurm`、`sbatch`/`sacct`、`flock`、NFS
- NWM 快照 / snapshot 溯源
