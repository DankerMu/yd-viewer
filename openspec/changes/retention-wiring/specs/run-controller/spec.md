## MODIFIED Requirements

### Requirement: 保留窗口与安全清理
清理 MUST 保留最新成功 cycle 往前 14 天的 `output` source 目录，窗口外目录与对应失败日志删除；每个删除目标（含成功轮 work 删除）MUST 先经 `realpath` 确认位于 yd 自己的根内，否则拒绝删除。node-22 本地 `cron.lock_path` 及其专属 `run/` 目录 MUST 位于全部 retention、work、staging 与 residue 清理根之外，并是所有清理的显式禁区；任何删除候选中的 symlink 都不得被跟随到该哨兵。 执行时机：`run_sources` 的每个 source worker 每次以报告正常返回时执行一次（追赶循环结束或 startup hygiene 停源；无论最后结果是成功还是 STOPPED；worker 以 `RunError` 结束时跳过），锚点只取本 source 自己最新的普通文件 `DONE`，MUST NOT 借用兄弟源或墙钟；删除之后对 `output/` 下所有早于本源 cutoff 的 `output/<T>` 逐个 no-follow `rmdir`，只删空目录，非空或不存在静默保留，遇到其它错误时其余候选照试；全程 MUST 持有双源共用的发布锁。有删除时以 `retention cleanup:` 前缀把计数与逐条绝对路径追加到该源本 tick 最后一条报告的 detail；失败 MUST 转为该源 `RunError(phase="cleanup")`（正文含 `retention`，保留 cause；execute 已完成而 rmdir 失败时，已删审计作为该错误的 note），不改本 tick 已有报告的 outcome，不影响兄弟源。

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

#### Scenario: 遗留空 cycle 目录下一 tick 收掉
- **WHEN** 早于本源 cutoff 的 `output/<T>` 已为空（上次中断或 rmdir 失败遗留）
- **THEN** 本 tick 它被 rmdir 并计入审计

#### Scenario: rmdir 部分失败保留审计
- **WHEN** execute 已完成，对某个空 `output/<T>` 的 rmdir 返回 EIO
- **THEN** 其余候选照试；该源的 cleanup 错误带一条 note，内容为已删审计；下一 tick 收掉该目录

#### Scenario: retention 失败不回滚已发布轮次
- **WHEN** 本 tick 某源已 SUCCEEDED，随后 retention 执行失败
- **THEN** 该源 DONE 与 SUCCEEDED 报告保留，run 以该源 `cleanup` 阶段错误结束，兄弟源结果不变

### Requirement: 双源独立追赶组合公共契约
公开入口 MUST 精确为 `run_sources(*, config: Config, local: LocalConfig, executors: Mapping[str, JobExecutor], drivers: Mapping[str, AttemptDriver], poll_waits: Mapping[str, Callable[[], None]], failure_exit_codes: Mapping[str, Callable[[JobRecord], str]]) -> RunSourcesReport`，全部参数 keyword-only 且无默认值。它 MUST 用两个固定 source worker 让 IFS/GFS 各自独立逐轮调用带组合选项的私有 `run_once`；只有 `SUCCEEDED` 才在同一 worker 内开始下一轮，`STOPPED`、`JOB_FAILED`、`SUCCEEDED_CLEANUP_PENDING` 都作为该源有序报告序列的末项。每轮 MUST 从已落盘 `DONE`/state 重新发现严格前沿，MUST NOT 缓存或自增 T、预扫更晚 raw、冻结调用开始时的 raw horizon，或并行提交同源多轮。#27 的公开 `catch_up_source` 签名与实现结构 MUST 保持不变；#28 只复用其“仅成功继续”规则，不重写其公共合同。

`executors`、`drivers`、`poll_waits`、`failure_exit_codes` 的键集 MUST 各自恰为 `{ifs,gfs}`，映射 MUST 在启动 worker 前快照；两源 MUST 使用不同的 executor 与 driver 实例。映射或实例不合法时 MUST 在任何发现、文件系统变更或提交前拒绝。两个 worker MUST 都启动并全部结束后才汇总结论；一个源停止或抛出 `RunError` 时 MUST NOT 取消、截断或阻塞另一个源继续追赶到自己的首次非成功结局。

两源都正常返回时，`run_sources` MUST 返回 frozen、keyword-only 的 `RunSourcesReport(ifs: tuple[RunReport, ...], gfs: tuple[RunReport, ...])`。两个 tuple 都至少一项，按该源轮次顺序排列，所有非末项 MUST 为 `SUCCEEDED`，末项 MUST 为首次非 `SUCCEEDED`；每项 `source` 必须与字段一致。任一 worker 抛出 `RunError` 时，MUST 在两源都结束后抛 `RunSourcesError(RuntimeError)`；其 `reports: Mapping[str, tuple[RunReport, ...]]` 是构造时取得、精确含 `{ifs,gfs}` 的不可变快照，tuple 可为空；其 `errors: Mapping[str, RunError]` 是构造时取得的非空、不可变 source 子集。同一 source MAY 同时在 `reports` 中有此前成功轮并在 `errors` 中有最终异常；`errors` 中源的 `reports` 非末项 MUST 为 `SUCCEEDED`，末项 MAY 为首次非 `SUCCEEDED`（该源追赶已正常结束、随后的 retention 失败时，见「保留窗口与安全清理」），其余形态 MUST 在构造时拒绝；错误文本 MUST 按 `ifs`、`gfs` 固定顺序列出。组合层 MUST NOT 丢弃异常前已完成的报告或兄弟源的完整报告序列。

`FAILED`/`TIMEOUT` 的自动失败收尾只属于 `run_sources` 路径：每源失败退出码 provider 是调用方 MUST 注入项，`run_sources` MUST 只调用本源 `failure_exit_codes[source]`，并把同一 terminal `JobRecord` 交给 provider。生产 provider MUST 对该 job ID 恰执行一次 `sacct -j <job_id> -X -n -P --format=ExitCode`，取得 nonblank 退出码字符串；轮询通道 MUST NOT 取 `ExitCode`，`JobRecord` 七字段不变。provider 返回原值 MUST 作为 `FailureInputs.exit_code` 传给 `finalize_failed_job`。provider 或失败收尾的普通异常 MUST 变为同 source/cycle/job ID 的 `RunError(phase="cleanup")`，但不得取消兄弟 source；该源此前的成功报告仍保留。直接调用既有六参数 `run_once` 时 MUST 保持原行为：返回 `JOB_FAILED`，不取得退出码、不调用失败收尾并保留 work。

对 raw 完整的合法 T，controller MUST 在任何 staging 写入前通过 no-follow 父目录排他创建精确 `work/<source>/<T>`，并冻结该目录的 `(st_dev, st_ino)` 作为本 attempt 的 ownership token。竞争者先创建任何形态时 MUST 零 staging、零提交、保留现有条目并以本源 `RunError(phase="raw")` 失败；普通的 check-then-create 不构成认领。共享 `work/` 与 `work/<source>/` 祖先 MUST 在 exact root 认领前由不参与 raw rollback 的 no-follow 创建负责；raw staging 的 rollback MUST NOT 删除兄弟 source 创建的共享祖先。

controller 路径的 scratch 读取、失败收尾与成功发布 MUST 消费并重验同一个 token，不得从后来可能重绑的 pathname、父 symlink 或 `realpath` 重新推导 ownership。`DONE` 前 identity 漂移 MUST 保留当前条目、不写 `DONE` 并产生对应 raw/collect/publish `RunError`；失败日志已提交后、work 删除前漂移 MUST 保留日志与 replacement 并成为 `RunError(phase="cleanup")`；`DONE` 已写后漂移 MUST 保留 replacement 并返回 `SUCCEEDED_CLEANUP_PENDING`。删除操作 MUST 在打开 named root 后和最终移除 root 前校验 expected identity，不能只在函数入口比较一次。standalone `rawcopy.stage_raw`、`PublishInputs`/`publish` 与 `FailureInputs`/`finalize_failed_job` 的既有调用形态 MUST 保持兼容；新增 claim 输入保持默认 `None`，controller 路径必须传入非空 token。#109 只允许 `PublishInputs` 在既有字段后追加默认 60 的 `output_interval_minutes` 以兼容旧构造；生产 controller MUST 显式传 `config.output_interval_minutes`。

raw staging 失败时 MUST 保持 rawcopy 既有“不留半套”和本控制器“下次从干净 work 重试”语义：controller 成功取得 token 后，`stage_raw` 在零写入 admission、写期 rollback、普通异常或 `BaseException` 的任一出口，都 MUST 执行同一 identity-bound exact-root release；本轮后代已完整 rollback 或尚未写入，且 exact root 仍匹配 token、确认为空时，只删除该 exact root，MUST NOT 递归删除或删除 source/shared ancestor。若 root 非空、漂移或无法确定，MUST 保留当前 entry，并在原 `RunError(phase="raw")` 中携带 cleanup 失败证据；原异常类型、kind、cause 与 `BaseException` 传播 MUST 保持。若排他 mkdir 后在 token 冻结/返回前失败，则无 claim 可授权删除，MUST fail closed 保留该 pre-token 条目，MUST NOT 仅凭 pathname 推导 ownership。ownership helper 打开的每个 directory/file fd MUST 在所有正常、`Exception` 与 `BaseException` 路径中恰当关闭；成功返回给 caller 的文件 fd 只由 caller 关闭。

#### Scenario: 双源输入在启动前完整校验
- **WHEN** 四份 mapping 任一缺源、多源、值类型非法，或 IFS/GFS 共用同一 executor 或 driver 实例
- **THEN** `run_sources` 在启动 worker 前拒绝，两个 source 的发现、work 与作业提交均为零

#### Scenario: 双源首轮并行且后续同源串行
- **WHEN** IFS/GFS 的首轮 fake 作业在首次 poll 前互相等待对方已提交，随后每源各有多轮 raw 可追赶
- **THEN** 两个首轮作业曾同时在途；各源只使用其对应 executor、driver 与 poll wait，后续轮只在本源上一轮结束后提交，任意时刻每源在途作业不超过一个

#### Scenario: 调用方改写不改变已启动 tick 的映射快照
- **WHEN** 四份原始可变 mapping 已通过预检且两个 source worker 已启动，调用方随后把其中的 executor、driver、poll wait 与退出码 provider 全部替换为串源哨兵
- **THEN** 当前 tick 的全部轮次仍只使用调用开始时快照的对象，两源 job、provider、报告与文件产物均不串线；哨兵零调用

#### Scenario: 失败源停止而成功源继续多轮追赶
- **WHEN** IFS 首轮返回失败终态，GFS 的 T、T+12h、T+24h 连续完整且 T+36h 不完整
- **THEN** IFS 以 `JOB_FAILED(T)` 作为唯一报告，GFS 报告依次为三个 `SUCCEEDED` 后 `STOPPED/RAW_INCOMPLETE(T+36h)`，GFS 三次发布均完成

#### Scenario: 成功源不等待失败收尾
- **WHEN** IFS 已返回失败终态但其失败日志/work 收尾被同步事件阻塞，而 GFS 已成功并请求发布且还有后续完整轮
- **THEN** GFS 的 publish、`DONE` 与后续轮推进可在解除 IFS 收尾阻塞前完成；解除后 IFS 才完成唯一日志提交与 work 删除

#### Scenario: 两源可有不同追赶长度
- **WHEN** IFS 在一轮成功后遇 raw 缺口，GFS 在三轮成功后才遇 raw 缺口
- **THEN** 两个有序报告 tuple 分别保留各自长度和首次缺口，短源结束不取消或限制长源

#### Scenario: 首错不取消兄弟并聚合部分证据
- **WHEN** IFS 成功若干轮后在下一轮抛出 `RunError`，GFS 随后继续完成自己的追赶
- **THEN** `run_sources` 等 GFS 结束后才抛 `RunSourcesError`；`reports["ifs"]` 保留异常前的全部成功报告，`errors["ifs"]` 保留原错误，`reports["gfs"]` 保留完整有序报告与已落盘 `DONE`

#### Scenario: 失败退出码绑定同一 terminal record
- **WHEN** IFS 返回 `FAILED` 且其 provider 对该 terminal job ID 执行退出码查询，GFS 成功并继续追赶
- **THEN** provider 只调用一次，查询 argv 逐元素为 `sacct -j <job_id> -X -n -P --format=ExitCode`，所得字符串作为同一轮 `FailureInputs.exit_code`；IFS 唯一失败日志逐字含该 job ID 与退出码，IFS work 在日志提交后删除；GFS provider 不调用且 GFS 正常发布后续轮

#### Scenario: ExitCode 查询排除 job-step 记录
- **WHEN** 同一失败作业有 allocation 记录（ExitCode=`42:7`）及 batch/extern step 记录（ExitCode=`0:0`），本源 provider 查询该 terminal job
- **THEN** 精确 argv `sacct -j <job_id> -X -n -P --format=ExitCode` 只选 allocation，provider 恰查询一次并返回 `42:7`，不把 step 行当作另一个退出码；测试边界在遗漏 `-X` 时返回多行并使该场景失败

#### Scenario: allocation 查询结果仍有歧义时保留 work
- **WHEN** ExitCode 查询已带 `-X`，结果仍为空、多非空行或多字段，或命令失败/客户端 timeout
- **THEN** provider 抛出绑定同一 job ID 的 `ExecutorError`，不取首行、不去重、不猜退出码、不重试；controller 保留本源 work 且不提交失败日志、不删 work，兄弟源继续

#### Scenario: 失败收尾异常按 source 聚合
- **WHEN** 一个 source 的退出码 provider 抛错、返回空白，或失败日志/work 收尾失败
- **THEN** 该 source 产生带同一 job ID 的 `RunError(phase="cleanup")`，该源此前成功报告不丢，另一 source 仍追赶到自己的结局且完整报告序列被保留

#### Scenario: 直接单源调用保持兼容
- **WHEN** 既有调用方直接调用六参数 `run_once` 且 job 返回 `FAILED` 或 `TIMEOUT`
- **THEN** 返回 `JOB_FAILED`，不调用退出码 provider或失败收尾，精确 work 保留

#### Scenario: final guard 后竞争者先占 exact work
- **WHEN** controller 的最终不存在检查已通过，但在本轮取得原子 claim 前，另一个 writer 创建精确 work 与 foreign marker
- **THEN** controller 认领失败并产生本源 `RunError(phase="raw")`，零 staging、零提交、零 `DONE`，foreign tree 字节与 identity 原样保留；兄弟 source 继续到自己的结局

#### Scenario: 一源 raw rollback 不删除共享 scratch 祖先
- **WHEN** 双源并行 staging，GFS 已创建共享 `work/` 祖先但尚未创建 source 子树，IFS 随后在 raw copy 中失败并 rollback
- **THEN** IFS 只回滚自己已认领的 exact work 内条目，`work/` 与 GFS 路径不被 IFS 删除；GFS 仍发布并继续追赶

#### Scenario: 失败收尾拒绝 replacement work
- **WHEN** 本源失败日志已提交，但删除前原 exact work 被移走并在同 pathname 放入 replacement，或 `work` 父根被重绑到外部同布局树
- **THEN** replacement/external tree 与外部日志逐字不变，已提交本源日志保留，controller 产生绑定同 source/cycle/job 的 `RunError(phase="cleanup")`，兄弟 source 不受影响

#### Scenario: DONE 后 cleanup 拒绝 replacement work
- **WHEN** `DONE` 已写成但 work 删除前 exact root identity 漂移
- **THEN** 本轮返回 `SUCCEEDED_CLEANUP_PENDING`，`DONE` 与 replacement 均保留，MUST NOT 删除当前 pathname 指向的非本 attempt tree

#### Scenario: retention 失败时保留完整报告序列
- **WHEN** ifs 本 tick 依次得到 `SUCCEEDED`、`STOPPED`（`raw_incomplete`），随后 retention 失败
- **THEN** `RunSourcesError.reports["ifs"]` 为这两条报告、`errors["ifs"]` 为 cleanup 阶段错误；若 ifs 报告的非末项不是 `SUCCEEDED`，构造 MUST 抛 `ValueError`

