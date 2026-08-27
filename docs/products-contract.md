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

viewer 只需要读取 `input/viewer` 与 `output`。`models`、`states`、`logs` 和 scratch 工件不属于本契约，也不得暴露为 viewer API。

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
