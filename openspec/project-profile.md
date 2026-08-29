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
- 连续变异体若只差一个**等长字符串字面量**，源文件大小不变；同一秒内写入时 CPython 判定 `.pyc` 仍有效（校验依据只有源文件 mtime 秒级时间戳 + 字节大小），直接复用旧字节码——这一轮报出来的是**上一个变异体**的结果。每个变异体之间 `export PYTHONDONTWRITEBYTECODE=1` 并清掉 `__pycache__`。这类假结果最阴险的地方是它自洽：数字稳定、可复现，只是对应错了变异体。
- 变异跑 MUST 用 `uv run python -m pytest`，**不得**用 `uv run pytest` 这个 console script。scratch 副本里的 `.venv/bin/pytest` shebang 可能指回另一棵树的解释器，于是测试在原仓库的 `yd_producer` 上跑，全部变异体（含校准变异体）假存活。第 52 行那条 `yd_producer.__file__` 落点断言**检测不到**这一条——断言本身也在错的解释器里跑，落点自然是自洽的。
- 上一条的失效面比 shebang 更宽（issue #23 round 2 实测）：scratch 副本里的 `uv run pytest` 会导入一份**陈旧的 wheel 快照**而不是副本的 `src/`，于是每一个变异体都「存活」、看起来像全套通过。两条判据可以当场识破：(1) 控制变异体不变红；(2) 同一个变异体用 `uv run python -m pytest` 跑会红。发现后不要重掷，换跑法重跑整批。
- scratch 副本除 `producer/` 外还须带上 `openspec/` 与 `docs/`：多个用例断言 fixture / 文档正文（溯源窗口、偏离清单、行号），缺了它们会得到与变异无关的红。
- 控制变异要挑真能变红的。已实测的**近等价变异体**反例：`_require` 改成 `return None` 在 77 与 95 两个规模下都全绿——`None` 继续流进 `_require_scalar`/`_require_table`，照样抛 `ConfigError`、照样带对的 `path`，只是措辞退化。校准失败要如实说并换一个，别默默重掷。

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

Orchestration hazards（由 PR #38/#40 的终局记录回灌，写进对应 brief）:
- **scratch 副本会静默绑回仓库 venv**：在仓外 scratch 副本里跑 `uv run pytest` 会经继承的 `VIRTUAL_ENV` 重新解析到本仓 `.venv`，加载**未变异**的源码，于是每个变异体都"存活"。副本内必须 `env -u VIRTUAL_ENV uv sync --frozen`，并对已 import 的模块做 `inspect.getsource` 变异标记断言。
- **grep 式闸门枚举看不见值传播闸门**：按 `if/elif/except/and/or` 关键字枚举守卫会漏掉 `status = path.stat()`、`open(path, "rb")`、`SOURCE_DIR_NAMES[source]` 这类**由取值本身承担判别**的闸门（PR #38 round 5 实测漏掉约 10 个、其中 2 个无判别力）。要求完整枚举时用 AST 遍历全部可执行语句，不用 grep。
- **并行 reviewer 不得共用同一 worktree**：PR #40 round 4 三个 reviewer 在同一 worktree 内各跑变异实验，互相覆写，一个测试文件中途丢了 36 行。凡 brief 允许跑变异实验，必须指定唯一命名的私有 scratch 副本（含 issue/round 标识）并断言 `yd_producer.__file__` 落在副本内。
- **本地套件永远看不见 merge-ref-only 的 CI 红**：CI 构建 PR 的 merge ref，分支本身不构建；master 在一次 run 内可能移动多次。Phase 8 冻结前应对 `origin/master` 的合并结果跑一次本地套件。
- **声明必须配判别器**：凡写下「只增加/不解除」「等价或更强」「已覆盖」「每一/全部/恰好」这类完整性断言，先构造证明它的变异体；双向声明要两个方向各一个判别器。本仓已有多处此类断言被事后证伪。
