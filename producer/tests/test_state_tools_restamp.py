"""`yd_producer.state.restamp` 的行为测试（任务 4.3 重戳到目标 cycle 绝对时间）。

判别力纪律：干净输入上「重戳成功」是平凡真。真正的承重条是**脏矩阵**——CRLF 行尾、header
行多空格分隔与行尾空格、Tab 分隔的数据行、段间空行、混合记法、无末尾换行。pin 式的
`"\\t".join(header)` 与整文件 `"\\n".join(lines)` 只在这里变红。

oracle 纪律：期望的 minute token 由**手算的 epoch 秒**得出（`2026-01-02T12:00:00Z` →
1767355200 秒 → 29455920 分），不由被测函数回读。
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta, timezone

import pytest
import source_probe
from cfg_ic_fixtures import (
    DEFAULT_MINUTE,
    build_cfg_ic,
    build_cfg_ic_rows,
    mesh_row,
    river_row,
)

from yd_producer.state import cfg_ic, header_time, restamp

#: init 首态语义的目标 T；epoch 秒 1767355200 为手算值（1970-01-01 起 20456 天 + 12h）。
TARGET_T = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
TARGET_T_EPOCH_SECONDS = 1767355200
#: 发布前 checkpoint 定戳语义的 T+12。
TARGET_T_PLUS_12 = TARGET_T + timedelta(hours=12)
EXPECTED_MINUTE_T = "29455920.000000"
EXPECTED_MINUTE_T_PLUS_12 = "29456640.000000"

#: header 判定的五个 pin 符号归 `header_time`（`tasks.md:811`）：本模块 MUST import，
#: 模块级定义名字集不得含它们。
HEADER_TIME_SYMBOLS = (
    "cfg_ic_header_minute_index",
    "cfg_ic_header_minute_time",
    "cfg_ic_header_shape",
    "CfgIcHeaderShape",
    "_VALID_CFG_IC_HEADER_TOKEN_COUNTS",
)

#: 从 NWM pin `state_cli.py` 移植的符号全集：每一个都必须自带**自己的**溯源注释。
PORTED_SYMBOLS = ("_ensure_utc", "restamp_to_absolute_time")

#: 裁决 7 的 non-goal：rekey 面七符号 + `_check_water_balance` non-goal 的两个名字，
#: 在本模块内一律不得有定义。两个集合**必须与 `test_state_tools_qc.py` 的 `NON_GOAL_SYMBOLS`
#: 逐字相同**：fixture 要求 state_qc / restamp 两侧都绑住 rekey 面与 water-balance 面，
#: 少一项就是一条静默解除的义务（`water_balance` 曾只绑在 state_qc 一侧）。
REKEY_SYMBOLS = (
    "StateCheckpoint",
    "StateRunContext",
    "_checkpoint_header_minute",
    "_valid_time_from_header_minute",
    "_checkpoint_with_header_time",
    "_lead_hours_from_run_valid_time",
    "STATE_CHECKPOINT_IC_HEADER_SHAPE_REKEY_SKIPPED",
    "_check_water_balance",
    "water_balance",
)


def _hand_minute(target: datetime) -> str:
    """独立 oracle：epoch 秒 / 60 的 `%.6f`，不经被测模块。"""
    return f"{target.timestamp() / 60.0:.6f}"


def _clean_doc(mesh_count: int = 3, river_count: int = 4) -> cfg_ic.CfgIcDocument:
    return cfg_ic.parse(
        build_cfg_ic(mesh_count=mesh_count, river_count=river_count).payload
    )


def _dirty_payload() -> bytes:
    """脏矩阵：CRLF + header 多空格分隔与行尾空格 + Tab 分隔的数据行 + 段间空行 +
    混合记法 + 无末尾换行。裁决 1/2 的唯一判别力来源。"""
    return build_cfg_ic_rows(
        mesh_rows=[
            mesh_row(1, canopy="1e-3", snow="-0.0", surface="2.5E+01"),
            mesh_row(2, unsat="0.000000"),
            mesh_row(3),
        ],
        river_rows=[river_row(1, "1e-3"), river_row(2, "2.5E+01")],
        eol="\r\n",
        delimiter="\t",
        header_delimiter="   ",
        header_trailing_spaces="  ",
        blank_lines=True,
        trailing_newline=False,
    ).payload


def _assert_only_header_changed(
    original: bytes, restamped: cfg_ic.CfgIcDocument, *, expected_minute: str
) -> None:
    """逐行比对：只有 header 行变化，且行内只有 minute token 的字节变化。

    逐行（不是整文件 hash）——整文件比对说不出是哪一行坏了。
    """
    before = cfg_ic.parse(original)
    after_lines = restamped.lines
    assert len(after_lines) == len(before.lines)
    for index, (old, new) in enumerate(zip(before.lines, after_lines, strict=True)):
        if index == before.header_index:
            continue
        assert new == old, f"line {index} changed: {old!r} -> {new!r}"

    old_header = before.lines[before.header_index]
    new_header = after_lines[before.header_index]
    old_body, new_body = old_header.splitlines()[0], new_header.splitlines()[0]
    assert old_header[len(old_body) :] == new_header[len(new_body) :], "行尾符被改写"

    old_spans = _token_spans(old_body)
    new_spans = _token_spans(new_body)
    assert len(old_spans) == len(new_spans)
    minute_index = len(old_spans) - 1
    assert (
        new_body[new_spans[minute_index][0] : new_spans[minute_index][1]]
        == expected_minute
    )
    # 行内除 minute token 外的字节（含 token 之间的原始分隔与行尾空格）逐字不变。
    assert (
        old_body[: old_spans[minute_index][0]] == new_body[: new_spans[minute_index][0]]
    )
    assert (
        old_body[old_spans[minute_index][1] :] == new_body[new_spans[minute_index][1] :]
    )


def _token_spans(text: str) -> list[tuple[int, int]]:
    """测试侧**独立**实现的 token 切片（不复用被测模块的同名辅助）。"""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, char in enumerate(text):
        if char.isspace():
            if start is not None:
                spans.append((start, index))
                start = None
        elif start is None:
            start = index
    if start is not None:
        spans.append((start, len(text)))
    return spans


# --- 重戳的核心行为 ---


def test_clean_document_restamps_only_the_header_minute_token() -> None:
    payload = build_cfg_ic(mesh_count=3, river_count=4).payload
    doc = cfg_ic.parse(payload)

    result = restamp.restamp_to_absolute_time(doc, TARGET_T)

    assert _hand_minute(TARGET_T) == EXPECTED_MINUTE_T
    assert f"{TARGET_T_EPOCH_SECONDS / 60:.6f}" == EXPECTED_MINUTE_T
    _assert_only_header_changed(payload, result, expected_minute=EXPECTED_MINUTE_T)
    # 原文档未被就地改动（纯函数）。
    assert cfg_ic.render(doc) == payload


def test_dirty_document_keeps_every_unchanged_byte_and_the_header_layout() -> None:
    payload = _dirty_payload()
    doc = cfg_ic.parse(payload)

    result = restamp.restamp_to_absolute_time(doc, TARGET_T)

    _assert_only_header_changed(payload, result, expected_minute=EXPECTED_MINUTE_T)
    header = result.lines[result.header_index]
    assert header.endswith("\r\n"), header
    # header 行的多空格分隔与行尾空格原样保留（pin 式 `"\t".join` 在此变红）。
    assert "   " in header
    assert header.rstrip("\r\n").endswith("  ")
    # 数据行仍是 Tab 分隔、仍是 CRLF、末行仍无换行（整文件 `"\n".join` 在此变红）。
    rendered = cfg_ic.render(result)
    assert b"\t" in rendered
    assert not rendered.endswith(b"\n")
    assert rendered.count(b"\r\n") == payload.count(b"\r\n")


def test_tab_delimited_header_keeps_its_tabs_byte_for_byte() -> None:
    """成功重戳路径上必须有一份 **Tab 分隔的 header**，否则本模块的字节 oracle 是盲的。

    `_dirty_payload()` 与 `_clean_doc()` 的 header 分隔都是空格，文件里唯一
    `header_delimiter="\t"` 的用例红在 shape 闸门上、根本到不了 `replace_tokens`。于是
    把本模块自带的 `_token_spans` 里的 `char.isspace()` 换成 `char == " "` 的变异体在全套
    下存活：Tab 不再被当分隔符，token 数从 3 塌成 1，而没有任何用例喂过 Tab header。
    （`test_state_tools_qc.py` 的同名双胞胎不存在这个洞——它的脏矩阵本就带 Tab。）

    生产侧本就正确：`'3\t6\t27000000.000000  \r\n'` 重戳成
    `'3\t6\t29455920.000000  \r\n'`，Tab 与行尾空格逐字节留存。本用例只是补上那双眼睛。
    """
    payload = build_cfg_ic_rows(
        mesh_rows=[mesh_row(index) for index in (1, 2, 3)],
        river_rows=[river_row(1), river_row(2)],
        header_delimiter="\t",
        header_trailing_spaces="  ",
        eol="\r\n",
    ).payload
    # 构造自检：header 确实是 Tab 分隔（`header_delimiter` 被悄悄忽略时本行变红）。
    assert payload.split(b"\r\n")[0] == b"3\t6\t" + DEFAULT_MINUTE.encode() + b"  "

    result = restamp.restamp_to_absolute_time(cfg_ic.parse(payload), TARGET_T)

    assert result.lines[result.header_index] == f"3\t6\t{EXPECTED_MINUTE_T}  \r\n"
    _assert_only_header_changed(payload, result, expected_minute=EXPECTED_MINUTE_T)


def _dirty_payload_with_minute(minute: str) -> bytes:
    """与 `_dirty_payload()` 同一张脏矩阵，只把 header 的 minute token 换成给定文本。"""
    return build_cfg_ic_rows(
        mesh_rows=[mesh_row(1), mesh_row(2), mesh_row(3)],
        river_rows=[river_row(1, "1e-3"), river_row(2, "2.5E+01")],
        header_tokens=("3", "6", minute),
        eol="\r\n",
        delimiter="\t",
        header_delimiter="   ",
        header_trailing_spaces="  ",
        blank_lines=True,
        trailing_newline=False,
    ).payload


def test_minute_token_is_rewritten_even_when_the_pin_would_short_circuit() -> None:
    """偏离 4 的行为面：pin 的 `header_changed` 短路（`state_cli.py:288-296`）不移植。

    pin 先读出 `observed_minute`，`round(observed) != round(expected)` 为假就**原样返回
    字节未动的产物**。两条子形态各钉一条断言：
    (a) 已是目标分钟的**整数写法** `29455920` —— pin 保留原字节，yd 侧改写成 `%.6f`；
    (b) 目标比 header 晚 20 s（`round()` 仍相等）—— pin **静默保留旧值**，yd 侧写入
        `29455920.333333`（手算：`(1767355200 + 20) / 60`）。
    (c) **分桶边界**：header `29455919.51` 对目标 `29455920.49` —— 两者 `round()` 同为
        29455920，相距 **58.8 s** 仍落在 pin 的静默保留窗口内。这一条钉死「窗口 = round()
        落同一分钟」而不是「差 < 30 s」：后一种口径下本例本该被重写，故它是模块头偏离 4
        那句定量描述的行为面判别器。
    三条 minute 期望值都由手算 epoch 秒得出，不经被测模块。
    """
    payload = _dirty_payload_with_minute("29455920")
    doc = cfg_ic.parse(payload)

    # (a) round() 相等且分钟数完全一致：pin 恒等返回，yd 侧规范化为 %.6f。
    same_minute = restamp.restamp_to_absolute_time(doc, TARGET_T)
    assert f"{TARGET_T_EPOCH_SECONDS / 60:.6f}" == EXPECTED_MINUTE_T
    header = same_minute.lines[same_minute.header_index]
    assert "29455920.000000" in header
    assert cfg_ic.render(same_minute) != payload, "产物与输入字节相同 = pin 式短路"
    _assert_only_header_changed(payload, same_minute, expected_minute=EXPECTED_MINUTE_T)

    # (b) 目标晚 20 s：pin 的 round() 判定为「未变」，yd 侧照写秒级残差。
    plus_20s = restamp.restamp_to_absolute_time(doc, TARGET_T + timedelta(seconds=20))
    expected_minute = f"{(TARGET_T_EPOCH_SECONDS + 20) / 60:.6f}"
    assert expected_minute == "29455920.333333"
    assert round((TARGET_T_EPOCH_SECONDS + 20) / 60) == round(29455920.0), (
        "构造失效：pin 的 round() 判定在此必须为「未变」，否则这条用例证不了短路被剥离"
    )
    _assert_only_header_changed(payload, plus_20s, expected_minute=expected_minute)

    # (c) 分桶边界：header 分钟 29455919.51 对目标 29455920.49，round() 相等而相距 58.8 s。
    edge_payload = _dirty_payload_with_minute("29455919.51")
    edge_doc = cfg_ic.parse(edge_payload)
    edge_target = TARGET_T + timedelta(seconds=29.4)
    edge_minute = _hand_minute(edge_target)
    assert edge_minute == "29455920.490000"
    assert round(29455919.51) == round(float(edge_minute)) == 29455920, (
        "构造失效：pin 的 round() 判定在此必须为「未变」"
    )
    # 「差 < 30 s」那种口径在这里会判「已变」——本例正是它与真谓词分叉的地方。
    assert abs(float(edge_minute) - 29455919.51) * 60 > 58.0

    edge = restamp.restamp_to_absolute_time(edge_doc, edge_target)

    _assert_only_header_changed(edge_payload, edge, expected_minute=edge_minute)


def test_restamp_is_idempotent() -> None:
    doc = _clean_doc()
    once = restamp.restamp_to_absolute_time(doc, TARGET_T)
    twice = restamp.restamp_to_absolute_time(once, TARGET_T)
    assert cfg_ic.render(twice) == cfg_ic.render(once)


# --- header 形状闸门（#1430 的中毒 IC 闸门） ---


def test_two_token_header_is_refused_before_any_overwrite() -> None:
    """pin #1197 的两 token 形状：被定位的「minute」其实是 mesh 状态列数。

    MUST NOT 用 pin 的原始 `23106\\t6` 字面值——`cfg_ic.parse` 强制 header 首个数值 token
    等于实际 mesh 行数，那需要 23106 行 mesh，否则会红在 parse 的 `truncated` 上。
    """
    payload = build_cfg_ic_rows(
        mesh_rows=[mesh_row(i) for i in (1, 2, 3)],
        header_tokens=("3", "6"),
        header_delimiter="\t",
    ).payload
    doc = cfg_ic.parse(payload)

    with pytest.raises(ValueError) as excinfo:
        restamp.restamp_to_absolute_time(doc, TARGET_T)

    message = str(excinfo.value)
    assert message.startswith(restamp.STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID)
    assert "2 numeric token(s)" in message
    # 未产出任何文档：原文档字节不变。
    assert cfg_ic.render(doc) == payload


def test_unknown_five_numeric_token_header_is_refused() -> None:
    payload = build_cfg_ic_rows(
        mesh_rows=[mesh_row(i) for i in (1, 2, 3)],
        header_tokens=("3", "6", "0", "0.0", DEFAULT_MINUTE),
    ).payload
    doc = cfg_ic.parse(payload)

    with pytest.raises(ValueError) as excinfo:
        restamp.restamp_to_absolute_time(doc, TARGET_T)

    assert str(excinfo.value).startswith(
        restamp.STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID
    )
    assert "5 numeric token(s)" in str(excinfo.value)


def test_four_token_compat_header_restamps_the_last_numeric_token() -> None:
    """`<mesh> <river> <lake> <minute>`：minute 落在**最后一个** token，不是第 3 个。"""
    payload = build_cfg_ic_rows(
        mesh_rows=[mesh_row(i) for i in (1, 2, 3)],
        river_rows=[river_row(i) for i in (1, 2, 3, 4)],
        header_tokens=("3", "4", "0", DEFAULT_MINUTE),
    ).payload
    doc = cfg_ic.parse(payload)

    result = restamp.restamp_to_absolute_time(doc, TARGET_T)

    tokens = result.lines[result.header_index].split()
    assert tokens == ["3", "4", "0", EXPECTED_MINUTE_T]
    _assert_only_header_changed(payload, result, expected_minute=EXPECTED_MINUTE_T)


# --- `_ensure_utc` 的两条分支 ---


def test_naive_target_is_interpreted_as_utc() -> None:
    doc = _clean_doc()
    # DTZ001 豁免的理由：naive datetime 正是本条要钉死的输入（pin 的 `_ensure_utc` 视之为 UTC）。
    naive_target = datetime(2026, 1, 2, 12, 0)  # noqa: DTZ001
    naive = restamp.restamp_to_absolute_time(doc, naive_target)
    aware = restamp.restamp_to_absolute_time(doc, TARGET_T)
    assert cfg_ic.render(naive) == cfg_ic.render(aware)
    assert naive.lines[naive.header_index].split()[-1] == EXPECTED_MINUTE_T


def test_non_utc_aware_target_is_converted_to_utc() -> None:
    """漏掉 `astimezone` 的实现只用 UTC 的用例上恒绿，故必须有这一条。"""
    doc = _clean_doc()
    offset = restamp.restamp_to_absolute_time(
        doc, datetime(2026, 1, 2, 7, 0, tzinfo=timezone(timedelta(hours=-5)))
    )
    assert offset.lines[offset.header_index].split()[-1] == EXPECTED_MINUTE_T


# --- 两条调用路径同源（init 首态 / 发布前 T+12） ---


def test_init_and_publish_paths_differ_only_in_the_target() -> None:
    doc = _clean_doc()
    init_state = restamp.restamp_to_absolute_time(doc, TARGET_T)
    publish_state = restamp.restamp_to_absolute_time(doc, TARGET_T_PLUS_12)

    assert init_state.lines[doc.header_index].split()[-1] == EXPECTED_MINUTE_T
    assert (
        publish_state.lines[doc.header_index].split()[-1] == EXPECTED_MINUTE_T_PLUS_12
    )
    assert _hand_minute(TARGET_T_PLUS_12) == EXPECTED_MINUTE_T_PLUS_12
    # 两次只差 target：其余每一行逐字节相同。
    for index in range(len(doc.lines)):
        if index == doc.header_index:
            continue
        assert init_state.lines[index] == publish_state.lines[index]


def test_module_exposes_exactly_one_header_rewriting_entry_point() -> None:
    """源码机检：不得分裂成两个重戳入口或加 `mode` 开关。"""
    source = source_probe.read_source(restamp.__file__)
    public = {
        name
        for name in source_probe.definition_names(source)
        if not name.startswith("_") and callable(getattr(restamp, name, None))
    }
    assert public == {"restamp_to_absolute_time"}
    signature = inspect.signature(restamp.restamp_to_absolute_time)
    assert list(signature.parameters) == ["doc", "target"]


# --- 溯源、隔离与裁决 6/7 的机检闭合 ---


def test_ported_symbols_carry_their_own_provenance_comment() -> None:
    source = source_probe.read_source(restamp.__file__)
    segments = source_probe.definition_segments(source)
    for symbol in PORTED_SYMBOLS:
        assert symbol in segments, symbol
        # 恰好一条：取窗不越进邻居，删掉自己那行即变红。
        assert (
            segments[symbol].count("NWM@8ae9b8f2 packages/common/state_cli.py") == 1
        ), symbol
    assert (
        "NWM@8ae9b8f2 packages/common/state_cli.py"
        in source_probe.module_docstring_block(source)
    )


def test_module_has_no_nwm_runtime_import_and_no_db_symbols() -> None:
    source = source_probe.read_source(restamp.__file__)
    for forbidden in (
        "import packages",
        "from packages",
        "psycopg",
        "sqlalchemy",
        "DATABASE_URL",
        "sacct",
        "sbatch",
    ):
        assert forbidden not in source, forbidden
    # `safe_fs` 只作为**闭包切点的记述**出现在模块头，不得出现在代码里。
    body = source[len(source_probe.module_docstring_block(source)) :]
    assert "safe_fs" not in body


def test_module_writes_no_files() -> None:
    """裁决 6 的执行子句：本 issue 不写任何文件。"""
    source = source_probe.read_source(restamp.__file__)
    assert source_probe.write_surface_calls(source) == []


def test_module_defines_no_rekey_surface_symbol() -> None:
    """裁决 7 的执行子句：rekey 面路由 #16/#24，落地即死代码。"""
    names = source_probe.definition_names(source_probe.read_source(restamp.__file__))
    assert names.isdisjoint(REKEY_SYMBOLS), names & set(REKEY_SYMBOLS)
    # 集合本身也钉住：删掉一项时上面那条断言是「取消检查」而不是变红。
    assert {"_check_water_balance", "water_balance"} <= set(REKEY_SYMBOLS)
    for symbol in REKEY_SYMBOLS:
        assert not hasattr(restamp, symbol), symbol
    # `water_balance` 也不得作为重戳入口的形参出现。
    assert "water_balance" not in restamp.restamp_to_absolute_time.__code__.co_varnames


def test_module_docstring_block_is_a_prefix_and_counts_lines_like_ast() -> None:
    """`source[len(block):]` 取模块体（上一条用例的 `body`）的前提是**前缀**性质。

    两条边界各一例：(a) docstring 恰好结束于 EOF 且文件**无**末尾换行——无条件补 `\\n`
    的实现在这里返回的不再是前缀；(b) docstring 内含 `\\x0c`——`str.splitlines()` 会在
    它上面多断一行，而 `ast` 的 `end_lineno` 只按 `\\n` 计，切片会**少**切，把 docstring
    截断在半路（方向 fail-closed，但仍是错的）。
    """
    header = "# NWM@8ae9b8f2 packages/common/state_cli.py\n"
    for source in (
        '"""doc"""\nVALUE = 1\n',
        '"""doc"""',
        '"""doc"""\n',
        f'{header}"""doc\n\nmore\n"""\nimport os\n',
    ):
        block = source_probe.module_docstring_block(source)
        assert source.startswith(block), (source, block)
        assert block.rstrip().endswith('"""'), block

    # (b) docstring 内的换页符：`splitlines()` 口径下 `[: end_lineno]` 会切在 `\x0c` 后。
    form_feed = '"""doc\x0c 换页符\n"""\nVALUE = 1\n'
    assert len(form_feed.splitlines()) != form_feed.count("\n")
    block = source_probe.module_docstring_block(form_feed)
    assert block == '"""doc\x0c 换页符\n"""\n'
    assert form_feed[len(block) :] == "VALUE = 1\n"

    # (a) 无末尾换行时返回值与源码逐字节相等（不多一个 `\n`）。
    assert source_probe.module_docstring_block('"""doc"""') == '"""doc"""'


def test_module_documents_the_deliberate_deviations() -> None:
    head = source_probe.module_docstring_block(
        source_probe.read_source(restamp.__file__)
    )
    assert "刻意偏离" in head
    # 条数由 docstring 解析得出并与序号闭合（子串断言 `"三条" in head` 对改条数的变异体
    # 可能存活——docstring 别处也可能出现同一个词）。
    declared = source_probe.declared_deviation_count(head)
    assert declared == 5
    for ordinal in range(1, declared + 1):
        assert head.count(f"\n{ordinal}. ") == 1, ordinal
    assert f"\n{declared + 1}. " not in head
    # 五条偏离各自的关键词。
    assert "cfg_ic_header_shape" in head and "cfg_ic_header_minute_index" in head
    assert "with_replaced_lines" in head
    assert "safe_fs" in head or "atomic_write_bytes_no_follow" in head
    # 偏离 4：pin 的 `header_changed` 短路不移植（下一条用例是它的行为面证据）。
    assert "header_changed" in head
    # 偏离 4 的定量描述 MUST 是**谓词本身**，不是「差 < 30 s」那种近似（round() 落同一
    # 分钟，窗口可达近 60 s；`test_..._short_circuit` 的 (c) 子例是它的行为面证据）。
    assert "round(observed_minute) != round(expected_minute)" in head
    assert "< 30 s" not in head
    # 偏离 5：错误契约替换（pin 的 `StateManagerError` -> 本仓 `ValueError`）。
    assert "StateManagerError" in head


def test_module_imports_the_header_symbols_instead_of_re_porting_them() -> None:
    """`tasks.md:811` 的双权威副本禁令：header 判定基座归 `header_time`，本模块只 import。"""
    names = source_probe.definition_names(source_probe.read_source(restamp.__file__))
    assert names.isdisjoint(HEADER_TIME_SYMBOLS), names & set(HEADER_TIME_SYMBOLS)
    assert restamp.cfg_ic_header_minute_index is header_time.cfg_ic_header_minute_index
    assert restamp.cfg_ic_header_shape is header_time.cfg_ic_header_shape
