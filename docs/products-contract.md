# yd 产物目录契约（v1 草案）

约束对象：yd 循环预报环（生产者，见 [compute-loop-design.md](compute-loop-design.md)）与
水文预报系统 yd-viewer（唯一消费者，只读）。模型包（几何/参数/率定/SHUD 二进制版本）
由 zhaochen 方提供，其变更义务见「模型包条款」。

## 目录布局

```
<products_root>/
  input/yd/gis/river.shp|dbf|prj     # 河网几何（随模型包分发，变更需与产物同步）
  input/yd/gis/domain.shp|dbf|prj    # 流域三角网（用于边界渲染）
  output/<cycle_id>/<source>/        # 每轮 × 每源一个目录，source ∈ {ifs, gfs}
    yd.rivqdown.dat                  # SHUD 原生二进制河道流量（必需，唯一匹配 *.rivqdown.dat）
    meta.json                        # 本轮元数据（degraded 标记、血缘摘要）
    DONE                             # 完成标记：本源本轮全部写完后最后创建
  status.json                        # 循环健康状态（各源最新成功 cycle、失败计数、熔断位）
```

- 27 侧 `<products_root>` = `/home/ghdc/yd`（node-22 视图 `/ghdc/data/yd`）；客户侧部署时约定。

## 条款

1. **cycle_id 命名**：`YYYYMMDDHH`（10 位数字），**UTC**（00/12 两轮）；
   viewer 展示时统一转北京时间。`.dat` 内部时间基准同为 UTC（forcing 起始日即 cycle 日）。
2. **DONE 最后写**：某源某轮全部产物写完后，最后创建空文件 `DONE`。
   无 `DONE` 的目录 viewer 视为进行中，不展示。cycle 目录下任一源 DONE 即该时次可选，
   过程线按实际可用源出曲线（一条或两条）。
3. **格式**：`yd.rivqdown.dat` 为 SHUD 原生二进制输出（little-endian float64，
   rSHUD `readout()` 可读）。列数必须等于 `river.shp` 的 reach 数（当前 3988）；
   不一致 viewer 拒绝展示并报错。
4. **保留与清理**：循环环负责清理 >14 天的 cycle 目录；viewer 只展示最新时次往前
   7 天窗口，删除窗口外目录不影响 viewer。
5. **权限**：产物目录对 viewer 运行账号可读即可；写入者仅循环环。

## 模型包条款（对 zhaochen 方）

- 模型包 = `input/yd/` 全套 + `CALIB/` + SHUD 二进制版本号，整体打 checksum 作状态血缘；
- SHUD 版本升级、输出格式变化、河网重构（reach 数变化）须提前通知：
  几何、率定、二进制、历史状态必须成套更换（旧 warm-start 状态链随包作废，重新 bootstrap）。
