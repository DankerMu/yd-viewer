"""任务 2.3：快照溯源头部检查（双向）与快照目录的 DB-free 隔离检查。

数据源是 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md` 的 §1 快照清单
表本身（`| 能力项 | NWM 原路径 | 目标路径 | 剥离点 | 备注 |`），在测试时解析，**不**在
本文件里转录一份 Python 副本——转录副本会与清单漂移，且会要求后续任务组手工维护第二份
名单。正向断言对表内每个已落地的目标路径生效，反向守卫强制后续组落地的快照文件必须先
登记进清单表。
"""

from __future__ import annotations

import re
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
HEADER_LINE_BUDGET = 5

SCANNED_ROOTS = (
    Path("producer") / "src" / "yd_producer",
    Path("producer") / "tests",
)

SNAPSHOT_DIRS = (
    Path("producer") / "src" / "yd_producer" / "store",
    Path("producer") / "src" / "yd_producer" / "raw",
)

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


def _inventory_rows() -> list[tuple[str, str]]:
    """解析清单 §1 表，返回 (目标路径, NWM 原路径) 对。

    只取 `## 1.` 与下一个 `## ` 之间的区段：§2 的能力项对照表也含反引号包裹的路径，
    整文件扫描会把它们一并吃进来。
    """

    text = INVENTORY.read_text(encoding="utf-8")
    section = re.search(r"^## 1\..*?(?=^## )", text, flags=re.MULTILINE | re.DOTALL)
    assert section is not None, f"{INVENTORY} 里找不到 §1 快照清单区段"

    rows: list[tuple[str, str]] = []
    for line in section.group(0).splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0] == "能力项" or set(cells[0]) <= {"-", ":"}:
            continue
        source_match = _BACKTICKED.search(cells[1])
        target_match = _BACKTICKED.search(cells[2])
        assert source_match is not None, (
            f"§1 行缺少反引号包裹的 NWM 原路径: {line[:80]}"
        )
        assert target_match is not None, f"§1 行缺少反引号包裹的目标路径: {line[:80]}"
        target = target_match.group(1)
        assert target.startswith("producer/"), (
            f"§1 目标路径应落在 producer/ 下: {target}"
        )
        rows.append((target, source_match.group(1)))
    return rows


INVENTORY_ROWS = _inventory_rows()
INVENTORY_TARGETS = {target: source for target, source in INVENTORY_ROWS}


def _header(path: Path) -> str:
    with path.open(encoding="utf-8") as handle:
        lines = []
        for index, line in enumerate(handle):
            if index >= HEADER_LINE_BUDGET:
                break
            lines.append(line)
    return "".join(lines)


def _scanned_python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCANNED_ROOTS:
        files.extend(sorted((REPO_ROOT / root).rglob("*.py")))
    return files


# --- 表本身没被解析空 ---------------------------------------------------------


def test_inventory_table_parses_into_a_non_trivial_path_map() -> None:
    # 解析器一旦静默返回空集合，下面的正向断言会真空通过、反向守卫会全量误报。
    assert len(INVENTORY_ROWS) >= 11
    assert len(INVENTORY_TARGETS) == len(INVENTORY_ROWS), "§1 出现重复目标路径"
    assert "producer/src/yd_producer/store/safe_fs.py" in INVENTORY_TARGETS


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
    landed = [target for target in INVENTORY_TARGETS if (REPO_ROOT / target).exists()]
    assert len(landed) >= 11, f"已落地的快照文件只有 {landed}"


# --- 反向：带 NWM 头的文件必须登记在清单表里 ---------------------------------


def test_every_file_with_a_provenance_header_is_registered_in_the_inventory() -> None:
    unregistered: list[str] = []
    for path in _scanned_python_files():
        if "NWM@" not in _header(path):
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative not in INVENTORY_TARGETS:
            unregistered.append(relative)

    assert not unregistered, (
        "以下文件头部带 NWM@ 溯源标记但不在 nwm-snapshot-inventory.md §1 路径表内，"
        f"请先登记再落地：{unregistered}"
    )


def test_registered_headers_name_the_pin_and_not_some_other_commit() -> None:
    wrong_pin: list[str] = []
    for path in _scanned_python_files():
        header = _header(path)
        if "NWM@" not in header:
            continue
        if PROVENANCE_MARKER not in header:
            wrong_pin.append(path.relative_to(REPO_ROOT).as_posix())

    assert not wrong_pin, f"以下文件的溯源头不是 {PROVENANCE_MARKER}：{wrong_pin}"


# --- DB-free 隔离：快照目录零禁区面 -------------------------------------------


@pytest.mark.parametrize("snapshot_dir", [d.as_posix() for d in SNAPSHOT_DIRS])
def test_snapshot_directories_are_free_of_db_and_scheduler_surfaces(
    snapshot_dir: str,
) -> None:
    root = REPO_ROOT / snapshot_dir
    assert root.is_dir(), f"{snapshot_dir} 不存在"

    hits: list[str] = []
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        scanned += 1
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for token in FORBIDDEN_SURFACES:
                if token in line:
                    hits.append(
                        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}: {token}"
                    )

    assert scanned > 0, f"{snapshot_dir} 下没有扫到任何 .py，检查本身失效"
    assert not hits, f"快照目录出现禁区面：{hits}"
