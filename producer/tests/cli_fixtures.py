"""`test_cli.py` / `test_nwm.py` 共用的 TOML fixture。

与 `test_config.py` 的 fixture **刻意不共用**：那份是配置装载器的账本之一（点分闭包由
round-trip 用例逐值承重），这里只需要"一份能过装载器的齐备配置"以便把测试焦点放在入口
层与薄外壳。两份各自独立转录同一张 schema。

TOML 逐字写出而非由 dict 渲染：入口层测试不校验 schema，写死文本更易读，且新增必需字段
时这里会直接红（`ConfigError`），不会静默漂移。
"""

import os
from pathlib import Path

# `nwm_mapping_builder_module` 是 issue #3 随 #32 三步落地的必需键；缺它则装载即 fail closed
CONFIG_TOML = """\
forecast_days = 7
output_interval_minutes = 60
checkpoint_hours = [12]
reach_count = 3988
nwm_mapping_builder_module = "workers.mapping_builder.cli"

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

MAPPING_BUILDER_MODULE = "workers.mapping_builder.cli"

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


def write_config(tmp_path: Path, name: str = "config.toml") -> Path:
    path = tmp_path / name
    path.write_text(CONFIG_TOML, encoding="utf-8")
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
