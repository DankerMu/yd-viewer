## ADDED Requirements

### Requirement: cfg.ic 路径读取绑定 no-follow 普通文件描述符
cfg.ic 路径读取 MUST 复用 safe_fs 的 O_NOFOLLOW 与 O_NONBLOCK open、打开后 fstat 普通文件及身份校验，判定和读取 MUST 绑定同一 fd。MUST 保持 max_bytes+1 有界读及 bytes-like 输入行为；路径的 no-follow/打开/读取失败 MUST 收敛为公开 ValueError。读端 MUST NOT resolve 输入以绕过叶子或祖先链接拒绝；#110 配置根之外的调用方须提供物理路径。取得的 fd MUST 在全部退出路径关闭一次，close-only 失败保持分类，原在途控制流异常不得被清理替换。

#### Scenario: 普通状态文件被替换为 FIFO
- **WHEN** 已判定为普通文件的 cfg.ic 在读取打开前被换为无写端 FIFO
- **THEN** 在隔离进程的有界等待内以 ValueError 拒绝，不阻塞、不改源文件

#### Scenario: 链接不再是可读状态
- **WHEN** cfg.ic 叶子或任一祖先是 symlink，或打开 fd 与预检普通文件身份不同
- **THEN** parse 以 ValueError 拒绝；init 的率定态解析同样拒绝且两源零写入

#### Scenario: 合法状态的字节与预算不变
- **WHEN** 读取无链接的合法状态或解析合法 bytes-like 输入，以及给定显式 max_bytes 的边界输入
- **THEN** 原有格式保真、超限拒绝和无部分文档行为保持
