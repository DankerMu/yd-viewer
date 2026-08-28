"""`yd_producer.nwm` 解释器薄外壳测试（design.md seam 7、D6、agent-ops §7.2）。

fail-closed 三态一律断言**两件事**：抛 `ConfigError`（含机检用的 `path`）**且注入 runner
的调用次数为 0**——只断言抛异常的话，"先起子进程再报错"的实现照样绿。

调用形态用真解释器脚本走真子进程验证：`#!/bin/sh` 假解释器把 argv/cwd/`PYTHONPATH` 写进
JSON，测试读回逐项断言。记录型 fake 测不出 `cwd=`/`env=` 是否真的作用于子进程。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cli_fixtures import (
    MAPPING_BUILDER_MODULE,
    write_config,
    write_fake_interpreter,
    write_local,
)

from yd_producer.config import ConfigError, load_config, load_local
from yd_producer.nwm import invoke_mapping_builder


class RecordingRunner:
    """记录型 runner：只计数与留存入参，绝不起子进程。"""

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        return subprocess.CompletedProcess(command, 0)


class CountingRunner:
    """真调用 `subprocess.run`，只额外计数——用于"非零退出如实上报"的判别。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, command, **kwargs):
        self.calls += 1
        return subprocess.run(command, check=False, **kwargs)


def _load(tmp_path, **local_kwargs):
    config = load_config(write_config(tmp_path))
    local = load_local(write_local(tmp_path, **local_kwargs), config)
    return local, config


# --- fail-closed 三态 --------------------------------------------------------


def test_missing_interpreter_raises_and_starts_no_process(tmp_path):
    local, config = _load(tmp_path, python=tmp_path.resolve() / "absent" / "python")
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as excinfo:
        invoke_mapping_builder(local, config, ["--package-path", "x"], runner)

    assert excinfo.value.path == "nwm.python"
    assert runner.calls == []


def test_directory_interpreter_raises_and_starts_no_process(tmp_path):
    directory = tmp_path.resolve() / "not-a-file"
    directory.mkdir()
    local, config = _load(tmp_path, python=directory)
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as excinfo:
        invoke_mapping_builder(local, config, [], runner)

    assert excinfo.value.path == "nwm.python"
    assert runner.calls == []


def test_non_executable_interpreter_raises_and_starts_no_process(tmp_path):
    script = tmp_path.resolve() / "python-no-x"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o644)
    local, config = _load(tmp_path, python=script)
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as excinfo:
        invoke_mapping_builder(local, config, [], runner)

    assert excinfo.value.path == "nwm.python"
    assert runner.calls == []


# --- 真子进程：调用形态 ------------------------------------------------------


def _run_fake(tmp_path, checkout_name="checkout", exit_code=0, args=("--dry-run",)):
    checkout = tmp_path.resolve() / checkout_name
    checkout.mkdir()
    record = tmp_path.resolve() / f"record-{checkout_name}.json"
    script = write_fake_interpreter(
        tmp_path.resolve() / f"fake-python-{checkout_name}",
        record,
        exit_code=exit_code,
    )
    local, config = _load(tmp_path, checkout_root=checkout, python=script)
    runner = CountingRunner()
    completed = invoke_mapping_builder(local, config, list(args), runner)
    return (
        script,
        checkout,
        json.loads(record.read_text(encoding="utf-8")),
        completed,
        runner,
    )


def test_fake_interpreter_receives_exact_command_and_context(tmp_path):
    script, checkout, recorded, completed, _ = _run_fake(
        tmp_path, args=("--package-path", "baseline")
    )

    assert completed.returncode == 0
    assert recorded["argv"][0].endswith(str(script))
    assert recorded["argv"][1:3] == ["-m", MAPPING_BUILDER_MODULE]
    assert recorded["argv"][3:] == ["--package-path", "baseline"]
    assert recorded["cwd"] == str(checkout)
    assert recorded["pythonpath"].split(os.pathsep)[0] == str(checkout)


def test_checkout_root_change_moves_cwd_and_pythonpath(tmp_path):
    """cwd/`PYTHONPATH` 取自 `local.toml` 的 checkout 字段，而非任何常量。"""
    _, first_checkout, first, _, _ = _run_fake(tmp_path, checkout_name="checkout-a")
    _, second_checkout, second, _, _ = _run_fake(tmp_path, checkout_name="checkout-b")

    assert first_checkout != second_checkout
    assert first["cwd"] == str(first_checkout)
    assert second["cwd"] == str(second_checkout)
    assert first["pythonpath"].split(os.pathsep)[0] == str(first_checkout)
    assert second["pythonpath"].split(os.pathsep)[0] == str(second_checkout)


def test_command_contains_no_interpreter_fallback(tmp_path):
    """回退禁令的负面证据（agent-ops §7.2）：命令里只有 `local.nwm.python` 一个解释器。"""
    script, _, recorded, _, _ = _run_fake(tmp_path)
    joined = " ".join(recorded["argv"])

    assert "uv" not in joined
    assert "--active" not in joined
    assert recorded["argv"][0] != sys.executable
    assert Path(recorded["argv"][0]).name == script.name


def test_nonzero_exit_reported_faithfully(tmp_path):
    """非零退出如实上报：不吞、不重试、不换解释器——runner 恰好被调用一次。"""
    _, _, _, completed, runner = _run_fake(tmp_path, exit_code=7)

    assert completed.returncode == 7
    assert runner.calls == 1


def test_pythonpath_prepends_checkout_without_dropping_inherited(tmp_path, monkeypatch):
    """继承的 `PYTHONPATH` 保留在后段，checkout 仍是首段。"""
    monkeypatch.setenv("PYTHONPATH", "/inherited/path")
    _, checkout, recorded, _, _ = _run_fake(tmp_path)

    assert recorded["pythonpath"].split(os.pathsep) == [
        str(checkout),
        "/inherited/path",
    ]
