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

### 2026-08-29：issue #20 / PR #81 合并后

审计数字：14 个多轮合并 PR，后轮捕获 core=68 / rotated=63。

本 PR 对轮换问题贡献的是**一个反例方向的强证据**：它后三轮只有两个 lens（`correctness`、
`test-evidence`），没有轮入任何新视角，而这三轮恰恰是本 PR 唯一没有白跑的三轮——它们关掉的
不是实现缺陷（那些第 2 轮就完了），而是本 PR 自己新增的一条交付物「契约标注约定」。

关键观察，与前几次复议同向但更硬：**第 4 轮和第 5 轮是同一个 lens、同一个 agent 角色，
差别只在方法论——抽样 vs 普查。第 4 轮抽 11 个标注，宣布该类已关闭；第 5 轮普查 74 个，
找出 3 条假归因。** 换视角在这里不可能有帮助，因为第 4 轮的结论不是「视角盲区」，
而是「样本量不足」。真正起作用的是第 4 轮自己写下的证伪条件（「11 个样本是抽样，不是普查」）
在下一轮被真正执行。

这给「轮换 vs 常驻」的成本收益加了第三个维度，前五次复议都没有单列：**同一 lens 的方法论
强度**。core=68 / rotated=63 这个比值把「换了个视角」和「同一视角做得更彻底」记成同一件事，
而本 PR 里后者贡献了 11 条 FIX_NOW（r3 2 + r4 6 + r5 3），全部记在 core 名下。若据此得出
「core 更能抓」，是把方法论升级的功劳算给了常驻编制。建议下次做正式成本收益时，
`catches` 增加一个字段区分「新视角首次看到」与「旧视角加大强度后看到」。

第二条不利于轮换的观察：本 PR 五轮里有三轮花在标注约定而非实现上，而该约定是在实现 PR 内
顺手引入的**新交付物**——它需要自己的证伪机制（今天靠人工纪律，机械强制路由 #91）。
这不是 lens 配置能解的问题，与 #62/#84/#73 记下的「记账/契约类缺陷靠流程改动闭合、
靠换视角接近零边际收益」是同一条结论的第四次发作。已在 loop-log 的 SIZING FEEDBACK 里
记为定级教训：在实现 PR 内引入全模块审查约定，应作为独立 issue 定级。

状态仍为**默认 keep、待人工确认**。本次未改变默认。

### 2026-08-29：issue #7 / PR #65 合并后

审计数字：15 个多轮合并 PR，后轮捕获 **core=77 / rotated=86**。比值自上次复议翻转
（68/63 → 77/86，rotated 反超），但**本 PR 不支持从这次翻转读出任何结论**，理由如下。

本 PR 是 15 个多轮 PR 里唯一一个五轮全不干净、触及终局上限并靠人类豁免合并的。它的
后四轮 lens 编制是 `correctness-full` + `mechanisms`/`class-closure` + `evidence-spec`，
其中 `mechanisms` 与 `class-closure` 按 round-1 编制算 rotated——于是本 PR 后轮的
17 条 FIX_NOW 大部分记在 rotated 名下，直接推动了这次翻转。但把它读成「轮换更能抓」是错的：

**这两个 lens 之所以能抓，不是因为换了视角，而是因为它们被派去查的东西变了。**
round 3 起我给的简报不再是「复审这段代码」，而是「用变异体证明这条声明有判别器」。
同一个 `correctness` 视角在 round 1 用普通复审方式看过同一批代码，什么也没抓到；
`mechanisms` 抓到的每一条，靠的都是实跑变异体而不是第二双眼睛。

这正是上一次复议（#20 / PR #81）提出、而这次得到**反向印证**的那个维度：
`core`/`rotated` 这个二分把「换了个视角」和「换了个取证方法」记成同一件事。
上次是同一 lens 加大强度、功劳记进 core；这次是新 lens 携带新取证方法、功劳记进 rotated。
两次的真实因果都是**取证方法**，两次却被记到了相反的一侧。**一个会因归因方式不同而
指向相反结论的指标，不能用来做 keep/cut 决策。**

因此本次给出一条比前六次更强的建议：在 `catches` 里增加取证方法维度之前，
`loop_log_audit.py` 的 lens-rotation DECIDABLE **不应被当作可决策信号**，
它目前度量的是「哪个名字下面记了更多条」，不是「换视角是否有边际收益」。
上次建议的字段（区分「新视角首次看到」与「旧视角加大强度后看到」）现在需要第三个取值：
「同一视角改用机械取证（变异体/AST）后看到」——本 PR 后四轮 17 条里绝大多数属于这一类。

第二条观察，与 #62/#84/#73/#81 记下的同一条结论第五次发作：本 PR 的主导失败类是
`declared-without-discriminator`，跨五轮以两个名字复发。它靠换 lens 接近零边际收益——
两次 depth retro 都判定复发的是**横切纪律**而非模块面。真正关掉它的是取证方法的升级
（round 4 的 verifier 先原型并实测修法再交简报），不是视角数量。

状态仍为**默认 keep、待人工确认**。本次未改变默认，但记下：**该 DECIDABLE 项在获得
取证方法维度之前，其信号本身存疑**，不宜据以 cut，也不宜据以宣称 keep 得到了数据支持。

### 2026-08-30：issue #13 / PR #101 合并后

审计数字：16 个多轮合并 PR，后轮捕获 **core=86 / rotated=86**——上次复议时是 77/86，本 PR
把 core 加了 **恰好 9 条**，比值从「rotated 反超」回到**完全打平**。这 9 条正是本 PR 的
round 2/3/4 后轮捕获（6+3+0），而本 PR 的后轮 lens 编制**全部取自 round-1 混编**
（round 2：correctness/integration/invariant-state/test-evidence；round 3、4：
correctness/spec-compliance/test-evidence），**零个轮换 lens**，故 9 条全部记进 core。

**本 PR 是上一次复议（#7 / PR #65）所提假说的受控对照实验，且方向相反、结论相同。**

- PR #65：**新 lens + 新取证方法** → 功劳记 rotated。
- 本 PR：**旧 lens + 新取证方法** → 功劳记 core。

两次真正起作用的都是**取证方法**。本 PR 里最值钱的那条后轮捕获（round 2 的 P1 覆盖缺口：
IFS 转换数学零值级断言，三条单行变异全部存活于 1285 个用例，含降水 8 倍误差）出自
`test-evidence`——**这个 lens 在 round 1 就在编制里，并且看过同一批代码，什么也没抓到**。
round 2 与 round 1 的差别不是视角，是它这次**实跑了变异体**。同一 lens、同一代码、
不同取证方法，结果一个空手一个 P1。这比 PR #65 的证据更强：那次还能辩称「新视角带来了新方法」，
本次视角是常量，方法是唯一变量。

由此，上一次「在 `catches` 里增加取证方法维度之前，lens-rotation DECIDABLE 不应被当作可决策信号」
的建议，从**推论**升级为**受控实验结论**。补充一条量化证据：该指标在最近三次复议里的取值分别是
68/63（core 领先）、77/86（rotated 反超）、86/86（打平），**三次翻转全部由「这一批功劳被记到哪个
名字下」驱动，没有一次由「换视角是否有边际收益」驱动**。一个在三次连续观测里因归因方式而
来回翻转、且当前恰好停在 50/50 的指标，不具备做 keep/cut 决策的判别力。

第二条观察（与 #62/#84/#73/#81/#65 同一结论第六次发作）：本 PR 的主导失败类是 `record-accuracy`
——yd 自撰的记录文本宣称超出码与用例实际确立的范围，**跨 round 2/3 复发五次，其中两次是
orchestrator 自撰**（裁决 12 越权引用 products-contract §3.2；PR body 的 `冻结提交` 指向上一个头）。
它对换 lens 的边际收益同样接近零：round 3 的三个 reviewer 各自独立命中同一条，说明这不是视角覆盖
问题。真正关掉它的是一条**机械检查**（body SHA == HEAD == origin tip 的实跑校验），而不是第四条散文
规则——裁决 18 作为规范写下后，**在引用它自己的那次编辑里又被违反了一次**，直到补上执行钩子才止住。
这为「取证方法 > 视角数量」再添一例，且这次的「取证方法」是 CI/脚本级的机械闸门，不是变异体。

状态仍为**默认 keep、待人工确认**。本次同样未改变默认。但相较上次，建议的强度提高：
在 `catches` 获得取证方法维度（至少三取值：新视角首见 / 旧视角加大强度 / 同一视角改用机械取证）
之前，**建议 `loop_log_audit.py` 直接停止把 lens-rotation 报成 DECIDABLE**，改报为 INFO 并附本
ADR 链接——继续每轮强制一次 keep/cut 审议，而审议每次都只能得出「信号不可用」，本身就是一种
仪式性开销，且有诱导人按噪声做决策的风险。

---

## 第 9 次复议（issue #24 / PR #93 合并后）

指标取值：**core 99 / rotated 86**，17 个多轮合并 PR。这是最近四次观测的第四次翻转
（68/63 core 领先 → 77/86 rotated 反超 → 86/86 打平 → 99/86 core 反超回来）。四次翻转
全部由归因口径驱动，与「换视角是否有边际收益」无关。上一次复议给出的判据在本次得到又一次确认。

本 PR 提供的是**第三次受控观测**，而且比前两次更干净：

**观测一：复发的那条失败类，四轮都被同一个 core lens 抓住，换视角一次也没起作用。**
发布目录权限判据在 round 1/2/3/4 连续失守，四轮的首报者分别是 security-perf（round 1 P1）、
security-perf（round 2 P1）、security-perf（round 3 P2）、security-perf（round 4 P2）。
`security-perf` 从 round 1 就在编制里，属 core。轮换进来的 lens（round 3 的 integration、
round 5 的 spec-compliance 与 integration）在这条线上零命中。若轮换对复发类有边际收益，
这是它最该显形的地方——它没有。

**观测二：真正终结这条复发的不是任何 lens，是取证方法。**
round 4 的 verifier 对自然掩码族 `(m & A) == B or (m & C) == D` 的全部 6 位掩码形式（531441 种）
做暴力扫描，量出「**即使把手写枚举从十格扩到十三格，仍有 94 个变异体存活**」，并当场构造两个
反例。这个数字不是任何视角看出来的，是跑出来的。正是它把纠正动作从「再补几格」逼成「换掉
验收形式」（穷举 4096 = `stat.S_IMODE` 完整值域 + 独立措辞 oracle + 手算 224 配重），
而换完之后 round 5 一轮即 clean。

**观测三：round 5 的三份 clean 报告，可信度也来自方法而非视角。**
correctness 与 test-evidence 各自**在仓外副本独立跑变异体**验证 oracle 独立性——把 shipped 的
组合常量改成 `0o054` 后 oracle 不跟随、测试变红。这条机检证据比任何「看起来不同构」的散文论证都硬。
test-evidence 还独立复现了那次 531441 形式的扫描，得到同样的 94。若这三位只是「换个角度读代码」，
它们的 clean 判定不会比 round 3 的 clean 判定更可信——而 round 3 的两份 clean 后来被证明漏了东西。

**观测四（新增，对本 ADR 的建议本身有意义）**：本 PR 里代价最高的两条缺陷，
`(1)` retro-1 定的验收规格（十个手写 mode 字面值冒充输入域）与 `(2)` fixture 里两句被实测证伪的
断言，**都是 orchestrator 自撰的规范文本，而不是实现代码**。它们既不在任何 reviewer lens 的
职责面上，也不可能靠增删 lens 抓到——抓到它们的是 verifier 对规范文本本身做的实测。这与
`record-accuracy` 在 #62/#84/#73/#81/#65 的第六次发作同源：**审查制度的产物需要和被审查的代码
受同一套取证标准约束**，而 lens 数量对此毫无杠杆。

状态仍为**默认 keep、待人工确认**，本次未改变默认。

上次的建议在此**第三次重申且不再降级**：在 `catches` 获得取证方法维度（至少三取值——新视角首见 /
旧视角加大取证强度 / 同一视角改用机械取证）之前，`loop_log_audit.py` 应停止把 lens-rotation 报成
DECIDABLE，改报 INFO 并链接本 ADR。九次复议、四次由归因口径驱动的翻转、三次指向「方法 > 视角」的
受控观测之后，继续每轮强制一次只能得出「信号不可用」的 keep/cut 审议，是纯仪式开销，并且有诱导
按噪声做决策的风险。

---

## 第 10 次复议（issue #21 / PR #92 合并后，2026-08-30）

审计数字：20 行（19 merged、1 terminal），18 个多轮合并 PR，后续轮次命中 **core=118 / rotated=96**。
比值仍在 55:45 附近，与前几次复议同量级——**归因口径未变，结论也未变**。

**本轮提供了一个近乎受控的观测，而它指向的仍是方法而非视角。**

PR #92 五轮全部 not-clean，触 5 轮天花板。round 5 我**刻意**轮入了一个此前从未用过的 lens
（`evidence-integrity`），职责面是「审计绑定文本本身对当前 head 是否为真」，并在 brief 里
强制它**自己复跑至少 3 个变异体核对红集，不许只读证据文件**。结果：

- 它复跑了 5 个，5/5 红集与证据文件逐条一致；把 81 个用例名与 `pytest --collect-only` 对拍，
  证明无一条捏造。**这些是它的通过项**，也是本轮唯一能证明证据面本身可信的东西。
- 它同时是判定本轮 not-clean 的那一位（F1/F2）。

看起来这是「轮换奏效」的强证据。但拆开看，**起作用的是 brief 里那条强制实测的要求，不是 lens
本身**：F1/F2 是 grep 就能查出来的（13 处行号引用对当前 head 全部为假、9 处残留计数），任何
一个被要求「逐条核对绑定文本」的视角都会撞上它们；而它带来的**不可替代**的部分——「证据文件
没有造假」——恰恰来自复跑，不来自视角。

**反向证据同样存在，且更硬。** 本轮代价最高的一条缺陷（R5-G：探测面比失败面窄一层）由
`invariant-state` 与 `contract-boundary` 两个 lens 独立报出，但**它们都没有发现该修复零 oracle**。
发现这一点的是 orchestrator 在修复后例行跑判别变异体——把逐级走查退回单分量 `lstat`，全套
1244 passed，**变异体存活**。若不跑它，该修复会以「已修复」的名义合入，而下一轮任何 lens 都
测不出它被改回去。**两个 lens 看对了缺陷，零个 lens 看得出缺陷的 oracle 不存在。**

这与观测四同源，且把它推进了一步：上次说的是「orchestrator 自撰的规范文本不在任何 lens 的职责
面上」；本次说的是——**即便缺陷落在 lens 的职责面正中，lens 也判不出「针对它的证据是否存在」，
只有实测能判**。

第三个同向观测：最终复审对 `ab303ac→69b458c` 用 `ast.parse` + `ast.dump` 做内容寻址比对
（text-equal=False、AST-equal=True），据此**拒绝重跑**变异体并说明理由——AST 全等证的是全部
输入，重跑只证一次采样。这是本 ADR 三次复议里第一次看到复审方主动用一个**更强**的机械论证
替换掉一次抽样，同样与 lens 数量无关。

状态仍为**默认 keep、待人工确认**，本次未改变默认。

上次的建议在此**第四次重申，并升级为阻塞性建议**：在 `catches` 获得取证方法维度之前，
`loop_log_audit.py` 应停止把 lens-rotation 报成 DECIDABLE，改报 INFO 并链接本 ADR。
理由不再只是「信号不可用」——本轮显示这个 DECIDABLE 会**系统性地把功劳归给 lens 轮换**：
`evidence-integrity` 是 round 5 轮入的，它报出的 5 条 finding 会全部计入 `rotated`，而实际起
作用的是 brief 里那条强制实测要求；同时那条「零 oracle」的发现根本不进 `catches`（它不是评审
发现，是 orchestrator 自查），于是**真正奏效的方法在统计里不可见，而搭便车的视角在统计里加分**。
十次复议之后，这个指标不是中立的噪声，它有确定方向的偏差。

---

## 第 11 次复议（issue #25 / PR #113 合并后，2026-08-31）

审计数字：21 行（20 merged、1 terminal），19 个多轮合并 PR，后续轮次命中仍为
**core=118 / rotated=96**。PR #113 的净捕获全部发生在 Round 1 或 Phase 6.2，因此没有改变这两个
数字；恰好是一个说明当前归因模型不完备的反例，而不是支持按 118/96 作 keep/cut 的证据。

PR #113 的 Round 1 发现 future retention anchor 可扩大删除窗，初次修复将其收成一个标量上界检查。
强制的 Phase 6.2 不变量盘点随后抓到同一条 current-DONE identity 传输链的 sibling 形态：
`controller.done_cycles` 会跟随中间 cycle symlink，伪造未来 authority。该 P1 不是新的 reviewer lens
在 Round 2 抓到的，而是 **method-change audit** 发现的；其闭环是从「比较一个数」改为 cleanup-owned
fd-bound `O_NOFOLLOW` authority discovery。Round 2 的三个 pinned reviewer 与 Phase 7 都 clean。

这条结果与第 10 次的结论同向、但暴露了另一个缺口：

- 它不属于 later comprehensive round，`rotation_attribution()` 根本不计它；
- 它的价值来自强制盘点 authority 传输链和用四分量×三入口矩阵实测，而不是换入某个 reviewer 名称；
- 如果只看 core/rotated，最重要的 P1 closure 会被统计成「没有发生」。

因此本次**不根据 118/96 作 cut**，也不把它解释为 keep 的数值支持。保持既有默认：
**保留 pinned core + 独立终审的安排，待人工确认；但在 `catches` 增加取证方法和 Phase 6.2/invariant-audit
归属之前，lens-rotation 的 DECIDABLE 继续只应视为需要记录的指标缺陷，而不是可用的策略选择信号。**

复议条件未触发：PR #113 的 Round 2/Phase 7 没有重报已关闭 finding；不存在「连续两个 rotated 命中均为
P3」的可解释新样本。共享 OpenSpec change 仍未 archive，因 M2 后续任务未完成。

---

## 第 12 次复议（issue #14 / PR #115 合并后，2026-08-31）

审计数字：22 行（21 merged、1 terminal），20 个多轮合并 PR，后续轮次命中变为
**core=122 / rotated=96**。PR #115 对当前算法的增量是 core +4、rotated +0：Round 2 的三条 P1
全部由 correctness 报出，Round 3 的真实 `CanonicalProduct` oracle 缺口由 spec-compliance 报出；两者都在
Round 1 lens 集合内。

若只看这四条，本样本当然不支持「轮换买到额外 finding」。但同一 PR 还提供了一个更强的反证，说明
**122/96 仍不能被解释为 cut 信号**：Phase 6.2 独立抓到四条 P1 sibling finding——alternate-repository
source-singleton bypass、dual-forged row identity、producer direct-alias sentinel 伪绿、dynamic file-store sentinel
伪绿。它们是本 PR 最有价值的一组 owner-altitude 发现，全部由 invariant audit 的方法变化得到；问责行如实把
它们记为 round 1.5，而 `rotation_attribution()` 对 `round < 2` 直接跳过，所以四条对 core/rotated 都贡献 0。
换言之，算法把 later comprehensive 的四条全算给 core，却把同一修复链里更深的四条全部丢弃；这个
样本的观测偏差方向仍然是确定的。

方法层的结果也与第 10/11 次同向：真正闭环的是 dual-forgery construction、alternate repository、对
sentinel 自身做变异，以及最终 32-family 正向/反向 obligation matrix；换 reviewer 名称不是充分解释。
Round 4 的六路 Sonnet 与 final-head Gap Sweep 均 clean，没有重报已闭合 finding。

因此本次**不按 122/96 自动 cut**，也不把默认 keep 解释成这组数字的胜利。决策保持：
**继续保留 pinned core + free-slot rotation + 独立终审的默认安排，待人工确认；在 `catches` 增加取证方法、
Phase 6.2/invariant-audit 归属并修正 round 1.5 丢失之前，lens-rotation DECIDABLE 继续只表示统计机制要求
复议，不足以决定 keep/cut。**

复议条件未触发：PR #115 后续轮没有 rotated finding，因而不存在「连续两个 rotated 命中均为 P3」的新样本；
Round 4 与两次 Phase 7 都没有已闭合项重报。共享 OpenSpec change 仍未 archive，因为 8.2/8.3 及其他 M2
任务未完成。

---

## 第 13 次复议（issue #15 / PR #118 合并后，2026-09-01）

审计数字：23 行（22 merged、1 terminal），21 个多轮合并 PR，后续轮次命中仍为
**core=122 / rotated=96**。PR #118 的三条净捕获全部发生在 Round 1；Round 2 六名 Sonnet reviewer 与
Phase 7 fresh Gap Sweep 均 clean，所以本样本对 later-round attribution 的增量是 **core +0 / rotated +0**。

这不是支持 cut 的零收益样本，也不是支持 keep 的数值样本：本 PR 的 Round 2 与 Round 1 使用同一组六个
角色，没有发生 free-slot rotation；Phase 7 的独立视角零 finding。没有轮换发生的样本，不能拿来估计轮换的
边际收益。它唯一能证明的是本次 pinned core 已足以在第一轮抓出三条真实缺陷，并在修复后给出 clean
收敛证明。

本 PR 同时再次暴露当前统计模型看不见的重要方法成本：Phase 6.2 对 geometry / full-token FSM / required-entry
preflight 做了完整 sibling-surface audit，最终 clean；49 腿 final-source mutation 与独立 literal oracle 才是
闭环可信度的主要来源。它们都不是 later-round `catches`，因此不进入 122/96。Round 2 还把一个既有 #14
forcing-package `.10g` sibling 风险路由到 #119，但因它是 pre-existing non-blocking note，也不进入该指标。
这与第 10–12 次复议的结论完全同向：当前数字只统计「哪个 lens 名下有 finding」，没有统计取证方法、
Phase 6.2/invariant-audit、clean closure 与 out-of-scope risk routing。

**决策不变：keep。** 继续保留 pinned core + 条件式 free-slot rotation + 独立终审的默认安排；但不把
122/96 解释成 keep 的数据支持，更不据此自动 cut。在 `catches` 增加取证方法与 Phase 6.2 归属、并让
`loop_log_audit.py` 能区分「无轮换发生」之前，lens-rotation DECIDABLE 仍只表示统计机制要求复议，
不是可直接执行的策略信号。

复议条件未触发：本 PR 后续轮零 catch，不存在连续两个 rotated P3 样本；Round 2 与 Phase 7 均逐项确认
C1/C2/C3 closed、C4 unchanged/discarded，零已闭合 finding 重报。共享 `m2-producer-core` 仍有后继任务，
本次继续不 archive。

---

## 第 14 次复议（issue #17 / PR #121 合并后，2026-09-02）

审计数字：24 行（23 merged、1 terminal），22 个多轮合并 PR，后续轮次命中仍为
**core=122 / rotated=96**。PR #121 的两条净捕获全部发生在 Round 1：test-evidence 抓到
casefold fixture 被 extension grammar 提前拦截的假覆盖，security-perf 抓到 forcing fd 的
`finally: os.close` 会裸逃或替换 primary。Round 2 四名 Sonnet 1M reviewer 与 Phase 7 fresh
Gap Sweep 都是零 finding，因此本样本对 later-round attribution 的增量仍是 **core +0 / rotated +0**。

本 PR 的 Round 2 采用「一名 full-PR comprehensive + 三个 pinned high-risk lens」：
file-I/O/error-contract、test-evidence、invariant-state。`comprehensive` 是新任务名，但没有产生
catch；另外三个均是 Round 1 已覆盖风险面的延续。故这个零增量不能解释为「轮换无价值」：没有
rotated catch 不等于 rotated lens 没有做收敛证明；同样也不能解释为 keep 的数值支持，因为没有
任何后轮 finding 可归因。它能证明的只有两点：Round 1 的两个 P1 在修复后确实关闭，且这一轮
没有把已关闭 finding 重报成新问题。

本 PR 还给当前指标的盲区补了一条**零事件样本**：最昂贵的后轮工作是 Phase 6.2 对全部 tracker
`safe_fs`/显式 close、authority、rollback 与 stale-state sibling surface 的完整盘点，以及 59 腿
mutation matrix 和 cancellation addendum。审计结果是 clean，因而它们不会进入 `catches`；但正是
这些负证据让 Round 2 与 Phase 7 的 clean 有可信基础。当前 core/rotated 计数既看不见
method-change audit 抓到的 finding（第 11/12 次复议已记录），也看不见 method-change audit 给出的
**无遗漏闭合证明**。只按 122/96 作 keep/cut 仍会把取证方法与 clean-closure 的价值全部丢掉。

**决策不变：keep。** 继续保留 pinned core + 条件式 free-slot rotation + 独立终审的默认安排；
本次不把 122/96 解释成 keep 的数据支持，也不据此 cut。在 `catches` 增加取证方法、Phase 6.2
归属和 clean-closure 结果，并让审计区分「发生轮换但零 catch」与「根本没有轮换」之前，
lens-rotation 的 DECIDABLE 仍只表示统计机制要求复议，不是可直接执行的策略信号。

复议条件未触发：PR #121 的 Round 2 与 Phase 7 零已关闭 finding 重报；后轮零 catch，因此不存在
连续两个 rotated P3 样本。共享 `m2-producer-core` 仍有后继任务，本次继续不 archive。

---

## 第 15 次复议（issue #26 / PR #124 合并后，2026-09-03）

审计数字：25 行（24 merged、1 terminal），23 个多轮合并 PR，后续轮次命中仍为
**core=122 / rotated=96**。PR #124 的两条净捕获全部发生在 Round 1：correctness 抓到
`StagedRaw.entries` 按变量扇出、`copied_files` 按 bundle 复制却被 controller 用位置/等基数
`zip(strict=True)` 配对；integration 抓到 checkpoint scratch 文档层级与 runtime 不一致。
Round 2 四名 Sonnet 1M reviewer 与 Phase 7 fresh Gap Sweep 都是零候选，因此本样本对
later-round attribution 的增量仍是 **core +0 / rotated +0**。

本 PR 的 Round 2 是首轮六个 lens 的四个子集（correctness / integration / security-perf /
test-evidence），没有 free-slot rotation；Phase 7 是独立 fresh context，但零 finding。故这个样本与
第 13 次复议的结论相同：**没有轮换发生的后轮零 catch，不能估计轮换的边际收益**。它证明的是两条
Round 1 finding 已关闭、完整 14.1 diff 在修复后 clean，不是 keep 或 cut 的数值证据。

本 PR 再次暴露当前归因模型看不见的高价值工作。Phase 6.2 对所有 `StagedRaw` producer/consumer、
raw manifest、`LocalObjectStore`、失败时序与 stale-work sibling surface 做全量不变量审计；编排层还对
旧 positional zip、跳过 fanout 校验、成功后由测试自身重建 work 三种机制分别做变异。前两种闭合
review finding，第三种闭合的是编排层 Phase 2 发现的 test-oracle 缺陷。它们是 clean closure 的主要
依据，却都不增加 later-round `catches`。这与第 10–14 次复议一致：当前 122/96 只记 reviewer 名称下的
finding，不记取证方法、Phase 6.2、oracle 自查或 clean-closure 证明。

**决策不变：keep。** 继续保留 pinned core + 条件式 free-slot rotation + 独立终审的默认安排；
不把 122/96 解释成 keep 的支持，也不据此 cut。在 `catches` 增加取证方法、Phase 6.2/orchestrator
归属和 clean-closure 结果，并让审计区分「没有轮换」与「发生轮换但零 catch」之前，lens-rotation
DECIDABLE 仍只表示统计机制要求复议，不是可直接执行的策略信号。

复议条件未触发：PR #124 的 Round 2 与 Phase 7 都没有重报已关闭 finding；后轮零 catch，不存在连续
两个 rotated P3 样本。共享 `m2-producer-core` 仍有 14.2/14.3 等后继任务，本次继续不 archive。
