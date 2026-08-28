"""`yd_producer.raw.region` 的 `GeoBBox` 构造/校验用例（清单 §4 风险 8：pin 上无可快照的既有测试）。

期望值来源是 pin `8ae9b8f2` 的 `workers/data_adapters/region.py` 源码，不从实现回读：
`GeoBBox.__post_init__`(L41-49) 的四条校验与其抛错文案、`as_dict`(L51-52)、
`identity`(L54-56) 的 `:g` 格式串。

清单 `剥离点` 已命令删掉四个 `DEFAULT_BBOX_*` 常量、`china_buffered_bbox_from_env`
与 `_env_float`：bbox 只能由 `config.toml` 显式注入（design.md D4 零默认），缺参即
fail closed，本文件对这三点做具名守卫。
"""

from __future__ import annotations

import dataclasses

import pytest

from yd_producer.raw import region as module
from yd_producer.raw.region import GeoBBox

# --- D4 零默认守卫 ------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "DEFAULT_BBOX_SOUTH",
        "DEFAULT_BBOX_NORTH",
        "DEFAULT_BBOX_WEST",
        "DEFAULT_BBOX_EAST",
        "china_buffered_bbox_from_env",
        "_env_float",
        "os",
    ],
)
def test_module_carries_no_default_bbox_or_environment_surface(name: str) -> None:
    assert not hasattr(module, name)


def test_geobbox_fields_carry_no_defaults() -> None:
    for field in dataclasses.fields(GeoBBox):
        assert field.default is dataclasses.MISSING
        assert field.default_factory is dataclasses.MISSING


def test_geobbox_without_any_bbox_argument_fails_closed() -> None:
    with pytest.raises(TypeError):
        GeoBBox()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"north": 34.0, "west": 100.0, "east": 104.0},
        {"south": 30.0, "west": 100.0, "east": 104.0},
        {"south": 30.0, "north": 34.0, "east": 104.0},
        {"south": 30.0, "north": 34.0, "west": 100.0},
    ],
)
def test_geobbox_with_a_missing_edge_fails_closed(kwargs: dict[str, float]) -> None:
    with pytest.raises(TypeError):
        GeoBBox(**kwargs)  # type: ignore[arg-type]


# --- 构造与派生 ---------------------------------------------------------------


def test_geobbox_accepts_a_well_formed_box_and_is_frozen() -> None:
    bbox = GeoBBox(south=30.0, north=34.0, west=100.0, east=104.0)

    assert (bbox.south, bbox.north, bbox.west, bbox.east) == (30.0, 34.0, 100.0, 104.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        bbox.south = 0.0  # type: ignore[misc]


def test_geobbox_as_dict_pins_the_key_names() -> None:
    bbox = GeoBBox(south=30.0, north=34.0, west=100.0, east=104.0)

    assert bbox.as_dict() == {
        "south": 30.0,
        "north": 34.0,
        "west": 100.0,
        "east": 104.0,
    }


def test_geobbox_identity_uses_the_general_float_format() -> None:
    bbox = GeoBBox(south=30.0, north=34.5, west=-100.25, east=104.0)

    assert bbox.identity() == "bbox:s30:n34.5:w-100.25:e104"


def test_geobbox_accepts_negative_longitudes_and_the_0_to_360_tail() -> None:
    assert (
        GeoBBox(south=-10.0, north=10.0, west=-20.0, east=-5.0).identity()
        == "bbox:s-10:n10:w-20:e-5"
    )
    assert (
        GeoBBox(south=-10.0, north=10.0, west=200.0, east=359.0).identity()
        == "bbox:s-10:n10:w200:e359"
    )


# --- 校验分型 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("south", "north"),
    [(-90.5, 34.0), (30.0, 90.5), (-91.0, 91.0)],
)
def test_geobbox_rejects_out_of_range_latitudes(south: float, north: float) -> None:
    with pytest.raises(ValueError, match=r"Latitude out of range \[-90, 90\]"):
        GeoBBox(south=south, north=north, west=100.0, east=104.0)


@pytest.mark.parametrize(
    ("west", "east"),
    [(-180.5, 104.0), (100.0, 360.5), (-181.0, 361.0)],
)
def test_geobbox_rejects_out_of_range_longitudes(west: float, east: float) -> None:
    with pytest.raises(ValueError, match=r"Longitude out of range \[-180, 360\]"):
        GeoBBox(south=30.0, north=34.0, west=west, east=east)


@pytest.mark.parametrize(("south", "north"), [(34.0, 34.0), (35.0, 34.0)])
def test_geobbox_rejects_a_non_ascending_latitude_pair(
    south: float, north: float
) -> None:
    with pytest.raises(ValueError, match="must be < north"):
        GeoBBox(south=south, north=north, west=100.0, east=104.0)


@pytest.mark.parametrize(("west", "east"), [(104.0, 104.0), (105.0, 104.0)])
def test_geobbox_rejects_a_non_ascending_longitude_pair(
    west: float, east: float
) -> None:
    with pytest.raises(ValueError, match="must be < east"):
        GeoBBox(south=30.0, north=34.0, west=west, east=east)
