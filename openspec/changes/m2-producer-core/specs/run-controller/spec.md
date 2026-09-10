# run-controller

来源：compute-loop-design §8、§9.2（状态时间头语义）、§10、§11、§12；products-contract §2、§4、§5、§7；agent-ops §8.2–8.4、§10。

## ADDED Requirements

### Requirement: 严格前沿确定待跑 cycle
每次 run MUST 为每个 source 独立确定严格前沿：只有共享 `output/` 根已确认为可枚举目录、且该源的 `DONE` 集合被确定为空时，才是全新链，待跑 T 为 init 写入的最早状态文件名；否则取最新 `DONE` cycle D，T 固定为 D+12h。`output/` 根遇 `ENOENT` 或 `ENOTDIR` 是根异常，MUST 以 `DISCOVERY_UNREADABLE` 停止本源，MUST NOT 当作空 `DONE` 集合或全新链。`states/<source>/<T>.cfg.ic` 缺失、不可读或时间头不对应绝对 T 时同样 MUST 停止该源；MUST NOT 取更旧状态、跨轮重戳、冷启动或互借另一源状态。

#### Scenario: 全新链取首态文件名
- **WHEN** `output/` 是可枚举目录，某源被确定为无 `DONE`，且只有 init 首态 `2026082000.cfg.ic`
- **THEN** 该源待跑 T=2026082000

#### Scenario: output 根缺失或不是目录即停本源
- **WHEN** `output/` 根缺失（`ENOENT`）或被普通文件等非目录条目占据（`ENOTDIR`）
- **THEN** 该源以 `DISCOVERY_UNREADABLE` 停止，不判全新链，不检查 raw，不提交作业；组合层仍逐源生成报告，另一源独立执行同一根检查（共享根异常时也会停止）

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
在 `output/` 根已确认为可枚举目录、前沿 T 可可靠确定的前提下，无 `DONE(T)` 却存在比 T 更晚的状态文件或 T 的 source 目录半成品时，MUST 判为上次发布中断的 NFS 残留并保留 T 状态。控制器 MAY 在删除这些 NFS 残留后重跑 T，但只有精确 `work/<source>/<T>` 不存在时才可自动重跑。若 `output/` 根缺失或不是目录，控制器 MUST 在残留规划之前停止本源，MUST NOT 生成或执行任何残留清单；不得以更早状态重建 T。若该 work 仍存在，控制器 MUST 停止本源、保留 work 并报告需人工确认，MUST NOT 假定同源无在途孤儿 Slurm 作业，也 MUST NOT 删除、复用或从该 work 恢复；运维确认无在途作业并移走 work 后，下一次 run 才可从 T 状态干净重跑。当前进程已取得同一 job 的明确 `FAILED`/`TIMEOUT` 终态不属于未知孤儿窗口：它 MUST 先完成失败日志提交与精确 work 删除，再返回失败结论。

#### Scenario: 无 scratch work 的崩溃残留恢复
- **WHEN** `output/` 根可枚举，模拟根中存在 T+12 状态与只含 DAT 无 `DONE` 的 T 目录，且精确 `work/<source>/<T>` 不存在
- **THEN** run 删除该 T+12 状态与半成品目录，以 T 状态重新组装本轮

#### Scenario: output 根异常时零残留清理
- **WHEN** `states/<source>/` 有一份或多份合法状态，但 `output/` 根缺失或不是目录
- **THEN** run 以 `DISCOVERY_UNREADABLE` 停止本源，所有状态、产物与 work 逐字节不变，残留判定与删除零调用

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

### Requirement: 残留清单绑定声明身份
公开 `ResiduePlan` MUST 在构造时将 yd_root resolve 并绑定非空单分量 source、合法 retained cycle 及每条删除路径。state_files MUST 词法等于该根的 `states/<source>/<cycle>.cfg.ic` 且 cycle 严格晚于 retained；half_product_dirs MUST 词法等于该根的 `output/<retained>/<source>`。执行器 MUST 在任何删除之前复验全部字段与全部条目；任一身份越界以 `SafeFilesystemError(kind="unsafe")` 拒绝整份清单且零删除。合法 tuple 规范化排序去重。不得用目标 realpath 消除词法越界；既有 safe_fs no-follow 继续负责文件系统使用点安全，合法清单的 IO 拒绝不承诺回滚。

#### Scenario: 手构清单不能删除兄弟源
- **WHEN** source=ifs 的手构计划带有 GFS output（含 DONE）或 GFS state，或跨根/lane/retained cycle 的条目
- **THEN** 构造即拒绝，所有根内外文件递归快照不变

#### Scenario: 执行期拒绝整份身份非法计划
- **WHEN** 绕过构造或篡改后的计划先列合法半成品，后列其它源、错误后缀、带遍历或不晚于 retained 的状态
- **THEN** execute_residue_plan 在第一笔删除前拒绝，合法半成品与所有状态也全部保留

#### Scenario: 自洽计划保持既有行为
- **WHEN** planner 或手工构造的完整计划满足 root/source/lane/cycle 身份，含重复或乱序合法路径
- **THEN** 规范化后只删除点名目标，保留 retained 与兄弟源；重复执行无副作用，DONE 判定仍仅由 planner 承担

### Requirement: run 启动时清理 DONE 已证明完成的历史 work
`run_sources` MUST 先完成四份依赖 mapping 的全局快照与校验，随后让每个 source worker 先完成该源既有纯 preflight，再恰执行一次 startup hygiene；任何前置失败都不得触发该范围内的 discovery 或删除。hygiene MUST 位于本源首次前沿发现之前，先确认共享 `output/` 根可枚举，再只枚举 resolved scratch `work_root/<source>` 的直接子项。只有现有公开 `parse_cycle_id` 接受且 hour 属于 `config.cycle.hours` 的名字是候选；其它名字 MUST 原样保留且不得映射成 `output` 路径。候选集合不得按墙钟、mtime、最新 DONE 或当前 frontier 截断。

候选 MUST 按 cycle 升序处理。对每个候选，只有既有 `safe_fs.stat_no_follow(..., containment_root=resolved YD_ROOT)` 返回普通文件身份时，同源 `output/<T>/<source>/DONE` 才是删除授权。DONE 缺席，或该探测确认叶子/父链为 symlink、目录、FIFO 等不安全或非普通形态时，均按“无有效 DONE”处理；`SafeFilesystemError.kind` 为 `io`、`identity_changed`、`indeterminate` 等无法确定状态时则产生 cleanup error，不能静默降成无 DONE。所有无有效 DONE 的候选 MUST 先保留，不能让其中一个候选遮蔽其它 DONE-backed 候选；完整扫描结束后，最早的无有效 DONE 候选 MUST 复用 `STOPPED/UNVERIFIED_WORK_RESIDUE` 成为本源首报告，且 frontier/raw/driver/submit 零调用。不得为本 Requirement 改变 `safe_fs.stat_no_follow` 或其它公共 helper 合同。

有普通文件 `DONE(T)` 时，它已证明对应 work 不再是运行 authority。若 exact work 是真实目录，MUST 先 no-follow 取得 `(st_dev, st_ino)`，再以 resolved scratch `work_root` 为 containment root 执行 expected-identity tree delete，并在打开目录与最终 `rmdir` 前复核同一 identity；树内 symlink 只删除链接、不跟随目标。不得等待 T 再次成为 frontier，也不得要求旧 attempt receipt。若有普通文件 DONE 的 exact work 是普通文件、FIFO、symlink/断链等非目录，现有原语无法 identity-conditionally unlink，它 MUST 保留并产生本源 `RunError(phase="cleanup")`；identity 漂移、枚举或 I/O 无法确定时同样保留并失败。已成功删除项不回滚；发生清理错误后尚未处理的后续候选不得删除。一源的 hygiene 停止或失败不得阻止、取消或截断兄弟 source worker。

每个成功删除项 MUST 以稳定 `startup cleanup:` 前缀记录 source、cycle 与绝对 exact path，并按 cycle 有序前缀到本源首个 `RunReport.detail`；该首报告可以是 hygiene 生成的 unknown-work STOPPED，也可以是首次 `run_once` 的报告。若首报告前另有 `RunError`，MUST 对原异常对象 `add_note` 写入同一清单，保留其 identity、cause 与既有 notes；启动清理自身失败的 `RunError` 正文 MUST 列出此前已删除项及失败 source/cycle/绝对路径。`RunSourcesError` 的单份人读文本 MUST 按 `ifs,gfs` 渲染底层错误及其 notes，每项恰一次；CLI MUST 把该完整消息输出一次且无 traceback。`RunReport` 八字段、`RunSourcesReport` 两字段与 outcome/phase 词表均不得扩展。

`output/` 根的 `ENOENT/ENOTDIR` 是特例：两源分别返回既有 `STOPPED/DISCOVERY_UNREADABLE`，startup hygiene 与 NFS residue 均零删除，不转成 cleanup error。该 no-follow DONE 判据只授权危险的历史 scratch 删除；既有前沿发现及 `residue.plan_residue` 的 NFS 语义不因本 Requirement 改写，特别是 `DONE(T)` 存在时后者仍返回空 NFS 清单。

#### Scenario: DONE 对应的全部历史 work 在启动时删除并入报告
- **WHEN** 某源乱序存在多个合法 cycle work，其中多个有普通文件 `DONE(T)` 且目录内含指向 scratch 外的 symlink，另一个更早候选无有效 DONE
- **THEN** 所有 DONE-backed work 都在首次前沿发现前按 cycle 删除，外部 symlink 目标与 NFS `output/`/`states/` 逐字节不变；随后最早无 DONE 候选产生 `STOPPED/UNVERIFIED_WORK_RESIDUE`，该首报告 `detail` 以 `startup cleanup:` 按序列出每个已删 source/cycle/绝对路径

#### Scenario: 无有效 DONE 的各种 work 都停源并保留
- **WHEN** 合法 cycle exact work 是目录、普通文件、FIFO、symlink 或断链 symlink，而对应 DONE 缺失或为目录、FIFO、最终 symlink/断链
- **THEN** run 不读、不删、不复用这些 work，完整扫描其它候选后以最早 cycle 返回 `STOPPED/UNVERIFIED_WORK_RESIDUE`；只有同源 no-follow 普通文件 DONE 才允许删除对应真实目录

#### Scenario: 普通文件 DONE 不授权 pathname-only unlink
- **WHEN** 普通文件 `DONE(T)` 存在，但对应 exact work 是普通文件、FIFO、symlink或断链
- **THEN** exact entry 及 symlink 目标均保留，本源产生指名 source/cycle/绝对路径的 `RunError(phase="cleanup")`，不得调用无 expected identity 的 unlink

#### Scenario: 历史 work identity 漂移时拒绝删除 replacement
- **WHEN** 启动清理已按 cycle 删除至少一个历史目录，随后另一个 exact work 在 identity 冻结后被替换为另一个 inode
- **THEN** replacement 与 NFS 正式产物保持不变，本源产生 `RunError(phase="cleanup")`，错误正文保留此前已删除路径及当前失败路径；已删项不回滚，后续候选不再删除，兄弟源继续到自身结局

#### Scenario: 启动清理后其它运行错误保留原对象与审计
- **WHEN** 某源成功清理一个或多个 DONE-backed work 后，在形成首报告前由首次 `run_once` 抛出已有 cause/note 的 `RunError`
- **THEN** `RunSourcesError.errors[source]` 保留同一个 `RunError` 对象及原 cause/note，只追加一条有序 startup-cleanup note；聚合错误的单份文本含底层错误与每条 note 各一次，CLI 将该完整文本打印一次

#### Scenario: output 根异常时历史 work 零删除
- **WHEN** `output/` 根缺失或不是目录，同时 scratch 有若干看似可清理的历史 work
- **THEN** 两源按 `DISCOVERY_UNREADABLE` 停止，work/state/NFS 逐字节不变，startup hygiene 与 residue delete 零调用

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

### Requirement: controller 在 driver 与 submit 前提交 work-local model/state capability
对 raw 完整的合法 T，controller MUST 在取得并持续验证同一 `WorkClaim`、`rawcopy.stage_raw` 成功后，调用 forcing-chain 的唯一 `stage_work_inputs`，把 `prepare.variant_targets(local, config)[source]` 的 #171 exact-five variant 与已由前沿选中的精确 state 搬入该 claim 下固定 `input/`。这一步 MUST 在 `driver.prepare`、JobSpec 构造和 `executor.submit` 之前完成；project 继续只来自 source variant 顶层率定态固定文件名，grid ID 继续取 `config.nwm_canonical_grid_id.<source>`，manifest/asset/state caps 继续取版本化代码常量而非 local/env。controller MUST 把前沿已经确认的 exact state path传给 stager，不得另扫、取旧态、重戳或从 staged target反推。

`AttemptRequest` 的 frozen kw-only 字段与公开 `AttemptDriver` 协议 MUST 保持不变。`request.variant_dir/state_path` 仍是两个 NFS source 路径，只供运行在登录节点的 production `driver.prepare` 独立调用 #171 source loader并与 `request.work_dir/input` 的 public staged loader结果对账；controller/fake 不得把字段改为 staged path而丢失 source authority。production driver 构造的 worker argv/环境/attempt handoff，及 worker receipt、compute-side canonical/forcing/assemble输入，MUST NOT包含这两个 NFS 路径，只允许 exact work-local relative paths、canonical staged-manifest checksum与已验证内容/identity。计算节点不得访问 `YD_ROOT`，登录节点不得为规避该约束提前运行 canonical/forcing/assemble/SHUD。

独立 worker MUST 通过 `load_staged_work_inputs` 重建本进程 capability 并复核 attempt handoff 绑定的 manifest digest，从 `staged.prepared` 创建同一 work 的 registry/forcing，调用 `assemble_staged`；MUST NOT 把 work-relative 输入根送给 legacy `assemble` 或序列化 controller 的 fd/inode 代替点用验证。worker 入口仍在 #132 六文件边界内的 `nwm.py`，不得为消费本 capability 增加新模块、console-script 或公开子命令。

staging 任一失败 MUST 变成保留 cause/notes 的 `RunError(phase="prepare", source, cycle, job_id=None)`，driver/executor零调用、零`DONE`。由于 raw 已成功写入，同一 exact work作为未验证 residue保留，不运行 raw 的 empty-root release，也不按 pathname细粒度删除 input；下一 tick继续由既有 `UNVERIFIED_WORK_RESIDUE` 停源。成功 publish与明确 FAILED/TIMEOUT failure finalizer仍按现有顺序整树删除，因 containment自动包含 input；submit/poll timeout、未知 worker崩溃与其它证据保留路径继续保留整树。不得新增 work 外 sibling staging、input sweeper、自动 crash recovery或第二套 cleanup owner。

#### Scenario: controller staging 顺序与登录/计算节点边界
- **WHEN** NFS prepared variant/state合法且 raw staging 成功
- **THEN** exact work先出现 checksum-bound staged capability，production `driver.prepare`随后收到仍指向NFS source的原 `AttemptRequest`并能对账；JobSpec/submit最后发生，worker command/env/handoff中零 NFS路径，compute-side chain只使用同一 work-local input

#### Scenario: staged input失败保留 exact work并阻止提交
- **WHEN** source/staged variant或state的形态、identity、entry set、size、schema、cycle、checksum任一非法，目标O_EXCL冲突，或stager最终reload发现漂移
- **THEN** controller抛`RunError(phase="prepare", job_id=None)`并保留同一claimed exact work和原cause/notes，driver/submit/poll/finalizer/publish/DONE零调用；兄弟source在`run_sources`下继续

#### Scenario: staged input 生命周期只跟随 exact work
- **WHEN** staged capability成功后分别发生submit timeout、poll timeout、未知worker崩溃、明确FAILED/TIMEOUT finalizer成功、或publish成功
- **THEN**前三类按既有证据政策连同整棵work保留且下一tick停源，后两类由既有failure/publish owner整树删除；work外零variant/state副本且没有input单独删除调用

#### Scenario: 新 worker 进程重验 staged capability 而不读取 NFS
- **WHEN** controller 已成功提交 staged input，登录侧 driver 对账后将 work-local handoff 交给新的 worker 进程，且原 NFS source 已删除或不可读
- **THEN** worker 在既有 `nwm.py` 私有入口通过 `load_staged_work_inputs` 重建本进程 capability 并复核 manifest digest，从 `staged.prepared` 创建 registry/forcing 后调用 `assemble_staged`；不新增入口/module/console-script，不传 NFS source 或 fd/inode，不放宽 legacy `assemble` 对 work-relative 根的拒绝

#### Scenario: worker 点用错误的 staged 证据即停止发布
- **WHEN** worker 或 collect 点用时 staged manifest digest、source/cycle/work、layout、内容 checksum 或本进程 root identity 与本 attempt 的已验证输入不一致
- **THEN** 当前阶段 fail closed 且零 `DONE`，不采用 legacy `assemble`、目录扫描或登录侧补跑绕过校验，不写删 replacement work

### Requirement: 作业提交经执行器抽象且身份可追溯
run MUST 经作业执行器抽象为每源提交至多一个作业；提交参数（partition、account、CPU、内存、walltime）MUST 全部取自 `local.toml`，代码 MUST NOT 为这些资源内置任何默认值；每次提交的 job ID、partition、终态与起止时间 MUST 记入本次运行报告，失败源的日志 MUST 含同一 job ID。真实 `sbatch`/`sacct` 行为归 M4 oracle，本地以注入 fake 验证。

每一次真实 `sbatch`、普通轮询 `sacct` 与失败 ExitCode `sacct` 客户端子进程 MUST 设置同一个正整数秒数的调用时限，取自 `LocalConfig.slurm_command_timeout_seconds`；其唯一缺省为配置装载器的版本化 60 秒。该值不得进入 `JobSpec.resources` 或 `sbatch` argv，也不是 Slurm job walltime、job watchdog 或取消策略。客户端超时 MUST 经既有异常漏斗转成 `ExecutorError`，不自动重试。

客户端 timeout 只证明 submit/query 调用没有及时返回，MUST NOT 伪造 `JobState.TIMEOUT`。若发生在 submit，controller 产生保留该 `ExecutorError` 为 cause 的 `RunError(phase="submit", job_id=None)`；若发生在普通 poll，产生 `RunError(phase="poll", job_id=<已知 job>)`。两者都必须保留 exact work、零 ExitCode provider/finalizer/collect/publish/DONE。若调度器已明确返回 terminal `FAILED/TIMEOUT`，但随后 ExitCode `sacct` 客户端 timeout，则产生绑定同一 job ID 的 `RunError(phase="cleanup")`，保留 work 与已在 scratch 的 job log，零失败日志提交/删除。三者都终止本源 worker并经 `RunSourcesError` 聚合，兄弟 source 继续到自己的结局，且均不自动重试。由于 `sbatch` timeout 可能发生在服务端已接收之后，下一 tick 仍由无 DONE work 的人工闸保护，不得自动删除重提。

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

### Requirement: 并发与锁
run 入口 MUST 使用非阻塞 flock：已有实例持锁时本次直接跳过不排队；锁 MUST 覆盖发现、提交、等待、发布、清理全生命周期。IFS/GFS 最多各一个作业并行。`cron.lock_path` MUST 是绝对路径：相对路径与 `~` 前缀（`Path` 不展开 `~`）MUST 在创建锁文件之前 fail closed，报错 MUST 指名 `cron.lock_path`。

`cron.lock_path` MUST 位于 node-22 本地文件系统的专属 `run/` 目录，MUST NOT 位于 yd/NWM NFS、`scratch_root` 或其它网络/共享挂载；这是部署时按实际挂载信息验收并写入 M4 receipt 的现场约束，业务代码 MUST NOT 按路径前缀、hostname 或平台猜文件系统类型。Linux NFS 把 `flock` 仿真为整文件 byte-range lock，不能提供本项目进程内判别器依赖的 per-open-file-description 前提。锁文件及其专属目录是长期哨兵：runlock 释放时只能 unlock/close，retention、work、staging 等任何仓内清理以及运维命令、tmp sweeper 等外部主体都 MUST NOT unlink、rename、replace 锁文件或删除/替换目录。

每次成功 `flock` 后，runlock MUST 先以 `fstat(lock_fd)` 冻结普通文件的 `(st_dev, st_ino)`，再以 no-follow path stat 核对 `cron.lock_path` 仍是同一普通文件；只有核对成功才可调用被包裹 action。首次核对不稳定时 MUST unlock/close 旧 fd 并从 open/flock 开始完整重取一次，旧 attempt 下 action 零调用；重取遇真实锁竞争仍按成功跳过，第二次仍缺失、为 symlink/非普通文件、identity 不一致或状态不可确定则 MUST 抛指名 `cron.lock_path` 的 `RunLockError`，不得修补、重建、删除或返回 `acquired=False`。

被包裹 action 返回或抛错后，runlock MUST 在仍持有 flock 时再次执行同一 no-follow 普通文件/identity 核对，再 unlock/close。退出核对失败时不得重跑 action：action 正常返回则抛 `RunLockError`；action 自身已抛异常则保留同一异常对象与 cause，追加锁身份漂移 note 后原样抛出。所有成功、跳过和异常路径都 MUST 释放本调用持有的锁并关闭 fd，且 MUST NOT unlink 当前 pathname 或 replacement。两次边界核对用于发现违反哨兵生命周期的替换，不宣称阻止两次检查之间的不合作 unlink；外部永不删除或替换哨兵仍是防止旧、新 inode 双持有者的必要不变量。

#### Scenario: 锁被持有即跳过
- **WHEN** 锁文件已被另一进程持有时进入 run 包装
- **THEN** 本次立即退出成功（跳过语义），不执行发现

#### Scenario: 非绝对锁路径即拒
- **WHEN** `cron.lock_path` 为 `yd.lock` 或 `~/yd.lock`
- **THEN** run 包装报错退出并指名 `cron.lock_path`，不创建任何锁文件，不执行发现

#### Scenario: 首次取得的 fd 与锁路径不是同一 inode
- **WHEN** 第一次 `flock` 成功后、action 前，`cron.lock_path` 缺失或 no-follow `(st_dev, st_ino)` 不等于 `fstat(lock_fd)`
- **THEN** runlock 在 action 零调用下释放旧 fd 并完整重取至多一次；稳定的第二次取得才执行 action，第二次仍不稳定则抛 `RunLockError` 且不删除当前路径

#### Scenario: 持锁期间锁路径被外部替换
- **WHEN** action 运行期间外部 unlink 或替换 `cron.lock_path`，使退出核对的普通文件身份与已冻结身份不一致
- **THEN** runlock 不重跑 action、不删除 replacement，释放旧锁/fd 后响亮失败；若 action 同时抛错则原异常对象与 cause 保持，只追加锁漂移 note

#### Scenario: 锁路径只部署在 node-22 本地盘
- **WHEN** M4 安装 cron 并检查 `local.toml` 中 `cron.lock_path` 的实际挂载
- **THEN** 只有专属 node-22 本地文件系统路径可写入 receipt 并启用 cron；yd/NWM NFS、scratch 或其它网络/共享挂载必须拒绝

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

### Requirement: NFS 提交顺序与 DONE 语义
发布 MUST 按固定顺序执行：

1. 把捕获的 T+12 checkpoint 重戳到绝对 T+12（复用 state-tools 重戳；同轮定戳，见 compute-loop §9.2）；
2. DAT 复制为 `output/<T>/<source>/` 下的临时文件并在 NFS 内原子 rename 为 `output/<T>/<source>/yd.rivqdown.dat`；
3. T+12 状态复制为临时文件并原子 rename 为 `states/<source>/<T+12>.cfg.ic`；
4. 最后原子创建 `output/<T>/<source>/DONE`；
5. `DONE` 成功后才删除比 T 更旧的状态，最终每源只保留 T 与 T+12 两份；旧的 T 状态在此之前 MUST 保留；
6. 旧状态清理完成后 MUST 删除本轮 scratch `work/<source>/<T>`（含 raw 副本、canonical、forcing、临时 registry 与 raw-manifest）。

复制进 NFS 的正式文件 MUST NOT 继承 scratch 源文件的 uid/gid/mode，由控制器按发布权限创建（agent-ops §10）。

写 `DONE` 前 MUST 通过自身契约检查：DAT 为 v2、行数等于 `forecast_days*24`、数据列数等于 `config.toml` 的 `reach_count` 且等于模型变体 reach 数、数据区第 `i` 行（从 0 起）的第 0 列逐值等于 `i * config.output_interval_minutes`、T+12 状态可按分段格式读取、本轮合并 stdout/stderr 日志可用。分钟列必须在 scratch 侧以 descriptor-bound 有界读取逐行校验，MUST NOT 整读数据区或在发布器中写死/反推间隔。

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

#### Scenario: DAT 相对分钟列不符不写 DONE
- **WHEN** DAT 的 v2 头、行数、列数与总字节数均正确，但任一数据行的第 0 列不等于该行序乘 `output_interval_minutes`（含整体偏移或非有限值）
- **THEN** 发布器在任何 NFS 写入前抛 `PublishError`，不创建 `DONE`，正式 output/states 逐项不变；检查只读取每行该一个 float64，不整读流量矩阵

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
作业在 `run_sources` 的当前控制器实例中明确返回 `FAILED`/`TIMEOUT` 时 MUST 不写 `DONE`、不推进状态链；双源组合器 MUST 按「双源独立追赶组合公共契约」调用 MUST 注入的本源失败收尾 provider。该 provider 对同一 job 恰执行一次 `sacct -j <job_id> -X -n -P --format=ExitCode` 并返回非空退出码字符串；轮询通道不得取 `ExitCode`，不得从 `JobState` 猜测，`JobRecord` 七字段不变。组合器 MUST 将该字符串作为 `FailureInputs.exit_code` 传给 `finalize_failed_job`，随后把完整 stdout/stderr、命令、job ID、起止时间与退出码合成一份 `logs/<source>/<T>.log`，日志原子提交成功后才删除整个精确 scratch work。失败收尾完成后，下次 run 从干净 work 对该 cycle 重试。MUST NOT 维护失败计数、退避或 `status.json`。一个源的失败或失败收尾错误 MUST NOT 取消另一源已经启动的作业；双源控制器在两源都结束后才返回或抛出错误。直接六参数 `run_once` 的兼容行为不在此自动收尾要求内：它仍返回 `JOB_FAILED` 并保留 work。

#### Scenario: 失败轮产物
- **WHEN** fake executor 返回失败
- **THEN** 该 source/cycle 无 `DONE`、状态链未动、存在唯一含该轮 job ID 的合并日志、work 目录不存在

### Requirement: 保留窗口与安全清理
清理 MUST 保留最新成功 cycle 往前 14 天的 `output` source 目录，窗口外目录与对应失败日志删除；每个删除目标（含成功轮 work 删除）MUST 先经 `realpath` 确认位于 yd 自己的根内，否则拒绝删除。node-22 本地 `cron.lock_path` 及其专属 `run/` 目录 MUST 位于全部 retention、work、staging 与 residue 清理根之外，并是所有清理的显式禁区；任何删除候选中的 symlink 都不得被跟随到该哨兵。

#### Scenario: 14 天窗口
- **WHEN** 模拟根含最新成功 cycle 与一个 15 天前的 source 目录
- **THEN** 窗口外目录被删除，窗口内完整保留

#### Scenario: symlink 越界拒删
- **WHEN** 待清理路径是指向 yd 根之外的 symlink
- **THEN** 清理拒绝删除该目标并报告
