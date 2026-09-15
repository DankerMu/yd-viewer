## ADDED Requirements

### Requirement: raw 可读性绑定 no-follow 普通文件描述符
rawscan._is_readable MUST 使用既有 safe_fs no-follow、O_NONBLOCK、打开后 fstat regular-file/identity 原语，从同一 fd 验证读取且全部退出归还 fd；不得在读端 resolve 链接。空普通文件仍可读。预检查已观察到的缺失、ENOTDIR、目录/FIFO、目录目标链接及断链保持 missing；权限/IO 以及读阶段 no-follow、身份、打开、读取或 close-only 拒绝 MUST 为 unreadable，不得误判 ok 或把所有拒绝统一为 missing。控制流异常仍传播且不被清理替换。

#### Scenario: raw 预检后的 FIFO 或 identity 替换
- **WHEN** 预检查通过的普通 raw 文件在打开前被替换为无写端 FIFO、symlink 或不同普通 inode
- **THEN** 有界返回 unreadable，不阻塞、不判完整

#### Scenario: raw symlink 三态迁移与空文件
- **WHEN** 分别检查普通文件目标 symlink、目录目标 symlink、断链 symlink、空普通文件
- **THEN** 分别为 unreadable、missing、missing、ok；扫描不修改任何文件
