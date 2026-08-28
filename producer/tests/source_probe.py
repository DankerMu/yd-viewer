"""源码机检辅助：按 `ast` 的**函数/类边界**取窗，供溯源、隔离与裁决闭合断言复用。

为什么不用定长窗口：#8 实测定长切片会越进下一个函数，于是一个辅助可以被**邻居的**溯源
注释满足，删掉它自己那行也照样绿。本模块一律按 `ast` 的 `get_source_segment` 取窗。

本模块 MUST NOT 从 `yd_producer` import 任何东西——它读的是**源码文本**，不是运行时对象。
"""

from __future__ import annotations

import ast
import pathlib
import re


def read_source(module_file: str) -> str:
    return pathlib.Path(module_file).read_text(encoding="utf-8")


def module_docstring_block(source: str) -> str:
    """模块头**前言**：文件开头直到模块 docstring 结束的原始文本（含三引号）。

    按 `ast` 取 docstring 节点的 `end_lineno`，不用 `source.index('\"\"\"', 3)`：后者假定
    文件第一个字符就是 docstring 的开引号。快照文件的**溯源头部注释**
    （`# NWM@<sha> <原路径>`，形式见 `snapshot_provenance_fixtures._MARKER_COMMENT`）必须
    落在前 5 行，于是 docstring 不再是第 0 个字节，旧写法会把返回值截成「注释 + 开引号」。

    仍返回**前缀**而非 docstring 单体：调用方按 `source[len(block):]` 取模块体做「体内
    不得出现某某字样」的断言，前缀性质是那条断言的前提。

    行切分按 `"\\n"` 而不是 `str.splitlines(keepends=True)`：后者还会在 `\\x0c` / `\\x85`
    / `\\u2028` 等字符上断行，而 `ast` 的 `end_lineno` 只按 `\\n` 计，两者一旦错位切片就
    会**少**切（方向 fail-closed：`head` 变短、`body` 变长，断言只会更严），但仍是错的。
    末尾换行按**源码里是否真有**那一个 `\\n` 补，不能无条件 `+ "\\n"`——docstring 恰好
    结束于 EOF 且文件无末尾换行时，无条件补会让返回值不再是 `source` 的前缀。
    """

    tree = ast.parse(source)
    assert tree.body, "空模块没有模块头"
    node = tree.body[0]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant), (
        "模块的第一条语句不是 docstring"
    )
    assert isinstance(node.value.value, str), "模块的第一条语句不是字符串字面量"
    assert node.end_lineno is not None
    parts = source.split("\n")
    block = "\n".join(parts[: node.end_lineno])
    return block + "\n" if len(parts) > node.end_lineno else block


def definition_segments(source: str) -> dict[str, str]:
    """顶层 `def` / `class` 各自的源码段（含其内部注释），按名字索引。"""
    tree = ast.parse(source)
    segments: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            segment = ast.get_source_segment(source, node)
            assert segment is not None, node.name
            segments[node.name] = segment
    return segments


def definition_names(source: str) -> set[str]:
    """模块级**定义**名字集：`def` / `class` / 顶层赋值目标。

    **不含** import 进来的名字——「从 `cfg_ic` 导入而非重新移植」正是靠这条区分：
    `from ... import _as_float` 是 `ImportFrom`，不产生定义。
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def function_body_by_name(source: str, name: str) -> ast.FunctionDef:
    """按名字取顶层函数节点（含嵌套在类体内的方法，用 `Class.method` 形态）。"""
    tree = ast.parse(source)
    if "." in name:
        class_name, method_name = name.split(".", 1)
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for child in node.body:
                    if (
                        isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                        and child.name == method_name
                    ):
                        return child
        raise AssertionError(f"method not found: {name}")
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == name
        ):
            return node
    raise AssertionError(f"function not found: {name}")


def count_raises(source: str, name: str) -> int:
    """指定函数体内 `raise` 语句的条数（含嵌套的内层函数）。"""
    return sum(
        1
        for node in ast.walk(function_body_by_name(source, name))
        if isinstance(node, ast.Raise)
    )


#: 无歧义的写文件属性调用名（裁决 6 的机检集）。
#: `replace` / `rename` **不**进这张表——`datetime.replace` / `dataclasses.replace` /
#: `str.replace` 同名不同物，按属性名判会把它们全部误报；落盘语义的那两个走
#: :data:`WRITE_DOTTED_CALLS` 的**限定名**匹配。
WRITE_CALL_NAMES = (
    "write_bytes",
    "write_text",
    "writelines",
    "mkdir",
    "unlink",
    "touch",
    "atomic_write_bytes_no_follow",
    "atomic_write_bytes",
    "atomic_write_text",
)

#: 按**限定名**匹配的落盘调用（`os.replace` 等，属性名本身有歧义）。
WRITE_DOTTED_CALLS = (
    "os.replace",
    "os.rename",
    "os.remove",
    "os.write",
    "shutil.move",
    "shutil.copy",
    "shutil.copyfile",
    "tempfile.NamedTemporaryFile",
)


def _dotted_name(node: ast.expr) -> str | None:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def write_surface_calls(source: str) -> list[str]:
    """返回源码里出现的写文件调用（裁决 6：本 issue 不写任何文件）。

    `open(..., mode)` 里 mode 含 `w`/`a`/`x` 的一律计入；属性调用按
    :data:`WRITE_CALL_NAMES` 判名，落盘的同名歧义调用按 :data:`WRITE_DOTTED_CALLS`
    判限定名。
    """
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            mode = ""
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = str(keyword.value.value)
            if any(char in mode for char in "wax"):
                found.append(f"open(mode={mode!r})")
        if isinstance(func, ast.Attribute) and func.attr in WRITE_CALL_NAMES:
            found.append(func.attr)
        dotted = _dotted_name(func)
        if dotted in WRITE_DOTTED_CALLS:
            found.append(dotted)
    return found


#: 中文数字 -> 整数（偏离清单条数的声明用的是中文数字）。
_CHINESE_NUMERALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def declared_deviation_count(head: str) -> int:
    """从模块头 docstring 里**解析**出「对 pin 的刻意偏离（N 条）」的声明值。

    为什么不写 `assert "八条" in head`：实测该写法对「把八条改回六条」这个变异体**存活**
    ——docstring 后文的「故不计入上面的八条偏离」也含「八条」，于是子串断言照样为真。
    这正是 profile 记录的那类假绿：自洽、可复现、只是没在测它以为在测的东西。故此处解析
    出唯一那个声明位点的数值，交由调用方与代码侧的分类表闭合。
    """
    match = re.search(r"刻意偏离\*\*（([一二三四五六七八九十]+)条", head)
    assert match is not None, "模块头未声明刻意偏离的条数"
    assert head.count("刻意偏离**（") == 1, "刻意偏离的条数声明位点不唯一"
    return _CHINESE_NUMERALS[match.group(1)]
