"""`yd_producer.nwm` 解释器薄外壳测试（design.md seam 7、D6、agent-ops §7.2）。

fail-closed 三态一律断言**两件事**：抛 `ConfigError`（含机检用的 `path`）**且注入 runner
的调用次数为 0**——只断言抛异常的话，"先起子进程再报错"的实现照样绿。

调用形态用真解释器脚本走真子进程验证：`#!/bin/sh` 假解释器把 argv/cwd/`PYTHONPATH` 写进
JSON，测试读回逐项断言。记录型 fake 测不出 `cwd=`/`env=` 是否真的作用于子进程。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from cli_fixtures import write_config, write_fake_interpreter, write_local

from yd_producer.config import ConfigError, load_config, load_local
from yd_producer.nwm import PREPARE_DRIVER_SCRIPT, invoke_mapping_builder


class RecordingRunner:
    """记录型 runner：只计数与留存入参，绝不起子进程。"""

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, *, cwd, env, capture_output, text):
        self.calls.append(
            (
                list(command),
                {
                    "cwd": cwd,
                    "env": env,
                    "capture_output": capture_output,
                    "text": text,
                },
            )
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class CountingRunner:
    """真调用 `subprocess.run`，只额外计数——用于"非零退出如实上报"的判别。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, command, *, cwd, env, capture_output, text):
        self.calls += 1
        return subprocess.run(
            command,
            check=False,
            cwd=cwd,
            env=env,
            capture_output=capture_output,
            text=text,
        )


def _load(tmp_path, **local_kwargs):
    config = load_config(write_config(tmp_path))
    local = load_local(write_local(tmp_path, **local_kwargs), config)
    return local, config


# --- fail-closed 三态 --------------------------------------------------------


def test_missing_interpreter_raises_and_starts_no_process(tmp_path):
    local, _config = _load(tmp_path, python=tmp_path.resolve() / "absent" / "python")
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as excinfo:
        invoke_mapping_builder(local, ["--source", "gfs"], runner)

    assert excinfo.value.path == "nwm.python"
    assert "不存在" in str(excinfo.value)
    assert runner.calls == []


def test_directory_interpreter_raises_and_starts_no_process(tmp_path):
    directory = tmp_path.resolve() / "not-a-file"
    directory.mkdir()
    local, _config = _load(tmp_path, python=directory)
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as cop:
        invoke_mapping_builder(local, [], runner)

    assert cop.value.path == "nwm.python"
    assert "不是普通文件" in str(cop.value)
    assert runner.calls == []


def test_non_executable_interpreter_raises_and_starts_no_process(tmp_path):
    script = tmp_path.resolve() / "python-no-x"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o644)
    local, _config = _load(tmp_path, python=script)
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as cop:
        invoke_mapping_builder(local, [], runner)

    assert cop.value.path == "nwm.python"
    assert "不可执行" in str(cop.value)
    assert runner.calls == []


def test_relative_interpreter_never_reaches_runner(tmp_path):
    local, _config = _load(tmp_path, python="nwm/.venv/bin/python")
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as cop:
        invoke_mapping_builder(local, [], runner)

    assert cop.value.path == "nwm.python"
    assert "绝对路径" in str(cop.value)
    assert runner.calls == []


def test_slashless_interpreter_never_reaches_runner(tmp_path):
    local, _config = _load(tmp_path, python="python")
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as cop:
        invoke_mapping_builder(local, [], runner)

    assert cop.value.path == "nwm.python"
    assert "斜杠" in str(cop.value)
    assert runner.calls == []


def test_missing_checkout_raises_and_starts_no_process(tmp_path):
    script = write_fake_interpreter(
        tmp_path.resolve() / "fake-python", tmp_path.resolve() / "record.json"
    )
    local, _config = _load(
        tmp_path,
        python=script,
        checkout_root=tmp_path.resolve() / "absent-checkout",
    )
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as cop:
        invoke_mapping_builder(local, [], runner)

    assert cop.value.path == "nwm.checkout_root"
    assert "不存在" in str(cop.value)
    assert runner.calls == []


def test_non_directory_checkout_raises_and_starts_no_process(tmp_path):
    script = write_fake_interpreter(
        tmp_path.resolve() / "fake-python", tmp_path.resolve() / "record.json"
    )
    checkout = tmp_path.resolve() / "checkout-file"
    checkout.write_text("not a directory\n", encoding="utf-8")
    local, _config = _load(tmp_path, python=script, checkout_root=checkout)
    runner = RecordingRunner()

    with pytest.raises(ConfigError) as cop:
        invoke_mapping_builder(local, [], runner)

    assert cop.value.path == "nwm.checkout_root"
    assert "不是目录" in str(cop.value)
    assert runner.calls == []


# --- 真子进程：调用形态 ------------------------------------------------------


def _run_fake(
    tmp_path,
    checkout_name="checkout",
    exit_code=0,
    args=("--source", "gfs"),
):
    checkout = tmp_path.resolve() / checkout_name
    checkout.mkdir()
    record = tmp_path.resolve() / f"record-{checkout_name}.json"
    script = write_fake_interpreter(
        tmp_path.resolve() / f"fake-python-{checkout_name}",
        record,
        exit_code=exit_code,
    )
    local, _config = _load(tmp_path, checkout_root=checkout, python=script)
    runner = CountingRunner()
    completed = invoke_mapping_builder(local, list(args), runner)
    return (
        script,
        checkout,
        json.loads(record.read_text(encoding="utf-8")),
        completed,
        runner,
    )


def test_fake_interpreter_receives_exact_command_and_context(tmp_path):
    script, checkout, recorded, completed, _ = _run_fake(
        tmp_path, args=("--source", "gfs", "--grid-id", "fixture-grid-gfs")
    )

    assert completed.returncode == 0
    assert recorded["argv"][0].endswith(str(script))
    assert recorded["argv"][1] == str(PREPARE_DRIVER_SCRIPT)
    assert recorded["argv"][2:] == ["--source", "gfs", "--grid-id", "fixture-grid-gfs"]
    assert recorded["cwd"] == str(checkout)
    assert recorded["pythonpath"] == str(checkout)


def test_checkout_root_change_moves_cwd_and_pythonpath(tmp_path):
    """cwd/`PYTHONPATH` 取自 `local.toml` 的 checkout 字段，而非任何常量。"""
    _, first_checkout, first, _, _ = _run_fake(tmp_path, checkout_name="checkout-a")
    _, second_checkout, second, _, _ = _run_fake(tmp_path, checkout_name="checkout-b")

    assert first_checkout != second_checkout
    assert first["cwd"] == str(first_checkout)
    assert second["cwd"] == str(second_checkout)
    assert first["pythonpath"] == str(first_checkout)
    assert second["pythonpath"] == str(second_checkout)


def test_command_contains_no_interpreter_fallback(tmp_path):
    """回退禁令的负面证据（agent-ops §7.2）：命令里只有 `local.nwm.python` 一个解释器。"""
    script, _, recorded, _, _ = _run_fake(tmp_path)
    joined = " ".join(recorded["argv"])

    assert "uv" not in joined
    assert "--active" not in joined
    assert recorded["argv"][0] != sys.executable
    assert Path(recorded["argv"][0]).name == script.name
    assert "-m" not in recorded["argv"]


def test_nonzero_exit_reported_faithfully(tmp_path):
    """非零退出如实上报：不吞、不重试、不换解释器——runner 恰好被调用一次。"""
    _, _, _, completed, runner = _run_fake(tmp_path, exit_code=7)

    assert completed.returncode == 7
    assert runner.calls == 1


def test_child_environment_overrides_pythonpath_and_drops_inherited_secrets(
    tmp_path, monkeypatch
):
    """child 只有明确 checkout 的 PYTHONPATH，且去掉 DATABASE_URL/PYTHONHOME。"""
    monkeypatch.setenv("PYTHONPATH", "/inherited/path")
    monkeypatch.setenv("DATABASE_URL", "postgres://example")
    monkeypatch.setenv("PYTHONHOME", "/poison/home")
    runner = RecordingRunner()
    checkout = tmp_path.resolve() / "checkout"
    checkout.mkdir()
    script = write_fake_interpreter(
        tmp_path.resolve() / "fake-python", tmp_path.resolve() / "record.json"
    )
    local, _config = _load(tmp_path, checkout_root=checkout, python=script)

    invoke_mapping_builder(local, ["--source", "ifs"], runner)

    assert len(runner.calls) == 1
    kwargs = runner.calls[0][1]
    env = kwargs["env"]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert env["PYTHONPATH"] == str(checkout)
    assert "DATABASE_URL" not in env
    assert "PYTHONHOME" not in env


def test_symlinked_interpreter_is_invoked_verbatim_not_resolved(tmp_path):
    """`nwm.python` 是 symlink 时，被调用的必须是 symlink 本身，而非其解析目标。

    生产里 `nwm.python` 就是 `<checkout>/.venv/bin/python`——一个指向仓外真身的 symlink，
    而 venv 激活取决于 `pyvenv.cfg` 与**被调用**的那个二进制同目录；调用 `resolve()` 后
    的目标会丢掉 NWM 的 site-packages，等价于 agent-ops §7.2 明禁的"回退到系统 Python"。
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

    local, _config = _load(tmp_path, checkout_root=checkout, python=link)
    completed = invoke_mapping_builder(local, [], CountingRunner())

    assert completed.returncode == 0
    recorded = json.loads(record.read_text(encoding="utf-8"))
    assert recorded["argv"][0] == str(link)
    assert recorded["argv"][0] != str(target)
    assert recorded["argv"][1] == str(PREPARE_DRIVER_SCRIPT)
