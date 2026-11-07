## Why

规格要求 MUST 保留最新成功 cycle 往前 14 天，窗口外的 output source 目录和对应失败日志删除（run-controller「保留窗口与安全清理」、compute-loop §12、products-contract §7.2）。`cleanup.plan_retention` / `execute_retention_plan`（#25）已经实现并有测试，但 `producer/src` 里没有任何调用方，所以 `run` 从来没清理过。2026-11-07 现场 node-27 `/home/ghdc/yd/output` 有 106 个 cycle（2026091500 → 2026110612），共 912M，每天增长约 21M。docs 已先行（PR #377：compute-loop §12 写明执行时机；PR #378：§6 规定 `RunSourcesError` 时先输出它携带的结果行，并修正 §12 的触发措辞）。Closes #372。

**硬约束：简化项目，不过度设计。** 只接线既有 seam，外加删空 `output/<T>/` 这一步。

```text
Issue type: feature
Fixture level: expanded
Upstream suggested level: absent (expanded: 首次上线不可逆地删除持久 NFS 产物，涉及 file IO、persisted state、并发)
Blast radius: 改错 → 删掉窗口内产物，或用兄弟源的锚误删本源产物，或与兄弟源发布竞态删掉正在发布的 output/<T>，或清理失败把已发布的轮次报成失败、甚至回滚
Selected risk packs: File IO / path safety / overwrite; Error handling / partial outputs; Concurrency / shared state; Legacy compatibility; Public API / CLI; Documentation
Evidence floor: producer ruff + format + pytest 全绿；两源锚点不同、窗口边界、空 cycle 目录 rmdir、兄弟源占用不删、发布锁持有、失败映射、报告行、每 tick 幂等这些测试红→绿；large_file_guard 通过
```

## What Changes

- `run_sources` 的每个 source worker 只要正常返回报告就执行（包括追赶循环结束、startup hygiene 停源；worker 以 `RunError` 结束则跳过），持有双源共用的 `publish_lock`，依次执行 `plan_retention(yd_root, source)` → `execute_retention_plan(plan)`，然后对每个被删的 `output/<T>/<source>` 尝试 `rmdir output/<T>/`。
- 有删除时，把 `retention cleanup:` 前缀的审计文本（output 目录数、失败日志数、逐条绝对路径）追加在该源本 tick 最后一条报告的 detail 末尾。
- plan / execute / rmdir 失败时，转为该源的 `RunError(phase="cleanup")`，正文以 `retention` 开头，原异常作为 cause。worker 带着已有报告返回这个错误，由 `RunSourcesError` 聚合。
- `cli` 的 `RunSourcesError` 分支在输出聚合错误块之前，先按既有结果行规则输出 `exc.reports`（compute-loop §6）。

不做：
- viewer 侧清理；
- 修改 14 天窗口值或把它放进配置；
- 修改 `cleanup.py` 中的 plan/execute 语义；
- 修改 RunReport 公开字段或 RunOutcome / StopReason 词表；
- 首次上线的一次性迁移脚本（首个 tick 就是迁移）。

## Capabilities

### Modified Capabilities
- `run-controller`：保留窗口与安全清理——规定执行时机、锚点、并发和报告。
- `cli-config`：run 的 stderr 行格式——`RunSourcesError` 时先输出它携带的结果行。

## Impact

- 代码：`producer/src/yd_producer/_controller_sources.py`（684 行），必要时加一个小的新私有模块；`cli.py` 的 `RunSourcesError` 分支；`cleanup.py`（997 行）不加行。
- 测试：见 tasks.md。
- 现场：首次部署时会一次性删除约 39 天、约 75 个 cycle 的 output 目录。部署需要用户明确授权，并留 node-22 receipt（删除前清单、删除后清单）和 node-27 `/api/cycles` 的回执。
