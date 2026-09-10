"""Prepare top-level calibrated-state cardinality (#97)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from prepare_fixtures import (
    RenameProbe,
    VariantScript,
    assert_untouched,
    make_builder,
    make_env,
    run,
    tree_snapshot,
)

from yd_producer import prepare as prepare_module
from yd_producer.controller import STATE_SUFFIX
from yd_producer.init import _locate_calibration_state
from yd_producer.prepare import VARIANT_CALIBRATED_STATE_NAME, PrepareError
from yd_producer.store import safe_fs

BACKUP = f"backup{STATE_SUFFIX}"
ALIAS = f"alias{STATE_SUFFIX}"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


def _probe(monkeypatch) -> RenameProbe:
    probe = RenameProbe(delegate=safe_fs.rename_entry_no_follow)
    monkeypatch.setattr(prepare_module.safe_fs, "rename_entry_no_follow", probe)
    return probe


def _plant(env) -> dict:
    (env.yd_root / "operator-keep").write_bytes(b"preexisting-root\n")
    return tree_snapshot(env.yd_root)


def _watch_yd_ensure(env, monkeypatch) -> list[Path]:
    recorded: list[Path] = []
    real = prepare_module._ensure_directory

    def watching(path, created_entries, *, lower_bound=None):
        candidate = Path(path)
        try:
            candidate.relative_to(env.yd_root)
        except ValueError:
            pass
        else:
            recorded.append(candidate)
        return real(path, created_entries, lower_bound=lower_bound)

    monkeypatch.setattr(prepare_module, "_ensure_directory", watching)
    return recorded


def _no_publish(env, before, probe, created) -> None:
    assert probe.count == 0
    assert created == []
    assert_untouched(env, before)
    assert not any(
        name.startswith(prepare_module._STAGING_PREFIX)
        for name in os.listdir(env.yd_root)
    )
    assert (env.yd_root / "operator-keep").read_bytes() == b"preexisting-root\n"


def _root_for(builder, source: str) -> Path:
    return next(req.variant_root for req in builder.requests if req.source_id == source)


def _two(root: Path) -> None:
    (root / BACKUP).write_bytes((root / VARIANT_CALIBRATED_STATE_NAME).read_bytes())


def _alias(root: Path) -> None:
    (root / VARIANT_CALIBRATED_STATE_NAME).rename(root / ALIAS)


@pytest.mark.parametrize("source", ["gfs", "ifs"])
@pytest.mark.parametrize(
    "hits,script",
    [
        (0, VariantScript(omit_entries=(VARIANT_CALIBRATED_STATE_NAME,))),
        (2, VariantScript(mutate=_two)),
    ],
    ids=["zero", "two"],
)
def test_run_prepare_refuses_zero_or_two_top_level_cfg_ic(
    env, monkeypatch, source, hits, script
):
    before = _plant(env)
    probe = _probe(monkeypatch)
    created = _watch_yd_ensure(env, monkeypatch)
    builder = make_builder(env, {source: script})
    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)
    message = str(excinfo.value)
    variant = _root_for(builder, source)
    assert source in message
    assert str(variant) in message
    assert f"命中 {hits} 个" in message
    assert "必须恰好 1 个" in message
    assert "exactly five" not in message
    if hits == 0:
        assert "（无）" in message
        assert str(variant / VARIANT_CALIBRATED_STATE_NAME) not in message
    else:
        assert str(variant / VARIANT_CALIBRATED_STATE_NAME) in message
        assert str(variant / BACKUP) in message
    _no_publish(env, before, probe, created)


def test_fixed_name_yd_cfg_ic_still_succeeds(env):
    report = run(env, make_builder(env))
    for root in report.variants.values():
        assert VARIANT_CALIBRATED_STATE_NAME == "yd.cfg.ic"
        assert prepare_module.calibrated_state_path(root) == root / "yd.cfg.ic"
        assert (root / "yd.cfg.ic").is_file()
        assert [n for n in os.listdir(root) if n.endswith(STATE_SUFFIX)] == [
            "yd.cfg.ic"
        ]
    assert tree_snapshot(env.scratch_root) == {}


def test_arbitrary_single_alias_is_still_refused(env, monkeypatch):
    before = _plant(env)
    probe = _probe(monkeypatch)
    created = _watch_yd_ensure(env, monkeypatch)
    builder = make_builder(env, {"gfs": VariantScript(mutate=_alias)})
    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)
    message = str(excinfo.value)
    assert "命中" not in message
    assert "exact five-entry" in message
    _no_publish(env, before, probe, created)


def test_shared_locator_counts_only_top_level_regular_suffix(tmp_path):
    root = tmp_path / "variant"
    root.mkdir()
    top = root / "yd.cfg.ic"
    top.write_bytes(b"top\n")
    nested = root / "output"
    nested.mkdir()
    (nested / "nested.cfg.ic").write_bytes(b"nested\n")
    (root / "yd.cfg.ic.update").write_bytes(b"update\n")
    (root / "dir.cfg.ic").mkdir()
    os.mkfifo(root / "fifo.cfg.ic")
    located = _locate_calibration_state(root)
    assert located == top


def test_discovery_error_is_prepare_error_before_staging(env, monkeypatch):
    before = _plant(env)
    probe = _probe(monkeypatch)
    created = _watch_yd_ensure(env, monkeypatch)
    from yd_producer.init import _DiscoveryUnreadable

    def boom(directory):
        raise _DiscoveryUnreadable(f"目录 {directory} 无法枚举（injected EIO）")

    monkeypatch.setattr("yd_producer.init._entry_names", boom)
    with pytest.raises(PrepareError) as excinfo:
        run(env, make_builder(env))
    message = str(excinfo.value)
    assert "gfs" in message
    assert "探测失败" in message
    assert "injected EIO" in message
    assert "命中" not in message
    _no_publish(env, before, probe, created)


def test_builder_replaced_symlink_root_is_not_traversed(env, monkeypatch, tmp_path):
    before = _plant(env)
    probe = _probe(monkeypatch)
    created = _watch_yd_ensure(env, monkeypatch)
    outside = tmp_path / "outside-target"
    outside.mkdir()
    secret = outside / "secret.cfg.ic"
    secret.write_bytes(b"do-not-read\n")
    seen: list[Path] = []
    real = os.listdir
    outside_real = Path(os.path.realpath(os.fspath(outside)))

    def watching(path):
        if not isinstance(path, int):
            candidate = Path(path)
            seen.append(candidate)
            resolved = Path(os.path.realpath(os.fspath(candidate)))
            assert resolved != outside_real
            assert not _is_under(resolved, outside_real)
            assert not candidate.is_symlink()
        return real(path)

    def mutate(root: Path) -> None:
        for child in list(root.iterdir()):
            child.unlink()
        root.rmdir()
        root.symlink_to(outside)

    monkeypatch.setattr(os, "listdir", watching)
    with pytest.raises(PrepareError) as excinfo:
        run(env, make_builder(env, {"gfs": VariantScript(mutate=mutate)}))
    message = str(excinfo.value)
    assert "gfs" in message
    assert secret.read_bytes() == b"do-not-read\n"
    assert outside not in seen
    assert secret not in seen
    _no_publish(env, before, probe, created)
