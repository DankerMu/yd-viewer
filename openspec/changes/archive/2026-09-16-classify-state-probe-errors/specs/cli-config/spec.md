## ADDED Requirements

### Requirement: 状态目录探测失败必须在守卫边界分类
`_check_states_dir` MUST 将探测根元数据、枚举根或源目录、迭代条目及判别条目类型时遇到的 `PermissionError` 与其它 `OSError` 分类成拒绝理由，`cli.main` MUST 返回守卫退出码 `1`，不得逃逸为 traceback 或降为通用运行期退出码 `3`。理由 MUST 区分权限不足与其它 IO 故障，包含绝对 states 路径和原始 errno 文案；存在具体失败子路径时 MUST 保留，异常不携带 filename 时仍须标识正在探测的路径。权限/IO 拒绝 MUST NOT 误报为空或缺失并提示执行 init。守卫 MUST 只读、不得修改内容或模式、不得委托 init/controller。既有真正缺失、非目录、空目录分类与 #95 任一源状态文件放行语义 MUST 保持；不要求成功早停后继续审计其它源。

#### Scenario: 无权限的 states 根
- **WHEN** 非 root 用户对 mode=000 的 states 根执行 run，或注入等价 PermissionError
- **THEN** cli.main 返回 1，stderr 标识权限、绝对路径及 errno，无 traceback、无 init 提示，模式内容不变且零 init/controller 调用

#### Scenario: 根或源扫描中发生 IO 错误
- **WHEN** 根元数据、根/源目录打开或迭代、源类型或状态文件类型探测抛出 OSError(EIO)
- **THEN** cli.main 返回 1，stderr 保留 states 根及失败子路径（如适用）、errno 文案，IO 理由不伪装成权限/空目录/缺失，不写入、不委托 init/controller

#### Scenario: 权限错误在任何探测层被分类
- **WHEN** 根元数据、根/源目录打开或迭代、源类型或状态文件类型探测抛出 PermissionError(EACCES)，包括缺少 filename 的异常
- **THEN** 仍以守卫退出码 1 分类为权限拒绝，包含实际探测路径及 errno，不泄漏 traceback
