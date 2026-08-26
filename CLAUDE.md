# 水文预报系统（yd-viewer）— Agent 指南

yd 流域 SHUD 产物展示服务 + node-22 循环预报环；最终部署在**客户服务器（不可达）**，离线自足为设计前提。本文件只做目录路由，细则按需读引用文档，勿全量加载。

## 文档路由

| 要做什么 | 读哪份 |
|---|---|
| viewer 方案 / API / 前端 / 部署形态 / 里程碑 M1–M5 | [docs/design.md](docs/design.md) |
| 循环预报环（warm start / 调度 / 清理 / §9 验证行） | [docs/compute-loop-design.md](docs/compute-loop-design.md) |
| 产物目录与格式契约（生产者↔消费者 + 模型包条款） | [docs/products-contract.md](docs/products-contract.md) |
| 节点拓扑 / 硬约束 / 验证 oracle 路由 / 与 NWM 仓关系 / 纪律 | [docs/agent-ops.md](docs/agent-ops.md) |

冲突解决：实现与文档冲突时以文档为准，**先改 docs 并 push，再动码**。

## 常驻两条（每次都要遵守，故不下沉）

- Python 一律 `uv`，禁裸 `python`/`pip`；前端 `corepack pnpm`。
- 跨节点操作、部署、验证声明前**必读** [docs/agent-ops.md](docs/agent-ops.md)——硬约束违反即事故。
