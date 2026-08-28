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
    ALT_MAPPING_BUILDER_MODULE,
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


def _load(tmp_path, module=MAPPING_BUILDER_MODULE, **local_kwargs):
    config = load_config(write_config(tmp_path, module=module))
    local = load_local(write_local(tmp_path, **local_kwargs), config)
    return local, config


# --- fail-closed 三态 --------------------------------------------------------


def test_missing_interpreter_raises_and_starts_no_process(tmp_path):
    local, config = _load(tmp_path, python=tmp_path.resolve() / "absent" / "python")
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as excinfo:
        invoke_mapping_builder(local, config, ["--package-path", "x"], runner)

    assert excinfo.value.path == "nwm.python"
    # 三态各断言**本态独有**的措辞：只断言 `path` 的话，三条消息互换后用例照样绿，
    # 而运维看到的补救方向就指错了（agent-ops §7.2 禁止他们重建 .venv）。
    assert "不存在" in str(excinfo.value)
    assert runner.calls == []


def test_directory_interpreter_raises_and_starts_no_process(tmp_path):
    directory = tmp_path.resolve() / "not-a-file"
    directory.mkdir()
    local, config = _load(tmp_path, python=directory)
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as excinfo:
        invoke_mapping_builder(local, config, [], runner)

    assert excinfo.value.path == "nwm.python"
    assert "不是普通文件" in str(excinfo.value)
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
    assert "不可执行" in str(excinfo.value)
    assert runner.calls == []


# --- 真子进程：调用形态 ------------------------------------------------------


def _run_fake(
    tmp_path,
    checkout_name="checkout",
    exit_code=0,
    args=("--dry-run",),
    module=MAPPING_BUILDER_MODULE,
):
    checkout = tmp_path.resolve() / checkout_name
    checkout.mkdir()
    record = tmp_path.resolve() / f"record-{checkout_name}.json"
    script = write_fake_interpreter(
        tmp_path.resolve() / f"fake-python-{checkout_name}",
        record,
        exit_code=exit_code,
    )
    local, config = _load(
        tmp_path, module=module, checkout_root=checkout, python=script
    )
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


def test_module_name_follows_config_value(tmp_path):
    """`-m` 后的 module 名取自 `config.toml`，而非代码里的常量。

    与 `test_fake_interpreter_receives_exact_command_and_context` 不重复：那条按 fixture
    钉死默认值，期望值与"把 `workers.mapping_builder.cli` 写死回 `nwm.py`"的实现产出完全
    相同（实测该变异体在 204 条下全部存活）。判别力只能来自**第二个不同的值**，形态照搬
    上面 checkout_root 的两值对照。
    """
    assert MAPPING_BUILDER_MODULE != ALT_MAPPING_BUILDER_MODULE
    _, _, first, _, _ = _run_fake(tmp_path, checkout_name="module-a")
    _, _, second, _, _ = _run_fake(
        tmp_path, checkout_name="module-b", module=ALT_MAPPING_BUILDER_MODULE
    )

    assert first["argv"][1:3] == ["-m", MAPPING_BUILDER_MODULE]
    assert second["argv"][1:3] == ["-m", ALT_MAPPING_BUILDER_MODULE]


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


def test_symlinked_interpreter_is_invoked_verbatim_not_resolved(tmp_path):
    """`nwm.python` 是 symlink 时，被调用的必须是 symlink 本身，而非其解析目标。

    生产里 `nwm.python` 就是 `<checkout>/.venv/bin/python`——一个指向仓外真身的 symlink，
    而 venv 激活取决于 `pyvenv.cfg` 与**被调用**的那个二进制同目录；调用 `resolve()` 后
    的目标会丢掉 NWM 的 site-packages，等价于 agent-ops §7.2 明禁的"回退到系统 Python"。

    判别力全在"symlink 名与目标名不同"这一条：上面几条用例的 `argv[0]` 期望值都经
    `endswith(str(script))` / `Path(...).name == script.name` 行使，在 fixture 里
    `resolve()` 恰是恒等变换，故把 `check_interpreter` 的 `return configured` 换成
    `return str(candidate.resolve())` 时全套仍全绿。这里让两个名字不同，只有原样返回
    才对。
    """
    checkout = tmp_path.resolve() / "checkout"
    checkout.mkdir()
    record = tmp_path.resolve() / "record-symlink.json"
    target = write_fake_interpreter(tmp_path.resolve() / "real-python-target", record)
    venv_bin = tmp_path.resolve() / "nwm-venv" / "bin"
    venv_bin.mkdir(parents=True)
    link = venv_bin / "python"
    link.symlink_to(target)
    assert link.name != target.name
    assert link.resolve() == target

    local, config = _load(tmp_path, checkout_root=checkout, python=link)
    completed = invoke_mapping_builder(local, config, [], CountingRunner())

    assert completed.returncode == 0
    recorded = json.loads(record.read_text(encoding="utf-8"))
    assert recorded["argv"][0] == str(link)
    assert recorded["argv"][0] != str(target)
