# 水文预报系统（yd-viewer）

独立的 yd 流域 SHUD 预报与展示项目：node-22 从 NWM 共享 NFS 只读取得 raw，并在 yd scratch 完成 IFS/GFS 双源计算，通过 yd NFS 发布 SHUD 原生产物；node-27 运行无数据库 viewer，展示最新河网流量与河段双源过程线。

当前阶段目标：**node-22 真计算 → NFS → node-27 真展示**。客户侧 producer 迁移待运行环境明确后另行设计。

- 总体设计：[docs/design.md](docs/design.md)
- node-22 计算环：[docs/compute-loop-design.md](docs/compute-loop-design.md)
- producer/viewer 文件契约：[docs/products-contract.md](docs/products-contract.md)
- 节点登录、部署与验证纪律：[docs/agent-ops.md](docs/agent-ops.md)

状态：方案已定稿，尚未开始实现。里程碑见 [docs/design.md §10](docs/design.md#10-里程碑)。
