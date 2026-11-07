## MODIFIED Requirements

### Requirement: 保留窗口与安全清理
清理 MUST 保留最新成功 cycle 往前 14 天的 `output` source 目录，窗口外目录与对应失败日志删除；每个删除目标（含成功轮 work 删除）MUST 先经 `realpath` 确认位于 yd 自己的根内，否则拒绝删除。node-22 本地 `cron.lock_path` 及其专属 `run/` 目录 MUST 位于全部 retention、work、staging 与 residue 清理根之外，并是所有清理的显式禁区；任何删除候选中的 symlink 都不得被跟随到该哨兵。 执行时机：`run_sources` 的每个 source worker 在本 tick 追赶循环正常结束后执行一次（无论最后结果是成功还是 STOPPED；循环以 `RunError` 结束或 startup hygiene 停源时跳过），锚点只取本 source 自己最新的普通文件 `DONE`，MUST NOT 借用兄弟源或墙钟；删除 `output/<T>/<source>` 后若 `output/<T>` 已空则 no-follow `rmdir`，非空或不存在静默保留；全程 MUST 持有双源共用的发布锁。有删除时以 `retention cleanup:` 前缀把计数与逐条绝对路径追加到该源本 tick 最后一条报告的 detail；失败 MUST 转为该源 `RunError(phase="cleanup")`（正文含 `retention`，保留 cause），不改本 tick 已有报告的 outcome，不影响兄弟源。

#### Scenario: 14 天窗口
- **WHEN** 模拟根含最新成功 cycle 与一个 15 天前的 source 目录
- **THEN** 窗口外目录被删除，窗口内完整保留

#### Scenario: symlink 越界拒删
- **WHEN** 待清理路径是指向 yd 根之外的 symlink
- **THEN** 清理拒绝删除该目标并报告

#### Scenario: 两源锚点各自计算
- **WHEN** gfs 最新 DONE 为 D、ifs 最新 DONE 为 D−17d，两源在 D−20d 都有 output 目录
- **THEN** 本 tick 后 gfs 的 D−20d 目录被删除，ifs 的 D−20d 目录保留

#### Scenario: 空 cycle 目录随最后一个 source 删除
- **WHEN** 某 T 早于两源各自的 cutoff
- **THEN** 两源目录删除后 `output/<T>` 不存在；若只有一源早于其 cutoff，`output/<T>` 保留另一源的目录

#### Scenario: retention 失败不回滚已发布轮次
- **WHEN** 本 tick 某源已 SUCCEEDED，随后 retention 执行失败
- **THEN** 该源 DONE 与 SUCCEEDED 报告保留，run 以该源 `cleanup` 阶段错误结束，兄弟源结果不变
