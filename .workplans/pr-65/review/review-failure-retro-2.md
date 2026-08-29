# Review Failure Retro（第二次 gate entry，更新版）

PR: #65, current head SHA: `12ebbc994bcb811b6b8211a207c8e9be5d462a33`
Issue: #7 / OpenSpec 任务 3.2
Gate: post-gate-budget（round 3 的 depth 复盘预算 1 轮，round 4 用掉且不干净）
Failure classes: silent-output-corruption, mechanism-discriminator-insufficiency,
gate-audit-completeness, self-confirming-oracle, compound-gate-single-component, doc-accuracy

Rounds affected:
- Round 1 | `6482ff2` | not-clean | 8 verified | major
- Round 2 | `d5d5206` | not-clean | 17 verified | major
- Round 3 | `b11138e` | not-clean | 12 verified | major | 触发三轮门限，depth 复盘 `review-failure-retro.md`
- Round 4 | `12ebbc9` | not-clean | 2 verified（Phase 4.5 **有意收窄**到两条决定性候选，见下）| major | 报告 `r4-{correctness-full,mechanisms,evidence-spec}.md`，裁决 `r4-verify-decisive.md`

**关于 round 4 的 `verified findings: 2`**：本轮共 16 条候选，我只对两条决定性候选跑了裁决，因为预算已尽、下一步是用户决策而非继续修。该数字是**已裁决数**，不是本轮发现数——不得据此读成收敛趋势。其余候选三名 reviewer 独立收敛、部分我自行核实，未裁决即未进计数。

Failure shape: **depth**（第二次）

## Depth evidence (required when shape = depth)

- Invariant: 凡本仓写下的「每一条/全部/恰好」式声明，MUST 配一个**能够执行该声明**的判别器（`openspec/project-profile.md`「声明必须配判别器」）。四轮里失败的一直是这条：判别器被取在表象轴上（异常类型 / 字段清单 / 语句形态 / 语句槽位），而声明立在语义轴上。
- Recurring findings:
  - 承接/自算不可区分：round 1 一条腿、round 2 六条、round 3 两条 —— 每次独立清扫都产出新实例 (rounds 1-3)
  - 闸门审计「无第三桶」完整性声明失实：round 3 五条腿在两桶之外 (round 3)
  - 同一声明在 round 4 **再次**失实，且违反者是本轮自己新增的四条闸门腿 (round 4 / sibling surface: 审计交付物)
  - 准入段地板的结构判别器只钉 `body[0]`/`body[1]`，M1 把语句移到 `body[2]` 即违反 MUST 而套件全绿，且语句**自我退出**探针参数集（21→20），无人断言该损失 (round 4 / sibling surface: 本轮为闭合前一类而新建的机制内部)

Split rebuttal (required from the second gate entry when shape is depth or noise):
- 不可独立拆分，理由是证据而非措辞：

1. **递归不变量禁止拆分**（gates.md depth 明文）：反复失败的是「声明必须配可执行的判别器」这条**横切**纪律，不是某个功能面。它不落在任何一条模块边界上——round 4 的四条实例分别落在 `_carried_metadata`（承接）、`stage_raw` 的地板（异常收口）、审计交付物（证据登记）、以及 fixture 文本（规范）四个互不相邻的面上。任何切法产生的每个子 PR 都会**同时**继承这条纪律与它的失败模式，各自再烧一遍轮次预算。
2. **issue #7 的最小可合并切片就是当前范围**：验收标准两条——「复制前后源内容与 mtime 不变」「manifest 字段齐全且路径全部位于 `work/raw/` 之下」。复制与 manifest 生成是同一个接缝 `stage_raw` 的两半，产出 manifest 的 entry 必须引用已落地的副本，切开任何一半都不独立可合并。
3. **残留量不支持拆分**：Invariant Matrix 21 行经两轮独立重走全部关闭；round 4 的运行期缺陷只有一条，且其修法已由 verifier 原型实跑（约 10 行，799 条现有用例一条不改全过）。拆分的成本远高于收尾成本。

## Why the round-3 corrective action did not close it

- Fixture scope gap: **yes（本轮新增判断，与 round 3 的结论相反）** —— round 3 我判「fixture 已写死正确谓词，缺的不是规范文本」。round 4 证伪了这条：我为 Item 1 写进 fixture 的判别器（「断言地板确实是 `body[0]`、写入段是 `body[1]`」）**在证明上无法执行同一 bullet 里的 MUST**（M1 违反 MUST 而满足判别器）；我为 Item 3 写的谓词把定义域取成「**被断言的**值」，于是未被断言的承接值结构性落在谓词之外，且交付当天就有两个实例。**规范侧的判别器本身取在了表象轴上——我复制了我要求别人别犯的错误。**
- Fix prompt too narrow: **no（本轮已纠正）** —— round 4 的简报要机制不要论证，实现者据此交付了真正的结构收口（地板的加法方向自动入列、handler 不可抛、两分量各自可判，均经实测），并**如实自报** Item 2「有界非闭合」、Item 3「枚举仍是人工的」。诚实的强度分级是有效的。
- Reviewer finding contract vague: **no** —— round 4 三名 reviewer 独立收敛到同两条机制边界，且 verifier 首次**把两个修法都原型实跑**。
- Missing regression evidence: **no** —— 数字经独立复核（+37 用例、控制变异首尾一致、红证 13/765、审计节点数 172/191 经独立重实现的脚本复现）。
- Cause never diagnosed: **no** —— 根因已诊断且本轮被进一步精确化到「判别器的**定义域/范围**取错」，而不只是「取在表象轴」。
- PR too broad / should split: **no** —— 见 Split rebuttal。

## Next corrective action（比上一轮更强：不再要求「设计出闭合机制」，而是采用已被实测证明的闭合形式）

上一轮的纠正动作是「refactor/redesign，让未来实例结构上不可能」。它在两处产出了真机制、在两处产出了取错定义域的判别器。本轮的动作因此更强也更窄：**采用 verifier 已原型并实跑过的两个闭合形式，不重新设计；并把同一把尺子回灌到规范与审计两个交付物上。**

1. **V-1 运行期闸门**：`_carried_metadata` 增加 `cycle_time == cycle`、`valid_time == cycle + lead` 一致性核对，归 `source-manifest`。verifier 已实跑：799 条现有用例一条不改全过，复现输入被拒。**注意剔除 reviewer 的错误理由**——「下游 converter 用 `valid_time` 定位时间」无 §3.1 支撑，可锚定的危害是产物自相矛盾本身。
2. **V-2 范围断言**：钉 `stage_raw` 顶层形状 `["Try","Assign","Try","Return"]`，并把地板 `Try` 体的首尾语句钉到 fixture 具名端点（`verdict.complete` … `os.path.lexists`/`target-exists`）。verifier 已实跑：head 绿、M1 红、尾部收缩变异体红。**MUST NOT 钉 `len(ADMISSION_INJECTION_TARGETS)`**——计数会在每次合法新增时变红，正是复盘所批判的枚举陷阱。
3. **判别器定义域回灌（本轮的真正闭合动作）**：把「定义域取错」这条尺子回灌到我自己写的两处 fixture 文本——Item 3 谓词的定义域由「被断言的值」改为「产出 manifest 的每一个字段」；Item 1 的判别器描述由「body[0]/body[1]」改为「地板 try 体覆盖准入段全部端点」。并补回被我删过头的「序列化 MUST 前置于复制」约束。
4. **审计交付物按同一尺子重扫**：本轮新增的四条闸门腿（`:427`/`:432`/`:171`/`:578`）逐条归桶；`:432` 是复合闸门单分量取信的新实例，需按分量补判别器。
5. 其余已裁决/三方收敛的 minor 一并落：探针死码（`raise` 后两行 + 收口分支缺零写入断言）、影子劫持用例的双闸门依赖与失实 docstring、`grib_short_name` 与复数 `idx_selectors` 入清扫表、`ADMISSION_FALLBACK_KIND` 的 fixture 理由改为「位置轴上的显式例外」而非声称与因果规则一致、`tasks.md:424` 的无支撑 pin 行为断言、`test_rawcopy.py:2291` 的恒真前提断言。

**用户决策已记录**：round 5 由用户明确授权买下（「买最后一轮」）。若 round 5 干净则按预授权合并；若不干净即触及 5 轮终局上限，届时只剩拆分/降范围/用户再裁决。
