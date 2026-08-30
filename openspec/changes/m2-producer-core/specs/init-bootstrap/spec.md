# init-bootstrap

来源：compute-loop-design §6.2；design.md（本 change）D7。

## ADDED Requirements

### Requirement: 只在全新根执行
`init` 发现 `states/` 下已有任一状态文件、或 `output/` 下已有任一 `DONE` 时，MUST 直接拒绝执行。

#### Scenario: 已有状态即拒绝
- **WHEN** 模拟根中存在 `states/gfs/2026082700.cfg.ic` 时执行 init
- **THEN** 拒绝退出，`states/` 与 `output/` 无任何变化

#### Scenario: 已有 DONE 即拒绝
- **WHEN** 模拟根中存在任一 `output/<cycle>/<source>/DONE` 时执行 init
- **THEN** 拒绝退出，无任何写入

### Requirement: 扫描窗内确定各源首轮
`init` MUST 以执行时刻往前 7 天为扫描窗，对每个 source 找到窗内最早的完整 00Z/12Z raw cycle 作为该源首轮 T；任一 source 窗内无完整 cycle 时 MUST 整体拒绝且不写任何状态（fail closed，等待 raw 补齐后重跑；compute-loop §6.2）。

#### Scenario: 双源各自首轮
- **WHEN** raw fixture 中 GFS 与 IFS 窗内最早完整 cycle 不同
- **THEN** 两源分别以各自最早完整 cycle 为首轮 T

#### Scenario: 单源窗内无完整 raw 即整体拒绝
- **WHEN** 窗内 IFS 无任何完整 cycle 而 GFS 有
- **THEN** init 拒绝退出，`states/` 下无任何文件

#### Scenario: 被跳过候选上的不可读 raw MUST 在成功理由中点名
- **WHEN** 某源窗内存在完整 cycle，但在**它之前**的候选 cycle 上存在不可读的 raw 文件（该候选因此不完整、被跳过），使该源首轮 T 落在更晚的 cycle 上
- **THEN** init 正常建链（方向不变），但成功理由 MUST 点名这些被跳过候选上存在不可读的 raw 文件（给出数目或路径），MUST NOT 静默把链起点推后——init 一生只跑一次，落盘后根已非全新，重跑必被「已有状态即拒绝」拦下，静默偏移无自愈路径

#### Scenario: raw 不可读不得伪装成缺 raw
- **WHEN** 某源窗内唯一完整 cycle 的 raw 文件存在但不可读（权限失败），使该源无完整 cycle
- **THEN** init 拒绝退出、`states/` 下无任何文件，且理由 MUST 点明存在不可读的 raw 文件，MUST NOT 只提示等待 raw 补齐后重跑
- **AND** 缺文件与不可读**同时存在**时理由 MUST 并列点名两者，MUST NOT 作出「不是缺数据」这类全称否定；此时仍 MUST NOT 提示「等待 raw 补齐后重跑」——该提示只在确为纯缺文件时才允许

### Requirement: 率定末态定位
`init` MUST 把每个变体目录（`config.toml` 的 `variants.<source>`，相对 `yd_root`）**顶层**恰好一个 `.cfg.ic` 普通文件识别为该源率定末态；命中数不为 1、目录不存在、或目录无法枚举时 MUST 整体拒绝且不写任何状态。`variants.<source>` 的取值 MUST 先经相对性校验：**绝对路径或含 `..` 分量的取值 MUST 被拒绝且不写任何状态**，MUST NOT 被拼接后读取——否则链起点会取自 `yd_root` 之外。

#### Scenario: 变体顶层无唯一率定末态即拒绝
- **WHEN** 某变体目录顶层的 `.cfg.ic` 文件数为 0 或 2
- **THEN** init 拒绝退出，`states/` 下无任何文件，拒绝理由区分「变体缺失」与「率定末态不唯一」

#### Scenario: 变体路径越出 yd_root 即拒绝
- **WHEN** `variants.<source>` 是绝对路径，或其路径分量含 `..`
- **THEN** init 拒绝退出，`states/` 下无任何文件，拒绝理由与「变体缺失」「率定末态不唯一」逐项可区分

### Requirement: 首态生成
`init` MUST 从两个变体内各自同源率定末态复制首态，重戳到该源首轮 T，写为 `states/<source>/<T>.cfg.ic`；MUST NOT 运行 SHUD，MUST NOT 写任何 `DONE`。所有判定 MUST 在任何写入之前完成；写入 MUST 拒绝覆盖已存在的目标文件。

#### Scenario: 首态写入
- **WHEN** 对合成变体与完整 raw fixture 执行 init
- **THEN** 每个建链 source 得到一个重戳到其首轮 T 的状态文件，`output/` 下无 `DONE`

#### Scenario: 写入阶段失败的收尾可观测
- **WHEN** 前序 source 的首态已写入后，后续 source 的目标路径上已存在一个条目（非普通文件，故未被「已有状态即拒绝」守卫拦下）导致排他写入被拒
- **THEN** init 以非零退出码报告失败，理由列出全部已落盘 source 的路径与「根已非全新，重跑前需人工清理 `states/`」，且已落盘文件 MUST NOT 被删除

#### Scenario: 零残留的写入失败不得宣称需要清理
- **WHEN** 写入序首位 source 的首态写入即失败，尚无任何 source 落盘，失败发生在目标文件被创建之前，**且写入路径上（终名 target 及其各级父目录分量）不存在任何持久外来条目**——例如 `states/` 被置为不可访问的权限位（故盘上零残留，恢复权限后直接重跑即可成功，无需移除任何条目）
- **THEN** init 以非零退出码报告失败，理由 MUST 指出零写入、根仍是全新根并给出根因，MUST NOT 宣称根已非全新或需要人工清理 `states/`

#### Scenario: 写入路径被持久外来条目挡住时不得宣称根仍是全新根
- **WHEN** 写入序首位 source 的首态写入被一个**非本次写入产生**的持久外来条目挡住，尚无任何 source 落盘，盘上零普通文件残留；该条目既可能占住终名 `states/<source>/<T>.cfg.ic`（排他创建撞已存在条目），也可能占住其**父目录分量** `states/<source>`（symlink、FIFO 或普通文件，使目录创建失败）
- **THEN** init 以非零退出码报告失败，理由 MUST 点名**被占住的那个路径本身**并要求重跑前先确认并移除它，MUST NOT 宣称根仍是全新根（重跑必然以同样理由再次失败），MUST NOT 宣称该目标可能已被部分写入
- **AND** 两种载体 MUST 走同一路话术：判据是「阻塞物是否为持久外来条目」，MUST NOT 由「哪条腿抛的异常」或「条目是否恰好落在终名 target 上」决定

#### Scenario: 写入中途的 I/O 失败点名可能的半写产物
- **WHEN** 目标文件被排他创建后，写入过程中途因 I/O 或配额错误失败
- **THEN** init 的失败理由 MUST 点名该目标路径可能已被部分写入、重跑前须一并人工确认

### Requirement: 扫描窗配置取值域自查
`init` MUST 在构造候选 cycle 之前自查 `cycle.hours` 非空且每个值都是合法小时；不满足时 MUST 以配置错误拒绝并点名该配置项，MUST NOT 退化为「窗内无完整 cycle」，也 MUST NOT 以未分类异常逃逸。

#### Scenario: 空的 cycle.hours 不得伪装成缺 raw
- **WHEN** `cycle.hours` 为空列表而 raw 目录树完整
- **THEN** init 以配置错误拒绝并点名 `cycle.hours`，MUST NOT 返回「窗内无完整 cycle、等待 raw 补齐」

#### Scenario: 非法小时不得以未分类异常逃逸
- **WHEN** `cycle.hours` 含 0–23 之外的值
- **THEN** init 以配置错误拒绝并点名 `cycle.hours`，CLI 不泄漏 traceback
