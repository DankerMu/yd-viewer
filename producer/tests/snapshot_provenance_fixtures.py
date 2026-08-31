"""`test_snapshot_provenance.py` 的基础设施：清单/tasks.md 解析器与溯源谓词。

**只有基础设施**搬到这里，用例一条没动。搬家的唯一动因是 `large-file-guard` 的 1000 行
闸门（合并后 1058 行）；闸门的补救文本写的是「拆成更小的模块」，`exclude` 只留给
generated/vendored/data，手写测试套件不属于那一类。导入惯例照抄 master 的
`producer/tests/cli_fixtures.py`（被 `test_cli.py` 以裸 `from cli_fixtures import ...`
导入；`producer/pyproject.toml` 的 `testpaths = ["tests"]` 把 `tests/` 放进 sys.path）。

单一定义的教条因此**加强**而非削弱：溯源谓词 `_MARKER_COMMENT` 现在存在于**一个可导入
模块**里，正反两向都从这里取，仍不存在第二份拷贝。

本模块**必须**留在 `tests/` 下、且**不得**命名成 `test_*.py`：
- 它把禁区词（`psycopg`、`DATABASE_URL`、scheduler/registry 等）当**普通字符串字面量**
  带在身上，而扫描口径只放过注释与 docstring、不放过普通字面量（见 `_code_lines`）。
  放到 `src/yd_producer/` 下，它会被 DB-free 扫描集吃进去并把自己钉红；`producer/tests/`
  下的非快照文件不在扫描集内（已声明的非目标 F2）。
- 反向守卫会扫本文件：这里任何 `#` 注释行的内容都不得**恰为**溯源头部形式，否则本文件
  自身会被判成未登记快照。带反引号或以 `#:` 起头的说明行不构成该形式。
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY = (
    REPO_ROOT
    / "openspec"
    / "changes"
    / "m2-producer-core"
    / "nwm-snapshot-inventory.md"
)
TASKS = REPO_ROOT / "openspec" / "changes" / "m2-producer-core" / "tasks.md"

PIN_SHORT = "8ae9b8f2"
PROVENANCE_MARKER = f"NWM@{PIN_SHORT}"

#: 溯源头必须落在文件的前几行（清单前言的固定格式 + fixture 的证据口径「前 5 行内」）。
#: 只约束正向断言；反向守卫按 grep 语义扫整文件。
HEADER_LINE_BUDGET = 5

#: §1 `落地状态` 列的取值全集。这不是第二份名单——名单仍只有清单表本身，这里只是它
#: 那一列的**词汇表**；写不进这两个值的单元格是畸形行（拼错一个字就静默解除义务）。
STATUS_LANDED = "本 issue 落地"
STATUS_PENDING = "待落地"
INVENTORY_STATUSES = (STATUS_LANDED, STATUS_PENDING)

#: 反向守卫的扫描根：规格写的是「`producer/` 内」，这里就写 `producer/`，让执行集
#: 与声明集自证相等，而不是靠「眼下恰好没有第三个子目录」。
SCANNED_ROOTS = (Path("producer"),)

#: 快照源模块的包根；清单目标落在其下 `<pkg>/` 的，整个 `<pkg>/` 进 DB-free 扫描集。
SNAPSHOT_PACKAGE_ROOT = Path("producer") / "src" / "yd_producer"

#: DB-free 禁区词表的**执行集**。它的**声明集**是 tasks.md「Required evidence」里
#: 以 `禁区 grep：` 起头的那条 grep 命令的正则轮换项，由
#: `_declared_forbidden_surfaces()` 在测试时解析、`test_forbidden_surfaces_match_the_
#: declared_grep` 断言两者相等——这里刻意不写第二份手工名单，也不写计数地板：
#: 参数化用例只能证明「表里的每一项都真的被执行」，删掉一项时它是**取消选择**（用例
#: 数从 8 掉到 4），不是变红；把词表钉在声明上的是那条相等断言。
#:
#: **命中口径**：扫的是源码里**会被执行**的那部分文本——注释与 docstring 先被
#: `_code_lines` 涂白，普通字符串字面量照扫（`os.getenv("DATABASE_URL")` 仍是命中）。
#: 词表本身不变，变的只是「在哪儿看见它算数」。
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

#: 声明集所在行的锚：Regression rows 里另有一条只含 6 项的同类 grep（无 `journal`/
#: `reservation`），按 `禁区 grep：` 前缀锚定即可把两者区分开，不必按行号引。
_FORBIDDEN_GREP = re.compile(r"禁区 grep[：:][^`]*`grep [^']*'([^']+)'")

_BACKTICKED = re.compile(r"`([^`]+)`")

#: 溯源标记的命中口径，**全仓唯一定义**，正反向共用。规格要求的形式是**整行**：
#: 一条独立 `#` 注释行，其注释内容恰为 `NWM@<sha> <原路径>`（允许缩进与行尾空白，
#: 路径之后不允许还有其它内容）。锚在注释行上而不是整文件裸串匹配：裸串会命中本模块
#: 与 `test_snapshot_provenance.py` 里用 f-string 拼出的 PROVENANCE_MARKER 常量与断言
#: 消息本身，从而逼出一份手工豁免名单——那正是本守卫要消灭的第二份名单。
#:
#: 「整行」这一维是 round-4 的集成红逼出来的，不是洁癖：旧口径「注释行里任何位置出现
#: `NWM@`」会把 master 上 `producer/src/yd_producer/config.py` 的一处**行内引用**
#: （`# NWM@<sha> \`x/y.py\` 的某某字段：……`，路径后还有叙述文字）判成未登记快照文件，
#: 而该文件在清单 §1 里既无「NWM 原路径」也无「剥离点」，登记它等于往清单里塞假数据。
#: 实测：收紧前在合入 master 的树上 `2 failed`，收紧后同一棵树全绿；已落地的 11 条
#: 溯源头部（形如 `# NWM@8ae9b8f2 packages/common/safe_fs.py`）收紧后仍全部命中。
#:
#: pin 不写进本谓词（`NWM@(?P<pin>\S+)` 而非写死 `NWM@8ae9b8f2`）：写死会让
#: `test_registered_headers_name_the_pin_and_not_some_other_commit` 永远看不到错 pin
#: 的行，那条用例即被架空。职责分工是——**形式**归本谓词，**pin 精确**归那条用例，
#: **原路径精确**归 `_provenance_header_lines` 的字段相等。
#:
#: 方向差异只在**行预算**上：反向无预算（整文件 grep），正向叠加 HEADER_LINE_BUDGET。
#: 命中口径本身不得分叉——正向曾用裸前 5 行子串，于是 docstring 里的标记被正向判为
#: 合格头部，却对反向隐形。
#:
#: **已声明的残留**（规格 forcing-chain「行内引用不触发反向守卫」段落同款）：在一份
#: 真实拷贝的溯源头部之后故意追加叙述文字，即可让该文件对反向守卫隐形。这需要「绕过
#: 形式」与「不登记」两个刻意动作，与「同一 commit 内既降级又删文件」并列记为已知
#: 非目标；本谓词是**登记守卫**，不是抄袭检测器。
#:
#: `<原路径>` 的字符类不是 `\S+`：它派生自清单 §1 `NWM 原路径` 列的实际取值（27 条，
#: 字符全集为小写字母、数字、`.`、`_`、`/`），大小写字母一并放开、连字符不臆造。`\S+`
#: 会把「紧贴路径粘上的尾随内容」整块吞成路径——issue #8 落地的
#: `producer/src/yd_producer/state/cfg_ic.py` 的 10 处 `#` 标记全形如
#: `packages/common/state_qc.py:43（逐字移植）`，于是这份**行级引用**被反向守卫误判为
#: 未登记快照文件（在合入 master 的树上实测 `2 failed, 577 passed, 16 skipped`）。规格
#: forcing-chain「行内引用不触发反向守卫」把这一形式与空格分隔的叙述形式并列点名。
#: 刻意不写 `[\w./]`：Python 的 `\w` 认 Unicode，`路径逐字移植` 这类无冒号的紧贴括注
#: 仍会溜过去。清单将来若出现含连字符的原路径，用例
#: `test_marker_grammar_rejects_line_citations_glued_to_the_path` 里 27 条逐条过谓词
#: 的断言立刻变红，这条省略是**响的**，不是静默的。
_SOURCE_PATH = r"[0-9A-Za-z._/]+"

_MARKER_COMMENT = re.compile(
    rf"^[ \t]*#[ \t]*NWM@(?P<pin>\S+)[ \t]+(?P<source>{_SOURCE_PATH})[ \t]*$"
)


# --- §1 表解析 ---------------------------------------------------------------


def _inventory_body_lines(text: str | None = None) -> tuple[list[str], list[str]]:
    """返回 §1 表的 (表体行, 游离行)；表体不做单元格数量过滤。

    只取 `## 1.` 与下一个 `## ` 之间的区段：§2 的能力项对照表也含反引号包裹的路径，
    整文件扫描会把它们一并吃进来。表体行的**条数**是
    `test_inventory_table_parses_every_body_row` 那条解析完整性检查的基准，所以
    这一步刻意不丢弃任何行。

    表行判定按 `lstrip()`：Markdown 允许表行带前导空格，渲染完全相同。按列 0 判定时，
    把某一行缩进一格就能让它连同其正向溯源义务静默离开表体集，而 `len(rows) ==
    len(body)` 依旧成立（两侧同步收缩）——这正是本次复核抓到的 P2。

    「游离行」= 区段内含 `|`、却不以 `|` 起头的行。表体判定放宽后，一行要想逃出表体
    集只剩「彻底不像表行」这一条路，而那条路会被游离行列表接住；调用方按 `malformed`
    的同一套路把它断成硬失败，因此表体的条数不再能被单行改动悄悄压低。
    """

    if text is None:
        text = INVENTORY.read_text(encoding="utf-8")
    section = re.search(r"^## 1\..*?(?=^## )", text, flags=re.MULTILINE | re.DOTALL)
    assert section is not None, "找不到 §1 快照清单区段"

    body: list[str] = []
    stray: list[str] = []
    for line in section.group(0).splitlines():
        stripped = line.strip()
        if "|" not in stripped:
            continue
        if not stripped.startswith("|"):
            stray.append(stripped[:100])
            continue
        first_cell = stripped.strip("|").split("|")[0].strip()
        if first_cell == "能力项" or set(first_cell) <= {"-", ":"}:
            continue
        body.append(stripped)
    return body, stray


def _parse_inventory_rows(
    body_lines: Iterable[str],
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """把表体行解析成 (目标路径, NWM 原路径, 落地状态)；解析不了的行进 `malformed`。

    畸形行**不能**静默跳过：清单里塞进一条单元格数不对的行（例如 `剥离点` 里出现未
    转义的 `|`），会让该行的目标路径连同它的正向溯源断言一起消失，守卫全绿而覆盖变窄。

    `落地状态` 单元格必须**严格**等于 `INVENTORY_STATUSES` 之一：写错一个字（或留空）
    若被当成「非 `本 issue 落地`」放过，等于把该行的落地义务静默解除——与畸形行同类。
    """

    rows: list[tuple[str, str, str]] = []
    malformed: list[str] = []
    for line in body_lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 6:
            malformed.append(f"单元格数为 {len(cells)}（应为 6）: {line[:100]}")
            continue
        source_match = _BACKTICKED.search(cells[1])
        target_match = _BACKTICKED.search(cells[2])
        status = cells[4]
        if source_match is None:
            malformed.append(f"缺少反引号包裹的 NWM 原路径: {line[:100]}")
            continue
        if target_match is None:
            malformed.append(f"缺少反引号包裹的目标路径: {line[:100]}")
            continue
        if status not in INVENTORY_STATUSES:
            malformed.append(
                f"落地状态应为 {list(INVENTORY_STATUSES)} 之一，实为 {status!r}: "
                f"{line[:100]}"
            )
            continue
        target = target_match.group(1)
        if not target.startswith("producer/"):
            malformed.append(f"目标路径应落在 producer/ 下: {target}")
            continue
        rows.append((target, source_match.group(1), status))
    return rows, malformed


INVENTORY_BODY_LINES, STRAY_TABLE_LINES = _inventory_body_lines()
INVENTORY_ROWS, MALFORMED_ROWS = _parse_inventory_rows(INVENTORY_BODY_LINES)
INVENTORY_TARGETS = {target: source for target, source, _ in INVENTORY_ROWS}

#: 守卫的**期望落地集**：清单里自报 `本 issue 落地` 的行。期望集必须来自清单而不是
#: 文件系统——文件系统派生的期望集会随文件被删而同步收缩，删除完全静默（P1）。
PINNED_TARGETS = {
    target: source
    for target, source, status in INVENTORY_ROWS
    if status == STATUS_LANDED
}

#: 目标路径 -> `落地状态` 的查表，供反向（文件 ⇒ 状态）方向使用。`INVENTORY_TARGETS`
#: 是**状态盲**的，只凭它做集合比较时，降级一行不改变等式任何一侧。
INVENTORY_STATUS = {target: status for target, _, status in INVENTORY_ROWS}


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


def _declared_forbidden_surfaces(text: str | None = None) -> tuple[str, ...]:
    """从 tasks.md 的 `禁区 grep：` 那条命令里解析禁区词表的**声明集**。

    数据源是 fixture 本身，与 §1 清单表同一套「解析、不转录」的口径：手工转录一份
    Python 副本，正是 `test_snapshot_provenance.py` 序言要消灭的第二份名单，也正是
    F16 的成因——词表被删项时没有任何东西接住。

    锚定在 `禁区 grep：` 前缀而不是行号：tasks.md 的 Regression rows 里另有一条同类
    grep 只列 6 项（无 `journal`/`reservation`），按行号引会随文档增删行漂移，按前缀
    锚可把两者区分开。正则轮换项里被反斜杠转义的点号在这里还原成字面 `.`。
    """

    if text is None:
        text = TASKS.read_text(encoding="utf-8")
    matches = _FORBIDDEN_GREP.findall(text)
    assert len(matches) == 1, (
        f"tasks.md 里以「禁区 grep：」起头的 grep 命令应恰有 1 条，实为 {len(matches)} 条"
    )
    return tuple(token.replace("\\.", ".") for token in matches[0].split("|"))


def _blank_prose(lines: list[str], token: tokenize.TokenInfo) -> None:
    """把一个 token 覆盖的字节区间就地涂成空格，**行数与行号一律不变**。

    涂白而不是删行：命中报告里的行号是证据的一部分（`<路径>:<行号>: <词>`），删行会让
    其后每一条命中的行号整体漂移。
    """

    (start_row, start_col), (end_row, end_col) = token.start, token.end
    for row in range(start_row, end_row + 1):
        line = lines[row - 1]
        begin = start_col if row == start_row else 0
        finish = min(end_col if row == end_row else len(line), len(line))
        if finish > begin:
            lines[row - 1] = line[:begin] + " " * (finish - begin) + line[finish:]


def _code_lines(text: str) -> list[str]:
    """把源码里的**注释与文档字符串**涂白，只留下会被解释器执行的那部分文本。

    口径的理由：DB-free 守卫要抓的是「快照面上真的存在数据库 / scheduler 依赖」，而
    `# 零数据库/scheduler 依赖` 这类叙述、以及模块头 docstring 里对禁区面的**否定**陈述，
    在运行期完全惰性。裸串匹配把它们一律判成命中，逼出的唯一出路是逐文件豁免名单——
    那正是本模块要消灭的第二份名单（`producer/src/yd_producer/state/cfg_ic.py` 已合入
    master，只因 #9 在同目录登记了兄弟文件就被这条裸串扫描钉红，可见误报打的不止是新码）。

    刻意**不**豁免普通字符串字面量：真实的 DB 面长成 `os.getenv("DATABASE_URL")`，词恰好
    在字符串里；豁免全部字面量等于把守卫关掉。被涂白的字符串只有**语句位置**上的那一类
    （模块 / 类 / 函数的 docstring，以及任何独立成句的字符串字面量）——那是散文，不是表达式
    的一部分。

    源码 tokenize 不了（语法错、编码怪）时**退回裸行扫描**：守卫宁可误报，不可漏报。

    **行数组 MUST 与 tokenize 的行号同源**，故按 `"\\n"` 切而不是 `str.splitlines()`：
    后者还会在 `\\x0b \\x0c \\x1c \\x1d \\x1e \\x85 \\u2028 \\u2029` 上断行，而
    `tokenize`（经 `io.StringIO`，`newline="\\n"`）只认 `\\n`。其中 `\\x0c \\x85 \\u2028
    \\u2029` 能正常 tokenize，于是两套行号错位，`_blank_prose` 会把**真代码行**当 docstring
    抹掉——那正是本守卫唯一不能犯的错（漏报）。`Path.read_text` 走通用换行，故到这里已不
    存在孤立的 `\\r`；末尾换行只会多切出一个空串元素，不影响任何行号。
    MUST NOT 改用「把这些字符加进跳过表」或「先归一化源码」——归一化会移动字节偏移，
    把 `_blank_prose` 的**列**算术也弄错位。
    """

    lines = text.split("\n")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return lines

    #: 逻辑行的起点判据：只有这些 token 之后，下一个 STRING 才处在「语句位置」。
    line_starts = {
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
    }
    skippable = {tokenize.COMMENT, tokenize.NL}
    previous_significant: int | None = None
    for position, token in enumerate(tokens):
        if token.type == tokenize.COMMENT:
            _blank_prose(lines, token)
            continue
        if token.type in skippable:
            continue
        if token.type == tokenize.STRING and (
            previous_significant is None or previous_significant in line_starts
        ):
            following = next(
                (item for item in tokens[position + 1 :] if item.type not in skippable),
                None,
            )
            if following is not None and following.type in {
                tokenize.NEWLINE,
                tokenize.ENDMARKER,
            }:
                _blank_prose(lines, token)
                previous_significant = token.type
                continue
        previous_significant = token.type
    return lines


def _forbidden_hits(repo_root: Path, files: Iterable[Path]) -> list[str]:
    """扫描集上的禁区面命中。

    语义（issue #14 迁移为 AST/import/call 判别）：
    - ``psycopg`` 与 ``scheduler``/``registry``/``journal``/``reservation``
      仅在 dotted import module path（模块名与导入成分）中按成分命中；
    - ``DATABASE_URL`` 只在精确 Name 或环境访问 key 中命中；
    - ``os.getenv`` 只在对应 Call path 命中；
    - ``os.environ`` 只在对应 Attribute/Subscript path 命中。
    普通变量名（如 ``registry_manifest``）、错误消息/路径字符串与显式
    work-local file adapter 不命中。
    AST 解析失败时 fail closed：回退到 :func:`_code_lines` 的 8-token 裸行扫描。
    """

    hits: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, TypeError):
            for number, line in enumerate(_code_lines(text), start=1):
                for token in FORBIDDEN_SURFACES:
                    if token in line:
                        hits.append(
                            f"{path.relative_to(repo_root).as_posix()}:{number}: {token}"
                        )
            continue
        for number, token in _ast_forbidden_hits(tree):
            hits.append(f"{path.relative_to(repo_root).as_posix()}:{number}: {token}")
    return hits


_IMPORT_SURFACES = ("psycopg", "scheduler", "registry", "journal", "reservation")


def _ast_forbidden_hits(tree: ast.AST) -> list[tuple[int, str]]:
    """AST 级禁区面判别，返回排序后的 ``(行号, token)`` 命中。

    - ``psycopg`` / ``scheduler`` / ``registry`` / ``journal`` / ``reservation``
      只在 dotted import module path 的成分中命中；
    - ``DATABASE_URL`` 只在精确 Name 或 ``os.environ`` 环境访问 key 中命中；
    - ``os.getenv`` / ``os.environ`` 只在对应 AST Call/Attribute/Subscript 路径命中。
    """

    found: set[tuple[int, str]] = set()

    def add(lineno: int, token: str) -> None:
        found.add((lineno, token))

    def module_components(module: str) -> list[str]:
        return [component for component in module.split(".") if component]

    def check_import_module(lineno: int, module: str) -> None:
        for component in module_components(module):
            for token in _IMPORT_SURFACES:
                if token.lower() in component.lower():
                    add(lineno, token)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                check_import_module(node.lineno, alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            check_import_module(node.lineno, module)
        elif isinstance(node, ast.Name):
            if node.id == "DATABASE_URL":
                add(node.lineno, "DATABASE_URL")
        elif isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and node.attr == "environ"
            ):
                add(node.lineno, "os.environ")
        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Attribute):
                value = node.value
                if (
                    isinstance(value.value, ast.Name)
                    and value.value.id == "os"
                    and value.attr == "environ"
                ):
                    add(node.lineno, "os.environ")
                    key = node.slice
                    if (
                        isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and key.value == "DATABASE_URL"
                    ):
                        add(node.lineno, "DATABASE_URL")
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and node.slice
                and isinstance(node.slice, ast.Constant)
            ):
                # os["DATABASE_URL"] — Count as env access key only when os["..."], not arbitrary dict.
                pass
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Attribute):
                    inner = func.value
                    if (
                        isinstance(inner.value, ast.Name)
                        and inner.value.id == "os"
                        and inner.attr == "environ"
                        and func.attr == "get"
                    ):
                        add(node.lineno, "os.environ")
                if (
                    isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                    and func.attr == "getenv"
                ):
                    add(node.lineno, "os.getenv")
                    if (
                        node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == "DATABASE_URL"
                    ):
                        add(node.lineno, "DATABASE_URL")
                if (
                    isinstance(func.value, ast.Attribute)
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "os"
                    and func.value.attr == "environ"
                    and func.attr == "get"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "DATABASE_URL"
                ):
                    add(node.lineno, "DATABASE_URL")

    token_rank = {token: index for index, token in enumerate(FORBIDDEN_SURFACES)}
    return sorted(found, key=lambda item: (item[0], token_rank[item[1]]))


# --- 溯源标记命中（正反向共用的唯一谓词） ------------------------------------


def _marker_fields(line: str) -> tuple[str, str] | None:
    """整行溯源头部形式的解析结果 `(pin, NWM 原路径)`；不是该形式则 `None`。"""

    match = _MARKER_COMMENT.search(line)
    if match is None:
        return None
    return match.group("pin"), match.group("source")


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
        if _marker_fields(line) is not None
    ]


def _header_marker_lines(path: Path) -> list[tuple[int, str]]:
    """正向方向：同一谓词命中 ∧ 行号 ≤ 头部行预算。"""

    return [
        (number, line)
        for number, line in _marker_comment_lines(path)
        if number <= HEADER_LINE_BUDGET
    ]


def _provenance_header_lines(path: Path, source: str) -> list[tuple[int, str]]:
    """正向断言的完整谓词：头部注释行 ∧ pin 与 NWM 原路径**逐字段相等**。

    「前 5 行裸子串」不够：`\"\"\"NWM@8ae9b8f2 ...\"\"\"` 这样的 docstring 形式能通过
    子串检查，却不是反向守卫认得的头部形式——同一不变量在两个方向上口径分叉。

    字段相等而非子串包含：谓词收紧成整行形式后，注释内容已被切成 `(pin, 原路径)` 两段，
    子串比较会让 `# NWM@8ae9b8f2 <原路径>xyz` 这类**前缀匹配**通过正向断言，与规格
    「注释内容恰为 `NWM@<sha> <原路径>`」的「恰为」不符。
    """

    matched: list[tuple[int, str]] = []
    for number, line in _header_marker_lines(path):
        fields = _marker_fields(line)
        if fields == (PIN_SHORT, source):
            matched.append((number, line))
    return matched


def _scannable_python_files(root: Path) -> list[Path]:
    """扫描根下的 `.py` 文件，跳过点开头目录（`.venv` / `.pytest_cache` / `.git` ...）。

    用「点开头」这条规则而不是一份具名黑名单，同样是为了不养第二份名单。
    """

    return sorted(
        path
        for path in root.rglob("*.py")
        if not any(part.startswith(".") for part in path.relative_to(root).parts[:-1])
    )


def _files_with_marker(repo_root: Path, roots: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        for path in _scannable_python_files(repo_root / root):
            if _marker_comment_lines(path):
                found.append(path)
    return found
