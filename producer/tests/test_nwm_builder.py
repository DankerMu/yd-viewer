import json
import subprocess
import sys
from pathlib import Path

import pytest
from cli_fixtures import write_config, write_fake_interpreter, write_local

from yd_producer.config import ConfigError, load_config, load_local
from yd_producer.nwm import PREPARE_DRIVER_SCRIPT, invoke_mapping_builder


class RecordingRunner:
    def __init__(self, delegate=None):
        self.calls: list[tuple[list[str], dict]] = []
        self._delegate = delegate

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
        if self._delegate is not None:
            return self._delegate(
                command, cwd=cwd, env=env, capture_output=capture_output, text=text
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def _load(tmp_path, **local_kwargs):
    """写齐备 TOML 并装载；未显式给 canonical 根时建出默认根（预检要求真实目录）。"""
    config = load_config(write_config(tmp_path))
    local = load_local(write_local(tmp_path, **local_kwargs), config)
    if "canonical_root" not in local_kwargs:
        Path(local.nwm.canonical_root).mkdir(parents=True, exist_ok=True)
    return local, config


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("missing-interpreter", "nwm.python"),
        ("directory-interpreter", "nwm.python"),
        ("non-executable-interpreter", "nwm.python"),
        ("relative-interpreter", "nwm.python"),
        ("slashless-interpreter", "nwm.python"),
        ("missing-checkout", "nwm.checkout_root"),
        ("file-checkout", "nwm.checkout_root"),
        ("missing-canonical", "nwm.canonical_root"),
        ("file-canonical", "nwm.canonical_root"),
        ("relative-canonical", "nwm.canonical_root"),
    ],
)
def test_invalid_prepare_runtime_starts_no_process(tmp_path, kind, field):
    interpreter = tmp_path.resolve() / "interpreter"
    checkout = tmp_path.resolve() / "checkout"
    # canonical 根默认合法：解释器/checkout 三类用例必须仍然点名自己的字段。
    canonical = tmp_path.resolve() / "canonical"
    canonical.mkdir()
    if kind.endswith("-canonical"):
        interpreter = write_fake_interpreter(
            tmp_path.resolve() / "fake-python", tmp_path.resolve() / "record.json"
        )
        checkout.mkdir()
        if kind == "missing-canonical":
            canonical = tmp_path.resolve() / "canonical-absent"
        elif kind == "file-canonical":
            canonical = tmp_path.resolve() / "canonical-file"
            canonical.write_text("not a directory\n", encoding="utf-8")
        else:
            canonical = Path("nwm/object-store/canonical")
    elif kind == "missing-interpreter":
        interpreter /= "absent"
        checkout.mkdir()
    elif kind == "directory-interpreter":
        interpreter.mkdir()
        checkout.mkdir()
    elif kind == "non-executable-interpreter":
        interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        interpreter.chmod(0o644)
        checkout.mkdir()
    elif kind == "relative-interpreter":
        interpreter = Path("nwm/.venv/bin/python")
        checkout.mkdir()
    elif kind == "slashless-interpreter":
        interpreter = Path("python")
        checkout.mkdir()
    else:
        interpreter = write_fake_interpreter(
            tmp_path.resolve() / "fake-python", tmp_path.resolve() / "record.json"
        )
        if kind == "file-checkout":
            checkout.write_text("not a directory\n", encoding="utf-8")
    local, _config = _load(
        tmp_path,
        python=interpreter,
        checkout_root=checkout,
        canonical_root=canonical,
    )
    runner = RecordingRunner()
    with pytest.raises(ConfigError) as caught:
        invoke_mapping_builder(local, [], runner)
    assert caught.value.path == field
    assert runner.calls == []


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
    runner = RecordingRunner(subprocess.run)
    completed = invoke_mapping_builder(local, list(args), runner)
    return (
        script,
        checkout,
        json.loads(record.read_text(encoding="utf-8")),
        completed,
        runner,
    )


def test_mapping_builder_uses_only_configured_environment(tmp_path, monkeypatch):
    script, checkout, recorded, completed, _ = _run_fake(
        tmp_path, args=("--source", "gfs", "--grid-id", "fixture-grid-gfs")
    )
    assert completed.returncode == 0
    assert recorded["argv"] == [
        str(script),
        str(PREPARE_DRIVER_SCRIPT),
        "--source",
        "gfs",
        "--grid-id",
        "fixture-grid-gfs",
    ]
    assert recorded["cwd"] == recorded["pythonpath"] == str(checkout)
    assert "uv" not in " ".join(recorded["argv"])
    assert "--active" not in recorded["argv"]
    assert recorded["argv"][0] != sys.executable
    _, first_checkout, first, _, _ = _run_fake(tmp_path, checkout_name="checkout-a")
    _, second_checkout, second, _, _ = _run_fake(tmp_path, checkout_name="checkout-b")
    assert first_checkout != second_checkout
    assert first["cwd"] == first["pythonpath"] == str(first_checkout)
    assert second["cwd"] == second["pythonpath"] == str(second_checkout)
    _, _, _, failed, runner = _run_fake(tmp_path, checkout_name="nonzero", exit_code=7)
    assert failed.returncode == 7 and len(runner.calls) == 1
    monkeypatch.setenv("PYTHONPATH", "/inherited/path")
    monkeypatch.setenv("DATABASE_URL", "postgres://example")
    monkeypatch.setenv("PYTHONHOME", "/poison/home")
    isolated = tmp_path.resolve() / "isolated-checkout"
    isolated.mkdir()
    local, _config = _load(
        tmp_path,
        checkout_root=isolated,
        python=write_fake_interpreter(
            tmp_path.resolve() / "isolated-python", tmp_path.resolve() / "isolated.json"
        ),
    )
    runner = RecordingRunner()
    invoke_mapping_builder(local, ["--source", "ifs"], runner)
    kwargs = runner.calls[0][1]
    assert kwargs["capture_output"] is True and kwargs["text"] is True
    assert kwargs["env"]["PYTHONPATH"] == str(isolated)
    assert "DATABASE_URL" not in kwargs["env"] and "PYTHONHOME" not in kwargs["env"]


def test_symlinked_interpreter_is_invoked_verbatim_not_resolved(tmp_path):
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
    completed = invoke_mapping_builder(local, [], RecordingRunner(subprocess.run))
    assert completed.returncode == 0
    recorded = json.loads(record.read_text(encoding="utf-8"))
    assert recorded["argv"][0] == str(link)
    assert recorded["argv"][0] != str(target)
    assert recorded["argv"][1] == str(PREPARE_DRIVER_SCRIPT)
