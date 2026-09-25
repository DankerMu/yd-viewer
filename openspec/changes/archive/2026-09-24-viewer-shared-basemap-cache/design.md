## Context

node-27 上 yd 反代与 NWM 反代（`NWM/apps/api/routes/basemap.py`）使用同一 key、同一上游、同一 `<layer>/<z>/<x>/<y>` 布局与 tmp + `os.replace` 写法。用户裁决（2026-09-25）两边共用 `/home/nwm/.cache/nhms/mvt/basemap/tianditu`，NWM 以 mtime 30 天判冷每日清理（DankerMu/SHUD-NWM#2627）。docs：design §6.1、agent-ops §9.2/§16.2（PR #353、#355，均已先行合并）。

## Governing invariant

- yd 永不删除缓存瓦片；唯一删除是自身写失败残留的 `tmp-<hex>`（`basemap.py` `_write_cached` 既有行为）。
- 命中且 mtime 早于 now − 86400 s 时 `os.utime(path)`（times=None，只需写权限，非属主经组写即可）；否则不写。任何 `OSError`（含 `PermissionError`、并发删除导致的 `FileNotFoundError`）吞掉，命中响应不变。
- 先判 PNG/JPEG 签名再刷新：非图片缓存文件仍按未命中处理，且不刷新。
- entrypoint 在 `set -eu` 后、任何写盘前 `umask 002`，`exec` 后 uvicorn 继承：新建目录 775、文件 664；属组靠现场目录 setgid（运维一次性设置，agent-ops §9.2），不在代码里 chown/chmod。

## Sibling surfaces

- NWM 清理任务（SHUD-NWM#2627）：依赖 yd 的 mtime 刷新语义（24 h 节流、30 天判冷）与 664/775 组可写。
- NWM 反代写入：同一目录并发 tmp + `os.replace`，yd 读到的永远是完整文件；NWM 的 tmp 名 `.<y>.<pid>.<tid>.<hex>.tmp` 与 yd 的 `tmp-<hex>` 不冲突。
- `_write_cached`（未命中路径）：mkdir/写入受新 umask 影响，逻辑不改。
- entrypoint 写 `basemaps.json`：在 umask 002 下变为 664，镜像内文件，无外部影响。
- `viewer-config-health`：`YD_BASEMAP_CACHE_DIR` 语义不变；Dockerfile 预建 `/cache` 保留（成为挂载点）。

## Decisions

- 节流阈值 24 h 固定常量，不加 env：每张瓦片每天最多一次 utime，爬虫压测下写盘可忽略。
- 用 `os.utime(path)` 而非 `Path.touch()`：后者在文件被并发删除时会重新创建空文件。
- compose 示例的 bind 源与 gid 用占位值（`/example/host/basemap-cache/tianditu`、`"1000"`），与既有占位风格一致；现场值只在 agent-ops §16.2。

## Non-goals

- 清理、配额、监控；bbox 或 z 上限；新 env；Dockerfile 改动；node-27 迁移与部署（另行授权）；NWM 代码。
- 清理与刷新的竞态（NWM stat 后 yd 刷新、NWM 仍删除）：只导致该瓦片下次回源一次，接受，不处理。

## Review focus

- `_read_cached` 的签名判定、stat、utime 顺序与异常吞咽范围（只吞 `OSError`，不吞其它）。
- 测试是否真的以"文件 mtime 前后值"断言，而非只断言调用。
- compose 合同测试是否仍拒绝整个 `YD_ROOT` 挂载与现场路径。
