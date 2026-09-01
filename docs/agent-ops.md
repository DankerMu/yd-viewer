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
- canonical、direct-grid forcing、state 和前端的最小代码按来源 commit 快照进本仓，之后独立维护；
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
- yd checkout 的远端实际路径在部署清单中记录，不在代码和本文猜测。操作前先确认路径与 commit。

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
- 控制器负责把模型与状态从 NFS 搬入 scratch，等待作业，验证后再搬回 NFS；
- 计算节点不能直接写 `YD_ROOT/output` 或 `YD_ROOT/states`。

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
- `prepare` 薄外壳只能用上述精确解释器调用已确认的 mapping-builder module；
- yd 不安装、不升级、不修复 NWM `.venv`。

此约束不意味着 yd 日常依赖 NWM 环境；它只约束一次性 builder 调用。

## 8. node-22 producer 操作

### 8.1 三个入口

实现完成后，所有操作只走本仓 CLI：

- `prepare --baseline <基线模型包路径>`：一次性从外部基线包生成 `yd_gfs`、`yd_ifs` 与两个 GeoJSON；基线包路径只在本次调用传入，不入 `config.toml`/`local.toml`（compute-loop §6.1）；
- `init`：只在全新根建立首态；已有任一状态或 `DONE` 时必须拒绝；
- `run`：日常循环，不自动 bootstrap。

在 CLI 尚未实现和通过本地测试前，禁止用手工 shell 拼出“等价生产流程”并声明完成。

`prepare` 和 `init` 都改变长期状态，必须有当前任务明确授权和现场 receipt；不得由 cron 自动调用。

### 8.2 cron 与 flock

- cron 只调用 `run`；
- 使用非阻塞 `flock -n`，已有实例时本 tick 跳过；
- 锁覆盖发现、Slurm 提交、等待、NFS 发布和清理的完整生命周期；
- 手工 `run` 使用同一把锁，不能绕开；
- 不同时启动第二个前台 controller；
- cron 最终分钟点由现场配置决定，未定前不写死。

### 8.3 Slurm

- forcing 与 SHUD 重任务都在 Slurm 作业内执行，不在登录节点直接计算；
- 同源最多一个 job，IFS/GFS 最多各一个；
- 只通过 yd CLI 提交，避免手拼 `sbatch` 参数；
- 观察可用 `squeue`/`sacct`，但不能修改 NWM job；
- 取消必须使用本次 yd receipt 中的精确 job ID；禁止 `scancel -u`、名称通配或模糊匹配；
- 不为未观察到的卡死编写 watchdog；walltime 属 Slurm 配置，异常由日志和人工操作处理；
- Slurm partition/account/CPU/内存/walltime 只放 `local.toml`。

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
- 天地图 URL/key 只走 env 或部署配置，不打印进 receipt，不复制 NWM 源码中的旧 key；
- 升级前记录当前镜像 digest和 compose 配置位置，保留上一镜像用于回滚。

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
| Slurm | 现有 job 名 | ~~job-name 加 yd 前缀~~ **未交付**：sbatch 模板未 patch，副本作业仍以 `nhms_*` 名入队，与 NWM 作业靠 JobID/提交账户区分（提交账户同为 frd_muziyao）。误 scancel 风险已知，是否补一行模板 patch 待裁决；同集群同分区 |
| 流域注册 | 33 basin | 副本 Basins 树只放 `yd/` 一个流域 |
| Nginx | `location / → :8080` | 仅新增 `location /yd/ { proxy_pass http://127.0.0.1:8081/; }`（剥前缀语义，§9.3） |

### 14.4 已登记代码 patch（fork 内登记维护）

1. `services/orchestrator/source_cycle_raw_manifest.py:38-39`：`NODE22_CANONICAL_NFS_RAW_AUTHORITY_ROOT`（现 `/ghdc/data/nwm/object-store`）与 manifest 前缀是代码字面量且 preflight 强制相等，副本改为 yd 数据面根（22 侧 fork commit `d65303cd`）；
2. `apps/frontend/src/App.tsx`：`BrowserRouter` 增加 `basename={import.meta.env.BASE_URL.replace(/\/+$/, "")}`（NWM 自身构建 BASE_URL=`/`，行为不变；27 侧 fork commit `537fc4a4`）；
3. `config/calibration_overrides.yaml` 置空为 `calibration_overrides: []`（上游含 hetianhe 条目，registry publisher 对 yd-only inventory fail-closed 拒发；配置文件而非代码，仍按 patch 登记；22 侧 fork commit `e75d2907`）——部署中发现，待用户追认，可否决回退。

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
- timers（enabled）：`yd-node27-download` 30 min、`yd-node27-autopipe` 10 min、`yd-compute-scheduler` 5 min。**retention/compression/governance/frontier-alert 类 timer 有意未装未启**——raw/object-store 无限增长，何时启用 yd 域 retention（env 已隔离锁与根）待裁决；
- 首轮全链（cycle 2026082712 双源）≈10–11 min/cycle，state index 已闭合（entry_count 4）；
- 已知偏差：Slurm job-name 仍 `nhms_*`（见 14.3）；`AUTOPIPE_MVT_PREWARM_ENABLED=0`（prewarm 会打 `:8080`，属只读越界，已关）。
