"""`test_cli.py` / `test_nwm.py` 共用的 TOML fixture。

与 `test_config.py` 的 fixture **刻意不共用**：那份是配置装载器的账本之一（点分闭包由
round-trip 用例逐值承重），这里只需要"一份能过装载器的齐备配置"以便把测试焦点放在入口
层与薄外壳。两份各自独立转录同一张 schema。

TOML 逐字写出而非由 dict 渲染：入口层测试不校验 schema，写死文本更易读，且新增必需字段
时这里会直接红（`ConfigError`），不会静默漂移。

唯一的例外是 `nwm_mapping_builder_module`：它必须能取**第二个不同的值**，否则"薄外壳调
用的 module 名取自 `config.toml`"这条断言的期望值与一个把 module 名写死回代码的实现产出
完全相同（实测：把 `config.nwm_mapping_builder_module` 换成字面量后全套仍全绿）。故该行
从模板里拆出来单独参数化——不用 `str.format`：TOML 里 `bundles` 含 `{lead}` 占位符。

`nwm_canonical_grid_id.gfs` / `.ifs` 走**同一条纪律的两个方向**（issue #20）：

* 两者 MUST 互不相同。`prepare` 的"两次 builder 调用的 `grid_id` 不同"这条断言，在两个
  值相同时退化成永真式——一个把 `grid_id` 写死成同一个常量的实现照样绿。
* 两者都 MUST 能取第二组值（`ALT_CANONICAL_GRID_IDS`），否则"`grid_id` 取自
  `config.toml`"与"取自代码里的字面量"两种实现产出完全相同。
"""

import os
from pathlib import Path

MAPPING_BUILDER_MODULE = "workers.mapping_builder.cli"
# 第二个点分名，与上面**必须不同**：判别性期望值，不是"另一个合法配置"。
ALT_MAPPING_BUILDER_MODULE = "other.builder.entry"

# 逐 source 的 NWM canonical grid 标识。两个值**必须互不相同**（见模块头）；生产取值的
# 复核归 #29，这里是合成值。
CANONICAL_GRID_IDS = {"gfs": "fixture-grid-gfs", "ifs": "fixture-grid-ifs"}
# 第二组值，与上面逐键**必须不同**：判别性期望值。
ALT_CANONICAL_GRID_IDS = {"gfs": "alt-grid-gfs", "ifs": "alt-grid-ifs"}

# `nwm_mapping_builder_module` 是 issue #3 随 #32 三步落地的必需键；缺它则装载即 fail closed
_CONFIG_HEAD = """\
forecast_days = 7
output_interval_minutes = 60
checkpoint_hours = [12]
reach_count = 3988
"""

_CONFIG_TAIL = """\

[cycle]
hours = [0, 12]

[variants]
gfs = "input/models/yd_gfs"
ifs = "input/models/yd_ifs"

[raw.ifs]
lead_hours = [0, 3, 6]
variables = ["fixture-var-a"]
bundles = ["fixture-ifs-{lead}.grib2"]
f000_special = false

[raw.gfs]
lead_hours = [0, 6, 12]
variables = ["fixture-var-c"]
bundles = ["fixture-gfs-{lead}.grib2"]
f000_special = true

[slurm]
required_fields = ["partition", "account", "cpus", "memory", "walltime"]
"""

_LOCAL_TEMPLATE = """\
yd_root = {yd_root}
scratch_root = {scratch_root}
shud_binary = {shud_binary}

[nwm]
raw_root = {raw_root}
checkout_root = {checkout_root}
python = {python}

[slurm]
partition = "cpu"
account = "yd-forecast"
cpus = 8
memory = "32G"
walltime = "04:00:00"

[cron]
lock_path = {lock_path}
log_dir = {log_dir}
"""


def _quote(value: str | os.PathLike[str]) -> str:
    """TOML basic string。fixture 路径全部来自 `tmp_path`，无引号/反斜杠。"""
    text = os.fspath(value)
    assert '"' not in text and "\\" not in text, f"fixture 路径含需转义字符：{text}"
    return f'"{text}"'


def render_config(
    module: str = MAPPING_BUILDER_MODULE,
    *,
    grid_ids: dict[str, str] | None = None,
    variants: dict[str, str] | None = None,
    reach_count: int | None = None,
) -> str:
    """渲染一份齐备 `config.toml` 文本。

    `module` / `grid_ids` 是判别性参数（见模块头）。`variants` 与 `reach_count` 供
    `prepare` 编排用例改写变体相对路径与 reach 期望值——两者都必须能取非默认值，否则
    "守卫跟着 config 走"与"守卫钉死字面量"两种实现产出完全相同。
    """
    grids = CANONICAL_GRID_IDS if grid_ids is None else grid_ids
    body = (
        _CONFIG_HEAD
        if reach_count is None
        else _CONFIG_HEAD.replace("reach_count = 3988", f"reach_count = {reach_count}")
    )
    tail = _CONFIG_TAIL
    if variants is not None:
        tail = tail.replace(
            'gfs = "input/models/yd_gfs"\nifs = "input/models/yd_ifs"',
            f"gfs = {_quote(variants['gfs'])}\nifs = {_quote(variants['ifs'])}",
        )
    return (
        body
        + f"nwm_mapping_builder_module = {_quote(module)}\n"
        + "\n[nwm_canonical_grid_id]\n"
        + f"gfs = {_quote(grids['gfs'])}\n"
        + f"ifs = {_quote(grids['ifs'])}\n"
        + tail
    )


def write_config(
    tmp_path: Path,
    name: str = "config.toml",
    *,
    module: str = MAPPING_BUILDER_MODULE,
    grid_ids: dict[str, str] | None = None,
    variants: dict[str, str] | None = None,
    reach_count: int | None = None,
) -> Path:
    path = tmp_path / name
    path.write_text(
        render_config(
            module, grid_ids=grid_ids, variants=variants, reach_count=reach_count
        ),
        encoding="utf-8",
    )
    return path


def write_local(
    tmp_path: Path,
    *,
    yd_root: Path | str | None = None,
    checkout_root: Path | str | None = None,
    python: Path | str | None = None,
    name: str = "local.toml",
) -> Path:
    """写一份齐备 `local.toml`；未指定的字段落在 `tmp_path` 下的合成路径。

    路径一律 `resolve()`：macOS 的 `/var` → `/private/var` symlink 会让假解释器记录的
    `pwd` 与未解析的入参不相等，那是 fixture 的噪声而非被测行为。
    """
    root = tmp_path.resolve()
    values = {
        "yd_root": root / "yd" if yd_root is None else yd_root,
        "scratch_root": root / "scratch",
        "shud_binary": root / "bin" / "shud",
        "raw_root": root / "nwm" / "raw",
        "checkout_root": root / "nwm" / "checkout"
        if checkout_root is None
        else checkout_root,
        "python": root / "nwm" / ".venv" / "bin" / "python"
        if python is None
        else python,
        "lock_path": root / "run" / "yd-producer.lock",
        "log_dir": root / "log",
    }
    path = tmp_path / name
    path.write_text(
        _LOCAL_TEMPLATE.format(**{k: _quote(v) for k, v in values.items()}),
        encoding="utf-8",
    )
    return path


# 假解释器：把收到的 argv、cwd、`PYTHONPATH` 写进 JSON 后退出。
# 只用 POSIX sh 内建，不依赖任何 Python——本仓禁止裸 `python`/`python3`，测试同样不例外。
_FAKE_INTERPRETER = """\
#!/bin/sh
{{
  printf '{{"argv": ['
  sep=''
  for a in "$0" "$@"; do
    printf '%s"%s"' "$sep" "$a"
    sep=', '
  done
  printf '], "cwd": "%s", "pythonpath": "%s"}}\\n' "$(pwd)" "$PYTHONPATH"
}} > {output}
exit {code}
"""


def write_fake_interpreter(path: Path, record_to: Path, *, exit_code: int = 0) -> Path:
    """写一个可执行的假解释器脚本，返回其路径。"""
    path.write_text(
        _FAKE_INTERPRETER.format(output=record_to.resolve(), code=exit_code),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path
