# PR #65 — 终局上限的用户裁定

**日期**：2026-08-29
**状态**：round ceiling 已触发并锁死（`review_gate.py record-round` exit 2）
**裁定人**：用户（本会话内显式作答）

## 触发事实（不因本裁定而改变）

```
Round 5 | d6a873313161f5941ecdb9984dc1848029df04ef | not-clean | verified findings: 3 |
highest severity: major | failure classes: mechanism-discriminator-insufficiency,
gate-audit-completeness, doc-accuracy, contract-completeness |
repeats prior class: yes (mechanism-discriminator-insufficiency (also round 4); ...) | gate: round-ceiling
```

Round 5 的 ledger 行**永久记 not-clean**。本裁定不修改、不撤销、不重记该行。

## 呈给用户的选项与所选

呈上四项：(1) 用户裁定修完直接终审合并；(2) 拆 PR（上限默认动作）；(3) 降范围；(4) 停下先看证据。
呈递时明确写出反对第 (1) 项的最强论据：「残留很小、修法已量」这句话在 round 4 原样说过，而那一轮又抓出两条 major——上限存在的理由正是这个。

**用户所选：(1) 用户裁定：修完直接终审合并。**

## 本裁定豁免了什么

Phase 8 pre-merge evidence hard-gate 的 clause (a) 第三项：
「最新一轮 comprehensive cross-review 在 `Last clean reviewed SHA` 上干净」。

PR #65 不存在干净的 comprehensive 轮次，且 round 6 被 CLI 机械拒绝，故该子句**无法**被满足。
用户显式豁免此一条。

## 本裁定**未**豁免什么

- Phase 7 独立终审仍须在冻结 HEAD 上跑，且须干净。
- V-1 / V-2 / V-4 三条 FIX_NOW 仍须修完（覆盖类发现按规矩一律修不延）。
- V-3（DEFER）仍须落成 tracked issue 才算关轮。
- branch-tip integrity、completion self-audit、oracle integrity、deferral routing 四条一条不减。
- `docs/review-loop-log.jsonl` 须如实记录本次豁免，不得伪装成一次干净收尾。

## 审计口径

任何后续读者应当据此理解：PR #65 是在 **5 轮均不干净、终局上限已触发**的状态下，
由人类显式承担残余风险后合并的，而非由流程判定收敛。

## 机制侧的显式改动（2026-08-29）

`review-gate` hook 在 `.review-gate.json` `locked: true` 时拦截 `implementer`/`reviewer` 派生，
而 `review_gate.py` 在 ceiling 状态下**不提供任何解锁转换**——用户裁定这条路径，流程没有一等公民表示法。

据用户裁定，将 `.review-gate.json` 的 `enabled` 置为 `false`（该字段是 hook 脚本第一项检查，
本就是为此设的关断开关）。

**同时明确**：
- `locked: true` 与 `lockReason` **原样保留**，round 5 的 ledger 行**未改动**。
- 未伪造 retro、未提前 `close`、未以假 outcome 通过任何闸门。
- `evidence_check.py` 若因 locked 状态失败，那是本次豁免的**预期产物**，按 skip block 如实记录，
  不抑制、不修改检查器。
