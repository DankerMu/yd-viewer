## ADDED Requirements

### Requirement: DONE 枚举与命名校验
目录枚举 MUST 只把 `output/<cycle>/<source>/DONE` 经 `lstat` 判为普通文件（symlink 一律不算）的 `(cycle, source)` 视为候选；`cycle` MUST 匹配 `^\d{10}$` 且小时段 ∈ {00, 12}，`source` MUST ∈ {`gfs`, `ifs`}；不满足命名的条目 MUST 忽略且不报错。

#### Scenario: 缺 DONE 不枚举
- **WHEN** `output/2026082700/gfs/` 只有 `yd.rivqdown.dat`
- **THEN** 该 source 不在结果中

#### Scenario: DONE 是 symlink 不枚举
- **WHEN** `output/2026082700/gfs/DONE` 是指向另一已完成目录 `DONE` 的符号链接
- **THEN** 该 source 不在结果中

#### Scenario: 非法目录名忽略
- **WHEN** `output/` 下存在 `tmp`、`2026082706`、`2026082700/GFS/DONE`
- **THEN** 三者均不在结果中，枚举不抛错

### Requirement: 结构层不合规的 source 排除
候选 `(cycle, source)` 的 DAT 未通过 viewer-dat-reader **结构层**校验（有界读头 + stat）时，该 source MUST 从结果中排除，并 MUST 以 WARNING 记录路径与原因；MUST NOT 传播为 5xx。枚举期 MUST NOT 整读数据区，也 MUST NOT 校验分钟列。

#### Scenario: 坏结构只排除本 source
- **WHEN** `2026082700/gfs` 的 DAT 只有 167 行而 `2026082700/ifs` 合规
- **THEN** cycle `2026082700` 的 sources 为 `["ifs"]`，日志出现一条含 `gfs` 路径的 WARNING

#### Scenario: 整个 cycle 无可用 source
- **WHEN** 某 cycle 两个 source 的 DAT 结构都不合规
- **THEN** 该 cycle 不出现在结果中

#### Scenario: 分钟列坏但结构合规仍列出
- **WHEN** 某 DAT 行数、列编号正确但第 0 列整体偏移
- **THEN** 该 source 仍出现在结果中（拒绝发生在取数时，见 viewer-api）

#### Scenario: 枚举期读取量有界
- **WHEN** 窗内有 30 个候选、每个 DAT 5.4 MB
- **THEN** 枚举期对每个 DAT 的读取字节数不超过 `1024 + 8*(2+nc)`

### Requirement: 7 天窗口与排序
结果 MUST 以最新可用 cycle 为锚，只包含起报时间 ≥ 锚 − 7 天的 cycle，按 cycle 倒序；每个 cycle 的 `sources` 按 `gfs`、`ifs` 固定顺序列出实际可用者。锚 MUST 是最新可用 cycle 而非墙钟。MUST 提供 `latest()` 返回锚 cycle 或 `None`。

#### Scenario: 窗口边界
- **WHEN** 可用 cycle 为 `2026082700`、`2026082000`、`2026081912`
- **THEN** 结果为 `2026082700`、`2026082000`

#### Scenario: 停更后仍展示
- **WHEN** 最新可用 cycle 比当前墙钟早 30 天
- **THEN** 结果仍以该 cycle 为锚，非空
