# M2 Producer 基础 — 任务分解

任务组按依赖排序（每组的"依赖"行给出真实前置，未列即可与前面各组并行）；全部落 `producer/`，本地测试是唯一门禁。每组尾部标注 compute-loop §13.1 归属行。

## 1. cli-config：配置装载与 CLI 骨架

- [x] 1.1 实现 `config.toml` 类型化装载与 fail-closed 校验（业务规则字段全集含 `reach_count`，spec cli-config）
- [x] 1.2 实现 `local.toml` 现场值装载（含 NWM checkout 根），缺失即报错、零内置默认
- [x] 1.3 实现 argparse 三入口骨架（prepare/init/run 薄委托，注册 `[project.scripts]` 入口点）、未知子命令拒绝、`DATABASE_URL` 环境守卫、run 入口状态目录缺失/为空即报错停止且不触发 init 逻辑
- [x] 1.4 实现 NWM 解释器薄外壳（精确路径调用、cwd/`PYTHONPATH` 取自 checkout 字段、fail-closed），以假解释器脚本测试调用形态

依赖：无
§13.1 归属：无直接行（基础设施，支撑全部行）
Suggested fixture level: compact - 内联 TOML 与假解释器小脚本即可覆盖全部场景
Minimal mergeable slice: 配置装载器（1.1–1.2）——纯函数加测试，可独立合并保绿，CLI 入口为后继

### Issue #2 fixture（任务 1.1–1.2）

Fixture level: expanded
Upstream suggested level: compact（override：配置对象是后续 26 个 issue 全部消费的共享契约，且改动面命中强制 expanded 触发词 parser/reader/schema/field/format）
Repair intensity: medium
Project profile: yd-viewer

Change surface:
- 新增 `producer/src/yd_producer/config.py`：`load_config(path) -> Config`、`load_local(path, config) -> LocalConfig`、公开异常 `ConfigError` 及其 dataclass 树
- 新增 `producer/tests/test_config.py`
- 不触碰 CLI 入口、解释器薄外壳、任何业务模块

Must preserve:
- `producer/pyproject.toml` 的 `dependencies = []`（仅 stdlib `tomllib`，D5）
- 现有 `tests/test_smoke.py` 继续通过

Must add/change:
- 按下方「TOML key schema」装载 `config.toml` 与 `local.toml`，返回类型化 dataclass 树
- 任何必需字段缺失或类型错误 fail closed，错误信息含该字段的完整点分路径（如 `raw.gfs.variables`）；代码中零内置现场默认值
- 两个装载器的全部失败路径抛同一个公开异常类型 `yd_producer.config.ConfigError`（本 fixture 钉死类名），不抛裸 `KeyError`/`TypeError`/`tomllib.TOMLDecodeError`

TOML key schema（本 issue 钉死；下游 issue 与生产 `config.toml` 实例必须对齐此 schema）:

`config.toml` — 全部 key 必需，无可选字段：

```toml
# 顶层 key 名由 spec cli-config 反引号逐字钉死，不得加表前缀
forecast_days = 7               # START=0 / END=7（products-contract §5.2）
output_interval_minutes = 60    # DT_QR_DOWN
checkpoint_hours = [12]         # T+12 捕获点
reach_count = 3988              # products-contract §5.1；run-controller spec 的 forecast_days*24 行校验同源
# NWM@8ae9b8f2 workers/mapping_builder/cli.py —— 版本化快照事实，非现场值（归属裁决见 #32）
nwm_mapping_builder_module = "workers.mapping_builder.cli"

[cycle]
hours = [0, 12]                 # 仅接受 00Z/12Z（compute-loop §7.1）

[variants]
gfs = "input/models/yd_gfs"     # 相对 yd_root（compute-loop §6.1）
ifs = "input/models/yd_ifs"

[raw.ifs]
lead_hours = [0, 3, 6]          # 该源预期 lead 全集（示例为合成值）
variables = ["<变量名>"]         # 值由 issue #4 勘察与 #6 判定确立，本 issue 只定 schema
bundles = ["<bundle 文件名模式>"]
f000_special = false            # IFS 无 f000 特例

[raw.gfs]
lead_hours = [0, 3, 6]
variables = ["<变量名>"]
bundles = ["<bundle 文件名模式>"]
f000_special = true             # GFS f000 特例（compute-loop §7.1）

[slurm]
required_fields = ["partition", "account", "cpus", "memory", "walltime"]
```

`raw.<source>.lead_hours` 是该源本轮**预期 lead 的全集**，逐源而非两源共用。理由（round 1 审核 cand-04，verifier CONFIRMED/FIX_NOW）：

- raw-scan spec §"完整性判定" 的 Scenario 要求"判定不完整并**列出缺失文件**"，而预期文件集 = `lead_hours` × `bundles`；没有 lead 全集就无法发现**中间某个 lead 缺失**，只能靠"末 lead 存在"之类的推断——那是同一 spec 明令禁止的；
- compute-loop §5 把"IFS/GFS raw 完整性规则"归入 `config.toml`，所以 lead 全集的归属是 config 而非 #6 的代码；
- 单一步长表达不了真实形态：§7.3 记载 forcing 原生 3 小时，而 GFS/IFS 的 lead 步长逐源且可能分段变化，故用显式列表而非 `step`；
- 不设 `[cycle].lead_hours_start/end`：那会与逐源 lead 列表构成第二权威，正是本 fixture 为 `[slurm]` 消除的形态。0–168h 的覆盖范围由列表自身的首末元素表达。

取值与 `variables`/`bundles` 同等待遇——本 issue 只钉 schema 与类型，真实取值由 #4 勘察与 #6 判定确立，测试用合成值。空列表的拒绝归 #6（见 Non-goals）。

`nwm_mapping_builder_module` 是 `prepare` 薄外壳要以 NWM 解释器调用的 module 点分名（spec cli-config「以该路径调用**配置的** mapping-builder module」）。它落在 `config.toml` 而非 `local.toml`，是 **#32 更正 5 的用户裁决**：module 名随 NWM 快照固定、不随现场变化，属 `docs/agent-ops.md` §7.2「已确认的」版本化事实；放 `local.toml` 等于要求每个部署点重填一个非现场值，放版本化常量则要放宽 spec 的「配置的」限定语。取值 `workers.mapping_builder.cli` 由对 pin 的只读取证确立（`git -C <NWM> ls-tree 8ae9b8f2 workers/__init__.py workers/mapping_builder/__init__.py` 两者均在，`workers/mapping_builder/cli.py` 末尾有 `if __name__ == "__main__"` 守卫，故 `-m` 形态在 cwd=`checkout_root` 下可解析可执行）；取证与「#4 不会产出该名字（mapping-builder 不快照）」的说明记在 **#32**。装载层对它与其它标量同等待遇——只校验存在性与 `str` 类型，**不校验该 module 是否可导入**（那需要 NWM 环境，归 1.4 的运行期 fail-closed 与 prepare 编排）。

`[slurm].required_fields` 即 compute-loop §5 的"Slurm 资源配置字段结构"：config 侧声明必需字段名，值只在 `local.toml` 提供。**它是 `local.toml` 的 `[slurm]` 表唯一的键集权威**——装载器不对 `local.[slurm]` 另设静态 schema，只按这份运行期列表校验，避免"静态 schema 与列表互相矛盾"的双权威。约束：

- `required_fields` MUST 是非空字符串列表，元素唯一；
- `local.[slurm]` 的键集 MUST 与 `required_fields` **完全相等**：缺项与多余项都抛 `ConfigError` 并指名该键（多余项必须报错，否则现场把 `partition` 写成 `partiton` 会被静默忽略）;
- `LocalConfig.slurm` 以映射（`dict[str, str | int]`）暴露，不做成五个固定 dataclass 字段——固定字段等于把键集第二次写死在代码里，正是这条要消除的双权威。

因此交叉校验要求先有 `Config`，`load_local` 的签名为 `load_local(path, config) -> LocalConfig`。

`local.toml` — `[slurm]` 之外全部 key 必需，无可选字段（NWM checkout/解释器仅 `prepare` 消费，但装载期一律必需：做成条件可选就必须引入条件性缺省，正是"零内置默认"禁止的）：

```toml
yd_root = "<绝对路径>"
scratch_root = "<绝对路径>"
shud_binary = "<绝对路径>"

[nwm]
raw_root = "<绝对路径>"
checkout_root = "<绝对路径>"
python = "<解释器绝对路径>"

[slurm]
# 键集完全由 config.toml 的 slurm.required_fields 决定，此处仅示例其生产取值形态
partition = "<名称>"
account = "<名称>"
cpus = 8
memory = "<如 32G>"
walltime = "<如 04:00:00>"

[cron]
lock_path = "<绝对路径>"
log_dir = "<绝对路径>"
```

Seams under test:
- `load_config(path) -> Config` 与 `load_local(path, config) -> LocalConfig`（file→object 纯函数，design.md「Sketch seams under test」之下的基础层；CLI 入口层不做**业务行为**测试，入口层自身契约见 design.md seam 6，由 issue #3 行使）

Risk packs considered (core):
- Public API / CLI / script entry: selected - 装载器是 `prepare`/`init`/`run` 三入口共用的公共 API，字段名与返回结构即契约
- Config / project setup: selected - 本 issue 全部内容
- File IO / path safety / overwrite: not selected - 只读单个 TOML 文件，不写、不删、不遍历目录；`local.toml` 内的路径本 issue 只装载不解引用（路径存在性校验归 1.3/1.4 与各业务模块）
- Schema / columns / units / field names: selected - config 字段全集即 schema，字段名/类型/单位（minutes、hours、days）错配会传导到全部下游模块
- Auth / permissions / secrets: not selected - `local.toml` 已 gitignored，字段为路径与 Slurm 资源，无凭据；装载器不打印文件内容
- Concurrency / shared state / ordering: not selected - 无共享状态、无并发，纯函数
- Resource limits / large input / discovery: not selected - 输入为人工维护的小 TOML，无发现逻辑
- Legacy compatibility / examples: not selected - 首个功能模块，仓库此前无业务代码，无既有消费者
- Error handling / rollback / partial outputs: selected - fail-closed 是本 issue 的核心验收项，需要稳定异常类型与含字段名的错误信息，且不得返回带默认值的半成品配置对象
- Release / packaging / dependency compatibility: selected - 必须只用 stdlib `tomllib`，不得引入新依赖，`uv sync --frozen` 无 drift
- Documentation / migration notes: not selected - 无迁移；字段清单由本 fixture 的「TOML key schema」钉死，下游 issue 直接读该 schema，无需另写文档

Domain packs (from active profile):
- Geospatial / CRS: not selected - 无几何
- Time series / forcing / temporal boundaries: selected - `cycle.hours`、`raw.<source>.lead_hours`、`forecast_days`、`output_interval_minutes`、`checkpoint_hours` 的取值与单位在此定型，错配直接污染 forcing 与 tracker
- 状态链 / warm-start 定戳: not selected - 本 issue 不读写状态
- NWM 快照溯源 / DB-free 隔离: not selected - 本 issue 无快照代码

Required evidence（每条 input -> expected output）:
- 齐备 `config.toml`（内联 TOML）-> 返回 `Config`，逐字段值与文件一致（含 `raw.ifs`/`raw.gfs` 嵌套表、`slurm.required_fields`、顶层 `reach_count`）
- 齐备 `local.toml` -> 返回 `LocalConfig`，暴露全部现场字段供三入口使用
- **参数化：** 对上方 schema 中 `config.toml` 的每个必需 key 各生成一份"删该 key"的 TOML -> 每份都抛 `ConfigError`，消息含该 key 的完整点分路径；测试以 schema 的必需 key 清单驱动，新增字段不加测试即漏测
- **参数化：** 对 `local.toml` 的每个必需 key 同上 -> 每份都抛 `ConfigError`，消息含完整点分路径
- 类型错误 `reach_count = "3988"`（字符串）-> 抛 `ConfigError`，消息含 `reach_count` 与期望类型
- 类型错误 `cycle.hours = 0`（非列表）-> 抛 `ConfigError`，消息含 `cycle.hours` 与期望类型
- `local.toml` 路径不存在 -> 抛 `ConfigError`，消息提示需现场创建，且不返回任何对象
- TOML 语法损坏（如未闭合字符串）-> 抛 `ConfigError`（不外泄 `tomllib.TOMLDecodeError`）
- **参数化：** 对 `slurm.required_fields` 的每一项，各生成一份"`local.[slurm]` 删该项"的 TOML -> 每份都抛 `ConfigError`，消息含该缺失键名
- `local.[slurm]` 含 `required_fields` 之外的多余键（如把 `partition` 误写为 `partiton`）-> 抛 `ConfigError`，消息含该多余键名（不得静默忽略）
- `slurm.required_fields = []`（空列表）-> 抛 `ConfigError`；元素重复 -> 抛 `ConfigError`；元素非字符串 -> 抛 `ConfigError`
- `required_fields` 增删一项后 `local.[slurm]` 同步增删 -> `load_local` 成功，`LocalConfig.slurm` 映射键集等于 `required_fields`（证明键集权威唯一，代码未第二次写死五个字段）
- 零默认值行为断言：`Config`/`LocalConfig` 及其全部嵌套 dataclass 的字段均无默认值（以 `dataclasses.fields()` 断言每个 field 的 `default` 与 `default_factory` 均为 `MISSING`），因此任何缺失只能走 fail-closed 路径而非静默填值
- 全部失败路径断言以 `pytest.raises(ConfigError)` 表达，不接受 `pytest.raises(Exception)`
- **字段定位可机检**：`ConfigError` MUST 暴露结构化属性 `path: str | None`，凡涉及具体字段的失败（缺字段、类型错误、`[slurm]` 键集不等）都 MUST 设为该字段的完整点分路径；全部缺字段与类型错误用例 MUST 断言 `excinfo.value.path == <期望点分路径>`，MUST NOT 仅用 `<key> in str(exc)` 子串探测。理由（round 1 审核 cand-08/cand-09，verifier CONFIRMED/FIX_NOW）：子串探测对"一次列出全部必需项"的消息恒真——实测把 `_require` 换成固定目录消息后 53 条测试全绿，25 条参数化用例合起来只等价于一条；且顶层标量类型错误可以整个丢掉字段名而无人发现。消息中仍保留人读的路径，但断言以 `path` 属性为准，与措辞解耦
- 非 UTF-8 编码的 `config.toml` / `local.toml`（GBK 中文注释、UTF-16 存盘）-> 两个装载器都抛 `ConfigError`，消息含文件路径，MUST NOT 外泄 `UnicodeDecodeError`（round 1 审核 cand-01）
- 类型判别负例（round 1 审核 cand-10，三处守卫此前零判别力）：`forecast_days = true` 与 `checkpoint_hours = [true]` -> 抛 `ConfigError`（bool 不得当作 int）；`slurm.cpus = 8.5`（float）与 `slurm.partition = {a = 1}`（table）-> 抛 `ConfigError`；表类型字段填标量（如 `cycle = 5`）-> 抛 `ConfigError` 且 `path == "cycle"`
- `cd producer && uv sync --frozen` -> 退出码 0（无 lock drift；本 issue 不得新增依赖）
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check .` 与 `uv run ruff format --check .` -> 退出码 0

Non-goals:
- argparse 三入口骨架、`DATABASE_URL` 守卫、run 状态目录守卫（任务 1.3，issue #3）
- NWM 解释器薄外壳与假解释器脚本测试（任务 1.4，issue #3）
- `local.toml` 内路径的存在性/可执行性校验（归各自使用点）
- **不做 `local.toml` 路径的绝对路径形态校验**：schema 把这些字段标注为 `<绝对路径>`，但装载器只校验其为 `str`。这条**不被上一行覆盖**——相对路径与 `~` 的危害不是"不存在"，而是被正常创建、正常打开却落在错的地方，使用点的存在性检查永远不报警（实测 `Path("~/x") / "y"` → `'~/x/y'`，`~` 不展开）。最尖锐的是 `cron.lock_path`：cron 以 cwd=`$HOME` 调 `run`、人工补跑在 checkout 目录走同一入口，相对路径会让两边 `flock` 拿到两个不同的锁文件，`specs/run-controller/spec.md:60` 的互斥静默失效，两个 controller 同时进入发布段（违反 agent-ops §8.4）。归属：`cron.lock_path` 归 **#23** task 12.3 的 flock 封装；`yd_root`/`scratch_root` 归各自写入面；裁决记录在 **#32**（若决定统一在装载期强制，按文档优先原则先改 `specs/cli-config/spec.md:19` 扩大 MUST 范围再动码）
- **不提交版本化 `producer/config.toml` 生产实例**：`raw.ifs`/`raw.gfs` 的变量名、bundle 文件模式与 GFS f000 具体取值出自 compute-loop §7.1 所称"NWM adapter 的当前事实"，由 issue #4 勘察与 issue #6 完整性判定确立，此刻不可知；本 issue 只钉 schema，测试全部用内联 TOML。生产实例落库已路由为 issue #29（`Depends on #2, #6`），并已挂入 epic #1 依赖图。
- 不提交 `local.toml.example`：compute-loop §5 明确 `local.toml` 不入库，现场值由实施方创建（agent-ops）
- **不做值域校验**：装载器只校验存在性与类型（spec cli-config 把 fail-closed MUST 限定为"任何必需字段缺失或类型错误"）。以下经 round 1 审核确认存在、但按 verifier 裁决 DEFER，本 issue 明确不做，归属逐条具名于 **issue #32**：`raw.*.variables`/`bundles`/`lead_hours` 的空列表拒绝（归 #6 task 3.1——空集会让"所有预期文件存在才算完整"恒真）；`variants.*` 的相对性校验（归 #20 task 10.3——绝对路径会让 `Path(yd_root) / variants.gfs` 静默丢弃 `yd_root`，使覆盖守卫检查的目录与实际写入目录分叉）；`cycle.hours ⊆ {0,12}`（归 #6 task 3.1，fail-closed 下游闸门）；`forecast_days` 与 `reach_count` 的正数约束（归 #24 task 13.1，DONE 前行数/列数校验）；`len(checkpoint_hours) == 1` 与 `output_interval_minutes` 的正数约束（**两项均为零归属项**，需 #32 裁决；建议同法由 #29 以生产实例钉死 `[12]` / `60` 并断言）；`lead_hours` 全集覆盖 0–168h 且与 `forecast_days*24` 一致（本 amendment 新增 `lead_hours` 后才存在，#32 原文早于该字段，已补入其验收标准）
- **不做 `LocalConfig.slurm` 的只读化**：fixture 于本块「TOML key schema」下方逐字钉死"以映射（`dict[str, str | int]`）暴露"，改为只读映射需先修订该钉死标注，归 **issue #31**（其中记录了一处关键更正：`MappingProxyType` 自身不可哈希，不能顺带解决 `hash(LocalConfig)` 与 `hash(Config)` 的不对称）

Review focus:
- 字段名/类型/单位是否与上方 TOML key schema 逐字对应，且 schema 的每个叶子字段可回溯到 compute-loop §5/§7.1 或 products-contract §5
- 是否存在任何隐式默认值、`.get(k, fallback)` 式兜底，或 dataclass 字段默认值
- 错误信息是否总能定位到具体字段的完整点分路径（含嵌套表内字段）
- 是否引入了 stdlib 之外的依赖
- 失败路径是否全部收敛到单一公开异常 `ConfigError`
- `local.[slurm]` 的键集是否只有 `config.slurm.required_fields` 一个权威——代码里若再出现 partition/account/cpus/memory/walltime 的固定字段清单即为双权威，属实现缺陷
- spec cli-config 用反引号钉死的 key 名（`forecast_days`、`output_interval_minutes`、`checkpoint_hours`、`reach_count`）是否逐字保留在顶层，未被加上表前缀

### Issue #3 fixture（任务 1.3–1.4）

Fixture level: expanded
Upstream suggested level: compact（override：改动面正面命中强制 expanded 触发词 `CLI` / `entrypoint` / `public API` / `path`，且顺带落地 `config.toml` 的 `nwm_mapping_builder_module` 属 `schema`/`field` 触发词——与 issue #2 同一条覆写理由）
Repair intensity: medium（无写入面：全部为只读探测与子进程调用；不选 high 的理由是本 issue 不写、不删、不发布任何文件，`Invariant Matrix` 因此不适用）
Project profile: yd-viewer

Change surface:
- 新增 `producer/src/yd_producer/cli.py`：`main(argv=None, env=None) -> int` 与 `build_parser() -> argparse.ArgumentParser`，argparse 三入口薄委托。**测试取用机制是契约的一部分**：(a) `build_parser()` 必须可独立取用（`main(["--help"])` 的 `SystemExit` 在 main 内部抛出，拿不到 parser 对象，故子命令集合断言只能经 `build_parser()`）；(b) `load_config`/`load_local` 与三个入口委托目标必须以**模块级名字**在 `cli` 内引用（`cli.load_config` 等），供 monkeypatch 注入记录型 fake——"fake 调用次数为 0"的全部负面断言依赖它
- 新增 `producer/src/yd_producer/nwm.py`：D6 解释器薄外壳
- `producer/pyproject.toml` 新增 `[project.scripts] yd-producer = "yd_producer.cli:main"`
- `producer/src/yd_producer/config.py`：`Config` 增 `nwm_mapping_builder_module: str` 字段与装载（#32 三步之第 2 步）
- `producer/tests/test_config.py`：三本账同步（#32 三步之第 3 步）——(a) `PINNED_CONFIG_KEYS` 手工转录新键；(b) `VALID_CONFIG` 加该键；(c) `test_load_config_returns_all_fields` 增 `config.nwm_mapping_builder_module == "workers.mapping_builder.cli"` 的逐值 round-trip 断言（该文件 `:299-303` 的注释说明第三本账正是靠这条逐值钉死承重，漏加则新键在第三本账里不承重）；(d) 新键**加入** `SPEC_PINNED_TOP_LEVEL_KEYS`——`specs/cli-config/spec.md:19` 现已用反引号把它钉在顶层，与既有四个键同判据，这是**记录下来的决定**而非默认
- 新增 `producer/tests/test_cli.py`、`producer/tests/test_nwm.py`

Must preserve:
- `load_config` / `load_local` 的既有签名、`ConfigError` 及其 `path` 属性语义、零默认值不变；新增字段只是多一个必需标量，既有失败路径行为不变
- `producer/tests/test_config.py` 既有用例全部继续通过（不新增红、不删除既有用例）——**不钉具体条数**：新增必需键会让参数化用例数随之增长，任何预钉计数按构造即错（`tasks.md` 别处出现的 53 是 round 1 的历史数字，非门禁）；`PINNED_CONFIG_KEYS` MUST 手工对着上方 schema 转录，MUST NOT 由 `_required_keys()` 反向生成（否则参数化缺键用例对新字段恒真）
- **不新增依赖、`producer/uv.lock` 不变**：本 issue 只用 stdlib（`argparse`/`os`/`subprocess`/`pathlib`）。注意 D5 的 `dependencies = []` 是**骨架期**表述，现已随组 6 / 任务 10.1 加入 7 个依赖（`producer/pyproject.toml:6-14`），故 must-preserve 的可核形式是"零新增 + lock 无 drift"，不是"依赖表为空"

Must add/change:
- `yd-producer --help` 列出且仅列出 `prepare`/`init`/`run`；未知子命令非零退出且不执行任何业务逻辑
- `DATABASE_URL` 存在即拒绝执行（agent-ops §2.2）。**位置钉死：在 `parse_args` 之前**，即 `main()` 的第一件事，先于任何参数解释与配置装载
- `run` 发现 `<yd_root>/states/` 不存在或为空即报错停止，MUST NOT 调用 init 逻辑或自建该目录
- `nwm.invoke_mapping_builder`：以 `local.nwm.python` 的**精确路径**、`config.nwm_mapping_builder_module` 的 module 名构造 `[<python>, "-m", <module>, *args]`，cwd 与 `PYTHONPATH` 取自 `local.nwm.checkout_root`；解释器路径不存在 / 非普通文件 / 不可执行即报错，且**不发起任何子进程**
- `prepare` 在守卫通过后 MUST 做解释器 fail-closed **预检**（`local.nwm.python` 存在 / 是普通文件 / 可执行），预检失败即报错退出且不发起任何 builder 调用——这是 spec Scenario「解释器缺失即停」的入口层落点，主语是 `prepare` 而非薄外壳，故不能只靠 seam 7 的单元级证据
- 退出码约定（本 fixture 钉死，测试逐条断言）：argparse 用法错误（未知子命令、缺子命令）= `2`（argparse 默认）；守卫或配置失败（`DATABASE_URL`、`ConfigError`、`states/` 缺失或为空、解释器 fail-closed）= `1`；分阶段未实现的业务体 = `3` 并在 stderr 指名归属任务号。**重叠规则**：`DATABASE_URL` 守卫在 `parse_args` 之前，故它与用法错误同时成立时守卫胜出，退出码为 `1`

Seams under test:
- `cli.main(argv, env) -> int`（design.md「Sketch seams under test」seam 6，本 issue 随 spec Scenario 补入）——进程内调用，不起子进程；配套取用点 `cli.build_parser()`，子命令集合断言经它行使，`main(["--help"])` 只断言 `SystemExit(0)`
- `nwm.invoke_mapping_builder(local, config, args, runner) -> CompletedProcess`（seam 7）——`runner` 为可注入的调用器（缺省 `subprocess.run`），既可用假解释器脚本走真子进程验证端到端形态，也可用记录型 fake 断言 argv/cwd/env 三元组

Selected risk packs（项目特有检查）:
- Public API / CLI / script entry: `yd-producer` 是全部 26 个下游 issue 的唯一操作入口；子命令集合、退出码、`--config`/`--local` 参数名即契约
- Config / project setup: `[project.scripts]` 入口点注册；`config.toml` 新增必需字段
- Schema / columns / units / field names: `nwm_mapping_builder_module` 进 pinned schema，三本账须同步
- Error handling / rollback / partial outputs: 全部守卫均为 fail-closed，且"报错时未发起任何调用/未创建任何目录"是可断言的负面证据
- Release / packaging / dependency compatibility: `[project.scripts]` 改 `pyproject.toml`，`uv sync --frozen` 须无 drift，且不得新增依赖

Risk packs considered (core):
- Public API / CLI / script entry: selected - 见上
- Config / project setup: selected - 见上
- File IO / path safety / overwrite: **selected** - 与 issue #2 不同：本 issue 首次**解引用** `local.toml` 的路径（`nwm.python` 的存在性/可执行性探测、`<yd_root>/states/` 的存在性与空判定）。但只做 `stat`/`os.access`/`iterdir`，**零写入、零删除、零发布**，故闭包清单里只有"存在性分类先于使用""非普通文件被拒绝"两项适用，其余（原子写、no-clobber、回滚清理、符号链接跨信任边界）逐条不适用——本 issue 不写任何路径，`states/` 的信任根是现场自己的 `local.toml`，不是外部输入
- Schema / columns / units / field names: selected - 见上
- Auth / permissions / secrets: not selected - 无凭据；`DATABASE_URL` 守卫是**拒绝**其存在，不读其值，错误信息 MUST NOT 回显该变量的值
- Concurrency / shared state / ordering: not selected - 三入口本身无并发；`run` 的 flock 互斥归 #23 task 12.3
- Resource limits / large input / discovery: not selected - `states/` 只判"是否存在且非空"，用 `next(iter(os.scandir()), None)` 早停，不遍历全目录、不递归
- Legacy compatibility / examples: not selected - CLI 此前不存在（`producer/pyproject.toml` 无 `[project.scripts]`），无既有调用者
- Error handling / rollback / partial outputs: selected - 见上
- Release / packaging / dependency compatibility: selected - 见上
- Documentation / migration notes: not selected - 无迁移；字段落点已由上方 schema 与 spec cli-config 承载

Domain packs (from active profile):
- Geospatial / CRS: not selected - 无几何
- Time series / forcing / temporal boundaries: not selected - 入口层不解释任何时间字段
- 状态链 / warm-start 定戳: not selected - `run` 只判 `states/` 是否存在且非空，不解析任何 `cfg.ic`
- NWM 快照溯源 / DB-free 隔离: **selected** - 本 issue 是全仓唯一主动进入 NWM 活动环境的代码路径（agent-ops §7.2 仅 prepare）。必须断言：调用命令里只出现 `local.nwm.python` 这一个解释器，不含 `uv`、`--active`、`python`、`python3` 任何形态的回退；`DATABASE_URL` 守卫先于一切执行（agent-ops §2.2）

Required evidence（每条 input -> expected output）:
- `main(["--help"])` -> `SystemExit(0)`，stdout 的子命令区**恰好**含 `prepare`/`init`/`run` 三项；断言以解析 argparse 子命令注册表（`_SubParsersAction.choices` 键集 == `{"prepare","init","run"}`）为准，**不**以 help 文本子串探测（文本探测对多注册一个子命令恒真）
- `main(["bootstrap"])` -> 返回 / 抛出退出码 `2`，且注入的三个委托目标 fake 的调用次数均为 0
- `main([])`（缺子命令）-> 退出码 `2`，无委托调用
- **参数化**，`env={"DATABASE_URL": "postgresql://x"}` 下对 argv `["run", <齐备参数>]`、`["prepare", <齐备参数>]`、`["init", <齐备参数>]`、`["bootstrap"]`、`[]`、`["--help"]` 各一份 -> **全部**退出码 `1`，stderr 指名 `DATABASE_URL`，且 **MUST NOT** 出现该变量的值（断言 `"postgresql://x" not in stderr`）；配置装载 fake 与三个委托 fake 的调用次数均为 0。这条同时是守卫位置的判别性证据：后三份（未知子命令、缺子命令、`--help`）在守卫位于 `parse_args` **之后**的实现里会分别得到 `2`/`2`/`0`，故该参数化不因 argv 无关而失去判别力，恰恰是它证明了守卫先于解析
- `main(["run", ...])`，`<yd_root>/states/` 不存在 -> 退出码 `1`，stderr 含该目录路径；**断言 `states/` 事后仍不存在**（负面证据：未自建），且 init 委托 fake 调用次数为 0
- `main(["run", ...])`，`<yd_root>/states/` 存在但为空目录 -> 同上退出码 `1`、init fake 零调用
- `main(["run", ...])`，`<yd_root>/states` 存在但**是普通文件**（非目录）-> 退出码 `1`，稳定错误信息指名该路径，事后该文件内容未被改写；**MUST NOT** 让 `os.scandir()` 的 `NotADirectoryError` 逃逸成 traceback（存在性分类先于 `iterdir`，即所选 File IO 包闭包清单的「stale regular-file lane」）
- `main(["run", ...])`，`states/` 非空 -> 越过守卫，进入分阶段未实现分支，退出码 `3`
- `main(["prepare", ...])`，`local.nwm.python` 指向不存在的路径 -> 退出码 `1`，stderr 指名该路径，注入的 runner fake 调用次数为 **0**（issue #3 正文验收标准「解释器缺失时 prepare 报错退出且无 builder 调用」的直接证据，必须经入口层而非 seam 7）
- 同上，`local.nwm.python` 存在但无执行位 -> 退出码 `1`，runner fake 零调用
- **正控制**：`main(["prepare", <齐备参数>])` 且 `local.nwm.python` 指向**可执行**的假解释器 -> 越过守卫与预检，进入分阶段未实现分支，退出码 `3`，stderr 指名归属任务号；runner fake 调用次数为 0（预检不代替调用）。没有这条，"预检恒失败"的实现也能满足上面两条负例
- **正控制**：`main(["init", <齐备参数>])` 且全部守卫通过 -> 退出码 `3`，stderr 指名归属任务号
- `main(["run"])` 缺 `--config` 或缺 `--local` -> 退出码 `2`（argparse 必需参数缺失），错误信息指名缺失参数；**MUST NOT** 回退到任何内置路径
- `main(["run", "--config", "<不存在>", "--local", "<齐备 local.toml>"])` -> 退出码 `1`，`ConfigError` 被入口层捕获并转成退出码，**MUST NOT** 抛 traceback 到用户面（断言无 `Traceback` 字样）
- `--config` / `--local` 传相对路径 -> 传给装载器的实参是 `Path.resolve()` 后的绝对路径（记录型 fake 断言），错误信息中出现的是解析后的绝对路径
- `nwm.invoke_mapping_builder`，解释器路径不存在 -> 抛 `ConfigError`，`path == "nwm.python"`，且注入 runner 的调用次数为 **0**
- 同上，路径存在但是目录 -> 抛 `ConfigError`，runner 零调用
- 同上，路径存在、是普通文件但无执行位（`chmod 0o644`）-> 抛 `ConfigError`，runner 零调用
- 同上，解释器为**假解释器脚本**（`#!/bin/sh`，把收到的 argv、cwd、`PYTHONPATH` 写入一个 JSON 文件后退出 0）-> 真子进程执行成功；读回该 JSON 断言：`argv[0]` 结尾为该脚本路径、`argv[1:3] == ["-m", "workers.mapping_builder.cli"]`、后续为透传的 `args`、cwd == `checkout_root`、`PYTHONPATH` 的首段 == `checkout_root`
- 同上，`local.toml` 的 `checkout_root` 变更 -> 假解释器记录的 cwd 与 `PYTHONPATH` 随之变更（证明取自 checkout 字段而非常量）
- 回退禁令的负面证据：上一条记录的 argv 全量 join 后 -> **不含** `uv`、`--active`；`argv[0]` 与 `sys.executable` 不相等（假解释器与测试解释器天然不同，故该断言有判别力）
- 假解释器返回非零退出码 -> 薄外壳把非零结果如实上报（不吞、不重试、不回退到别的解释器），runner 调用次数恰为 1
- `Config` 新增字段的三本账（四个落点，与上方 Change surface 逐条对应）：`PINNED_CONFIG_KEYS` 与 `SPEC_PINNED_TOP_LEVEL_KEYS` 均含 `nwm_mapping_builder_module`；`VALID_CONFIG` 含该键；`test_load_config_returns_all_fields` 有其逐值 round-trip 断言；既有参数化"删该 key"用例自动覆盖它并断言 `path == "nwm_mapping_builder_module"`；`_scalar_leaves` 自动生成其类型错误用例
- `cd producer && uv sync --frozen` -> 退出码 0（`[project.scripts]` 变更不得引入 lock drift，也不得新增依赖）
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check .` 与 `uv run ruff format --check .` -> 退出码 0
- 入口点实际可用：`cd producer && uv run yd-producer --help` -> 退出码 0 且列出三入口（证明 `[project.scripts]` 注册真的生效，进程内 `main()` 测试无法覆盖这一点）

本 issue 显式记录的四项决策（issue #3 正文「补充验收标准」要求"必须是被记录的决定而非默认"）:
1. **`--config` / `--local` 的路径形态：在 CLI 边界 `Path.resolve()` 后再交给装载器。** 理由：agent-ops §8.2 规定 cron 以 cwd=`$HOME` 调 `run`、人工补跑在 checkout 目录走同一入口——同一条相对路径在两处指向不同文件，而 `yd_producer.config` 的失败消息忠实回显入参（#2 经 verifier 裁定为正确行为，不改），相对路径回显因此无法告诉运维实际找的是哪个文件。用 `Path.resolve()` 而非 `os.path.abspath`：后者对已是绝对路径的入参做词法 `..` 折叠，跨 symlink 会指向不存在的目录（issue #3 正文已实测 `abspath('/nfs/yd/../x/config.toml')` → `/nfs/x/config.toml`）。`resolve()` 在路径不存在时不抛（`strict=False` 缺省），故不与 fail-closed 冲突。**库层不变**：忠实回显它收到的入参，只是 CLI 保证送进去的已是绝对路径。
2. **`DATABASE_URL` 守卫位于 `parse_args` 之前，与用法错误重叠时守卫胜出（退出码 `1`）。** 理由：agent-ops §2.2 把"不连 NWM 数据库"列为硬约束，环境本身有缺陷时，最 fail-closed 的形态是在解释任何参数之前拒绝，代码路径也只有一条（放在解析后就要在三个子命令里各挂一次，或在解析后再补一个共用前置——多一处可漏）。**被接受的后果已明确记录**：`yd-producer --help` 在 `DATABASE_URL` 存在时同样以 `1` 退出而不打印帮助。这是刻意的——环境错了就先修环境，不给"帮助能出来说明装对了"的错觉。
3. **`--config` 与 `--local` 均为必需参数，无内置默认。** 理由：`specs/cli-config/spec.md:30`「代码中 MUST NOT 内置任何现场默认值」直接禁止 `--local` 有默认路径；`--config` 虽是版本化文件、不受该条约束，但给它一个内置默认等于在代码里第二次写死仓库布局，且两个参数一必需一可选会让 cron 行与人工补跑行长得不一样（agent-ops §8.2 要求两者走同一入口）。KISS：两个都必需。
4. **`load_config`/`load_local` 对非 `Path`/`str` 入参仍抛裸 `TypeError`，不改为 `ConfigError`。** 理由：CLI 边界现在恒传 `Path`（决策 1），该路径在产品内不可达；剩下的触发者只有程序内误用，裸 `TypeError` 正是 Python 对误用的正确回答。加一层类型规范化只会把编程错误伪装成配置错误。

Non-goals:
- **三入口的业务实现**：`prepare` 的 mapping 资产产出与变体组装、`init` 的 bootstrap、`run` 的控制器循环全部归后续 issue（组 8–14；`run` 的入口体承接者是 **14.1**「单源单轮 `run_once` 骨架打通」，不是 12.1——12.1 是被它调用的严格前沿纯函数）。本 issue 的三入口在守卫全部通过后走**分阶段未实现分支**：以退出码 `3` 退出并在 stderr 指名归属任务号。这是**显式记录的分阶段交付**，不是占位符——守卫、参数解析、退出码、薄外壳全部为真实实现且有测试；未实现的只有被本 issue 明确划出范围的业务体
- **`prepare` 不实际调用 mapping-builder**：`prepare` 只做守卫 + 解释器 fail-closed 预检（spec Scenario「解释器缺失即停」在此边界满足），预检通过后即走上一条的未实现分支。真正构造 builder 参数并调用属业务实现。spec Scenario「以精确解释器调用」的主语是薄外壳，由 seam 7 直接行使
- **不校验 `nwm_mapping_builder_module` 是否可导入**：那需要活的 NWM 环境（agent-ops §7.2 的维护窗口约束），归 prepare 编排的归属 issue
- **不做 `local.toml` 路径的绝对路径形态校验**：沿用 issue #2 的 Non-goal 与归属（`cron.lock_path` 归 #23，裁决在 #32）；本 issue 只解引用 `nwm.python` 与 `<yd_root>/states/`，对它们做的是存在性/类型/可执行性检查，不是形态校验
- **不做值域校验**：沿用 issue #2 Non-goal 与 #32 的归属表
- **不实现 flock 互斥**：`run` 的单实例约束归 #23 task 12.3
- **已知限制（不属本 issue，路由给 prepare 编排的归属 issue）**：在 pin `NWM@8ae9b8f2` 上 `workers/mapping_builder/cli.py` 的 argparse `main` 只做 `--package-path` 解析并输出 resolution JSON，尚不驱动完整 build（其 docstring 明写 SUB-5 未落地，「Programmatic callers invoke `build_direct_grid_variant` directly」）。本 issue 只测**调用形态**，不受影响；但要让 prepare 真产出 mapping 资产的那个 issue 必须自行确认调 `-m ...cli` 是否足够。已记入 #32

Review focus:
- `--help` 的三入口断言是否用 argparse 注册表而非文本子串（子串探测对"多注册一个子命令"恒真）
- `DATABASE_URL` 守卫是否**先于**配置装载执行，且错误信息不回显该变量的值
- `run` 的 `states/` 守卫失败后是否留下任何副作用——测试是否有"事后目录仍不存在"的负面断言，而不只是断言退出码
- 薄外壳的 fail-closed 三态（不存在 / 目录 / 无执行位）是否都断言了"runner 零调用"，而不只是断言抛异常
- 是否存在任何解释器回退路径（`uv`、`--active`、`sys.executable`、`shutil.which`）——一处都不允许
- `PINNED_CONFIG_KEYS` 是否手工转录；有无从 `_required_keys()` 反向生成的痕迹
- 分阶段未实现分支是否真的只覆盖被划出范围的业务体，有无把本 issue 范围内的守卫也一并跳过
- 退出码是否按上方约定三分（2 / 1 / 3），有无某条失败路径落到未定义的退出码；`DATABASE_URL` 与用法错误重叠时是否按约定返回 `1`
- `prepare`/`init` 是否都有走到退出码 `3` 的正控制——只有负例的话，"预检/守卫恒失败"的实现也能全绿

## 2. forcing-chain（一）：NWM 快照勘察与基础结构

- [x] 2.1 只读勘察 NWM@`8ae9b8f2`，产出精确快照文件清单（模块 → 原路径 → 目标路径，含 tracker 与补跑），落为 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md`；表格列固定 `| 能力项 | NWM 原路径 | 目标路径 | 剥离点 | 落地状态 | 备注 |`（`落地状态` 于 issue #5 补入，取值只允许 `本 issue 落地` / `待落地`，是守卫期望落地集的唯一来源；守卫解析器硬要求 6 格，把表回退成 5 列会让 27 行全部变成畸形行），一行一个文件、路径反引号包裹；凡原模块触及 DB/scheduler/registry/journal/reservation 的行，`剥离点` 必须点名具体 import、符号或分支（供 2.2 逐文件消费），无耦合写 `无`，禁止“已剥离 DB 分支”一类无点名的笼统措辞
- [x] 2.2 快照 object-store/path 基础函数与 IFS/GFS source、raw manifest 数据结构，含其最小测试；剥离 DB/scheduler 分支
- [x] 2.3 增加溯源头部检查测试：所有快照模块（含后续组落地的 converter/forcing/tracker）头部含 `NWM@8ae9b8f2 <原路径>`

依赖：无
§13.1 归属：DB-free 链（基础结构部分）
Suggested fixture level: compact - 结构与路径函数用内存对象与 tmp 目录即可
Minimal mergeable slice: 勘察清单（2.1）——纯文档产物独立合并，快照代码为后继

### 组 2 剩余任务（2.2/2.3）的 issue #5 fixture

Fixture level: expanded（issue #5 分诊上调，理由见 `.workplans/5/triage.md`）
Repair intensity: high（File IO/path safety + 新建共享 helper 根）

Change surface:
- 新增 `producer/src/yd_producer/store/{object_store.py,object_path.py,safe_fs.py}`
- 新增 `producer/src/yd_producer/raw/{source_identity.py,manifest.py,cycle_hours.py,region.py}`
- 新增 `producer/tests/{test_safe_fs.py,test_object_path.py,test_source_identity.py,test_data_adapter_resolution.py}`（快照）
- 新增 `producer/tests/{test_object_store.py,test_manifest.py,test_cycle_hours.py,test_region.py}`（新写，见清单 §4 风险 3/4/8）
- 新增 `producer/tests/test_snapshot_provenance.py`（任务 2.3）

Must preserve:
- pin `8ae9b8f2` 上被保留符号的语义逐字节等价（modulo 清单 §1 逐行枚举的剥离点）
- `producer/` 既有测试（config/geometry/smoke）全绿
- `docs/products-contract.md:37`「`source` 固定小写 `gfs`/`ifs`」

Must add/change:
- 七个快照源模块，逐文件消费清单 §1 对应行的 `剥离点` 列
- 清单 §4 风险 3/4/8 点名的新写测试：`ManifestEntry`/`DownloadManifest` 的 `as_dict`/`from_dict` roundtrip 与 `cycle_id_for`；`LocalObjectStore` 与 `normalize_object_key`/`sha256_bytes` 最小用例；`parse_cycle_hours_utc`/`normalize_cycle_hours_utc` 的显式入参用例；`GeoBBox` 构造/校验用例
- 溯源头部检查测试：以清单 §1 的「目标路径 → NWM 原路径」表为数据源，双向断言

Seams under test（design.md「Sketch seams under test」第 4 条「快照模块另带 NWM 来源最小测试」；本组为其基础结构部分）:
- 模块级公共函数/数据类（`normalize_source_id`、`ManifestEntry.as_dict`、`GeoBBox`、`validate_object_path`、`LocalObjectStore` 方法、safe_fs 的 no-follow 族）
- 溯源检查的 seam 是仓库文件树本身（路径表 → 文件头字面量）

Invariant Matrix
Governing invariant: 每个快照文件（含测试）头部含字面量 `NWM@8ae9b8f2 <NWM 原路径>`，文件内零 DB/scheduler/registry/journal/reservation 面与零环境变量默认，且被保留符号与 pin 语义等价（只允许清单 §1 该行 `剥离点` 枚举的偏差）。
Source-of-truth identity/contract: 清单 §1 表的「目标路径 ↔ NWM 原路径 ↔ 剥离点」三元组；pin commit `8ae9b8f29c8b72c574e8cbd95f2994160bd42832`。
Surfaces:
- Producers: 七个快照源模块 + 四个快照测试文件（写入方即本 PR 的实现者）
- Validators/preflight: `producer/tests/test_snapshot_provenance.py`（头部与路径表双向）、禁区 grep 断言
- Storage/cache/query: `store/object_store.py` 的 `LocalObjectStore`（本地文件后端读写）、`store/object_path.py` 的键校验
- Public routes/entrypoints: none - 本 PR 不接 CLI/controller（issue PR Boundary）
- Frontend/downstream consumers: none - 组 3/6/7/8/9 尚未落地；`raw/manifest.py` 信封的下游消费在任务 3.2 与 7.1
- Failure paths/rollback/stale state: `store/safe_fs.py` 的 no-follow / 非常规文件 / 超限拒绝分型；`normalize_source_id` 未知源抛错；`cycle_hours`/`region` 缺参 fail closed
- Evidence/audit/readiness: 模块头溯源注释本身；`nwm-snapshot-inventory.md` §1 表
Regression rows:
- 每个新增快照文件（源与测试） -> 前 5 行内存在一条 `#` 注释行，其内容含 `NWM@8ae9b8f2 <该文件在清单里的原路径>`。**正反向必须共用同一个「什么算溯源头部」的谓词**：注释形式（规格「原路径注释」的字面要求）+ 行预算只作用于正向。守卫自身不得出现第二份口径——round 1（位置维度）与 round 2（形式维度）两次失守都源于正反向各有一套定义
- `yd_producer`/`producer/tests` 内任一文件带上述谓词命中的溯源注释、却不在清单路径表内 -> 检查测试失败（反向守卫，无行预算，保证后续组落地必须登记）。反向侧刻意锚在注释行而非裸串：裸串会命中守卫文件自身拼出的 `PROVENANCE_MARKER` 常量，逼出第二份手工豁免名单
- `store/`、`raw/` 全目录跑 Required evidence 里以 `禁区 grep：` 开头的那一条命令 -> 零命中。**本行刻意不复述词表**：先前这里另写了一份 6 词表（缺 `journal`、`reservation`），与 `禁区 grep：` 的 8 词表内容不一致，构成同一禁区面的两份互相矛盾的声明（round-4 修复轮报出）。词表的唯一真源是 `禁区 grep：` 那一行，测试侧由 `_declared_forbidden_surfaces()` 从该行解析、并断言全文恰有一处该锚点。
- **pin 等价性（`剥离点` 为 `无` 或仅注释改写的四行）**：`producer/src/yd_producer/store/object_path.py`、`store/safe_fs.py`、`producer/tests/test_data_adapter_resolution.py`、`store/object_store.py`，各自 `diff` `git -C <NWM 本地 checkout> show 8ae9b8f29c8b72c574e8cbd95f2994160bd42832:<清单该行原路径>`，忽略新增的溯源头部与 import 路径改写（`packages.common.*`/`workers.data_adapters.*` → `yd_producer.*`）、object_store 行 `剥离点` 点名改写的那条注释，**以及一批纯换行重排（如 `_DIR_FLAGS`）**——该重排面是 round-4 实测补记的，先前的忽略清单漏了它。因此本行钉的是 **AST 全等**（`ast.dump(parse(pin)) == ast.dump(parse(本仓))`，round 4 对 `safe_fs.py` 与 `object_path.py` 实测为 `True`），**不是字节等价**；先前写作「字节等价」不准。抽取/改写式的七行（`:40` source_identity、`:41` manifest、`:42` cycle_hours、`:43` region、`:50` test_safe_fs、`:51` test_object_path、`:52` test_source_identity）不适用本行，其等价证据是实现者的逐文件剥离点符合性说明
- `normalize_source_id("IFS"/"ifs"/"Ifs")` -> `"ifs"`；`normalize_source_id("ERA5")` -> 抛错（ERA5 条目已删）
- `ManifestEntry`/`DownloadManifest` 的 `as_dict` → `from_dict` roundtrip -> 字段等价
- **`from_dict` 的拒绝面只覆盖「缺字段」与「两个强制转换字段」，不做类型校验**（探针实测，勿写成笼统的类型拒绝）：缺必需字段 -> 稳定 `KeyError`（`ManifestEntry` 缺 `remote_url`、`DownloadManifest` 缺 `source_id`），不返回半成品对象；`forecast_hour` 走 `int()`、`cycle_time` 走 `parse_cycle_time`，**畸形值的异常类型按实测分三种、不是笼统的 `ValueError`**：`forecast_hour`：`'abc'` -> `ValueError`，`None`/`[]`/`{}` -> `TypeError`，而 `3.7` **根本不被拒绝、静默截断为 `3`**；`cycle_time`：`'not-a-time'` -> `ValueError`，`None`/`123`/`[]` -> `AttributeError`。组 3/7 写 `except ValueError` 会漏掉 `TypeError`/`AttributeError` 两类并放过静默截断。**其余字段类型错一律不拒**——`from_dict({"remote_url": 123, "local_key": ["not","a","str"], "expected_size_bytes": "abc", ...})` 实测**正常返回**一个字段类型全错的 `ManifestEntry`。这是 pin 语义（`raw/manifest.py:192,222`），本 PR 不改；具名用例 `test_manifest.py:196,220` 探的正是那两个强制转换字段，勿把它们读成通用类型闸门。组 3/7 若需要类型校验须自建
- `cycle_id_for(<已知 source_id + cycle datetime>)` -> pin 语义的已知 cycle id 字面量（清单 §4 风险 3 点名的新写覆盖）
- `GeoBBox` 无 bbox 入参 -> fail closed（清单 §4 风险 14：四个 `DEFAULT_BBOX_*` 已删，禁止发明缺省）
- `env_cycle_hours_utc` 显式入参传 `None` -> 仍走 `normalize_cycle_hours_utc(default, field_name=...)`，零 `os.getenv`
- `parse_cycle_hours_utc` 收到畸形输入（`"0,25"`、`""`、`"0,abc"`）-> 稳定抛错
- **键校验的闸门归属（三个函数各管一段，勿把任一段读成总闸门）**：以下每条均已跑探针核实。`normalize_object_key`（在 `store/object_store.py`，不在 `object_path.py`）拒 `..`（出现在任何位置即 `ValueError`）与空键；**绝对路径它不拒**——按 pin 语义 `strip("/")` 后继续，故 `'/raw/gfs/2026050700/a.grib2'` 被**接受**并根相对化为 `'raw/gfs/2026050700/a.grib2'`，而 `'/etc/passwd'` 被拒是因为 `etc/` 不在前缀白名单里，与「绝对」无关。`validate_object_path` 只做前缀白名单匹配、变量段原样捕获：`..` 出现在**已识别前缀之后**时返回 `valid=True`（`'raw/gfs/../../../etc/passwd'` -> `valid=True, cycle_time='..'`），而**开头**的 `..`（`'../etc/passwd'`）返回 `valid=False`——但拒因仍是前缀不匹配，不是穿越检测；两种结果都不构成穿越闸门。闭合 containment 的是复合入口 `LocalObjectStore.resolve_path` = `normalize_object_key` → `validate_object_path` → `relative_to(root)`。-> 复合入口对三类输入稳定拒绝；`validate_object_path` 的单独 permissive 行为由具名用例 `test_object_store.py::test_validate_object_path_alone_accepts_parent_traversal` 钉死（该用例用「前缀之后的 `..`」这一精确输入），以免组 3/7/13 把它当作穿越闸门
- `LocalObjectStore` 对已存在对象再写 -> **覆盖允许（last-write-wins）**，非 no-clobber：探针实测 `write_bytes_atomic` 同键写第二次成功且内容被替换，`.part` 临时文件不残留。此为 pin 语义，本 PR 不得改动；组 12/13 若依赖「已存在即拒」需自行加闸门
- `sha256_bytes(<已知字节串>)` -> 已知摘要字面量（独立 oracle：`printf ... | shasum -a 256`）
- safe_fs 拒绝分型 -> 稳定拒绝，逐条注明覆盖来源（原措辞「快照测试原有覆盖保留」对**非常规文件**一项前提为假：pin 的 `tests/test_safe_fs.py` 14 个用例里无一触及 `S_ISREG`，round 1 verifier 已在 pin 上核实）：
  - 符号链接叶 / 符号链接祖先（`directory_identity_no_follow` 面）-> 快照用例 `test_directory_identity_refuses_symlink_components`（parametrized final/ancestor）已覆盖
  - 超限读的**两个方向要分开记**：下界（溢出可检测，即必须多读出一个哨兵字节）由 `test_object_store.py::test_read_bytes_limited_refuses_beyond_the_byte_ceiling` 经 `LocalObjectStore.read_bytes_limited` 覆盖；上界（有界读本身，即绝不把整个文件读进内存）由本 PR 新写的 `test_safe_fs_refusals.py::test_read_bytes_limited_reads_at_most_one_sentinel_byte_past_the_ceiling` 直接钉 `read_bytes_limited_no_follow`。**原措辞把上界也算在 object_store 那条名下是假覆盖**（round 3 r3-cand-01）：把 `safe_fs.py` 的上限整段删掉、或放大一千倍，那条用例照样绿——它由 `object_store.py:220` 自己的事后 `len(content) > max_bytes` 检查满足，与内层是否有界无关
  - **非常规文件（FIFO/设备，`safe_fs.py` 的 `S_ISREG` 前后置校验）-> 本 PR 新写具名用例**
  - **写入面符号链接，两个函数语义不同，勿合并成一句拒绝声明**：`atomic_write_bytes_no_follow` 的符号链接叶与符号链接祖先 -> 拒绝，本 PR 新写具名用例；`rename_entry_no_follow` 的符号链接**祖先（父目录，源与目的两侧）** -> 拒绝（两侧父目录均 `O_DIRECTORY|O_NOFOLLOW` 打开并自 containment root 逐段走），本 PR 新写具名用例；`rename_entry_no_follow` 的符号链接**叶** -> **不拒绝，按搬移语义整体移动该链接本身**（pin docstring 原文：a symlink at `name` is MOVED as a link and never followed or inspected），本 PR 新写具名用例钉死这一搬移语义，不得写成拒绝断言
  - 新用例禁止写进 `producer/tests/test_safe_fs.py`（该文件是逐字节快照，改它即破坏 pin 等价性行）
- 既有 `producer/tests/{test_config,test_geometry,test_smoke}.py` -> 保持全绿（未改动兄弟面）

Boundary-surface checklist（high 强度）:
- 共享 helper 根：`store/`（safe_fs 被组 13 原子 rename 与清理复用）、`raw/`（manifest 信封被组 3/7 消费）——本 PR 是这两个根的出生点，公共符号名与签名一经落地即为后续组的契约
- 写/覆盖面：`LocalObjectStore` 的写路径、`safe_fs` 的 `atomic_write_bytes_no_follow`/`rename_entry_no_follow`/`rmtree_no_follow`
- 读面：`read_bytes_limited_no_follow`（字节上限）、`stat_no_follow`
- 生产者/消费者证据边界：`raw-manifest.json` 信封（本 PR 只交付数据结构，不交付生成逻辑）
- 未改动的下游消费者：`yd_producer.config`、`yd_producer.geometry`（本 PR 零改动，必须保持全绿）

Risk packs considered (core):
- Public API / CLI / script entry: not selected - 本 PR 不触碰 CLI/controller（issue PR Boundary「不触碰 CLI/controller」）；模块级公共符号的契约性归下方 Legacy compatibility 包
- Config / project setup: selected - D4 零默认：`cycle_hours` 与 bbox 由显式入参注入，缺参 fail closed；`region.py` 的四个 `DEFAULT_BBOX_*` 与 `cycle_hours.py` 的 `os.getenv` 必须删净
- File IO / path safety / overwrite: selected - `safe_fs.py` 整文件即 no-follow/原子写/umask 语义面；`object_store.py`/`object_path.py` 的键与路径校验
- Schema / columns / units / field names: selected - `ManifestEntry`/`DownloadManifest` 信封是 compute-loop §7.2 的下游 schema 真相；`normalize_source_id` 的小写契约（products-contract.md:37）；`GeoBBox` 字段
- Auth / permissions / secrets: not selected - 无凭据面；umask/mode 语义归 File IO 包
- Concurrency / shared state / ordering: not selected - 本组交付的是纯数据结构与无状态文件操作函数，无并发与共享状态；flock 与 NFS 提交顺序归组 12/13
- Resource limits / large input / discovery: selected - `read_bytes_limited_no_follow` 的字节上限语义随 safe_fs 快照，是后续组读取外部文件的唯一上限闸门
- Legacy compatibility / examples: selected - 快照必须与 pin `8ae9b8f2` 语义等价（modulo 清单 §1 逐行枚举的剥离点），否则后续 5 组消费出静默偏差
- Error handling / rollback / partial outputs: selected - safe_fs 的稳定拒绝分型、`normalize_source_id` 未知源抛错、`from_dict` 畸形输入抛错、缺参 fail closed
- Release / packaging / dependency compatibility: not selected - 七个模块全部纯 stdlib，本 PR 不新增依赖（D5：numpy/xarray/cfgrib 归组 6）；`uv sync --frozen` 无 drift 仍在证据里
- Documentation / migration notes: not selected - 无对外文档变更；模块头溯源注释即迁移记录，其正确性由 2.3 检查测试机检

Domain packs (from active profile):
- Geospatial / CRS / shapefile sidecars: not selected - `region.py` 只搬 `GeoBBox` 数据结构，无重投影、无 CRS、无 shapefile sidecar
- Time series / forcing / temporal boundaries: selected - `manifest.py` 保留的 cycle/lead 排程函数族（`parse_cycle_time`/`valid_time_for`/`generate_segmented_forecast_hours`/`validate_forecast_hours`）与 `cycle_hours.py` 的 00/12 规范化
- 状态链 / warm-start 定戳一致性: not selected - 本 PR 不读写 `cfg.ic` 与状态目录，该面归组 4/11
- NWM 快照溯源与 DB-free 隔离: selected - 本 issue 的核心验收（2.3 + 禁区 grep）

Required evidence（每条 input -> expected output）:
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check . && uv run ruff format --check .` -> 退出码 0
- `cd producer && uv sync --frozen` -> 退出码 0（本 PR 不新增依赖）
- `openspec validate m2-producer-core --strict --no-interactive` -> 退出码 0
- 禁区 grep：`grep -rnE 'psycopg|DATABASE_URL|scheduler|registry|journal|reservation|os\.getenv|os\.environ' producer/src/yd_producer/store producer/src/yd_producer/raw` -> 零命中（该断言同时以测试形式固化，不只是人工命令）
- 溯源头部检查（2.3）：对清单 §1 路径表内每个已存在的目标路径 -> 该文件前 5 行内存在一条 **`#` 注释行**，其内容含字面量 `NWM@8ae9b8f2 <对应原路径>`（与 Regression row `:226`、规格 Scenario 共用同一谓词；写在 docstring 或字符串里不算数——此处刻意不留第二份更弱的口径）
- 溯源反向守卫（2.3）：扫描根即 `producer/`（与规格字面相等），按「相对路径分量以 `.` 开头即跳过」的规则排除 `.venv`/`.pytest_cache` 等（规则而非具名名单，不引入第二份名单）；其下带溯源注释谓词命中的文件集合 -> 必须是清单 §1 路径表的子集，多一个即失败。实测 2800 个 `.py` → 跳点开头目录后 40 个 → 带标记 11 个（前两个数随 `.venv` 内容与 master 合入面漂移、不承担判别器职责，round 5 复核时由 2786/26 实测更新为 2800/40；**承重的是「11」**，它由逐文件正反双向断言钉住）
- 上方 Regression rows 分两类，**本条不作「每一行」式的全称声明**（那正是下一条禁止的形状，且对本 fixture 为假）。**第一类·机械覆盖**（除下列两行外的全部 Regression rows）-> 对应一个具名 pytest 用例（用例名与断言的期望值须来自 pin 源码或独立 oracle，不得从实现回读），**且该用例必须能杀死对应行为的变异体**——「有一个具名用例指向它」不构成覆盖。round 3 的 r3-cand-01 正是踩在这条的旧措辞上：`超限读` 行确实指向一个真实存在的具名用例，而把 `safe_fs.py` 的字节上限整段删掉，那个用例照样绿（它由 `object_store.py` 自己的事后长度检查满足）。声明覆盖与实际覆盖之间必须有变异证明这一层机械绑定，否则假覆盖行可以长期存活。**第二类·已声明的手工证据形式，共两行、不再增加**：`:229`（pin 等价性，证据是人工 `git show` diff 加 AST 全等比对，套件不 shell out 到 git，故套件内无信号——这是该行自己写明的形态）与 `:245`（对写码行为的禁令，无可能的运行期用例）。这两行之外若再出现没有具名用例的 Regression row，即属本条违反。
- **完整性断言无变异证明不得写入**：凡形如「只增加/不解除」「等价或更强」「已覆盖」「每一/全部/恰好」的断言，写进 fixture、清单序言或 PR 偏离记录之前，必须先构造出证明它的变异体；双向声明要两个方向各有一个判别器。本 change 已有两处这样的断言被事后证伪——6.2 P1 的「等价或更强」（实为不可比）与偏离 F10 的「只增加义务、不解除任何一条」（一个词的降级即解除一个文件的义务），两处都是在无变异证明的情况下写下的

Non-goals:
- 清单 §1 的 `direct_grid_contract.py` / `shud_forcing_contract.py` 两行（能力 3 的 direct-grid 契约面）——属 forcing 本体，issue #5 Out of Scope，归组 8
- converter / forcing / state / tracker 各行——归组 6/7/8/4/9
- raw 完整性判定与 `raw-manifest.json` 生成逻辑——归任务 3.1/3.2（本 PR 只交付数据结构）
- `config.toml` 的 bbox / forcing 上限字段落地——清单 §4 风险 14 已显式交接任务 1.1；本 PR 只保证缺参 fail closed
- 不为 `manifest.py` 保留的排程函数族新写测试——`test_data_adapter_resolution.py` 整文件快照（10 个用例）已覆盖，重写等于二次实现
- 不改动 pin 上被保留符号的语义以"顺手改好"——语义等价是本 PR 的验收项；改进意见记为 follow-up issue
- **不把反向扫描面扩到 `producer/` 之外**（如仓库根、`viewer/`）——本 PR 的守卫只对快照落地面负责，跨面扫描属组 13 的仓库级检查；此边界经 round 1/2 三名 reviewer 复核接受，记录在此以免后续轮次重开庭（PR #40 偏离记录 F2）
- **不认 docstring / 字符串形式的溯源标记**：正反两向共用注释谓词后，`"""NWM@8ae9b8f2 ..."""` 这类写法**不算**溯源头部，因而一个未登记、仅带 docstring 标记的散落文件不承担登记义务、也不被守卫接触。这是「单一谓词 + 反向必须保持注释锚」的必然推论——反向若放宽成裸串，守卫会命中自身拼出的 `PROVENANCE_MARKER` 常量，逼出一份手工豁免名单。该语义由具名用例 `test_forward_guard_rejects_docstring_form_markers` 钉死，非疏漏
- **已知限度：完全失去竖线的表体行不可达**。§1 的游离行检查抓的是「含 `|` 却不以 `|` 起头」；一行若把**所有**竖线都丢掉就退化成散文，除非冻结一份路径名单否则无法机械发现。影响面不对称：该行若标 `本 issue 落地` 且文件在盘上带头部，反向守卫仍会因「未登记」报错；若标 `待落地` 则静默。不冻结名单是刻意取舍——名单正是本守卫要消灭的东西。留给组 13 的仓库级检查，或清单结构化（如 §1 转 YAML）时一并解决

Review focus:
- 逐行核对清单 §1 的 11 个 in-scope 行的 `剥离点` 列是否被逐条执行，尤其 `:40` 的 `_STORAGE_SOURCE_IDS` 字面量改写与 `:52` 的 10 处下游断言同步、`:43` 的四个 `DEFAULT_BBOX_*` 与 `_env_float` 删除、`:42` 的 `os.getenv` 删除
- `:50` 的三处**函数内** import（L37 模块级、L270、L289 函数内）是否删净——函数内 import 漏改不会被 `pytest --collect-only` 抓到
- 是否存在任何被发明的缺省值（bbox、上限、lead），违反 D4 零默认
- 2.3 的检查测试是否真的双向（正向逐文件断言 + 反向越界守卫），以及是否会在后续组落地新快照文件时自动生效而非需要手工维护第二份清单
- import 路径改写是否一致（`packages.common.*` / `workers.data_adapters.*` → `yd_producer.store.*` / `yd_producer.raw.*`，含 `storage.py` → `object_path.py` 的重命名）

## 3. raw-scan：完整性扫描与临时 manifest

- [x] 3.1 实现 IFS/GFS 完整性规则判定（00/12 限定、0–168h、变量/bundle 模式、GFS f000 特例、逐文件检查）
- [ ] 3.2 实现 raw 只读复制到 `work/raw/`（源不可变断言）与临时 `raw-manifest.json` 生成（entry 只引用副本）

依赖：组 1（规则来自 config）、组 2（manifest 结构）
§13.1 归属：raw 扫描
Suggested fixture level: compact - tmp 目录树按文件模式生成空壳文件即可覆盖判定与复制
Minimal mergeable slice: 完整性判定纯函数（3.1）——不含复制与 manifest，可独立合并保绿

### Issue #6 fixture（任务 3.1）

Fixture level: expanded
Upstream suggested level: compact（override：改动面命中强制 expanded 触发词 `path`/`reader`/`field` 与 "external data discovery"，且判定结果是整条日常链的准入闸门；profile 的 domain expanded-trigger `cycle`/`raw manifest` 同样命中）
Repair intensity: medium
Project profile: yd-viewer

Change surface:
- 新增 `producer/src/yd_producer/rawscan.py`。design.md "Sketch seams under test" 第 2 条原写作 `raw_scan.scan(raw_root, source, cycle) -> Manifest | Incomplete`，与本 fixture 有三处分歧（模块/函数名、缺 `config` 形参、返回 `Manifest`）；按 CLAUDE.md「文档优先」，**design.md 该行已于本轮同步修订为 `rawscan.judge(raw_root, source, cycle, config) -> ScanVerdict`**，分歧已消除，实现者以 design.md 与本块的一致签名为准
- 新增 `producer/tests/test_rawscan.py`
- 不触碰 `config.py`、CLI 入口与任何其它模块

Must preserve:
- `producer/pyproject.toml` 的 `dependencies = []`（本 issue 只用 stdlib）
- 现有 `tests/test_config.py`、`tests/test_geometry.py`、`tests/test_smoke.py` 继续通过
- `yd_producer.config` 的公开面（`Config` 树与 `ConfigError`）不变——本 issue 复用其 `ConfigError` 作为配置类失败的异常类型，不新造第二个配置异常

Seam 与返回形态（本 fixture 钉死）:

```python
def judge(raw_root, source, cycle, config) -> ScanVerdict
# raw_root: str | os.PathLike[str]（NWM raw 根，其下为 <存储身份>/<YYYYMMDDHH>/；
#           存储身份逐源非对称，见下方「raw 布局与文件名」）
# source:   "ifs" | "gfs"（其它取值 fail closed）
# cycle:    datetime，MUST tz-aware 且为 UTC，且 minute/second/microsecond MUST 均为 0
#           （目录名取 UTC 紧凑戳 YYYYMMDDHH，非整点值若放行会被静默截断成另一个 cycle）
#           naive、非 UTC、或带非零分/秒/微秒即 fail closed
# config:   yd_producer.config.Config
```

`ScanVerdict`（frozen dataclass）字段：

- `complete: bool` —— 当且仅当 `missing_files` 与 `unreadable_files` 均为空
- `expected_files: tuple[Path, ...]` —— 预期文件全集，按 lead 升序、组内按 `bundles` 声明序，绝对路径
- `missing_files: tuple[Path, ...]` —— 不存在的预期文件（`expected_files` 的子序列，保序）
- `unreadable_files: tuple[Path, ...]` —— 存在但不可读的预期文件（同上保序）
- `expected_variables: dict[int, tuple[str, ...]]` —— 每个预期 lead 的预期变量集（供 3.2 的 manifest 逐变量扇出消费）

raw 布局与文件名（NWM pin 事实转录，勘察清单 §3.1 桥）:

- 目录布局 `<raw_root>/<存储身份>/<YYYYMMDDHH>/<bundle 文件名>`，`YYYYMMDDHH` 为 cycle 的 UTC 紧凑戳（`NWM@8ae9b8f2 workers/data_adapters/base.py format_cycle_time`），目录段取 `raw/{source_id}/{compact_cycle}/{bundle_filename}`（`gfs_adapter.py:615`）中的 `source_id`。
- **目录段逐源非对称，MUST NOT 一律小写**（round 1 审核 cand-03，verifier CONFIRMED/FIX_NOW）：pin 的存储身份表 `_STORAGE_SOURCE_IDS = {"GFS": "gfs", "ERA5": "ERA5", "IFS": "IFS"}`（`NWM@8ae9b8f2 packages/common/source_identity.py:5-9`）刻意让 GFS 落**小写** `gfs`、IFS 落**大写** `IFS`，两个 adapter 的默认 `source_id` 与之一致且 IFS 的 `local_key` 逐字使用（`ifs_adapter.py:182`、`:622-624`），object store 侧不做任何大小写归一。故 yd 侧 MUST 以显式映射 `{"ifs": "IFS", "gfs": "gfs"}`（带 pin 溯源注释）把**入参 source**（同时也是 `config.raw` 的属性名，恒小写）翻译成**目录段**，二者 MUST NOT 硬绑同一字面量。本条与 yd 自己产物侧的小写 `source`（`docs/products-contract.md` §5）无关，勿混用。`nwm-snapshot-inventory.md` §3.1 已同步补入该事实行与勘误
- `raw_root` 是否已指向对象存储的 `raw/` 前缀属现场值，归 `local.toml`（#29/#20），本 issue 不推断
- 预期文件集 = `lead_hours × bundles`（tasks.md 组 1 已逐字钉死该公式），无变量维度：pin 上一个 forecast hour 只落**一个**物理 bundle 文件、内含该小时全部变量（`gfs_adapter.py:611-636` 的 `layout="per_forecast_hour"`）
- `bundles` 元素是 `str.format` 文件名模式，占位符词表**恰好两个具名字段**：`{cycle_hour}`（int，cycle 的 UTC 小时）与 `{lead}`（int）；允许格式说明符，故 pin 的真实形态可表达为 `"gfs.t{cycle_hour:02d}z.pgrb2.0p25.f{lead:03d}.bundle.grib2"`（`gfs_adapter.py:1878-1880`）与 `"ifs.t{cycle_hour:02d}z.f{lead:03d}.bundle.grib2"`（`ifs_adapter.py:1688-1690`）。出现词表外的具名字段、位置字段（`{}`/`{0}`）或语法损坏的模式，一律 fail closed 并在错误中点名该模式与该字段——不得静默留下未替换的 `{...}` 去 stat 一个必然不存在的路径
- **每个模式 MUST 含 `{lead}`，且渲染出的预期文件集 MUST 单射**（round 1 审核 cand-01，verifier CONFIRMED/FIX_NOW）：只做占位符白名单不足以守住「预期文件集 = `lead_hours × bundles`」——漏写 `{lead}` 的模式（如把 `.f{lead:03d}` 写丢）会让全部 lead 渲染成同一路径，`expected_files` 基数虚高而判定域只剩一个点，57 个 lead 里只要 1 个文件落盘即判整轮完整。这与本 fixture 拒空列表是同一病理的两扇门（一扇让预期集为空、一扇让它塌缩），故一并 fail closed：模式的顶层具名字段集合内没有 `lead` 即 `ConfigError`。注意 `string.Formatter().parse` 不暴露嵌套格式说明符内的字段，故 `"f{cycle_hour:0{lead}d}"` 这类只把 `lead` 写在 spec 里的模式会被拒——方向 fail-closed，属有意为之，须在代码注释写明。漏写 `{cycle_hour}` 无害（cycle 目录已隔离），不必拒。等价地，`expected_files` MUST 无重复项——这条同时覆盖「两个 bundle 模式渲染同名」与「`lead_hours` 含重复值」（cand-05 的错位触发门，见 Non-goals）
- 渲染结果 MUST 是单个文件名：含路径分隔符或 `..` 段即 fail closed（`config.toml` 虽为版本化文件，但渲染路径逃出 `<raw_root>/<存储身份>/<cycle>/` 就使"只读 NWM 原件"的边界失效）

GFS f000 特例（`RawSourceConfig.f000_special`）:

- `f000_special = true` 时，lead 0 的预期变量集 = `variables` 去掉 f000 不可用变量集 `{"apcp", "dswrf"}`；该集合为 pin 事实转录（`NWM@8ae9b8f2 workers/data_adapters/gfs_adapter.py:107 GFS_F000_UNAVAILABLE_VARIABLES`，pin 注释：累积/平均量在 f000 分析时刻无定义，cloud `.idx` 与 NOMADS 均无此记录），MUST 以带该溯源注释的模块级常量表达，MUST NOT 按 `source == "gfs"` 分支——特例由 config 的布尔开关驱动，不由源名驱动
- lead 0 的 **bundle 文件仍属预期**：pin 的 `_effective_forecast_hours`(`gfs_adapter.py:1624`) 是恒等映射，f000 为瞬时场保留在 manifest 内。所谓"不误报缺失"指的是不要求 f000 提供累积量，而不是不要求 f000 文件
- `f000_special = false` 时各 lead 变量集恒为 `variables`；lead 0 不在 `lead_hours` 时该规则不产生任何效果
- 退化情形（`variables` 去除后为空）镜像 pin 行为：文件仍属预期，该 lead 变量集为空元组，不报错——yd 侧不发明 pin 上没有的语义

判定顺序（MUST 逐段短路，配置类与请求类失败一律发生在任何文件系统访问之前）:

1. 配置取值域校验（归属见 tasks.md 组 1 Non-goals 的"不做值域校验"条，该条把这两项逐条路由到本任务 3.1）：`cycle.hours` 非空且 ⊆ `{0, 12}`；**`raw.ifs` 与 `raw.gfs` 两个源**的 `lead_hours`/`variables`/`bundles` 均非空。两个源都查而不是只查被请求的那个源——这一段不依赖 `source` 合法，故可排在词表校验之前，双重非法输入（词表外 `source` + 空列表）下的行为因此是确定的。违反即抛 `ConfigError` 且 `path` 为完整点分路径（如 `raw.gfs.bundles`）。空集必须拒绝的理由：预期文件集为空会让"所有预期文件存在才算完整"恒真，把缺口判成完整
2. 请求校验：`source` ∈ `{"ifs", "gfs"}`（`path=None`）；`cycle` tz-aware、UTC、分/秒/微秒为 0（`path=None`）；`cycle.hour` ∈ `config.cycle.hours`（`path="cycle.hours"`）。违反即抛 `ConfigError`
3. 模式校验与渲染（占位符词表、单文件名约束）
4. 逐文件检查：对每个预期文件判「存在且为普通文件」（语义同 `Path.is_file()`：跟随 symlink 后仍须是普通文件），**且该检查 MUST 自行 stat 并显式分类，MUST NOT 直接依赖 `Path.is_file()`**（round 1 审核 cand-02 verifier CONFIRMED/FIX_NOW，取证方法经修复轮实测再修订）——`Path.is_file()` 吞掉哪些 errno **随 CPython 版本变**：3.12 只吞 ENOENT/ENOTDIR/EBADF/ELOOP 而 EACCES/EIO/ESTALE 上抛，3.13+ 起吞掉全部 `OSError`。生产 raw 根正是 NFS 上由 NWM 以另一 uid 写入、cycle 目录常缺 x 位的形态，依赖它会让同一个输入在 3.12 上以裸 `PermissionError` 逃出 `judge`（违反本 fixture「不完整不是异常」），在 3.13+ 上被静默记成「缺失」（`unreadable_files` 分支不可达）——两种都错，且 CI 钉 3.12、开发机可能更新，差异不会被本地测试暴露。故显式区分：`FileNotFoundError`/`NotADirectoryError` 归 `missing_files`，其余 `OSError` 与非普通文件之外的访问失败归 `unreadable_files`，收敛策略与 `open` 一致。再判可读（以 `os.access(..., os.R_OK)` 之外的实际 `open(..., "rb")` 读一个字节为准——`os.access` 在部分挂载/权限模型下与真实 `open` 不一致）。MUST NOT 以目录 mtime、末 lead 存在或任何动态推断替代逐文件检查（spec raw-scan 的 MUST NOT）

Selected risk packs:

- Public API / CLI / script entry: not selected - 纯函数模块，不接 CLI，不注册入口点（接入归组 11/12 控制器）
- Config / project setup: selected - 判定规则全部取自 `Config`，本 issue 新增两项 fail-closed 取值域校验
- File IO / path safety / overwrite: selected（只读面）- 只 stat/open 读 NWM 原件，零写零删；渲染文件名的越界约束是本 pack 的落点
- Schema / columns / units / field names: selected - lead 与变量集语义（含 f000 变量集）是 3.2 manifest 扇出的输入契约
- Auth / permissions / secrets: not selected - 无凭据；权限只作为"存在但不可读"的负例出现
- Concurrency / shared state / ordering: not selected - 无状态纯函数；raw 目录在扫描期被 NWM downloader 并发写入导致的 TOCTOU 归 3.2 的复制面与控制器
- Resource limits / large input / discovery: selected - 检查规模有界（`len(lead_hours) × len(bundles)`，0–168h/3h 上界 57 个 lead），MUST NOT 递归遍历 `raw_root` 或列目录后过滤
- Legacy compatibility / examples: not selected - 全新模块，无既有消费者
- Error handling / rollback / partial outputs: selected - fail-closed 分支即本 issue 主体行为；不完整不是异常，MUST 以 `ScanVerdict` 返回并列出缺失清单
- Release / packaging / dependency compatibility: selected - 只用 stdlib，`uv sync --frozen` 无 drift
- Documentation / migration notes: not selected - 无对外文档变更

Domain packs (from active profile):

- Geospatial / CRS: not selected - 无几何
- Time series / forcing / temporal boundaries: selected - cycle 00/12 限定、lead 全集、f000 时刻语义全在本 issue 定型
- 状态链 / warm-start 定戳: not selected - 不读写状态
- NWM 快照溯源 / DB-free 隔离: selected - f000 变量集与文件名/布局均为 pin 事实转录，须带 `NWM@8ae9b8f2 <原路径>` 溯源注释；MUST NOT 运行时 import NWM 或连接任何数据库

Required evidence（每条 input -> expected output；`cd producer && uv run pytest`）:

- 全部预期文件存在（两源各一例）-> `complete is True`，`expected_files` 等于 `lead_hours × bundles` 的完整有序清单，`missing_files`/`unreadable_files` 为空（spec Scenario "全部预期文件存在"）
- 删掉一个**中间** lead 的文件 -> `complete is False`，`missing_files` 恰为该文件（spec Scenario "缺失单个文件"）
- 只保留最末 lead 的文件 -> `complete is False`，`missing_files` 为其余全部预期文件（spec Scenario "仅有末 lead 文件不算完整"）
- `f000_special = true` 且 f000 文件存在 -> f000 文件出现在 `expected_files`、不出现在 `missing_files`；`expected_variables[0]` 等于 `variables` 去掉 `apcp`/`dswrf`，其余 lead 的变量集等于 `variables`（spec Scenario "GFS f000 特例"）
- 同一 fixture 把 `f000_special` 改为 `false` -> `expected_variables[0]` 等于 `variables`（对照组，证明该开关有判别力，非恒等实现）
- `f000_special = true` 但删掉 f000 文件 -> `complete is False` 且 f000 文件在 `missing_files`（证明特例不是"放行 f000"）
- `f000_special = true` 且 `variables` 恰为 `["apcp", "dswrf"]` -> f000 文件仍属预期，`expected_variables[0] == ()`，不抛异常（退化情形镜像 pin）
- 请求 06Z cycle -> 抛 `ConfigError`，且 `raw_root` 指向**不存在的目录**时同样抛该异常（证明拒绝发生在任何文件系统访问之前，spec Scenario "非 00/12 cycle 被拒绝"）
- `cycle.hours = [0, 6, 12]` -> 抛 `ConfigError` 且 `path == "cycle.hours"`（值域校验，tasks.md 组 1 路由本任务）
- **参数化：** `raw.<source>.lead_hours`/`variables`/`bundles` 各置空列表 -> 每份都抛 `ConfigError` 且 `path` 为对应完整点分路径（同上路由）
- naive `datetime`（无 tzinfo）与非 UTC tzinfo -> 均抛 `ConfigError`
- `source = "ecmwf"`（词表外）-> 抛 `ConfigError`
- bundle 模式含词表外具名字段（`{member}`）、位置字段（`{}`）、语法损坏（`"a{lead"`）-> 各抛 `ConfigError`，消息含该模式与该字段名
- bundle 模式渲染出路径分隔符或 `..`（`"../{lead:03d}.grib2"`、`"sub/{lead:03d}.grib2"`）-> 抛 `ConfigError`
- 预期路径存在但是**目录**而非文件 -> 计入 `missing_files`（`is_file()` 语义），不误判为完整
- 预期文件存在但不可读（`chmod 0o000`）-> `complete is False` 且该文件在 `unreadable_files`、不在 `missing_files`；用例以 `os.geteuid() != 0` 为 skipif 条件（root 下 DAC 权限位不生效）
- `<raw_root>/<source>/<YYYYMMDDHH>` 目录整体不存在（合法 00Z 请求）-> **不抛异常**，`complete is False` 且 `missing_files == expected_files`（主消费方 11.1 的 7 天扫描窗绝大多数请求正是打在不存在的 cycle 目录上；"不完整"不是错误）
- 预期路径是**指向目录的 symlink** / **断链 symlink** -> 两者均计入 `missing_files`（`is_file()` 跟随 symlink 后须仍是普通文件）
- `cycle` 带非零 minute（如 `00:37Z`）-> 抛 `ConfigError`（不得静默截断成 `YYYYMMDD00`）
- 零副作用断言：对完整 fixture 跑一次 `judge`，断言 `raw_root` 子树的文件清单、内容与 mtime 前后不变（spec raw-scan "raw 只读"的判定侧半条）
- MUST NOT 动态推断的机检（一）：判定期不列目录——以 fixture 在 `<cycle>` 目录内额外放置若干**不属预期集**的文件（如 `f999`、`manifest.json`）断言它们既不出现在 `expected_files` 也不影响 `complete`

Required evidence（round 1 审核后追加；上表的取证方法被变异体证伪或未覆盖的部分）:

- **取证方法修订（cand-06 -> r2-cand-01，两轮）**：哨兵 MUST 桩在 **`os` 层**，MUST NOT 只桩 `pathlib` 名字。理由是两轮实测的教训：round 1 版本逐字点名 `pathlib.Path.is_file`/`iterdir`/`rglob` + `builtins.open`，而同轮的 cand-02 修复把实现的文件系统入口换成了 `Path.stat()`——`Path.is_file` 成为死桩，round 2 的五个变异体（`os.walk`/`os.scandir`/`os.listdir`/`os.stat`/带 `except OSError` 的 `Path.stat`，均带 `is_dir()` 守卫或吞异常，插在第 1 段之前）**全部存活**。这条取证一旦以"某个具体名字"表达，就会随实现换原语而静默失效。
  正确形态：monkeypatch **`os.stat`、`os.scandir`、`os.listdir`** 三个为抛 `AssertionError` 的桩（verifier 实测：只加这三桩，干净实现全绿而上述五个变异体全部变红）。**不必单独桩 `Path.stat`**——3.12 与 3.14 的 `pathlib.Path.stat` 都是 `return os.stat(self, ...)`，`os.walk` 亦经 `os.scandir`，`os` 层是全部 pathlib 拼法的汇流处，这正是"桩不变量而非桩例子"的落点。`builtins.open` 与 `Path.iterdir`/`rglob`/`glob` 可一并保留作纵深。
  两个用例的桩集不同，MUST 分开写：**拒绝路径**哨兵（非法 `cycle.hours`、空列表、词表外 `source`、naive/非整点 cycle、坏模式）桩上述三者 -> 仍抛 `ConfigError`；**happy path** 哨兵只桩 `os.scandir`/`os.listdir`（与 `Path.iterdir`/`rglob`/`glob`）-> 判定照常完成，**MUST NOT** 桩 `os.stat`/`Path.stat`——逐文件 stat 是 happy path 的规定行为。
  仅以"不存在的 `raw_root`"断言不足以取证：它只杀得掉**不吞异常**的探针，任何带守卫或 `except OSError` 的探针照样存活
- **以真实 `open` 而非 `os.access` 判可读的机检（cand-08）**：在 chmod 不可读用例内 `monkeypatch.setattr(os, "access", ...)` 使其被调用即 fail -> 判定照常完成。理由：`chmod 0o000` 在普通 DAC 下两种实现结论一致，对该 MUST 零判别力（实测 `_is_readable` 换成 `os.access` 后 47 例全绿）
- **cycle 目录不可搜索（cand-02 的负例）**：完整 fixture 后对 `<cycle>` 目录 `chmod 0o000`（`skipif(os.geteuid() == 0)`，`finally` 恢复）-> **不抛裸 `OSError`**，全部预期文件落入 `unreadable_files`，`complete is False`
- **cycle 路径位置上是普通文件（r2-cand-04，ENOTDIR）**：把 `<cycle>` 目录的位置写成一个普通文件（NWM 半写、人工误放，或 `raw_root` 配错一层）-> `complete is False`，`missing_files == expected_files`，`unreadable_files == ()`。这条与上一条成对，钉死判定顺序第 4 段的**两支分类**：`FileNotFoundError`/`NotADirectoryError` 归 `missing_files`，其余 `OSError` 归 `unreadable_files`；只有 ENOENT 与 EACCES 的用例分不出 `NotADirectoryError` 在哪一支（实测把它从 missing 支删掉，80 例全绿）。macOS/Linux 对 `stat("<普通文件>/child")` 均抛 `NotADirectoryError`，无平台依赖、无需 skipif
- **裸 `ValueError` 不得外泄（r2-cand-02）**：`bundles` 模式或 `raw_root` 含 NUL 字符（TOML 的 `\u0000` 转义可承载，`tomllib` 接受、装载器只做类型校验）-> **不外泄裸 `ValueError`**。两个入口都要有用例：`raw_root` 那支根本不经过模式校验，故只在渲染侧加守卫关不掉它
- **模式缺 `{lead}`（cand-01）**：`bundles` 内任一模式不含 `{lead}` -> 抛 `ConfigError` 且 `path` 为该源 bundles 的点分路径、消息点名该模式；另断言合法路径下 `len(set(expected_files)) == len(expected_files)`
- **预期集单射性的两个兄弟触发门（cand-01/cand-05）**：`bundles` 含两个渲染同名的模式、`lead_hours` 含重复值 -> 各自抛 `ConfigError` 且 `path` 为对应点分路径
- **lead 升序有判别力（cand-07）**：`lead_hours = (6, 0, 3)` -> `expected_files` 等于按 `(0, 3, 6)` **字面**构造的清单；构造该期望值时 MUST NOT 复用测试助手内部的 `sorted`，否则 oracle 与被测实现共享同一排序行为而恒真（实测去掉实现侧排序后 47 例全绿）
- **IFS 目录段为大写 `IFS`（cand-03）**：对 `source="ifs"` 断言 `expected_files` 的目录段逐字为 `IFS`；对 `source="gfs"` 断言为 `gfs`。fixture 树 MUST 用与实现**不同来源**的字面量构造（直接写死 `IFS`/`gfs`，不得复用实现导出的映射），否则两侧同步漂移而恒真；另注意 macOS 文件系统大小写不敏感，该用例的判别力来自路径**字面比对**而非文件是否存在
- **相对 `raw_root`（偏离 13）**：`monkeypatch.chdir(tmp_path)` 后以相对 `raw_root` 调用 -> `expected_files` 全为绝对路径且等于 `tmp_path` 下的字面清单
- **非 PathLike `raw_root`（偏离 9）**：`judge(123, ...)` -> 抛 `ConfigError` 且 `path is None`（不得外泄裸 `TypeError`）
- **渲染出 `""`、`"."` 或 `".."`（偏离 5 / r2-cand-03）**：三者与分隔符同样被拒。**三个取值都 MUST 用含 `{lead}` 且渲染结果逐字为该值的模式来构造，并配 `lead_hours` 单元素**——否则用例会被更早的门吸收而零判别力：不含 `{lead}` 的模式（如裸 `".."`）先被 `{lead}` 必需门拦下、报文是"不含 `{lead}`"，而"断言抛 `ConfigError` 且 `path` 为 bundles 点分路径"在两个门下同样成立，于是该参数分不出单文件名约束在不在。多 lead 则会被单射门吸收。这是本 issue 记录在案的**第三处门重叠**（另两处：不含任何占位符的模式被单射门吸收；`""`/`"."` 曾被单射门吸收后由单元素 `lead_hours` 解开）
- **UTC 判据为零偏移量而非 tzinfo 身份（cand-04）**：以 `zoneinfo.ZoneInfo("UTC")` 或 `timezone(timedelta(0), name="Z")` 构造 cycle -> 判定正常返回。MUST NOT 用 `timezone(timedelta(0))`：CPython 对无名零偏移返回 `timezone.utc` 单例，该取值对本条零判别力（实测判据换成 `tzinfo is UTC` 后 47 例全绿）
- **非 datetime 的 `cycle`（round 3 r3-cand-03；与上方"非 PathLike `raw_root`"成对）**：`judge(root, "gfs", cycle, config)` 传 `date`/`str`/`int`/`None` -> 各抛 `ConfigError` 且 `path is None`（不得外泄裸 `AttributeError`）。理由：主消费方 3.2/11.1 传 `date` 而非 `datetime`、或从别处拿到字符串戳，是这条链上最常见的传参错；实测把该守卫变异成 `if False:` 时 87 例全绿，而变异体下 `date(2026,3,4)` 会在 `cycle.utcoffset()` 抛裸 `AttributeError`，直接违反 Invariant A
- **词表门与 `_render` 异常漏斗 MUST 可分离（round 3 r3-cand-01）**：上方"bundle 模式含词表外具名字段/位置字段"那行的三个取值，仅断言"抛 `ConfigError` 且消息含字段名"时零判别力——删掉整个词表校验循环后它们仍全绿，因为落进 `_render` 的 `KeyError`/`IndexError` 兜底产出同 `path`、同含模式名与字段名的报文。用例 MUST 另断言 `excinfo.value.__cause__` 不是 `KeyError`/`IndexError`（词表门直接 `raise`、无 `__cause__`）**且**报文含 `_validate_pattern` 独有措辞。位置字段 `{}` 的 `field` 为空串，故守卫 MUST 写 `if field is not None:` 而非 `if field:`，否则该参数被静默跳过
- **哨兵桩 MUST 对宽捕获探针有判别力（round 3 r3-cand-02）**：桩体抛 `AssertionError` 只杀得掉不吞异常与 `except OSError` 的探针；`except Exception:` / 裸 `except:` 探针全部存活（实测 `os.walk`/`os.stat`/`os.listdir` 四个宽捕获变异体均 87 全绿，而同形态的 `except OSError` 对照组变红 20–21 例）。正确形态：桩体先把被调原语名 append 进模块级列表再抛 `AssertionError`（保留纵深），并在拒绝路径 `monkeypatch.undo()` 之后、happy path `judge` 返回之后断言该列表为空。**MUST NOT** 改用 `BaseException` 子类——裸 `except:` 同样吞得掉。`os.access` 那条兄弟取证（cand-08）换用同一记录式桩即顺带闭合
- **嵌套 format_spec 绕过词表门后的渲染失败（round 4 r4-cand-01）**：`string.Formatter().parse` 不暴露嵌套格式说明符内的字段（实现注释已载明），故 `gfs.f{lead:{member}}.grib2` 一类模式的内层字段对词表门不可见、`{lead}` 门放行，直到 `_render` 才由 `str.format` 抛出。四个内层形态 `{member}` / `{0}` / `{lead[0]}` / `{lead.nosuch}` 分别触发 `KeyError`/`IndexError`/`TypeError`/`AttributeError`，MUST 各有一条用例断言 `judge` 抛 `ConfigError` 且 `path == "raw.<源>.bundles"`。**MUST NOT 断言 `__cause__` 的异常类型**——日后若在 `_validate_pattern` 补 format_spec 门，该异常将由词表门直接 raise 而无 `__cause__`，钉 cause 会让用例二次重写。这四腿是 Invariant A 在该输入类上唯一的承重结构：把 `_render` 的 except 元组缩成 `(ValueError,)` 时 92 例全绿，而变异体下 `judge` 漏出裸 `KeyError`
- **请求门判据 MUST 是 `config.cycle.hours` 而非常量域（round 4 r4-cand-02）**：`cycle.hours` 取 `{0, 12}` 真子集是设计内合法取值（运维用 `cycle.hours = [0]` 表达"本环境只跑 00Z"）。上方"请求 06Z cycle"那行用默认 `(0, 12)` 配置，06Z 在两个判据下同样被拒，对本条零判别力。MUST 另有一条用例：`cycle_hours=(0,)` + 12Z 请求 -> 抛 `ConfigError` 且 `path == "cycle.hours"`（实测判据换成 `CYCLE_HOURS_DOMAIN` 时 92 例全绿）
- **12Z 的 happy path（round 4 r4-cand-03）**：全部产出 verdict 的用例都跑 00Z 时，把 cycle 小时带进路径的两道门（`{cycle_hour}` 的实参、cycle 目录戳）与常量 `0` 不可区分。MUST 有一条 12Z 完整用例，其预期清单以**字面量**构造（目录段逐字 `2026030412`、文件名逐字含 `t12z`），断言 `expected_files` 相等且 `complete is True`；MUST NOT 复用测试助手里的 cycle 戳常量。一条用例同时杀死两道门（实测两个变异体各 1 failed，此前均 92 全绿）
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check .` 与 `uv run ruff format --check .` -> 退出码 0
- `cd producer && uv sync --frozen` -> 退出码 0（不得新增依赖）

Pattern escalation（round 2 后补入；两个 failure class 连续两轮复发，repair intensity medium 的升级门已触发）:

```text
Pattern escalation: yes
Failure class A: contract —— judge 的异常收敛面
Invariant A: `judge` 对外只抛 `ConfigError`；任何文件系统原语的**全部**异常面 MUST 被显式收敛成 verdict 数据或 `ConfigError`，MUST NOT 逐个 errno/异常类型地补。
Trigger A: round 1 cand-02（`Path.is_file()` 的 `OSError` 未收敛）-> round 2 r2-cand-02（重写后的 `_check` 丢掉 `Path.is_file()` 原本吞的 `ValueError`）。同一处代码、同一条不变量、连续两轮。

Failure class B: test-evidence —— 取证方法钉的是"例子"而不是"不变量"
Invariant B: 每条证据行 MUST 表达被测**性质**，且其构造 MUST 与被测实现的具体原语/拼法解耦；用例 MUST 由一个能证伪该性质的变异体验证过。
Invariant B 的验收判据（round 4 再收紧）：门的枚举 MUST 是**机械枚举**——用 grep 把 `rawscan.py` 全部 `if`/`elif`/`except`/布尔合取项/排序去重调用连同行号抽出，逐行与审计表对账；任何一行落在"表内"与"死腿登记"两桶之外即阻塞，MUST NOT 以"这不算门"的判断跳过。审计表 MUST 作为交付物落盘（`.workplans/pr-<N>/review/`），commit message 里的聚合数不算数。理由：round 4 的实现者自报 54 门 49 杀，reviewer 用三个存活变异体证伪了其中至少三道门，其中两道根本不在任何一桶里——判断式枚举漏门时没有失败信号。
Invariant B 的验收判据（round 3 换轨）：审计对象 MUST 是"**逐守卫是否存在杀手变异体**"，MUST NOT 是"逐个已有用例是否有判别力"——后者扫不出"该有而根本没写的用例"（r3-cand-03 即此类）。`judge` 的每一道门（含类型守卫、值域门、模式门、分类分支）MUST 各有一个能杀死它的变异体；不可达的防御腿（如三个调用点均不可达的 `ValueError`）MUST 在报告里显式登记为死腿，不得默认为已闭合。
Trigger B: round 1 cand-06/07/08（哨兵桩具体名字、oracle 复用实现的 sorted、chmod 用例对 open-vs-access 零判别力）-> round 2 r2-cand-01/03/04（哨兵变死桩、`..` 参数被 `{lead}` 门吸收、ENOTDIR 分支无用例）。 -> round 3 r3-cand-01/02/03（词表门 3 个参数零判别力、哨兵对宽捕获探针零判别力、`isinstance(cycle, datetime)` 守卫零覆盖）。 -> round 4 r4-cand-01/02/03/04（死腿 D1 登记事实错误——`_render` 四腿全部可达、`cycle.hours` 请求门无杀手且未登记、12Z 从未走 happy path、54 门审计表未落盘）。**连续三轮复发，三轮形态各异（桩错函数 -> 桩错边界层 -> 门根本没有对应用例），已触发 three-round hard gate；retro shape = depth，corrective action = 上述审计对象换轨**（见 `.workplans/pr-38/review/review-failure-retro.md`）。
```

Non-goals:

- raw 只读复制到 `work/raw/` 与临时 `raw-manifest.json` 生成（任务 3.2，后继 issue）——本 issue 只产出判定结果，不产生 manifest 结构、不写任何文件
- 打开 GRIB 文件校验其内部变量/记录：M2 无真实数据，内容与数值正确性归 M4 receipt；本 issue 的"可读"只到能读出一个字节为止
- `lead_hours` 覆盖 0–168h 且与 `forecast_days * 24` 一致的校验：tasks.md 组 1 Non-goals 已把该项路由到 **issue #32**，本 issue 不做（`lead_hours` 是否覆盖 0–168h 属 config 取值正确性，与"逐文件判定"正交）
- 版本化 `producer/config.toml` 生产实例与其真实 `variables`/`bundles`/`lead_hours` 取值：归 **issue #29**；本 issue 全部用例使用内联 TOML 合成值，`{"apcp", "dswrf"}` 仅以模块常量出现、不预设 `variables` 必须包含它们
- cycle 发现/枚举（"最近 7 天窗内最早完整 cycle"）：归 init-bootstrap（任务 11.1）；本 issue 只判定**给定**的一个 source/cycle
- 扫描期与 NWM downloader 并发写入的 TOCTOU：判定结果的时效性由调用方（控制器）承担，归 3.2/组 12
- **`ScanVerdict` 不提供 `(lead, variable, file)` 三元组**（round 1 审核 cand-05，verifier CONFIRMED/DEFER，决定性测试 T2）：3.2 的 manifest 逐变量扇出需要该键关系，但其消费方尚不存在，且 `ScanVerdict` 的五字段形态由本 fixture 逐字钉死；键关系属 3.2 定义自身 seam 时该确立的契约，归 **3.2**。本 issue 只消除该缺口今天可观察的那一半——`expected_files` 的重复项，已由上方单射性要求（cand-01）一并封死
- **raw 清单（manifest）侧的一切要求归 3.2**：`nwm-snapshot-inventory.md` §3.1 原文一律写作"任务 3.1"，其中逐变量扇出、`metadata` 六键契约、`cfgrib_filter_by_keys`/`grib_short_name`、`idx_selector` 单数键落盘、manifest 级 `forecast_hours` 承接，以及该节末"APCP 累积元数据的 fail-closed 要求（R4B2）"两条，**全部属清单写入面，归 3.2**；该文件已于本轮补入同义的「任务号勘误」段。本 issue 只返回 `ScanVerdict`，不产生任何 manifest 结构，也不读写 GRIB `.idx`

Handoff（本 issue 无法自证、必须由下游 issue 关闭的绑定）:

- **归 #29（生产 `config.toml` 实例）**：本 issue 的 f000 不可用变量集以模块常量 `{"apcp", "dswrf"}` 转录自 pin，而 `variables` 取值由 #29 确立——两者命名词表若不一致，f000 规则在生产上退化为恒等空操作，且本 issue 的全部合成用例照常全绿（合成值不预设包含它们）。#29 MUST 断言该模块常量的成员名 ⊆ `raw.gfs.variables` 的词表，否则该开关不可能生效。此绑定已逐字记入 `nwm-snapshot-inventory.md` §3.1 的 f000 行

Review focus:

- f000 规则是否真的有判别力（`f000_special` 两个取值必须产生不同的 `expected_variables[0]`），以及是否误把特例实现成"f000 文件可缺失"
- 拒绝路径是否真的在文件系统访问之前短路——以不存在的 `raw_root` 断言，而非只看代码顺序
- 是否出现按 `source == "gfs"` 的硬分支，或把 `{"apcp","dswrf"}` 之外的 pin 事实（变量全集、lead 步长、horizon）也硬写进代码——那些属 config 取值，归 #29
- 空列表/取值域校验的 `ConfigError.path` 是否为完整点分路径，断言是否用 `excinfo.value.path ==` 而非子串探测（组 1 已裁定子串探测无判别力）
- 是否引入 stdlib 之外的依赖，或运行时 import NWM
- 预期文件集是否严格由 `lead_hours × bundles` 构造，而非列目录后过滤

## 4. state-tools：cfg.ic 工具链

- [x] 4.1 快照并适配 `cfg.ic` 原生分段解析与回写（mesh/river/lake），字节级 roundtrip 测试
- [x] 4.2 实现结构检查（缺段、行数与 header 不符、数值区损坏）
- [x] 4.3 实现重戳到目标 cycle 绝对时间（只改 header、数据不变；服务 init 首态与发布前 T+12 定戳两条路径）
- [x] 4.4 快照负残差归零与域均修正阈值检查纯函数

依赖：组 2（勘察清单定原路径）
§13.1 归属：state
Suggested fixture level: compact - 合成分段状态文件（小规模 mesh/river/lake）覆盖全场景
Minimal mergeable slice: 分段解析与 roundtrip（4.1）——格式层独立合并保绿，重戳/残差为后继

### Issue #8 fixture（任务 4.1）

Fixture level: expanded
Upstream suggested level: compact（override：改动面正面命中强制 expanded 触发词 `parser` / `writer` / `format` / `column` / `field`，并命中 profile 的 domain 触发词 `cfg.ic` 与「状态链」——与 issue #2/#3 同一条覆写理由）
Repair intensity: high（本模块是整条状态链的共享格式根：`init` 首态、每轮 T+12 checkpoint 定戳、发布到 `states/<source>/<T>.cfg.ic` 全部经此解析/回写；profile 把「断链即整链失效」列为首位风险轴，故适用 `Invariant Matrix`）
Project profile: yd-viewer

**核心设计裁决（本 fixture 钉死，实现不得自行改写）**：spec state-tools 要求「解析后 MUST 能无损回写」且验收标准是**字节等价**，但 NWM pin 上的 `packages/common/state_qc.py` **没有 writer**——`_parse_ic_file`（`:424-493`）只返回 `tuple[list[list[float]], list[list[float]], list[list[float]]]`，且解析过程三重有损：`line.strip()` 丢首尾空白、空行被丢弃、token 经 `float()` 丢失原始记法（`0.100000` / `1e-3` / `-0.0` 回写后不可复原）。因此**字节等价不可能由「快照 + 补一个 writer」得到**，格式保真层是本 issue 的新代码，任务标题里的「适配」承担实质工作。数据模型 MUST 逐行保真（保留原始行文本与行序，含行尾形态），而非只保留 float。这一点同时被下游钉死：#9 的重戳要求「数据区 MUST 保持不变」，而 `cfg.ic` 的产出方是 SHUD 求解器与率定末态（格式不由本项目控制），只有逐字保留模型能在不控制格式的前提下满足它。

Change surface:
- 新增 `producer/src/yd_producer/state/__init__.py` 与 `producer/src/yd_producer/state/cfg_ic.py`：格式保真的解析/回写层
- 从 NWM pin 移植的分段识别辅助（逐函数带 `NWM@8ae9b8f2 packages/common/state_qc.py` 溯源注释）：`_looks_like_column_header`（`:741`）、`_section_from_column_header`（`:751`）、`_native_lake_section_preamble`（`:762`）、`_header_counts`（`:574`）、`_numeric_row`（`:730`，仅作内部分类器）、`_read_bytes_limited`（含其说明「为何刻意不走 no-follow 安全读」的 docstring，原样保留）、`_as_float`（`:878-882`，`_header_counts` 的被调用方，随之强制移植）与 `MAX_STATE_IC_BYTES`（`:43`）
- 新增 `producer/tests/test_cfg_ic.py` 与合成 fixture 构造器
- 快照清单 `nwm-snapshot-inventory.md:44` 的目标路径 `state/state_qc.py` 由 **#9** 补齐：本 issue 只落格式层子集到 `state/cfg_ic.py`，不建空的 `state_qc.py` 占位（避免死代码），该行的落地状态在本 PR 内标注为「部分（格式层）」

Must preserve:
- 移植函数的判定语义与 NWM pin 逐字一致（分段识别、lake preamble 处理、declared-vs-actual lake 行数校验）；偏离 MUST 在模块头注明
- 本模块 MUST 保持 stdlib-only、零运行时 NWM import、零数据库/scheduler 依赖（agent-ops §2.2 / §7.2）；不依赖 #5 在途的 object-store 工作
- 不新增依赖、`producer/uv.lock` 不变

Must add/change:
- `parse(path_or_bytes) -> CfgIcDocument`：文档模型 MUST 同时携带 (a) 原始行序列的逐字副本、(b) header 行位置、(c) 每段（mesh / river / lake，lake 可缺）的行区间与行数、(d) 段内数值视图（供 #9 消费的只读派生，非回写来源）
- `render(doc) -> bytes`：回写 MUST 由逐字行还原，MUST NOT 由数值重新格式化。对任何本解析器接受的输入，`render(parse(b)) == b` 逐字节成立
- **行归属必须是全覆盖划分**：文档模型 MUST 把每一行恰好归入一个区域（header / 段列头 / 某段的数据行 / lake preamble / 空行），MUST NOT 存在"未归属"的行。两条由 pin 语义反推的钉死裁决（空行单独成一类：pin 在分段前先丢空行，逐字保真模型必须保留它们才能字节等价，又不能计入任何段的数据行，否则污染 #9 继承的段行数与行区间；见下方证据行 (b)。roundtrip 无法分辨这两种猜法，必须在此定死，否则 #9 的结构检查会继承一个错误划分）：(a) **header 行 = 首个非空行**（pin 在取 `lines[0]` 前先丢空行）；(b) mesh 段内**超出 header 声明 `mesh_count` 的多余数据行 MUST 抛 `ValueError`**——这是对 pin 的**刻意偏离**并 MUST 在模块头注明：pin 的 `_parse_sectioned_rows`（`state_qc.py:531-534`，其中 `:532-533` 是 `if len(mesh_rows) < mesh_count: mesh_rows.append(row)` 这一对）静默丢弃多余 mesh 行，而格式保真根不得静默丢状态行
- 解析级 fail-closed（本 issue 拥有，语义取自 NWM 场景）：文件不存在、路径为目录或不可读、非 UTF-8、超过字节上界、空文件、header 不可解析、分段体被截断、`max_bytes` 为负值、**输入中不存在任何分段列头（即非原生的计数式兼容布局）**——MUST 抛 `ValueError`，MUST NOT 外泄 `UnicodeDecodeError`，MUST NOT 无界读入，MUST NOT 回退到计数式布局。其中把文件不存在/不可读的 `OSError` 统一封装为 `ValueError` 是对 pin 的**刻意偏离**（pin 的 `_read_bytes_limited` `:563-571` 直接抛 `OSError`，由调用方 `except (OSError, ValueError)` 兜住），本 issue 收敛为单一异常以便调用方无需知道两种类型；MUST 在模块头注明该偏离。仓库级错误封装仍归 #9 的边界
- **字节上界 MUST 可注入**：`parse(..., max_bytes=MAX_STATE_IC_BYTES)`，模块默认值等于 pin 的 `64 * 1024 * 1024`。理由：上界的两条边界用例（恰好、超一字节）是杀死"`>` 改 `>=`"变异体的唯一手段，而在真实上界上构造 64 MiB 合法文件并逐行保真读入会让每次 `uv run pytest` 多耗数百 MB 内存与可观时间；边界用例 MUST 在**小的注入上界**上跑，另用一条廉价断言钉死模块默认值等于 pin 常量
- lake preamble（末条 river 行与 lake 列头之间的 `<lake-count> <lake-state-columns>` 行）MUST NOT 被计为 river 行，且回写后仍在原位

Seams under test:
- `state.cfg_ic.parse(...) -> CfgIcDocument` 与 `state.cfg_ic.render(doc) -> bytes`（design.md「Sketch seams under test」seam 3 的文件级纯函数边界）——file→file，无 IO 副作用之外的状态

Selected risk packs（项目特有检查）:
- Schema / columns / units / field names: 分段列头与段归属判定即契约
- File IO / path safety / overwrite: 只读解析 + 有界读；本 issue **不写任何文件**（`render` 返回 bytes，落盘归调用方），故闭包清单只有「有界读」「非普通文件/不可读被拒」适用
- Error handling / rollback / partial outputs: 全部解析失败为 fail-closed，且「失败时不返回部分文档」是可断言的负面证据
- Resource limits / large input / discovery: `MAX_STATE_IC_BYTES` 上界，含**恰好等于上界**与**超出一字节**两条边界用例
- Legacy compatibility / examples: NWM 解析器同时支持兼容的计数式布局；本 issue 的取舍见 Non-goals

Risk packs considered (core):
- Public API / CLI / script entry: not selected - 不接入 CLI，纯模块级函数，无入口注册
- Config / project setup: not selected - 不读 `config.toml`/`local.toml`，无新配置字段
- File IO / path safety / overwrite: selected - 见上
- Schema / columns / units / field names: selected - 见上
- Auth / permissions / secrets: not selected - 无凭据面
- Concurrency / shared state / ordering: not selected - 纯函数，无共享状态；发布顺序归 #24
- Resource limits / large input / discovery: selected - 见上
- Legacy compatibility / examples: selected - 见 Non-goals 的兼容布局取舍
- Error handling / rollback / partial outputs: selected - 见上
- Documentation / migration notes: not selected - 无迁移；溯源由模块头注释承载

Domain packs (from active profile):
- Geospatial / CRS: not selected - 无几何
- Time series / forcing / temporal boundaries: not selected - 本 issue **不解释** header 的时间语义，只保留其行文本；`cfg_ic_header_minute_index/_time/_shape` 归 #9 任务 4.3
- 状态链 / warm-start 定戳: **selected** - 本模块是状态链的格式根，逐字保真是 #9 重戳「数据区不变」的前提
- NWM 快照溯源 / DB-free 隔离: **selected** - 移植函数须带 `NWM@8ae9b8f2 <原路径>` 头；断言模块内零 NWM import、零 DB 符号

Invariant Matrix:
- Governing invariant: 状态文件穿过 producer 时逐字节不变，除非某次操作显式改写指定字段
- Source-of-truth identity/contract: `cfg.ic` 原生分段布局——header 行 + mesh 段 + river `Stage` 段 +（可选）lake preamble 与 lake 段
- Producers: 外部（SHUD 求解器输出、率定末态基线包，均不由本项目控制格式）
- Validators/preflight: 本模块的解析级 fail-closed；结构检查 `state_ic_structure_complete` 归 #9
- Storage/cache/query: `states/<source>/<T>.cfg.ic`（写入面归 #21/#24）
- Public routes/entrypoints: none - 本 issue 不接入 CLI，入口经 #11/#23 的 init/run
- Frontend/downstream consumers: #9（结构检查/重戳/负残差）、#16（checkpoint tracker）、#24（发布）
- Failure paths/rollback/stale state: 解析失败即 fail-closed 抛错，不返回部分文档；无写入面故无回滚
- Evidence/audit/readiness: 字节级 roundtrip 用例 + 结构索引 oracle + 溯源头检查（任务 2.3 的通用检查未落地前，本模块自带断言）
- Regression rows:
  - 干净合成 mesh+river 文件 -> `render(parse(b)) == b`，且段索引与手算期望一致
  - **脏**合成文件（CRLF、行尾空格、空行、`0.100000`/`1e-3`/`-0.0` 混合记法、无末尾换行）-> 同样逐字节等价（这条是判别力的承重条：canonical 化的 writer 在干净输入上恒绿，只在脏输入上变红）
  - 超上界文件 / 非 UTF-8 字节 / 截断的分段体 -> 抛 `ValueError`，不返回文档、不无界读

Required evidence（每条 input -> expected output）:
- mesh+river 干净合成文件 -> `render(parse(b)) == b` 逐字节；`doc.header_index == 0`，mesh/river 段行区间等于手算期望
- mesh+river+lake 干净合成文件（含 `<lake-count> <lake-state-columns>` preamble）-> 逐字节等价；lake preamble 行**不**落在 river 段区间内，且 river/mesh/lake 三段行区间均等于按合成构造手算的期望值。**注意 native header 不声明 river/lake 行数**：其第二个数值 token 是 mesh 状态列数而非 river 元素数（`state_qc.py:496-506` 的 docstring 与 `_header_counts` `:601-606` 逐字为证），lake 行数由 preamble 声明。故 MUST NOT 写"river 行数 == header 声明值"这类断言；river/lake 计数与权威模型元数据的比对归 #9
- **两种不同 mesh 规模**（如 3 与 7）各跑一遍上述索引断言 -> 期望值随规模变化（防止把段索引写成常量而恒真）
- 脏合成文件矩阵（CRLF 行尾 / 行尾多余空格 / 段间空行 / 数值记法混合 `0.100000`+`1e-3`+`-0.0` / 文件无末尾换行）-> 每条均 `render(parse(b)) == b` 逐字节
- **脏输入不得降级为"只保字节、不分段"**：脏矩阵中至少一条（CRLF + 行尾空格 + 段间空行三者叠加）除逐字节等价外，MUST 同时跑**完整的段索引 oracle**。理由：pin 在分段前先 strip 并丢空行，逐字保真模型若在 detection 路径上绕过该归一化，就会在脏输入上退化为"全部行未归属、段区间为空"——这种实现对只断言字节等价的脏用例**全绿**
- preamble 声明 lake 数与实际 lake 行数不符（lake 体被截断）-> 抛 `ValueError`（对应 pin `state_qc.py:552-557`）。**不写"header 声明 lake 但无 lake 段"用例**：native header 的 lake 槽恒为 0，该场景在原生布局下不可达；若 lake 段整体缺失，preamble 行会被当作普通 river 行消费而不报错，这是 pin 的既有语义
- 合法的**计数式兼容布局**文件（无任何分段列头）-> 抛 `ValueError`，消息指明需要原生分段布局（钉死 Non-goals 的 native-only 裁决，防止实现顺手移植 pin 的回退分支）
- 路径不存在 / 路径是目录 / 路径不可读 -> 均抛 `ValueError`（而非 `OSError`）
- 恰好等于**注入上界**字节的合法文件 -> 解析成功；上界 + 1 字节 -> 抛 `ValueError` 且消息含上界语义；另断言模块默认 `max_bytes == 64 * 1024 * 1024`
- `max_bytes` 为负 -> 抛 `ValueError` 且**在任何读取之前**（round 1 验证闸门 batch 3 cand-1 CONFIRMED/FIX_NOW：`handle.read(max_bytes + 1)` 在 `max_bytes == -2` 时退化为 `read(-1)`，实测把整个文件读入，随后 `len(data) > max_bytes` 仍然抛错，于是这次无界读**长得和一次正常拒绝一模一样**）
- **生成器发射包络 MUST 覆盖解析器接受域**（round 1 验证闸门 batch 1 的不变式闭合，5 条同类 CONFIRMED/FIX_NOW）：凡解析器接受的输入形态，合成生成器 MUST 能构造。已实测的三处缺口逐条补齐并各配一条断言——(a) **Tab 分隔**：真实 native `cfg.ic` 是 Tab 分隔（pin `tests/test_state_qc.py` 的 `_write_native_ic` 用 `"\t".join(...)`、QHH fixture 用 `Index\tRiver_Stage`），而脏矩阵此前 100% 空格分隔，实测「`render` 把 `\t` 归一为空格」的变异体在全套 46 条下存活，却会逐字节损坏每一个生产文件；(b) **首行为空行/纯空白行** -> `doc.header_index == 1`（resp. 2）、`doc.roles[0] is BLANK`，钉死上方「header 行 = 首个非空行」裁决——该裁决此前无任何用例，实测退回 `lines[0]` 的变异体全绿；(c) **`lake_count=0`（lake 段存在但为空）** -> `doc.lake is not None`、`row_count == 0`、`span is None`、`declared_lake_count == 0`，钉死「lake 缺席」与「lake 段空」可区分——pin 的 `_native_lake_section_preamble` 只拒 `lake_count < 0`，故空 lake 段在接受域内
- **数值视图 MUST 断言值而非只断形状**（batch 1 cand-2）：以 mixed_notation 构造的手算元组逐值断言（如 `mesh.rows[0] == (1.0, 0.1, 0.001, -0.0, 25.0, 0.0)`）。此前只断 `len()` 与 `isinstance(float)`，实测把三处 `append` 全换成 `tuple(0.0 for _ in row)` 后 46 条全绿——而 #9 的负残差处理正是消费这个视图，全零视图会报告"零负残差"并且什么都不修正
- **逐函数溯源断言 MUST 按函数边界取窗**（batch 1 cand-4）：现行 `source[marker:marker+1200]` 定长窗口会越进下一个函数，实测 6 个辅助里有 4 个可被**邻居的**溯源注释满足（`_numeric_row` 窗口内有 3 个标记），删掉 `_numeric_row` 自己那行注释后全绿。改为切到下一个 `\ndef ` 或用 `ast` 取函数源码段，并对 6 个辅助逐一验证「删掉自己那行即变红」
- 空文件 / 非 UTF-8 字节串（`b"\xff\xfe\x00\x01..."`）/ 非数值行 / 截断 body -> 均抛 `ValueError`，MUST NOT 外泄 `UnicodeDecodeError`
- 模块头溯源断言：`state/cfg_ic.py` 含 `NWM@8ae9b8f2 packages/common/state_qc.py`；模块源码内无 `import` NWM 包、无数据库符号
- **预登记必须被杀死的变异体**：(a) `render` 改为按 float 重新格式化 -> 脏输入用例必须变红；(b) 段归属判定偏移一行 -> 结构索引 oracle 必须变红；(c) lake preamble 计入 river 行 -> 三段用例必须变红；(d) 上界比较由 `>` 改 `>=` -> 恰好上界用例必须变红。**变异证明 MUST 按 `openspec/project-profile.md:50-55` 的"Mutation-testing hazards"执行**（本仓已实测绊倒多个独立 agent，四种假绿都长得像好消息）：`rsync --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache'` 且副本内 `rm -rf .venv && uv sync`、先断言 `yd_producer.__file__` 落在 scratch 副本内、每个变异体之间 `PYTHONDONTWRITEBYTECODE=1` 并清 `__pycache__`（(a)/(c) 易出等长字面量改动，会复用上一个变异体的 `.pyc`）、scratch 目录名含 `issue-8` 唯一标识，并另跑一个必然变红的控制变异做校准
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check .` 与 `uv run ruff format --check .` -> 退出码 0
- `cd producer && uv sync --frozen` -> 退出码 0（不得新增依赖）

Non-goals:
- 结构检查 `state_ic_structure_complete` / `run_state_variable_qc`（任务 4.2，issue #9）、重戳与 `cfg_ic_header_minute_index/_time/_shape`（任务 4.3，issue #9）、负残差与域均修正（任务 4.4，issue #9）
- **不移植 NWM 的 `tests/test_state_qc.py` 测试代码**：该文件的解析失败用例（`test_empty_file_is_parse_failure`、`test_oversized_ic_fails_without_crash`、`test_binary_non_utf8_ic_fails_without_crash` 等）全部经 `run_state_variable_qc` 行使，而该函数归 #9。本 issue 在新 seam 上**新写**测试，以 NWM 的**场景**为独立真值来源，测试代码本身随 #9 到位。清单 `nwm-snapshot-inventory.md` 中该测试文件的落地归 #9
- **不落 `producer/tests/test_cfg_ic_header.py`**：`nwm-snapshot-inventory.md:56` 记录了配对约束——该测试引用的三个符号全部出自 `runtime.py` 抽取集（capability 6），缺任一即不可导入；归 #9 与 capability 6 之后
- **不支持兼容的计数式布局**（NWM `_parse_ic_file` 在原生分段解析失败时的回退分支）：issue #8 的 In Scope 逐字写作「原生分段」。取舍依据与**未决点**：唯一可能是非原生格式的输入是率定末态基线包，而 compute-loop §6 明写「基线模型包的现场路径和归档方式由实施方管理，不进入 Git」，其实际格式在本阶段**不可核**。故本 issue 按原生分段 fail-closed（非原生输入抛 `ValueError` 而非静默走回退），并记录该假设；「率定末态是否为原生分段格式」的核实与兼容布局的归属裁决路由至 **#32**，触发点是 #11（init 首态）真正读入基线包时。MUST NOT 静默支持两种布局
- **本模块是共享格式根，#9 MUST 复用而非重新移植**：`_looks_like_column_header` / `_section_from_column_header` / `_native_lake_section_preamble` / `_header_counts` 落在 `state/cfg_ic.py` 后，#9 的 `state/state_qc.py` MUST 从 `cfg_ic` 导入这四个符号；再移植一份即为 pin 分段逻辑的双权威副本，本 fixture 显式禁止
- 不接入 CLI、不写入任何文件、不做发布顺序相关工作

Review focus:
- `render` 是否真由逐字行还原——任何经 `float`/格式化字符串重建行文本的路径都是缺陷（会在脏输入上丢字节，而干净输入恒绿，看不出来）
- roundtrip 断言是否具判别力：脏输入矩阵是否真覆盖 CRLF / 尾空格 / 空行 / 记法混合 / 无末尾换行五类，结构索引 oracle 是否用了两种 mesh 规模而非常量期望
- 移植函数是否与 NWM pin 逐字一致、是否逐函数带溯源注释；有无引入运行时 NWM import 或数据库符号
- `MAX_STATE_IC_BYTES` 是否在**读取前**生效（有界读），而非先读满再判断
- 是否越界落地了 #9 的结构检查/重戳/负残差符号（含"顺手先放着"的死代码）

### Issue #9 fixture（任务 4.2–4.4）

Fixture level: expanded
Upstream suggested level: compact（override：改动面正面命中强制 expanded 触发词 `parser` / `format` / `schema` / `field`（header token 布局判定、段列头契约、状态列位置语义），并命中 profile 的 domain 触发词 `cfg.ic`、「状态链」、`重戳/restamp`、`T+12`、`checkpoint`——与 issue #8 同一条覆写理由链；此外本 issue 是格式根上的**第一个写面**（header 覆写）与 `Section.rows`/`span` 的**第一个消费方**）
Repair intensity: high（本 issue 决定「状态时间头是否对应 cycle 绝对时间」与「状态数值是否可信」两条判定，profile 把「断链即整链失效」列为首位风险轴；重戳写错即整条 warm start 链静默错位，故适用 `Invariant Matrix`）
Project profile: yd-viewer
Width exception: merged-tasks（issue 正文声明）——4.2/4.3/4.4 三个纯函数同属 state 工具族、共用同一批合成 fixture 与 pytest 路径
Minimal mergeable slice: 组 4 余量（issue 正文声明）；首刀（4.1 格式层）已由 #8 剥离并合并

**上游硬依赖：issue #54 是 `Blocks #9`**。#54 的六条验收标准逐条并入本 fixture（见下方「#54 并入映射」），不得只做 4.2/4.3/4.4 的字面三项而把 #54 留给下一个 PR——#54 的定位就是「归 #9 的 QC/结构检查层」，其五条发现全部是本 issue 构建其上的格式根的既有 fail-open。

#### 核心设计裁决（本 fixture 钉死，实现不得自行改写）

**裁决 1 —— 序列化法则：4.3 与 4.4 一律重建在 `CfgIcDocument` 之上，MUST NOT 沿用 pin 的整文件重拼。**
pin 的 `normalize_state_negative_residuals`（`state_qc.py:154`）与 `_normalized_checkpoint_ic_file`（`state_cli.py:256`）都以 `content.splitlines()` 拆开、改完后 `"\n".join(lines)` 重拼整个文件，改行内部再 `"\t".join(tokens)`。这在 yd 侧**不可用**：spec state-tools 要求重戳「数据区 MUST 保持不变」，而整文件重拼会把每一条**未被改动**的行的 CRLF 归一为 LF、把行尾空格与 Tab/空格混排全部重写。#8 已经为此把格式层做成逐行保真模型。故本 issue 的法则是：

- 只有**被刻意改动的那几行**重新序列化；其余每一行 MUST 逐字节保持原样（含各自的行尾符）。
- 重戳改动集 = **仅 header 行**；负残差改动集 = **仅真正含负值的数据行**。
- 判别力承重条：该法则**只在脏 fixture 上可证伪**——canonical 化的写法在干净输入上恒绿。故所有「未改动行逐字节不变」断言 MUST 在 CRLF / 行尾空格 / Tab 分隔 / 空行 / 混合数值记法的脏矩阵上跑（见下方 Required evidence）。

**裁决 2 —— 改行的内部形态：改动行 MUST 只替换目标 token 的字节，保留该行其余空白布局与行尾符。**
pin 的 `header[minute_index] = ...` + `"\t".join(header)` 会把 header 行里**未被改动的 token 之间**的原始分隔（空格/多空格/Tab 混排）一律重写成单 Tab。这是对 spec「只改写状态时间头」的超出：mesh 计数 token 与列数 token 的字节不该动。故 yd 侧 MUST 就地替换 token 切片，保留前后空白。这是对 pin 的**刻意偏离**，MUST 在模块头注明，并由一条「header 行用多空格 + 行尾空格 + CRLF 构造」的用例钉死（只有 minute token 的字节变化，行内其余字节与行尾符不变）。同一条适用于负残差的改行。

**裁决 3 —— 文档改写只经显式 API，禁止裸 `dataclasses.replace`（#54 第 5 条）。**
`cfg_ic.CfgIcDocument` 新增 `__post_init__` 构造期不变量校验（至少：`len(roles) == len(lines)`；`0 <= header_index < len(lines)`；每个 `Section.column_header_index` 与 `data_line_indices` 全部落在 `[0, len(lines))`；`lake_preamble_index` 为 None 或在界内），以及**行替换 API** `with_replaced_lines(replacements: Mapping[int, str]) -> CfgIcDocument`：按行号替换行文本、**行数恒定**（故 `roles` / `header_index` / 各段行号全部继续有效），替换值不含行尾符、由 API 重新贴回**原行的**行尾符（原行无行尾符则替换后也无）。越界行号或试图改变行数 MUST 抛 `ValueError`。4.3 与 4.4 一律经此 API 产出新文档。

**裁决 4 —— 非有限值 MUST 在负残差投影之前被拒（#54 第 1 条的完整闭合）。**
#54 第 1 条只说「移植 `_check_block_range` 时连 isfinite 门一起移植」。本 fixture 复核 pin 的**残差函数**后发现该条只闭合了一半——pin 的 `normalize_state_negative_residuals` 自身**没有** finiteness 门，实测（`uv run --no-project python`，本 fixture 亲自跑）：

```text
nan >= 0.0 -> False          # 进入归零分支，NaN 被静默写成 0
correction=-nan -> nan       # nan > 1e-2 -> False，不计入 over_tolerance_clamp
sum -> nan                   # 域均 = nan
mean>cap -> False            # nan > cap 为 False → 两条阈值门全部放行 → accepted
inf >= 0.0 -> True           # +inf 不进归零分支，原样存活到输出
-inf >= 0.0 -> False         # 仅 -inf 因 correction=inf 触发域均超限而 fail-closed
```

**作用域更正（fixture 复核补，比上面的实测更严）**：`-inf` 也**不是**普遍被拦——两条域均和只由 **unsat 列**与 **river stage 列**累加（pin `state_qc.py:248-255`），故 `-inf` 落在 canopy / snow / surface / gw 或 lake 列时只计入 `max_correction_m` 与 `over_tolerance_clamp_count`，**照样 accepted**。即 pin 的残差层对 **NaN 静默归零并接受**、对 **+inf 原样放行**、对**非域均列的 -inf 归零并接受**，只有落在 unsat / river-stage 列的 `-inf` 才因域均 inf 被拦。故：

- 结构检查（4.2）MUST 移植 pin `_check_block_range` 的 isfinite 门，且 MUST 保持 pin 的判定次序——**finiteness（`state_qc.py:827`）先于负值（`:832`）**（#54 明文要求写进实现说明，「NaN 从 `value < 0` 底下溜过去」是错误描述，不得照抄）；
- 负残差处理（4.4）MUST 在任何投影之前拒绝非有限值，抛 `ValueError`。这是对 pin 的**刻意偏离**（pin 无此门），MUST 在模块头注明，理由即上方实测。

**裁决 5 —— 负残差超阈值的错误契约：抛 `ValueError`，证据不丢。**
spec state-tools 明写「超阈值 MUST 报错」「处理报错，不产出修正后状态」，而 pin 返回 `accepted=False` 的 dataclass。yd 侧 MUST 抛 `ValueError`（承 #8 已确立的「本模块族的结构性/语义性拒绝一律 `ValueError`」约定），且异常实例 MUST 携带 `.evidence: dict[str, Any]`——内容即 pin `StateResidualNormalization.evidence()` 的完整载荷（`accepted=False` 与 `reason` 在内），使 receipt 侧证据零丢失，同时「不产出修正后状态」由「不返回文档」结构性保证。这是对 pin 的**刻意偏离**，MUST 在模块头注明。成功路径仍返回 pin 形状的 `StateResidualNormalization`（含新文档），`accepted` 字段保留以对齐 pin 的证据形状。

**裁决 6 —— 本 issue 不写任何文件（承 #8 先例）。**
pin 的重戳经 `atomic_write_bytes_no_follow` 落 `.{name}.normalized` 点前缀兄弟文件；该 helper 属 `safe_fs`，是 **issue #5 在途**、本仓尚未落地的面（`producer/src/yd_producer/store/` 不存在）。故 4.3/4.4 的公共 seam 一律 **doc→doc / doc→bytes**，落盘归调用方（#21 init 首态、#24 发布器）。design.md seam 3 的「file→file」按 #8 已确立的读法执行：**读**可由 `cfg_ic.parse` 收路径（有界读），**写**返回 bytes。本条是记录在案的解释，不是新裁决。

**裁决 7 —— 4.3 只落重戳面，rekey 面路由到 #16/#24。**
快照清单 `nwm-snapshot-inventory.md` §1 中 `packages/common/state_cli.py` 行的抽取集含两族符号：(a) **重戳面**——把 header minute token 覆写为目标绝对时间；(b) **rekey 面**——`_checkpoint_with_header_time` / `_valid_time_from_header_minute` / `_lead_hours_from_run_valid_time` / `StateCheckpoint` / `StateRunContext`，即「读 header 反推 checkpoint 的 valid_time 并改写 lead_hours」。issue #9 正文的 In Scope 对 4.3 的定义是逐字的「重戳到目标 cycle 绝对时间（只改 header、数据不变；服务 init 首态与发布前 T+12 定戳两条路径）」，不含反推。rekey 面的消费者是 tracker（#16）与发布器（#24），在本 issue 内无调用方，落地即死代码。故本 issue 只落 (a)，(b) 记为 non-goal 并路由；`nwm-snapshot-inventory.md` §1 中 `packages/common/state_cli.py` 行的落地状态同步改注为「部分（重戳面）」，同清单 §1 中 `tests/test_state_manager.py` 行（L2187-2471 抽取段）同理只落其中行使重戳面的用例，rekey 用例随 (b) 走。

#### Change surface

- 新增 `producer/src/yd_producer/state/state_qc.py`：任务 4.2 结构检查 + 任务 4.4 负残差；header 判定基座**不在本模块**，从 issue #22 的 `state/header_time.py` 导入并转出（见下方 4.3 段）
- 新增 `producer/src/yd_producer/state/restamp.py`：任务 4.3 重戳面
- 改 `producer/src/yd_producer/state/cfg_ic.py`：#54 第 3/4/5 条（分段唯一性守卫、BOM 感知诊断、`__post_init__` 不变量与 `with_replaced_lines`）。**连带强制项**：第 3/4 条各往 `parse` 里新增一条**无 pin 对应物**的 `raise ValueError`，故模块头的「对 pin 的刻意偏离」清单 MUST 由六条扩到**八条**（段重入守卫、BOM 拒绝），且 `__post_init__`/`with_replaced_lines` 若引入新的拒绝路径一并入册。漏更清单是 #8 已复发两轮的同一失败类（`false-exhaustiveness-claim`），本 issue MUST 由上面改造后的 `ast` 计数测试机械闭合
- 改 `producer/src/yd_producer/state/__init__.py`：导出新符号，并更新模块 docstring 里「4.2–4.4 另行落地」的措辞
- 新增 `producer/tests/test_state_tools_qc.py`、`producer/tests/test_state_tools_restamp.py`（**刻意避开** `producer/tests/test_state_qc.py` / `producer/tests/test_state_restamp.py` 这两个清单 §1 为 pin 用例**移植**保留的目标路径：本 issue 的用例是按 NWM 场景新写的，占用那两个路径就要贴上 `# NWM@8ae9b8f2 tests/...` 溯源头，等于在清单里申报一次没发生过的移植）；扩充 `producer/tests/cfg_ic_fixtures.py` 的合成构造器（非有限值、BOM、段重入、U+0085 内嵌、负残差矩阵）
- 改 `producer/tests/test_cfg_ic.py`：#54 第 3/4/5 条在格式层的负例；**并按 #54 评论 2 的方向改造 `:718-733` 的偏离穷尽性测试**（现行写法自指——只断 docstring 写着「六条」、从代码零导出，故对「偏离清单漏登记」恒绿）：改为用 `ast` 数 `parse` 体内的 `ast.Raise` 节点并与模块头登记的偏离条数闭合。**计数域 MUST 覆盖所有承载登记偏离的函数**：`__post_init__` / `with_replaced_lines` 的拒绝路径若入册，其 `ast.Raise` 一并计入，否则漏登记不被该测试覆盖——那正是本测试要终结的那类恒绿
- 改 `openspec/changes/m2-producer-core/specs/state-tools/spec.md`：结构检查 Requirement 补两条 Scenario（非有限值、river 行数与权威计数），负残差 Requirement 补一条 Scenario（非有限值在归零前被拒）并把「沿用 NWM 语义」收窄为「除模块头登记的偏离外沿用」——裁决 4 的 spec 授权
- 改 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md`：`:44` 落地状态由「部分（格式层）」改为完成状态并点名本 issue 落的符号；`:45` 标注「部分（重戳面）」并写明 rekey 面路由 #16/#24、`_read_limited_*_no_follow` 的闭包切点；`:55` 标注只落重戳用例
- 不改 `config.py` / `cli.py` / `geometry.py` / `nwm.py` / `executor.py` / `pyproject.toml` / `uv.lock`

#### Must preserve

- `render(parse(b)) == b` 对本解析器接受域内的**全部**输入继续逐字节成立；#8 的 `test_cfg_ic.py` 既有用例除本 issue 明确收窄接受域的三条（段重入、BOM、非有限值——若实现选择在解析层拒绝）外全部继续通过
- 从 `cfg_ic` **导入**分段识别与读取基座，MUST NOT 再移植一份：`_looks_like_column_header`、`_section_from_column_header`、`_native_lake_section_preamble`、`_header_counts`、`_as_float`、`_numeric_row`、`_read_bytes_limited`、`MAX_STATE_IC_BYTES`（`nwm-snapshot-inventory.md` §1 中 `packages/common/state_qc.py` 行的双权威副本禁令）
- 移植函数的判定语义与 NWM pin 逐字一致，逐函数带 `NWM@8ae9b8f2 <原路径>` 溯源注释；每一条偏离 MUST 在模块头注明（本 fixture 的裁决 1/2/4/5 即偏离全集的下界，实现若再生偏离一并入册）
- **模块头偏离台账的两条口径**（round-2 verifier 裁定，与 #8 已落地的 `cfg_ic.py` 惯例对齐）：(a) **错误契约替换**（pin 的 `StateManagerError` → 本仓 `ValueError`）算一条偏离，MUST 记进模块头，不得只记在 `nwm-snapshot-inventory.md` 的 `剥离点` 列——`cfg_ic.py` 把 `OSError`→`ValueError` 记作其偏离 3、`state_qc.py` 也已把同类改动记为自己的一条，`restamp.py` 却漏记，属家族内不对称；(b) 台账里**对 pin 行为的定量描述 MUST 是谓词本身而非近似**：pin 的 `header_changed = round(observed_minute) != round(expected_minute)`（`state_cli.py:294`，两边单位都是**分钟**）意味着静默保留窗口是「round() 落同一分钟」，差可达近 60 s（`9.51` 与 `10.49` 同为 10，相距 58.8 s 被静默保留；而 `10.4` 与 `10.6` 分属 10/11，相距仅 12 s 却会被重写），且 banker's rounding 只会**放宽**该窗口（`round(9.5)==round(10.5)==10`，整 60 s 亦被静默保留）。写成 `< 30 s` 既不充分也不必要
- pin 的数值常量逐字保留：`MAX_UNSAT_MEAN_CORRECTION_M = 2.0e-4`、`MAX_RIVER_MEAN_CORRECTION_M = 2.0e-3`、`_NEGATIVE_ZERO_TOLERANCE = 1.0e-2`、`_MAX_STATE_VALUE_M = 1.0e6`、`_MESH_STATE_COLUMNS` / `_RIVER_STATE_COLUMNS` / `_LAKE_STATE_COLUMNS`
- pin 的域均分母语义逐字保留：unsat 域均除以 **mesh 行数**，river 域均除以 **river 行数**；`_NEGATIVE_ZERO_TOLERANCE` **不**闸投影，只切分证据（`over_tolerance_clamp_count`）
- stdlib-only、零运行时 NWM import、零数据库/scheduler 依赖；不新增依赖、`producer/uv.lock` 不变
- 不依赖 #5 在途的 `safe_fs` / object-store 工作

#### Must add/change

**A. 任务 4.2 结构检查（`state/state_qc.py`）**

- `run_state_variable_qc(source, *, expected_mesh_count=None, expected_river_count=None, expected_lake_count=None) -> StateQCResult`：pin `state_qc.py:324` 的移植。解析失败**本身即 QC 失败而非崩溃**（pin 明文语义）——捕获 `cfg_ic.parse` 的 `ValueError`，返回 `passed=False` 且 `reason` 以 `IC parse failed: ` 起头。`StateQCResult(passed, checks, reason)` 与 `to_dict()` 形状逐字保留。
- `state_ic_structure_complete(source, *, expected_mesh_count=None, expected_river_count=None, expected_lake_count=None) -> bool`：pin `:391` 的移植，窄谓词，供 #16 tracker 在轮询非原子重写的 `cfg.ic.update` 时使用；解析失败返回 `False`（不抛）。
- `_check_row_counts`（pin `:791`）与 `_check_block_range`（pin `:802`）逐字移植，**含 isfinite 门且次序不变**（isfinite 先于负值先于上界）。
- **缺段的具体报错（spec Scenario「缺 river 段被拒 → 指明缺失段」）**：pin 返回三个 list，没有「段缺席」概念。报错**措辞**（点名 `missing river section` 而非 `river row count 0 != expected N`）是对 pin 的扩展；但**无条件性**是一条刻意偏离，MUST 按偏离入册而非按扩展记：round-2 verifier 把 pin 模块拷出直接执行，在同一份字节负载（mesh 段完整、**river 列头整段缺席**——连列头都没有、`expected_*` 全为 `None`）上得到 pin `passed=True` / `structure_complete=True`，yd 得到 `passed=False` / `False`——**同一输入上的判定反转**，机制是 pin 的 `_check_row_counts`（`state_qc.py:791-798`）对 `expected is None` 逐类跳过、`_check_block_range("river", [], …)` 对空 list 返回 `None`。**反转的触发条件是列头缺席，不是行数为零**：river 列头在场而其下零行，对 yd 而言该段「存在」，走 `_check_row_counts` 的行数消息，而该门对 `expected is None` 跳过——两侧同为 `passed=True`，**不构成反转**，MUST NOT 拿这种负载做本条偏离的用例。故 `state_qc.py` 模块头的刻意偏离计为**五条**，且 `run_state_variable_qc` / `state_ic_structure_complete` 的逐函数溯源注释 MUST NOT 单挂 `（逐字移植）`——二者的判定路径已含这道非 pin 闸门，注释须点名它。段存在但行数不符仍走 `_check_row_counts` 的行数消息（该门对 `expected is None` 仍跳过，不受本条影响）。
- `expected_*` 计数由**调用方**传入（pin 的 `expected_*_count` 约定）。把权威 `reach_count`（`Config.reach_count`，本仓值 3988）接进来是**调用方**领域（#21 init / #24 发布器），本 issue 只落「传了就比对、不符即失败」的判定与用例，不在 state 层读 `config.toml`。
- **不移植 `_check_water_balance`（pin `:843`）与 `water_balance` 形参**：pin 自己标注为 Lane 2 TODO、恒 `skipped`；数值正确性在本项目显式归 M4（profile 风险轴）。落地即死参数，记为 non-goal。

**B. 任务 4.3 重戳（`state/restamp.py`）**

- `cfg_ic_header_minute_index(header_tokens) -> int | None`（pin `state_qc.py:609`）、`cfg_ic_header_minute_time(header_tokens) -> float | None`（`:629`）、`CfgIcHeaderShape` 与 `cfg_ic_header_shape(header_tokens, *, expected_mesh_count=None)`（`:664`）、`_VALID_CFG_IC_HEADER_TOKEN_COUNTS = (3, 4)`（`:646`）逐字移植。**落点是 `state/header_time.py`，不是 `state_qc.py`**：issue #22（任务 12.1）因严格前沿闸门需要同一判定基座而先行落地了这五个符号，快照清单 §1 中 `packages/common/state_qc.py` 行明文「#9 MUST 从 `header_time` 导入这五个符号，MUST NOT 再移植一份」。故 `state_qc.py` / `restamp.py` 一律 **import**，两模块的模块级定义名字集 MUST NOT 含这五个名字——否则即造出 pin header 判定的双权威副本。（本条原写「落点为 `state_qc.py`（pin 同文件）」，是 #22 落地前的写法；#22 合并后按文档优先原则更正。）
- `restamp_to_absolute_time(doc: CfgIcDocument, target: datetime) -> CfgIcDocument`：**唯一**重戳函数，两条调用路径（init 首态定 T、发布前 checkpoint 定 T+12）只差 `target` 实参，MUST NOT 分裂成两个函数或加 `mode` 开关。语义：
  - `minute = _ensure_utc(target).timestamp() / 60.0`，写成 `f"{minute:.6f}"`（pin `state_cli.py:299` 的字面格式）；
  - 覆写**之前** MUST 先过 `cfg_ic_header_shape(header_tokens)`；不合法则抛 `ValueError`，消息以 `STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID` 起头（pin #1430 的中毒 IC 闸门：两 token 形状下被定位的「minute」其实是 mesh 状态列数，覆写它会造出让 SHUD 申请约 183 GB 的文件）；
  - **闸门次序是对 pin 的刻意偏离，MUST 在模块头注明**：pin 的 `_normalized_checkpoint_ic_file` 先取 `cfg_ic_header_minute_index`（`state_cli.py:271`）、`None` 即早退，再查 shape（`:283`）；yd 侧把 **shape 提到 minute-index 之前**，因为 shape 合法（3 或 4 个数值 token）蕴含 minute-index 必不为 None，反序才有「先按不合法布局定位再拒绝」的中间态。连带后果：`cfg_ic_header_minute_index` 返回 `None` 这条分支在本 seam 上**不可达**（见下方证据表的对应说明）；
  - `cfg_ic_header_minute_index` 返回 `None` 在本 seam 上**恒不可达**，故 MUST 写成带 `# pragma: no cover` 的内部不变量自检（承 #8 在 `parse` 末尾全覆盖划分自检上确立的同一手法），**MUST NOT** 为它写用例。可达性论证：shape 合法 ⟺ 恰 3 或 4 个数值 token（`cfg_ic_header_shape`，pin `state_qc.py:700`），而 minute-index 为 `None` ⟺ 数值 token < 2（pin `:620-623`），两集合交空；shape 闸门在前（见上一条次序偏离），故任何到达取 minute-index 那一步的 header 都必有 minute token。经 `cfg_ic.parse` 的输入更早一层就被拦：数值 token < 2 时 `_header_counts` 返回 `None`，`parse` 抛 `unreadable IC header`（`cfg_ic.py:187-189`）。**任何为它写的用例都会红在 `STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID` 上或只断 `ValueError` 而恒绿**，两种都是假证据；
  - 改动集仅 header 行，且行内仅 minute token 的字节变化（裁决 2）；经 `with_replaced_lines` 产出新文档（裁决 3）。
- `_ensure_utc`（pin `state_cli.py:1186`）随之移植：naive datetime MUST 按 UTC 解释还是拒绝，按 pin 语义逐字执行并在模块头写明。
- 常量 `STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID`（pin `state_cli.py:86`）移植。`STATE_CHECKPOINT_IC_HEADER_SHAPE_REKEY_SKIPPED`（`:87`）与 `LOGGER`（`:46`）随 rekey 面走，本 issue **不**落。

**C. 任务 4.4 负残差（`state/state_qc.py`）**

- `StateResidualNormalization` dataclass 与 `evidence()` 的字段/键名逐字移植（pin `:109-151`），`policy` 值保持 `"unbounded_physical_zero_projection_v4"`。**唯一登记的字段改名**：pin 的 `content: str`（`state_qc.py:111`）改为 `document: CfgIcDocument`，是裁决 1/6 的连带后果（本层不再持有整文件字符串）；`evidence()` 的键集**不受影响**（pin `:126-151` 本就不含 `content`）。除此之外不得增删改任何字段名。
- `normalize_negative_residuals(doc: CfgIcDocument) -> StateResidualNormalization`：
  - **先**拒绝非有限值（裁决 4）；
  - 无条件把所有状态列（列索引 >= 1）的负值投影为零，**无逐格修正上限**（pin 模块头逐字记录的 owner directive 与 4327 个生产文件的分布数据是该裁决的依据，MUST 在模块头保留该说明的实质）；
  - 拒绝判据**只有**两条域均门：unsat 域均 > `2.0e-4`、river 域均 > `2.0e-3`，超限抛 `ValueError` 并带 `.evidence`（裁决 5）；
  - 段/列归属经 `CfgIcDocument` 的段模型判定（mesh 段的 `unsat` 列、river 段的 `stage`/`river_stage` 列），**MUST NOT** 重走 pin 的「按 `current_columns` 边扫边猜」路径——那是 pin 因为没有文档模型才需要的；列索引由段列头文本定位（`current_columns.index("unsat")` 的等价物），MUST NOT 写死索引 4。**列名来源钉死**：`cfg_ic.Section` 无列名字段，4.4 MUST 就地重切 `doc.lines[section.column_header_index]` 取列名（`.split()` 后 `strip().lower()`，与 pin `:197` 一致），**MUST NOT** 为此给 `Section` 加字段——那会越出本 issue 声明的 `cfg_ic.py` 变更面（仅 #54 第 3/4/5 条）。
  - 改动集仅真正含负值的数据行，经 `with_replaced_lines` 产出新文档（裁决 1/2/3）。

**D. #54 并入映射（六条验收标准逐条落地或记为 non-goal）**

| #54 条目 | 本 issue 动作 | 落点 |
|---|---|---|
| 1 非有限值 fail-open | isfinite 门随 `_check_block_range` 移植且次序不变；**并**在 4.4 投影前加门（裁决 4，本 fixture 新增的另一半） | `state_qc.py` |
| 2 river 行数无门 | `expected_river_count` 比对落地 + 一条「物理行内嵌 U+0085」负例 | `state_qc.py` / 测试 |
| 3 分段列头重入 | 采纳 #54 推荐 (a)：同名段列头第二次出现即 `ValueError` | `cfg_ic.py` |
| 4 BOM 误诊 | `parse` 检出首行以 U+FEFF 起头即直说「文件带 UTF-8 BOM」，不再报 `truncated` | `cfg_ic.py` |
| 5 文档无构造期不变量 | `__post_init__` 校验 + `with_replaced_lines`（裁决 3） | `cfg_ic.py` |
| 6 未做项须记 non-goal | 第 1/3/4/5 条全做；第 2 条的**判定与用例**落地、**权威 `reach_count`(3988) 接线**记为 non-goal 路由 #21/#24（`expected_river_count=None` 时该门不生效，是本 issue 明确保留的收窄） | PR 描述 |
| 7（#54 评论 1）`cfg_ic.py:281-284` 的 mesh 列头守卫无用例（#54 评论按 commit 7a14c77 之前的行号写作 `:270-273`）（`if False:` 变异下全套 339 条全绿），明文路由「自然随 #9 落地」 | 补该守卫的用例，复现输入 `b'0 6 27000000.000000\nIndex Stage\n1 0.100000\n'` | `test_cfg_ic.py` |
| 8（#54 评论 2）`test_cfg_ic.py:718-733` 偏离穷尽性测试自指 | 改为 `ast` 计数闭合（见 Change surface） | `test_cfg_ic.py` |

`_as_float` 接受 Python 下划线数字字面量（`1_0` → `10.0`）宽于 C/Fortran 读者：#54 明写「记在第 1 条内作为已知面，不单独立项」，本 issue **不收窄**（收窄即偏离 pin 的词法且无 pin 对应物），在模块头记为已知面。

`_ensure_utc` 的 pin 语义（`state_cli.py:1186-1189`）是逐字的「naive 视为 UTC，aware 转 UTC」：

```python
if value.tzinfo is None:
    return value.replace(tzinfo=UTC)
return value.astimezone(UTC)
```

移植时**不得**改成拒绝 naive——那是无 pin 对应物的收窄；但 MUST 有一条用例把「naive 与等值 aware 产出同一 minute」钉死，另有一条把「非 UTC tz 的等值时刻产出同一 minute」钉死（防止实现漏掉 `astimezone`）。

#### Seams under test

- `state_qc.run_state_variable_qc(source, expected_*) -> StateQCResult`、`state_qc.state_ic_structure_complete(...) -> bool`、`state_qc.normalize_negative_residuals(doc) -> StateResidualNormalization`、`restamp.restamp_to_absolute_time(doc, target) -> CfgIcDocument`——design.md「Sketch seams under test」seam 3 的文件级纯函数边界（parse/restamp/negative-residual/check），无 IO 副作用之外的状态
- `cfg_ic.CfgIcDocument.with_replaced_lines(...)` 与 `__post_init__`——本 issue 新增的文档改写边界，是裁决 1/2/3 的唯一执行点

#### Selected risk packs（项目特有检查）

- **Schema / columns / units / field names**: 状态列语义（mesh 的 `unsat` 列、river 的 `stage`/`river_stage` 列）与 header token 布局（3/4 token）即契约。**两层定位方式不同，不得互相污染**：4.2 的 `_check_block_range` 逐字移植 pin 的**按位置**语义（pin 模块 docstring `state_qc.py:24-25` 明写 "Column semantics are applied by position"，用 `_MESH_STATE_COLUMNS` / `_RIVER_STATE_COLUMNS` 按位置命名）；4.4 的**投影列定位** MUST 由段列头文本查找（pin `:210-222` 的 `current_columns.index("unsat")` 语义），MUST NOT 写死索引 4
- **Error handling / rollback / partial outputs**: 全部拒绝为 fail-closed；「超阈值不产出修正后状态」是可断言的负面证据；解析失败在 QC 层降级为 `passed=False` 而非崩溃（pin 明文语义），两种收敛方式并存且各有用例
- **Legacy compatibility / examples**: pin 的判定语义与常量逐字保留；本 issue 的四条偏离（裁决 1/2/4/5）逐条注明并各有用例
- **Resource limits / large input / discovery**: 有界读经 `cfg_ic.parse` 的 `MAX_STATE_IC_BYTES` 承担；本 issue 不新增读取面，只断言未绕过（QC 入口传 `max_bytes` 到 `parse` 且默认值不变）

Risk packs considered (core):
- Public API / CLI / script entry: not selected - 不接入 CLI；入口经 #21/#24
- Config / project setup: not selected - 不读 `config.toml`/`local.toml`；`expected_*` 由调用方传入
- File IO / path safety / overwrite: not selected - 本 issue **不写任何文件**（裁决 6），读面完全复用 #8 已闭包的 `cfg_ic.parse`
- Schema / columns / units / field names: selected - 见上
- Auth / permissions / secrets: not selected - 无凭据面
- Concurrency / shared state / ordering: not selected - 纯函数，无共享状态；轮询竞态归 #16，发布顺序归 #24
- Resource limits / large input / discovery: selected - 见上
- Legacy compatibility / examples: selected - 见上
- Error handling / rollback / partial outputs: selected - 见上
- Documentation / migration notes: not selected - 无迁移；溯源由模块头注释与快照清单承载

Domain packs (from active profile):
- Geospatial / CRS: not selected - 无几何
- Time series / forcing / temporal boundaries: **selected** - 本 issue 首次解释 header 的时间语义（epoch 分钟 ↔ cycle 绝对时间），T+12 与 init 首态两条定戳路径同源
- 状态链 / warm-start 定戳: **selected** - 重戳正确性即 §8「时间不对应 T 即停」的判定基座
- NWM 快照溯源 / DB-free 隔离: **selected** - 移植函数须带 `NWM@8ae9b8f2 <原路径>` 头；断言零 NWM import、零 DB 符号

#### Invariant Matrix

- Governing invariant: 状态文件穿过 producer 时逐字节不变，**除非**某次操作显式改写指定字段——重戳只改 header 行的 minute token，负残差只改真正含负值的数据行的负值 token；任何其他字节的变化都是缺陷
- Source-of-truth identity/contract: (a) header 的**最后一个数值 token** 是 epoch 分钟（3 token native / 4 token 兼容两种布局，`cfg_ic_header_shape` 是唯一判定权威）；(b) **4.4 的投影列**由段列头文本定位，不由位置常量定位（4.2 的范围检查另按 pin 的位置语义移植，见 Selected risk packs 的 Schema 行——两者不得互相改写）
- Producers: 外部（SHUD 求解器输出 `cfg.ic.update`、率定末态基线包），格式不由本项目控制
- Validators/preflight: `state_qc.run_state_variable_qc` / `state_ic_structure_complete`（本 issue 新增）；`cfg_ic.parse` 的解析级 fail-closed（#8 已落，本 issue 补 #54 第 3/4 条）
- Storage/cache/query: `states/<source>/<T>.cfg.ic`（写入面归 #21/#24，本 issue 无写面）
- Public routes/entrypoints: none - 不接入 CLI
- Frontend/downstream consumers: #16（tracker 轮询用 `state_ic_structure_complete`）、#21（init 首态重戳）、#24（发布前 T+12 定戳与发布顺序）
- Failure paths/rollback/stale state: 重戳/残差的拒绝一律抛 `ValueError` 且不返回文档；QC 层把解析失败降级为 `passed=False`；无写入面故无回滚
- Evidence/audit/readiness: `StateResidualNormalization.evidence()` 的完整载荷（含拒绝路径经异常 `.evidence` 携带）；逐函数 `NWM@8ae9b8f2` 溯源头；未改动行逐字节不变断言
- Regression rows:
  - 干净 mesh+river 文档 + 目标 T -> 只有 header 行变化，minute token == `target.timestamp()/60` 的 `%.6f`，其余全部行逐字节等同原文件
  - **脏**文档（CRLF + 行尾空格 + Tab 分隔 + 段间空行 + 混合记法 + 无末尾换行）+ 目标 T -> 同上；且 header 行内除 minute token 外的字节与行尾符不变（这条是判别力承重条：pin 式 `"\t".join` 与整文件 `"\n".join` 在干净输入上恒绿）
  - 两 token header（`23106\t6`，pin #1197 形状）+ 目标 T -> 抛 `ValueError` 且消息以 `STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID` 起头，**不产出文档**
  - 含少量负残差、域均在阈值内的文档 -> 负值归零，未含负值的行逐字节不变，`evidence()` 各计数为手算值
  - 域均超阈值的文档 -> 抛 `ValueError`，`.evidence['accepted'] is False`，**不产出修正后文档**
  - 任一状态列含 `nan` / `inf` / `-inf` -> 4.2 与 4.4 两个 seam 均拒绝；NaN **不得**被静默归零、`+inf` **不得**原样存活
  - river 段行数 != `expected_river_count` -> QC 失败并报出实际/期望；含 U+0085 内嵌构造的负例
  - **未改动的兄弟消费者**：#8 的 `test_cfg_ic.py` 全套（除本 issue 明确收窄接受域的三条）继续全绿，`render(parse(b)) == b` 不回归

#### Boundary-surface checklist（high 强度必填）

- 共享 helper 根：`state/cfg_ic.py` 是**本 issue 唯一被改的既有共享根**；改动限于 #54 第 3/4/5 条，MUST NOT 顺手改分段识别辅助的判定语义（那会让 #8 的 Must-preserve 失效）
- 公共入口：无（不接入 CLI）
- 读面：`cfg_ic.parse`，本 issue 不新增读路径，MUST 断言 `max_bytes` 一路传到 `parse` 且模块默认值未变
- 写/删/覆写面：无（裁决 6）
- staging/发布/回滚面：无（归 #24）
- 生产者/消费者证据边界：`StateResidualNormalization.evidence()` 是发布 receipt 的输入，字段名 MUST 与 pin 一致
- 陈旧状态/幂等边界：重戳与残差均为纯函数，同输入同输出；MUST 有一条「重戳两次结果相同」的幂等断言
- 未改动的下游消费者：#8 的格式层测试全套；`state/__init__.py` 的既有导出不得改名或改语义

#### Required evidence（每条 input -> expected output）

**重戳（4.3）**
- 干净 mesh+river 文档 + `datetime(2026,1,2,12,0,tzinfo=UTC)` -> 新文档 `render` 后：header 行 minute token == `f"{1767355200/60:.6f}"` 的手算值，其余**每一行**与原 bytes 逐字节相等（逐行比对，不是整文件 hash——整文件比对说不出是哪一行坏了）
- **脏文档**（CRLF 行尾 + header 行多空格分隔 + header 行尾空格 + Tab 分隔的数据行 + 段间空行 + 混合记法 + 无末尾换行）+ 同一目标 -> 同上；**且** header 行内除 minute token 外的字节序列与原 header 行相同、行尾符仍为 `\r\n`。这条是裁决 1/2 的唯一判别力来源
- 两 token header **`3\t6`（配 3 行 mesh，使其能经 `cfg_ic.parse` 构造出文档）** -> `ValueError`，消息以 `STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID` 起头且含 `cfg_ic_header_shape` 的 reason 文字；**断言未产出任何文档**。**MUST NOT 用 pin #1197 的原始 `23106\t6` 字面值**：`cfg_ic.parse` 取 header 首个数值 token 作 `declared_mesh_count` 并强制与实际 mesh 行数相等（`cfg_ic.py:190-192`、`:271-275`），那需要 23106 行 mesh，否则用例会红在 parse 的 `truncated sectioned IC body` 而非 restamp 的形状闸门——即 mutant (g) 的唯一执行点会红在错误原因上
- 5 个及以上数值 token 的未知 header 布局 -> `ValueError`（pin `cfg_ic_header_shape` 对 `>= 5` 显式 fail-closed，且其 docstring 明记「比 runtime injector 更严」——不得照抄 injector 的宽松分支）
- 4 token 兼容 header（`<mesh> <river> <lake> <minute>`）-> 重戳成功，minute 落在**最后一个**数值 token 上（不是第 3 个）
- header 无可解析 minute（数值 token < 2）-> **显式不写用例**：该分支在 shape 闸门之后恒不可达，按 Must add/change B 落成 `# pragma: no cover` 的内部自检。这是**有意的不写**，不是遗漏——两条可写法（手工构造文档、喂 bytes）分别红在 SHAPE_INVALID 与 `unreadable IC header` 上，都不行使本分支
- naive `datetime(2026,1,2,12,0)` 与 `datetime(2026,1,2,12,0,tzinfo=UTC)` -> 产出同一 minute（钉死 `_ensure_utc` 的 naive-as-UTC 分支）
- `datetime(2026,1,2,7,0,tzinfo=timezone(timedelta(hours=-5)))` -> 与上条同一 minute（钉死 `astimezone` 分支；漏掉它的实现在只用 UTC 的用例上恒绿）
- **两条调用路径同源**：以 init 首态语义的 T 与发布语义的 T+12 各调一次**同一个函数**，断言两次只差 `target`；并断言模块内**不存在**第二个重戳入口（源码机检：`state/restamp.py` 的公共符号集不含第二个改 header 的函数）
- 幂等：`restamp(restamp(doc, T), T)` 的 bytes == `restamp(doc, T)` 的 bytes

**负残差（4.4）**
- mesh unsat 列含 3 个 -1e-6、river stage 列含 2 个 -5e-4 的文档（mesh 5 行 / river 4 行）-> 归零成功；`evidence()` 的 `normalized_value_count == 5`、`normalized_unsat_row_count == 3`、`normalized_river_row_count == 2`、`mesh_row_count == 5`、`river_row_count == 4`、`mean_unsat_correction_m == 3e-6/5`、`mean_river_correction_m == 1e-3/4`（**逐值手算断言，不是 `> 0` 或 `isinstance`**）
- **未含负值的行逐字节不变**：上条的 fixture 用脏矩阵构造，断言未改动行（含 header、列头、空行、正值数据行）与原 bytes 逐行相等，且改动行只有负值 token 的字节变化
- **同一行多个负值 token（裁决 2 的排序不变量）**：`replace_tokens` 自右向左替换，使先落的替换不移动后落 token 的 span。该性质在 round 2 前全仓无 oracle——去掉 `reverse=True` 后 777 全绿，而 verifier 实测它会**静默**产出合法数值行：`mesh_row(1, canopy="-1e-9", unsat="-1e-9")` 配 `data_delimiters=("   ","\t","   ")` 下，`'1   -1e-9\t0.100000   0.100000   -1e-9\t0.100000\n'` 被写成 `'1   0\t0.100000   0.100000   -1e-000000\n'`，即 1e-9 m 的残差被发布成 **-1.0 m**、GW 列被吞掉，而 `accepted` 仍为 True、evidence 仍报 `max_correction_m == 1e-9`。故 MUST 两层各一条：(a) 对**公共辅助** `replace_tokens` / `token_spans` 的直接单元用例，≥2 个替换 index、分隔符混合，断言结果 bytes 逐字面量相等（两个调用点当前都只传单键，多键契约在任何层面都未被测）；(b) 脏矩阵里加一行**同行两个负值**的 mesh 行，断言归零后整行 bytes 逐字面量相等。现有 `_assert_only_one_token_spliced` 按构造断言 `len(differing) == 1`，**结构上无法**覆盖本行——MUST 推广成「接受期望改动 index 集合」，且 MUST NOT 因此放松既有单 token 用例的断言强度
- unsat 域均恰好 `2.0e-4` -> **接受**；`2.0e-4 + eps` -> 抛 `ValueError`（钉死 `>` 而非 `>=`，两条边界各一条用例；river 域均在 `2.0e-3` 上同样两条）
- 域均超限 -> `ValueError` 且 `exc.evidence['accepted'] is False`、`exc.evidence['reason']` 含实际域均与上界的 `%.9f` 文本、`exc.evidence['policy'] == "unbounded_physical_zero_projection_v4"`；**断言未产出修正后文档**
- **单格大幅负值、域均仍在阈值内 -> 接受并归零**：river 段 100 行、其中一格 stage 为 `-0.15` -> `mean_river_correction_m == 0.15/100 == 1.5e-3 < 2.0e-3` 故**接受**，`over_tolerance_clamp_count == 1`、`max_correction_m == 0.15`（钉死 pin 模块头逐字记录的「无逐格上限、可用性优先」owner directive；把 `_NEGATIVE_ZERO_TOLERANCE` 误用成投影闸门的实现会在此变红）。**规模必须与幅度配套算过再写**：域均门的分母是 river 行数，pin 记录的 -17 m 反例要 river ≥ 8500 行才落在阈值内（17/8500 恰为 `2.0e-3`，门为 `>` 故 8500 行仍接受），用小规模 fixture 配大幅值会得到**拒绝**，实现者会据此误判 pin 语义并去加逐格上限
- 负值幅度 `9e-3`（< `_NEGATIVE_ZERO_TOLERANCE`）-> 归零且 `over_tolerance_clamp_count == 0`；`1.1e-2` -> 归零且 `over_tolerance_clamp_count == 1`（两条边界钉死 tolerance 只切分证据、不闸投影）。**两条负值 MUST 钉在 canopy 列**（非域均列）：`over_tolerance_clamp_count` 的累加与列无关（pin `state_qc.py:246-247`），故 canopy 列完整服务本行的判别目的，同时避开域均门——放 unsat 列时小规模 fixture（mesh 5 行）的域均 `1.8e-3 > 2.0e-4` 会**拒绝**，得到 `ValueError` 而非本行期望的「归零」（同上一行立的规模配套规则）
- 元素 id 列（列索引 0）为负 -> **不**被归零（pin 的循环自 `range(1, len(row))` 起），且该行字节不变
- `nan` / `inf` / `-inf` -> 抛 `ValueError`（裁决 4）；**三个值各一条用例且列位置逐条钉死**，理由是三者在 pin 上走四条不同路径：`nan` 放 unsat 列（被静默归零、域均为 nan、两门放行 → accepted）、`+inf` 放 unsat 列（`>= 0.0` 早退、原样存活 → accepted）、`-inf` 放 **canopy 列**（非域均列：归零、只计 `max_correction_m` 与 `over_tolerance_clamp_count` → **accepted**）、`-inf` 放 **unsat 列**（域均 inf → pin 唯一拒绝的那条）。前三条是真 fail-open，只测第四条的实现会全绿——**MUST 四条齐备**
- 幂等：`normalize(normalize(doc).document)` 的 bytes 与一次的相同，且第二次 `normalized_value_count == 0`

**结构检查（4.2）**
- 缺 river 段（无 river 列头）+ `expected_river_count=4` -> `passed=False`，`reason` 点名缺失的是 **river 段**（不是 `river row count 0 != expected 4`）
- river 段存在但行数 3 != `expected_river_count=4` -> `passed=False`，`reason` 含实际 3 与期望 4
- **U+0085 内嵌**：一条物理 river 行中间插入 U+0085 使 `splitlines` 断成两行 + `expected_river_count` 为真实行数 -> `passed=False`（#54 第 2 条：这是 river 段唯一的门，字节 roundtrip 抓不到它）
- `expected_*` 全为 `None` -> **行数比对**跳过（pin 语义），结构仍被解析；**段缺席不随之跳过**：
  mesh + river 双段齐全（无 lake）时 `passed=True`（lake 段本就可选），而缺 river 段时即使
  `expected_*` 全为 `None` 也 -> `passed=False` 且 reason 点名 river 段、`state_ic_structure_complete`
  返回 `False`。理由：spec state-tools 的第一条 Requirement 独立要求「至少包含 mesh 状态段与
  river `Stage` 段」，`结构检查` 的「缺 river 段被拒」Scenario 也不带前置条件；把段缺席挂在调用方
  计数上，等于让 #16 tracker 的默认调用（全 `None`）把只写到一半、river 段还没落盘的 checkpoint
  判成「完整」——正是 `state_ic_structure_complete` docstring 自己点名要防的那一幕
- 任一状态列 `nan` -> `passed=False` 且 reason 指明**非有限**；**并**断言同一行若改为负值则 reason 指明**负值**——两条一起才钉死「finiteness 先于负值」的次序（#54 明文要求；只测 NaN 被拒不区分是哪道门拦的）
- **次序的精确语义是「行内逐列单遍」而非「整块两遍」**（pin `state_qc.py:826-839`：同一列先 isfinite 再负值再上界）。故 MUST 另有一条：同一行**前列为负值、后列为 NaN** -> reason 报**负值**（两遍式实现——先扫全块非有限、再扫全块负值——会在此报非有限而变红）
- 状态值 > `_MAX_STATE_VALUE_M`（`1.0e6`）-> `passed=False` 指明超界；恰好 `1.0e6` -> 通过（钉死 `>` 而非 `>=`）
- 负值 `-9e-3`（幅度 < `_NEGATIVE_ZERO_TOLERANCE`）-> **通过**；`-1.1e-2` -> 拒绝（pin `:832` 的 `value < -_NEGATIVE_ZERO_TOLERANCE`，两条边界）
- 行短于 `1 + len(columns)` -> `passed=False` 指明缺列（pin `:822-824`）
- 解析失败的输入（空文件 / 非 UTF-8 / 截断 body）-> `run_state_variable_qc` 返回 `passed=False` 且 `reason` 以 `IC parse failed: ` 起头、`checks['parsed'] is False`，**MUST NOT 抛异常**（pin 明文「parse failure is itself a QC failure, never a crash」）
- 同一批输入喂 `state_ic_structure_complete` -> 返回 `False`，同样不抛
- `StateQCResult.to_dict()` 的键集与 pin 逐字一致（`passed` / `checks` / `reason`），`checks` 含 `ic_path` / `parsed` / `row_counts` / `range` 四键

**#54 第 3/4/5 条（格式层，落 `test_cfg_ic.py`）**
- river 段之后再次出现 mesh 列头 -> `parse` 抛 `ValueError` 点名段重入（#54 推荐 (a)）；**并**断言 #8 既有的合法三段用例不受影响
- 首行带 UTF-8 BOM 的合法文件 -> `ValueError` 消息含「UTF-8 BOM」字样，**MUST NOT** 再报 `truncated sectioned IC body`（#54 实测该误诊会把运维支到错误方向）
- BOM + `header 6 6 0 0.0` + 6 行 mesh（#54 实测的静默误解析巧合）-> 同样报 BOM，不再静默通过
- `CfgIcDocument(...)` 以 `len(roles) != len(lines)` 构造 -> `__post_init__` 抛 `ValueError`；`header_index` 越界、段行号越界各一条
- `with_replaced_lines({i: "..."})` 替换一行 -> 行尾符沿用原行；替换值内含 `\n` 或行号越界 -> `ValueError`
- `dataclasses.replace(doc, lines=<长度不同的元组>)` -> `__post_init__` 立即抛 `ValueError`（#54 第 5 条实测的「静默产出看起来正常的 bytes」路径变红）

**预登记必须被杀死的变异体**
- (a) 重戳改用 pin 式 `"\t".join(header)` 重拼 header 行 -> 脏 header 用例必须变红
- (b) 重戳改用整文件 `"\n".join(lines)` 重拼 -> CRLF 脏用例必须变红
- (c) 负残差把 unsat 域均分母误写成 river 行数（或反之）-> 逐值手算证据用例必须变红
- (d) 两条域均门的 `>` 改 `>=` -> 恰好等于阈值的用例必须变红（四条边界各覆盖一次）
- (e) 删掉 4.4 的 finiteness 门 -> NaN 用例必须变红（这是裁决 4 的执行证明）
- (f) `_check_block_range` 里把 isfinite 门挪到负值门**之后** -> 「NaN 报非有限而非报负值」的用例必须变红
- (g) 重戳略过 `cfg_ic_header_shape` 直接覆写 -> 两 token 用例必须变红
- (h) 结构检查的段缺席分支退回 pin 的行数消息 -> 「指明缺失段」用例必须变红
- (i) `with_replaced_lines` 丢弃原行尾符改贴 `\n` -> CRLF 用例必须变红
- (j) `cfg_ic.py:281-284` 的 mesh 列头守卫体改为 `if False:` -> #54 评论 1 的新用例必须变红（#8 实测该变异在全套 339 条下存活）
- (k) 模块头偏离清单由八条改回六条 -> 改造后的 `ast` 计数测试必须变红

**变异证明 MUST 按 `openspec/project-profile.md` 的「Mutation-testing hazards」执行**（本仓已实测绊倒多个独立 agent，四种假绿都长得像好消息）：`rsync --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache'` 且副本内 `rm -rf .venv && uv sync`；先断言 `yd_producer.__file__` 落在 scratch 副本内；每个变异体之间 `export PYTHONDONTWRITEBYTECODE=1` 并清 `__pycache__`（(a)/(c)/(d) 极易出等长字面量改动，会复用上一个变异体的 `.pyc`，报出来的是上一个变异体的结果且自洽）；scratch 目录名含 `issue-9` 唯一标识；另跑一个必然变红的控制变异做校准，控制变异若意外全绿要如实说并换一个（profile 记录的近等价变异体反例：`_require` 改 `return None` 在两种规模下都全绿）。

**裁决 6 / 裁决 7 的机检闭合（缺之即两条裁决无执行子句）**
- `state_qc.py` / `restamp.py` 源码内**无写文件调用**：`ast` 扫描无 `open(..., "w"/"wb"/"a"/"x")`、无 `Path.write_bytes` / `write_text` / `os.replace` / `atomic_write*` 调用（裁决 6）
- 两模块的模块级定义名字集**不含** rekey 面七符号（`StateCheckpoint`、`StateRunContext`、`_checkpoint_header_minute`、`_valid_time_from_header_minute`、`_checkpoint_with_header_time`、`_lead_hours_from_run_valid_time`、`STATE_CHECKPOINT_IC_HEADER_SHAPE_REKEY_SKIPPED`）与 `water_balance`（裁决 7 + `_check_water_balance` non-goal），复用下方八符号机检的同一 `ast` 名字集手法

**溯源与隔离**
- `state/state_qc.py` 与 `state/restamp.py` 含 `NWM@8ae9b8f2 packages/common/state_qc.py` / `packages/common/state_cli.py` 模块头；每个移植函数带**自己**的溯源注释，取窗 MUST 按函数边界（切到下一个 `\ndef ` 或用 `ast` 取函数源码段——#8 实测定长窗口会越进下一个函数，6 个辅助里 4 个可被邻居的注释满足），并对每个移植函数验证「删掉自己那行即变红」
- 两模块源码内无 NWM 包 import、无数据库/scheduler 符号
- **DB-free 扫描器的行模型（本 issue 改了 #5 的共享守卫，故由本 issue 取证）**：`snapshot_provenance_fixtures._code_lines` 用 tokenize 抹掉注释与语句位字符串，其行数组 MUST 与 tokenize 的行号同源。`str.splitlines()` 会在 `\x0b \x0c \x1c \x1d \x1e \x85 \u2028 \u2029` 上断行，而 tokenize 只认 `\n`——round-2 verifier 实测：`\x0b \x1c \x1d \x1e` 在字符串外触发 `TokenError` 走回退（fail-closed 无害），但 `\x0c \x85 \u2028 \u2029` 能正常 tokenize 并使两者错位，于是**真代码行被当 docstring 抹掉**。取证 MUST 断言**命中**而非行数（只断言 `len(_code_lines(src)) == n` 在坏实现上恒绿，因为 `splitlines()` 对它自己那套行定义是自洽的）：`'\x0c\ndef f():\n    URL = os.getenv("DATABASE_URL")\n    """note: DB-free, honest"""\n    return URL\n'` -> 扫描仍报出 `DATABASE_URL` 命中，且行号与解释器行号一致。修法是 `text.split("\n")`；**MUST NOT** 改用「把 `\x0c`/`\x85` 加进跳过表」或「先归一化源码」——归一化会移动字节偏移，把 `_blank_prose` 的**列**算术也弄错位
- **扫描器的 tokenize 失败回退**（同一函数 docstring 明写「守卫宁可误报，不可漏报」）：`except (TokenError, SyntaxError)` 分支返回裸行数组这一承诺 MUST 有用例钉住——改成 `return []` 后 777 全绿即为未钉。一条不可 tokenize 的源（如未闭合字符串）内含禁区面 -> 仍报出命中
- `source_probe.module_docstring_block` 有同源错位，但方向是 fail-**closed**（`splitlines()` 只会比 `\n` 行数多，`[: end_lineno]` 只会**少**切，于是 `head` 变短、`body` 变长，断言只会更严、更响）。修它可选且优先级低于上两条；若修，MUST 保住「返回值是 `source` 的前缀」这一契约（调用方按 `source[len(block):]` 取 body），即 `"\n".join(source.split("\n")[: node.end_lineno]) + "\n"`，并补一条 docstring 结束于 EOF 且无末尾换行的用例
- 源码机检：`state_qc.py` / `restamp.py` **不重新定义** `_looks_like_column_header` / `_section_from_column_header` / `_native_lake_section_preamble` / `_header_counts` / `_as_float` / `_numeric_row` / `_read_bytes_limited` / `MAX_STATE_IC_BYTES`，而是从 `cfg_ic` 导入（清单 `:44` 的双权威副本禁令，用 `ast` 断言模块级定义名字集不含这八个符号）

**验证命令**
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check . && uv run ruff format --check .` -> 退出码 0
- `cd producer && uv sync --frozen` -> 退出码 0（不得新增依赖）
- `openspec validate m2-producer-core --strict --no-interactive` -> 退出码 0
- `bash scripts/check-stage-pipeline-log.sh origin/master` -> 退出码 0

#### Non-goals

- **rekey 面**（裁决 7）：`_checkpoint_with_header_time`(`state_cli.py:305`)、`_checkpoint_header_minute`(`:327`)、`_valid_time_from_header_minute`(`:359`)、`_lead_hours_from_run_valid_time`(`:1149`)、`StateCheckpoint`(`:62`)、`StateRunContext`(`:50`)、`STATE_CHECKPOINT_IC_HEADER_SHAPE_REKEY_SKIPPED`(`:87`)、`LOGGER`(`:46`) 一律不落——本 issue 无调用方，落地即死代码。路由：tracker 侧 **#16**，发布侧 **#24**。清单 `:45` 与 `:55` 同步改注
- **`producer/tests/test_cfg_ic_header.py`**（清单 `:56`）：其引用的三个符号全部出自 `runtime.py` 抽取集（capability 6），缺任一即不可导入；随 **#16/#17** 落地
- **`_check_water_balance`（pin `state_qc.py:843`）与 `water_balance` 形参**：pin 自标 Lane 2 TODO、恒 `skipped`；数值正确性在本项目显式归 M4。落地即死参数
- **`MAX_STATE_CHECKPOINT_MANIFEST_BYTES`**：清单 `:45` 已把它移出抽取集（唯一使用点在已剥离的 `_read_state_checkpoint_manifest_payload` 体内）
- **`_read_limited_text_no_follow`(`:978`) / `_read_limited_bytes_no_follow`(`:966`)**：闭包切点。二者委派 `safe_fs`（**issue #5 在途，本仓未落地**），而 `cfg_ic.parse` 已提供有界读与 `MAX_STATE_IC_BYTES`。切点 MUST 在 `restamp.py` 模块头注明；清单 `:45` 同步记该切点
- **落盘与发布顺序**：裁决 6，归 #21/#24
- **把权威 `reach_count`（3988）接进 QC 调用**：`expected_*` 由调用方传入是 pin 约定；接线归 #21/#24。本 issue 只落判定与用例
- **`_as_float` 的 Python 数字词法宽于 C**（`1_0` → `10.0`）：#54 明文「记在第 1 条内作为已知面，不单独立项」，模块头记录，不收窄
- **兼容的计数式布局**：#8 已 fail-closed，本 issue 不改该裁决；率定末态基线包的真实格式核实仍路由 **#32**（触发点 #11）
- 不接入 CLI、不做数值正确性判定（M4）

#### Review focus

- **裁决 1/2 是否真落地**：重戳与残差是否只重写改动行、改动行是否只替换目标 token 的字节。任何 `"\n".join` / `"\t".join` 整行整文件重拼路径都是缺陷——它在干净输入上恒绿，只在脏矩阵上变红
- **裁决 4 是否两处都做**：`_check_block_range` 的 isfinite 门（含次序）**与** 4.4 投影前的门，缺任一则 NaN 仍被静默归零并接受
- 域均分母是否分别为 mesh 行数与 river 行数；`_NEGATIVE_ZERO_TOLERANCE` 是否被误用成投影闸门（pin 明确它只切分证据）
- 是否从 `cfg_ic` 导入八个基座符号而非重新移植（双权威副本禁令）
- `cfg_ic.py` 的改动是否严格限于 #54 第 3/4/5 条，有无顺手改动分段识别辅助的判定语义（那会让 #8 的 Must-preserve 失效）
- 是否越界落地了 rekey 面 / 落盘面 / `water_balance` 的死代码
- 逐值手算证据是否真的逐值（#8 实测：只断 `len()` 与 `isinstance(float)` 时，把三处 `append` 换成全零元组的变异体在全套 46 条下存活）

## 5. 执行器抽象：JobExecutor 协议与 fake

- [x] 5.1 定义 `JobExecutor` 协议（submit/poll、job ID/partition/终态/起止时间语义）与进程内 fake（成功/失败/超时可编排），接口契约测试
- [x] 5.2 实现 Slurm 生产执行器（`sbatch`/`sacct` 封装，参数全部装配自 `local.toml`、零内置默认）；本阶段不做行为测试（M4 oracle），本地判据 = 参数装配纯函数检查 + 协议一致性

依赖：组 1（Slurm 字段结构）
§13.1 归属：控制器（支撑）
Suggested fixture level: compact - 进程内接口契约与参数装配测试，无文件 fixture
Minimal mergeable slice: 协议与 fake（5.1）——接口层独立合并保绿，生产执行器为后继

Fixture level: expanded
Project profile: yd-viewer
Repair intensity: medium
Upstream suggested level: compact（override: 命中核心强制 expanded 触发词 `public API` 与 `schema`/`field` —— 协议是控制器 #26–#28 与 Slurm 生产执行器 #11 共用的公共契约，`JobRecord` 的字段名/类型/时区语义即下游运行报告与 receipt 的 schema；组 1（#2）在「无既有消费者但为跨 issue 共享契约」这一同构情形下已 override 为 expanded，本组保持 compact 与该先例冲突。产物集无需新增文件：design.md D3 与 specs/run-controller delta 均已存在；repair intensity = medium，不要求 Invariant Matrix）
Change surface:
- 新增 `producer/src/yd_producer/executor.py`：`JobState`、`JobSpec`、`JobRecord`、`JobExecutor` 协议、`ExecutorError`、进程内 `FakeJobExecutor`、`FakeOutcome`、确定性 `StepClock`
- 新增 `producer/tests/test_executor.py`
- 不改动 `config.py` / `geometry.py` / `pyproject.toml`

Must preserve:
- `yd_producer.config`、`yd_producer.geometry` 的行为与既有测试全绿
- producer 依赖面不变：本 issue 只用 stdlib（`dataclasses`/`enum`/`datetime`/`typing`/`pathlib`），`uv sync --frozen` 无 drift

Must add/change（协议语义逐条钉死，实现方消费不重议）:

- `JobState` 枚举：`PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`TIMEOUT`；`is_terminal` 属性对后三者为真。`TIMEOUT` MUST 与 `FAILED` 分立——walltime 超时在 `sacct` 是独立终态，运维据此判断"加 walltime"还是"查作业本身"，合并即销毁该判据（issue 验收标准的"三态"即 SUCCEEDED/FAILED/TIMEOUT）。
- `JobSpec`：frozen、`kw_only`、**全字段无默认值**的 dataclass，字段为 `name: str`、`work_dir: Path`、`command: tuple[str, ...]`、`log_path: Path`、`resources: Mapping[str, str | int]`。
- `resources` MUST 是不透明映射，协议层 MUST NOT 出现 `partition`/`account`/`cpus`/`memory`/`walltime` 任何固定字段或字段清单——键集的唯一权威是 `Config.slurm.required_fields`（组 1 fixture 已钉死），协议里再写一遍即第二权威。
- `JobExecutor`：`typing.Protocol` 且 `@runtime_checkable`，两个方法 `submit(spec: JobSpec) -> JobRecord` 与 `poll(job_id: str) -> JobRecord`。
- `JobRecord`：frozen、`kw_only`、**全字段无默认值**的 dataclass，字段为 `job_id: str`、`name: str`、`state: JobState`、`resources: Mapping[str, str | int]`、`submitted_at: datetime`、`started_at: datetime | None`、`ended_at: datetime | None`。
- job 身份四元组的承载方式：job ID = `job_id`；partition = `resources["partition"]`（记录携带**提交时的整份 resources**，调用方按 `required_fields` 的键取值，故 partition 可入运行报告而协议不写死键名）；终态 = `state`；起止时间 = `started_at`/`ended_at`。
- 时间语义：全部 `datetime` MUST tz-aware 且 UTC 偏移为 0；naive datetime 进入即抛 `ExecutorError`。理由：receipt 与 cycle 绝对时间比较，naive 值会静默错位而非报错。
- 记录不变式（MUST，逐条有测试）：非终态 `ended_at is None`；终态 `ended_at is not None`；`started_at is not None` 时 `submitted_at <= started_at`；`started_at` 与 `ended_at` 均非 None 时 `started_at <= ended_at`（两条分列而非串成三元比较——`RUNNING` 记录的 `ended_at` 为 None，串写会在 datetime 与 None 之间比较抛 `TypeError`）；`ended_at` 非 None 时 `submitted_at <= ended_at`（**无条件**，不以 `started_at` 为闸——两条 `started_at` 锚定的比较在未起跑终态上都不生效，那正是 fixture 允许 `started_at is None` 的那条路径，缺这条则负墙钟时长可构造并直达运行报告；`started_at` 存在时它由上下两条推出，故无条件添加安全）；`started_at is None` 的终态只允许 `FAILED`/`TIMEOUT`（作业未启动即终止）。
- 打戳时机（确定性可断言，MUST）：`submit` 取一次时钟写 `submitted_at`；使 `started_at` 由 None 变为非 None 的推进各取一次时钟；使记录进入终态的推进各取一次时钟；不改变状态的 `poll` MUST NOT 取时钟。因此 `polls_until_terminal=0` 且 `started=True` 时，单次 `poll` 内两次推进各取一次时钟，得到 `started_at < ended_at`（严格递增，非同一时刻）；`started=False` 的终态只取终态那一次。
- `job_id` 分配（MUST）：`submit` 为每次**成功**提交分配唯一、确定性、不复用的 `job_id`（按提交序单调生成即可）。`JobSpec.name` 是 fake 的编排键，`job_id` 是 `poll` 的索引键，二者不等同：同一 `name` 允许重复提交（控制器重试路径），每次 MUST 得到不同 `job_id`，且各自的记录互不串台。
- `resources` 提交时快照（MUST）：`JobSpec` 与 `JobRecord` 在构造时把入参 `resources` **复制**为不可变映射（`MappingProxyType(dict(...))` 一类），调用方事后修改原 dict MUST NOT 影响已构造对象。注意：不可变映射不可哈希，故这两个 dataclass 的实例 `hash()` 会抛 `TypeError` —— 本 issue 明确不提供哈希语义（需要作 dict 键时用 `job_id`），但相等性（`__eq__`）必须可用。这条是承 issue #31 记录的事实，不是重新裁决。
- `poll` 语义：未知 `job_id` MUST 抛 `ExecutorError`（不返回 `None`、不静默造记录）；已达终态的 job 再次 `poll` MUST 返回同一记录且 MUST NOT 迁回非终态。
- `ExecutorError`：本模块唯一公开异常，MUST 暴露结构化属性 `job_id: str | None`（涉及具体作业的失败必须置为该 id）。测试断言以该属性为准，MUST NOT 仅用子串探测（承组 1 `ConfigError.path` 先例）。
- `FakeJobExecutor(outcomes: Mapping[str, FakeOutcome], clock: Callable[[], datetime])`：编排按 `JobSpec.name` 索引；**未被编排的 name MUST 抛 `ExecutorError`**（零内置默认，禁止"默认成功"）；`clock` 必填且无默认，禁止读挂钟——模块提供 `StepClock(start: datetime, step: timedelta)` 供注入，每次调用推进固定步长。
- `FakeOutcome`：frozen、`kw_only`、无默认值，字段为 `final_state: JobState`、`polls_until_terminal: int`、`started: bool`。`final_state` 非终态 MUST 抛 `ExecutorError`；`polls_until_terminal < 0` MUST 抛 `ExecutorError`；`started is False` 且 `final_state is SUCCEEDED` MUST 抛 `ExecutorError`。语义：前 `polls_until_terminal` 次 `poll` 返回 `RUNNING`（`started` 为假时返回 `PENDING`），其后返回终态。
- 在途观测：`FakeJobExecutor` MUST 暴露 `submissions: tuple[JobRecord, ...]`（提交序，用于断言"逐轮串行"）、`inflight() -> tuple[str, ...]`（当前非终态 job id）、`max_inflight: int`（历史峰值）。这三项直接服务 `specs/run-controller/spec.md` 的"每源至多一个作业"Scenario，是 fake 的对外契约而非测试内部细节。

Seams under test:
- `FakeJobExecutor.submit/poll`（进程内、注入 `StepClock`）——协议契约唯一可行使的公共边界（design.md「Sketch seams under test」的支撑层；控制器端到端归组 14）
- `JobState.is_terminal`、`JobSpec`/`JobRecord`/`FakeOutcome` 的 dataclass 结构（以 `dataclasses.fields()` 断言零默认值）

Risk packs considered (core):
- Public API / CLI / script entry: selected - 协议是控制器（#26–#28）与 Slurm 生产执行器（#11）共用的公共契约，方法签名与记录字段即接口
- Config / project setup: not selected - 本 issue 不读 TOML；资源参数以不透明映射入参，装配自 `local.toml` 归 #11
- File IO / path safety / overwrite: not selected - `work_dir`/`log_path` 只作为值传递，进程内 fake 不打开、不创建、不删除任何文件
- Schema / columns / units / field names: selected - `JobRecord` 字段名/类型/时区语义即下游运行报告与 receipt 的 schema，错配传导至 #11、#24、#26
- Auth / permissions / secrets: not selected - 无凭据、无权限判定；`resources` 为 partition/account 一类调度参数
- Concurrency / shared state / ordering: selected - submit→poll 的状态推进顺序与"在途 ≤1"观测由 fake 承载；实现进程内单线程、无共享状态、无真实并发
- Resource limits / large input / discovery: not selected - 无发现逻辑、无大输入；轮询次数由 `polls_until_terminal` 有界编排
- Legacy compatibility / examples: not selected - 首次引入 executor 模块，仓库内零既有消费者
- Error handling / rollback / partial outputs: selected - fail-closed 是核心验收项：未编排 name、未知 job_id、非法 `FakeOutcome`、naive datetime 全部收敛到 `ExecutorError`，不返回半成品记录
- Release / packaging / dependency compatibility: selected - 只用 stdlib，不得新增依赖，`uv sync --frozen` 无 drift
- Documentation / migration notes: not selected - 无迁移；协议语义由本 fixture 块钉死，下游 issue 直接读

Domain packs (from active profile):
- Geospatial / CRS: not selected - 无几何
- Time series / forcing / temporal boundaries: selected - 起止时间的 tz-aware UTC 约束与 `submitted_at <= started_at <= ended_at` 序关系在此定型
- 状态链 / warm-start 定戳: not selected - 本 issue 不读写 `cfg.ic`
- NWM 快照溯源 / DB-free 隔离: not selected - 全新代码，非快照，无 NWM import

Required evidence（每条 input -> expected output）:
- `FakeJobExecutor` 实例 -> `isinstance(fake, JobExecutor)` 为真（`runtime_checkable` 只校验方法存在，故另有一条按签名逐参数比对 `JobExecutor.submit/poll` 与 fake 同名方法的 `inspect.signature` 一致性的用例）
- 编排 `FakeOutcome(final_state=SUCCEEDED, polls_until_terminal=2, started=True)` -> `submit` 返回 `state is PENDING` 且 `started_at is None`、`ended_at is None`；第 1、2 次 `poll` 返回 `RUNNING`；第 3 次返回 `SUCCEEDED` 且 `started_at`/`ended_at` 均非 None
- 同上但 `final_state=FAILED` / `final_state=TIMEOUT` -> 三态各自可达且 `state` 精确等于编排值（`TIMEOUT` MUST NOT 被折叠成 `FAILED`）
- `started=True` 的在途记录 -> 首次返回 `RUNNING` 的记录 `started_at is not None` 且 `ended_at is None`；其后不改变状态的 `poll` 返回的 `started_at` 与该值精确相等（未再取时钟）
- `polls_until_terminal=0` 且 `started=True` -> 首次 `poll` 即返回终态，且 `started_at < ended_at`（两次推进各取一次 `StepClock`，按 `start`/`step` 断言精确值）
- `FakeOutcome(final_state=FAILED, started=False, polls_until_terminal=1)` -> 轮询期间 `state is PENDING`，终态记录 `started_at is None`、`ended_at is not None`
- 终态后再 `poll` 两次 -> 三次返回值相等（记录幂等），`state.is_terminal` 恒真
- `poll("nosuch")` -> 抛 `ExecutorError` 且 `exc.job_id == "nosuch"`
- 提交未编排的 `JobSpec.name` -> 抛 `ExecutorError`，`exc.job_id is None`，且 `fake.submissions` 长度不变（失败提交不入账）
- `FakeOutcome(final_state=RUNNING, ...)`、`polls_until_terminal=-1`、`started=False` 且 `final_state=SUCCEEDED` -> 三者各抛 `ExecutorError`
- `StepClock(start=naive_datetime, ...)` 或以 naive `start` 构造 -> 抛 `ExecutorError`；由 `StepClock` 产出的每个记录时间戳断言 `tzinfo is not None and utcoffset() == timedelta(0)`
- **构造点强制（直接构造 `JobRecord`，不经 fake 路径——fake 的正常路径永远产生不出这些组合，只测 fake 等于该 MUST 无证据）**，每条 -> 抛 `ExecutorError`：① 非终态 `state` 且 `ended_at` 非 None；② 终态 `state` 且 `ended_at is None`；③ `started_at < submitted_at`；④ `started_at`/`ended_at` 均非 None 且 `ended_at < started_at`；⑤ `state=SUCCEEDED` 且 `started_at is None`；⑥ `submitted_at`/`started_at`/`ended_at` 任一为 naive `datetime`（与 `StepClock` 那条不重复：naive 拒绝是**记录层**的 MUST，时钟层只是其一个入口）
- 时间序关系：任一终态记录 -> `submitted_at <= started_at <= ended_at`（`started_at` 非 None 时），且两次提交的 `submitted_at` 严格递增（`StepClock` 确定性可断言精确值，不得用"约等于现在"）
- `resources={"partition": "cpu", "account": "a", "cpus": 8, "memory": "32G", "walltime": "04:00:00"}` 提交 -> `record.resources` 与入参逐键相等，`record.resources["partition"] == "cpu"`（partition 可入运行报告）；提交后修改该入参 dict（增删改任一键）-> `spec.resources` 与 `record.resources` 均不变
- 同一 `JobSpec.name` 连续提交两次（编排允许）-> 两次 `job_id` 不同，分别 `poll` 各自独立推进，互不影响；`fake.submissions` 含两条记录
- `hash(record)` -> 抛 `TypeError`（不可变映射不可哈希，本 issue 不提供哈希语义）；两个字段逐项相同的 `JobRecord` -> `==` 为真
- 源码机检：`producer/src/yd_producer/executor.py` 文本中 MUST NOT 出现 `partition`/`account`/`cpus`/`memory`/`walltime` 任一字面量（第二权威守卫；测试直接读该文件断言）
- 零默认值断言：`JobSpec`/`JobRecord`/`FakeOutcome` 的每个 `dataclasses.fields()` 项，`default` 与 `default_factory` 均为 `MISSING`；且三者均为 `frozen=True, kw_only=True`（`kw_only` 堵位置构造，承组 1 先例）
- 两源交替提交（`ifs-T`、`gfs-T`）并各自跑到终态 -> `fake.max_inflight == 2`；单源逐轮串行（`ifs-T` 跑完再提 `ifs-T+12`）-> 过程中 `len(fake.inflight()) <= 1`，`fake.submissions` 的 name 序等于提交序
- 全部失败路径以 `pytest.raises(ExecutorError)` 表达，MUST NOT 用 `pytest.raises(Exception)`
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check .` 与 `uv run ruff format --check .` -> 退出码 0
- `cd producer && uv sync --frozen` -> 退出码 0

Non-goals:
- Slurm 生产执行器（`sbatch`/`sacct` 封装、`local.toml` 参数装配）——任务 5.2，issue #11
- 控制器对本协议的消费（前沿、双源并行、失败隔离、flock）——组 12–14，issue #22–#28
- 真实作业提交/查询行为与 `sacct` 状态码映射 —— M4 oracle（design.md D3）
- 作业取消 / watchdog —— compute-loop §10 明确不做
- `resources` 键集与取值的语义校验（partition 是否存在、walltime 格式）—— 归 #11 的参数装配与 #2 装载器，协议层只做不透明透传
- 日志文件的实际写入与合并（`logs/<source>/<T>.log`）—— 归发布器 #24；本 issue 只传递 `log_path` 值

Review focus:
- `resources` 是否真的不透明：源码内是否出现任何 Slurm 字段名字面量或固定字段清单（双权威即实现缺陷）
- 记录不变式是否在**构造点**强制（非终态带 `ended_at`、终态缺 `ended_at`、naive datetime 是否都不可构造），而非仅靠 fake 的调用路径碰巧不产生
- 三态是否真正可区分：`TIMEOUT` 是否在任何路径上被折叠为 `FAILED`
- 是否存在任何隐式默认：`clock` 是否可省、未编排 name 是否落到"默认成功"、dataclass 是否带默认值
- 失败路径是否全部收敛到 `ExecutorError`，且 `job_id` 属性可机检定位
- 时间断言是否用注入时钟的确定性精确值，而非挂钟近似

### Issue #11 fixture（任务 5.2）

Fixture level: expanded
Upstream suggested level: compact（override：改动面正面命中强制 expanded 触发词 `public API`（本 issue 是 `JobExecutor` 的生产实现，#26–#28 直接消费）与 `parser`/`format`/`schema`（`sacct` 输出解析、五项资源字段→`sbatch` flag 的翻译表）。#2 与 #10 已在同构情形——"无既有消费者但为跨 issue 共享契约"——override 为 expanded，本组保持 compact 与该先例冲突）
Repair intensity: medium（无写入面：本模块不打开、不创建、不删除任何文件，`log_path` 只作为 `sbatch --output/--error` 的实参传递；故 `Invariant Matrix` 不适用，与 #10 同判据）
Project profile: yd-viewer

**模块归属裁决（先读这条，它决定文件落点）**：issue 正文写"Module / Scope: producer 包 `yd_producer.executor`（Slurm 实现）"，但 Slurm 生产实现 **MUST NOT 写进 `producer/src/yd_producer/executor.py`**。理由是硬的：#10 fixture 把"源码机检"钉成了验收项，`producer/tests/test_executor.py:518-520` 逐字断言 `executor.py` 文本中不出现 `partition`/`account`/`cpus`/`memory`/`walltime` 任一字面量；而本 issue 的验收标准"装配产物含全部五项资源参数"必然要求这些字段名出现在装配层——`sbatch` flag 无法从键名推导（`cpus`→`--cpus-per-task`、`memory`→`--mem`、`walltime`→`--time`）。把实现塞进 `executor.py` 只有两条出路：删掉那条守卫测试（oracle 完整性违规，Phase 8 硬闸拦截），或让装配拿不到字段名（做不出验收）。故本 issue 落**同包新模块** `producer/src/yd_producer/slurm.py`：`executor.py` 是**不透明协议层**，`slurm.py` 是 #10 fixture 所称"调用方按 `required_fields` 的键取值"里的那个**受权解释点**。issue 的 "PR Boundary: executor 模块扩展" 按包义解读（`yd_producer` 的执行器面扩展），不按单文件解读。

Change surface:
- 新增 `producer/src/yd_producer/slurm.py`：`SBATCH_FLAGS`、`build_sbatch_command`、`parse_sbatch_job_id`、`build_sacct_command`、`SACCT_ENV`、`parse_sacct_record`、`SLURM_STATE_MAP`、`SlurmJobExecutor`、`subprocess_runner`
- 新增 `producer/tests/test_slurm.py`
- 不改动 `executor.py`、`config.py`、`cli.py`、`geometry.py`、`nwm.py`、`pyproject.toml`、`uv.lock`

Must preserve:
- `producer/src/yd_producer/executor.py` **零改动**，`producer/tests/test_executor.py` 全部用例继续通过——含 `:518-520` 的字面量守卫（本 issue 不放宽、不改写、不删除该守卫；它守的是协议层，不是本模块）
- `yd_producer.config` / `cli` / `geometry` / `nwm` 行为不变，既有测试全绿
- producer 依赖面不变：本 issue 只用 stdlib（`dataclasses`/`datetime`/`os`/`shlex`/`subprocess`/`typing`），`uv sync --frozen` 无 drift

Must add/change（逐条钉死，实现方消费不重议）:

**A. 字段翻译表与键集权威的分工**

- `SBATCH_FLAGS: Mapping[str, str]` 是本模块的**翻译表**：把 `local.toml` 的资源字段名映射为 `sbatch` 长选项名，至少含 `partition`→`--partition`、`account`→`--account`、`cpus`→`--cpus-per-task`、`memory`→`--mem`、`walltime`→`--time`。它是**翻译**权威，不是**键集**权威。
- `build_sbatch_command(spec: JobSpec, *, required_fields: Sequence[str]) -> tuple[str, ...]`：键集权威仍是 `Config.slurm.required_fields`，由调用方逐次传入，**MUST NOT** 在本模块内写死一份"必需五项"的清单或从 `SBATCH_FLAGS` 的键集反推必需性。判定：
  - `spec.resources` 的键集与 `required_fields` 不完全相等（缺项或多余项）-> 抛 `ExecutorError`，消息含首个缺/多的键名（承 `load_local` 的键集相等语义；缺项即验收标准的"缺任一 Slurm 字段时装配报错"）；
  - `required_fields` 里出现 `SBATCH_FLAGS` 没有对应 flag 的字段名 -> 抛 `ExecutorError` 并指名该字段（**MUST NOT** 静默丢弃：静默丢弃等于把一个现场配置的资源约束扔掉后照常提交）；
  - `SBATCH_FLAGS` 里有 `required_fields` 未声明的条目 -> **不报错**（config 是键集权威，翻译表允许有富余条目）。
- **零内置默认**：本模块 MUST NOT 为任何资源参数提供 fallback 取值；`build_sbatch_command` 的 `required_fields` 与 `SlurmJobExecutor` 的 `clock`/`runner` 一律无默认值。

**B. `sbatch` 命令装配（纯函数，确定性可断言）**

产物为 `tuple[str, ...]`，顺序 MUST 固定为：

1. `"sbatch"`、`"--parsable"`
2. `"--job-name", spec.name`
3. `"--chdir", str(spec.work_dir)`
4. `"--output", str(spec.log_path)`、`"--error", str(spec.log_path)`——**同一个路径**，使 stdout 与 stderr 落进同一份文件；两个 flag **都显式给出**，MUST NOT 依赖"省略 `--error` 时 Slurm 隐式并流"这一隐含默认。`spec.log_path` 是**调用方给定的作业日志路径**（计算节点可见，即 scratch 侧），**不是** `logs/<source>/<T>.log` 本身：后者只在失败轮存在（compute-loop §13 的产物表）、由控制器作为唯一 NFS writer 写入（agent-ops §8.4，且 §8.5 禁止把 NFS 路径当作发布目标交给看不到 NFS 的计算节点），其**写入与合并归发布器 #24**（#10 fixture 的 Non-goals 已如此归属）。本模块只把这个路径当值传递，不打开、不创建（round 2 审核 cand-10，verifier CONFIRMED/FIX_NOW：早先此处把两者写成同一份文件，与 §D 自相矛盾）
5. 资源 flag：按 `required_fields` 的字段名 **`sorted()` 升序**逐项展开为 `SBATCH_FLAGS[name], str(value)`（排序而非沿用 `required_fields` 的书写序，是为了让产物只依赖键集不依赖 `config.toml` 里的行序；`str(value)` 使 `cpus = 8` 这类 int 取值可入 argv）
6. `"--wrap", shlex.join(spec.command)`

**C. `sbatch` 输出解析（纯函数）**

- `parse_sbatch_job_id(stdout: str) -> str`：`--parsable` 下 `sbatch` 输出 `<jobid>` 或 `<jobid>;<cluster>`。取 `;` 前首段并 `strip()`；结果为空、或非全数字 -> 抛 `ExecutorError`（`job_id=None`，此刻还没有 id）。多行输出取首个**非空行**。
- **「非空行」的判据 MUST 为「`strip()` 后非空」**（本模块所有涉及"非空行"的地方同判据，含 §D 的行数判定）。纯空白行 MUST 被跳过/不计数，MUST NOT 只用 `if line:` 一类真值判断——那会把 `"   "` 当成一行有效输出。**`strip()` 与该判据都是可机检的属性，MUST 各有一条"改坏即变红"的接受态用例**（见 Required evidence；round 3 审核 cand-14 与 gate retro 的直接产物：此前 `strip()` 只写在散文里、未进 Required-evidence 枚举，测试忠实照枚举写，于是该属性三轮无 oracle）。

**D. `sacct` 命令与输出解析（纯函数）**

- `build_sacct_command(job_id: str) -> tuple[str, ...]` MUST 为 `("sacct", "-j", job_id, "-X", "--noheader", "--parsable2", "--format=JobID,State,Start,End")`。**四列止步于此是本 fixture 的明示裁决，不是遗漏**——两项本可加的列各有归属，逐条见下：
  - **不取 `ExitCode`**：`specs/run-controller/spec.md`「失败处理」MUST 要求合并日志含退出码，但 `JobRecord`（#10 已合并、frozen、全字段无默认）没有承载它的字段；加字段即改 `executor.py` 的公共 schema，与本 issue「`executor.py` 零改动」和 #10 既有测试直接冲突，属跨 issue 变更。故本 issue **不取该列**，理由**只**有两条、且都是本 issue 自身的边界：`JobRecord`（#10 已合并、frozen、全字段无默认）没有承载退出码的字段，加字段即改 `executor.py` 的公共 schema，与本 issue「`executor.py` 零改动」冲突；且本 fixture 已把 `build_sacct_command` 的 argv 逐元素钉死，改列即改那条 oracle。

承载体的裁决 issue 是**已存在的 #47**「失败日志退出码的载体待裁决」（PR #39 已 DEFER 到此），**不另开新 issue**——本 fixture 早先写的"Phase 8 经 issue-scribe 落一条 tracked issue"是重复路由，已更正。**本 fixture MUST NOT 替 #47 作裁决**：#47 的方案 (b) 含两个并列支——「由作业自身的批处理封装写进它自己的日志（该日志由 #24 合并）」**或**「由 #11 另起一次 `sacct` 取得」——**两支在 #47 处均保持开放**。本 fixture 早先的措辞只复述了前一支并加了"不经 `sacct` 取退出码"，等于替 #47 关掉了后一支（round 2 审核 cand-09，verifier CONFIRMED/FIX_NOW），已删除。若 #47 最终选 `sacct` 支，届时按文档优先原则先修订本 fixture 钉死的 argv 再动码。在 #47 落定前，**#24 的日志合成 MUST NOT 假定退出码来自本模块**。
  - **不取 `Submit`**：`submitted_at` 由 `submit` 取一次本地时钟写定（#10 fixture 的打戳时机 MUST），而 `sacct` 在作业刚提交时可能尚无记录（见 Known limits），提交那一刻根本取不到 `Submit`；若改为 `poll` 时用 `Submit` 覆写，同一字段会在一次 run 内先后报出两个值——一个字段两个权威，比时钟偏斜更坏。故保留本地钟，偏斜风险按 Known limits 归 M4。`-X` **不可省**：缺它 `sacct` 会连作业步（`.batch`/`.extern`）一起吐，解析拿到多行且首行未必是分配本体。
- `SACCT_ENV: Mapping[str, str]` MUST 至少含 `{"TZ": "UTC", "SLURM_TIME_FORMAT": "standard"}`。**并入语义钉死为叠加而非替换**：`SlurmJobExecutor` 在调用 runner 前构造 `{**os.environ, **SACCT_ENV}` 传入；MUST NOT 只传 `SACCT_ENV`——那会让子进程丢掉 `PATH` 与 Slurm 客户端环境，M4 现场每次 `poll` 都失败，正是这条钉死本要防的失败类。叠加发生在 executor 侧（可测），不在 `subprocess_runner` 侧（不测）。理由：`sacct` 默认吐集群本地时间且格式受该环境变量左右，而 `JobRecord.__post_init__` 对 naive 与非零偏移 `datetime` 一律 fail closed（`executor.py:_require_utc`）——不钉死时区就是把一个必然的 `ExecutorError` 留到 M4 现场触发。
- `parse_sacct_record(stdout: str, job_id: str) -> tuple[JobState, datetime | None, datetime | None]`：
  - 非空行数 MUST 恰为 1（「非空行」判据同 §C：`strip()` 后非空）；为 0 或 >1 -> 抛 `ExecutorError(job_id=job_id)`（`-X` 下多行意味着出现了未预期的作业副本，静默取首行会让 `poll` 报告一个没被查询的实体）。该唯一行按 `|` 拆四列；列数不等于 4 -> 抛 `ExecutorError(job_id=job_id)`；
  - 首列 JobID 与查询 `job_id` 不相等 -> 抛 `ExecutorError(job_id=job_id)`（防串台）；
  - State 列先按空格截首词（`sacct` 的 `CANCELLED by 1234` 形态 MUST 归一为 `CANCELLED`），再查 `SLURM_STATE_MAP`；**未知状态串 -> 抛 `ExecutorError(job_id=job_id)`，MUST NOT 兜底映射为 `FAILED`**（兜底会把"没见过的调度器状态"伪装成"作业自身失败"，正是 #10 拆分 `TIMEOUT`/`FAILED` 要保住的那条运维判据）；
  - Start/End 列为 `Unknown` / `None` / 空 -> `None`；否则按 `%Y-%m-%dT%H:%M:%S` 解析并挂 `timezone.utc`；格式不合 -> 抛 `ExecutorError(job_id=job_id)`。
- `SLURM_STATE_MAP: Mapping[str, JobState]` MUST 逐条为：`PENDING`/`REQUEUED`/`REQUEUE_HOLD`→`PENDING`（重排队是健康作业的中间态，落进「未知串必抛」会把它变成假故障）；`RUNNING`/`CONFIGURING`/`COMPLETING`/`RESIZING`/`SUSPENDED`→`RUNNING`；`COMPLETED`→`SUCCEEDED`；`TIMEOUT`→`TIMEOUT`（**MUST NOT** 折叠进 `FAILED`）；`FAILED`/`CANCELLED`/`NODE_FAIL`/`OUT_OF_MEMORY`/`BOOT_FAIL`/`DEADLINE`/`PREEMPTED`/`REVOKED`→`FAILED`。

**E. `SlurmJobExecutor`（协议一致性，注入式运行器）**

- `SlurmJobExecutor(*, required_fields: Sequence[str], clock: Callable[[], datetime], runner: Callable[..., str])`，三者**均无默认值**。`runner(argv: Sequence[str], *, env: Mapping[str, str] | None) -> str` 返回子进程 stdout；模块另提供 `subprocess_runner` 作为真实实现。
- 注入 runner **不是** design.md D3 所弃的 "subprocess mock"：D3 弃的是"用命令行 mock 替代 `JobExecutor` 协议 fake 去测控制器状态机"；此处 runner 是本模块自身的进程边界，注入它测的是 argv 装配与输出解析的**组装**，argv 形态由本 fixture 逐字钉死，不脆弱。真实 `sbatch`/`sacct` 行为仍归 M4。
- `submit(spec) -> JobRecord`：`build_sbatch_command` → `runner` → `parse_sbatch_job_id` → 取一次 `clock()` 作 `submitted_at` → 返回 `JobRecord(state=PENDING, started_at=None, ended_at=None, resources=spec.resources)`；并在实例内按 `job_id` 记住该记录（`poll` 需要 `name`/`resources`/`submitted_at`，`sacct` 不提供）。runner 抛出的任何异常 MUST 转成 `ExecutorError`，MUST NOT 外泄 `OSError`/`CalledProcessError`。
- `submit` 调用 runner 时 `env=None`（继承当前进程环境，无需时区钉死——`sbatch` 的输出只有 job id）；`poll` 调用 runner 时 `env={**os.environ, **SACCT_ENV}`。
- `poll(job_id) -> JobRecord`：本实例未提交过的 `job_id` -> 抛 `ExecutorError(job_id=job_id)`（与协议「未知 `job_id` 必抛」一致）；否则 `build_sacct_command` → `runner`（并入 `SACCT_ENV`）→ `parse_sacct_record` → 用解析出的 state/start/end 重建 `JobRecord`（`submitted_at`/`name`/`resources` 取自记住的提交记录），更新实例内记录并返回。记录不变式仍由 `JobRecord.__post_init__` 在构造点复检——本模块 MUST NOT 自行复制那套不变式（第二权威）。
- 终态幂等：已达终态的 job 再 `poll` MUST 返回同一记录且 MUST NOT 再调 runner。
- `isinstance(SlurmJobExecutor(...), JobExecutor)` MUST 为真，且 `submit`/`poll` 的 `inspect.signature` 与协议同名方法逐参数一致（承 #10 evidence 先例）。

Seams under test:
- `build_sbatch_command` / `parse_sbatch_job_id` / `build_sacct_command` / `parse_sacct_record`（纯函数，可逐值断言——issue 正文所称"参数装配纯函数检查"）
- `SlurmJobExecutor.submit/poll` 配记录型假 runner + `StepClock`（"协议一致性"）
- `subprocess_runner`：**不测**（真实进程边界，M4 oracle）

Risk packs considered (core):
- Public API / CLI / script entry: selected - `SlurmJobExecutor` 是 `JobExecutor` 的生产实现，#26–#28 按协议消费；四个纯函数是本模块公开面
- Config / project setup: selected - 资源参数全部装配自 `local.toml`，键集权威归属与零默认是核心验收项
- File IO / path safety / overwrite: not selected - 本模块不打开、不创建、不删除任何文件；`work_dir`/`log_path` 只作为 argv 实参转成字符串
- Schema / columns / units / field names: selected - `sacct --format` 的四列顺序、`|` 分隔、状态串词表、时间格式即解析 schema，错配直接污染运行报告与 receipt
- Auth / permissions / secrets: not selected - argv 里只有队列名/账户名/资源额度一类调度参数，无凭据；本模块不打印命令输出
- Concurrency / shared state / ordering: selected - 实例内 job 记录表是共享可变状态；终态幂等与"未知 job_id 必抛"是其契约（真实并发归控制器 flock，本模块单线程）
- Resource limits / large input / discovery: not selected - 无发现逻辑；`sacct` 输出为单作业单行（`-X`）
- Legacy compatibility / examples: not selected - 全新模块，仓库内零既有消费者（控制器尚未落地）
- Error handling / rollback / partial outputs: selected - fail-closed 是核心验收项：键集不等、无 flag 字段、未知状态串、空/畸形 `sacct` 输出、runner 异常全部收敛到 `ExecutorError`，不返回半成品记录
- Release / packaging / dependency compatibility: selected - 只用 stdlib，不得新增依赖，`uv sync --frozen` 无 drift
- Documentation / migration notes: not selected - 无迁移；模块语义由本 fixture 钉死

Domain packs (from active profile):
- Geospatial / CRS: not selected - 无几何
- Time series / forcing / temporal boundaries: selected - `sacct` 时间戳→tz-aware UTC 的转换在此定型，错配会静默错位 receipt 与 cycle 的绝对时间比较
- 状态链 / warm-start 定戳: not selected - 本模块不读写 `cfg.ic`
- NWM 快照溯源 / DB-free 隔离: not selected - 全新代码，非快照，无 NWM import、无数据库连接

Required evidence（每条 input -> expected output）:

装配（B）:
- `required_fields=("partition","account","cpus","memory","walltime")`、`resources={"partition":"cpu","account":"acct","cpus":8,"memory":"32G","walltime":"04:00:00"}`、`spec.command=("shud","yd")` -> `build_sbatch_command` 返回**逐元素精确相等**的元组：`("sbatch","--parsable","--job-name",<name>,"--chdir",<work_dir>,"--output",<log_path>,"--error",<log_path>,"--account","acct","--cpus-per-task","8","--mem","32G","--partition","cpu","--time","04:00:00","--wrap","shud yd")`（资源段按字段名 `sorted()` 升序；`cpus=8` 的 int 已转字符串）
- 同上但 `required_fields` 书写序改为 `("walltime","cpus","partition","memory","account")` -> 产物与上一条**完全相同**（证明产物只依赖键集，不依赖行序）
- **参数化：** 逐个删掉 `resources` 中的一项（五次）-> 每次抛 `ExecutorError`，消息含该缺失键名
- `resources` 多一个 `required_fields` 未声明的键 -> 抛 `ExecutorError`，消息含该多余键名
- `required_fields` 含 `SBATCH_FLAGS` 无对应 flag 的字段名（如 `"gres"`，且 `resources` 同步带该键使键集相等）-> 抛 `ExecutorError`，消息含 `gres`（MUST NOT 静默丢弃，MUST NOT 只是少一个 flag 地正常返回）
- `SBATCH_FLAGS` 有富余条目而 `required_fields` 只声明其中三项（`resources` 同步只带三项）-> 正常返回，产物只含那三项的 flag（config 是键集权威）
- `spec.command=("shud","--in","a b")` -> `--wrap` 的实参为 `shlex.join` 结果（含空格的元素被引起来），argv 长度不变（`--wrap` 后恒为**单个**字符串）
- `--output` 与 `--error` 的实参**都**等于 `str(spec.log_path)`（显式并流；断言两个 flag 均在产物中出现）
- 源码机检：`producer/src/yd_producer/executor.py` 的文本在本 PR 后仍不含 `partition`/`account`/`cpus`/`memory`/`walltime` 任一字面量（`test_executor.py:518-520` 的既有守卫继续绿即为证据，不新增重复用例）

`sbatch` 输出（C）:
- `"12345\n"` -> `"12345"`；`"12345;cluster0\n"` -> `"12345"`；`"\n12345\n"` -> `"12345"`
- **接受态归一化（钉死 `strip()`；去掉 `strip()` 后这两条 MUST 变红）**：`"12345 \n"` -> `"12345"`；`"12345 ; cluster0\n"` -> `"12345"`
- **接受态「非空行」判据（把 `line.strip()` 换成 `line` 后 MUST 变红）**：`"   \n12345\n"` -> `"12345"`（纯空白行被跳过，不被当作首个非空行）
- `""`、`"   \n"`、`"abc"`、`";cluster0"` -> 各抛 `ExecutorError` 且 `exc.job_id is None`

`sacct`（D）:
- `build_sacct_command("12345")` -> 逐元素等于 `("sacct","-j","12345","-X","--noheader","--parsable2","--format=JobID,State,Start,End")`
- `SACCT_ENV["TZ"] == "UTC"` 且 `SACCT_ENV["SLURM_TIME_FORMAT"] == "standard"`
- `"12345|COMPLETED|2026-08-28T00:00:00|2026-08-28T01:00:00"` -> `(SUCCEEDED, 2026-08-28T00:00:00+00:00, 2026-08-28T01:00:00+00:00)`，两个 `datetime` 断言 `tzinfo is not None and utcoffset() == timedelta(0)`
- **参数化：** `SLURM_STATE_MAP` 的每个键各一行 -> 得到该键映射的 `JobState`；其中 `TIMEOUT` 行 -> `JobState.TIMEOUT`（**断言 `is not JobState.FAILED`**）
- `"12345|CANCELLED by 1234|...|..."` -> `FAILED`（空格后缀被截）
- `"12345|PENDING|Unknown|Unknown"` -> `(PENDING, None, None)`；`None` 与空串两种写法同样得 `None`
- `"12345|BOGUS_STATE|Unknown|Unknown"` -> 抛 `ExecutorError` 且 `exc.job_id == "12345"`（MUST NOT 得到 `FAILED`）
- `""`（空输出）-> 抛 `ExecutorError`，`exc.job_id == "12345"`（fail closed；见 Known limits 的 sacct 落库延迟一条）
- **「非空行」判据（把行过滤的 `line.strip()` 换成 `line` 后 MUST 变红）**：`"   \n12345|COMPLETED|2026-08-28T00:00:00|2026-08-28T01:00:00"` -> 正常解析为 `SUCCEEDED`（纯空白行不计入行数，不触发"行数不为 1"）
- 两行合法记录（`"12345|COMPLETED|...|...\n12345|FAILED|...|..."`）-> 抛 `ExecutorError`，`exc.job_id == "12345"`（MUST NOT 静默取首行）
- `"12345|COMPLETED|2026-08-28T00:00:00"`（三列）-> 抛 `ExecutorError`
- `"99999|COMPLETED|...|..."`（JobID 串台）-> 抛 `ExecutorError`，`exc.job_id == "12345"`
- `"12345|COMPLETED|28/08/2026 00:00|..."`（时间格式不合）-> 抛 `ExecutorError`

协议一致性与组装（E）:
- `isinstance(SlurmJobExecutor(...), JobExecutor)` 为真；`inspect.signature` 逐参数比对 `submit`/`poll` 与 `JobExecutor` 同名方法一致
- 记录型假 runner（返回 `"12345"`）+ `StepClock(start=..., step=...)` 下 `submit(spec)` -> 假 runner 收到的 argv 与 `build_sbatch_command` 的产物逐元素相等；返回记录 `state is PENDING`、`job_id == "12345"`、`started_at is None`、`ended_at is None`、`submitted_at` 等于 `StepClock` 的首值（精确值断言，非"约等于现在"）、`resources` 与 `spec.resources` 逐键相等
- 承上 `poll("12345")`，假 runner 返回 `"12345|RUNNING|2026-08-28T00:00:00|Unknown"` -> 收到的 argv 等于 `build_sacct_command("12345")`；返回记录 `state is RUNNING`、`started_at` 非 None、`ended_at is None`、`name`/`resources`/`submitted_at` 与提交记录一致
- 承上再 `poll`，返回 `COMPLETED` 行 -> 记录进 `SUCCEEDED`；此后再 `poll` 两次 -> 返回值相等且假 runner **调用次数不再增加**（终态幂等，不再触 `sacct`）
- `poll("nosuch")`（本实例未提交过）-> 抛 `ExecutorError` 且 `exc.job_id == "nosuch"`，假 runner 调用次数为 0
- **env 叠加（两条判别性断言，各挡一种错法）**：`monkeypatch.setenv("YD_SENTINEL", "1")` 与 `monkeypatch.setenv("TZ", "Asia/Shanghai")` 后 `poll` -> 假 runner 收到的 `env` **同时**含 `YD_SENTINEL=1`（挡 `env=SACCT_ENV` 的整体替换写法）与 `env["TZ"] == "UTC"`（挡 `{**SACCT_ENV, **os.environ}` 的顺序写反）；`submit` 收到的 `env is None`
- 假 runner 抛 `OSError` / `subprocess.CalledProcessError` -> `submit` 与 `poll` 各抛 `ExecutorError`（原异常 MUST NOT 外泄；以 `pytest.raises(ExecutorError)` 断言），且 `str(exc)` **含原异常文本**（`sbatch` 的诊断信息走 stderr、runner 只回 stdout，转译若不带原文就只剩「非全数字」一类无信息消息）
- `inspect.signature(subprocess_runner)` 与 `runner` 契约一致——首参为位置 argv、`env` 为 keyword-only——且模块可导入（该符号不测行为，但必须存在且签名对得上——否则它可以完全不存在而全绿）
- 假 runner 令 `sacct` 返回 `COMPLETED` 但 `Start` 早于 `submitted_at` -> 抛 `ExecutorError`（不变式由 `JobRecord.__post_init__` 拦下；证明本模块未绕过构造点校验）
- `SlurmJobExecutor` 的 `required_fields`/`clock`/`runner` 三个参数均**不可省**（缺任一即 `TypeError`），且均为 keyword-only
- 全部失败路径以 `pytest.raises(ExecutorError)` 表达，MUST NOT 用 `pytest.raises(Exception)`

工程门禁:
- `cd producer && uv run pytest` -> 退出码 0（含 `test_executor.py` 既有用例全绿）
- `cd producer && uv run ruff check .` 与 `uv run ruff format --check .` -> 退出码 0
- `cd producer && uv sync --frozen` -> 退出码 0

Non-goals:
- 真实 `sbatch`/`sacct` 提交与查询行为、真实状态码在集群上的取值分布 —— M4 oracle（design.md D3、issue 正文 Out of Scope）
- `subprocess_runner` 自身的**行为**测试（真实进程边界，M4 oracle）。其存在性与签名一致性仍有一条 evidence（见上），因为仓库没有类型检查闸，纯靠类型标注等于没有闸。
- 控制器对本执行器的消费（前沿、双源并行、失败隔离、flock）—— 组 12–14，issue #22–#28
- 作业取消 / watchdog —— compute-loop §10 明确不做
- `local.toml` 资源取值的语义校验（`walltime` 格式、`partition` 是否真实存在、`memory` 单位）—— 装载层只校验类型（#2 已裁决"不做值域校验"），真实取值的正确性归 M4 现场
- **不钉以下三处 `strip()`，按 slack 处理**（round 3 的系统性归一化扫描共发现 6 个存活变异体，其余三处逐条裁决于此，使后续轮次不再重复发现同一批）：`_parse_sacct_time` 对时间列的 `raw.strip()`、JobID 串台比对的 `reported_id.strip()`、状态串截首词前的 `raw_state.strip()`。理由：三者都是 `--parsable2` 输出上不会出现的形态（该模式不产生列内 padding），且都不在 spec 或本 fixture 的任何 MUST 之下；它们是防御性余量，不是被钉死的属性。若日后现场证明 `sacct` 会吐 padding，按文档优先先修订本条再动码。
- 跨进程的 job 记录持久化：`poll` 依赖实例内提交记录，故只支持"同一 run 进程内提交后轮询"。这正是 `specs/run-controller/spec.md`「并发与锁」的形态（单进程持 flock 覆盖提交→等待→发布全生命周期），非缺陷
- `squeue` 回退：`sacct` 落库前的查询空窗按 fail closed 处理（见 Known limits），本 issue 不引入第二个查询通道

Known limits（每条在 PR 工作说明中复述，并按 Phase 8 规则路由）:
- **`sacct` 落库延迟**：作业刚提交时 `sacct` 可能尚无记录，本模块按 fail closed 抛 `ExecutorError`。M2 无真实调度器，无法判定该窗口是否需要重试/回退 `squeue`；归 M4 现场验证。
- **时钟偏斜**：`submitted_at` 取本地时钟、`started_at` 由 Slurm 报告，登录节点与计算节点时钟偏斜可能触发 `JobRecord` 的 `submitted_at <= started_at` 不变式而抛 `ExecutorError`。M2 不引入容差（容差是一个内置默认，正是本 issue 要消除的形态）；归 M4 现场验证。
- **退出码不经本模块（承载体裁决仍在 #47，本 fixture 不预判）**：`specs/run-controller/spec.md`「失败处理」要求合并日志含退出码，本模块的 `sacct --format` 明示不取 `ExitCode`（理由见 §D，均为本 issue 自身边界）。承载体的裁决 issue 是已存在的 **#47**，不另开新件；其方案 (b) 的两个并列支（作业体自写日志由 #24 合并 / 由 #11 另起一次 `sacct`）**均未被本 fixture 关闭**。在 #47 落定前 **#24** 的日志合成 MUST NOT 假定退出码来自本模块。
- **进程死亡窗口（孤儿作业）**：`poll` 只认本实例提交过的 `job_id`（fixture §E 的 MUST），故 run 进程在等待期被杀后，flock 随进程释放而 Slurm 作业仍在跑，下一个 tick 无从发现它——按「未提交残留清理重跑」删 work 并重新提交，会出现同源两个在途作业（违反 agent-ops §8.3），且孤儿作业继续往被删的目录写。**更正本 fixture 早先的措辞**：Non-goals 里「跨进程 job 记录持久化…正是「并发与锁」的形态，非缺陷」只对**进程存活**的正常路径成立；崩溃一支不被「并发与锁」覆盖。归属：崩溃恢复前置由 **#23/#28** 的 fixture 裁决（按 receipt 里的 job ID 作一次存活确认，或"见半成品 work 即停该源等人工"）。
- **已提交但未登记窗口**：`submit` 先调 runner 再解析 job id，故 `sbatch` 退出 0、作业已排队、而 stdout 形态超出 §C 钉死的域时，`parse_sbatch_job_id` 抛错且 `self._records` 里什么都没有——后果与上一条同类。缓解：错误消息带原始行/原始 stdout，运维可人工定位。归属同上（**#23/#28**）。
- **`poll` 抛错 ≠ 作业失败**：`ExecutorError` 只有 `job_id` 一个结构化属性，「`sacct` 尚未落库」与「解析失败」抛的是同一个无类别异常，控制器没有可机检的判别位。本 issue 不加判别位（那要么改 `executor.py` 的公共异常、要么在协议层外另立一套，均越界）；**#26** 的 fixture MUST 钉死「`poll` 抛出异常不得直接触发「失败处理」的 work 删除」。
- **重排队语义未钉（M4 现场剧本）**：`REQUEUED`/`REQUEUE_HOLD` 被当作健康中间态，但三处相邻面都没有保证一次重排队能活下来——(i) `sacct -j <id> -X` 对重排队作业是否吐多行未经现场确认，多行会被「行数恰为 1」硬抛；(ii) `sbatch` 未给 `--open-mode`，重跑是截断还是追加取决于站点 `JobFileAppend`，截断会抹掉首次尝试的 stdout/stderr，与「失败处理」的"完整 stdout/stderr"相抵；(iii) `PREEMPTED`→`FAILED` 见下条。M4 剧本：提交 → `scontrol requeue <id>` → 跑本 fixture 钉死的 `build_sacct_command` argv，数行数并核对日志是否保留首次尝试。`--open-mode` 的归属由 **#24/#25** 的 fixture 裁决（加 flag 会改本 fixture 逐元素钉死的 argv，故不在本 issue 做）。
- **`PREEMPTED` 被钉成终态**：`PREEMPTED`→`FAILED` 叠加「终态幂等、不再查 `sacct`」，意味着被抢占后重排队仍在跑的作业会被本模块永久报成失败，控制器据此删 work 目录。M2 无真实调度器无法判定该形态是否出现；归 compute-loop §10 / M4 现场验证（本模块不做取消、不做 watchdog 是 §10 明确不做的范围）。
- **`sacct` 时间列与 `JobRecord` 不变式的对撞（双向）**：本 fixture 假定运行中作业 `End=Unknown`、终态作业两列都有值。若某版本对运行中作业给出预估 `End`，「非终态不得带 `ended_at`」硬抛；反向地，终态行若 `End=Unknown`，「终态必须带 `ended_at`」硬抛，`COMPLETED` 行若 `Start=Unknown`，「终态（非 FAILED/TIMEOUT）必须带 `started_at`」也硬抛（`executor.py:150-176`）。方向与 fail closed 一致（宁可报错不可乱报时间），但会让 `poll` 在该形态上不可用；归 M4 现场验证。
- **`required_fields` 删项 = 交给 Slurm 默认**：若 `config.toml` 的 `slurm.required_fields` 删掉某项（如 `partition`），装配层按键集权威正常产出不含该 flag 的命令，实际取值退化为集群默认。这不是本模块的内置默认（本模块无 fallback 取值），但确实是一条"配置层可静默放弃约束"的路径；`config.toml` 生产实例的字段完整性归 **#29**。

Review focus:
- `executor.py` 是否真的零改动，`test_executor.py:518-520` 的字面量守卫是否原样保留（放宽/删除该守卫即 oracle 完整性违规）
- 键集权威是否唯一：`slurm.py` 内是否出现任何"必需五项"的固定清单，或以 `SBATCH_FLAGS` 的键集充当必需性判据（两者皆为第二权威）
- 是否存在任何隐式默认：资源参数的 fallback 取值、`required_fields`/`clock`/`runner` 的默认值、未知 `sacct` 状态串兜底为 `FAILED`、空 `sacct` 输出兜底为 `PENDING`
- `TIMEOUT` 是否在任何路径上被折叠为 `FAILED`
- 时间是否一律 tz-aware UTC，且 `SACCT_ENV` 的时区钉死是否真的并入了子进程环境（只定义常量而不传入等于没做）
- 记录不变式是否仍由 `JobRecord.__post_init__` 独家承担，本模块是否复制了一份（第二权威）
- 失败路径是否全部收敛到 `ExecutorError` 且 `job_id` 属性可机检定位；runner 的原生异常是否被吞而未转译
- 传给 runner 的 `env` 是 `os.environ` 叠加 `SACCT_ENV`（子进程仍有 `PATH`），还是把 `SACCT_ENV` 当整个环境替换掉
- **成功路径的输入归一化是否也有 oracle**（常驻轴，非一次性检查）：`strip()`、"非空行"判据、分隔符切分这一类**接受态**属性，是否各有一条"改坏即变红"的用例——而不是只测失败路径。本 PR 三轮复发的 test-coverage-gap 全部源自这一轴从未进过变异清单（gate retro 的根因结论）
- argv 断言是否逐元素精确比对，而非"包含某个 flag"式的弱断言

## 6. forcing-chain（二）：科学计算依赖引入

- [x] 6.1 引入 numpy/xarray/cfgrib 并 `uv lock`，加 import 冒烟测试，确保 CI producer job 绿（必要时 CI 补 eccodes 系统依赖，作为依赖引入的伴生动作显式提交）

依赖：无
§13.1 归属：DB-free 链（支撑）
Suggested fixture level: none - import 冒烟即验证目标，无业务 fixture
Minimal mergeable slice: atomic - 依赖+lock+冒烟+CI 绿是一条验证路径上的原子提交，子项无独立价值

## 7. forcing-chain（三）：canonical 转换

- [ ] 7.1 快照 DB-free canonical converter 并以合成 raw fixture → canonical NetCDF + catalog 端到端测试（无数据库连接断言）

依赖：组 2、组 3（manifest）、组 6（依赖）
§13.1 归属：DB-free 链
Suggested fixture level: expanded - 需构造可被 cfgrib 读取的合成 GRIB 样本，fixture 制作本身有分量
Minimal mergeable slice: atomic - converter 与其端到端测试互为验证，先合无测试的 converter 或无 converter 的 fixture 都不构成独立绿（依赖引入已剥离到组 6）

## 8. forcing-chain（四）：direct-grid forcing 与 SHUD 输入组装

- [ ] 8.1 快照 file-backend direct-grid forcing 生产（格点即站点、binding 权重 1、`Time_Day=0` 锚 cycle）
- [ ] 8.2 实现 work 内临时 registry/model manifest 生成与随 work 清理
- [ ] 8.3 实现 SHUD 输入组装：变体 + forcing + `states/<source>/<T>.cfg.ic` → 运行目录；warm-start 状态 MUST 覆盖变体自带率定末态（可区分 IC fixture 断言）；固定覆盖六项参数（START=0/END=7/DT_QR_DOWN=60/Update_IC_STEP=720/BINARY_OUTPUT=1/ASCII_OUTPUT=0），00Z/12Z 同参数测试

依赖：组 7（canonical 结构）、组 4（状态文件）
§13.1 归属：DB-free 链
Suggested fixture level: compact - 合成 canonical NetCDF、合成变体目录与可区分状态文件即可
Minimal mergeable slice: direct-grid forcing 生产（8.1）——对合成 canonical 独立可验证；registry 与组装为后继

## 9. checkpoint-tracker：T+12 捕获与补跑

- [x] 9.1 快照并适配 `cfg.ic.update` 轮询捕获（命中 720 分钟复制 + 分段格式校验；产物保持相对时间头），以模拟覆写序列测试正常/漏采/副本损坏三态
- [ ] 9.2 快照并适配漏采补跑（同一 Slurm 作业内、同初态同 forcing、END=0.5、末态采纳；注入假 SHUD 调用测试；补跑失败传导整轮失败；控制器提交计数不变）

依赖：组 2（勘察清单定原路径）、组 4（分段校验）、组 8（运行目录形态）
§13.1 归属：tracker
Suggested fixture level: compact - 模拟覆写序列与假 SHUD 调用即可确定性重放竞态
Minimal mergeable slice: 捕获轮询（9.1）——独立于补跑可合并保绿

### Issue #16 fixture（任务 9.1）

**落点与边界裁决（先读这条）**：issue 正文写 "Module / Scope: producer 包 `yd_producer.tracker`（捕获）"、"PR Boundary: tracker 模块与测试"。本 issue 落 `producer/src/yd_producer/tracker/`（新包），**并在 `producer/src/yd_producer/state/cfg_ic.py` 增加一个公开的 header 分钟读取函数**。后者是对 PR Boundary 的**一处刻意越界**，理由是硬的：tracker 的轮询判据是「header 的最后一个数值 token 即 minute-time」，这条规则在本仓已经有唯一实现（`cfg_ic._header_counts` 的 `numeric[:-1]` 取法，逐字移植自 pin `packages/common/state_qc.py:574-606`），而 pin 侧该规则的公开出口 `cfg_ic_header_minute_time`(`state_qc.py:629`) 与 `_header_counts` **同文件同规则**。把它在 tracker 里另写一份，就是 `state/__init__.py` docstring 明令禁止的第二份分段/header 逻辑（「MUST 复用 `cfg_ic` 里的分段识别辅助，不得再移植一份 NWM pin 的分段逻辑」），且两份实现漂移时轮询与结构检查会对「哪个 token 是 minute-time」产生分歧——这正是 pin 在 `cfg_ic_header_minute_index` docstring 里写明要避免的失败。故 `state/cfg_ic.py` 的改动限定为**只新增两个逐字移植自同一 pin 文件的函数**（公开 `header_minute_time` + 私有 `_header_minute_index`），既有代码一字不改，不动 `parse`/`render`/`_header_counts`，不引入 #9（任务 4.2–4.4）的结构检查与重戳。

**落点裁决修订 R1（2026-08-28，越界撤回；本条覆盖上一段的越界部分）**：本 PR 在评审期间，`origin/master` 推进到 `0d16ee1`（PR #61，issue #9 state-tools），其中 **issue #22（任务 12.1）已落 `producer/src/yd_producer/state/header_time.py`**，含 `cfg_ic_header_minute_index`(pin `state_qc.py:609`)、`cfg_ic_header_minute_time`(`:629`)、`cfg_ic_header_shape`(`:664`)、`CfgIcHeaderShape`(`:650`)、`_VALID_CFG_IC_HEADER_TOKEN_COUNTS`(`:646`) 五个逐符号移植——**正是本 issue 越界新增的那两个符号**，且落点更宽、更正。裁决据此改为：

- 本 issue **撤回**对 `state/cfg_ic.py` / `state/__init__.py` / `test_cfg_ic.py` 的全部改动，改为**消费** `from yd_producer.state import cfg_ic_header_minute_time`。改动面收回到 issue 正文写死的 "PR Boundary: tracker 模块与测试"，**越界归零**。
- 上一段那条越界理由（避免「哪个 token 是 minute-time」出现双权威）在新事实下**由消费实现得更彻底**：唯一权威是 `header_time.py`，tracker / `state_qc` / `restamp` 三方共用，正是 master 的 `state/__init__.py` docstring 明令的形态。
- 语义等价已核对：master 的 `cfg_ic_header_minute_time` 与本 PR 撤回的 `cfg_ic.header_minute_time` 移植自**同一 pin 行段**（`:609-639`），`_as_float` 亦同源；对 §G1 的七个 header 输入逐值相同。
- **非有限值守卫（`nan`/`inf`）仍留在 tracker 一侧**（`_header_minute_of` 的单一出口）：`header_time` 只做 token 提取，不做值域判定；本 issue 的「非有限即本次观测无结果」是 tracker 的轮询语义，不上移。
- 由此**作废**：round 2 的 cand-16 / cand-18 对清单 §1 `packages/common/state_qc.py` 行的修订义务（该行已由 #9/#22 在 master 侧写全，本 PR 合并时取 master 侧为权威）；清单 §1「最小测试（cap 5 header）」行的配对约束回到 master 侧措辞。
- **搭车修改**：合并 master 时 `.large-file-guard.json` 增加两条 exclude（`producer/tests/test_cfg_ic.py` 1049 行、`producer/tests/test_state_tools_qc.py` 1129 行），二者是 master 上已有的超限文件（PR #61 走服务端合并，不经本地 PreToolUse 钩子），而钩子按暂存集判定，导致 master 向任何分支的合并都会被自己的守卫挡死。拆分超出本 issue 范围，另开 issue 跟踪；`maxLines` 不动，无任何断言/测试/CI 被削弱。


**本 issue 只做 9.1（捕获轮询）**，9.2（漏采补跑）归 issue #17。清单 §1 的第 6/7 行（`runtime.py` → `tracker/checkpoint_tracker.py`、`tests/test_shud_runtime.py` → `tests/test_checkpoint_tracker.py`）同时覆盖捕获与补跑两半，本 issue 只搬捕获半；两行的 `落地状态` 仍必须在本 PR 翻成 `本 issue 落地`——溯源守卫的反向判别器 `test_files_carrying_a_provenance_header_are_marked_landed` 一旦见到带溯源头的目标文件就要求该行标 `本 issue 落地`，留 `待落地` 会直接变红。**翻转 MUST 与文件落地同一个 commit**：正向判别器 `test_landed_snapshot_files_carry_their_provenance_header` 的缺席分支反向同样成立（「落地状态也不得先于文件翻转」），故 fixture 先行的 docs commit 里两行 MUST 仍是 `待落地`，由实现 commit 一并翻转。两行 `备注` 同步补记「本 issue 落捕获半，补跑半归 #17 落进同一文件」，并在 design.md **D9** 记录该分次落地偏离（spec `快照可追溯` Requirement 自带的逃生口：「或 design 中存在显式偏离记录」）。

**改动面**：

- 新增 `producer/src/yd_producer/tracker/__init__.py`、`producer/src/yd_producer/tracker/checkpoint_tracker.py`
- 新增 `producer/tests/test_checkpoint_tracker.py`
- **不改动** `producer/src/yd_producer/state/**` 与 `producer/tests/test_cfg_ic.py`（裁决修订 R1：header 分钟读取从 master 的 `state/header_time.py` **消费**，不再自行移植）
- 修改 `.large-file-guard.json`（合并 master 的搭车修改，见裁决修订 R1）
- 修改 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md`（§1 第 6/7 行 `落地状态` 与 `备注`）、`design.md`（D9）、本文件（本 fixture + 勾选 9.1）
- **不改动** `executor.py`、`slurm.py`、`config.py`、`cli.py`、`geometry.py`、`nwm.py`、`rawscan.py`、`store/**`、`raw/**`、`pyproject.toml`、`uv.lock`

**Must-preserve behavior**：

- `producer/tests/test_cfg_ic.py`、`producer/src/yd_producer/state/**` 相对 master **零改动**（裁决修订 R1）：`git diff origin/master -- producer/src/yd_producer/state producer/tests/test_cfg_ic.py` MUST 为空
- `test_snapshot_provenance.py` 的正反向守卫继续绿（新文件登记进清单、带溯源头、清单行标 `本 issue 落地`）
- producer 依赖面不变：本 issue 只用 stdlib（`dataclasses`/`hashlib`/`pathlib`/`typing`），`uv sync --frozen` 无 drift

**抽取集（对清单 §1 第 6 行的本 issue 子集，逐符号）**：`_StateCheckpointTracker` 的 `capture_available`(:3717)、`capture_final`(:3737)、`_capture`(:3887)、`missing_hours`(:3919)，加 `_read_cfg_ic_header_minute`(:3618)、`_header_minute_matches_checkpoint`(:3963)。**明示不抽**（全部归 #17 或后继）：`_format_header_minute`(:3634)（pin 里只服务补跑的 `gate_rejected(header=...)` 诊断串，本 issue 无该消息面）、`install_recovered`(:3827)、`record_recovery_outcome`(:3740)、`recovery_outcome_summary`(:3750)、`write_manifest`(:3766)、`_manifest_provenance`(:3793)、`_final_ic_entry`(:3803)、`_state_checkpoint_poll_seconds`(:3952)、`_state_checkpoint_hours`(:3923)、`_forecast_horizon_hours`(:3944)、`_recover_missing_state_checkpoints`(:784)、`_log_recovery_refusal`(:939)、`_clear_recovery_scratch_root`(:2799)、`_shift_cfg_ic_time`(:3653)。不抽 `write_manifest` 的连带效果：pin 的 `_task_outcome_attempt_identity`(:2558) 两处 `SLURM_*` 环境读取（清单第 6 行 D4 段声明为「合法保留项」的那两处）**本 PR 完全不涉及**——本模块零环境读取。

**A. 包与公开面**

- `yd_producer.tracker` 是新包；`tracker/__init__.py` 只做再导出（`CheckpointTracker`、`CapturedCheckpoint`、`TrackerError`），不含逻辑。
- `checkpoint_tracker.py` 头部第一行块 MUST 含整行溯源注释 `# NWM@8ae9b8f2 workers/shud_runtime/runtime.py`（形式由 `snapshot_provenance_fixtures._MARKER_COMMENT` 唯一定义，行号 ≤ `HEADER_LINE_BUDGET`），并在模块 docstring 里逐条写明下方 §F 的对 pin 偏离。
- `test_checkpoint_tracker.py` 同样带 `# NWM@8ae9b8f2 tests/test_shud_runtime.py`。
- `TrackerError(Exception)` 是本模块唯一对外异常类型；MUST NOT 外泄 `OSError`/`SafeFilesystemError`/`ValueError` 给调用方（构造期参数校验除外，见 §B）。
- **禁区字面量告警（对实现方与测试方都适用）**：`checkpoint_tracker.py` 与 `test_checkpoint_tracker.py` 都是清单登记目标，落地后进 DB-free 扫描集，其中 `psycopg`/`DATABASE_URL`/`scheduler`/`registry`/`journal`/`reservation`/`os.getenv`/`os.environ` 八个串**逐字出现即变红**，注释与 docstring 同样被扫。写 §F 偏离说明与 §D 映射表时用中文表述（「零环境变量读取」「不接调度器数据库」），MUST NOT 写出这些字面量。

**B. `CheckpointTracker` 构造（零内置默认）**

- `CheckpointTracker(*, run_dir: Path, project_name: str, checkpoint_hours: Sequence[int])`，三者**均无默认值**、均 keyword-only。
- 观测源固定为 `run_dir / f"{project_name}.cfg.ic.update"`（暴露为只读属性 `source_path`）；捕获产物落 `run_dir / "state_checkpoints"`（只读属性 `checkpoint_dir`）。**MUST NOT** 从 manifest 里按四路 fallback 猜 project name（pin `_project_name`(:4114) 的四选一加下标兜底），也 **MUST NOT** 递归搜索其它文件名——文件名由调用方显式给出，猜错就是永远读不到 header。
- `checkpoint_hours` 的**唯一权威是 `Config.checkpoint_hours`**（`config.toml`，已由 issue #2 落装载器）。本模块 MUST NOT 写死 `12` 或 `720`，MUST NOT 从 manifest 读、MUST NOT 从 forecast horizon 推。
- 构造期 fail closed（每条各抛 `TrackerError`）：`checkpoint_hours` 为空；含 ≤0 的小时；含重复项；`project_name` 为空或含路径分隔符 / `.` / `..` / **含 NUL 字节**；`run_dir` 的任一分量**含 NUL 字节**。**这是对 pin 的刻意偏离**：pin 的 `_state_checkpoint_hours`(:3923-3941) 对不可解析值 `continue`、对 ≤0 与超 horizon 静默过滤、对重复静默去重——三条都是 fail-open，一个配置笔误会退化成「跑完没有 checkpoint 也不报错」。
- **NUL 这两条是 rung-1 义务而非洁癖**：`os.stat`/`os.open` 对路径里的 NUL 抛的是 `ValueError` 而**不是** `OSError`，`safe_fs` 也不转译它，于是它绕过 `_FS_FAILURES` 直接从 `capture_available()` 外泄，违 §A「唯一对外异常」。而它**可从配置到达**——TOML 的基本字符串接受 `\u0000`。修法 MUST 是构造期的**纯字符串检查**（不碰文件系统，§B 不破），MUST NOT 把 `ValueError` 并进 `_FS_FAILURES`。**理由（round 2 更正）**：放宽 `_FS_FAILURES` 会把这个外泄换成观测期的静默「本次观测无结果」——一个可从配置到达的 NUL 于是变成整轮零捕获，且与「SHUD 从没启动」逐字节相同，比外泄更坏。原先写在这里的理由「会吞掉 `_copy_is_intact` 依赖的 `state.parse` 判别信号」**结构上为假**：`_copy_is_intact` 在 `_capture` 的 `try` **之外**调用，`state.parse` 的 `ValueError` 由它自己的局部 `except ValueError` 消费，永远到不了 `_FS_FAILURES`。该假理由源自一条未经核实的 round-1 verifier 笔记，经本 fixture 传播到代码注释与 commit message，round 2 由一个决定性变异体推翻（同时放宽 `_FS_FAILURES` 并删掉两条 NUL 守卫 → 只有两条 NUL 用例因**构造时机**而红，全部截断/撕裂/结构用例仍绿）。保留这条更正记录：一条靠假理由站着的 rung-1 条款会被未来实现方一次 grep 推翻，进而连正确的结论一起丢掉。
- 构造 **MUST NOT** 触碰文件系统（不建目录、不读源文件）：构造出的 tracker 在 SHUD 尚未启动时也必须是安全的。
- **调用方前置条件（本模块无法自检，MUST 写进模块 docstring 并列入 Known limits）**：`run_dir` MUST 是规范路径——**其任一祖先分量都不得是符号链接**。`safe_fs` 的每个原语都以 `O_DIRECTORY|O_NOFOLLOW` 逐段打开路径，且 `_anchor_for` 会把 containment root 自己也从 `/` 重新走一遍，所以 `containment_root=run_dir` **并不豁免 run_dir 的祖先**；`/scratch → /mnt/...` 这类 HPC 常规布局会让每一次观测都抛 `SafeFilesystemError`，被 §C 步骤 1 归进「本次观测无结果」，整整一轮零捕获、`observed_header_minutes` 保持为空——与「SHUD 从没启动」逐字节相同。本模块**不得**自行 `resolve()`（构造期 resolve 违本节上一条；惰性 resolve 等于把符号链接根接受下来，正好废掉 `safe_fs` 要守的东西），故这是调用方契约，由作业脚本接线侧（组 8 / 后继 issue）保证并落一条 tracked issue。

**C. 观测与捕获（`capture_available()`，单次观测，无 sleep）**

- 签名 `capture_available(self) -> None`；`capture_final(self) -> None` 是它的别名（pin 同名语义：末次观测与常规观测同判据）。
- **本模块不含轮询循环、不含 `time.sleep`、不读轮询间隔**。「反复观测」由调用方（作业脚本侧，归后继 issue）重复调用 `capture_available()` 实现；测试即以「按序调用 N 次」重放覆写序列。这是对 pin 的刻意偏离，同时消掉 pin `_state_checkpoint_poll_seconds`(:3952) 的 `0.01` 内置默认。
- 单次观测的步骤，逐条：
  1. 读 `source_path` 的 header 分钟：文件不存在 / 不是普通文件 / 是符号链接 / 为空 / 非 UTF-8 / 首行不含 minute-time token → **本次观测无结果**，直接返回，MUST NOT 抛错、MUST NOT 记录观测值（SHUD 未启动或正在覆写都会命中这一支）。
  1b. **非有限的 header 分钟（`nan` / `inf` / `-inf`）MUST 同样判为「本次观测无结果」**，MUST NOT 记进 `observed_header_minutes`。理由是硬的：本仓的 `header_minute_time` 与 pin 一样只做裸 `float()`，而 `float("nan")`/`float("inf")` 都解析成功——这正是 pin 的 `_format_header_minute`(:3634) 把非有限判定放在**第一步**的原因。若让它流下去，`round(nan)` 抛 `ValueError`、`round(inf)` 抛 `OverflowError`，两者都会穿透 `capture_available` 外泄给调用方（违 §A「不外泄」与本步「不抛错」），把一次撕裂读升级成整个 tracker 崩溃；记进观测轨迹同样有害——`nan != nan` 使相邻去重永不生效，轨迹被无限追加。撕裂的 `cfg.ic.update` 首行出现 `nan`/`inf` 是**真实可达**的：SHUD 就地覆写时数值区可能半写。
  2. 得到 header 分钟 `m` 时记入 `observed_header_minutes`：**仅当与上一个记录值不同才追加**（pin 同款去重，连续同值不重复记）。这是漏采诊断的唯一现场证据。
  3. 对每个**尚未捕获**的目标小时 `h`：`round(m) == round(h * 60)` 才捕获，否则跳过。
- **相等判据 MUST 是四舍五入后的精确相等**，MUST NOT 用 `<=`/`>=`/区间/容差。这一条直接对应 spec 的「MUST NOT 以更晚时刻的版本冒充 T+12」：`m=1440` 对 `h=12` MUST NOT 命中。
- **不接受 epoch 形式的 header**（pin `_header_minute_matches_checkpoint`(:3963) 的第二支）。理由：本 issue 的产物**保持相对时间头**，绝对定戳归 run-controller 发布路径（compute-loop §9.2）；接受 epoch 形式需要 `start_time` 与 `_ensure_utc`，那是 #9 重戳与 #13.1 发布的面。刻意偏离，效果是 fail closed（epoch 形式的 header 一律判为未命中 → 如实报漏采）。

**D. 单次捕获（`_capture`）与副本校验**

- 目标文件名 MUST 为 `f"{project_name}.f{hour:03d}.cfg.ic.update"`（pin 同款；`f012` 三位补零）。
- 步骤：建 `checkpoint_dir`（`store.safe_fs.ensure_directory_no_follow`，`containment_root=run_dir`）→ 有界读源文件（`read_bytes_limited_no_follow`，上限 `state.MAX_STATE_IC_BYTES`）→ 原子写副本（`atomic_write_bytes_no_follow`，`containment_root=run_dir`）→ **从磁盘回读副本**并做两项校验。
- **超限源文件的处置**：`read_bytes_limited_no_follow` 在超限时返回 `max_bytes + 1` 字节（截断），随后 `state.parse` 对超限必抛 `ValueError`（`parse` 自带 `len(data) > max_bytes` 判定）→ 走「校验失败」支：删副本、保持未捕获。fail closed，且**不是**靠截断后的内容碰巧解析失败——`parse` 的超限判定是显式的。§C 步骤 1 的 header 读同样走有界读（同上限），不得用无界 `read_bytes_no_follow`。
- **本模块 MUST NOT 自带任何 no-follow / 原子写 / unlink 原语**，全部复用 `yd_producer.store.safe_fs`（pin 的 `_copy_staged_file_no_follow`/`_write_staged_bytes`/`_read_staged_bytes`/`_ensure_directory`/`_regular_file_exists`/`unlink_no_follow` 一族在本仓已有对应物，逐一映射写进模块 docstring；无对应物的一个都不新造）。
- 副本的两项校验，**都必须对回读到的字节做**（不得复用写之前那份内存副本——那样校验的是「我读到什么」而不是「盘上是什么」）：
  1. 副本 header 分钟 `round()` 后仍等于 `h * 60`；
  2. `yd_producer.state.parse(<副本字节>)` 不抛 `ValueError`（即「可按原生分段格式读取」——spec 场景「捕获副本校验失败不算成功」的判据）。
- **任一校验失败 → 删除副本（`unlink_no_follow(..., missing_ok=True)`）、该小时保持未捕获、不抛错、正常返回**。语义是「这次撕裂了，下次再来」：SHUD 就地覆写 `cfg.ic.update`，header 已到 720 不代表 body 写完。**后续一次带完整内容的观测 MUST 仍能成功捕获**（撕裂重试，单列一条用例）。
- 结构检查用 `state.parse` 而不是 pin 的 `state_ic_structure_complete`：后者属任务 4.2（issue #9，未落地）。本 issue **不**引入 river 行数等预期值比对——那需要 `expected_river_count`，来源是 work manifest（组 8，未落地）。刻意偏离，记录在案。
- 捕获成功 → 写入 `captured[h] = CapturedCheckpoint(...)`；`CapturedCheckpoint` 是 `frozen=True, kw_only=True` dataclass，字段恰为 `lead_hours: int`、`relative_minute: float`、`path: Path`、`source_name: str`、`checksum: str`（`hashlib.sha256` 的 hexdigest，对副本字节）。**`relative_minute` 是目标值 `float(hour * 60)`，不是观测到的 header 值**（pin 的 `targets[h]["relative_minute"]` 同此）：G6 的 `719.6` 用例正是两者分叉处——那次捕获的 `relative_minute` MUST 是 `720.0`。**不含** `valid_time`/`provenance`/`relative_path`（前者需 `start_time`，后两者是补跑与 manifest 的面）。
- 已捕获的小时在后续观测中 MUST 被跳过（`if hour in captured: continue`）：捕获是**一次性**的，晚到的同值 header MUST NOT 覆盖已捕获副本。

**E. 查询面**

- `captured` -> `Mapping[int, CapturedCheckpoint]`，只读视图（返回 `types.MappingProxyType` 或不可变副本），调用方改不动内部表。
- `missing_hours()` -> `tuple[int, ...]`，升序，恰为「目标集减去已捕获集」。spec「快速覆盖漏采如实报告」的判据即此：覆写序列跳过 720 时 `missing_hours() == (12,)`。
- `observed_header_minutes` -> `tuple[float, ...]`，按观测序、相邻去重。

**F. 对 pin 的刻意偏离（此处即全集，逐条写进模块 docstring）**

1. 目标小时来自 `Config.checkpoint_hours` 显式入参，不解析 manifest；pin 的三路 fail-open 过滤（不可解析 `continue`、≤0 与超 horizon 静默丢、重复静默去重）改为构造期 fail closed。**下游可见的副作用**：不再有 horizon 过滤，超出预报时长的小时不会被静默丢弃，而是成为**永久漏采**并原样喂给 #17 的补跑判定——这是有意的（配置错误应当可见），但 #17 的 fixture 需知道它可能收到一个物理上不可能捕获的小时。
2. `project_name` / `run_dir` 为显式入参，不走 pin `_project_name`(:4114) 的四路 fallback + 下标兜底。**连带不抽 `_safe_path_component`(:2987) 与 `_SAFE_PATH_COMPONENT`(:2984)**（清单第 6 行把二者列在「闭包补项（全部必须随抽取搬运）」里，此处即其明示处置，补上 round 2 发现的记录缺口）：pin 的校验集是「非 str / 空 / 前导 `-` / NUL / `/` / `\` / 任意 `..` 子串 / 正则 `^[A-Za-z0-9_.-]+$` 之外」，本模块按 §B 只拒「空 / `/` / `\` / 恰为 `.` 或 `..` / NUL」，即 `-x`、`a b`、`..foo`、控制字符与任意非 ASCII 都被接受。**这是刻意窄化，不是遗漏**：`project_name` 来自版本化配置而非用户输入，且每一次写入都经 `safe_fs` 在 `run_dir` 下做 containment，无遍历或注入路径；pin 的正则还会连合法的非 ASCII 流域名（如 `yd_黄河`）一起拒掉。**后继实现方（#17 / 接线侧）MUST NOT 假定此处仍有 pin 级的名字校验。**
3. 无轮询循环、无 `sleep` 等待、无轮询间隔配置（连带消掉 pin 的 `0.01` 秒默认）；观测由调用方驱动。**docstring 里 MUST 写作「`sleep` 等待」而不是带点的全名**——G7 的源码机检断言的正是那个带点的全名在整个文件中不出现，写全名会让模块自述与自己的守卫互相打架。
4. 只接受相对分钟 header，不接受 epoch 形式（fail closed）。
5. 结构校验用本仓 `state.parse`，不引入 pin 的 `state_ic_structure_complete` 与 `expected_river_count`（归 #9 / 组 8）。
6. 不写 `state_checkpoints.json`、不记 recovery outcome、不做 `final_ic` 认领（归 #17 与发布路径）；连带本模块零环境变量读取。
7. IO 原语全部复用 `store.safe_fs`，不移植 pin 的 staged-IO 族。
8. 异常类型收敛为单一 `TrackerError`。

（§D 的 `CapturedCheckpoint` 字段裁剪——去掉 pin 的 `valid_time`/`relative_path`/`original_shud_filename`/`checkpoint_filename`/`provenance`——是偏离 6「不写 manifest」的直接后果，不另计一条；「八条即全集」按此口径成立。）

**G. header 分钟读取的落点（裁决修订 R1 后：消费，不移植）**

- tracker MUST `from yd_producer.state import cfg_ic_header_minute_time`（master `state/header_time.py`，pin `state_qc.py:629`），MUST NOT 在本仓任何位置再移植一份「最后一个数值 token 即 minute-time」的实现。这条禁令与 master `state/__init__.py` docstring 对 `state_qc`/`restamp` 下的是同一条。
- `producer/src/yd_producer/state/**` 与 `producer/tests/test_cfg_ic.py` 在本 PR 相对 master **零改动**（机检见 Must-preserve）。
- 「非有限 header 分钟即本次观测无结果」的守卫**属 tracker**，实现在 `_header_minute_of` 的单一出口：`header_time` 只提取 token 值，`nan`/`inf` 是合法 float，值域判定是轮询语义而非 header 语义。
- 本 issue **不**新建 `producer/tests/test_cfg_ic_header.py`（清单 §1「最小测试（cap 5 header）」行仍为 `待落地`，其 `_shift_cfg_ic_time` 重戳用例属 #9 面）。

**G1 header 分钟读取（消费面，不重测）**

- 本 issue 对 `cfg_ic_header_minute_time` **不新增单元用例**：它是 master 既有面，用例在 `producer/tests/test_header_time.py`（issue #22 落地）。裁决修订 R1 撤回了原 G1 的七条参数化断言，连同被撤回的移植一起。
- 保留的义务只有一条，且已由 §G3–§G7 的端到端序列覆盖：tracker 对 3-token 与 4-token（含 lake count）两种 header 形态 MUST 取**最后一个**数值 token；G3 的 `header=360 -> 720 -> 1440` 与 G5 的畸形 header 序列即其见证。
- **行为保持**：`git diff origin/master -- producer/tests/test_cfg_ic.py producer/src/yd_producer/state` 为空，故 `_header_counts` / `parse` / `render` 的既有行为按 master 全绿为准，本 PR 不触碰。

**G2 构造期 fail closed**

- **参数化**：`checkpoint_hours=()`、`(0,)`、`(-12,)`、`(12, 12)` -> 各抛 `TrackerError`，消息含触发原因的可辨识词
- **参数化**：`project_name=""`、`"a/b"`、`"a\\b"`、`"."`、`".."`、`"yd\x00evil"` -> 各抛 `TrackerError`（`"a\\b"` 为 Phase 6.2 审计第 3 条补入：复合守卫的反斜杠操作数原先无见证）
- **参数化（NUL 的两个入口，钉死 §B；去掉任一条 NUL 拒绝后 MUST 变红，且是以 `ValueError` 从 `capture_available()` **外泄**的形态变红，不是断言失败）**：`project_name` 含 NUL；`run_dir` 的某一分量含 NUL -> 各在**构造期**抛 `TrackerError`
- 合法构造后 `run_dir` 下**零新增条目**（构造不碰文件系统；断言 `list(run_dir.iterdir()) == []`）

**G3 正常捕获（spec 场景「正常捕获」）**

- 覆写序列 `header=360 -> 720 -> 1440`，每步后调一次 `capture_available()`：结束时 `missing_hours() == ()`，`captured[12].lead_hours == 12`、`.relative_minute == 720.0`
- 捕获副本路径 == `run_dir/"state_checkpoints"/f"{project}.f012.cfg.ic.update"`，且该文件**存在**
- **副本字节与写入 720 那一版的源字节完全相等**（`read_bytes() == 那一版内容`）——这是「产物保持相对时间头」的判据：副本首行 MUST 仍是 720 的相对分钟头，MUST NOT 被改写成绝对时间
- `captured[12].checksum` == 该字节串的 `sha256().hexdigest()`
- 序列继续到 `1440` 后再调观测：`captured[12]` **不变**（同一 path、同一 checksum）
- **捕获一次性（钉死 `if hour in captured: continue`；去掉该跳过后 MUST 变红）**：在 `720` 成功捕获后，把源文件覆写成**同 header `720` 但 body 已截断**的内容（`state.parse` 必抛的那种）再观测一次 -> `captured[12].checksum` 与副本磁盘字节**逐字节不变**、副本文件**仍存在**、`missing_hours() == ()`。这条不能用「继续到 1440 再观测」代替：`round(1440) != round(720)` 时无论有没有跳过守卫都不会重入 `_capture`，那条用例对该守卫零判别力。缺跳过守卫的真实后果是：第二次同值观测重入 `_capture` → 覆写好副本 → 校验失败 → `unlink` → `captured[12].path` 变成悬空路径，而 `missing_hours()` 仍报空——静默数据丢失伪装成成功
- `capture_final()` 单独驱动一次 `720` 覆写序列，捕获结果与 `capture_available()` 相同（别名不得被实现成 no-op）。两个 tracker 必然在不同 `run_dir` 下，`path` 绝对路径不可能相等，故逐一比较 `lead_hours`/`relative_minute`/`source_name`/`checksum` 与 `path.name`
- `observed_header_minutes == (360.0, 720.0, 1440.0)`

**G4 漏采如实报告（spec 场景「覆写跳过 720」）**

- 覆写序列 `header=360 -> 1440`（跳过 720），每步一次观测 -> `missing_hours() == (12,)`、`captured == {}`
- **冒充守卫**：`1440` 的那次观测 MUST NOT 产生任何 `state_checkpoints/` 下的文件。断言取强的一支——**`checkpoint_dir` 整个不存在**，而不是「不存在或为空」：判据放宽成 `<=` 后 `360` 那一次就会去试捕获、建目录、校验失败删副本，留下一个**空目录**，弱断言对该变异体零判别力。相等判据改成 `<=` 或 `>=` 后本条 MUST 变红
- `observed_header_minutes == (360.0, 1440.0)`：漏采时观测轨迹仍完整留痕（诊断可定位）

**G5 副本校验失败不算成功（spec 场景「捕获副本校验失败不算成功」）**

- 源文件 header 已是 `720` 但 body 截断/缺分段列头（`state.parse` 必抛 `ValueError` 的合成内容）-> 观测后：`missing_hours() == (12,)`、`captured == {}`、且 `state_checkpoints/` 下**无残留副本**（断言目标文件不存在）
- **撕裂重试**：紧接上一条，把源文件覆写成同 header `720` 的**完整**内容再观测一次 -> 捕获成功，`missing_hours() == ()`（证明失败不是终态）
- **「校验对回读字节做」的判别用例**（唯一能把它与「校验内存里那份」区分开的形态）：monkeypatch `safe_fs.atomic_write_bytes_no_follow`，令其落盘**截断**的字节（源文件本身完整）-> 捕获 MUST 失败、副本被删、该小时保持未捕获。改成校验写前的内存副本后本条 MUST 变红
- 源文件不存在 -> 观测不抛错、`captured == {}`、`observed_header_minutes == ()`
- 源文件为空 / 首行无数值 token / 非 UTF-8 字节 -> 同上，不抛错、不记观测值
- **参数化（非有限 header，钉死 §C 步骤 1b；去掉非有限判定后 MUST 变红，且是以 `ValueError`/`OverflowError` 外泄的形态变红）**：首行为 `3988\tnan`、`3988\tinf`、`3988\t-inf` -> 三条各自：观测**不抛任何异常**、`captured == {}`、`observed_header_minutes == ()`
- **参数化（`safe_fs` 真会抛的三条路径，钉死 §A「不外泄」与 §C 步骤 1 的静默返回）**：`source_path` 是指向合法 `cfg.ic` 的符号链接（`open_file_no_follow` 抛 `SafeFilesystemError`）；`source_path` 是目录；`run_dir` 整个不存在（`_open_parent_dir(create=False)` 抛 `FileNotFoundError`，这是 SHUD 尚未建目录时的真实状态）-> 三条各自：观测**不抛任何异常**、`captured == {}`、`observed_header_minutes == ()`

**G6 成功路径输入归一化（常驻轴）**

- `header=719.6` -> 命中 `h=12`（`round()` 存在的 oracle；去掉 `round()` 后 MUST 变红）
- 连续两次观测同一 `header=360` -> `observed_header_minutes == (360.0,)`（相邻去重；去掉去重后 MUST 变红）
- `header=360 -> 720 -> 360` -> `observed_header_minutes == (360.0, 720.0, 360.0)`（**只**去重相邻，非全局去重；改成 `set`/全局去重后 MUST 变红）
- `checkpoint_hours=(5,)` -> 文件名为 `...f005.cfg.ic.update`（`:03d` 补零的 oracle；改成 `{hour}` 后 MUST 变红）

**G7 结构、只读性与模块自述**

- `captured` 返回值上执行 `__setitem__` MUST 抛（`TypeError`）：内部表不可从外部改写
- `checkpoint_hours=(24, 12)` -> `missing_hours() == (12, 24)`（升序，与入参书写序无关）
- `isinstance(tracker.source_path, Path)` 且 `== run_dir / f"{project}.cfg.ic.update"`；`checkpoint_dir == run_dir / "state_checkpoints"`
- **构造签名结构钉（§B「三者均 keyword-only」；去掉 `__init__` 的 `*` 后 MUST 变红）**：`inspect.signature(CheckpointTracker.__init__).parameters[n].kind is inspect.Parameter.KEYWORD_ONLY`，`n` 取 `run_dir`/`project_name`/`checkpoint_hours` 三者（先例：`producer/tests/test_slurm.py` 承 #10 evidence 的同款断言）
- **`CapturedCheckpoint` 的 dataclass 形态（§D；`frozen=True→False` 或 `kw_only=True→False` 后各 MUST 变红）**：对已捕获记录赋值抛 `FrozenInstanceError`（`MappingProxyType` 只挡 `__setitem__`，挡不住成员改写，故只读性用例覆盖不到这一半）；位置构造 `CapturedCheckpoint(12, 720.0, path, "s", "c")` 抛 `TypeError`
- **docstring 自述机检**（precedent：`test_cfg_ic.py:718` 按条数钉死偏离清单）：`checkpoint_tracker.__doc__` 中 §F 的八条偏离**逐条编号存在**（断言 `"\n1. "` … `"\n8. "` 均在，且 `"\n9. "` 不在——「此处即全集」是可机检的声明，不是修辞），且含 §D 要求的 pin→`safe_fs` 原语映射表
- 源码机检：`checkpoint_tracker.py` 文本中不出现 `time.sleep`（零轮询循环，对应 §F 偏离 3；docstring 按 §F 偏离 3 的措辞约定回避该字面量）。**环境读取一项不写进本文件的断言**：`tracker/` 整个包已由 `test_snapshot_provenance.py` 的 DB-free 扫描覆盖（`FORBIDDEN_SURFACES` 含 `os.getenv`/`os.environ`），本 issue 若在测试里写出这两个字面量，反而会让 `test_checkpoint_tracker.py` **自己**成为扫描集里的命中行而变红——该文件是清单登记目标，扫描集不看 `落地状态`

**G8 捕获阶段的实现级 MUST（round 1 新增；本节整体是「不沿 spec Scenario 反推」那条枚举规则的产物）**

- **源在两次读之间前进（钉死 §D 校验 1，即副本 header 复检；删掉 `_copy_is_intact` 的 header 合取后 MUST 变红）**：`capture_available` 已按 header `720` 命中，但 `_capture` 重读源文件之前源被覆写成**另一份完全合法**的 `1440` 内容 -> 该小时保持未捕获、`state_checkpoints/` 下无该文件。**这条不能用既有回读用例代替**：那条落盘的是**截断**内容，`state.parse` 先抛错，header 合取从来不是判别项。缺它的后果正是 spec 禁止的「以更晚时刻的版本冒充 T+12」——变异后 `f012` 会逐字节持有 1440 的 body 且 `missing_hours()` 报空。
- **捕获段的异常收敛（§A「不外泄」在捕获阶段的对偶；把 `_capture` 的 `except _FS_FAILURES` 收窄后 MUST 变红）**：header 已命中之后的某一步 FS 操作失败——例如 `run_dir` 下存在一个名为 `state_checkpoints` 的**普通文件**占位 -> `capture_available()` **不抛任何异常**、该小时保持未捕获。G5 的三条敌意形态全部在 `_read_header_minute` 里就返回了，**没有一条进入 `_capture`**，故捕获段的 `try` 在 round 1 时零见证。
- **超限源文件（§D 超限处置 + §C 步骤 1「不得用无界读」；把源读、回读、`state.parse` 三处上限同时放开后 MUST 变红）**：一份**结构合法且 > `MAX_STATE_IC_BYTES`** 的源文件 -> 该小时未捕获、无残留副本。三处上限**各自**放开都是语义等价（只有资源放大，无契约可见差异），故本条 MUST 同时放开三处才具判别力；实测生成该源文件在进程内数秒、原码路径 RSS 峰值约 575 MB，成本可接受。

**G9 捕获阶段不变式面清单（round 2 pattern escalation 的类 A 纠正动作；清单本身即产物）**

**不变式（closure prompt）**：捕获阶段的**每一个**复合守卫的**每一个操作数**、每一个异常元组的**每一个成员**、每一个 `is None` 身份判定、每一个传给 `safe_fs` 的关键字实参、以及共享读取器的每一个解析维度，**要么有一个「改坏即变红」的见证，要么有一条书面的等价理由**。「这一段有测试」不算数；「这个操作数有测试」才算。

**为什么是清单重构而不是第三次采样**：round 1 补了三个点；round 2 的两个存活体分别落在 round 1 所钉那个布尔的**另一个操作数**（F1）与那个异常元组的**另一个成员**（F2）；verifier 顺手九个变异体的点查又找出**第三个**存活（`capture_available` 的 `is None` → 真值判定，经典 falsy-zero）。同一形态被两次采样连续漏掉，第三次采样没有理由更好。本仓已有同款先例：#11 的同形补丁打到第三次才发现整轴缺席。

**范围（有界、可穷尽）**：`_capture`、`_copy_is_intact`、`_discard`、`capture_available` 四个函数，加 `_read_header_minute`、`_header_minute_of` 两个共享读取器。**实现方 MUST 在测试文件里落一张结账表**（注释或 docstring 形式均可），逐单元给出「使其变红的用例名」或「等价理由 + 依据」，两者必居其一，不得留空：

| 轴 | 单元 |
|---|---|
| 1. 异常元组成员 × except 站点 | `_FS_FAILURES` 的两个成员 × `_capture`/`_discard`/`_read_header_minute` 三站点 = 6 格；另加 `_copy_is_intact` 的 `except ValueError` 与 `_header_minute_of` 的 `except UnicodeDecodeError` |
| 2. 布尔操作数（**逐操作数**独立） | `_copy_is_intact` 的 header 判定（2）、`_header_minute_of` 的返回判定（2）、`capture_available` 的相邻去重条件（2） |
| 3. `is None` → 真值判定 | `capture_available` 的 header 判定、`_copy_is_intact` 的、`_header_minute_of` 的 |
| 4. `safe_fs` 关键字实参 | `containment_root` ×**6** 调用点（建目录 / 源读 / 原子写 / 回读 / `unlink` / header 读）、`max_bytes` ×**3** 调用点（源读 / 回读 / header 读）、`missing_ok` ×1。**计数按代码实测，初稿写的 4 与 2 是错的**（round 2 结账时由实现方指出，按「完备性声明必须可机检」纪律更正） |
| 5. 共享读取器的解析维度 | 分词（`split()`）、行选择（`lines[0]`）、`splitlines()`、`decode`、以及取首行前的 `if not lines` 空表守卫。**`splitlines()` 一格的判定口径**：变异为 `split("\n")` 或 `splitlines(keepends=True)` 均可，两者都因 `str.split()` 吃掉行尾符而等价——写明口径是为了让审计者与实现方对同一格得出同一结论 |

**已知的既有结账**（可直接引用，不必重跑）：round 1 判定三处大小上界**各自单独**放开为等价（只有资源放大，无契约可见差异，故 §G8 第三条要求三处同时放开）、`missing_ok=True→False` 为等价（产生的 `FileNotFoundError` 是 `OSError`，被 `_discard` 自己的 `except` 吞掉）；round 2 判定两处 `containment_root` 变体为等价（目标自构造、不可判别）。轴 5 的「行选择」已作为 cand-14 记入 Known limits，按 DEFER 结账。

**round 2 时已知缺见证的三格**（= F1/F2/F3，MUST 各补见证）：

- 轴 2：`_copy_is_intact` 的 `header_minute is None` 析取。删掉后**回读副本** header 不可读时漏 `TypeError`（`_copy_is_intact` 在 `_capture` 的 `try` **之外**）。三种域内见证：副本落成零字节 / 非 UTF-8 / 非有限 header——一条参数化即可闭合。MUST 以**异常外泄**形态变红。
- 轴 1：`_FS_FAILURES` 的 `OSError` 半在 `_capture` 与 `_discard` **两站点**均无见证（对照：`_read_header_minute` 两半都有）。**每站点各需一个见证，且失败必须是朴素 `OSError`**：`_capture` 用「源文件在两次读之间被 unlink」（SHUD 的 rename/unlink-in-place，只注入时机不注入错误，与 §G8 第一条同款钩子），`_discard` 用「unlink 抛 `PermissionError`」。把该站点收窄成只接 `SafeFilesystemError` 时 MUST 各自变红。注意现有的两个 RED 单元格由**同一个测试**产出——那是采样的签名，不是扫描。
- 轴 5：整个 tracker 套的载荷全是空格分隔，而本仓自己的 `producer/tests/cfg_ic_fixtures.py` 写明真实 native `cfg.ic` 是 **Tab 分隔**（`test_cfg_ic.py` 也构造真实 tab 载荷）。`split()` → `split(' ')` 存活；仅有的三处 tab 字节都在「无结果」用例里，变异下**因错误理由通过**。MUST 补一条**成功捕获**路径由 tab 分隔载荷驱动，使全空白分词有正向见证。

**Phase 6.2 强制审计**：本节落地后，由**未参与本 PR 任何 lens** 的独立审计者按上表逐单元复核结账属实，并对随机抽取的若干单元自造变异体验证。审计不通过即视为纠正动作未完成。

**Phase 6.2 审计结论（2026-08-28，独立审计者，57 个变异体，对 head `9322482` 干净树重跑）：不通过，三条 MUST 修复**

1. **轴 4 `max_bytes` 行的结账尾句为假**（`test_checkpoint_tracker.py` 结账表）。表写「三处同时放开 -> `test_oversize_source_is_not_captured`」，但轴 4 的「三处」按本节定义是**源读 / 回读 / header 读**三个 `safe_fs` 关键字实参；审计者实测三处同时 ×100 **全绿**。真正的红是 §G8 第三条那一组：**源读 / 回读 / `state.parse` 自带上限**（`state/cfg_ic.py:166`，不是 `safe_fs` 的 kwarg，不属轴 4 单元）。MUST 改成后者措辞并点明 `state.parse` 上限不属轴 4。逐单元记账（三处各自单独放开为等价）经实测属实，只有尾句的交叉引用是假的——这正是本节 cand-15「转述即核实」同形，故判不通过而非 Note。
2. **结账表的数目对不上**（「完备性声明必须可机检」同形，cand-18 同形）。实现方报「25 格 = 21 见证 + 4 等价」「23 个变异体、21 红 4 绿」；审计者实点为 **19 见证 + 5 等价 + 1 DEFER = 25 行**，且 21+4=25≠23。MUST 按实际重报，并在表头写明计数口径（**按表的行数**，与轴 4「10 个调用点单元按 3 行分组登记」的区别要写出来，否则同一张表有两个总数）。
3. **轴 2 的兄弟操作数在 `__init__` 一侧漏了一格**（审计者顺手探到，本 issue 内闭合，不外派）：`checkpoint_tracker.py` 的 `"/" in project_name or "\\" in project_name` 复合守卫，第二个操作数**无见证**——删掉后全量套件纯绿。§G2 的参数化 `["", "a/b", ".", ".."]` 没有反斜杠一例。MUST 补 `"a\\b"` 一例（POSIX 下 `\\` 非分隔符，该操作数是对 Windows 的防御性收窄，用户可见影响低，但 closure prompt 的纪律要求它有见证）。同时 MUST 把 `__init__` 的复合守卫纳入结账表——本节原「范围」四函数不含 `__init__`，这是**范围本身的缺口**，如实记为本轮的第三个同形复发点。

**审计核实属实的部分**（不必重跑）：枚举完整性无缺格（审计者独立按五轴穷尽为 32 单元 / 25 行，与表逐一对上）；13 个 RED 见证形态逐条复现（含「`_FS_FAILURES` 两成员各自判别，非共用一条测试」与「删相邻去重前件红 23 条」）；五组等价理由成立，其中 `containment_root` 六处**各自单独**删除审计者补跑了六个变异体（表只给了「六处同时」），六个全绿，结论比表更强。

**审计 Note（不加权，但 MUST 修文字）**：`splitlines()` 等价理由写窄了——对**单独 `\r`（CR-only 行尾）**不成立（`split("\n")` 会把整份文件并成一行，取到的是全文最后一个数值 token）。结论在声明域内（SHUD 于 Linux 写 `\n`）仍成立，MUST 把域写进理由。

**复审范围**：修完只需复核轴 4 那一格（重跑 `a4-maxbytes-*` 三个单站点 + `g8-three-limits`）与新增的 `__init__` 反斜杠格；其余 24 行本次已实测封账。


**事件后对账清单（round 2 pattern escalation 的类 B 纠正动作，本 issue 起常驻）**：编排者把同一条声明扇出到 fixture / 清单 / PR body / 代码注释 / commit message 五处，而事件改变事实基准时无人重走依赖件——round 2 的五条 record-fidelity FIX_NOW 是同一机制的五个出口，不是五次疏漏。下列事件各触发一次**依赖件重走**：

- **落地任何 pin 符号** -> 重走清单 §1 **全部**行的抽取集与反重复条款，不只本 issue 动过的行（cand-16 正是漏了第 5 行：它仍把已落地的两个 header 符号记为「待落地、归 #9」，会让 #9 再移植一份，恰是本 issue 越界所要防的双权威）
- **任何代码提交** -> 重走 PR body 的行数、用例数、文件清单、零 diff 声明
- **任何 fixture 修订** -> 重走 PR body 的 `偏离记录` 与 Known limits
- **任何 DEFER/DISCARD 裁决** -> fixture 与 PR body 的 Known limits **条数与归属必须一致**（cand-17：fixture 八条、body 六条，漏掉的恰是三条带下游义务的）

另两条常驻纪律：

- **转述即核实**：把任何 reviewer/verifier 的**机制性**声明写进 fixture 成为 MUST/MUST NOT 之前，编排者 MUST 自行验证该机制可达（读控制流或跑一个变异体）。cand-15 的代价是一条 rung-1 条款靠假理由站着。
- **完备性声明必须可机检**：凡写「此处即全集」「全部必须随抽取搬运」，MUST 配一条按条数或按集合比对的断言。cand-18 与 round 1 的 cand-01 同形——自称完备的枚举其实不完备。

**变异证明要求**（承 issue #11 的方法债，brief 必带）：复制 `producer/` 到**唯一命名**的 scratch 目录时 `rsync --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache'`；**MUST NOT 在 scratch 副本里跑 `uv run`**（会把共享可编辑安装的 `.pth` 重新指回 worktree 源码，全部变异体假存活），用 `uv run --no-project --with pytest` 并显式 `PYTHONPATH=<scratch>/producer/src`；跑前断言 `yd_producer.__file__` 落在 scratch 内；每个变异体之间 `PYTHONDONTWRITEBYTECODE=1` 并清 `__pycache__`；先用一个必然变红的控制变异校准，校准失败如实说并换一个。

**Known limits（round 1 verifier 裁定为 DEFER/DISCARD 的项，逐条记录归属）**：

- **`run_dir` 的祖先若含符号链接则整轮零捕获**（cand-03，CONFIRMED/DEFER）：见 §B 的调用方前置条件。归作业脚本接线侧，另落 tracked issue。fail closed —— `missing_hours()` 仍诚实，漏采会驱动 #17 补跑，同一个 `run_dir` 上再失败即「整轮失败、不写 DONE」，是响亮的控制器边界失败而非静默坏数据。
- **重启后同 `run_dir` 会删掉已验证副本**（cand-04，CONFIRMED/DEFER #17）：一次性守卫只在**实例内**（`self._captured` 是内存态）。#17 落持久化半时 MUST 显式裁决 `run_dir` 能否跨 attempt 复用——要么禁止复用，要么构造时从 `state_checkpoints/` 回填 `_captured`。
- **撕裂副本可能停在规范文件名上**（cand-08，PLAUSIBLE/DEFER #17）：`_discard` 的 unlink 失败、或作业在原子写与校验之间被 walltime 杀掉（这个窗口无条件且常规），都会把未验证字节留在 `state_checkpoints/<project>.fNNN.cfg.ic.update`。本模块 API 仍诚实（`captured` 为空）。#17 MUST NOT 把「该文件名存在」当作已验证捕获，只信 `captured` 记录与其 checksum。
- **去掉预报时长过滤后，物理上不可能的小时会变成永久漏采**（cand-02，PLAUSIBLE/DEFER #17）：`Config` 不做值域校验（归 #32），`checkpoint_hours = [720]` 这类时/分混淆能穿过装载器与本模块构造器。#17 的 fixture MUST 为这类目标定义补跑行为。
- **`lines[0]` 与 `cfg_ic.parse` 的「首个非空行」对「header 行」的定义分歧**（cand-14，PLAUSIBLE/DEFER M4）：本实现与 pin `_read_cfg_ic_header_minute`(:3618) 逐字一致，且 §C 步骤 1 明文如此，两条现存锚点都支持当前行为。M4 首次真跑时 MUST 核对真实 `cfg.ic.update` 的**第一物理行**就是 header。
- **不设产物 mode，权限随 umask**（cand-07，CONFIRMED/DISCARD）：命中本 fixture 自己的 rung-1 否定锚点「Auth / permissions：本模块不设 mode」，且 `store/object_store.py` 同形，属仓库级约定而非本 PR 回归。
- **header 读把整份有界内容 decode + splitlines 只取首行**（cand-05，CONFIRMED/DISCARD）：64 MiB 上界下峰值约 4.7 倍线性放大。`cfg_ic.parse` 在捕获路径上做同样的事、同一个上界，属仓库既有模式；只修轮询侧是化妆。
- **checksum 取 `copied` 而非 `payload` 在声明域内不可判别**（cand-13，PLAUSIBLE/DISCARD）：原子写 + 私有落点 + 单写者使 `copied == payload` 在每条可达路径上恒成立；真正的部分写会先被 §D 两项校验拦下，走不到 checksum。
- **epoch 形式 header 的 M4 具体核验钩子**（cand-19，CONFIRMED/FIX_NOW）：偏离 4「只认相对分钟」的正当性**只**建立在时间线证据上——`docs/compute-loop-design.md` 在本 PR 之前就已声明 `cfg.ic.update` 的 header 是模型相对分钟，且 spec 的收窄早于实现提交 26 分钟。它**不**建立在 pin 的行为上：round 2 直读 pin 控制流确认，`capture_available`(:3717-3736) 把 `_header_minute_matches_checkpoint`(:3963-3974) 的**两支都无条件**用在同一个 `<project>.cfg.ic.update` 上，分支注释只是归因不是守卫；而 yd 自己的初态正是 epoch 定戳的（pin `_shift_cfg_ic_time`(:3653) 在求解前把绝对分钟写进 header），所以「SHUD 把初态的时间基带进 update 文件」是**默认生产拓扑**而非异常。**M4 首次真跑 MUST 核验第一份真实 `cfg.ic.update` 的 header 是相对分钟形式**；若为 epoch 形式则每轮永久漏采（fail closed 且响亮，但总量为零），偏离 4 MUST 重新裁决。此处不接受通用的「真实 SHUD 行为归 M4」一句——它不会被解析成这一项具体检查（同类先例：cand-14 已按此标准给了自己的具体钩子）。

**Non-goals（本 issue 明示不做）**：漏采补跑（#17）、轮询循环与作业脚本接线、`state_checkpoints.json` 落盘、绝对 T+12 定戳（#9 重戳 + #13.1 发布）、river 行数等结构检查（#9）、work manifest 契约（组 8）、真实 SHUD 行为（M4）。

## 10. prepare-variants：变体与几何

- [x] 10.1 引入几何依赖（pyshp/pyproj/shapely）并 `uv lock`，构造带自定义 Albers `.prj` 的合成 shapefile 基线 fixture，实现 `.prj` 解析与重投影工具，CI 绿
- [x] 10.2 实现 `rivers.geojson`（`reach_id`=DBF Index、数量一致）与 `boundary.geojson`（单元合并边界）生成，落点 `input/viewer/`
- [ ] 10.3 实现 prepare 编排：拒绝覆盖检查 → 薄外壳按源两次调用 builder（记录型假 builder 断言两次入参 source/grid 不同、输出分别落 `yd_gfs`/`yd_ifs`）→ 变体 reach 数等于 `reach_count` 校验 → 提交到 `input/models/` 与 `input/viewer/` → scratch 清理

依赖：组 1（薄外壳、`reach_count`）
§13.1 归属：prepare
Suggested fixture level: expanded - 需构造带自定义投影的合成 shapefile 基线包
Minimal mergeable slice: 几何依赖与重投影工具（10.1）——依赖+fixture+纯函数独立合并保绿，产物生成与编排为后继

### Issue #18 fixture（任务 10.1）

Fixture level: expanded
Upstream suggested level: expanded（agree）
Repair intensity: medium
Project profile: yd-viewer

Change surface:
- 新增 `producer/src/yd_producer/geometry.py`：`.prj` → CRS 装载、到 EPSG:4326 的重投影、最小 shapefile 读取，公开异常 `GeometryError`
- 新增 `producer/tests/geometry_fixtures.py`：**程序化**合成基线 shapefile 生成器（river 折线 + domain 面 + 自定义 Albers `.prj`），供本 issue 与后继 10.2/10.3 复用
- 新增 `producer/tests/test_geometry.py`
- `producer/pyproject.toml`：`dependencies` 加入 `pyshp`/`pyproj`/`shapely`；`producer/uv.lock` 同步提交
- 不触碰 CLI 入口、config 装载器、任何 forcing/state/controller 模块

Must preserve:
- 现有 `tests/test_smoke.py` 与（若已合并）`tests/test_config.py` 继续通过
- `producer/src/yd_producer/config.py` 的 `dependencies` 中立性：config 装载器仍只用 stdlib，本 issue 新增的三个依赖不得被 config 模块 import
- CI 四个 job 全绿；`uv sync --frozen` 无 lock drift

Must add/change:
- `load_prj_crs(prj_path)`：读取 shapefile `.prj` 旁文件的 WKT 并构造 `pyproj.CRS`；文件缺失、为空、WKT 不可解析一律抛 `GeometryError` 且消息含该路径，不回退到任何默认 CRS（fail closed；design.md §11「现场值不得在代码中猜测」的同一纪律）
- `to_wgs84_transformer(src_crs)`：构造到 EPSG:4326 的 `pyproj.Transformer`，**必须 `always_xy=True`**（否则 pyproj 对 EPSG:4326 返回 lat/lon 轴序，GeoJSON 要求 lon/lat，轴序反转是本 issue 的头号静默错误）
- `reproject_geometry(geom, transformer)`：把 shapely 几何整体重投影到 EPSG:4326，保持几何类型与环/部件结构不变
- `read_shapefile(shp_path)`：返回 `(crs, [(record_dict, shapely_geom), ...])`。**失败契约与 `.prj` 同纪律**：`.shp`/`.shx`/`.dbf` 任一缺失或内容损坏，一律抛 `GeometryError` 且消息含该路径，不外泄 pyshp 的 `ShapefileException`——本模块对外只有 `GeometryError` 一个公开异常类型。**scope note（对 issue 正文 In Scope 的显式扩展）**：issue 正文只列「`.prj` 解析与重投影工具」，但合成 shapefile 基线 fixture 若无读取端就无法被端到端行使，验收标准「自定义 Albers 坐标经工具重投影后落 EPSG:4326 合理范围」也无从断言；因此本 fixture 把最小只读 shapefile 读取纳入 10.1。读取只做「几何 + DBF 记录 → 内存对象」，`reach_id` 语义、要素数量一致性校验与 GeoJSON 序列化仍属 10.2

Fixture 形态决策（**不提交二进制**）:
- 合成基线以 pyshp 在 `tmp_path` 内**程序化生成**，不向仓库提交 `.shp/.shx/.dbf/.prj` 二进制
- 硬原因：仓库根 `.gitignore` 的 `fixtures/` 无前导斜杠，会匹配任意层级的 `fixtures/` 目录——`producer/tests/fixtures/` 会被静默忽略，本地绿而 CI 红。程序化生成从根上绕开该陷阱，同时让基线参数（Albers 参数、河段数 N、单元数）成为测试可参数化的入参
- 生成器落 `producer/tests/geometry_fixtures.py`，为 10.2/10.3 的共享测试基建
- **生成器 MUST 与被测代码独立**：生成器自行用 `.prj` 的 WKT 构造 `pyproj.Transformer` 做正向投影，MUST NOT 从 `yd_producer.geometry` import 任何 CRS/transformer 构造函数或重投影函数。否则正向与反向共用同一条构造路径，一个漏掉 `always_xy=True` 的错误会在往返中自相抵消，把轴序断言与往返 oracle 一起变成永真式
- **基线几何形态 MUST 覆盖多部件与内环**：river 至少含一条多段（multi-part）折线；domain 至少含一个带 interior ring（洞）的面。真实基线是 7891 个单元的合并边界，带洞多边形与多部件是 10.2 的必然消费形态；只有单环单部件的基线无法证伪"只重投影外环/首部件"的实现

测试 oracle（**禁止手写期望坐标**）:
- 基线的 Albers 坐标不得手工硬编码：先取一组已知 lon/lat 锚点，用 pyproj **正向**投影到自定义 Albers，把结果写进合成 shapefile；再断言工具的重投影能把这些点还原回原始 lon/lat（容差量级 1e-6 度）
- 附加「合理范围」断言：还原坐标落在合法经纬度域（lon ∈ [-180, 180]、lat ∈ [-90, 90]）且等于锚点邻域，而非未投影的米制坐标（后者会远超经纬度域，是轴序/未转换错误的直接判据）
- yd 流域真实经纬度范围本仓文档未记录，**不得猜测**；因此「合理范围」以往返还原 + 合法域表达，不引入虚构的流域边界框

Seams under test:
- `load_prj_crs(path) -> CRS`、`to_wgs84_transformer(crs) -> Transformer`、`reproject_geometry(geom, transformer) -> geom`、`read_shapefile(path) -> (crs, features)`（file→object / object→object 纯函数）
- **上游 seam 缺口（记录为偏离，非重新协商）**：change design.md「Sketch seams under test」列出的 5 条 seam 不含几何层——该清单按「从高到低」只列了控制器/扫描/状态/forcing/tracker 五条主干 seam，几何工具属其下的纯函数基础层（与 config 装载器同层）。本 fixture 就地声明上列 seam，不修改 design.md

Risk packs considered (core):
- Public API / CLI / script entry: selected - `geometry.py` 的函数签名是 10.2/10.3 的消费契约，改名即破坏后继任务
- Config / project setup: not selected - 本 issue 不读 `config.toml`/`local.toml`，无配置面
- File IO / path safety / overwrite: not selected - 只读单个 shapefile 组及其 `.prj` 旁文件，不写、不删、不遍历用户目录；路径来自 prepare 编排（10.3）而非外部不可信输入
- Schema / columns / units / field names: selected - `.prj` WKT 解析、DBF 字段读出、坐标轴序（lon/lat vs lat/lon）与单位（米 vs 度）均为格式面，错配静默产出错误几何
- Auth / permissions / secrets: not selected - 无凭据、无权限判定
- Concurrency / shared state / ordering: not selected - 纯函数，无共享状态
- Resource limits / large input / discovery: not selected - 真实基线为 3988 河段 / 7891 单元的一次性 `prepare` 输入，量级小且非发现式扫描；合成 fixture 更小
- Legacy compatibility / examples: not selected - 首个几何模块，无既有消费者
- Error handling / rollback / partial outputs: selected - `.prj` 缺失/损坏/空必须 fail closed 抛 `GeometryError`，不得回退默认 CRS、不得返回半成品几何
- Release / packaging / dependency compatibility: selected - 本 issue 一半内容即引入 pyshp/pyproj/shapely 并 `uv lock`；design D5 明确禁 GDAL/geopandas；CI ubuntu 上三者须走 manylinux wheel 装成功
- Documentation / migration notes: not selected - 无迁移；契约由本 fixture 与 spec prepare-variants 钉死

Domain packs (from active profile):
- Geospatial / CRS / shapefile sidecars: selected - 本 issue 的全部内容
- Time series / forcing / temporal boundaries: not selected - 无时间面
- 状态链 / warm-start 定戳一致性: not selected - 不读写状态
- NWM 快照溯源与 DB-free 隔离: not selected - 本 issue 无快照代码，几何工具为本仓自写（非 NWM 拷贝），无溯源头要求

Required evidence（每条 input -> expected output）:
- 合成基线生成器产出的 `.prj` -> `load_prj_crs` 返回的 `CRS` 与生成时使用的自定义 Albers 参数一致（投影方法为 Albers Equal Area，标准纬线/中央经线/假东北与生成参数相等）
- `.prj` 文件不存在 -> 抛 `GeometryError`，消息含该路径；不返回任何 CRS
- `.prj` 为空文件 -> 抛 `GeometryError`
- `.prj` 内容为不可解析的垃圾 WKT -> 抛 `GeometryError`（不外泄 pyproj 原生异常类型）
- **往返 oracle：** 已知 lon/lat 锚点正向投到自定义 Albers 写入 shapefile -> `read_shapefile` + `reproject_geometry` 还原坐标与锚点逐点相差 < 1e-6 度
- 合法域断言：还原后全部坐标 lon ∈ [-180, 180] 且 lat ∈ [-90, 90]
- **轴序回归：** 断言还原结果的第一分量是经度、第二分量是纬度（取经纬度差异显著的锚点，使 lon/lat 互换必然使断言失败）——钉死 `always_xy=True`
- 米制坐标未被当作经纬度：重投影**前**的 Albers 坐标绝对值远超 180（生成器保证），断言重投影确实发生而非恒等直通
- 几何结构保持：N 条 river 折线重投影后仍为 N 条折线且每条顶点数不变；domain 面重投影后仍为面且外环顶点数不变
- **多部件/内环结构保持：** 多段折线重投影后 part 数与各 part 顶点数不变；带洞面重投影后 interior ring 数与各内环顶点数不变，且内环仍落在外环内部（`geom.is_valid` 为真）
- **内环/多部件坐标真被转换：** 内环与非首 part 的还原坐标同样落在合法经纬度域并与其锚点相差 < 1e-6 度（只处理外环/首部件的实现会在此留下未转换的米制坐标而失败）
- `.dbf`（或 `.shx`）缺失 -> `read_shapefile` 抛 `GeometryError`，消息含该路径，不外泄 `ShapefileException`
- `.shp` 内容损坏（截断/垃圾字节）-> `read_shapefile` 抛 `GeometryError`
- `read_shapefile` 读出的 DBF 记录含生成器写入的 `Index` 字段且值与生成顺序一致（供 10.2 消费；本 issue 只断言读出正确，不做 `reach_id` 语义）
- `cd producer && uv sync --frozen` -> 退出码 0（pyproject 与 uv.lock 同步提交，无 drift）
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check .` 与 `uv run ruff format --check .` -> 退出码 0
- CI producer job 绿（三个新依赖在 ubuntu-latest + Py3.12 上装成功，无需 apt 额外包）

Non-goals:
- `rivers.geojson` / `boundary.geojson` 生成、`reach_id`=DBF `Index` 语义、要素数量一致性、单元合并边界、落点 `input/viewer/`（任务 10.2）
- prepare 编排：拒绝覆盖检查、薄外壳两次调用 builder、`reach_count` 校验、提交与 scratch 清理（任务 10.3）
- 真实外部基线模型包的读取与其现场路径（M4；`.gitignore` 的 `fixtures/` 即为该外部包预留）
- 数值/几何精度的最终验证——合成 fixture 只验结构与管线，真实几何落点归 M4/M5 receipt（design.md §10 M2 门禁口径）
- 不引入 GDAL/geopandas/Fiona（design D5 与 products-contract §6 硬约束）

Review focus:
- `always_xy=True` 是否存在且被测试钉死；输出坐标是否确为 (lon, lat)
- `.prj` 的三条失败路径（缺失/空/垃圾）是否全部收敛到 `GeometryError`，是否存在任何默认 CRS 回退或 `try/except: pass`
- 测试期望坐标是否由 pyproj 正向投影生成而非手写常数（手写常数即把工具自身当 oracle）
- 是否向仓库提交了 shapefile 二进制（应为零二进制，全部程序化生成）
- `pyproject.toml` 三个依赖是否落在 `[project].dependencies` 运行依赖（而非 dev group），`uv.lock` 是否同步
- 是否越界实现了 10.2/10.3 的内容（GeoJSON 序列化、边界合并、编排）
- 重投影是否对所有几何类型逐部件/逐环处理，而非只处理外环或首个部件

### Issue #19 fixture（任务 10.2）

Fixture level: expanded
Upstream suggested level: expanded（agree）
Repair intensity: high
Project profile: yd-viewer

Change surface:
- 扩展 `producer/src/yd_producer/geometry.py`：新增 `build_rivers_geojson` / `build_boundary_geojson` / `write_viewer_geojson` 三个公开函数；`GeometryError` 仍是模块唯一公开异常
- 扩展 `producer/tests/geometry_fixtures.py`，三处（穷举）：
  - **单元相邻布局**：新增关键字参数使相邻单元沿**经度方向**共边（共享边为经线段），令合并边界由构造已知；默认布局（互不相接）不变
  - **故障 DBF 图层**：现 `_write_layer` 把字段写死 `writer.field("Index", "N", 10, 0)`、索引写死 `range(1, N+1)`，无法生成「缺 `Index` 字段」「`Index` 重复」「`Index` 为 C 型文本非数字」三种图层；新增关键字参数放开**字段名 / 字段类型 / 显式 index 值序列**三者（默认值保持现行为）
  - **越域坐标图层**：直接写入源 CRS 的原始米制坐标（issue #19 评论实测的 `1e12` 量级），**绕过锚点正向投影与 `METRIC_GUARD`**，并给该要素一个已知 `Index`
  - 三处扩展同受 `geometry_fixtures.py` 既有独立性硬约束：MUST NOT 从 `yd_producer.geometry` import 任何 CRS/transformer 构造或重投影函数
- 扩展 `producer/tests/test_geometry.py`
- 不新增依赖、不改 `pyproject.toml`/`uv.lock`；不触碰 CLI 入口、config、forcing/state/controller

Must preserve:
- `load_prj_crs` / `to_wgs84_transformer` / `reproject_geometry` / `read_shapefile` 的签名与失败契约（10.1 已钉死，10.3 待消费）不变
- `geometry_fixtures.py` 的独立性硬约束：生成器 MUST NOT 从 `yd_producer.geometry` import 任何 CRS/transformer 构造或重投影函数；新增的相邻布局与越域图层同受此约束
- 生成器现有默认行为保持不变（`river_count=3`、`unit_count=2`、现默认单元间距、DBF 字段定义 `("Index", "N", 10, 0)` 与 `range(1, N+1)` 的索引序列），#18 既有测试逐条继续通过；相邻布局、故障 DBF、越域图层一律以**新增关键字参数或新增函数**引入
- `config.py` 的 stdlib 中立性；CI 四个 job 全绿

Must add/change:
- `build_rivers_geojson(shp_path) -> dict`：读基线河网 shapefile，返回 EPSG:4326 的 GeoJSON `FeatureCollection`
  - 要素数量与基线河段数严格相等，**顺序与 DBF 记录顺序一致**（确定性输出，逐次运行字节一致）
  - 每个要素 `properties` **只含** `reach_id`，取自 DBF `Index` 字段并转 `int`；不透传其它 DBF 字段（viewer 只消费 `reach_id`，products-contract §6）
  - DBF 缺 `Index` 字段、`Index` 值非整数、或 `Index` 在图层内重复 -> 抛 `GeometryError` 且消息点名该图层路径与出问题的记录序号（fail closed：重复 `reach_id` 会让 viewer 的 DAT 列定位静默错位）
  - 0 要素的合法图层 -> 返回 0 要素的 `FeatureCollection`，**不报错**（`reach_count` 一致性校验属 10.3）
- `build_boundary_geojson(shp_path) -> dict`：读 domain 单元面图层，返回 EPSG:4326 的 `FeatureCollection`，**恰含 1 个要素**（合并边界），`properties` 为空对象 `{}`
  - 合并顺序钉死：**先在基线源投影 CRS 内 `unary_union` 合并，再对合并结果整体重投影**（等积 Albers 平面内合并；在经纬度域合并会让共享边在角度空间不严格重合而留下缝隙/线状伪影）
  - 单元共边处的内部边界 MUST 被溶解：合并结果内不得出现原单元的公共边
  - 内环（洞）MUST 保留
  - 单元互不相接时结果为 `MultiPolygon`（不额外要求连通性——spec 与 products-contract 均未要求，不凭空发明约束）
  - 0 要素的 domain 图层 -> 抛 `GeometryError` 且消息含该路径（无单元即无边界，不返回空要素集）
- 两个 builder 的输出 MUST 满足 RFC 7946 环向（**外环逆时针、内环顺时针**）。shapefile 约定与之相反（外环顺时针），且实测 `unary_union` 输出为顺时针，故这是一条必须显式做、且可被证伪的转换
- 所有坐标 MUST 为有限值：序列化 MUST 用 `json.dumps(..., allow_nan=False)`（或等价的逐要素有限性守卫），非有限坐标 -> 抛 `GeometryError` 且**点名该要素**（河网点名 `reach_id`，边界点名图层路径），**不写出任何含裸 `Infinity`/`NaN` 的文件**
  - 硬原因（issue #19 评论实测，pyproj 3.7.2）：`reproject_geometry` 走 `shapely_transform(transformer.transform, geom)`，pyproj 默认 `errcheck=False`，投影域外顶点静默变 `inf`；`json.dumps` 默认发裸 `Infinity`，Python 的 `json.loads` 接受而浏览器 `JSON.parse` 拒绝——一个坏顶点会让 viewer 整层河网变白
- `write_viewer_geojson(*, rivers_shp, domain_shp, out_dir) -> tuple[Path, Path]`：把两份 FeatureCollection 写成 `out_dir/rivers.geojson` 与 `out_dir/boundary.geojson`，返回二者路径
  - `out_dir` 由调用方给出（10.3 的 scratch 目录），不存在时创建；**本函数不解析 `YD_ROOT`、不做拒绝覆盖检查、不做提交与清理**（均属 10.3）
  - **无部分产物**：两份文档 MUST 先全部构建并序列化成字符串成功后再落盘；任一图层失败时 `out_dir` 内不得留下任何本次写出的文件
  - 编码 UTF-8；`ensure_ascii=False`；坐标不做精度截断（精度策略属后继性能议题，非本 issue）

Seams under test:
- `build_rivers_geojson(path) -> dict`、`build_boundary_geojson(path) -> dict`（file -> 内存 GeoJSON 对象，纯函数）
- `write_viewer_geojson(*, rivers_shp, domain_shp, out_dir) -> (Path, Path)`（唯一写盘 seam，落点由入参给定）
- 上游 seam 缺口同 #18：design.md 的 5 条主干 seam 不含几何层；本 fixture 就地声明，不改 design.md

测试 oracle（**禁止手写期望坐标，禁止用被测库自判**）:
- 期望坐标一律来自 `geometry_fixtures` 的 lon/lat 锚点：生成器正向投影写入 shapefile，测试断言 builder 输出还原回锚点（容差 1e-6 度）
- **该纪律的唯一显式豁免**：越域坐标图层。合法 lon/lat 锚点正向投影只会得到有限 Albers 坐标，非有限回归无法经锚点路径构造，故该图层直写原始米制坐标；它验的是错误路径与失败归属，不做坐标往返断言
- 合并边界的期望形状**由构造已知**：两个**沿经度方向共边**的单元（新增相邻布局参数）合并后外环顶点集 = 两单元外角锚点 ∪ 共享边端点。相邻方向必须是经度方向：共享边端点落在纬线弧上，在 Albers 平面内与两侧外角**不共线**，因而必然是 union 输出的顶点；若改成纬度方向堆叠，共享边端点落在（Albers 内为直线的）经线上，该 oracle 就变成对 GEOS 是否保留共线节点的赌注，min/max lon/lat 等于两单元锚点的极值；**不得在测试内用 shapely 再做一次 union 当期望值**（被测库自判，与 #18 禁止共用 transformer 构造路径同纪律）
- 内部边界溶解的判据取构造已知的点：共享边中点严格落在合并面内部（`covers` 为真且不在 `boundary` 上）
- 环向断言用 shapely 的 `is_ccw` 判定，但期望值（外环 CCW / 内环 CW）由 RFC 7946 给定，非由实现产出反推

Risk packs considered (core):
- Public API / CLI / script entry: selected - 三个新函数是 10.3 编排的消费契约
- Config / project setup: not selected - 不读配置
- File IO / path safety / overwrite: selected - 首次写盘；无部分产物是硬要求（覆盖/发布/清理语义属 10.3）
- Schema / columns / units / field names: selected - GeoJSON 是 viewer 的输入 schema；`reach_id`/DBF `Index`/轴序/环向全在此面
- Auth / permissions / secrets: not selected - 无凭据
- Concurrency / shared state / ordering: not selected - 纯函数 + 单次写出，无共享状态（要素顺序确定性在 schema 面覆盖）
- Resource limits / large input / discovery: not selected - 真实基线 3988 河段 / 7891 单元，一次性 prepare，量级小
- Legacy compatibility / examples: not selected - viewer 尚无既有 GeoJSON 消费实现可破坏
- Error handling / rollback / partial outputs: selected - 非有限坐标、重复/缺失 `Index`、空 domain 均须 fail closed 且不留半成品
- Release / packaging / dependency compatibility: not selected - 不动依赖与 lock
- Documentation / migration notes: not selected - products-contract §6 已描述产物，无需改文档

Domain packs (from active profile):
- Geospatial / CRS / shapefile sidecars: selected - 本 issue 的全部内容
- Time series / forcing / temporal boundaries: not selected - 无时间面
- 状态链 / warm-start 定戳一致性: not selected - 不读写状态
- NWM 快照溯源与 DB-free 隔离: not selected - 无快照代码

Required evidence（每条 input -> expected output）:
- N 条河段的合成基线 -> `rivers.geojson` 含恰 N 个要素，`reach_id` 序列等于生成器写入的 `Index` 序列且顺序一致
- 每个河段要素 `properties` 的键集合恰为 `{"reach_id"}`；`reach_id` 类型为 `int`
- 河网坐标 -> 逐点还原到锚点，差 < 1e-6 度；全部落 lon ∈ [-180,180]、lat ∈ [-90,90]
- 轴序回归：取经纬差异显著的锚点，断言坐标第一分量为经度（lon/lat 互换必然失败）
- 多部件折线 -> GeoJSON 类型为 `MultiLineString`，part 数与各 part 顶点数与基线一致
- DBF 缺 `Index` 字段 -> `GeometryError`，消息含图层路径
- `Index` 重复（同一图层两条记录同值）-> `GeometryError`，消息点名重复值与记录序号；**不写出任何文件**
- `Index` 值非整数（生成器以 C 型文本字段写入非数字值）-> `GeometryError`
- 0 要素河网图层 -> 0 要素 `FeatureCollection`，不报错
- **合并边界（共边单元）** -> `boundary.geojson` 恰 1 个要素，`type` 为 `Polygon`；外环还原坐标集合等于两单元外角锚点 ∪ 共享边端点（差 < 1e-6 度）；min/max lon/lat 等于锚点极值
- 共享边中点严格在合并面内部（内部边界已溶解）
- 带洞单元 -> 合并结果保留恰 1 个内环，其还原坐标与洞锚点一致
- 互不相接的两个单元（生成器默认布局）-> `type` 为 `MultiPolygon`，含 2 个成员
- 环向：`boundary.geojson` 外环 CCW、内环 CW；`rivers.geojson` 不受环向约束
- 0 要素 domain 图层 -> `GeometryError`，消息含该路径
- **非有限坐标：** 含投影域外顶点的河网图层（生成器新增，要素带已知 `Index`）-> `GeometryError`，消息含该要素的 `reach_id`；`out_dir` 内无任何文件；写出的文本中不出现 `Infinity`/`NaN`
- 非有限坐标出现在 domain 图层 -> `GeometryError`，消息含图层路径；无文件残留
- `write_viewer_geojson` 成功路径 -> `out_dir` 下恰有 `rivers.geojson` 与 `boundary.geojson` 两个文件，返回值与之相等；两文件均能被 `json.loads(..., parse_constant=<拒绝>)` 或严格解析器接受
- **无部分产物：** domain 图层损坏而河网合法 -> 抛 `GeometryError` 且 `out_dir` 内不存在 `rivers.geojson`
- `out_dir` 不存在 -> 被创建后写入成功
- 确定性：同一基线连续调用两次，两次输出字节完全一致
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check .` 与 `uv run ruff format --check .` -> 退出码 0
- `openspec validate m2-producer-core --strict --no-interactive` -> 退出码 0
- CI producer job 绿

Invariant Matrix:
- Governing invariant: 写出的两份 GeoJSON MUST 是 RFC 8259 合法（零非有限数）、EPSG:4326 经纬度、`reach_id` 与基线 DBF `Index` 一一对应且数量相等的文档；任何一处失败必须在落盘前抛 `GeometryError` 并点名责任要素/文件，磁盘上不留部分或非法产物
- Source-of-truth identity/contract: 基线 shapefile 的 DBF `Index` 字段（-> `reach_id`）与 `.prj` 自定义 Albers（-> 坐标语义）
- Producers: `geometry.build_rivers_geojson` / `build_boundary_geojson` / `write_viewer_geojson`
- Validators/preflight: `reach_id` 存在性/整数性/唯一性检查；有限坐标守卫（`json.dumps(allow_nan=False)` 或等价）；空 domain 检查
- Storage/cache/query: `out_dir/rivers.geojson`、`out_dir/boundary.geojson`（10.3 再提交到 `YD_ROOT/input/viewer/`）
- Public routes/entrypoints: 无 CLI 面；三个函数由 10.3 的 prepare 编排调用
- Frontend/downstream consumers: viewer 前端按 `reach_id` 关联 DAT 流量列（products-contract §6）；viewer 不读 shapefile、不做投影
- Failure paths/rollback/stale state: 先构建后落盘的两阶段写出；失败时 `out_dir` 无本次产物；不做覆盖判定（10.3）
- Evidence/audit/readiness: `producer/tests/test_geometry.py` 的 GeoJSON 用例组；`geometry_fixtures.py` 的相邻布局与越域图层生成器
- Regression rows:
  - 合法共边基线 -> 2 份文件写出，N 个 `reach_id` 与 `Index` 一一对应，边界为单一 Polygon 且共享边溶解
  - 投影域外顶点 / 重复 `Index` / 空 domain -> `GeometryError` 点名责任对象，`out_dir` 内零文件
  - #18 既有的 `load_prj_crs`/`read_shapefile`/`reproject_geometry` 用例 -> 全部保持通过，签名与失败归属不变

Non-goals:
- prepare 编排：`YD_ROOT` 解析、拒绝覆盖检查、薄外壳两次调用 builder、`reach_count` 一致性校验、提交到 `input/models/`+`input/viewer/`、scratch 清理（任务 10.3）
- #36（`to_wgs84_transformer` 接受 ballpark 基准变换，`transformer.accuracy == -1.0`）与 #37（`.shx` 记录内容未校验）——既有开口，本 issue 不修
- 真实基线的要素数（3988 河段 / 7891 单元）验证（M4 真实数据阶段）
- GeoJSON 坐标精度截断与文件体积优化
- viewer 侧读取与渲染

Review focus:
- 合并边界的期望值是否由构造/锚点给出，而非在测试里再跑一次 shapely union（被测库自判 = 永真式）
- 非有限坐标守卫是否**在落盘前**生效、是否点名具体要素，而非只在文件写完后校验
- 失败路径是否真的零残留：河网先写、domain 后失败的顺序下 `out_dir` 是否干净
- `reach_id` 是否严格来自 DBF `Index` 且保序，重复/缺失是否 fail closed
- 环向转换是否显式实现（`unary_union` 实测输出为顺时针，未转换即违反 RFC 7946）
- 是否越界实现了 10.3 的编排、覆盖检查或 `YD_ROOT` 解析
- 生成器新增能力是否仍与 `yd_producer.geometry` 独立、是否改动了 #18 的默认布局与默认 DBF 字段定义

## 11. init-bootstrap：首态建链

- [ ] 11.1 实现 init 编排：非全新根拒绝守卫、7 天扫描窗定各源首轮（复用 raw-scan）、任一源窗内无完整 cycle 即整体拒绝不写状态（fail closed）、率定末态重戳写首态（复用 state-tools）

依赖：组 3（扫描）、组 4（重戳）
§13.1 归属：无直接行（测试归属见 change design D7）
Suggested fixture level: compact - 复用 raw 目录树与合成状态 fixture
Minimal mergeable slice: atomic - 单一编排函数，拒绝守卫/扫描窗/首态写入共享同一条 init 验证路径，无独立可交付子集

## 12. run-controller（一）：前沿发现与锁

- [x] 12.1 实现严格前沿纯函数：`DONE`/状态文件集合 → 每源待跑 T 或停止原因（全新链、D+12h、状态缺失、时间头不对应 T、raw 缺口、缺轮阻塞）
- [ ] 12.2 实现未提交残留识别与清理重跑判定（保留 T 状态、删更晚状态与半成品）
- [ ] 12.3 实现非阻塞 flock 封装（持有即跳过、覆盖全生命周期），进程内测试跳过语义

依赖：组 1、组 4（12.1 时间头校验读分段 header）
§13.1 归属：控制器（前沿/flock 幂等/raw 缺口）
Suggested fixture level: compact - tmp 目录树表达 DONE/状态组合即可
Minimal mergeable slice: 前沿确定纯函数（12.1）——判定逻辑独立合并保绿，残留清理与锁为后继

### Issue #22 fixture（任务 12.1）

Fixture level: expanded
Upstream suggested level: compact（override：正面命中 `openspec/project-profile.md` 的 domain expanded-triggers `DONE`、`cycle`、前沿/frontier、状态链/warm start——profile 触发词按 `issue-risk-contract.md` 与核心触发词同为强制；另本 issue 需落一个共享 helper 根 `state/header_time.py`）
Repair intensity: high（本函数是 profile 首位风险轴「断链即整链失效」的**唯一执行点**：它决定每源用哪一份状态起跑；且本 issue 落 `state/header_time.py` 这一共享 helper 根，由 #9 的重戳与结构检查复用。适用 `Invariant Matrix`）
Project profile: yd-viewer

**上游契约偏离（consumed not renegotiated，须回流 stage-change-pipeline sizing-retro）**：issue #22 的依赖只列 #2/#8，但验收标准里的「时间头不对应绝对 T 即停」需要 header 时间语义符号（`cfg_ic_header_minute_index` / `cfg_ic_header_shape`），而 `nwm-snapshot-inventory.md:44` 把它们归在 #9（任务 4.3）。缺失的 seam 本 issue 自行补齐（见下方裁决 1），并按核心规则「needed-but-missing seam is a reported deviation」记录在此。

**核心设计裁决（本 fixture 钉死，实现不得自行改写）**：

1. **读侧 header 时间原语落地在本 issue，不注入 fake**。把 pin 的 `cfg_ic_header_minute_index`(`state_qc.py:609`)、`cfg_ic_header_minute_time`(`:629`)、`cfg_ic_header_shape`(`:664`)、`CfgIcHeaderShape`(`:650`) 与其闭包常量 `_VALID_CFG_IC_HEADER_TOKEN_COUNTS`(`:646`) 移植到**新文件** `producer/src/yd_producer/state/header_time.py`，`_as_float` **MUST 从 `state.cfg_ic` 导入**（pin 的 docstring 逐字声明这三个符号与 `_header_counts` 共享「最后一个数值 token 即 minute-time」规则，两份定义即双权威）。这是 `nwm-snapshot-inventory.md:44` 行的**第二次部分落地**（第一次是 #8 的格式层），该行的落地状态注记随本 PR 更新。#9 MUST 从 `header_time` 导入这五个符号，MUST NOT 再移植一份。
   - **不选注入式 seam** 的理由：若把「header 时间是否对应 T」做成调用方传入的 callable，验收 Scenario「时间头不对应 T 即停」退化为「fake 说停就停」的永真式，spec 的 MUST 没有任何用例把守。
   - 不落 `state/state_qc.py`、不动 `state/cfg_ic.py`：后者的 fixture 带逐函数溯源窗口断言与已审的变异套件，改它等于重开 #8 的审核面。
2. **MUST NOT 移植 `_valid_time_from_header_minute`**（`state_cli.py:359`，归 #9）。该函数**刻意接受相对分钟**（`0 <= m <= horizon` 时按 `cycle_time + m` 解释）——对 checkpoint 重戳是对的，对前沿闸门是**错的**：compute-loop §8 与 `specs/run-controller/spec.md` 的判据逐字是「以绝对时间判定」，宽容读法会让一份未重戳的残留 header（如 `720.000000`）在 T=cycle+12h 时被判为「对应 T」而放行，正是断链的入口。本 issue 的判据是自有的绝对时间比较（裁决 3），并把该**刻意不移植**写进模块头。
3. **绝对时间判据钉死**：header shape 有效 → 取 minute token → **先过 `math.isfinite` 闸**（`_as_float` 逐字移植自 pin，`nan`/`inf`/`-inf` 都会被 `float()` 接受并计入数值 token，随后 `round()` 会抛 `ValueError`/`OverflowError`）→ `round(observed_minute) == round(T.timestamp() / 60)` 才算对应 T，否则停。非有限值一律 `HEADER_TIME_MISMATCH`，MUST NOT 以异常逃逸。四舍五入到整分钟是因为 header 的 minute token 是浮点文本（pin 写入侧是 `valid_time.timestamp()/60`，形如 `27000000.000000`）；cycle 间距 12h，±30s 容差不产生歧义。shape 无效（数值 token 数不是 3 或 4）一律停，MUST NOT 退化为「取最后一个数值 token」的宽松读法。
4. **只读 header 行，MUST NOT 全量解析文件体**。结构检查（缺段、行数不符、数值区损坏）是任务 4.2 / #9 的面；本函数对 T 状态只做「存在、可读、header 时间对应 T」三判。读取 MUST 走**只读首行的有界读**：先用已取得的 `st_size` 判超界（`> MAX_STATE_IC_BYTES` 即 `STATE_UNREADABLE`），再分块读到首个非空行为止，累计读入 MUST NOT 超过 `MAX_STATE_IC_BYTES + 1` 字节。MUST NOT「先 `read(MAX+1)` 再 `decode` 再 `splitlines()`」——那样 64 MiB 上界会放大成数百 MiB 常驻（round 1 验证闸门 batch resource-limits cand-04 CONFIRMED/FIX_NOW：实测 63 MiB 短行文件 traced peak 297 MiB / `ru_maxrss` 537 MiB，16 MiB 纯换行文件 traced peak 168 MiB ≈ 10.5x），正好架空 `MAX_STATE_IC_BYTES` 自述的 OOM 保护意图。**候选行本身也 MUST 有界**（round 2 batch resource-and-coverage-2 cand-12 CONFIRMED/FIX_NOW）：字节预算只约束「读了多少」，不约束「首行有多长」——首个 `MAX+1` 字节里没有 `\n` 时（64 MiB 无换行文本，或截断/预分配出来的**全 NUL** 文件——NUL 是合法 UTF-8 且不是 `str.strip()` 的空白，整个文件成为一个巨大的 header 行），`pending`、`bytes(pending)`、`decode()`、`split()` 各自实体化一份文件大小的对象，实测端到端 traced peak 576 MiB / `ru_maxrss` 681 MiB。故新增规则：首个非空行累计超过 `MAX_HEADER_LINE_BYTES`（模块常量，取 64 KiB——原生 header 只有 3–4 个 token，两个数量级余量）仍未遇到 `\n` 时，MUST 立即判 `STATE_UNREADABLE` 并停止读取，MUST NOT 继续累积；跳过前导空行时已丢弃的空白不计入候选行长度。注意这条**改变了可观测行为**：全 NUL 的 64 MiB 状态由 `HEADER_TIME_MISMATCH` 变为 `STATE_UNREADABLE`——两者都停源，方向一致。状态文件的可读性判定 MUST **跟随** symlink（与 `state/cfg_ic.py` 的 `_read_bytes_limited` 调用点注释逐字保留的「刻意不走 no-follow 安全读」同一理由：macOS `/tmp` 本身是 symlink，测试树会被误拒）；只有目录、socket/FIFO、断链 symlink、不可读、非 UTF-8、超界归 `STATE_UNREADABLE`。no-follow 的越界拒绝属删除/发布面，归 #24/#25。
5. **cycle 可见集判据（`states/` 与 `output/` 对称适用）**：条目名为 10 位数字**且**可被 `%Y%m%d%H` 解析（小时不限 00/12——形态与可解析性是唯一的门），`states/<source>/` 侧另需固定后缀 `.cfg.ic`。不满足者对前沿**不可见**，MUST NOT 因此报错停源，也 MUST NOT 抛 `ValueError`。两侧对称是必需的：`output/` 下同样会有 stray 文件、#25 保留窗口清理中断留下的半删目录与 `.DS_Store`。**可见集另加一道可表示性门**：解析出的 cycle MUST 满足 `cycle + 12h` 在 `datetime` 值域内，否则同样判为不可见——`9999123123` 是 10 位数字且 `%Y%m%d%H` 可解析，`datetime(9999,12,31,23) + 12h` 抛 `OverflowError`（round 1 batch error-classification cand-03 CONFIRMED/FIX_NOW，实测逃逸出 `decide_frontier`）。选可见性门而非新增停止原因，是为了保住闭合词表：这类条目在语义上就是「不是合法 cycle」。理由：`specs/run-controller/spec.md:75` 的发布把临时文件 rename 在 `states/<source>/` **目录内**完成，临时名的形态由 #24 定；若前沿对不可解析文件名 fail-closed，一次崩溃的发布会把该源**永久砖化**，与「无 `DONE` 残留必须可干净重跑」直接冲突。残留的**清理**归 #23。
6. **全新链取最早状态文件名**（`spec.md` 的 MUST 逐字：「待跑 T 为 init 写入的最早状态文件名」）。compute-loop §10 另有一句「全新链只允许存在 init 写入的最早状态」，读作前沿侧的 fail-closed 会与 spec 的 MUST 冲突；本 fixture 按 spec 取最早，不把「无 DONE 却有多份状态」判为异常（那是 #23 残留面的判定）。该张力记录在此，路由至 #23 fixture，不在本 PR 处理。
7. **前沿只由 DONE 集合推进**：存在比 T 更晚的状态文件时，待跑 T **仍是** D+12h，MUST NOT 因更晚状态而前进。这一条同时是「崩溃残留恢复」在前沿层的边界证据（清理动作归 #23）。
9. **枚举/探测失败 MUST 与「不存在」分流，且 MUST NOT fail-open**（round 1 batch error-classification cand-01/cand-02 双 CONFIRMED/FIX_NOW，实测：`chmod 0o000 output/<最新 cycle>` 让前沿**倒退**到已 `DONE` 的 cycle 且仍判可跑；`chmod 0o000 states/<source>` 让 `PermissionError` 直接逃出 `decide_frontier`）。统一规则：
   - **只有** `FileNotFoundError` / `NotADirectoryError`（路径确实不存在）才等价于「空集合」；
   - 其余任何 `OSError`（`EACCES`/`EPERM`/`EIO`/`ESTALE`/`ELOOP`…）MUST 判为**无法确定**，停该源并返回新增的第六个停止原因 `DISCOVERY_UNREADABLE`（词表由 5 项扩为 6 项，本轮起为新的闭合词表）；
   - 该规则适用于**每一处**文件系统探测：`output/` 与 `states/<source>/` 的列目录、`DONE` 的普通文件判定、状态文件的存在性/symlink 探测（MUST NOT 用裸 `Path.exists()`/`is_symlink()`——`pathlib` 只吞 `ENOENT/ENOTDIR/EBADF/ELOOP/EINVAL`，`EACCES`/`EIO` 会穿透）；
   - 状态文件**本身**不可读仍归 `STATE_UNREADABLE`（其 docstring 已含「权限拒绝」）；`DISCOVERY_UNREADABLE` 专指**集合无法枚举/条目无法判定**这一层；
   - fail-open 的方向性理由：`states/` 侧判空是 fail-closed（`NO_INITIAL_STATE`），`output/` 侧判空却让链看起来是全新链，把前沿**倒退**到已发布 cycle——products-contract §4.4「重复运行看到 `DONE` 时不覆盖正式产物」的发布侧守卫归 #24 尚未落地，本函数当前是唯一闸门。
10. **注入的 `raw_complete` 的可接受输入域 MUST 写进 docstring**（round 1 batch integration-contract cand-06 CONFIRMED/FIX_NOW）：本函数返回的 T 可能带任意可解析的 cycle 小时（裁决 5 刻意如此），而生产实现 `rawscan.judge` 只对 `config.cycle.hours` 全域（实测 18Z 目标使其在任何文件系统访问之前抛 `ConfigError`，异常穿透 `decide_frontier`）。本 issue owed 的是**声明的前置条件**而非守卫；接线与守卫归 #26，并在组 14 记录该约束。
8. **raw 完整性以注入方式消费**：前沿函数接收「给定 cycle 是否 raw 完整」的可调用判定（`Callable[[datetime], bool]`），不在本 issue 内组装 `Config` 与 raw 目录树。理由：`rawscan.judge` 已由 #6 落地并自带完整用例，前沿层要证明的是**「raw 不完整 → 停在 T、前沿不前进、MUST NOT 跳轮」**这一条控制流。多轮追赶的**顺序执行**归 #26/#27。

Change surface:
- 新增 `producer/src/yd_producer/state/header_time.py`：移植的 header 时间原语（逐函数带 `NWM@8ae9b8f2 packages/common/state_qc.py:<行>` 溯源头）
- 新增 `producer/src/yd_producer/controller.py`：严格前沿纯函数与停止原因词表
- 新增 `producer/tests/test_header_time.py`、`producer/tests/test_controller_frontier.py` 与 tmp 目录树 fixture 构造器
- 更新 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md:44` 的落地状态注记（第二次部分落地：读侧 header 时间原语）

Must preserve:
- 移植的五个符号与 pin 逐字一致（含「最后一个数值 token 即 minute-time」与 3/4 token shape 门）；任何偏离 MUST 在模块头注明
- stdlib-only、零运行时 NWM import、零数据库/scheduler 依赖；不新增依赖、`producer/uv.lock` 不变
- 零写入：本 issue 的两个模块 MUST NOT 创建/修改/删除任何路径（残留清理归 #23、发布归 #24）
- `state/cfg_ic.py` 与 `producer/tests/test_cfg_ic.py` MUST NOT 被修改（#8 已审面）

Must add/change:
- `state/header_time.py`：`cfg_ic_header_minute_index(tokens) -> int | None`、`cfg_ic_header_minute_time(tokens) -> float | None`、`cfg_ic_header_shape(tokens, *, expected_mesh_count=None) -> CfgIcHeaderShape`，加 `CfgIcHeaderShape` 与 `_VALID_CFG_IC_HEADER_TOKEN_COUNTS`（共五个符号）；`_as_float` 从 `cfg_ic` 导入
- `controller.py`：
  - `StopReason` 枚举，**闭合词表**且逐项可区分：`NO_INITIAL_STATE`（无 DONE 且无任何合法状态文件）、`STATE_MISSING`（T 的状态文件不存在）、`STATE_UNREADABLE`（存在但不可读/非普通文件/非 UTF-8/超界）、`HEADER_TIME_MISMATCH`（header shape 无效、minute token 非有限、或时间不对应绝对 T）、`RAW_INCOMPLETE`（T 的 raw 未齐）
  - `FrontierDecision`（frozen dataclass）：`source`、`cycle: datetime | None`（可跑时为待跑 T，停止时 `None`）、`stop_reason: StopReason | None`、`detail: str`（含具体路径/观测值，供运行报告与日志）；`cycle` 与 `stop_reason` **恰有一个**非 `None`
  - `decide_frontier(*, yd_root: Path, source: str, raw_complete: Callable[[datetime], bool]) -> FrontierDecision`：`DONE` 集合来自 `output/<cycle_id>/<source>/DONE`（普通文件），状态集合来自 `states/<source>/<cycle_id>.cfg.ic`
  - 判定顺序固定（compute-loop §10 逐条）：DONE 定 D → T=D+12h（无 DONE 则取最早状态名）→ 状态存在/可读/header 时间 → raw 完整性
  - 时间一律 UTC aware；`cycle_id` 解析用 `datetime.strptime(..., "%Y%m%d%H").replace(tzinfo=UTC)`，解析失败即「不可见」（裁决 5），MUST NOT 让 `ValueError` 逃逸——「10 位数字」与「可解析」不等价（`2026023100`、`9999999999` 都是 10 位数字却非法）

Seams under test:
- `controller.decide_frontier(...)`：tmp 目录树（`output/`、`states/`）+ 注入的 `raw_complete` → `FrontierDecision`，无写入
- `state.header_time.*`：纯 token 序列 → 判定，无 IO

Selected risk packs（项目特有检查）:
- Schema / columns / units / field names: header shape 门（3/4 数值 token）与 minute token 语义即契约
- File IO / path safety / overwrite: 只读、有界读、非普通文件/不可读被分类为 `STATE_UNREADABLE`；零写入是可断言的负面证据
- Error handling / rollback / partial outputs: 每类停止都有专属 `StopReason`，MUST NOT 以异常逃逸；`OSError` 不外泄
- Resource limits / large input / discovery: 目录枚举只认裁决 5 的可见集（10 位数字且可解析）；状态文件读取有界
- Legacy compatibility / examples: 3-token native 与 4-token 兼容 header 都要判；相对分钟 header 明确判为不对应 T

Risk packs considered (core):
- Public API / CLI / script entry: not selected - 不接入 CLI（`run` 入口体归 #26）
- Config / project setup: not selected - 不读 `config.toml`/`local.toml`；raw 判定经注入
- File IO / path safety / overwrite: selected - 见上
- Schema / columns / units / field names: selected - 见上
- Auth / permissions / secrets: not selected - 无凭据面；权限相关只体现为不可读分类
- Concurrency / shared state / ordering: not selected - 纯判定函数无共享状态；flock 归 #23、双源并行归 #28
- Resource limits / large input / discovery: selected - 见上
- Legacy compatibility / examples: selected - 见上
- Error handling / rollback / partial outputs: selected - 见上
- Release / packaging / dependency compatibility: not selected - 不新增依赖，lock 不变
- Documentation / migration notes: not selected - 无迁移；溯源由模块头注释与清单行注记承载

Domain packs (from active profile):
- Geospatial / CRS: not selected - 无几何
- Time series / forcing / temporal boundaries: **selected** - cycle 00/12、D+12h 推进、绝对分钟时间头
- 状态链 / warm-start 定戳一致性: **selected** - 本函数即该风险轴的执行点
- NWM 快照溯源与 DB-free 隔离: **selected** - 五个移植符号须带溯源头；断言零 NWM import、零 DB 符号

Invariant Matrix
Governing invariant: 每源的待跑 cycle 只由该源自己的 `DONE` 集合推进（无 DONE 取最早首态，否则 D+12h）；目标 T 的状态缺失/不可读/时间头非绝对 T 或 T 的 raw 未齐时一律停该源，MUST NOT 取更旧状态、跳轮、冷启动或互借另一源状态。
Source-of-truth identity/contract: `output/<YYYYMMDDHH>/<source>/DONE`（完成判据）与 `states/<source>/<YYYYMMDDHH>.cfg.ic` 的**文件名 cycle** 与**header 绝对分钟时间**必须同时对应同一个 T
Surfaces:
- Producers: none - 本 issue 零写入；`DONE`/状态的产出面归 #21（init）与 #24（发布）
- Validators/preflight: `controller.decide_frontier`、`state/header_time.py` 的 shape 门
- Storage/cache/query: 只读 `<YD_ROOT>/output/**/DONE` 与 `<YD_ROOT>/states/<source>/*.cfg.ic`
- Public routes/entrypoints: none - 不接入 CLI，入口经 #26 的 `run_once`
- Frontend/downstream consumers: #23（残留清理复用同一前沿结论）、#26/#27（run_once 与多轮追赶）、#28（双源并行）
- Failure paths/rollback/stale state: 每类停止走 `StopReason` 返回值而非异常；无写入故无回滚；崩溃残留（更晚状态/半成品目录）MUST NOT 改变前沿结论
- Evidence/audit/readiness: `FrontierDecision.detail` 是运行报告里该源停止原因的载体
Regression rows:
- 某源 `output/2026082600/<source>/DONE` 存在且 `states/<source>/2026082612.cfg.ic` header 对应绝对 2026-08-26T12Z、raw 齐 -> 待跑 T=2026082612
- 同上但 header 是相对分钟 `720.000000` -> `HEADER_TIME_MISMATCH`，MUST NOT 被解释为 cycle+720min
- 同上但 `states/<source>/` 另有更晚的 `2026082700.cfg.ic`（崩溃残留） -> 待跑 T 仍为 2026082612
- 同上但 T 状态缺失、只有更旧的 `2026082600.cfg.ic` -> `STATE_MISSING`，MUST NOT 回退到旧状态
- 另一源目录完全为空 / 停止 -> 本源结论不受影响（逐源独立，两源交叉断言）
- `states/<source>/` 内有发布残留临时名（如 `.2026082612.cfg.ic.tmp` 与一个子目录） -> 不影响任何结论，不抛错
- `output/` 存在但不可枚举（EACCES/EIO/ESTALE 类） -> `DISCOVERY_UNREADABLE`，**MUST NOT** 判为全新链或让前沿倒退到已 `DONE` 的 cycle
- `output/` / `states/<source>/` 不存在 -> 仍分别是全新链 / `NO_INITIAL_STATE`（「不存在」与「不可读」严格分流）
- 任一探测点遇到 `EACCES`/`EIO`/`OverflowError` -> 一律收敛为停止原因，MUST NOT 逃出 `decide_frontier`
- 64 MiB 无换行（含全 NUL）的状态文件 -> `STATE_UNREADABLE`，常驻内存与 `MAX_HEADER_LINE_BYTES` 同量级
- 一源的 `output/<cycle>/<source>/` 不可读、另一源正常 -> 前者 `DISCOVERY_UNREADABLE`、后者结论不受影响
- `states/ifs/<T>.cfg.ic` 缺失而 `states/gfs/<T>.cfg.ic` 存在且 header 正确 -> ifs `STATE_MISSING`，MUST NOT 互借 gfs 的同名状态
- `output/<cycle>/<source>/DONE` 是目录或断链 symlink -> 该 cycle 不计入 DONE 集合，不抛错

Required evidence（每条 input -> expected output）:
- **全新链**：无任何 `DONE`，`states/ifs/` 只有 `2026082000.cfg.ic`（header 对应绝对 2026-08-20T00Z）、raw 齐 -> `cycle == 2026-08-20T00:00Z`，`stop_reason is None`（spec Scenario「全新链取首态文件名」）
- **全新链多份状态**：无 `DONE`，状态有 `2026082000` 与 `2026082012` -> 取**最早** `2026082000`（裁决 6）
- **无 DONE 且无任何合法状态文件**（空目录 / 目录不存在 / 只有非法名） -> `NO_INITIAL_STATE`，不抛异常
- **前沿推进**：最新 `DONE` 为 `2026082600` -> T=`2026082612`（spec Scenario「前沿推进 D+12h」）
- **最新 DONE 取最大而非最后写入**：`DONE` 集合为 `{2026082600, 2026082512, 2026082700}` 且 mtime 逆序 -> D=`2026082700`，T=`2026082712`（钉死「取最大 cycle」而非「取 mtime 最新」）
- **DONE 逐源独立**：`output/2026082600/gfs/DONE` 存在而 `ifs/` 无 -> 对 `ifs` 该 cycle 不计入其 DONE 集合（两源在同一棵树上交叉断言）
- **状态缺失即停 + 不互借另一源**：`states/ifs/` 只有更旧的 `2026082600.cfg.ic`（T=`2026082612` 缺失），而 `states/gfs/2026082612.cfg.ic` **存在且 header 正确** -> ifs `STATE_MISSING`（MUST NOT 借 gfs 的同名状态、MUST NOT 回退旧状态），gfs 在同一棵树上同次得到正常结论（spec Scenario「精确状态缺失即停该源」+ MUST NOT「互借另一源状态」）
- **时间头不对应 T 即停**（spec Scenario）：逐条各一用例 -> 全部 `HEADER_TIME_MISMATCH`
  - header 绝对分钟对应 T-12h（拿旧状态改名冒充）
  - header 是相对分钟 `0.000000` 与 `720.000000`（裁决 2 的承重条：**移植了 `_valid_time_from_header_minute` 的实现必须在这两条上变红**）
  - header 只有 2 个数值 token（`23106\t6`，pin issue #1197 形态）-> shape 无效
  - header 有 5 个数值 token -> shape 无效（fail-closed，MUST NOT 取最后一个 token 蒙混）
  - header 行非数值/为空 -> 无效
  - header 为 `23106\t6\tnan` / `23106\t6\tinf` / `23106\t6\t-inf`（三个数值 token，shape 判 valid）-> `HEADER_TIME_MISMATCH`，MUST NOT 外泄 `ValueError`/`OverflowError`
- **时间头对应 T 的正例覆盖两种布局**：3-token native（`<mesh> <mesh-state-columns> <minute>`）与 4-token 兼容（`<mesh> <river> <lake> <minute>`）各一 -> 均放行
- **不可读分类**：状态文件为目录 / socket 或 FIFO / **断链 symlink** / `chmod 0o000`（非 root 时；root 下 `pytest.skip` 并说明）/ 非 UTF-8 字节 / 超字节上界 -> 均 `STATE_UNREADABLE`，MUST NOT 外泄 `OSError`/`UnicodeDecodeError`，MUST NOT 无界读入
- **symlink 跟随的正例**：状态文件是**指向合法状态文件的 symlink**（header 对应 T） -> **放行**（可跑），钉死裁决 4 的跟随语义
- **raw 缺口不提交**：T 的 `raw_complete` 返回 False -> `RAW_INCOMPLETE`，`cycle is None`（spec Scenario「raw 未齐不提交」）
- **缺轮阻塞不跳轮**：`raw_complete` 对 T 为 False、对 T+12h/T+24h 为 True -> 结论仍是**停在 T**，返回值中 MUST NOT 出现 T+12h/T+24h（spec「MUST NOT 自动跳过 cycle」的判别条）
- **判定顺序**：T 状态缺失**且** raw 也未齐 -> `STATE_MISSING`（状态判据先于 raw，compute-loop §10 顺序），且此时 `raw_complete` **MUST NOT 被调用**（用记录型 fake 断言调用次数为 0）
- **崩溃残留不改变前沿**：树中同时有 `states/<source>/<T+12>.cfg.ic` 与只含 DAT 无 `DONE` 的 `output/<T>/<source>/` -> 待跑 T 不变；本函数不删除任何路径（跑前跑后对整棵树做**递归快照比对**，证明零写入；快照维度 MUST 钉死为「相对路径 + 条目类型 + `st_mode` + size + 内容摘要」，否则等长原地改写在比对下不可见）
- **非法条目不砖化，两侧对称**（裁决 5）：`states/<source>/` 内共存临时名文件、子目录、点文件、`2026023100.cfg.ic`（10 位但非法日期）、`9999999999.cfg.ic`；`output/` 下共存 stray 文件、`.tmp-2026082600/` 半删目录与非 10 位目录 -> 结论与干净树逐字段一致，不抛错
- **`DONE` 必须是普通文件**：某 cycle 的 `DONE` 是**目录**、另一 cycle 的 `DONE` 是**断链 symlink** -> 两者均不计入 DONE 集合（若无其他 DONE 则走全新链/`NO_INITIAL_STATE` 分支），不抛错
- **header_time 单元级**：`cfg_ic_header_minute_index` / `_minute_time` / `cfg_ic_header_shape` 对 2/3/4/5 数值 token、含非数值 token、`expected_mesh_count` 匹配与不匹配 -> 与 pin 逐条一致；断言 `_as_float` 来自 `cfg_ic`（`header_time._as_float is cfg_ic._as_float`），防重复移植
- **溯源与隔离断言**：`state/header_time.py` 与 `controller.py` 含/不含相应标记——五个移植符号（三个函数 + `CfgIcHeaderShape` + `_VALID_CFG_IC_HEADER_TOKEN_COUNTS`）**逐符号**带 `NWM@8ae9b8f2 packages/common/state_qc.py:<行>`（取窗按函数边界，不用定长窗口，见 #8 的实测教训）；两模块源码内无 NWM import、无数据库符号
- **预登记必须被杀死的变异体**（按 `openspec/project-profile.md` 的 "Mutation-testing hazards" 执行：`rsync --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache'` 到含 `issue-22` 唯一标识的 scratch 目录、副本内 `rm -rf .venv && uv sync`、先断言 `yd_producer.__file__` 落在副本内、每个变异体之间 `PYTHONDONTWRITEBYTECODE=1` 并清 `__pycache__`、另跑一个必然变红的控制变异校准）：
  - (a) 绝对时间判据放宽为「接受相对分钟」（即移回 pin 的 `_valid_time_from_header_minute` 语义）-> 相对分钟用例必须变红
  - (b) `D+12h` 改为 `D+24h` 或改为「取最晚状态文件名」-> 前沿推进用例与崩溃残留用例必须变红
  - (c) 状态缺失时回退到更旧状态 -> `STATE_MISSING` 用例必须变红
  - (d) raw 未齐时前进到下一个 raw 齐的 cycle -> 缺轮阻塞用例必须变红
  - (e) shape 门去掉（3/4 token 限制放开）-> 2-token 与 5-token 用例必须变红
  - (f) 去掉 `math.isfinite` 闸 -> `nan`/`inf` 用例必须变红（异常逃逸）
  - (g) 状态路径查找回退到兄弟源目录 -> 「不互借另一源」用例必须变红
  - (h) `DONE` 判定由 `is_file()` 改为 `exists()` -> 「DONE 是目录/断链 symlink」用例必须变红
- **枚举失败不 fail-open**（裁决 9）：`output/` 整体不可枚举 -> `DISCOVERY_UNREADABLE`，`cycle is None`，**MUST NOT** 走全新链分支；`output/<最新 DONE cycle>/` 单个目录不可读（其余 cycle 正常）-> 同样 `DISCOVERY_UNREADABLE`，MUST NOT 退回更旧的 `DONE` 让前沿倒退；`states/<source>/` 不可枚举 -> `DISCOVERY_UNREADABLE` 而非 `NO_INITIAL_STATE`（三条均带非 root 跳过守卫）
- **「不存在」仍是空集合**：`output/` 目录不存在、`states/<source>/` 目录不存在 -> 分别仍为全新链 / `NO_INITIAL_STATE`（钉死 errno 分流没有把「不存在」一起判成不可读）
- **状态存在性探测不得让 `OSError` 穿透**（裁决 9）：`chmod 0o000 states/<source>`（父目录不可读，非 root）-> 返回停止原因并在 detail 指名，MUST NOT 抛 `PermissionError`。现有的「文件 `chmod 0o000`」用例对这条**没有判别力**（`stat()` 仍成功，失败落在已被 guard 的读取处）
- **可表示性门**（裁决 5 增补）：`output/9999123123/<source>/DONE`（10 位、可解析、+12h 溢出）-> 该条目不可见，结论与不含它的树逐字段一致，MUST NOT 抛 `OverflowError`；`states/<source>/9999123123.cfg.ic` 同理
- **首行有界读不放大内存**（裁决 4 增补）：构造接近上界但合法的状态文件（如 16 MiB 纯换行 + 末尾 header 行），在 `tracemalloc` 下断言 `_read_header_line` 的 traced peak 与文件大小同量级（不得达到其数倍）；恰好上界/超一字节的既有分类行为不变
- **无换行的巨大首行被截断拒绝**（裁决 4 二次增补）：64 MiB 无 `\n` 的状态文件（可打印字节与**全 NUL** 两种载荷各一）-> `STATE_UNREADABLE`，且 `tracemalloc` traced peak MUST 与 `MAX_HEADER_LINE_BYTES` 同量级（远小于文件大小）；同时断言合法的长空行前缀（首行位于数 MB 空行之后）仍能被正确读出
- **候选行上界的边界方向**（裁决 4 二次增补的边界行；Phase 6.2 不变量审计报出的覆盖缺口——把 `newline > MAX_HEADER_LINE_BYTES` 改成 `>=` 能在当时的 584 条用例里存活）：首行长度**恰好** `MAX_HEADER_LINE_BYTES` 的合法状态 -> 正常可跑（不得因上界而拒绝）；`MAX_HEADER_LINE_BYTES + 1` -> `STATE_UNREADABLE`。上界常量 MUST 由测试从 `controller` 导入，不得在用例里写死 65536；接受侧 MUST 走到真实可跑结论（padding 用 `str.split()` 会丢弃的尾随空格），不得因无关原因而通过
- **新停止原因同样逐源隔离**（裁决 9 的遗漏行，round 2 cand-14 CONFIRMED/FIX_NOW 的覆盖缺口）：同一棵树上 `output/<cycle>/gfs/` 不可读而 `ifs/` 正常 -> gfs `DISCOVERY_UNREADABLE`、ifs 得到正常可跑结论（现有四条 discovery 用例全是单源，既有的逐源行只覆盖「缺失」与 `STATE_MISSING`）
- **`raw_complete` 输入域声明**（裁决 10）：`decide_frontier` 的 docstring 含「返回的 T 可能带任意可解析 cycle 小时，`raw_complete` MUST 对该域全域（或由调用方把 `ConfigError` 收敛为停源）」，并以一条源码断言钉住
- **溯源行号与偏离清单准确**：`header_time.py` 的 `cfg_ic_header_minute_time` 溯源窗口为 `:629-639`（不得越进 `_VALID_CFG_IC_HEADER_TOKEN_COUNTS` 的注释块）；模块头「刻意偏离」清单 MUST 覆盖**全部** ruff 格式化差异（含 `mesh_count` 三元折行、`r` 前缀、docstring 后空行删除），不得再写不成立的「此处即全集」
- 预登记变异体追加九条并同样 KILLED（round 1 复核后四条 (i)–(l)；round 2 三条：fixture 新登记的 (m)/(n) 与重跑时自选的反向配重 (m2)，baseline 584；Phase 6.2 审计两条 (o)/(o2)，baseline 586；此处刻意写全数而不写「四条」——该计数曾随两次追加而失准）：(i) `except OSError` 恢复为一律判空 -> 枚举失败三条必须变红；(j) 状态存在性探测改回裸 `Path.exists()` -> 父目录不可读用例必须变红；(k) 去掉可表示性门 -> `9999123123` 用例必须变红；(l) 首行有界读改回 `read(MAX+1)+splitlines()` -> 内存用例必须变红；(m) 去掉 `MAX_HEADER_LINE_BYTES` 截断（候选行无上界）-> 无换行巨大首行用例必须变红；(n) `_done_cycles` 改为枚举 `output/<cycle>/` 目录项而非逐源 stat `DONE` -> 逐源隔离用例必须变红；(m2) 上界改为计入**累计已读字节**（被跳过的空行也计数）-> 长空行前缀用例必须变红（该条是上一条的反向配重：只防放大而不防过严，会让合法的长空行前缀被误拒）；(o) 换行处的上界比较 `newline > 上界` 改 `>=` -> 恰好上界用例必须变红；(o2) 块尾的 `len(pending) > 上界` 改 `>=` -> 同一条用例必须变红（(o)/(o2) 由 Phase 6.2 审计追加；因 `_READ_CHUNK_BYTES == MAX_HEADER_LINE_BYTES`，恰好上界的首行必然跨块，一条用例同时钉住两处判定）
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check . && uv run ruff format --check .` -> 退出码 0
- `cd producer && uv sync --frozen` -> 退出码 0（不得新增依赖）
- `openspec validate m2-producer-core --strict --no-interactive` -> 退出码 0

Non-goals:
- 残留清理与 flock（任务 12.2/12.3，issue #23）：本 issue 只证明残留**不改变前沿结论**，不删除任何路径
- 发布顺序、`DONE` 写入、旧状态清理（任务 13.x，issue #24/#25）
- `run_once` 编排、多轮追赶的顺序执行、双源并行与失败隔离（issue #26/#27/#28）
- 重戳、结构检查、负残差（任务 4.2–4.4，issue #9）：本 issue 只落**读侧** header 时间原语，且明确不移植 `_valid_time_from_header_minute`
- 与真实 `rawscan.judge` 的接线（`Config` 装配）：归 #26；本 issue 以注入判定证明控制流
- 真实 NFS/Slurm 行为、数值正确性：归 M4

Review focus:
- 绝对时间判据是否真的**只**接受绝对分钟——任何在 `0 <= m <= horizon` 上按相对分钟解释的分支都是缺陷（正例恒绿，只在相对分钟用例上变红）
- 前沿是否真由 `DONE` 集合推进而非由状态文件名的最大值推进（崩溃残留用例是唯一判别条）
- 是否越界落地了 #23 的清理动作、#24 的发布动作或 #9 的结构检查符号（含"顺手先放着"的死代码）；零写入是否有递归树快照证据
- `states/` 与 `output/` 的可见集是否严格按裁决 5（10 位数字**且** `%Y%m%d%H` 可解析）判定，非法条目是否真的不砖化该源
- 停止原因是否逐类可区分且不以异常逃逸；`OSError`/`UnicodeDecodeError` 是否被吞成分类结果
- 移植三符号是否与 pin 逐字一致、逐函数带溯源注释、`_as_float` 是否复用 `cfg_ic` 而非重复定义

## 13. run-controller（二）：发布、失败与清理

- [ ] 13.1 实现发布器：T+12 checkpoint 重戳到绝对 T+12（复用 4.3）→ DONE 前契约检查（v2、`forecast_days*24` 行、数据列数等于 `reach_count` 且等于变体 reach 数、T+12 可读、合并日志可用）→ DAT 原子 rename 为 `yd.rivqdown.dat` → 状态 rename → `DONE` 最后写 → 删旧状态只留两份 → 删本轮 work；正式文件不继承 scratch uid/gid/mode；记录型文件操作测试顺序与终名
- [ ] 13.2 实现失败处理（合并日志、删 work、不推进；复用 12.2 判定，仅接入失败/重跑路径）
- [ ] 13.3 实现 14 天保留清理（`realpath` 圈定 yd 根、symlink 越界拒删）

依赖：组 4（重戳）、组 12
§13.1 归属：发布（无 DONE 崩溃恢复/DONE 最后写/状态只留两份）
Suggested fixture level: expanded - 多状态目录树与记录型发布器
Minimal mergeable slice: 发布器（13.1）——发布顺序与契约检查对记录型文件操作独立可验证；失败与清理为后继

## 14. run-controller（三）：主循环集成

- [ ] 14.1 单源单轮 `run_once` 骨架打通：发现 → 组装 → 提交 fake → 发布 → work 清理；job ID/partition/终态/起止时间进运行报告；`local.toml` 缺 Slurm 字段即停
- [ ] 14.2 多轮追赶与缺口停等：raw 一次补齐 T/T+12h/T+24h 时序推进、每源在途提交计数 ≤1、缺轮停在缺口（§13.1：同源顺序/raw 缺口）
- [ ] 14.3 双源并行、单源失败隔离与崩溃恢复端到端：IFS 失败 GFS 继续、失败日志与 work 清理、无 DONE 残留下次重跑（§13.1：双源并行/单源失败/无 DONE 崩溃恢复）

依赖：组 5、组 8、组 9、组 12、组 13
§13.1 归属：控制器/发布（逐 task 标注场景）
Suggested fixture level: expanded - 多轮端到端目录树与可编排 fake executor
Minimal mergeable slice: 单源单轮骨架（14.1）——一条端到端路径独立合并保绿，追赶与双源为后继

**14.1 接线约束（由 issue #22 / PR #62 round 1 验证闸门传下，batch integration-contract cand-06 CONFIRMED）**：`controller.decide_frontier` 返回的待跑 T 可能带**任意可解析的 cycle 小时**（issue #22 fixture 裁决 5 刻意如此：对不可解析文件名 fail-closed 会让一次崩溃的发布永久砖化该源），而 `rawscan.judge` 只对 `config.cycle.hours` 全域——实测一个 stray 的 `states/<source>/2026081918.cfg.ic` 会让 18Z 目标进入 `judge`，在任何文件系统访问之前抛 `ConfigError` 并**穿透** `decide_frontier`，把「停一个源」放大成「整个 tick 崩」。14.1 MUST 二选一并配用例：(a) 接线前把目标限制到 `config.cycle.hours`，或 (b) 把注入的 `raw_complete` 包一层，把 `ConfigError` 收敛为该源的停止原因。
