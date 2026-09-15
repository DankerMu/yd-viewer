"""NWM mapping-builder 解释器薄外壳（design.md D4/D6、agent-ops §7.2）。

`prepare` 是全仓唯一主动进入 NWM 活动环境的代码路径。本模块只做两件事：

1. **fail-closed 预检**：`local.toml` 的 `nwm.python` 必须存在、是普通文件、有执行位；
   `nwm.checkout_root` 必须是绝对目录。任何一条不满足即抛 `ConfigError` 并且**不发起
   任何子进程**；
2. **以该精确路径调用**：命令形如 `[<python>, <driver 绝对脚本>, *args]`，脚本是 yd
   随包分发的 `_nwm_prepare_driver.py`。cwd 与唯一的 `PYTHONPATH` 取自
   `local.toml` 的 `nwm.checkout_root`。

**绝无回退**：这里不出现 `uv`、`--active`、`sys.executable`、`shutil.which`、字面
`python`/`python3`，也不 `Path.resolve()` venv symlink、不做 PATH fallback。解释器
缺失时只能报错停止（agent-ops §7.2：NWM #1831 维护窗口完成前禁止任何会隐式重建
`.venv` 的命令）。

子进程的非零退出码如实上报（不吞、不重试、不换解释器重来）。child 环境只覆盖
`PYTHONPATH` 为明确 checkout，并去掉 `DATABASE_URL`/`PYTHONHOME` 与继承的
`PYTHONPATH`。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from yd_producer.config import ConfigError, LocalConfig

__all__ = ["PREPARE_DRIVER_SCRIPT", "check_interpreter", "invoke_mapping_builder"]

# `ConfigError.path` 一律指向 `local.toml` 里承载解释器路径的字段，供调用方机检定位。
_INTERPRETER_FIELD = "nwm.python"
_CHECKOUT_FIELD = "nwm.checkout_root"
_DRIVER_ENV_DROPS = ("DATABASE_URL", "PYTHONHOME")

PREPARE_DRIVER_SCRIPT = Path(__file__).with_name("_nwm_prepare_driver.py")


def check_interpreter(local: LocalConfig) -> str:
    """校验 NWM 解释器路径可用，返回 `local.toml` 里配置的**原样路径**。

    分类顺序是存在性 → 是否普通文件 → 是否可执行：目录与"存在但没有执行位"是两种不同
    的现场故障，合并成一条会让运维看不出该修哪儿。返回原样字符串而非 `Path.resolve()`
    的结果——spec 要求"以 `local.toml` 指定的精确解释器路径"调用。相对路径与无斜杠
    取值在存在性检查之前拒绝，不得落到 runner。
    """
    configured = local.nwm.python
    if not configured or "/" not in configured:
        raise ConfigError(
            f"NWM 解释器路径必须是含斜杠的绝对路径：{configured}；"
            "yd 不安装、不升级、不修复 NWM .venv（agent-ops §7.2），"
            "不回退到 PATH 或任何其它解释器",
            _INTERPRETER_FIELD,
        )
    candidate = Path(configured)
    if not candidate.is_absolute():
        raise ConfigError(
            f"NWM 解释器路径必须是绝对路径：{configured}；"
            "yd 不安装、不升级、不修复 NWM .venv（agent-ops §7.2），"
            "不回退到 PATH 或任何其它解释器",
            _INTERPRETER_FIELD,
        )
    if not candidate.exists():
        raise ConfigError(
            f"NWM 解释器路径不存在：{configured}；"
            "yd 不安装、不升级、不修复 NWM .venv（agent-ops §7.2），"
            "不回退到任何其它解释器",
            _INTERPRETER_FIELD,
        )
    if not candidate.is_file():
        raise ConfigError(
            f"NWM 解释器路径不是普通文件：{configured}",
            _INTERPRETER_FIELD,
        )
    if not os.access(candidate, os.X_OK):
        raise ConfigError(
            f"NWM 解释器不可执行：{configured}",
            _INTERPRETER_FIELD,
        )
    return configured


def check_checkout(local: LocalConfig) -> str:
    """校验 NWM checkout 是明确的绝对目录，返回配置的原样路径。"""
    configured = local.nwm.checkout_root
    if not configured or not Path(configured).is_absolute():
        raise ConfigError(
            f"NWM checkout 必须是绝对目录：{configured}",
            _CHECKOUT_FIELD,
        )
    candidate = Path(configured)
    if not candidate.exists():
        raise ConfigError(
            f"NWM checkout 不存在：{configured}",
            _CHECKOUT_FIELD,
        )
    if not candidate.is_dir():
        raise ConfigError(
            f"NWM checkout 不是目录：{configured}",
            _CHECKOUT_FIELD,
        )
    return configured


def invoke_mapping_builder(
    local: LocalConfig,
    args: Sequence[str] = (),
    runner: Callable[..., Any] = subprocess.run,
) -> subprocess.CompletedProcess[Any]:
    """以 NWM 解释器调用随包 prepare driver 的绝对脚本路径。

    `runner` 缺省为 `subprocess.run`，测试可注入记录型 fake 断言 argv/cwd/env 三元组。
    预检不通过时抛 `ConfigError`，**runner 一次也不会被调用**。
    """
    interpreter = check_interpreter(local)
    checkout_root = check_checkout(local)
    script = PREPARE_DRIVER_SCRIPT
    if not script.is_file():
        raise ConfigError(
            f"prepare driver 脚本不存在：{script}",
            _INTERPRETER_FIELD,
        )

    env = dict(os.environ)
    for name in _DRIVER_ENV_DROPS:
        env.pop(name, None)
    env["PYTHONPATH"] = checkout_root

    command = [interpreter, str(script), *args]
    return runner(
        command,
        cwd=checkout_root,
        env=env,
        capture_output=True,
        text=True,
    )
