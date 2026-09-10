## Context

yd 只有一个流域、两个 forcing source。已有 prepare 事务、staged-input 文件搬运、assembly、forcing 和 controller/publish 都继续使用；缺的是完整 native 输入与真实 builder 调用，不是一个新的包管理平台。

用户已确认：原生 `model/input/yd/`；完整变体替代 five-only；真实 driver 归 M2；本次新增设计从简，不借机重构已合并的清理/发布防御。此前关于 registry 导出、snapshot UUID、审批链、通用资产角色与任意目标路径的提议撤回。

## Goals / Non-Goals

Goals：`prepare --baseline <模型目录>` 真实生成 GFS/IFS 完整变体与 GeoJSON；同一变体可经既有 staging/forcing/assembly 被 stock SHUD 读取。

Non-Goals：通用流域/湖泊/外部边界条件支持；registry 服务/导出与生命周期；NWM 平台审批、EvidencePackage/rollback 体系；旧 five-only 自动兼容；新重试/监控/缓存；重写既有 safe_fs、WorkClaim、cleanup、publish、flock；修改 NWM 环境/代码/DB；本地结果冒充 M4 数值验证。

## What already exists

- `prepare.run_prepare` 拒绝覆盖、scratch→YD_ROOT staging、四终名提交；`VariantBuildRequest` 已携带 source/grid/baseline/output。
- `prepare_handoff.load_prepared_variant_handoff`、`stage_work_inputs`/`load_staged_work_inputs`、`assemble_staged` 是现有消费者边界。扩展固定文件集合，不另建一套 validator 或能力框架。
- `render_shud_parameters` 与 state parser 已负责运行参数、绝对 T 与 warm state；forcing 已负责有序网格/绑定兼容，不在 builder driver 重写这些算法。
- PR #197 独立修复 `-o`、DAT/checkpoint 输出目录与日志；本 change 不重开该 finding。

## Decisions

### D1 固定 yd 输入，不设计角色系统

`--baseline` 指向直接包含 `yd.*` 与 `gis/` 的模型目录。2026-09-10 只读 node-22 `stat` 观察到 `/ghdc/data/yd/input/yd/yd.sp.mesh`、其 `gis/` 目录存在，而 `/ghdc/data/yd/gis` 不存在；只证明布局，未运行模型。现场路径不写入配置默认或代码。

prepare 读取下列固定 native 文件：

| 类别 | 文件 |
|---|---|
| 配置/初态 | `yd.cfg.ic`, `yd.cfg.para`, `yd.cfg.calib` |
| 空间 | `yd.sp.mesh`, `yd.sp.att`, `yd.sp.riv`, `yd.sp.rivseg` |
| 物理参数 | `yd.para.lc`, `yd.para.soil`, `yd.para.geol` |
| 辅助时序 | `yd.tsd.lai`, `yd.tsd.mf` |

baseline 的 `yd.tsd.forc` 仅供旧 FORC 索引解析/映射；prepared 变体不携带旧天气 CSV 或旧 forcing index，run 每轮生成它们。`yd.tsd.rl` 在 SHUD pin 的 loadinput 中未使用，不复制；GIS 只用于 prepare 的 CRS 和 viewer 输出，不搬进日常 work。GIS 固定使用 `gis/river.shp`、`gis/domain.shp` 及各自必需 sidecar，不猜复数文件名。

这是当前 yd 的无湖泊/无外部 BC 模型，不实现条件资产解释器。prepare 如观察到 element BC/SS/LAKE 或 river BC 非零，报告不支持的输入，而不是悄悄丢掉它。该检查只在 prepare 做一次。已读取本地受控模型的 7891 element 与 3988 reach，相关列全为零；不将这些行数写成算法常量，仍按既有模型/配置校验。

### D2 一个现有 handoff，固定完整文件集合

保留文件名 `yd.direct-grid-handoff.json`，schema 升到 `yd.prepare.direct-grid-handoff.v2`。沿用原有 source/project/四个版本标识/DirectGridForcingContract；sp_att 固定 `yd.sp.att`，binding 仍为 `yd.binding` 的 opaque bytes。增加一个简单 `file_checksums` 映射，键固定为上述 12 个 native 文件加 `yd.binding`，值复用现有 `sha256:` 表达；不加 role、size descriptor、任意相对目标或插件字段。`.sp.att` 与 binding 的 checksum 与既有 contract 引用保持一致。

变体顶层固定 14 文件（12 native + binding + handoff），不包含 `yd.para`。这是现有 handoff 的干净升级，不保留两个生产 loader/v1 fallback。`calibrated_state_path` 仍返回顶层 `yd.cfg.ic`，因此 init/source reach authority 不改。prepared 参数名改为原生 `yd.cfg.para`，迁移全部调用者。

staged schema 相应升为 `yd.run.staged-inputs.v2`；只扩展现有固定路径/file-checksum map，沿用既有 copy、work-local loader 与 owner。不要新增一层整包哈希、另一套 snapshot identity 或每个函数重新扫描全包。旧 five-only 输入在提交 worker 前明确要求重新 prepare。

### D3 原生路径与实际 SHUD 文本语法

最终目录：

```text
work/model/                         # SHUD cwd，RunDirectory.path，tracker 输出根
  input/yd/                         # 12 native，三项按本轮替换/生成
    yd.cfg.ic                       # 当前 source 的 T 状态，不是 baseline 初态
    yd.cfg.para                     # render_shud_parameters，START=0 END=7
    yd.tsd.forc                     # 本轮 forcing index
    ...其它固定 native 文件...
  <forcing CSV 文件名>              # 保持既有 forcing 的平铺相对名字
  yd.rivqdown.dat                    # #197 显式 -o 的输出
  state_checkpoints/
```

stock SHUD 的 forcing reader 从 `yd.tsd.forc` 第二行的 path 相对进程 cwd 打开 CSV，不相对 index 文件。CSV 因而仍复制到 `model/`，index path 行明确为 `.`；不修改 timestep、站点行、坐标、Z 或天气值。索引重定位只由 assembly 完成一次。

参数也必须是原生语法，不是只改后缀。SHUD pin 的 `Control_Data::read` 使用 `sscanf("%s %lf")`；现有 writer 的 `KEY = value` 不是合法数值行，并且不识别真实 baseline 的 `KEY<TAB>value`，会留下旧值再追加第二行。已用实际 baseline + 当前 renderer + libc sscanf 复现（END 保留 9132，追加行只解析到一个 token）。native assembly 必须让共享 parameter writer 采用明确的 native 输出模式：六项 runtime key 各写一行无前导空格的 `KEY<TAB>value`，识别 baseline 原生空白分隔/大小写，主运行 END=7、恢复 END=0.5，其余参数 bytes 不改。保留 legacy 调用的原有模板模式；模式是内部调用选择，不成为 TOML/用户配置或另一套 writer。native 参数证明必须按 stock reader 的数值语法读取，不能继续用 split('=') 的合成 oracle。

`RunDirectory` 字段集合不增：path 仍为 model 根，state/parameter/index 字段指向 `input/yd/`，CSV 字段指向 model 根；controller/receipt/recovery 按字段消费，迁移其中任何写死 `yd.para` 或平铺初态的点。恢复运行必须调整其实际 `parameter_path`，仍用独立 `-o output_dir`，不改主运行输出。

外部-root legacy `assemble` 在旧合同中还有独立使用者：不以兼容别名调用 native loader，不新增 v1 native fallback。保留它现有的外部-root API/安全语义，native staged 路径复用同一 assembly kernel，通过内部明确的输入/输出位置传入，不复制整个 kernel。native 生产 worker 只消费完整 staged v2。

### D4 薄 driver 复用算法，不移植 NWM 平台

当前 `build_direct_grid_variant` 除算法外还要求 Approvals、RollbackTarget、CapacityReport 等 NWM 平台 evidence 输入。本项目不为凑齐它们生成假审批/假 QA/假 UUID；不调用 resolution-only CLI 假装完成 build。

yd 维护一个 prepare-only Python driver，由 `local.nwm.python` 执行；它直接复用 NWM pin 的库：

1. `workers.grid_registry.input_record.read_input_record` + `registry.prepare_snapshot` 从 NWM checkout 已有的 source grid.json/metadata 构造文件内存快照。后者不读写 registry；adapter 只实现算法需要的 source/grid 查找，不登记 DB snapshot、不生成 UUID。IFS 的 NWM 物理文件路径为 `canonical/IFS/...`，yd 日常对象键仍保持现有小写 `canonical/ifs/...`；显式 source 对照不是大小写搜索/fallback。
2. 复用 `mapping_builder.algorithm` 的 nearest-cell mapping、used-cell subset 和 forcing index 顺序；不重新实现空间算法或裁剪完整网格来迎合 binding。小于四个 used cells 维持库的明确拒绝，不新增审批流程。
3. 复用 NWM sp.att FORC 重写与 Z-policy/sampler（现有固定 verdict/nearest mesh elevation），再使用 binding emitter。只消费其真实 manifest/binding 输出转换为 D2 的已有 direct-grid contract，不生成 NWM EvidencePackage。
4. 固定复制 D1 文件，替换 rewritten sp.att，写出 handoff。原有四个版本标识由 prepare builder 一次声明并存入 handoff，不在 run 推导；不引入新的 UUID 字段。版本值以实际 baseline native 内容与 source/grid/mapping 版本的稳定摘要命名，按各自对象区分 basin/native-baseline/river/model；controller 仍只逐字消费，不承担命名逻辑。

网格文件和 NWM 算法与 checkout pin 一起固定，供 prepare 一次读取；记录使用的现有 grid signature 即可，不新建 registry 导出/有效性审批系统。真实 raw 转换后仍由既有 forcing 边界发现 grid 不匹配，禁止覆盖 canonical grid 来凑结果。

执行器保留现有 `invoke_mapping_builder` 公共入口，改为调用 yd 随包提供的 driver 脚本绝对路径；移除不再使用的 `nwm_mapping_builder_module` 配置和调用者，不保留一个无效开关。脚本使用标准库接收既有 source/grid/baseline/output 参数，在 NWM 环境 import NWM；yd 日常 worker 绝不 import NWM。fixed interpreter 不 resolve venv symlink、不做 PATH 回退；子进程 `PYTHONPATH` 仅为明确的 NWM checkout，移除 `DATABASE_URL`/`PYTHONHOME`。这是 #45 的 exec 现场义务与明确 prepend→固定路径 cutover，不扩展成环境白名单平台。

`prepare.default_builder` 不再抛 builder-unavailable；删去相关未实现分支与文字型测试。真实 GIS 使用 river.shp；保留原有 prepare 拒绝覆盖、四终名事务和失败分类。

## Sketch seams under test

- `stage_work_inputs → assemble_staged`：一个代表性固定 native 变体，断开 source 后仍在 work 内组装；核对 warm state、原生参数、CSV 实际解析位置与未改变的输出根。
- `yd-producer prepare --baseline`：在固定 NWM 运行环境真正调用库生成两变体/GeoJSON；测试只替换进程/环境边界时不得将合成 builder 计为真实 driver 证明。M2 可使用小型合法 native 几何和网格，另运行 pin 库 smoke；真实 yd 数值仍归 M4。

不新增为证明字段转发、源码字符串、默认值或每个 helper 的重复测试。保留现有真实错误/状态转换测试，迁移格式变化破坏的 fixtures；新增回归只覆盖原生路径、FORC 重写与单个真实失败路径。

## Phases / Verification

1. Native runtime cutover：固定 handoff + staged 搬运 + native assembly，作为同一格式迁移切片；existing prepare 的 builder seam 消费新格式，但实际 driver 随下一切片接入。一个 native staged→assembly 路径验证格式闭环。不同层不能先后独立改变同一个生产格式，因此该 PR 是耦合边界，不按文件拆成中间损坏版本。
2. Real prepare driver：依赖切片 1，接入固定 NWM 库调用与 GIS/CLI，移除 unavailable/无效 module 开关。一个 CLI prepare 路径验证两 source 和事务。不开 NWM 平台 evidence 子项目。
3. #132 集成：吸收两前置，复核 worker/recovery 使用新的 RunDirectory 路径并完成原 PR 审核；现有 Round 1 计数与 finding 记录不重置。
4. M4：真实 baseline/binary，IFS/GFS 的 00Z/12Z、7 日 DAT、T+12/下一轮与 NFS receipt。此步骤不再补业务代码，也不由本地 smoke 替代。

每个实现切片运行 project-profile 的 producer/lint/OpenSpec 门禁；文档 PR 不运行 native/Slurm。runtime 与 builder 各自有单一路径验收，避免每个 helper 再跑一套全包防御矩阵。

## Risks / Trade-offs

- 固定无湖泊/无 BC 范围不支持未来换流域；到时另改需求，不预建条件角色框架。
- NWM 库仍带自身校验和读取行为；仅在 prepare 使用，不复制/修改其实现，不把它的 DB 生命周期带进来。
- 现有 private NWM mesh-Z helper 如需使用，必须按固定 pin 写清实际调用，不发明 Z=0 fallback；库接口漂移属于显式升级失败。
- 旧变体不能直接运行。重新 prepare 到空的目标根是迁移操作，不能自动删现有状态/DONE/变体。

## Rollback or containment

代码与输入格式成对回退；停 controller 后选择旧代码及旧完整 YD_ROOT/配置，禁止新旧变体混用。不会自动覆盖现有根、回收不明 work 或降级回 five-only。真实现场操作继续受 agent-ops 授权约束。

## Not yet specified

无。本 change 只针对当前固定 yd 模型；现场 checkout/SHUD binary/Slurm 参数按既有 M4 操作流程实测，不成为本地实现的猜测默认。

## Open decisions

无待用户决策项。上述简化边界已由用户最后一次选择「按精简方案推进」确认。

## References

- #202 与 PR #197；原生路径：SHUD `3aec65755926c478e13ca7d4fea80715e4e90345` 的 CommandIn.cpp、IO.cpp、MD_readin.cpp。
- NWM `8ae9b8f29c8b72c574e8cbd95f2994160bd42832`：workers/mapping_builder/{cli,algorithm,rewrite,binding,z_policy_verdict}.py；workers/grid_registry/{input_record,registry}.py。
- docs/agent-ops.md；docs/compute-loop-design.md；openspec/project-profile.md；active M2 D19/D20。
