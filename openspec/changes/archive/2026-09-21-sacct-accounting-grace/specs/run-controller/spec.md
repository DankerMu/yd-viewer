## MODIFIED Requirements

### Requirement: 作业提交经执行器抽象且身份可追溯
run MUST 经作业执行器抽象为每源提交至多一个作业；提交参数（partition、account、CPU、内存、walltime）MUST 全部取自 `local.toml`，代码 MUST NOT 为这些资源内置任何默认值；每次提交的 job ID、partition、终态与起止时间 MUST 记入本次运行报告，失败源的日志 MUST 含同一 job ID。真实 `sbatch`/`sacct` 行为归 M4 oracle，本地以注入 fake 验证。

每一次真实 `sbatch`、普通轮询 `sacct` 与失败 ExitCode `sacct` 客户端子进程 MUST 设置同一个正整数秒数的调用时限，取自 `LocalConfig.slurm_command_timeout_seconds`；其唯一缺省为配置装载器的版本化 60 秒。该值不得进入 `JobSpec.resources` 或 `sbatch` argv，也不是 Slurm job walltime、job watchdog 或取消策略。客户端超时 MUST 经既有异常漏斗转成 `ExecutorError`，不自动重试。

客户端 timeout 只证明 submit/query 调用没有及时返回，MUST NOT 伪造 `JobState.TIMEOUT`。若发生在 submit，controller 产生保留该 `ExecutorError` 为 cause 的 `RunError(phase="submit", job_id=None)`；若发生在普通 poll，产生 `RunError(phase="poll", job_id=<已知 job>)`。两者都必须保留 exact work、零 ExitCode provider/finalizer/collect/publish/DONE。若调度器已明确返回 terminal `FAILED/TIMEOUT`，但随后 ExitCode `sacct` 客户端 timeout，则产生绑定同一 job ID 的 `RunError(phase="cleanup")`，保留 work 与已在 scratch 的 job log，零失败日志提交/删除。三者都终止本源 worker并经 `RunSourcesError` 聚合，兄弟 source 继续到自己的结局，且均不自动重试。由于 `sbatch` timeout 可能发生在服务端已接收之后，下一 tick 仍由无 DONE work 的人工闸保护，不得自动删除重提。

`sbatch` 成功后 Slurm accounting 有数秒滞后，此窗口内普通轮询 `sacct -j <id> -X` 返回 0 行。执行器 MUST 只在注入时钟的当前时刻距该作业 `submitted_at` 不超过 120 s（模块常量，不进 `Config`/`LocalConfig`、`JobSpec.resources` 或任何 argv）时把「0 个非空行」视为 accounting 滞后：返回该作业已有记录，状态保持 `PENDING`，`started_at`/`ended_at` 不变，不替换已落库记录，由既有 poll 间隔继续重查。超过 120 s 仍 0 行、或任何时刻出现多行，MUST 照旧经 `parse_sacct_record` fail closed 为同 job ID 的 `ExecutorError`，进而由 controller 产生 `RunError(phase="poll")` 停源保留 work。放宽只针对行数为 0，MUST NOT 放宽未知状态串、空字段或 JobID 串台的拒绝，也 MUST NOT 伪造任何状态或时间。

#### Scenario: job 身份进入运行报告
- **WHEN** fake executor 返回 job ID 与终态，完成一轮双源 run
- **THEN** 运行报告含两源各自的 job ID、partition、终态与起止时间

#### Scenario: 缺 Slurm 现场字段即停
- **WHEN** `local.toml` 缺少 partition 字段，或版本化的 `slurm.required_fields` 未声明 partition
- **THEN** 在发现、残留清理、work 创建和提交之前报错退出，无作业提交、无文件系统变更

#### Scenario: 单源单轮报告绑定提交记录
- **WHEN** 单源单轮 fake 作业从提交推进到成功终态并完成发布
- **THEN** 运行报告中的 job ID、partition、终态、submitted/started/ended 时间逐项来自同一次提交及其终态记录，且该 source/cycle 恰有一次 executor submission

#### Scenario: 作业成功终态之后才接收产物
- **WHEN** fake executor 尚未返回成功终态，或返回的产物不属于同一 source/cycle/work/job
- **THEN** run 不接收 DAT、日志或 checkpoint，不发布、不写 `DONE`；提交前预埋的规范文件名不能冒充本次作业产物

#### Scenario: 漏采补跑不增加提交
- **WHEN** 同一 fake 作业的主跑跳过 T+12 捕获，并在作业内用相同初态与 forcing 完成确定性 12 小时补跑
- **THEN** controller 收到同一 attempt-local checkpoint authority 并正常发布，且该 source/cycle 的 executor submission count 仍精确为 1

#### Scenario: 每源至多一个作业
- **WHEN** 一次 run 中某源有多轮 raw 可追赶
- **THEN** 任意时刻该源在 executor 上的在途提交计数不超过 1（逐轮串行）

#### Scenario: sbatch 客户端超时保留未知提交证据
- **WHEN** IFS 的 `sbatch` 子进程达到配置的 `command_timeout_seconds` 而抛 `TimeoutExpired`，GFS 可正常追赶
- **THEN** IFS 产生 `RunError(phase="submit", job_id=None)`，其 cause 链含 `ExecutorError`/原 `TimeoutExpired`；IFS exact work 保留且零 ExitCode/失败 finalizer/collect/publish/DONE，GFS 不被取消；不得假定服务端未接收作业或自动重提

#### Scenario: sacct 客户端超时不伪造作业 TIMEOUT
- **WHEN** IFS 已取得 job ID 后，普通轮询 `sacct` 达到同一命令时限，GFS 可正常追赶
- **THEN** IFS 产生绑定同一 job ID 的 `RunError(phase="poll")`，work 保留、零 ExitCode provider/finalizer/collect/publish/DONE；不得构造 `JobState.TIMEOUT`，GFS 继续到自己的结局

#### Scenario: 失败退出码查询超时不猜测后删除
- **WHEN** IFS 已明确得到 terminal `FAILED` 或 `TIMEOUT`，但独立 ExitCode `sacct` 达到客户端命令时限
- **THEN** IFS 产生绑定同一 job ID 的 `RunError(phase="cleanup")`，保留 exact work 与 scratch job log，零正式失败日志提交和 work 删除；不得猜退出码或重试查询，GFS 继续到自己的结局

#### Scenario: 三条 Slurm 命令共享一个客户端时限
- **WHEN** 生产装配以显式非默认 `command_timeout_seconds` 分别执行 sbatch、普通 sacct 与失败 ExitCode sacct
- **THEN** 三次底层 subprocess 调用的 `timeout` 均逐字等于该配置值，且 `JobSpec.resources`/sbatch argv 中不含 `command_timeout_seconds`

#### Scenario: 提交后 120 s 内 sacct 0 行视为 accounting 滞后
- **WHEN** 作业已提交，注入 runner 的首两次普通轮询 `sacct` 返回空 stdout，第三次返回 `<id>|RUNNING|<start>|Unknown`，且三次轮询的注入时钟均在 `submitted_at + 120 s` 之内（含恰好 120 s）
- **THEN** 前两次 `poll` 返回状态仍为 `PENDING`、`started_at`/`ended_at` 为 `None` 的既有记录，执行器内部记录未被替换；第三次返回 `RUNNING` 与解析出的 `started_at`；全过程无 `ExecutorError`，sacct argv 与 sbatch argv 不变

#### Scenario: 超过宽限或多行仍 fail closed
- **WHEN** 注入时钟使空 stdout 的轮询发生在 `submitted_at + 121 s`，或在窗口内任一次轮询返回 2 个非空行
- **THEN** `poll` 抛出绑定同一 job ID 的 `ExecutorError`，措辞与 `parse_sacct_record` 既有的「期望恰好 1 行记录，实际 N 行」一致，不伪造状态或时间；`parse_sacct_record` 对 0 行的直接调用行为与措辞不变
