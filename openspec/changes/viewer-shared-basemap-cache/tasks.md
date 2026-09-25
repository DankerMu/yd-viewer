## 0. Risk packs

- Config / project setup — 选中：compose 示例与 entrypoint → 3.1/3.2、2.1/2.2。
- File IO / path safety / overwrite — 选中：命中刷新 mtime、不删除瓦片 → 1.1/1.2。
- Auth / permissions / secrets — 选中：umask 002、`group_add`、`PermissionError` → 1.2、2.2、3.2、4.3。key 不进日志保持既有测试。
- Concurrency / shared state / ordering — 选中：读后被 NWM 删除 → 1.2（`FileNotFoundError` 仍 hit）；清理/刷新竞态 → design Non-goals 接受。
- Error handling — 选中：`OSError` 吞咽 → 1.2。
- Release / packaging — 选中：compose 示例、entrypoint、镜像构建 → 4.3 `docker build` 与容器实测。
- Legacy compatibility — 选中：反代既有场景全保留 → 4.1 既有测试全绿。
- Resource limits — 不选：配额/清理归 NWM#2627。
- Public API / Schema — 不选：路由、响应头、目录布局不变。
- Documentation / migration — 不选：docs 由 #353、#355 先行合并（本分支已 rebase 其后）；node-27 迁移另行授权。

## 1. 反代命中刷新 mtime

- [ ] 1.1 `viewer/src/yd_viewer/basemap.py` `_read_cached`：正文判定为 PNG/JPEG 后，若 `time.time() - st_mtime > 86400` 则 `os.utime(path)`（times=None）；`OSError` 吞掉，仍返回命中；未满 24 h 不调用 `os.utime`；不删除任何瓦片
- [ ] 1.2 `viewer/tests/test_basemap.py`：mtime 设为 25 h 前的命中文件，响应 hit 且文件 mtime 与当前时间差 < 60 s；1 h 前的 mtime 前后相等；monkeypatch `os.utime` 抛 `PermissionError` 与 `FileNotFoundError` 时响应均 200、`X-Tile-Cache: hit`、正文不变；非图片缓存文件仍按未命中处理且 mtime 不变

## 2. entrypoint umask

- [ ] 2.1 `viewer/entrypoint.sh`：`set -eu` 之后、生成 basemaps.json 之前执行 `umask 002`
- [ ] 2.2 `viewer/tests/test_entrypoint.sh`：在 umask 022 下运行 entrypoint，sentinel uvicorn 记录的 `umask` 为 `0002`，生成的 `basemaps.json` 权限为 `664`；既有 basemaps 生成与 URL/key 不进日志断言保持通过

## 3. compose 示例

- [ ] 3.1 `viewer/compose.example.yml`：缓存挂载改为占位 bind `/example/host/basemap-cache/tianditu:/cache`（不带 `:ro`），service 加 `group_add: ["1000"]`（占位，注释：取共享缓存目录属组 gid，现场值见 agent-ops §16.2）；删除顶层 `volumes:` 与命名卷；头部注释同步
- [ ] 3.2 `viewer/tests/test_container_contract.py`：volumes 恰三条——两条 `/` 开头且 `:ro`，一条 `/` 开头、以 `:/cache` 结尾、不带 `:ro`；`group_add` 恰一条；无顶层 `volumes:`；`yd-` 前缀检查不再含命名卷；仍断言不含 `YD_ROOT`

## 4. 验证（输入 → 预期）

- [ ] 4.0 前置：`git merge-base --is-ancestor <#355 merge commit> HEAD` → exit 0（docs 先于代码）
- [ ] 4.1 `cd viewer && uv run ruff check . && uv run ruff format --check . && uv run pytest` → 全绿；`bash viewer/tests/test_entrypoint.sh`（非 root）→ exit 0
- [ ] 4.2 `openspec validate viewer-shared-basemap-cache --strict --no-interactive` → valid
- [ ] 4.3 `docker build -f viewer/Dockerfile -t yd-viewer:t354 .` 成功；用 root 辅助容器在命名卷中预置 `vec/3/6/3`：PNG 内容、属主 4242:1000、`664`、目录 `2775` 属组 1000、`touch -d '25 hours ago'`；以该卷挂 `/cache`、`group_add 1000`、`YD_TIANDITU_KEY=dummy` 起容器：`docker exec <c> grep Umask /proc/1/status` → `Umask:	0002`；`curl -s -o /dev/null -D - http://127.0.0.1:<p>/api/basemap/tianditu/vec/3/6/3`（GET；路由只注册 GET，HEAD 会落到 `/api` 404）→ 200 且 `X-Tile-Cache: hit`；辅助容器 `stat -c %Y` → 与当前时间差 < 60 s（非属主经组写权限刷新成功）。假 key 下未命中回 502 不写盘，故 issue 验收的「未命中新建目录 775、文件 664」以 PID 1 `Umask: 0002` 加 2.2 的 `basemaps.json` 664 间接证明，现场直接证据留待 node-27 部署 receipt
