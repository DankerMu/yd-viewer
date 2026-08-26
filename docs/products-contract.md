# yd 产物目录契约（v0 草案，待计算方确认）

约束对象：SHUD 计算方（产物生产者）与 水文预报系统 yd-viewer（唯一消费者，只读）。

## 目录布局

```
<products_root>/
  input/yd/gis/river.shp|dbf|prj     # 河网几何（随 basin 包分发，变更需与产物同步）
  input/yd/gis/domain.shp|dbf|prj    # 流域三角网（用于边界渲染）
  output/<cycle_id>/                 # 每轮预报一个目录
    yd.rivqdown.dat                  # SHUD 原生二进制河道流量（必需，唯一匹配 *.rivqdown.dat）
    DONE                             # 完成标记（见下）
    ...                              # 其余 SHUD 输出可共存，viewer 忽略
```

- 27 侧 `<products_root>` = `/home/ghdc/yd`（node-22 视图 `/ghdc/data/yd`）；客户侧由部署时约定。

## 条款

1. **cycle_id 命名**：`YYYYMMDDHH`（10 位数字），按北京时间。【待确认：与 .dat 内部
   起始日期的时间基准是否同为北京时间；若计算按 UTC，请明示，viewer 侧统一换算】
2. **DONE 最后写**：本轮全部产物写完后，最后创建空文件 `DONE`。无 `DONE` 的目录
   viewer 视为进行中，不展示。
3. **格式**：`yd.rivqdown.dat` 为 SHUD 原生二进制输出（little-endian float64，
   rSHUD `readout()` 可读）。列数必须等于 `river.shp` 的 reach 数（当前 3988）；
   不一致 viewer 会拒绝展示并报错。
4. **变更通知**：SHUD 版本升级、输出格式变化、河网重构（reach 数变化）需提前通知
   viewer 维护方，几何与产物必须成对更新。
5. **保留与清理**：旧时次的删除由计算方/磁盘管理方负责；viewer 只展示最新时次往前
   7 天窗口内的时次，删除窗口外目录不影响 viewer。
6. **权限**：产物目录对 viewer 运行账号可读即可，viewer 不写产物目录。
