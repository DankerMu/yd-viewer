"""未提交残留的识别与清理：判定（零写入）与执行（真删除）严格分离（任务 12.2）。

契约来源：`docs/compute-loop-design.md` §10 步骤 4、§11.2、§11.3、§12，
`docs/products-contract.md` §2/§4，
`openspec/changes/m2-producer-core/specs/run-controller/spec.md` 的
「未提交残留清理重跑」Requirement。

本模块实现 issue #23 fixture 的下列裁决（编号即裁决号，故不连续：裁决 8/11 是
`runlock.py` 的锁面）：

1. **裁决 1（判定/执行分离，判定零写入）**：`plan_residue` 只 `lstat` / 列目录，
   MUST NOT 创建、修改或删除任何路径；`execute_residue_plan` 只删除清单里逐字列出的
   路径。分离不是风格：任务 13.2（失败/重跑路径）复用的是**判定**而不是删除动作，
   融成一个函数即让 13.2 无从复用。
2. **裁决 2（定义域：NFS 侧、逐源）**：残留 = `states/<source>/` 里 cycle **严格晚于**
   T 的合法状态文件 + `output/<T>/<source>/`（存在且其下无 `DONE`）整棵。
   **不含** scratch `work/<source>/<T>`（归 #26/#28）、不含 14 天保留窗清理（归 13.3）、
   不含 `output/<T>/` 父目录、不含其它源。多一类或少一类都是缺陷。
3. **裁决 3（全新链同样适用）**：T 一律取 `FrontierDecision.cycle`——该值在无任何
   `DONE` 时就是 `states/<source>/` 里**最早**的合法状态（`controller._decide` 的
   `min(state_cycles)`）。本模块因此不自己算 T：多出来的状态份数只可能来自一次中断的
   首轮发布，按同一条规则删除后重跑 T，MUST NOT 判为异常停源（那会让首轮崩溃永久
   砖化该源）。`stop_reason` 非空的源 MUST NOT 进入清理——不知道 T 就无从定义「更晚」。
4. **裁决 4（`DONE` 是删除前置，粒度是 source 子目录）**：半成品判据复用
   `controller.done_cycles`（`os.stat` + `stat.S_ISREG`，products-contract §4：`DONE`
   是唯一完成标志），MUST NOT 以「目录非空」或「有 `yd.rivqdown.dat`」代替；空目录
   （mkdir 后即崩）同样是半成品。删除粒度是 `output/<cycle>/<source>/`，**不是**
   `output/<cycle>/`：另一源可能在同一 cycle 目录下已有 `DONE`。
5. **裁决 5（不可见条目永不删除）**：cycle 可见集判据（10 位数字、`%Y%m%d%H` 可解析、
   `+12h` 不溢出，`states/` 侧另需 `.cfg.ic` 后缀）从 `controller` **导入**
   （`visible_state_cycles` / `done_cycles` / `cycle_id`），MUST NOT 重写。不可解析 ⇒
   无法判定是否比 T 晚 ⇒ 不删。只删除能被正面识别为残留的路径，是本模块的 fail-closed
   形态。
6. **裁决 6（删除原语一律走 `store/safe_fs.py`，两类路径策略不对称）**：
   - 半成品 `output/<cycle>/<source>/` 用 `remove_tree_allow_symlinks`：该原语的
     docstring 逐字说明它为「内容按构造不可信的 residue/quarantine 树」而存在——被杀死
     的发布尝试留下什么形态都可能（symlink、FIFO、设备节点）；换成拒绝 symlink 的
     `rmtree_no_follow` 会「permanently lock the run at the hygiene hook」，即一条崩溃
     残留把该源永久卡在清理钩子上。symlink 条目按**链接本身**被 unlink，永不跟随，故
     链接的目标不会被删。
   - 残留状态文件用 `unlink_no_follow`（遇 symlink 抛 `SafeFilesystemError`）。
     **不对称的理由**：`states/<source>/<cycle>.cfg.ic` 只由发布器以「普通文件原子
     rename」写入（spec「NFS 提交顺序与 DONE 语义」步骤 3），该位置出现 symlink 不是
     崩溃残留而是异常，按 fail-closed 停该源。两侧的构造性可信度不同，策略因此不同。
   - 两者都传 `containment_root=<YD_ROOT>`，落实 compute-loop §12「清理只允许作用于经
     确认位于 yd 自己根目录下的对象；不得跟随路径进入 NWM raw 根」。`safe_fs` 全程
     `O_NOFOLLOW` 逐段锚定，严于「先 `realpath` 再比前缀」（后者在解析与使用之间仍有
     TOCTOU 窗口）。MUST NOT 用 `shutil.rmtree` / 裸 `Path.unlink`。
   - **判定期的类型判据同样不对称**：半成品目录要求 `os.lstat` 下是**真目录**（该位置
     是 symlink 时不能被正面识别为半成品，不删）；状态文件在判定期**只过文件名可见
     集，不做 symlink 过滤**——若判定期就把 symlink 状态滤掉，裁决 6 要的「遇 symlink
     停该源」就永远不可能发生，拒绝是 `unlink_no_follow` 在**执行期**的职责。
7. **裁决 7（清理失败即停该源）**：`SafeFilesystemError` **原样上抛**（该异常的消息已
   逐字指名失败的路径，且 `kind` 字段可供调用方区分 tamper / io），本模块不吞、不续删。
   删了一半就重跑，等于让下一步在一个既非干净也非完整的树上组装。
   **幂等**：两个删除调用都带 `missing_ok=True`，且清单为空时零调用，故对已清理干净的
   树重复「判定+执行」、乃至重复执行**同一份旧清单**，都是 no-op（零删除、零异常）。
   这是 cron 每小时重入的必需性质。
9. **裁决 9（#59 崩溃恢复前置）**：本模块的删除集合与任何 Slurm 作业的写入集合按构造
   不相交（孤儿作业的 `--chdir` 在 scratch `work/` 下，而 work 不在本集合内；NFS 侧的
   `output/`/`states/` 只由控制器写，控制器写入被 `runlock` 覆盖），故 12.2 不需要在途
   作业存活确认。一旦 #28 把 work 的删除接进重跑路径，两个窗口都恢复可达——完整裁决
   归 #28，本模块 MUST NOT 替它选。

删除顺序固定为「先半成品树、后更晚状态」。该顺序对正确性不重要（前沿只由 `DONE` 推进，
T 不受任一侧影响，任一步崩溃后下次重入会重新判定并补删），钉死只为让执行序可复现。

探测层的「无法确定」（`ENOENT`/`ENOTDIR` 之外的 `OSError`，即
`controller.DiscoveryUnreadableError`）收敛成 `ResidueError`：与裁决 7 同向，不可确定
一律停该源，MUST NOT fail-open 成「空清单」而让残留留在树上被下一轮当成正常产物。

本模块 stdlib-only：零新增依赖（裁决 12）。零 `cli.py` 改动（裁决 10，接线归 14.1）。
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from yd_producer.controller import (
    STATE_SUFFIX,
    DiscoveryUnreadableError,
    FrontierDecision,
    cycle_id,
    done_cycles,
    visible_state_cycles,
)
from yd_producer.store.safe_fs import (
    remove_tree_allow_symlinks,
    unlink_no_follow,
)

__all__ = [
    "ResidueError",
    "ResiduePlan",
    "execute_residue_plan",
    "plan_residue",
]


class ResidueError(RuntimeError):
    """残留**判定**无法完成：本源本次停止，不清理、不重跑。

    只覆盖判定期的「不可确定」（目录列不出、条目元数据探测遇到 `ENOENT`/`ENOTDIR`
    之外的 `OSError`）。**执行期**的拒绝不走这里：`safe_fs.SafeFilesystemError` 原样
    上抛（裁决 7），它自带指名路径的消息与 `kind` 分类。
    """


@dataclass(frozen=True)
class ResiduePlan:
    """单源的残留清单：判定的**全部**产出，执行的**唯一**输入。

    `state_files` 与 `half_product_dirs` 逐字就是将被删除的路径集合——执行函数不再做
    第二次发现，所以「删对了没有」这个问题在清单这一层就已经可判（任务 13.2 只消费
    本清单，不触发删除）。
    """

    #: 传给 `safe_fs` 的 `containment_root`（compute-loop §12）。
    yd_root: Path
    source: str
    #: 保留的 T：`FrontierDecision.cycle`。`states/<source>/<T>.cfg.ic` 永不进清单。
    retained_cycle: datetime
    #: cycle 严格晚于 T 的合法状态文件，按 cycle 升序。
    state_files: tuple[Path, ...]
    #: 无 `DONE` 的本源半成品目录（当前定义域下最多一个：`output/<T>/<source>/`）。
    half_product_dirs: tuple[Path, ...]

    @property
    def empty(self) -> bool:
        return not (self.state_files or self.half_product_dirs)


def plan_residue(
    *,
    yd_root: Path,
    source: str,
    decision: FrontierDecision,
) -> ResiduePlan | None:
    """判定 `source` 的未提交残留清单。**零写入**：只 `lstat` 与列目录。

    `decision` 是同一棵树、同一个源上 `controller.decide_frontier` 的结论。它带
    `stop_reason`（不可跑）时返回 `None`：不知道 T 就无从定义「更晚」，该源本次
    MUST NOT 进入清理（裁决 3）。

    `source` 必须与 `decision.source` 一致——两者分叉会让清单落到另一个源的目录上，
    正是裁决 2「逐源」要防的形态。

    返回的清单可能为空（树是干净的）：那是幂等重入的正常结果，不是异常。

    Raises:
        ValueError: `source` 与 `decision.source` 不一致。
        ResidueError: 判定期有任何一处「无法确定」（裁决 7 同向的 fail-closed）。
    """
    if source != decision.source:
        raise ValueError(
            f"plan_residue 的 source={source!r} 与 decision.source="
            f"{decision.source!r} 不一致：残留清单必须逐源自洽"
        )
    if decision.cycle is None:
        return None

    root = Path(yd_root)
    retained = decision.cycle
    try:
        states = _later_state_files(root, source, retained)
        half_products = _half_product_dirs(root, source, retained)
    except DiscoveryUnreadableError as error:
        raise ResidueError(f"{source}: 残留判定无法完成——{error.detail}") from error

    return ResiduePlan(
        yd_root=root,
        source=source,
        retained_cycle=retained,
        state_files=states,
        half_product_dirs=half_products,
    )


def execute_residue_plan(plan: ResiduePlan) -> None:
    """删除清单逐字列出的路径，且只删这些路径（裁决 1/6/7）。

    删除全部经 `store/safe_fs.py` 且带 `containment_root=plan.yd_root`。
    `SafeFilesystemError` 原样上抛：该源本次停止（不重跑、不提交），错误消息自带失败的
    路径。两个调用都带 `missing_ok=True`，所以重复执行同一份清单是 no-op（裁决 7）。
    """
    for directory in plan.half_product_dirs:
        remove_tree_allow_symlinks(
            directory.parent,
            directory.name,
            containment_root=plan.yd_root,
            missing_ok=True,
        )
    for state_path in plan.state_files:
        unlink_no_follow(
            state_path,
            containment_root=plan.yd_root,
            missing_ok=True,
        )


def _later_state_files(
    yd_root: Path, source: str, retained: datetime
) -> tuple[Path, ...]:
    """`states/<source>/` 里 cycle **严格晚于** T 的合法状态文件（裁决 2/5）。

    `>` 而不是 `>=`：T 自己的状态是重跑本轮的起点，在任何路径上都不被删除
    （spec「未提交残留清理重跑」：保留 T 状态）。比较的是**解析后的 cycle**，不是文件名
    字符串——文件名比较在跨世纪/非等宽记法下与时间序不同构。
    """
    states_dir = yd_root / "states" / source
    later = sorted(
        cycle for cycle in visible_state_cycles(states_dir) if cycle > retained
    )
    return tuple(states_dir / f"{cycle_id(cycle)}{STATE_SUFFIX}" for cycle in later)


def _half_product_dirs(
    yd_root: Path, source: str, retained: datetime
) -> tuple[Path, ...]:
    """`output/<T>/<source>/` 存在且其下无 `DONE` 时返回它，否则空（裁决 2/4）。

    `DONE` 的存在性复用 `controller.done_cycles`（普通文件判据 + cycle 可见集），
    不另起一套：两份判据一旦分叉，「已完成」在发现层与清理层就会不一致。
    目录形态用 `os.lstat` 判：该位置是 symlink（或普通文件）时不能被正面识别为半成品，
    不删（裁决 5 的 fail-closed 方向）。空目录**是**半成品（mkdir 后即崩）。
    """
    output_root = yd_root / "output"
    if retained in done_cycles(output_root, source):
        return ()
    candidate = output_root / cycle_id(retained) / source
    try:
        mode = os.lstat(candidate).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return ()
    except OSError as error:
        raise DiscoveryUnreadableError(
            f"半成品判定失败：{candidate} 无法确定（{error}）"
        ) from error
    if not stat.S_ISDIR(mode):
        return ()
    return (candidate,)
