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

# NWM@8ae9b8f2 workers/mapping_builder/cli.py:602 的 `grid_id` —— 同属版本化快照事实
# （字段随任务 10.3 / #20 加入；生产取值归 #29 的生产实例复核）
[nwm_canonical_grid_id]
gfs = "<GFS canonical grid id>"
ifs = "<IFS canonical grid id>"

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
- **不做值域校验**：装载器只校验存在性与类型（spec cli-config 把 fail-closed MUST 限定为"任何必需字段缺失或类型错误"）。以下经 round 1 审核确认存在、但按 verifier 裁决 DEFER，本 issue 明确不做，归属逐条具名于 **issue #32**：`raw.*.variables`/`bundles`/`lead_hours` 的空列表拒绝（归 #6 task 3.1——空集会让"所有预期文件存在才算完整"恒真）；`variants.*` 的相对性校验（归 #20 task 10.3——绝对路径会让 `Path(yd_root) / variants.gfs` 静默丢弃 `yd_root`，使覆盖守卫检查的目录与实际写入目录分叉）；`cycle.hours ⊆ {0,12}`（归 #6 task 3.1，fail-closed 下游闸门）；`forecast_days` 与 `reach_count` 的正数约束（归 #24 task 13.1，DONE 前行数/列数校验）；`len(checkpoint_hours) == 1` 与 `output_interval_minutes` 的正数约束（**两项均为零归属项**，需 #32 裁决；建议同法由 #29 以生产实例钉死 `[12]` / `60` 并断言）；`lead_hours` 全集覆盖 0–168h 且与 `forecast_days*24` 一致（本 amendment 新增 `lead_hours` 后才存在，#32 原文早于该字段，已补入其验收标准）；`raw.<source>.variables` 的**单射性**（重复变量不报错，会让任务 3.2 的 manifest 产出重数 >1 的 `(lead, variable, local_key)` 三元组，而 spec `raw-scan` :58 的集合相等断言是集合式的、对重数天然失明；`lead_hours` 轴的对称闸门已在 `rawscan._expected_leads` 落地，`bundles` 轴由 `_reject_collisions` 守，唯独本轴无守卫。本条原为**零归属项**——issue #7 round 1 verifier CONFIRMED/DEFER 时本账本完全没有它——现已具名为 **issue #72**，仍需 #32 裁决认领。禁止静默去重：那等于替运维发明一份他没写的配置）
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
**本节的代码/用例引用一律用可 grep 的符号锚点（函数名、用例名、语句片段），MUST NOT 用行号**（round 5 R5-A）：行号与计数是同一个「第二真源无同步机制」问题，而行号更重——它被用来定义变异体位点与授权改动面，按失效的行号字面执行会落到无关行、跑出全绿，把已闭合的缺口误判为存活。实测本 PR 的证据面早已绕开绑定文本自行用符号锚点导航。

**回归行判别力规则（round 3 depth retro 的纠正动作，本 fixture 起对每一条回归行生效）**：凡覆盖本 change 引入分支的回归行，MUST 在行内声明一个**判别变异体**——对被覆盖分支的一处最小改写，该行必须因此转红。仅断言代理量（例如以「`states/` 树下零普通文件」代替「重跑仍能成功」）、仅断言某措辞**不出现**、或其构造根本走不到被覆盖分支的行，**不满足本规则**。声明留在 fixture（规范面），**实测红集留在 PR 证据（`.workplans/pr-92/`）而不写进 fixture**——round 2 已实证预测红集会在安全方向出错（变异体 B 实测红 3 条、brief 预测 1 条），把具体数字钉进绑定文本只会让每次用例改名都复现 round 2 的「变异表被证伪」争议类。

理由（depth retro 的复发不变量）：这条规则此前只是隐含约定，于是 round 1 逐条修掉 5 行（cand-02~06）、round 2 在新拆出的三路分支上又产生未钉腿、round 3 再抓 3 条（cand-R3-04/05/06）。修行不修「造行的规则」= 下一轮必有第四批。

**Matrix 全行清扫结论**（round 3 retro 步骤 2；桶划分即证据义务，MUST 按桶执行。计数一律不复述——round 4 R4-I 证明自指计数会漏掉强制实测义务；桶成员以下方列举与 `[桶 C-` 锚点为准）：

- **桶 A — 已有实测变异体**：STATES_NOT_EMPTY（round-1 控制变异体禁用守卫；红集随套件世代变化，**MUST 对当前 head 重测而非引用旧数字**）、未来 cycle 排除（cand-02）、扫描窗上端点闭、`O_EXCL`（cand-03）、`DONE` 名字收窄（cand-05）、stat 层 fail-closed（cand-06）、首位 source 写中途失败（R2-04）、新谓词反向钉死（R2-07）、`_entry_kind` FOLLOW 臂（R2-05）。~~引用既有实测即可，MUST NOT 重跑。~~ **round 4 起该豁免被撤销**（batch-2 verifier 裁决，R4-E）：`written` join 那条引用在本 head 上**从未成立**（`rawscan.SOURCES` 是 2 元组，`init.py` 写入循环 `for source in rawscan.SOURCES:` 内三条失败腿均 `return`，故 `len(written) ∈ {0,1}`，端到端不可能出现两元 `written`；把 `landed = "、".join(...)` 换成 `written[0]` 时全套静默全绿）。**round 5 起该缺口已闭合**：`[桶 C-7]` 的单元级用例把同一变异体杀掉，实测红集恰为该用例。抽查只能证明「未被扰动」，证不了「引用本身为真」——这是两个命题。**桶 A 每条引用 MUST 对当前 head 逐条重新核对，一行一个变异体。**
- **桶 A∩C — 有实测变异体、但作用域被 round 3 证明过窄**：`WRITE_FAILED` 零落盘收尾话术行（`written` join 部分由 cand-04 实测钉死；**话术部分**被 cand-R3-01 证伪，见下方 CORRECTION）、零残留 open 期失败行（`kind` 代理谓词部分由 R2-01 实测钉死；**对 M5 不敏感**且重跑承诺只有代理断言，见 cand-R3-04/R3-06）。
- **桶 B — 有正向判别断言、变异体未声明**：全新根成功路径、`DONE_PRESENT`、`VARIANT_MISSING`/`AMBIGUOUS`、`HEADER_SHAPE_INVALID`、`chmod 0o000 states/`、变体目录不可枚举、`output/` 树不可枚举、`CALIBRATION_STATE_UNREADABLE`、`ConfigError` 原样上抛、naive `now`、扫描窗下端点闭、`decide_frontier` 4-token 参数化、跳过语义、4-token 兼容 header、非默认配置取值、阶段 B 中途失败（gfs 空目录）、`decide_frontier` 端到端、`cli.main` 成功/拒绝。这些行都断言了一个**具体的不同终态**（refusal 枚举项 / 文件内容 / 退出码），判别力有结构性理由但未被声明。~~本轮 MUST 为每行补一句判别变异体声明；实测随实现方的变异表一并给出，MUST NOT 为每行单独跑一轮。~~ **round 4 起该豁免被撤销**（batch-2 verifier 裁决）：round 4 泄漏的两条 major 全部出自免测的桶 A/桶 B，唯一强制实测的桶 C 五组全部闭合，免测/泄漏相关性 2/2 对 0/5；且 inspection 在本 fixture 上已有三次证实的假阴性（`0o500` vs `0o600`、`states/` vs `states/ifs/`、以及 R4-D——一条 docstring 白纸黑字写着「本行约束的不是 X」的行仍被判入桶 B）。**桶 B 剩余每条声明 MUST 实测。** 其中「全新根成功路径」「4-token 兼容 header」「`decide_frontier` 4-token 参数化」三行的声明变异体落在本 change 未改动的模块（`state/header_time.py`、`controller.py`），与规则「对被覆盖分支的一处最小改写」字面不符——**按声明原样实测**（它们仍会转红），另记「选点偏移」待改，MUST NOT 边测边改声明。
- **桶 B 的一行已于 round 4 被移出**：「未来 cycle 不夺首轮」经 batch-2 verifier 判为**假声明**（`init.py:352-359` 的 `span = (now.date() - start_date).days` 使 `NOW+12h` 从不进入候选集；另一构造由 `:358` 的 `<= now` 过滤），两个构造的 complete 候选集均为**单元素**，声明的变异体必绿。该行按本 fixture 自己的判据（构造走不到被覆盖分支）改归**桶 C**，见 `[桶 C-6]`。
- **桶 C — 仅代理量 / 仅反向断言 / 构造走不到被覆盖分支**：见下方所有带 `[桶 C-` 前缀的行（锚点可 grep，不复述条数）。**桶 C 是本轮唯一强制实测的桶**——inspection 在这里有已证实的假阴性率（`0o500` vs `0o600`、`states/` vs `states/ifs/` 正是 inspection 漏掉的两处）。

**桶 B 的判别变异体声明**（round 3 retro 步骤 2；声明即规范面，实测随实现方变异表给出，本轮不为每行单独跑一轮）：

| 回归行 | 判别变异体（对被覆盖分支的最小改写） |
|---|---|
| 全新根成功路径 | header minute token 由 `round(T.timestamp()/60)` 改为 `T.timestamp()`，或跳过重戳直接复制率定末态 |
| `DONE_PRESENT` | 删除 `output/` 侧的 `DONE` 守卫 |
| `VARIANT_MISSING` / `CALIBRATION_STATE_AMBIGUOUS` | 两者返回同一枚举项（本行要求逐项可区分） |
| `HEADER_SHAPE_INVALID` | 把 `restamp_to_absolute_time` 的 `ValueError` 收敛成 `CALIBRATION_STATE_UNREADABLE` |
| `chmod 0o000 states/` | `_entry_names` 的 `OSError` 臂改为 `return ()`（判空即放行写入） |
| 变体目录不可枚举 | 该处 `OSError` 收敛成 `VARIANT_MISSING`（本行要求与「不存在」分流） |
| `output/` 树不可枚举 | 该处 `OSError` 改为判空放行 |
| `CALIBRATION_STATE_UNREADABLE` | 把 `state.parse` 的 `ValueError` 收敛成 `HEADER_SHAPE_INVALID`（本行要求三者逐项可区分） |
| `ConfigError` 原样上抛 | 在 `_first_complete_cycle` 外包一层 `except ConfigError: return None` |
| naive `now` | 删除 `now.tzinfo` 自查，按宿主时区静默重释 |
| 扫描窗下端点闭 | `window_start <= cycle` 改为 `window_start < cycle` |
| `decide_frontier` 4-token 参数化 | `decide_frontier` 的 header 数值 token 数门收成只认 3 |
| 跳过语义 | 「升序找第一个 complete」改为「取窗内最早候选」（不判 complete） |
| 4-token 兼容 header | `restamp_to_absolute_time` 的 token 数门收成只认 3 |
| 非默认配置取值 | 硬编码 `cycle.hours = [0, 12]`，或硬编码变体目录名 `input/models/yd_<source>` |
| 阶段 B 中途失败（gfs 空目录） | 把「失败不回滚」改为失败时删除已落盘的前序首态 |
| `decide_frontier` 端到端 | init 写出的 header 改用相对时间 token（本行必红于 `HEADER_TIME_MISMATCH`） |
| `cli.main` 成功/拒绝 | 「任何 refusal -> `EXIT_GUARD`」改为 `return 0` |

桶 B 的共同结构性理由：每行都断言了一个**具体的不同终态**（refusal 枚举项 / 文件内容 / 退出码），故上列变异体各自把该终态改成另一个可观测值。这与桶 C 的失效模式（断言的是代理量或某措辞不出现，变异后终态不变）正相反——这正是划桶的判据。



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
- [x] 3.2 实现 raw 只读复制到 `work/raw/`（源不可变断言）与临时 `raw-manifest.json` 生成（entry 只引用副本）

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

### Issue #7 fixture（任务 3.2）

Fixture level: expanded
Upstream suggested level: compact（override：改动面命中**强制** expanded 触发词 `file output`/`path`/`temp`/`writer`/`schema`/`format`——本任务写文件、定 JSON 契约；profile 的 domain expanded-trigger `raw manifest`/`forcing`/`cycle` 同样命中。另据 PR #38 终局回灌的上游 sizing-retro：唯一 oracle 是合成 fixture 的任务不应默认 compact）
Repair intensity: high（File IO / path safety / overwrite；producer→converter 的产物契约边界）
Project profile: yd-viewer

Risk packs（逐条 selected / not selected）:
- Public API / CLI / script entry: not selected —— 本 issue 只交付库函数，CLI 接线归组 12
- Config / project setup: selected —— `raw.<source>.variables`/`lead_hours` 决定扇出与复制集；取值域校验已由 3.1 承担，本 issue 只消费
- File IO / path safety / overwrite: **selected（主风险面）** —— 只读 NWM 原件 + 写 work 副本；containment、不覆盖、不跟随 symlink、部分失败清理
- Schema / columns / units / field names: **selected** —— `raw-manifest.json` 是 producer→converter 的产物契约（entry 6 键 + `idx_selector` + manifest 级四键）
- Auth / permissions / secrets: not selected —— 无凭据面；NWM 原件的权限失败已由 3.1 归入 `unreadable_files`，本 issue 只在 `complete=True` 后运行
- Concurrency / shared state / ordering: selected —— 扫描→复制窗口的 TOCTOU（本 issue 收敛复制期的一半，残余归组 12）
- Resource limits / large input / discovery: not selected —— 复制集由 `expected_files` 有界钉死（57 lead × bundles），不做目录发现
- Legacy compatibility / examples: selected —— manifest MUST 与 pin 的 `DownloadManifest.as_dict()` 同形，否则 converter 读不了
- Error handling / rollback / partial outputs: **selected** —— fail-closed 的零写入拒绝与部分复制失败的清理
- Release / packaging / dependency compatibility: not selected —— 本 issue 只用 stdlib，不新增任何依赖（判别器是 `uv sync --frozen` 无漂移）
- Documentation / migration notes: not selected —— 无面向用户的行为变更；契约细则落在本 fixture 与 spec delta
- Domain：Time series / forcing / temporal boundaries: **selected** —— lead 全集承接与 APCP 累积语义的 fail-closed
- Domain：NWM 快照溯源与 DB-free 隔离: selected —— 承接 pin 语义、禁运行时 NWM import 与 DB 连接
- Domain：Geospatial / CRS: not selected —— 本 issue 不解析 GRIB 内容，不触及投影
- Domain：状态链 / warm-start: not selected —— 与 cfg.ic 状态面无交集

Change surface:
- 新增 `producer/src/yd_producer/rawcopy.py`（复制 + manifest 生成）
- 新增 `producer/tests/test_rawcopy.py`
- `rawscan.py` **仅**允许把 bundle 文件名渲染面提升为可复用（薄公开包装 + `__all__` 登记，不改任何既有公开行为），理由见下方复制语义的 containment 段；这是本 issue 触碰该文件的唯一例外，且 MUST 作为偏离上报
- 不触碰 `config.py`、CLI 入口、`store/`、`raw/manifest.py`——本 issue 只**消费** `ScanVerdict` 与 `DownloadManifest`/`ManifestEntry` 数据结构，不改其定义

Must preserve:
- **不新增任何依赖**：`producer/pyproject.toml` 现有 7 个依赖（cfgrib/eccodeslib/numpy/pyproj/pyshp/shapely/xarray），本 issue 只用 stdlib，判别器是 `uv sync --frozen` 无 lock drift。（本行原写作「`dependencies = []`」，系从 #6 fixture 转抄的过期事实，由 issue #7 的实现者证伪并更正——#6 fixture 的同一行同样过期，归 follow-up）
- `rawscan.judge` 的公开面与 `ScanVerdict` 五字段形态逐字不变（由 #6 的 fixture 钉死）
- `raw/manifest.py` 的 `ManifestEntry`/`DownloadManifest` 语义与 `as_dict()` 键序——它们是 NWM pin 的字节等价面，本 issue 只构造实例，不改类
- 现有全部 producer 测试继续通过

#### Seam（本 fixture 钉死）

```python
def stage_raw(
    verdict: ScanVerdict,      # rawscan.judge 的返回；MUST complete=True，否则拒绝
    raw_root: str | os.PathLike[str],  # 与 judge 同一入参（源根）
    work_dir: str | os.PathLike[str],  # 本轮 work/<source>/<cycle>/ 根
    source: str,               # "ifs" | "gfs"
    cycle: datetime,           # 与 judge 同一入参（tz-aware UTC 整点）
    config: Config,
) -> StagedRaw
```

`StagedRaw`（frozen dataclass）字段：

- `manifest_path: Path` —— 落盘的 `<work_dir>/raw-manifest.json` 绝对路径
- `copied_files: tuple[Path, ...]` —— 副本绝对路径，与 `verdict.expected_files` **同序同长**
- `entries: tuple[ManifestEntry, ...]` —— 写进 manifest 的 entry，按 (lead 升序, `variables` 声明序)

失败一律抛 `RawStagingError`（本 issue 新增，`Exception` 子类），带**闭合词表**的 `kind` 字段，供调用方与测试机检；MUST NOT 外泄裸 `OSError`/`KeyError`/`json.JSONDecodeError`。词表恰好九项：

| `kind` | 触发 |
|---|---|
| `incomplete-verdict` | `verdict.complete is False` |
| `unsupported-layout` | `len(bundles) != 1`（见下方单 bundle 约束） |
| `source-symlink` | 源侧 bundle 路径或其祖先段是 symlink |
| `source-manifest` | **归属规则（不是穷举清单，勿再写触发条数）**：凡因果落在「源 manifest 这份外部 JSON 的内容或形态」上的失败，一律归本 kind。已实现的触发含但不限于：文件缺失/不可解析/`forecast_hours` 缺失或形态错/覆盖不全/顶层非 Mapping/entry `metadata` 非 Mapping 或六键缺一/entry `variable` 非字符串/`forecast_hour` 会被 `int()` 有损归一/`(forecast_hour, variable)` 重复键/本轮 manifest 序列化失败/**准入段兜底地板收敛的任何非词表异常**（`ADMISSION_FALLBACK_KIND`）。本行**有意不写条数**——该计数已在 round 1/2/3 连续三轮失实（每次都是新增闸门未回灌到计数上），改以归属规则表述后不再有可失实的数 |
| `verdict-mismatch` | **归属规则**：凡因果落在「`verdict` 与本次调用形参不同源」上的失败，一律归本 kind。已实现的触发：① 由形参重新构造的源路径与 `verdict.expected_files` 不一致（含相对 `raw_root` 的处置，见下方复制语义）；② `verdict.expected_variables` 的 lead 集合与由形参重构的 lead 集合**不相等**（单向包含不够，见下方三元组契约）；③ 某个 lead 的变量集不是 `tuple`/`list`（`str` 可迭代，会被逐字符扇出成变量名，静默产出全错的 manifest）。另有一条不可达防御腿（`_reject_symlinks` 的 `relative_to` `ValueError`）登记在 gate-audit 死腿桶。原文列的第 ④ 条「形参与 verdict 不同源」不是独立路径，而是 ①②③ 的因果表述（三处消息里都逐字带着它），已删——round 3 verifier CONFIRMED |
| `accumulation-metadata` | R4B2 的三条 APCP fail-closed 中任一条不成立 |
| `source-mutated` | 复制前后源 `lstat` 元组不一致 |
| `target-exists` | 目标副本路径已存在 |
| `copy-failed` | 复制期的其它 IO 失败（权限、ENOSPC 等） |

`ConfigError` 的分工（round 2 修订）：取值域校验仍归 `rawscan`，本 issue 不重复；但**形参之间的关系**与**形参自身的形态**由本模块自守，同样抛 `ConfigError`、**不占**上表九项 kind 名额——语义是「调用写错了」而非「本轮环境不满足前置」。已授权的形参守卫：`source` 词表、`cycle` 的 tz-aware 与整点、空 `lead_hours`，以及 `raw_root` 与 `work_dir` 互相包含（后者由 round-1 verifier 指名要求，守 `docs/compute-loop-design.md` §4.1 的「NWM 树零写入」硬约束；其判定 MUST 经 `resolve()` 归一并辅以 inode 身份比对，纯词法比较会被大小写别名、`..` 段与根 symlink 三种形态绕过——round 2 实测）。上表九项 kind 一项也不归 `ConfigError`。每个 `kind` MUST 各有一个具名用例并各有一个杀手变异体（把该分支删掉/改成放行即变红）。

**`(lead, variable, file)` 三元组契约（#6 round 1 cand-05 路由到本 issue 的 seam）**：本 issue 在此确立该键关系，形态为
`(entry.forecast_hour, entry.variable, entry.local_key)`。同一 (lead, bundle) 的**全部变量 entry 共享同一个 `local_key`**（pin 的逐变量扇出，`NWM@8ae9b8f2 gfs_adapter.py:611-636`：外层 hour 算一次 `local_key`，内层逐变量产 entry）。三元组的完整性由下式定义并 MUST 被断言：
`{(lead, var) for lead, vars in verdict.expected_variables.items() for var in vars}` 与 `{(e.forecast_hour, e.variable) for e in entries}` **相等**（不是包含）。

**单 bundle 约束（fail closed）**：上式只在「一个 lead 恰好对应一个物理文件」时有定义。`RawSourceConfig.bundles` 是元组、允许多模式（#6 的 fixture 就行使了两个模式），此时 `entries` 是 lead × variables 而 `copied_files` 是 lead × bundles，**变量落在哪个 bundle 无处可查**——`config` 不携带 variable→bundle 映射，`ScanVerdict` 也不提供。故 `stage_raw` MUST 在任何写入之前检查 `len(config.raw.<source>.bundles) == 1`，否则以 `kind="unsupported-layout"` 拒绝。这不是把 3.1 的能力砍掉：3.1 的判定对多 bundle 完全有效（它只需要文件在不在），是 manifest 侧的语义在多 bundle 下不存在。pin 侧同源事实：bundle 文件名逐 hour 只产一个（`gfs_adapter.py:1878-1880`、`ifs_adapter.py:1688-1690`），多 bundle 是 yd 的 config 允许而 pin 上不存在的形态。放开该约束需要先在 `config` 里长出 variable→bundle 映射——归 **#29 / #32**，本 issue 不发明。

消费 `ScanVerdict` 的两条 #6 交接约定：`ScanVerdict` 是持有可变 `dict` 的 frozen dataclass，`hash()` 抛 `TypeError`——MUST NOT 放进 set 或用作 dict key；`ScanVerdict` 不提供三元组，故上式是本 issue 自己从 `expected_files` 与 `expected_variables` 交叉构造的。

#### 落盘布局与 `local_key` 形态（pin 事实转录）

`entry.local_key` 在 pin 上**不是文件系统路径而是 object-store key**（§3.1 记有 IFS 的 `local_key` 逐字使用该默认值，以及 `packages/common/object_store.py` 的 `normalize_object_key`(L44-75) 与 `resolve_path`(L273-285) 均不做大小写归一）。**「消费端把 `local_key` 交给 `resolve_path` 解析」这一步不作 pin 断言**——§3.1 无任何一行记载该调用路径，而本仓唯一可核的 pin 桥就是 §3.1（round 2 verifier CONFIRMED/FIX_NOW；此前两版分别引 `converter.py:1460` 与「§3.1 实有的事实」，前者不存在于 §3.1，后者换了引用却没换命题）。承重结论改由**本仓自身**给出，不依赖任何 pin 事实：`store/object_store.py` 的 `LocalObjectStore` 每一条访问面都走 `self.resolve_path(key)`（`:156`/`:186`/`:204`/`:215`/`:233`/`:246`/`:261`/`:306`，定义在 `:314`），其根取 `work_dir`。故本 issue 逐字沿用 pin 的 key 形态并让 object-store 根落在 work 内：

- `local_key = f"raw/{存储身份}/{YYYYMMDDHH}/{bundle 文件名}"`（`gfs_adapter.py:615`）
- object-store 根 = `work_dir`；于是 `resolve_path` 结果为 `<work_dir>/raw/<存储身份>/<YYYYMMDDHH>/<bundle>`，**位于 `work/raw/` 之下**，满足 spec 的「entry 路径 MUST 只引用 `work/raw/` 临时副本」
- 存储身份逐源非对称，复用 `rawscan.SOURCE_DIR_NAMES`（`ifs -> "IFS"`、`gfs -> "gfs"`），MUST NOT 另抄一份字面量
- `remote_url` 沿用源 manifest 对应 entry 的值；源 manifest 不可用时整轮 fail closed，MUST NOT 以 `""` 占位（见 fail-closed 段）
- `expected_checksum`、`expected_size_bytes`、`manifest_uri` 三者 MUST 落 `None`，逐条理由 MUST 写进实现注释：前两者在 pin 的**构建期**同样是 `None`（下载期才可能有值），yd 复制的是已落盘的字节、无独立 oracle，写进去等于制造一个无人校验的声明；`manifest_uri` 留空的理由**不依赖任何 pin 事实**（§3.1 未记载该字段，故本仓不就它作 pin 声明；原文的「pin 上是 object-store URI（`ifs_adapter.py:667` 一带）」无 §3.1 支撑，已删——同上 verifier 裁定）：yd 的 manifest 落在 `<work_dir>/raw-manifest.json`，不是 object-store 对象，写一个 `file://` 路径等于发明一个本仓不持有的身份

#### 源 manifest 承接（不发明语义）

pin 在 raw cycle 目录内落盘 `manifest.json`（GFS `_persist_manifest_metadata` L1599-1609 调用点 L774；IFS 于 `ifs_adapter.py:667` 构建期一次性写入）。yd MUST **读取** `<raw_root>/<存储身份>/<YYYYMMDDHH>/manifest.json` 并从中承接 entry 级语义，MUST NOT 在 yd 侧发明：

- `grib_short_name`、`cfgrib_filter_by_keys`、`logical_remote_url`、`cycle_time`、`valid_time`、`bundle` —— pin 构建期写的 6 键，逐条承接
- **累积语义**：pin 下载期把 `IdxSelection.as_metadata()`（键 `step_range`/`accumulation_type`/`idx_record_number`/`selector_warning`）按变量收进**复数** `idx_selectors`（`gfs_adapter.py:1070`），**只有单变量 bundle 才另写单数 `idx_selector`**（L1071-1072）。消费端 `_apcp_selector_metadata`(converter L677) **只读单数键**。yd 的扇出是逐变量的，故每条 entry 的单数键有唯一定义：`idx_selector = 源 manifest 的 idx_selectors[variable]`。两个键都落盘（复数保持与 pin 同形），MUST NOT 只落复数键。

源 manifest 缺失/不可解析/其 entry 集合无法覆盖本轮 (lead, variable) 全集 —— 一律 fail closed，不得以空值或推导补齐。

#### fail-closed 契约（勘察清单 §3.1「APCP 累积元数据的 fail-closed 要求（R4B2）」逐条落地）

1. 凡 `variable == "apcp"` 的 entry，其 `idx_selector.accumulation_type`（或别名 `accumulation_policy`）MUST 存在且取值 ∈ `{"cumulative_since_cycle", "interval_bucket"}`；取 `"interval_bucket"` 时 `step_range`（或别名 `stepRange`）MUST 一并存在。缺失或越域即报错停止。
2. MUST NOT 继承 pin converter L1726 的 `or "cumulative_since_cycle"` 静默默认；MUST NOT 依赖 converter L678「`idx_selector` 不是 Mapping 就回退到 entry metadata 顶层」这条兜底——yd 的落盘位置固定为**单数 `idx_selector` 子 Mapping**，平铺到顶层不作为 yd 的落盘约定。
3. manifest 级 forecast hours 键分两侧，MUST NOT 混为一谈：
   - **源侧（读）**：源 manifest MUST 带 `forecast_hours` 且其值 MUST 是 list，缺失或非 list 即 `kind="source-manifest"` 报错。只此一键强制——它是 converter `_configured_forecast_hours`(L1611-1622) 唯一读的键，缺了会回落到 L1622 的 `sorted({entry["forecast_hour"]})`，用「实际有的」当「应该有的」，完整性检查恒为真。**`requested_forecast_hours` MUST NOT 对源侧强制**：IFS 在 pin 上从不写该键（见勘察清单 §3.1「R4B2 的作用域与可用性」段），对它强制会让每个 IFS cycle 无条件失败。
   - **yd 侧（写）**：yd 自产的 `raw-manifest.json` 的 manifest 级 metadata **四键全写**，取值由 yd 自己确定而不从源 manifest 抄：`forecast_hours == requested_forecast_hours == sorted(verdict.expected_variables)`（即本轮 `lead_hours` 全集；yd 不做 pin 那种 requested/effective 的裁剪，两者相等是本 issue 的形态），`first_/last_forecast_hour` 取其 min/max。
   - **两侧的一致性检查**：`set(源 manifest 的 forecast_hours) ⊇ set(yd 的 forecast_hours)` MUST 成立，否则 `kind="source-manifest"` 报错——源 manifest 没声明的小时，yd 不能凭副本存在就声明它齐全。反向不要求（源可以比 yd 要的多）。
4. 本 fixture 实测的 pin 事实（限定 R4B2 的作用域，MUST 写进实现注释）：`IFS_VARIABLES = ("2t","2d","10u","10v","tp","sp","ssr","str")`（`ifs_adapter.py:47`）**不含 `apcp`**，且 IFS 侧全文无 `idx_selector`——故第 1 条只对 GFS 产生约束。IFS 的降水变量是 `tp`、且同为累积量，但 pin 未给 IFS 任何累积元数据，本 issue **不为 IFS 发明**该语义（见 Non-goals 与 Known limits）。
5. 已知运行期后果（MUST 记入 PR 已知限制）：pin 只在**云镜像**下载路径注入 `idx_selectors`（`_download_cloud_mirror` L1068-1072）；NOMADS 直连路径（`_download_with_retries`）不注入。故一个经 NOMADS 下载的 GFS cycle 会在本 issue 的第 1 条上 fail closed、整轮拒绝。这是 R4B2 要求的方向（宁停勿错），不是缺陷；运行期若命中需以运维决策而非放宽本闸门处理。

#### 复制语义

- 复制集 **恰好** `verdict.expected_files`，一个不多一个不少；`verdict.complete is False` -> 拒绝，且**不得写任何文件**（含不得建目录、不得写半个 manifest）
- 复制**前后**对每个源文件取 `os.lstat`，比对 `(st_size, st_mtime_ns, st_ino, st_mode)`；任何一项变化即报错。这同时是「源不可变」的证据与扫描→复制窗口 TOCTOU 的收敛点（残余 TOCTOU 归组 12 控制器，见 Non-goals）
- **源侧 symlink 一律拒绝**（`kind="source-symlink"`）：对每个源 bundle 路径，MUST 逐段 `lstat` 检查——路径自身或 `<raw_root>` 之下的任一祖先段是 symlink 即拒绝，MUST NOT 跟随。这里刻意**比 3.1 更严**：`rawscan._check` 走 `is_file()` 语义、跟随 symlink（`rawscan.py:255-274`），故 `judge` 可能对一个 symlinked bundle 返回 `complete=True`；而本 issue 钉死的源不可变取证是 `os.lstat`（看链本身、不看目标），两者叠加会在「MUST NOT 跟随 symlink」这句话正下方留一个洞：链的元组不变而目标被换掉，取证照样通过。收口方式是拒绝而不是改用 `os.stat`——`stat` 版本要再补目标的 containment 检查与第二个 TOCTOU 窗口，复杂度换不来收益（NWM 经 object store 的 `write_bytes_atomic` 落盘，raw 树内出现 symlink 属异常形态）。该不对称是**有意的**，MUST 写进实现注释：3.1 判「NWM 说它在」，3.2 判「yd 愿意复制它并为其身份背书」
- 复制 MUST NOT 写、删、改、重命名 NWM 原件的任何路径（compute-loop §4.1 硬约束）
- 源路径 containment：每个源 bundle 路径 MUST 由 `raw_root`/`SOURCE_DIR_NAMES[source]`/cycle 紧凑戳/bundle 文件名**重新构造**，并与 `verdict.expected_files` 的对应项逐字相等；MUST NOT 直接信任 `expected_files` 里的路径——形参与 verdict 由不同调用点提供，不一致即 `kind="verdict-mismatch"` 报错（调用序错误）。三条落地约束，缺一即该检查要么误报要么无判别力：
  - **绝对化必须与 `judge` 同法**：`judge` 接受相对 `raw_root` 并以 `Path.cwd()` 提升（`rawscan.py:370-386`，#6 为此钉了一条 Regression row），故 `expected_files` 恒为绝对路径。重新构造 MUST 走同一次提升（相对 `raw_root` 先 `Path.cwd() / root`），否则一个**合法**调用会被本检查误拒。收口方式只有这一种：**必须与 `judge` 同法提升**。曾考虑的等价方案「要求 `raw_root` 绝对、相对即拒绝」已排除——下方 Regression rows 钉了一条「合法的相对 `raw_root` 调用 -> 正常产出」，那正是本检查没有误拒的唯一判别器，拒绝相对路径会让该行不可满足。提升逻辑 MUST 写进实现注释。
  - **bundle 文件名 MUST 复用 `rawscan` 的渲染路径**，MUST NOT 在本模块重抄一份模式校验/渲染规则。理由与 `SOURCE_DIR_NAMES` 同：第二份字面量会让两处对同一模式给出不同结果，而本检查恰恰以「两处相等」为判据——自己抄一份等于让检查比对自己，判别力归零。`rawscan` 的 `_render`/`_validate_pattern` 目前是私有的；实现者 MUST 在**不改变 `rawscan` 既有公开行为**的前提下把渲染面提升为可复用（最小改动：加一个薄公开包装并在 `__all__` 登记），这是本 issue 允许触碰 `rawscan.py` 的**唯一**例外，且 MUST 作为偏离逐条上报。
  - 该检查 MUST 有自己的 `kind` 与自己的具名用例，MUST NOT 折进 `source-manifest`——后者按归属规则收纳一整族源侧失败（见上方 kind 表），把它折进去就意味着删掉 containment 检查也不会有任何用例变红（本仓记录在案的「声明必须配判别器」失效模式）。
- 目标目录不存在则创建；目标已存在同名文件 -> 报错停止，MUST NOT 覆盖（work 是一次性隔离单元，同名存在意味着调用序错误）
- 部分失败（第 k 个文件复制失败）-> 报错，并把本轮已写入的 work 侧路径清理干净（不留半套 raw），MUST NOT 让 `raw-manifest.json` 与实际副本不一致
- 副本与 manifest MUST 全部落在 `work_dir` 之下；`YD_ROOT` 模拟根内 MUST 不出现任何 raw 副本

#### Invariant Matrix

Governing invariant: **本轮 manifest 声明的每一条 (lead, variable) 都对应一个已落在 `work/raw/` 之下的真实副本，其语义键逐字承接自源 manifest；三者中任何一项不成立时，整个 staging 以异常终止且不留任何部分产物。**

Source-of-truth identity/contract: `(entry.forecast_hour, entry.variable, entry.local_key)` 三元组，及其 `idx_selector` 累积语义子 Mapping

Surfaces:
- Producers: `rawcopy.stage_raw`（唯一写面）
- Validators/preflight: `verdict.complete` 闸门、源 manifest 解析与覆盖检查、R4B2 fail-closed 检查、复制前后 lstat 比对
- Storage/cache/query: `<work_dir>/raw/<存储身份>/<cycle>/` 副本树、`<work_dir>/raw-manifest.json`
- Public routes/entrypoints: none —— CLI 接线归组 12 控制器（本 issue 只交付库函数）
- Frontend/downstream consumers: 任务 7.1 canonical converter（经 `local_key` + `object_store.resolve_path` 与 `idx_selector` 消费）
- Failure paths/rollback/stale state: 部分复制失败的 work 侧清理；`complete=False` 与 fail-closed 的零写入拒绝
- Evidence/audit/readiness: `StagedRaw` 返回值、`raw-manifest.json` 自身

Regression rows:
- `stage_raw` + 完整 verdict + 带完整 `idx_selectors` 的源 manifest -> 副本齐全、manifest 三元组集合与 `expected_variables` 相等、entry 路径全部解析到 `work/raw/` 之下
- `stage_raw` + `complete=False` 的 verdict -> `kind="incomplete-verdict"`，且 `work_dir` 下零新增路径
- `stage_raw` + 源 manifest 缺 apcp 的 `accumulation_type` -> `kind="accumulation-metadata"`，且 `work_dir` 下零新增路径（不落半个 manifest）
- `stage_raw` + 源 manifest 的 `accumulation_type` 取域外值（如 `"unknown"`）-> `kind="accumulation-metadata"`（不静默降级成 cumulative）
- `stage_raw` + `accumulation_type == "interval_bucket"` 但缺 `step_range` -> `kind="accumulation-metadata"`
- `stage_raw` + 源 manifest 缺 manifest 级 `forecast_hours` -> `kind="source-manifest"`
- `stage_raw` + 源 manifest 某条 entry 的 `cycle_time` 不等于本轮 cycle -> `kind="source-manifest"`，零写入（round 5 补立：承接的时间标记与本轮自算不一致时 fail closed）
- `stage_raw` + 源 manifest 某条 entry 的 `valid_time` 不等于 cycle + 该 entry 的 lead -> `kind="source-manifest"`，零写入
- `stage_raw` + 源 manifest 的 entry 时间写成另一时区偏移但指向同一时刻（如 `-05:00`）-> **正常产出**（该行是上两行的对照：只有它能证明时间闸门比的是**时刻**而不是文本；删掉它，上两行仍可由一个逐字文本比较满足）
- `stage_raw` + 第 k 个源文件在复制中途被替换（lstat 前后不一致）-> `kind="source-mutated"`，work 侧不留半套副本
- `stage_raw` + `raw_root` 形参指向另一个根（与 verdict 由不同调用点产生）-> `kind="verdict-mismatch"`，零写入
- `stage_raw` + **相对** `raw_root` 且与 verdict 同源（合法调用）-> 正常产出（该行是上一行的对照：只有它能证明 containment 检查没有把合法的相对路径调用一并误拒）
- `stage_raw` + 目标路径已存在同名文件 -> `kind="target-exists"`，MUST NOT 覆盖
- `stage_raw` + `len(bundles) == 2` 的合法配置 -> `kind="unsupported-layout"`，零写入（多 bundle 下 variable→bundle 无定义）
- `stage_raw` + 源 bundle 路径本身是 symlink（指向同根内的真实文件）-> `kind="source-symlink"`，零写入；**同一 fixture 下 `judge` 返回 `complete=True`**（钉死 3.1/3.2 的有意不对称）
- `stage_raw` + cycle 目录段是 symlink -> `kind="source-symlink"`，零写入（祖先段检查，不只查叶子）
- `stage_raw` + 第 k 个文件复制时目标不可写（权限/ENOSPC）-> `kind="copy-failed"`，work 侧不留半套副本（与 `source-mutated` 分属两条清理路径，各自具名）
- `stage_raw` + 源 manifest 的 `forecast_hours` 不覆盖本轮某个 lead -> `kind="source-manifest"`，零写入
- `stage_raw` + 源 manifest 缺 `requested_forecast_hours`（IFS 的 pin 形态）-> **正常产出**，yd 自己写该键 = `lead_hours` 全集（钉死「不对源侧强制」）
- 产出的 `raw-manifest.json`（正向 schema 断言）-> `DownloadManifest.from_dict(json.load(...))` roundtrip 成功；`source_id == SOURCE_DIR_NAMES[source]`（逐源大小写非对称，本仓已记录的 CONFIRMED/FIX_NOW 陷阱）；每条 entry 的 `metadata` 含且仅含承接的 6 键加 `idx_selector`/`idx_selectors`（**IFS 源两个 idx 键均不出现**——pin 侧 IFS 无累积语义，写空 Mapping 等于发明一个「查过了、是空的」的声明；该断言对 IFS 是「两键缺席」而非「两键为空」）；`idx_selector` 是该变量的 Mapping 而非被整个 `idx_selectors` 塞进去；manifest 级四键齐全且 `forecast_hours == requested_forecast_hours == sorted(expected_variables)`；`expected_checksum`/`expected_size_bytes`/`manifest_uri` 三者均为 `None`
- `stage_raw` + IFS 源（无 apcp、无 idx_selectors）-> 正常产出，R4B2 第 1 条不触发（作用域证据）
- `stage_raw` + GFS `f000_special` 生效 -> lead 0 的 entry 变量集等于 `verdict.expected_variables[0]`，且 lead 0 的**文件**仍在副本集内（f000 只削变量集不削文件集）
- 源文件树（不变的兄弟面）-> 全部 `lstat` 元组在调用前后逐字相等；NWM 根下零新增/删除/改名
- `YD_ROOT` 模拟根（不变的下游消费面）-> 调用前后内容相等

#### Boundary-surface checklist

- Shared helper roots: `rawscan.SOURCE_DIR_NAMES`（复用不复制）、`raw/manifest.py` 的两个 dataclass（构造不改类）
- Public entrypoints: none（组 12 接线）
- Read surfaces: NWM raw 树（只读）、源 `manifest.json`
- Write/delete/overwrite surfaces: `<work_dir>` 内副本与 manifest；失败清理路径
- Staging/publish/rollback surfaces: 本 issue 全在 work 内，不触 `YD_ROOT` 发布面
- Producer/consumer evidence boundaries: `raw-manifest.json` ↔ 任务 7.1 converter
- Stale-state/idempotency boundaries: 目标同名存在即拒绝（不覆盖、不续跑）
- Unchanged downstream consumers: `rawscan` 的既有全部用例、`test_manifest.py` 的 pin 快照用例

#### Required evidence（`cd producer && uv run pytest`）

上方 Regression rows 的**每一行**对应一个具名 pytest 用例，且该用例 MUST 由一个能证伪它的变异体验证过（"有一个用例指向它"不构成覆盖）。另加：

- **承接/自算可区分谓词（round 3 门限的纠正动作，MUST 作为一次穷尽清扫兑现，不是逐条打补丁）**：凡产出 `raw-manifest.json` 里的**每一个字段**（manifest 级与 entry 级，含 `metadata` 的每个子键），合成源 manifest 里的对应值 MUST 被偏移，使「承接自源」与「由 yd 自算」两种实现产出不同结果；每一处发散 MUST 由一个变红的变异体证明。该谓词在测试侧以一条自检用例落地。
  - **定义域勘误（round 4 门限）**：本条原写作「凡测试**对产出 manifest 断言的**每一个值」。那个定义域是错的——它把「未被任何用例断言的承接值」结构性地排除在谓词之外，于是谓词再穷尽执行也发现不了它们，而交付当天就有两个实例（复数键 `idx_selectors` 的**值**从未被断言，整份伪造它的变异体全套件绿；`grib_short_name` 同样在清扫表外）。判别器的**定义域**取错，与判别器取在表象轴上是同一类错误，且这一处是编排者自己写进 fixture 的。定义域改为「产出 manifest 的每一个字段」后，「有没有用例断言过它」不再影响它是否受谓词约束。
  - **类边界（有意排除，勿"修"）**：`forecast_hour` 与 `variable` 允许与源侧重合——它们是 `_index_source_entries` 的查找键，偏移它们会破坏查找关系本身而不是判别实现。这两项 MUST 在清扫表里显式标注为排除项并写明理由。
  - 立此谓词的原因：该失效类在 round 1 找到 1 条腿、round 2 找到 6 条、round 3 又找到 2 条。逐轮清点是搜索，不是闭合；有了可复核的谓词才谈得上闭合条件。
- **序列化前置于复制**：本轮 manifest 的序列化（`_render_manifest`）MUST 排在任何复制之前，且 MUST 位于准入段兜底地板的 `Try` 体之内。
  - **判别器（round 5 门限补立）**：MUST 有一条由 AST 断言 `_render_manifest` 的调用点落在地板 `Try` 体覆盖范围内的用例；杀手变异体是把该调用移出地板——移进写入段 `try`（SERIAL）、或移进地板的 `orelse`（ORELSE）——二者 MUST 各自变红。MUST NOT 以「编码严格性」类变异体（如改 `errors="surrogatepass"`）充当本条的杀手变异体：那测的是**能否编码**，不是**位置**。
  - **理由勘误（round 5 门限）**：本条原称违反它「会留下『副本全落地 + 0 字节 manifest』的半套产物」。**该后果不会发生**，round 5 实测证伪：`_render_manifest` 自己抛 `RawStagingError(kind="source-manifest")`，被写入段的 `except RawStagingError` 接住后 `written.rollback()` 再抛，0 字节 manifest 从未被创建，`snapshot(work_dir) == {}` 仍成立（SERIAL 变异体下 `test_rawcopy.py` 该用例照常通过）。真实代价是：序列化后置把一段**注定失败**的输入的失败点推到复制之后，此时零写入转而依赖**回滚自身成功**，而回滚失败是本 fixture 另行承认的可失败动作。约束成立，原写的理由不成立；`test_rawcopy.py` 中复述该理由的 docstring MUST 一并订正。
  - （沿革：该约束原写在 kind 表 `source-manifest` 行的括注里，round 4 改写该行时被编排者一并删去；round 4 恢复时未配判别器，round 5 实测 SERIAL 变异体全套件 807 绿、零红条。两处都是编排者的疏漏。）
- **准入段兜底地板**：`stage_raw` 的准入段（形参守卫直到 `target-exists` 预检）MUST 整体位于**函数体第一条语句**的 `try` 内，任何非 `{ConfigError, RawStagingError}` 的异常 MUST 被收敛成 `RawStagingError`，`kind` 取 `ADMISSION_FALLBACK_KIND = "source-manifest"`（本行即该取值的钉死处）。**该取值是位置轴上的一条显式例外，不是因果规则的一个实例**：地板按**位置**兜住准入段的一切非词表异常，而 `source-manifest` 的归属规则是**因果**的；两者定义域不同，准入段 21 个注入点里多数（路径归一、containment、`target-exists` 预检等）与源 manifest 无因果关系。选既有 kind 而非扩表，是因为九项词表由本 fixture 钉死且其判别力问题已归 #76；代价是调用方无法凭 kind 区分「上游清单坏了」与「yd 准入段自身出错」，此限制 MUST 出现在 Known limits。（勘误：本行原称该取值「因果落在源侧外部 JSON，与该 kind 的归属规则一致」——被它自己的判别器证伪，round 4 CONFIRMED。）`BaseException` MUST NOT 被改写。判别器 MUST 是**由地板 try 体的 AST 派生**的参数化逃逸探针（新调用点自动入列），**外加三条范围断言**：钉 `stage_raw` 顶层语句形状为 `[Try, Assign, Try, Return]`；钉地板 `Try` 体的**首尾语句**为准入段的具名端点（`verdict.complete` 检查 … `target-exists` 预检）；并钉地板 `Try` 的 `orelse` 与 `finalbody` **均为空**。
  - **判别器勘误（round 4 门限）**：本条原要求的是「一条断言地板确实是 `body[0]`、写入段是 `body[1]` 的结构用例」。那条判别器**在证明上无法执行同一 bullet 里的 MUST**：把一条准入语句移到 `body[2]` 即违反 MUST，而该断言满足、全套件绿，且该语句**自我退出**探针参数集（21→20）而无人断言该损失。范围必须钉在**端点**上而不是槽位上。
  - **覆盖轴勘误（round 5 门限）**：端点断言仍不够——它只钉 `Try.body`，而 `try/except/else` 的 `else:` 体中抛出的异常**不被本 try 的 handler 捕获**（Python 语义）。把一条准入语句从 `body` 移进同一 `Try` 的 `orelse`：顶层形状不变、首尾端点不变、全套件 807 绿、该语句自我退出探针参数集（21→20），而注入的 `RecursionError` **逃出封闭词表**——即违反本 bullet 的第二个合取项。这与 round 4 的 M1 是同一类错误换了个位置：轴从「槽位」换到了「端点」，却没有换到**覆盖**本身。故补第三条范围断言，`finalbody` 一并钉空（把语句放进 `finally` 虽会红 81 条，但不能靠副作用兜底）。MUST NOT 改为钉 `len(ADMISSION_INJECTION_TARGETS)`——计数会在每次合法新增时变红，正是本 PR 四轮反复失败的枚举模式
- 源不可变的取证 MUST 是 `os.lstat` 全元组比对（size/mtime_ns/ino/mode），MUST NOT 只比对内容——只比内容抓不到 mtime 被改
- 三元组完整性 MUST 断言**集合相等**，MUST NOT 断言包含；两个方向各需一个杀手变异体（漏一条 entry / 多一条 entry）
- 零写入拒绝的取证 MUST 是调用前后对 `work_dir` 做递归快照比对，MUST NOT 只断言 `manifest_path` 不存在
- 变异实验按 profile 的 "Mutation-testing hazards" 与 "Orchestration hazards" 两节执行：唯一命名的私有 scratch 副本、`rsync --exclude='.venv'`、`env -u VIRTUAL_ENV uv sync --frozen`、`PYTHONDONTWRITEBYTECODE=1` 且逐变异体清 `__pycache__`、断言 `yd_producer.__file__` 落在副本内、先跑一个必然变红的控制变异校准
- 闸门枚举 MUST 用 **AST 遍历全部可执行语句**（不用 grep——grep 看不见 `status = path.stat()` 这类值传播闸门），逐条落进「表内」或「死腿登记」两桶，审计表作为交付物落盘到 `.workplans/pr-<N>/review/`
- DB-free 隔离检查（**刻意不写成「禁区 grep：」**：该前缀是组 2 词表声明集的唯一锚，`test_snapshot_provenance.py::test_forbidden_surfaces_match_the_declared_grep` 断言全文恰有一条，第二条会把那条判别器打红）：`grep -rnE 'psycopg|DATABASE_URL|scheduler|registry|reservation' producer/src/yd_producer/rawcopy.py` -> 零命中
- `cd producer && uv sync --frozen` -> 无 lock drift（「不新增依赖」是 Must-preserve 项，本条是它的判别器）
- `cd producer && uv run ruff check . && uv run ruff format --check .` -> 退出码 0
- `openspec validate m2-producer-core --strict --no-interactive` -> 退出码 0

#### Non-goals

- canonical 转换、forcing 组装（组 6/7/8）——本 issue 只产出 manifest 与副本
- CLI/控制器接线、前沿推进、work 目录生命周期管理（组 12）——本 issue 只交付库函数，调用序由控制器负责
- **扫描→复制窗口的残余 TOCTOU**：本 issue 以复制前后 lstat 比对收敛「复制期间源被改」这一半；「judge 判完整之后、stage 调用之前源被删」属调用方时效性，归组 12
- **不为 IFS 发明累积语义**：pin 侧 IFS 无 `idx_selectors`、`IFS_VARIABLES` 无 `apcp`，R4B2 只约束 GFS。IFS 的 `tp`/`ssr`/`str` 同为累积量但 pin 未给元数据——在 yd 侧补一套等于发明语义，明确不做，记为已知限制并路由 follow-up
- 不实现第二套下载器、不在 NWM 缺件时代为补齐（compute-loop §4.1）
- 不做 GRIB 内容校验（归 M4 receipt）——"可读"只到能发起读为止
- 不改 `rawscan.py`：若实现中发现 `ScanVerdict` 形态不够用，记为偏离并上报，MUST NOT 顺手改 #6 已合入的契约

#### Known limits（MUST 出现在 PR 已知限制，逐条带 follow-up issue 或不落 issue 的一行理由）

- NOMADS 下载路径的 GFS cycle 会在 R4B2 上整轮 fail closed（上方 fail-closed 第 5 条）
- IFS 累积语义缺口（上方 Non-goals 第 4 条）
- `work_dir` 的创建/清理生命周期归组 12，本 issue 只保证自己不留半套
- 目标侧**无**逐段 symlink 拒绝：`work_dir` 之下的链段会让副本物理落在 work 树之外（源侧有 `_reject_symlinks` 逐段 `os.lstat`，目标侧的 `_ensure_dir` 用跟随 symlink 的 `exists()` 探测）。round 1 verifier CONFIRMED/DEFER，承重理由只有一条：它与「work 是全新单次目录」这条组 12 的契约是同一个设计决策、应一起落地。（勘误：本行原以「下游 `object_store` 的 `*_no_follow` 已实测 fail-closed」作降级理由。该观察本身为真，但**用错了侧**——`rawcopy` 全文不经 `object_store`，写侧四个原语 `mkdir(parents=True)`/`os.open`/`unlink`/`rmdir` 全是裸的、都跟随父目录链，读侧的 no-follow 撑不起写侧降级。round 2 verifier CONFIRMED/FIX_NOW。）severity 维持 minor，依据是写侧爆炸半径有界（work 为一次性隔离单元）。follow-up：**issue #71**
- `raw.<source>.variables` 轴无单射性闸门，重复变量会产出重数 >1 的三元组（本 issue 的集合相等断言对重数失明）。归配置取值域，follow-up：**issue #72**（并已补入任务 2.x 的取值域归属账本）
- 多 bundle 配置（`len(bundles) != 1`）在 staging 侧不受支持，需 config 长出 variable→bundle 映射后才能放开（上方单 bundle 约束）
- 源侧 symlink 一律拒绝，与 3.1 的 `is_file()` 跟随语义有意不对称（上方复制语义）
- **源 `manifest.json` 叶子段自身不查 symlink，本 issue 有意排除**：symlink MUST 的作用域由上方复制语义逐字限定为「每个源 **bundle 路径**」，Regression rows 有链 bundle 行与链 cycle 目录行、无叶子行。该叶子的**全部祖先段**已被 bundle 走查覆盖，故缺口仅限「叶子自身是链」这一形态。round 1 verifier REFUTED、round 2 复现机制为真后仍维持 REFUTED（无绑定文本归属，verifier 不以翻转判决来裁定范围）。若后续要收口，属 fixture 修订而非实现缺陷
- **地板兜底 kind 无法与真实源侧失败区分**：`ADMISSION_FALLBACK_KIND = "source-manifest"` 让准入段的任何非词表异常都以该 kind 外抛，而准入段多数注入点（路径归一、containment、`target-exists` 预检）与源 manifest 无因果关系。后果是组 12 控制器按 kind 分流时，无法区分「上游 NWM 清单坏了、该 cycle 不可用」与「yd 准入段自身出了未预期的错」——两者运维处置不同。不扩第十项是因为九项词表由本 fixture 钉死、其判别力问题已归 **#76**；本条随该 issue 一并裁决。
- **目标侧 symlink containment 的 inode 判据在 CI 上无判别器**：唯一支撑用例在大小写敏感卷（CI 的 ubuntu-latest/ext4）上必然自跳过，而无特权的无条件 seam 级判别器不可构造。补救属 CI 作业面或 fixture 作用域，不是「补一个用例」。follow-up：**issue #89**
- **九项闭合 kind 词表本身无判别器**：九个 kind 各有具名用例与杀手变异体，但测试从不 import `ERROR_KINDS`，加入第十项的变异体在 747 passed 全绿下存活（round 2 实测，同环境控制变异体 13 failed，harness 已校准）。今日无运行期影响故判 DEFER，但本模块最强的 oracle 锚（逐字钉死的闭合集）目前不设防。follow-up：**issue #76**
- **产出 manifest 不拒 `NaN`/`Infinity`**：`json.dumps` 默认 `allow_nan=True`，源侧若带非标准 JSON 字面量会原样穿过序列化闸门，严格解析器读不了（round 2 实测）。判 DEFER 的依据是唯一具名契约边界未断——pin 侧 converter 用 `json.load`，默认同样接受。加固成本为一个实参 `allow_nan=False`（抛的 `ValueError` 直接落进现有 except、kind 仍为 `source-manifest`）。follow-up：**issue #75**
- **六键逐字承接后无内部一致性核对**：`grib_short_name` 与 `cfgrib_filter_by_keys["shortName"]` 在 pin 上同源同值（`nwm-snapshot-inventory.md:109`），yd 两者都逐字承接却从不核对二者相等，故源侧改动其一即产出一份自相矛盾的 `raw-manifest.json` 而 staging 照常成功（round 5 实测复现）。消费端 `_cfgrib_backend_kwargs` 优先取 `cfgrib_filter_by_keys`（`:110`），converter 会静默读错 GRIB 变量——该后果由 §3.1 转录**推理**得出、未实跑（converter 属任务 7.1，不在本 PR）。判 DEFER 的依据：两个操作数均逐字承接，治理不变式的「承接」合取项**未破**，矛盾是继承而非 yd 制造，弱于 round 5 修掉的 entry 时间闸门（那一处矛盾的是 yd **自算**的字段）；且该承接循环先于本轮存在，`d6a8733` 未改动它。follow-up：**issue #99**
- **entry 时间闸门依赖「pin 侧 `valid_time` = cycle + forecast_hour」这一算法**：§3.1 只记录了六键的存在与注入时机，未记录该算法；支撑证据是本仓逐字副本 `raw/manifest.py:51-52` 的 `valid_time_for`（§3.1:45 将该文件登记为转录）。若 pin 对**区间累积**变量另按区间端点写 `valid_time`，本闸门会对每一个 cycle 硬拒；而按上方「地板兜底 kind」一条，组 12 无法凭 kind 把它与「yd 自身出错」区分开。不落 issue 的理由：该形态在 pin 当前源码上不成立，属 pin 演进时才需重估的假设，本条即其登记点。

#### Review focus

- 三元组完整性是否断言的是**集合相等**而非包含，两个方向是否各有杀手变异体
- 零写入拒绝是否真的零写入（含目录），取证是否为递归快照比对
- `local_key` 是否为 object-store key 形态而非绝对文件系统路径，`resolve_path` 后是否确实落在 `work/raw/` 之下
- `idx_selector` 是否从复数键**按变量**取，而非把整个 `idx_selectors` 塞进单数键
- R4B2 的域检查是否覆盖别名（`accumulation_policy`/`stepRange`），且是否**没有**引入 `or "cumulative_since_cycle"` 之类的默认
- 是否出现按 `source == "gfs"` 的硬分支（应由 `variable == "apcp"` 与配置驱动）
- 存储身份是否复用 `rawscan.SOURCE_DIR_NAMES` 而非另抄字面量
- 源不可变断言是否含 mtime_ns 与 ino（只比内容不算）
- 单 bundle 约束是否在**任何写入之前**短路，且是否真的以 `len(bundles)` 判而非以「渲染出几个文件名」间接判
- symlink 检查是否覆盖**祖先段**而不只是叶子；拒绝是否发生在复制之前
- 源侧是否只对 `forecast_hours` 强制、`requested_forecast_hours` 确实**不**强制（IFS 用例是这条的判别器）
- 九个 `kind` 是否各有具名用例与杀手变异体；是否有裸 `OSError`/`KeyError`/`JSONDecodeError` 从 `stage_raw` 穿出
- 是否引入 stdlib 之外的依赖，或运行时 import NWM

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

- [x] 7.1 快照 DB-free canonical converter 并以合成 raw fixture → canonical NetCDF + catalog 端到端测试（无数据库连接断言）

依赖：组 2、组 3（manifest）、组 6（依赖）
§13.1 归属：DB-free 链
Suggested fixture level: expanded - 需构造可被 cfgrib 读取的合成 GRIB 样本，fixture 制作本身有分量
Minimal mergeable slice: atomic - converter 与其端到端测试互为验证，先合无测试的 converter 或无 converter 的 fixture 都不构成独立绿（依赖引入已剥离到组 6）

### Issue #13 fixture（任务 7.1）

Fixture level: expanded
Upstream suggested level: expanded（agree，无 override）
Repair intensity: high（写侧落 canonical NetCDF 与 catalog 到 object-store；catalog 是组 8 唯一的 canonical 产物真相，属 producer/consumer evidence 边界；溯源守卫与 DB-free 禁区守卫的期望落地集随本 PR 扩面。适用 `Invariant Matrix`）
Project profile: yd-viewer

**上游契约偏离（consumed not renegotiated，须回流 stage-change-pipeline sizing-retro）**：

1. **issue #12（任务 6.1）的依赖引入不覆盖 NetCDF 写引擎，7.1 在现锁上不可实现**。pin 的 `CanonicalConverter._serialize_product`(converter.py L1861-1900) 硬 `import netCDF4` 且 `to_netcdf(engine="netcdf4", format="NETCDF4")`；读侧 fallback L1474 同样是 `engine="netcdf4"`；`producer/uv.lock` 内 `netCDF4`/`h5netcdf`/`scipy` 三者皆无。故本 PR **必须**引入 `netCDF4` 并 `uv lock`，作为本任务的伴生动作显式提交（与任务 6.1 自己写的「必要时 CI 补系统依赖，作为依赖引入的伴生动作显式提交」同一姿态）。已实测 `netCDF4==1.7.4` + xarray 写读 roundtrip 在 darwin 通过；ubuntu CI 走 manylinux wheel，**不新增 CI 系统依赖步骤**——若 CI 实测需要 HDF5 系统包，按 `ci-only` 修复补 `apt-get`，不改本裁决。
   **MUST NOT 改引擎**（h5netcdf / scipy）：那是对忠实快照的一处未登记语义编辑，且 scipy 根本写不了 NETCDF4 格式。
2. **issue #13 的 In Scope 写「无数据库连接断言」，但未说明断言形态**。本 fixture 裁决 6 给出可机检的形态。

**核心设计裁决（本 fixture 钉死，实现不得自行改写）**：

1. **清单 §1 第 35/51/52 行的 `剥离点` 列是本任务的封闭规范，逐字执行、不得自行增删**。清单约定 3 明写「规范性动作只能写在 `剥离点` 列」，故：`剥离点` 点名的动作 MUST 全部执行；`剥离点` **未**点名的符号、分支、常量 MUST 原样保留，即便它在剥离后变成无调用者的死代码。实现者对任一条有异议时，MUST 作为偏离上报，MUST NOT 自行裁决。
2. **落码方式 MUST 是 `git show` 基线复制 + 定点编辑，MUST NOT 手抄**。三个文件各自的基线命令写死：
   - `git -C <NWM> show 8ae9b8f2:workers/canonical_converter/converter.py > producer/src/yd_producer/canonical/converter.py`
   - `git -C <NWM> show 8ae9b8f2:tests/test_canonical_converter.py > producer/tests/test_canonical_converter.py`
   - `git -C <NWM> show 8ae9b8f2:packages/common/test_netcdf4.py > producer/tests/netcdf_fixture.py`（清单 §1 第 52 行的强制改名：原名会被 pytest 误收集）

   `<NWM>` = 本机 `/Users/danker/Desktop/Hydro-SHUD/NWM`（pin `8ae9b8f2` 已实测可读）。基线之上**只允许四类编辑**，任何第五类编辑都是偏离。**yd 自撰的新用例 MUST NOT 写进这三个快照文件**（写进去就在 diff-vs-pin 里造出无法归类的差异段，把裁决 2 的机械收敛证据废掉）：它们落在未登记的新文件 `producer/tests/test_canonical_db_free.py`（yd 自撰，无溯源头，不进清单路径表）：
   (a) 清单 `剥离点` 点名的删除/改写；
   (b) import 重映射：`packages.common.object_store` → `yd_producer.store.object_store`、`packages.common.storage` → `yd_producer.store.object_path`、`packages.common.source_identity` → `yd_producer.raw.source_identity`、`packages.common.test_netcdf4` → `netcdf_fixture`、`workers.canonical_converter.converter` → `yd_producer.canonical.converter`（全集以实跑 `grep -n '^from \|^import \|importlib.import_module' ` 收敛，逐个报告）；
   (c) 溯源头部注释（裁决 3）；
   (d) `uv run ruff format` 重排版 + `ruff check --fix` 的**全部**自动修复（**round-1 放宽**：原措辞只写「import 排序」，但 ruff 0.16.4 对 pin 代码还会产出 `UP034` 去括号等 autofix，且留着它们会挂 CI 的 `uv run ruff check .`——即原措辞把一类机械强制、语义惰性的编辑判成了偏离。放宽后仍要求逐条在忠实度证明里点名 autofix 规则码）。**(d) 是既有先例而非本轮发明**：`producer/src/yd_producer/store/safe_fs.py` 与 pin 相差 226 行、差异全是 88 列换行与溯源头（NWM 用 `line-length=120`，yd 用 ruff 默认 88）。
3. **溯源头部的形式由 `specs/forcing-chain/spec.md`「快照模块可追溯」逐字约束，正反两向共用同一谓词**。三个快照文件各写一条**独立 `#` 注释行**，注释内容**恰为**：
   - `producer/src/yd_producer/canonical/converter.py` → `NWM@8ae9b8f2 workers/canonical_converter/converter.py`
   - `producer/tests/test_canonical_converter.py` → `NWM@8ae9b8f2 tests/test_canonical_converter.py`
   - `producer/tests/netcdf_fixture.py` → `NWM@8ae9b8f2 packages/common/test_netcdf4.py`

   路径后 MUST NOT 有任何尾随内容（含 `:<行号>`、括注、说明文字）；写在 docstring 或字符串里不算数。
   **`producer/src/yd_producer/canonical/__init__.py` 是 yd 自撰文件，MUST NOT 带溯源头部形式的注释行**——它不在清单路径表内，带了就触发反向守卫（spec「未登记快照文件的反向守卫」）。
4. **清单 §1 第 35/51/52 行的 `落地状态` MUST 由 `待落地` 翻成 `本 issue 落地`**。这不是记账：`落地状态` 是溯源守卫的**期望落地集**来源（清单 §1 序言），不翻面则守卫的期望集不含这三个新文件，issue 验收标准「溯源头部检查覆盖新模块」**静默落空**——正向检查一条都不会跑到新模块上。翻面后 `producer/tests/test_snapshot_provenance.py` 自动扩面，MUST 实跑证明它对新三个文件确有断言（删掉任一溯源头即变红）。
5. **§4 风险 7 的落码期确认已由本 fixture 完成，实现者只需复核不需裁决**（风险 7 原文要求「在 7.1 落码时确认，避免删过头」）：
   - `evaluate_canonical_readiness`(L403-581) **零** ERA5 分支（`grep -n -i era5` 在该区间无输出），原样保留，不得因「ERA5 面」误删。
   - `剥离点` **未点名**的 ERA5 残面共 6 处，按裁决 1 **全部保留**：`ERA5_VARIABLE_MAPPING`(L81)、`ERA5_STANDARD_UNITS`(L112)、`ERA5_REQUIRED_STANDARD_VARIABLES`(L142)、`REQUIRED_STANDARD_VARIABLES_BY_SOURCE` 的 `"ERA5"` 条目(L153)、`compute_relative_humidity_values`(L846)、`convert_era5_radiation_values`(L891)。后两者的唯一调用点（L2050 / L2155）落在被删的 `ERA5CanonicalConverter` 体内，剥离后成为无调用者的模块级函数——**这是刻意的保留，不是遗漏**，ruff 不报未使用函数。保留用例对这 6 处零引用。
   - **DB-free 剥离使三处分支成为不可达死代码，按裁决 1 一律保留、不得顺手删**（fixture 复核实测，pin 行号）：`_get_existing_product`(L1902-1909) 在 `repository is None` 时无条件 `return None`，故 `_existing_product_is_current`(L1911-1934) **仍被调用**(L1767) 但首句 `existing is None` 即 `return False`，其后半身（converter_version 比对、checksum 比对、object-store 存在性核验）永不可达；`_convert_record` 的 `status="already_done"` 分支(L1775) 整支不可达（`existing` 恒 `None` ⇒ 状态恒 `"created"`，L1811 三元式）。`_upsert_product`(L1936-1943) 与 `_update_cycle_status`(L1945-1965) 在 `repository is None` 时是 no-op，故 pin 的「fail 产物记录」与「cycle 状态置 `failed_convert`」两个面在 yd 侧**整体消失**——这是 DB-free 的必然后果，不是实现缺陷。
   - `REQUIRED_STANDARD_VARIABLES_BY_SOURCE` 的 `"ERA5"` 键在 `normalize_source_id` 删掉 ERA5 条目后不可达（`required_standard_variables_for_source("ERA5")` 会在归一化处抛 `ValueError` 而非 `CanonicalConversionError`）。**本 issue MUST NOT 顺手"修"它**：它是清单未点名的继承矛盾，与 `:53` 记录的六键承接矛盾同类，按裁决 1 保留并作为偏离上报。
6. **`剥离点` 的「改断言 object-store catalog」这条指令在 4 个保留用例上不可直译，本 fixture 逐例拍板，实现者不得自行裁决**（fixture 复核实测；清单第 51 行给的是**改写指令**而非逐例处置，此处补齐它没有预见的 4 个「oracle 在 DB-free 下结构性缺席」的情形。结论 MUST 回填清单第 51 行 `备注`）：
   - `test_conversion_is_idempotent_on_rerun`(tcc L785)：**保留并改写**。pin 的 oracle 是 `repository.upsert_count` 与 `{"already_done"}` 状态集，二者在 DB-free 下都不存在（见裁决 5 的死分支登记）。DB-free 下唯一可断言且有判别力的幂等语义是**重写幂等**：两次 `convert_manifest` 各产出 14 份产物、状态集恒为 `{"created"}`，且**第二次跑完后每份 canonical 产物的 checksum 与 catalog 的 JSON 字节与第一次逐字节相同**。MUST NOT 断言「第二次不写盘」——pin 在 DB-free 下确实会重写，断言不写盘等于发明 pin 没有的行为。
   - `test_quality_flag_fail_triggers_reconversion`(tcc L839)：**删除**。其前提是先在 repository 里把已有产物的 `quality_flag` 改成 `fail` 再重跑，DB-free 下没有任何可写入的前置状态面，用例**不可构造**。这是一处**刻意的净覆盖损失**：它覆盖的 `_existing_product_is_current` 的 fail-flag 分支在 DB-free 下本就不可达（裁决 5）。MUST NOT 为了保住它而把 repository 缝搬回来。
   - `test_missing_required_variable_marks_cycle_failed_and_records_fail_product`(tcc L979) 与 `test_missing_variable_for_one_forecast_hour_records_specific_fail_product`(tcc L995)：**保留并改写**。`pytest.raises(CanonicalConversionError, match=...)` 与消息断言（`"Missing required canonical variables"` / `"dswrf->shortwave_down f003"`）在 DB-free 下完全成立且是本 issue 最有价值的失败路径 oracle，逐字保留；对 `repository.products[...]` / `repository.cycles[...]` 的断言（fail 产物行、`status == "failed_convert"`、`error_code == "CONVERT_FAILED"`）随其记录面消失而**删除**，替换为**取反方向**的可机检断言：该 cycle **没有**写出 catalog（`canonical/<source>/<cycle>/_catalog/catalog.json` 不存在），且缺失那一对 `(变量, lead)` **没有**写出 canonical 产物对象。该替换有真实判别力——`convert_manifest`(pin L1224-1228) 在任何产物写入**之前**就检出 missing pairs 并抛错，catalog 只在成功路径的 `_complete_cycle_after_conversion` 里写，故「失败即零产物零 catalog」是可被变异证伪的性质。
   - 这 4 例之外，其余带 `repository` 断言的保留用例按清单第 51 行原指令改断言 catalog 或改断言 `ConversionResult`，逐例在报告中列出改法。
   - **`test_conversion_without_repository_preserves_lineage_for_identity_readiness`(tcc L817) 的直接构造须同时去掉 `repository=None` 实参**（`剥离点` 已删该形参，留着即构造期 `TypeError`），并按清单第 51 行的跨行处置在 tcc L826 的 `CanonicalConverterConfig(...)` 补 `object_store_root=tmp_path` 与 `object_store_prefix=""` 两个 kwarg；`build_converter` 内的 tcc L109 同样补这两个 kwarg。

7. **「无数据库连接」断言 MUST 是可机检的三条，MUST NOT 用「代码里没有 psycopg 字样」这种代理量单独充数**：
   - **静态**：`producer/src/yd_producer/canonical/` 下零 `psycopg` / `DATABASE_URL` / scheduler / registry import 与环境变量读取（复用 `producer/tests/test_snapshot_provenance.py` 既有的禁区检查形态，扩面到新目录）。
   - **动态（本条是判别力承重条）**：端到端转换用例执行期间，MUST 断言**没有发生任何出站 socket 连接**——以 `monkeypatch.setattr(socket.socket, "connect", <fail>)` 一类的运行期闸门实现，而不是只读源码。理由：静态检查对「运行期动态 import 一个 DB 驱动」零判别力，而本 issue 的验收正是运行期性质。该闸门 MUST 覆盖完整的 `convert_manifest` 路径（读 raw → 转换 → 写 NetCDF → 写 catalog）。
   - **构造面**：`CanonicalConverter.__init__` 与两个保留子类的签名里 MUST 不存在 `repository` 形参，且 `CanonicalRepository` Protocol 已不存在（`hasattr(converter_module, "CanonicalRepository") is False`）。
8. **合成 GRIB 端到端用例是 yd 自撰的新增交付物，与快照的 40 个用例并列，MUST NOT 用它替换快照用例**。理由与形态：
   - pin 的测试套按设计走 **NetCDF fallback**（`packages/common/test_netcdf4.py` 的 docstring 逐字写着 "replaces mock_grib for test data generation"），故 40 个快照用例里**没有一个真正让 cfgrib 解码过一个字节**：`xr.open_dataset(file_path, engine="cfgrib")`(pin L1466) 在每个用例上都抛错并落进 L1474 的 netcdf4 回退。**措辞须精确到符号**（fixture 复核更正）：`_cfgrib_backend_kwargs` 与 `_select_cfgrib_data_variable` 两个 helper **已有**保留用例覆盖——`test_cfgrib_variable_mismatch_does_not_fallback_to_first_data_var`(tcc L1008) 直接调 `_select_cfgrib_data_variable`，`test_bundle_entries_open_cfgrib_with_entry_specific_filter`(tcc L1024) 以假 xarray 断言 `engine == "cfgrib"` 与 `backend_kwargs` 的形状；且这两个 helper 在回退路径上照样执行（L1464 在 try 之前、L1497/L1498 在两支之后）。缺的是**真实 cfgrib 解码**这一段，不是这两个符号。清单 §1 第 51/52 行命令快照这套 NetCDF 用例，本 fixture 不推翻它。
   - issue #13 的 `Suggested fixture level` 理由（「需构造可被 cfgrib 读取的合成 GRIB 样本」）由**一条** yd 自撰的 e2e 用例兑现：用锁内 `eccodes`（cfgrib 的依赖，无需新增依赖）`codes_grib_new_from_samples('regular_ll_sfc_grib2')` 造真 GRIB2，设 `shortName`/`Ni`/`Nj`/网格角点/`dataDate`/`dataTime`/`step`/values，写进 object-store 的 `raw/...grib2` 键，配一份带 `metadata.grib_short_name` 的 manifest entry，跑 `convert_manifest`，断言：**cfgrib 分支实际被走到**（fallback 未触发，可用 caplog 断言 L1470 那条 "falling back to netcdf4" 警告**未**出现）、canonical NetCDF 与 catalog 落盘、格点值与写入值对应。已实测该造法可行（215 字节 GRIB2，cfgrib 读回 `t2m` + 3×4 网格）。
   - 该用例是**真实 cfgrib 解码路径**（eccodes 解码 GRIB2 字节 → xarray Dataset → `_select_cfgrib_data_variable` 在真数据上选变量）在本仓的**唯一**覆盖；若 GRIB 造样在落码时被证明不可行（例如 eccodes 样本在 CI 的 ubuntu 上不可用），MUST 作为偏离上报并给出实测证据，MUST NOT 静默降级为再写一个 NetCDF 用例。
9. **D4 零默认在本文件的落点由 `剥离点` (a)(b)(c)(d) 四条封闭，实现者 MUST NOT 自行发明缺省**。特别是清单 §4 风险 14 已显式声明：在 `config.toml` / `local.toml` 字段落地前（归任务 1.1），7.1 **不得**自行发明缺省。本 issue **MUST NOT 修改** `producer/src/yd_producer/config.py`、`config.toml` schema、`cli.py`，也不出 `specs/cli-config/spec.md` 的 delta——converter 的三个路径字段（`workspace_root`/`object_store_root`/`object_store_prefix`）按 `剥离点`(b) 改为无默认必填 kw-only 构造入参，由调用方（组 8 与任务 14.1）装配，本 PR 内只有测试作为调用方。
10. **本 PR 不接线任何 CLI、不接线控制器**。`cli.py` 的子命令行为逐字不变；converter 只作为 `yd_producer.canonical` 包的库符号存在。NWM 侧 `workers/canonical_converter/cli.py` 不快照（清单 §1 第 35 行 `剥离点` 在处置 `from_env` 调用点时逐字记为「D5：NWM CLI 不快照」；design.md 的 D5 条目本身讲的是依赖策略，该引用在清单侧即为悬空，本 fixture 不修清单的措辞，只按其实质结论执行——NWM 的 CLI 入口不进快照集）。
11. **零跨节点、零 NWM 运行时 import**。`yd_producer.canonical` MUST NOT 在运行期 import 任何 `workers.*` / `packages.*`；对 NWM 的访问只发生在**落码期**的只读 `git show`（agent-ops §5），实现者 MUST NOT 在 NWM 工作树里执行任何写操作、MUST NOT 在 NWM checkout 内跑 `uv sync` / `uv run`（agent-ops §7.2 维护窗口硬约束）。

12. **【round-1 新增裁决 · canonical 身份归一】`source_id` MUST 在 `convert_manifest` 入口归一一次，此后全链只用归一值。** 这是 fixture 属主对 round-1 三个 reviewer 独立汇聚、verifier CONFIRMED/FIX_NOW 的 P1（`identity-drift-1`/cand-01）的裁定，实现者不得另行选路。
    - **缺陷**：`evaluate_canonical_readiness`(:346) 以 `normalize_source_id(source_id)` 过滤行，而 `_canonical_product_result_readiness_row`(:603-620) 与 `_complete_cycle_after_conversion`(:1370) 用**原始** `source_id` 打戳。pin 上 `normalize_source_id("IFS") == "IFS"`，二者一致；yd 在 issue #5 落地的 `raw/source_identity.py` 映射 `"IFS" -> "ifs"`（本 fixture 的 Must-preserve 钉死），于是 IFS 的每一行都被丢弃。实测：完整的 7 变量 x 2 lead IFS 产物集写出全部产物与 catalog，却返回 `status="canonical_incomplete"` 且七个必需变量全部报缺；GFS 不受影响（`normalize("gfs") == "gfs"`）。输入域**现在就存在**：`rawcopy.py:992` 经 `rawscan.py:48` 的 `SOURCE_DIR_NAMES` 当下就发出 `source_id="IFS"` 的 manifest。
    - **裁定的修法（唯一合法解）**：在 `CanonicalConverter.convert_manifest` 与 `IFSCanonicalConverter.convert_manifest` 两处入口的 `source_id == self.config.source_id` 相等校验**之后**，各插入一行 `source_id = normalize_source_id(source_id)`。
    - **为什么是这一处而不是别处**：只修 readiness 打戳会让落盘 catalog 行继续带 `"IFS"`，组 8 对 catalog 重跑 readiness 仍看到零行——爆炸半径不变，故**不可取**。入口归一使 object key、catalog key、catalog 行 `source_id`、`canonical_product_id`、readiness 行与过滤、缺失产物记录**同用一个小写身份**。canonical 命名空间归 yd 所有；`raw/` 侧的逐源非对称键沿用继承形态，不在本裁决范围内。
    - **【round-2 勘误 · 权威来源与枚举范围】** 本裁决原称权威是 `docs/products-contract.md` §3.2「`source` 固定为小写 `gfs` 或 `ifs`」+ 文档优先于实现——**这是越权引用，round-2 orchestrator 实测、verifier 复核确认**：§3.2 位于该文档 §3，注解的是 §2 的**发布**布局 `<YD_ROOT>/output/<cycle_id>/<source>/`（products-contract.md:14-32、:37），而 §2 收尾行逐字写着「`models`、`states`、`logs` 和 scratch 工件不属于本契约」；object-store 的 `canonical/…` 键位于 `work/<source>/<cycle>/object-store/`（compute-loop-design.md:76-90，「每轮 work 目录是一次性隔离单元。成功发布后删除」），是 scratch 工件，**不受 §3.2 约束**。修法本身不受影响：其权威是本裁决自身（「canonical 命名空间归 yd 所有」）加上那个**真实的功能缺陷**（过滤用归一值、打戳用原始值），不是契约文档。凡引用本裁决的实现注释与 PR 文本 MUST NOT 再复述 §3.2 这条论据。
    - **【round-2 勘误 · 枚举遗漏】** 上列枚举**不覆盖** `IFSCanonicalConverterConfig.grid_definition_uri`（`converter.py:206`），该字段是 pin 常量、不由 `source_id` 派生，入口归一在结构上够不着它。其后果与处置见裁决 16。
    - **登记为第五类编辑**：这是 `剥离点` 未点名的 pin 源码编辑，按裁决 2 属第五类，由本裁决**显式批准**并同批回填清单第 35 行 `剥离点`。
    - **附带的 fail-closed 收紧（须在偏离记录写明）**：归一化对未知 source 抛 `ValueError` 而非 `CanonicalConversionError`。相等校验已把取值锁在 `config.source_id` 上，故正常路径不可达；这是可接受的 fail-closed，但**必须记录**，不得静默。
13. **【round-1 新增裁决 · 快照测试的 IFS 行戳编辑予以批准，但必须去掉其「唯一 IFS oracle」地位】** `producer/tests/test_canonical_converter.py` 把两处 `canonical_rows(source_id=...)` 由 pin 的 `"IFS"` 改成 `"ifs"`，是**未申报的第五类编辑**（verifier CONFIRMED/FIX_NOW，cand-02）。
    - **不回退**：该用例直调 `evaluate_canonical_readiness` 而不经 `convert_manifest`，故裁决 12 的入口归一救不了它；在 yd 的 `{"IFS": "ifs"}` 归一化下，「逐字忠实」与「用例为绿」不可兼得。回退等于删用例，而清单第 51 行命令保留它。
    - **批准并登记**：本裁决显式批准该编辑，须同批回填清单第 51 行、写进 PR `偏离记录`、并在忠实度证明里作为具名差异段归类。
    - **但真正的病根是它曾是仓内唯一的 IFS oracle**——正因如此它把裁决 12 的 P1 盖住了。故 MUST 在 `producer/tests/test_canonical_db_free.py` 内新增一条 **IFS 端到端**用例（按 `rawcopy.py` 实际发出的 `source_id` 构造完整 IFS manifest，断言 `status == "canonical_ready"`），并附**在裁决 12 修法之前变红**的红证明。
14. **【round-1 新增裁决 · catalog schema 断言】** verifier 在 scratch 副本内把 `_write_product_catalog` 的 `source_version`/`grid_id`/`native_time_resolution`/`native_spatial_resolution` 四个键**全部改名，1337 个测试依然全绿**（控制变异正常变红），确证 catalog schema 零断言（cand-04，CONFIRMED/FIX_NOW，属本 PR 引入行为的覆盖缺口，按 carve-out 不可延期）。MUST 在 `producer/tests/test_canonical_db_free.py` 内钉住 `sorted(payload.keys())` 的 4 个键与 `sorted(row.keys())` 的 16 个键，并补齐上列四个字段的取值断言。**MUST NOT 写进快照文件**（裁决 2）。
15. **【round-2 新增裁决 · 落地的 IFS 转换数学 MUST 带值级 oracle】** round-2 verifier 在健全谐调器内（控制变异 `converter.py:719` `-273.15 → -270.00` 正常变红两例；进程内 `__file__` 落在 scratch 副本内已断言）实测**三条单行变异全部存活于 1285 个用例**：`:1045` `rate = delta / step_seconds → rate = delta`（shortwave_down 丢掉 J/m²→W/m²，100 变 1,080,000）、`:1012` `(ssr_delta + str_delta) → (ssr_delta - str_delta)`（net_radiation 合成符号翻转）、`:967`（IFS 专属点）`tuple(max(0.0, delta) * 24.0 / step_hours …) → tuple(max(0.0, delta) …)`（降水丢掉 mm/step→mm/day，主水文强迫量 8 倍误差）。三处均经探针证明**被新增 IFS e2e 实际走到**，属「走到但无断言」，不是不可达继承码。
    - **carve-out 的显式扩张（本裁决的核心裁定）**：裁决 14 已把「继承的 pin 行为首次落到 yd」认定为**本 PR 引入的行为**，其覆盖缺口按 carve-out 不可延期。IFS 转换数学是同一类继承码、同一种首次落地、同一个未接线状态，**同一条 carve-out 必须一致适用**——round 1 对 catalog schema 判 FIX_NOW 而 round 2 对转换数学判 DEFER，是不可辩护的双标。
    - **Non-goals 不构成豁免**：Non-goals 那条逐字是「数值正确性与**真实 NWM 数据**验证……归 M4」，它把**真实数据**验证推给 M4，同时把**合成 fixture 点名为 M2 的 oracle**。本裁决要求的正是合成 fixture 上的值断言，落在 M2 的 oracle 形态内。GFS e2e 早已这么做（`test_canonical_db_free.py:284-298`），IFS 侧不这么做没有理由。
    - **MUST**：在 `producer/tests/test_canonical_db_free.py` 既有 IFS e2e 内补三条值断言（f003 的 `prcp_rate_or_amount == 24.0`、`shortwave_down == 100.0`、`net_radiation == 50.0`，`pytest.approx`），数值取该文件 `:316-319` 自己已算好的那三个。**MUST NOT 写进快照文件**（裁决 2）。verifier 已实测：补上后 HEAD 绿，三条变异逐条判红（`[1080000.0]`/`[150.0]`/`[3.0]` vs 期望）。
    - **判别力口径**：`net_radiation` 不在 `IFS_REQUIRED_STANDARD_VARIABLES` 内、readiness 忽略它，故 `status` 断言对它**结构上无力**；只有值断言能钉住。
16. **【round-2 新增裁决 · `grid_definition_uri` 的大小写裂口：登记为 Known limit，不改 pin 常量】** 三个 reviewer 独立汇聚、verifier CONFIRMED 的 P2（cand-r2-04）：`IFSCanonicalConverterConfig.grid_definition_uri`(`converter.py:206`) 是 pin 逐字常量 `"canonical/IFS/grid/ifs_0p25/grid.json"`，裁决 12 的入口归一够不着它，于是修法之后 IFS 产物与 catalog 落 `canonical/ifs/<cycle>/…` 而网格定义仍落 `canonical/IFS/grid/…`——修法之前整棵 IFS 树同为大写、自洽，故这处不一致**由本 PR 引入**。
    - **裁定：不改常量。** 三条理由：(a) 写入点(`:1941`)、存在/签名守卫(`:1926`) 与每一处对外发出点(`:1429`/`:1747`/`:1866`/`:2524`/`:2577`) **共用同一个 config 字段**，故每一行 catalog 的 `grid_definition_uri` 指针都解析到真实存在的对象，**没有任何查找会断**（verifier 明确指出三份 reviewer 报告里有两份把影响说得比证据强）；(b) 该值 pin 逐字、`剥离点` 未点名，本 PR 的全部价值是忠实快照，为一处无功能后果的命名卫生再开一条第五类编辑，与裁决 1 的取向相反；(c) `canonical/` 树位于 `work/<source>/<cycle>/object-store/`，每轮用后即删，跨轮不可能积下两棵树。
    - **MUST 登记**：Known limits 补一条，配 follow-up issue，条目 MUST 携带两条下游义务：组 8/组 13 对 canonical 前缀做枚举/保留/清理时 MUST 同时覆盖两种大小写；且 `store/object_path.py` 会把该网格键解析成一个幻影 source `IFS`。
    - **MUST 钉住例外**：在 IFS e2e 的 catalog 行循环内加一条 `row["grid_definition_uri"] == "canonical/IFS/grid/ifs_0p25/grid.json"` 断言。这是字符串级断言，不依赖文件系统大小写敏感性（darwin 的 APFS 会把两棵树合并，本机**无法**用存在性判别），且当 follow-up 日后把常量改小写时它会自动变红。
    - **MUST 订正两处 yd 自撰的过度声明**（均非 pin 文本，裁决 1 不庇护，verifier 判 FIX_NOW）：(a) `converter.py:1232`/`:2064` 插入注释里「归一后对象键……同用一个小写身份」对网格对象键为假，且其 §3.2 引用按裁决 12 的 round-2 勘误已被推翻；(b) 该 IFS 端到端用例的**用例名**与 docstring「全链只用一个小写身份」宣称了它并不检查的不变量。判为缺陷而非命名裕度的依据是裁决 13 自己的诊断——本文件已经有过一次「误导性 IFS oracle 盖住 P1」的病史。
    - **【round-3 落实 · 用例名】** round-3 三个 reviewer 独立汇聚、verifier CONFIRMED/FIX_NOW：`809163f` 只订正了 docstring，用例名未改，于是用例名同时与自己的 docstring（「不宣称『全链一个小写身份』」）和自己的用例体（`:432` 蓄意断言大写网格键）相矛盾。**裁定：改名，不以修订裁决的方式把未执行的 MUST 读成已满足**——那样做正命中 Phase 8 的 oracle 完整性条款（规范修改只在追认真实契约变更时合法，绝不用来消音一次失败）。新名 `test_ifs_convert_manifest_is_ready_and_uses_lowercase_identity_except_grid_uri`，MUST 与本行引用同批落地。实现者当初拒绝单方面改名是**正确的**（tasks.md 归 orchestrator 独占，单方面改名会使 fixture 失同步）；本轮的缺陷在 orchestrator 的 round-3 简报把 MUST 写成了「optional」，不在实现者的执行。
17. **【round-2 新增裁决 · 被丢弃的 pin oracle 必须登记】** verifier 确证 `8ae9b8f2:tests/test_ifs_canonical.py` 是一份 15 用例的文件，逐条覆盖 `convert_ifs_precipitation_with_metadata` / `convert_ifs_radiation_values` / `convert_ifs_shortwave_down_values`，并带 yd 侧完全缺失的值 oracle（`approx([24.0])`、`approx([50.0])`、`approx([66.6666667])`）、负降水三分支、GRIB 量化噪声、四条 shortwave 分支与 lineage 结构。它在本仓只出现一次——清单第 35 行 `CanonicalConverterConfig(` 调用点枚举里的一句「……均不快照」，那句回答的是「哪些构造点需要 kwarg 处理」，**不是**对覆盖后果的登记；fixture 的 Known limits 净覆盖损失至今只列了裁决 6 删掉的那一条。
    - **勘误（verifier 对 reviewer 的更正，照录）**：`compute_ifs_relative_humidity_values` **不**在该 pin 文件里被直接测试，其覆盖是经产物读回 `approx([0.525])` 与 lineage `method == "magnus_formula"` 间接给的。
    - **MUST**：Known limits 补一条净覆盖损失，点名该 pin 文件与 15 个用例，按 Phase 8 routing 配 follow-up issue，并写明裁决 15 的三条值断言只结清三处单位换算，**残量**（负降水小/显著/连续三分支 `:940-965`、shortwave 量化与告警 `:1053-1075`、Magnus RH、lineage 结构）留给 follow-up。
    - **MUST NOT** 在本 PR 移植该 pin 文件——那是清单行级动作，需另立裁决，不在本轮范围。

18. **【round-3 新增裁决 · 记录准确性不再依赖散文自觉】** 本 PR 的三轮闸门里，同一条不变量失败了五次：**yd 自撰的记录文本 MUST 只陈述码与用例真正确立的东西**。实例：裁决 12 自己的 §3.2 越权引用（orchestrator 自撰）、`converter.py` 插入注释的「同用一个小写身份」、IFS 用例名、PR body 的 `冻结提交` 指向 `d1bb89c` 却在描述 `809163f` 的修复、以及 body 偏离 7 的「每处 6 行（1 行代码 + 5 行注释）」——**该行在 HEAD 上为假**，两处实为 9 行（1 行代码 + 8 行注释，`converter.py:1229-1237`/`:2064-2072`，verifier 实测）。逐条修补已连续两轮未能收住这条不变量，故本裁决把它从散文自觉改成冻结期的机械义务。
    - **MUST（冻结期机械复算，不得手抄）**：冻结 SHA 之后、发证据之前，PR body 的每一条可机械核验的断言 MUST 对着**冻结头**重新求值一遍，逐项包括：`冻结提交` 行、五条命令的验证表（**重跑，不得复制上一轮的数字**）、偏离条目里的行数/计数、以及 fixture Known limits 与 body Known limits 的逐条对应。
    - **MUST**：任何 yd 自撰的注释、用例名、docstring 与 PR 文本，其宣称范围 MUST 不超过同处断言实际覆盖的范围；宣称一条不变量却不检查它，按裁决 13 的病史判为缺陷，不算命名裕度。
    - **MUST**：把 round-3 的三条值断言红证明与本轮改名的证据落成 `implementer-evidence.md` 的 section C，使记录自立——现有红证据散在 PR body 偏离 8、`809163f` 提交信息与两份 reviewer 报告里（verifier 已订正 round-3 reviewer 的过度声明：红值**确有**两处持久化，缺的是逐条变异的原始 transcript）。
    - **【round-4 补强 · 执行钩子】** round-4 reviewer 的判词照录：「作为规范它成立，作为机制它不成立」——本裁决 MUST #1 是本 fixture 里**唯一没有文件落点**的义务，也是**唯一失败的那条**。实证：添加偏离 11 的那次 body 编辑**引用了本裁决、并把 `冻结提交` 指向上一个头列为第 4 个失败实例**，却仍旧没改那一行；规则由其作者在同一次编辑里写下、引用、违反。故补一条机械钩子，冻结期 MUST 实跑并留证：
      ```bash
      BODY_SHA=$(gh pr view <PR> --json body -q .body | /usr/bin/grep -o '冻结提交：`[0-9a-f]\{40\}`' | /usr/bin/grep -o '[0-9a-f]\{40\}')
      test "$BODY_SHA" = "$(git rev-parse HEAD)" && test "$(git rev-parse HEAD)" = "$(git rev-parse origin/<branch>)"
      ```
      同批记录一条工具面硬约束：本仓 shell 的 `grep` 是过滤 gitignore 路径的包装器，而 `.workplans/` 被 gitignore——凡声称「全仓已核」的记录扫描 MUST 用 `/usr/bin/grep`（实测：某次旧用例名扫描用包装器只得 1 处，实为 9 处）。
    - **本裁决的自指条款**：本条同样约束 fixture 自身。裁决 12 的 §3.2 越权引用就是这条不变量在 orchestrator 侧的实例，已按 round-2 勘误订正。

Change surface:
- 新增 `producer/src/yd_producer/canonical/__init__.py`（yd 自撰，空导出或最小 re-export，无溯源头）
- 新增 `producer/src/yd_producer/canonical/converter.py`（快照，清单 §1 第 35 行）
- 新增 `producer/tests/netcdf_fixture.py`（快照，清单 §1 第 52 行，强制改名）
- 新增 `producer/tests/test_canonical_converter.py`（抽取式快照，清单 §1 第 51 行：39 个保留用例（44 − 剥离点点名删的 4 − 裁决 6 判删的 1）+ 4 个 helper + 14 项模块级 import shim）
- 新增 `producer/tests/test_canonical_db_free.py`（yd 自撰，**无溯源头、不进清单路径表**：no-DB 运行期闸门用例与合成 GRIB e2e 用例）
- 修改 `producer/pyproject.toml` + `producer/uv.lock`：新增 `netCDF4`
- 修改 `producer/tests/test_snapshot_provenance.py`（若守卫需要显式登记新目录；期望是清单翻面后自动扩面，实测为准）
- 修改 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md`：第 35/51/52 行 `落地状态` 翻面；§4 风险 7 记入落码期确认结论；第 51 行 `备注` 回填裁决 6 的 4 例逐例处置与 `test_quality_flag_fail_triggers_reconversion` 的净覆盖损失，并把该 `备注` 现存的「减去上列 4 个删除用例 = 40 个」这处算术订正为 **39**（否则规范侧留一份与交付数不符的计数）
- 修改 `openspec/changes/m2-producer-core/tasks.md`：勾选 7.1
- 修改 `openspec/changes/m2-producer-core/specs/forcing-chain/spec.md`：为「DB-free canonical 转换」Requirement 增补 cfgrib 真实读路径的 Scenario

Must preserve:
- `producer` 现有 66 个源/测试文件的行为逐字不变；`uv run pytest` 既有全套通过
- `producer/tests/test_snapshot_provenance.py` 的正反两向谓词不变（本 PR 只让期望集扩面，MUST NOT 放宽谓词）
- `yd_producer.store.object_store.LocalObjectStore` / `object_path` / `safe_fs` 的公开行为不变（converter 是它们的新消费者，MUST NOT 为迁就 converter 修改它们）
- `yd_producer.raw.source_identity.normalize_source_id` 的 `{"GFS": "gfs", "IFS": "ifs"}` 字面量不变
- `cli.py` 子命令集合与 `config.py` 字段集合不变

Must add/change:
- `yd_producer.canonical.converter` 提供 DB-free 的 `CanonicalConverter` / `IFSCanonicalConverter`、`convert_manifest` / `convert_manifest_uri`、catalog 写入，全程零数据库
- canonical 产物与 catalog 落 object-store：产物键由 pin 的 `_serialize_product` 路径决定，catalog 键为 `canonical/<source_id>/<compact_cycle>/_catalog/catalog.json`、payload `schema_version = "nhms.canonical.product_catalog.v1"`（逐字承接，MUST NOT 改名或改 schema 版本串）
- `netCDF4` 进入 `pyproject.toml` 依赖与 `uv.lock`

Seams under test（上游声明，consumed not renegotiated）:
- `CanonicalConverter.convert_manifest(manifest)` —— 本任务的主 seam：合成 raw（object-store 内）+ manifest dict → `ConversionResult` + 落盘的 canonical NetCDF + catalog。change design.md「Sketch seams under test」的 forcing 主干 seam 在本任务的落点。
- `yd_producer.store.object_store.LocalObjectStore` —— 产物落盘的读回 seam（不新增 seam，复用 issue #5 已落地的）。
- **上游 seam 缺口（记录为偏离，非重新协商）**：design.md 的 seam 清单不含「快照溯源守卫」这一层，它是 issue #5 建立的仓内自有 seam（`producer/tests/test_snapshot_provenance.py`）。本 fixture 就地声明，不修改 design.md。

Selected risk packs（逐项给项目具体检查）:
- **Public API / CLI / script entry**: selected - `yd_producer.canonical` 是新公开包；`__init__.py` 的导出面、构造签名（config 必填 kw-only、无 `repository`）是组 8 的调用契约
- **File IO / path safety / overwrite**: selected - 产物与 catalog 经 `LocalObjectStore.write_bytes_atomic` 落盘；键由 `source_id`/`cycle`/`variable`/`forecast_hour` 拼接，须经 `object_path.validate_object_path` 既有约束；重复转换的覆写语义按裁决 6 的**重写幂等**读法取证（`_existing_product_is_current` 在 DB-free 下不可达，MUST NOT 拿它当断言目标）
- **Schema / columns / units / field names**: selected - catalog 的 16 个行字段与 4 个 payload 字段、`schema_version` 串、`VARIABLE_MAPPING`/`STANDARD_UNITS`/`CONVERSION_PARAMS` 的单位契约，是组 8 的下游 schema 真相
- **Resource limits / large input / discovery**: selected - `_read_records` 逐 entry 打开数据集；`_configured_forecast_hours` 的 lead 全集；合成 fixture 规模须小到 CI 可跑
- **Legacy compatibility / examples**: selected - 快照忠实度本身：`剥离点` 之外零改写，diff-vs-pin 必须只含四类允许编辑
- **Error handling / rollback / partial outputs**: selected - `CanonicalConversionError` 是唯一的公开失败类型；缺变量/缺 lead/不可解析 raw/序列化失败四条路径各须有用例；catalog **自身**原子写（`write_bytes_atomic`），且转换失败时**不写** catalog。**MUST NOT 断言产物级回滚**：pin 逐份写产物、写完才写 catalog，失败时已写的产物对象留在 object-store，pin 无回滚——按裁决 1 这是继承行为，登记为已知非目标，不是本 issue 要补的缺口
- **Release / packaging / dependency compatibility**: **selected** - 本 PR 引入 `netCDF4`，`uv sync --frozen` 与 CI producer job 是硬证据
- **Documentation / migration notes**: selected - 清单 `落地状态` 翻面、§4 风险 7 结论回填、spec delta
- Config / project setup: not selected - 裁决 8 明令不碰 `config.py` / `config.toml` schema
- Auth / permissions / secrets: not selected - 无凭据面；DB 面已整体剥离
- Concurrency / shared state / ordering: not selected - converter 是单进程纯转换，无锁、无共享可变状态、无重入路径（并发面归控制器组 12）

Domain packs（active profile）:
- **Time series / forcing / temporal boundaries**: selected - `compute_time_axis`、`parse_cycle_time`、`_step_hours` / `_step_hours_from_step_range` 的 APCP 累积语义、`lead_time_hours` 与 `valid_time` 的对应
- **NWM 快照溯源 / DB-free 隔离**: selected - 本 issue 是清单第 35/51/52 行的落地点，正反两向守卫与运行期无连接闸门都在此定型
- Geospatial / CRS / shapefile sidecars: not selected - converter 不做重投影，只读 GRIB/NetCDF 自带经纬网格（`_grid_definition_signature` 是签名不是投影）
- 状态链 / warm-start 定戳一致性: not selected - 本 issue 不触碰 `cfg.ic` 与 `states/`

Required evidence（输入 → 期望输出）:
- `uv run pytest producer/tests/test_canonical_converter.py` -> 39 个快照用例全绿；`uv run pytest producer/tests/test_canonical_db_free.py` -> yd 自撰用例全绿
- 合成 raw（NetCDF fixture，7 变量 × 2 lead）+ manifest -> `convert_manifest` 产出 14 份 canonical NetCDF + 1 份 catalog；catalog `products` 长度 14、`schema_version == "nhms.canonical.product_catalog.v1"`
- 合成**真 GRIB2**（eccodes 造，`shortName` 与 manifest `metadata.grib_short_name` 一致）-> cfgrib 分支被走到（无 "falling back to netcdf4" 警告）、canonical 产物与 catalog 落盘、值对应
- 转换全程 `socket.socket.connect` 被闸门拦截 -> 零调用（闸门被触发即用例红）
- `hasattr(converter_module, "CanonicalRepository")` -> `False`；`CanonicalConverter.__init__` 签名无 `repository`
- 缺必需变量的 manifest（`omitted_variables={"dswrf"}`）-> `CanonicalConversionError`，消息匹配 `Missing required canonical variables`；该 cycle 的 catalog **不存在**；零 canonical 产物对象
- 单个 lead 缺变量的 manifest（`omitted_pairs={("dswrf", 3)}`）-> `CanonicalConversionError`，消息匹配 `dswrf->shortwave_down f003`；catalog 不存在；`shortwave_down` 的 f003 与 f000 产物对象均不存在（pin 侧靠 repository 区分二者，DB-free 下两者都不落盘，取反方向断言）
- 同一 manifest 连跑两次 -> 两次各 14 份产物、状态集恒 `{"created"}`，第二次的每份产物 checksum 与 catalog JSON 字节与第一次逐字节相同（重写幂等，裁决 6）
- 不可解析的 raw 字节 -> `CanonicalConversionError`，消息含 `local_key`；**不产出半份 catalog**
- `CanonicalConverterConfig(workspace_root=..., )` 缺 `object_store_root` / `object_store_prefix` -> `TypeError`（必填 kw-only 生效，空串回退已删）
- `producer/tests/test_snapshot_provenance.py` -> 正向覆盖新三个文件（删任一溯源头即红）；反向守卫不误判 `canonical/__init__.py`
- `cd producer && uv sync --frozen` -> 无 lock drift
- `cd producer && uv run ruff check . && uv run ruff format --check .` -> 退出码 0
- `openspec validate m2-producer-core --strict --no-interactive` -> 退出码 0

**红证明（red-proof）义务**：yd 自撰或改写的新断言（no-DB 运行期闸门、合成 GRIB e2e、溯源守卫扩面、裁决 6 改写出的三条取反/幂等断言）MUST 各给一条实跑过的红证据——把闸门/断言反过来、删掉溯源头、或让失败路径提前写一份 catalog，粘贴红输出。逐字移植的快照用例不承担红证明（它们在 pin 上已有历史），但 MUST 报告移植后首次运行的完整结果。

**忠实度证明（本任务的判别力承重条）**：MUST 提交三份 `diff` 的机械收敛证据，即对每个快照文件跑 `diff <(git -C <NWM> show 8ae9b8f2:<pin路径>) <目标文件>`，并把每一段差异归入裁决 2 的四类允许编辑之一；无法归类的差异即偏离。另 MUST 重跑清单第 35 行自带的再生命令并报告命中数：
- `grep -c 'repository' producer/src/yd_producer/canonical/converter.py` -> 0
- `grep -nE 'os\.getenv|_float_env\(|_env_flag\(' producer/src/yd_producer/canonical/converter.py` -> 0 行
- `grep -n 'from_env' producer/src/yd_producer/canonical/converter.py` -> 0 行
- `grep -nE 'ERA5CanonicalConverter|ERA5CanonicalConverterConfig|convert_era5_precipitation_with_metadata|expected_converter_version' producer/src/yd_producer/canonical/converter.py` -> 0 行
- `grep -c '^def test_' producer/tests/test_canonical_converter.py` -> **39**（44 个 pin 用例 − `剥离点` 点名删除的 4 个 − 裁决 6 判删的 `test_quality_flag_fail_triggers_reconversion` 1 个）
- `grep -c '^def test_' producer/tests/test_canonical_db_free.py` -> yd 自撰用例数（no-DB 闸门 + 合成 GRIB e2e，单独报数）

Invariant Matrix:
- **Governing invariant**: 落进 `yd_producer/canonical/` 的每一个字节，要么与 pin `8ae9b8f2` 逐字对应，要么落在裁决 2 的四类允许编辑内；且该模块在任何执行路径上都不建立出站连接、不读环境变量、不 import NWM。
- **Source-of-truth identity/contract**: pin commit `8ae9b8f2` + 清单 §1 第 35/51/52 行的 `剥离点`/`抽取`/`落地状态` 三列
- **Producers**: `CanonicalConverter.convert_manifest` / `convert_manifest_uri`、`_serialize_product`、`_write_product_catalog`
- **Validators/preflight**: `_missing_required_pairs`、`_ensure_grid_definition`、`_select_cfgrib_data_variable`、`required_standard_variables_for_source`
- **Storage/cache/query**: `yd_producer.store.object_store.LocalObjectStore`（未改动的既有消费面）、`object_path.validate_object_path`
- **Public routes/entrypoints**: `yd_producer.canonical.__init__` 的导出面；`cli.py` —— **none - 本 PR 不接线，裁决 9**
- **Frontend/downstream consumers**: 组 8 的 direct-grid forcing（尚未落地，经 catalog 与 canonical 产物键消费）；本 PR 的义务是把 catalog schema 逐字承接，不改名
- **Failure paths/rollback/stale state**: `CanonicalConversionError` 全集；失败路径的「零产物零 catalog」（missing-pairs 分支，pin L1224-1228 在任何写入之前）；catalog 写失败路径。`_existing_product_is_current` / `already_done` / `_upsert_product` / `_update_cycle_status` 四处在 DB-free 下不可达或 no-op，保留但**不作为断言目标**（裁决 5）
- **Evidence/audit/readiness**: `producer/tests/test_snapshot_provenance.py`（正向溯源 + 反向守卫 + DB-free 禁区）、清单 `落地状态` 列、catalog 本身
- **Regression rows**:
  - `convert_manifest` + 完整合成 raw -> 14 份 canonical NetCDF + 1 份 catalog，catalog 行字段齐全，零出站连接
  - `convert_manifest` + 真 GRIB2 raw -> 走 cfgrib 分支（无 fallback 警告），产物与 catalog 同上
  - `convert_manifest` + 缺变量/不可解析 raw -> 稳定的 `CanonicalConversionError` 或 `MissingForecastVariable`，无半份 catalog
  - `CanonicalConverterConfig` 缺必填 kw-only 字段 -> 构造期 `TypeError`，不静默回退到 `workspace_root`
  - 删除任一快照文件的溯源头 -> `test_snapshot_provenance.py` 变红并指名该文件（守卫扩面的判别器）
  - `canonical/__init__.py`（yd 自撰、无溯源头）-> 反向守卫不误判（未登记文件的正确放行）
  - 未改动的兄弟消费者 `yd_producer.store.*` / `yd_producer.raw.*` 的既有用例 -> 逐条仍绿

Boundary-surface checklist:
- 共享 helper 根：`yd_producer/store/{object_store,object_path,safe_fs}.py` —— 只读消费，MUST NOT 修改
- 公开入口：`yd_producer.canonical` 新包；`cli.py` 不动
- 读面：object-store 内的 raw 字节（cfgrib 优先、netcdf4 fallback）
- 写/覆写面：canonical NetCDF 产物键、catalog 键（原子写）
- staging/publish/rollback 面：none - 本模块只写 work 内 object-store，NFS 发布归组 13
- producer/consumer evidence 边界：catalog ↔ 组 8 forcing；清单 `落地状态` ↔ 溯源守卫
- 陈旧态/幂等边界：同一 manifest 重复转换的**重写幂等**（裁决 6 第一条）；`_existing_product_is_current` 不可达，不在边界面内
- 未改动的下游消费者：`yd_producer.raw.manifest` 的 `DownloadManifest` 信封（converter 是它的第一个消费者，MUST 按既有字段读，MUST NOT 改信封）

Known limits（须走 Phase 8 的 deferral routing：每条配 follow-up issue 或一行不落 issue 的理由）:
- **净覆盖损失**：`test_quality_flag_fail_triggers_reconversion`(tcc L839) 按裁决 6 删除，`_existing_product_is_current` 的 fail-flag 重转分支在 yd 侧零覆盖（该分支在 DB-free 下不可达，故覆盖损失与行为损失不等价）
- **继承的死代码**：裁决 5 登记的 3 处不可达分支 + 2 处 no-op（`_get_existing_product` 后半、`_existing_product_is_current`、`already_done` 状态支、`_upsert_product`、`_update_cycle_status`）与 6 处 ERA5 残面，按裁决 1 保留
- **继承的矛盾**：`REQUIRED_STANDARD_VARIABLES_BY_SOURCE` 的 `"ERA5"` 键不可达（`normalize_source_id("ERA5")` 抛 `ValueError` 而非 `CanonicalConversionError`）
- **无产物级回滚**：转换中途失败时已写的 canonical 产物对象留在 object-store（pin 行为，无回滚）
- **【round-2 补登记】`canonical/` 网格键的大小写裂口**：裁决 12 的入口归一之后，IFS 产物与 catalog 落 `canonical/ifs/<cycle>/…`，而 `IFSCanonicalConverterConfig.grid_definition_uri`(`converter.py:206`) 是 pin 常量，网格定义仍落 `canonical/IFS/grid/ifs_0p25/grid.json`。按裁决 16 不改常量：写入点与全部读出点共用同一 config 字段，无查找会断；`canonical/` 树是每轮删的 work 内工件。**下游义务**：组 8/组 13 对 canonical 前缀做枚举、保留或清理时 MUST 同时覆盖两种大小写，且 `store/object_path.py` 会把该网格键解析成幻影 source `IFS`；darwin 的 APFS 大小写不敏感会把两棵树合并，本机测试**无法**用存在性判别这道裂口（IFS e2e 只做字符串级断言）。配 follow-up issue。
- **【round-2 补登记】被丢弃的 pin oracle**：`8ae9b8f2:tests/test_ifs_canonical.py`（15 用例）覆盖 `convert_ifs_precipitation_with_metadata` / `convert_ifs_radiation_values` / `convert_ifs_shortwave_down_values` 并带值 oracle，本 PR 未快照它（清单第 35 行那句「均不快照」是构造点枚举的附带说明，不构成覆盖后果登记）。裁决 15 的三条值断言只结清三处单位换算；**残量**——负降水小/显著/连续三分支(`:940-965`)、shortwave 量化与告警(`:1053-1075`)、Magnus RH、lineage 结构——在 yd 侧零覆盖。配 follow-up issue。
- **【round-1 补登记】读侧 symlink 不走 no-follow**：`converter.py` 由 `object_store.resolve_path()` 取裸 `Path` 交给 `xr.open_dataset`，而 `resolve_path`(object_store.py:314-326) 只做键归一 + `validate_object_path` + 字符串级容纳，**无 `O_NOFOLLOW`**；`LocalObjectStore` 的其它每一个消费者都走 `*_no_follow(..., containment_root=self.root)`。store 根内 `raw/<source>/<cycle>/<file>` 任一段的 symlink 会被 eccodes/netCDF4 跟随，读到容纳根之外的字节并据以产出 canonical 产物与 catalog。pin 逐字继承（pin `object_store.py:273-285` 的 `resolve_path` 与 yd 逐字符相同），故裁决 1 禁止在本 PR 修。
  **两点必须写进 follow-up，否则会传播一条陈旧论据**：(a) 「object-store 树只由 `write_bytes_atomic`（no-follow）写入、symlink 须带外植入」对 `raw/` 子树**不成立**——`rawcopy.py:736-737` 把 object-store 根取作 `work_dir`，而 `raw/` 由 `rawcopy.py:893` 自己的 `mkdir` 建立，不经 store；(b) **issue #71 把自身严重性上限建立在「最终消费者经 `object_store.py:190,206,263` 的 `*_no_follow` 读取、故转换器 fail-closed」这一前提上，而本 PR 落地的转换器正是那个消费者且不走那三行**——#71 的 fail-closed 上限自本 PR 起不再成立。
- **【round-1 补登记】对半可信输入无规模上界**：`load_manifest`(:1213-1216) 与 `grid_definition_uri` 读(:1922) 用无上限的 `read_bytes`，而 store 自带 `MAX_OBJECT_MANIFEST_BYTES = 16MiB`(object_store.py:24) 与 `read_bytes_limited`(:212)，本模块**从不使用**；raw 文件交给 cfgrib/netCDF4 前无 size/stat 检查（模块内 `grep MAX_` 零命中）；`:1502` 把整张格点物化成 Python float 元组，IFS 路径(:2082-2094) 一次持有一小时的全部八个原生变量。
  **量级按实测写，不用全球网格的数字**：真实 raw 由 NWM 下载器按 `download_bbox = {east:145, north:64, south:8, west:63}` 裁剪，约 329x225 ≈ 74k 点 ≈ **2.4MB/变量、8 变量的 IFS 小时约 19MB**（不是全球 0.25° 的约 265MB）。输入域为**半可信**：自家 NWM 下载器写在共享 NFS 上，非对抗，但跨节点、在 yd 写控制之外、且从不做尺寸校验。三个面在 pin 上皆逐字，裁决 1 禁止在本 PR 修。

Non-goals:
- direct-grid forcing 生产、work 内临时 registry、SHUD 输入组装（组 8）
- 数值正确性与真实 NWM 数据验证（profile：合成 fixture 是 M2 唯一 oracle，数值正确性显式归 M4）
- `config.toml` / `local.toml` 新字段（清单 §4 风险 14，归任务 1.1）
- CLI 接线与控制器接线（任务 14.1）
- 修复清单继承下来的矛盾（`"ERA5"` 键不可达、`:53` 记录的六键承接矛盾 #99）——报告，不修

Review focus:
- 忠实度：三份 diff-vs-pin 的每一段差异是否都能归入四类允许编辑；`剥离点` 的每一条是否都执行、且**只**执行了它点名的；yd 自撰用例是否真的全部落在 `test_canonical_db_free.py`（写进快照文件即废掉收敛证据）
- 裁决 6 的 4 例改写是否照做：改写后的断言是否真有判别力（尤其失败路径的「零产物零 catalog」与重写幂等的逐字节比对），有没有被降级成永真式
- 守卫扩面是否真有判别力：清单 `落地状态` 翻面后，正向检查是否真的跑到了新三个文件（删溯源头须变红），反向守卫是否误判 `canonical/__init__.py`
- 「无数据库连接」是否只有静态代理量，运行期闸门是否覆盖完整 `convert_manifest` 路径
- 合成 GRIB e2e 是否真的走了 cfgrib 分支（fallback 静默生效是本用例最可能的假绿形态）
- `netCDF4` 引入是否伴随 `uv lock` 并通过 `uv sync --frozen`；是否误改了引擎
- catalog 的 schema 串与字段名是否逐字承接（组 8 的下游契约）

## 8. forcing-chain（四）：direct-grid forcing 与 SHUD 输入组装

- [x] 8.1 快照 file-backend direct-grid forcing 生产（格点即站点、binding 权重 1、`Time_Day=0` 锚 cycle）
- [ ] 8.2 实现 work 内临时 registry/model manifest 生成与随 work 清理
- [ ] 8.3 实现 SHUD 输入组装：变体 + forcing + `states/<source>/<T>.cfg.ic` → 运行目录；warm-start 状态 MUST 覆盖变体自带率定末态（可区分 IC fixture 断言）；固定覆盖六项参数（START=0/END=7/DT_QR_DOWN=60/Update_IC_STEP=720/BINARY_OUTPUT=1/ASCII_OUTPUT=0），00Z/12Z 同参数测试

依赖：组 7（canonical 结构）、组 4（状态文件）
§13.1 归属：DB-free 链
Suggested fixture level: compact - 合成 canonical NetCDF、合成变体目录与可区分状态文件即可
Minimal mergeable slice: direct-grid forcing 生产（8.1）——对合成 canonical 独立可验证；registry 与组装为后继

### Issue #14 fixture（任务 8.1）

**风险分级**：fixture level = **expanded**；repair intensity = **high**；project profile = `yd-viewer`。上游建议 `compact`，本 issue 上调：profile 的 mandatory expanded triggers 明列 `forcing`、`canonical`、NetCDF 与 `cycle`，且本 PR 新增 file/JSON/NetCDF 读面、SHUD forcing 格式和跨 source 时间/网格身份。Blast radius = high（错误 forcing 会让整轮 SHUD 数值链失真；M2 只以合成 fixture 证明结构/管线，不声明真实数值正确）。

**PR 边界与落点裁决**：
- 本 issue **只做 8.1**。公开验收 seam 是 `yd_producer.forcing.ForcingProducer.produce(...) -> ForcingProductionResult`；design.md D10 覆盖旧草图 `forcing.build(...)`。不新增 build facade，不生成临时 registry，不组装 SHUD 运行目录。
- 快照目标按 `nwm-snapshot-inventory.md` §1 第 36/37/38/42/43/53 行落地：`forcing/{producer,file_store,canonical_json,direct_grid_contract,shud_forcing_contract}.py` 与抽取式 `tests/test_forcing_producer.py`；`forcing/__init__.py`、grid-identity/no-follow 适配 helper 与 yd 验收测试为本仓自撰，不带 NWM 溯源头。
- 上述六条清单行的 `落地状态` 必须与对应文件在**同一实现提交**翻为 `本 issue 落地`；fixture-first 文档提交仍保持 `待落地`。
- `producer.py`、`file_store.py`、抽取式 `test_forcing_producer.py` 保持 pin 文件边界。实际超过 1000 行时只允许把这三份逐文件加入 `.large-file-guard.json`；yd 自撰测试/helper 必须拆分在 1000 行内。若发生豁免，Phase 8 路由一条规模 follow-up；不扩大既有 glob/目录豁免。
- 不改 `config.py`/`cli.py`/`controller.py`/`assemble`、viewer、依赖版本或 lockfile。构造参数由测试/后继编排显式提供；config 接线归 #15/#26。

**Must preserve**：
- `producer/tests/test_canonical_db_free.py::test_product_catalog_pins_the_inherited_payload_and_row_schema` 钉住的 catalog payload 4 键、product row 16 键与 GFS/IFS 小写 `source_id`；forcing 只消费，不改 catalog schema。
- 现有 `LocalObjectStore` key/atomic-write/checksum 合同与 `safe_fs` no-follow 原语；不在 forcing 内手搓第二套路径 containment。
- pin 的 canonical→SHUD 单位/派生语义：PRCP `mm/day`、TEMP `degC`、RH `0-1`、wind `sqrt(u²+v²)`、RN `W/m²`；Press 可保留 timeseries，但 SHUD station CSV 仍只有 `Time_Day/Precip/Temp/RH/Wind/RN`。
- NWM pin 的 package manifest canonical JSON 字节与 checksum。`canonical_json.py` 的 `_json_bytes` 保持 `json.dumps(..., sort_keys=True, separators=(",", ":"), default=_json_default)` 的默认 `ensure_ascii=True`；`file_store.py` 自带的本地 `_json_bytes` 保持 `ensure_ascii=False`。两者对含 Unicode 的 payload 字节不等价，MUST NOT 合并；合并会改变 `forcing_domain_package.json` / handoff payload checksum。
- direct-grid lineage 只保留 `payload["contract_grid_signature"] == contract.grid_signature`；随重算传参链删除的 `payload["grid_signature"]` 与 `payload["validated_grid_signature"]` 不得继续生成或断言。生产路径对 contract signature 的真实性校验由 yd 纯 grid-identity helper 承担，lineage 键只记录合同值、不冒充重算证据。
- IFS grid-definition URI 的已知大小写裂口按 #104 现状消费；本 PR 不改 canonical producer，也不得仅按小写前缀枚举而漏掉 catalog 给出的精确 URI。
- 所有现有 producer 测试、ruff、OpenSpec 与 stage-pipeline 锚保持绿色；不删/弱化任何既有 oracle。

**Must add/change**：
- Direct-grid station set = binding 声明的 grid-cell set：每个 binding station/每个 forcing variable 恰一条 `method="direct_grid"`、`weight=1.0` mapping；station id、index、filename 与 `grid_cell_id` 一一对应。canonical 中未绑定的额外 cell 不读、不输出；模块内不存在可调用的 `compute_idw_weights`/105 站 fallback。
- GFS 与 IFS 各用自己的 `applicable_source_ids`、`grid_id`、ordered grid cells 与 binding；任一身份跨 source/跨 grid 复用都在 ready 写入前失败。
- grid identity 保护不得因剥离 NWM grid-registry 面而净损失：yd 自撰纯 helper 对 ordered `(grid_cell_id, round(lon, 12), round(lat, 12))` canonical JSON 求 SHA-256，与 contract `grid_signature` 比较；不 import NWM/grid-registry。此裁决**覆盖**清单第 42 行旧备注的「惰性字段 / 不再参与生产校验」：字段解析/记录仍逐字保留，且生产路径 MUST 重新验证其值，只是不恢复被剥离的 NWM helper/import/bbox 链。
- `ForcingProducerConfig` 改为 frozen + kw-only；`workspace_root`、`object_store_root`、`object_store_prefix` 无默认必填，`ForcingProducer.__init__` 的 config 也无默认必填。删除 `ForcingProducerConfig.__post_init__` 的空 `object_store_root -> workspace_root` 回退。五个版本化保护默认逐字保留 pin 数值：`max_station_count=10000`、`max_timestep_count=10000`、`max_grid_cell_count=5000000`、`max_timeseries_row_count=10000000`、`max_manifest_bytes=33554432`；`min_lead_hours: int | None = None`。pin 保留构造点只补缺少的 `object_store_root`/`object_store_prefix`，其中 L4229 原有 `max_manifest_bytes=16` 继续保留。
- `Time_Day=0` 绑定显式 `cycle_time`：00Z/12Z 的第一行都恰为 cycle。最早可产出 valid time 晚于 cycle 时 fail closed，不得把它重新标零；无 ready package/version/cycle 状态。
- `FileForcingRepository` 只接受显式、无默认、object-store 相对 key 的 work-local manifest，例如 `models/<model_id>/registry.json`；拒绝绝对路径、S3 manifest URI、`..` 与未知 prefix。删除 `from_env`、宿主 `Path.read_*` 和 object-store 失败后的裸路径 fallback。
- registry/model manifest 与 canonical catalog 经一个 bounded/no-follow JSON helper 读取：最多 16 MiB、最大深度 64、最多 250000 个 JSON container/scalar node；invalid UTF-8、malformed JSON、`RecursionError`、超深/超宽/超限均映射成稳定 `ForcingStoreError`，不得产生 ready 输出。
- binding 与 `.sp.att` 读取/校验和受 `ForcingProducerConfig.max_manifest_bytes` 限制；非 UTF-8 `.sp.att`、checksum mismatch、unsafe member、missing FORC index 均在 ready 输出前失败。
- canonical NetCDF 读必须 descriptor-bound：先经 `safe_fs.open_file_no_follow(..., containment_root=object_store.root)` 打开普通文件，再在 fd 生命周期内用 Linux `/proc/self/fd/<fd>` 或 Darwin `/dev/fd/<fd>` 交给 xarray；symlink leaf/ancestor、FIFO/目录/设备与别名不可用都稳定失败，绝不回退裸 Path。单对象版本化上限固定为 `MAX_CANONICAL_NETCDF_BYTES = 536870912`（512 MiB）：同一 fd 先以 `fstat` 拒绝已知超限文件，checksum 流式读取再按累计字节作第二道 fail-closed guard；不新增环境变量或第二份配置来源。
- catalog row、精确 canonical object key 与 NetCDF identity 必须联合一致：key 由 normalized source、compact cycle、variable、`canonical_product_id` 唯一导出；dataset 只接受逐字匹配的 data variable，不保留 singleton fallback，并逐项核对 `cycle_time`、`valid_time`、`lead_time_hours`、`unit`、`grid_id`。任何 foreign source/cycle/variable/object 或 attrs mismatch 在 value extraction/ready 前失败。
- public `produce` 在任何 repository lookup/write/cleanup 前验证 cycle 为 UTC 整点且 hour 恰为 00/12；06Z、非零 minute/second/microsecond 稳定映射为 `ForcingProductionError`，不得碰撞或清理同小时的合法 ready evidence。
- forcing lineage/package 记录 stable output-config identity（canonical JSON + SHA-256），覆盖所有影响 package bytes/shape/path/选择策略的 config：`rn_shortwave_factor`、`forcing_filename`、`csv_filename`、`package_manifest_filename`、`output_variables`、`required_canonical_variables`、`era5_latency_fallback_hours`、`min_lead_hours`。existing-ready 同时比较 record 与 manifest identity；任一漂移必须重算/拒绝，不返回旧 `already_done`。
- DB-free 守卫改为 AST/import/call 语义，但**不另立声明集**：任务 2.3 第 443 行现有且唯一的禁区声明锚、8 项词表和 `_declared_forbidden_surfaces()` 解析合同原样保留；#14 的 evidence 不复述该锚前缀。`_forbidden_hits` 对这 8 个 token 的执行语义写死：`psycopg` 与 `scheduler/registry/journal/reservation` 仅在 dotted import module path 中按成分命中（因此 `grid_registry_bbox_guard` 仍命中）；`DATABASE_URL` 只在精确 Name 或环境访问 key 中命中；`os.getenv` 只在对应 Call path 命中；`os.environ` 只在对应 Attribute/Subscript path 命中。普通 `registry_manifest` Name、错误消息、路径字符串与显式 work-local file adapter 不命中。同步迁移**全部现有判别器**，不只参数化用例：`test_each_forbidden_surface_is_individually_enforced` 的每项注入改成真实 import/name/call；`test_db_free_scan_catches_unregistered_files_inside_a_snapshot_package` 的 `from app import scheduler` 改成 import module path 自身含 `scheduler`（如 `from app.scheduler import run`）；散文/真实代码、行尾注释、tokenize 行号与不可解析源码的 sibling 用例按新 AST 语义重写期望。AST parse 失败时 MUST fail closed 到旧的 `_code_lines`/8-token raw 扫描，故不可解析真代码仍命中。另加正对照证明 `registry_manifest` 与 `models/demo/registry.json` 不误报。不得通过删 token、删用例或改弱唯一锚点让现有用例转绿。

**Seams under test（由高到低，implementer 消费，不重议）**：
1. `ForcingProducer.produce`（合成 object-store + 显式 file repository）——验收站点/格点值、权重、source isolation、00Z/12Z、失败不 ready。
2. `FileForcingRepository(object_store=..., registry_manifest=...)`——验收 catalog/manifest/binding/.sp.att 的 bounded/no-follow 与无宿主路径 fallback。
3. `parse_direct_grid_forcing_contract`——保留 pin 的字段、source、index、filename、资源上限拒绝矩阵；不另写第二个 parser。
4. `test_snapshot_provenance.py` 的 AST 级 DB-free 与清单正反向守卫——验收溯源/禁 import，不把词面当行为。
5. 自撰纯 helper 可单测 grid signature canonical bytes 与 descriptor alias 选择；这些单测是公共 seam 1/2 的错误路径支撑，不能取代 produce 端到端。

**Risk packs considered（core）**：
- Public API / CLI / script entry: selected - 新增 `yd_producer.forcing` 公开包与 `ForcingProducer.produce`；不接 CLI。
- Config / project setup: selected - 路径字段显式 kw-only，资源上限是版本化默认；无环境 fallback。
- File IO / path safety / overwrite: selected - manifest/catalog/binding/.sp.att/NetCDF 读与 forcing package 原子写；no-follow、bounded、失败无 ready。
- Schema / columns / units / field names: selected - canonical catalog、direct-grid contract、`.tsd.forc` 与 station CSV 列/单位。
- Auth / permissions / secrets: not selected - 无凭据/租户/权限模型；文件 mode 与 no-follow 归 File IO。
- Concurrency / shared state / ordering: selected - file repository 进程内 ready 状态与输出顺序；单线程、无跨进程共享，失败必须先于 ready/finalize。
- Resource limits / large input / discovery: selected - station/timestep/row/JSON/asset byte limits，canonical 只保留 bound cells；目录 fallback discovery 删除。
- Legacy compatibility / examples: selected - NWM pin 抽取闭包与 package shape；IDW 作为 yd 非目标被结构性剥离。
- Error handling / rollback / partial outputs: selected - 所有合同/IO/identity/time 错误稳定收敛且无 ready；work 内失败中间物由 #15 生命周期清理，本 PR 不跨边界删目录。
- Release / packaging / dependency compatibility: selected - 新包导出与现有 numpy/xarray/netCDF4 依赖；无新增依赖、`uv sync --frozen` 无 drift。
- Documentation / migration notes: selected - snapshot inventory 状态/剥离点、D10、spec 场景与大文件例外记录。

**Domain packs（project profile）**：
- Geospatial / CRS / shapefile sidecars: selected - ordered grid cell id/lon/lat 与 source grid identity；无 CRS 转换/shapefile。
- Time series / forcing / temporal boundaries: selected - cycle 00/12、GFS interval row、IFS valid time、`Time_Day=0`。
- 状态链 / warm-start 定戳一致性: not selected - 本 PR 不读写 `cfg.ic`；组装归 #15。
- NWM 快照溯源与 DB-free 隔离: selected - 六个快照目标、抽取闭包、禁外部运行面。

**Invariant Matrix**：
- Governing invariant: 对 `(source, cycle, model)` 的每个输出站点，身份和值只能来自同 source 的 binding 所指 canonical cell；时间零点只能是显式 cycle；任何不匹配/不安全读都不得产生 ready 输出。
- Source-of-truth identity/contract: `DirectGridForcingContract` 的 `applicable_source_ids/grid_id/grid_signature/stations[*].grid_cell_id` + canonical catalog row 的 `source_id/cycle_time/grid_id/grid_definition_uri/object_uri/checksum` + `cycle_time` 调用参数。
- Producers: `ForcingProducer.produce`、direct-grid weight materialization、SHUD package formatter。
- Validators/preflight: direct-grid parser、binding/.sp.att checksum/index validation、yd grid-signature helper、unit/time/limit guards、bounded JSON、descriptor-bound NetCDF。
- Storage/cache/query: `FileForcingRepository` work-local object-store；无 DB、无环境发现、无跨轮动态 registry。
- Public routes/entrypoints: `yd_producer.forcing` exports；CLI/controller = none（#15/#26）。
- Frontend/downstream consumers: #15 SHUD input assembler 消费 `ForcingProductionResult.forcing_package_uri` 与 package files；现阶段以 package shape 测试替代接线。
- Failure paths/rollback/stale state: invalid contract/source/grid/cycle/cell/unit/path/JSON/asset -> `ForcingProductionError`/`ForcingStoreError`，cycle failed 可记录但 ready/finalize 不发生；重跑同输入保持 pin idempotency。
- Evidence/audit/readiness: snapshot provenance inventory + 13 个 pin 保留种子 + yd 验收/安全测试 + red proof/mutation matrix + full producer suite。
- Regression rows:
  - GFS 00Z + 两个 bound cells/一个 unbound cell -> 两站、逐值等于 bound cells、每 mapping 权重 1；每份 station CSV 第 1 行为 `<row-count>\t6\t<start-date>\t<end-date>`，第 2 行逐字为 `Time_Day\tPrecip\tTemp\tRH\tWind\tRN`，第 3 行是首个数据行且 `Time_Day=0`，合成 `u=3,v=4` 的 Wind 字段为手算字面值 `5`（另一站 `u=6,v=8` -> `10`），不用 production helper 重算期望。
  - IFS 12Z + 可区分 grid/cells -> 只消费 IFS binding，首行 Time_Day 0，无 0.5 日偏移。
  - 最早 valid time > cycle -> 稳定失败，无 ready package/version。
  - binding missing cell / source-grid-signature mismatch / `.sp.att` missing index 或 non-UTF-8 -> 稳定失败，无 IDW fallback、无 ready；`applicable_source_ids` 在生产解析时必须是只含当前 normalized source 的单例，`[IFS]`→GFS 与 `[GFS,IFS]` 共享 binding 均拒绝；有效包 lineage 恰含 `contract_grid_signature == contract.grid_signature`，不含 `grid_signature`/`validated_grid_signature` 两键。
  - symlink leaf/ancestor、FIFO、oversize/deep/wide/malformed JSON、oversize binding、超过 536870912 bytes 的 sparse canonical NetCDF -> 稳定失败，无根外读取、无 ready。
  - catalog row 指向 foreign source/cycle/variable/object、singleton wrong data variable 或 NetCDF `cycle_time/valid_time/lead_time_hours/unit/grid_id` mismatch -> value extraction 前稳定失败；正常 GFS/IFS canonical writer 形状继续通过。
  - 合法 12Z ready 后调用 06Z、12:30、非零秒/微秒 -> public request validation 失败，原 12Z record checksum 与 sidecar/domain/handoff/cycle-ready bytes 保持逐字不变。
  - IFS factor 1.0 ready 后仅改 `rn_shortwave_factor=0.5`（`producer_version` 不变）-> output-config identity 改变并重算，RN bytes 相应改变；另以输出 filename/variable policy sibling 证明 fingerprint 不是 factor 特判。
  - `ForcingProducerConfig` 缺 `object_store_root` 或 `object_store_prefix` -> 构造期 `TypeError`；显式三路径构造 -> 五个上限为 `10000/10000/5000000/10000000/33554432` 且 `min_lead_hours is None`；传空 `object_store_root` 不再回退到 workspace。
  - `canonical_json._json_default(object())` -> `TypeError`；naive `2026-05-07 00:00` 与 `+08:00` aware datetime -> 分别归一成末尾 `Z` 的 ISO8601；含 Unicode/时间 payload 的 `_json_bytes` -> 逐字节等于 `json.dumps(..., sort_keys=True, separators=(",", ":"), default=_json_default).encode("utf-8")`。
  - 同一含 Unicode payload 分别过 `canonical_json._json_bytes` 与 `file_store._json_bytes` -> 前者输出 `\u` 转义、后者输出原生 UTF-8，字节明确不等；两者各自 checksum 固定，不得共享实现。
  - unchanged canonical/catalog/store tests -> 全绿，schema/oracle 未改弱。

**Boundary-surface checklist**：
- Shared helper roots: `store/{object_store,safe_fs,object_path}.py` 只消费，MUST NOT 修改；forcing 自撰 grid identity/descriptor helper 单 owner。
- Public entrypoints: `yd_producer.forcing`；无 CLI/controller 接线。
- Read surfaces: work-local registry/model manifest、canonical catalog/NetCDF/grid definition、binding、`.sp.att`。
- Write/delete/overwrite surfaces: work object-store 的 forcing package/version sidecars；无 NFS、无跨 work 删除。
- Staging/publish/rollback: package finalize 前失败不 ready；NFS publish/cleanup 归 #24/#15。
- Producer/consumer evidence: canonical catalog → forcing package；forcing package → #15 assembler（package contract 测试钉形状）。
- Stale-state/idempotency: 同 source/cycle/model 与同 binding identity 重跑不得重复 ready；identity drift 必须重算/拒绝，不复用 stale ready。
- Unchanged downstream consumers: canonical converter、publish、prepare、state/tracker、viewer 均不改。

**Required evidence（input -> expected output）**：
- `openspec validate m2-producer-core --strict --no-interactive` 与 `openspec validate --all` -> 退出码 0。
- `bash scripts/check-stage-pipeline-log.sh origin/master` -> `m2-producer-core` 锚存在。
- `cd producer && uv sync --frozen` -> 退出码 0、lock 无 drift。
- `cd producer && uv run pytest` -> 全绿；新行为测试先对 pre-change source 做一次批量 red proof，随后恢复 source 全绿，stash 无 `red-proof` 残留。
- `cd producer && uv run ruff check . && uv run ruff format --check .` -> 退出码 0。
- provenance/DB-free focused suite -> 六行落地状态与文件头双向一致；现有单一禁区声明仍解析为原 8 项；逐项真实 import/name/env-call 注入均变红；仅含 `registry_manifest = "models/demo/registry.json"`、错误消息和普通变量的模块不命中。#14 证据不得创建第二个声明锚。
- canonical JSON focused tests -> `_json_default(object())` 抛 `TypeError`；naive/aware datetime 均归一成末尾 `Z`；`canonical_json._json_bytes` 与精确 `json.dumps` 字节相等；含 Unicode payload 下 canonical_json（ASCII escape）与 file_store（UTF-8）字节不等且各自 checksum 与独立字面 oracle 相等。
- D4/config focused tests -> 缺 `object_store_root` 或 `object_store_prefix` 构造期 `TypeError`；显式构造的五个上限/`min_lead_hours` 逐字等于 fixture 数值；空 root 不回退；pin 两个保留构造点仅补两个路径 kwarg且 L4229 继续使用 `max_manifest_bytes=16`。
- package schema focused tests -> 每份 station CSV 第 1 行为 `<row-count>\t6\t<start-date>\t<end-date>`，第 2 行逐字为 `Time_Day\tPrecip\tTemp\tRH\tWind\tRN`，第 3 行是首个数据行；`u/v=(3,4)` 与 `(6,8)` 的该行 Wind 字段分别为 `5` 与 `10`；lineage 恰保留 `contract_grid_signature` 合同值且不含两个被删重算键。
- mutation matrix（scratch 必须按 project-profile 的 venv/pyc 纪律）：`weight=1.0→0.5`、cycle formatter 改回 `valid_times[0]`、跳过 source/grid-signature 比较、descriptor helper 回退裸 Path、JSON byte/depth/node guard 各删除一腿、合并两套 `_json_bytes`、恢复 config 空串回退、恢复被删 lineage 键 -> 每个变异体至少一条对应新用例转红；报告实际红用例，不预写计数。Round 1 repair 另须杀死：删除 production-cycle domain guard、恢复 multi-source membership、移除 object-key/NetCDF identity 任一比较、移除 descriptor byte cap、从 output-config identity 删除 `rn_shortwave_factor`、宽松 decode `.sp.att`、删除 station/timestep/row limit guard；12Z expected 与 IFS uppercase URI 必须由 stdlib/字面 fixture 提供，不得复用 production parser/path builder。
- frozen final head 再跑 producer pytest + ruff + OpenSpec；若 `origin/master` 前进，按 profile 在 merge ref 上再跑同组。

**Known limits / deferral routing**：
- canonical converter 自身的 unbounded read 与 path-follow 分别由 #102/#103 跟踪；本 PR 只保证 forcing 新读面不复制缺陷，不修改 canonical 源码。
- IFS grid-definition URI 大小写裂口由 #104 跟踪；本 PR 按 catalog 精确 URI 消费。
- file-backend handoff package 的完整 parser/receipt 覆盖按 inventory 风险 12 归 #15；本 PR 只钉 forcing package 本身与直接 JSON identity，不恢复 2777 行校验器。
- 若三份 snapshot 文件触发 large-file exclude，Phase 8 由 issue-scribe 建立/去重规模债 follow-up；#100/#107 仅覆盖既有 rawcopy/canonical 文件，不能假称已覆盖本 PR 新文件。
- 真实 IFS/GFS 数值与 node-22 运行属 M4；本 PR 只声明合成 fixture 下的结构、映射、时间和 IO 安全。

**Non-goals**：
- 8.2 work-local registry 生成/清理、8.3 SHUD input 组装（#15）。测试可构造 manifest fixture，但生产代码不生成它。
- CLI/controller/Slurm/SHUD/checkpoint/NFS publish/retention；不真实连接 NWM、数据库、scheduler 或远端节点。
- 恢复 IDW、legacy 105 站、ERA5、数据库镜像、grid-registry/bbox preflight。
- 修改 canonical 物理转换、catalog schema、raw manifest、模型变体或 `cfg.ic`。

**Review focus**：
- 清单 §1 剥离点与抽取闭包逐项执行，六行状态与文件同提交翻转；无 DB/env/grid-registry 残面。
- station/grid/source/time 三重身份贯穿 parser → canonical read → mapping → package/lineage，错误均在 ready 前。
- `Time_Day=0` 是否真的由 cycle 参数约束，12Z 和缺 cycle 行是否能判红旧实现。
- bounded/no-follow 是否覆盖 manifest/catalog/binding/.sp.att/NetCDF 每个读面，且无裸 Path fallback。
- tests 的 expected values 是否来自 spec 字面/手算，不从实现重算；mutation/red proof 是否真实咬合。

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
- 由此**作废**：round 2 的 cand-16 / cand-18 对清单 §1 `packages/common/state_qc.py` 行的修订义务（该行已由 #9/#22 在 master 侧写全，本 PR 合并时取 master 侧为权威）；清单 §1「最小测试（cap 5 header）」行**取 master 侧措辞为底，并补一条重述义务**（commit `c24aea6`：`_read_cfg_ic_header_minute` 的规则在 #16/#22 之后被拆成 `header_time.cfg_ic_header_minute_time` 与 tracker 私有的 `_read_header_minute`/`_header_minute_of`，本仓不存在同名可导入符号，落地该行时 MUST 按实际符号名重述配对约束）。**MUST NOT 把该行"还原"回纯 master 措辞**——那会删掉这条义务（round 3 F2 的失败形态）。
- **搭车修改**：合并 master 时 `.large-file-guard.json` 增加两条 exclude（`producer/tests/test_cfg_ic.py` 1050 行、`producer/tests/test_state_tools_qc.py` 1129 行），二者是 master 上已有的超限文件（PR #61 走服务端合并，不经本地 PreToolUse 钩子），而钩子按暂存集判定，导致 master 向任何分支的合并都会被自己的守卫挡死。拆分超出本 issue 范围，已开 issue #82 跟踪（issue-scribe 核实时另钉出这是**第三次复发**：`3f7d46e`/`f130883` 已因同一原因豁免过 `test_config.py`/`test_geometry.py`/`test_rawscan.py`，豁免清单单调增长而守卫覆盖面单调缩小）；`maxLines` 不动，无任何断言/测试/CI 被削弱。


**本 issue 只做 9.1（捕获轮询）**，9.2（漏采补跑）归 issue #17。清单 §1 的第 6/7 行（`runtime.py` → `tracker/checkpoint_tracker.py`、`tests/test_shud_runtime.py` → `tests/test_checkpoint_tracker.py`）同时覆盖捕获与补跑两半，本 issue 只搬捕获半；两行的 `落地状态` 仍必须在本 PR 翻成 `本 issue 落地`——溯源守卫的反向判别器 `test_files_carrying_a_provenance_header_are_marked_landed` 一旦见到带溯源头的目标文件就要求该行标 `本 issue 落地`，留 `待落地` 会直接变红。**翻转 MUST 与文件落地同一个 commit**：正向判别器 `test_landed_snapshot_files_carry_their_provenance_header` 的缺席分支反向同样成立（「落地状态也不得先于文件翻转」），故 fixture 先行的 docs commit 里两行 MUST 仍是 `待落地`，由实现 commit 一并翻转。两行 `备注` 同步补记「本 issue 落捕获半，补跑半归 #17 落进同一文件」，并在 design.md **D9** 记录该分次落地偏离（spec `快照可追溯` Requirement 自带的逃生口：「或 design 中存在显式偏离记录」）。

**改动面**：

- 新增 `producer/src/yd_producer/tracker/__init__.py`、`producer/src/yd_producer/tracker/checkpoint_tracker.py`
- 新增 `producer/tests/test_checkpoint_tracker.py`
- **不改动** `producer/src/yd_producer/state/**` 与 `producer/tests/test_cfg_ic.py`（裁决修订 R1：header 分钟读取从 master 的 `state/header_time.py` **消费**，不再自行移植）
- 修改 `.large-file-guard.json`（合并 master 的搭车修改，见裁决修订 R1）
- 修改 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md`（§1 第 6/7 行 `落地状态` 与 `备注`；round 3 F1 另作废 `state_cli.py` 行与 cap 5 重戳行的 rekey 面 tracker 半路由）、`design.md`（D9 + 两条补记）、本文件（本 fixture + 勾选 9.1）
- 修改 `openspec/changes/m2-producer-core/specs/checkpoint-tracker/spec.md`（**收窄运行期 T+12 捕获的 epoch 接受面**——删「（或等价的 T+12 绝对分钟）」并加一段把 epoch 明确划出 tracker）与 `docs/compute-loop-design.md`（§9.2 步骤 3 同步同一条收窄）。**这两处是本 issue 唯一改到 spec / 设计文档正文的地方**：Known limits cand-19 要求 M4 首次真跑核验真实 header 形态，若判定为 epoch 形式则偏离 4 必须重新裁决，届时 MUST 连同这两处一并放宽，不得只改 fixture。
- **改动面枚举的机检口径**（round 3 Phase 7 P3-1：本枚举曾漏掉上面这两个文件，是 record-fidelity 枚举不完备的第四例）：散文枚举天生不可机检（round 3 Phase 7 P3-1 即由此漏掉两个文件，是 record-fidelity 枚举不完备的第四例），故本节另钉一份**逐字可 diff 的路径全集**：

```text
.large-file-guard.json
docs/compute-loop-design.md
openspec/changes/m2-producer-core/design.md
openspec/changes/m2-producer-core/nwm-snapshot-inventory.md
openspec/changes/m2-producer-core/specs/checkpoint-tracker/spec.md
openspec/changes/m2-producer-core/tasks.md
producer/src/yd_producer/tracker/__init__.py
producer/src/yd_producer/tracker/checkpoint_tracker.py
producer/tests/test_checkpoint_tracker.py
```

该块 MUST 与 `git diff origin/master --name-only | sort` 逐行相等（9 行）。合并前跑一次 `diff`，不等即视为本节陈旧。上面的散文条目只解释「改了什么、为什么」，**全集以本块为准**。
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
  1b. **非有限的 header 分钟（`nan` / `inf` / `-inf`）MUST 同样判为「本次观测无结果」**，MUST NOT 记进 `observed_header_minutes`。理由是硬的：本仓的 `cfg_ic_header_minute_time`（`state/header_time.py`） 与 pin 一样只做裸 `float()`，而 `float("nan")`/`float("inf")` 都解析成功——这正是 pin 的 `_format_header_minute`(:3634) 把非有限判定放在**第一步**的原因。若让它流下去，`round(nan)` 抛 `ValueError`、`round(inf)` 抛 `OverflowError`，两者都会穿透 `capture_available` 外泄给调用方（违 §A「不外泄」与本步「不抛错」），把一次撕裂读升级成整个 tracker 崩溃；记进观测轨迹同样有害——`nan != nan` 使相邻去重永不生效，轨迹被无限追加。撕裂的 `cfg.ic.update` 首行出现 `nan`/`inf` 是**真实可达**的：SHUD 就地覆写时数值区可能半写。
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

**Seams under test**（上游声明消费，不重议；**就地声明一处形态差异，不改 design.md**——承 #18/#19 先例）：design.md `Sketch seams under test` 第 5 条写作 `tracker.capture(shud_dir, target_minute) -> CheckpointResult`，本 fixture 落成 `CheckpointTracker(*, run_dir, project_name, checkpoint_hours)` + `capture_available()`。差异有两个来源：目标是**一组**小时（`Config.checkpoint_hours` 是元组）而非单个 `target_minute`；「漏采如实报告」要求跨多次观测**保持状态**（哪些已捕获、观测到过哪些 header），单次纯函数装不下。语义无差异，草图不改。


- `CheckpointTracker` 的构造 + `capture_available()` 序列调用（即 issue 正文的「模拟覆写序列」）：以合成 `cfg.ic` 字节按序覆写 `source_path`，逐次调用观测
- `state.cfg_ic_header_minute_time`（master `state/header_time.py` 的既有面，纯函数；本 issue 只消费，不重测——单元用例归 `producer/tests/test_header_time.py`）
- `state.parse` 作为副本校验 oracle（既有面，不新测）

**Fixture level: expanded**（override 上游的 `compact`；**本行系补记**——本 fixture 自始按 expanded 作业（完整 risk-pack 表、Review focus 常驻轴、§A–§G9 逐条 Required evidence、§G9 的不变式面清单等同 Invariant Matrix），但一直没把这条分级判定显式写下来，属「偏离必须记录、不得沉默」的漏记，round 3 后补上）。override 理由：改动面正面命中强制 expanded 触发词 `parser`/`format`/`schema`/`field`（header token 布局判定、相对分钟单位、产物文件名形态）与 `path`（全程 no-follow + containment 的文件系统面），并命中 `openspec/project-profile.md` 的 domain 触发词 `cfg.ic`、「状态链 / warm start」、`T+12`、`checkpoint`——与 issue #8/#9/#22 同一条覆写理由链。

**Repair intensity: high**（本模块是 T+12 状态的**唯一来源**，profile 把「断链即整链失效」列为首位风险轴；且它是本仓第一个在 SHUD **就地覆写的同一目录下**读写文件的模块，撕裂读是一等公民。适用 `Invariant Matrix`——本 fixture 以 §G9 不变式面清单落地）。

**Risk packs（selected / not selected 与理由）**：

- Public API / CLI / script entry: selected - `CheckpointTracker` 是 #17 与作业脚本的消费面
- Config / project setup: selected - `checkpoint_hours` 的权威归属与零默认是核心验收项
- File IO / path safety / overwrite: **selected** - 本模块读源文件、建目录、原子写副本、失败删副本，且全程在 SHUD 就地覆写的同一目录下；no-follow 与 containment 全部经 `store.safe_fs`
- Schema / columns / units / field names: selected - header 的「最后一个数值 token 即 minute-time」与相对分钟单位即本模块的 schema，错配直接导致永不命中或冒充命中
- Auth / permissions / secrets: not selected - 无凭据；产物权限由 `safe_fs` 既有语义决定，本模块不设 mode
- Concurrency / shared state / ordering: **selected** - 观测的是一个**正在被 SHUD 就地覆写**的文件，撕裂读是本设计的一等公民（校验-删除-重试即为此存在）；tracker 实例内的 `captured`/`observed` 是可变状态，顺序语义（一次性捕获、相邻去重）是契约
- Resource limits / large input / discovery: selected - 源文件为有界读（`MAX_STATE_IC_BYTES`），无递归发现
- Legacy compatibility / examples: not selected - 全新模块，仓库内零既有消费者
- Error handling / rollback / partial outputs: **selected** - 「校验失败即删副本、保持未捕获、可重试」与「漏采如实报告不冒充」都是 fail-closed 判据
- Release / packaging / dependency compatibility: selected - 只用 stdlib，不得新增依赖
- Documentation / migration notes: not selected - 无迁移
- Geospatial / CRS: not selected
- Time series / forcing / temporal boundaries: **selected** - 相对分钟 ↔ 目标小时的换算、以及「不接受 epoch 形式」是本模块的时间语义分界
- 状态链 / warm-start 定戳: selected - 捕获产物是 T+12 状态的来源，冒充或漏报直接污染状态链
- NWM 快照溯源 / DB-free 隔离: **selected** - 本模块是快照件，溯源头与清单行的双向义务、以及零 NWM import / 零数据库连接由既有守卫承担

**Review focus（常驻轴，承 issue #11 三轮硬闸 retro）**：审核方 MUST 逐条核对以下三轴**各自**都有「改坏即变红」的 oracle，缺任一轴即为 finding：

1. **失败路径**：构造期四类拒绝、源文件缺失/空/畸形 header、副本校验失败
2. **结构属性**：文件名形态、目标目录、`captured` 只读性、`missing_hours` 升序
3. **捕获阶段的实现级 MUST**（round 1 verifier 的批级结论）：**Required evidence MUST NOT 只沿 spec 的四个 Scenario 反推**——Scenario 只描述**观测层**的输入-结果对，而 §D 把「header 命中 → `captured[h]` 落表」这一段写得最细（回读两项校验**各自**的判别力、异常收敛在捕获段的对偶、有界读、失败即删）。这些只活在 fixture 散文里的实现级 MUST **MUST 逐条过一遍「改坏即变红」的筛子**并落进 Required evidence。round 1 有四条 CONFIRMED 全部落在这一段，且它们不是四次独立疏漏，是同一条枚举流程偏差。
4. **成功路径的输入归一化**（issue #11 三轮硬闸的直接产物，历史上整轴缺席）：`round()` 的存在（`m=719.6` 命中 `h=12`）、相邻去重（同值连续观测只记一次）、已捕获跳过（第二次同值观测不重写副本）、`f{hour:03d}` 的补零

**Required evidence**（逐条可机检；测试 MUST 覆盖每一条）：

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

1. **轴 4 `max_bytes` 行的结账尾句为假**（`test_checkpoint_tracker.py` 结账表）。表写「三处同时放开 -> `test_oversize_source_is_not_captured`」，但轴 4 的「三处」按本节定义是**源读 / 回读 / header 读**三个 `safe_fs` 关键字实参；审计者实测三处同时 ×100 **全绿**。真正的红是 §G8 第三条那一组：**源读 / 回读 / `state.parse` 自带上限**（`state/cfg_ic.py:313`，不是 `safe_fs` 的 kwarg，不属轴 4 单元；**审计报告与本节初稿都把该行记成 `:166`，实测为 `:313`，`:166` 落在 `CfgIcDocument` 字段声明处，由实现方指出后按实测更正**）。MUST 改成后者措辞并点明 `state.parse` 上限不属轴 4。逐单元记账（三处各自单独放开为等价）经实测属实，只有尾句的交叉引用是假的——这正是本节 cand-15「转述即核实」同形，故判不通过而非 Note。
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
- **任何清单 / design / spec 修订** -> **重走 fixture 自身**。（round 3 F2 的可命名设计缺陷：上列四条触发器的目标集只有 PR body 与清单，**定义本清单的这份 fixture 不在任何触发器的目标集里**，于是 `c24aea6` 那次清单修订在纸面上不欠 fixture 任何义务，fixture 成了唯一没被对账的站点。）

**触发器 1 的完备性 oracle（round 3 F1 的可命名设计缺陷，本 issue 起常驻）**：「重走**全部**行」此前只以人工断言存在，走一行和走全部行**在纸面上不可区分**——这正是本节自己那条「完备性声明必须可机检」被违反，只是违反发生在**上一层**（对账清单自身就是一条未加机检的完备性声明）。故触发器 1 的执行 MUST 产出一件**可数的产物**：一张**逐行处置表**（清单 §1 的行号 -> 本次重走结论，结论取 `无需改动` / `已改动（改了什么）` / `作废（作废了谁的什么声明）` 三者之一），落在 PR body 或 `.workplans/pr-<n>/` 下。**该表的行数 MUST 等于清单 §1 的数据行数**，口径写死为

```bash
awk '/^## 1\./,/^## 2\./' openspec/changes/m2-producer-core/nwm-snapshot-inventory.md | grep -c '^| '
```

减 2（表头行与分隔行）。issue #16 落地时该值为 **29 - 2 = 27**。行数不等即视为触发器 1 未执行。Phase 7 的终审 brief MUST 核这张表存在且行数相等。

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
- **相对 `run_dir` 会让实例状态与磁盘状态 fail-open 地错位**（round 3 cand-r3is-01，CONFIRMED/DEFER）：构造期原样存 `Path(run_dir)` 不校验绝对性，而 `safe_fs._expand_path` 对非绝对路径**每次调用**都按当时的 `Path.cwd()` 重新锚定。已实测的失败序列：cwd=A 观测 header 360 → 进程 `chdir` 到 B → 再观测 B 下另一份文件的 720，结果 `missing_hours()` 返回 `()`（声称 A 这一轮的 T+12 已捕获，实际从未捕获），且 `CapturedCheckpoint.path` 是相对路径，换 cwd 即「有记录、无文件」。**与 cand-03 失败方向相反**（那条 fail closed、`missing_hours()` 诚实），故不是重复。归属与 cand-03 **同一条 tracked issue #77**，措辞 MUST 收紧为「`run_dir` MUST 规范：**既绝对、祖先亦无符号链接**」，并注明两者方向相反。本 PR **不加**构造期 `is_absolute()` 守卫：verifier 裁定它超出本 PR 声明的改动面——§B 的构造期拒绝清单是闭合枚举，轴 6 那张刚被 Phase 6.2 独立审计封账的「10 行 = 10 见证」结账表要加第 11 行，须先改 fixture 再动码，而那会重新打开刚关掉的审计。当前不可达于声明域：全仓 `CheckpointTracker` 除模块自身与测试（`tmp_path`，恒绝对）外零调用点，`config.py` 无 `run_dir` 字段，`src/` 内零 `chdir`。
- **epoch 形式 header 的 M4 具体核验钩子**（cand-19，CONFIRMED/FIX_NOW）：偏离 4「只认相对分钟」的正当性**只**建立在时间线证据上——`docs/compute-loop-design.md` 在本 PR 之前就已声明 `cfg.ic.update` 的 header 是模型相对分钟，且 spec 的收窄早于实现提交 26 分钟。它**不**建立在 pin 的行为上：round 2 直读 pin 控制流确认，`capture_available`(:3717-3736) 把 `_header_minute_matches_checkpoint`(:3963-3974) 的**两支都无条件**用在同一个 `<project>.cfg.ic.update` 上，分支注释只是归因不是守卫；而 yd 自己的初态正是 epoch 定戳的（pin `_shift_cfg_ic_time`(:3653) 在求解前把绝对分钟写进 header），所以「SHUD 把初态的时间基带进 update 文件」是**默认生产拓扑**而非异常。**M4 首次真跑 MUST 核验第一份真实 `cfg.ic.update` 的 header 是相对分钟形式**；若为 epoch 形式则每轮永久漏采（fail closed 且响亮，但总量为零），偏离 4 MUST 重新裁决。此处不接受通用的「真实 SHUD 行为归 M4」一句——它不会被解析成这一项具体检查（同类先例：cand-14 已按此标准给了自己的具体钩子）。

**欠 #17 fixture 的一项显式裁决（round 3 F4，CONFIRMED/P3；本 issue 不替 #17 决定，只把它记成必须先裁的项）**：本 issue 在三处写死「补跑半随 #17 落进**同一个文件**」（本 fixture、design.md D9、清单 §1 cap 6 行），测试模块头的结账表另写「#17 MUST 按同一格式续表」。而 `producer/tests/test_checkpoint_tracker.py` 落地即 **806 行**，`.large-file-guard.json` 的 `maxLines` 为 **1000**、`exclude` 不含该文件，余量 **194 行**。碰撞是被清单自己的闭包清单**强制**的、不是密度估算：cap 6 行把 19 个补跑用例的闭包写死为 8 个 helper 加 13 项模块级常量（pin 上仅 `_FAST_SOLVER_STUB`(L4678)–`_DISTINCTIVE_STAGED_IC`(L5136) 一段就跨约 458 行）并明写「无法手搓等价，必须整体搬运」，实测本文件对 `SOLVER_STUB`/`_write_basins_package`/`install_recovered`/`run_shud`/`recover` 的命中**全为 0**——闭包一行未落，单它就超余量一倍以上，19 个用例本体与续表尚未计入。钩子按暂存集判定，故 #17 的**每一次本地 `git commit`** 都会被 `exit 2` 拒绝。#17 的 fixture MUST 在动码前三选一并写明理由：**(a)** 把测试拆成 `test_checkpoint_tracker.py` + `test_checkpoint_recovery.py` 两个文件，同时修订本 issue 写下的三处「同一个文件」MUST 与清单 cap 6 行；**(b)** 给 `test_checkpoint_tracker.py` 加**第四条** exclude——注意这正是 issue #82 记录在案的「豁免清单单调增长而守卫覆盖面单调缩小」模式的第四次复发，选它必须在 #82 里同步登记；**(c)** 拆分被搬运的闭包（把 13 项常量与 8 个 helper 落进独立 fixture 模块），只在本文件留用例。**MUST NOT 默认走 (b)**。

**Non-goals（本 issue 明示不做）**：漏采补跑（#17）、轮询循环与作业脚本接线、`state_checkpoints.json` 落盘、绝对 T+12 定戳（#9 重戳 + #13.1 发布）、river 行数等结构检查（#9）、work manifest 契约（组 8）、真实 SHUD 行为（M4）。

## 10. prepare-variants：变体与几何

- [x] 10.1 引入几何依赖（pyshp/pyproj/shapely）并 `uv lock`，构造带自定义 Albers `.prj` 的合成 shapefile 基线 fixture，实现 `.prj` 解析与重投影工具，CI 绿
- [x] 10.2 实现 `rivers.geojson`（`reach_id`=DBF Index、数量一致）与 `boundary.geojson`（单元合并边界）生成，落点 `input/viewer/`
- [x] 10.3 实现 prepare 编排：拒绝覆盖检查 → 薄外壳按源两次调用 builder（记录型假 builder 断言两次入参 source/grid 不同、输出分别落 `yd_gfs`/`yd_ifs`）→ 变体 reach 数等于 `reach_count` 校验 → 提交到 `input/models/` 与 `input/viewer/` → scratch 清理

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

### Issue #20 fixture（任务 10.3）

Fixture level: expanded
Upstream suggested level: expanded（agree）
Repair intensity: **high** —— 拒绝覆盖、目录级提交/回滚、scratch 删除三面同时落在写/删/发布语义上，属 `phase-flow` Phase 0.5 的 high 触发词全集（file IO / path safety、publish/delete/rollback、数据丢失）。fixture level 不因此上抬（`expanded` 已是本 change 的上限档），但 Invariant Matrix 与边界面清单为硬门禁
Project profile: yd-viewer

Change surface:
- 新增 `producer/src/yd_producer/prepare.py`：`run_prepare` 编排、`VariantBuildRequest`、`PrepareError`、生产 builder 绑定、变体终名解析函数
- 扩展 `producer/src/yd_producer/config.py`：新增必需字段 `nwm_canonical_grid_id`（表，含 `gfs`/`ifs` 两个 `str`），与既有 `nwm_mapping_builder_module` 同纪律——只校验存在性与类型
- 扩展 `producer/src/yd_producer/cli.py`：`prepare` 子命令新增必需参数 `--baseline`；`prepare()` 由退出码 `3` 的未实现分支改为真实委托 `prepare.run_prepare`；`main` 的异常兜底新增 `PrepareError`
- 扩展 `producer/tests/cli_fixtures.py`（`config.toml` 生成器补新字段；`nwm_canonical_grid_id.gfs`/`.ifs` MUST 取**两个不同值**，否则「两次 `grid_id` 不同」这条断言退化成永真式——同 `cli_fixtures.py:10-12` 对 `nwm_mapping_builder_module` 的既有纪律）、`producer/tests/test_config.py`、`producer/tests/test_cli.py`、`producer/tests/test_rawscan.py`（`test_rawscan.py:75` 直接构造 `Config`，新增必需字段后须补齐）
- 新增 `producer/tests/prepare_fixtures.py`（合成基线包 + 记录型假 builder）与 `producer/tests/test_prepare.py`
- 复用不改：`geometry.write_viewer_geojson`、`state/cfg_ic.parse`、`store/safe_fs.*`、`nwm.check_interpreter`/`invoke_mapping_builder`
- scratch -> `YD_ROOT` staging 的树复制目前 `safe_fs` 无对应原语；新增的复制逻辑 MUST 按发布权限新建条目（不继承源 mode/属主），并只落在 `prepare.py` 内（不扩 `safe_fs` 的公共面，那是 #24/#25 发布面的归属）
- 不新增依赖、`producer/uv.lock` 不变；不触碰 forcing/controller/rawscan/executor/slurm

Must preserve:
- `geometry.py` 四个 10.1 函数与三个 10.2 函数的签名与失败契约（`GeometryError` 仍是该模块唯一公开异常）不变；`write_viewer_geojson` 的 `out_dir` 语义不变——本 issue **消费**它写 `YD_ROOT` 内 staging（见 `run_prepare` 步骤 6），不改它
- `state/cfg_ic.parse` 的字节保真契约与异常类型不变（本 issue 只读 `river.row_count`）
- `cli.py` 既有退出码约定：`2` argparse 用法错误、`1` 守卫/配置失败、`3` 分阶段未实现；`DATABASE_URL` 守卫仍是 `main()` 的第一件事、先于 `parse_args`；`--config`/`--local` 仍 `Path.resolve()` 后交装载器
- `init`/`run` 两个子命令的参数集合不变（`--baseline` 只加在 `prepare`）
- `config.py` 的 stdlib 中立性与"零内置默认"；新字段是**必需**字段，故 issue #2/#3/#30 遗留的全部 `Config` 构造点（测试 fixture、`cli_fixtures.py`）必须同步补齐并继续全绿
- CI 四个 job 全绿

Must add/change:
- `cli.py`：`prepare` 子命令加 `--baseline`（`required=True`、`type=Path`），与 `--config`/`--local` 同纪律 `resolve()` 后传下游；`init`/`run` 不加
- `prepare.variant_targets(local, config) -> dict[str, Path]`：**唯一**的变体终名来源，拒绝覆盖检查与提交写入 MUST 都走它（#32 记录的守卫/写入面分叉在此消除）
  - 值取自 `config.variants.gfs`/`.ifs`，形态为 `Path(local.yd_root) / <relative>`
  - 相对性 fail-closed：`variants.*` 为绝对路径、或规范化后逃出 `yd_root`（含 `..`）-> `PrepareError` 点名该字段与该值
  - **两两互异 fail-closed**：`variant_targets` ∪ `viewer_targets` 的四个终名 MUST 两两不同，且任一变体终名 MUST NOT 是另一终名的祖先。`config.toml` 里把 `variants.gfs` 与 `variants.ifs` 抄成同一值（装载器只校验存在性与类型，不拦）会让两个终名重合：`lexists` 守卫全过（两者都不存在）、`gfs` 提交成功、第二次 rename 撞 `ENOTEMPTY`，`YD_ROOT` 停在"只有一个变体"的半提交态——这是普通配置笔误就能触发的、直接违反总不变量的路径，必须在任何写入之前拦下
- `prepare.viewer_targets(local) -> dict[str, Path]`：`input/viewer/rivers.geojson` 与 `boundary.geojson`（products-contract §2 的字面量落点，非配置驱动）
- `prepare.VariantBuildRequest`（frozen dataclass）：`source_id: str`、`grid_id: str`、`baseline_root: Path`、`variant_root: Path`
  - 字段形态是**消费上游契约、不重新协商**：`source_id` 与 `grid_id` 逐字对应 pin `NWM@8ae9b8f2 workers/mapping_builder/cli.py:601-602` 的 `build_direct_grid_variant` 同名关键字参数
  - `source_id` 取值走既有 `raw/source_identity.normalize_source_id` 的 `"gfs"`/`"ifs"`；`grid_id` 取自 `config.nwm_canonical_grid_id`
- `prepare.run_prepare(*, local, config, baseline_root, builder=<生产绑定>) -> PrepareReport`，严格按序：
  1. **拒绝覆盖**：四个终名（两变体目录 + 两 GeoJSON）任一 `lexists` 即 `PrepareError`，此时 MUST NOT 创建 scratch、MUST NOT 调 builder
  2. 在 `local.scratch_root` 下建**本次运行专属**工作目录（名字含 pid + 随机 token，避免并发/重跑互相覆写）
  3. 对 `("gfs","ifs")` 各建一个**此前不存在**的 `variant_root` 子目录，各调 `builder(request)` 一次
  4. **产物校验**（逐变体）：`variant_root` 存在且为目录；率定末态 `cfg.ic` 可 `cfg_ic.parse`；`doc.river` 非 `None`（`Section | None` —— 缺 river 段 MUST 判失败，MUST NOT 当 0 条）；`doc.river.row_count == config.reach_count`；目录内无 `.tmp` 后缀或其它未预期残留条目
  5. **搬运到 `YD_ROOT` 内 staging**：在 `YD_ROOT` 之内建本次专属 staging 位置，把校验通过的两棵变体树按**发布权限新建条目**的方式复制进去（MUST NOT `cp -a`/`copytree(copy2)` 把计算节点 uid/gid/mode 带进 NFS，agent-ops §10）
  6. **GeoJSON 直接落 staging**：`geometry.write_viewer_geojson` 的 `out_dir` 取该 `YD_ROOT` 内 staging 位置，两份 GeoJSON **不经 scratch**（唯一落点，无第二处 staging）。staging 位置 MUST NOT 落在 `input/viewer/` 之内——products-contract §2 只允许该目录存在两个文件，把 staging 建在里面等于让 viewer 看见中间态
  7. **提交**：四个终名逐个 rename 提交（`safe_fs.rename_entry_no_follow`），源为 staging 内条目——**同文件系统**；顺序钉死为「两变体 → rivers → boundary」
  8. **清理**：无论成败，scratch 工作目录与 `YD_ROOT` 内 staging 位置一并删除；提交阶段失败时**同时**删除本次为提交而新建的父目录（仅限本次新建的），使 `YD_ROOT` 回到执行前的条目集合

  > **为什么不是"scratch 目录直接 rename 到 `YD_ROOT`"**（PR #50 路由过来的审计建议的字面形态）：生产上 `yd_root` 在 NFS（`/ghdc/data/yd`，agent-ops §4.1）而 `scratch_root` 在本地盘（`/scratch/.../yd-loop/`，agent-ops §4.2）——两棵真不同的树（`producer/tests/test_cli.py:220-222` 已就此立过约定），而 `safe_fs.rename_entry_no_follow` 明写 `EXDEV` 是硬错误、**刻意没有** fallback copy 路径（`store/safe_fs.py:630-631`）。直接 rename 会在本地测试（两根同在 `tmp_path`）全绿而在现场必然失败。本协议与控制器发布面的既有做法同构：agent-ops §8.4「DAT 复制到 NFS 临时文件并在 NFS 内 rename」
- 异常与退出码（两级，**不得合并**）：`prepare.PrepareError` 是本模块公开异常**基类**，`cli.main` 捕获后走退出码 `1`（fail-closed 校验拒绝）；`prepare.BuilderUnavailableError(PrepareError)` 专表"生产 builder 绑定尚未可用"，`cli.main` 先于基类捕获它并走退出码 `3`（与既有"分阶段未实现"约定一致）。两者可区分是硬要求——把"配置/产物不合法"与"这条路还没通"报成同一个码，运维无从判断该改配置还是该等 M4。
  - **外来异常一律包装**，源恰为三处（此即全集）：`cfg_ic.parse` 的 `ValueError`、`geometry.*` 的 `GeometryError`、`safe_fs.*` 的 `SafeFilesystemError`。第三处最易漏：`rename_entry_no_follow` 把 `renameat` 的 `OSError` 包成 `SafeFilesystemError`，而后者是 **`RuntimeError` 子类而非 `OSError`**（`store/safe_fs.py:11`），`except OSError` 兜不住它；`cli.main` 只捕 `ConfigError`（`cli.py:186`），逃逸即打 traceback 而非干净退出 `1`。包装 MUST 保留 `__cause__`
- **生产 builder 绑定 fail-closed**：默认 builder 在**发起任何子进程之前**抛出指名归属的失败（`cli` 侧映射为退出码 `3`，与既有"分阶段未实现"约定一致）。理由是对 pin 的只读取证，不是未做：
  - `workers/mapping_builder/cli.py` 的 argparse `main` 在 pin 上只解析 `--package-path` 并输出 resolution JSON，**不驱动 build**（其 docstring 明写 SUB-5 未落地），故 `-m workers.mapping_builder.cli` 形态不足以产出变体——这正是 `tasks.md` 组 8 已记入 #32 的待确认项，本 issue 以取证结清
  - 唯一能建变体的是 `build_direct_grid_variant`，它是 **programmatic-only** 且需调用方预先算好约 24 个关键字参数（`grid_snapshot_loader`/`snapshot_cells`/`grid_snapshot_reference`/`approvals`/`rollback_target`/`distance_qa`/`capacity_report`/`proj_crs_database_version` 等），其中多项来自 NWM grid registry；而 yd MUST NOT 在运行时 import NWM（D6 / agent-ops §7.2），故真实调用需要 NWM 侧另加 driver，归 M4
  - 该绑定 MUST NOT 静默成功、MUST NOT 先起子进程再失败（后者会拿到退出码 0 的 resolution JSON，随后在 reach 校验处报出一条**归属谎报**的错误）
- `config.py`：`nwm_canonical_grid_id` 表 -> `CanonicalGridConfig(gfs: str, ifs: str)`，走既有 `_require_table`/`_require_str`，缺失或类型错即 `ConfigError` 并指明字段名

Seams under test:
- `prepare.run_prepare(*, local, config, baseline_root, builder) -> PrepareReport`（合成 `YD_ROOT`/scratch 目录树 + 记录型假 builder）——本 issue 的主 seam，spec prepare-variants 五类 Scenario 全部在此行使
- `prepare.variant_targets(local, config) -> dict[str, Path]`（纯函数）——守卫/写入同源与相对性校验的最细边界
- `cli.main(argv, env) -> int`（design.md seam 6，进程内）——`--baseline` 必需性与 `prepare` 委托形态；委托目标以注入 fake 替换，不牵连业务模块
- 上游 seam 缺口同 #18/#19：design.md 5 条主干 seam 不含 prepare 编排层；本 fixture 就地声明，不改 design.md

测试 oracle（禁止用被测实现自判）:
- **假 builder 是记录型 + 可编排**：记录每次调用的 `VariantBuildRequest`，并按测试给定的剧本在 `variant_root` 内写出合成变体（率定末态 `cfg.ic` 的 river 段行数可编排、可额外留 `.tmp`、可整个不建目录）
- 合成率定末态 `cfg.ic` 复用既有 `producer/tests/cfg_ic_fixtures.py` 的原生分段生成器，**不在本 issue 手写第二套格式**——reach 数的期望值由生成器写入的 river 行数给定
- 合成基线 GIS 复用 `producer/tests/geometry_fixtures.py`（10.1/10.2 已钉死的锚点纪律）；GeoJSON 内容正确性归 10.2 的既有用例，本 issue 只断言**落点、数量与提交/清理语义**
- "无新写入"一律以**执行前后 `YD_ROOT` 全树快照（相对路径 + 文件字节）逐一比对**断言，不用"某个特定文件不存在"这种单点探测——单点探测对"写到别处去了"的实现恒真
- 基线包内部布局与变体内率定末态的文件名是**本 fixture 定义的合成约定**，以 `prepare.py` 的共享常量/函数暴露给 11.1 消费；真实布局的核实归 M4（tasks.md 组 10 已记：真实外部基线模型包的读取与其现场路径属 M4）

Required evidence（每条 input -> expected output）:
- 干净 `YD_ROOT` + 合法合成基线 -> 退出成功；`YD_ROOT` **全树条目集合**等于「执行前 ∪ 恰好四个终名及其必要父目录」——即**无 staging 残留、无多余目录**（单点探测 `input/models/`、`input/viewer/` 各有几个条目对"staging 留在 `YD_ROOT` 顶层"恒真，故此处必须走全树）；`input/viewer/` 下**恰有** `rivers.geojson` 与 `boundary.geojson` 两个条目；`scratch_root` 下无任何残留条目
- 同一次成功运行 -> 假 builder 恰被调用 2 次；两次 `source_id` 分别为 `"gfs"`/`"ifs"`、两次 `grid_id` 取自 config 且互不相等；两次 `variant_root` 互不相同、**调用当时为空且由本次运行新建**（编排在调用 builder 前建目录，故「调用前不存在」只能指「不是上一次运行的遗留」——该性质由「两次运行取不同 scratch/staging 名」的用例行使）、均在 `scratch_root` 之下；两次 `baseline_root` 相同
- 成功运行 -> 两变体的水文参数文件（假 builder 从同一基线复制）字节一致（同源）；两变体的 binding 文件字节不同（不共用）
- `YD_ROOT/input/models/yd_gfs` 预先存在 -> `PrepareError`；全树快照与执行前完全一致；`scratch_root` 无新条目；builder 调用次数 **0**
- `YD_ROOT/input/viewer/rivers.geojson` 预先存在且内容已知 -> `PrepareError`；该文件字节与执行前**逐字节相等**；`input/models/` 无新目录；builder 调用次数 **0**（PR #50 路由过来的 CONFIRMED 发现在此结清）
- `input/viewer/boundary.geojson` 预先存在（rivers 不存在）-> 同上，同样 0 次调用
- `config.variants.gfs` 为绝对路径 -> `PrepareError` 点名 `variants.gfs` 与该值；该绝对路径下与 `YD_ROOT` 均无写入；builder 调用次数 0
- `config.variants.gfs` 含 `..` 且规范化后逃出 `yd_root` -> 同上
- **守卫/写入同源判别性证据**：把 `variants.gfs` 改成非默认相对值（如 `models/alt_gfs`），预先在**该新路径**上放一个同名目录 -> 必须被拒绝（守卫跟着 config 走，而非钉死字面量 `input/models/yd_gfs`）；反向：字面量 `input/models/yd_gfs` 存在但 config 指向别处 -> 提交落在 config 指定处
- 假 builder 产出的变体 river 段行数 ≠ `reach_count` -> `PrepareError` 点名该 source、期望值与实际值；`YD_ROOT` 全树快照与执行前一致；`scratch_root` 无残留
- 假 builder 产出的率定末态 `cfg.ic` **无 river 段** -> `PrepareError`（消息区分于"数量不符"）；MUST NOT 判为 0 条；`YD_ROOT` 无新写入
- 假 builder 在 `variant_root` 内留下一个 `.tmp` 文件（其余合法）-> `PrepareError` 点名该残留条目；`input/models/` 下无任何变体目录
- 假 builder 对 `ifs` 抛异常（`gfs` 已成功建好）-> `PrepareError`；`YD_ROOT` 全树快照与执行前一致（**`gfs` 变体不得被提交**）；`scratch_root` 无残留
- **失败路径清理**：上述每一条失败用例都断言 `scratch_root` 下无本次工作目录
- 假 builder 产出的变体率定末态 `cfg.ic` **不可解析**（截断/非 UTF-8）-> `PrepareError`（`cfg_ic.parse` 的 `ValueError` MUST NOT 逃逸出 `prepare`）；`YD_ROOT` 全树快照与执行前一致
- 假 builder 返回但**根本没建** `variant_root` -> `PrepareError` 点名该 source；`YD_ROOT` 无新写入；scratch 已清
- `write_viewer_geojson` 抛 `GeometryError`（注入损坏的 domain 图层）-> `PrepareError`（`GeometryError` MUST NOT 逃逸）；`YD_ROOT` 全树逐字节不变（**两个变体已校验通过也不得提交**）；scratch 与 staging 均已清
- `variants.gfs == variants.ifs` -> `PrepareError` 点名两字段；builder 调用次数 **0**；`YD_ROOT` 全树逐字节不变
- **终名互为祖先**：`variants.gfs = "input/models/a"`、`variants.ifs = "input/models/a/b"` -> 在任何写入与任何 builder 调用之前即 `PrepareError` 点名两字段；builder 调用次数 **0**；`YD_ROOT` 全树逐字节不变。两个 `lexists` 守卫与步骤 4 的产物校验都不会发现它，而提交后 `ifs` 变体会躺在已提交的 `gfs` 变体**目录内部**
- staging 位置不在 `input/viewer/` 之内（断言 staging 的实际路径），且成功后该位置已不存在
- **同文件系统判别性证据**：成功路径下断言每次 rename 的源与终名 `os.stat().st_dev` 相等，且源位于 `yd_root` 之内——把 staging 放回 scratch 的实现在这条上必红（生产 `EXDEV`，本地两根同盘时不会自己暴露）
- **提交中途失败**：注入令首次终名 rename 失败的探针 -> `PrepareError`（`SafeFilesystemError` MUST NOT 逃逸；它是 `RuntimeError` 子类，`except OSError` 兜不住）；`YD_ROOT` 的**条目集合**与执行前相同（本次为提交新建的父目录与 staging 均已回滚）；scratch 已清
- 不注入 builder（走生产绑定）-> `BuilderUnavailableError`，消息指名归属（承接它的任务号，或在无编号任务时指名承接阶段）；注入的 `runner`/`subprocess` 探针调用次数 **0**（在起子进程之前就停）；`YD_ROOT` 无写入；`scratch_root` 无残留
- `main(["prepare", ...])` 走生产 builder 绑定 -> 退出码 **`3`**；同一组参数下由 `PrepareError` 拒绝（如 reach 数不符）-> 退出码 **`1`**（两码可区分是断言点）
- `main(["prepare", "--config", ..., "--local", ...])` 缺 `--baseline` -> `SystemExit(2)`；注入的配置装载 fake 与 `run_prepare` fake 调用次数均为 0
- `main(["prepare", ..., "--baseline", <path>])` 且注入成功的 `run_prepare` fake -> 退出码 0，fake 收到的 `baseline_root` 是 `Path.resolve()` 后的绝对路径
- `main(["prepare", ...])` 且注入抛 `PrepareError` 的 fake -> 退出码 `1`，stderr 含该消息
- **清理证据必须到达运维（round-2 verified，cand-r2-A1/A2）**：`main(["prepare", ...])` 走生产绑定且两个删除原语均注入失败 -> 退出码仍 `3`，stderr **同时**含 `BuilderUnavailableError` 消息与注入的清理失败文本，且不含 `Traceback`（`str(exc)` 不含 `__notes__`，只断退出码的用例对本条恒绿）；注入返回带非空 `cleanup_warnings` 报告的 `run_prepare` fake -> 退出码 `0` 且 stderr 含每条告警
- **异常类边界必须被钉死（round-2 verified，cand-r2-B1）**：三条判别性用例——(a) 回滚的**最后**一个清理步骤抛 `KeyboardInterrupt` -> 该 `KeyboardInterrupt` 上抛（`except BaseException` 变异体在此必红）；(b) 清理步骤内 `prepare.os.close` 抛裸 `OSError` -> 其余步骤仍执行、上抛的仍是原始 `PrepareError`、该 `OSError` 进 `__notes__`（`except PrepareError` 变异体在此必红；注意经 `rmtree_no_follow` 注入的 `OSError` 被 `_wrap_fs` 翻译，杀不掉该变异体）；(c) builder 抛 `KeyboardInterrupt` -> 回滚完成后原样上抛（回滚边界收窄为 `except Exception` 的变异体在此必红）。全套注入面此前只有 `SafeFilesystemError`，它被 `_wrap_fs` 翻译成 `PrepareError`，令三种异常类选择塌缩到同一条路径
- **散文声明的行为契约必须逐条可判别（round-3 verified，cand-r3-1/-2；Review Failure Retro 形状 depth 的闭合动作）**：`prepare.py` 与 `cli.py` 中每一条带 `MUST`/`MUST NOT`/`刻意`/`钉死`/`不做…兜底` 的散文条款，MUST 就地注明钉住它的测试 id，或显式标注「等价变异/不可判别」或「归 M4，不在本阶段声明」。逐条补测不构成闭合——round-2 的修复提交自己又造出了 round-3 的 UNPINNED 条款。具体证据行：`main` 的退出码 **1** 分支同样渲染 `__notes__`（注入抛带 note 的 `PrepareError` 的 `run_prepare` fake -> 退出码 1、stderr 含 note 文本、不含 `Traceback`；note 文本 MUST NOT 是 `str(exc)` 的子串，否则 `_fail` 单独即可满足、变异体存活）；`prepare.py` 模块头 I1 段落 MUST NOT 声称 `cli` 只打印 `str(exc)`（该句已被 `f124f4b` 证伪）
- `main(["init", ...])` / `main(["run", ...])` 带 `--baseline` -> `SystemExit(2)`（该参数只属 prepare）
- `build_parser()` 的 `prepare` 子 parser 必需参数集合恰为 `{--config, --local, --baseline}`；`init`/`run` 恰为 `{--config, --local}`
- `DATABASE_URL` 存在时 `["prepare", ..., "--baseline", ...]` -> 退出码 `1`，stderr 指名该变量且**不含其值**，`run_prepare` fake 调用次数 0（既有守卫不因新参数而位移）
- `config.toml` 缺 `nwm_canonical_grid_id` -> `ConfigError` 指明字段名；缺 `nwm_canonical_grid_id.ifs` -> `ConfigError` 指明 `nwm_canonical_grid_id.ifs`；该键为非表类型 / 其值为非 `str` -> `ConfigError` 指明类型
- `cd producer && uv run pytest` -> 退出码 0（既有全部用例继续通过）
- `cd producer && uv run ruff check . && uv run ruff format --check .` -> 退出码 0
- `cd producer && uv sync --frozen` -> 无 lock drift
- `openspec validate m2-producer-core --strict --no-interactive` -> 退出码 0
- CI 四个 job 绿

Invariant Matrix:
- Governing invariant: `prepare` 对 `YD_ROOT` 的效果 MUST 是**全有或全无**——要么四个终名（两变体 + 两 GeoJSON）全部由本次运行新建，要么 `YD_ROOT` 回到执行前的条目集合且**既有内容逐字节不变**（本次为提交新建的父目录与 staging 属本次条目，失败时 MUST 一并回滚）；任何既有条目 MUST NOT 被覆盖或删除；无论成败 scratch 工作目录与 staging MUST 被删除。唯一已接受的例外是四个终名 rename 之间的进程被杀窗口（见 Failure paths 行）
- Source-of-truth identity/contract: 变体终名由 `variant_targets(local, config)` 单点计算（`local.yd_root` + `config.variants.*`）；GeoJSON 终名由 products-contract §2 字面量给定；reach 身份由变体率定末态 `cfg.ic` 的 river 段行数对 `config.reach_count`
- Producers: `prepare.run_prepare`、注入的 `builder`、`geometry.write_viewer_geojson`
- Validators/preflight: 四终名 `lexists` 拒绝覆盖检查；`variants.*` 相对性校验；`cfg_ic.parse` + river 段存在性 + 行数校验；scratch 目录内容精确集合校验
- Storage/cache/query: `YD_ROOT/input/models/{yd_gfs,yd_ifs}`、`YD_ROOT/input/viewer/{rivers,boundary}.geojson`、`scratch_root/<本次专属>`
- Public routes/entrypoints: `yd-producer prepare --config --local --baseline`（`cli.main` seam 6）
- Frontend/downstream consumers: viewer 读 `input/viewer/`（products-contract §2/§6）；`init`（11.1）读变体内同源率定末态；`run`（组 12–14）读变体
- Failure paths/rollback/stale state: 拒绝覆盖在任何写入之前；builder 失败/校验失败一律不提交任何变体；提交阶段失败回滚本次新建的父目录与 staging；`finally` 清 scratch 与 staging；**已接受残留**：四个终名的 rename 逐个原子，但四者之间没有跨名事务，进程在其间被 SIGKILL（或 NFS `ESTALE`）会留下部分提交的 `YD_ROOT`——在无跨目录事务的 POSIX 文件系统上不可消解。提交顺序钉死为「两变体 → rivers → boundary」，这是 **best-effort 的排序偏好，不是对 viewer 的就绪保证**：`products-contract` §2/§6 没有为 `input/viewer/` 定义任何就绪标记（不同于 `output/` 的 `DONE`，§4），本 issue 也不发明一个。就绪标记的取舍与崩溃后的人工恢复程序路由为 follow-up issue（见 Non-goals）
- Evidence/audit/readiness: `producer/tests/test_prepare.py` 的全树快照比对用例组；`prepare_fixtures.py` 的记录型假 builder 调用记录
- Regression rows:
  - 干净根 + 合法基线 -> 四个终名全部新建，scratch 清空，builder 恰 2 次且 source/grid 各异
  - 四个终名任一预先存在 -> 拒绝，`YD_ROOT` 全树逐字节不变，builder 0 次
  - `ifs` builder 抛异常（`gfs` 已建好）-> `YD_ROOT` 全树逐字节不变（部分成功不得提交），scratch 清空
  - `variants.gfs` 为绝对路径 / 逃逸路径 -> 拒绝，两处均无写入
  - `variants.gfs == variants.ifs` -> 任何写入之前拒绝，builder 0 次
  - 提交阶段首次 rename 失败 -> `YD_ROOT` 条目集合与执行前相同（含回滚本次新建的父目录/staging）
  - 每次终名 rename 的源与终名同 `st_dev` 且源在 `yd_root` 内 -> 恒真（生产 NFS/scratch 跨设备的判别式）
  - 未改动的同级消费者：`cli` 的 `init`/`run` 守卫与退出码用例、`geometry`/`cfg_ic`/`safe_fs` 既有用例 -> 全部继续通过

Boundary surfaces（high 强度必填）:
- 共享 helper 根：`store/safe_fs.*`（复用，MUST NOT 在 `prepare.py` 里另写一套 fs 原语；**唯一豁免**是 scratch -> staging 的树复制——`safe_fs` 公共面确无 copy 原语，故它按 Must add/change 只落在 `prepare.py` 内，不算越界）、`geometry.write_viewer_geojson`、`state/cfg_ic.parse`、`raw/source_identity.normalize_source_id`
- 公共入口：`cli.build_parser`/`cli.main`
- 读面：基线包、变体内率定末态 `cfg.ic`
- 写/删/覆盖面：`YD_ROOT/input/models/*`、`YD_ROOT/input/viewer/*`、`YD_ROOT` 内本次 staging、`scratch_root/<本次专属>`（删除面恰为后两者 + 提交失败时本次新建的父目录）
- staging/发布/回滚面：scratch 工作目录（builder 产出）-> `YD_ROOT` 内本次专属 staging（按发布权限新建）-> 四个终名的同盘 rename 提交；回滚面含本次新建的父目录与 staging
- 生产者/消费者证据边界：viewer 的 `input/viewer/` 契约（products-contract §2/§6）；11.1 消费变体内率定末态
- 陈旧态/幂等边界：重跑必须被拒绝覆盖挡住（prepare 不幂等、无 `--force`，compute-loop §6.1）
- 未改动的下游消费者：`init`/`run` 入口、`controller`、`rawscan`

Risk packs considered (core):
- Public API / CLI / script entry: selected - 新增 CLI 参数与新公开模块，11.1/12–14 的消费契约
- Config / project setup: selected - 新增必需 config 字段；`variants.*` 相对性 fail-closed 闸门
- File IO / path safety / overwrite: selected - 本 issue 的核心面
- Schema / columns / units / field names: selected - `VariantBuildRequest` 字段名对 NWM `build_direct_grid_variant` 的同名参数；`nwm_canonical_grid_id` 的 TOML 键名
- Auth / permissions / secrets: not selected - 无凭据；权限/属主归 #24/#25 的发布面
- Concurrency / shared state / ordering: selected - 提交顺序、scratch 目录唯一性、并发/重跑不得互相覆写
- Resource limits / large input / discovery: not selected - 一次性操作，3988 河段/7891 单元量级
- Legacy compatibility / examples: selected - 新增**必需** config 字段会打断全部既有 `Config` 构造点
- Error handling / rollback / partial outputs: selected - 部分成功不得提交、失败必清 scratch
- Release / packaging / dependency compatibility: not selected - 零新增依赖、lock 不变
- Documentation / migration notes: selected - `--baseline`、`nwm_canonical_grid_id`、变体路径相对性三处已先行改 docs 并 push（`3e9239d`、`37dae4a`）

Domain packs (from active profile):
- Geospatial / CRS / shapefile sidecars: selected - 消费 10.1/10.2 的几何链，落点归本 issue
- Time series / forcing / temporal boundaries: not selected - prepare 无 cycle/时间面
- 状态链 / warm-start 定戳一致性: selected - 变体内率定末态是状态链的**起点**，本 issue 决定它被不被提交；重戳属 11.1
- NWM 快照溯源与 DB-free 隔离: selected - prepare 是全仓唯一进入 NWM 活动环境的路径；生产 builder 绑定 MUST 不 import NWM、不起数据库连接、不隐式起子进程

Non-goals:
- **真实 NWM builder 调用**（issue 正文 Out of Scope，M4）：`build_direct_grid_variant` 的约 24 个关键字参数与 NWM 侧 driver 归 M4；本 issue 交付编排与 fail-closed 绑定，并以对 pin 的只读取证结清 `tasks.md` 组 8 记入 #32 的「`-m ...cli` 是否足够」待确认项
- 真实外部基线模型包的现场布局与读取（M4）；本 issue 的基线包内部布局是合成约定
- `init` 的首态建链与率定末态重戳（11.1）
- `nwm_canonical_grid_id` 的**生产取值**（归 #29 的生产实例复核，与 `reach_count=3988`、`checkpoint_hours=[12]` 同批）
- 变体内容的数值/映射正确性（mapping-builder 的职责，M4 oracle）
- 提交的属主/权限（uid/gid/mode 不继承 scratch）——归 #24/#25 的发布面
- 四个终名之间的 SIGKILL/`ESTALE` 窗口：是否给 `input/viewer/` 定义就绪标记（products-contract 契约变更，需先改文档），以及 `prepare` 半提交后的人工恢复程序（`prepare` 无 `--force` 且四名任一存在即拒绝，故半提交态目前无文档化出路）——已接受残留，路由为 follow-up issue **#78**

Review focus:
- 拒绝覆盖是否**在任何 scratch 写入与任何 builder 调用之前**，且覆盖**全部四个**终名而非只有两个变体目录
- 守卫路径与提交路径是否真的同源（改 `variants.*` 的判别性用例是否存在），还是各写了一遍字面量
- 部分成功是否会漏出：`ifs` 失败时 `gfs` 变体是否可能已被提交
- 失败与成功两条路径是否都清 scratch（`finally` 而非只在成功分支）
- `doc.river is None` 是否被当成 0 条 reach（`Section | None`，`reach_count` 恰为 0 的配置下会静默通过）
- 生产 builder 绑定是否在起子进程**之前**失败；有没有偷偷用 `-m ...cli` 冒充真实构建
- 是否复用 `safe_fs` 原语，还是在 `prepare.py` 里另写了一套 fs 操作
- "无新写入"的断言是全树快照还是单点探测
- 新增必需 config 字段后，既有 `Config` 构造点是否全部补齐且未被改成带默认值

## 11. init-bootstrap：首态建链

- [x] 11.1 实现 init 编排：非全新根拒绝守卫、7 天扫描窗定各源首轮（复用 raw-scan）、任一源窗内无完整 cycle 即整体拒绝不写状态（fail closed）、率定末态重戳写首态（复用 state-tools）

依赖：组 3（扫描）、组 4（重戳）
§13.1 归属：无直接行（测试归属见 change design D7）
Suggested fixture level: compact - 复用 raw 目录树与合成状态 fixture
Minimal mergeable slice: atomic - 单一编排函数，拒绝守卫/扫描窗/首态写入共享同一条 init 验证路径，无独立可交付子集

### Issue #21 fixture（任务 11.1）

Fixture level: expanded
Upstream suggested level: compact（override：正面命中 `openspec/project-profile.md` 的 domain expanded-triggers `cfg.ic`、重戳/restamp、`cycle`、`DONE`、状态链/warm start，以及核心强制触发词 `file output`、`writer`、`path`、`CLI`——本 issue 是全仓**第一处向 `YD_ROOT/states/` 落盘**的代码；profile 触发词按 `issue-risk-contract.md` 与核心触发词同为强制）
Repair intensity: high（首次写 NFS 发布根、部分产物即把系统**永久砖化**——「已有任一状态即拒绝」使一次半写死锁住所有后续 init；同时命中 profile 首位风险轴「断链即整链失效」的**链起点**。适用 `Invariant Matrix`）
Project profile: yd-viewer

**上游契约偏离（consumed not renegotiated，须回流 stage-change-pipeline sizing-retro）**：issue #21 的验收标准依赖「从两个变体内各自同源率定末态复制首态」（`specs/init-bootstrap/spec.md`、compute-loop §6.2 第 4 步），但**率定末态在变体目录内的落点，全仓无任何文档、spec 或配置钉死**：compute-loop §6.1 只说变体「水文参数和率定状态来自同一基线」，`config.toml` 的 `variants.*` 只到变体目录一级，`nwm-snapshot-inventory.md:132` 的 `_project_name` 只决定 tracker 轮询的 `<project>.cfg.ic.update` 文件名、且该 manifest 在 init 期不存在。该 seam 由本 issue 自行补齐（裁决 2），按核心规则「needed-but-missing seam is a reported deviation」记录在此，并**约束尚未落地的 #20 / 任务 10.3**（prepare 提交变体时必须满足裁决 2 的形态）。

**核心设计裁决（本 fixture 钉死，实现不得自行改写）**：

1. **落点与形态**：新增 `producer/src/yd_producer/init.py`（issue 正文逐字指定 `yd_producer.init`），`cli.init()` 由 `_unimplemented` 改为**薄委托**——保持 `cli.py` 既有的「入口只做守卫 + 委托」形态（见 `cli.py` 模块头与 `main` 的分派注释），业务判定与落盘一律在新模块。入口体 MUST NOT 自行解析 `YD_ROOT` 之外的路径。
   - `now` **MUST 可注入**（`bootstrap(*, local, config, now: datetime) -> InitReport`，`cli.init()` 传 `datetime.now(UTC)`）。7 天窗对「执行时刻」有语义依赖，不可注入即测试只能自证。
2. **率定末态定位判据（补齐的 seam）**：对每个 source，率定末态 = `Path(local.yd_root) / getattr(config.variants, source)` **顶层**（非递归）恰好一个后缀为 `.cfg.ic` 的**普通文件**。
   - 命中 0 个或 ≥2 个 → **整体拒绝、零写入**（fail closed）。
   - **不选**「按 `<变体目录名>.cfg.ic` 猜文件名」：项目名与目录名的等同关系全仓无任何权威来源，猜错的失败模式是「文件不存在」，与「prepare 未跑」不可区分；「恰好一个」把该歧义变成可断言的显式拒绝。
   - **不选**递归搜索：变体内 SHUD 运行期会产生 `cfg.ic.update` 等衍生物（compute-loop §9.2），递归会把它们卷进候选集；且 init 只在全新根执行，顶层一层足够。
   - **不选**配置新增 key：`cli-config` spec 的顶层 key 名逐字钉死（见本文件「顶层 key 名逐字钉死」一条），扩 schema 即拖入 #29/#32 面。
   - 目录不存在 / 不是目录 / 无法枚举 → 同样整体拒绝、零写入，且拒绝理由 MUST 逐条可区分（见裁决 6 的词表）。
   - **相对性闸门（round 3 cand-R3-03 CONFIRMED，本条为 fixture 遗漏的补正）**：`getattr(config.variants, source)` 的取值 MUST 先过一道闸门——`Path(v).is_absolute()` 为真，或其任一 `parts` 分量等于 `os.pardir`（`".."`），则判新增的 `VARIANT_PATH_INVALID` 并整体拒绝、零写入。上面那条 join 公式只对**通过闸门后**的取值成立。理由：不设闸门时绝对路径与 `..` 取值会让链起点读自 `YD_ROOT` 之外（实测：`variants.{ifs,gfs}` 置为仓外绝对路径时 `refusal=None` 且两份首态照写，calibration 解析到 `YD_ROOT` 外），而约束此前只活在 `docs/compute-loop-design.md` §6.2 的「相对 `yd_root`，不得为绝对路径」一句「相对 `yd_root`，不得为绝对路径」，`specs/init-bootstrap/spec.md`「率定末态定位」Requirement 里的「相对 `yd_root`」当时只是描述性括注而非 MUST-refuse（**round 3 起已改为逐字 MUST-refuse**）。姊妹消费方 `prepare._resolve_variant_relative`已强制同一对判据并有三条钉死用例——实现 MUST 抽公共 helper 复用，MUST NOT 复制第二份判据。
   - 该判据同步补进 `docs/compute-loop-design.md` §6.2 与 `specs/init-bootstrap/spec.md`（本 PR 内一并落，docs 与实现同 PR 不产生「文档滞后实现」）。
3. **扫描窗与首轮判据**：窗 = `[now - 7 天, now]`，**双端闭**。候选 cycle 由 `config.cycle.hours`（**不得硬编码 `[0, 12]`**）在窗内按 UTC 生成，**升序**取第一个 `rawscan.judge(...).complete is True` 的 cycle 作为该源首轮 T。
   - `now` MUST 归一为 UTC aware；naive `now` MUST 抛 `ConfigError`（MUST NOT 按宿主时区静默重释——`restamp._ensure_utc` 的同类缺口已由 issue #67 立案）。
   - 严格 `cycle <= now`：未来 cycle 不进候选集。
   - `rawscan.judge` 抛的 `ConfigError`（配置取值域 / 请求校验 / 模式校验）**MUST 原样上抛**，MUST NOT 被吞成「不完整」——那会把一个配置错误伪装成「等 raw 补齐」，让运维永远重跑 init。本文件已钉死「cycle 目录整体不存在**不是**错误」（见 `rawscan.judge` 的验收条目），故只有 `complete is False` 这一条走「继续找下一个候选」。
   - **`cycle.hours` 的取值域 MUST 在构造候选网格之前自查**（round 1 验证闸门 cand-01 CONFIRMED/FIX_NOW，两个子案例均实测）。全仓唯一的域校验 `rawscan._validate_config_domain` 位于 `judge` **体内**，而 `_candidate_cycles` 是本路径上 `config.cycle.hours` 的**第一个**消费者、跑在任何 `judge` 调用之前，于是有两个案例根本到不了域校验：
     - `hours = ()` -> 候选集为空 -> `judge` 一次都不调 -> 返回 `NO_COMPLETE_RAW_CYCLE`「等待 raw 补齐后重跑 init」，而 raw 其实是齐的。这**逐字**就是本裁决上一段禁止的伪装。
     - `hours` 含 `0..23` 之外的值（`24`/`25`/`-1`）-> `datetime(..., hour=...)` 抛**裸 `ValueError`**，不是 `ConfigError`，`cli.main` 的 `except ConfigError` 接不住，traceback 逃逸出 CLI，违反裁决 6「MUST NOT 以异常逃逸」。
     故 `bootstrap`（或 `_candidate_cycles` 头部）MUST 在枚举之前校验：`hours` 非空、且每个值都在 `0..23` 内，不满足即抛 `ConfigError` 并点名 `cycle.hours`。
     - **MUST NOT 在 `init` 内重新声明 `{0, 12}` 这个域**，也 MUST NOT 导入私有的 `rawscan._validate_config_domain`（`rawscan.py` 属 Must preserve 面）。理由：候选网格一旦非空且可构造，第一次 `judge` 调用就会施加 `{0, 12}`，`ConfigError` 原样上抛——实测 `hours=(13,)` 正是如此。本裁决只补上「域校验结构性不可达」的那两个洞，`rawscan` 仍是取值域的唯一权威。
     - 同一缺口的兄弟面：#26/#27 的 run 接线同样会在 `judge` 之前消费 `config.cycle.hours`。仅记录、不在本 issue 处理。
   - `local.nwm.raw_root` 是 `judge` 的 `raw_root` 入参。
4. **任一源无完整 cycle 即整体拒绝**（spec MUST，逐字 fail closed）：**所有** source 的首轮 T 全部确定之前，MUST NOT 发生任何写入。
5. **两阶段落盘，这是本 fixture 的中心不变量**：**判定与内存构造全部在前，落盘集中在最后**。
   - 阶段 A（零写入）：拒绝守卫 → 定位两份率定末态 → `state.parse` 两份文档 → 扫描窗定两个 T → `state.restamp_to_absolute_time` 得两份重戳文档 → `state.render` 得两份字节。任一步失败即返回拒绝，`states/` 与 `output/` 逐字节不变。
   - 阶段 B（唯一写入窗）：`store.safe_fs.ensure_directory_no_follow` 建 `states/<source>/`，`store.safe_fs.write_bytes_no_follow_exclusive` 写 `states/<source>/<T:%Y%m%d%H>.cfg.ic`。
   - **写用 exclusive 而非 `atomic_write_bytes_no_follow`**：后者按语义覆盖已有文件，与「只在全新根执行」直接冲突；`O_EXCL` 让守卫与写入之间的 TOCTOU 窗口 fail closed（`FileExistsError` → 拒绝，不覆盖）。
   - **阶段 B 的写入顺序钉死为 `rawscan.SOURCES` 的迭代序**（当前值 `("ifs", "gfs")`），MUST NOT 依赖 `dict`/`set` 的偶然序。理由：部分落盘的 `detail` 必须可预期，否则收尾报告在两次执行间不可复现。
   - **阶段 B 内失败的可观测后果 MUST 被钉死**：某个 source 写入失败时，其**前序已落盘**的 source 文件留在盘上（本函数 MUST NOT 回滚删除——删除面归 #23/#25，且 init 无权删它没确认过的东西），但 MUST 以非零退出码与明确理由报告，`detail` MUST 列出**全部前序已落盘 source 的路径**（而非硬编码某一个源）。这是**已接受的代价**而非缺陷：把它写成回滚会让 init 获得 `states/` 删除权，风险远大于收益。
   - **收尾话术 MUST 随 `written` 分支**（round 1 验证闸门 cand-08 CONFIRMED/FIX_NOW，实测：`chmod 0o500 states/` 使阶段 A 枚举全过、阶段 B 首个 `ensure_directory_no_follow` 抛 `EACCES`，得 `written == ()` 且 `states/` 事后为空，收尾却仍宣称「根已非全新，需人工清理」）：`written` **非空**时理由文本 MUST 指出「根已非全新，重跑 init 前需人工清理 `states/`」；`written` **为空且盘上零残留**时 MUST NOT 宣称需要清理，而 MUST 报「零写入，根仍是全新根」并把根因（被转述的 `SafeFilesystemError` / `OSError`）放在首位。判据 MUST 是「`written` 非空 **或** 存在可能半写的目标」而非单看 `written`：类二失败发生在**首个** source 时 `written` 为空，但盘上已有一份截断文件，照「只看 `written`」的字面读法会报「根仍是全新根」，而下一次 init 必然 `STATES_NOT_EMPTY`——两条话术直接矛盾。原裁决把该 MUST 写成无条件，是把一个 `written` 非空的语境套到了它从未设想的终态上。
     - **「零写入」的量纲是「零普通文件落盘」，不是「文件系统逐字节不变」**（round 2 修复轮 STEP 0 不变量清扫的审计条目 13'，登记在案、刻意不改）：`ensure_directory_no_follow` 建出 `states/<source>/` 之后目标 `open` 才失败时，盘上会留下一个**空目录**。这不构成话术缺陷：本模块的「全新根」判据由裁决 8 定义为**只数普通文件**，空目录既不触发 `STATES_NOT_EMPTY`、也不妨碍下一次 `init` 的 `ensure_directory_no_follow` 空操作成功，故重跑仍能成功——与该话术承诺的运维后果一致。对应的回归行（零残留的 open 期失败）断言的正是「`states/` 树下零**普通文件**」而非空树。
   - **阶段 B 的失败构造分两类，MUST NOT 再写「唯一可达构造」**（round 1 验证闸门 cand-07 CONFIRMED/FIX_NOW，实测证伪）：
     - **类一（`EEXIST`，用例的钉死构造）：目标路径预置为一个空目录**（`states/gfs/<T_gfs>.cfg.ic/`）。这是裁决 8 的守卫（只认**普通文件**）与 `_FILE_FLAGS` 的 `O_CREAT|O_EXCL`（对任何已存在的条目都得 `EEXIST`）之间的可达缝：预置**普通文件**会在阶段 A 就命中 `STATES_NOT_EMPTY`、永远走不到阶段 B，用它构造出的用例是假绿。该缝隙（非普通文件条目过得了 bootstrap 守卫、却挡得住写入）是**已知且刻意**的：把守卫扩到「任何条目」会让 `states/` 下一个 `.DS_Store` 目录永久砖化建链，方向与裁决 8 的「宁可要求人工确认」相反。
     - **类二（写循环中途的 I/O 失败）：目标路径不存在，`O_EXCL` 成功创建后 `os.write` 中途抛 `ENOSPC`/`EDQUOT`/`EIO`**（NFS 发布根上最现实的失败类）。`safe_fs.write_bytes_no_follow_exclusive` 的 `except OSError` 臂只 `_close_file_fd` 后转抛，**不 unlink**（与同模块 `atomic_write_bytes_no_follow` 的失败路径不对称），故盘上留下一份 **header 合法、body 截断**的普通文件。实测（`RLIMIT_FSIZE=4096` + 忽略 `SIGXFSZ`）：抛 `SafeFilesystemError … [Errno 27] File too large`，目标 `exists: True size: 4096`，首行是完整 header。因此 `init.py` MUST 在 `detail` 里点名 `{target}` **可能已被部分写入、重跑前须一并人工确认**——该文件既不在 `written` 里、也不算「前序已落盘」，运维照当前话术清理会漏掉它。
       **判据 MUST 是「盘上是否真的留下了条目」，MUST NOT 用 `SafeFilesystemError.kind` 当代理**（round 2 验证闸门 cand-R2-01/-02 CONFIRMED/FIX_NOW，实测证伪）：`kind == "io"` 与「inode 已被创建」不等价。`safe_fs.write_bytes_no_follow_exclusive` 的 `except OSError` 臂覆盖了整个写入体，**含 `os.open(..., O_CREAT|O_EXCL, ...)` 本身**；父目录分量走查（`_open_parent_dir`，在该 `try` 之外）另从自身站点抛出，`kind` 可为 `"io"` 或 `"unsafe"`。于是 open 期的 `EACCES`/`EROFS`/`ENOSPC`/`ESTALE`（**盘上零残留**）与真正的写中途 `ENOSPC` 拿到同一个 `kind`。实测（预置 `states/ifs/` 为 `0o500` 空目录，其余为全新有效根，非 root）：`refusal = write_failed`、`written == ()`、`states/` 下零普通文件，`detail` 却同时宣称「已被排他创建但写入中途失败，可能已被部分写入」与「根已非全新，重跑 init 前需人工清理 `states/`」——两句话都与盘上真实终态相反。
       故捕获写入腿的异常后 MUST 用 no-follow 的 `os.lstat(target)` **直接探测目标**：条目存在 -> `possibly_partial = True`；`FileNotFoundError` -> `False`（此时零残留话术是**精确**的，不是保守近似——`0o500` 构造里父目录仍带 `x`，实测 lstat 干净地拿到 `FileNotFoundError`）；`lstat` 自身失败 -> fail closed 到 `True` 但话术须 hedge。`FileExistsError` 仍走自己的分支且**先于**该臂捕获，故预置条目不会被误认成自己的半写产物。该修复完全落在 `init.py` 的 catch 之后，**不需要改 `store/safe_fs.py`**（Must preserve 面保持不动）。
       **无条件的「已被排他创建」措辞 MUST 移出**：它只在探测到条目时成立。
     - **MUST NOT 用 monkeypatch 伪造写入失败**来替代类一构造——那会让「阶段 B 真的用了 `O_EXCL`」这条判据退化为永真式。该禁令只针对**伪造写入结果**；在 `ensure_directory_no_follow` 这个**另一个** seam 上做真实副作用（例如建目录时顺带植入一个真实的普通文件，让真实的 `O_EXCL` 自然失败）不在禁令内，且是裁决 5 的 `O_EXCL` 选择唯一的判别构造（见新增回归行）。
     - **不在本 issue 修的两条**（均落在 Must preserve 面，已路由）：`write_bytes_no_follow_exclusive` 的失败路径缺 unlink（`store/safe_fs.py`）；`controller._classify_state` 只读 header 行故接受截断状态（`controller.py`，#22 面）。
   - 以上分类 MUST 写进模块头，且模块头 MUST NOT 再出现「唯一可达构造」的说法。
   - **裁决 5-bis：收尾三路的判据是「阻塞物是否为持久外来条目」**（round 4 R4-A CONFIRMED/P2/FIX_NOW，层位=fixture/spec 层，实现只是忠实执行了写错的谓词）：第二路（零写入，根仍是全新根）与第三路（点名外来条目）的分流 MUST NOT 由「哪条腿抛的异常」或「条目是否恰好落在终名 `target` 上」决定。外来条目的两种载体——占住终名 `target`（排他创建撞已存在条目）与占住父目录分量 `states/<source>`（symlink/FIFO/普通文件 -> `ensure_directory_no_follow` 抛 `NotADirectoryError`/`ELOOP`，见 `safe_fs.ensure_directory_no_follow`：它对每个 part 的 `os.open` 带自己的内联 `NotADirectoryError`/`ELOOP` 臂，各自被包成 `SafeFilesystemError`（兄弟站点 `_open_child_dir` 的错误文本逐字相同，但它只在 `FileNotFoundError -> mkdir` 后的重试路径上被调用，不是本载体实际触发的站点））——运维后果逐字节相同（重跑复现同一失败，必须先移除该条目），MUST 走同一路。实现侧：ensure 腿捕获后 MUST 用 no-follow 的 `os.lstat` **逐级盘上探测**——**自 `yd_root` 起走查写入路径的每一级分量**，返回第一个非目录分量（round 5 R5-G：`ensure_directory_no_follow` 逐 `part` 做 `O_DIRECTORY|O_NOFOLLOW` 的 open、可在任一分量失败，只探末分量比失败面窄一层；且 `os.lstat` 对中间分量**跟随** symlink，`states/` 自身被占住时对 `states/<source>` 的 lstat 会穿过去拿到 `FileNotFoundError` 或 `ENOTDIR`，两者都被误判成「无外来条目」。仅把 `ENOTDIR`/`ELOOP` 翻成 `True` 不够：symlink 载体上该翻译根本不触发，FIFO 载体上第三路会点名一个盘上并不存在的 `states/<source>`）（沿用本模块「MUST NOT 用 `SafeFilesystemError.kind` 当代理」的既定规则）——探到非目录条目 -> 第三路；`FileNotFoundError` 或探到真实目录（权限类，如 `states/` 置 `0o500`/`0o600` 使父目录 open 拿 `EACCES`）-> 仍走第二路，`chmod` 后直接重跑即可成功。**第三路话术插值的路径 MUST 是被占住的那个路径本身**：ensure 腿是 `target_dir`，排他创建腿是 `target`——直接复用 `target` 会让「点名该条目路径」的承诺在下一层再次为假。不新增第四条腿。
   - **判据的措辞 MUST 在全部 prose 面上一致**（round 2 验证闸门 cand-R2-03 CONFIRMED/FIX_NOW）：`init.py` 模块头的失败分类段与收尾话术段、`docs/compute-loop-design.md` §6.2，MUST 全部写成上面的探测式判据。**（round 2 现状记录，已由后续修复关闭：两处当时都留着被本裁决废弃的「只看 `written`」读法，而按 `CLAUDE.md`「实现与文档冲突时以文档为准」会把维护者指引到 `if written:` 这个改动上——该变异当时全套静默通过，把 cand-08 的缺陷装回去。）**`specs/init-bootstrap/spec.md` 的两条 Scenario 曾被本裁决判为「已正确限定、不需要改」——**round 4（R4-A）证伪了这句**：那两条 Scenario 的 WHEN 把判据写成「失败发生在目标文件被创建之前」与「条目在**目标路径**上」，于是外来条目占住**父目录分量** `states/<source>`（symlink/FIFO/普通文件，ensure 腿拿 `NotADirectoryError`/`ELOOP`）时逐字满足零残留腿的 WHEN，却输出一条被实测证伪的运维指令。判据 MUST 改为「**阻塞物是否为持久外来条目**」，见下方裁决 5-bis。教训记在此处不删：fixture 对 spec 现状作出的「不需要改」这类断言与任何其他绑定断言同级，MUST 有机械核对，否则就是为一个错误谓词背书。
6. **拒绝理由闭合词表**（`InitRefusal` 枚举，逐项可区分，MUST NOT 以异常逃逸）：round 3 起新增 `VARIANT_PATH_INVALID`（见裁决 2 的相对性闸门）。**词表的唯一真源是 `producer/src/yd_producer/init.py` 的 `InitRefusal` 枚举体本身**；条数只允许在紧贴枚举成员处陈述一次，`tasks.md`、`spec.md`、模块头、PR 正文等远端面 MUST 引用而 MUST NOT 复述条数——复述即制造第二真源，而本仓没有任何机制让两份真源保持一致（round 4 batch-3 裁决；同形先例见本文件禁区词表一行的「刻意不复述」处理）。词表的**闭合性**与**逐项可区分**仍是硬约束，不因删除条数而放宽。
   - `STATES_NOT_EMPTY`：`states/` 树下存在任一普通文件
   - `DONE_PRESENT`：`output/` 树下存在任一名为 `DONE` 的普通文件
   - `VARIANT_MISSING`：变体目录不存在 / 不是目录
   - `VARIANT_PATH_INVALID`：`config.variants.<source>` 是绝对路径或含 `..` 分量（裁决 2 的相对性闸门；`detail` 带 source 与原始取值）
   - `CALIBRATION_STATE_AMBIGUOUS`：变体顶层 `*.cfg.ic` 命中数 ≠ 1（`detail` 带命中数与路径）
   - `CALIBRATION_STATE_UNREADABLE`：率定末态存在但 `state.parse` 抛 `ValueError`（含超界、非 UTF-8、结构不可用）
   - `HEADER_SHAPE_INVALID`：`restamp_to_absolute_time` 抛 `ValueError`（header 数值 token 数不为 3/4）
   - `NO_COMPLETE_RAW_CYCLE`：某源窗内无完整 cycle（`detail` 带 source 与窗口端点）
   - `DISCOVERY_UNREADABLE`：任一文件系统探测失败（见下条）
   - `WRITE_FAILED`：阶段 B 失败（`detail` MUST 指明已落盘的 source 集合）
7. **枚举/探测失败 MUST 与「不存在」分流，且 MUST NOT fail-open**（沿用 issue #22 裁决 9 的同一规则，本 issue 是同一风险面的写侧）：**只有** `FileNotFoundError` / `NotADirectoryError` 等价于「空集合」；其余任何 `OSError`（`EACCES`/`EPERM`/`EIO`/`ESTALE`/`ELOOP`…）MUST 判 `DISCOVERY_UNREADABLE` 并整体拒绝。**分层切分（镜像 #22 裁决 9）**：`DISCOVERY_UNREADABLE` 专指**集合无法枚举 / 条目无法判定**这一层；率定末态**本身**被定位成功后的读失败（含 mode 000 的 `EACCES`）由 `state.parse` 收敛为 `ValueError`，一律归 `CALIBRATION_STATE_UNREADABLE`。方向性理由与 #22 相反且更严：这里判空即**放行写入**，`states/` 因权限不可枚举时若按「空」处理，就会往一个可能已有状态的根上写首态——直接断链。适用于每一处探测：`states/` 与 `output/` 的树遍历、变体目录列举、率定末态的普通文件判定（MUST NOT 用裸 `Path.exists()`/`is_file()`——`pathlib` 只吞 `ENOENT/ENOTDIR/EBADF/ELOOP/EINVAL`，`EACCES`/`EIO` 会穿透）。
8. **`DONE` 与状态的可见性判据取「宽」，与 #22 的前沿可见集**刻意不同**：#22 的前沿对不可解析条目判「不可见」是为了不让一次崩溃的发布永久砖化该源；本 issue 是**唯一的 bootstrap 闸门**，方向相反——`states/` 下**任一**普通文件（含不合命名规则的残留）都算「已有状态」而拒绝，`output/` 树下**任一**名为 `DONE` 的普通文件都算已有产物而拒绝。这张力已知且刻意：init 只在系统历史第一次执行，宁可要求人工确认，也不能在一个有残留的根上重新建链。该差异 MUST 写进模块头。
9. **MUST NOT 运行 SHUD、MUST NOT 写任何 `DONE`、MUST NOT 触碰 `output/`**（spec 逐字）。这是可断言的负面证据：用例 MUST 断言 `output/` 树在 init 前后逐字节不变，且不存在任何 subprocess 调用面。
10. **不做 QC / 不做负残差归零**：`state_qc.run_state_variable_qc` 与 `normalize_negative_residuals` 在 init 期**不调用**。理由：率定末态是 prepare 提交的、已被 #20 校验过的基线产物，init 只做「复制 + 重戳」（compute-loop §6.2 第 4 步逐字）；把 QC 塞进 init 会让一份合法基线因阈值判定被拒而无法建链，且 §8 的负残差归零逐字属**运行期**语义。记为显式 non-goal，不是缺口。
11. **单源建链 / 事后补链入口 MUST NOT 提供**（compute-loop §6.2 逐字「不提供单源建链或事后补链入口」）：不加 `--source`、`--force`、`--from` 一类参数；`build_parser()` 的 `init` 子命令参数集不变。

Change surface:
- 新增 `producer/src/yd_producer/init.py`：`InitRefusal` 枚举、`InitReport`（frozen dataclass）、`bootstrap(*, local, config, now)`
- 修改 `producer/src/yd_producer/cli.py`：`init()` 由 `_unimplemented` 改为薄委托（拒绝 → `_fail` 语义的非零退出；成功 → `0` 并打印两条落盘路径）
- 新增测试面（受仓库 `large-file-guard` 的 1000 行闸门约束，按 fixture/主题分文件，逐一列举，**不复述文件数**）：`producer/tests/init_bootstrap_fixtures.py`（共享锚点常量、`Tree` 构造器、`snapshot`/`all_files`/`unreadable`/`skip_if_root`/`assert_zero_write`）、`test_init_bootstrap.py`（阶段 A 拒绝守卫与率定末态定位）、`test_init_scan_window.py`（扫描窗语义与 `now` 归一）、`test_init_write_phase.py`（阶段 B、负面证据与端到端）、`test_init_write_failure_wording.py`（round 5 补列，`[桶 C-3]` 父目录腿与 `[桶 C-7]` 单元级钉死的宿主——它承载两条强制 MUST 的唯一 oracle，漏列会让整文件在 boundary 检查里被跳过）
- 修改 `producer/tests/test_cli.py`：`test_init_reaches_staged_unimplemented`(:356-358) 与 `:199` 的 `init` 退出码断言随裁决 1 失效，MUST 改写为「`init` 经守卫后进入真实业务体」的等价正控制（保留该用例的原意：守卫全过后 `init` 不在入口层被拦），MUST NOT 删除了事。`prepare` / `run` 的同类断言不得改动
- 修改 `docs/compute-loop-design.md` §6.2：补「率定末态在变体内的定位判据」与「阶段 B 部分落盘的收尾语义」两句
- 修改 `openspec/changes/m2-producer-core/specs/init-bootstrap/spec.md`：新增「率定末态定位」Requirement 与「部分落盘可观测收尾」Scenario

Must preserve:
- `producer/src/yd_producer/state/**`、`rawscan.py`、`store/safe_fs.py` MUST NOT 被修改（#8/#9/#22/#6/#5 已审面）；本 issue 只**消费**其公开签名
- `cli.py` 的 `prepare` / `run` 分支、`_check_states_dir`、退出码常量与 `build_parser()` 的参数集不变（#3 已审面）
- stdlib-only；不新增依赖，`producer/uv.lock` 不变
- 零运行时 NWM import、零数据库/scheduler 依赖

Must add/change:
- `init.py`：
  - `InitRefusal(StrEnum)`：裁决 6 的闭合词表
  - `InitReport`（frozen dataclass）：`written: tuple[Path, ...]`、`refusal: InitRefusal | None`、`detail: str`；`written` 非空与 `refusal` 非 `None` **可同时成立**（`WRITE_FAILED` 的部分落盘，裁决 5）——这是与 #22 的 `FrontierDecision` 「恰有一个」不同的地方，MUST 在 docstring 写明
  - `bootstrap(*, local: LocalConfig, config: Config, now: datetime) -> InitReport`
  - 判定顺序固定：`states/`/`output/` 拒绝守卫 → 逐源定位并解析率定末态 → 逐源扫描窗定 T → 逐源重戳 → 集中落盘
  - source 词表取 `rawscan.SOURCES`，MUST NOT 再定义一份
  - cycle 目录名格式取 `rawscan.CYCLE_DIR_FORMAT`，MUST NOT 再写一份 `"%Y%m%d%H"`
- `cli.py`：`init()` 薄委托，拒绝时 `stderr` 打印 `refusal` 与 `detail`、返回 `EXIT_GUARD`；成功打印两条落盘路径、返回 `0`

Seams under test:
- `init.bootstrap(...)`：tmp 目录树（`YD_ROOT` 的 `input/models/yd_{gfs,ifs}`、`states/`、`output/` + 合成 raw 树）+ 注入 `now` → `InitReport` + 盘上可断言的产物集
- `cli.main([...])`：经真实 `bootstrap` 走通一次成功与一次拒绝，断言退出码与 stderr 文本；MUST NOT 用 fake 替换 `bootstrap`（否则 spec 的 MUST 无用例把守）

Invariant Matrix
Governing invariant: `init` 要么让 `YD_ROOT` 从「全新根」转到「每个 source 恰有一份重戳到其首轮 T 的首态」，要么**一个字节都不写**；除阶段 B 内的写入失败外，不存在第三种终态，且任何情况下 `output/` 与已有 `states/` 内容都不被修改或删除。
Source-of-truth identity/contract: `states/<source>/<T:%Y%m%d%H>.cfg.ic` 的**文件名 T** 与其 header minute token（`T.timestamp()/60`）**必须互相对应**；T 本身由「窗内最早完整 raw cycle」唯一确定。
Surfaces:
- Producers: `init.bootstrap` 阶段 B 的两次 `write_bytes_no_follow_exclusive`
- Validators/preflight: `init.bootstrap` 阶段 A 的拒绝守卫、率定末态定位、`state.parse`、`restamp_to_absolute_time` 的 shape 门
- Storage/cache/query: `YD_ROOT/states/<source>/`（唯一写入面）；`YD_ROOT/input/models/yd_<source>/`（只读）；NWM `raw_root`（只读，经 `rawscan.judge`）
- Public routes/entrypoints: `cli.init()` / `yd-producer init`
- Frontend/downstream consumers: `controller.decide_frontier`（#22，读 `states/<source>/<T>.cfg.ic` 并以**绝对**时间头判 T）；`run` 的 `_check_states_dir`（#3）
- Failure paths/rollback/stale state: 阶段 A 的各类拒绝（零写入）；阶段 B 部分落盘的 `WRITE_FAILED` 收尾（不回滚、报告已落盘集合）
- Evidence/audit/readiness: `InitReport.written` / `refusal` / `detail`；`cli` 的 stderr 文本与退出码
Regression rows:
- 全新根 + 两源窗内各有完整 cycle（且最早完整 cycle 不同）-> 两个文件落盘，各自文件名 T = 该源窗内最早完整 cycle，header minute token == `round(T.timestamp()/60)`，`output/` 树逐字节不变
- `states/gfs/2026082700.cfg.ic` 已存在 -> `STATES_NOT_EMPTY`，`states/`/`output/` 逐字节不变（含 mtime 不变的可断言证据）
- `output/<cycle>/gfs/DONE` 已存在 -> `DONE_PRESENT`，零写入
- 窗内 IFS 无任何完整 cycle、GFS 有 -> `NO_COMPLETE_RAW_CYCLE`，`states/` 下**无任何文件**（不是「只写了 gfs」）
- 变体目录缺失 / 顶层 0 个 `.cfg.ic` / 顶层 2 个 `.cfg.ic` -> `VARIANT_MISSING` / `CALIBRATION_STATE_AMBIGUOUS`（可区分），零写入
- 率定末态 header 只有 2 个数值 token -> `HEADER_SHAPE_INVALID`，零写入（`restamp` 的 shape 门 MUST NOT 被绕过）
- `chmod 0o000 states/` -> `DISCOVERY_UNREADABLE`，**MUST NOT** 判空后放行写入
- 变体目录不可枚举（`chmod 0o000 input/models/yd_gfs`）-> `DISCOVERY_UNREADABLE`（**不是** `VARIANT_MISSING`、**不是** `CALIBRATION_STATE_AMBIGUOUS`），零写入
- `output/` 树不可枚举（`chmod 0o000 output/<cycle>`）-> `DISCOVERY_UNREADABLE`，零写入（MUST NOT 判空后放行）
- 率定末态存在但 `state.parse` 抛 `ValueError`（截断 / 非 UTF-8 / 超 `MAX_STATE_IC_BYTES`）-> `CALIBRATION_STATE_UNREADABLE`（与 `HEADER_SHAPE_INVALID`、`CALIBRATION_STATE_AMBIGUOUS` 逐项可区分），零写入
- 配置使 `judge` 抛 `ConfigError`（如 `raw.gfs.bundles` 的模式渲染出重名）-> `ConfigError` **原样上抛**，MUST NOT 被收敛成 `NO_COMPLETE_RAW_CYCLE`；`cli.main` 转成退出码 `1`，零写入
- naive（无 tzinfo）的 `now` -> `ConfigError`，零写入；MUST NOT 按宿主时区静默重释
- 扫描窗下端点闭：唯一的完整 cycle 恰好落在 `now - 7 天` 这一时刻 -> **被接受**为首轮 T；同一 fixture 把该 cycle 整体前移**一个 cycle 步长**（12 小时）-> `NO_COMPLETE_RAW_CYCLE`。位移 MUST 是整 cycle 步长而非 1 小时——挪 1 小时会被 `config.cycle.hours` 过滤器排除，钉不住窗下界，一个回扫 30 天的实现能在那种构造下全绿。**本行的 fixture MUST 用 `cycle.hours = (0, 12)`**（与「非默认配置取值」行的 `hours=[12]` 分开）：`hours=[12]` 下前移 12 小时会落到 00Z、同样被候选网格过滤器排除，该行会丧失全部判别力
- **未来 cycle 排除（构造 MUST 用 off-grid 的 `now`）**：`now = 2026-08-27T06:00Z`、`hours=(0, 12)`，窗内唯一的完整 cycle 落在**同一天**的 `12Z`（严格晚于 `now`，但**在被枚举的日期上**）-> 判 `NO_COMPLETE_RAW_CYCLE`，零写入。**位于 `now` 之后的另一天不算数**：`_candidate_cycles` 的 `span = (now.date() - start_date).days` 已经把更晚的日期整个排除，用 `now + 12 小时` 跨天构造出的用例约束的是日期网格上界、而不是 `cycle <= now` 这个比较（round 1 验证闸门 cand-02 CONFIRMED/FIX_NOW，实测把 `<= now` 整条删掉后全套仍全绿）
- **未来 cycle 不夺首轮**（round 4 R4-D 后重构，`[桶 C-6]`）：本行覆盖 `producer/tests/test_init_scan_window.py` 的 `test_future_cycle_does_not_win_the_first_frontier` 与 `test_same_day_future_cycle_does_not_win_over_an_earlier_one` **两个用例**（原 Matrix 只写了一条构造，另一条长期无主，是 R4-D 得以在桶 B 里潜伏的一半原因）。两个用例各自 MUST 在窗内铺**两个及以上**完整 cycle，使 complete 候选集非单元素——否则「升序取第一个」与「取窗内最晚」恒等，本行对被覆盖分支零判别力。构造：off-grid `now` 下另在 `2026-08-26T12:00Z` 补一个完整 cycle -> T 取 `2026-08-26T12:00Z`，MUST NOT 取同日更晚的 `12Z`
- **扫描窗上端点闭**：`now` 取某个候选网格点（如 `2026-08-27T12:00Z`），窗内唯一的完整 cycle **恰好等于 `now`** -> **被接受**为首轮 T。本行独立钉死上端点的闭合性：把判据改成 `window_start <= cycle < now` 时全套仍全绿（同上实测），故没有这一行就没有任何用例区分闭/开
- **`O_EXCL` 而非覆盖写**：在 `ensure_directory_no_follow` 这个 seam 上包一层——建 `states/gfs/` 时**真实建目录**并顺带在 gfs 目标路径植入一个带哨兵字节的**真实普通文件**（绕过阶段 A 是因为该文件在阶段 A 之后才出现，正是裁决 5 要 fail closed 的 TOCTOU 窗）-> `WRITE_FAILED`，且哨兵字节**逐字节不变**。本行是裁决 5「写用 exclusive 而非 `atomic_write_bytes_no_follow`」唯一的判别构造：把调用换成 `atomic_write_bytes_no_follow` 后全套仍全绿（cand-03 CONFIRMED/FIX_NOW），因为既有的空目录构造对两个 helper **都**失败（`os.replace` 到目录同样报错）
- **`WRITE_FAILED` 且零落盘的收尾话术（round 3 cand-R3-01 CONFIRMED，本行为 CORRECTION）**：写入序首位（`ifs`）的目标预置为空目录 -> `WRITE_FAILED`、`written == ()`、`detail` 含「（无）」，且 **MUST NOT** 出现「需人工清理 `states/`」；同时 `states/gfs/` 从未被创建。本行同时钉死 `detail` 的「列出全部前序已落盘 source」不是硬编码某一个源——既有构造只堵第二个源，`written` 恒为单元素，把 join 换成 `written[0]` 全套仍全绿（cand-04 CONFIRMED/FIX_NOW）。**本行原先强制的「零写入，根仍是全新根」在这条腿上被撤销**：该腿走的是 `FileExistsError` 分支，盘上确有一个**外来**条目占住目标，实测 run 1 与 run 2 的 `detail` 逐字节相同——「根仍是全新根」承诺的运维后果（直接重跑）在这里为假，而同一句话术在真零残留腿上为真。故 `_write_failed` 的收尾 MUST 改为**三路**：(a) `written` 非空或探到残留 -> 「需人工清理 `states/`」；(b) 零残留且目标处**无**条目（open 期失败）-> 「零写入，根仍是全新根」；(c) 零残留但**写入路径上有持久外来条目**（终名 target 被占 -> 排他创建撞 `FileExistsError`；父目录分量 `states/<source>` 被占 -> ensure 腿抛 `NotADirectoryError`/`ELOOP`。**两种载体同路**，见裁决 5-bis）-> 第三路话术，点名**被占住的那个路径本身**：点名该条目路径、声明它**不是**本次写入产生（故不建议删除 `states/` 全树）、并明确重跑前须先确认并移除该条目，MUST NOT 出现「根仍是全新根」、亦 MUST NOT 出现「可能已被部分写入」。**授权改动的既有断言**：`producer/tests/test_init_write_phase.py` 中断言 `FRESH_CLAIM in report.detail` 的那一条（该行现自带「授权更正」注释，按注释 grep 定位，不按行号）期望改为第三路话术——这是本轮**唯一**被授权翻转的既有钉死断言。判别变异体：把第三路合并回 (b) -> 本行必红。
- **`DONE` 名字收窄的负面证据**：成功路径的 fixture 在 `output/<cycle>/gfs/` 下额外放一个**非 `DONE`** 的普通文件（如 `yd.rivqdown.dat`）-> init **仍然成功**。本行钉死裁决 8 刻意画出的两侧不对称（`states/` 侧认任一普通文件，`output/` 侧只认名为 `DONE` 的）：把 `name=DONE_NAME` 删掉后全套仍全绿（cand-05 CONFIRMED/FIX_NOW），而放宽后的守卫会让带任何残留的全新根永久无法建链，与「无 DONE 残留必须可干净重跑」直接冲突
- **stat 层 fail-closed（非仅列目录层）**：把目录置为 `0o444`（**可列目录、子项 `lstat` 抛 `EACCES`**，darwin 实测成立）-> `DISCOVERY_UNREADABLE`，MUST NOT 判空后放行写入。既有三条 `chmod 0o000` 用例都让**目录本身**不可列，只覆盖 `_entry_names`；把 `_entry_kind` 与 `_is_directory` 的 `OSError` 全部吞掉后全套仍全绿（cand-06 CONFIRMED/FIX_NOW），而 stat 层正是「判空即放行写入」防线的最后一层
- **写入中途 I/O 失败的收尾**（裁决 5 类二）：在 `RLIMIT_FSIZE` 之类的真实约束下让 `os.write` 中途失败 -> `WRITE_FAILED`，`detail` MUST 点名该目标**可能已被部分写入**、须一并人工确认；该目标既不在 `written` 内、也不在「已落盘的首态」列表内
- **零残留的 open 期失败 MUST 报全新根**（round 2 cand-R2-01 CONFIRMED/FIX_NOW，新谓词的正向钉死）：把 `states/ifs/` 预置为 `0o500` 的**空目录**、其余为全新有效根 -> 阶段 A 全过（守卫只数普通文件）、`ensure_directory_no_follow` 空操作成功、`O_EXCL` 的 `os.open` 拿 `EACCES` -> `WRITE_FAILED`；`detail` MUST 含「零写入，根仍是全新根」，MUST NOT 含「可能已被部分写入」或「需人工清理 `states/`」，且 MUST 断言 `states/` 树下零普通文件。本行必红于「用 `kind` 当代理」的实现——实测该实现在此构造下两句话都反着说。
- **首位 source 的写中途失败 MUST 报需清理**（round 2 cand-R2-04 CONFIRMED/FIX_NOW，`or possibly_partial` 析取项唯一的判别构造）：`payloads={"ifs": 超限载荷, "gfs": 默认载荷}` 使**写入序首位**在 `RLIMIT_FSIZE=4096` 下中途失败 -> `written == ()` 但盘上留一份截断文件；`detail` MUST 同时含「可能已被部分写入」与「需人工清理 `states/`」，MUST NOT 含「零写入，根仍是全新根」，且 MUST 断言 `0 < 目标.st_size < len(载荷)`。本行必红：把判据削成 `if written:` 后全套静默全绿（实测），只有本行能杀。
- **新谓词的反向钉死（lstat 自身失败 -> fail closed）**（round 2 cand-R2-07 CONFIRMED/FIX_NOW，防止单向缺口随修复平移）：`kind` 谓词被探测取代后，MUST 有一行让 catch 后的 `lstat(target)` 自身抛非 `FileNotFoundError` 的 `OSError`，断言落到 `possibly_partial=True` 的 **hedge 话术**（不得出现无条件的「已被排他创建」）。理由：旧谓词只在 `kind=="io"` 一个方向被钉住——实测把它松成 `isinstance(error, SafeFilesystemError)` 后该变异存活。
- **`_entry_kind` 的 `os.stat` FOLLOW 臂 MUST fail closed**（round 2 cand-R2-05 CONFIRMED/FIX_NOW，裁决 7 的「stat 层 fail-closed」行此前只钉住了 `os.lstat` 臂）：`states/<source>/<T>.cfg.ic` 是指向 `vault/prior.cfg.ic` 的 symlink、`vault` 置 `0o000`（`lstat` 成功——它是 symlink 不是目录；`os.stat` 得 `EACCES`）-> `DISCOVERY_UNREADABLE`、`written == ()`、`states/` 逐字节不变。本行必红：把该臂的两条 except 收成 `except OSError: return (False, False)` 后该变异存活，且实测退化为 `refusal is None` 并**往一个已持有可达前态的根上写了两份首态**——正是裁决 7 禁止的断链。载体用 symlink 只是本地差分手段，`ESTALE`/`EIO` 在 NFS 发布根上无需任何 symlink 即可到达同一臂。兄弟面：`_locate_calibration_state` 同样经 `_entry_kind`，MUST 一并补一行同构用例。**与 round 1 cand-10（DEFER，已立案 #96）不冲突**：cand-10 是 symlink→**目录**、要求**改行为**且布局在输入域外；本行是 symlink→**普通文件**（模块 docstring 自己声明「守卫取宽」在契约内），只给一条**已存在且正确**的守卫补证据，实现零改动。
- **`decide_frontier` 对 4-token 兼容 header 的接受**（round 2 rider-A，覆盖缺口）：`test_decide_frontier_accepts_the_state_init_writes` MUST 参数化到 `{默认载荷, 4-token 兼容载荷}` 两条。当前 4-token 只在写入侧被验，从未喂给 `decide_frontier`。无既有缺陷，纯补覆盖。
- **[桶 C-1] ENSURE 腿的探针不可达 MUST 报全新根**（round 3 cand-R3-04 CONFIRMED/FIX_NOW，阶段 B 两段 `try` 拆分唯一未被钉住的腿）：`states/` 置 `0o600`（可读不可执行）、其余为合法全新根 -> `ensure_directory_no_follow` 在**父目录 open** 上拿 `EACCES`，`WRITE_FAILED`、`written == ()`、`detail` MUST 含「零写入，根仍是全新根」，MUST NOT 含「可能已被部分写入」或「需人工清理 `states/`」，且 MUST 断言 `states/` 树下零普通文件。**与既有两行的差异必须理解为构造差异而非重复**：既有 `0o500` 行（`r-x`）下探针仍拿得到 `x`、`states/ifs` 从未创建，故得干净的 `FileNotFoundError`，按构造即对本变异不敏感；既有 `0o600` 行植的是 `states/ifs/`（不是 `states/`），`ensure` 在既存目录上成功，只走**写**腿。判别变异体（M5）：把 `ensure_directory_no_follow` 并回 `write_bytes_no_follow_exclusive` 的同一个 `try` -> 本行必红（实测该变异当时存活）。darwin 暴露同既有 `0o600` 行，Linux 等价性属推理未执行。
- **[桶 C-1] 零残留失败后 MUST 可直接重跑成功**（round 3 cand-R3-06 CONFIRMED/FIX_NOW，把代理量换成承诺本身）：在「零残留的 open 期失败」行末追加——恢复权限后**再次调用 `bootstrap`**，MUST 断言 `refusal is None` 且两个源的首态均落盘。理由：`detail` 里的「零写入，根仍是全新根」本身就是一条运维指令（「直接重跑」），而全仓此前**没有任何**用例在阶段 B 失败后重跑 `bootstrap`；既有断言 `all_files(states) == []` 只是该承诺的代理量，fixture 自己也承认这一点。判别变异体：任何让失败后残留阻塞重跑的实现（例如 (b) 腿不再清理/不再避免建目录、或把第三路话术错用到本腿）-> 本行必红。**同一断言对 `FileExistsError` 腿必然失败**（实测），故本行 MUST 只加在零残留腿上，且这正是它与桶 C-3 互为交叉验证的原因。
- **[桶 C-2] 排他创建话术的正向钉死**（round 3 cand-R3-05 CONFIRMED/FIX_NOW，单向缺口的反向补齐）：在「写入中途 I/O 失败的收尾」行上追加 MUST——`detail` 含「已被排他创建但写入中途失败」这一**确定性**措辞，MUST NOT 含对冲措辞「残留无法探测…保守起见按可能已被部分写入处理」。理由：`EXCLUSIVE_CLAIM` 此前被断言**不出现**两次、断言出现**零次**，而裁决 5 与对冲腿行（本 Matrix 的「新谓词的反向钉死」）合起来使 EXCLUSIVE 与 HEDGE 构成**可判别对**；只钉一侧就是同一单向缺口的反方向。判别变异体（S1）：把 `_probe_partial_residue` 的成功臂改吐对冲文本 -> 本行必红（实测该变异当时存活，而同一臂改成 `return None` 会红两条，证明该臂为活代码、S1 的存活是 oracle 缺口而非死代码假象）。
- **[桶 C-3] 外来条目第三路话术（两种载体）**（round 3 cand-R3-01 CONFIRMED，行 18 CORRECTION 的正向钉死）：两组构造，**均 MUST 覆盖**：(i) 终名腿——`states/ifs/<T_ifs>.cfg.ic` 预置为**空目录**（或悬垂 symlink，两载体实测同构）；(ii) **父目录腿**（round 4 R4-A）——`states/ifs` 预置为 symlink→目录 / FIFO / 悬垂 symlink（三种载体已由三位 reviewer 各自独立复现），使 `ensure_directory_no_follow` 抛 `NotADirectoryError`/`ELOOP`。其余为合法全新根 -> `WRITE_FAILED`、`written == ()`、`states/` 树下零普通文件；`detail` MUST 点名**被占住的那个路径本身**（终名腿是 `states/ifs/<T_ifs>.cfg.ic`，父目录腿是 `states/ifs`——插值 `target` 而非 `target_dir` 会让承诺在下一层再次为假）并要求重跑前先移除它，MUST NOT 含「零写入，根仍是全新根」、MUST NOT 含「可能已被部分写入」。并 MUST 断言：**补救后（移除该条目）重跑 `bootstrap` 得 `refusal is None` 且两份首态落盘**——这一半是本行与桶 C-1 重跑行的共同判据，也是行 18 原话术被证伪的直接证据（不移除条目时 run 2 与 run 1 逐字节相同）。判别变异体：把第三路合并回 (b) 腿（即恢复行 18 修订前的行为）-> 本行必红；**父目录腿另需一个独立变异体**：把 ensure 腿的 `blocked_by_foreign_entry` 固定为 `False`（即恢复 round-4 修复前的现状）-> 本行必红。
- **[桶 C-4] `NO_COMPLETE_RAW_CYCLE` MUST 区分「缺数据」与「不可读」**（round 3 cand-R3-02 CONFIRMED/FIX_NOW）：把某源唯一完整 cycle 的 bundle 文件 `chmod 0o000`（`judge` 返回 `missing_files == 0`、`unreadable_files == 2`）-> 仍判 `NO_COMPLETE_RAW_CYCLE`、零写入（方向不变，fail closed），但 `detail` MUST 点名存在**不可读**的 raw 文件（带数目或路径），且 **MUST NOT** 出现「等待 raw 补齐后重跑 init」这一伪装话术。理由：`rawscan.ScanVerdict` 早已把 `missing_files` 与 `unreadable_files` 分开（见 `ScanVerdict` 的字段定义与 `_check` 的 docstring——后者点名生产 raw 根是由另一 uid 写入的 NFS 树），而 `_first_complete_cycle` 只取 `.complete` 丢弃了整个 verdict；本 PR 自己已在 `cycle.hours` 路径上禁止了同一伪装（`test_config_error_from_judge_propagates_untouched`，其 docstring 逐字写着「把配置错误伪装成缺数据，运维会永远重跑」），raw 权限故障是同一伪装的未守卫版本。判别变异体：把 detail 退回不区分的原话术 -> 本行必红。注：裁决 7 的适用面枚举**不含 raw 面**（其方向性理由是「判空即放行写入」，本处判空是拒绝），故本条是裁决 7 的精神而非字面，属新增约束。
- **[桶 C-6] 未来 cycle 不夺首轮：本行无判别力，MUST 重构构造**（round 4 R4-D CONFIRMED/major/FIX_NOW，自桶 B 移入）：现有两个构造（`test_future_cycle_does_not_win_the_first_frontier` 与 `test_same_day_future_cycle_does_not_win_over_an_earlier_one`）的 complete 候选集都是**单元素**——前者因 `_candidate_cycles` 的日期网格 `span = (now.date() - start_date).days` 使 `NOW+12h` 从不进入候选集，后者因 `window_start <= cycle <= now` 把同日更晚 cycle 滤掉——两个排除机制都在**进入选取循环之前**生效，「不夺首轮」在剩余候选集上恒真，「升序取第一个」与「取窗内最晚」在单元素集上恒等。**MUST NOT 为本行补新声明**：两个排除机制的判别证据已分别由回归行 14 `test_future_cycle_is_not_a_candidate`（桶 A/cand-02）与 `test_same_day_future_cycle_is_excluded_by_the_now_comparison` 承担，补声明只会第四次复现 round-1 cand-02 的形状。修法是让本行**真正走到选取分支**：构造一个 complete 候选集含**两个及以上**元素的 raw 树（窗内两个都完整的 cycle），断言 T 取最早那个。判别变异体：把「升序取第一个 complete」改为「取窗内最晚 complete」-> 本行必红。并 MUST 在 Matrix 中写明本行覆盖哪几个用例（见本 Matrix 「未来 cycle 不夺首轮」一行，已按用例名列全）——原 Matrix 只写了一条构造，另一条长期无主。
- **[桶 C-7] `WRITE_FAILED` 的「列出全部已落盘 source」MUST 由单元级测试钉死**（round 4 R4-E CONFIRMED/major/FIX_NOW，round-1 cand-04 的真正闭合）：`rawscan.SOURCES` 是 2 元组且写入循环内三条失败腿均 `return`，故失败时 `len(written) ∈ {0,1}`——「列出**全部**」这条契约（`InitRefusal.WRITE_FAILED` 成员上方的注释）在端到端层**结构性不可达**，全仓无任何用例钉死它，`SOURCES` 一旦扩到 3 源即静默丢源。修法：对纯函数 `_write_failed`（无 I/O）直调，传入**伪造的两元 `written`**，断言两条路径都出现在 `detail` 中。MUST NOT 把契约弱化为单数（与本 Matrix 行 18 逐字冲突），MUST NOT monkeypatch `SOURCES`（那会动 Must-preserve 面的 `rawscan`）。判别变异体：把 `landed = "、".join(str(path) for path in written) or "（无）"` 换成 `str(written[0]) if written else "（无）"` -> 本行必红（**round 5 实测该变异被本行杀掉，红集恰为本用例**；在本行存在之前它在端到端全套下存活）。
- **[桶 C-8] 被跳过候选上的不可读 raw MUST 在成功理由中点名**（round 4 R4-C CONFIRMED/P2/FIX_NOW，层位=fixture 遗漏，实现对裁决 3 逐字合规）：把某源 `T0` 的一个 bundle 文件 `chmod 0o000`（该候选因此不完整）、`T0+12h` 完整 -> init **成功建链**（方向不变），T 取 `T0+12h`，但成功 detail MUST 点名被跳过候选上存在不可读的 raw 文件（数目或路径）。理由 **（round 4 现状记录，已由本轮修复关闭）**：`_first_complete_cycle` 当时在命中首个完整 cycle 时 `return cycle, ()`，丢弃此前累积的 `unreadable`，链起点被静默推后 12h **并落盘**；此后根已非全新，重跑必被 `STATES_NOT_EMPTY` 拒绝，**无自愈路径**。**MUST NOT 改为 fail-closed 拒绝**——那会让一个 raw 权限故障阻断整次建链，与 `§6.2` 的「方向不变、区别只在给运维的下一步动作」冲突。**MUST NOT 扩词表**：`NO_COMPLETE_RAW_CYCLE` 在此为假（确有完整 cycle），`DISCOVERY_UNREADABLE` 被裁决 7 的分层切分排除在 raw 面之外。判别变异体：恢复 `return cycle, ()` 的丢弃行为 -> 本行必红。
- **[桶 C-9] 缺文件与不可读同时存在时 MUST 并列点名**（round 4 R4-B CONFIRMED/P3/FIX_NOW，纯实现层，不需要改 docs——`§6.2` 已是 MUST）：构造某源唯一候选同时含缺失文件与 `chmod 0o000` 文件（`judge` 返回 `missing_files` 与 `unreadable_files` **均非空**）-> 仍判 `NO_COMPLETE_RAW_CYCLE`、零写入，detail MUST 并列点名两者，**MUST NOT 出现「不是缺数据」这类全称否定**，且此时仍 MUST NOT 出现「等待 raw 补齐后重跑」（`§6.2` 逐字限定该提示只在确为纯缺文件时才允许——把它扩成「补齐 missing 提示」是过度修复）。**（round 4 现状记录，已由本轮修复关闭）** 当时判别式是 `if unreadable:` 并断言「不是缺数据」，而 `_first_complete_cycle` 只累积 `unreadable_files`、丢弃 `missing_files`，该分支结构性地无法表达混合态；混合态在生产 NFS 上是主导形态（`rawscan.judge` 的 docstring 自陈「7 天扫描窗的绝大多数请求正落在这里」）。判别变异体：把并列措辞退回全称否定 -> 本行必红。
- **[桶 C-10] 「目录存在但零文件落地」不得被当成缺席**（round 5 R5-I CONFIRMED/P2/FIX_NOW，覆盖缺口）：ifs 窗内两个候选——一个**目录存在但一个预期文件都没落地**（发布中途崩溃的真实形态），另一个预期文件全部 `chmod 0o000` -> 拒绝理由 **MUST NOT** 含「不是缺数据」这类全称否定。理由：`_first_complete_cycle` 用 `verdict.missing_files == verdict.expected_files` 当「cycle 目录整体不存在」的代理判据，而 `ScanVerdict` 四字段全是文件级、接口不暴露目录存在性，该判据**在消费侧结构性不可逆**，同时抹掉了一类真实数据缺口。**删掉该过滤器不是合法修法**：7 天窗的绝大多数候选正是整目录缺席，不过滤则任何纯不可读拒绝都退化成混合态、纯不可读 Scenario 结构性不可达。真正分开两者需 `rawscan.judge` 带目录存在性信号（Must-preserve 面，先改文档），已另行记账。判别变异体：在纯不可读腿上恢复全称否定 -> 本行必红（实测红本行 + 纯不可读行）。
- **[桶 C-11] cycle 目录自身探不动时 MUST 点名目录而非逐条列举预期文件**（round 5 R5-J CONFIRMED/P2/FIX_NOW，覆盖缺口）：ifs 的更早候选是一个 `chmod 0o000` 的**空目录**、更晚候选完整 -> 成功建链，成功理由 MUST 点名该**目录**，MUST NOT 出现该目录下任何预期文件的路径。理由：`rawscan._check` 对每个**预期**路径 `stat()`，`EACCES` 不在 `FS_MISSING_ERRORS` 内，故目录权限位受限时每个预期路径都落进 `unreadable_files`——**无论盘上有没有这些文件**；逐条列举等于断言一批可能并不存在的文件「存在但不可读」，而真正的阻塞物一次都没被点名。措辞同时 MUST 降为**可访问性**口径（「无法访问」，存在性未知），因为「`unreadable_files == expected_files`」在「文件确实存在且全为 `0o000`」上同样成立，阻塞物位置相反——点名目录时 MUST 只把它作为**共同位置**给出，MUST NOT 断言目录权限位就是阻塞物。判别变异体：去掉整目录塌缩、退回逐条列举 -> 本行必红（实测红本行 + 纯不可读行）。
- **[桶 C-12] CLI 成功时的运维理由 MUST 在用户边界外露**（round 5 R5-F CONFIRMED/P2/FIX_NOW，覆盖缺口）：`cli.init` 成功分支 MUST 把 `report.detail` 打到 **stderr**（落盘路径列表仍走 stdout，两者分列，避免污染可管道消费的路径列表）。理由：round 4 把「成功理由 MUST 点名被跳过候选上无法访问的 raw」这条 MUST 的 oracle 落在 `InitReport.detail`（**库**边界）上，而 `cli.init` 当时只 `print(path)`、从不外露 `detail`——库层合规、用户可观测行为逐字节未变，MUST 在端到端上归零。**本行的 oracle MUST 落在用户边界**（stdout/stderr），这正是它与库层用例的区别。判别变异体：删掉那句 `print(report.detail, file=sys.stderr)` -> 本行必红（实测）。
- **[桶 C-13] 阻塞物在 `states/` 这一级时同样走第三路并点名该级**（round 5 R5-G CONFIRMED/P2/FIX_NOW）：把 `states/` **自身**预置成 symlink→空目录 / FIFO（两载体参数化），其余为合法全新根 -> 第三路话术，点名 `states` 这一级，**MUST NOT** 点名它下面那个盘上并不存在的 `states/<source>`，MUST NOT 出现「零写入，根仍是全新根」。理由见裁决 5-bis 的谓词域。**本行是 round 5 自查补出的**：R5-G 的修复原本零 oracle——把逐级走查退回单分量 `lstat(target_dir)` 的变异体在全套下**存活**，补本行后实测红两条（两个载体各一）。
- **[桶 C-5] 变体路径相对性闸门**（round 3 cand-R3-03 CONFIRMED，裁决 2 新增子项的正向钉死）：三条构造 -> `VARIANT_PATH_INVALID`、零写入：(i) `variants.{ifs,gfs}` 置为 `YD_ROOT` 外的**绝对路径**；(ii) 置为含 `..` 分量的相对路径；(iii) 参数化覆盖两个 source 各自单独越界的情形。实测未加闸门时三者均 `refusal=None` 且两份首态照写、calibration 解析到 `YD_ROOT` 外（只越界**读**，写入面恒为 `yd_root/"states"`，故为 P2 而非 P1）。判别变异体：删除闸门恢复裸 join -> 本行必红。**并 MUST 带一条对照行**：`variants.gfs == variants.ifs` 指向同一**合法相对**目录时——该目录持两份 `.cfg.ic` 判 `CALIBRATION_STATE_AMBIGUOUS`，持一份（`calibration_names` 两源同名）则**被接受**且两条链起点同源。该对照来自 verifier 对候选措辞的更正，MUST 按此构造，不得写成「共享变体目录被接受」。
- 跳过语义：窗内最早的两个候选 cycle 不完整、第三个完整 -> T 取第三个（证明「升序找第一个 complete」而非「取窗内最早候选」）
- 4-token 兼容 header（含 lake 段）的率定末态 -> 正常重戳落盘，minute token == `round(T.timestamp()/60)`，其余字节逐字不变
- 非默认配置取值（`config.cycle.hours = [12]` 单值 + `variants.gfs = "input/models/alt_gfs"` 非默认目录名）-> 仍正确定位变体、仍只在 12Z 候选上扫描；硬编码 `[0, 12]` 或硬编码 `input/models/yd_<source>` 的实现在本行必红
- 阶段 B 中途失败（`states/gfs/<T_gfs>.cfg.ic` 预置为**空目录**，写入序为 ifs→gfs）-> `WRITE_FAILED`，`detail` 列出已落盘的 `states/ifs/<T_ifs>.cfg.ic`，该文件**仍在**且内容不变，进程退出码非零
- 未改动的下游 `controller.decide_frontier`（#22）读 init 写出的首态 -> 判为「全新链、待跑 T = 该文件名」，不因 header 时间被判 `HEADER_TIME_MISMATCH`（跨 issue 兼容性回归，本 PR MUST 有一条端到端用例）
- `cli.main(["init", ...])` 成功一次、拒绝一次 -> 退出码 `0` / `EXIT_GUARD`，且 `run` 的 `_check_states_dir` 在 init 之后不再拒绝

Boundary-surface checklist:
- 共享 helper 根：`store/safe_fs`（写）、`state/`（解析/重戳）、`rawscan`（判定）——本 issue **只消费不修改**，MUST 报告检查过但未改的清单
- 公共入口：`cli.init()`；`build_parser()` 的参数集不变
- 读面：变体目录（顶层枚举 + 单文件读）、NWM raw 树（经 `judge`）
- 写/覆盖面：仅 `states/<source>/`，且 `O_EXCL` 拒绝覆盖
- staging/publish/rollback 面：无 staging（首态直接落终名；本 issue 不参与 §11 的发布顺序）；**不提供回滚**（裁决 5）
- 生产者/消费者证据边界：`states/<T>.cfg.ic` 是 #22 前沿函数与 #24 发布器的上游
- 陈旧状态/幂等边界：init **非幂等**且刻意如此——第二次执行必然 `STATES_NOT_EMPTY`
- 未改动的下游消费者：`controller.decide_frontier`、`cli.run` 的 `_check_states_dir`

Selected risk packs（项目特有检查）:
- Public API / CLI / script entry: `yd-producer init` 首次成为真实入口；退出码与 stderr 是运维唯一可见面
- Config / project setup: 读 `config.variants.*`、`config.cycle.hours`、`local.yd_root`、`local.nwm.raw_root`；MUST NOT 硬编码任何一项。判别证据由「非默认配置取值」回归行承担——fixture 树 MUST 用 `cycle.hours = [12]` 与非默认变体目录名，使任何硬编码必红
- File IO / path safety / overwrite: 首次向 NFS 发布根落盘；no-follow + `O_EXCL`；零删除
- Schema / columns / units / field names: 文件名 T 与 header minute token 的互相对应即契约
- Concurrency / shared state / ordering: 守卫与写入之间的 TOCTOU 由 `O_EXCL` fail closed；两源写入的顺序与部分落盘语义
- Resource limits / large input / discovery: 7 天扫描窗的候选集有界（`len(cycle.hours) * 8`，由「扫描窗边界」与「未来 cycle」两条回归行钉死上下端点）；率定末态读取经 `state.parse` 的 `MAX_STATE_IC_BYTES` 有界读（由 `CALIBRATION_STATE_UNREADABLE` 回归行的超界分支钉死）；变体目录枚举**非递归且只取顶层**（由 `CALIBRATION_STATE_AMBIGUOUS` 回归行钉死）
- Error handling / rollback / partial outputs: `InitRefusal` 闭合词表 + 阶段 A 零写入 + 阶段 B 部分落盘的显式语义
- Legacy compatibility / examples: 3-token 原生与 4-token 兼容 header 都要能重戳

Risk packs considered (core):
- Public API / CLI / script entry: selected - 见上
- Config / project setup: selected - 见上
- File IO / path safety / overwrite: selected - 见上
- Schema / columns / units / field names: selected - 见上
- Auth / permissions / secrets: not selected - 无凭据面；权限失败只体现为 `DISCOVERY_UNREADABLE` 分类
- Concurrency / shared state / ordering: selected - 见上
- Resource limits / large input / discovery: selected - 见上
- Legacy compatibility / examples: selected - 见上
- Error handling / rollback / partial outputs: selected - 见上
- Release / packaging / dependency compatibility: not selected - 不新增依赖，lock 不变
- Documentation / migration notes: selected - 裁决 2 补齐的 seam 必须同步进 compute-loop §6.2 与 init-bootstrap spec，否则 #20 落地时会与本 issue 分叉

Domain packs (from active profile):
- Geospatial / CRS: not selected - 无几何面
- Time series / forcing / temporal boundaries: **selected** - 7 天窗、`cycle.hours` 全域、UTC aware 归一
- 状态链 / warm-start 定戳一致性: **selected** - 本 issue 是整条状态链的**起点**，首态戳错即全链失效
- NWM 快照溯源与 DB-free 隔离: not selected - 本模块无 pin 移植（只消费已落地的移植产物），无新增溯源头

Evidence floor:
- `cd producer && uv run pytest`
- `cd producer && uv run ruff check . && uv run ruff format --check .`
- `openspec validate m2-producer-core --strict --no-interactive`
- 新行为用例的红证据（对 pre-change 源跑一次必红），按 implementer 契约批量提供
- 所有 `chmod 0o000` 的 discovery 用例 MUST 带既有 `producer/tests` 的 root 跳过口径（root 下 `chmod 0o000` 不产生 `EACCES`，用例必须 skip 而不是假绿）
- 所有传给 `safe_fs` 的路径 MUST 由 `tmp_path.resolve()` 派生：macOS 的 `tempfile.gettempdir()` 落 `/var/folders/...` 而 `/var` 是 symlink，`safe_fs._anchor_for` 逐段拒 symlink，未 resolve 即得与实现无关的假红（既有 `producer/tests` 已用此口径）

Non-goals（越界即偏离）:
- prepare 编排与变体生成（任务 10.3 / #20）
- run 控制器循环、前沿推进、残留清理、锁（组 12–14）
- 状态 QC / 负残差归零（裁决 10）
- 单源建链、补链、`--force`、覆盖参数（裁决 11）
- 任何删除动作（归 #23/#25）
- 数值正确性（归 M4）

Review focus:
- 阶段 A 是否**真的**零写入：有没有在扫描或解析路径上顺手 `mkdir` 了 `states/<source>/`
- 「任一源无完整 cycle 即整体拒绝」是否由**先全部定 T、后集中写**的结构保证，而不是靠恰好的循环顺序
- 拒绝守卫的枚举是否 fail-closed（`EACCES` 是否被误当成「空」）
- 写是否用 `O_EXCL`，而不是会覆盖的 `atomic_write_bytes_no_follow`
- 首态 header 的 minute token 是否等于 `round(T.timestamp()/60)`，且用例的期望值由**构造/锚点**给出，而不是在测试里再调一次 `restamp_to_absolute_time`（被测函数自判 = 永真式）
- `now` 与 `cycle.hours` 是否真的来自入参/配置，而非硬编码
- 是否越界实现了 10.3 的 prepare 编排或组 12–14 的控制器

## 12. run-controller（一）：前沿发现与锁

- [x] 12.1 实现严格前沿纯函数：`DONE`/状态文件集合 → 每源待跑 T 或停止原因（全新链、D+12h、状态缺失、时间头不对应 T、raw 缺口、缺轮阻塞）
- [x] 12.2 实现未提交残留识别与清理重跑判定（保留 T 状态、删更晚状态与半成品）
- [x] 12.3 实现非阻塞 flock 封装（持有即跳过、覆盖全生命周期），进程内测试跳过语义

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

### Issue #23 fixture（任务 12.2/12.3）

Fixture level: expanded
Upstream suggested level: compact（override：正面命中 `openspec/project-profile.md` 的 domain expanded-triggers `DONE`、`cycle`、前沿/frontier、状态链 与 `flock`、NFS；且本 issue 是 M2 里**第一处删除 `YD_ROOT` 内路径**的代码——profile 首位风险轴「断链即整链失效」在此从「读错」升级为「删错」）
Repair intensity: high（12.2 的删除集合直接毗邻状态链；一次多删 T 自己的状态即整链失效且不可逆。适用 `Invariant Matrix`）
Project profile: yd-viewer

**上游契约偏离（consumed not renegotiated，须回流 stage-change-pipeline sizing-retro）**：

1. issue #23 的验收标准原文不含 `cron.lock_path` 绝对路径要求；该条由 #32 的裁决在 `tasks.md:166` 路由至本 issue 任务 12.3。本 fixture 消费该路由并按 fail-closed 落地（裁决 8），同批出 `specs/run-controller/spec.md`「并发与锁」的 delta。
2. issue #22 fixture 裁决 6 把「无 `DONE` 却有多份状态是否算异常」路由至本 issue；本 fixture 裁决 3 给出判定。
3. issue #59 把「崩溃恢复前置：凭什么断定同源无在途孤儿作业」路由至 **#23 与 #28** 两份 fixture；本 fixture 只对**本 issue 的删除集合**给出边界裁决（裁决 9），完整裁决（含两条候选的取舍）归 #28，理由见该裁决。
4. `specs/run-controller/spec.md` 的保留清理 Requirement 把「每个删除目标 MUST 先经 `realpath` 确认位于 yd 自己的根内」写成了跨全部删除点的字面 MUST。本 issue 以 `safe_fs` 的 `containment_root` 满足它（裁决 6）：该机制全程 `O_NOFOLLOW` 逐段锚定，消除了「先 `realpath` 再比前缀」在解析与使用之间的 TOCTOU 窗口，故不出 spec delta；`realpath` 字面实现归 13.3 的保留窗清理。**两者不是包含关系，原措辞「严于」不成立**（round 1 containment 批核验推翻，round 2 fixture-accuracy 批更正论据）：按「每个删除目标」= 树内每一个被 unlink 的条目 这一逐条目读法，存在 `containment_root` 放行而字面 `realpath` 拒绝的方向——`remove_tree_allow_symlinks` 会 unlink 半成品树内一个 `realpath` 落在 `YD_ROOT` 外的 symlink 条目（`producer/tests/test_controller_residue.py` 的 `test_symlink_inside_half_product_tree_is_unlinked_not_followed` 钉住：链接被删、目标存活），而「先 `realpath` 再比前缀」会拒删该条目。这是刻意的 fail-safe（unlink link, never traverse：不跟随即不可能进入 NWM raw 根），故不出 spec delta，字面 `realpath` 归 13.3。另有一条方向相反的差异，但它证明的是 `containment_root` **更严**而非更松，MUST NOT 拿它当「不是包含关系」的论据（round 2 核验指出原文正是这么写的，论证方向反了）：`containment_root` 额外要求 root **自身逐段无 symlink**（`safe_fs.py:824-843` 以 `containment_root=None` 从 `/` 重新锚定，逐分量过 `O_NOFOLLOW`），而 `realpath` 语义允许 root 经 symlink 到达。这条额外前置条件是实打实的调用方约束，故裁决 6 增补：传给 `safe_fs` 的 `containment_root` MUST 是 `Path(yd_root).resolve()`，且该前置条件 MUST 写进 `residue.py` 模块头与 `ResiduePlan.yd_root` 的契约。**root 层的 `resolve()` 是容纳机制的前置条件，不是 spec 那条逐目标 `realpath` MUST 的兑现**，两者不得混为一谈。
5. **`docs/compute-loop-design.md` §10 把残留清理放在步骤 4、raw 扫描放在步骤 5，本 issue 的实现把清理挪到了 raw 扫描之后**（round 1 docs 批核验 CONFIRMED）。机制：裁决 2 以 `FrontierDecision` 不可跑为清理前置，而 `RAW_INCOMPLETE` 也是一个 `stop_reason`，于是「T 的 raw 未齐」会连带跳过清理——但那一刻 T 其实是**已知**的（`controller.py` 把它写进了 `detail`），裁决 2 原先给的理由「不知道 T 就无从定义更晚」对这一支不成立。后果限于磁盘：残留留在 NFS 上直到 raw 补齐，`products-contract.md` §4.3 使 viewer 不读无 `DONE` 的源目录，前沿只认 `DONE`，故不产生错误产物；且集合逐源有界，不增长。**按 docs 优先，§10 是对的，不修订 §10**；持久解法是让 `RAW_INCOMPLETE` 携带 T（`FrontierDecision` 形态变更），归任务 14.1 的 `run_once` 接线一并裁决。此处记录偏离，不在本 issue 改形态。

**核心设计裁决（本 fixture 钉死，实现不得自行改写）**：

1. **判定与执行严格分离，且判定 MUST 零写入**。产出两个符号：一个纯判定函数返回「本源的残留清单」（更晚状态文件列表 + 半成品 cycle 目录列表 + 保留的 T），一个执行函数按该清单删除。理由不是风格：任务 13.2（`## 13. run-controller（二）` 组首）逐字写着「复用 12.2 判定，仅接入失败/重跑路径」——失败路径要的是**判定**而不是删除动作，融成一个函数即让 13.2 无从复用。判定函数与 `decide_frontier` 同姿态：只 `stat` / 列目录，MUST NOT 创建、修改或删除任何路径，以递归树快照证明。
2. **残留集合的定义域是 NFS 侧、逐源**，对齐 compute-loop §10 步骤 4 与 `specs/run-controller/spec.md`「未提交残留清理重跑」的**删除集合**（步骤**顺序**上的偏离见上方偏离记录第 5 条）：
   - `states/<source>/` 里 cycle **严格晚于** T 的合法状态文件；
   - `output/<T>/<source>/` 存在且其下**无 `DONE`** 时，该 source 目录整棵；
   - **不含** scratch `work/<source>/<T>`（compute-loop §11.3 与 §12 把 work 的删除定为失败收尾与保留清理的动作，归 #26/#28/#13.x），不含 14 天保留窗清理（§12，归 13.3）。
   输入 T 取 `decide_frontier` 的结论 cycle；`FrontierDecision` 不可跑（带 `stop_reason`）时本源 MUST NOT 进入清理——`FrontierDecision` 在不可跑时不携带 cycle，调用方拿不到 T 就无从定义「更晚」。`RAW_INCOMPLETE` 因此被连带跳过，属已记录偏离（偏离记录第 5 条），不是本裁决的目的。
   **`source` 的输入域 MUST 在判定入口 fail closed**（round 1 A3 PLAUSIBLE/DEFER，因后果属数据丢失类而在本轮一并落地）：仅校验 `source == decision.source` 不够——空串会让 `output_root / cycle_id(T) / ""` 塌回 `output/<cycle>/`（`Path("/a/b") / ""` 就是 `/a/b`），删除粒度由本源子目录放大为整个 cycle 目录，连同另一源已带 `DONE` 的正式产物一起消失，且 `safe_fs` 帮不上忙（它看到的条目名是一个合法的 10 位 cycle id）。`source` MUST 非空；越界的 source 名 MUST 报错而不是构造路径。
3. **全新链同样适用，且 T 仍取最早状态文件名**（结清 #22 裁决 6 路由的张力）：该源无任何 `DONE` 时 T = `states/<source>/` 里最早的合法状态，比它更晚的状态一律是残留。理由：init 只写一份首态（`spec.md`「全新链取首态文件名」），多出来的份只可能来自一次中断的首轮发布；按同一条规则删除后重跑 T，与「无 `DONE` 残留必须可干净重跑」一致。MUST NOT 把「无 `DONE` 却有多份状态」判为异常停源——那会让首轮崩溃永久砖化该源。
4. **`DONE` 的存在性是每个 `output/<cycle>/<source>/` 的删除前置**，MUST 逐源 stat `DONE` 这个普通文件，MUST NOT 以「目录非空」或「有 `yd.rivqdown.dat`」代替（products-contract §4：`DONE` 是唯一完成标志）。删除的粒度是 `output/<cycle>/<source>/`，**不是** `output/<cycle>/`：另一源可能在同一 cycle 目录下已有 `DONE`。父目录 `output/<T>/` 在删完本源子目录后 MUST 保留（是否删空目录归 13.3 的保留清理）。
   **`DONE(retained)` 存在时整个清单 MUST 为空，不只是半成品那一半**（round 1 chain-destruction 批 A1 CONFIRMED/FIX_NOW，实测）：该闸只在调用方**交来**一个 T 时可达（`decide_frontier` 自己产出的 T 永远不在 `done_cycles` 里），而交来 T 正是任务 13.2 的复用姿态；只挡半成品、不挡状态文件，会在「publish 已写 `states/<T+12>` 与 `DONE(T)`、其后某步失败」时把刚提交的下一环判为残留删掉，前沿随即永久停在 `STATE_MISSING`。这直接违反本 fixture 的 Must-preserve「清理前后对同一棵树调用 `decide_frontier`，T 不变」。故该判据 MUST 提到 `plan_residue` 一层，两类删除共用。
5. **不可见条目永不删除**。`decide_frontier` 的 cycle 可见集判据（10 位数字且 `%Y%m%d%H` 可解析且 `cycle+12h` 不溢出；`states/` 侧另需 `.cfg.ic` 后缀）在本 issue **原样复用**，MUST 从 `controller` 导入而非重写一份。方向与 #22 裁决 5 相反但同源：那里「不可解析 ⇒ 不可见 ⇒ 不砖化该源」，这里「不可解析 ⇒ 无法判定是否比 T 晚 ⇒ 不删」。只删除能被正面识别为残留的路径，是本 issue 的 fail-closed 形态。
6. **删除原语一律走 `store/safe_fs.py`，两类路径策略不同且不对称，理由须写进模块头**：
   - 半成品 `output/<cycle>/<source>/` 用 `remove_tree_allow_symlinks`——该原语的 docstring 逐字说明它就是为「内容按构造不可信的 residue/quarantine 树」而存在，且拒绝 symlink 会「permanently lock the run at the hygiene hook」；本处正是该场景（被杀死的发布尝试留下什么都可能）。
   - 残留状态文件用 `unlink_no_follow`（遇 symlink 抛 `SafeFilesystemError`）。不对称的理由：`states/<source>/<cycle>.cfg.ic` 只由发布器以「普通文件原子 rename」写入（`spec.md`「NFS 提交顺序与 DONE 语义」步骤 3），该位置出现 symlink 不是崩溃残留而是异常，按 fail-closed 停该源。
   - 两者都 MUST 传 `containment_root=<YD_ROOT>`，落实 compute-loop §12「清理只允许作用于经确认位于 yd 自己根目录下的对象；不得跟随路径进入 NWM raw 根」。MUST NOT 用 `shutil.rmtree` / 裸 `Path.unlink`。
   - **测试树 MUST 用 `tmp_path.resolve()` 作 `YD_ROOT`**：`safe_fs._open_directory_no_follow` 会把 `containment_root` **自身的每一个祖先分量**重新过一遍 `O_NOFOLLOW`（issue #77 的证据链，`safe_fs.py:824-843`），而 macOS 的 `/var` 是 symlink，未 resolve 的 `tmp_path` 会得到与被测逻辑无关的红。
7. **清理失败即停该源，MUST NOT 静默继续**。任一删除抛 `SafeFilesystemError` 时本源本次停止（不重跑、不提交），错误 MUST 指名失败的路径。理由与 #22 裁决 9 同向：删了一半就重跑，等于让下一步在一个既非干净也非完整的树上组装。**幂等**：对已清理干净的树重复调用判定+执行是 no-op（清单为空、零删除、零异常），这是 cron 每小时重入的必需性质。
8. **`cron.lock_path` 非绝对路径 fail closed，闸门位置在 flock 封装的最前**（消费 #32 经 `tasks.md:166` 的路由）。相对路径与 `~` 前缀两种形态都拒（`Path` **不**展开 `~`：`Path("~/x") / "y"` 得到 `'~/x/y'`），报错 MUST 指名 `cron.lock_path`，且 MUST 在**任何文件系统副作用之前**——否则 cron 的工作目录一变，锁文件就落到另一个路径上，两个实例各持各的锁，互斥静默失效，这正是本条要防的危害。**不选**在 `config.py` 装载期强制：`local.toml` 的其余现场路径字段当前都不做绝对性校验，只为本字段在装载期开一个特例会让 `cli-config` spec 的 MUST 范围与实现不一致（`specs/cli-config/spec.md` 的装载 Requirement 未含路径形态约束）；闸放在唯一的消费点更窄且可测。同批已出 `specs/run-controller/spec.md`「并发与锁」的 delta。
9. **#59 崩溃恢复前置：本 issue 的删除集合与任何 Slurm 作业的写入集合按构造不相交，故 12.2 不需要在途作业存活确认；完整裁决归 #28**。两个窗口逐一点名：
   - **窗口 1（进程死亡）**：孤儿作业 12345 的 `--chdir` 是 scratch `work/<source>/<T>`（`--chdir=work_dir` 的绑定在代码侧：`producer/src/yd_producer/slurm.py:133-140`，由 `tasks.md` 的 #11 fixture 与 `producer/tests/test_slurm.py` 逐元素钉住；compute-loop §3.3 / §10 步骤 6 只给出「work 是 scratch 下的一次性隔离单元」这一层，**不含** `--chdir` 字样——round 3 核验更正原引用），它写的全部路径都在 scratch 下。裁决 2 已把 work 排除出本 issue 的删除集合，故「下一 tick 删掉正在被写的 work 目录」这条后果在 12.2 上不可达。NFS 侧的 `output/` 与 `states/` 只由控制器进程写（`spec.md`「NFS 提交顺序与 DONE 语义」的六步全部是控制器动作），而控制器写入被 12.3 的锁覆盖，孤儿的是 Slurm 作业不是控制器。
   - **窗口 2（已提交但未登记）**：同上——没有任何 job ID 存在，但也没有任何 Slurm 作业会写 NFS 侧路径，故对 12.2 的删除集合同样不可达。
   - **仍然成立的危害与其落点**：一旦 #28 把 work 的删除接进重跑路径，两个窗口都恢复可达，且窗口 2 按 #59 的构造性不对称无法用 job ID 覆盖。因此 #59 的两条候选（(a) 存活确认 / (b) 见半成品即停等）与 `spec.md`「未提交残留清理重跑」是否需要 delta，**整体归 #28 裁决**，本 issue MUST NOT 替它选。本 issue 的义务是把边界写死在此并在 #59 上留证。
10. **不接线 `run` CLI**。`cli.py:116` 的 `run` 仍是 `_unimplemented`，接线归任务 14.1（issue #26/#27）。12.3 交付的是一个可复用的上下文管理器 / 包装函数，MUST NOT 修改 `cli.py` 的子命令行为。
11. **flock 语义钉死**：用 `fcntl.flock(fd, LOCK_EX | LOCK_NB)`，MUST NOT 用 `fcntl.lockf`；释放时 MUST NOT `unlink` 锁文件（删掉后另一实例会在新 inode 上建锁，两个持有者同时成立）；被包裹的可调用对象在跳过分支 MUST NOT 被调用；跳过是**成功**语义（与「跑过了」可区分的返回值，不是异常，不是非零退出）。`fcntl.flock` 的锁挂在 open file description 上，故同一进程内两次独立 `open()` 互相冲突——进程内用例因此是有效判别器。spec 的 Scenario 写的是「另一进程」，任务 12.3 写的是「进程内测试」：等价性由上一句给出，但 MUST 另加一条子进程用例正面覆盖 spec 的字面 WHEN。
    **进程内跳过用例的第一持有者 MUST 也经同一个封装取得锁，MUST NOT 由测试自己直接 `fcntl.flock`**（fixture 复核实测，darwin 24.6.0）：XNU 把 `flock` 与 `lockf` 并进同一条 lock list，测试自持 `flock` 时封装侧的 `lockf` 仍报 `EAGAIN`，于是 `flock → lockf` 变异体照样走跳过分支、用例保持绿而存活。两侧同经封装则该变异体使两把锁都变成同进程不冲突的 `lockf`，第二次进入会**真执行**，用例变红。这条不是风格：判别器的两端必须同时被变异，否则平台的锁合并语义会把变异体藏住。
12. **零新增依赖**：`fcntl`、`os`、`pathlib` 全在 stdlib。本 issue MUST NOT 引入 `filelock` 之类的第三方包。

Must-preserve behavior:
- `decide_frontier` 与 `controller` 现有导出的行为逐字不变（本 issue 只新增符号）；`producer/tests/test_controller_frontier.py` 全套通过（本 PR 只改了其中一处 docstring 措辞 `_done_cycles`→`done_cycles`，无断言变更；`frontier_fixtures.snapshot_tree` 的快照元组增加 `st_mtime_ns` 维度，由零写入证据条目要求）
- 「前沿只由 `DONE` 推进」——清理动作 MUST NOT 反过来影响 T 的计算：清理前后对同一棵树调用 `decide_frontier`，T 不变（清理后 T 仍是 T，正是「以 T 状态重新组装本轮」在发现层的可证形式）
- `states/<source>/<T>.cfg.ic` 在任何路径上都不被删除
- 已带 `DONE` 的 `output/<cycle>/<source>/` 及其 `yd.rivqdown.dat` 在任何路径上都不被删除
- `store/safe_fs.py` 零改动（本 issue 是它的消费者，不是它的维护者）

Seams under test:
- 目录树 fixture（`tmp_path.resolve()` 下的合成 `YD_ROOT`），无注入式 fake——删除是真实文件系统动作，记录型 fake 会让「删对了没有」退化为永真式
- 锁：封装自身持锁 + 同进程第二次进入同一封装（跳过语义；两端同经封装，见裁决 11 末段）+ 一个子进程持锁（spec 字面 WHEN）
- 时间/cycle：直接构造文件名，不注入时钟

Required evidence:
- **纯判定零写入**：判定函数调用前后对整棵 `YD_ROOT` 做递归快照（路径、类型、大小、mtime）逐项相等
- **保留 T**：树含 `DONE(T-12)`、`states/<T>.cfg.ic`、`states/<T+12>.cfg.ic`、无 `DONE` 的 `output/<T>/<source>/`（只含 DAT）-> 清理后 `states/<T>.cfg.ic` 仍在，`states/<T+12>.cfg.ic` 与 `output/<T>/<source>/` 已删，`output/<T-12>/` 整棵未动；再调 `decide_frontier` 仍返回 T
- **边界方向**：cycle **恰好等于** T 的状态文件永不删（这条是变异体 (a) 的判别器）；cycle 为 `T+12`、`T+24` 的多份更晚状态一次全删
- **逐源隔离**：IFS 与 GFS 在同一 cycle 上各有更晚状态与半成品，只清 IFS；GFS 侧递归快照不变。`output/<T>/` 父目录在 IFS 子目录删完后仍存在
- **`DONE` 保护**：`output/<T>/<source>/` 下同时有 `DONE` 与 DAT -> 不在清单内、零删除；把 `DONE` 换成同名**目录**或**断链** symlink -> 按 `DONE` 的普通文件判据视为无 `DONE`（与 `controller.py` 的 `DONE` 判据一致：该处 `os.stat` 跟随 symlink，故指向普通文件的 symlink **算**已完成，此形态刻意不在本用例内），进入清单
- **空半成品目录**：`output/<T>/<source>/` 存在但为空（mkdir 后即崩）-> 判为半成品并删除
- **不可见条目不删**：`states/<source>/` 下有 `2026082612.cfg.ic.tmp`、`nine.cfg.ic`、`9999123123.cfg.ic`、`.DS_Store`；`output/` 下有 `stray/`、`.DS_Store` -> 清理后逐个仍在
- **symlink 策略两侧**：`states/<source>/<T+12>.cfg.ic` 是 symlink -> 停该源并报错指名该路径，链接与其目标都还在；`output/<T>/<source>/` 树内含一个指向 `YD_ROOT` 外的 symlink 条目 -> 该树被删除，链接的**目标**未被删除（unlink link, never traverse）
- **containment**：`states/<source>/<T+12>.cfg.ic` 是指向 `YD_ROOT` 外普通文件的 symlink 时（上一条）目标存活；另断言判定+执行传入的 `containment_root` 就是 `YD_ROOT`（以越界路径构造的调用被 `safe_fs` 拒绝）
- **幂等**：同一棵树上连跑两次判定+执行 -> 第二次清单为空、零删除、零异常，树快照与第一次结束时相等；**且 MUST 再执行同一份旧清单对象一次**（不重新判定）-> 同样零删除、零异常。后一步才是变异体 (s) 的判别器（round 3 核验实测：只做前一步时 (s) 存活——重新判定后的空清单让 `execute_residue_plan` 迭代两个空元组，根本走不到 `safe_fs`）
- **交来的 T 已有 `DONE` 时整个清单为空**（裁决 4 增补）：树含 `DONE(T)`、`output/<T>/<source>/` 半成品、**以及 `states/<T+12>.cfg.ic`**，以直接构造的 `FrontierDecision(cycle=T)` 调用 -> `state_files` 与 `half_product_dirs` **都**为空、零删除；随后 `decide_frontier` 仍返回 T+12 且可跑。既有的同姿态用例只放了半成品而没放 `states/<T+12>`，恰好绕开了这条
- **`containment_root` 就是 `YD_ROOT`**（两个独立判别器，缺一不可）：其一，`plan_residue` 产出的 `plan.yd_root` 等于 `Path(传入的 YD_ROOT).resolve()`——传入值已是实路径时即等于它本身，经 symlink 到达时等于解析后的实路径（见下一条用例）；把它改成 `root.parent` 会静默放宽容纳域而现有用例全绿；其二，越界的手搓 plan **只带 `state_files`、`half_product_dirs` 为空**时执行仍被拒（现有越界用例走的是半成品那条臂，状态文件那条臂的 `containment_root` 掉了也不会红）
- **`YD_ROOT` 经 symlink 到达时清理仍成功**（裁决 6 增补）：以 `link -> real` 构造根并把未 resolve 的 `link/yd` 传给 `plan_residue` -> 判定与执行都成功，删除结果与直接用 `real/yd` 一致。这条钉住「判定侧跟随 symlink 而执行侧对 symlink 致命」的不对称
- **`source` 输入域**（裁决 2 增补）：`source=""` -> 报错，且 `output/<cycle>/` 与另一源的 `DONE` 产物零改动
- **`source` 输入域 MUST 逐合取项各有判别器**（round 2；上一条只跑 `""`，闸里其余两项无判别器）：同一条用例参数化跑 `["", ".", "..", "a/b", "ifs/"]` 五个形态，每个都断言抛 `ValueError`、树的递归快照不变、另一源的 `DONE` 产物仍在。映射逐条不可省——`""` 只被 `not source` 挡；`"."` 与 `".."` 只被**显式点名集**挡（`Path("..").name` 就是 `".."`，`Path(".").name` 是空串只属 pathlib 的顺带效果，MUST NOT 依赖）；`"a/b"` 与 `"ifs/"` 只被单分量判据挡
- **`..` 条目名的拒绝 MUST 有消费者侧的钉子**（round 2；`store/safe_fs.py` 零改动，故义务落在本 issue 的用例里）：手搓一份 `half_product_dirs` 含 `output/<T>/..` 的 `ResiduePlan` -> `execute_residue_plan` 抛 `SafeFilesystemError` 且 `kind == "unsafe"`，执行前后 `YD_ROOT` 递归快照逐字节相等、另一源的 `DONE` 产物仍在。理由：该保证由 `safe_fs.remove_tree_allow_symlinks` 首行的 `_reject_unsafe_entry_name` 独家承载，仓内无用例钉住它
- **`ResiduePlan.empty` 的半成品臂 MUST 有判别器**（round 2）：树为 `DONE(D)` + `states/<source>/<T>.cfg.ic` + **空的** `output/<T>/<source>/` -> `plan.state_files == ()`、`plan.half_product_dirs` 非空、`plan.empty is False`。两个断言缺一不可：没有 `state_files == ()` 这条前提，用例对 `return not self.state_files` 没有判别力
- **执行序 MUST 有判别器**（round 2）：模块头逐字钉死「先半成品树、后更晚状态」。判别树为 `states/<source>/<T+12>.cfg.ic` 是指向 `YD_ROOT` 外普通文件的 symlink + 同时存在半成品树 -> 抛 `SafeFilesystemError` 之后半成品目录**已不存在**、symlink 与其目标都还在。两种顺序都抛，只有钉死的顺序会先删半成品
- **半成品位置不是目录时不删**（裁决 6 的类型判据）：`output/<T>/<source>` 分别是 symlink、普通文件、FIFO 三种形态 -> 三者都不入清单，执行后条目仍在
- **判定侧的 fail-closed 收敛**：`chmod 0o000` 掉 `states/<source>/` -> `plan_residue` 抛 `ResidueError`，MUST NOT 返回空清单（空清单会让残留留在树上被下一轮当成正常产物）
- **锁的竞争/真错分流**：monkeypatch `fcntl.flock` 抛 `PermissionError(EACCES)` -> 异常向外传播，MUST NOT 变成 `acquired=False`，且被包裹的可调用对象零调用
- **不可跑源不清理**：`FrontierDecision` 带 `stop_reason`（如 `STATE_MISSING`）时该源零删除
- **全新链**：无任何 `DONE`、`states/` 有 `T`、`T+12` 两份 -> T 取最早、`T+12` 被删（裁决 3）
- **锁：持有即跳过**：同进程第一个 fd 持锁，第二次进入包装 -> 立即返回跳过结果、被包裹的可调用对象零调用、进程不阻塞（用例带超时）
- **锁：子进程持有**（spec 字面 WHEN）：子进程持锁期间父进程进入包装 -> 同上
- **锁：释放后可再取**：第一次正常退出后第二次进入 -> 真正执行；锁文件在释放后**仍存在**（不 unlink）
- **锁：异常路径也释放**：被包裹的可调用对象抛异常 -> 异常向外传播且锁已释放（同棵树第二次进入能拿到锁）
- **非绝对锁路径**：`"yd.lock"` 与 `"~/yd.lock"` 两种形态 -> 抛错且消息含 `cron.lock_path`；断言 cwd 下与 `Path.home()` 下**都没有**新建锁文件，且被包裹的可调用对象零调用（spec Scenario 逐字要求「不执行发现」；副作用先于闸门是本条要杀的形态）
- 预登记变异体（(a)–(af) 共 32 条，此处刻意写全数；(t)–(aa) 由 round 1 核验门追加，(ab)–(af) 由 round 2 核验门追加），每条 MUST 被上列用例杀死（跑法见 `openspec/project-profile.md` 的 Mutation-testing hazards，用 `uv run python -m pytest`）：
  (a) 「更晚」判据 `>` 改 `>=` -> 保留 T 用例变红；
  (b) 逐源过滤去掉（对 `states/` 全域比较）-> 逐源隔离用例变红；
  (c) `DONE` 存在性判据改为「目录非空」-> `DONE` 保护用例变红；
  (d) 可见集门去掉（不可解析文件名也参与比较/删除）-> 不可见条目用例变红；
  (e) 判定函数里顺手删除（判定与执行融合）-> 零写入快照用例变红；
  (f) 删除粒度由 `output/<cycle>/<source>/` 放大到 `output/<cycle>/` -> 逐源隔离用例变红；
  (g) `remove_tree_allow_symlinks` 换成 `rmtree_no_follow` -> 半成品树含 symlink 的用例变红（该变异体正是原语 docstring 说的 permanent lock）；
  (h) `unlink_no_follow` 换成 `Path.unlink` -> symlink 状态文件用例变红（目标被删或未停源）；
  (i) 去掉 `containment_root` 参数 -> containment 用例变红；
  (j) `fcntl.flock` 改 `fcntl.lockf` -> 持有即跳过（进程内）用例变红；
  (k) 去掉 `LOCK_NB` -> 持有即跳过用例超时变红（用例 MUST 自带超时，否则测试自身挂死）；
  (l) 跳过分支仍调用被包裹对象 -> 零调用断言变红；
  (m) 释放时 `unlink` 锁文件 -> 「释放后锁文件仍在」用例变红；
  (n) 绝对路径闸移到 `open()` 之后 -> 「拒绝后无锁文件」用例变红；
  (o) 绝对性判据改为 `Path(p).expanduser().is_absolute()`（展开后再判，`~/yd.lock` 被判为绝对而放行）-> `~/yd.lock` 用例变红。**MUST NOT** 用「只查开头 `/`」当这条的变异体：POSIX 下 `os.path.isabs(s)` 对 str 就是 `s.startswith("/")`，二者对 `~/yd.lock` 同为 `False`，那是等价变异体；
  (p) 忽略 `FrontierDecision.stop_reason` 照常清理 -> 不可跑源用例变红；
  (q) 全新链的 T 取 `max(states)` 而非 `min(states)` -> 全新链用例变红；
  (r) 空目录不判为半成品（以「目录非空」为半成品判据）-> 空半成品目录用例变红；
  (s) 删除调用不带 `missing_ok` / 执行前不重新判定 -> 幂等用例第二次抛 `FileNotFoundError` 变红；
  (t) `DONE(retained)` 闸只留在半成品那一半（回到 round 1 前的形态）-> 「交来的 T 已有 `DONE`」用例变红；
  (u) `plan.yd_root` 由 `root` 改为 `root.parent` -> `containment_root` 判别器之一变红；
  (v) 状态文件删除调用**单独**去掉 `containment_root` -> `containment_root` 判别器之二变红（半成品那条臂不变，故必须两个判别器都在）；
  (w) 去掉 `Path(yd_root).resolve()` -> symlink 根用例变红；
  (x) 去掉 `source` 非空校验 -> `source=""` 用例变红；
  (y) 去掉半成品的 `S_ISDIR` 类型判据 -> symlink/普通文件/FIFO 三形态用例变红；
  (z) `plan_residue` 把 `DiscoveryUnreadableError` 吞成空清单而不抛 `ResidueError` -> `chmod 0o000` 用例变红；
  (aa) `except BlockingIOError` 放宽为 `except OSError` -> `PermissionError` 用例变红（该变异体在 round 1 实测存活，全套 993 绿）
  (ab) `source` 闸去掉 `.` / `..` 的显式点名（回到只有 `not source` 与单分量两项）-> `source=".."` 参数用例变红（`Path("..").name` 就是 `".."`，单分量判据放行它，清单随即是 `output/<T>/..`——整棵 `output/`；该变异体在 round 2 实测存活，全套 1003 绿）；
  (ac) 整道 `source` 闸退化成 `if not source:`（点名集与单分量两项一并去掉）-> `source` 参数用例的 `"."` / `".."` / `"a/b"` / `"ifs/"` 四腿变红（round 2 实测存活：此前该用例只跑 `""`，其余两项无判别器）；
  (ad) `safe_fs.remove_tree_allow_symlinks` 首行的 `_reject_unsafe_entry_name(name)` 删除（**变异只在 scratch 副本内做**，`store/safe_fs.py` 仓内零改动）-> `..` 条目名用例变红。这条登记的是**消费者侧依赖**：`..` 清单不会真删到 `output/` 是由该行独家承载的，而仓内此前无任何用例钉住它（round 2 实测：删掉该行后全套 1003 绿，且 `..` 清单会真的删掉另一源已提交的 `DONE` 产物）；
  (ae) `ResiduePlan.empty` 退化成 `return not self.state_files`（丢掉半成品那条臂）-> 半成品独臂清单用例变红（round 2 实测存活：既有断言用的树两臂要么同空、要么同非空；`empty` 是公开 API 且 13.2 只消费清单不执行，按它分支的调用方会静默跳过真实半成品）；
  (af) `execute_residue_plan` 的两个删除循环对调（先状态、后半成品）-> 执行序用例变红（round 2 实测存活。判别树：`states/<source>/<T+12>.cfg.ic` 是 symlink + 同时有半成品树；两种顺序都抛 `SafeFilesystemError`，但钉死的顺序在抛之前已把半成品删掉，对调后半成品每 tick 原地不动）
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check . && uv run ruff format --check .` -> 退出码 0
- `cd producer && uv sync --frozen` -> 退出码 0（不得新增依赖）
- `openspec validate m2-producer-core --strict --no-interactive` -> 退出码 0

Known limits（合并时按此验收，不得按「Scenario 全绿」验收）:
- **变异体 (z) 在 root 身份下不可杀**（round 2 核验裁为 DISCARD，记录而不改用例）：(z) 的唯一判别器 `test_unreadable_states_dir_raises_residue_error` 以 `_skip_if_root()` 自跳过——root 无视 mode 位，`chmod 0o000` 仍可枚举，用例在该身份下本就无判别力。故以 root 跑变异证明时 (z) 必然「存活」，那是身份造成的假阳性而不是覆盖缺口。CI 跑的是 GitHub 托管的 `ubuntu-latest`（非 root），(z) 在每个 PR 上都被杀死；本地以 root 复现变异批次时 MUST 换非 root 身份，MUST NOT 因此去掉那道 skip（去掉只会把用例变成恒真的空转）。
- spec 的 Scenario「崩溃残留恢复」后半句「以 T 状态重新组装本轮」依赖 `run_once`（任务 14.1，issue #26/#27）。本 issue 只能让**删除**半句变绿，重组半句以「清理后 `decide_frontier` 仍返回 T」这一发现层可证形式代替（见 Must-preserve）。该 Scenario 的完整验收归 14.1。

Non-goals:
- scratch `work/<source>/<T>` 的删除与孤儿 Slurm 作业存活确认（裁决 9）：归 #28；#59 的两条候选取舍不在本 issue
- 发布顺序、`DONE` 写入、`DONE` 成功后的旧状态清理（任务 13.1，issue #24）
- 14 天保留窗清理与 `realpath` 圈定 yd 根（任务 13.3，issue #25）：本 issue 的 containment 用 `safe_fs` 的 `containment_root`，不实现保留窗
- `run_once` 编排、把锁接进 `cli.py run`（任务 14.1）
- 状态读路径 stat->open 的 TOCTOU / FIFO 阻塞（issue #63）：本 issue 是该问题的**放大器**（卡死进程持锁 -> 后续 cycle 持续跳过），但加固的三处读路径均不在本 issue 的改动面；毗邻、已跟踪、刻意不动
- `run_dir` 符号链接祖先致零捕获（issue #77）：面在 checkpoint-tracker 接线，本 issue 只在**测试树**上按同一机制用 `tmp_path.resolve()`（裁决 6 末条），不改 `safe_fs` 也不改 tracker
- `cron.lock_path` 在 `config.py` 装载期的绝对性校验（裁决 8 明确不选）
- 真实 NFS/Slurm 行为、数值正确性：归 M4

Review focus:
- 12.2 的删除集合是否**严格**等于「更晚状态 + 无 `DONE` 的本源半成品目录」——多一类（work、`output/<cycle>/` 父目录、其它源、不可见条目）或少一类都是缺陷
- 「更晚」的边界方向：T 自己是否可能进入删除集合（任何 `>=`、任何以文件名字符串而非解析后 cycle 比较的写法都要当作缺陷查）
- 判定函数是否真的零写入（递归树快照是唯一判别条），13.2 是否真的能只复用判定
- symlink 两侧策略是否按裁决 6 落地且理由写进模块头；有没有出现 `shutil.rmtree` / 裸 `Path.unlink` / 缺 `containment_root`
- 跳过语义是否与「跑过了」可区分，跳过分支是否真的零副作用；异常路径是否仍释放锁
- 绝对路径闸是否真的先于任何文件系统副作用（看调用顺序，不看注释）
- 有没有越界落地 #24 的发布动作、#25 的保留清理或 #28 的 work 删除（含"顺手先放着"的死代码）

## 13. run-controller（二）：发布、失败与清理

- [x] 13.1 实现发布器：T+12 checkpoint 重戳到绝对 T+12（复用 4.3）→ DONE 前契约检查（v2、`forecast_days*24` 行、数据列数等于 `reach_count` 且等于变体 reach 数、T+12 可读、合并日志可用）→ DAT 原子 rename 为 `yd.rivqdown.dat` → 状态 rename → `DONE` 最后写 → 删旧状态只留两份 → 删本轮 work；正式文件不继承 scratch uid/gid/mode；记录型文件操作测试顺序与终名
- [ ] 13.2 实现失败处理（合并日志、删 work、不推进；复用 12.2 判定，仅接入失败/重跑路径）
- [ ] 13.3 实现 14 天保留清理（`realpath` 圈定 yd 根、symlink 越界拒删）

依赖：组 4（重戳）、组 12
§13.1 归属：发布（无 DONE 崩溃恢复/DONE 最后写/状态只留两份）
Suggested fixture level: expanded - 多状态目录树与记录型发布器
Minimal mergeable slice: 发布器（13.1）——发布顺序与契约检查对记录型文件操作独立可验证；失败与清理为后继

### Issue #24 fixture（任务 13.1：发布器）

Fixture level: expanded
Upstream suggested level: expanded（不覆盖：正面命中 `openspec/project-profile.md` 的 domain expanded-triggers `DONE`、`cycle`、`T+12`、`checkpoint`、状态链/重戳、NFS，且本 issue 是 M2 里**第一处向 `YD_ROOT` 提交正式产物**的代码）
Repair intensity: high（发布/删除/权限三面同时命中 Phase 0.5 step 3 的 high 判据：`DONE` 写早一步即让 viewer 读到半成品；旧状态删多一份即断链且不可逆；正式文件继承 scratch 0600 即让 node-27 读不到。适用 `Invariant Matrix`）
Project profile: yd-viewer

**上游契约偏离（consumed not renegotiated，须回流 stage-change-pipeline sizing-retro）**：

1. issue #24 的 `Depends on` 只列 #9 / #2，但验收标准里的「数据列数等于 `reach_count` 且等于**变体 reach 数**」需要一个「变体 reach 数」的来源符号，仓内不存在（`geometry.py:164` 逐字把「要素数是否符合业务预期」推给 prepare-variants，`src/` 全域无 `.riv` 解析）。缺失的 seam 本 issue **不自行补齐**，按裁决 1 收敛为调用方入参（`variant_reach_count: int`），并按核心规则「needed-but-missing seam is a reported deviation」记录在此；该入参的真实来源（prepare 侧变体 reach 计数）归 #20 / 14.1 接线。
2. `tasks.md:169` 把 `forecast_days` 与 `reach_count` 的**正数约束**逐字路由到「#24 task 13.1，DONE 前行数/列数校验」。本 fixture 消费该路由并按裁决 4 落地（期望行数与期望列数 MUST > 0，否则 `PublishError`），使该路由不再是孤儿。
3. `specs/run-controller/spec.md` 的「NFS 提交顺序与 DONE 语义」步骤 1 把重戳列在契约检查之前，issue 正文的箭头序同向；本 fixture 照此落地（裁决 2），并把「T+12 状态可按分段格式读取」这一检查明确定义为**对重戳后文档**的检查——否则该检查会放行一份重戳后才损坏的状态。
4. `docs/products-contract.md` §5.2 要求数据区第 0 列逐值为 `0, 60, …, 10020`，但 spec 与 issue 的 DONE 前契约检查清单**都不含**该项。本 issue 按 spec 与 issue 正文的清单落地，不擅自加检查项（加了会让「检查清单」在两份文档间分叉）；该缺口按「out-of-scope findings: report, don't fix」记入下方 Known limits 并路由。

**核心设计裁决（本 fixture 钉死，实现不得自行改写）**：

1. **输入面全部由调用方交来，发布器零发现、零推导**。落 `producer/src/yd_producer/publish.py` 新模块（`yd_producer.publish`，issue 正文的 Module/Scope）。入参以一个 frozen dataclass `PublishInputs` 表达：`yd_root`、`source`、`cycle`（待跑 T）、`scratch_dat`（作业产出的 DAT）、`scratch_checkpoint`（tracker 捕获的 T+12 checkpoint，**未重戳**）、`merged_log`（本轮合并 stdout/stderr）、`work_dir`（本轮 scratch `work/<source>/<T>`）、`expected_rows`（= `config.forecast_days * 24`）、`reach_count`（= `config.reach_count`）、`variant_reach_count`。**MUST NOT** 在发布器里读 `config.toml`、扫 `states/`、猜 T、或从 DAT 自己的列编号表反推「变体 reach 数」（后者是循环论证：那张表就是被校验的对象，只能做**内部一致性**校验，见裁决 4）。理由：本模块要在 14.1 之前独立可测，且 issue 的 PR Boundary 就是「publish 模块与记录型文件操作测试」。
   **`source` 与 `cycle` 的输入域 MUST 在入口 fail closed**（复用 #23 裁决 2 的同一条实测教训）：`source` 为空串、`.`、`..`、含 `/` 的任一形态 MUST 抛 `ValueError` 且零文件系统副作用——`output/<T>/""` 会塌回 `output/<T>/`，`..` 会把删除/写入面抬到另一源。
2. **执行序逐字钉死，且「检查」与「提交」严格分离**。公开两个符号：`check_publish_contract(inputs) -> None`（**零写入**，只读 scratch 侧）与 `publish(inputs) -> PublishResult`（先调前者，再按序提交）。`publish` 的序列 MUST 逐字是：
   1. 读 `scratch_checkpoint` -> `state.parse` -> `restamp_to_absolute_time(doc, T+12h)` -> 渲染出**内存中的**重戳字节（**MUST NOT** 回写 scratch 原文件：原文件是失败路径要回收的证据）；
   2. `check_publish_contract`（对 scratch DAT、上一步的重戳字节、`merged_log` 三者，外加 NFS 侧 `DONE(T)` 的**不存在**前置，见裁决 7）；
   3. DAT 写入 `output/<T>/<source>/` 的临时文件并在**同目录内**原子 rename 为 `yd.rivqdown.dat`；
   4. 重戳字节写入 `states/<source>/` 的临时文件并原子 rename 为 `<T+12>.cfg.ic`；
   5. `output/<T>/<source>/DONE` 以 `O_EXCL` 原子创建（最后写）；
   6. 删除 `states/<source>/` 下 cycle **严格早于** T 的合法状态文件（裁决 5）；
   7. 删除 `work/<source>/<T>`（裁决 6）。
   步骤 2 失败时 **NFS 侧零字节变更**（这是 fail-closed 的全部含义：`PublishError` 抛出后 `output/<T>/<source>/` 与 `states/<source>/` 的递归快照与调用前逐项相等，`DONE` 不存在，`work` **不删**——work 的失败侧回收归 13.2）。
3. **步骤 3–7 中途失败不回滚，按 §11.2 的恢复模型留半成品**。任一步抛错时 `publish` 让错误向外传播、**MUST NOT** 反手删除已 rename 的 DAT 或已写的状态，也 MUST NOT 补写 `DONE`。理由是硬的：`docs/compute-loop-design.md` §11.2 的恢复协议就是「无 `DONE` 即半成品，下次由 12.2 判定清理后整轮重跑」；发布器自己做补偿删除等于在崩溃恢复之外发明第二套协议，且它自己的补偿动作同样可能在中途死掉。**唯一例外是步骤 3/4 各自的临时文件**：`safe_fs` 的原子写原语在 rename 前失败时自行清理其 `.tmp`，这是原语的既有行为，不是发布器的补偿逻辑。
   **但「`DONE` 之前失败」与「`DONE` 之后失败」对调用方 MUST 可分辨**（fixture 复核 P2）：`DONE` 已写成之后（步骤 6/7）抛出的错误 MUST 携带一个显式标志，让 14.1 知道**本轮已完成**、MUST NOT 触发 13.2 的失败侧回收（那会删掉一份已被 `DONE` 承诺的产物的 work 证据，并把一个成功 cycle 记成失败）。落地形式钉死为**两个异常类型**：`PublishError`（`DONE` 之前，本轮未完成）与 `PublishCleanupError`（`DONE` 之后的旧状态/work 清理失败，本轮已完成，`.done_path` 指向已写成的 `DONE`）；后者 MUST NOT 是前者的子类——子类关系会让 14.1 的 `except PublishError` 把它一起吞成失败。
4. **契约检查的判据逐条钉死，全部 fail-closed，且全部在 scratch 侧完成**：
   - **期望值正数闸**（消费偏离 2 的路由）：`expected_rows <= 0`、`reach_count <= 0`、`variant_reach_count <= 0` 三者任一 -> `PublishError`。这条**先于**读文件，理由：`expected_rows == 0` 会让「行数相等」在一个空数据区上恒真。
   - **`reach_count == variant_reach_count`** 否则 `PublishError`（issue 正文的「且等于」是两两相等，不是二选一）。
   - **DAT 为 v2**：v2 布局按 `rSHUD/R/readout.R:26-31` 与 `SHUD/src/classes/Model_Control.cpp:254-259` 双向核对得到，逐字是 `[0:1024)` 文本头 + `st`(float64) + `nc`(float64) + `nc` 个列编号(float64) + 数据区，数据区每行 `nc+1` 个 float64。v2 判据 MUST 是**文本头形状**：`[0:1024)` 必须是「可打印 ASCII 前缀 + 其后全 NUL」（SHUD 侧 `char header[1024] = {}` + `strcpy` 的必然形态），任一不满足 -> `PublishError` 指名「非 v2」。**MUST NOT** 用「文件够大」或「`nc` 恰好等于 `reach_count`」当 v2 判据：v1 布局（`nc` 在 offset 0）在 `nc == 3988` 时前 8 字节是 `3988.0` 的 little-endian 表示，两者都放行它。
   - **列数**：`nc` MUST 是有限、整数值、且 `== reach_count`；列编号表 MUST 完整存在（`DAT_FIXED_HEADER_BYTES + 8*nc <= size`），否则 `PublishError`。
   - **行数**：数据区字节数 MUST 恰好等于 `expected_rows * (nc + 1) * 8`。**残行一律拒绝**（`docs/products-contract.md` §5.1 逐字「不规定残行修复」；`readout.R:41` 对残行只 `message` 不报错，那份宽容不得进入 producer 的写 `DONE` 闸）。多一行、少一行、多半行三种形态都 -> `PublishError`。
   - **T+12 状态可读**：对**重戳后**字节（裁决 2 步骤 1 的产物）`state.parse` 成功，且 `state_ic_structure_complete(payload, expected_river_count=reach_count)` 判为完整——**MUST 传权威计数**（round 1 verifier 裁定，cand-04 CONFIRMED/FIX_NOW）：不传时 `state_qc._check_row_counts` 对每一类都 `if expected is None: continue`，唯一还生效的结构闸只剩「分段存在」，于是一份 river 段被截断的 checkpoint（tracker 在 SHUD 非原子改写 `cfg.ic.update` 期间捕获，正是 `state_qc.py:474-481` docstring 点名的形态）照样拿到 `DONE`，下一轮从中毒 IC 起跑且下游无人复检（`residue.plan_residue` 在 `DONE(T)` 存在时清单整体为空）。本条按治理不变量（「`DONE` 一旦存在，状态就已是**完整**的正式产物」）而非本裁决的原措辞裁定：`tasks.md:806`/`:840` 与 `state_qc.py:399` 两处独立锚点都**指名**把权威 `reach_count` 接进来是 #21 init / **#24 发布器**的领域，而本 fixture 的 Known limits 从未记录放弃这条路由——故原先的不传计数是**未记录的偏离**，不是被钉死的决定。计数取 `PublishInputs.reach_count`（`_check_positive_expectations` 已强制它等于 `variant_reach_count`，故 #20 变体计数的来源歧义在本调用点是 moot 的）；且 header 的绝对时间 MUST 对应 T+12——判据 MUST 与 `controller._classify_state` 逐字同构（`round(cfg_ic_header_minute_time(tokens)) == round((T+12).timestamp()/60)`），**MUST NOT** 接受相对分钟。这条是本 fixture 的治理不变量在发布侧的自闭合：写出去的那份状态，正是下一轮前沿闸门要读的那份。
   - **合并日志可用**：`merged_log` MUST 存在、是普通文件、非空（`st_size > 0`）。目录、FIFO、symlink、零字节四种形态都 -> `PublishError`。理由：失败时要回收的就是它，一份 0 字节日志等于没有。
   - 全部检查 MUST 在**第一处 NFS 写入之前**跑完，`check_publish_contract` 自身 MUST 零写入（递归快照证明）。
5. **旧状态删除集合：cycle 严格早于 T 的合法状态文件，且仅此**。可见性判据（10 位数字、`%Y%m%d%H` 可解析、`cycle+12h` 不溢出、`.cfg.ic` 后缀）MUST 从 `controller` 导入既有符号（`visible_state_cycles` / `parse_cycle_id`），**MUST NOT** 重写一份。边界方向：`== T` 与 `== T+12` 永不删（spec 步骤 5 的「最终保留 T 与 T+12 两份」正是这条的直接后果）；`> T+12` 的更晚状态**也不删**（那是 12.2 的残留集合，发布器越界删它等于把两处删除面耦合起来）。删除原语用 `unlink_no_follow(path, containment_root=Path(yd_root).resolve())`：状态文件只由本发布器以「普通文件原子 rename」写入，遇 symlink 即抛 `SafeFilesystemError`（与 #23 裁决 6 的状态侧策略逐字一致）。`containment_root` MUST 是 `Path(yd_root).resolve()`——#23 裁决 6 增补的前置条件（`safe_fs.py:824-843` 会把 root 自身逐分量过 `O_NOFOLLOW`），该前置 MUST 写进 `publish.py` 模块头。
   **且「入口 resolve 一次，全部 NFS 目标路径由该值派生」**（fixture 复核 P2）：`safe_fs._relative_parts_under_root`（`safe_fs.py:944-960`）是**纯词法** `relative_to`，只 `_expand_path` 不 `resolve`；若 `containment_root` 传 resolve 后的值而目标路径仍由未 resolve 的 `yd_root` 拼出，一个含 symlink 分量的 `yd_root` 会让每一次 unlink/写入都在 containment 处 fail closed。故 `PublishInputs` 的入口 MUST 计算一次 `root = Path(yd_root).resolve()`，`output/`、`states/` 两棵子树的**全部**路径由 `root` 派生，MUST NOT 再出现第二个 `yd_root` 的用法。测试种子 `tmp_path.resolve()` 对这一点**没有判别力**，故另需一条以 symlink 形态 `yd_root` 传入的正例（见 Required evidence）。
6. **work 删除用 `remove_tree_allow_symlinks`，`containment_root` 是 scratch work 根而不是 `YD_ROOT`**。理由两条：其一，`work/<source>/<T>` 在 scratch 上，不在 `YD_ROOT` 内，传 `YD_ROOT` 会被 `safe_fs` 直接拒；其二，该树的内容按构造不可信（作业自己写的 raw 副本/canonical/forcing/registry，可能含 symlink），拒 symlink 会 permanently lock 住每一轮成功发布——这正是 `remove_tree_allow_symlinks` docstring 描述的场景，与 #23 裁决 6 的半成品侧策略同向。`work_dir` 的父链 MUST 由调用方交来一个显式的 `work_root`（`PublishInputs.work_root`），发布器 MUST NOT 用 `work_dir.parent.parent` 反推——反推在 `work_dir` 被构造成 `.../work/ifs/T/..` 时会把 containment 抬到任意高度。
7. **`DONE` 双闸：前置不存在 + `O_EXCL` 创建**。`products-contract.md` §4.4 逐字「重复运行看到 `DONE` 时视为已完成，不覆盖正式产物」。故：契约检查阶段 stat `output/<T>/<source>/DONE`，存在（任何类型）即 `PublishError` 且零 NFS 写入；步骤 5 仍以 `O_EXCL` 创建，作为「检查到创建」之间的竞态兜底，`FileExistsError` MUST 收敛为 `PublishError` 而不是穿透。两道闸都要，缺任一条都有判别器（见 Required evidence）。**前置探测的实现 MUST 让 symlink 形态也收敛为 `PublishError`**（fixture 复核 Note）：`stat_no_follow` 对 symlink 抛的是 `SafeFilesystemError` 而不是「存在」，该异常 MUST 被捕获并收敛，MUST NOT 穿透。
8. **正式文件按发布权限创建，不继承 scratch 的 uid/gid/mode**（`docs/agent-ops.md` §10「不用 `cp -a` 把计算节点 uid/gid/模式带入 NFS；由控制器按发布权限创建」）。模块常量 `PUBLISH_FILE_MODE = 0o644`（node-27 以 `nwm` 身份只需读；§10 的「共享组 + setgid」是现场目录策略，不由本模块设置 gid）。落地方式 MUST 是**读字节 -> 新建文件写入**：DAT 与状态都走 `atomic_write_bytes_no_follow(..., mode=PUBLISH_FILE_MODE)`（该原语在 `os.open` 之后额外 `fchmod`，故落地位不受 umask 削弱）。**MUST NOT** 用 `shutil.copy2` / `copystat` / `os.link` / `Path.rename` 跨设备搬运——前两者的全部作用就是把源的 mode/时间带过去，后两者会把 scratch inode（连同其 uid/gid/mode）直接接进 NFS。目录 `output/<T>/<source>/` 用 `ensure_directory_no_follow` 创建，**随后 MUST 显式放宽到 `PUBLISH_DIR_MODE = 0o755`**（fixture 复核 P1）：该原语逐字拒绝 `fchmod`（`safe_fs.py:107-131`：「the umask may further restrict a safe_fs directory, it may never loosen it」），故在 umask 0o077 的现场它落地即 0o700，node-27 连**穿越**都做不到——文件位设成 0o644 也白设。放宽方式 MUST 是 fd 绑定的：`fd = open_directory_no_follow(dir, containment_root=root)` 后 `os.fchmod(fd, PUBLISH_DIR_MODE)`，且该 fd MUST 在 `try/finally` 里 `os.close`（原语交回的是裸 fd，不是上下文管理器）；正是 `safe_fs.py:129-131` 注释里点名的「caller needing cross-uid access has to widen after creation」这一既定模式；MUST NOT 用跟随 symlink 的 `<dir>.chmod(...)`。
   **放宽面 = 本次发布在 `output` 子树上自建的每一级目录，含 `output/` 自身**（fixture 复核 round 2 的 P1）：`output/` 这一级在全新根上同样由本发布器补建——`atomic_write_bytes_no_follow(..., create=True)` 经 `ensure_directory_no_follow` 逐分量 `mkdir`，而 `tasks.md` 全文无任何上游任务负责创建它（11.1 的 init 只写 `states/`）。umask 0o077 下漏掉它，node-27 连 `output/` 都穿不进去，下面两级的 0o755 全部白设。故放宽序列逐字是 `output/` -> `output/<T>/` -> `output/<T>/<source>/` 三级，**逐级、非递归**：MUST NOT 递归 walk 已存在的历史 cycle 目录，MUST NOT 触碰 `states/`、`logs/` 或 `YD_ROOT` 自身（agent-ops §10：`a+rX` 只作用于发布目录，不递归开放模型、状态和日志）。**MUST 先 stat 再决定**（round 1 cand-02 CONFIRMED/FIX_NOW，实测 `output/` 预置为 `0o2750` 时被改成 `0o755`）：三级各自在 `ensure_directory_no_follow` **之前** stat，只对**本次调用之前不存在**的层级 `fchmod`；已存在的层级一律不动。原措辞「对已存在且 mode 已合规的目录重复 `fchmod` 是幂等的」只在「已合规」这个前提成立时才成立，而现场按 `docs/agent-ops.md` §10 的**首选**做法把 `output/` 设成共享组 + setgid（如 `2750`）时前提不成立——`fchmod` 可观测地**改变**了 mode，且清掉 setgid 不是「放宽」而是收紧。此外任何确实要做的 `fchmod` MUST **保留高位**（`S_ISGID`/sticky）：`chmod` 写的是整个 mode 字，直接写 `0o755` 会让其后在该目录下新建的每一个条目都不再继承共享 gid，运维手动 `chmod g+s` 的补救每轮都被抹掉。
   **且「只放宽自建层级」MUST 配一道 fail-closed 的可穿越后置断言**（round 2 cand-02 CONFIRMED/FIX_NOW/P1，verifier 实测两条探针）：「本次调用之前不存在」不是可持久化的属性——三级 stat 完成到 `fchmod` 循环跑完之间任何一次失败（NFS EIO/ESTALE，或 SIGKILL/节点重启），已 `mkdir` 的层级就以 umask 0o077 下的 `0o700` **永久闩死**，因为其后每一轮都把它看作「已存在」而不动；实测「首轮 widen 抛 EIO -> 次轮干净重跑」得到 `DONE=True` 且三级全是 `0o700`，实测「预置 `0o700` 的 `output/`」得到 `DONE=True` 且 `output/` 仍是 `0o700`。canonical 恢复路径救不回来：`residue._half_product_dirs`（`residue.py:296-311`）只删 `output/<T>/<source>/`，从不碰它的父级，故重跑只重建并放宽叶子，`output/` 与 `output/<T>/` 照旧 `0o700`——而任一父级不可穿越就等于 node-27 什么都看不到，同时状态链照常推进、无任何信号。这直接违反治理不变量的「node-27 **可读**」半边，literal 合规于本裁决前半段不构成豁免。故 MUST 逐级复 stat 并施加下述判据，任一级不满足即在**第一处 NFS 写入之前**抛 `PublishError` 指名该层级与其 mode。
   **判据 MUST 由消费者契约推导，MUST NOT 再写一个掩码字面值**（round 3 cand-01 CONFIRMED/FIX_NOW/P2，以及本 PR 的 Review Failure Retro 认定的根因）。权威出处是 `docs/products-contract.md` §8：node-27 的 `nwm` 「只需对 `input/viewer` 和 `output` 有目录**遍历与读取**权限」——**遍历与读取两个词都在**，`docs/agent-ops.md` §10 第二条同样并列写了「`output` 的**读**/遍历权限」。故判据逐字是「**组或其他之中，至少有一类同时具备 `r` 与 `x`**」：
   ```python
   (mode & 0o050) == 0o050 or (mode & 0o005) == 0o005
   ```
   owner 位不得计入：发布进程自己永远进得去，算上它这条断言即恒真（变异体 (aq)）。
   本条前后共写坏两次，两次都是**拿上一轮的反例去调掩码字面值**而不是回头读契约，故此处把推导链写死以免第四次：初稿的 `& 0o055` 放行 `0o744`（能列名字、进不去）；round 2 修复轮改成的 `& 0o011` 放行 `0o711`/`0o710`/`0o701`（进得去、列不出名字），是同一个洞的镜像——而 `output/` 恰恰是 viewer 必须 `readdir` 才能枚举 cycle 的那一级（`products-contract.md` §7.1 逐字「锚点是最新 `DONE` cycle，不是墙钟」，§7.3 要求算停后仍可显示，两条都堵死了按墙钟猜候选路径的退路）。r+x 判据同时拒掉这两组，且经 verifier 实测不拒任何一种有文档依据的现场配置：§10 首选的共享组 + setgid `0o2750` 通过（`& 0o050 == 0o050`），`a+rX` 得到的 `0o755` 通过。高位不受影响——两个掩码都在低 9 位，`S_ISGID`/sticky 经 `stat.S_IMODE` 原样穿过。**且断言 MUST 跑两趟**：已存在的层级在 `ensure_directory_no_follow` **之前**先断言一次，三级全部创建并放宽之后再整体断言一次。只留后置那趟不可满足——一个 `0o700` 的 `output/` 会先让下面两级被 `mkdir` 出来，与证据行「自建层级不可穿越即拒」要求的「`YD_ROOT` 递归快照逐项不变」直接冲突。两趟 MUST 共用同一个判据函数，避免两处实现漂移。该后置断言与本裁决前半段无冲突：现场按 §10 首选设的 `0o2750` 满足 `(mode & 0o050) == 0o050` 而原样通过，被闩死的 `0o700` 则以 pre-`DONE` 的响亮失败暴露，而不是封一个不可读的 `DONE`。**MUST NOT** 改成「发现不可穿越就放宽已存在的层级」——那会把 cand-02 原样放回来（现场的 `2750` 会被改写）。
9. **`DONE` 的 mode 需要一处 `safe_fs` 扩展，且是本 issue 唯一的共享 helper 改动**。`write_bytes_no_follow_exclusive` 当前以硬编码 `0o666` 打开、无 `fchmod`，落地位是 `0o666 & ~umask`——在 umask 0077 的现场会得到 0600 的 `DONE`，node-27 读不到，直接违反裁决 8 与 §10。故给它加一个 `mode: int | None = None` 关键字参数，语义**逐字镜像** `atomic_write_bytes_no_follow`（`os.open` 传 mode，随后 `fchmod` 以抵消 umask）；`None` 时行为与今日**逐字节相同**，既有调用方与 `test_safe_fs*.py` 零改动。MUST NOT 在 `publish.py` 里自己 `os.open(O_EXCL)` 绕开 `safe_fs`（会丢掉父目录的 `O_NOFOLLOW` 锚定与 `containment_root`），也 MUST NOT 改 `atomic_write_bytes_no_follow` 去支持 `O_EXCL`（它的语义是 replace，掺进 no-clobber 会让既有调用方的失败模式漂移）。
10. **顺序可观测性的 seam 是 `safe_fs` 调用边界，用 monkeypatch 录制，零生产面**。spec Scenario「提交顺序可观测」要求「以可记录文件系统操作的发布器完成一轮成功发布」。落地方式：测试侧 monkeypatch `yd_producer.publish` 模块内绑定的 `safe_fs` 函数名，包一层记录器后转调真实实现（真实文件系统动作照常发生，录的是调用序与终名）。**MUST NOT** 为此在生产代码里加 recorder 参数、hook 列表或事件回调——那是把测试脚手架焊进发布路径。断言的是**终名序**：`yd.rivqdown.dat` 的 rename 早于 `<T+12>.cfg.ic` 的 rename，`DONE` 的创建晚于两者，旧状态 unlink 晚于 `DONE`，work 删除最末。
11. **uid/gid 的可测边界**：非 root 身份下测试无法制造跨 uid/gid 的源文件，故「不继承 uid/gid」由**结构**满足（裁决 8 的「新建写入，禁 `copy2`/`copystat`/`link`」）并由一条源码机检钉住（`publish.py` 文本中不出现 `copy2`/`copystat`/`os.link`/`shutil`）；可断言的行为面是 **mode 不继承**：scratch DAT 与 checkpoint 置 0600 -> NFS 侧三份产物 mode 均为 0o644。另 MUST 在一个显式 `os.umask(0o077)` 的用例里重跑该断言——不设这条，`fchmod` 与「裸 `os.open(mode)`」两种写法在默认 umask 022 下不可分辨。
12. **零新增依赖**：`struct`/`os`/`pathlib`/`datetime` 全在 stdlib，`numpy` **不引入**（列数/行数校验是整除与相等判定，读的是定长 float64 头部与文件大小，不需要把 168×3989 的数据区读进内存）。**有界读的 MUST 只约束契约检查阶段**（fixture 复核 P1：与裁决 8 的 `atomic_write_bytes_no_follow(content: bytes)` 曾表面冲突，此处划清）：`check_publish_contract` 读 DAT 时 MUST 只取 `[0, DAT_FIXED_HEADER_BYTES + 8*nc)` 这段头部（两趟：先以模块常量 `DAT_FIXED_HEADER_BYTES = 1040`（= 1024 文本头 + `st` + `nc` 两个 float64）读出 `nc`，再读列编号表），原语用 `read_bytes_limited_no_follow`，文件大小走 `stat_no_follow`，行数由 `st_size` 算术得出，MUST NOT 在检查阶段把数据区读进内存——`expected_rows` 是配置驱动的，检查阶段的无界读会把一处配置错误放大成 OOM，而检查的全部目的正是挡住这类输入。**步骤 3 的复制读全量字节是允许且必需的**（`safe_fs` 无 fd 流式写原语，`atomic_write_bytes_no_follow` 只收 `bytes`），其上界已由前置契约检查钉死的 `st_size == DAT_FIXED_HEADER_BYTES + 8*nc + expected_rows*(nc+1)*8` 约束——即「先证明大小合法，再整读」，顺序不得颠倒。**且整读之后 MUST 复核长度**（round 1 cand-08 PLAUSIBLE/FIX_NOW）：`_check_dat` MUST 把 `expected_size` 交回，`_publish_dat` MUST 断言 `len(payload) == expected_size` 后才写，否则 `DONE` 会封住一份**从未被校验过**的字节——校验读的是发布前那一刻的 `st_size`，整读是另一次独立的 open，两者之间 scratch 上若有滞留/重投的作业写入（裁决 6 自己把 scratch 树称作「按构造不可信」，且没有任何 spec 条款保证 scratch 静默），落地的就是一份带半行尾巴的 DAT。这条**不是**裁决 12 字面顺序的违反（顺序是遵守的），被违反的是本模块自己写下的那句「整读的上界已由 `_check_dat` 钉死的 `st_size` 等式约束」前提。零额外 IO。
14. **scratch 侧只有一条 symlink 策略，且 `work_root` 与 `work_dir` MUST 一起 resolve**（round 1 cand-05 与 cand-06 双双 CONFIRMED/FIX_NOW；两者是**相反极性**的同一处失配，必须一并收口）。当前状态是三种策略并存：NFS 根入口 resolve 一次；scratch DAT / 日志 / work 严格 no-follow（`containment_root=None` 时 `safe_fs._anchor_for` 从 `/` 起把**每一个**祖先分量过 `O_NOFOLLOW`，故 scratch 路径上任何一节 symlink 都致命——实测 `/scratch -> /mnt/scratch` 这类布局下每轮 pre-`DONE` 失败，而只有 work 一条腿走 symlink 时更糟：`DONE`/DAT/状态全部正常落地，随后步骤 7 抛 `PublishCleanupError`，于是**每一个成功轮**都以清理错误收尾并留下无人回收的孤儿 work）；而 checkpoint 经 `state.parse(Path)` 走 `cfg_ic.py:504-513` 的裸 `open()`，**跟随** symlink——实测把 `scratch_checkpoint` 换成指向 scratch 树外的 symlink，那份外来文件会被重戳后发布成正式的 `{T+12}.cfg.ic`，而同样构造在 `scratch_dat` 上被拒。
    落地要求：(a) checkpoint MUST 改为 no-follow 有界读后再解析（`parse(read_bytes_limited_no_follow(checkpoint, max_bytes=MAX_STATE_IC_BYTES))`，`parse` 的 `bytes` 分支保留尺寸闸）——它是唯一会变成正式 NFS 产物的 scratch 输入，却是唯一没有 no-follow 保护的读；(b) scratch 侧路径的策略（入口 resolve，还是要求调用方交已 resolve 的路径）MUST 二选一并写进模块头与 `PublishInputs` 的字段 docstring，参照姊妹模块 `residue.py:69-76,227` 的既有写法；(c) 若选入口 resolve，`work_root` 与 `work_dir` MUST **一起** resolve——`safe_fs._relative_parts_under_root`（`:944-960`）是纯词法 `relative_to`，只 resolve 其中一个会让 containment 判定当场断裂，制造出一个每轮必现的新 `PublishCleanupError`。`cfg_ic.py:305-310` 那条「刻意宽容」注释不构成反驳：它讲的是快照层可信 staged 文件与 symlink **祖先**目录（macOS `/tmp`），没有覆盖一个逃出 scratch 树的 symlink **叶子**。
15. **不接线 `run` CLI、不碰 `controller.py` / `residue.py`**。本 issue 交付一个纯被调用的发布器；`cli.py` 的 `run` 仍是 `_unimplemented`（接线归 14.1）。

Invariant Matrix
Governing invariant: `DONE(T)` 一旦存在，`output/<T>/<source>/yd.rivqdown.dat` 与 `states/<source>/<T+12>.cfg.ic` 就已是完整、合约达标、node-27 可读的正式产物，且 `states/<source>/<T>.cfg.ic` 与 `<T+12>.cfg.ic` 两份俱在——即「`DONE` 之前无正式承诺，`DONE` 之后无删除本轮所需状态」。
Source-of-truth identity/contract: `output/<T>/<source>/DONE` 这一空普通文件（`products-contract.md` §4：唯一完成判据），及其守护的二元组「v2 DAT（`expected_rows` 行 × `reach_count` 列）+ 时间头对应绝对 T+12 的 `cfg.ic`」。
Surfaces:
- Producers: `publish.publish` 的步骤 3/4/5（`atomic_write_bytes_no_follow` × 2、`write_bytes_no_follow_exclusive` × 1）
- Validators/preflight: `publish.check_publish_contract`（v2/行/列/状态可读/日志可用/`DONE` 不存在/期望值正数）
- Storage/cache/query: `output/<T>/<source>/`、`states/<source>/`（NFS 侧）；`work/<source>/<T>`（scratch 侧）
- Public routes/entrypoints: `yd_producer.publish` 的 `PublishInputs` / `PublishResult` / `check_publish_contract` / `publish` / `PublishError` / `PublishCleanupError`；`cli.py` **不在本 issue 内**（14.1）
- Frontend/downstream consumers: 下一轮 `controller.decide_frontier`（读 `DONE` 与 `states/<source>/<T+12>.cfg.ic` 的绝对时间头）；`residue.plan_residue`（以「无 `DONE`」判半成品）；viewer（只枚举带 `DONE` 的 source 目录）
- Failure paths/rollback/stale state: 契约检查失败（零 NFS 变更，`PublishError`）；步骤 3–5 中途失败（pre-`DONE`：留无 `DONE` 半成品，`PublishError`，交 12.2 判定清理后整轮重跑）；**步骤 6/7 失败（post-`DONE`：本轮已完成，`PublishCleanupError`，MUST NOT 触发 13.2/12.2 的失败侧回收）。残留归属逐条点名（fixture 复核 round 2 更正）：未删净的**旧状态**由**下一轮发布的步骤 6** 收（裁决 5 的「严格早于 T'」自然覆盖它），**不是** 12.2——`residue.plan_residue` 在 `DONE(T)` 存在时清单整体为空（`residue.py:227-237`）且 `_later_state_files` 只取严格晚于 T 的状态（`:272-286`）；孤儿 `work/<source>/<T>` **当前无归属**，见 Known limits**；`DONE` 已存在（拒绝且零变更，`PublishError`）
- Evidence/audit/readiness: `merged_log` 的可用性检查；`PublishResult` 交回的终名路径集合（供 14.1 写运行报告）
Error-domain invariant（round 1 pattern escalation 追加）: `check_publish_contract` 与 `publish` 的公共边界上，**每一个**逃出的异常 MUST 恰好是 `PublishError`（本轮未完成）或 `PublishCleanupError`（本轮已完成）；且「已完成」的判据是 **`DONE` 在盘上存在**，不是「`_create_done` 返回了」。根因是一条可复用的错误假设：本模块曾假定 `safe_fs` 把一切失败都包成 `SafeFilesystemError`，而实际上 `open_file_no_follow` 对非 `ELOOP` 的 `OSError` 与 `FileNotFoundError` 是**裸抛**（`safe_fs.py:340-341,349-355`），`stat_no_follow` 对 symlink 抛 `SafeFilesystemError`，`controller.DiscoveryUnreadableError` 根本不是 `OSError` 的子类。该不变量在 round 1 于四个独立点同时失守（cand-01 `_create_done`、cand-03 两处检查期有界读、cand-10 `merged_log` symlink 臂、cand-09 步骤 6 的 `DiscoveryUnreadableError` 臂），故按 high 强度触发 pattern escalation，修复 MUST 是跨切面收口而非逐行打补丁。
**round 2 该类复发，追加一条极性 MUST（cand-01 CONFIRMED/FIX_NOW）**：「`DONE` 在盘上存在」这一判据的**复探原语 MUST 是裸 `os.lstat(done_path)`——成功即 `True`，任何 `OSError` 即 `False`**，与姊妹模块 `controller.done_cycles`（`controller.py:308-317`）、`residue._half_product_dirs`（`residue.py:302-309`）同一高度。根因与 round 1 是**同一条**可复用错误假设的反面：round 1 假定「`safe_fs` 把一切失败都包成 `SafeFilesystemError`」，round 2 的复探则假定「抛出 `SafeFilesystemError` 就意味着条目存在」——同样是假的。`stat_no_follow` 把 EACCES/EIO/ESTALE 一律包成 `SafeFilesystemError(kind="io")`（`safe_fs.py:369-397`），`_open_child_dir` 把父链上的一切非 `FileNotFoundError` 失败包成 `SafeFilesystemError`（`:795-811`），于是 `except SafeFilesystemError: return True` 把「测不出来」翻译成「本轮已完成」，而 `except OSError: return False` 那条臂实际是死代码。verifier 实测：`chmod 0o000` 于 `output/<T>/<source>/` 且 `DONE` 不存在 -> 复探返回 `True` -> 公共边界抛 `PublishCleanupError`（本轮已完成）。**按 `kind` 分支（`unsafe` -> True，`io` -> False）已被实测证伪**：父级被换成普通文件时 `kind` 是默认的 `unsafe`，而 `DONE` 按构造不可能存在，仍得到错误的 `True`。裸 `lstat` 同时保住 docstring 要求的「任何条目（含 symlink、目录）都算在盘」。该缺陷的下游后果按 verifier 实测**止于误报**而非丢轮：`decide_frontier` 只认 `DONE`（`controller.py:200-217`，两条分支都回到 T，且已 rename 的 `<T+12>` 进的是 `min` 不是 `max`），`plan_residue` 照常把半成品与更晚状态判入清单，下一轮干净重跑——故定级 P2 而非 P1。
Regression rows:
- 公共边界的任一失败输入 -> 只抛两个声明类型之一，绝不逃出裸 `OSError`/`SafeFilesystemError`/`ValueError`/`DiscoveryUnreadableError`
- **本轮自建的** `DONE` 已在盘 + 其后任一步失败 -> `PublishCleanupError` 且带正确的 `done_path`。**「本轮自建的」这个限定不可省**（round 3 cand-04 CONFIRMED/FIX_NOW）：裁决 7 对**外来 writer** 在契约检查之后抢先创建 `DONE` 的那条臂钉的是相反结论（`FileExistsError` MUST 收敛为 `PublishError`），而那条臂里「`DONE` 在盘」与「某一步失败」两个前件同时成立，按原措辞两行对同一个状态要求相反的异常类型。实现遵循裁决 7 且是对的：把竞争者的 `DONE` 当成 `PublishCleanupError`，等于告诉 14.1 一轮它从未完成的作业成功了，而步骤 6 的旧状态清理根本没跑、`work` 也不会被回收。反向也安全——报 `PublishError` 时 `residue.plan_residue` 在 `DONE(T)` 存在的前提下清单整体为空（`residue.py:227-237`），竞争者的产物不会被毁
- `DONE` 未在盘 + 任一步失败 -> `PublishError`，`work_dir` 仍在；**「`YD_ROOT` 递归快照不变」这一子句只对契约检查阶段的失败成立**（round 2 cand-04b CONFIRMED/FIX_NOW）：步骤 4 失败时 DAT 已 rename 且按裁决 3 **刻意保留**（见下方步骤 4 那行），此时快照必然已变。原措辞把上一行只适用于检查阶段的子句泛化到「任一步失败」，照字面实现会去补一段回滚——正是裁决 3 明令禁止、变异体 (v) 专门要杀的东西
- 合法一轮（scratch DAT 168×3988、checkpoint header 相对 720 分钟、非空日志）-> 五个终名按序落地，`DONE` 最后，`states/` 只剩 T 与 T+12，`work/<source>/<T>` 不存在，三份 NFS 文件 mode 0o644
- 发布后对同一棵树调用 `controller.decide_frontier` -> 返回 T+12 且无 `stop_reason`（治理不变量的端到端判别器：写出去的状态正是下一轮读的那份）
- 行数少一行 / 多半行 / `nc != reach_count` / v1 布局 / 日志 0 字节 / `merged_log` 是目录 -> 各自 `PublishError`，且 `output/<T>/<source>/` 与 `states/<source>/` 递归快照与调用前逐项相等（`DONE` 不存在、DAT 不存在、`work` 仍在）
- `DONE(T)` 已存在 -> `PublishError`，既有 `DONE` 与 `yd.rivqdown.dat` 字节不变
- 步骤 4 失败（`states/<source>/<T+12>.cfg.ic` 位置预置为 symlink）-> 抛错，DAT 已 rename 且**保留**，`DONE` 不存在，`work` 仍在；随后 `residue.plan_residue` 把该半成品判入清单（与 12.2 的接缝对得上）
- 未改动的姊妹消费者：`controller.decide_frontier` 与 `residue.plan_residue` 全套既有用例逐字通过；`store/safe_fs.py` 既有调用方（`mode=None` 默认路径）行为逐字节不变

Boundary-surface checklist（high 强度必需）:
- 共享 helper 根：`store/safe_fs.py` —— **有改动**，仅 `write_bytes_no_follow_exclusive` 新增可选 `mode`（裁决 9）；`state/*`、`controller.py`、`residue.py` —— 零改动，只作为消费者导入
- 公共入口：`yd_producer.publish` 六个符号（`PublishInputs`、`PublishResult`、`check_publish_contract`、`publish`、`PublishError`、`PublishCleanupError`）；`cli.py` 不动
- 读面：scratch DAT（有界读头部）、scratch checkpoint（`state.parse`）、`merged_log`（只 stat）、NFS `DONE` 前置探测
- 写/删/覆盖面：`output/<T>/<source>/{.tmp, yd.rivqdown.dat, DONE}`、`states/<source>/{.tmp, <T+12>.cfg.ic}`、旧状态 unlink、`work/<source>/<T>` 整树；`output/`、`output/<T>/`、`output/<T>/<source>/` **三级**目录的创建与 mode 放宽（裁决 8）
- staging/publish/rollback 面：同目录临时文件 + 原子 rename；无回滚（裁决 3）
- producer/consumer 证据边界：`DONE` ↔ `decide_frontier` / `plan_residue` / viewer
- 陈旧态/幂等边界：`DONE` 已存在即拒（裁决 7）；同一 `PublishInputs` 第二次调用 MUST 稳定拒绝而不是二次提交
- 未改动的下游消费者：`controller`、`residue`、`geometry`、viewer 侧读路径

Must-preserve behavior:
- `store/safe_fs.py` 现有全部导出的行为在 `mode` 缺省时逐字节不变；`producer/tests/test_safe_fs.py` / `test_safe_fs_refusals.py` 零断言改动
- `controller.py`、`residue.py`、`state/**` 零改动，其全套既有用例通过
- 「前沿只由 `DONE` 推进」：发布器 MUST NOT 写除上述五个终名之外的任何 NFS 路径（尤其不写 `status.json` / `meta.json`，`products-contract.md` §4.5）
- `states/<source>/<T>.cfg.ic`（本轮起跑状态）在任何路径上都不被删除
- 已带 `DONE` 的历史 `output/<cycle>/<source>/` 在任何路径上都不被读改删

Seams under test:
- 目录树 fixture（`tmp_path.resolve()` 下的合成 `YD_ROOT` + 独立 scratch 根；`resolve()` 的理由同 #23 裁决 6 末条：macOS `/var -> /private/var` 会让 `containment_root` 的逐分量 `O_NOFOLLOW` 锚定失败）
- 合成 v2 DAT 构造器（`producer/tests/` 新增 fixture helper：给定 `nc`/`rows`/header 文本产出字节；v1 与残行两种反例由同一构造器的参数产出）
- checkpoint `cfg.ic` 复用 `producer/tests/cfg_ic_fixtures.py` 既有构造器，不新造第二份
- 顺序录制：monkeypatch `publish` 模块内的 `safe_fs` 绑定名（裁决 10），无注入式生产参数
- 时间/cycle：直接构造 `datetime`，不注入时钟

Risk packs considered (core):
- Public API / CLI / script entry: selected —— 新增 `yd_producer.publish` 公共面；`cli.py` 不动
- File IO / path safety / overwrite: selected —— 本 issue 的主面（原子 rename、`O_EXCL`、symlink 拒绝、containment、整树删除）
- Schema / columns / units / field names: selected —— v2 DAT 布局与列数/行数即 schema 判定
- Auth / permissions / secrets: selected —— 发布权限位与「不继承 scratch uid/gid/mode」
- Error handling / rollback / partial outputs: selected —— fail-closed 检查与「不回滚、留半成品」的恢复模型
- Concurrency / shared state / ordering: selected —— 提交顺序是 spec 的核心 Requirement；`DONE` 检查到创建的竞态
- Resource limits / large input / discovery: selected —— DAT 有界读（裁决 12）
- Legacy compatibility / examples: not selected —— `products-contract.md` §5.1 逐字「不要求兼容 v1」；v1 只作为**被拒绝**的反例出现
- Config / project setup: not selected —— 发布器不读 `config.toml`（裁决 1），配置校验归 #2/#32
- Release / packaging / dependency compatibility: not selected —— 零新增依赖（裁决 12）
- Documentation / migration notes: not selected —— 无对外文档契约变化（spec 既有 Requirement 已覆盖本 issue 全部七类 Scenario，故本 issue **无 spec delta**）

Required evidence（每条一个用例，`producer/tests/test_publish.py`）:
- **顺序可观测**（spec Scenario 逐字）：一轮成功发布 -> 录得的终名序为 `yd.rivqdown.dat` rename < `<T+12>.cfg.ic` rename < `DONE` 创建 < 旧状态 unlink < `work` 删除；每对相邻关系各自断言（合成一条「序列相等」断言会在只错一处时给不出定位）
- **checkpoint 发布前定戳**（spec Scenario 逐字）：checkpoint header 为相对 720 分钟 -> 发布后 `states/<source>/<T+12>.cfg.ic` 的 header 分钟对应绝对 T+12
- **链闭合**（治理不变量的端到端判别器）：发布完成后对同一棵树调用 `controller.decide_frontier` -> `cycle == T+12` 且 `stop_reason is None`
- **行数不足不写 DONE**（spec Scenario 逐字）：`expected_rows - 1` 行 -> `PublishError`，`DONE` 不存在，且 `output/<T>/<source>/` 与 `states/<source>/` 递归快照与调用前逐项相等
- **行数边界三向**：`expected_rows + 1` 行、`expected_rows` 行 + 半行尾巴（多 8 字节）-> 均 `PublishError`；恰好 `expected_rows` 行 -> 通过。第三条是反向配重，缺了它「一律拒绝」这种恒假实现也能全绿
- **reach 数不符不写 DONE**（spec Scenario 逐字）：`nc = reach_count - 1` -> `PublishError`；另一条 `reach_count != variant_reach_count`（DAT 本身与 `reach_count` 一致）-> 同样 `PublishError`，两条各自的错误消息 MUST 可区分
- **v2 判据有判别力**：v1 布局（无 1024 文本头，`nc` 在 offset 0，且 `nc == reach_count`）-> `PublishError` 指名非 v2。这条是变异体 (c) 的唯一判别器
- **文本头形状**：`[0:1024)` 含 NUL 之后又出现非 NUL 字节 -> 拒；含非可打印字节 -> 拒；全 NUL（空 header）-> 接受（SHUD 的 `char header[1024] = {}` 允许空描述）
- **期望值正数闸**（消费偏离 2）：`expected_rows = 0`、`reach_count = 0`、`variant_reach_count = 0` 三条参数化 -> 各自 `PublishError`；`expected_rows = 0` 那条 MUST 用一个数据区为空的 DAT 构造，否则它会被行数判据顺带挡住而失去判别力
- **状态不可读不写 DONE**：重戳后文档缺一个分段（`state_ic_structure_complete` 判不完整）-> `PublishError`，零 NFS 变更；header 形状非法（2 token）-> `restamp_to_absolute_time` 的 `ValueError` MUST 收敛为 `PublishError` 而不是穿透
- **日志可用五形态**：`merged_log` 不存在 / 是目录 / 是 FIFO / **是 symlink（断链与指向真实非空文件各一）** / 0 字节 -> 各自 `PublishError`，零 NFS 变更。symlink 那条是 round 1 的 cand-10（CONFIRMED/FIX_NOW）：裁决 4 逐字写了四形态含 symlink，本行原先把 symlink 悄悄换成了「不存在」，测试跟着弱的这行走，于是 `stat_no_follow` 抛 `SafeFilesystemError` 的那条臂**一次都没被执行过**（目录与 FIFO 的 lstat 是成功的，失败发生在其后的 `S_ISREG`）。裁决是更高的 oracle 层级，本行按裁决订正
- **零 NFS 变更是逐项快照**：上述每一条失败用例共用一个断言 helper——对 `YD_ROOT` 整棵做递归快照（路径、类型、大小、mode、mtime）并逐项相等，且 `work_dir` 仍存在。**MUST NOT** 以「`DONE` 不存在」单条断言代替（那放行「DAT 已 rename 但没写 DONE」这种真实缺陷）
- **`check_publish_contract` 零写入**：单独调用它，前后 `YD_ROOT` 与 scratch 两棵树的递归快照均逐项相等（含通过与失败两种入参）
- **`DONE` 前置闸**：`output/<T>/<source>/DONE` 已存在（普通文件）-> `PublishError`，既有 `DONE` 与既有 `yd.rivqdown.dat` 字节不变；`DONE` 是**目录**、是 symlink 两种形态同样拒
- **`O_EXCL` 兜底闸**（前置闸的独立判别器）：monkeypatch 让前置探测恒报「不存在」，树上真有 `DONE` -> 步骤 5 抛 `FileExistsError` 并被收敛为 `PublishError`，既有 `DONE` 字节不变。缺这条，删掉 `O_EXCL` 的变异体存活
- **状态只保留两份**（spec Scenario 逐字）：树上预置 `T-24`、`T-12`、`T` 三份状态 -> 发布后只剩 `T` 与 `T+12`
- **旧状态删除的边界方向**：`== T` 的状态永不删（变异体 (i) 的判别器）；`> T+12` 的更晚状态**不删**（变异体 (j) 的判别器，越界删它是把 12.2 的面吃进来）
- **不可见条目永不删**：`states/<source>/` 下预置 `2026082612.cfg.ic.tmp`、`nine.cfg.ic`、`9999123123.cfg.ic`、`.DS_Store`，**外加 `2026-08-25.cfg.ic`** -> 发布后逐个仍在。最后一条是变异体 (k) 的唯一判别器（fixture 复核 P2 实测的方向更正）：`nine.cfg.ic` 与 `9999123123.cfg.ic` 在字符串序下都**晚于**任何 2026 cycle（`'n'`=0x6e、`'9'`=0x39 均 > `'2'`=0x32），字符串比较的错误实现照样不删它们；`'-'`=0x2d < `'0'`=0x30，故 `2026-08-25.cfg.ic` 是唯一会被字符串序误判为「更旧」而删掉的形态
- **逐源隔离**：GFS 侧同 cycle 有更旧状态与产物 -> 发布 IFS 后 GFS 侧递归快照逐项不变
- **成功轮 work 被删除**（spec Scenario 逐字）：发布后 `work/<source>/<T>` 不存在，且 `work/<source>/` 父目录与另一 cycle 的 work 仍在
- **work 树含越界 symlink 仍可删**：树内一个指向 scratch 根外的 symlink 条目 -> 链接被 unlink、其**目标存活**（`remove_tree_allow_symlinks` 的 unlink-not-traverse 语义，与 #23 同一条判据）
- **work containment 不反推**：构造 `work_dir` 为 `<work_root>/<source>/<T>` 但 `work_root` 传一个不含它的根 -> `safe_fs` 拒绝，抛错且零删除（变异体 (o) 的判别器）
- **发布文件不带 scratch 权限**（spec Scenario 逐字）：scratch DAT 与 checkpoint mode 0600 -> `yd.rivqdown.dat`、`<T+12>.cfg.ic`、`DONE` 三者 mode 均 `0o644`；**同一断言在 `os.umask(0o077)` 下重跑一次**（裁决 11）
- **发布目录可穿越 / 自建面**（裁决 8 的目录侧，第一条）：在 `os.umask(0o077)` 下、从一棵**不含 `output/`** 的根发布 -> `output/`、`output/<T>/`、`output/<T>/<source>/` **三级**目录 mode 均为 `0o755`（三级各自单独断言；缺 `output/` 那级正是 round 1 P1 的形态）；同一用例断言 `states/<source>/`、`logs/`、`YD_ROOT` 自身的 mode **未被本次发布改动**（放宽面既不外溢也不递归）
- **发布目录可穿越 / 已存在面**（裁决 8 的目录侧，第二条；round 2 cand-04a CONFIRMED/FIX_NOW）：预置一个历史 `output/<T-12>/` 且把 `output/` 设为 `0o2750` -> 发布后 `output/` 仍逐位是 `0o2750`，历史 `output/<T-12>/` 的 mode 未被改动。**这两条 MUST 是两个用例，MUST NOT 合并**：原措辞要求「同一用例」同时断言「从不含 `output/` 的根发布」与「预置历史 `output/<T-12>/` 未被改动」，而预置历史目录**必然创建 `output/`**，在裁决 8 下它就成了已存在层级、不再被放宽——两半在一个用例里互斥，原行不可满足
- **可穿越判据的验收形式 = 穷举真值表 + 独立措辞 oracle**（裁决 8 的判据侧；round 4 的第二份 Review Failure Retro 认定的核心产出，**本条是本 fixture 里判据类断言的验收形式**）：
  1. **穷举**：一条纯函数用例，对 `m in range(0o1000)` 全部 512 个低九位 mode，外加 `S_ISUID`/`S_ISGID`/`S_ISVTX` 的**全部 8 种组合**（512 × 8 = 4096，恰好是 `stat.S_IMODE` 能返回的每一个值），断言 `_is_readable_and_traversable(m)` 与一份**独立措辞**的 oracle 逐值相等。**MUST 是 8 种组合而不是三种单叠**：只叠单个高位会漏掉「setgid + sticky」这类形态，而形如「…且不得同时置 setgid 与 sticky」的错判据能从那个洞里钻过去——见变异体 `(aq9)`，它的唯一杀手就是 8 组合版本（round 5 cand-02 CONFIRMED/DEFER/P2；原措辞的 2048 值域被 shipped 测试超覆盖，此处是把 fixture 补齐到实测形式，不是放宽）。
  2. **独立措辞 MUST 可验证**：该 oracle MUST 写成按类循环的自然语言直译——「`{group, other}` 中存在某一类，同时具备 `r` 与 `x`」——**MUST NOT** 是 shipped 掩码表达式（`(mode & 0o050) == 0o050 or (mode & 0o005) == 0o005`）的改写或复制。实现方 MUST 在 PR 报告中说明它为何不是同义反复。这一条是本行的承重条款：一份抄自被测实现的 oracle 使整条用例退化为恒真。
  3. **端到端十三格降级为接线证据**（证明判据真的被 `publish()` 调用、拒绝真的发生在第一处 NFS 写入之前）：放行 `0o755`、`0o750`、`0o705`、`0o2750`、`0o2751`、**`0o2770`**；拒绝 `0o700`、`0o744`、`0o710`、`0o711`、`0o701`、**`0o741`**、**`0o714`**。三个加粗格是 round 4 的判别器：`0o2770` 是 `docs/agent-ops.md` §10 首选的共享组 + setgid 形态（误拒方向 = 该源永久停摆），`0o741`/`0o714` 是 `r` 与 `x` 分处两类的形态。
  4. **逐级断言**：另加一条预置**不合规中间层级**的用例（`output/` 合规、`output/<T>/` 为 `0o700`）-> `PublishError` 指名该中间层级，且 `output/<T>/<source>/` **未被创建**、`YD_ROOT` 递归快照逐项不变。
  为什么不再是「逐格枚举」：round 3 的第一份 retro 把「覆盖整个输入域」落成了十个手写 mode 字面值，而 round 4 的 verifier 实测证明那与「拿反例调掩码」是同一个错误高了一层——三条自然变异体在十格下全部存活，且**即使扩到十三格，对自然掩码族做暴力扫描仍有 94 个变异体存活**。样本永远追不上域；只有穷举加独立 oracle 能真正关掉这条复发路径
- **DAT 短于定长头部即拒**（裁决 4 的 v2 判据侧，round 3 cand-03 CONFIRMED/FIX_NOW/P2）：`scratch_dat` 字节数少于 `DAT_FIXED_HEADER_BYTES`（边界值 1039，另可加一条 ~100 字节）-> `PublishError` 且消息含「非 v2」与「定长头部不足」，`YD_ROOT` 递归快照逐项不变。断言 MUST 钉住异常**类型**是 `PublishError`（而不只是「抛了错」）——被违反的正是「公共边界只抛两个声明类型之一」那条，删掉该闸后逃出来的是 `struct.error`。用例的期望长度 MUST 从 `publish.DAT_FIXED_HEADER_BYTES` 推出，不得写死 1040。既有的 `test_column_table_read_error_converges_to_publish_error` 对这条**没有判别力**：它在 1040 字节定长前缀**之后**截断，根本走不到这条臂；文本头形状闸也拦不住——被截断的 v2 前缀仍然是「可打印 ASCII + 其后全 NUL」，照样通过
- **自建层级不可穿越即拒**（裁决 8 的后置断言，round 2 cand-02 CONFIRMED/FIX_NOW/P1）：在 `os.umask(0o077)` 下预置 `output/` 为 `0o700`（无组/其他 `x`）-> `PublishError` 指名该层级，`DONE` **不存在**，`YD_ROOT` 递归快照逐项不变。今日行为是 `DONE=True` 且 `output/` 留在 `0o700`，故这条是该修复的唯一判别器
- **闩死态在下一轮仍被拒**（同上，第二个入口）：monkeypatch 令首轮的目录放宽抛 `OSError(EIO)`（`os.fchmod` 层，而非整个 `_widen_publish_dir`，以贴近真实失败面）-> 首轮 `PublishError`；恢复真实实现后对**同一轮**重跑 -> 仍 `PublishError` 且无 `DONE`，而不是把 `DONE` 封在一棵 `0o700` 的树上。`output/<T>/` 这一级另跑一次同形态（verifier 实测该级同样会闩死，「只有 `output/` 会永久闩住、cycle 目录会自愈」的说法被 PROBE1 证伪）
- **源码机检**：`publish.py` 文本中不出现 `copy2`、`copystat`、`os.link`、`shutil`（裁决 8 的结构判据）；另对 `.chmod(` 做匹配并**显式排除 `os.fchmod(`** —— 生产代码里跟随 symlink 的写法是 `some_dir.chmod(0o755)`，文本中根本不出现 `Path.chmod` 这个串，按字面禁 `Path.chmod` 对它要防的构造零判别力（fixture 复核 round 2）。断言形式：源码去掉全部 `os.fchmod(` 出现后，剩余文本中 `.chmod(` 的计数为 0
- **`yd_root` 经 symlink 到达时发布仍成功**（裁决 5 增补，fixture 复核 P2）：以 `link -> real` 构造根并把未 resolve 的 `link/yd` 传给 `publish` -> 五个终名照常落地，删除与写入结果与直接传 `real/yd` 一致。测试种子 `tmp_path.resolve()` 对这条无判别力，故必须单列
- **`DONE` 之后失败可分辨**（裁决 3 增补，fixture 复核 P2）：令步骤 6 的旧状态 unlink 抛错（预置一份旧状态为 symlink）-> 抛 `PublishCleanupError` 而非 `PublishError`，`DONE` 与 `yd.rivqdown.dat` 与 `<T+12>.cfg.ic` 三者俱在且字节正确；另断言 `PublishCleanupError` **不是** `PublishError` 的子类（`issubclass` 直接断言），否则 14.1 的 `except PublishError` 会把已完成轮吞成失败
- **`source` / `cycle` 输入域**：`source` 参数化跑 `["", ".", "..", "a/b", "ifs/"]` 五形态 -> 各自 `ValueError`，且两棵树递归快照逐项不变
- **中途失败留半成品且可被 12.2 接手**：`states/<source>/<T+12>.cfg.ic` 位置预置为 symlink -> 步骤 4 抛 `SafeFilesystemError`（收敛为 `PublishError`），DAT 已在且 `DONE` 不存在；随后 `residue.plan_residue` 对同一棵树把 `output/<T>/<source>/` 判入 `half_product_dirs`
- **幂等/重入**：同一 `PublishInputs` 连调两次 -> 第二次因 `DONE` 前置闸拒绝，第一次的产物字节不变，`states/` 仍是两份
- **`safe_fs` 既有行为不变**：`write_bytes_no_follow_exclusive` 不传 `mode` 时落地位与改动前一致（在 `os.umask(0o077)` 与 `0o022` 两种环境各断言一次），传 `mode=0o644` 时抵消 umask
- **`DONE` 复探失败时收敛为「未完成」**（round 2 cand-01/03a CONFIRMED/FIX_NOW）：令步骤 5 的写入抛错**且**同时把 `output/<T>/<source>/` 置为 `0o000`（`finally` 里恢复，否则 tmp 清理会卡住），`DONE` 不创建 -> 抛 `PublishError` 而不是 `PublishCleanupError`。今日行为是 `PublishCleanupError`，故这条是该修复的唯一判别器；与既有的「`DONE` 已落盘后失败 -> `PublishCleanupError`」构成一对反向配重，缺任一条都放行一个恒定实现
- **`DONE` 是 symlink 时复探仍判在盘**（同上的反向半边）：`DONE` 位置是一条 symlink 且步骤 5 之后失败 -> `PublishCleanupError`（裸 `lstat` 的语义：任何条目都算在盘）
- **checkpoint 不可解析收敛为 `PublishError`**（round 2 cand-03c CONFIRMED/FIX_NOW）：`scratch_checkpoint` 为二进制垃圾字节（另一条为零字节）-> `PublishError` 指名「无法解析」，零 NFS 变更。既有的 header 形状用例走的是重戳臂，从未执行到 `parse` 的 `except` 臂；`parse` 的契约是「任何结构性不可用一律抛 `ValueError`」（`cfg_ic.py:290-291`），该 `ValueError` 若穿透，14.1 的 `except PublishError` 接不住
- **`nc` 必须是有限整数值**（round 1 cand-12，round 2 cand-04c 补登记）：把 offset 1032 处的 `nc` 改写为 `NaN` 与 `8.5` 两形态 -> 各自 `PublishError` 指名「不是有限整数值」，零 NFS 变更
- **aware 非 UTC 的 `cycle` 归一到 UTC 路径**（round 1 cand-11，round 2 cand-04c 补登记）：`cycle` 传 `+08:00` 的 20:00 -> 产物落在 `output/2026082612/` 与 `states/<source>/2026082700.cfg.ic`，且 `output/2026082620/` **不存在**。「往返相等」那类断言对这条无判别力
- **`output/<T>/<source>/` 内容逐项精确**（round 1 cand-14，round 2 cand-04c 补登记）：成功轮后该目录 `iterdir()` 排序后**恰好**等于 `["DONE", "yd.rivqdown.dat"]`。缺这条，遗留的 `.tmp` 或多写一份 `meta.json`（违反 `products-contract.md` §4.5）都能全绿
- 预登记必须被杀死的变异体（按 `openspec/project-profile.md` 的 "Mutation-testing hazards" 执行：`rsync --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache'` 到含 `issue-24` 唯一标识的 scratch 目录、副本内 `rm -rf .venv && uv sync`、副本须同时带上 `openspec/` 与 `docs/`、先断言 `yd_producer.__file__` 落在副本内、每个变异体之间 `PYTHONDONTWRITEBYTECODE=1` 并清 `__pycache__`、跑法用 `uv run python -m pytest` 而非 `uv run pytest`、另跑一个必然变红的控制变异校准）：
  (a) `DONE` 创建移到状态 rename 之前 -> 顺序用例变红；
  (b) `DONE` 创建移到 DAT rename 之前 -> 顺序用例变红；
  (c) v2 判据从「文本头形状」改为「`size >= 1040`」-> v1 布局用例变红；
  (d) 行数判据 `==` 改为 `>=` -> `expected_rows + 1` 用例变红；
  (e) 行数判据改为 `bytes // (8*(nc+1)) == expected_rows`（整除，容忍残行）-> 半行尾巴用例变红；
  (f) `nc == reach_count` 判据删除（只留 `reach_count == variant_reach_count`）-> reach 数不符用例变红；
  (g) `reach_count == variant_reach_count` 判据删除 -> 第二条 reach 用例变红；
  (h) 期望值正数闸删除 -> `expected_rows = 0` 用例变红；
  (i) 旧状态删除的 `<` 改 `<=` -> 「`== T` 永不删」用例变红（这条一旦漏网即断链且不可逆）；
  (j) 旧状态删除改为「除 T 与 T+12 之外全删」-> 「`> T+12` 不删」用例变红；
  (k) 可见性判据改为「文件名字符串比较」而非解析后 cycle 比较 -> 不可见条目用例变红，**唯一判别器是 `2026-08-25.cfg.ic`**（`'-'`=0x2d < `'0'`=0x30，字符串序下排在任何 2026 cycle 之前而被误判为「更旧」删掉）。`nine.cfg.ic` 与 `9999123123.cfg.ic` 对这条**没有**判别力：`'n'`=0x6e、`'9'`=0x39 都 > `'2'`=0x32，字符串序下它们排在更后，错误实现照样不删（round 2 复核更正）；
  (l) 旧状态删除移到 `DONE` 创建之前 -> 顺序用例变红；
  (m) work 删除移到旧状态删除之前 -> 顺序用例变红；
  (n) `remove_tree_allow_symlinks` 换成 `rmtree_no_follow` -> work 含越界 symlink 用例变红；
  (o) `work_root` 参数改为 `work_dir.parent.parent` 反推 -> containment 不反推用例变红；
  (p) `atomic_write_bytes_no_follow` 的 `mode=PUBLISH_FILE_MODE` 改为 `mode=None` -> umask 0o077 下的 mode 用例变红（默认 umask 022 下 0o666&~022 == 0o644，**与期望值撞车**，这正是裁决 11 要求另跑一个 umask 的原因）；
  (q) `write_bytes_no_follow_exclusive` 的 `fchmod` 删除（只留 `os.open` 的 mode 实参）-> umask 0o077 下 `DONE` 的 mode 用例变红；
  (r) `DONE` 前置存在性闸删除 -> `DONE` 已存在用例变红（`O_EXCL` 会在步骤 5 拒绝，但此时 DAT 与状态**已经 rename 落地**，快照断言变红）；
  (s) 步骤 5 的 `O_EXCL` 换成 `O_CREAT|O_TRUNC` -> `O_EXCL` 兜底用例变红；
  (t) 契约检查移到 DAT rename 之后 -> 任一失败用例的递归快照断言变红；
  (u) 契约检查失败时补一句 `work` 删除 -> 失败用例的 `work_dir` 仍存在断言变红；
  (v) 步骤 3–7 中途失败时补一句「删掉已 rename 的 DAT」-> 中途失败留半成品用例变红；
  (w) `restamp_to_absolute_time` 的 target 由 `T+12h` 改为 `T` -> 定戳用例与链闭合用例双双变红；
  (x) 重戳后的 header 绝对时间校验删除 -> 需另造一个「重戳后 header 仍不对应 T+12」的判别器；若造不出（重戳成功即蕴含对应），如实记为等价变异体并说明，MUST NOT 默默重掷；
  (y) `merged_log` 的 `st_size > 0` 判据改为「存在即可」-> 0 字节日志用例变红；
  (z) `source` 输入域闸删除 -> `source` 五形态用例变红；
  (aa) 目录创建后的 `fchmod(PUBLISH_DIR_MODE)` 删除 -> umask 0o077 下的目录 mode 用例变红（默认 umask 022 下 `ensure_directory_no_follow` 已落地 0o755，与期望值撞车，故该用例 MUST 在 0o077 下跑）；
  (ab) 目录 mode 放宽改为递归作用于 `output/` 整棵 -> 「放宽面不外溢」断言变红；
  (ac) 入口的一次性 `Path(yd_root).resolve()` 删除（全部路径由原始 `yd_root` 拼出）-> symlink 形态 `yd_root` 用例变红；
  (ad) `PublishCleanupError` 改为继承 `PublishError` -> `issubclass` 断言变红；
  (ae) 步骤 6/7 的错误改抛 `PublishError` -> `DONE` 之后失败可分辨用例变红；
  (ag) `_create_done` 失败时不再复探 `done_path`（一律 `PublishError`）-> `DONE` 已落盘后失败的用例变红（round 1 cand-01）；
  (ah) `_read_dat_head` / `_check_dat` 的 `except` 收窄回只有 `SafeFilesystemError` -> `chmod 0o000` scratch DAT 的用例变红（round 1 cand-03）；
  (ai) 目录放宽恢复为无条件 `fchmod`（不先 stat） -> 预置 `0o2750` 的 `output/` 用例变红（round 1 cand-02）；
  (aj) 目录 `fchmod` 丢掉高位保留（直接写 `0o755`）-> setgid 存活断言变红（round 1 cand-02）；
  (ak) `state_ic_structure_complete` 的 `expected_river_count` 参数去掉 -> river 段截断的 checkpoint 用例变红（round 1 cand-04）；
  (al) `_publish_dat` 的 `len(payload) == expected_size` 断言删除 -> 整读长度复核用例变红（round 1 cand-08）；
  (am) checkpoint 改回 `parse(Path)`（跟随 symlink）-> 指向 scratch 树外的 symlink checkpoint 用例变红（round 1 cand-06）；
  (an) scratch 侧只 resolve `work_dir` 不 resolve `work_root`（或反之）-> symlink scratch 根的用例变红（round 1 cand-05 的反向配重：该变异体正是 verifier 点名的「修一半反而制造每轮 `PublishCleanupError`」形态）；
  **等价变异体（如实登记，不追判别器）**：`nc <= 0` 判据与 DAT 的 `S_ISREG` 判据各自删除后均无法构造判别器（round 1 verifier 实证）——`_check_positive_expectations` 先跑保证 `reach_count > 0`，故任何 `nc <= 0` 必先撞上 `nc != reach_count`；而 `open_file_no_follow` 自身即拒非普通文件（`safe_fs.py:345-346`）并已在 `publish.py:319-320` 被转换，故目录/FIFO 形态的 `scratch_dat` 照样得到 `PublishError` 且不会在 FIFO 上挂死。按本 fixture 变异体 (x) 的既定惯例「如实记为等价变异体并说明」处理。**round 2 追加第三条等价变异体**：`_entry_exists` 的 `except (SafeFilesystemError, OSError): return True` 翻成 `return False` 无判别器（round 2 cand-03b REFUTED，verifier 实测）——`stat_no_follow` 与 `ensure_directory_no_follow` 共用同一条 `_open_parent_dir`/`_open_child_dir` 走链，故任何**持久**的探测失败形态（`output/` 是 symlink、叶子是 symlink、父级 `0o000`）在其后的 `ensure_directory_no_follow` 上同样失败，翻转后的实现根本走不到 `fchmod`；两者只在「一次调用中途的瞬时故障」下可分辨，与上面两条同属一个等价类。MUST NOT 为它编一段 mock 编排充数。
  (ao) `_done_is_on_disk` 的复探恢复为 `except SafeFilesystemError: return True`（或按 `kind` 分支）-> 「复探失败时收敛为未完成」用例变红（round 2 cand-01）；
  (ap) 可穿越断言整体删除（前置与后置两趟一并删）-> 预置 `0o700` `output/` 的用例变红（round 2 cand-02）。**MUST 删两趟**：只删后置那趟按设计存活（round 3 cand-02 REFUTED，verifier 实测 `(ap-post-only)` 全绿；`(ap-pre-only)` 被杀。**杀手计数是随轮次变的量，此处不再固化**：round 3 测得它只被 `自建层级不可穿越即拒` 一条杀死，round 4 加宽格子后测得 `KILLED | 9 failed`、日志列出六个具名杀手（round 5 cand-04 CONFIRMED/DEFER/P3：原括注是 round-3 的一次性测量，已过期）。本条承重的是 `(ap-post-only)` **按设计存活**这半句，它在 round 4 日志中仍为 `SURVIVED | 129 passed`）。后置那趟唯一独占的场景是「`fchmod` 返回成功却不生效」，全项目文档无此机制、最接近的真实类比（父目录 default POSIX ACL clamp）已由 Known limits 路由到 M4 现场验证，故后置那趟按 belt-and-braces 保留而不追判别器，**MUST NOT** 为它编一段 `os.fchmod` 静默空转的 mock 编排（与本节三条等价变异体同一条惯例）；
  (aq) 可穿越判据放松为「只要 owner 有 `r`+`x`」-> 预置 `0o700` 的用例变红（防止把断言写成恒真：发布进程自己永远进得去）；
  (aq2) 判据放松回「组或其他有 `x` 即可」（丢掉 `r` 的要求）-> `0o710`/`0o711`/`0o701` 三格变红（round 3 cand-01 的直接判别器）；
  (aq3) 判据放松为「组或其他有 `r` 即可」（丢掉 `x` 的要求）-> `0o744` 格变红（(aq2) 的反向配重）。**原措辞「这两条合起来钉死『r 与 x 必须同类兼备』」按 round 4 verifier 实测为假，已删除**：跨类合取变异体同时保留 `r` 与 `x` 两项要求、只丢掉「同类」耦合，(aq2)/(aq3) 都杀不掉它，见 (aq4)；
  (aq4) 判据放松为「组/其他里有 `r`，且组/其他里有 `x`」（**不要求同类**，`(mode & 0o044) != 0 and (mode & 0o011) != 0`）-> `0o741`/`0o714` 两格变红，穷举表变红（round 4 cand-01a；该变异体与 shipped 判据在 512 个低九位 mode 中有 64 个分歧，误放行方向）；
  (aq5) 判据改为**整位段相等** `(mode & 0o070) == 0o050 or (mode & 0o007) == 0o005` -> `0o2770` 格变红，穷举表变红（round 4 cand-01c；误**拒** `0o2770`/`0o770`/`0o707`，即 §10 首选形态被永久拒绝，该源停摆）；
  (aq6) 判据改为 `(mode & 0o050) == 0o050 or (mode & 0o007) == 0o005`（只有 other 位段相等）-> 穷举表变红（round 4 verifier 构造的第四轴；误拒 `0o707`/`0o2707`，十三格全部看不见它）；
  (aq7) 判据改为 `(mode & 0o054) == 0o050 or (mode & 0o005) == 0o005`（group 子句附加禁 other-`r`）-> 穷举表变红（同上第四轴；误拒 `0o754`，十三格同样看不见）；
  (aq9) 判据附加「不得同时置 setgid 与 sticky」（`… and not (mode & S_ISGID and mode & S_ISVTX)`）-> 穷举表变红（round 4 修复实施方自行构造并登记；**唯一杀手是 8 种高位组合的穷举表**，三种单叠版本对它零判别力——这是本条第 1 小项要求 8 组合的实证依据）；
  **(aq4)–(aq7) 四条 MUST 全部被杀**，这是本轮验收的可机检事实：前两条由端到端格子加穷举表共同杀死，后两条**只有穷举表杀得掉**——它们正是「样本追不上域」的实证。
  (aq8) 前置那趟只断言首级（`for directory in levels[:1]`）-> 预置不合规中间层级的用例变红（round 4 cand-01b；该变异体不落入等价类，存在自然、无 mock 的判别器）；
  (av) `_read_dat_head` 的 `len(head) < DAT_FIXED_HEADER_BYTES` 长度闸删除 -> DAT 短于定长头部的用例变红（round 3 cand-03 CONFIRMED/FIX_NOW/P2；verifier 实测删掉后 `struct.error` 直接穿透 `publish` 与 `check_publish_contract` 两个公共入口——它既不是 `OSError` 也不是 `ValueError`，沿途两处 `except (SafeFilesystemError, OSError)` 都接不住，14.1 的 `except PublishError` 更接不住）；
  (ar) `_restamped_bytes` 中 `parse(raw)` 外的 `except ValueError` 删除 -> checkpoint 垃圾字节用例变红（round 2 cand-03c）；
  (as) `nc` 的有限/整数判据删除 -> `NaN` 与 `8.5` 用例变红（round 1 cand-12，本轮补登记）；
  (at) `_normalize_cycle` 改为 `replace(tzinfo=UTC)`（丢弃 offset 而非换算）-> aware 非 UTC 用例变红（round 1 cand-11，本轮补登记）；
  (au) 步骤 3 的临时文件不 rename 而是留下 -> `output/<T>/<source>/` 精确内容用例变红（round 1 cand-14，本轮补登记）；
  (af) 契约检查阶段的 `read_bytes_limited_no_follow` 换成 `read_bytes_no_follow`（整读）-> 需一条「检查阶段峰值内存与头部同量级」的 `tracemalloc` 断言（构造一个 `st_size` 巨大但头部合法的 DAT），照 #22 的既有做法登记；该断言 MUST 用从 `publish` 导入的 `DAT_FIXED_HEADER_BYTES` 推出期望量级（`DAT_FIXED_HEADER_BYTES + 8*nc`），不得写死 1040 或 5.4 MB 这类字面量

Verification（本 issue 合并前逐条跑）:
- `cd producer && uv run pytest` -> 退出码 0
- `cd producer && uv run ruff check . && uv run ruff format --check .` -> 退出码 0
- `cd producer && uv sync --frozen` -> 退出码 0（不得新增依赖）
- `openspec validate m2-producer-core --strict --no-interactive` -> 退出码 0

Known limits（合并时按此验收）:
- **跨 source 在共享 `output/<T>/` 层级上的放宽竞态**（round 5 cand-06 PLAUSIBLE/DEFER/P3）：`_prepare_output_dir` 先 stat 定「自建层级」、再 mkdir、再 fchmod，无锁。并发发布者落进他人的 `mkdir`→`fchmod` 窗口即把该层级误判为已存在，抛一次 `DONE` 前的 `PublishError`。响亮、零 NFS 损伤、下一 tick 自愈；仅当 14.3 双源并行后可达。tracked issue：**#106**，其中钉死约束：无条件 `fchmod` 是被禁的变异体 `(ai)`（round 1 cand-02 P1），修法不得重提。
- `docs/products-contract.md` §5.2 的「数据区第 0 列逐值为 `0, 60, …, 10020`」不在 spec 与 issue 的 DONE 前检查清单内（偏离 4），本 issue 不实现；一份分钟列错乱但行列数正确的 DAT 仍会被写 `DONE`。按「out-of-scope findings: report, don't fix」路由为独立 issue：**#109**（Phase 8 deferral routing 已出链接）。
- 「不继承 uid/gid」只有结构证明与源码机检，无跨 uid 行为断言（裁决 11）；真实 NFS 上的 uid/gid 落点归 M4 现场验证。
- 裁决 8 的目录放宽只解决 umask 造成的收紧；`safe_fs.py:124-131` 点名的另一条路径——父目录带 default POSIX ACL 时 mode 实参会 clamp 掉继承的 ACL mask——本模块不处理（`fchmod` 到 0o755 同样不恢复被 clamp 的 `#effective` 位）。现场若采用 ACL 而非共享组 setgid，属部署侧配置，归 M4 现场验证与 `docs/agent-ops.md` §10 的部署约定。
- `PublishCleanupError`（`DONE` 之后的清理失败）遗留的孤儿 `work/<source>/<T>` **当前无回收归属**（fixture 复核 round 2）：`residue.plan_residue` 的清单在 `DONE(T)` 存在时整体为空且全程不碰 scratch，13.3 的面是 `output` 的 14 天保留窗，两者都收不到它。这违反 `docs/compute-loop-design.md` §12「每轮成功或失败收尾后删除」，但补一处 scratch 侧的孤儿扫描属 13.2/14.1 的收尾面，本 issue 不越界实现。按「out-of-scope findings: report, don't fix」路由为独立 issue：**#108**（Phase 8 deferral routing 已出链接）。#108 另记录了同一缺口的第二个入口：步骤 5 与 6/7 之间进程被硬杀会留下同样的孤儿且**零异常**，故「14.1 捕获 `PublishCleanupError` 重试」单独收不了口。
- **`work_dir` 的形状未被校验**（round 1 cand-07 CONFIRMED/**DEFER**，路由到 14.1）：`_remove_work` 只校 containment，不校 `work_dir` 是否真的等于 `work_root / source / cycle_id(cycle)`。实测传 `work_dir = work_root / SOURCE`（少一节、单个干净分量、确在 `work_root` 内）时 `publish()` **返回成功**，整棵按源 work 树被删——含另一 cycle 的 work，即 `test_publish.py:271` 明确断言必须存活的那个目录；且发生在 `DONE` 之后，重跑救不回来。DEFER 的依据是 fixture 把这层校验分给了调用方（裁决 1 只 fail-close `source`/`cycle`，裁决 6 只钉「containment 不得反推」）且 `cli.py run` 仍是 `_unimplemented`、无生产暴露面。该 DEFER **以路由成立为条件**：必须在 14.1 落一条入口守卫 `work_dir == work_root / source / cycle_id(cycle)`（落在 `PublishInputs.__post_init__` 亦可），并配一条「传少一节路径 -> 姊妹 cycle 的 work 存活」的回归用例。tracked issue：**#94**。
- spec Scenario「状态只保留两份」的字面 WHEN 是「连续发布两轮成功」，本 issue 无 `run_once`，以「预置三份历史状态 + 一轮发布」这一等价树形式验收；两轮连跑的端到端形式归 14.1。
- **`reach_count` 与真实 `cfg.ic` river 段行数的恒等式由测试构造断言，未经真实数据验证**（round 2 cand-05 PLAUSIBLE/**DEFER**，本行即该 DEFER 的成立条件）：裁决 4 的 `expected_river_count=reach_count` 把一条无任何仓内文件明说的恒等式变成了硬闸，而 `test_publish.py` 全套由单个 `REACH_COUNT` 常量同时喂 `build_dat_bytes(nc=...)` 与 `build_cfg_ic(river_count=...)`，对真实分叉零判别力（套件里没有任何一份真实 yd `cfg.ic`）。失败方向是**对的**：若真实 `cfg.ic` 的 river 段行数不等于 3988，结果是每轮 pre-`DONE` 响亮 `PublishError`、零 NFS 损伤、该源永久停在原地重试，而不是 round 1 那种静默中毒。故本 issue 不补代码；该恒等式的真实数据验证归 M4 现场验证与 14.1 的配置接线（与本节 uid/gid、ACL 两条同一路由）。

Non-goals:
- 失败处理（合并日志生成、失败侧删 work、不推进）：任务 13.2，issue 待定
- 14 天保留窗清理与字面 `realpath` 圈定 yd 根：任务 13.3，issue #25
- `run_once` 编排、把发布器接进 `cli.py run`、运行报告：任务 14.1
- checkpoint 的**捕获**（tracker）：issue #16；本 issue 只消费一个已捕获的路径
- 变体 reach 数的真实来源（prepare 侧计数）：#20 / 14.1 接线（偏离 1）
- `residue.plan_residue` / `decide_frontier` / `state/**` 的任何改动
- 真实 NFS/Slurm 行为、DAT 数值正确性：归 M4

Review focus:
- 契约检查是否**真的**全部先于第一处 NFS 写入（看调用顺序，不看注释）；失败路径的 `YD_ROOT` 递归快照是否真的逐项不变
- 五个终名的相对序是否逐对被钉住；有没有把顺序断言退化成「都发生了」
- 旧状态删除集合是否**严格**等于「cycle 严格早于 T 的合法状态」——多一类（`> T+12`、不可见条目、另一源）或少一类都是缺陷；`<` / `<=` 与「字符串比较 vs 解析后比较」两处边界要当作缺陷主动查
- `containment_root` 两处是否各自正确（NFS 侧 `Path(yd_root).resolve()`、work 侧显式 `work_root`），有没有出现 `shutil` / 裸 `Path.unlink` / `copy2` / `os.link`
- `safe_fs` 的 `mode` 扩展是否**只**新增可选参数、缺省路径逐字节不变；既有用例有没有被改动（oracle 完整性）
- DAT 读取是否真的有界（有没有 `read()` 整个文件、有没有把数据区读进内存）
- v2 判据是否有判别力（能否分辨 v1），还是退化成「文件够大」
- `DONE` 的两道闸是否都在，`FileExistsError` 有没有穿透
- 有没有越界落地 13.2 的失败处理、13.3 的保留窗，或 14.1 的编排（含"顺手先放着"的死代码）

## 14. run-controller（三）：主循环集成

- [ ] 14.1 单源单轮 `run_once` 骨架打通：发现 → 组装 → 提交 fake → 发布 → work 清理；job ID/partition/终态/起止时间进运行报告；`local.toml` 缺 Slurm 字段即停
- [ ] 14.2 多轮追赶与缺口停等：raw 一次补齐 T/T+12h/T+24h 时序推进、每源在途提交计数 ≤1、缺轮停在缺口（§13.1：同源顺序/raw 缺口）
- [ ] 14.3 双源并行、单源失败隔离与崩溃恢复端到端：IFS 失败 GFS 继续、失败日志与 work 清理、无 DONE 残留下次重跑（§13.1：双源并行/单源失败/无 DONE 崩溃恢复）

依赖：组 5、组 8、组 9、组 12、组 13
§13.1 归属：控制器/发布（逐 task 标注场景）
Suggested fixture level: expanded - 多轮端到端目录树与可编排 fake executor
Minimal mergeable slice: 单源单轮骨架（14.1）——一条端到端路径独立合并保绿，追赶与双源为后继

**14.1 接线约束（由 issue #22 / PR #62 round 1 验证闸门传下，batch integration-contract cand-06 CONFIRMED）**：`controller.decide_frontier` 返回的待跑 T 可能带**任意可解析的 cycle 小时**（issue #22 fixture 裁决 5 刻意如此：对不可解析文件名 fail-closed 会让一次崩溃的发布永久砖化该源），而 `rawscan.judge` 只对 `config.cycle.hours` 全域——实测一个 stray 的 `states/<source>/2026081918.cfg.ic` 会让 18Z 目标进入 `judge`，在任何文件系统访问之前抛 `ConfigError` 并**穿透** `decide_frontier`，把「停一个源」放大成「整个 tick 崩」。14.1 MUST 二选一并配用例：(a) 接线前把目标限制到 `config.cycle.hours`，或 (b) 把注入的 `raw_complete` 包一层，把 `ConfigError` 收敛为该源的停止原因。
