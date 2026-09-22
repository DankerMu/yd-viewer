# Agent 操作细则（拓扑 / 跨节点 / 部署 / 验证）

由 [CLAUDE.md](../CLAUDE.md) 路由进来。涉及 node-22、node-27、NFS、Slurm、部署或验证声明前必须通读本文件。

## 1. 当前阶段与权威来源

本期唯一生产闭环：

```text
node-22 yd 真计算 → yd NFS → node-27 yd 真展示
```

客户服务器不可达且运行环境未定；当前不执行客户侧 producer 迁移，也不得声称已经具备客户侧计算部署能力。

### 1.1 文档权威

yd 内部冲突顺序：

1. 已合并实现、测试和真实 receipt；
2. [products-contract.md](products-contract.md)；
3. [compute-loop-design.md](compute-loop-design.md) 与 [design.md](design.md)；
4. 本操作手册；
5. README、历史提交说明。

实现与方案冲突时，先修改并提交 docs，再修改代码。

涉及 NWM 当前物理部署时，权威是兄弟仓当前版本的：

1. `NWM/docs/governance/ROLE_BOUNDARY.md` 的 current physical deployment；
2. `NWM/docs/runbooks/current-production-ops.md`；
3. 节点专项当前 runbook；
4. `NWM/CLAUDE.md`；
5. 历史架构文档。

`NWM/docs/runbooks/two-node-deployment-overview.md` 描述过设计意图，不能覆盖当前 22/27 物理部署。引用 NWM 运维事实前先看其文档状态，不从历史段落抄当前值。

## 2. 与 NWM 的边界

### 2.1 允许的关系

- NWM downloader 在 node-27 将 raw GRIB 写入共享 NFS；node-22 yd 控制器只读该 NFS，并把本轮所需文件临时复制到 yd scratch；
- `yd-producer prepare` 一次性调用 NWM mapping-builder；
- canonical、direct-grid forcing、state 和前端的最小代码按来源 commit 快照进本仓，之后独立维护；其中 `safe_fs.py`、`object_store.py`、`converter.py`、`cfg_ic.py` 可在 yd 本仓修复缺陷，但每处相对 pin 的偏离须先在 `openspec/changes/archive/2026-09-15-m2-producer-core/nwm-snapshot-inventory.md` 对应行「剥离点」登记“问题 + 修法”，不要求逐字等价；
- node-27 复用现有域名和有效天地图配置，但 yd 使用独立容器、端口和 `/yd/` location。

### 2.2 禁止的关系

- yd 不连接 NWM PostgreSQL，不设置 `DATABASE_URL`；
- 不调用 NWM scheduler、autopipe、ingest、state registry、display API 或 Slurm Gateway；
- 不修改 NWM raw、canonical、published、registry、配置或 systemd unit；
- 不占用 NWM display API 的回环 `:8080`；
- 不改 node-27 的 NWM `/`、`/ops`、PG、ingest、autopipe、下载器或前端；
- 不把 NWM 的 DB、MVT、retention、file journal、provider refresh 等运维命令照搬到 yd。

任何 yd producer 进程中出现 `DATABASE_URL` 或 libpq 连接意图都视为配置错误，先停，不尝试“连通看看”。

本节约束的是 yd producer/viewer 主线。应急 yd-NWM 副本实例是另一套系统，其边界单独定义在 §14；副本实例拥有自己的 PG、scheduler 与 env，不落入本节“yd 不得设置 `DATABASE_URL`”等条款，但对 **NWM 本体**的全部禁止关系（不修改 NWM 原件、unit、env、registry，不占 `:8080`，不连 NWM PG）对副本实例同样成立。

## 3. 节点拓扑与登录

| 节点 | 登录 | 当前角色 |
|---|---|---|
| 本地 Mac | 本仓 `/Users/danker/Desktop/Hydro-SHUD/yd-viewer` | 编辑、测试、commit、push、构建镜像 |
| node-22 | `ssh -p 32099 frd_muziyao@210.77.77.22` | yd producer 控制器、Slurm/SHUD 计算、NFS 发布 |
| node-27 | `ssh -p 32099 nwm@210.77.77.27` | yd viewer 旁路 staging、Nginx `/yd/`、live receipt |
| 客户服务器 | 不可达 | 未来生产；本期不操作 |

NWM 当前物理角色必须牢记：

- node-27 是 NWM active production host，运行 PG `:55432`、下载、ingest、display API `:8080` 和前端；
- node-22 是 NWM 计算/Slurm host，不是活数据库 writer；本机 `:55433` 已归档停用，**不要连接**；
- node-22 上 NWM checkout 为 `/scratch/frd_muziyao/NWM`；node-27 上为 `/home/nwm/NWM`；
- yd checkout 的远端实际路径在 §15「M4 部署登记」中记录，不在代码中猜测。操作前先确认路径与 commit。

## 4. 存储与可见性

### 4.1 yd NFS

同一份 yd NFS：

| 视图 | 路径 |
|---|---|
| node-22 | `/ghdc/data/yd` |
| node-27 | `/home/ghdc/yd` |

两者是同一数据，不做 rsync。各节点通过自己的 `YD_ROOT` 配置表达路径，不创建统一绝对路径软链接，也不在代码中判断 hostname。

### 4.2 Slurm scratch

已确认的 yd 计算边界：

- node-22 登录节点和 Slurm 计算节点共同可见 `/scratch`；
- Slurm 计算节点看不到 `/ghdc` yd NFS；
- 作业只能在 yd 自己的 `/scratch/.../yd-loop/work/...` 内运行；
- 控制器取得 exact-work ownership 后，提交作业前将完整固定 yd native v2 变体与精确 cycle state 搬入同一 work 的 `input/`；沿用既有 staged copy/loader、checksum 和 owner，不添加通用包框架或重复整包验证；
- `AttemptRequest.variant_dir`/`state_path` 的 NFS source 路径只供登录节点 `driver.prepare` 对账；worker argv/环境、attempt handoff、receipt 与计算节点 assemble 输入只能引用已验证的 work-local capability，不得携带 `YD_ROOT` 路径；
- 计算节点不能直接写 `YD_ROOT/output` 或 `YD_ROOT/states`。

staged input 不是第二个长期模型仓或恢复 registry：成功发布/明确失败收尾时随既有 exact-work owner 删除；`sbatch`/`sacct` 客户端 timeout、未知 worker 崩溃等保留证据路径则连同整棵 work 原样保留，下一 tick 继续由 `UNVERIFIED_WORK_RESIDUE` 停源。禁止把副本放到 exact work 外的 scratch sibling、单独扫除 `input/`、让 worker 直接读 NFS、或在登录节点提前运行 canonical/forcing/assemble/SHUD。

### 4.3 NWM raw

NWM downloader 在 node-27 写共享 NFS。当前权威路径：

| 视图 | 路径 |
|---|---|
| node-27 writer | `/home/ghdc/nwm/object-store/raw` |
| node-22 yd reader | `/ghdc/data/nwm/object-store/raw` |

`/scratch/frd_muziyao/nhms-prod/object-store` 是 NWM 调度器私有根，不是 raw 来源。node-22 部署前必须以 `frd_muziyao` 身份实际确认 raw 根可遍历、目标 manifest 和 GRIB 可读；权限不足时停止并报告，不修改 NWM 目录权限。

对 NWM 原件只允许 `stat`、枚举和读取：

- 控制器可把本轮所需文件复制到 yd scratch work；这是临时副本，不得写入 yd NFS 或长期缓存；
- 不修改、移动、重命名、删除 NWM 原件；
- 不在 yd 根内建立可能被递归清理跟随的 raw symlink；
- 清理前先用 `realpath` 证明目标位于 yd 自己的 scratch 或 NFS 根；
- NWM raw 缺失时记录并等待，不启动 yd 下载器。

## 5. 本地开发纪律

- Python 一律 `uv`，禁止裸 `python`、`python3`、`pip`；
- 前端一律 `corepack pnpm`；
- 前端 base 和 fetch 使用相对路径，保证根路径与 `/yd/` 共用构建物；
- 外部基线模型包、真实 fixtures、`local.toml`、`.env`、天地图 key 和节点凭据不入 Git；
- 当前模型 fixture 目录 `fixtures/` 是本地受控资料，不得提交；
- 复制 NWM 代码时只搬经设计批准的最小子集，记录完整来源 commit，不保留未用 DB/scheduler 分支；
- 不在 NWM 有未提交改动的工作树上做任何修改。本项目只读勘察 NWM 时也要尊重其工作区状态。

## 6. 远端 Git 同步

每次在 22 或 27 更新 yd checkout 前：

1. 进入已登记的 yd checkout；
2. 运行 `git status --porcelain`；
3. 有未知修改、未跟踪模型包、现场配置或 receipt 时停止，先辨认所有者；
4. 干净后只用 `git pull --ff-only`；
5. 记录 `git rev-parse HEAD` 到本次 receipt。

禁止：

- 裸 `git stash`、`git stash pop`；远端 stash 栈是共享状态；
- 为了 pull 自动删除 untracked 或 gitignored 文件；
- `git reset --hard`、`git clean -fd` 处理未知现场状态；
- 在默认分支上临时改代码后直接运行；
- 把本地模型包、`.env` 或 `local.toml` 混进 commit。

若 ff-only 被本地同名文件阻塞，先比较内容并把有价值的现场文件复制到 checkout 外的带日期备份目录；无法确认归属就报告，不擅自清理。

跨节点传单个文本文件时优先使用 stdin：

```text
ssh ... 'cat > target' < local-file
```

不要在多层 shell 引号中嵌入密钥、SQL 或大段配置。长时间人工操作使用 `tmux`/`screen`；不要假定 SSH 断开后前台进程仍安全存活。

## 7. Python 环境边界

### 7.1 yd 自己的环境

- yd 日常 producer 只使用本仓自己的 `uv` 环境；
- node-22 上不允许 yd 日常 `run` import NWM checkout；
- 同步 yd 环境前先确认不影响任何正在运行的 yd controller/Slurm job；
- 生产执行应指向确定的 yd checkout 与 lockfile 对应环境。

### 7.2 NWM 活动环境：仅 prepare

NWM 当前维护窗口约束来自 `NWM/CLAUDE.md` 与 `current-production-ops.md`：

- 活动解释器为 `/scratch/frd_muziyao/NWM/.venv/bin/python`；
- NWM #1831 维护窗口完成前，禁止在 node-22 NWM checkout 执行 `uv sync`、裸 `uv run` 或任何会隐式重建 `.venv` 的命令；
- `--active` 不是安全替代；解释器缺失时 fail closed；
- `prepare` 用上述精确解释器执行随 yd 分发的薄 driver 脚本，直接复用已确认的 NWM mapping 库；不执行 resolution-only CLI 假装 build，不修改 NWM checkout；
- yd 不安装、不升级、不修复 NWM `.venv`。
- 子进程 PYTHONPATH 只指向明确 NWM checkout，去掉 DATABASE_URL/PYTHONHOME，不继承其它 Python 路径；保留 venv 解释器的原始绝对路径，不 resolve 到系统解释器；

此约束不意味着 yd 日常依赖 NWM 环境；它只约束一次性 builder 调用。

## 8. node-22 producer 操作

### 8.1 三个入口

实现完成后，所有操作只走本仓 CLI：

- `prepare --baseline <模型目录>`：目录直接含 `yd.*` 与 `gis/river.shp`/`domain.shp`，一次性生成两个完整 source 变体及 GeoJSON。真实 driver/native 适配代码归 M2，M4 只做现场验证；baseline 路径不入 config/local。旧 five-only 变体需在空目标重新 prepare，不自动覆盖现有数据；
- `init`：只在全新根建立首态；已有任一普通状态文件、`states/<source>` 自身或其树内有任一 symlink、或已有 `DONE` 时必须拒绝；symlink 不跟随且不区分目标类型；
- `run`：日常循环，不自动 bootstrap；`output/` 根缺失或不是目录时必须停源，不能当作全新链，也不能触发状态/产物残留清理；M2 必须先把它接到 `controller.run_sources`，逐源注入生产 Slurm executor、attempt driver、10 秒 poll wait 与独立失败退出码 provider，M4 只做真实 node-22 验证和 cron 安装。

在 CLI 尚未实现和通过本地测试前，禁止用手工 shell 拼出“等价生产流程”并声明完成。

`prepare` 和 `init` 都改变长期状态，必须有当前任务明确授权和现场 receipt；不得由 cron 自动调用。

`prepare` 启动时若发现 `YD_ROOT` 顶层有名字以代码常量 `prepare._STAGING_PREFIX` 开头的条目，必须列出全部精确路径并拒绝；程序不得按 PID、mtime 或条目类型猜测它已陈旧，也不得自动删除。人工处置顺序固定为：先确认没有活动的 `yd-producer prepare` 进程或对应现场操作；再逐项 `lstat` 并核对 CLI 报出的每个精确路径仍是待处理条目；记录路径、类型与处置 receipt；最后只清理这些已核对的精确条目，再重新运行 `prepare`。禁止通配符删除、`find -delete` 或在未确认无活动实例时清理。`init` 不认领也不清理该命名空间；正常顺序仍是 `prepare` 成功并确认无 staging 残留后才执行 `init`。

### 8.2 cron 与 flock

- cron 只调用 `run`，且直接调用 checkout 内 `producer/.venv/bin/yd-producer`；不用裸 `uv run`（每 tick 隐式 sync，违反 §7.1），如需 uv 入口只允许 `uv run --frozen --no-sync`；crontab 顶部须设 `PATH=` 包含 `sbatch`/`sacct` 所在目录（cron 默认 PATH 没有 Slurm 客户端）；
- 非阻塞 `flock -n` 语义由 CLI 自身的 `runlock` 在 `cron.lock_path` 上提供，已有实例时本 tick 跳过；cron 行**不得**再套外层 `flock -n` 同一文件——外层持有的 open file description 会让 CLI 内部的第二次 `flock` 永远拿不到锁，每 tick 静默跳过；
- 锁覆盖发现、Slurm 提交、等待、NFS 发布和清理的完整生命周期；
- 手工 `run` 使用同一把锁，不能绕开；
- `cron.lock_path` 必须位于 node-22 本地文件系统的专属 `run/` 目录，不得位于 yd/NWM NFS 或其它网络、共享挂载。部署时必须按该路径的实际挂载信息确认并写入 receipt，不能按路径前缀或 hostname 猜测；Linux 在 NFS 上会把 `flock` 仿真为整文件 byte-range lock，本项目依赖的 per-open-file-description 判别前提在那里不成立；
- 锁文件是长期哨兵。producer 释放时只 unlock/close；retention、work、staging 等任何清理以及运维命令、tmp sweeper 等外部主体都不得 unlink、rename、replace `cron.lock_path`，也不得删除或替换其专属 `run/` 目录。迁移该路径前必须先停 cron，确认无 controller 持锁或运行，再更新现场配置并记录 receipt；
- `runlock` 在 `flock` 成功后必须以 `fstat(lock_fd)` 冻结 `(st_dev, st_ino)`，并在调用 controller 前、controller 返回或抛错后但 unlock 前，分别以 no-follow path stat 确认 `cron.lock_path` 仍是同一普通文件。首次取锁后的检查不一致时只允许释放旧 fd 并重取一次；再次不一致、路径缺失、symlink、类型异常或持锁期间 identity 漂移都必须响亮失败，不能修补、重建或静默报告成功。controller 自身异常与 identity 漂移同时发生时保留原异常并附加锁漂移证据；任何路径都仍须 unlock/close；
- 上述 identity 检查用于在入口和退出边界发现违反生命周期约束的替换，不宣称能阻止两个检查点之间的不合作外部 unlink；禁止外部删除长期哨兵才是防止新 inode 上出现第二持有者的必要前提；
- 每次 run 在本源首次前沿发现前完整扫描 `work/<source>/` 顶层合法 00/12 cycle：先确认 `output/` 根可枚举，只对同源 `DONE(T)` 经 no-follow 判为普通文件的真实目录 exact work 做 identity-bound 删除，并把 source/cycle/绝对 path 写进本轮报告；无有效 DONE 的候选全部保留，扫完后以最早 cycle 停源待人工确认，不能遮住其它可回收目录；
- 不同时启动第二个前台 controller；
- cron 最终分钟点由现场配置决定，未定前不写死。

### 8.3 Slurm

- forcing 与 SHUD 重任务都在 Slurm 作业内执行，不在登录节点直接计算；
- 同源最多一个 job，IFS/GFS 最多各一个；
- 只通过 yd CLI 提交，避免手拼 `sbatch` 参数；
- 普通轮询用 `sacct` 读取 job ID/state/start/end，不取 `ExitCode`；提交后 120 s 内 `sacct` 返回 0 行按 accounting 滞后视为仍 PENDING 继续轮询（compute-loop §10），超窗仍 0 行或任何多行照旧 fail closed；`submitted_at` 按整秒记录，与 `sacct` 的秒精度对齐（compute-loop §10）；仅在同一 yd job 已终态 `FAILED`/`TIMEOUT` 后，由失败收尾 provider 单独执行一次 `sacct -j <job_id> -X -n -P --format=ExitCode`，所得字符串进入失败日志；
  - `-X`（`--allocations`）不可省：它和普通轮询一样只选 job allocation，排除 `.batch` / `.extern` 等 step；不能在解析器里取首行、去重或猜 allocation。即使带 `-X` 仍出现多非空行，也按查询失败保留 work。
- 取消必须使用本次 yd receipt 中的精确 job ID；禁止 `scancel -u`、名称通配或模糊匹配；
- 不为未观察到的作业卡死编写 watchdog；walltime 属 Slurm 作业配置，异常由日志和人工操作处理；
- 每次 `sbatch`、轮询 `sacct` 与失败 ExitCode `sacct` 客户端命令必须使用 `[slurm].command_timeout_seconds`（缺席默认 60 秒）的同一时限；这不是 job walltime/watchdog。命令 timeout 后保留 work、停本源且不自动重试/删 work，兄弟源继续；`sbatch` 可能已被服务端接收，运维须按保留证据排查，下一 tick 的无 DONE work 闸会阻止重复提交；
- Slurm partition/account/CPU/内存/walltime 与客户端 command timeout 只放 `local.toml`；timeout 从 JobSpec 资源映射剥离。

### 8.4 发布

控制器是唯一 NFS writer，顺序不可改变：

1. 保留 cycle T 的旧状态；
2. DAT 复制到 NFS 临时文件并在 NFS 内 rename；
3. T+12 状态复制并 rename；
4. 最后创建 `DONE`；
5. 之后才清理旧状态和 scratch。

没有 `DONE` 就按整轮未发布处理。不要手工补 `DONE`，也不要把只有 DAT 的目录改成“完成”。失败只保留一份合并 stdout/stderr 日志。

### 8.5 node-22 禁区

- 不连接 `localhost:55433`；
- 不设置或继承 NWM `DATABASE_URL`；
- 不修改 NWM scheduler timer/env/registry；
- 不在 NWM raw 根运行清理；
- 不把 yd work 放进 NWM `nhms-prod` 的受管子目录；
- 不在登录节点执行 SHUD 重计算；
- 不把 NFS 路径传给看不到 NFS 的计算节点作为发布目标。

## 9. node-27 viewer 操作

### 9.1 旁路边界

node-27 是 NWM active production host。yd 只能操作：

- 独立的 yd 镜像和 compose project；
- 独立回环端口；
- `/home/ghdc/yd/input/viewer` 与 `/home/ghdc/yd/output` 两个只读挂载；
- Nginx 中唯一的 `/yd/` location；
- yd 自己的 env、日志和 health receipt。

明确禁止：

- 操作 `nhms-db` 容器、PG `:55432`、NWM display API `:8080`；
- 运行 NWM `start-display-api.sh`；
- restart/stop NWM 容器或 user systemd units；
- 修改 NWM `/`、`/ops`、download、autopipe、ingest、MVT 或前端文件；
- 用 NWM 开发 compose 文件作为 yd 或数据库部署模板；
- 以生产 `DATABASE_URL` 跑测试。

若发现 NWM DB/display 异常，停止 yd 操作并按 NWM 当前 runbook 交给对应运维流程；不要顺手修。

### 9.2 端口与容器

- yd 端口必须先检查占用，现场确认后写入 node-27 私有 env；
- host 端口只绑定 `127.0.0.1`，由 Nginx 对外；
- compose project 名、container 名、network 和 image tag/digest必须带 yd 前缀，避免与 NWM 冲突；
- 挂载必须显式 `:ro`；不挂整个 `YD_ROOT`；
- env 文件为 0600、属主 `nwm:nwm`，不入 Git；
- 天地图 URL/key 只走 env 或部署配置，不打印进 receipt；现役 NWM key 可复用（用户裁决 2026-09-22，key 绑定域名白名单），复制只在 node-27 上私有 env 之间进行，不经聊天、Git 或 receipt；
- 升级前记录当前镜像 digest和 compose 配置位置，保留上一镜像用于回滚。

M3 容器配置合同（这里只规定打包与配置，不执行 M5 部署）：

- `viewer/Dockerfile` 多阶段构建：Node 22 执行 `corepack pnpm install --frozen-lockfile` 与 `corepack pnpm build`；Python 3.12 用 `uv sync --frozen --no-dev` 安装后端，复制前端 dist；运行镜像不含 Node、pnpm、uv 缓存或 dev 依赖。
- Dockerfile 以 `ENV YD_VIEWER_STATIC_DIR=<镜像内前端目录>` 固定静态目录，含 `index.html` 且对运行用户可写；运维不得覆盖，`viewer/env.example` 与 `viewer/compose.example.yml` 均不得包含该变量。
- entrypoint 与 uvicorn 使用同一非 root 用户；`viewer/entrypoint.sh` 在启动前写 `$YD_VIEWER_STATIC_DIR/basemaps.json`，随后 `exec uvicorn` 固定监听 `0.0.0.0:8000`，容器端口不读 env。
- 运维 env 清单仅为 `YD_VIEWER_INPUT_DIR`、`YD_VIEWER_OUTPUT_DIR`、`YD_VIEWER_PORT`、`YD_BASEMAP_VECTOR_URL`、`YD_BASEMAP_SATELLITE_URL`、`YD_BASEMAP_TERRAIN_URL`、`YD_BASEMAP_VECTOR_ANNOTATION_URL`、`YD_BASEMAP_SATELLITE_ANNOTATION_URL`、`YD_BASEMAP_TERRAIN_ANNOTATION_URL`；env.example 列键但无真实值。
- 两个目录 env 指向容器内只读挂载；host 端口映射固定形状为 `127.0.0.1:${YD_VIEWER_PORT}:8000`，env_file 指向不入库的私有 env。compose 示例恰有 input/viewer、output 两条 `:ro` 挂载，不挂整个 `YD_ROOT`，不写挂载路径。
- compose project（`name`）、service、container、network、image 均用 `yd-` 前缀，例如依次为 `yd-viewer`、`yd-web`、`yd-web`、`yd-network`、`yd-viewer:<tag>`，不借用 NWM 对象。
- basemaps JSON 的键为 `vector`/`satellite`/`terrain`；有底图 URL 才写 `{"tiles":[url],"annotation":[url]或null}`，全缺写 `{}` 并正常进入 uvicorn。URL 必须原样写入，stdout/stderr 与 receipt 不得出现 URL/key；不得开启 shell trace 打印秘密。文件位于镜像文件系统而非 NFS 挂载。前端消费规则见 [design.md](design.md) §7。

部署是对外动作，实际执行前必须有明确授权。构建、加载、启动失败要原样报告，不用重启 NWM 服务“试试”。

### 9.3 Nginx `/yd/`

目标语义：

```nginx
location /yd/ {
    proxy_pass http://127.0.0.1:<yd-port>/;
}
```

`proxy_pass` 末尾 `/` 用于剥掉 `/yd/` 前缀。实际修改时：

1. 先读取并备份当前 `/etc/nginx/conf.d/test.nwm.ac.cn.conf`；
2. 确认只增加或修改 `/yd/` location，不改变 `/`、`/ops`、TLS 或其他 upstream；
3. 先验证 yd 本地回环 health；
4. 执行 `sudo nginx -t`；
5. 仅在配置检查成功后执行 `sudo systemctl reload nginx`；
6. **禁止 `restart nginx`**；
7. 验证公网 `/yd/api/health` 和页面，同时复核 NWM 原 `/health`、`/`、`/ops`。

任何 Nginx 变更都是 outward-facing，执行前需要明确授权。若 `nginx -t` 失败，不 reload，恢复文件并报告错误。

### 9.4 健康与排障顺序

1. 容器状态与 yd 日志；
2. 回环 `http://127.0.0.1:<yd-port>/api/health`；
3. NFS 挂载和 node-27 `nwm` 账户的目录遍历/读取权限；
4. `https://test.nwm.ac.cn/yd/api/health`；
5. 浏览器 `/yd/`；
6. 仅当回环成功而公网失败时检查 `/yd/` Nginx location。

不要因为 yd health 失败去重启 NWM display API、PG 或 Nginx。

### 9.5 回滚

viewer 回滚只作用于 yd：

- 恢复上一 yd 镜像 digest/tag 与 compose 配置；
- 复用同一只读 NFS 挂载；
- 验证回环 health；
- Nginx location 未变时无需 reload；
- 若本次新增 `/yd/` 且要撤回，恢复备份配置，`nginx -t` 后 reload；
- 不回滚、不 checkout、不重启任何 NWM 组件。

## 10. 权限与密钥

- `YD_ROOT/output`、`states`、`logs` 的写入者只能是 node-22 producer；
- node-27 只需 `input/viewer` 和 `output` 的读/遍历权限；优先使用双方共享组和目录 setgid；若现场采用 `a+rX`，只作用于发布目录，不递归开放模型、状态和日志；
- 从 node-22 发布后必须以 node-27 `nwm` 身份实际读取验证，不能只看 22 权限位；
- 复制 scratch 文件不用 `cp -a` 把计算节点 uid/gid/模式带入 NFS；由控制器按发布权限创建；
- `.env`、`local.toml`、token、天地图 key、SSH/DB/SMTP 密钥均不入库；
- 私有配置必须是普通文件、0600，拒绝意外 symlink；
- receipt 中记录配置键名和非敏感值，不记录 key、密码或完整带凭据 URL。

## 11. 验证 oracle 与 receipt

### 11.1 路由

| 改动 | 必须在哪验证 |
|---|---|
| 文档、纯函数、v2 解析、API、目录窗口 | 本地测试与文档一致性检查 |
| direct-grid、forcing、SHUD、T+12、00Z/12Z、Slurm | node-22 真运行 |
| NFS 权限、容器、Nginx、地图与曲线 | node-27 真产物 live receipt |
| 客户侧计算/部署 | 本期无 oracle，不得声明完成 |

### 11.2 node-22 receipt 最小字段

- yd commit SHA；
- source、cycle、运行入口和配置文件路径（不含秘密）；
- raw 根与实际读取文件集合摘要；
- raw 操作前后未修改证据；
- Slurm job ID、partition、状态、开始/结束时间；
- SHUD `START=0`、`END=7`、`DT_QR_DOWN=60`；
- DAT v2、168 行、分钟 `0..10020`、3988 reach；
- T+12 checkpoint 文件名和下一轮消费证据；
- NFS DAT、状态、`DONE` 的提交时间顺序；
- scratch 已清理或失败日志路径。

### 11.3 node-27 receipt 最小字段

- yd 镜像 digest、compose project、回环端口；
- `/home/ghdc/yd` 与 node-22 cycle/source 的同一 NFS 身份；
- 回环与公网 yd health；
- `/api/cycles`、`/api/map/latest`、单河段单/双源曲线摘要；
- 168 点、地图 GFS 优先/IFS fallback、colorbar、历史起报、三种底图；
- Nginx `-t` 与 reload 结果（发生配置变更时）；
- NWM 原 `/health`、`/`、`/ops` 在操作前后未受影响。

本地绿色测试、合成 fixture、NWM 自身线上产物都不能冒充 yd 的 22/27 真闭环 receipt。

## 12. 标准发布顺序

1. 本地修改 docs/代码，运行本地门禁；
2. commit/push 只在用户明确要求时执行；
3. node-22 同步指定 commit，先做 producer 真运行和 NFS receipt；
4. node-27 确认同一 NFS 已可读；
5. 构建/加载指定 yd 镜像并以独立端口启动；
6. 回环 health；
7. 经授权后修改 `/yd/` location，`nginx -t` + reload；
8. 完成 node-27 API/浏览器 receipt；
9. 复核 NWM 服务无影响；
10. receipt 与 commit 一起留档。

没有 node-22 真产物时，可用合成数据开发 viewer，但不得把它计作 M4/M5 完成。

## 13. 完工纪律

- 先 docs，后代码；
- 不修复任务范围外发现，只在报告中列出；
- 不删除、覆盖或重启未知现场对象；
- 不在不可逆或 outward-facing 操作前自行扩大授权；
- 未跑过对应 oracle 时不得使用“已验证”“可上线”“可迁移”；
- 测试失败、步骤跳过、节点不可达必须如实写进最终报告；
- 每次跨节点操作结束时留下 commit、命令、结果和路径可复核的 receipt。

## 14. 应急 yd-NWM 副本实例

主线（M2–M5 producer/viewer）不变。为尽快获得 yd 可看可算的服务，另起一套 **NWM 完整服务的独立副本实例**，只注册 yd 流域。本节记录 2026-09-01 两节点只读勘察结论与部署边界；实际部署每一步仍需当时明确授权，并按 §13 留 receipt。

### 14.1 原则

- 副本代码与 NWM 上游**逐字节一致**，仅允许 14.4 登记的 patch；其余隔离全部落在部署身份层（checkout、env、unit、端口、路径）。禁止 sed 批量改名 `nwm`/`nhms` 内部符号。
- 副本不能影响 NWM 业务化：不修改 NWM 的 checkout、unit、env、registry、DB、object-store、raw；不占 `:8080`/`:55432`；Nginx 只新增 `/yd/` location，`nginx -t` 后 reload，禁 restart。
- 所有破坏性服务（retention/compression/governance）的作用域由 env 圈定（已逐脚本核实无硬编码路径）；副本的 `DATABASE_URL`、object-store/pgdata 根、以及全部 `*_LOCK_PATH`/`*_LOG_ROOT`/`*_RECEIPT_PATH`/`*_REPO(_ROOT)` 必须换成 yd 专属值——锁默认在 `/tmp`，照抄会与 NWM timer 互斥/竞态。

### 14.2 勘察结论（2026-09-01，只读）

node-27（`nwm@210.77.77.27`）：

- NWM 生产为裸金属 user systemd：display-api（uvicorn，`127.0.0.1:8080`）、download/autopipe/frontier-alert/raw-retention/resource-governance/timeseries-compression/timeseries-retention 各 timer 均 active；
- PG 是 `nhms-db` 容器，`127.0.0.1:55432->5432`；**`8081` 空闲**；
- NFS 根 `/home/ghdc/`（22 侧 `/ghdc/data/`）权限 777，可建 yd 副本专属同级根；`/home/ghdc/yd` 为主线 YD_ROOT（当前为空），副本数据面不得放入其中；
- NFS 总量 1.7T、余 164G（90% 用）；NWM raw 14 天滚动仅 3.0G，副本独立下载 raw 体量可承受；
- `Basins/` 共 33 个流域，**无 yd**；yd 原始数据的权威源是 NFS `/home/ghdc/yd`（22 侧 `/ghdc/data/yd`）：`input/yd/` 全套 SHUD 成员 + `CALIB/lz.calib`，`gis/` 含 domain/river/seg 三套完整 shapefile，布局符合 Basins 约定（registry import 所需的 `gis/river.shp`、`gis/seg.shp` 均在）——副本注册 yd 的成本即拷贝该目录到副本的两棵 Basins 树。本仓 `fixtures/input/yd/` 只是它的本地子集（缺 `seg.*`），不作为注册来源。

node-22（`frd_muziyao@210.77.77.22`）：

- 生产 scheduler = user systemd `nhms-compute-scheduler.timer`（每 5 分钟）→ `plan-production --submit --continuous --max-passes 1`，EnvironmentFile 为 `compute.scheduler-dbfree.env`；compute-api、slurm-gateway 均 active；
- NWM checkout `/scratch/frd_muziyao/NWM`（勘察时 HEAD `ea6bcf1c`，仅 untracked `.nhms-work/`）；
- `/volume/nwm/Basins`（本地 175T 盘，余 84T）与 NFS `/ghdc/data/nwm/Basins` 是**两棵独立树**：scheduler 读 /volume，27 侧 ingest 读 NFS——副本的 Basins 也要两处各放一份；
- Slurm 分区 CPU（24 节点）/GPU（1），walltime 上限 10 天；`/scratch` 余 18T。

### 14.3 身份隔离矩阵

| 层 | NWM 现值 | yd 副本 |
|---|---|---|
| checkout | 27 `/home/nwm/NWM`；22 `/scratch/frd_muziyao/NWM` | 独立 clone（如 `.../yd-NWM`）+ 独立 venv；具体路径部署时登记 |
| systemd | `nhms-*` user units | 全套 `yd-*` 前缀，ExecStart/WorkingDirectory/EnvironmentFile 指 yd checkout 与 yd env |
| display 端口 | `127.0.0.1:8080` | `127.0.0.1:8081`（`NHMS_DISPLAY_API_PORT` 本为变量） |
| PG | `nhms-db` 容器 `:55432` | 第二容器（如 `yd-db`）：新端口、新 pgdata、库名 `yd` |
| 数据面 | `/{home/ghdc,ghdc/data}/nwm/...` | NFS 同级新根（如 `.../yd-nwm/`）：object-store、published、Basins（NFS 份）；22 本地 `/volume` 下新根放 Basins（scheduler 份） |
| Slurm | 现有 job 名 | job-name `yd_<stage>` 前缀（2026-09-01 用户裁决后交付，fork commit `9bff45df`：14 个 sbatch 模板 + reconcile 双前缀容忍——sacct 名字校验是次级防护，legacy `nhms_*` 行永久可接受以覆盖切换窗）；同集群同分区 |
| 流域注册 | 33 basin | 副本 Basins 树只放 `yd/` 一个流域 |
| Nginx | `location / → :8080` | 仅新增 `location /yd/ { proxy_pass http://127.0.0.1:8081/; }`（剥前缀语义，§9.3） |

### 14.4 已登记代码 patch（fork 内登记维护）

1. `services/orchestrator/source_cycle_raw_manifest.py:38-39`：`NODE22_CANONICAL_NFS_RAW_AUTHORITY_ROOT`（现 `/ghdc/data/nwm/object-store`）与 manifest 前缀是代码字面量且 preflight 强制相等，副本改为 yd 数据面根（22 侧 fork commit `d65303cd`）；
2. `apps/frontend/src/App.tsx`：`BrowserRouter` 增加 `basename={import.meta.env.BASE_URL.replace(/\/+$/, "")}`（NWM 自身构建 BASE_URL=`/`，行为不变；27 侧 fork commit `537fc4a4`）；
3. `config/calibration_overrides.yaml` 置空为 `calibration_overrides: []`（上游含 hetianhe 条目，registry publisher 对 yd-only inventory fail-closed 拒发；配置文件而非代码，仍按 patch 登记；22 侧 fork commit `e75d2907`）——部署中发现，待用户追认，可否决回退；
4. `apps/frontend/src/api/base.ts`：`buildApiUrl` 对相对前缀 base（`/yd`）改走字符串拼接——`new URL(path, "/yd/")` 因 base 非绝对 URL 直接抛 TypeError，React 整树崩溃白屏（2026-09-01 上线后用户报障，headless Chrome 复现定位）；NWM 现行两条路径（空 base、绝对 URL base）行为不变（27 侧 fork commit `4c7b89a5`）；
5. Slurm job-name `yd_<stage>`（22 侧 fork commit `9bff45df`）：14 个 `infra/sbatch/*.sbatch` 模板 + `services/orchestrator/reconcile.py`（`_expected_job_name_token`→`yd_`、`FALLBACK_JOB_NAME="yd_forecast,nhms_forecast"`、`_GENERIC_ARRAY_JOB_NAMES` 双前缀、`_strip_job_name_prefix` 容忍 legacy 行，规避切换窗 wedge）；
6. yd 展示定制（27 侧 fork commit `edde7932`，2026-09-01 用户要求）：径流分档取消 1000–10000 档、`>1000` 直接红色 `#CB181D`（`overviewDataContracts.ts` 图例+`m11DischargeColor`、`m11MapBuilders.ts` MVT log 阶插值 stops 同步）；`OverviewPage.tsx` 单流域部署时初始相机 fit 到该流域 bbox（多流域行为不变）；
7. 流域边界总开关（27 侧 fork commit `c44b7161`）：`m11MapBuilders.ts:58` `m11BasinBoundaryOverlayEnabled` false→true——上游产品口径不展示边界，yd 单流域部署需要；headless 截图验证边界多边形+描边+Yd 标签正常渲染；
8. 空边界提示防闪（27 侧 fork commit `b991b507`）：翻开边界开关后暴露上游瞬态——basins 先到、versions（含几何）后到的窗口里「当前没有可见流域边界」闪现；`m11MapRuntime.tsx` 给该提示加 1.5s 驻留判定（`useSettledCondition`），真实缺边界仍提示；配套测试改用 fake timers 并固化「瞬态不显示」断言。

代码之外的实例数据变更（非 patch，登记备查）：`core.basin_version` 中 `basins_yd_vbasins.geom` 由注册导入的 7891 个 mesh 三角形（31,564 点，被前端几何预算拒绝 → 无边界/无 bbox）替换为 mesh union+simplify 的 238 点流域边界 MultiPolygon（SRID 4490 保留，`ogr2ogr ST_Union` 自 `/home/ghdc/yd/input/yd/gis/domain.shp`）；原 geom 备份 `/home/nwm/yd-backup-basins_yd_vbasins-geom-20260901.json`（0600）。该表仅注册导入路径写入，autopipe 不覆写。

前端构建：`--base=/yd/` + `VITE_API_BASE_URL=/yd`（API client 与 MVT 瓦片 URL 均取自该变量，已核实无其它根绝对调用）。

### 14.5 部署前已知缺口

- 注册来源用 NFS `/home/ghdc/yd`（完整，含 `seg.*`），不用本地 fixture 子集；`/home/ghdc/yd` 同时是主线 YD_ROOT 的现场根，副本只读拷出，不在其中新建任何目录；
- 副本 venv 构建方式照抄 NWM 现场同款（NWM #1831 冻结约束只作用于 NWM 自己的 checkout，不约束副本 checkout，但部署时先确认现场构建方法）；
- NFS 余量 164G 需在副本 retention 生效前监控。

### 14.6 上线登记（2026-09-01）

2026-09-01 部署完成并上线，逐步 receipt 见 `/home/nwm/yd-deploy-receipt-20260901.md`（27）与 `/scratch/frd_muziyao/yd-nwm-prod/deploy-receipt-20260901.md`（22）。要点：

- checkout：27 `/home/nwm/yd-NWM`（`537fc4a4`，branch yd-instance）；22 `/scratch/frd_muziyao/yd-NWM`（`e75d2907`）；
- 端口：display `127.0.0.1:8081`，`yd-db` 容器 `127.0.0.1:55434`（pgdata `/home/nwm/yd-pgdata`），slurm-gateway `127.0.0.1:8092`；
- 数据面：NFS `/{home/ghdc,ghdc/data}/yd-nwm/`；22 本地 `/scratch/frd_muziyao/yd-nwm-prod/`（Basins scheduler 份在 `/scratch/frd_muziyao/yd-nwm/Basins`，未用 /volume）；
- 公网：`https://nwm.ac.cn/yd/`、`https://test.nwm.ac.cn/yd/`（两 conf 各插一处 `location /yd/`，`nginx -t` 后 reload）；
- registry：仅 `basins_yd_shud`，direct-grid 2 行 canonical（dg-gfs-8827efa1…/dg-ifs-f2e14f8c…），packaged-IC audit 4/4 qualified；
- timers（enabled）：`yd-node27-download` 30 min、`yd-node27-autopipe` 10 min、`yd-compute-scheduler` 5 min；2026-09-01 用户裁决后加 `yd-node27-raw-retention`（每日 04:05 UTC，14 天窗，anchor=display watermark）与 `yd-node27-timeseries-retention`（每日 05:45 UTC，14 天窗，enforce，archive gate=disabled 按 ADR 0002 Rev 2026-08-11）——env/锁/日志根全 yd 前缀，首跑均 rc=0 零删除、作用域核实仅 yd 根。**compression/governance/frontier-alert 类 timer 仍有意未装未启**；
- scheduler 回看窗保持 96h（用户裁决）：更老的已拷 raw 只作存档，不会被计算；
- 首轮全链（cycle 2026082712 双源）≈10–11 min/cycle，state index 已闭合（entry_count 4）；
- 已知偏差：`AUTOPIPE_MVT_PREWARM_ENABLED=0`（prewarm 会打 `:8080`，属只读越界，已关）。Slurm job-name 偏差已于当日修复（patch 5）。

## 15. M4 部署登记（node-22 主线 producer）

本节是 §3 所指的「部署清单」。值来自 2026-09-21 只读勘察（receipt：`/scratch/frd_muziyao/yd/receipts/m4-recon-20260921.md`）与同日用户裁决；现场执行的每一步 receipt 追加到同一目录，并在本节「执行登记」追记。**本节只登记路径、键名与非敏感值，不含任何密钥。**

### 15.1 勘察结论（2026-09-21，只读）

- 文件系统：`/scratch` 是 NFS（`flash:/scratch`），`/users/frd_muziyao` 是登录节点本地 ext4 且与 `/opt` 一起 NFS 导出给计算节点（`showmount -e`），`/ghdc/data` 是 `ghdc:/home/ghdc` NFSv4；
- NWM raw 根 `/ghdc/data/nwm/object-store/raw/` 下 source 目录名为 `gfs` 与 `IFS`，与 `rawscan.SOURCE_DIR_NAMES` 逐字一致（#53 销账）；每 cycle 目录另含一份 `manifest.json`，不在 bundle 模式内；
- 基线包 `/ghdc/data/yd/input/yd/`：12 个 native 文件 + `yd.tsd.forc`、`yd.tsd.rl` + `gis/{domain,river,seg}.{shp,shx,dbf,prj}`，顶层恰一个 `yd.cfg.ic`；`.prj` 为 WGS84 基准 Albers 投影，pyproj 转换 accuracy 0.0、非 ballpark（#36 放行）；
- `/ghdc/data/yd` 为 `frd_muziyao:nwmuser 770`，`input/models`、`input/viewer`、`states`、`output`、`logs` 均不存在，无 `.yd-prepare-staging*` 残留；node-27 `nwm` 属 `nwmuser`（gid 1107），可穿越；
- NWM checkout `/scratch/frd_muziyao/NWM` HEAD `87236ca5`，包含 pin `8ae9b8f2`，prepare driver 依赖的全部模块自 pin 起零提交；`canonical/{gfs,IFS}/grid/{gfs_0p25,ifs_0p25}/{grid.json,grid_snapshot_metadata.json}` 在 checkout 内；活动解释器 `/scratch/frd_muziyao/NWM/.venv/bin/python` → `/opt/anaconda3/2024.10/bin/python3`（3.12.7）；
- Slurm：分区 `CPU`（24 节点，MaxTime 10 天）、账户 `friends`；`sacct -j <id> -X` 对已有作业恰 1 行（#60 样本）；`sbatch`/`sacct` 在 `/usr/bin`；
- SHUD 二进制（用户裁决 2026-09-21）：`/scratch/frd_muziyao/shud-bin/cpu-accel-v1.1.1/shud`，sha256 `4254222b2180de4bc7697f40cc0c8c19a59ef24d2cfdcbd5a6cef6f281914c5e`（OpenMP 版，PROVENANCE 见同目录）；RUNPATH 指向 `/users/frd_muziyao/sundials/lib` 与 `/scratch/frd_muziyao/local/hypre-3.1.0/lib`；
- 工具链：`uv` 0.11.25 在 `~/.local/bin`（不在默认 PATH）；出网正常；无 crontab；umask 0022；
- 副本实例 `yd-compute-scheduler.timer` 每 5 min 活动，作业名前缀 `yd_`，与主线 `yd-{source}-{cycle}` 不同前缀。

### 15.2 路径与配置（用户裁决 2026-09-21）

| 项 | 值 |
|---|---|
| checkout | `/scratch/frd_muziyao/yd/checkout`（本仓 master） |
| venv | `<checkout>/producer/.venv`，uv 托管 Python 3.12（默认目录 `~/.local/share/uv/python`，已导出给计算节点），`uv sync --frozen` |
| `local.toml` | `<checkout>/producer/local.toml`（gitignored，0600） |
| `yd_root` | `/ghdc/data/yd` |
| `scratch_root` | `/scratch/frd_muziyao/yd/loop` |
| `cron.lock_path` | `/users/frd_muziyao/yd-run/yd-producer.lock`（本地 ext4；`~/yd-run/` 即 §8.2 所说的专属目录，home 内只放此一项） |
| `cron.log_dir` | `/scratch/frd_muziyao/yd/logs` |
| receipts | `/scratch/frd_muziyao/yd/receipts/` |
| `shud_binary` | 见 15.1 |
| `[nwm]` | `raw_root=/ghdc/data/nwm/object-store/raw`、`canonical_root=/ghdc/data/nwm/object-store/canonical`（grid.json 权威，2026-09-21 首轮 run 实证）、`checkout_root=/scratch/frd_muziyao/NWM`、`python=/scratch/frd_muziyao/NWM/.venv/bin/python` |
| `[slurm]` | `partition=CPU`、`account=friends`、`cpus=4`、`memory=8G`、`walltime=02:00:00`；`command_timeout_seconds` 取默认 60 |
| cron | `17 * * * *`，行形态按 §8.2 |
| `prepare --baseline` | `/ghdc/data/yd/input/yd`（原地，不复制） |

### 15.3 M4 验证口径（用户裁决 2026-09-21）

- 每源至少 2 个连续 cycle SUCCEEDED，且 IFS/GFS 都覆盖 00Z 与 12Z；第二轮 receipt 必须引用第一轮写出的 `<T+12>.cfg.ic`；
- 「单源失败不影响另一源」、requeue/PREEMPTED/sacct 多行只观察不诱发；未发生时 receipt 写「未行使」；
- node-27 以 `nwm` 身份实读 `output/<T>/<source>/{yd.rivqdown.dat,DONE}` 与 `input/viewer/*.geojson` 是 M4 出口条件（§10、§12 步骤 4）；
- 首次 `run` 为积压追赶（7 天窗内全部完整 cycle），在 tmux 内执行，取证窗口为作业运行中（成功后 exact work 即删除）。

### 15.4 执行登记

receipt 目录 `/scratch/frd_muziyao/yd/receipts/`；每行一步，失败也登记。

- 2026-09-21 阶段 2 环境落地：checkout `e0561b3`，uv Python 3.12.13（`/scratch/frd_muziyao/.local/share/uv/python/…`），pytest 3729 passed / 4 skipped，`local.toml` 装载正反验证，锁 `~/yd-run/yd-producer.lock`（ext4）。`m4-stage2-env-20260921.md`。
- 2026-09-21 阶段 3 prepare：第 1 次因真实 `yd.cfg.ic` 的 river 段前导行被解析器拒绝（#305 → PR #306）；第 2 次（checkout `e61917e`）成功，四终名齐全，无 staging 残留。`m4-stage3-prepare-attempt1-20260921.md`、`m4-stage3-prepare-20260921.md`。`output/` 由人工 `mkdir -m 755` 预建（run 要求预存在）。
- 2026-09-21 阶段 4 init：两源首轮 T=2026091412，`states/{gfs,ifs}/2026091412.cfg.ic`。`m4-stage4-init-20260921.md`。
- 2026-09-21 阶段 5 run attempt 1：双源停止、零发布。ifs：sacct 提交后 0 行 → poll fail closed（A）；作业 52756 因 grid_signature 不一致 FAILED（B：prepare 用 checkout grid.json，运行期用 object-store grid）；gfs：apcp 缺 `idx_selector`（C：manifest v3 形态）。裁决 A/B/C/D 见 `m4-stage5-run1-20260921.md`；本节 §15.2 已按 B 增 `canonical_root`。修复合并后按 D 清理 `input/models`、`input/viewer`、`states`、失败 work，重跑 prepare → init → run。
- 2026-09-21 阶段 5 重跑前置（#308 PR #312、#309 PR #314、#310 PR #316 合并后）：checkout ff-only `e61917e` → `cc1ade8`，`uv sync --frozen`；`local.toml` `[nwm]` 增 `canonical_root`（备份 `receipts/local.toml.bak-20260921-pre-canonical`）；决策 D 清理四项（`m4-stage5-rerun-cleanup-20260921.txt`）；prepare 成功且 #309 oracle 通过：两源 handoff `grid_signature = 6c008901…`，两份 object-store grid.json 仅 `grid_id` 不同、经纬度相同（`m4-stage5-rerun-prepare-20260921.log`）；init 两源首轮 T=2026091500（`m4-stage5-rerun-init-20260921.log`）。
- 2026-09-21 阶段 5 run attempt 2：sbatch 52782/52783 成功、双源 work 建立（gfs raw staging 含 #310 apcp 自证通过），控制器随即在 `phase=poll` 因 `started_at 不得早于 submitted_at` 停源（E：sacct Submit=Start 同一秒，`submitted_at` 带微秒）。裁决 E1：提交时刻截到整秒（compute-loop §10）。作业不取消，待其自然结束；修复合并后删 `loop/work/{gfs,ifs}/2026091500` 只重跑 run。`m4-stage5-rerun-20260921.md`、`rerun-raw-stat-before.txt`。
- 2026-09-21 阶段 5 run attempt 3（#319 PR #320 合并后，checkout `a1073e3`）：13:19Z 启动，15:41Z 自然退出；两源各 13 轮 SUCCEEDED（2026091500→2026092100，00Z/12Z 全覆盖），26 个作业全部 COMPLETED（CPU/friends，每轮 10–11 min）；26 份 DAT 均 3988 列、168 行、分钟 `0..10020`；DAT→`<T+12>.cfg.ic`→`DONE` 顺序在 2026092012/2026092100 两轮 ns 级验证；第二轮消费第一轮状态成立；raw 前后快照仅 `raw/{gfs,IFS}/2026092100/` 目录与 `manifest.json` 的 mtime 变动（nwm 所有、yd uid 无写权限，尺寸/inode 不变，判为 NWM 侧复核；快照无内容哈希，内容不变未证）；#308 grace 与 D6 故障路径未行使。退出码未捕获（launcher 后台化）。`m4-stage5-rerun-20260921.md`、`m4-stage5-rerun-run3-evidence-20260921.txt`。
- 2026-09-21 阶段 6：node-27 以 `nwm` 实读 26 份 `output/<T>/<src>/{yd.rivqdown.dat,DONE}`（sha256 与 22 侧一致）与两份 GeoJSON；发现 `states/`、`input/models` 因 umask 0022 为 755 亦可列，经授权非递归 `chmod 750`（`m4-stage6-chmod-20260921.txt`），27 侧复验 denied、发布目录不变；`input/yd` 基线包保持现场预置的 770 nwmuser（用户裁决，只登记）。
- 2026-09-22 阶段 7 cron：00:11Z 安装（`m4-stage7-cron-install-20260921.txt`、`crontab-readback-20260921.txt`），行形态按 §8.2/§15.2；锁文件位于本地 ext4（`findmnt /users` → `/dev/nvme1n1`）。tick 1（00:17Z）真跑 2026092112：作业 53091/53092 COMPLETED，发布 + `2026092200.cfg.ic` + DONE，work 清空，随后停在 2026092200 raw 缺口；tick 2（01:17Z）无 raw 无提交，仅追加一行缺口报告。注意：停在缺口的 tick 以 `错误：…raw 未齐…` 写入 `cron.log`（compute-loop §7 的 STOPPED 语义，退出码 3），这是正常等待，不是故障。锁竞争跳过与 #308 grace 未行使；作业内 `model/yd.cfg.para` 未在运行中直读，`START=0/END=7/DT_QR_DOWN=60` 以代码路径 + DAT 结构推导登记。
- 2026-09-22 阶段 8：M4 出口条件（§15.3、design §9.2、compute-loop §13.2）逐项映射见 `m4-stage5-rerun-20260921.md` 末节；receipt 只留在 node-22 `receipts/`，本仓以本节条目为索引，不入 Git。

## 16. M5 部署登记（node-27 主线 viewer）

### 16.1 勘察结论（2026-09-22，只读，`nwm@210.77.77.27`）

- docker 28.2.2 + compose v2.36.2；`nwm` ∈ `docker` 组；`sudo` 需密码 → Nginx 改动与 reload 由用户手工执行，agent 只准备命令并只读核验（与 §14.6 副本上线同法）；
- docker daemon 拉不到 Docker Hub（`registry-1.docker.io` 超时，`docker manifest inspect python:3.12-slim-bookworm` 失败），`ghcr.io`/deb/npm/pypi 可达，daemon 未配代理 → 现场不做 `docker build`；
- 端口占用：`8080` NWM display、`8081` 副本 display、`8086/8088/8089/8787/9090/13000/55432/55434` 已占；`8082–8085`、`18080` 空闲；
- Nginx 1.30.4；`/etc/nginx/conf.d/nwm.ac.cn.conf` 与 `test.nwm.ac.cn.conf` 各一处 `location /yd/ { proxy_pass http://127.0.0.1:8081/; … }`（副本，§14.6）；公网 `test.nwm.ac.cn/yd/` 200、`/yd/api/health` 404（副本无此端点）；
- `/home/ghdc/yd`（本机 ext4，NFS 服务端）：`output/`、`input/viewer` 755，文件 644 uid 1103；容器 uid 10001 靠 other 位读取，bind mount 直接挂目标目录，父目录 770 不影响；
- 磁盘：`/home` 余 1.1T，`/` 余 46G；副本工件位于 `/home/nwm/yd-*`，主线 viewer 不得混用；
- NWM 天地图形态：`https://t{0-7}.tianditu.gov.cn/DataServer?T=<layer>_w&x={x}&y={y}&l={z}&tk=<key>`，key 在 NWM 私有 display env（`NHMS_TIANDITU_KEY`）；六层为 vec/cva、img/cia、ter/cta；
- 本机（macOS arm64）docker 29.1.3 + buildx 可交叉构建 `linux/amd64`。

### 16.2 路径与配置（用户裁决 2026-09-22）

| 项 | 值 |
|---|---|
| `/yd/` 归属 | 主线 viewer 接管 `test.nwm.ac.cn/yd/`；副本继续持有 `nwm.ac.cn/yd/` |
| 镜像 | 本机 `docker buildx build --platform linux/amd64 -t yd-viewer:<git sha>`，`docker save \| gzip` → scp → node-27 `docker load`；登记 digest |
| 回环端口 | `127.0.0.1:8082`（`YD_VIEWER_PORT=8082`） |
| 部署目录 | `/home/nwm/yd-viewer/`：`compose.yml`、`.env`（0600 nwm:nwm）、`images/`、`receipts/` |
| 挂载 | `/home/ghdc/yd/input/viewer:/input:ro`、`/home/ghdc/yd/output:/output:ro` |
| 天地图 | 六个 `YD_BASEMAP_*_URL` 复用 NWM 现役 key（§9.2）；由 agent 在 node-27 上从 NWM 私有 env 复制进 yd `.env`，全程不回显 |
| Nginx | 仅改 `test.nwm.ac.cn.conf` 的 `/yd/` `proxy_pass` 8081→8082，用户手工：备份 `.bak.$TS` → `sudo nginx -t` → `sudo systemctl reload nginx` |
| 浏览器 receipt | API 项 curl（回环 + 公网）；页面项本机 headless Chrome 截图留档 + 用户人工逐项确认 |
| 回滚 | 首次部署无上一镜像：`docker compose down` + 恢复 Nginx 备份并 reload |

### 16.3 执行登记

receipt 目录 `/home/nwm/yd-viewer/receipts/`；每行一步，失败也登记。
- 2026-09-22 步骤 A/B（授权，15:33–15:34Z）：`/home/nwm/yd-viewer/{compose.yml,.env(600),images,receipts}`；天地图 key 取自 NWM 代码默认值（`apps/api/routes/basemap.py DEFAULT_TIANDITU_KEY`，NWM 私有 display env 未设 `NHMS_TIANDITU_KEY`），机器内拷贝不回显；镜像 `yd-viewer:e23b357`（本机 linux/amd64 构建，smoke 全绿）`docker load`；`docker compose up -d` → `yd-web` `127.0.0.1:8082`，uid 10001，两条 :ro 挂载；回环 health/cycles/map/reach/basemaps/geometry 全部通过，NWM `:8080` 前后 200。`receipts/m5-stepAB-20260922.txt`。
- 2026-09-22 步骤 C（用户手工 sudo，23:39 CST）：`test.nwm.ac.cn.conf` `/yd/` upstream 8081→8082（备份 `.bak.20260922T233935`，diff 一行，`nginx -t` 后 reload）；公网 `/yd/*` 200，`nwm.ac.cn.conf` 未动，NWM `/`、`/ops`、`/health`、副本 `nwm.ac.cn/yd/` 均 200。`receipts/m5-stepC-nginx-20260922.txt`。
- 2026-09-22 浏览器 receipt（本机 Playwright + 用户人工核对）：design §9.3 七项中地图着色/colorbar/单位、GFS 优先、双源 168 点曲线、历史起报只改曲线窗、NWM 不受影响均通过；卫星/地形底图通过，矢量层因天地图 key 限流（`该tk已限流`，key 与 NWM 生产共用）未通过——用户裁决接受只登记；IFS 回落与单源曲线未行使。截图 `receipts/shots/`。用户追加裁决：不做气象代站；页头加系统标题「永登流域水文模拟系统」（design §7，PR #324/#326/#327）。
- 2026-09-22 重部署（授权，22:55Z）：`yd-viewer:0ec9fbc`（含页头标题）`docker load`，`compose.yml` tag 切换（备份 `compose.yml.bak-e23b357`），容器重建；回环与公网 `<title>` 与 banner 三行确认，`latest_cycle` 2026092212；旧镜像 e23b357 保留供回滚。`receipts/m5-redeploy-0ec9fbc-20260922.txt`、汇总 `receipts/m5-node27-receipt-20260922.md`。M5 出口：agent-ops §11.3 字段中镜像 digest/compose project/回环端口、同一 NFS 身份（本机 ext4 服务端）、回环与公网 health、四个 API、168 点、GFS 优先、colorbar、历史起报、三底图（矢量受限流）、NWM 不受影响均有值；Nginx `-t`/reload 由用户执行并登记。
