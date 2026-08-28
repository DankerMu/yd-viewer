# Review Failure Retro

PR: #65, current head SHA: `b11138e34e7b5af7b172110b680907b093d8d57a`
Issue: #7 / OpenSpec 任务 3.2（raw 只读复制与临时 raw manifest）
Gate: three-round hard gate（round 3 not-clean，`repeats prior class: yes` ×8）
Failure classes: self-confirming-oracle, vocabulary-escape, silent-output-corruption, path-containment,
compound-gate-single-component, gate-audit-completeness, contract-completeness, doc-accuracy,
rollback-ledger-bypass

Rounds affected:
- Round 1 | `6482ff2` | not-clean | 8 verified | major | 报告 `.workplans/pr-65/review/{correctness,integration,security-perf,test-evidence,spec-compliance,invariant-state}.md`，裁决 `verify-{A,B,C,D}-*.md`
- Round 2 | `d5d5206` | not-clean | 17 verified | major | 报告 `r2-{correctness-full,rollback-delta,test-evidence,spec-compliance}.md`，裁决 `r2-verify-{A,B,C,D}-*.md`
- Round 3 | `b11138e` | not-clean | 12 verified | major | 报告 `r3-{correctness-full,class-closure,evidence-spec}.md`，裁决 `r3-verify-{A,B,C,D}-*.md`

Failure shape: **depth**

`converging` 不可选，且不是我的判断——CLI 机械否决：存在类重复，且 round 3 含 major。
`breadth` 不成立：发现并非散布在无共同根因的独立面上。三名 round-3 reviewer 与三名 verifier 在**互不通信**的情况下收敛到同一条根因（见下）。
`noise` 不成立：round 3 的 16 条候选里 15 条 CONFIRMED、1 条 PLAUSIBLE，0 条 REFUTED。

## Depth evidence (required when shape = depth)

- Invariant: `stage_raw` 对外只以 `{ConfigError, RawStagingError(九项闭合词表)}` 两类失败终止，且任何失败都不留下部分产物（fixture `tasks.md:659`「失败**一律**抛 `RawStagingError`」+ governing invariant 第三合取项「不留任何部分产物」；无前提 MUST）
- Recurring findings:
  - A1 `UnicodeEncodeError`（孤代理）绕过 rollback，留下 3 份副本 + 0 字节 manifest (round 1)
  - A-1 NUL 路径让 `rollback` 自身抛裸 `ValueError`，替换正在构造的 `RawStagingError`，穿透全部三层 handler (round 2)
  - A-2 不可哈希 `accumulation_type` 让裸 `TypeError` 在 try 块之前逃逸 (round 2 / sibling surface `_check_accumulation`)
  - A-1 `OverflowError`（`int(1e400)`）与 `RecursionError`（深嵌套 JSON）在 `stage_raw:931` 逃逸，零写入但逃出词表 (round 3 / sibling surface `_load_source_manifest`)

**第二条并行复发的不变量**（form 只允许一条 `Invariant:` 行，故在此正文具名，不省略）：
「产出 `raw-manifest.json` 的每个值，其『承接自源』与『由 yd 自算』两种实现必须可区分」
（fixture Required evidence：每条 Regression row MUST 由一个能证伪它的变异体验证过）。
- round 1：`local_key` 一条腿（`entry_payload` 把被测值喂进合成源 manifest）
- round 2：另六条腿（`idx_selector`/entry `cycle_time`/`valid_time`/manifest `cycle_time`/`source_id`/`first_+last_forecast_hour`）
- round 3：再两条腿（`expected_checksum`/`expected_size_bytes`；metadata「仅含」半边）

## Why Phase 5/6 did not close it

- Fixture scope gap: **no** —— fixture 的 Required evidence 段已经写死了正确的谓词；它从未被作为一次清扫兑现。缺的不是规范文本。
- Fix prompt too narrow: **yes** —— 这是主因。三轮修复简报都是**按条目**组织的（F1…F9），即使 round 2 的简报明确要求「类闭合论证」，实现者交付的仍是「我搜了这些点位、都修了」。搜索的产物无法证明类已闭合。
- Reviewer finding contract vague/inconsistent: **no** —— 三轮的 reviewer/verifier 质量逐轮上升；round 3 的 verifier 自建大小写敏感 APFS 卷来公平模拟 ubuntu，另一位纠正了 reviewer 的绑定锚点，第三位纠正了后果论的 §3.1 支撑。契约本身在起作用。
- Missing regression evidence: **no** —— 变异证据可信且经双向对账（控制变异 13→17 的归因被逐条列名核实）。
- Cause never diagnosed (no red repro before fixes): **partly** —— 每条**具名发现**都有红证，但**类**从未被诊断过。没有任何一轮为「这个类的成员资格判据是什么」建立过红证。
- PR too broad / should split: **no** —— 且按 gates.md，depth 形状**禁止拆分**：每个子 PR 都会继承同一条不变量与同一个修复模式。

## 根因（三名 verifier 独立收敛，逐条引证）

**修复一直是对实例的枚举，而不是对类的闭合谓词。**

- batch A verifier：「修复轮的枚举是按**异常类型**穷举而非按**操作数形态**穷举，天然不完备」——并指出该判据出自修复轮**自己写的辩护词**（`_index_source_entries` docstring 逐字写着「外部 JSON」「在 try 块之前，三层 handler 一条也接不到」），那句话原样适用于它没有覆盖的 `int()` 与 `json.load`。
- batch C verifier：「F4 是被搜刮空了，不是闭合了」。最锋利的证据：`tasks.md:691` 是**一条 MUST 管三个字段**，round 2 偏移了第三个（`manifest_uri`）、漏了前两个——同一条 MUST 的成员被切在了清扫边界两侧。并给出至今无人写下的谓词：**凡测试对产出 manifest 断言的每一个值，源侧对应值 MUST 被偏移使承接与自算发散，且每处发散各由一个变红的变异体证明。**
- batch D verifier：D-3 与 D-4 是**方向相反、同源**的错误——闸门按**语句形态**（都是 `sorted`）而非按语义合并，于是既漏登记活闸门、又把活闸门错记成死腿；而 88/20/108 整组数字全部架在那条合并规则上。

三个类的失败形状一致：**判据取在了表象轴（异常类型 / 字段清单 / 语句形态）上，而不是取在语义轴上。** 因此每轮都能修掉被点名的实例，每轮又都留下同类的新实例。

## Next corrective action

**Refactor/redesign（depth 默认动作），三项，按此顺序：**

1. **准入期失败收口改为结构性**：不再在每个消费点枚举异常类型。把 `stage_raw` 的**整个准入段**（从形参守卫到序列化）纳入一个收口器，任何非 `{ConfigError, RawStagingError}` 的异常一律转成带 kind 的 `RawStagingError`。这使 vocabulary-escape 类的**未来实例在结构上不可能**，而不是恰好不存在。写入段已由 round 2 的三层 handler 覆盖，本项补的是它前面那一段。
   - 判别器要求：不是「为 `OverflowError` 和 `RecursionError` 各加一个用例」，而是一个**参数化的逃逸探针**，对准入段每个外部值消费点注入一个非词表异常并断言收口。加用例只会重演本轮。

2. **A-2 两条独立缺陷各自修**（verifier 实测证明可分离，只补一个会直接重演类闭合失败）：`forecast_hour` 的形态闸门（拒绝 `int()` 有损归一）**与** `_index_source_entries` 的 injectivity 守卫（拒绝重复键静默后写覆盖）。这是本轮唯一产生**静默错误输出**的缺陷——影子 entry 可劫持真实 `(lead, variable)` 的 `remote_url` 与六键而 staging 正常成功。

3. **oracle 类按谓词清扫一次**：采用 batch C verifier 写下的谓词，对产出 manifest 的**全部**断言值做一次穷尽偏移，并把该谓词写进 fixture 的 Required evidence 段作为**可复核的闭合条件**——这样下一轮不必再靠人去猜是否还有第 N+1 条腿。

**同时降级为记录而非修复**（避免把预算花在不改变结论的条目上）：D-3/D-4/D-1/D-2 是审计与计数的准确性，改的是交付物不是代码；B-4 只改一句措辞。这些随修复轮一并落，但不单独消耗复审预算。

**DEFER 路由**（轮末必须具名，其中三条需新开 issue）：
- B-1（inode 半边在 CI 上零判别力）—— verifier 判 DEFER 的理由是**不存在无特权的无条件判别器**：seam 级用例在大小写敏感卷上必然自跳过。修法属 CI/fixture 范围（加一个大小写不敏感的 CI job，或把该行钉为 darwin-only 并记录平台缺口）。需新 issue。
- B-3（TOCTOU）—— 判 PLAUSIBLE/DEFER，路由为 **#71 的动态孪生**，同一修复族，记在 #71 上而非另开。注意 verifier 的纠正：round 2 的 `resolve()` 已关掉静态半边，**净残留比修复前更窄**，不应按「本轮新引入」升级。
- D-5（非 Mapping `idx_selectors` 静默丢弃）—— 需先做 fixture 决策，与 #75 同路由。需新 issue 或并入 #75。
- R3-06（gate-audit 的「157 节点」未随重修同步）—— 我的分批失误使它未获裁决；它是 D-3/D-4 的结构前提，随审计修复一并处理。

**预算**：depth 形状 post-gate 预算 = **1 轮综合复审**（round 4）。round 4 若仍不干净，须携更强动作重回门限；round 5 不干净即触及**终局上限**（5 轮），届时唯一出路是 PR split / descope / 用户裁决。

**对预授权 merge 的影响**：用户预授权的是「终审干净 + CI 通过 + 证据与中文小结已发」之后的自动合并。门限不因预授权而跳过，当前 head 也远未满足该前提。合并授权仍然有效，但要等到 round 4 之后的终审干净。
