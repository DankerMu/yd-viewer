"""任务 2.3：快照溯源头部检查（双向）与快照面的 DB-free 隔离检查。

数据源是 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md` 的 §1 快照清单
表本身（`| 能力项 | NWM 原路径 | 目标路径 | 剥离点 | 备注 |`），在测试时解析，**不**在
本文件里转录一份 Python 副本——转录副本会与清单漂移，且会要求后续任务组手工维护第二份
名单。正向断言对表内每个已落地的目标路径生效，反向守卫强制后续组落地的快照文件必须先
登记进清单表。

守卫的「执行集」必须等于它「声明的集合」，因此三条口径都从清单派生、不写死：

1. DB-free 扫描集 = 已落地的清单目标（含快照**测试**文件）∪ 这些目标所在的
   `producer/src/yd_producer/<pkg>/` 包目录整目录（未登记的散落文件也逃不掉）。
   后续任务组落地新目标时扫描集自动扩张，无需改本文件。
2. 反向守卫按 grep 语义扫**整文件**（清单前言只对正向断言规定「前 5 行内」预算），
   命中口径锚在注释行上。
3. §1 表体的每一行都必须解析成功：畸形行是硬失败，不再静默跳过。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY = (
    REPO_ROOT
    / "openspec"
    / "changes"
    / "m2-producer-core"
    / "nwm-snapshot-inventory.md"
)

PIN_SHORT = "8ae9b8f2"
PROVENANCE_MARKER = f"NWM@{PIN_SHORT}"

#: 溯源头必须落在文件的前几行（清单前言的固定格式 + fixture 的证据口径「前 5 行内」）。
#: 只约束正向断言；反向守卫按 grep 语义扫整文件。
HEADER_LINE_BUDGET = 5

#: 反向守卫的扫描根：任何带溯源标记的文件都必须先登记进清单表。
SCANNED_ROOTS = (
    Path("producer") / "src" / "yd_producer",
    Path("producer") / "tests",
)

#: 快照源模块的包根；清单目标落在其下 `<pkg>/` 的，整个 `<pkg>/` 进 DB-free 扫描集。
SNAPSHOT_PACKAGE_ROOT = Path("producer") / "src" / "yd_producer"

FORBIDDEN_SURFACES = (
    "psycopg",
    "DATABASE_URL",
    "scheduler",
    "registry",
    "journal",
    "reservation",
    "os.getenv",
    "os.environ",
)

_BACKTICKED = re.compile(r"`([^`]+)`")

#: 溯源标记的命中口径。锚在注释行上，而不是整文件裸串匹配：裸串会命中本文件里
#: 用 f-string 拼出的 PROVENANCE_MARKER 常量与断言消息本身，从而逼出一份手工豁免
#: 名单——那正是本守卫要消灭的第二份名单。
_MARKER_COMMENT = re.compile(r"^[ \t]*#.*NWM@")


# --- §1 表解析 ---------------------------------------------------------------


def _inventory_body_lines() -> list[str]:
    """返回 §1 表的表体行（去掉表头与分隔行），不做单元格数量过滤。

    只取 `## 1.` 与下一个 `## ` 之间的区段：§2 的能力项对照表也含反引号包裹的路径，
    整文件扫描会把它们一并吃进来。表体行的**条数**是下面解析完整性检查的基准，所以
    这一步刻意不丢弃任何行。
    """

    text = INVENTORY.read_text(encoding="utf-8")
    section = re.search(r"^## 1\..*?(?=^## )", text, flags=re.MULTILINE | re.DOTALL)
    assert section is not None, f"{INVENTORY} 里找不到 §1 快照清单区段"

    body: list[str] = []
    for line in section.group(0).splitlines():
        if not line.startswith("|"):
            continue
        first_cell = line.strip().strip("|").split("|")[0].strip()
        if first_cell == "能力项" or set(first_cell) <= {"-", ":"}:
            continue
        body.append(line)
    return body


def _parse_inventory_rows(
    body_lines: Iterable[str],
) -> tuple[list[tuple[str, str]], list[str]]:
    """把表体行解析成 (目标路径, NWM 原路径) 对；解析不了的行进 `malformed`。

    畸形行**不能**静默跳过：清单里塞进一条单元格数不对的行（例如 `剥离点` 里出现未
    转义的 `|`），会让该行的目标路径连同它的正向溯源断言一起消失，守卫全绿而覆盖变窄。
    """

    rows: list[tuple[str, str]] = []
    malformed: list[str] = []
    for line in body_lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            malformed.append(f"单元格数为 {len(cells)}（应为 5）: {line[:100]}")
            continue
        source_match = _BACKTICKED.search(cells[1])
        target_match = _BACKTICKED.search(cells[2])
        if source_match is None:
            malformed.append(f"缺少反引号包裹的 NWM 原路径: {line[:100]}")
            continue
        if target_match is None:
            malformed.append(f"缺少反引号包裹的目标路径: {line[:100]}")
            continue
        target = target_match.group(1)
        if not target.startswith("producer/"):
            malformed.append(f"目标路径应落在 producer/ 下: {target}")
            continue
        rows.append((target, source_match.group(1)))
    return rows, malformed


INVENTORY_BODY_LINES = _inventory_body_lines()
INVENTORY_ROWS, MALFORMED_ROWS = _parse_inventory_rows(INVENTORY_BODY_LINES)
INVENTORY_TARGETS = {target: source for target, source in INVENTORY_ROWS}


# --- 扫描集派生 --------------------------------------------------------------


def _landed_targets(repo_root: Path, targets: Iterable[str]) -> list[Path]:
    return sorted(
        repo_root / target for target in targets if (repo_root / target).is_file()
    )


def _scan_files(repo_root: Path, targets: Iterable[str]) -> list[Path]:
    """DB-free 扫描集：已落地的清单目标 ∪ 它们所在的 yd_producer 包目录整目录。

    目标派生的那一半覆盖快照**测试**文件（治理不变量的「含测试」半边），包目录派生
    的那一半兜住散落进快照包、却没登记进清单的文件。测试目录不整目录展开：
    `producer/tests/` 下的自写测试（含本文件的禁区词表、`test_manifest.py` 正文里
    出现的 "scheduler" 字样）不是快照文件，不受 DB-free 口径约束。
    """

    package_root = repo_root / SNAPSHOT_PACKAGE_ROOT
    files: set[Path] = set()
    for path in _landed_targets(repo_root, targets):
        files.add(path)
        try:
            relative = path.relative_to(package_root)
        except ValueError:
            continue
        if len(relative.parts) >= 2:
            files.update((package_root / relative.parts[0]).rglob("*.py"))
    return sorted(files)


def _forbidden_hits(repo_root: Path, files: Iterable[Path]) -> list[str]:
    hits: list[str] = []
    for path in files:
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for token in FORBIDDEN_SURFACES:
                if token in line:
                    hits.append(
                        f"{path.relative_to(repo_root).as_posix()}:{number}: {token}"
                    )
    return hits


# --- 溯源标记命中（反向守卫共用） --------------------------------------------


def _marker_comment_lines(path: Path) -> list[tuple[int, str]]:
    """整文件扫描，返回带溯源标记的注释行 `(行号, 行内容)`。

    反向方向的证据口径是 grep 语义、无行预算：把标记压到第 6 行以后就能绕过守卫，
    正是 round 1 复核抓到的盲区。
    """

    return [
        (number, line)
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if _MARKER_COMMENT.search(line)
    ]


def _files_with_marker(repo_root: Path, roots: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        for path in sorted((repo_root / root).rglob("*.py")):
            if _marker_comment_lines(path):
                found.append(path)
    return found


def _header(path: Path) -> str:
    with path.open(encoding="utf-8") as handle:
        lines = []
        for index, line in enumerate(handle):
            if index >= HEADER_LINE_BUDGET:
                break
            lines.append(line)
    return "".join(lines)


# --- 表本身没被解析空、也没被静默丢行 -----------------------------------------


def test_inventory_table_parses_every_body_row() -> None:
    # 解析器一旦静默返回空集合或丢行，正向断言会真空通过、反向守卫会全量误报。
    assert not MALFORMED_ROWS, f"§1 表体存在无法解析的行：{MALFORMED_ROWS}"
    assert len(INVENTORY_ROWS) == len(INVENTORY_BODY_LINES), (
        f"§1 表体 {len(INVENTORY_BODY_LINES)} 行只解析出 {len(INVENTORY_ROWS)} 行"
    )
    assert len(INVENTORY_BODY_LINES) >= 11, "§1 区段没被解析到（表体行过少）"
    assert len(INVENTORY_TARGETS) == len(INVENTORY_ROWS), "§1 出现重复目标路径"
    assert "producer/src/yd_producer/store/safe_fs.py" in INVENTORY_TARGETS


def test_malformed_body_rows_are_reported_instead_of_silently_dropped() -> None:
    # 回归：`剥离点` 单元格里出现未转义的 `|`（如引用类型标注 `str | None`）时，
    # 该行曾被静默丢弃，连同它的正向溯源断言一起消失。
    good = (
        "| 3 object-store | `packages/common/safe_fs.py` "
        "| `producer/src/yd_producer/store/safe_fs.py` | `无` | 整文件快照 |"
    )
    poisoned = (
        "| 9 新能力 | `packages/common/widget.py` "
        "| `producer/src/yd_producer/widget/widget.py` | 改写 `f(x: str | None)` | 备注 |"
    )

    rows, malformed = _parse_inventory_rows([good, poisoned])

    assert len(rows) == 1
    assert len(malformed) == 1, "畸形行必须被报告"
    assert "单元格数为 6" in malformed[0]


# --- 正向：表内已落地的目标路径必须带对应溯源头 -------------------------------


@pytest.mark.parametrize(
    ("target", "source"),
    INVENTORY_ROWS,
    ids=[target for target, _ in INVENTORY_ROWS],
)
def test_landed_snapshot_files_carry_their_provenance_header(
    target: str, source: str
) -> None:
    path = REPO_ROOT / target
    if not path.exists():
        pytest.skip(f"{target} 尚未落地（归后续任务组）")

    assert f"{PROVENANCE_MARKER} {source}" in _header(path), (
        f"{target} 的前 {HEADER_LINE_BUDGET} 行缺少溯源头 `{PROVENANCE_MARKER} {source}`"
    )


def test_at_least_the_issue_5_snapshot_files_have_landed() -> None:
    landed = _landed_targets(REPO_ROOT, INVENTORY_TARGETS)
    assert len(landed) >= 11, f"已落地的快照文件只有 {landed}"


# --- 反向：带溯源标记的文件必须登记在清单表里 --------------------------------


def test_every_file_with_a_provenance_header_is_registered_in_the_inventory() -> None:
    found = _files_with_marker(REPO_ROOT, SCANNED_ROOTS)
    assert len(found) >= 11, f"反向扫描只命中 {len(found)} 个文件，检查本身失效"

    unregistered = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in found
        if path.relative_to(REPO_ROOT).as_posix() not in INVENTORY_TARGETS
    ]

    assert not unregistered, (
        "以下文件带溯源标记但不在 nwm-snapshot-inventory.md §1 路径表内，"
        f"请先登记再落地：{unregistered}"
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
    assert PROVENANCE_MARKER not in _header(stray)


def test_reverse_guard_does_not_self_trigger_on_the_marker_constant() -> None:
    # 锚在注释行上，本文件里的 PROVENANCE_MARKER 常量与断言消息不算命中。
    assert Path(__file__).resolve() not in {
        path.resolve() for path in _files_with_marker(REPO_ROOT, SCANNED_ROOTS)
    }


# --- DB-free 隔离：快照面零禁区面 ---------------------------------------------


def test_snapshot_scan_set_covers_every_landed_target_and_snapshot_package() -> None:
    scanned = _scan_files(REPO_ROOT, INVENTORY_TARGETS)
    landed = _landed_targets(REPO_ROOT, INVENTORY_TARGETS)

    assert len(landed) >= 11
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
            "producer/src/yd_producer/store/helper.py": "from app import scheduler\n",
        },
    )

    hits = _forbidden_hits(tmp_path, _scan_files(tmp_path, targets))

    assert [hit.split(":")[0] for hit in hits] == [
        "producer/src/yd_producer/store/helper.py"
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
