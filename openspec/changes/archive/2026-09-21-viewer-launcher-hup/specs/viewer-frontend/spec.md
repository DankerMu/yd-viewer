## ADDED Requirements

### Requirement: 本地开发终端挂断清理
README一键全栈命令 MUST 将 SIGHUP 与 SIGTERM 接入同一清理路径；实际测试终端挂断后 MUST 停止本次记录的后台进程组并删除本次创建的临时目录，MUST NOT 删除其他文件或终止不属于本命令的进程。Ctrl-C、SIGTERM、任一子进程退出和固定端口冲突的既有行为 MUST 保持；不承诺 SIGKILL 后清理。

#### Scenario: 终端挂断
- **WHEN** 在独占测试PTY或tmux pane从viewer原样启动README命令，两服务就绪后关闭该测试终端
- **THEN** 本次两子进程组退出，8000/5173释放，打印的临时目录删除；仅向内层Python发信号不能代替此终端关闭验证

#### Scenario: 直接及重复挂断信号
- **WHEN** 另一次原样启动README命令后，向实际Python launcher发送SIGHUP，并在清理等待阶段重复发送SIGHUP
- **THEN** 复用同一清理路径，本次两子进程组退出、端口释放、临时目录删除；重复信号不能中断清理，此验证与终端关闭验证均须通过
