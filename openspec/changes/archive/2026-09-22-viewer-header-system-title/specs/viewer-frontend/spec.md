## MODIFIED Requirements

### Requirement: 页头
页头 MUST 显示系统标题「永登流域水文模拟系统」、最新可用 cycle 的起报时间（北京时间，标「起报」与「北京时间」）与「流量 (m³/s)」文案，标题位于其余两行之前；文档 `<title>` MUST 为同一标题；MUST NOT 显示停更原因、source 失败或任何内部计算状态；无可用 cycle 时显示「暂无数据」。

#### Scenario: 页头时间
- **WHEN** `map/latest` 的 cycle 为 `2026082712`
- **THEN** 页头含 `2026-08-27 20:00`

#### Scenario: 页头标题
- **WHEN** 页头以任意 cycle（含 `null`）渲染
- **THEN** 标记中含「永登流域水文模拟系统」，且其出现在「流量 (m³/s)」之前；`cycle` 为 `null` 时仍含「暂无数据」
