"""Claim-aware zero-write rawcopy admission regressions."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_rawcopy import CYCLE, build_tree, make_config

from yd_producer import rawcopy as rawcopy_module
from yd_producer._work_claim import claim_exact_work
from yd_producer.config import ConfigError
from yd_producer.rawcopy import RawStagingError, stage_raw
from yd_producer.rawscan import judge


def _claim(tmp_path: Path, *, name: str):
    return claim_exact_work(
        work_root=tmp_path / "claimed-scratch" / "work",
        source="ifs",
        cycle=CYCLE,
        cycle_name=name,
    )


def test_claimed_physical_raw_work_containment_releases_empty_exact_root(
    tmp_path: Path,
) -> None:
    raw_root, _unused_work_dir = build_tree(tmp_path)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    claim = _claim(tmp_path, name="containment")

    with pytest.raises(ConfigError):
        stage_raw(
            verdict,
            raw_root,
            raw_root / "would-write-inside-raw",
            "gfs",
            CYCLE,
            config,
            claim=claim,
        )

    assert not claim.work_dir.exists()
    assert claim.work_dir.parent.is_dir()
    assert claim.work_root.is_dir()


@pytest.mark.parametrize("exit_kind", ["fallback", "keyboard", "system-exit"])
def test_claimed_final_admission_exit_releases_empty_exact_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_kind: str
) -> None:
    raw_root, _unused_work_dir = build_tree(tmp_path)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    claim = _claim(tmp_path, name=f"{exit_kind}-claim")
    touched = {"called": False}

    if exit_kind == "fallback":

        def fallback(*, source, cycle, leads, entries, cycle_root):
            touched["called"] = True
            raise RuntimeError("injected admission fallback")

        monkeypatch.setattr(rawcopy_module, "_render_manifest", fallback)
        expected = RawStagingError
    else:
        signal = KeyboardInterrupt("injected keyboard interrupt")
        if exit_kind == "system-exit":
            signal = SystemExit("injected system exit")

        def interrupt(*, source, cycle, leads, entries, cycle_root):
            touched["called"] = True
            raise signal

        monkeypatch.setattr(rawcopy_module, "_render_manifest", interrupt)
        expected = type(signal)

    with pytest.raises(expected) as info:
        stage_raw(
            verdict,
            raw_root,
            claim.work_dir,
            "gfs",
            CYCLE,
            config,
            claim=claim,
        )

    assert touched["called"] is True
    if exit_kind == "fallback":
        assert info.value.kind == "source-manifest"
        assert isinstance(info.value.__cause__, RuntimeError)
        assert info.value.__suppress_context__ is True
    else:
        assert info.value is signal
    assert not claim.work_dir.exists()
    assert claim.work_dir.parent.is_dir()
    assert claim.work_root.is_dir()
