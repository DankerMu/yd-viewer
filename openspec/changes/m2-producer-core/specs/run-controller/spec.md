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

### Requirement: 未提交残留清理重跑
无 `DONE(T)` 却存在比 T 更晚的状态文件或 T 的 source 目录半成品时，MUST 判为上次发布中断残留：保留 T 状态，删除残留后重跑 T。

#### Scenario: 崩溃残留恢复
- **WHEN** 模拟根中存在 T+12 状态与只含 DAT 无 `DONE` 的 T 目录
- **THEN** run 删除该 T+12 状态与半成品目录，以 T 状态重新组装本轮

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
- **THEN** GFS 正常发布并继续追赶，IFS 本次停止；IFS 失败留一份合并日志 `logs/ifs/<T>.log`，其 work 被删除

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
作业失败时 MUST 不写 `DONE`、不推进状态链；MUST 把完整 stdout/stderr、命令、job ID、起止时间与退出码合成一份 `logs/<source>/<T>.log`；MUST 删除整个 scratch work；下次 run 从干净 work 对该 cycle 重试。MUST NOT 维护失败计数、退避或 `status.json`。

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
