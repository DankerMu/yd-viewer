## MODIFIED Requirements

### Requirement: manifest 语义键承接与 fail-closed
manifest 的 entry 级语义键（GRIB filter、累积语义、时间标记）MUST 逐条承接自 NWM 源 manifest，MUST NOT 在本仓发明或以默认值补齐。降水累积语义缺失或越域时 MUST 报错停止。承接进来的 entry 级时间标记 MUST 与本轮自算的时间一致——`cycle_time` MUST 等于本轮 cycle，`valid_time` MUST 等于本轮 cycle 加该 entry 的 lead；比较 MUST 在**时刻**上做而非文本上做（同一时刻的不同时区写法视为一致）。不一致时 MUST 报错停止，MUST NOT 承接一个与本轮自算值矛盾的时间标记。源 manifest MUST 声明其 forecast hours 全集，且该全集 MUST 覆盖本轮预期的全部 lead；本仓产出的 manifest MUST 自行显式写出 forecast hours 相关键，MUST NOT 从源 manifest 转抄、也 MUST NOT 依赖消费端由实际 entry 反推。

累积量（当前只有 GFS `apcp`）的 `idx_selector` 子 Mapping 来源分两种。源 manifest 的 entry 已带 `idx_selectors`/`idx_selector`（pin 形态）时 MUST 原样承接、MUST NOT 重新推导。源 entry 两个 idx 键都缺席（NWM `gfs-idx-selector-v3` 起，策略只记在顶层 `source_policy.apcp_selector_policy`，选择结果只体现在 bundle 里）时，staging MUST 在复制之前以只读、不跟随 symlink（`O_NOFOLLOW`，与复制步骤同一第二道闩；open 时已成 symlink（`ELOOP`）同样归 `accumulation-metadata` 拒绝，不跟随）打开该 entry 对应的 **NWM raw 根下的原件 bundle**（`_reject_symlinks` 之后的同一路径），交给 eccodes 顺序 `codes_grib_new_from_file` 枚举记录、只保留 `shortName` 等于该 entry `grib_short_name` 的记录并收集其 `stepRange`；MUST NOT 经 cfgrib/xarray 打开、MUST NOT 写 `.idx`、MUST NOT 在 `raw_root` 下创建任何文件。恰一条记录且 `stepRange` 为 `0-<lead>` → 写入 `{"accumulation_type": "cumulative_since_cycle", "step_range": "0-<lead>"}`；恰一条且起点 ≠ 0、终点 = lead → `{"accumulation_type": "interval_bucket", "step_range": "<start>-<lead>"}`；零条、多条、终点 ≠ lead、`stepRange` 不是 `<int>-<int>`、或 eccodes 无法解码该文件 → `RawStagingError(kind="accumulation-metadata")`，消息列出实测到的记录。MUST NOT 从 `source_policy` 字符串或默认值推断。既有 R4B2 闸门在推导之后照常运行。IFS 的 `tp`/`ssr`/`str` 不在本闸门内。

#### Scenario: 源 manifest 不可用
- **WHEN** 源 manifest 缺失、不可解析，或其 entry 无法覆盖本轮预期的 `(lead, variable)` 全集
- **THEN** 报错停止，不以空值或推导补齐

#### Scenario: 承接的 entry 时间与本轮自算不一致
- **WHEN** 源 manifest 某条 entry 的 `cycle_time` 不等于本轮 cycle，或其 `valid_time` 不等于本轮 cycle 加该 entry 的 lead
- **THEN** 报错停止，work 根内不出现任何新增路径

#### Scenario: 时间一致性按时刻比较而非文本比较
- **WHEN** 源 manifest 的 entry 时间写成与本轮 cycle 不同的时区偏移，但指向同一时刻
- **THEN** 视为一致，正常生成 manifest

#### Scenario: 降水累积语义缺失
- **WHEN** 某条降水变量 entry 的累积类型缺失
- **THEN** 报错停止，MUST NOT 静默默认为「自起报累积」

#### Scenario: 累积类型取值越域
- **WHEN** 累积类型取声明取值域之外的值
- **THEN** 报错停止

#### Scenario: 区间累积缺少区间范围
- **WHEN** 累积类型为区间桶但区间范围键缺失
- **THEN** 报错停止

#### Scenario: 源 manifest 缺少 forecast hours 全集
- **WHEN** 源 manifest 缺少 manifest 级 forecast hours 全集键，或其值不是列表
- **THEN** 报错停止，不落到消费端「由实际 entry 反推应有小时表」的自证式回退

#### Scenario: 源 manifest 的 forecast hours 覆盖不全
- **WHEN** 源 manifest 声明的 forecast hours 全集不包含本轮预期的某个 lead
- **THEN** 报错停止，不以副本存在为由声明该轮齐全

#### Scenario: 源侧未声明「请求小时表」不构成失败
- **WHEN** 源 manifest 只声明 forecast hours 全集而未声明「请求小时表」键
- **THEN** 正常产出，本仓产出的 manifest 自行写出该键，取值为本轮预期 lead 全集

#### Scenario: 产出 manifest 的结构可回读
- **WHEN** 对完整 cycle 生成 manifest
- **THEN** 该 JSON 可被 NWM 快照的 manifest 结构原样回读，source 身份段取该源的存储身份拼法，且未经校验的校验和/大小/清单 URI 三个字段留空而非填入本仓臆造的值

#### Scenario: 源 entry 无 idx 键时以 bundle 的 stepRange 自证为自起报累积
- **WHEN** GFS 源 manifest 的 apcp entry 没有 `idx_selectors`/`idx_selector`，其 raw 原件 bundle 恰有一条 `shortName=tp` 记录、`stepRange` 为 `0-3`，lead=3
- **THEN** 落盘 entry 的 `idx_selector` 为 `{"accumulation_type": "cumulative_since_cycle", "step_range": "0-3"}`，R4B2 闸门通过，`raw_root` 之下无任何新增或改动路径（含无 `.idx`）

#### Scenario: 源 entry 无 idx 键时起点非零自证为区间桶
- **WHEN** 同上但该 tp 记录的 `stepRange` 为 `3-6`，lead=6
- **THEN** `idx_selector` 为 `{"accumulation_type": "interval_bucket", "step_range": "3-6"}`

#### Scenario: 零条或多条 tp 记录拒绝
- **WHEN** 该 bundle 没有 `shortName=tp` 记录，或有两条（如 `0-3` 与 `3-6`）
- **THEN** `RawStagingError` kind `accumulation-metadata`，消息列出实测到的每条 `stepRange`（零条时写明零条），work 根内无新增路径

#### Scenario: stepRange 终点与 lead 不符拒绝
- **WHEN** lead=6 但唯一的 tp 记录 `stepRange` 为 `0-3`
- **THEN** `RawStagingError` kind `accumulation-metadata`，不得写入任何 `idx_selector`

#### Scenario: pin 形态优先不重推导
- **WHEN** 源 entry 已带 `idx_selectors`/`idx_selector`
- **THEN** 逐字承接，不打开 bundle，既有用例行为不变

#### Scenario: lstat 闸门之后被换成 symlink 的 bundle 不被跟随
- **WHEN** `_reject_symlinks` 通过后、承接打开之前，该 apcp 原件路径被换成指向 `raw_root` 之外一个合法 GRIB 的 symlink
- **THEN** 不跟随的打开以 `ELOOP` 失败，staging 抛 `RawStagingError` kind `accumulation-metadata`，外部文件的 `stepRange` 不进入任何 manifest，work 根内无新增路径
