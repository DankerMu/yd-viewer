## ADDED Requirements

### Requirement: 配置根只解析一次并保持唯一权威
LocalConfig MUST 在每次构造时把绝对 yd_root 输入以 Path.resolve(strict=False) 解析一次，并仅以 yd_root: Path 保存 canonical 根供所有 producer I/O 消费；load_local MUST 使用同一构造路径。MUST 接受根自身或祖先的合法 symlink 别名，不得保留另一份可用于 I/O 的未解析根，消费侧不得重新解析原别名。相对路径及 ~ 拼写 MUST 在 resolve 之前以 ConfigError(path="yd_root") 拒绝；解析的 OSError/RuntimeError MUST 同样分类。MUST NOT 新增根必须存在的装载约束，不解析其它现场路径。safe_fs 的 no-follow 安全检查 MUST 保持，根内链接及 canonical 路径被替换成链接仍不得借本规则获得授权。

#### Scenario: 合法根别名装载后改指向
- **WHEN** yd_alias 指向 real_a，配置装载后把别名改指向 real_b，再用同一配置执行 init
- **THEN** 初始化只在 real_a 建链，real_b 不变；prepare/run/cleanup/publish 同样只消费该 canonical 根

#### Scenario: 根祖先别名与根内链接分界
- **WHEN** 根自身或祖先含合法 symlink，根内 states 或目标路径含 symlink
- **THEN** 前者可装载且与 realpath 根等价，后者仍被既有 no-follow 闸门拒绝且链接目标不变

#### Scenario: 非绝对或无法解析的根
- **WHEN** yd_root 是相对路径、~ 拼写或 symlink loop
- **THEN** 配置构造抛 ConfigError 且 path 为 yd_root，不写入、不建议移除合法挂载别名

#### Scenario: 未存在的绝对根不在装载期创建或拒绝
- **WHEN** yd_root 是不存在的绝对路径且无解析错误
- **THEN** 装载返回 canonical Path 且零目录创建，是否需要已有根仍由各入口的原有策略决定
