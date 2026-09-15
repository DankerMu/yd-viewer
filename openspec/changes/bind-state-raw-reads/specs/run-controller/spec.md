## ADDED Requirements

### Requirement: 状态首行读取不得因 FIFO 替换阻塞
controller._read_header_line MUST 使用既有 safe_fs no-follow、O_NONBLOCK、fstat regular-file/identity 原语，并从同一 fd 流式读取；MUST 保持总字节预算、MAX_HEADER_LINE_BYTES、首个非空行和解码语义，不得整文件读取替代流式算法。no-follow、打开、读取或 close-only 的文件系统拒绝 MUST 为 STATE_UNREADABLE；所有退出均归还已取得的 fd，原控制流异常不得被 close 替换。MUST NOT 在读端 resolve 路径。

#### Scenario: 持锁状态读取遇 FIFO 替换
- **WHEN** 预检普通状态文件后、实际读取 open 前该名字被替换为无写端 FIFO
- **THEN** 读取有界返回 STATE_UNREADABLE，不因 FIFO open 无限持锁

#### Scenario: 首行读取拒绝链接与身份漂移
- **WHEN** 首行读取的叶子/祖先为 symlink 或打开时变成不同普通 inode
- **THEN** STATE_UNREADABLE，零错误文件内容采信；正常早返回和错误退出均关闭取得的 fd
