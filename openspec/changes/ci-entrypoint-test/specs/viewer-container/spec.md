## MODIFIED Requirements

### Requirement: CI 前端 job
`.github/workflows/ci.yml` MUST 新增 `viewer-frontend` job，在 `viewer/frontend` 执行 `corepack pnpm install --frozen-lockfile`、`pnpm typecheck`、`pnpm test`、`pnpm build`；现有 `producer`、`openspec` job MUST 不变。`viewer-backend` job MUST 在 `pytest` 之后执行 `bash tests/test_entrypoint.sh`（working-directory `viewer`，runner 非 root），脚本失败 MUST 使该 job 失败。

#### Scenario: job 存在
- **WHEN** 解析 ci.yml
- **THEN** 存在名为 `viewer-frontend` 的 job 且四条命令按序出现

#### Scenario: entrypoint 契约回归使 CI 失败
- **WHEN** 某 PR 删除 `viewer/entrypoint.sh` 中的 `umask 002`，或把 `exec uvicorn` 改为直接调用
- **THEN** viewer-backend job 在 `bash tests/test_entrypoint.sh` 步骤失败
