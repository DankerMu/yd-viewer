"""`yd-producer` 命令行入口：`prepare` / `init` / `run` 三个子命令的薄委托层。

入口层只承载 spec `cli-config` 明文钉死的四件事——子命令枚举、未知子命令拒绝、
`DATABASE_URL` 环境守卫、`run` 的状态目录守卫——业务实现全在各自模块（design.md
seam 6：入口层不做业务行为测试，但入口层自身的契约必须在此边界行使）。

退出码约定（issue #3 fixture 钉死）：

- `2`：argparse 用法错误（未知子命令、缺子命令、缺必需参数），由 argparse 自身产生；
- `1`：守卫或配置失败（`DATABASE_URL`、`ConfigError`、`states/` 缺失或为空、NWM 解释器
  fail-closed）；
- `3`：分阶段未实现的业务体，stderr 指名归属任务号。

**守卫位置**：`DATABASE_URL` 检查是 `main()` 的第一件事，先于 `parse_args` 与任何配置
装载（agent-ops §2.2 把"不连 NWM 数据库"列为硬约束，环境本身有缺陷时最 fail-closed 的
形态是在解释任何参数之前拒绝，且代码路径只有一条）。**被接受的后果**：`DATABASE_URL`
存在时 `yd-producer --help` 同样以 `1` 退出而不打印帮助——环境错了就先修环境。

**路径形态**：`--config` / `--local` 在此边界一律 `Path.resolve()` 后再交给装载器
（agent-ops §8.2：cron 以 cwd=`$HOME` 调 `run`、人工补跑在 checkout 目录走同一入口，同
一条相对路径在两处指向不同文件，而装载器的失败消息忠实回显入参）。用 `Path.resolve()`
而非 `os.path.abspath`：后者对已是绝对路径的入参做词法 `..` 折叠，跨 symlink 会指向不
存在的目录。`resolve()` 路径不存在时不抛（`strict=False`），故不与 fail-closed 冲突。

两个参数都**必需**、无内置默认：spec cli-config 禁止内置现场默认值，而给 `--config`
一个默认等于在代码里第二次写死仓库布局。
"""

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from yd_producer import nwm
from yd_producer.config import Config, ConfigError, LocalConfig, load_config, load_local

# 只导入 `bootstrap` 这一个符号：`from yd_producer import init` 会用模块对象遮蔽本模块
# 的 `init()` 委托目标，`main` 的按名解析随即失效。
from yd_producer.init import bootstrap

__all__ = ["build_parser", "main"]

EXIT_GUARD = 1
EXIT_USAGE = 2
EXIT_UNIMPLEMENTED = 3

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
    return parser


# --- 三入口委托目标 ----------------------------------------------------------
#
# 三个函数以**模块级名字**被 `main` 在调用时解析（不是导入时冻结进 dict），既是 spec
# 的薄委托形态，也让测试能注入记录型 fake 断言"未被调用"这类负面证据。
#
# 业务体归后续 issue（issue #3 fixture 的 Non-goals 明确划出）：本 issue 交付的是守卫、
# 参数解析、退出码与薄外壳，全部为真实实现；走到这里说明全部守卫都已通过。


def prepare(local: LocalConfig, config: Config) -> int:
    """`prepare`：守卫 + NWM 解释器 fail-closed 预检；业务体归任务 10.3。

    预检在此（而非只在薄外壳）是 spec Scenario「解释器缺失即停」的入口层落点——该
    Scenario 的主语是 `prepare`。预检失败抛 `ConfigError`，由 `main` 转成退出码 `1`，
    且不发起任何 builder 调用。
    """
    nwm.check_interpreter(local)
    return _unimplemented("prepare", "10.3（prepare 编排：变体与几何产出）")


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
    return 0


def run(local: LocalConfig, config: Config) -> int:
    """`run`：状态目录守卫为真实实现；控制器循环归组 12–14，入口体承接者是任务 14.1。

    spec「run 永不自动 bootstrap」：`states/` 缺失或为空即报错停止，MUST NOT 调用 init
    逻辑，MUST NOT 自建该目录。
    """
    guard = _check_states_dir(Path(local.yd_root) / "states")
    if guard is not None:
        return _fail(guard)
    return _unimplemented("run", "14.1（run 主循环集成：`run_once` 骨架）")


def _check_states_dir(states: Path) -> str | None:
    """返回拒绝理由；`None` 表示守卫通过。只读探测：不创建、不写入、不删除。

    存在性分类先于目录遍历：`states` 是普通文件时直接 `os.scandir()` 会抛
    `NotADirectoryError` 逃逸成 traceback。空判定用 `next(...)` 早停，不遍历全目录。
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


def _fail(message: str) -> int:
    print(f"错误：{message}", file=sys.stderr)
    return EXIT_GUARD


def _unimplemented(command: str, owner: str) -> int:
    print(
        f"`{command}` 的业务实现尚未落地，归属任务 {owner}；"
        "入口守卫已全部通过（分阶段交付，见 openspec/changes/m2-producer-core）",
        file=sys.stderr,
    )
    return EXIT_UNIMPLEMENTED


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """CLI 入口（design.md seam 6）。

    `argv` 缺省取 `sys.argv[1:]`，`env` 缺省取 `os.environ`。argparse 的用法错误与
    `--help` 仍以 `SystemExit` 表达（退出码 `2` / `0`），本函数不拦截。
    """
    environ = os.environ if env is None else env
    if _DB_ENV_VAR in environ:
        # 只拒绝其存在，不读、不回显其值（agent-ops §2.2：先停，不尝试"连通看看"）。
        return _fail(
            f"检测到环境变量 {_DB_ENV_VAR}：yd producer 不连接 NWM PostgreSQL"
            "（agent-ops §2.2）。请清除该变量后重试，不要尝试连通"
        )

    args = build_parser().parse_args(argv)

    try:
        config = load_config(args.config.resolve())
        local = load_local(args.local.resolve(), config)
    except ConfigError as exc:
        return _fail(str(exc))

    try:
        if args.command == "prepare":
            return prepare(local, config)
        if args.command == "init":
            return init(local, config)
        return run(local, config)
    except ConfigError as exc:
        return _fail(str(exc))


if __name__ == "__main__":  # pragma: no cover - 入口点走 [project.scripts]
    sys.exit(main())
