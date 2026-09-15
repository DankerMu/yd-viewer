"""`yd_producer.rawcopy.stage_raw` 准入期收口结构探针。"""

import ast
import builtins
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from rawcopy_fixtures import (
    build_tree,
    expect_kind,
    snapshot,
    source_manifest_payload,
    staged,
    write_source_manifest,
)

from yd_producer import rawcopy as rawcopy_module
from yd_producer.config import ConfigError
from yd_producer.rawcopy import RawStagingError

# --- Row：准入期收口（floor）的结构探针 --------------------------------------
#
# 本节不是「给 `OverflowError` 和 `RecursionError` 各补一个用例」。三轮修复都按
# **异常类型**枚举消费点，每轮都修掉被点名的实例、每轮又留下同类新实例（round 1 的
# `UnicodeEncodeError`、round 2 的裸 `ValueError`/`TypeError`、round 3 的
# `OverflowError`/`RecursionError`）。判据改取**位置**：准入段整体在一个收口块内，
# 于是「非词表异常从准入期逃逸」这件事在结构上不可能，而不是恰好不存在。
#
# 两条断言分工：
# - `test_admission_phase_is_structurally_enclosed_by_one_floor`：AST 机检
#   `stage_raw` 的**第一条语句**就是收口 `try`、其后紧跟写入期起点，故不存在任何一条
#   落在收口之外的准入语句。新增的准入语句只能落在块内——这是闭合的来源。
# - `test_admission_call_boundary_contains_injected_non_vocabulary_exception`：对
#   AST **枚举出来**的每一个准入期调用点注入一个非词表异常，断言收口生效且零写入。
#   参数集由源码派生，不是手写清单：将来在准入段新写一个调用点会**自动**入表。


class _ProbeEscape(Exception):
    """注入用的异常：直接继承 `Exception`，**不是**被测模块任何一条 `except` 元组
    成员（`OSError`/`ValueError`/`TypeError`/`KeyError`/`AttributeError`/
    `JSONDecodeError`/`UnicodeDecodeError`）的子类。

    这一点是判别力的全部来源：若注入 `ValueError`，`_load_source_manifest` 自己的
    handler 就会吃掉它，探针在**删掉收口块**的情况下照样通过，从而不是判别器。
    """


def _stage_raw_body() -> list[ast.stmt]:
    tree = ast.parse(inspect.getsource(rawcopy_module))
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "stage_raw"
    )
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body = body[1:]  # docstring
    return body


def _admission_try() -> ast.Try | None:
    """`stage_raw` 的收口块。**不在这里断言**：收口不存在时若模块级代码抛异常，整个
    文件会在 collect 期就报错，其余用例连红都变不出来（红证会被淹掉）。结构本身由
    `test_admission_phase_is_structurally_enclosed_by_one_floor` 断言。
    """
    node = _stage_raw_body()[0]
    return node if isinstance(node, ast.Try) else None


def _dotted(func: ast.expr) -> str | None:
    """`os.path.lexists` -> `"os.path.lexists"`；`work_real.is_relative_to` -> 同形。

    根不是 Name（例如 `"、".join(...)`）时返回该调用目标的源码文本。
    """
    parts: list[str] = []
    node: ast.expr = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ast.unparse(func)


def _admission_call_targets() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """AST 遍历（**不是 grep**）准入段的全部调用点，按可注入性分两桶。

    可注入 = 点号链的根是被测模块的**模块级名字**（含被模块全局遮蔽的 builtin），
    因此可以在模块命名空间上替换。另一桶是对**局部对象**取方法（`work_real
    .is_relative_to(...)`、`cycle.astimezone(...)`、`"、".join(...)`）：它们没有模块级
    的注入点，但同样**词法落在收口块内**，由上一条结构断言覆盖。
    """
    patchable: set[str] = set()
    local_methods: set[str] = set()
    node = _admission_try()
    for stmt in node.body if node is not None else []:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            root = name.split(".")[0]
            if root.isidentifier() and (
                root in vars(rawcopy_module) or hasattr(builtins, root)
            ):
                patchable.add(name)
            else:
                local_methods.add(name)
    return tuple(sorted(patchable)), tuple(sorted(local_methods))


ADMISSION_INJECTION_TARGETS, ADMISSION_LOCAL_METHOD_CALLS = _admission_call_targets()

# 注入后**不会被调用**的准入期调用点，逐条附理由。它不是豁免清单：探针会断言实际
# 未命中的集合与本表**恰好相等**，于是将来某个点位从「happy path 会走到」变成
# 「走不到」（或反之）都会打红，必须显式改这里而不能悄悄漏掉。
ADMISSION_UNREACHED_ON_HAPPY_PATH = {
    # 只在失败分支被构造。且它同时是收口器**自己**用来落地的类型，把它换成注入器
    # 等于连收口器一起换掉——那不是对准入段的有效注入，而是把收口器本身拆了。
    "RawStagingError": "只在失败分支构造；且收口器自身依赖它",
    "ConfigError": "只在 containment 失败分支构造",
}

# 被**非调用**方式消费的准入期名字：替换它们不会走到 `raiser.__call__`，但同样把一个
# 非词表异常送进准入段。逐条附机制，并在探针里单独走一条断言分支——不这样分，
# 「注入确实生效了」这条前提就会在这些点位上悄悄失效。
ADMISSION_NONCALL_CONSUMPTION = {
    "str": "同时被 `isinstance(variable, str)` 当作类型实参；替换后由 isinstance 抛 "
    "`TypeError`，仍必须被收口",
}


class _AttrShim:
    """把 `mod.attr` 换成别的对象，其余属性照转的薄壳（用于 `os.path.lexists`
    这类多级点号链：不去动真正的 `os`，只在被测模块的命名空间里换一层）。"""

    def __init__(self, target: Any, attr: str, value: Any) -> None:
        self.__dict__["_target"] = target
        self.__dict__["_attr"] = attr
        self.__dict__["_value"] = value

    def __getattr__(self, name: str) -> Any:
        if name == self.__dict__["_attr"]:
            return self.__dict__["_value"]
        return getattr(self.__dict__["_target"], name)


def _install(monkeypatch: pytest.MonkeyPatch, dotted: str, replacement: Any) -> None:
    root, *rest = dotted.split(".")
    if not rest:
        monkeypatch.setattr(rawcopy_module, root, replacement, raising=False)
        return
    current = getattr(rawcopy_module, root)
    chain = [current]
    for part in rest[:-1]:
        current = getattr(current, part)
        chain.append(current)
    value: Any = replacement
    for part, holder in zip(reversed(rest), reversed(chain), strict=True):
        value = _AttrShim(holder, part, value)
    monkeypatch.setattr(rawcopy_module, root, value, raising=False)


def test_admission_phase_is_structurally_enclosed_by_one_floor() -> None:
    """`stage_raw` 的**整个**准入段落在唯一一个收口 `try` 内，无第二块、无块外语句。

    判据取在**范围**上而不是槽位上。只钉 `body[0]` 是 `try`、`body[1]` 是写入期起点
    是不够的：一条准入语句被移到 `body[2]`（仍在任何写入之前）就同时逃出地板**和**
    AST 派生的探针参数集（实测 21 -> 20），违反 MUST 而全套件全绿。故这里钉三件事：
    函数体顶层的**完整形状**（多出任何一条语句即红）；地板 `try` 体的**首尾**语句就是
    fixture 具名的准入段两端点（任一端被移出即红）；以及地板 `Try` 的 `orelse` 与
    `finalbody` **均为空**——端点只钉 `Try.body`，而 `else:` 体抛出的异常按 Python 语义
    **不被本 try 的 handler 捕获**，把一条准入语句从 `body` 移进同一 `Try` 的 `orelse`
    时顶层形状与首尾端点都不变（实测全套件绿、探针 21 -> 20），注入的异常却逃出封闭
    词表。

    MUST NOT 改成钉 `len(ADMISSION_INJECTION_TARGETS)`：计数会在每次**合法**新增调用
    点时变红，且它对「语句被移出地板」与「语句被删掉」不可区分——那正是本仓复盘所
    批判的枚举式判别器。

    **两条如实登记的残留**（本断言覆盖不到，不粉饰）：
    - 准入语句被移进**写入段**的 `try`：那里有另一套 handler，是另一个问题（就
      `_render_manifest` 这一条而言，该出口由
      `test_manifest_serialization_call_site_is_inside_the_admission_floor` 单独钉住）；
    - 准入工作被抽成函数、由写入段调用：任何 AST 用例都测不出来。
    """
    body = _stage_raw_body()
    # 顶层形状：地板 try / 写入期起点 / 写入段 try / return，恰好四条。
    assert [type(stmt).__name__ for stmt in body] == [
        "Try",
        "Assign",
        "Try",
        "Return",
    ], [type(stmt).__name__ for stmt in body]
    node = body[0]
    assert isinstance(node, ast.Try), (
        "函数体第一条语句必须是收口 try（否则块前有裸语句）"
    )
    assert ADMISSION_INJECTION_TARGETS, "探针参数集为空：枚举没取到准入段"
    following = body[1]
    assert isinstance(following, ast.Assign), "收口块之后必须紧接写入期起点"
    assert [t.id for t in following.targets if isinstance(t, ast.Name)] == ["written"]
    # 地板 try 体的首尾 = 准入段的两个端点（fixture 逐字：「形参守卫直到
    # `target-exists` 预检」）。首端是 `verdict.complete` 检查，尾端是
    # `os.path.lexists` 的 target-exists 预检。
    first = ast.unparse(node.body[0])
    last = ast.unparse(node.body[-1])
    assert "verdict.complete" in first, first
    assert "os.path.lexists" in last and "target-exists" in last, last
    # 覆盖轴：地板的 handler 只覆盖 `Try.body`。`else:` 体里抛出的异常按 Python 语义
    # 不被本 try 的 handler 捕获，`finally:` 体同理；两者却都在词法上「在 try 内」，
    # 且都不在 `_admission_call_targets` 的遍历范围里（探针会静默 21 -> 20）。
    assert node.orelse == [] and node.finalbody == [], (
        "地板 `Try` 的 orelse/finalbody MUST 为空：这两段虽在 try 内，却**不被本 try 的 "
        "handler 覆盖**，准入语句挪进去即逃出兜底地板并静默退出 AST 探针参数集；"
        f"实得 orelse={[ast.unparse(s) for s in node.orelse]} "
        f"finalbody={[ast.unparse(s) for s in node.finalbody]}"
    )

    kinds = [ast.unparse(handler.type) for handler in node.handlers]
    assert kinds == [
        "(ConfigError, RawStagingError)",
        "Exception",
        "BaseException",
    ], kinds
    # 第一层只做原样外抛（保 kind/`__cause__`/`is` 身份），再走 claim release。
    first_handler = ast.unparse(node.handlers[0])
    assert "release_raw_claim_after_stage_failure" in first_handler
    assert first_handler.rstrip().endswith("raise")
    # 第二层把非词表异常收敛成 `RawStagingError`，并在 raise 前走同一 claim release。
    source = ast.unparse(node.handlers[1])
    assert "RawStagingError" in source and "ADMISSION_FALLBACK_KIND" in source
    assert "release_raw_claim_after_stage_failure" in source
    assert "from exc" in source  # `__cause__` 保留
    # BaseException 不包装/吞掉；只在原样 raise 前释放已认领的 empty exact root。
    base_handler = ast.unparse(node.handlers[2])
    assert "release_raw_claim_after_stage_failure" in base_handler
    assert base_handler.rstrip().endswith("raise")
    # 收口器自身不得抛：kind 取自闭合词表，消息拼装走不抛的 `_safe_repr`。
    assert rawcopy_module.ADMISSION_FALLBACK_KIND in rawcopy_module.ERROR_KINDS
    assert "_safe_repr" in source and "!r}" not in source


def _render_manifest_call_sites(region: list[ast.stmt]) -> set[int]:
    """`region` 这几条语句里 `_render_manifest` 全部调用点的 AST 节点身份。

    取 `id()` 而不是计数：本用例要判别的是「调用点落在哪个区域」，计数式判别器
    （`== 1`、`len(...) == n`）会在每次**合法**新增时变红，且分不清「被移走」与
    「被删掉」——本仓复盘反复批判的枚举模式。
    """
    sites: set[int] = set()
    for stmt in region:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and _dotted(node.func) == "_render_manifest":
                sites.add(id(node))
    return sites


def test_manifest_serialization_call_site_is_inside_the_admission_floor() -> None:
    """本轮 manifest 的序列化调用点 MUST 落在准入段兜底地板的 `Try` **体**之内。

    这是 fixture「序列化 MUST 前置于复制，且 MUST 位于地板 `Try` 体之内」那条的判别器。
    与顶层形状断言合起来即得到「前置于复制」：地板 `Try` 是函数体第一条语句，一切复制
    都在其后的写入段 `try` 里。

    **该约束买到的是什么**（原写的理由已被 round 5 实测证伪，勿再复述）：违反它**不会**
    留下「副本全落地 + 0 字节 manifest」——`_render_manifest` 自己抛
    `RawStagingError(kind="source-manifest")`，写入段的 `except RawStagingError` 会
    `written.rollback()` 后原样再抛，0 字节 manifest 从未被创建，`snapshot(work_dir) == {}`
    仍成立。真实收益是：一段**注定失败**的输入，其失败点留在任何复制之前，于是零写入
    **不依赖回滚自身成功**——而回滚可能失败是本仓另行承认的事实（失败时留残留，并让
    下一次重试被 `target-exists` 预检硬拒）。

    杀手变异体（各自 MUST 变红）：
    - SERIAL：把该调用移进写入段 `try`、复制循环之后；
    - ORELSE：把该调用移进地板 `Try` 的 `else:` 体（词法在 try 内，语义在 handler 外）。
    """
    body = _stage_raw_body()
    floor = body[0]
    assert isinstance(floor, ast.Try), (
        "函数体第一条语句必须是地板 try（结构本身由 "
        "`test_admission_phase_is_structurally_enclosed_by_one_floor` 断言）"
    )
    # 一次解析、同一棵树：两个集合必须来自同一次 `ast.parse`，否则 `id()` 天然不等。
    inside = _render_manifest_call_sites(floor.body)
    anywhere = _render_manifest_call_sites(body)
    assert inside, (
        "`_render_manifest` 的调用点不在地板 `Try` 体内：序列化已不再前置于复制，"
        "一段注定失败的输入要到复制之后才失败，零写入转而依赖回滚自身成功；"
        f"实际找到的调用点区域={'函数体别处' if anywhere else '整个 stage_raw 内都没有'}"
    )
    assert anywhere == inside, (
        "`stage_raw` 里存在落在地板 `Try` 体**之外**的 `_render_manifest` 调用点"
        "（写入段 `try`、地板的 `orelse`/`finalbody`/handler 都算外部）："
        f"体内 {len(inside)} 处、全函数 {len(anywhere)} 处"
    )


def test_admission_local_method_calls_are_inside_the_floor() -> None:
    """无模块级注入点的那一桶：登记其存在，并声明它们由词法包含覆盖。"""
    assert ADMISSION_LOCAL_METHOD_CALLS  # 该桶非空，别把它当成「不存在」
    for name in ADMISSION_LOCAL_METHOD_CALLS:
        # 全部是对局部对象/字面量取方法；没有一个是模块级名字或 builtin
        # （否则它应当落在可注入桶里，而不是靠词法包含兜底）。
        root = name.split(".")[0]
        assert not hasattr(rawcopy_module, root)
        assert not (root.isidentifier() and hasattr(builtins, root))


@pytest.mark.parametrize("dotted", ADMISSION_INJECTION_TARGETS)
def test_admission_call_boundary_contains_injected_non_vocabulary_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dotted: str
) -> None:
    """对准入段每个调用点注入一个非词表异常，断言收口生效且零写入。

    参数集由 AST 派生，故这不是「加两个用例」：准入段将来新写的调用点会自动入表。
    """
    raw_root, work_dir = build_tree(tmp_path)
    calls: list[str] = []

    def raiser(*args: Any, **kwargs: Any) -> Any:
        calls.append(dotted)
        raise _ProbeEscape(f"注入到 {dotted}")

    _install(monkeypatch, dotted, raiser)
    try:
        staged(raw_root, work_dir)
    except (RawStagingError, ConfigError) as exc:
        if dotted in ADMISSION_NONCALL_CONSUMPTION:
            assert not calls, f"{dotted} 已按调用点生效，登记表该行已陈旧"
        else:
            assert calls, f"{dotted} 未被调用，但 staging 失败了"
            assert isinstance(exc.__cause__, _ProbeEscape) or isinstance(
                exc.__context__, _ProbeEscape
            ), "注入的异常必须留在 `__cause__`/`__context__` 里，不得被抹掉"
        if isinstance(exc, RawStagingError):
            assert exc.kind in rawcopy_module.ERROR_KINDS
        # 零写入取证 MUST 落在**收口成功**这条分支上——19 个注入点实际走的就是它。
        # （原先这两行写在下面 `raise` 之后，是不可达死码：探针于是对 governing
        # invariant 第三合取项「不留任何部分产物」零判别力。）
        monkeypatch.undo()
        assert snapshot(work_dir) == {}, "准入期失败 MUST 零写入"
    except BaseException as exc:  # 探针要看的就是逃逸
        raise AssertionError(
            f"{dotted} 处注入的 {type(exc).__name__} 逃出了 "
            "{ConfigError, RawStagingError} 词表"
        ) from exc
    else:
        monkeypatch.undo()
        assert not calls, f"{dotted} 被调用了却没有失败，注入无效"
        assert dotted in ADMISSION_UNREACHED_ON_HAPPY_PATH, (
            f"{dotted} 在正向路径上未被调用，且不在 "
            "ADMISSION_UNREACHED_ON_HAPPY_PATH 登记表内"
        )


def test_admission_unreached_ledger_has_no_stale_rows() -> None:
    """登记表不得有陈旧行：每一行都必须仍是准入段的一个调用点。"""
    assert set(ADMISSION_UNREACHED_ON_HAPPY_PATH) <= set(ADMISSION_INJECTION_TARGETS)
    assert set(ADMISSION_NONCALL_CONSUMPTION) <= set(ADMISSION_INJECTION_TARGETS)
    assert not set(ADMISSION_NONCALL_CONSUMPTION) & set(
        ADMISSION_UNREACHED_ON_HAPPY_PATH
    )


def _json_depth_that_overflows(cap: int = 200_000) -> int | None:
    """找出让 `json.loads` 抛 `RecursionError` 的最小可测嵌套深度；找不到返回 `None`。

    深度写死是不可移植的：CPython 3.12 上 6 万层必炸，3.14 上同一份文本解析通过
    （json 的 C 扫描器不再按 Python 递归上限计数）。用例的取证对象是**收口**而不是
    解析器的实现细节，故这里自标定；标定不到就跳过并说明。
    """
    depth = 2000
    while depth <= cap:
        try:
            json.loads("[" * depth + "]" * depth)
        except RecursionError:
            return depth
        depth *= 4
    return None


@pytest.mark.parametrize(
    ("shape", "kind"),
    [
        ("overflow", "source-manifest"),
        ("recursion", "source-manifest"),
    ],
)
def test_round3_named_escapes_are_now_contained(
    tmp_path: Path, shape: str, kind: str
) -> None:
    """round-3 verifier 实测逃逸的两条具名形态，作为收口的端到端回归。

    它们**不是**本类的闭合证据（闭合由上面的结构断言与参数化探针承担），只是把
    verifier 的两条复现钉住，防止将来的重构把收口挪走而探针恰好都走别的分支。
    """
    if shape == "overflow":
        payload = source_manifest_payload("gfs")
        # `int(1e400)` -> `OverflowError`（不是 `ValueError`，不在任何 except 元组里）。
        # 形态闸门先接住它并给出更准的消息；无论走闸门还是走地板，都必须落进词表。
        raw_text = json.dumps(payload).replace(
            '"forecast_hour": 0', '"forecast_hour": 1e400', 1
        )
    else:
        # 6 万层嵌套放在一份**其余部分完全合规**的 manifest 的附加键里：`json.load`
        # 抛 `RecursionError`（`RuntimeError` 子类），同样不在任何 except 元组里。
        depth = _json_depth_that_overflows()
        if depth is None:
            pytest.skip(
                "本解释器的 JSON 解析器在可测深度内不抛 `RecursionError`"
                "（CPython 3.14 起 json 的 C 扫描器不再按 Python 递归上限计数）；"
                "该形态的收口由上方参数化逃逸探针无条件覆盖，CI 钉 3.12 会实跑本行"
            )
        payload = source_manifest_payload("gfs")
        payload["metadata"]["deep"] = "<PLACEHOLDER>"
        # 文本拼装，**不在用例里 `json.loads` 整份**：那会先把测试进程自己撞到上限。
        raw_text = json.dumps(payload).replace(
            '"<PLACEHOLDER>"', "[" * depth + "]" * depth, 1
        )
    raw_root, work_dir = build_tree(tmp_path)
    write_source_manifest(raw_root, "gfs", raw_text)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, kind)
    assert snapshot(work_dir) == {}
