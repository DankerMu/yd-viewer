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
