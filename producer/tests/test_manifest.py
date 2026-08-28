"""`yd_producer.raw.manifest` 信封最小用例（清单 §4 风险 3：pin 上引用 `DownloadManifest`/
`ManifestEntry`/`cycle_id_for` 的 22 个 NWM 测试全是 adapter/scheduler/DB 重面文件，
无一可作最小快照，故本文件为 yd 新写）。

期望值来源是 pin `8ae9b8f2` 的源码，不从实现回读：

- `cycle_id_for`(base.py L46-48) = `f"{normalize_source_id(source_id).lower()}_{format_cycle_time(cycle_time)}"`，
  `format_cycle_time`(L42-43) = `parse_cycle_time(...).strftime("%Y%m%d%H")`。
  配合本 PR 改写后的 `_STORAGE_SOURCE_IDS = {"GFS": "gfs", "IFS": "ifs"}`，IFS 分支必须
  出小写 `ifs_...`（docs/products-contract.md:37）。
- `ManifestEntry.as_dict`(L194-203) / `from_dict`(L205-215) 与 `DownloadManifest.as_dict`(L226-233) /
  `from_dict`(L235-243) 的键名逐字取自 pin。
- 畸形输入的抛错形态取自 pin 的下标取值（缺键 -> `KeyError`）与 `int(...)`/`parse_cycle_time`
  （类型错 -> `ValueError`）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from yd_producer.raw.manifest import (
    DownloadManifest,
    ManifestEntry,
    cycle_id_for,
    ensure_utc,
    format_cycle_time,
    parse_cycle_date,
    parse_cycle_time,
    valid_time_for,
)

_CYCLE = datetime(2026, 5, 7, 0, 0, tzinfo=UTC)


def _entry() -> ManifestEntry:
    return ManifestEntry(
        remote_url="https://example.invalid/gfs.t00z.pgrb2.0p25.f003.bundle.grib2",
        local_key="raw/gfs/2026050700/gfs.t00z.pgrb2.0p25.f003.bundle.grib2",
        variable="t2m",
        forecast_hour=3,
        expected_checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        expected_size_bytes=1024,
        metadata={"cycle_time": "2026050700", "bundle": {"layout": "bundle"}},
    )


def _manifest() -> DownloadManifest:
    return DownloadManifest(
        source_id="gfs",
        cycle_time=_CYCLE,
        entries=(_entry(),),
        manifest_uri="raw/gfs/2026050700/raw-manifest.json",
        metadata={"forecast_hours": [0, 3]},
    )


# --- cycle id / 时间族 --------------------------------------------------------


@pytest.mark.parametrize(
    ("source_id", "expected"),
    [
        ("GFS", "gfs_2026050700"),
        ("gfs", "gfs_2026050700"),
        ("IFS", "ifs_2026050700"),
        ("ifs", "ifs_2026050700"),
    ],
)
def test_cycle_id_for_is_lowercase_source_plus_compact_cycle(
    source_id: str, expected: str
) -> None:
    assert cycle_id_for(source_id, "2026050700") == expected
    assert cycle_id_for(source_id, _CYCLE) == expected


def test_cycle_id_for_rejects_an_unknown_source() -> None:
    with pytest.raises(ValueError, match="Unknown source_id"):
        cycle_id_for("ERA5", "2026050700")


def test_parse_cycle_time_accepts_compact_and_iso_and_z_forms() -> None:
    assert parse_cycle_time("2026050700") == _CYCLE
    assert parse_cycle_time("2026-05-07T00:00:00+00:00") == _CYCLE
    assert parse_cycle_time("2026-05-07T00:00:00Z") == _CYCLE
    assert parse_cycle_time(datetime(2026, 5, 7)) == _CYCLE  # noqa: DTZ001 naive 输入是被测语义


def test_ensure_utc_and_format_and_valid_time() -> None:
    assert ensure_utc(datetime(2026, 5, 7)) == _CYCLE  # noqa: DTZ001 naive 输入是被测语义
    assert format_cycle_time("2026-05-07T00:00:00Z") == "2026050700"
    assert valid_time_for("2026050700", 12) == datetime(2026, 5, 7, 12, tzinfo=UTC)
    assert parse_cycle_date("2026-05-07") == _CYCLE.date()


# --- 信封 roundtrip -----------------------------------------------------------


def test_manifest_entry_as_dict_pins_the_envelope_keys() -> None:
    assert _entry().as_dict() == {
        "remote_url": "https://example.invalid/gfs.t00z.pgrb2.0p25.f003.bundle.grib2",
        "local_key": "raw/gfs/2026050700/gfs.t00z.pgrb2.0p25.f003.bundle.grib2",
        "variable": "t2m",
        "forecast_hour": 3,
        "expected_checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "expected_size_bytes": 1024,
        "metadata": {"cycle_time": "2026050700", "bundle": {"layout": "bundle"}},
    }


def test_manifest_entry_roundtrips_through_as_dict() -> None:
    entry = _entry()

    assert ManifestEntry.from_dict(entry.as_dict()) == entry


def test_manifest_entry_optional_fields_default_to_none_and_empty_metadata() -> None:
    entry = ManifestEntry.from_dict(
        {
            "remote_url": "https://example.invalid/a.grib2",
            "local_key": "raw/gfs/2026050700/a.grib2",
            "variable": "t2m",
            "forecast_hour": "6",
        }
    )

    assert entry.expected_checksum is None
    assert entry.expected_size_bytes is None
    assert entry.metadata == {}
    assert entry.forecast_hour == 6


def test_download_manifest_as_dict_pins_the_envelope_keys() -> None:
    assert _manifest().as_dict() == {
        "source_id": "gfs",
        "cycle_time": "2026-05-07T00:00:00+00:00",
        "manifest_uri": "raw/gfs/2026050700/raw-manifest.json",
        "metadata": {"forecast_hours": [0, 3]},
        "entries": [_entry().as_dict()],
    }


def test_download_manifest_roundtrips_through_as_dict() -> None:
    manifest = _manifest()

    restored = DownloadManifest.from_dict(manifest.as_dict())

    assert restored == manifest
    assert restored.entries == manifest.entries
    assert restored.cycle_time == _CYCLE


def test_download_manifest_from_dict_accepts_the_compact_cycle_form() -> None:
    restored = DownloadManifest.from_dict(
        {"source_id": "ifs", "cycle_time": "2026050700", "entries": []}
    )

    assert restored.cycle_time == _CYCLE
    assert restored.entries == ()
    assert restored.manifest_uri is None
    assert restored.metadata == {}


# --- 畸形输入稳定抛错 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"local_key": "k", "variable": "t2m", "forecast_hour": 3}, KeyError),
        ({"remote_url": "u", "variable": "t2m", "forecast_hour": 3}, KeyError),
        ({"remote_url": "u", "local_key": "k", "forecast_hour": 3}, KeyError),
        ({"remote_url": "u", "local_key": "k", "variable": "t2m"}, KeyError),
        (
            {
                "remote_url": "u",
                "local_key": "k",
                "variable": "t2m",
                "forecast_hour": "abc",
            },
            ValueError,
        ),
        (
            {
                "remote_url": "u",
                "local_key": "k",
                "variable": "t2m",
                "forecast_hour": None,
            },
            TypeError,
        ),
    ],
)
def test_manifest_entry_from_dict_fails_closed_on_malformed_input(
    payload: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        ManifestEntry.from_dict(payload)


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"cycle_time": "2026050700", "entries": []}, KeyError),
        ({"source_id": "gfs", "entries": []}, KeyError),
        ({"source_id": "gfs", "cycle_time": "2026050700"}, KeyError),
        ({"source_id": "gfs", "cycle_time": "not-a-time", "entries": []}, ValueError),
        (
            {
                "source_id": "gfs",
                "cycle_time": "2026050700",
                "entries": [{"local_key": "k", "variable": "t2m", "forecast_hour": 3}],
            },
            KeyError,
        ),
    ],
)
def test_download_manifest_from_dict_fails_closed_on_malformed_input(
    payload: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        DownloadManifest.from_dict(payload)
