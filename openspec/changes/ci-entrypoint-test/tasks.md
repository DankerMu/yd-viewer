## 0. Risk packs

- Config / project setup — 选中：CI 配置 → 1.1、2.x。
- Release / packaging / dependency compatibility — 选中：脚本内 `uv run --offline --no-python-downloads --python 3.12` 须在 `astral-sh/setup-uv@v7`（python-version 3.12）下找到解释器 → 1.1 兜底、2.2 实跑确认。
- Auth / permissions — 选中：脚本以 root 运行会直接 `exit 1`（`test_entrypoint.sh:7-10`），写失败用例（`chmod a-w`）也只在非 root 下成立；由 GitHub runner 的非 root 用户满足 → 2.2 绿灯为证。
- File IO、Public API / Schema、Concurrency、Error handling — 不选：不改运行时代码与测试脚本本身。

## 1. CI

- [x] 1.1 `.github/workflows/ci.yml` viewer-backend job：在 `- run: uv run pytest` 之后加 `- run: bash tests/test_entrypoint.sh`；如 runner 上脚本内 `uv run --python 3.12 --no-python-downloads` 找不到解释器，只在该步加最小环境设置（如 `UV_PYTHON`），不改脚本

## 2. 验证（输入 → 预期）

- [x] 2.1 本地：`cd viewer && bash tests/test_entrypoint.sh`（非 root）→ `test_entrypoint.sh: all cases passed`，exit 0
- [x] 2.2 本 PR 的 CI：viewer-backend job 日志中新步骤执行并输出 `all cases passed`，job 绿（job 107945174641）
- [x] 2.3 变异（临时 draft PR，基于本分支，验证后关闭并删分支；两个变异各自单独一次 CI run，第二个变异前先恢复 umask，不叠加）：
  - 删除 `viewer/entrypoint.sh` 的 `umask 002` 行 → viewer-backend 在新步骤失败，日志含 `umask='0022', expected '0002'`（`test_entrypoint.sh:97-98`）；
  - 恢复后把 `exec uvicorn` 改为 `uvicorn` → 新步骤失败，日志含 `PID mismatch`（`:91-94`）；
  - 两次 run 中 `uv sync`、ruff、pytest 步骤均为绿（`viewer/tests/*.py` 不引用 entrypoint），失败只在新步骤，排除环境原因误判；
  - 本 PR 描述记录两次 run 链接与失败行（job 107945118705、107945136106）
- [x] 2.4 `openspec validate ci-entrypoint-test --strict --no-interactive` → valid
