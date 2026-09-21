"""prepare 专用的 NWM mapping builder 调用层：为满足 1000 行文件守卫从 `nwm.py` 拆出，
负责解释器/checkout/canonical 根的预检与 `_nwm_prepare_driver.py` 子进程的环境隔离，
公开入口仍是 `yd_producer.nwm` 的再导出。"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from yd_producer.config import ConfigError, LocalConfig

_INTERPRETER_FIELD = "nwm.python"
_CHECKOUT_FIELD = "nwm.checkout_root"
_CANONICAL_FIELD = "nwm.canonical_root"
_DRIVER_ENV_DROPS = ("DATABASE_URL", "PYTHONHOME")

PREPARE_DRIVER_SCRIPT = Path(__file__).with_name("_nwm_prepare_driver.py")


def check_interpreter(local: LocalConfig) -> str:
    configured = local.nwm.python
    candidate = Path(configured)
    if not configured or "/" not in configured:
        raise ConfigError(
            f"NWM 解释器路径必须是含斜杠的绝对路径：{configured}",
            _INTERPRETER_FIELD,
        )
    if not candidate.is_absolute():
        raise ConfigError(
            f"NWM 解释器路径必须是绝对路径：{configured}", _INTERPRETER_FIELD
        )
    if not candidate.exists():
        raise ConfigError(f"NWM 解释器路径不存在：{configured}", _INTERPRETER_FIELD)
    if not candidate.is_file():
        raise ConfigError(
            f"NWM 解释器路径不是普通文件：{configured}", _INTERPRETER_FIELD
        )
    if not os.access(candidate, os.X_OK):
        raise ConfigError(f"NWM 解释器不可执行：{configured}", _INTERPRETER_FIELD)
    return configured


def invoke_mapping_builder(
    local: LocalConfig,
    args: Sequence[str] = (),
    runner: Callable[..., Any] = subprocess.run,
) -> subprocess.CompletedProcess[Any]:
    interpreter = check_interpreter(local)
    checkout_root = local.nwm.checkout_root
    checkout = Path(checkout_root)
    if not checkout_root or not checkout.is_absolute():
        raise ConfigError(
            f"NWM checkout 必须是绝对目录：{checkout_root}", _CHECKOUT_FIELD
        )
    if not checkout.exists():
        raise ConfigError(f"NWM checkout 不存在：{checkout_root}", _CHECKOUT_FIELD)
    if not checkout.is_dir():
        raise ConfigError(f"NWM checkout 不是目录：{checkout_root}", _CHECKOUT_FIELD)
    # grid.json 的权威是 object-store canonical 根（compute-loop §6.1 step 4）；
    # 在任何子进程之前预检，driver 只从显式 `--canonical-root` 取它。
    canonical_root = local.nwm.canonical_root
    canonical = Path(canonical_root)
    if not canonical_root or not canonical.is_absolute():
        raise ConfigError(
            f"NWM canonical root 必须是绝对目录：{canonical_root}", _CANONICAL_FIELD
        )
    if not canonical.exists():
        raise ConfigError(
            f"NWM canonical root 不存在：{canonical_root}", _CANONICAL_FIELD
        )
    if not canonical.is_dir():
        raise ConfigError(
            f"NWM canonical root 不是目录：{canonical_root}", _CANONICAL_FIELD
        )
    if not PREPARE_DRIVER_SCRIPT.is_file():
        raise ConfigError(
            f"prepare driver 脚本不存在：{PREPARE_DRIVER_SCRIPT}",
            _INTERPRETER_FIELD,
        )
    env = dict(os.environ)
    for name in _DRIVER_ENV_DROPS:
        env.pop(name, None)
    env["PYTHONPATH"] = checkout_root
    return runner(
        [interpreter, str(PREPARE_DRIVER_SCRIPT), *args],
        cwd=checkout_root,
        env=env,
        capture_output=True,
        text=True,
    )
