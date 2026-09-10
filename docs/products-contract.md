# yd 产物目录契约（v1）

约束对象：node-22 yd producer 与 node-27 yd-viewer。viewer 是只读消费者；计算过程、状态链和日志不属于展示契约。

## 1. 契约根

同一份 NFS 使用节点本地挂载路径：

- node-22：`/ghdc/data/yd`
- node-27：`/home/ghdc/yd`

下文统一称 `<YD_ROOT>`。

## 2. 目录布局

```text
<YD_ROOT>/
  input/
    viewer/
      rivers.geojson
      boundary.geojson
  output/
    <cycle_id>/
      gfs/
        yd.rivqdown.dat
        DONE
      ifs/
        yd.rivqdown.dat
        DONE
```

viewer 只需要读取 `input/viewer` 与 `output`。`models`、`states`、`logs` 和 scratch 工件不属于 viewer 契约，也不得暴露为 viewer API；其中 prepared model variant 另受下列 producer 内部交接合同约束。

每个 `config.toml variants.<source>` 指向完整 yd native 变体：`yd.cfg.ic`、`yd.cfg.para`、`yd.cfg.calib`、`yd.sp.mesh`、`yd.sp.att`、`yd.sp.riv`、`yd.sp.rivseg`、`yd.para.lc`、`yd.para.soil`、`yd.para.geol`、`yd.tsd.lai`、`yd.tsd.mf`，加 opaque `yd.binding` 与固定 `yd.direct-grid-handoff.json`，共十四个顶层文件。handoff schema 为 `yd.prepare.direct-grid-handoff.v2`，保留 source/project、四个 builder 声明的稳定版本标识与既有 direct-grid contract；`file_checksums` 用现有 `sha256:<64 lowercase hex>` 绑定十三个非 manifest 文件，contract 的 binding/sp.att checksum 与之相符。project 仍来自唯一顶层率定态文件名，不从变体目录名推导。它不是动态 registry 或 viewer API，也不包含 cycle/work/job。旧 five-only/v1 需重新 prepare，不自动兼容；真实 prepare/native 适配业务代码归 M2，M4 只核对现场数值与 receipt。固定文件细节见 [native-yd-model-input](../openspec/changes/native-yd-model-input/design.md)，不建设通用资产角色框架。

`YD_ROOT` 顶层名字以 `.yd-prepare-staging`（代码权威为 `prepare._STAGING_PREFIX`）开头的条目，是一次性 `prepare` 在同盘 rename 前使用的保留临时命名空间。它只允许在一个已授权、仍在运行的 `prepare` 生命周期内短暂存在，不得放进 `input/viewer/`，也不得挂载或暴露给 viewer。进程中断后留下的匹配条目不是下一次运行可自动认领的产物：后续 `prepare` 必须列出全部残留并拒绝，不得自动删除；运维确认没有活动 `prepare` 后按 [agent-ops.md](agent-ops.md) 的人工程序处理。

## 3. cycle 与 source

1. `cycle_id` 固定为 10 位数字 `YYYYMMDDHH`，使用 UTC；本期只生产 00Z、12Z。
2. `source` 固定为小写 `gfs` 或 `ifs`。
3. IFS/GFS 独立发布；cycle 下任一 source 完成即可展示。
4. viewer 页面可把 UTC 时间格式化为北京时间，但 API 中的绝对时刻使用带 `Z` 的 UTC。

## 4. 完成语义

1. `DONE` 是空文件，也是唯一完成判据。
2. producer 必须先完成本轮 DAT 和下一轮 warm-start 状态的正式提交，最后才创建 `DONE`。
3. 无 `DONE` 的 source 目录视为临时或失败结果，viewer 不枚举、不读取。
4. 重复运行看到 `DONE` 时将该 source/cycle 视为已完成，不覆盖正式产物。
5. 不使用 `meta.json`、`status.json` 或第二套完成状态。

## 5. `yd.rivqdown.dat`

### 5.1 文件格式

只支持当前 SHUD v2 二进制：

- 文件开头为 1024 字节文本头；
- 其后是 little-endian float64 形式的起始日期、列数和列编号表；
- 数据区每行包含一个相对分钟值和所有河段流量值；
- 当前河段数为 3988，编号来自 yd 河网的 SHUD `Index`；
- 格式权威是当前 SHUD 写出代码和 rSHUD `readout()` 的 v2 分支。

producer 与 viewer 随同一契约升级；本契约不要求兼容 v1，也不规定残行修复。

### 5.2 时间轴

producer 固定：

```text
START = 0 day
END = 7 days
DT_QR_DOWN = 60 minutes
```

因此每个正式文件必须包含 168 行，数据区第 0 列依次为：

```text
0, 60, 120, …, 10020
```

绝对时间只按以下规则计算：

```text
valid_time = UTC(cycle_id) + relative_minutes
```

不得仅用 v2 日期头作为 12Z 的绝对时间锚。00Z 与 12Z 使用同一算法。

每行代表标签之后一小时区间内的平均河道流量：

- 第 1 行：`[cycle, cycle+1h)`；
- 最后一行：`[cycle+167h, cycle+168h)`。

### 5.3 单位

DAT 中的 `rivqdown` 单位为 m³/day。viewer API 返回和页面展示统一转换为 m³/s：

```text
value_m3s = value_m3day / 86400
```

## 6. 几何

`rivers.geojson` 和 `boundary.geojson` 必须是 EPSG:4326。

- `rivers.geojson` 当前包含 3988 条河段；
- 每条河段带 SHUD `reach_id`，用于定位 DAT 对应流量列；
- `boundary.geojson` 是 yd 流域边界；
- 几何由 producer 的一次性 `prepare` 生成，模型变体更新时成套替换；
- viewer 运行时不读取 shapefile，也不做投影转换。

## 7. 窗口与清理

1. viewer 枚举最新成功 cycle 往前 7 天；锚点是最新 `DONE` cycle，不是墙钟。
2. producer 保留最新成功 cycle 往前 14 天；窗口外 source 目录可删除。
3. 计算停更时最后一批完成产物仍可展示，不应因墙钟推进而把页面自动清空。
4. 状态和日志的保留规则属于 [compute-loop-design.md](compute-loop-design.md)，不影响 viewer 契约。

## 8. 权限与所有权

- producer 账户是 `output` 的唯一写入者；
- node-27 `nwm` 账户只需对 `input/viewer` 和 `output` 有目录遍历与读取权限；
- viewer 容器挂载必须为只读；
- NWM raw object store 不在 `<YD_ROOT>` 内，生命周期归 NWM，yd 无权修改或清理。

## 9. 变更规则

以下变化属于契约变更，必须先修改并提交本文件、`design.md` 和 `compute-loop-design.md`，再修改 producer/viewer。本期禁止在现有 `YD_ROOT` 原地覆盖模型变体、状态或几何；升级必须使用干净 staging 根完成重新 prepare/init 和 22→27 真闭环，再另行批准切换：

- SHUD 二进制格式或版本；
- 河网编号或 reach 数；
- 输出间隔、预报长度或流量单位；
- cycle/source 命名；
- 目录布局或 `DONE` 语义；
- 几何投影与 `reach_id` 映射。
