# viewer-container Specification

## Purpose
TBD - created by archiving change m3-viewer. Update Purpose after archive.

## Requirements

### Requirement: 单镜像构建
`viewer/Dockerfile` MUST 多阶段构建：Node 22 阶段执行 `corepack pnpm install --frozen-lockfile && pnpm build`，Python 3.12 阶段以 `uv sync --frozen --no-dev` 安装后端并复制前端 `dist` 到镜像内固定目录，该目录以 `ENV YD_VIEWER_STATIC_DIR=<路径>` 写死且对运行用户可写；运行阶段 MUST 以非 root 用户启动 `viewer/entrypoint.sh`，MUST NOT 包含 Node、pnpm、uv 缓存或 dev 依赖。

#### Scenario: 本地构建
- **WHEN** 在仓库根执行 `docker build -f viewer/Dockerfile .`
- **THEN** 构建成功

#### Scenario: 非 root 且静态目录可写
- **WHEN** 检查镜像的 `USER` 与 `YD_VIEWER_STATIC_DIR` 目录属主
- **THEN** `USER` 不是 root，且该目录对该用户可写并含 `index.html`

### Requirement: entrypoint 生成 basemaps.json
`viewer/entrypoint.sh` MUST 在任何写盘之前执行 `umask 002`（与 NWM 共享的瓦片缓存须组可写：新建目录 `775`、文件 `664`），并 MUST 在启动 uvicorn 前写出 `$YD_VIEWER_STATIC_DIR/basemaps.json`：若 `YD_TIANDITU_KEY` 非空（反代模式），MUST 写出固定六条相对路径 `api/basemap/tianditu/<layer>/{z}/{x}/{y}`（`vector`=vec/cva、`satellite`=img/cia、`terrain`=ter/cta，注记非 null）并忽略六个 `YD_BASEMAP_*_URL`；否则读取 `YD_BASEMAP_VECTOR_URL`、`YD_BASEMAP_SATELLITE_URL`、`YD_BASEMAP_TERRAIN_URL` 与对应 `*_ANNOTATION_URL`：对每个设置了底图 URL 的键生成 `{"tiles":[url],"annotation":[url] 或 null}`；未设置底图 URL 的键 MUST 缺席；三者全缺时 MUST 写出 `{}`。URL 与 key MUST 原样使用、MUST NOT 打印到日志。随后 MUST `exec uvicorn` 监听 `0.0.0.0:8000`（容器内端口固定，不读 env）。shell 单测 MUST 以非 root 用户运行。

#### Scenario: 两种底图
- **WHEN** 只设置 `YD_BASEMAP_VECTOR_URL`、`YD_BASEMAP_VECTOR_ANNOTATION_URL`、`YD_BASEMAP_SATELLITE_URL`
- **THEN** JSON 含 `vector`（annotation 非空）与 `satellite`（annotation 为 null），无 `terrain`

#### Scenario: 全缺
- **WHEN** 六个变量都未设置
- **THEN** 写出 `{}` 且 entrypoint 以 exit 0 进入 `exec uvicorn`（容器内 8000 `/api/health` 可达属 M5 现场 receipt，M3 不断言）

#### Scenario: URL 不进日志
- **WHEN** 设置含 `tk=SECRET` 的底图 URL 并启动
- **THEN** 容器 stdout/stderr 不含 `SECRET`

#### Scenario: 反代模式
- **WHEN** 设置 `YD_TIANDITU_KEY=SECRET` 且同时设置任意 `YD_BASEMAP_*_URL`
- **THEN** JSON 恰为三键，`vector.tiles` 为 `["api/basemap/tianditu/vec/{z}/{x}/{y}"]`、`vector.annotation` 为 `["api/basemap/tianditu/cva/{z}/{x}/{y}"]`（satellite=img/cia、terrain=ter/cta 同理），URL env 被忽略，stdout/stderr 不含 `SECRET`

#### Scenario: umask 先于写盘
- **WHEN** 检查 `viewer/entrypoint.sh`
- **THEN** `umask 002` 出现在生成 basemaps.json 的 `uv run` 与 `exec uvicorn` 之前

### Requirement: compose 示例与 env 清单
`viewer/compose.example.yml` MUST 只读挂载 `input/viewer` 与 `output` 两个目录（显式 `:ro`），另以恰一条可写 bind（不带 `:ro`）挂瓦片缓存目录到 `/cache`，并以恰一条 `group_add` 加入该缓存目录的属组；MUST NOT 声明命名卷；端口映射 `127.0.0.1:${YD_VIEWER_PORT}:8000`，`env_file` 指向不入库的 env 文件；project、service/container、network、image MUST 带 `yd-` 前缀；MUST NOT 挂载整个 `YD_ROOT`；三条 bind 源路径与 `group_add` gid MUST 为占位值，MUST NOT 写入现场路径（现场值见 agent-ops §16.2）。`viewer/env.example` MUST 列出 `YD_VIEWER_INPUT_DIR`、`YD_VIEWER_OUTPUT_DIR`、`YD_VIEWER_PORT`、`YD_TIANDITU_KEY`、`YD_BASEMAP_CACHE_DIR` 与六个 `YD_BASEMAP_*`（无真实值），MUST NOT 含 `YD_VIEWER_STATIC_DIR`。镜像 MUST 预建 `/cache` 且属运行用户。

#### Scenario: 挂载只读
- **WHEN** 检查 compose 文件的 volumes
- **THEN** 恰三条 bind 挂载：两条以 `:ro` 结尾，一条目标为 `/cache` 且不带 `:ro`；`group_add` 恰一条；无顶层 `volumes:` 与命名卷

#### Scenario: 端口映射
- **WHEN** 检查 compose 文件的 ports
- **THEN** 恰一条且为 `127.0.0.1:${YD_VIEWER_PORT}:8000`

#### Scenario: yd 前缀
- **WHEN** 检查 compose 文件的 `name`、service、`container_name`、network 与 `image`
- **THEN** 均以 `yd-` 开头

### Requirement: CI 前端 job
`.github/workflows/ci.yml` MUST 新增 `viewer-frontend` job，在 `viewer/frontend` 执行 `corepack pnpm install --frozen-lockfile`、`pnpm typecheck`、`pnpm test`、`pnpm build`；现有 `producer`、`viewer-backend`、`openspec` job MUST 不变。

#### Scenario: job 存在
- **WHEN** 解析 ci.yml
- **THEN** 存在名为 `viewer-frontend` 的 job 且四条命令按序出现
