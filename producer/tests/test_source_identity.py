# NWM@8ae9b8f2 tests/test_source_identity.py
from __future__ import annotations

import pytest

from yd_producer.raw.source_identity import normalize_source_id


def test_normalize_source_id_gfs_variants() -> None:
    assert normalize_source_id("GFS") == "gfs"
    assert normalize_source_id("gfs") == "gfs"
    assert normalize_source_id("Gfs") == "gfs"


def test_normalize_source_id_ifs() -> None:
    assert normalize_source_id("IFS") == "ifs"
    assert normalize_source_id("ifs") == "ifs"
    assert normalize_source_id("Ifs") == "ifs"


def test_normalize_source_id_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown source_id"):
        normalize_source_id("UNKNOWN")


@pytest.mark.parametrize(
    ("source_id", "expected"),
    [
        ("GFS", "gfs"),
        ("gFs", "gfs"),
        ("IFS", "ifs"),
        ("iFs", "ifs"),
    ],
)
def test_user_input_case_insensitive_maps_to_canonical_storage_id(
    source_id: str, expected: str
) -> None:
    assert normalize_source_id(source_id) == expected
