r"""前沿判定用的 `YD_ROOT` 目录树构造器（程序化，零二进制入库）。

独立性硬约束：本模块 MUST NOT 从 `yd_producer` import 任何东西。cycle 与 header 分钟
时标的期望值由**构造过程**记录（`StateSpec.absolute_minute` 是写入时算出来的那一个数），
不由被测控制器回读——否则「header 对应绝对 T」的断言退化为「实现与自己一致」的永真式。

分钟时标的换算在这里只用 stdlib `datetime`，且测试里另有一条**手算**交叉校验：
1970-01-01 到 2026-08-26 共 20691 天 → 20691*1440 = 29795040 分钟（2026-08-26T00Z），
+720 → 29795760（12Z）。

发射包络覆盖前沿判定的接受域与拒绝域：
- header 布局：native 3-token `<mesh> <mesh-state-columns> <minute>` 与兼容 4-token
  `<mesh> <river> <lake> <minute>`
- header 时标：绝对分钟（正确 / 冒充的 T-12h）、相对分钟（`0.000000` / `720.000000`）、
  `nan` / `inf` / `-inf`、2 与 5 数值 token、非数值行、空文件
- 条目形态：普通文件、指向合法状态的 symlink、断链 symlink、目录、FIFO、`chmod 0o000`、
  非 UTF-8 字节、超字节上界
- 非法名：发布临时名 `.<cycle>.cfg.ic.tmp`、点文件、10 位但非法日期 `2026023100`、
  `9999999999`、非 10 位目录、`.tmp-<cycle>/` 半删目录
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

CYCLE_ID_FORMAT = "%Y%m%d%H"
STATE_SUFFIX = ".cfg.ic"

#: native header 的第二个数值 token 是 mesh 状态列数（不是 river 元素数）。
MESH_COUNT = 23106
MESH_STATE_COLUMNS = 6
#: 兼容 4-token 布局的 river / lake 计数。
RIVER_COUNT = 413
LAKE_COUNT = 1

#: 状态文件体：前沿只读 header 行，body 只需存在且不参与判定。
_BODY_LINES = (
    "Index\tCanopy\tSnow\tSurface\tUnsat\tGW",
    "1\t0.100000\t0.000000\t0.010000\t0.200000\t1.500000",
    "Index\tRiver_Stage",
    "1\t0.300000",
)


def cycle_id(cycle: datetime) -> str:
    return cycle.strftime(CYCLE_ID_FORMAT)


def parse_cycle(cycle_text: str) -> datetime:
    """测试侧的 cycle 字面量 → UTC aware 时刻（构造用，不是被测实现）。"""
    return datetime.strptime(cycle_text, CYCLE_ID_FORMAT).replace(tzinfo=UTC)


def absolute_minute(cycle: datetime) -> int:
    """该时刻的绝对 epoch 分钟数（写入 header 的那个数）。"""
    return round(cycle.timestamp() / 60)


def shift(cycle_text: str, hours: int) -> datetime:
    return parse_cycle(cycle_text) + timedelta(hours=hours)


def header_line(minute_text: str, *, layout: str = "native") -> str:
    """按布局拼一行 header。`layout` ∈ {native, compat}。"""
    if layout == "native":
        counts = (MESH_COUNT, MESH_STATE_COLUMNS)
    elif layout == "compat":
        counts = (MESH_COUNT, RIVER_COUNT, LAKE_COUNT)
    else:  # pragma: no cover - 构造器自检
        raise ValueError(f"unknown header layout: {layout!r}")
    return "\t".join([*(str(value) for value in counts), minute_text])


def state_payload(minute_text: str, *, layout: str = "native") -> bytes:
    lines = (header_line(minute_text, layout=layout), *_BODY_LINES)
    return ("\n".join(lines) + "\n").encode("utf-8")


def absolute_minute_text(cycle: datetime) -> str:
    """真实写入侧的记法：`valid_time.timestamp()/60` 的六位小数浮点文本。"""
    return f"{absolute_minute(cycle)}.000000"


@dataclass
class YdRootBuilder:
    """在 `root` 下构造 `output/` 与 `states/` 子树，并记录构造期望值。"""

    root: Path
    written_dones: dict[str, set[str]] = field(default_factory=dict)
    written_states: dict[str, dict[str, int | None]] = field(default_factory=dict)

    # --- output/ 侧 ---

    def source_output_dir(self, cycle_text: str, source: str) -> Path:
        return self.root / "output" / cycle_text / source

    def write_done(self, cycle_text: str, source: str) -> Path:
        """写一个普通文件 `DONE`（products-contract §4.1：空文件即唯一完成判据）。"""
        directory = self.source_output_dir(cycle_text, source)
        directory.mkdir(parents=True, exist_ok=True)
        done = directory / "DONE"
        done.write_bytes(b"")
        self.written_dones.setdefault(source, set()).add(cycle_text)
        return done

    def write_done_as_directory(self, cycle_text: str, source: str) -> Path:
        """`DONE` 是**目录**：不是完成（不计入 `written_dones`）。"""
        done = self.source_output_dir(cycle_text, source) / "DONE"
        done.mkdir(parents=True, exist_ok=True)
        return done

    def write_done_as_dangling_symlink(self, cycle_text: str, source: str) -> Path:
        """`DONE` 是**断链 symlink**：不是完成。"""
        directory = self.source_output_dir(cycle_text, source)
        directory.mkdir(parents=True, exist_ok=True)
        done = directory / "DONE"
        done.symlink_to(directory / "nowhere-DONE-target")
        return done

    def write_output_dat(self, cycle_text: str, source: str) -> Path:
        """只写 DAT 不写 `DONE`：上次发布中断的半成品。"""
        directory = self.source_output_dir(cycle_text, source)
        directory.mkdir(parents=True, exist_ok=True)
        dat = directory / "yd.rivqdown.dat"
        dat.write_text("# partial\n", encoding="utf-8")
        return dat

    def write_output_clutter(self) -> None:
        """`output/` 下的非法条目：stray 文件、半删目录、非 10 位目录、点文件。"""
        output = self.root / "output"
        output.mkdir(parents=True, exist_ok=True)
        (output / ".DS_Store").write_bytes(b"\x00\x01")
        (output / "README.txt").write_text("stray\n", encoding="utf-8")
        (output / ".tmp-2026082600").mkdir(exist_ok=True)
        (output / ".tmp-2026082600" / "gfs").mkdir(exist_ok=True)
        (output / "2026082600extra").mkdir(exist_ok=True)
        (output / "20260826").mkdir(exist_ok=True)
        # 10 位数字但不是合法日期 —— 形态门过、解析门不过
        for illegal in ("2026023100", "9999999999"):
            source_dir = output / illegal / "gfs"
            source_dir.mkdir(parents=True, exist_ok=True)
            (source_dir / "DONE").write_bytes(b"")

    # --- states/ 侧 ---

    def states_dir(self, source: str) -> Path:
        return self.root / "states" / source

    def state_path(self, cycle_text: str, source: str) -> Path:
        return self.states_dir(source) / f"{cycle_text}{STATE_SUFFIX}"

    def _prepare(self, cycle_text: str, source: str) -> Path:
        directory = self.states_dir(source)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{cycle_text}{STATE_SUFFIX}"

    def write_state(
        self,
        cycle_text: str,
        source: str,
        *,
        minute_text: str | None = None,
        layout: str = "native",
    ) -> Path:
        """写一份状态文件。默认 header 分钟时标 = 该 cycle 的**绝对** epoch 分钟。"""
        path = self._prepare(cycle_text, source)
        cycle = parse_cycle(cycle_text)
        recorded: int | None
        if minute_text is None:
            minute_text = absolute_minute_text(cycle)
            recorded = absolute_minute(cycle)
        else:
            recorded = None
        path.write_bytes(state_payload(minute_text, layout=layout))
        self.written_states.setdefault(source, {})[cycle_text] = recorded
        return path

    def write_state_bytes(self, cycle_text: str, source: str, payload: bytes) -> Path:
        path = self._prepare(cycle_text, source)
        path.write_bytes(payload)
        self.written_states.setdefault(source, {})[cycle_text] = None
        return path

    def write_state_as_symlink_to(
        self, cycle_text: str, source: str, target: Path
    ) -> Path:
        """状态文件是**指向另一份合法状态**的 symlink（可读性判定 MUST 跟随）。"""
        path = self._prepare(cycle_text, source)
        path.symlink_to(target)
        return path

    def write_state_as_dangling_symlink(self, cycle_text: str, source: str) -> Path:
        path = self._prepare(cycle_text, source)
        path.symlink_to(self.states_dir(source) / "nowhere-state-target")
        return path

    def write_state_as_directory(self, cycle_text: str, source: str) -> Path:
        path = self._prepare(cycle_text, source)
        path.mkdir()
        return path

    def write_state_as_fifo(self, cycle_text: str, source: str) -> Path:
        """FIFO：非普通文件。`os.mkfifo` 而非 AF_UNIX socket——macOS 的 `sun_path`
        上限约 104 字节，`tmp_path` 很容易越界而让用例假失败。"""
        path = self._prepare(cycle_text, source)
        os.mkfifo(path)
        return path

    def write_state_unreadable(self, cycle_text: str, source: str) -> Path:
        path = self.write_state(cycle_text, source)
        path.chmod(0o000)
        return path

    def write_state_invalid_utf8(self, cycle_text: str, source: str) -> Path:
        return self.write_state_bytes(cycle_text, source, b"\xff\xfe\x00bad header\n")

    def write_state_oversized(
        self, cycle_text: str, source: str, *, limit_bytes: int
    ) -> Path:
        """写一份**超出上界**的状态文件（稀疏文件，不真占盘）。"""
        path = self._prepare(cycle_text, source)
        cycle = parse_cycle(cycle_text)
        head = state_payload(absolute_minute_text(cycle))
        with open(path, "wb") as handle:
            handle.write(head)
            handle.truncate(limit_bytes + 1)
        self.written_states.setdefault(source, {})[cycle_text] = None
        return path

    def write_state_at_size(
        self, cycle_text: str, source: str, *, size_bytes: int
    ) -> Path:
        """写一份**恰好 `size_bytes`** 字节的合法状态文件（header 在首行，尾部是空洞）。

        用于钉死「恰好上界」这一侧的分类：上界内的文件按普通文件走三判，与
        `write_state_oversized`（上界 +1）成对。
        """
        path = self._prepare(cycle_text, source)
        cycle = parse_cycle(cycle_text)
        head = state_payload(absolute_minute_text(cycle))
        assert len(head) <= size_bytes
        with open(path, "wb") as handle:
            handle.write(head)
            handle.truncate(size_bytes)
        self.written_states.setdefault(source, {})[cycle_text] = absolute_minute(cycle)
        return path

    def write_state_trailing_header(
        self, cycle_text: str, source: str, *, blank_bytes: int
    ) -> Path:
        """`blank_bytes` 个纯换行**在前**、header 行在后的合法状态文件。

        首个非空行要跨越整段空行才能取到，是「首行有界读是否分块」的判别构造：无界读法
        的峰值会随 `blank_bytes` 线性放大到文件大小的数倍。
        """
        path = self._prepare(cycle_text, source)
        cycle = parse_cycle(cycle_text)
        with open(path, "wb") as handle:
            handle.write(b"\n" * blank_bytes)
            handle.write(state_payload(absolute_minute_text(cycle)))
        self.written_states.setdefault(source, {})[cycle_text] = absolute_minute(cycle)
        return path

    def write_state_header_padded(
        self, cycle_text: str, source: str, *, line_bytes: int
    ) -> Path:
        """写一份 header 行被尾随空格垫到**恰好 `line_bytes`** 字节的合法状态文件。

        垫的是 `str.split()` 会丢掉的尾随空格，所以这条行仍是一条能解析的真 header：
        上界之内的那一侧要走到 runnable，而不是因为别的理由绿。与 `line_bytes = cap + 1`
        成对，钉死候选 header 行上界这道闸的边界（上界本身由测试侧传入——本模块 MUST NOT
        import `yd_producer`）。
        """
        path = self._prepare(cycle_text, source)
        cycle = parse_cycle(cycle_text)
        head = header_line(absolute_minute_text(cycle))
        assert len(head.encode("utf-8")) <= line_bytes
        padded = head + " " * (line_bytes - len(head.encode("utf-8")))
        payload = padded + "\n" + "\n".join(_BODY_LINES) + "\n"
        raw = payload.encode("utf-8")
        assert raw.index(b"\n") == line_bytes
        path.write_bytes(raw)
        self.written_states.setdefault(source, {})[cycle_text] = None
        return path

    def write_state_newline_free(
        self, cycle_text: str, source: str, *, size_bytes: int, payload: str
    ) -> Path:
        """写一份**整篇没有 `\\n`** 的状态文件：整个文件就是一条候选 header 行。

        `payload` ∈ {printable, nul}：前者是可打印字节（`b"1 "` 重复，流式写入，不在测试
        进程里驻留整份载荷），后者是 `truncate` 出来的全 NUL 稀疏文件（NUL 是合法 UTF-8
        且不是 `str.strip()` 的空白，所以同样构成一条巨大的非空行）。两种载荷都是候选行
        上界的判别构造：无上界的读法会把整份文件实体化成 str 再 `.split()`。
        """
        path = self._prepare(cycle_text, source)
        if payload == "printable":
            block = b"1 " * (512 * 1024)  # 1 MiB，无 `\n`
            assert b"\n" not in block
            whole, tail = divmod(size_bytes, len(block))
            with open(path, "wb") as handle:
                handle.writelines(block for _ in range(whole))
                handle.write(block[:tail])
        elif payload == "nul":
            with open(path, "wb") as handle:
                handle.truncate(size_bytes)
        else:  # pragma: no cover - 构造器自检
            raise ValueError(f"unknown payload shape: {payload!r}")
        assert path.stat().st_size == size_bytes
        self.written_states.setdefault(source, {})[cycle_text] = None
        return path

    def write_states_clutter(self, source: str) -> None:
        """`states/<source>/` 下的非法条目：发布临时名、子目录、点文件、非法日期名。"""
        directory = self.states_dir(source)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".2026082612.cfg.ic.tmp").write_bytes(b"partial\n")
        (directory / "2026082612.cfg.ic.tmp").write_bytes(b"partial\n")
        (directory / ".hidden").write_bytes(b"")
        (directory / "scratch").mkdir(exist_ok=True)
        (directory / "notes.txt").write_text("stray\n", encoding="utf-8")
        (directory / "20260826.cfg.ic").write_bytes(b"short name\n")
        for illegal in ("2026023100", "9999999999"):
            (directory / f"{illegal}{STATE_SUFFIX}").write_bytes(
                state_payload("1.000000")
            )


class RecordingRawComplete:
    """记录型 `raw_complete` fake：记下被问过哪些 cycle，按集合作答。"""

    def __init__(self, complete_cycles: set[str]) -> None:
        self._complete = set(complete_cycles)
        self.calls: list[datetime] = []

    def __call__(self, cycle: datetime) -> bool:
        self.calls.append(cycle)
        return cycle_id(cycle) in self._complete

    @property
    def asked(self) -> list[str]:
        return [cycle_id(cycle) for cycle in self.calls]


def snapshot_tree(root: Path) -> dict[str, tuple[str, int, int, str]]:
    """整棵树的递归快照：相对路径 → (条目类型, `st_mode`, size, 内容摘要)。

    维度必须钉死到内容摘要：只比对路径集合的话，**等长原地改写**在比对下不可见。
    `lstat` 而非 `stat`：断链 symlink 也要能快照。只有普通文件才 `open()` 读摘要——
    对 FIFO 做 `open()` 会永久阻塞。
    """
    snapshot: dict[str, tuple[str, int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        relative = str(path.relative_to(root))
        mode = info.st_mode
        if stat.S_ISLNK(mode):
            kind = "symlink"
            digest = hashlib.sha256(os.readlink(path).encode("utf-8")).hexdigest()
        elif stat.S_ISDIR(mode):
            kind = "dir"
            digest = ""
        elif stat.S_ISREG(mode):
            kind = "file"
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                # `chmod 0o000` 的文件读不到内容；其余维度仍参与比对
                digest = "<unreadable>"
        else:
            kind = "special"
            digest = ""
        snapshot[relative] = (kind, mode, info.st_size, digest)
    return snapshot
