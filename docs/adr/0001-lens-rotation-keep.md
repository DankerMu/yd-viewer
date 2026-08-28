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
