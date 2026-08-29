# ADR 0001：保留 reviewer lens 轮换

- 日期：2026-08-28
- 状态：**已接受（默认 keep）——待人工确认**
- 触发：`loop_log_audit.py` 在 issue #5 / PR #40 合并后报出 DECIDABLE lens-rotation

## 背景

`docs/review-loop-log.jsonl` 累计 10 行（9 merged、1 terminal）。审计对 8 个多轮合并 PR
做轮换归因，结果是后续轮次的命中：

- **core（round 1 就在的 lens）：23**
- **rotated（后续轮次换进来的 lens）：33**

即换进来的 lens 抓到的比一直在的还多，轮换不是空转。

## 决策

**保留轮换**，不退回 round-1 固定阵容。

## 理由

1. 数字本身支持 keep：33 vs 23，且这是后续轮次的命中，正是轮换要影响的那一段。
2. PR #40 是活样本。前三轮固定用 correctness / spec-compliance / test-evidence，
   火力全压在测试与证据面；round 4 让 correctness 转向生产代码本身，抓出
   `ensure_directory_no_follow` 的 fd 泄漏导致 `unsafe`/`io` 分类翻转（issue #55），
   那是前三轮碰都没碰的面。round 5 换成一个未参与过任何 lens 的独立复核者，
   它做的第一件有价值的事是**推翻编排者自己的辩护理由**（F19/F20 的时序论证），
   这类纠正天然来自没有承诺过该论证的人。
3. 反向证据也在同一份日志里：issue #6 / PR #38 走到 5 轮触顶，
   其 note 记录同一条不变量以五种不同形状复发——固定阵容对付「换形状的复发」
   本就吃亏，轮换是当前唯一在用的对冲。

## 已知的反对意见与处置

- 轮换会让每轮 reviewer 缺少前几轮的上下文，可能重报已闭合项。
  处置：不靠取消轮换解决，靠 brief 里带「已闭合清单 + finding 标准 + 空报告是有效结论」
  的反噪音脚手架；PR #40 的 round 4/5 用了这套,三个 lens 零重复、零噪音。
- `gate_net_catch` 不区分 P1 与 P3，rotated 的 33 里可能严重度偏低。
  处置：本条记为该指标的已知局限，不作为推翻结论的依据；
  若日后要更细的归因，应改进指标而不是先改策略。

## 复议条件

出现下列任一情况时重开此决策：
- 某一轮的 rotated 命中里连续两个 PR 全为 P3；
- 轮换导致的重复上报（已闭合项被重报）在某个 PR 内超过该轮 finding 总数的一半。

## 备注

按 skill 规则，keep/cut 是**人工裁决、默认 keep**。此处按默认 keep 记录并给出依据，
**尚未经人工确认**；如需改为 cut，直接修改本文件状态即可，无需回溯已合并的 PR。

## 复议记录

### 2026-08-28：issue #11 / PR #57 合并后

`loop_log_audit.py` 再次报出同一条 DECIDABLE。新归因：**9 个多轮合并 PR，
core=28、rotated=34**（上次 8 个 PR、23 / 33）。本次 PR 贡献 core +5、rotated +1。

**决策不变：keep。** 复议条件逐条核对，均未触发：

- 「rotated 命中连续两个 PR 全为 P3」：PR #57 的 rotated 命中是 round 2 的
  spec-compliance `ruling-overreach`，**P1/major** —— 而且抓的是编排者自己写的 fixture
  越权替 issue #47 作裁决，属于固定阵容结构上抓不到的一类（写 fixture 的人不会
  自查越权）。连续两个 P3 不成立。
- 「已闭合项被重报超过该轮 finding 总数一半」：#57 四轮零重报。

新增一条支持 keep 的证据：round 4 换进 invariant-state 后三位 reviewer 零 finding，
而正是这一轮独立扫了 29 个变异体、判定枚举完备性 closed —— 轮换进来的 lens 的价值
不止于「多抓几条」，也包括**给出固定阵容给不出的收敛证明**。

已知的指标缺陷（本次发现，记录不修）：`loop_log_audit.py` 读的是每条记录的
`catches` 键，而 issue #5 / PR #40 那行写成了 `findings`，因此该 PR 对轮换归因
贡献为 0，审计口径其实是 9 个 PR 里的 8 个。结论方向不受影响（#40 的 round 4/5
命中全部来自轮换进来的 lens，补上只会让 rotated 更高）。

状态仍为**默认 keep、待人工确认**。

### 2026-08-28：issue #22 / PR #62 合并后

`loop_log_audit.py` 第三次报出同一条 DECIDABLE。新归因：**10 个多轮合并 PR，
core=32、rotated=35**（上次 9 个 PR、28 / 34）。本次 PR 贡献 **core +4、rotated +1**。

**决策不变：keep。** 复议条件逐条核对，均未触发：

- 「rotated 命中连续两个 PR 全为 P3」：不成立。PR #57 的 rotated 命中是 P1/major；
  本 PR 的 rotated 命中是 minor，未构成连续两个。
- 「已闭合项被重报超过该轮 finding 总数一半」：#62 三轮零重报，每轮 reviewer 都按 brief
  逐条给出前轮 finding 的 closed / still-failing 判定并附证据。

本次的归因形状与前两次不同，值得记一笔：**#62 的 round 1 一次就铺满了六个 lens，
round 2/3 都是它的子集，所以常规轮次的 rotated 贡献为 0**——按本 ADR 的口径，
「轮换」在这个 PR 的复核轮里根本没发生。唯一那条 rotated 命中来自 **Phase 7 的
`final-head-confirmation`**：一个不在任何 round-1 阵容里的 lens，任务只有「这些声明在
这个 head 上是不是真的」。它抓到的是编排者**为修一处不实断言而写的提交自己引入的
另一处不实断言**（变异体 (m2) 的批次归属，PR #62 偏离 23）。

这条对 keep 的支持方向与前两次不同，因此单独记录：前两次的论据是「换进来的 lens 抓得更多」，
这一次的论据是**有些缺陷在结构上只有换人才可能被抓到**——写下该断言的人刚刚才校对过它，
再让同一个视角复查一遍，得到的是同一个判断。这不是注意力问题，无法靠更仔细来解决。

同时记一条对轮换**不利**的观察，不作为推翻依据但应进入下次复议：本 PR 14 条 CONFIRMED 里
有 7 条属 evidence-accuracy / evidence-record，而这一类的根因是机械的（数字写在旧 head 上、
head 一动即过期，且 `evidence_check.py` 不校验计数）。**换 lens 对它没有帮助**——三轮里
换了几个视角都在重复报同一批过期数字。真正闭合它的是流程改动（在冻结 head 上一次性重测重写、
先贴评论再写指针），不是阵容改动。轮换的边际价值在**判断类**缺陷上高，在**记账类**缺陷上近乎为零；
若日后要给轮换做成本收益，应按缺陷类别分开算，而不是看总命中数。

状态仍为**默认 keep、待人工确认**。

### 2026-08-28：issue #9 / PR #61 合并后

`loop_log_audit.py` 第四次报出同一条 DECIDABLE。新归因：**11 个多轮合并 PR，
core=44、rotated=45**（上次 10 个 PR、32 / 35）。本次 PR 贡献 **core +12、rotated +10**。

**决策不变：keep。** 复议条件逐条核对，均未触发：

- 「rotated 命中连续两个 PR 全为 P3」：不成立。本 PR 的 rotated 命中里有一条 **major**
  （Phase 7 的 merge-content-loss）。
- 「已闭合项被重报超过该轮 finding 总数一半」：三轮零重报，每轮 reviewer 都逐条给出前轮
  finding 的 closed / still-failing 判定并附证据。

本次的形状**同时给出了支持与不支持轮换的证据，值得分开记**：

- **支持**：五条 Phase 7 命中**全部**落在 rotated（`final-comprehensive`），其中包括本 PR
  唯一一条 major——编排者解决合并冲突时用 master 那一侧整份文件重建清单、只手工补了两行，
  于是凡是分支改过而 master 没改的行全被静默回退到 merge-base。**970 全绿抓不到它**
  （清单的 `落地状态` 列有双向机检、备注列散文没有），而且它是编排者自己造成的，让编排者
  复核自己的合并结果在结构上就抓不到。这与上次记的论据同向：有些缺陷只有换人才可能被抓到。
- **不支持**：本 PR 的 core 贡献（12）高于 rotated（10），且**三轮里最重的四条判别力缺口
  全部由 core lens（`oracle-strength`）抓到**。本 PR 贯穿三轮的失败模式是「判别力盲点位移」
  ——同一模块同一条 oracle 纪律连漏三轮、每轮换一个性质。抓住这条模式恰恰需要**同一个
  lens 连续盯**：round 2 的 oracle-strength 之所以能发现盲点已从回写形态移到排序不变量，
  是因为它知道 round 1 钉的是什么。换人会丢掉这个连续性。

综合：**轮换与常驻各有不可替代的场合**，与上次记的「按缺陷类别分开算」是同一结论的另一面。
上次的分界是「判断类 vs 记账类」，本次补一条更实用的分界：

- **需要连续性的缺陷**（盲点位移、同一纪律的递进泄漏）→ 常驻 lens 更强；
- **需要独立性的缺陷**（编排者自己的产出、刚被校对过的断言、合并结果）→ 轮换 lens 更强，
  且常驻 lens 在结构上抓不到。

故 keep 的正确形态不是「每轮都换」，而是**保留一部分常驻以维持连续性、同时保证每个 PR 至少
有一个从未参与过的视角落在编排者自己的产出上**。本 PR 的实际阵容已是这个形状（三轮里
`oracle-strength` / `nwm-pin-fidelity` 常驻，`fix-integrity` / `shared-guard-integration` /
`production-correctness` 逐轮换入，Phase 7 全新），建议写进下次的 lens 选择口径。

状态仍为**默认 keep、待人工确认**。

**顺带记一条日志自身的缺陷**（已另立 issue）：本条目最初用 `findings` 作为命中数组的键，
审计脚本读的是 `catches`，于是 36 条命中一条都没进归因——归因数字与上次一字不差，正是这样
被发现的。日志里 issue #5 / PR #40 那一行（第 10 行，11 条命中）至今仍用 `findings`，对审计
不可见；`evidence_check.py --loop-log-entry` 也不校验键名，两处都放行了。

### 2026-08-28：issue #23 / PR #84 合并后

`loop_log_audit.py` 第五次报出同一条 DECIDABLE。新归因：**12 个多轮合并 PR，
core=44、rotated=57**（上次 11 个 PR、44 / 45）。本次 PR 贡献 **core +0、rotated +12**
——后续轮次的命中**全部**来自换进来的 lens，是四次复议里最极端的一次。

**决策不变：keep。** 复议条件逐条核对，均未触发：

- 「rotated 命中里连续两个 PR 全为 P3」：不成立。本 PR 的 rotated 命中含三条 major
  （round 2 的 `input-domain-gate` 被两个独立 lens 各抓一次、`cross-module-invariant-unpinned`）。
- 「已闭合项被重报超过该轮 finding 总数一半」：零重报。round 2/3 的每份 brief 都带闭合清单
  与「空报告是有效结论」的反噪音脚手架。

**本次的形状与前四次都不同，是本 ADR 至今最值得记的一条。**

本 PR 的主导失败类不是实现缺陷，而是 **fixture-text-accuracy：编排者写进 binding fixture
的断言本身不成立**，七条。样本：自称「逐字对齐 compute-loop §10 步骤 4」而实现把清理挪到了
raw 扫描之后；`containment_root`「严于」`realpath` 的论证方向反了，**改正之后又反了一次**；
两条 Required evidence 在 `resolve()` 落地后互相矛盾；幂等证据条目漏掉了变异体 (s) 的真正
判别器（照该条目字面写测试，(s) 会存活——核验者实测）；引用了一个不存在的测试文件；
`--chdir` 的出处根本不存在。

这一类**固定阵容在结构上抓不到**，理由与 PR #62 记录的那条同源但更尖锐：写下断言的人刚刚
才校对过它，再让同一视角复查一遍，得到的是同一个判断。#62 的形式是「编排者为修一处不实断言
而引入另一处」；本 PR 的形式是「编排者为修一条论证方向错误的记录，写出了第二条方向同样错误的
记录」——同一处文本、连续两轮、同一种错法。这不是注意力问题，无法靠更仔细解决。

本轮的对冲是**显式派一个 lens 去审编排者自己的 fixture 修订**（round 2 的
`spec-compliance-fixture-audit`、round 3 的 `fixture-self-audit`），七条里五条由它们抓，
另两条由 Phase 7 的 `final-head-confirmation` 抓——全部来自「任务就是不信任编排者输出」的视角。
因此本次对 keep 的支持不止是「换进来的抓得多」，而是：**有一类缺陷只有当某个 lens 的任务被
明确定义为「审计编排者的产出」时才可能被抓到**，它比「换个人看」更强，是「换个立场看」。

记两条不利/存疑的观察，不作为推翻依据但应进入下次复议：

1. 归因口径把 `spec-compliance-fixture-audit` 判为 rotated，而它其实是 round 1
   `spec-compliance` 的任务变体（审对象由「代码 vs spec」换成「编排者的文本 vs 现实」）。
   按名字比对的口径会把「同一 lens 换任务」记成轮换。core +0 这个数字因此偏乐观，
   真实的轮换增益小于 12。若日后要更细的归因，应按**任务定义**而非 lens 名比对。
2. **机械检查缺位**：`openspec validate --strict` 只校验 fixture 的结构，不校验其中的断言
   是否属实；`evidence_check.py` 也不校验。fixture 正确性目前完全依赖「记得派这个 lens」，
   属流程记忆而非工具保证。这与 PR #62 记下的「记账类缺陷靠流程改动而非阵容改动闭合」是同一
   方向：**轮换对判断类缺陷有效，对记账类缺陷无效**，而 fixture-text-accuracy 横跨两者
   ——它的内容是判断，它的失效模式是记账。

状态仍为**默认 keep、待人工确认**。

### 2026-08-29：issue #16 / PR #73 合并后

`loop_log_audit.py` 第六次报出同一条 DECIDABLE。新归因：**13 个多轮合并 PR，
core=52、rotated=63**（上次 12 个 PR、44 / 57）。本次 PR 贡献 **core +8、rotated +6**
——回到常驻略占优的形状（round 2 的 test-evidence 3 条、round 3 的 integration 4 条
均来自常驻位）。

**决策不变：keep。** 复议条件逐条核对，均未触发：

- 「rotated 命中里连续两个 PR 全为 P3」：不成立。本 PR 的 rotated 命中含一条 **major**
  （round 2 `spec-compliance` 的清单第 5 行仍把已落地符号记为待落地，其反重复条款只枚举
  #8 子集，会让 #9 再移植一份——正是本 PR 越界要防的双权威）。
- 「已闭合项被重报超过该轮 finding 总数一半」：三轮零重报。

**本次最值得记的一条与 lens 阵容无关，因此也暴露了本 ADR 指标的边界。**

本 PR 收益最高的产出不是任何一条 finding，而是 **round 2 verifier 的批级结论**：它判定
round 1 的四条 test-oracle 修复「未关闭，是补丁不是扫描」，证据是 round 2 的两个存活变异体
恰落在 round 1 所钉那个布尔的**另一个操作数**、那个异常元组的**另一个成员**上，并自行做
9 个变异体点查又找出第三个存活体。由此纠正动作从「再采样一次」升级为**清单重构**
（§G9 五轴不变式面清单，逐单元「要么有变红见证、要么有书面等价理由」，清单本身作为产物
落进测试模块 docstring），round 3 该类未复发。

**这条产出既不属 core 也不属 rotated——它来自验证环节而非评审环节，`gate_net_catch` 完全
看不见它。** 上次记的分界（判断类 / 记账类、连续性 / 独立性）是关于**谁去看**；本次补一条
关于**看什么层级**：单条 finding 的归因永远算不出「这一批 finding 是否证明区域已关闭」这种
批级判断的价值，而后者才是终止一个失败类的东西。若日后要给轮换做成本收益，除按缺陷类别
分开算之外，还应把 verifier 的批级结论单列，不要塞进 lens 归因。

记两条不利观察，进入下次复议：

1. **本 PR 最严重的一次内容丢失，轮换与常驻都没抓到。** 编排者用起止行号切片重写 §G，
   静默删掉夹在 §G 与 §G1 之间的三个规范区块（Seams under test、Risk packs、Review focus
   常驻四轴）。round 3 与 Phase 7 都放行，因为**两者都是拿当前 fixture 对实现**，没有任何
   视角的任务是「拿 fixture 对它自己的前一版做 diff」。这与 #9 的 merge-content-loss 同形
   （凶器从 merge 换成行号切片），是同一缺陷类的第二次发作。结论：这类缺陷靠换视角无解，
   只有机检可解（fixture 编辑后 MUST diff 标题集合，非预期标题消失即红）——与 #62、#84
   记下的「记账类缺陷靠流程改动闭合」同向，且证据更硬：这一次连「派一个专门审编排者产出的
   lens」都不够，因为审计对象是**版本之间的差**，不是任何单一版本的内容。
2. 本 PR 的主导失败类 record-fidelity **连续三轮复发**，且 verifier 指出纠正动作本身有两处
   可命名的设计缺陷（触发器 1 无完备性 oracle；fixture 自身不在任何触发器的目标集里）。
   换 lens 在这一类上依旧接近零边际收益——三轮换了几个视角，报的是同一批过期数字的不同实例。
   真正闭合它的是把散文枚举换成机检形态（逐行处置表行数 MUST 等于清单数据行数；改动面换成
   逐字可 diff 的路径全集）。

状态仍为**默认 keep、待人工确认**。
