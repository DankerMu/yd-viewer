"""`yd-producer` 命令行入口：`prepare` / `init` / `run` 三个子命令的薄委托层。

入口层只承载 spec `cli-config` 明文钉死的四件事——子命令枚举、未知子命令拒绝、
`DATABASE_URL` 环境守卫、`run` 的状态目录守卫——业务实现全在各自模块（design.md
seam 6：入口层不做业务行为测试，但入口层自身的契约必须在此边界行使）。pinned:
test_parser_registers_exactly_three_subcommands、test_unknown_subcommand_exits_two_without_delegation、
test_database_url_guard_wins_before_parsing、test_run_rejects_missing_states_dir_and_creates_nothing。

退出码约定：

- `2`：argparse 用法错误（未知子命令、缺子命令、缺必需参数），由 argparse 自身产生；
  `run` 的 `ConfigError` / 配置装配错误同样返回 `2`；
- `1`：守卫失败（`DATABASE_URL`、`states/` 缺失或为空、NWM 解释器 fail-closed、
  `prepare` 编排的 `PrepareError`）；装载失败在 `prepare`/`init` 仍为 `1`；
- `3`：`prepare` 的 `BuilderUnavailableError`；`run` 任一 `STOPPED` / `JOB_FAILED` /
  `SUCCEEDED_CLEANUP_PENDING` 或运行期 controller/executor/driver/provider 错误。

**守卫位置**：`DATABASE_URL` 检查是 `main()` 的第一件事，先于 `parse_args` 与任何配置
装载（agent-ops §2.2）。路径形态：`--config` / `--local` / `prepare` 的 `--baseline`
在此边界一律 `Path.resolve()` 后再交给装载器/编排层。
"""

import argparse
import os
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from yd_producer import nwm
from yd_producer.config import Config, ConfigError, LocalConfig, load_config, load_local
from yd_producer.controller import (
    RunError,
    RunOutcome,
    RunSourcesError,
    RunSourcesReport,
    run_sources,
)
from yd_producer.executor import ExecutorError
from yd_producer.init import bootstrap
from yd_producer.nwm import ProductionAttemptDriver
from yd_producer.prepare import BuilderUnavailableError, PrepareError, run_prepare
from yd_producer.runlock import RunLockError, run_with_lock
from yd_producer.slurm import (
    SlurmJobExecutor,
    query_failure_exit_code,
    subprocess_runner,
)

__all__ = ["build_parser", "main"]

EXIT_GUARD = 1
EXIT_USAGE = 2
EXIT_RUNTIME = 3
EXIT_UNIMPLEMENTED = EXIT_RUNTIME
POLL_INTERVAL_SECONDS = 10
_SOURCE_ORDER = ("ifs", "gfs")

_DB_ENV_VAR = "DATABASE_URL"

_SUBCOMMAND_HELP = {
    "prepare": "一次性从外部基线包生成 yd_gfs / yd_ifs 变体与两个 GeoJSON",
    "init": "只在全新根建立首态；已有任一状态或 DONE 时拒绝",
    "run": "日常循环：发现前沿、提交作业、发布；永不自动 bootstrap",
}


def build_parser() -> argparse.ArgumentParser:
    """构造三入口 parser。

    独立取用是契约的一部分：`main(["--help"])` 的 `SystemExit` 在 main 内部抛出，拿不到
    parser 对象，故"子命令集合恰好是三项"的断言只能经本函数行使
    （`_SubParsersAction.choices` 的键集，而非 help 文本子串）。

    pinned: test_parser_registers_exactly_three_subcommands（断言取键集相等；多注册一个子
    命令即变红，help 文本子串探测则对此恒真）。
    """
    parser = argparse.ArgumentParser(
        prog="yd-producer",
        description="yd 流域 SHUD 预报 producer（node-22 计算环）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in _SUBCOMMAND_HELP.items():
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        sub.add_argument(
            "--config",
            required=True,
            type=Path,
            help="版本化 config.toml 路径（业务规则）",
        )
        sub.add_argument(
            "--local",
            required=True,
            type=Path,
            help="现场 local.toml 路径（gitignored，代码不内置任何默认）",
        )
        if name == "prepare":
            # 只加在 `prepare`：基线包路径只被它消费一次，做成三入口共有参数等于要求
            # `init`/`run` 也填一个它们从不读的值（compute-loop §6.1）。必需且无默认
            # ——spec cli-config「代码 MUST NOT 内置默认路径」。
            # pinned: test_required_option_sets_per_subcommand（`init`/`run` 两 lane 是
            # 判别性负面证据）、test_prepare_without_baseline_exits_two、
            # test_baseline_is_rejected_on_other_subcommands
            sub.add_argument(
                "--baseline",
                required=True,
                type=Path,
                help="外部基线模型包路径（一次性传入，不入 config.toml/local.toml）",
            )
    return parser


# --- 三入口委托目标 ----------------------------------------------------------
#
# 三个函数以**模块级名字**被 `main` 在调用时解析（不是导入时冻结进 dict），既是 spec
# 的薄委托形态，也让测试能注入记录型 fake 断言"未被调用"这类负面证据。
#
# pinned: test_dispatch_resolves_delegates_at_call_time（把派发改成导入时冻结的 dict 后
# 三个参数化用例全红）。注意其余注入用例对这三个名字只有 `count == 0` 的负面断言，对该
# 变异体恒绿——判别性只来自这条"打了桩的那一支确实被调用一次"的正面证据。
# `prepare` 内的 `run_prepare` 那一层另由 test_prepare_delegates_resolved_baseline_path
# （`fake.count == 1`）钉住。
#
# 业务体归后续 issue（issue #3 fixture 的 Non-goals 明确划出）：本 issue 交付的是守卫、
# 参数解析、退出码与薄外壳，全部为真实实现；走到这里说明全部守卫都已通过。


def prepare(local: LocalConfig, config: Config, baseline_root: Path) -> int:
    """`prepare`：守卫 + NWM 解释器 fail-closed 预检，随后薄委托 `prepare.run_prepare`。

    预检在此（而非只在薄外壳）是 spec Scenario「解释器缺失即停」的入口层落点——该
    Scenario 的主语是 `prepare`。预检失败抛 `ConfigError`，由 `main` 转成退出码 `1`，
    且不发起任何 builder 调用（pinned: test_prepare_stops_when_interpreter_missing、
    test_prepare_stops_when_interpreter_not_executable——两者都断言 builder 侧零调用）。

    `run_prepare` 以**模块级名字**解析（不在导入时冻结），与三个委托目标同纪律，使入口
    层测试能注入 fake 断言"未被调用"这类负面证据（pinned:
    test_prepare_delegates_resolved_baseline_path，`fake.count == 1` 是正面证据）。编排的
    两级失败由 `main` 分码：`BuilderUnavailableError` -> `3`，其余 `PrepareError` -> `1`
    （pinned: test_prepare_rejection_and_unimplemented_binding_use_different_exit_codes、
    test_prepare_error_becomes_exit_one）。

    成功路径上报告里的 `cleanup_warnings` MUST 打到 stderr（spec cli-config「prepare 的
    清理告警与残留证据 MUST 到达运维」）：它记的是 scratch 或 `YD_ROOT` 内 staging 的
    残留，是 agent-ops §8.1 那份 receipt 唯一能拿到的证据；丢掉它等于让运维在一次
    "成功"之后对着一棵有中间态的树。它 MUST NOT 改变退出码——四个终名都已提交
    （pinned: test_success_path_cleanup_warnings_reach_stderr_without_changing_the_exit_code
    ——断言的是 `capsys.readouterr().err`，故流向 stderr 这一点也被钉住）。

    报告不做 `None` 兜底：委托目标按契约必返回 `PrepareReport`，兜底只会把坏掉的委托
    伪装成成功。（pinned: test_a_none_report_is_never_reported_as_success——它不点名异常
    类，只断言"退出码既不是 `0` 也不是 `None`"，故 `return 0` 兜底与穿透到 `return None`
    的兜底两种变异体都变红。）
    """
    nwm.check_interpreter(local)
    report = run_prepare(local=local, config=config, baseline_root=baseline_root)
    for warning in report.cleanup_warnings:
        print(f"警告：{warning}", file=sys.stderr)
    return 0


def init(local: LocalConfig, config: Config) -> int:
    """`init`：薄委托到 `yd_producer.init.bootstrap`（非全新根拒绝、7 天窗定首轮、重戳）。

    入口体只做三件事：把「执行时刻」注入业务体（`now` 可注入是 `bootstrap` 的契约，7 天
    扫描窗对它有语义依赖）、把拒绝转成退出码 `1` 与 stderr 文本、把成功的落盘路径打到
    stdout。判定与落盘一律在 `yd_producer.init`，本函数 MUST NOT 自行解析 `YD_ROOT` 之外
    的任何路径。

    `bootstrap` 抛的 `ConfigError`（naive `now` 不可能在此发生；`rawscan.judge` 的配置类
    拒绝会）由 `main` 统一转成退出码 `1`，MUST NOT 在此吞掉。
    """
    report = bootstrap(local=local, config=config, now=datetime.now(UTC))
    if report.refusal is not None:
        # 部分落盘（`WRITE_FAILED`）时 `written` 非空且 `refusal` 非 None 同时成立，
        # 故成败一律以 `refusal` 判，MUST NOT 以 `written` 是否为空判。
        return _fail(f"init 拒绝执行（{report.refusal.value}）：{report.detail}")
    for path in report.written:
        print(path)
    # 成功理由走 **stderr**（round 5 R5-F）：`bootstrap` 的成功 `detail` 会点名被跳过候选上
    # 无法访问的 raw——链起点因此比 raw 实际到达情况更晚，而 init 一生只跑一次，落盘后重跑
    # 必被 `STATES_NOT_EMPTY` 拒绝，静默偏移没有自愈路径。此前本分支只 `print(path)`，那条
    # 规范里写着 MUST 的运维信号在端到端上被整个丢弃：终端输出与「不存在任何无法访问的
    # raw」时逐字节相同。走 stderr 而非 stdout 是为了不污染可管道消费的落盘路径列表。
    if report.detail:
        print(report.detail, file=sys.stderr)
    return 0


class _StatesGuardFailed(Exception):
    """states/ 守卫失败：锁内发现，退出码仍为 1。"""


def run(local: LocalConfig, config: Config) -> int:
    """`run`：同一 `run_with_lock` 生命周期内装配 `run_sources`。

    spec「run 永不自动 bootstrap」：`states/` 缺失或为空即报错停止，MUST NOT 调用 init
    逻辑，MUST NOT 自建该目录。锁竞争成功跳过返回 `0` 且零工厂调用、零发现。
    """

    def action() -> RunSourcesReport:
        guard = _check_states_dir(Path(local.yd_root) / "states")
        if guard is not None:
            raise _StatesGuardFailed(guard)
        runner = partial(
            subprocess_runner,
            command_timeout_seconds=local.slurm_command_timeout_seconds,
        )
        executors = {
            source: SlurmJobExecutor(
                required_fields=config.slurm.required_fields,
                clock=lambda: datetime.now(UTC),
                runner=runner,
            )
            for source in _SOURCE_ORDER
        }
        drivers = {
            source: ProductionAttemptDriver(
                grid_id=getattr(config.nwm_canonical_grid_id, source)
            )
            for source in _SOURCE_ORDER
        }
        poll_waits = {source: _production_poll_wait for source in _SOURCE_ORDER}
        failure_exit_codes = {
            source: partial(query_failure_exit_code, runner=runner)
            for source in _SOURCE_ORDER
        }
        return run_sources(
            config=config,
            local=local,
            executors=executors,
            drivers=drivers,
            poll_waits=poll_waits,
            failure_exit_codes=failure_exit_codes,
        )

    try:
        locked = run_with_lock(lock_path=local.cron.lock_path, action=action)
    except _StatesGuardFailed as exc:
        return _fail(str(exc))
    except RunLockError as exc:
        return _runtime_fail(str(exc))
    if not locked.acquired:
        return 0
    return _run_exit(locked.value)


def _production_poll_wait() -> None:
    time.sleep(POLL_INTERVAL_SECONDS)


def _run_exit(report: RunSourcesReport) -> int:
    reports = (*report.ifs, *report.gfs)
    if all(item.outcome is RunOutcome.SUCCEEDED for item in reports):
        return 0
    details = [item.detail for item in reports if item.detail]
    message = "; ".join(details) if details else "run 未全部成功"
    return _runtime_fail(message)


def _runtime_fail(message: str) -> int:
    print(f"错误：{message}", file=sys.stderr)
    return EXIT_RUNTIME


def _check_states_dir(states: Path) -> str | None:
    """返回拒绝理由；`None` 表示守卫通过。只读探测：不创建、不写入、不删除。

    存在性分类先于目录遍历：`states` 是普通文件时直接 `os.scandir()` 会抛
    `NotADirectoryError` 逃逸成 traceback（pinned:
    test_run_rejects_states_path_that_is_a_regular_file——断言 `"不是目录"` 且
    `"Traceback" not in err`；三条 lane 各断言本 lane 独有的措辞，见各用例注释）。
    「空判定用 `next(...)` 早停」是性能选择，不是行为选择（等价变异，不可判别：改成
    `list(entries)` 只是把整个目录读完再判空，空/非空的判定结果、返回的拒绝理由与是否
    写入都不变，没有可观测差别可断言）。
    """
    if not states.exists():
        return (
            f"状态目录不存在：{states}；"
            "run 永不自动 bootstrap，请先经授权执行 `yd-producer init`"
        )
    if not states.is_dir():
        return f"状态目录不是目录：{states}"
    with os.scandir(states) as entries:
        if next(entries, None) is None:
            return (
                f"状态目录为空：{states}；"
                "run 永不自动 bootstrap，请先经授权执行 `yd-producer init`"
            )
    return None


def _print_notes(exc: BaseException) -> None:
    """把在途异常的 `__notes__` 打到 stderr（spec cli-config）。

    `str(exc)` **不含** notes——notes 只在 `traceback.format_exception` 里出现，而本入口
    对**被 `main` 的分派 handler 接住的**异常刻意不打 traceback（未被接住而逃逸的异常仍会
    在控制台入口打出 traceback，见 test_a_none_report_is_never_reported_as_success 那条
    路径；该子句不是全称的）。`prepare` 的回滚/清理失败恰恰只以 `add_note` 附在原始异常上
    （`prepare.run_prepare` 步骤 8：抛出会替换原始异常并降级退出码），故不在这里渲染
    就等于把"`YD_ROOT` 里还有残留"这条证据丢掉。
    （"不打 traceback"pinned: test_config_error_becomes_exit_one_without_traceback、
    test_run_rejects_states_path_that_is_a_regular_file、
    test_prepare_with_executable_interpreter_reaches_production_builder_binding、
    test_cleanup_failure_does_not_downgrade_the_unimplemented_exit_code、
    test_cleanup_failure_text_reaches_stderr_on_the_failure_path、
    test_prepare_rejection_and_unimplemented_binding_use_different_exit_codes、
    test_prepare_error_becomes_exit_one、
    test_cleanup_note_reaches_stderr_on_the_exit_one_path——八条各断言
    `"Traceback" not in err`。）

    三个分派 handler 各调一次，而不是塞进 `_fail`：`BuilderUnavailableError` 那支退出码
    是 `3`、根本不经 `_fail`，只改 `_fail` 覆不全。`run_prepare` 的 `except BaseException`
    按类型无差别地给在途异常挂 note，故三支都可能拿到证据——但 `ConfigError` 那支今天按
    构造不可达，见下面第 3 条。

    三个调用点逐个交代（spec cli-config「prepare 的清理告警与残留证据 MUST 到达运维」的
    失败路径子句没有退出码限定，故三支都要有交代）：

    1. `BuilderUnavailableError`（退出码 `3`）pinned:
       test_cleanup_failure_text_reaches_stderr_on_the_failure_path；
    2. `PrepareError`（退出码 `1`）pinned:
       test_cleanup_note_reaches_stderr_on_the_exit_one_path（cand-r3-1；用例里的 note 文本
       与 `str(exc)` 无公共子串，否则 `_fail` 单独即可满足断言、不具判别性）；
    3. `ConfigError`（退出码 `1`）是**防御性声明**，今天按构造挂不上 note：
       `nwm.check_interpreter` 跑在 `run_prepare` 之前、builder 抛出的 `ConfigError` 在
       `prepare.py` 里被包装成 `PrepareError`、装载期的 `ConfigError` 由更早一个 handler
       接走。（等价变异，不可判别：无可达输入能让它渲染出任何东西。）
    """
    for note in getattr(exc, "__notes__", ()):
        print(note, file=sys.stderr)


def _fail(message: str) -> int:
    print(f"错误：{message}", file=sys.stderr)
    return EXIT_GUARD


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """CLI 入口（design.md seam 6）。

    `argv` 缺省取 `sys.argv[1:]`，`env` 缺省取 `os.environ`。argparse 的用法错误与
    `--help` 仍以 `SystemExit` 表达（退出码 `2` / `0`），本函数不拦截（pinned:
    test_unknown_subcommand_exits_two_without_delegation、
    test_missing_subcommand_exits_two_without_delegation、test_help_exits_zero）。
    """
    environ = os.environ if env is None else env
    if _DB_ENV_VAR in environ:
        # 只拒绝其存在，不读、不回显其值（agent-ops §2.2：先停，不尝试"连通看看"）。
        # pinned: test_database_url_guard_wins_before_parsing（断言 `_DB_URL not in err`）
        return _fail(
            f"检测到环境变量 {_DB_ENV_VAR}：yd producer 不连接 NWM PostgreSQL"
            "（agent-ops §2.2）。请清除该变量后重试，不要尝试连通"
        )

    args = build_parser().parse_args(argv)

    try:
        config = load_config(args.config.resolve())
        local = load_local(args.local.resolve(), config)
    except ConfigError as exc:
        if getattr(args, "command", None) == "run":
            print(f"错误：{exc}", file=sys.stderr)
            return EXIT_USAGE
        return _fail(str(exc))

    try:
        if args.command == "prepare":
            return prepare(local, config, args.baseline.resolve())
        if args.command == "init":
            return init(local, config)
        return run(local, config)
    except BuilderUnavailableError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        _print_notes(exc)
        return EXIT_UNIMPLEMENTED
    except PrepareError as exc:
        code = _fail(str(exc))
        _print_notes(exc)
        return code
    except ConfigError as exc:
        if args.command == "run":
            print(f"错误：{exc}", file=sys.stderr)
            _print_notes(exc)
            return EXIT_USAGE
        code = _fail(str(exc))
        _print_notes(exc)
        return code
    except RunSourcesError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except (RunError, ExecutorError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        if getattr(exc, "source", None):
            print(f"source={exc.source}", file=sys.stderr)
        if getattr(exc, "phase", None):
            print(f"phase={exc.phase}", file=sys.stderr)
        if getattr(exc, "job_id", None):
            print(f"job={exc.job_id}", file=sys.stderr)
        _print_notes(exc)
        return EXIT_RUNTIME
    except OSError as exc:
        if args.command != "run":
            raise
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except Exception as exc:
        if args.command != "run":
            raise
        print(f"错误：{exc}", file=sys.stderr)
        if getattr(exc, "source", None):
            print(f"source={exc.source}", file=sys.stderr)
        if getattr(exc, "phase", None):
            print(f"phase={exc.phase}", file=sys.stderr)
        if getattr(exc, "job_id", None):
            print(f"job={exc.job_id}", file=sys.stderr)
        _print_notes(exc)
        return EXIT_RUNTIME


if __name__ == "__main__":  # pragma: no cover - 入口点走 [project.scripts]
    sys.exit(main())
