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

- [x] 2.1 只读勘察 NWM@`8ae9b8f2`，产出精确快照文件清单（模块 → 原路径 → 目标路径，含 tracker 与补跑），落为 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md`；表格列固定 `| 能力项 | NWM 原路径 | 目标路径 | 剥离点 | 备注 |`，一行一个文件、路径反引号包裹；凡原模块触及 DB/scheduler/registry/journal/reservation 的行，`剥离点` 必须点名具体 import、符号或分支（供 2.2 逐文件消费），无耦合写 `无`，禁止“已剥离 DB 分支”一类无点名的笼统措辞
- [ ] 2.2 快照 object-store/path 基础函数与 IFS/GFS source、raw manifest 数据结构，含其最小测试；剥离 DB/scheduler 分支
- [ ] 2.3 增加溯源头部检查测试：所有快照模块（含后续组落地的 converter/forcing/tracker）头部含 `NWM@8ae9b8f2 <原路径>`

依赖：无
§13.1 归属：DB-free 链（基础结构部分）
Suggested fixture level: compact - 结构与路径函数用内存对象与 tmp 目录即可
Minimal mergeable slice: 勘察清单（2.1）——纯文档产物独立合并，快照代码为后继

## 3. raw-scan：完整性扫描与临时 manifest

- [ ] 3.1 实现 IFS/GFS 完整性规则判定（00/12 限定、0–168h、变量/bundle 模式、GFS f000 特例、逐文件检查）
- [ ] 3.2 实现 raw 只读复制到 `work/raw/`（源不可变断言）与临时 `raw-manifest.json` 生成（entry 只引用副本）

依赖：组 1（规则来自 config）、组 2（manifest 结构）
§13.1 归属：raw 扫描
Suggested fixture level: compact - tmp 目录树按文件模式生成空壳文件即可覆盖判定与复制
Minimal mergeable slice: 完整性判定纯函数（3.1）——不含复制与 manifest，可独立合并保绿

## 4. state-tools：cfg.ic 工具链

- [x] 4.1 快照并适配 `cfg.ic` 原生分段解析与回写（mesh/river/lake），字节级 roundtrip 测试
- [ ] 4.2 实现结构检查（缺段、行数与 header 不符、数值区损坏）
- [ ] 4.3 实现重戳到目标 cycle 绝对时间（只改 header、数据不变；服务 init 首态与发布前 T+12 定戳两条路径）
- [ ] 4.4 快照负残差归零与域均修正阈值检查纯函数

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

## 5. 执行器抽象：JobExecutor 协议与 fake

- [x] 5.1 定义 `JobExecutor` 协议（submit/poll、job ID/partition/终态/起止时间语义）与进程内 fake（成功/失败/超时可编排），接口契约测试
- [ ] 5.2 实现 Slurm 生产执行器（`sbatch`/`sacct` 封装，参数全部装配自 `local.toml`、零内置默认）；本阶段不做行为测试（M4 oracle），本地判据 = 参数装配纯函数检查 + 协议一致性

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
4. `"--output", str(spec.log_path)`、`"--error", str(spec.log_path)`（stdout/stderr 合并进同一份 `logs/<source>/<T>.log`，`specs/run-controller/spec.md`「失败处理」要求"合成一份"。两个 flag **都显式给出**，MUST NOT 依赖"省略 `--error` 时 Slurm 隐式并流"这一隐含默认）
5. 资源 flag：按 `required_fields` 的字段名 **`sorted()` 升序**逐项展开为 `SBATCH_FLAGS[name], str(value)`（排序而非沿用 `required_fields` 的书写序，是为了让产物只依赖键集不依赖 `config.toml` 里的行序；`str(value)` 使 `cpus = 8` 这类 int 取值可入 argv）
6. `"--wrap", shlex.join(spec.command)`

**C. `sbatch` 输出解析（纯函数）**

- `parse_sbatch_job_id(stdout: str) -> str`：`--parsable` 下 `sbatch` 输出 `<jobid>` 或 `<jobid>;<cluster>`。取 `;` 前首段并 `strip()`；结果为空、或非全数字 -> 抛 `ExecutorError`（`job_id=None`，此刻还没有 id）。多行输出取首个非空行。

**D. `sacct` 命令与输出解析（纯函数）**

- `build_sacct_command(job_id: str) -> tuple[str, ...]` MUST 为 `("sacct", "-j", job_id, "-X", "--noheader", "--parsable2", "--format=JobID,State,Start,End")`。**四列止步于此是本 fixture 的明示裁决，不是遗漏**——两项本可加的列各有归属，逐条见下：
  - **不取 `ExitCode`**：`specs/run-controller/spec.md`「失败处理」MUST 要求合并日志含退出码，但 `JobRecord`（#10 已合并、frozen、全字段无默认）没有承载它的字段；加字段即改 `executor.py` 的公共 schema，与本 issue「`executor.py` 零改动」和 #10 既有测试直接冲突，属跨 issue 变更。故本 issue **不取该列**。承载体的裁决 issue 是**已存在的 #47**「失败日志退出码的载体待裁决」（PR #39 已 DEFER 到此），**不另开新 issue**——本 fixture 早先写的"Phase 8 经 issue-scribe 落一条 tracked issue"是重复路由，已更正。#11 侧按 #47 推荐的**方案 (b) 带外流转**裁决并点名机制：**退出码由作业自身的批处理封装（`spec.command` 所指的 yd CLI 作业体）写进它自己的作业日志，再由发布器 #24 在合成 `logs/<source>/<T>.log` 时并入**；本模块只负责把 `--output`/`--error` 指向该日志路径，不经 `sacct` 取退出码。#24 侧的落地与 #47 的最终关闭仍归 #47。
  - **不取 `Submit`**：`submitted_at` 由 `submit` 取一次本地时钟写定（#10 fixture 的打戳时机 MUST），而 `sacct` 在作业刚提交时可能尚无记录（见 Known limits），提交那一刻根本取不到 `Submit`；若改为 `poll` 时用 `Submit` 覆写，同一字段会在一次 run 内先后报出两个值——一个字段两个权威，比时钟偏斜更坏。故保留本地钟，偏斜风险按 Known limits 归 M4。`-X` **不可省**：缺它 `sacct` 会连作业步（`.batch`/`.extern`）一起吐，解析拿到多行且首行未必是分配本体。
- `SACCT_ENV: Mapping[str, str]` MUST 至少含 `{"TZ": "UTC", "SLURM_TIME_FORMAT": "standard"}`。**并入语义钉死为叠加而非替换**：`SlurmJobExecutor` 在调用 runner 前构造 `{**os.environ, **SACCT_ENV}` 传入；MUST NOT 只传 `SACCT_ENV`——那会让子进程丢掉 `PATH` 与 Slurm 客户端环境，M4 现场每次 `poll` 都失败，正是这条钉死本要防的失败类。叠加发生在 executor 侧（可测），不在 `subprocess_runner` 侧（不测）。理由：`sacct` 默认吐集群本地时间且格式受该环境变量左右，而 `JobRecord.__post_init__` 对 naive 与非零偏移 `datetime` 一律 fail closed（`executor.py:_require_utc`）——不钉死时区就是把一个必然的 `ExecutorError` 留到 M4 现场触发。
- `parse_sacct_record(stdout: str, job_id: str) -> tuple[JobState, datetime | None, datetime | None]`：
  - 非空行数 MUST 恰为 1；为 0 或 >1 -> 抛 `ExecutorError(job_id=job_id)`（`-X` 下多行意味着出现了未预期的作业副本，静默取首行会让 `poll` 报告一个没被查询的实体）。该唯一行按 `|` 拆四列；列数不等于 4 -> 抛 `ExecutorError(job_id=job_id)`；
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
- 跨进程的 job 记录持久化：`poll` 依赖实例内提交记录，故只支持"同一 run 进程内提交后轮询"。这正是 `specs/run-controller/spec.md`「并发与锁」的形态（单进程持 flock 覆盖提交→等待→发布全生命周期），非缺陷
- `squeue` 回退：`sacct` 落库前的查询空窗按 fail closed 处理（见 Known limits），本 issue 不引入第二个查询通道

Known limits（每条在 PR 工作说明中复述，并按 Phase 8 规则路由）:
- **`sacct` 落库延迟**：作业刚提交时 `sacct` 可能尚无记录，本模块按 fail closed 抛 `ExecutorError`。M2 无真实调度器，无法判定该窗口是否需要重试/回退 `squeue`；归 M4 现场验证。
- **时钟偏斜**：`submitted_at` 取本地时钟、`started_at` 由 Slurm 报告，登录节点与计算节点时钟偏斜可能触发 `JobRecord` 的 `submitted_at <= started_at` 不变式而抛 `ExecutorError`。M2 不引入容差（容差是一个内置默认，正是本 issue 要消除的形态）；归 M4 现场验证。
- **退出码不经本模块（带外流转，机制已点名）**：`specs/run-controller/spec.md`「失败处理」要求合并日志含退出码，本模块的 `sacct --format` 明示不取 `ExitCode`。按 **#47** 推荐的方案 (b)：退出码由作业自身的批处理封装写进作业日志，由 **#24** 在合成 `logs/<source>/<T>.log` 时并入。裁决 issue 是已存在的 **#47**，不另开新件。
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

- [ ] 9.1 快照并适配 `cfg.ic.update` 轮询捕获（命中 720 分钟复制 + 分段格式校验；产物保持相对时间头），以模拟覆写序列测试正常/漏采/副本损坏三态
- [ ] 9.2 快照并适配漏采补跑（同一 Slurm 作业内、同初态同 forcing、END=0.5、末态采纳；注入假 SHUD 调用测试；补跑失败传导整轮失败；控制器提交计数不变）

依赖：组 2（勘察清单定原路径）、组 4（分段校验）、组 8（运行目录形态）
§13.1 归属：tracker
Suggested fixture level: compact - 模拟覆写序列与假 SHUD 调用即可确定性重放竞态
Minimal mergeable slice: 捕获轮询（9.1）——独立于补跑可合并保绿

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

- [ ] 12.1 实现严格前沿纯函数：`DONE`/状态文件集合 → 每源待跑 T 或停止原因（全新链、D+12h、状态缺失、时间头不对应 T、raw 缺口、缺轮阻塞）
- [ ] 12.2 实现未提交残留识别与清理重跑判定（保留 T 状态、删更晚状态与半成品）
- [ ] 12.3 实现非阻塞 flock 封装（持有即跳过、覆盖全生命周期），进程内测试跳过语义

依赖：组 1、组 4（12.1 时间头校验读分段 header）
§13.1 归属：控制器（前沿/flock 幂等/raw 缺口）
Suggested fixture level: compact - tmp 目录树表达 DONE/状态组合即可
Minimal mergeable slice: 前沿确定纯函数（12.1）——判定逻辑独立合并保绿，残留清理与锁为后继

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
