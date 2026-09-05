# run-controller

来源：compute-loop-design §8、§9.2（状态时间头语义）、§10、§11、§12；products-contract §2、§4、§5、§7；agent-ops §8.2–8.4、§10。

## ADDED Requirements

### Requirement: 严格前沿确定待跑 cycle
每次 run MUST 为每个 source 独立确定严格前沿：该源无任何 `DONE` 时，待跑 T 为 init 写入的最早状态文件名；否则取最新 `DONE` cycle D，T 固定为 D+12h。`states/<source>/<T>.cfg.ic` 缺失、不可读或时间头不对应绝对 T 时 MUST 停止该源；MUST NOT 取更旧状态、跨轮重戳、冷启动或互借另一源状态。

#### Scenario: 全新链取首态文件名
- **WHEN** 某源无 `DONE` 且只有 init 首态 `2026082000.cfg.ic`
- **THEN** 该源待跑 T=2026082000

#### Scenario: 前沿推进 D+12h
- **WHEN** 某源最新 `DONE` 为 2026082600
- **THEN** 待跑 T=2026082612

#### Scenario: 精确状态缺失即停该源
- **WHEN** 待跑 T 的状态文件缺失但存在更旧状态
- **THEN** 该源本次停止，不使用旧状态，另一源不受影响

#### Scenario: 时间头不对应 T 即停该源
- **WHEN** `states/<source>/<T>.cfg.ic` 存在但其时间头不对应绝对 T
- **THEN** 该源本次停止，不提交作业，另一源不受影响

### Requirement: 未提交残留清理与可证安全重跑
无 `DONE(T)` 却存在比 T 更晚的状态文件或 T 的 source 目录半成品时，MUST 判为上次发布中断的 NFS 残留并保留 T 状态。控制器 MAY 在删除这些 NFS 残留后重跑 T，但只有精确 `work/<source>/<T>` 不存在时才可自动重跑。若该 work 仍存在，控制器 MUST 停止本源、保留 work 并报告需人工确认，MUST NOT 假定同源无在途孤儿 Slurm 作业，也 MUST NOT 删除、复用或从该 work 恢复；运维确认无在途作业并移走 work 后，下一次 run 才可从 T 状态干净重跑。当前进程已取得同一 job 的明确 `FAILED`/`TIMEOUT` 终态不属于未知孤儿窗口：它 MUST 先完成失败日志提交与精确 work 删除，再返回失败结论。

#### Scenario: 无 scratch work 的崩溃残留恢复
- **WHEN** 模拟根中存在 T+12 状态与只含 DAT 无 `DONE` 的 T 目录，且精确 `work/<source>/<T>` 不存在
- **THEN** run 删除该 T+12 状态与半成品目录，以 T 状态重新组装本轮

#### Scenario: 未验证 work 阻断自动重跑
- **WHEN** 无 `DONE(T)` 且精确 `work/<source>/<T>` 仍存在，无论其中是否含 job 日志或产物
- **THEN** run 返回 `STOPPED` 且原因是 `UNVERIFIED_WORK_RESIDUE`，保留该 work 并停止本源，不提交新作业；另一源不受影响

#### Scenario: 人工排除孤儿后下一 tick 干净重跑
- **WHEN** 上一 tick 因未验证 work 停止，运维已确认无在途作业并移走该 work，T 状态仍在
- **THEN** 下一 tick 清理剩余 NFS 残留并从 T 状态重新组装，不采纳旧 work 中任何文件

#### Scenario: 已 DONE 的产物不在残留集合内
- **WHEN** 同一棵根里 `output/<T-12>/<source>/DONE` 存在、而 `output/<T>/<source>/` 是无 `DONE` 的半成品
- **THEN** 只删除 T 的半成品目录，`output/<T-12>/` 及其 `DONE`、`yd.rivqdown.dat` 原样保留

#### Scenario: 清理只作用于本源
- **WHEN** IFS 有无 `DONE(T)` 的半成品与比 T 更晚的状态，GFS 在同一 cycle 上也有更晚状态
- **THEN** 只删除 IFS 侧的残留，GFS 的状态与产物不受影响

### Requirement: raw 缺口阻塞不跳轮
待跑 T 的 raw 不完整时该源本次 MUST 不提交；raw 一次补齐多轮时 MUST 按时序逐轮全补；中间永久缺轮时 MUST 停在缺口等待，MUST NOT 自动跳过 cycle。

#### Scenario: raw 未齐不提交
- **WHEN** T 的 raw 缺文件
- **THEN** 该源本次无作业提交，前沿不变

#### Scenario: 补齐多轮按时序追赶
- **WHEN** raw fixture 一次含 T、T+12h、T+24h 三轮完整数据
- **THEN** 该源按 T → T+12h → T+24h 顺序逐轮跑完（fake executor 下三次发布）

#### Scenario: 中间缺口不被更晚完整轮绕过
- **WHEN** T 与 T+24h 的 raw 完整、T+12h 的 raw 不完整
- **THEN** 该源跑完 T 后停在 T+12h，T+24h 零提交；补齐 T+12h 后的下一次 run 先跑 T+12h 再跑 T+24h

#### Scenario: 追赶期间到达的连续轮继续处理
- **WHEN** 调用开始时只有 T 的 raw 完整，但 T+12h、T+24h 分别在前一轮运行期间补齐，此后 T+36h 保持不完整
- **THEN** 同一次持锁 run 按 T → T+12h → T+24h 处理，并在首次观察到 T+36h 不完整时停止；MUST NOT 在调用开始冻结 raw horizon 或设置任意轮数上限

### Requirement: 作业提交经执行器抽象且身份可追溯
run MUST 经作业执行器抽象为每源提交至多一个作业；提交参数（partition、account、CPU、内存、walltime）MUST 全部取自 `local.toml`，代码 MUST NOT 内置任何默认值；每次提交的 job ID、partition、终态与起止时间 MUST 记入本次运行报告，失败源的日志 MUST 含同一 job ID。真实 `sbatch`/`sacct` 行为归 M4 oracle，本地以注入 fake 验证。

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

### Requirement: 并发与锁
run 入口 MUST 使用非阻塞 flock：已有实例持锁时本次直接跳过不排队；锁 MUST 覆盖发现、提交、等待、发布、清理全生命周期。IFS/GFS 最多各一个作业并行。`cron.lock_path` MUST 是绝对路径：相对路径与 `~` 前缀（`Path` 不展开 `~`）MUST 在创建锁文件之前 fail closed，报错 MUST 指名 `cron.lock_path`。锁文件 MUST NOT 在释放时删除。

#### Scenario: 锁被持有即跳过
- **WHEN** 锁文件已被另一进程持有时进入 run 包装
- **THEN** 本次立即退出成功（跳过语义），不执行发现

#### Scenario: 非绝对锁路径即拒
- **WHEN** `cron.lock_path` 为 `yd.lock` 或 `~/yd.lock`
- **THEN** run 包装报错退出并指名 `cron.lock_path`，不创建任何锁文件，不执行发现

#### Scenario: 双源并行单源失败不阻塞
- **WHEN** fake executor 令 IFS 作业失败、GFS 作业成功
- **THEN** 两源作业曾同时在途，GFS 正常发布且不等待 IFS 失败收尾，IFS 本次停止；IFS 失败留一份合并日志 `logs/ifs/<T>.log`，其 work 被删除；GFS 的后续逐轮追赶由同源循环继续

#### Scenario: 双源 publish 串行保护共享层级
- **WHEN** IFS/GFS 同一 cycle 的作业都成功并几乎同时进入 publish
- **THEN** 两次 publish 不重叠，预置 `output/` 的 mode 不被改写，两源均正常落 `DONE`

### Requirement: 双源独立追赶组合公共契约
公开入口 MUST 精确为 `run_sources(*, config: Config, local: LocalConfig, executors: Mapping[str, JobExecutor], drivers: Mapping[str, AttemptDriver], poll_waits: Mapping[str, Callable[[], None]], failure_exit_codes: Mapping[str, Callable[[JobRecord], str]]) -> RunSourcesReport`，全部参数 keyword-only 且无默认值。它 MUST 用两个固定 source worker 让 IFS/GFS 各自独立逐轮调用带组合选项的私有 `run_once`；只有 `SUCCEEDED` 才在同一 worker 内开始下一轮，`STOPPED`、`JOB_FAILED`、`SUCCEEDED_CLEANUP_PENDING` 都作为该源有序报告序列的末项。每轮 MUST 从已落盘 `DONE`/state 重新发现严格前沿，MUST NOT 缓存或自增 T、预扫更晚 raw、冻结调用开始时的 raw horizon，或并行提交同源多轮。#27 的公开 `catch_up_source` 签名与实现结构 MUST 保持不变；#28 只复用其“仅成功继续”规则，不重写其公共合同。

`executors`、`drivers`、`poll_waits`、`failure_exit_codes` 的键集 MUST 各自恰为 `{ifs,gfs}`，映射 MUST 在启动 worker 前快照；两源 MUST 使用不同的 executor 与 driver 实例。映射或实例不合法时 MUST 在任何发现、文件系统变更或提交前拒绝。两个 worker MUST 都启动并全部结束后才汇总结论；一个源停止或抛出 `RunError` 时 MUST NOT 取消、截断或阻塞另一个源继续追赶到自己的首次非成功结局。

两源都正常返回时，`run_sources` MUST 返回 frozen、keyword-only 的 `RunSourcesReport(ifs: tuple[RunReport, ...], gfs: tuple[RunReport, ...])`。两个 tuple 都至少一项，按该源轮次顺序排列，所有非末项 MUST 为 `SUCCEEDED`，末项 MUST 为首次非 `SUCCEEDED`；每项 `source` 必须与字段一致。任一 worker 抛出 `RunError` 时，MUST 在两源都结束后抛 `RunSourcesError(RuntimeError)`；其 `reports: Mapping[str, tuple[RunReport, ...]]` 是构造时取得、精确含 `{ifs,gfs}` 的不可变快照，tuple 可为空；其 `errors: Mapping[str, RunError]` 是构造时取得的非空、不可变 source 子集。同一 source MAY 同时在 `reports` 中有此前成功轮并在 `errors` 中有最终异常；错误文本 MUST 按 `ifs`、`gfs` 固定顺序列出。组合层 MUST NOT 丢弃异常前已完成的报告或兄弟源的完整报告序列。

`FAILED`/`TIMEOUT` 的自动失败收尾只属于 `run_sources` 路径：它 MUST 只调用本源 `failure_exit_codes[source]`，并把同一 terminal `JobRecord` 交给 provider；provider 的返回必须是 nonblank `str`，原值交给 `finalize_failed_job`。provider 或失败收尾的普通异常 MUST 变为同 source/cycle/job ID 的 `RunError(phase="cleanup")`，但不得取消兄弟 source；该源此前的成功报告仍保留。直接调用既有六参数 `run_once` 时 MUST 保持原行为：返回 `JOB_FAILED`，不取得退出码、不调用失败收尾并保留 work。

对 raw 完整的合法 T，controller MUST 在任何 staging 写入前通过 no-follow 父目录排他创建精确 `work/<source>/<T>`，并冻结该目录的 `(st_dev, st_ino)` 作为本 attempt 的 ownership token。竞争者先创建任何形态时 MUST 零 staging、零提交、保留现有条目并以本源 `RunError(phase="raw")` 失败；普通的 check-then-create 不构成认领。共享 `work/` 与 `work/<source>/` 祖先 MUST 在 exact root 认领前由不参与 raw rollback 的 no-follow 创建负责；raw staging 的 rollback MUST NOT 删除兄弟 source 创建的共享祖先。

controller 路径的 scratch 读取、失败收尾与成功发布 MUST 消费并重验同一个 token，不得从后来可能重绑的 pathname、父 symlink 或 `realpath` 重新推导 ownership。`DONE` 前 identity 漂移 MUST 保留当前条目、不写 `DONE` 并产生对应 raw/collect/publish `RunError`；失败日志已提交后、work 删除前漂移 MUST 保留日志与 replacement 并成为 `RunError(phase="cleanup")`；`DONE` 已写后漂移 MUST 保留 replacement 并返回 `SUCCEEDED_CLEANUP_PENDING`。删除操作 MUST 在打开 named root 后和最终移除 root 前校验 expected identity，不能只在函数入口比较一次。standalone `rawcopy.stage_raw`、`PublishInputs`/`publish` 与 `FailureInputs`/`finalize_failed_job` 的既有调用形态 MUST 保持兼容；新增 claim 输入只能是末尾有默认值的 additive 参数，controller 路径则必须传入非空 token。

raw staging 失败时 MUST 保持 rawcopy 既有“不留半套”和本控制器“下次从干净 work 重试”语义：本轮后代已完整 rollback 且 exact root 仍匹配 token、确认为空时，controller MUST 仅以 identity-bound `rmdir` 删除该 exact root；MUST NOT 删除 source/shared ancestor。若 root 非空、漂移或无法确定，MUST 保留当前 entry，并在原 `RunError(phase="raw")` 中携带 cleanup 失败证据。ownership helper 打开的每个 directory/file fd MUST 在所有正常、`Exception` 与 `BaseException` 路径中恰当关闭；成功返回给 caller 的文件 fd 只由 caller 关闭。

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
- **WHEN** IFS 返回 `FAILED` 且其 provider 对该 terminal job ID 返回非默认退出码，GFS 成功并继续追赶
- **THEN** provider 只调用一次，IFS 唯一失败日志逐字含该 job ID 与退出码，IFS work 在日志提交后删除；GFS provider 不调用且 GFS 正常发布后续轮

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

### Requirement: NFS 提交顺序与 DONE 语义
发布 MUST 按固定顺序执行：

1. 把捕获的 T+12 checkpoint 重戳到绝对 T+12（复用 state-tools 重戳；同轮定戳，见 compute-loop §9.2）；
2. DAT 复制为 `output/<T>/<source>/` 下的临时文件并在 NFS 内原子 rename 为 `output/<T>/<source>/yd.rivqdown.dat`；
3. T+12 状态复制为临时文件并原子 rename 为 `states/<source>/<T+12>.cfg.ic`；
4. 最后原子创建 `output/<T>/<source>/DONE`；
5. `DONE` 成功后才删除比 T 更旧的状态，最终每源只保留 T 与 T+12 两份；旧的 T 状态在此之前 MUST 保留；
6. 旧状态清理完成后 MUST 删除本轮 scratch `work/<source>/<T>`（含 raw 副本、canonical、forcing、临时 registry 与 raw-manifest）。

复制进 NFS 的正式文件 MUST NOT 继承 scratch 源文件的 uid/gid/mode，由控制器按发布权限创建（agent-ops §10）。

写 `DONE` 前 MUST 通过自身契约检查：DAT 为 v2、行数等于 `forecast_days*24`、数据列数等于 `config.toml` 的 `reach_count` 且等于模型变体 reach 数、T+12 状态可按分段格式读取、本轮合并 stdout/stderr 日志可用。

#### Scenario: 提交顺序可观测
- **WHEN** 以可记录文件系统操作的发布器完成一轮成功发布
- **THEN** 操作序列中 DAT rename（终名 `yd.rivqdown.dat`）先于状态 rename，`DONE` 创建最后，旧状态删除在 `DONE` 之后，work 删除最末

#### Scenario: checkpoint 发布前定戳
- **WHEN** tracker 捕获 header 为相对 720 分钟的 checkpoint 并走完发布
- **THEN** `states/<source>/<T+12>.cfg.ic` 的时间头对应绝对 T+12

#### Scenario: 行数不足不写 DONE
- **WHEN** 作业产出的 DAT 行数不足
- **THEN** 不创建 `DONE`，本轮按失败处理并留日志

#### Scenario: reach 数不符不写 DONE
- **WHEN** DAT 数据列数不等于 `reach_count`
- **THEN** 不创建 `DONE`，本轮按失败处理并留日志

#### Scenario: 发布文件不带 scratch 权限
- **WHEN** scratch 中的 DAT 与状态文件 mode 为 0600
- **THEN** NFS 正式文件按发布权限创建，mode 不等于 0600

#### Scenario: 成功轮 work 被删除
- **WHEN** 一轮成功发布完成
- **THEN** `work/<source>/<T>` 不存在

#### Scenario: 状态只保留两份
- **WHEN** 连续发布两轮成功
- **THEN** 该源 `states/` 下只存在最新待跑状态及其前一份

### Requirement: 失败处理
作业在 `run_sources` 的当前控制器实例中明确返回 `FAILED`/`TIMEOUT` 时 MUST 不写 `DONE`、不推进状态链；双源组合器 MUST 按「双源独立追赶组合公共契约」从本源显式退出码 provider 取得同一 job 的非空退出码，MUST NOT 从 `JobState` 猜测；随后 MUST 把完整 stdout/stderr、命令、job ID、起止时间与退出码合成一份 `logs/<source>/<T>.log`，日志原子提交成功后才删除整个精确 scratch work。失败收尾完成后，下次 run 从干净 work 对该 cycle 重试。MUST NOT 维护失败计数、退避或 `status.json`。一个源的失败或失败收尾错误 MUST NOT 取消另一源已经启动的作业；双源控制器在两源都结束后才返回或抛出错误。直接六参数 `run_once` 的兼容行为不在此自动收尾要求内：它仍返回 `JOB_FAILED` 并保留 work。

#### Scenario: 失败轮产物
- **WHEN** fake executor 返回失败
- **THEN** 该 source/cycle 无 `DONE`、状态链未动、存在唯一含该轮 job ID 的合并日志、work 目录不存在

### Requirement: 保留窗口与安全清理
清理 MUST 保留最新成功 cycle 往前 14 天的 `output` source 目录，窗口外目录与对应失败日志删除；每个删除目标（含成功轮 work 删除）MUST 先经 `realpath` 确认位于 yd 自己的根内，否则拒绝删除。

#### Scenario: 14 天窗口
- **WHEN** 模拟根含最新成功 cycle 与一个 15 天前的 source 目录
- **THEN** 窗口外目录被删除，窗口内完整保留

#### Scenario: symlink 越界拒删
- **WHEN** 待清理路径是指向 yd 根之外的 symlink
- **THEN** 清理拒绝删除该目标并报告
