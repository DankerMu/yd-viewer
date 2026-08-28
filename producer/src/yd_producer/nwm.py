"""NWM mapping-builder 解释器薄外壳（design.md D6、agent-ops §7.2）。

`prepare` 是全仓唯一主动进入 NWM 活动环境的代码路径。本模块只做两件事：

1. **fail-closed 预检**：`local.toml` 的 `nwm.python` 必须存在、是普通文件、有执行位；
   任何一条不满足即抛 `ConfigError` 并且**不发起任何子进程**；
2. **以该精确路径调用**：命令形如 `[<python>, "-m", <module>, *args]`，module 名取自
   `config.toml` 的 `nwm_mapping_builder_module`（版本化快照事实，非现场值），module
   的解析上下文（cwd 与 `PYTHONPATH` 首段）取自 `local.toml` 的 `nwm.checkout_root`。

**绝无回退**：这里不出现 `uv`、`--active`、`sys.executable`、`shutil.which`、字面
`python`/`python3`。解释器缺失时只能报错停止（agent-ops §7.2：NWM #1831 维护窗口完成
前禁止任何会隐式重建 `.venv` 的命令）。

module 是否可导入不在此校验——那需要活的 NWM 环境，归 prepare 编排的归属 issue。
子进程的非零退出码如实上报（不吞、不重试、不换解释器重来）。
"""

import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from yd_producer.config import Config, ConfigError, LocalConfig

__all__ = ["check_interpreter", "invoke_mapping_builder"]

# `ConfigError.path` 一律指向 `local.toml` 里承载解释器路径的字段，供调用方机检定位。
_INTERPRETER_FIELD = "nwm.python"


def check_interpreter(local: LocalConfig) -> str:
    """校验 NWM 解释器路径可用，返回 `local.toml` 里配置的**原样路径**。

    分类顺序是存在性 → 是否普通文件 → 是否可执行：目录与"存在但没有执行位"是两种不同
    的现场故障，合并成一条会让运维看不出该修哪儿。返回原样字符串而非 `Path.resolve()`
    的结果——spec 要求"以 `local.toml` 指定的精确解释器路径"调用。
    """
    configured = local.nwm.python
    candidate = Path(configured)
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


def invoke_mapping_builder(
    local: LocalConfig,
    config: Config,
    args: Sequence[str] = (),
    runner: Callable[..., Any] = subprocess.run,
) -> subprocess.CompletedProcess[Any]:
    """以 NWM 解释器调用 `config.nwm_mapping_builder_module`。

    `runner` 缺省为 `subprocess.run`，测试可注入记录型 fake 断言 argv/cwd/env 三元组。
    预检不通过时抛 `ConfigError`，**runner 一次也不会被调用**。
    """
    interpreter = check_interpreter(local)
    checkout_root = local.nwm.checkout_root

    env = dict(os.environ)
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        checkout_root if not inherited else checkout_root + os.pathsep + inherited
    )

    command = [interpreter, "-m", config.nwm_mapping_builder_module, *args]
    return runner(command, cwd=checkout_root, env=env)
