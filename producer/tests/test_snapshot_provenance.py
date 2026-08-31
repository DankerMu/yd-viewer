"""任务 2.3：快照溯源头部检查（双向）与快照面的 DB-free 隔离检查。

数据源是 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md` 的 §1 快照清单
表本身（`| 能力项 | NWM 原路径 | 目标路径 | 剥离点 | 落地状态 | 备注 |`），在测试时解析，**不**在
本文件里转录一份 Python 副本——转录副本会与清单漂移，且会要求后续任务组手工维护第二份
名单。正向断言对表内每个已落地的目标路径生效，反向守卫强制后续组落地的快照文件必须先
登记进清单表。

守卫的「执行集」必须等于它「声明的集合」，因此下列口径都从清单/文件系统派生、不写死：

1. DB-free 扫描集 = 已落地的清单目标（含快照**测试**文件）∪ 这些目标所在的
   `producer/src/yd_producer/<pkg>/` 包目录整目录（未登记的散落文件也逃不掉）。
   后续任务组落地新目标时扫描集自动扩张，无需改本文件。
2. 「什么算溯源头部」全仓只有**一个**定义：`snapshot_provenance_fixtures.py` 里的
   `_MARKER_COMMENT`——**整行即溯源头部形式**的 `#` 注释行（注释内容恰为
   `NWM@<sha> <原路径>`，允许缩进与行尾空白）。
   `<原路径>` 是**纯仓库相对路径**，字符类派生自清单 §1 的 `NWM 原路径` 列；`路径:行号
   （括注）` 这类紧贴路径的行级引用不是溯源头部。
   正反向共用它，只在**行预算**上分叉——反向按 grep 语义扫整文件（无预算），正向再叠加
   「行号 ≤ `HEADER_LINE_BUDGET`」与「pin 与原路径逐字段相等」。两侧曾各有一份定义
   （正向是裸前 5 行子串），于是 docstring 形式的标记被正向奖励、又对反向隐形；
   `test_forward_guard_rejects_docstring_form_markers` 钉死这个形式维度。
3. 反向扫描根 = 规格声明的 `producer/` 整棵树，只跳过点开头的目录（`.venv` 等）：
   扫描根写成两个子目录时，执行集与声明集只是「眼下恰好相等」。
4. §1 表体的每一行都必须解析成功：畸形行是硬失败，不再静默跳过。表体行的判定按
   `lstrip()` 后是否以 `|` 起头——缩进一格是合法 CommonMark、渲染完全相同，若按列 0
   判定，缩进行会连同它的溯源义务一起悄悄离开表体集，而 `len(rows) == len(body)`
   完整性检查会真空通过。§1 区段内任何「含 `|` 却不构成表行」的行都是硬失败。
5. 「哪些目标必须已落地」由清单 §1 的 `落地状态` 列驱动，不由文件系统派生：期望集
   一旦派生自磁盘，删文件时期望与实际同步收缩，删除就完全静默。
6. `落地状态` 列的义务是**双向**的，两个方向各有判别器：正向「标了 `本 issue 落地`
   ⇒ 文件必须在」由 `test_landed_snapshot_files_carry_their_provenance_header` 的
   缺席分支承担；反向「文件在且带溯源头 ⇒ 必须标 `本 issue 落地`」由
   `test_files_carrying_a_provenance_header_are_marked_landed` 承担。只做正向时，
   把一行降级成 `待落地` 是零信号的（全套 359 passed 不变），随后再删文件也零失败——
   一次改词就静默解除了该行的全部落地义务。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
from snapshot_provenance_fixtures import (
    FORBIDDEN_SURFACES,
    HEADER_LINE_BUDGET,
    INVENTORY_BODY_LINES,
    INVENTORY_ROWS,
    INVENTORY_STATUS,
    INVENTORY_TARGETS,
    MALFORMED_ROWS,
    PIN_SHORT,
    PINNED_TARGETS,
    PROVENANCE_MARKER,
    REPO_ROOT,
    SCANNED_ROOTS,
    STATUS_LANDED,
    STRAY_TABLE_LINES,
    _code_lines,
    _declared_forbidden_surfaces,
    _files_with_marker,
    _forbidden_hits,
    _header_marker_lines,
    _inventory_body_lines,
    _landed_targets,
    _marker_comment_lines,
    _marker_fields,
    _parse_inventory_rows,
    _provenance_header_lines,
    _scan_files,
)

# --- 表本身没被解析空、也没被静默丢行 -----------------------------------------


def test_inventory_table_parses_every_body_row() -> None:
    # 解析器一旦静默返回空集合或丢行，正向断言会真空通过、反向守卫会全量误报。
    assert not MALFORMED_ROWS, f"§1 表体存在无法解析的行：{MALFORMED_ROWS}"
    # 含 `|` 却不构成表行的行：某一行被改得「不像表行」就会带着自己的溯源义务离开
    # 表体集，而下一条 `len(rows) == len(body)` 会真空成立（两侧同步收缩）。
    assert not STRAY_TABLE_LINES, (
        f"§1 区段存在含 `|` 却不以 `|` 起头的游离行：{STRAY_TABLE_LINES}"
    )
    assert len(INVENTORY_ROWS) == len(INVENTORY_BODY_LINES), (
        f"§1 表体 {len(INVENTORY_BODY_LINES)} 行只解析出 {len(INVENTORY_ROWS)} 行"
    )
    # 非空性从数据派生，不写计数地板：地板要么随后续行落地而失去咬合力，要么在最后
    # 一组落地、`待落地` 清空时误报。`本 issue 落地` 集非空 + 下面的具名锚点足以钉死
    # 「§1 根本没被解析到」。
    assert PINNED_TARGETS, "§1 没有任何标记「本 issue 落地」的行（区段没被解析到）"
    assert len(INVENTORY_TARGETS) == len(INVENTORY_ROWS), "§1 出现重复目标路径"
    assert "producer/src/yd_producer/store/safe_fs.py" in PINNED_TARGETS


def test_malformed_body_rows_are_reported_instead_of_silently_dropped() -> None:
    # 回归：`剥离点` 单元格里出现未转义的 `|`（如引用类型标注 `str | None`）时，
    # 该行曾被静默丢弃，连同它的正向溯源断言一起消失。
    good = (
        "| 3 object-store | `packages/common/safe_fs.py` "
        "| `producer/src/yd_producer/store/safe_fs.py` | `无` | 本 issue 落地 "
        "| 整文件快照 |"
    )
    poisoned = (
        "| 9 新能力 | `packages/common/widget.py` "
        "| `producer/src/yd_producer/widget/widget.py` | 改写 `f(x: str | None)` "
        "| 待落地 | 备注 |"
    )
    bad_status = (
        "| 9 新能力 | `packages/common/widget.py` "
        "| `producer/src/yd_producer/widget/widget.py` | `无` | 本issue落地 | 备注 |"
    )

    rows, malformed = _parse_inventory_rows([good, poisoned, bad_status])

    assert rows == [
        (
            "producer/src/yd_producer/store/safe_fs.py",
            "packages/common/safe_fs.py",
            STATUS_LANDED,
        )
    ]
    assert len(malformed) == 2, "畸形行必须被报告"
    assert "单元格数为 7" in malformed[0]
    # 状态列写错一个字不得被当成「非落地」放过——那等于静默解除该行的落地义务。
    assert "落地状态应为" in malformed[1] and "本issue落地" in malformed[1]


def test_indented_body_rows_stay_in_the_body_set() -> None:
    # 回归（P2）：前导空格是合法 CommonMark、渲染相同；按列 0 判定时，把某一行缩进
    # 一格就能让它连同其正向溯源义务静默离开表体集，守卫仍全绿。
    section = (
        "## 1. 快照清单\n\n"
        "| 能力项 | NWM 原路径 | 目标路径 | 剥离点 | 落地状态 | 备注 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        " | 1 x | `workers/x.py` | `producer/src/yd_producer/x/x.py` | `无` "
        "| 待落地 | 备注 |\n\n"
        "## 2. 下一节\n"
    )

    body, stray = _inventory_body_lines(section)
    rows, malformed = _parse_inventory_rows(body)

    assert not stray
    assert not malformed
    assert [target for target, _, _ in rows] == ["producer/src/yd_producer/x/x.py"]


def test_rows_that_stop_looking_like_table_rows_are_reported_as_stray() -> None:
    # 表体判定放宽后，一行要逃出表体集只剩「彻底不像表行」这条路；它必须留下信号，
    # 否则 `len(rows) == len(body)` 会随两侧同步收缩而真空通过。
    section = (
        "## 1. 快照清单\n\n"
        "| 能力项 | NWM 原路径 | 目标路径 | 剥离点 | 落地状态 | 备注 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "1 x | `workers/x.py` | `producer/src/yd_producer/x/x.py` | `无` "
        "| 待落地 | 备注 |\n\n"
        "## 2. 下一节\n"
    )

    body, stray = _inventory_body_lines(section)

    assert body == []
    assert len(stray) == 1 and "workers/x.py" in stray[0]


def test_pinned_targets_come_from_the_status_column_not_the_filesystem() -> None:
    """P1 的机制断言：期望落地集派生自 `落地状态` 列，不派生自文件系统。

    **执行集的实测口径（不要按名字读成更多）**：把 `PINNED_TARGETS` 换回文件系统派生
    （`(REPO_ROOT / target).is_file()`，即 P1 原样复发）时，全仓当前状态下全套 362
    passed 一条不红——因为两种派生在「每个 `本 issue 落地` 行的文件都在、每个 `待落地`
    行的文件都不在」这个状态下**取值相同**。两者取值分叉只有两种状态：钉住的文件被删，
    或 `待落地` 行的文件出现；这两种状态都已被
    `test_landed_snapshot_files_carry_their_provenance_header` 判红（缺席分支 / 在场必须
    带头部分支）。所以那条变异体在「全套还能绿」的状态空间里是**等价变异体**，不是漏网
    之鱼；删文件的检测由该正向用例的 `status` 形参承担，与 `PINNED_TARGETS` 的定义无关。

    下面两条断言是把模块常量与状态列做**机制绑定**（同义重述），实测只在上述分叉状态里
    与正向用例一同变红；保留它是为了让「换回文件系统派生」这件事在改动点上就有一条具名
    断言接住，而不是宣称它独立地钉死了 P1。

    末段的解析器断言与模块常量无关：它只证明「按状态列过滤」这个表达式对两行虚构清单
    行的行为正确（两行的文件都不在磁盘上）。
    """

    # 机制绑定：模块常量 == 按状态列现算的期望集。
    assert PINNED_TARGETS == {
        target: source
        for target, source, status in INVENTORY_ROWS
        if status == STATUS_LANDED
    }
    # 且它对文件系统免疫：`待落地` 行即使文件已在磁盘上，也不得混进期望集。
    assert all(INVENTORY_STATUS[target] == STATUS_LANDED for target in PINNED_TARGETS)

    # 解析器一侧：两行的目标路径在磁盘上都不存在，期望集却只含标了「本 issue 落地」的那行。
    section = (
        "## 1. 快照清单\n\n"
        "| 能力项 | NWM 原路径 | 目标路径 | 剥离点 | 落地状态 | 备注 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| 1 x | `workers/x.py` | `producer/src/yd_producer/x/x.py` | `无` "
        "| 本 issue 落地 | 备注 |\n"
        "| 2 y | `workers/y.py` | `producer/src/yd_producer/y/y.py` | `无` "
        "| 待落地 | 备注 |\n\n"
        "## 2. 下一节\n"
    )

    body, _ = _inventory_body_lines(section)
    rows, _ = _parse_inventory_rows(body)

    pinned = [target for target, _, status in rows if status == STATUS_LANDED]
    assert pinned == ["producer/src/yd_producer/x/x.py"]
    assert not (REPO_ROOT / pinned[0]).exists()


# --- 正向：表内已落地的目标路径必须带对应溯源头 -------------------------------


@pytest.mark.parametrize(
    ("target", "source", "status"),
    INVENTORY_ROWS,
    ids=[target for target, _, _ in INVENTORY_ROWS],
)
def test_landed_snapshot_files_carry_their_provenance_header(
    target: str, source: str, status: str
) -> None:
    path = REPO_ROOT / target
    if not path.exists():
        # 「该不该存在」由清单的 `落地状态` 列裁决，不由文件系统自证：期望集若派生自
        # 磁盘，删掉一个已钉住的快照文件时正向断言会静默退化成 skip（P1：删两个已钉
        # 住目标后全套零失败，只有 skip 计数从 16 变 18）。
        assert status != STATUS_LANDED, (
            f"{target} 在 §1 里标记为「{STATUS_LANDED}」，但磁盘上不存在；"
            "已钉住的快照文件不得被删除，落地状态也不得先于文件翻转"
        )
        pytest.skip(f"{target} 尚未落地（归后续任务组，落地状态为「{status}」）")

    assert _provenance_header_lines(path, source), (
        f"{target} 的前 {HEADER_LINE_BUDGET} 行缺少溯源头**注释**行 "
        f"`# ... {PROVENANCE_MARKER} {source}`；"
        f"命中的头部注释行为 {_header_marker_lines(path)}"
    )


def test_landed_targets_are_exactly_the_files_that_carry_a_provenance_header() -> None:
    """已落地的登记目标集 == 带溯源头的文件集。

    与被它取代的 `>= 11` 计数地板是**两个不可比的维度**，不是「等价或更强」：

    - 本条更强的那一维是**耦合**：它逐文件同时钉死「登记且已落地 ⇒ 有头部」与
      「有头部 ⇒ 已登记」，地板只数数。
    - 本条更弱的那一维是**存活**：等式两侧都派生自文件系统，删掉任意一批快照文件时
      两侧同步收缩，等式仍成立。原先声称「具名锚点兜住了这一维」是错的——锚点只兜住
      `safe_fs.py` 自己被删；删除任何不含它的子集完全静默（Phase 6.2 审计 P1：删掉
      `test_safe_fs.py` + `test_source_identity.py` 后全套零失败，正向断言静默退化成
      skip）。

    存活这一维现由 `test_landed_snapshot_files_carry_their_provenance_header` 承担：
    期望集来自清单 §1 的 `落地状态` 列（`STATUS_LANDED`），与文件系统无关，删任一钉住
    目标即红，且随后续任务组翻转自己那几行自动扩面、不会腐烂。本条只保留耦合那一维。

    下面的具名锚点因此只剩「检查本身失效」的角色（与
    `test_inventory_table_parses_every_body_row` 里的同名锚点一致），不再被当作删除
    检测手段。
    """

    landed = {path.resolve() for path in _landed_targets(REPO_ROOT, INVENTORY_TARGETS)}
    with_header = {
        path.resolve() for path in _files_with_marker(REPO_ROOT, SCANNED_ROOTS)
    }

    anchor = (REPO_ROOT / "producer/src/yd_producer/store/safe_fs.py").resolve()
    assert anchor in landed, "锚点快照文件未落地，检查本身失效"
    assert landed == with_header, (
        f"只在清单里：{sorted(str(p) for p in landed - with_header)}；"
        f"只在磁盘上：{sorted(str(p) for p in with_header - landed)}"
    )


# --- 反向：带溯源标记的文件必须登记在清单表里 --------------------------------


def test_every_file_with_a_provenance_header_is_registered_in_the_inventory() -> None:
    found = _files_with_marker(REPO_ROOT, SCANNED_ROOTS)

    # 非空性/存活性不用写死的计数地板，改从数据派生：已落地的登记目标必须全在扫描结果里。
    # 正则或扫描根一旦失灵，这条先红，`unregistered` 才不会真空为空。
    missed = {
        path.resolve() for path in _landed_targets(REPO_ROOT, INVENTORY_TARGETS)
    } - {path.resolve() for path in found}
    assert not missed, (
        f"反向扫描漏掉已落地的登记目标，检查本身失效：{sorted(map(str, missed))}"
    )

    unregistered = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in found
        if path.relative_to(REPO_ROOT).as_posix() not in INVENTORY_TARGETS
    ]

    assert not unregistered, (
        "以下文件带溯源标记但不在 nwm-snapshot-inventory.md §1 路径表内，"
        f"请先登记再落地：{unregistered}"
    )


def test_files_carrying_a_provenance_header_are_marked_landed() -> None:
    """`落地状态` 义务的**反向**：文件在场且带溯源头 ⇒ §1 里必须标「本 issue 落地」。

    正向（标了落地 ⇒ 文件必须在）由
    `test_landed_snapshot_files_carry_their_provenance_header` 的缺席分支承担，但它只在
    文件**不存在**时才读状态列。于是把任一行降级成 `待落地`、文件原样不动，是零信号的：
    `INVENTORY_TARGETS` 状态盲，`landed == with_header` 两侧都不变，全套 359 passed 不变；
    随后再删该文件也零失败。本条补上缺的那个方向，让「降级」成为必须显式重写清单行的动作。

    **残留（不得声称已完全关闭）**：一次提交里同时降级并删除某一行的文件，仍能全绿——
    本条只看在场文件，文件没了就没有反向义务。彻底关闭需要一份冻结路径名单，而那是已裁定
    的非目标——`openspec/changes/m2-producer-core/tasks.md` 的 Non-goals「已知限度：完全
    失去竖线的表体行不可达」一条写明「不冻结名单是刻意取舍——名单正是本守卫要消灭的
    东西」（PR 偏离 F8/F10）：名单会与清单表构成第二份名录并要求后续任务组手工同步。本条的执行集 = 「在场且带头部的已登记
    文件」，不多不少。

    未登记的带头文件不在本条范围内（`.get()` 取不到即跳过）——那是
    `test_every_file_with_a_provenance_header_is_registered_in_the_inventory` 的职责，
    在这里重复报会让同一个缺陷红两次而遮蔽真正的降级信号。
    """

    demoted: list[str] = []
    checked: list[str] = []
    for path in _files_with_marker(REPO_ROOT, SCANNED_ROOTS):
        relative = path.relative_to(REPO_ROOT).as_posix()
        status = INVENTORY_STATUS.get(relative)
        if status is None:
            continue
        checked.append(relative)
        if status != STATUS_LANDED:
            demoted.append(f"{relative}（§1 落地状态为「{status}」）")

    assert checked, "没有任何带溯源头的已登记文件进入检查，检查本身失效"
    assert not demoted, (
        "以下文件已落地并带溯源头，§1 却没标「"
        f"{STATUS_LANDED}」——落地状态不得先于文件翻转：{demoted}"
    )


def test_registered_headers_name_the_pin_and_not_some_other_commit() -> None:
    wrong_pin: list[str] = []
    for path in _files_with_marker(REPO_ROOT, SCANNED_ROOTS):
        for number, line in _marker_comment_lines(path):
            if PROVENANCE_MARKER not in line:
                relative = path.relative_to(REPO_ROOT).as_posix()
                wrong_pin.append(f"{relative}:{number}")

    assert not wrong_pin, f"以下溯源标记不是 {PROVENANCE_MARKER}：{wrong_pin}"


def test_reverse_guard_sees_markers_below_the_header_line_budget(
    tmp_path: Path,
) -> None:
    # 回归：反向守卫曾只读前 5 行，把标记压到第 8 行即可绕过登记义务。
    root = tmp_path / "producer" / "tests"
    root.mkdir(parents=True)
    stray = root / "leak_helper.py"
    stray.write_text(
        '"""' + "\n".join(f"docstring line {n}" for n in range(1, 7)) + '"""\n'
        f"# {PROVENANCE_MARKER} packages/common/bogus.py\n",
        encoding="utf-8",
    )

    found = _files_with_marker(tmp_path, (Path("producer") / "tests",))

    assert found == [stray]
    marker_line = _marker_comment_lines(stray)[0][0]
    assert marker_line == 7 > HEADER_LINE_BUDGET
    assert not _header_marker_lines(stray)


def test_forward_guard_rejects_docstring_form_markers(tmp_path: Path) -> None:
    """形式维度回归：溯源标记搬进 docstring，正向断言必须不认。

    位置维度（上一条）关上后仍开着的另一半：正向侧曾是「前 5 行裸子串」，把已登记
    快照的溯源行搬进模块 docstring，正向依旧 PASS，而反向守卫（注释锚）根本看不见
    这种形式——同一不变量的两个方向对「什么算溯源头部」口径分叉。
    """

    source = "workers/data_adapters/region.py"
    disguised = tmp_path / "region_docstring.py"
    disguised.write_text(
        f'"""{PROVENANCE_MARKER} {source}\n\n伪装成模块 docstring 的溯源行。"""\n',
        encoding="utf-8",
    )
    proper = tmp_path / "region_comment.py"
    proper.write_text(
        f'# {PROVENANCE_MARKER} {source}\n"""真正的模块 docstring。"""\n',
        encoding="utf-8",
    )

    # 旧口径（前 5 行裸子串）会把 docstring 形式判为合格头部——这正是被修掉的洞。
    first_lines = disguised.read_text(encoding="utf-8").splitlines()[
        :HEADER_LINE_BUDGET
    ]
    assert f"{PROVENANCE_MARKER} {source}" in "\n".join(first_lines)

    assert not _provenance_header_lines(disguised, source), (
        "docstring 形式不得算溯源头部"
    )
    assert not _marker_comment_lines(disguised), "反向侧同样看不见 docstring 形式"

    # 正对照：注释形式命中，且正反向共用同一谓词。
    assert _provenance_header_lines(proper, source) == [
        (1, f"# {PROVENANCE_MARKER} {source}")
    ]
    assert _files_with_marker(tmp_path, (Path("."),)) == [proper]


def test_reverse_guard_catches_a_bare_marker_line_anywhere(tmp_path: Path) -> None:
    """收紧后仍保留的那一半：**裸溯源头部行出现在未登记文件的任何位置 ⇒ 反向守卫必须抓到**。

    这是谓词从「注释行里任何位置的 `NWM@`」收紧成「整行即溯源头部形式」时最容易被顺手
    收掉的性质：反向不设行预算，把一份真拷贝的头部压到第 20 行、缩进进类体里，或多打
    几个空格，都不得让它对守卫隐形。

    与 `test_reverse_guard_sees_markers_below_the_header_line_budget` 的分工：那条钉的是
    「行预算只作用于正向」这一维（`_header_marker_lines` 为空而 `_marker_comment_lines`
    非空）；本条钉的是收紧后的**形式容差**——缩进、`#` 后多空格、行尾空白仍算命中。
    """

    root = tmp_path / "producer" / "src" / "yd_producer" / "store"
    root.mkdir(parents=True)
    variants = {
        "plain.py": f"# {PROVENANCE_MARKER} packages/common/a.py\n",
        "indented.py": f"class C:\n    # {PROVENANCE_MARKER} packages/common/b.py\n",
        "padded.py": f"#   {PROVENANCE_MARKER}   packages/common/c.py   \n",
        "buried.py": (
            "\n".join(f"x{n} = {n}" for n in range(1, 20))
            + f"\n# {PROVENANCE_MARKER} packages/common/d.py\n"
        ),
    }
    for name, body in variants.items():
        (root / name).write_text(body, encoding="utf-8")

    found = _files_with_marker(tmp_path, SCANNED_ROOTS)

    assert [path.name for path in found] == sorted(variants)
    # 埋在第 20 行的那条对正向（行预算内）不可见，对反向可见——方向差异只在行预算上。
    assert not _header_marker_lines(root / "buried.py")
    assert _marker_comment_lines(root / "buried.py")


def test_reverse_guard_ignores_prose_trailing_inline_citations(
    tmp_path: Path,
) -> None:
    """收紧引入的那一半：**行内引用（路径后还有叙述文字）⇒ 反向守卫不得判它为未登记快照**。

    实例取自 master 的 `producer/src/yd_producer/config.py`（PR #41 落地）：它在字段上方
    顺带引用了 NWM 的一处事实，既不是任何文件的拷贝，在清单 §1 里也没有「NWM 原路径」与
    「剥离点」可填——旧口径把它判成未登记快照文件，等于给后续所有 PR 强加一条无法满足的
    义务（在实际 merge ref 上实测：修复前 `2 failed, 494 passed, 16 skipped`，
    修复后 `509 passed, 16 skipped`）。

    「路径之后不允许还有其它内容」这一条同时决定了正向侧的「恰为」：
    `# NWM@<pin> <原路径> 附注` 既不算未登记快照，也不算合格溯源头部。
    """

    root = tmp_path / "producer" / "src" / "yd_producer"
    root.mkdir(parents=True)
    source = "workers/mapping_builder/cli.py"
    citation = root / "config.py"
    citation.write_text(
        f"# {PROVENANCE_MARKER} `{source}` 的点分 module 名：随快照固定的版本化\n"
        "# 事实，非现场值（归属裁决见 #32）。\n"
        "nwm_mapping_builder_module: str\n",
        encoding="utf-8",
    )
    # 同族：路径写对、但后面追加了叙述文字。
    trailing = root / "note.py"
    trailing.write_text(
        f"# {PROVENANCE_MARKER} {source} 的点分 module 名\n", encoding="utf-8"
    )

    assert _files_with_marker(tmp_path, SCANNED_ROOTS) == []
    assert not _marker_comment_lines(citation)
    assert not _marker_comment_lines(trailing)
    assert not _provenance_header_lines(trailing, source)

    # 正对照：同一文件加一条整行形式的头部后，反向守卫立刻看见它。
    proper = root / "copied.py"
    proper.write_text(f"# {PROVENANCE_MARKER} {source}\n", encoding="utf-8")
    assert _files_with_marker(tmp_path, SCANNED_ROOTS) == [proper]


def test_marker_grammar_rejects_line_citations_glued_to_the_path() -> None:
    """`<原路径>` 是纯相对路径：`路径:行号（括注）` 紧贴路径的行级引用不是溯源头部。

    实例取自 issue #8 落地的 `producer/src/yd_producer/state/cfg_ic.py`（10 处 `#` 标记全是
    该形式）。旧文法 `(?P<source>\\S+)` 没有空格可停，把 `state_qc.py:43（逐字移植）`
    整块吞成路径，整行于是「看起来恰为 `NWM@<sha> <原路径>`」——该文件被反向守卫判成
    未登记快照（合入 master 的树上实测 `2 failed, 577 passed, 16 skipped`）。

    三个维度各有独立判别器（把文法放回 `\\S+` 时逐条实测变红）：**谓词**归本条；
    **反向**归真树上的 `..._registered_in_the_inventory` 与
    `test_landed_targets_are_exactly_...`；**正向**归下一条。
    """

    glued = f"# {PROVENANCE_MARKER} packages/common/state_qc.py:43（逐字移植）"
    assert _marker_fields(glued) is None
    assert (
        _marker_fields(
            f"        # {PROVENANCE_MARKER} "
            "packages/common/state_qc.py:431-435（调用点注释逐字保留）"
        )
        is None
    )
    # 正对照：去掉紧贴路径的尾随内容，同一条注释仍是合格头部（无行预算方向亦然）。
    bare = f"# {PROVENANCE_MARKER} packages/common/state_qc.py"
    assert _marker_fields(bare) == (PIN_SHORT, "packages/common/state_qc.py")

    # 收紧后的文法必须仍接纳清单 §1 声明的**每一条** NWM 原路径（27 条，非 11 条）。
    for _, source, _ in INVENTORY_ROWS:
        assert _marker_fields(f"# {PROVENANCE_MARKER} {source}") == (PIN_SHORT, source)


def test_forward_guard_ignores_line_citations_inside_the_header_budget(
    tmp_path: Path,
) -> None:
    """正向侧的判别器：行预算内的行级引用不得进入「命中的头部注释行」候选集。

    进了候选集，一份**缺**溯源头部的快照文件在正向失败信息里会拿一条行级引用冒充证据；
    字段相等只挡住断言本身，挡不住候选集被污染。反向侧同款由 `_marker_comment_lines`
    承担（见上一条）。
    """

    citation = tmp_path / "cfg_ic.py"
    citation.write_text(
        f"# {PROVENANCE_MARKER} packages/common/state_qc.py:43（逐字移植）\n",
        encoding="utf-8",
    )

    assert _header_marker_lines(citation) == []
    assert _marker_comment_lines(citation) == []
    # 正对照：换成纯路径后，同一位置立刻是命中的头部注释行。
    citation.write_text(
        f"# {PROVENANCE_MARKER} packages/common/state_qc.py\n", encoding="utf-8"
    )
    assert len(_header_marker_lines(citation)) == 1


def test_forward_guard_requires_the_path_to_match_exactly(tmp_path: Path) -> None:
    """正向侧的「恰为」：pin 与原路径是**字段相等**，不是子串包含。

    子串口径下 `# NWM@<pin> <原路径>xyz` 会以前缀匹配通过正向断言；`# NWM@<别的 sha>
    <原路径>` 的 pin 检查则由
    `test_registered_headers_name_the_pin_and_not_some_other_commit` 承担，故本谓词
    刻意不把 pin 写死（写死会让那条用例永远看不到错 pin 的行）。
    """

    source = "packages/common/safe_fs.py"
    exact = tmp_path / "exact.py"
    exact.write_text(f"#  {PROVENANCE_MARKER} {source}  \n", encoding="utf-8")
    prefixed = tmp_path / "prefixed.py"
    prefixed.write_text(f"# {PROVENANCE_MARKER} {source}.bak\n", encoding="utf-8")
    other_pin = tmp_path / "other_pin.py"
    other_pin.write_text(f"# NWM@deadbeef {source}\n", encoding="utf-8")

    assert _provenance_header_lines(exact, source)
    assert not _provenance_header_lines(prefixed, source)
    assert not _provenance_header_lines(other_pin, source)
    # 错 pin 的行仍在共用谓词的命中集里，否则错 pin 检查会被架空。
    assert _marker_comment_lines(other_pin)
    assert _marker_fields(f"# NWM@deadbeef {source}") == ("deadbeef", source)


def test_reverse_guard_does_not_self_trigger_on_the_marker_constant() -> None:
    # 锚在注释行上，本文件里的 PROVENANCE_MARKER 常量与断言消息不算命中。
    assert Path(__file__).resolve() not in {
        path.resolve() for path in _files_with_marker(REPO_ROOT, SCANNED_ROOTS)
    }


# --- DB-free 隔离：快照面零禁区面 ---------------------------------------------


def test_snapshot_scan_set_covers_every_landed_target_and_snapshot_package() -> None:
    scanned = _scan_files(REPO_ROOT, INVENTORY_TARGETS)
    landed = _landed_targets(REPO_ROOT, INVENTORY_TARGETS)

    assert landed, "已落地目标为空，检查本身失效"
    assert set(landed) <= set(scanned), "扫描集没覆盖全部已落地目标，检查本身失效"

    relatives = {path.relative_to(REPO_ROOT).as_posix() for path in scanned}
    for package in ("store", "raw"):
        prefix = f"producer/src/yd_producer/{package}/"
        assert any(item.startswith(prefix) for item in relatives), (
            f"{prefix} 未进入扫描集"
        )
    assert any(item.startswith("producer/tests/") for item in relatives), (
        "快照测试文件未进入扫描集（治理不变量的「含测试」半边）"
    )


def test_snapshot_files_are_free_of_db_and_scheduler_surfaces() -> None:
    scanned = _scan_files(REPO_ROOT, INVENTORY_TARGETS)

    assert scanned, "扫描集为空，检查本身失效"
    assert not _forbidden_hits(REPO_ROOT, scanned), (
        f"快照面出现禁区面：{_forbidden_hits(REPO_ROOT, scanned)}"
    )


def test_declared_grep_parser_reports_a_missing_or_ambiguous_anchor() -> None:
    """解析器自证：锚点找不到 / 找到多条时硬失败，而不是静默返回空词表。

    声明集若能静默解析成空元组，下面那条相等断言会被两侧同步收缩掏空——正是本文件
    对 §1 表解析同样防的那种真空通过。
    """

    one = "- 禁区 grep：`grep -rnE 'psycopg|os\\.getenv' producer/src` -> 零命中\n"

    assert _declared_forbidden_surfaces(one) == ("psycopg", "os.getenv")

    with pytest.raises(AssertionError, match="实为 0 条"):
        _declared_forbidden_surfaces("- 全目录 grep `psycopg|registry` -> 零命中\n")
    with pytest.raises(AssertionError, match="实为 2 条"):
        _declared_forbidden_surfaces(one * 2)


def test_forbidden_surfaces_match_the_declared_grep() -> None:
    """执行集（`FORBIDDEN_SURFACES`）== 声明集（tasks.md 的 `禁区 grep：` 命令）。

    这条是词表内容的**唯一**判别器。下面按 `FORBIDDEN_SURFACES` 参数化的用例证明的是
    另一个方向——「表里列的每一项都真的被 `_forbidden_hits` 执行」；它对**删项**是盲的，
    因为删掉一项时该参数用例随之消失（取消选择，用例数变少），没有任何用例变红。
    实测：把 `registry`/`journal`/`reservation`/`os.getenv` 四项从元组里删掉，
    本条报出这四项缺失。
    """

    declared = _declared_forbidden_surfaces()

    assert len(set(FORBIDDEN_SURFACES)) == len(FORBIDDEN_SURFACES), (
        f"执行集内有重复项：{FORBIDDEN_SURFACES}"
    )
    assert set(FORBIDDEN_SURFACES) == set(declared), (
        f"只在声明里（tasks.md 的禁区 grep）：{sorted(set(declared) - set(FORBIDDEN_SURFACES))}；"
        f"只在执行集里：{sorted(set(FORBIDDEN_SURFACES) - set(declared))}"
    )


@pytest.mark.parametrize("token", FORBIDDEN_SURFACES, ids=FORBIDDEN_SURFACES)
def test_each_forbidden_surface_is_individually_enforced(
    token: str, tmp_path: Path
) -> None:
    """逐项判别器：词表里的每一项各自都能在扫描集里被抓到。

    F16 之前，8 项里只有 4 项（`psycopg`/`DATABASE_URL`/`scheduler`/`os.environ`）在
    别处被顺带命中；把另外 4 项从元组里删掉，全套 362 passed 一条不红，而「删词表 +
    在 `store/object_path.py` 真落一个 registry/journal/reservation 面」这个组合态
    可达且无覆盖。本条给每一项配一个注入式 fixture，`test_forbidden_surfaces_match_
    the_declared_grep` 负责词表本身不被删项。

    AST 语义（issue #14 迁移）：`psycopg`/`scheduler`/`registry`/`journal`/
    `reservation` 只在 dotted import module path 成分命中；`DATABASE_URL` 只在
    精确 Name 或 `os.environ` 环境访问 key 命中；`os.getenv`/`os.environ` 只在
    对应 AST Call/Attribute/Subscript 路径命中。注入点用真实 import/name/call 形态。
    """

    injection = {
        "psycopg": "import psycopg\n",
        "DATABASE_URL": "DATABASE_URL = 'postgres://x'\n",
        "scheduler": "from app.scheduler import run\n",
        "registry": "from app.registry import models\n",
        "journal": "from app.journal import write\n",
        "reservation": "from app.reservation import reserve\n",
        "os.getenv": "import os\nx = os.getenv('DATABASE_URL')\n",
        "os.environ": "import os\nx = os.environ.get('DATABASE_URL')\n",
    }

    targets = {
        "producer/src/yd_producer/store/safe_fs.py": "packages/common/safe_fs.py"
    }
    _fake_repo(
        tmp_path,
        {
            "producer/src/yd_producer/store/safe_fs.py": "x = 1\n",
            "producer/src/yd_producer/store/leaked.py": injection[token],
        },
    )

    hits = _forbidden_hits(tmp_path, _scan_files(tmp_path, targets))

    expected_line = (
        1
        if token
        in {
            "psycopg",
            "DATABASE_URL",
            "scheduler",
            "registry",
            "journal",
            "reservation",
        }
        else 2
    )
    expected_tokens = (
        ("DATABASE_URL", token) if token in {"os.getenv", "os.environ"} else (token,)
    )
    assert hits == [
        f"producer/src/yd_producer/store/leaked.py:{expected_line}: {expected_token}"
        for expected_token in expected_tokens
    ]


def _fake_repo(tmp_path: Path, files: Mapping[str, str]) -> Path:
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_db_free_scan_follows_targets_that_later_task_groups_land(
    tmp_path: Path,
) -> None:
    # 回归：扫描集曾是写死的 (store, raw) 二元组，后续组落地的 canonical/、
    # 以及清单登记的快照**测试**文件都扫不到。
    targets = {
        "producer/src/yd_producer/canonical/converter.py": "workers/x/converter.py",
        "producer/tests/test_data_adapter_resolution.py": "tests/y.py",
        "producer/src/yd_producer/store/safe_fs.py": "packages/common/safe_fs.py",
    }
    _fake_repo(
        tmp_path,
        {
            "producer/src/yd_producer/canonical/converter.py": "import psycopg\n",
            "producer/tests/test_data_adapter_resolution.py": (
                'x = os.environ.get("DATABASE_URL")\n'
            ),
            "producer/src/yd_producer/store/safe_fs.py": "import os\n",
        },
    )

    hits = _forbidden_hits(tmp_path, _scan_files(tmp_path, targets))

    assert any("canonical/converter.py" in hit for hit in hits)
    assert any("test_data_adapter_resolution.py" in hit for hit in hits)


def test_db_free_scan_catches_unregistered_files_inside_a_snapshot_package(
    tmp_path: Path,
) -> None:
    # 回归：散落进快照包但没登记进清单的文件，仍须被 DB-free 扫描吃到。
    targets = {
        "producer/src/yd_producer/store/safe_fs.py": "packages/common/safe_fs.py"
    }
    _fake_repo(
        tmp_path,
        {
            "producer/src/yd_producer/store/safe_fs.py": "import os\n",
            "producer/src/yd_producer/store/helper.py": "from app.scheduler import run\n",
        },
    )

    hits = _forbidden_hits(tmp_path, _scan_files(tmp_path, targets))

    assert [hit.split(":")[0] for hit in hits] == [
        "producer/src/yd_producer/store/helper.py"
    ]


def test_db_free_scan_ignores_inert_prose_but_not_real_code(tmp_path: Path) -> None:
    """扫描口径：注释与 docstring 里的禁区词不算命中，**执行得到的代码**里算。

    两向都要有实例，否则这条口径退化成单向豁免：只证「散文不再命中」，把整段扫描改成
    `return []` 也照样绿；只证「代码仍命中」，则涂白规则是否生效无从判别。

    成因是真的：`state/state_qc.py` / `state/restamp.py` / 已合入 master 的
    `state/cfg_ic.py` 的模块头都写着「零数据库/scheduler 依赖」——**否定**陈述被裸串扫描
    判成了肯定命中。
    """

    targets = {
        "producer/src/yd_producer/store/safe_fs.py": "packages/common/safe_fs.py"
    }
    _fake_repo(
        tmp_path,
        {
            "producer/src/yd_producer/store/safe_fs.py": "x = 1\n",
            "producer/src/yd_producer/store/prose.py": (
                '"""零数据库/scheduler 依赖，不读 os.environ。"""\n'
                "\n"
                "# 本模块不碰 registry / journal / reservation。\n"
                "VALUE = 1\n"
                "\n"
                "\n"
                "def helper() -> int:\n"
                '    """不经 psycopg，也不读 DATABASE_URL。"""\n'
                "    return VALUE\n"
            ),
            "producer/src/yd_producer/store/real.py": (
                'import os\n\nURL = os.getenv("DATABASE_URL")\n'
            ),
        },
    )

    hits = _forbidden_hits(tmp_path, _scan_files(tmp_path, targets))

    assert hits == [
        "producer/src/yd_producer/store/real.py:3: DATABASE_URL",
        "producer/src/yd_producer/store/real.py:3: os.getenv",
    ]


def test_db_free_scan_stays_in_step_with_tokenize_row_numbers(tmp_path: Path) -> None:
    """`_code_lines` 的行数组与 tokenize 的行号必须同源，否则守卫会**漏报**。

    `str.splitlines()` 还在 `\\x0c`（换页）等字符上断行，`tokenize`（经 `io.StringIO`，
    `newline="\\n"`）不会。两套行号一旦错位，`_blank_prose` 就按 tokenize 给的行号去
    splitlines 的数组里涂白，把**真执行代码**当 docstring 抹掉——本例里 docstring 在
    第 4 行，错位 1 行后被抹掉的正是第 3 行的 `os.getenv("DATABASE_URL")`。

    断言必须落在**命中**上，不能只断 `len(_code_lines(src))`：`splitlines()` 对它自己
    那套行定义是自洽的，行数断言在坏实现上恒绿。
    """
    source = (
        "\x0c\n"
        "def helper() -> str:\n"
        '    url = os.getenv("DATABASE_URL")\n'
        '    """note: DB-free, honest"""\n'
        "    return url\n"
    )
    # 构造自检：这段源码确实能正常 tokenize（不走 fail-closed 回退），且两套行模型确实
    # 不一致——否则这条用例对错位变异体没有判别力。
    assert len(source.splitlines()) != len(source.split("\n")) - 1
    assert _code_lines(source)[2].strip() == 'url = os.getenv("DATABASE_URL")'

    targets = {
        "producer/src/yd_producer/store/safe_fs.py": "packages/common/safe_fs.py"
    }
    _fake_repo(
        tmp_path,
        {
            "producer/src/yd_producer/store/safe_fs.py": "x = 1\n",
            "producer/src/yd_producer/store/formfeed.py": source,
        },
    )

    hits = _forbidden_hits(tmp_path, _scan_files(tmp_path, targets))

    # 行号 3 = 解释器的行号（`\x0c` 只占第 1 行的一个字符，不另起一行）。
    assert hits == [
        "producer/src/yd_producer/store/formfeed.py:3: DATABASE_URL",
        "producer/src/yd_producer/store/formfeed.py:3: os.getenv",
    ]


def test_db_free_scan_still_sees_code_that_precedes_a_trailing_comment(
    tmp_path: Path,
) -> None:
    """行尾注释的涂白 MUST 从**注释自己的起始列**开始，不能从第 0 列开始。

    `_blank_prose` 的 `begin = start_col if row == start_row else 0` 换成 `begin = 0`
    的变异体在全套下存活：既有用例里的注释全都**独占一行**（起始列的差别在那里不可
    观测）。而带行尾注释的代码行在坏实现下会被整行抹掉——本例的两行 pristine 报三条
    命中，`begin = 0` 下报零条。那是 fail-**OPEN**，正违 `_code_lines` 写死的
    「守卫宁可误报，不可漏报」。

    断言落在 `begin` 这一半上：兄弟的 `finish` 钳位改坏后行为近乎等价，对着它写的用例
    钉不住承重的这一半。
    """
    source = (
        "import os\n"
        'URL = os.getenv("DATABASE_URL")  # 说明：这里读环境变量\n'
        'Q = "psycopg"  # tail\n'
        'R = "registry"  # tail\n'
    )
    # 构造自检：正常路径确实**只**抹掉注释、保留其左侧的可执行字节（不走 fail-closed
    # 回退，也不是整行原样保留）。
    code_lines = _code_lines(source)
    assert code_lines[1].startswith('URL = os.getenv("DATABASE_URL")')
    assert "环境变量" not in code_lines[1]
    assert code_lines[2].startswith('Q = "psycopg"')

    targets = {
        "producer/src/yd_producer/store/safe_fs.py": "packages/common/safe_fs.py"
    }
    _fake_repo(
        tmp_path,
        {
            "producer/src/yd_producer/store/safe_fs.py": "x = 1\n",
            "producer/src/yd_producer/store/inline.py": source,
        },
    )

    hits = _forbidden_hits(tmp_path, _scan_files(tmp_path, targets))

    # AST 语义: Q="psycopg" 与 R="registry" 是普通字面量,不命中。
    assert hits == [
        "producer/src/yd_producer/store/inline.py:2: DATABASE_URL",
        "producer/src/yd_producer/store/inline.py:2: os.getenv",
    ]


def test_db_free_scan_falls_back_to_raw_lines_when_the_source_cannot_be_tokenized(
    tmp_path: Path,
) -> None:
    """`_code_lines` 的 fail-closed 承诺（「宁可误报，不可漏报」）本身要有用例。

    把 `except (TokenError, SyntaxError)` 分支改成 `return []` 的变异体在 round 2 前
    全套 777 条全绿——没有任何用例喂过不可 tokenize 的源。
    """
    source = (
        'BROKEN = "unterminated\n'
        "# 注释里的 psycopg 在正常路径上会被涂白\n"
        'URL = os.getenv("DATABASE_URL")\n'
    )
    # 构造自检：确实走了回退——正常路径会把第 2 行的注释涂白，回退则逐字保留裸行。
    assert _code_lines(source) == source.split("\n")

    targets = {
        "producer/src/yd_producer/store/safe_fs.py": "packages/common/safe_fs.py"
    }
    _fake_repo(
        tmp_path,
        {
            "producer/src/yd_producer/store/safe_fs.py": "x = 1\n",
            "producer/src/yd_producer/store/unparseable.py": source,
        },
    )

    hits = _forbidden_hits(tmp_path, _scan_files(tmp_path, targets))

    assert hits == [
        "producer/src/yd_producer/store/unparseable.py:2: psycopg",
        "producer/src/yd_producer/store/unparseable.py:3: DATABASE_URL",
        "producer/src/yd_producer/store/unparseable.py:3: os.getenv",
    ]


def test_db_free_scan_leaves_non_snapshot_test_files_alone(tmp_path: Path) -> None:
    # 自写测试（禁区词表、正文里出现 "scheduler" 字样的散文）不是快照文件。
    targets = {"producer/tests/test_object_path.py": "tests/test_storage.py"}
    _fake_repo(
        tmp_path,
        {
            "producer/tests/test_object_path.py": "import os\n",
            "producer/tests/test_manifest.py": '"""排程 scheduler 字样的散文。"""\n',
        },
    )

    assert not _forbidden_hits(tmp_path, _scan_files(tmp_path, targets))
