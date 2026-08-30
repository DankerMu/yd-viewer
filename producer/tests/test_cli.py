"""`yd_producer.cli` 入口层测试（design.md seam 6、spec cli-config）。

全部用例**进程内**调用 `cli.main(argv, env=...)`，不起子进程：入口层自身的契约（三入口
枚举、未知子命令拒绝、`DATABASE_URL` 守卫、`run` 状态目录守卫、退出码三分）在此边界钉
死。`[project.scripts]` 注册是否真的生效由 `uv run yd-producer --help` 覆盖，进程内测试
覆盖不到那一点。

`env` 一律显式传入（守卫用例传 `{"DATABASE_URL": ...}`，其余传 `{}`）：依赖环境里恰好
没有 `DATABASE_URL` 是潜在 flake。

委托目标与装载器都以模块级名字注入记录型 fake——"fake 调用次数为 0"是本文件大量负面证据
的表达形式，只断言退出码测不出"报错前已经干了活"。
"""

import argparse
from pathlib import Path

import pytest
from cli_fixtures import write_config, write_fake_interpreter, write_local

from yd_producer import cli, nwm
from yd_producer import prepare as prepare_module
from yd_producer.config import load_config, load_local
from yd_producer.init import InitReport

# --- 记录型 fake -------------------------------------------------------------


class Recorder:
    """记录调用并返回固定结果；`delegate` 非空时转调真实实现。"""

    def __init__(self, result=None, delegate=None):
        self.calls: list[tuple[tuple, dict]] = []
        self._result = result
        self._delegate = delegate

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._delegate is not None:
            return self._delegate(*args, **kwargs)
        return self._result

    @property
    def count(self) -> int:
        return len(self.calls)


def _fake_everything(monkeypatch) -> dict[str, Recorder]:
    """把三个委托目标、两个装载器与薄外壳全部换成记录型 fake。"""
    fakes = {
        "prepare": Recorder(result=0),
        "init": Recorder(result=0),
        "run": Recorder(result=0),
        "load_config": Recorder(result=object()),
        "load_local": Recorder(result=object()),
        # `prepare` 的委托目标（issue #20）。它与 `prepare` 同时被换成 fake 是刻意的：
        # 两层各自承担一条负面证据——守卫拒绝时**两层**都必须零调用。
        "run_prepare": Recorder(result=None),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(cli, name, fake)
    fakes["invoke_mapping_builder"] = Recorder(result=None)
    monkeypatch.setattr(nwm, "invoke_mapping_builder", fakes["invoke_mapping_builder"])
    return fakes


def _exit_code(argv, env):
    """argparse 的用法错误/`--help` 以 `SystemExit` 表达，守卫与业务分支以返回值表达。"""
    try:
        return cli.main(argv, env=env)
    except SystemExit as exc:
        return exc.code


def _argv(command, tmp_path, *, baseline=None, **local_kwargs):
    """齐备 argv。`prepare` 额外带必需的 `--baseline`（`init`/`run` 不带）。

    `yd_root`/`scratch_root` 一并建出来：`prepare.run_prepare` 的步骤 0 预检要求两个运行
    根都是**已存在**的目录（打错一个字就凭空造出影子根、还返回成功，是 issue #20 复核里
    确认的失效）。这是 arrange 侧的新前置条件，本文件的断言一条未动——`init`/`run` 不经
    该预检，对它们是惰性的。
    """
    config_path = write_config(tmp_path)
    local_path = write_local(tmp_path, **local_kwargs)
    local = load_local(local_path, load_config(config_path))
    Path(local.yd_root).mkdir(parents=True, exist_ok=True)
    Path(local.scratch_root).mkdir(parents=True, exist_ok=True)
    argv = [command, "--config", str(config_path), "--local", str(local_path)]
    if command == "prepare":
        argv += [
            "--baseline",
            str(tmp_path / "baseline" if baseline is None else baseline),
        ]
    return argv


# --- 三入口枚举 --------------------------------------------------------------


def test_parser_registers_exactly_three_subcommands():
    """断言取 argparse 的子命令注册表，不做 help 文本子串探测。

    子串探测对"多注册一个子命令"恒真：help 里多出 `bootstrap` 时 `"prepare" in text`
    照样成立。键集相等才有判别力。
    """
    actions = [
        action
        for action in cli.build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    ]

    assert len(actions) == 1
    assert set(actions[0].choices) == {"prepare", "init", "run"}


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("prepare", {"--config", "--local", "--baseline"}),
        ("init", {"--config", "--local"}),
        ("run", {"--config", "--local"}),
    ],
)
def test_required_option_sets_per_subcommand(command, expected):
    """`--baseline` 只属 `prepare`（compute-loop §6.1）。

    断言取 argparse 注册表的**必需**选项集合而非 help 文本：`init`/`run` 那两条是判别性
    的负面证据——把 `--baseline` 加到公共循环里时，只测 `prepare` 的实现照样绿。
    """
    actions = [
        action
        for action in cli.build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    parser = actions[0].choices[command]
    required = {
        option
        for action in parser._actions
        if action.required
        for option in action.option_strings
    }

    assert required == expected


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"], env={})

    assert excinfo.value.code == 0
    assert capsys.readouterr().out != ""


# --- 用法错误 ----------------------------------------------------------------


def test_unknown_subcommand_exits_two_without_delegation(monkeypatch, capsys):
    fakes = _fake_everything(monkeypatch)

    assert _exit_code(["bootstrap"], env={}) == 2
    capsys.readouterr()
    assert all(fake.count == 0 for fake in fakes.values())


def test_missing_subcommand_exits_two_without_delegation(monkeypatch, capsys):
    fakes = _fake_everything(monkeypatch)

    assert _exit_code([], env={}) == 2
    capsys.readouterr()
    assert all(fake.count == 0 for fake in fakes.values())


@pytest.mark.parametrize("missing", ["--config", "--local"])
def test_missing_required_option_exits_two(monkeypatch, capsys, tmp_path, missing):
    """两个参数都必需、无内置默认：缺任一即用法错误，且不装载任何内置路径。"""
    fakes = _fake_everything(monkeypatch)
    argv = _argv("run", tmp_path)
    index = argv.index(missing)
    del argv[index : index + 2]

    assert _exit_code(argv, env={}) == 2

    err = capsys.readouterr().err
    assert missing in err
    assert fakes["load_config"].count == 0
    assert fakes["run"].count == 0


def test_prepare_without_baseline_exits_two(monkeypatch, capsys, tmp_path):
    """spec cli-config「prepare 的基线包路径必需」：不装载配置、不执行任何业务逻辑。"""
    fakes = _fake_everything(monkeypatch)
    argv = _argv("prepare", tmp_path)
    index = argv.index("--baseline")
    del argv[index : index + 2]

    assert _exit_code(argv, env={}) == 2

    assert "--baseline" in capsys.readouterr().err
    assert fakes["load_config"].count == 0
    assert fakes["prepare"].count == 0
    assert fakes["run_prepare"].count == 0


@pytest.mark.parametrize("command", ["init", "run"])
def test_baseline_is_rejected_on_other_subcommands(
    monkeypatch, capsys, tmp_path, command
):
    fakes = _fake_everything(monkeypatch)
    argv = _argv(command, tmp_path) + ["--baseline", str(tmp_path / "baseline")]

    assert _exit_code(argv, env={}) == 2

    capsys.readouterr()
    assert all(fake.count == 0 for fake in fakes.values())


# --- DATABASE_URL 守卫（agent-ops §2.2）--------------------------------------

_DB_URL = "postgresql://x"


@pytest.mark.parametrize(
    "argv_kind",
    ["run", "prepare", "init", "unknown-subcommand", "no-subcommand", "help"],
)
def test_database_url_guard_wins_before_parsing(
    monkeypatch, capsys, tmp_path, argv_kind
):
    """守卫在 `parse_args` 之前，故它与用法错误重叠时胜出，退出码恒为 1。

    后三份 argv 是判别性证据：守卫若落在解析之后，它们会分别得到 2 / 2 / 0。
    """
    fakes = _fake_everything(monkeypatch)
    # 用成员判定而非 `or` 兜底：`no-subcommand` 的 argv 是空列表（假值），`or` 会把它
    # 悄悄换成一份齐备参数的 argv，那条 evidence 就不再被行使。
    special = {
        "unknown-subcommand": ["bootstrap"],
        "no-subcommand": [],
        "help": ["--help"],
    }
    argv = special[argv_kind] if argv_kind in special else _argv(argv_kind, tmp_path)

    assert _exit_code(argv, env={"DATABASE_URL": _DB_URL}) == 1

    err = capsys.readouterr().err
    assert "DATABASE_URL" in err
    assert _DB_URL not in err  # 只拒绝其存在，不回显其值
    assert all(fake.count == 0 for fake in fakes.values())


# --- 配置装载失败 ------------------------------------------------------------


def test_config_error_becomes_exit_one_without_traceback(capsys, tmp_path):
    argv = _argv("run", tmp_path)
    argv[argv.index("--config") + 1] = str(tmp_path / "absent-config.toml")

    assert _exit_code(argv, env={}) == 1

    err = capsys.readouterr().err
    assert "absent-config.toml" in err
    assert "Traceback" not in err


def test_relative_paths_are_resolved_before_reaching_loaders(monkeypatch, tmp_path):
    """CLI 边界 `Path.resolve()` 后再交给装载器（库层仍忠实回显它收到的入参）。"""
    from yd_producer import config as config_module

    # 刻意不用 `config.toml`/`local.toml` 两个缺省名：用缺省名时，一个忽略入参、直接
    # `Path("config.toml").resolve()` 的实现在 cwd=tmp_path 下产出同一个绝对路径，断言
    # 无判别力。
    write_config(tmp_path, "c1.toml")
    write_local(tmp_path, name="l1.toml")
    load_config = Recorder(delegate=config_module.load_config)
    load_local = Recorder(delegate=config_module.load_local)
    monkeypatch.setattr(cli, "load_config", load_config)
    monkeypatch.setattr(cli, "load_local", load_local)
    monkeypatch.chdir(tmp_path)

    # `init` 的业务体已落地（任务 11.1），故这里不再是退出码 3：`tmp_path` 下没有变体
    # 目录，真实 `bootstrap` 在阶段 A 判 `VARIANT_MISSING` 并以 `EXIT_GUARD` 退出。本用例
    # 的主张与退出码无关——它只要求装载器**已经**收到解析后的绝对路径。
    assert (
        _exit_code(["init", "--config", "c1.toml", "--local", "l1.toml"], {})
        == cli.EXIT_GUARD
    )

    assert load_config.calls[0][0][0] == (tmp_path / "c1.toml").resolve()
    assert load_local.calls[0][0][0] == (tmp_path / "l1.toml").resolve()


def test_error_message_carries_resolved_absolute_path(monkeypatch, capsys, tmp_path):
    write_local(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert (
        _exit_code(["run", "--config", "missing.toml", "--local", "local.toml"], env={})
        == 1
    )

    assert str((tmp_path / "missing.toml").resolve()) in capsys.readouterr().err


# --- run 的状态目录守卫（spec「run 永不自动 bootstrap」）---------------------

# 本 lane 的 `yd_root` 一律取 `custom-yd-root` 而非 `yd`：判别性期望值，不是"换个名字好
# 看"。`write_local` 的 scratch_root 是 `tmp_path/scratch`，故 `scratch_root.parent/"yd"`
# 与 `tmp_path/"yd"` 逐字相同——守卫若把树认错成 scratch 侧（node-22 上 yd_root 在 NFS、
# scratch_root 在本地盘，是两棵真不同的树，见 docs/agent-ops.md），用 `yd` 命名的用例仍
# 全绿（实测该变异体存活全套）。叶名只要不可由 local.toml 其余字段推出，断言的路径就只能
# 来自 `local.yd_root`。改回 `yd` 即毁掉本 lane 的全部判别力。


def test_run_rejects_missing_states_dir_and_creates_nothing(
    monkeypatch, capsys, tmp_path
):
    init_fake = Recorder(result=0)
    monkeypatch.setattr(cli, "init", init_fake)
    yd_root = tmp_path / "custom-yd-root"
    yd_root.mkdir()
    argv = _argv("run", tmp_path, yd_root=yd_root)

    assert _exit_code(argv, env={}) == 1

    err = capsys.readouterr().err
    assert str(yd_root / "states") in err
    # 三条 lane 各断言**本 lane 独有**的措辞：只断言退出码与路径的话，把"不存在"这条
    # 分类整个删掉、由 `is_dir()` 分支兜底报「不是目录」，用例照样绿，而运维会去查文件
    # 类型，真因却是 `.venv`/`states` 缺失（agent-ops §7.2 禁止他们自行重建）。
    assert "不存在" in err
    assert not (yd_root / "states").exists()  # 未自建
    assert init_fake.count == 0


def test_run_rejects_empty_states_dir(monkeypatch, capsys, tmp_path):
    init_fake = Recorder(result=0)
    monkeypatch.setattr(cli, "init", init_fake)
    yd_root = tmp_path / "custom-yd-root"
    states = yd_root / "states"
    states.mkdir(parents=True)
    argv = _argv("run", tmp_path, yd_root=yd_root)

    assert _exit_code(argv, env={}) == 1

    err = capsys.readouterr().err
    assert str(states) in err
    assert "为空" in err
    assert list(states.iterdir()) == []
    assert init_fake.count == 0


def test_run_rejects_states_path_that_is_a_regular_file(monkeypatch, capsys, tmp_path):
    """陈旧普通文件占位：存在性分类先于遍历，`NotADirectoryError` 不得逃逸成 traceback。"""
    init_fake = Recorder(result=0)
    monkeypatch.setattr(cli, "init", init_fake)
    yd_root = tmp_path / "custom-yd-root"
    yd_root.mkdir()
    states = yd_root / "states"
    states.write_text("stale placeholder\n", encoding="utf-8")
    argv = _argv("run", tmp_path, yd_root=yd_root)

    assert _exit_code(argv, env={}) == 1

    err = capsys.readouterr().err
    assert str(states) in err
    assert "不是目录" in err
    assert "Traceback" not in err
    assert states.read_text(encoding="utf-8") == "stale placeholder\n"
    assert init_fake.count == 0


def test_run_with_non_empty_states_reaches_staged_unimplemented(
    monkeypatch, capsys, tmp_path
):
    """正控制：守卫全过后进入分阶段未实现分支，退出码 3 且 stderr 指名归属任务号。"""
    init_fake = Recorder(result=0)
    monkeypatch.setattr(cli, "init", init_fake)
    yd_root = tmp_path / "custom-yd-root"
    states = yd_root / "states"
    states.mkdir(parents=True)
    (states / "gfs").mkdir()
    argv = _argv("run", tmp_path, yd_root=yd_root)

    assert _exit_code(argv, env={}) == 3

    assert "14.1" in capsys.readouterr().err
    assert init_fake.count == 0


# --- prepare 的解释器预检（spec「解释器缺失即停」）---------------------------


def test_prepare_stops_when_interpreter_missing(monkeypatch, capsys, tmp_path):
    runner = Recorder(result=None)
    monkeypatch.setattr(nwm, "invoke_mapping_builder", runner)
    absent = tmp_path.resolve() / "nwm" / ".venv" / "bin" / "python"
    argv = _argv("prepare", tmp_path, python=absent)

    assert _exit_code(argv, env={}) == 1

    err = capsys.readouterr().err
    assert str(absent) in err
    assert "不存在" in err  # 与"不是普通文件"/"不可执行"两 lane 区分
    assert runner.count == 0


def test_prepare_stops_when_interpreter_not_executable(monkeypatch, capsys, tmp_path):
    runner = Recorder(result=None)
    monkeypatch.setattr(nwm, "invoke_mapping_builder", runner)
    script = tmp_path.resolve() / "python-no-x"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o644)
    argv = _argv("prepare", tmp_path, python=script)

    assert _exit_code(argv, env={}) == 1

    err = capsys.readouterr().err
    assert str(script) in err
    assert "不可执行" in err
    assert runner.count == 0


# --- prepare 的委托与两级退出码（issue #20）---------------------------------


def _prepare_argv(tmp_path, **kwargs):
    """带可执行假解释器的 `prepare` argv：越过预检后进入真正的编排委托。"""
    script = write_fake_interpreter(
        tmp_path.resolve() / "fake-python", tmp_path.resolve() / "record.json"
    )
    return _argv("prepare", tmp_path, python=script, **kwargs)


def test_prepare_with_executable_interpreter_reaches_production_builder_binding(
    monkeypatch, capsys, tmp_path
):
    """正控制：可执行解释器下越过预检，进入生产 builder 绑定并以退出码 `3` 停。

    预检不代替调用：绑定在**发起任何子进程之前**失败，故薄外壳零调用。
    """
    runner = Recorder(result=None)
    monkeypatch.setattr(nwm, "invoke_mapping_builder", runner)

    assert _exit_code(_prepare_argv(tmp_path), env={}) == 3

    err = capsys.readouterr().err
    assert prepare_module.BUILDER_OWNER in err  # 指名归属
    # 归属断言取字面量：只断言模块常量出现在消息里是自指的（把常量置空并删掉归属子句
    # 仍然全绿），测不出"消息里到底有没有指名归属"。
    assert "归属 M4" in err
    assert "Traceback" not in err
    assert runner.count == 0


def test_cleanup_failure_does_not_downgrade_the_unimplemented_exit_code(
    monkeypatch, capsys, tmp_path
):
    """清理失败 MUST NOT 把退出码 `3` 降级成 `1`（issue #20 复核 cand-02）。

    这是今天唯一生产可达的那一支：`cli` 传的就是生产 `default_builder`，绑定抛
    `BuilderUnavailableError` 之后清理 scratch；清理失败若替换掉正在传播的异常，`main`
    的 `except BuilderUnavailableError` 就不再匹配，运维拿到 `1` 会去改一份没问题的配置。
    """
    monkeypatch.setattr(nwm, "invoke_mapping_builder", Recorder(result=None))

    def refuse(*args, **kwargs):
        raise prepare_module.safe_fs.SafeFilesystemError(
            "injected cleanup failure", kind="io"
        )

    # 两个删除原语一起注入：编排改用哪一个来清 scratch 都不影响本用例要钉的性质。
    monkeypatch.setattr(prepare_module.safe_fs, "remove_tree_allow_symlinks", refuse)
    monkeypatch.setattr(prepare_module.safe_fs, "rmtree_no_follow", refuse)

    assert _exit_code(_prepare_argv(tmp_path), env={}) == 3

    err = capsys.readouterr().err
    assert "归属 M4" in err
    assert "Traceback" not in err


def test_cleanup_failure_text_reaches_stderr_on_the_failure_path(
    monkeypatch, capsys, tmp_path
):
    """清理失败的**文本**必须到达运维，不只是退出码（cand-r2-A1）。

    上一条用例只断言退出码没被降级，对"证据是否可见"恒绿：`str(exc)` 不含
    `__notes__`，而 `prepare` 的回滚失败只以 `add_note` 附在原始异常上。渲染缺失时
    `YD_ROOT`/scratch 里的残留在 agent-ops §8.1 的 receipt 上没有任何痕迹。
    """
    monkeypatch.setattr(nwm, "invoke_mapping_builder", Recorder(result=None))

    def refuse(*args, **kwargs):
        raise prepare_module.safe_fs.SafeFilesystemError(
            "injected cleanup failure", kind="io"
        )

    monkeypatch.setattr(prepare_module.safe_fs, "remove_tree_allow_symlinks", refuse)
    monkeypatch.setattr(prepare_module.safe_fs, "rmtree_no_follow", refuse)

    assert _exit_code(_prepare_argv(tmp_path), env={}) == 3

    err = capsys.readouterr().err
    assert "归属 M4" in err  # 原始异常还在，没被清理失败顶掉
    assert "injected cleanup failure" in err  # 清理失败也在
    assert "Traceback" not in err


def test_success_path_cleanup_warnings_reach_stderr_without_changing_the_exit_code(
    monkeypatch, capsys, tmp_path
):
    """成功路径的 `cleanup_warnings` 必须打出来，且退出码仍为 `0`（cand-r2-A2）。

    告警说的是"四个终名都提交了，但 staging/scratch 还有残留"。升格成失败会让重跑撞上
    拒绝覆盖守卫；丢掉则运维不知道有中间态留在 `YD_ROOT` 里。
    """
    warnings = ("残留 staging：/x/.prepare-staging-1", "残留 scratch：/y/prepare-1")
    fake = Recorder(
        result=prepare_module.PrepareReport(
            variants={},
            rivers_geojson=tmp_path / "rivers.geojson",
            boundary_geojson=tmp_path / "boundary.geojson",
            cleanup_warnings=warnings,
        )
    )
    monkeypatch.setattr(cli, "run_prepare", fake)

    assert _exit_code(_prepare_argv(tmp_path), env={}) == 0

    err = capsys.readouterr().err
    for warning in warnings:
        assert warning in err


def test_prepare_rejection_and_unimplemented_binding_use_different_exit_codes(
    monkeypatch, capsys, tmp_path
):
    """同一组参数：干净根 -> `3`（这条路还没通）；终名已存在 -> `1`（拒绝执行）。

    两码可区分是硬要求——合并成一个码，运维无从判断该改配置还是该等 M4。
    """
    monkeypatch.setattr(nwm, "invoke_mapping_builder", Recorder(result=None))
    argv = _prepare_argv(tmp_path)
    yd_root = tmp_path.resolve() / "yd"

    # 第一段：干净根。同一份 `argv` 走到生产 builder 绑定，以 `3` 停。这一段是本用例的
    # 判别力所在——缺了它，把 `EXIT_UNIMPLEMENTED` 并进 `EXIT_GUARD` 仍然全绿。
    assert _exit_code(argv, env={}) == 3

    capsys.readouterr()  # 排空第一段的 stderr，下面只断言第二段的输出

    # 第二段：终名已存在。同一份 `argv`，只改 `YD_ROOT` 的状态，以 `1` 停。
    (yd_root / "input" / "models" / "yd_gfs").mkdir(parents=True)

    assert _exit_code(argv, env={}) == 1

    err = capsys.readouterr().err
    assert str(yd_root / "input" / "models" / "yd_gfs") in err
    assert "Traceback" not in err


def test_prepare_delegates_resolved_baseline_path(monkeypatch, tmp_path):
    """`--baseline` 与 `--config`/`--local` 同纪律：`Path.resolve()` 后再传下游。

    fake 返回一份**真** `PrepareReport`（而不是 `None`）：入口层要读它的
    `cleanup_warnings`，而 `cli.prepare` 刻意不做 `None` 兜底——兜底会把坏掉的委托伪装
    成成功。这里的报告清理干净，故本用例对 stderr 保持沉默。
    """
    fake = Recorder(
        result=prepare_module.PrepareReport(
            variants={},
            rivers_geojson=tmp_path / "rivers.geojson",
            boundary_geojson=tmp_path / "boundary.geojson",
        )
    )
    monkeypatch.setattr(cli, "run_prepare", fake)
    monkeypatch.chdir(tmp_path)
    argv = _prepare_argv(tmp_path, baseline="baseline")

    assert _exit_code(argv, env={}) == 0

    assert fake.count == 1
    assert fake.calls[0][1]["baseline_root"] == (tmp_path / "baseline").resolve()


def test_prepare_error_becomes_exit_one(monkeypatch, capsys, tmp_path):
    def raising(**kwargs):
        raise prepare_module.PrepareError("变体 reach 数与 reach_count 不符")

    monkeypatch.setattr(cli, "run_prepare", raising)

    assert _exit_code(_prepare_argv(tmp_path), env={}) == 1

    err = capsys.readouterr().err
    assert "变体 reach 数与 reach_count 不符" in err
    assert "Traceback" not in err


def test_cleanup_note_reaches_stderr_on_the_exit_one_path(
    monkeypatch, capsys, tmp_path
):
    """退出码 `1` 的失败路径同样渲染 `__notes__`（cand-r3-1）。

    spec cli-config「prepare 的清理告警与残留证据 MUST 到达运维」的失败路径子句没有退出
    码限定，但此前只有 `BuilderUnavailableError`（退出码 `3`）那一支被钉住：删掉
    `except PrepareError` 分支里的 `_print_notes(exc)` 全套仍然全绿。M4 之后这一支才是主
    要残留路径——提交阶段失败叠加清理失败即退出码 `1` 且 `YD_ROOT`/scratch 有残留。

    note 文本与 `str(exc)` **刻意无公共子串**：note 若是异常消息的子串，`_fail(str(exc))`
    单独就能满足断言，删掉 `_print_notes` 照样绿，用例不具判别性。
    """

    def raising(**kwargs):
        exc = prepare_module.PrepareError("提交失败：变体 rename 撞上既有条目")
        exc.add_note("回滚/清理未完成：injected rollback residue")
        raise exc

    monkeypatch.setattr(cli, "run_prepare", raising)

    assert _exit_code(_prepare_argv(tmp_path), env={}) == 1

    err = capsys.readouterr().err
    assert "提交失败：变体 rename 撞上既有条目" in err  # 原始异常消息
    assert "injected rollback residue" in err  # notes 被渲染
    assert "Traceback" not in err


def test_a_none_report_is_never_reported_as_success(monkeypatch, tmp_path):
    """委托返回 `None` 时 MUST NOT 报成成功（`cli.prepare` 的"报告不做 `None` 兜底"）。

    断言里两个部件都是承重的，别"化简"掉：

    - `except BaseException: rc = "escaped"` 用的是**哨兵**而不是 `None`。逃逸与"返回
      `None`"是两种不同的结局，收敛成同一个值会漏掉一整类兜底变异体；
    - `rc is not None` 钉的正是那一类：`if report is not None: ... return 0` 之后**穿透**
      到函数末尾的变异体让 `main()` 返回 `None`，在真实边界 `sys.exit(main())` 上就是
      `sys.exit(None)`、进程退出码 `0`——恰恰是本条契约要禁的"坏掉的委托被报成成功"。
      只写 `rc != 0` 时该变异体存活。

    本用例不点名任何异常类：钉的是"不得报成成功"这条契约，不是当前实现偶然抛出的
    `AttributeError`。两种同样正确的替代实现（抛 `PrepareError` 走退出码 `1`、或以退出码
    `2` 显式报错）下它都仍绿。附带记录：这条路径今天以未被三个 handler 接住的异常收场，
    故控制台入口上会打出 traceback，见 `cli._print_notes` 的 docstring。
    """
    monkeypatch.setattr(cli, "run_prepare", Recorder(result=None))

    try:
        rc = cli.main(_prepare_argv(tmp_path), env={})
    except SystemExit as exc:  # 必须排在 BaseException 之前
        rc = exc.code
    except BaseException:  # noqa: BLE001 - 见 docstring：结局分三类，逃逸是其中一类
        rc = "escaped"  # 哨兵：逃逸当然不是成功，但也不是"返回 None"

    assert rc != 0 and rc is not None


@pytest.mark.parametrize("command", ["prepare", "init", "run"])
def test_dispatch_resolves_delegates_at_call_time(monkeypatch, tmp_path, command):
    """三个委托目标 MUST 以**模块级名字**在调用时解析，而非导入时冻结进 dict。

    判别性：本文件其余注入用例对这三个名字只断言 `count == 0`，那是恒真的负面证据——把
    `main` 的派发改成导入时冻结的 dict 之后，`monkeypatch` 装上的 fake 根本不会被查到，
    真实委托目标照跑，而 `count == 0` 依旧成立（实测该变异体全套 1061 passed 存活）。
    唯一能判别的是"打了桩的那一支确实被调用了一次"这条**正面**证据。

    仍在 seam 6 边界之内：三支全部换成记录型 fake，本用例只观察派发去向与退出码透传，
    不行使任何业务体。
    """
    fakes = {name: Recorder(result=0) for name in ("prepare", "init", "run")}
    for name, fake in fakes.items():
        monkeypatch.setattr(cli, name, fake)

    assert _exit_code(_argv(command, tmp_path), env={}) == 0

    assert fakes[command].count == 1
    assert [name for name, fake in fakes.items() if fake.count] == [command]


def test_init_reaches_the_real_business_body(capsys, tmp_path):
    """正控制：`init` 在守卫全过后进入**真实业务体**（任务 11.1 已落地）。

    本用例是 `test_init_reaches_staged_unimplemented` 的等价改写：原意「守卫全过后
    `init` 不在入口层被拦」逐字保留，只是可观测的落点从「分阶段未实现」变成了业务体的
    拒绝——`tmp_path` 下没有变体目录，`bootstrap` 在阶段 A 判 `variant_missing`。判据取
    「拒绝理由词表里的项出现在 stderr」而非退出码本身：退出码 `1` 与入口层的
    `DATABASE_URL`/`ConfigError` 守卫共用，单看它区分不出是谁拒绝的。
    """
    assert _exit_code(_argv("init", tmp_path), env={}) == cli.EXIT_GUARD

    err = capsys.readouterr().err
    assert "variant_missing" in err
    assert "11.1" not in err  # 已不再是分阶段未实现的外壳
    assert "尚未落地" not in err


def test_init_success_detail_reaches_the_operator_on_stderr(capsys, monkeypatch):
    """[桶 C-12] `init` 成功时的运维理由 MUST 在 CLI 边界外露（round 5 R5-F）。

    `bootstrap` 的成功 `detail` 会点名被跳过候选上无法访问的 raw——链起点因此比 raw 实际
    到达情况更晚。init 一生只跑一次，落盘之后重跑必被 `STATES_NOT_EMPTY` 拒绝，静默偏移
    没有自愈路径，所以 `specs/init-bootstrap/spec.md` 把「成功理由 MUST 点名」写成 MUST。

    round 4 的修复把该 MUST 的 oracle 落在 `InitReport.detail`（库边界）上，而
    `cli.init` 的成功分支只 `print(path)`、从不外露 `detail`：库层合规、用户可观测行为
    逐字节未变，MUST 在端到端上归零。本行把 oracle 挪到**用户边界**上。

    落盘路径列表走 stdout（可管道消费），理由走 stderr，两者 MUST 分列。

    判别变异体：删掉 `cli.init` 里那句 `print(report.detail, file=sys.stderr)` ->
    本行必红。
    """
    detail = "ifs 首轮 T=2026082512；ifs 的链起点跳过了更早的候选，那些候选上有 1 个预期 raw 文件**无法访问**"
    written = (Path("/yd/states/ifs/2026082512.cfg.ic"),)
    monkeypatch.setattr(
        cli,
        "bootstrap",
        lambda **_: InitReport(written=written, refusal=None, detail=detail),
    )

    assert cli.init(local=None, config=None) == 0

    captured = capsys.readouterr()
    # 落盘路径在 stdout，且**只有**它——理由不得污染可管道消费的路径列表。
    assert captured.out.splitlines() == [str(written[0])]
    # 理由在 stderr，逐字外露。
    assert detail in captured.err
