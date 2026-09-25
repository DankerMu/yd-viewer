## Why

`viewer/tests/test_entrypoint.sh` 守护 entrypoint 的四条运行时契约：`umask 002`、URL/key 不进日志、`exec` 不 fork、写盘失败不启动。但 CI 的 viewer-backend job 从不运行它，删掉 `umask 002` 的 PR 也能全绿合入，后果到 node-27 与 NWM 共享的瓦片缓存上才暴露。docs 已先行（design §9.1，PR #360）。Closes #358。

**硬约束：简化项目，不过度设计。** 只在已有 job 里加一步。

```text
Issue type: test
Fixture level: compact
Upstream suggested level: absent (agree: 只改 CI 配置，不改运行时代码)
Blast radius: 步骤写错 → viewer-backend 常红阻塞所有 PR，或静默不跑失去门控
Selected risk packs: Config / project setup; Release / packaging / dependency compatibility; Auth / permissions
Evidence floor: 本 PR CI 绿（新步骤实际执行并通过）；变异 PR（删 umask 002、exec→直接调用）CI 在新步骤变红
```

## What Changes

- `.github/workflows/ci.yml` 的 `viewer-backend` job 在 `uv run pytest` 之后增加 `bash tests/test_entrypoint.sh`（working-directory `viewer`）。
- 不改 `viewer/entrypoint.sh`、测试脚本本身或其它 job。
- 不采用 issue 备选的 `viewer/tests/test_container_contract.py` 静态断言（shell 测试已守行为，静态断言只守行序，重复）；issue 验收第 4 条因此不适用。

design.md 省略：compact 级别，无设计取舍。

## Capabilities

### Modified Capabilities
- `viewer-container`：CI 要求增加 entrypoint shell 测试步骤。

## Impact

- `.github/workflows/ci.yml`。
- Must preserve：viewer-backend 既有四步（`uv sync --frozen`、ruff check、ruff format --check、pytest）及顺序；producer、viewer-frontend、openspec、stage-pipeline-log job 不变。
