## MODIFIED Requirements

### Requirement: run 的 stderr 行格式
`yd-producer run` 写到 stderr 的每一个物理行 MUST 以 UTC 时间 `YYYY-MM-DDTHH:MM:SSZ` 加一个空格开头。取得锁并完成 `run_sources` 后，MUST 按 `ifs`、`gfs` 顺序为每条结果各写一行 `<时间> <标签>：<detail>`：`SUCCEEDED` 标「完成」，`STOPPED` 且停因为 `raw_incomplete` 标「等待」，其余结果标「错误」；detail 为空时以结果名代替；detail 含多行时标签只放第一行、其余行只加时间前缀、文本恰出现一次；一个 source 有多条结果时逐条写，先 ifs 全部、再 gfs 全部；全部成功时同样写这些行。`run` 其它失败路径的文案 MUST 不变，只在每个物理行（含 `source=`/`phase=`/`job=` 行与每条 note）加同一时间前缀；`_StatesGuardFailed`、`RunSourcesError` 与 run 下 `OSError` 的 handler MUST 把该异常对象自身的 `__notes__` 每条恰输出一次，位于「错误：」块之内（与 `RunError`/`ExecutorError`/兜底异常的既有做法一致）；`RunSourcesError` 的聚合文本仍 MUST 整体输出一次，前缀逐行加在该文本上，内容与各项出现次数不变；锁竞争跳过 MUST 不写任何行。退出码 MUST 与既有约定完全一致，不受标签影响。`prepare`、`init`、参数解析之前的 `DATABASE_URL` 守卫与 argparse 用法错误的输出 MUST NOT 带时间前缀。

#### Scenario: 发布与等待分行
- **WHEN** 一次 tick 中 ifs 结果为 `SUCCEEDED`，gfs 结果为停因 `raw_incomplete` 的 `STOPPED`
- **THEN** 退出码为 `3`，stderr 恰两行，依次匹配 `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z 完成：ifs: ` 与 `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z 等待：gfs: `

#### Scenario: 全部成功也留痕
- **WHEN** 两源结果均为 `SUCCEEDED`
- **THEN** 退出码为 `0`，stderr 恰两行，均为带时间前缀的「完成：」行

#### Scenario: 失败结果不得标为完成或等待
- **WHEN** 结果为其它停因的 `STOPPED`、`JOB_FAILED` 或 `SUCCEEDED_CLEANUP_PENDING`
- **THEN** 对应行标签为「错误」，退出码为 `3`

#### Scenario: 聚合错误逐行加前缀且不丢 note
- **WHEN** `run_sources` 抛出带 notes 的 `RunSourcesError`
- **THEN** 退出码为 `3`，stderr 每行都带时间前缀，每个底层错误与每条 note 恰出现一次，不含 traceback

#### Scenario: 非 run 输出不变
- **WHEN** `prepare` 或 `init` 失败，或设置了 `DATABASE_URL`
- **THEN** stderr 不含时间前缀，与改动前逐字节一致

#### Scenario: 多行 detail 与多条结果
- **WHEN** ifs 首条结果 detail 为 `startup cleanup: …` 换行接 `ifs: 一轮成功发布完成（…）`，其后 ifs 还有一条停因 `raw_incomplete` 的 `STOPPED`，gfs 为停因 `raw_incomplete` 的 `STOPPED`
- **THEN** stderr 依次为：带前缀的「完成：startup cleanup: …」、带前缀但无标签的「ifs: 一轮成功发布完成（…）」、带前缀的「等待：ifs: …」、带前缀的「等待：gfs: …」；审计文本恰出现一次；退出码为 `3`

#### Scenario: 外层异常的 note 不丢
- **WHEN** 运行期间锁 identity 漂移，`runlock` 把漂移 note 附到 `action()` 抛出的 `_StatesGuardFailed`、`RunSourcesError` 或 `OSError` 上
- **THEN** stderr 在「错误：」行之后含带时间前缀的该 note 行，恰出现一次，不含 traceback；退出码分别为 `1`、`3`、`3`
