## MODIFIED Requirements

### Requirement: 残留清单绑定声明身份
公开 `ResiduePlan` MUST 在构造时消费配置构造已解析的 canonical yd_root，不得再次 resolve；并绑定非空单分量 source、合法 retained cycle 及每条删除路径。state_files MUST 词法等于该根的 `states/<source>/<cycle>.cfg.ic` 且 cycle 严格晚于 retained；half_product_dirs MUST 词法等于该根的 `output/<retained>/<source>`。执行器 MUST 在任何删除之前复验全部字段与全部条目；任一身份越界以 `SafeFilesystemError(kind="unsafe")` 拒绝整份清单且零删除。合法 tuple 规范化排序去重。不得用目标 realpath 消除词法越界；既有 safe_fs no-follow 继续负责文件系统使用点安全，合法清单的 IO 拒绝不承诺回滚。

#### Scenario: 手构清单不能删除兄弟源
- **WHEN** source=ifs 的手构计划带有 GFS output（含 DONE）或 GFS state，或跨根/lane/retained cycle 的条目
- **THEN** 构造即拒绝，所有根内外文件递归快照不变

#### Scenario: 执行期拒绝整份身份非法计划
- **WHEN** 绕过构造或篡改后的计划先列合法半成品，后列其它源、错误后缀、带遍历或不晚于 retained 的状态
- **THEN** execute_residue_plan 在第一笔删除前拒绝，合法半成品与所有状态也全部保留

#### Scenario: 自洽计划保持既有行为
- **WHEN** planner 或手工构造的完整计划满足 root/source/lane/cycle 身份，含重复或乱序合法路径
- **THEN** 规范化后只删除点名目标，保留 retained 与兄弟源；重复执行无副作用，DONE 判定仍仅由 planner 承担
