## MODIFIED Requirements

### Requirement: run 永不自动 bootstrap
`run` 发现状态目录缺失或为空时 MUST 报错停止，MUST NOT 调用 init 逻辑或自建状态。「为空」MUST 按 `states/<source>/` 的直接子文件判定：任一源目录下存在以既有 `STATE_SUFFIX`（`.cfg.ic`）结尾的文件即可通过存在性守卫，不要求两源齐备，不解析内容或 cycle。顶层文件、空源目录、杂项、以该后缀命名的目录及更深层文件 MUST NOT 被视为状态文件。拒绝为空时 MUST 返回守卫退出码 `1`，给出「请先经授权执行 `yd-producer init`」，保持目录内容与模式不变；有状态文件后仍由生产 controller 决定运行结果。

#### Scenario: 状态目录缺失
- **WHEN** `states/` 不存在时执行 `run`
- **THEN** 报错退出，`states/` 仍不存在，未提交任何作业

#### Scenario: 空源目录不代表已建链
- **WHEN** `states/ifs/` 为空，或根只有顶层状态文件、杂项、后缀目录或更深层文件
- **THEN** `cli.main` 返回 `1`，stderr 含绝对 states 路径及授权 init 提示、不含 traceback，零 init/controller 委托且内容与模式不变

#### Scenario: 任一源状态即可进入生产委托
- **WHEN** 仅一个源或两个源的直接子文件中存在 `.cfg.ic` 文件
- **THEN** 守卫放行进入既有持锁生产委托，不自动 init，不把内容或 cycle 校验迁入 CLI
