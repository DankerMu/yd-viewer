# ADR 0001 后期复议记录（第 20 次起）

- 根决策与早期复议：[0001-lens-rotation-keep.md](0001-lens-rotation-keep.md)
- 回到 [决策](0001-lens-rotation-keep.md#决策) / [复议记录](0001-lens-rotation-keep.md#复议记录)
- 本卷只保存第 20 次起的复议正文；keep/cut 状态以根文件为准。

## 第 20 次复议（issue #134 / PR #140 合并后，2026-09-06）

审计数字：30 行（29 merged、1 terminal），26 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #140 是 `fixture:none` 的 docs-only fixture 修复，`rounds=0`；独立
Sonnet 文档审核是额外证据，不属于代码 PR 的综合 review round。因此本样本不进入 rotation attribution，
也不改变 core/rotated 数字。

本条只把 `none` 桶从 1 个 merged PR 增为 2 个，累计 `gate_net_catch=0`，尚未达到每个 fixture level
至少 8 个样本的 keep/cut 门槛。即使未来达到，也只能裁定 docs-only none 路径是否需要额外审核，不能据此
缩减 compact/expanded/high 代码 PR 的 reviewer 席位。当前 lens-rotation DECIDABLE 完全是前 26 个多轮代码
PR 的既有信号，本 PR 没有后续轮次，既不能支持 rotation，也不能反对 rotation。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；不把
零轮次的 docs-only clean 样本计作“轮换发生但零收益”，也不据此改变任何代码 fixture 的 seat cap。累计
rotated 捕获仍显著，证据不足时默认 keep，符合工作流优先正确性的规则。

复议条件未触发：不存在综合轮、rotated catch、verifier candidate 或已关闭 finding 重报。共享
`m2-producer-core` 仍服务 #135、#136、#137 与 #132，本次继续不 archive。

---

## 第 21 次复议（issue #31 / PR #143 合并后，2026-09-06）

审计数字：31 行（30 merged、1 terminal），26 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #143 只有 Round 1：四席覆盖六个 canonical lens，候选 finding 为 0，
独立 Phase 7 Gap Sweep 同样 clean。它不满足 `rounds >= 2`，所以没有进入 rotation attribution，也没有
改变多轮样本数或 core/rotated 数字。

high 桶现在有 10 个 merged PR、累计 `gate_net_catch=49`。本次零 catch 不满足 keep/cut 的“整个桶总捕获
为零”前提，也不否定 high-tier 覆盖：fixture review、测试先行和三个针对 representation/alias 的可杀变异
已在综合审核前封闭主要缺口，四席随后独立确认了 keyset authority、copy-before-freeze、consumer API 与
证据独立性。这是一个前置证据充分后正常收敛的 clean 样本，不是 reviewer 失效的证明。

本 PR 也没有 free-slot rotation 的机会。Round 1 后无需修复，因而没有后续综合轮；把各 lens 的 seated
次数加一而 catches 不变解释为“轮换零收益”，会把初审席位与后轮轮换混为一谈。与第 19 次复议相同，
该样本只能说明一次完整 high-tier 初审 clean，不能估计某个新 lens 在修复后轮入时的边际收益。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；
不以单轮 clean 缩减 high-tier Round 1，也不把它纳入 rotation 的正反证。累计 rotated 捕获仍显著，证据
不足时默认 keep，符合工作流优先正确性的规则。

复议条件未触发：不存在后续综合轮、rotated catch 或已关闭 finding 重报。共享 `m2-producer-core` 仍承载
#32、#72、#48、#46 及其它后继任务，本次继续不 archive。

---

## 第 22 次复议（issue #135 fixture / PR #144 合并后，2026-09-06）

审计数字：32 行（31 merged、1 terminal），26 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #144 是 `fixture:none` 的 docs-only 前置，`rounds=0`；Phase 0.5
fixture reviewer 的 revise/pass 与 base 漂移后的 fresh review 都不属于代码综合轮。因此本样本不进入
rotation attribution，也不改变 core/rotated 数字。

`none` 桶现在有 3 个 merged PR、累计 `gate_net_catch=0`，尚未达到 8 个样本的 keep/cut 门槛；即使
未来达到，也只用于裁定 docs-only 路径，不可外推到 #135 后继代码 PR 的 high-tier 席位。该代码 PR 仍按
fixture 的 expanded/high 执行完整 review loop。

本 PR 的有效信号是 pre-merge hard gate，而不是 lens rotation：旧 SHA 已全绿且 fixture review pass 后，
`master` 合入 #31，改变同一 `tasks.md` 与 `LocalConfig.slurm` 权威契约。hard gate 正确阻断了旧 base；重新
rebase、语义 reconciliation、fresh review 与新 CI 后才合并。它证明 SHA/base 完整性门禁有价值，但对后轮
free-slot rotation 的正反证均为零。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；
不把零轮次 docs-only 样本计作“轮换发生但零收益”。累计 rotated 捕获仍显著，证据不足时默认 keep。

复议条件未触发：不存在综合轮、rotated catch、verifier candidate 或已关闭 finding 重报。共享
`m2-producer-core` 仍服务 #135、#136、#137 与 #132，本次继续不 archive。

---

## 第 23 次复议（issue #32 / PR #148 合并后，2026-09-06）

审计数字：33 行（32 merged、1 terminal），26 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #148 只有 Round 1：四席覆盖六个 canonical lens，候选 finding 为 0，
独立 Phase 7 Gap Sweep 同样 clean。它不满足 `rounds >= 2`，所以没有进入 rotation attribution，也没有
改变多轮样本数或 core/rotated 数字。

high 桶现在有 11 个 merged PR、累计 `gate_net_catch=49`。本次零 catch 不满足 keep/cut 的“整个桶总捕获
为零”前提，也不支持缩减 high-tier 初审。#148 在综合审核前已用测试先行、动态 1 天边界、装配/错误优先级
矩阵、结构审计与 5 个判别变异闭合三条 loader 域；四席随后独立确认 owner altitude、异常路径、下游兼容与
oracle 独立性。一次完整证据链后的 clean 是正常收敛，不是 reviewer 无价值的证据。

本 PR 没有 free-slot rotation 的机会：Round 1 零 candidate，无 Phase 6 或后续综合轮。把本次各 lens 的 seated
次数增加而 catches 不变解释成“轮换零收益”，会再次把初审席位与修复后轮换混为一谈。它只能说明本次
high-tier 初审与独立终审均 clean，不能估计新 lens 在后轮换入时的边际收益。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；
不以单轮 clean 缩减 high-tier Round 1，也不把它纳入 rotation 的正反证。累计 rotated 捕获仍显著，且证据
不足时默认 keep，符合工作流优先正确性的规则。

复议条件未触发：不存在后续综合轮、rotated catch 或已关闭 finding 重报。共享 `m2-producer-core` 仍承载
#72、#48、#46 及其它后继任务，本次继续不 archive。

---

## 第 24 次复议（issue #72 / PR #150 合并后，2026-09-06）

审计数字：34 行（33 merged、1 terminal），26 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #150 只有 Round 1：四席覆盖六个 canonical lens，候选 finding 为 0，
独立 Phase 7 Gap Sweep 同样 clean。它不满足 `rounds >= 2`，因此不进入 rotation attribution，也不改变
多轮样本数或 core/rotated 数字。

high 桶现在有 12 个 merged PR、累计 `gate_net_catch=49`。本次产品 review 的零 catch 不满足整个桶总捕获
为零的 cut 前提。测试先行、逐 source/path 矩阵、#32/结构错误优先级、消息 token 多重数 oracle 与 6 个
判别变异已在综合审核前闭合 variables 单射性；四席与终审随后独立确认 owner、下游兼容与无静默规范化。
这是前置证据充分后的正常 clean，不支持缩减 high-tier Round 1。

本 PR 的 fixture review Round 1 确实抓到一处显示顺序契约自相矛盾，并在实现前修复、Round 2 pass；该 catch
属于 Phase 0.5 `fixture-review`，不是产品综合 round，也不是 free-slot rotation。它证明 docs-first fixture
复核有独立价值，但不能被挪入 core/rotated 任一侧为轮换策略加分。产品 Round 1 后无 Phase 6 或后续综合轮，
因此本样本没有真实 rotation 机会。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；
不以单轮 clean 缩减 high-tier 初审，也不把 fixture-review catch 错归给后轮轮换。累计 rotated 捕获仍显著，
且证据不足时默认 keep，符合工作流优先正确性的规则。

复议条件未触发：不存在后续综合轮、rotated catch 或已关闭 finding 重报。共享 `m2-producer-core` 仍承载
#48、#46 及其它后继任务，本次继续不 archive。

---

## 第 25 次复议（issue #135 / PR #151 合并后，2026-09-06）

审计数字：35 行（34 merged、1 terminal），27 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #151 有两个综合轮，但 Round 2 不是 finding 修复后的 free-slot rotation：
Round 1 与首次 Phase 7 在旧 head 上均 clean，随后 #72/PR #150 合入并改动同一 `config.py`、
`test_config.py` 与共享 fixture，pre-merge hard gate 要求在新 base 上重新验证。Round 2 因此只用
`invariant-state` 与 `test-evidence+spec-compliance` pinned core 对当前合并树复核，零 candidate、零 catch；
它增加一个多轮样本，但不改变 core/rotated 捕获数。

high 桶现在有 13 个 merged PR、累计 `gate_net_catch=49`。本次零 catch 不满足“整个桶总捕获为零”的
cut 前提，也不支持缩减 high-tier Round 1。实现进入综合审核前已经由严格配置矩阵、兼容构造测试和
calibrated mutation corpus 封住主要缺口；真正有价值的过程信号是 base/tip 门禁：旧 review 与 CI 全绿时，
同面上游仍可能改变 merge result，故必须拒绝 stale SHA 并重跑语义并集、Phase 2、mutation 与 review。

本样本没有 rotated-in lens，不能拿后轮零 catch 评价 rotation 的收益。累计 rotated 捕获仍为 96，接近 core
的 125；既有样本继续证明，在 major/repeat 信号出现时保留一个互补 free slot 能补 pinned core 的盲区。
一次由 base drift 强制的 pinned-core 复核不构成反证。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；
不以本次零 finding 缩减 high-tier Round 1，也不把 base-drift reconciliation 冒充一次 rotation。证据不足时
默认 keep，符合工作流优先正确性的规则。

复议条件未触发：Round 2 无 rotated seat、无 catch、无已关闭 finding 重报；两轮均无 candidate，故不存在
verifier disposition 或 residual deferral。共享 `m2-producer-core` 仍服务 #136、#137 与 #132，本次继续不
archive。

---

## 第 26 次复议（issue #48 / PR #153 合并后，2026-09-06）

审计数字：36 行（35 merged、1 terminal），28 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #153 有两个综合轮，但 Round 2 与上一条 #135 样本同形：Round 1 与首次
Phase 7 在旧 head 上均 clean，随后 master 合入 #135/PR #151，在同一 `LocalConfig` / `test_config.py` /
共享 fixture 面新增 timeout policy 叶。Pre-merge hard gate 拒绝沿用旧 28-leaf review 与 CI；Round 2 是
rebase 后由 `invariant-state` 和 `test-evidence+spec-compliance` 两个 pinned-core 席位完成的 current-merge-tree
reconciliation，零 candidate、零 catch。因此该 PR 增加一个多轮样本，但不改变 core/rotated 捕获数。

本样本没有发生 free-slot rotation，不能把后轮零 catch 解释为轮换无收益。两席都已在 Round 1 出现，且后轮
任务是核对 #135 的资源/policy 分离、A 缺席默认 60 / B 显式 37、19+10=29 叶完整性及冲突解决没有丢失
upstream 测试。它能证明 pinned core 在 base drift 后给出了 clean closure，不能估计未轮入 lens 的边际收益。
这与第 25 次复议的判据一致。

本 PR 的有效过程信号再次来自机械方法而非 catch 数量：hard gate 在旧 SHA 四项 CI 全绿、四席与 Phase 7
均 clean 的情况下仍发现 PR 已与 master 冲突；随后 task 被重新开放，fixture 先升级为 29 叶，再 rebase、重跑
Phase 2 和 calibrated 29/29 provenance mutation，最后才重新勾选。若只看 `gate_net_catch=0`，这条避免把
新增 timeout 叶漏出完整 oracle 的关键收益完全不可见。它进一步说明当前 core/rotated 数字不记录 base/tip
integrity、schema reconciliation、mutation 方法或 clean-closure 价值，不能据此自动缩减阵容。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；
不把本次无 rotation 的 pinned-core reconciliation 计作轮换零收益，也不因 high 桶新增一个零 catch 样本而
缩减 Round 1。High 桶累计仍有 49 条净捕获，rotated 累计仍为 96；证据不足时默认 keep，符合工作流优先
正确性的规则。

复议条件未触发：Round 2 无 rotated seat、无 finding，也没有重报已关闭项；两轮均零 candidate，故无
verifier disposition 或 residual deferral。共享 `m2-producer-core` 仍服务 #46 等后继任务，本次继续不
archive。

---

## 第 27 次复议（issue #136 / PR #155 docs-only fixture 合并后，2026-09-06）

审计数字：37 行（36 merged、1 terminal），仍为 28 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #155 是 docs-only fixture 前置，问责口径为 `fixture=none`、`rounds=0`；
其 Sonnet fixture review 不是产品综合 round，也没有 candidate、verifier 或 catch。因此本次 append 没有
增加 rotation attribution 的分母或分子，不能从完全相同的样本重新推导一个相反决定。

本 PR 唯一新增过程信号是 pre-merge base/tip 门禁再次生效：#48/PR #153 修改同一共享 `tasks.md` 后，旧
head 的 PASS 与 CI 被作废；rebase 后才核对 task 1.9 与 #136 task 9.3、D12/D16/#59、#132 14.2 的语义
并集。随后仅问责文件前进的 master 不触发无意义 rebase，当前 merge-result CI 仍覆盖最终 base。这个信号
属于证据新鲜度与变更面分类，不是 reviewer lens 的边际收益样本。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审。
Rotated-in lenses 累计仍贡献 96 条 later-round catch，对 core 的 125 条并非可忽略；本次没有新 attribution，
无依据缩减或恢复固定 Round 1 阵容。证据不足时默认 keep，符合工作流优先正确性的规则。

复议条件未触发：本 PR rounds=0，故无 rotated seat、finding 重报、verifier disposition 或 residual deferral。
Issue #136 保持 OPEN，后续代码 PR 才进入 expanded/high 综合审核；共享 `m2-producer-core` 继续服务
#136、#137 与 #132，本次继续不 archive。

---

## 第 28 次复议（issue #46 / PR #158 合并后，2026-09-06）

审计数字：38 行（37 merged、1 terminal），仍为 28 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #158 只有 Round 1：expanded/medium 三席
`correctness`、`test-evidence+spec-compliance`、`invariant-state` 均零 in-scope candidate，Phase 7 产品
Gap Sweep CLEAN。它不满足 `rounds >= 2`，所以不进入 rotation attribution，也不改变 core/rotated 数字。

Expanded 桶现在有 17 个 merged PR、累计 `gate_net_catch=325`，远非整个桶零捕获的 cut 条件。本次单轮
clean 也不是审核失效：综合审核前，直接 metadata AST、10/10 kw-only + 10/10 frozen + 1/1 TypeError
confounder 已封闭主要 oracle 风险；三席随后独立确认 10-class completeness、删除行为等价或更强、Python
下界和 userspace non-goal。把“前置证据充分后未找到第二个缺陷”解释为应缩减 expanded seats，没有依据。

本 PR 也没有 free-slot rotation 机会。Phase 7 发现的是两个未发布、gitignored evidence 草稿的计数笔误，
随后由新鲜 Sonnet 复核修正为 90 个顶层测试、89 个 sibling、106 个顶层函数；它不是产品综合 finding、
没有改变 tracked SHA，也不计 `gate_net_catch`。Checkpoint tracker 的既存同类测试 hardening 经 issue-scribe
核实后路由到 #159，同样不属于本 PR residual deferral。这两项说明终审与 out-of-scope 路由有价值，但不能
挪进 later-round core/rotated 归因。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；
不以一个没有后轮的 clean PR 评价 rotation，也不以它缩减 expanded Round 1。Rotated 累计 96 条 later catch
仍不可忽略，且证据不足时默认 keep，符合工作流优先正确性的规则。

复议条件未触发：不存在后续综合轮、rotated catch 或已关闭 finding 重报；Round 1 零 candidate，故无
verifier disposition 或 residual deferral。共享 `m2-producer-core` 仍服务 #136/#132 等后继任务，本次继续
不 archive。

---

## 第 29 次复议（issue #136 / PR #161 产品实现合并后，2026-09-06）

审计数字：39 行（38 merged、1 terminal），仍为 28 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #161 只有 Round 1：四席 `correctness`、`invariant-state`、
`test-evidence+spec-compliance`、`security-perf+integration` 均零 candidate，Phase 7 fresh Gap Sweep
同样 CLEAN。它不满足 `rounds >= 2`，所以不进入 rotation attribution，也没有改变 core/rotated 数字。

High 桶现在有 15 个 merged PR、累计 `gate_net_catch=49`，远非整个桶零捕获的 cut 条件。本次实现进入
综合审核前已由 execution-time red、61-case 聚焦矩阵、23/23 calibrated mutation、完整 Phase 2 和 exact-head
CI 封闭主要风险；四席与终审随后独立核对 same-object authority、foreign pre-read conflict、bounded no-follow
current bytes、唯一 size owner、失败原子性与既有 controller identity consumer。一次证据充分后的 clean 是正常
收敛，不支持缩减 high-tier Round 1。

本 PR 没有 free-slot rotation 机会：Round 1 零 finding，故没有修复和后续综合轮。把本次各 lens 的 seated 次数
增加而 catches 不变解释成“轮换零收益”，会再次把初审席位与修复后轮换混为一谈。它只能证明 #136 的
expanded/high authority seam 在单轮综合审核与独立终审均 clean，不能估计新 lens 在后轮换入时的边际收益。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；
不以单轮 clean 缩减 high-tier 初审，也不把它纳入 rotation 的正反证。累计 rotated 捕获仍显著，且证据不足时
默认 keep，符合工作流优先正确性的规则。

复议条件未触发：不存在后续综合轮、rotated catch、已关闭 finding 重报或 residual deferral。Issue #136 已由
PR #161 关闭；共享 `m2-producer-core` 仍服务 #137 与 #132，本次继续不 archive。

---

## 第 30 次复议（issue #137 / PR #163 docs-only fixture 合并后，2026-09-06）

审计数字：40 行（39 merged、1 terminal），仍为 28 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #163 是 `fixture=none`、`rounds=0` 的 docs-only implementation fixture；
正式 Sonnet Phase 0.5 fixture review 不计产品综合 round。因此本样本不进入 rotation attribution，也不改变
core/rotated 数字。

None 桶由 4 个增至 5 个 merged PR，累计 `gate_net_catch=0`，仍低于每个 fixture level 至少 8 个样本的
keep/cut 门槛；即使未来达到，也只用于裁定 docs-only none 路径，不能外推到 #137 后续 expanded/medium 产品
review。产品分级上调的原因是公开异常文本格式由 #132 CLI stderr 消费；docs-only 问责 token 与后续产品 fixture
是两件事，不能混用。

本 PR 的有效信号来自 fixture review 与 evidence hygiene：正式 reviewer 一次完整 verdict 即 pass，三次早期
`ECONNRESET` 均在报告前终止，未被误计为审核 round 或结果；PR body 的错误完整 SHA 也在发布前由 local/remote
核对抓到并修正。它们是传输与证据新鲜度门禁的价值，不是 free-slot rotation 收益样本。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；不把
零轮次 docs-only clean 样本计作“轮换发生但零收益”，也不据此缩减后续 expanded 产品席位。累计 rotated
后轮捕获仍显著，证据不足时默认 keep。

复议条件未触发：不存在产品综合轮、rotated catch、verifier disposition、已关闭 finding 重报或 residual
deferral。Issue #137 保持 OPEN，task 14.5 待严格两文件产品 PR 完成；共享 `m2-producer-core` 继续服务
#137/#132，本次不 archive。

---

## 第 31 次复议（issue #67 / PR #166 docs-only fixture 合并后，2026-09-06）

审计数字：41 行（40 merged、1 terminal），仍为 28 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #166 是 `fixture=none`、`rounds=0` 的 docs-only implementation fixture；
正式 fixture reviewer 的 pass 与补充核对不计产品综合 round。因此本样本不进入 rotation attribution，也不改变
core/rotated 数字。

None 桶由 5 个增至 6 个 merged PR，累计 `gate_net_catch=0`，仍低于每个 fixture level 至少 8 个样本的
keep/cut 门槛。该结果只能描述 docs-only 前置，不能外推到 #67 后续 `expanded/high` 产品 PR；后者仍须四席
综合审核、候选 verifier gate 与独立 Gap Sweep。

本 PR 的有效信号来自 fixture owner 与机械闸门：初稿独立 change 因 stage-anchor 不成立而在 tracking 前迁入
已有 `m2-producer-core/state-tools`，fresh reviewer 对最终形态重新 pass；reviewer 又指出 `parse` 体内 16 条
`raise` 的既有 AST 闭合，fixture 随即固定 bytes-like 上界不能新增第二个 size-limit `raise`。这些是文档/机械
oracle 的收益，不是后轮 lens rotation 的收益样本。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；不把
零轮次 docs-only clean 样本计作“轮换发生但零收益”，也不据此缩减后续 high 产品席位。累计 rotated 后轮捕获
仍显著，证据不足时默认 keep，符合工作流优先正确性的规则。

复议条件未触发：不存在产品综合轮、rotated catch、verifier disposition、已关闭 finding 重报或 residual
deferral。Issue #67 保持 OPEN；#54/#66/#68/#70 保持 CLOSED；共享 `m2-producer-core` 继续服务
#67/#137/#132，本次不 archive。

---

## 第 32 次复议（issue #137 / PR #165 产品实现合并后，2026-09-06）

审计数字：42 行（41 merged、1 terminal），29 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #165 有两个综合轮，但 Round 2 是 #67 docs-only base drift 触发的 current-merge
reconciliation，不是 finding 修复后的 free-slot rotation。Round 1 三席与首次 Phase 7 在旧 head clean；rebase 后
Round 2 仅保留 Round 1 已出现的 `correctness`、`test-evidence+spec-compliance` 两席，零 candidate；current-head
Phase 7 同样 CLEAN。因此本 PR 增加一个多轮样本，却不改变 core/rotated 捕获数。

Expanded 桶由 17 增至 18 个 merged PR，累计 `gate_net_catch=325`，显然不满足整个桶零捕获的 cut 条件。本次
零 catch 也不能解释为 expanded reviewer 无价值：进入审核前，test-body red 与 9/9 mutation 已封住旧摘要、漏项、
首条、重复、source mapping 顺序及 note sort/set 等主要反例；两轮 reviewer 与两次 Gap Sweep随后独立确认 legacy
文本、source/note 双层顺序、exception identity/cause/snapshot 与 #132 one-print consumer。前置证据充分后的 clean
是正常收敛。

本 PR 的 Round 2 没有发生 rotation，不能拿它评价 free-slot 边际收益。它的价值在 base/tip integrity：#67 只改
state docs，#137/#132 fixture 与产品 hashes 全部不变，hard gate 仍要求重建 current merge-result Phase 2/review/CI；
随后另一次 branch-tip gate 又抓到外部并发 checkout 把本 worktree 切到无关分支，阻断了在错误 local HEAD 上发布/
合并。两次阻断都是机械完整性收益，不进入 `catches`，也与 reviewer lens 编制无关。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；不把
base-drift pinned reconciliation 当作“发生轮换但零收益”，也不以一次零 finding 缩减 expanded Round 1。累计
rotated 后轮捕获仍显著，证据不足时默认 keep。

复议条件未触发：Round 2 无 rotated seat、无 finding，也没有已关闭项重报或 residual deferral。Issue #137 已由
PR #165 关闭；#132 保持 OPEN，shared `m2-producer-core` 继续服务 #67/#132，本次不 archive。

---

## 第 33 次复议（issue #67 / PR #168 产品实现合并后，2026-09-06）

审计数字：43 行（42 merged、1 terminal），30 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #168 有两个综合轮：Round 1 四席抓到 1 条经独立 verifier 确认的
`test-evidence` minor/FIX_NOW；Round 2 只保留 Round 1 已出现的 `invariant-state+security-perf` 与
`test-evidence+spec-compliance` pinned core，双重确认 finding CLOSED 且零新 candidate。因此本 PR 增加一个
多轮样本，但唯一 catch 在 Round 1，不改变 later-round core/rotated 数字。

High 桶由 15 增至 16 个 merged PR，累计 `gate_net_catch` 由 49 增至 50，显然不满足整个桶零捕获的 cut
条件。本次 catch 还有明确增量价值：生产实现本身正确，但 runtime memoryview 用例被 post-copy guard 的同一
`ValueError` 掩盖，旧 AST 又只钉 guard-before-copy，故精确 `source.nbytes -> len(source)` mutant 可绿着恢复
超限副本分配。Round 1 test-evidence 抓到后，verifier 以 T1/T2/T3 确认，修复用精确 AST true-branch pin 杀死
mutant。这是 high 初审 test-evidence 席位的实证收益。

它仍不是 free-slot rotation 的正反样本。Round 2 的两席全部来自 Round 1，且因前轮最高仅 minor、无 failure-class
repeat，按成本规则没有轮入自由席；后轮 clean 证明修复闭合，不能用于估计“若轮入新 lens 会不会多抓”。本 PR
同时再次说明方法与 lens 名称不能混为一谈：真正关闭 finding 的是精确 mutant + AST 判别器，catch 归属只说明
哪个席位提出缺口，不代表换视角本身完成证明。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；保留
high Round 1 的 test-evidence 覆盖，不以本次无 rotation 的后轮 clean 缩减后轮策略。Rotated 累计 96 条后轮
catch 仍显著，且证据不足时默认 keep，符合工作流优先正确性的规则。

复议条件未触发：Round 2 无 rotated seat、无新 finding、无已关闭项重报或 residual deferral；Phase 7 CLEAN。
Issue #67 已由 PR #168 关闭；#54/#66/#68/#70 保持 CLOSED；#132 保持 OPEN，shared `m2-producer-core`
继续服务 #132，本次不 archive。

---

## 第 34 次复议（issue #171 / PR #172 docs-only fixture 合并后，2026-09-07）

审计数字：44 行（43 merged、1 terminal），仍为 30 个多轮合并 PR，后续轮次命中仍为
**core=125 / rotated=96**。PR #172 是 `fixture=none`、`rounds=0` 的 docs-first implementation fixture，
没有综合轮或 free-slot rotation，因此既不增加 rotation attribution 分母，也不改变 core/rotated 数字。
脚本再次输出 DECIDABLE 是达到门槛后的重复提醒，不是新的轮换样本。

本 PR 确实提供了两条新增审核收益，但归因面不同：fresh Phase 7 Gap Sweep 找到 loader 排他入参清单漏三项与
owner spec 漏 checksum-correct/non-UTF-8 asset 失败腿，独立 verifier 均判 CONFIRMED，修复后第二次 Gap
Sweep clean。问责行将它们记为 `round=0`、`lens=gap-sweep`；`loop_log_audit.py` 明确把 phase lens 与
pinned/rotated 分桶隔离，故不能把这两条挪给任一综合席位。这反而再次证明独立终审有价值，但不是
free-slot rotation 的正反样本。

None 桶由 6 个增至 7 个 merged PR，累计 `gate_net_catch` 从 0 增至 2。它尚未达到 8 个样本门槛；即使
下一条达到，也已因累计 catch 非零而不满足「整个桶零捕获」的 narrow/cut 条件。本次 docs-only catch
支持保留独立 Gap Sweep，不能外推成 product high fixture 的席位结论；#171 产品 PR 仍按 expanded/high
四席初审执行。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的 free-slot rotation + 独立终审；
不把 phase-lens catch 错归给 rotation，也不把无后轮的 docs-only 样本解释为轮换零收益。累计 rotated 后轮
捕获仍显著，且证据不足时默认 keep，符合工作流优先正确性的规则。

复议条件未触发：不存在 rotated catch、连续 rotated P3 或已关闭 finding 重报；两项 docs candidate 均经
verifier 后在合并前关闭，无 residual deferral。Issue #171 与任务 10.4 保持 OPEN/未完成，#132 仍依赖
#171；shared `m2-producer-core` 继续服务二者，本次不 archive。

---

## 第 35 次复议（issue #171 / PR #174 产品实现合并后，2026-09-08）

审计数字：45 行（44 merged、1 terminal），31 个多轮合并 PR；high 桶由 16/50 增至 **17/52**。
后续轮次 attribution 仍为 **core=125 / rotated=96**，另有 9 条历史 non-compliant catch 被脚本明确排除。
脚本再次输出 DECIDABLE 是达到阈值后的重复提醒；本次没有新增 rotated catch，不能把数字未变误写成轮换反证。

PR #174 的 high Round 1 四席产生两条独立净收益：`test-evidence` 抓到公共 loader 缺 duplicate-cell
semantic discriminator，`correctness` 首先提出 scratch/staging 对 state/parameter 内容不保真的既有残留。
两项都经 verifier 判 CONFIRMED：前者 FIX_NOW，以 test-only 修复和精确 guard-removal mutant 闭合；后者因
同一路径在 base 已存在且不属于冻结十字段 API 而 DEFER，并路由 #175。因此本产品样本支持保留 high 初审的
`test-evidence`、`correctness` 与独立 verifier；不能因一项最终 deferred 就从 net catch 中抹掉真实审核收益。

Round 2 由 `correctness` full-PR 与 `test-evidence+spec-compliance` delta/full-access 两席复核后 clean。前轮虽有
major signal，按规则最多可买一个 free slot，但 Round 1 已覆盖全部 canonical lens，没有尚未使用的 lens 可轮入；
所以本轮只增加 multi-round 样本数，不增加 core/rotated catch，也不是 free-slot rotation 的正反实验。
Phase 7 fresh Gap Sweep 在同一 final SHA 上 CLEAN，证明修复闭合但同样不应错归给 rotation。

**决策不变：keep。** 继续保留 pinned core + major/repeat 信号触发的未使用 free-slot rotation + 独立终审；
保留 high Round 1 四席。累计 rotated 后轮 catch 仍为 96，足以证明轮入互补 lens 曾持续提供额外 recall；本次
缺少可轮换候选，不能支持回退到固定 Round 1 mix。证据不足时默认 keep，符合工作流优先正确性的规则。

复议条件未触发：Round 2 无 rotated seat、无新 finding、无已关闭项重报；唯一 residual deferral 已路由 #175。
Issue #171 已由 PR #174 关闭，task 10.4 在同一问责 PR 结账；#47 保持 CLOSED，#132 与 Epic #1 保持 OPEN。
Shared `m2-producer-core` 继续服务 #132，本次不 archive。

---

## 第 36 次复议（issue #177 / PR #178 文档前置合并后，2026-09-08）

追加后审计为 46 行（45 merged、1 terminal）；none 桶为 8 个 merged PR、2 个 net catches，
未满足总收益为零的 cut 条件。多轮样本仍为 31，later core=125 / rotated=96，历史排除 9 条不变。
PR #178 的 fixture review PASS、独立 Gap Sweep CLEAN，无候选、无综合轮次、无 rotation；
这不能成为缩减后续 high 产品审核席位的证据。

**决策不变：keep。** 保留 pinned core、major/repeat 信号触发的 unused free-slot rotation 与独立终审。
本次 DECIDABLE 是累计样本阈值提醒，既有 rotated 收益未消失，没有新的 cut 依据。
#177 产品及 #132 接线尚未完成；task 14.6/14.2 保持未勾，shared change 不 archive。

---

## 第 37 次复议（issue #177 / PR #186 私有 IO 边界文档补充合并后，2026-09-09）

追加后审计为 50 行（49 merged、1 terminal）；none 桶 9 个 merged PR、2 个 net catches。
多轮样本 32，later core=125 / rotated=96，历史 non-compliant 排除 9 条；脚本再次输出 lens-rotation DECIDABLE。
本次是用户授权的两文件 docs-only 补充，fixture review PASS、CI 四项成功，无产品综合轮次或 rotation 样本。
记录复议延期：本次没有新增轮换收益/损失数据，不据此自动改变维护者已有 keep 决策；保持现有审核配置，后续 keep/cut 由维护者结合产品 #181 闭环决定。#181 round1 计数不重置，#177/shared change 仍未完成。

## 第 38 次复议（issue #177 / PR #191 专用测试文件边界补充合并后，2026-09-09）

追加后审计为55行（54 merged、1 terminal），多轮样本32，later core=125 / rotated=96，历史排除9条。脚本再次输出 lens-rotation DECIDABLE。
本次仅用户授权的两文件 docs-first 补充，fixture review PASS、四项 CI 成功，没有新增产品轮换样本。记录复议延期：没有新的轮换收益/损失依据，不自动改变已有 keep 决策；维持现有配置，待产品 #181 闭环后由维护者复议。#181 当前为 Round 2 not-clean，计数不重置；#177 与 shared change 继续未完成。

## 第 39 次复议（issue #177 / PR #181 产品合并后，2026-09-09）

追加后审计为56行（55 merged、1 terminal），high桶26个PR、58个net catches；多轮样本33，later core=127 / rotated=96，历史排除9条。PR #181三轮为not-clean/not-clean/clean，最终独立审核approve；6个CONFIRMED中5个为net catches，另1个已由CI暴露。Round 2两项P1由固定security-perf面捕获，没有未使用lens可轮入。
记录复议延期：本次没有新的rotated对照样本，不能凭两个core catches自动撤销已有keep决策；保持当前pinned core及信号触发轮换配置，后续keep/cut由维护者作明确记录。#177已关闭，14.6完成；#132仍未完成，shared m2-producer-core继续active，不整体archive。

## 第 40 次复议（issue #132 / PR #194 上游合同修复合并后，2026-09-09）

追加后审计为59行（58 merged、1 terminal），high桶29个PR、58个net catches；多轮样本33，later core=127 / rotated=96，历史排除9条。脚本再次输出 lens-rotation DECIDABLE。
本次是用户授权的docs-only合同修复，Stage 4/4.5三路审核及三组独立核销归入stage-pipeline log；没有产品综合轮次或新的轮换对照样本。记录复议延期：不以本次合同文档修复改变已有keep决策，保持当前配置，后续keep/cut由维护者明确记录。#132的upstream-contract-defective阻塞已解除，产品仍OPEN；历史三份revise保留，14.2及shared change继续active。

## PR #195 accountability checkpoint (2026-09-10)

Loop audit remains DECIDABLE (33 multi-round PRs, core=127, rotated=96). Decision deferred to maintainer: #195 adds one clean single-round sample and no new rotation evidence; retain the existing keep policy, do not auto-narrow reviewer seats.

## PR #196 accountability checkpoint (2026-09-10)

DECIDABLE rotation attribution unchanged: 33 multi-round PRs, core=127, rotated=96. Decision deferred to maintainer because #196 adds only a clean single-round sample. Retain existing keep policy; no automatic narrowing.

## PR #198 accountability checkpoint (2026-09-10)

DECIDABLE rotation attribution remains 33 multi-round PRs, core=127, rotated=96. Decision deferred to maintainer: this single-round clean PR supplies no new rotation evidence. Retain existing keep policy; no automatic narrowing.

## PR #201 accountability checkpoint (2026-09-10)

追加后审计为64行（63 merged、1 terminal），high桶34个PR、58个net catches；多轮样本33，later core=127 / rotated=96，历史排除9条。lens-rotation 再次 DECIDABLE。
本次仅修正 #47 allocation-only 文档合同，独立核销与 issue 对齐记入 stage log，没有新增产品综合轮次或轮换对照样本。记录复议延期：保持已有 keep 决策，不自动收窄。产品 PR #197 仍在原 Round 1 后修复，计数不重置；#132/task14.2/shared change 均未完成。
