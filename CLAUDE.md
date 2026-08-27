# 水文预报系统（yd-viewer）— Agent 指南

yd 流域独立 SHUD 预报与展示项目。当前阶段闭环是 **node-22 真计算 → NFS → node-27 真展示**；最终客户侧迁移待客户运行环境明确后另行设计。本文件只做目录路由，细则按需读引用文档，勿全量加载。

## 文档路由

| 要做什么 | 读哪份 |
|---|---|
| viewer 需求 / API / 前端 / node-27 部署 / 里程碑 | [docs/design.md](docs/design.md) |
| node-22 producer（raw / direct-grid / warm start / Slurm / 发布 / 验证） | [docs/compute-loop-design.md](docs/compute-loop-design.md) |
| producer ↔ viewer 的 `YD_ROOT` 文件契约 | [docs/products-contract.md](docs/products-contract.md) |
| 22/27 登录、NFS/scratch 拓扑、远端 Git、环境、权限、部署、Nginx、receipt | [docs/agent-ops.md](docs/agent-ops.md) |

冲突解决：实现与文档冲突时以文档为准，**先改 docs 并 push，再动码**。

## 常驻两条

- Python 一律 `uv`，禁裸 `python`/`pip`；前端 `corepack pnpm`。
- 跨节点操作、部署、验证声明前**必读** [docs/agent-ops.md](docs/agent-ops.md)；硬约束违反即事故。
