# M2 Producer 基础 — 任务分解

任务组按依赖排序（每组的"依赖"行给出真实前置，未列即可与前面各组并行）；全部落 `producer/`，本地测试是唯一门禁。每组尾部标注 compute-loop §13.1 归属行。

## 1. cli-config：配置装载与 CLI 骨架

- [x] 1.1 实现 `config.toml` 类型化装载与 fail-closed 校验（业务规则字段全集含 `reach_count`，spec cli-config）
- [x] 1.2 实现 `local.toml` 现场值装载（含 NWM checkout 根），缺失即报错、零内置默认
- [ ] 1.3 实现 argparse 三入口骨架（prepare/init/run 薄委托，注册 `[project.scripts]` 入口点）、未知子命令拒绝、`DATABASE_URL` 环境守卫、run 入口状态目录缺失/为空即报错停止且不触发 init 逻辑
- [ ] 1.4 实现 NWM 解释器薄外壳（精确路径调用、cwd/`PYTHONPATH` 取自 checkout 字段、fail-closed），以假解释器脚本测试调用形态

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
- `load_config(path) -> Config` 与 `load_local(path, config) -> LocalConfig`（file→object 纯函数，design.md「Sketch seams under test」之下的基础层；CLI 入口层不做行为测试）

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

## 2. forcing-chain（一）：NWM 快照勘察与基础结构

- [x] 2.1 只读勘察 NWM@`8ae9b8f2`，产出精确快照文件清单（模块 → 原路径 → 目标路径，含 tracker 与补跑），落为 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md`；表格列固定 `| 能力项 | NWM 原路径 | 目标路径 | 剥离点 | 备注 |`，一行一个文件、路径反引号包裹；凡原模块触及 DB/scheduler/registry/journal/reservation 的行，`剥离点` 必须点名具体 import、符号或分支（供 2.2 逐文件消费），无耦合写 `无`，禁止“已剥离 DB 分支”一类无点名的笼统措辞
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
- `store/`、`raw/` 全目录 grep `psycopg|DATABASE_URL|scheduler|registry|os.getenv|os.environ` -> 零命中
- **pin 等价性（`剥离点` 为 `无` 或仅注释改写的四行）**：`producer/src/yd_producer/store/object_path.py`、`store/safe_fs.py`、`producer/tests/test_data_adapter_resolution.py`、`store/object_store.py`，各自 `diff` `git -C <NWM 本地 checkout> show 8ae9b8f29c8b72c574e8cbd95f2994160bd42832:<清单该行原路径>`，忽略新增的溯源头部与 import 路径改写（`packages.common.*`/`workers.data_adapters.*` → `yd_producer.*`）、以及 object_store 行 `剥离点` 点名改写的那条注释 -> 无其他差异。抽取/改写式的七行（`:40` source_identity、`:41` manifest、`:42` cycle_hours、`:43` region、`:50` test_safe_fs、`:51` test_object_path、`:52` test_source_identity）不适用本行，其等价证据是实现者的逐文件剥离点符合性说明
- `normalize_source_id("IFS"/"ifs"/"Ifs")` -> `"ifs"`；`normalize_source_id("ERA5")` -> 抛错（ERA5 条目已删）
- `ManifestEntry`/`DownloadManifest` 的 `as_dict` → `from_dict` roundtrip -> 字段等价
- `DownloadManifest.from_dict` / `ManifestEntry.from_dict` 收到缺必需字段或类型错的 dict -> 稳定抛错，不返回半成品对象
- `cycle_id_for(<已知 source_id + cycle datetime>)` -> pin 语义的已知 cycle id 字面量（清单 §4 风险 3 点名的新写覆盖）
- `GeoBBox` 无 bbox 入参 -> fail closed（清单 §4 风险 14：四个 `DEFAULT_BBOX_*` 已删，禁止发明缺省）
- `env_cycle_hours_utc` 显式入参传 `None` -> 仍走 `normalize_cycle_hours_utc(default, field_name=...)`，零 `os.getenv`
- `parse_cycle_hours_utc` 收到畸形输入（`"0,25"`、`""`、`"0,abc"`）-> 稳定抛错
- **键校验的闸门归属（三个函数各管一段，勿把任一段读成总闸门）**：以下每条均已跑探针核实。`normalize_object_key`（在 `store/object_store.py`，不在 `object_path.py`）拒 `..`（出现在任何位置即 `ValueError`）与空键；**绝对路径它不拒**——按 pin 语义 `strip("/")` 后继续，故 `'/raw/gfs/2026050700/a.grib2'` 被**接受**并根相对化为 `'raw/gfs/2026050700/a.grib2'`，而 `'/etc/passwd'` 被拒是因为 `etc/` 不在前缀白名单里，与「绝对」无关。`validate_object_path` 只做前缀白名单匹配、变量段原样捕获：`..` 出现在**已识别前缀之后**时返回 `valid=True`（`'raw/gfs/../../../etc/passwd'` -> `valid=True, cycle_time='..'`），而**开头**的 `..`（`'../etc/passwd'`）返回 `valid=False`——但拒因仍是前缀不匹配，不是穿越检测；两种结果都不构成穿越闸门。闭合 containment 的是复合入口 `LocalObjectStore.resolve_path` = `normalize_object_key` → `validate_object_path` → `relative_to(root)`。-> 复合入口对三类输入稳定拒绝；`validate_object_path` 的单独 permissive 行为由具名用例 `test_object_store.py::test_validate_object_path_alone_accepts_parent_traversal` 钉死（该用例用「前缀之后的 `..`」这一精确输入），以免组 3/7/13 把它当作穿越闸门
- `LocalObjectStore` 对已存在对象再写 -> **覆盖允许（last-write-wins）**，非 no-clobber：探针实测 `write_bytes_atomic` 同键写第二次成功且内容被替换，`.part` 临时文件不残留。此为 pin 语义，本 PR 不得改动；组 12/13 若依赖「已存在即拒」需自行加闸门
- `sha256_bytes(<已知字节串>)` -> 已知摘要字面量（独立 oracle：`printf ... | shasum -a 256`）
- safe_fs 拒绝分型 -> 稳定拒绝，逐条注明覆盖来源（原措辞「快照测试原有覆盖保留」对**非常规文件**一项前提为假：pin 的 `tests/test_safe_fs.py` 14 个用例里无一触及 `S_ISREG`，round 1 verifier 已在 pin 上核实）：
  - 符号链接叶 / 符号链接祖先（`directory_identity_no_follow` 面）-> 快照用例 `test_directory_identity_refuses_symlink_components`（parametrized final/ancestor）已覆盖
  - 超限读 -> `test_object_store.py` 经 `LocalObjectStore.read_bytes_limited` 覆盖 `read_bytes_limited_no_follow` 的字节上限
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
- 溯源头部检查（2.3）：对清单 §1 路径表内每个已存在的目标路径 -> 该文件前 5 行内含字面量 `NWM@8ae9b8f2 <对应原路径>`；表内路径尚未落地的行跳过（后续组落地即自动纳入）
- 溯源反向守卫（2.3）：扫描根即 `producer/`（与规格字面相等），按「相对路径分量以 `.` 开头即跳过」的规则排除 `.venv`/`.pytest_cache` 等（规则而非具名名单，不引入第二份名单）；其下带溯源注释谓词命中的文件集合 -> 必须是清单 §1 路径表的子集，多一个即失败。实测 2786 个 `.py` → 跳点开头目录后 26 个 → 带标记 11 个
- 上方 Regression rows 的每一行 -> 对应一个具名 pytest 用例（用例名与断言的期望值须来自 pin 源码或独立 oracle，不得从实现回读）

Non-goals:
- 清单 §1 的 `direct_grid_contract.py` / `shud_forcing_contract.py` 两行（能力 3 的 direct-grid 契约面）——属 forcing 本体，issue #5 Out of Scope，归组 8
- converter / forcing / state / tracker 各行——归组 6/7/8/4/9
- raw 完整性判定与 `raw-manifest.json` 生成逻辑——归任务 3.1/3.2（本 PR 只交付数据结构）
- `config.toml` 的 bbox / forcing 上限字段落地——清单 §4 风险 14 已显式交接任务 1.1；本 PR 只保证缺参 fail closed
- 不为 `manifest.py` 保留的排程函数族新写测试——`test_data_adapter_resolution.py` 整文件快照（10 个用例）已覆盖，重写等于二次实现
- 不改动 pin 上被保留符号的语义以"顺手改好"——语义等价是本 PR 的验收项；改进意见记为 follow-up issue
- **不把反向扫描面扩到 `producer/` 之外**（如仓库根、`viewer/`）——本 PR 的守卫只对快照落地面负责，跨面扫描属组 13 的仓库级检查；此边界经 round 1/2 三名 reviewer 复核接受，记录在此以免后续轮次重开庭（PR #40 偏离记录 F2）
- **不认 docstring / 字符串形式的溯源标记**：正反两向共用注释谓词后，`"""NWM@8ae9b8f2 ..."""` 这类写法**不算**溯源头部，因而一个未登记、仅带 docstring 标记的散落文件不承担登记义务、也不被守卫接触。这是「单一谓词 + 反向必须保持注释锚」的必然推论——反向若放宽成裸串，守卫会命中自身拼出的 `PROVENANCE_MARKER` 常量，逼出一份手工豁免名单。该语义由具名用例 `test_forward_guard_rejects_docstring_form_markers` 钉死，非疏漏

Review focus:
- 逐行核对清单 §1 的 11 个 in-scope 行的 `剥离点` 列是否被逐条执行，尤其 `:40` 的 `_STORAGE_SOURCE_IDS` 字面量改写与 `:52` 的 10 处下游断言同步、`:43` 的四个 `DEFAULT_BBOX_*` 与 `_env_float` 删除、`:42` 的 `os.getenv` 删除
- `:50` 的三处**函数内** import（L37 模块级、L270、L289 函数内）是否删净——函数内 import 漏改不会被 `pytest --collect-only` 抓到
- 是否存在任何被发明的缺省值（bbox、上限、lead），违反 D4 零默认
- 2.3 的检查测试是否真的双向（正向逐文件断言 + 反向越界守卫），以及是否会在后续组落地新快照文件时自动生效而非需要手工维护第二份清单
- import 路径改写是否一致（`packages.common.*` / `workers.data_adapters.*` → `yd_producer.store.*` / `yd_producer.raw.*`，含 `storage.py` → `object_path.py` 的重命名）

## 3. raw-scan：完整性扫描与临时 manifest

- [ ] 3.1 实现 IFS/GFS 完整性规则判定（00/12 限定、0–168h、变量/bundle 模式、GFS f000 特例、逐文件检查）
- [ ] 3.2 实现 raw 只读复制到 `work/raw/`（源不可变断言）与临时 `raw-manifest.json` 生成（entry 只引用副本）

依赖：组 1（规则来自 config）、组 2（manifest 结构）
§13.1 归属：raw 扫描
Suggested fixture level: compact - tmp 目录树按文件模式生成空壳文件即可覆盖判定与复制
Minimal mergeable slice: 完整性判定纯函数（3.1）——不含复制与 manifest，可独立合并保绿

## 4. state-tools：cfg.ic 工具链

- [ ] 4.1 快照并适配 `cfg.ic` 原生分段解析与回写（mesh/river/lake），字节级 roundtrip 测试
- [ ] 4.2 实现结构检查（缺段、行数与 header 不符、数值区损坏）
- [ ] 4.3 实现重戳到目标 cycle 绝对时间（只改 header、数据不变；服务 init 首态与发布前 T+12 定戳两条路径）
- [ ] 4.4 快照负残差归零与域均修正阈值检查纯函数

依赖：组 2（勘察清单定原路径）
§13.1 归属：state
Suggested fixture level: compact - 合成分段状态文件（小规模 mesh/river/lake）覆盖全场景
Minimal mergeable slice: 分段解析与 roundtrip（4.1）——格式层独立合并保绿，重戳/残差为后继

## 5. 执行器抽象：JobExecutor 协议与 fake

- [ ] 5.1 定义 `JobExecutor` 协议（submit/poll、job ID/partition/终态/起止时间语义）与进程内 fake（成功/失败/超时可编排），接口契约测试
- [ ] 5.2 实现 Slurm 生产执行器（`sbatch`/`sacct` 封装，参数全部装配自 `local.toml`、零内置默认）；本阶段不做行为测试（M4 oracle），本地判据 = 参数装配纯函数检查 + 协议一致性

依赖：组 1（Slurm 字段结构）
§13.1 归属：控制器（支撑）
Suggested fixture level: compact - 进程内接口契约与参数装配测试，无文件 fixture
Minimal mergeable slice: 协议与 fake（5.1）——接口层独立合并保绿，生产执行器为后继

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
- [ ] 10.2 实现 `rivers.geojson`（`reach_id`=DBF Index、数量一致）与 `boundary.geojson`（单元合并边界）生成，落点 `input/viewer/`
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
