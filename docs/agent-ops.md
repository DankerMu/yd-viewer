# Agent 操作细则（拓扑 / 硬约束 / oracle 路由 / 纪律）

由 [CLAUDE.md](../CLAUDE.md) 路由进来；涉及部署、跨节点操作、验证声明前必读。

## 与 NWM 仓的关系

- 独立仓库，**零共享代码**；前端不移植 m11，按需新写。
- forcing 链搭 NWM 仓 DB-free CLI 便车（`nhms-{ifs,gfs} download → nhms-canonical convert → nhms-forcing produce`），symlink 复用 node-22 上 NWM 的 raw/canonical，不动 NWM 任何数据与配置。
- node-27 上为**旁路 staging**：`test.nwm.ac.cn/yd`，独立端口 + nginx 一条 `location /yd/`；**绝不影响 NWM 现有服务**——不动 `/`、`/ops`、PG、ingest，nginx 只 `reload` 不 `restart`。

## 节点拓扑

| 端 | 地址 | 角色 |
|---|---|---|
| 本地 Mac | `/Users/danker/Desktop/Hydro-SHUD/yd-viewer` | 编辑、commit、push、ruff、pytest（合成 fixture）、tsc/pnpm build |
| node-22 | `ssh -p 32099 frd_muziyao@210.77.77.22` | 循环预报环（cron+flock+sbatch），产物写 NFS `/ghdc/data/yd/output/` |
| node-27 | `ssh -p 32099 nwm@210.77.77.27` | 同款镜像 staging，读 NFS `/home/ghdc/yd`，live receipt oracle |
| 客户服务器 | **不可达** | 最终生产；`docker load` + compose，产物目录只读挂载 |

22 的 `/ghdc/data/yd` 与 27 的 `/home/ghdc/yd` 是同一份 NFS。

## 硬约束（违反即事故）

- **SHUD 输出是二进制**（`BINARY_OUTPUT=1`），格式权威是 rSHUD `readout()`（LE float64，v1/v2 双版本头，尾部可能残行）；大文件必须 **memmap 按列抽取**，禁止整文件进内存。
- **列数守卫**：`yd.rivqdown.dat` 列数必须等于 `river.shp` reach 数（当前 3988），不一致返回 409 拒绝展示。
- **warm start 绝不冷启动**：快照 `valid_time == 下轮 T`；精确命中 → 降级取最新 ≤T（标 degraded）→ >7 天陈旧停更 + status.json 告警。bootstrap 状态仅首启使用。
- **时间基准全 UTC**（cycle 命名、forcing、`.dat` 内部），仅展示层转北京时间；12Z 轮的 SHUD `START` 锚定按 compute-loop-design §4 的规则式（实测钉死前不得假定常量）。
- **`DONE` 最后写**：无 `DONE` 的 cycle 目录 viewer 视为进行中，不展示。
- **天地图 key**：NWM 旧 key 已泄漏，本仓一律用新申请、域名绑定的 key，走配置项，**禁止 hardcode 进代码**。
- **node-22 纪律**（继承 NWM 仓约束）：调用 NWM CLI 须用精确解释器 `/scratch/frd_muziyao/NWM/.venv/bin/python -m ...`，维护窗口（NWM #1831）前禁 `uv sync` / 裸 `uv run`；本机 `:55433` 归档库**不要连**；共享 stash 栈，禁裸 `git stash`/`pop`。

## 验证 oracle 路由

| 改了什么 | 在哪验 |
|---|---|
| .dat 解析、窗口/DONE 门控、几何转换、API 分支 | 本地 pytest（合成二进制 fixture + 真实 shapefile） |
| 前端 | 本地 tsc + pnpm build；交互走 27 实机浏览器 |
| forcing 接线、两段跑接力、12Z 锚定、QC/降级/幂等 | **node-22 实跑**（compute-loop-design §9 逐行） |
| 端到端 | **node-27 live receipt**：`/yd/` 加载几何、时次列表、点击出曲线 |
| 客户侧 | 不可达——验收标准是离线 bundle 在 27 上从零 `docker load` 起得来 |

## 开发环境

- Python 一律 `uv`（`uv run`、`uv sync`），禁裸 `python`/`pip`；前端 `corepack pnpm`。
- 构建与请求路径一律**相对**（base `./`，fetch `api/...`）——根路径与 `/yd/` 子路径共用同一构建产物。

## 完工纪律

- 方案先行：设计/契约变更先落 docs 并 push，再动实现。
- 每个里程碑对应 design.md §8 验证表与 compute-loop-design §9 验证行，**跑过再声明完成**；27/22 侧验证须附实机 receipt，不得用本地绿冒充。
