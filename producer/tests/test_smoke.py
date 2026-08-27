import yd_producer


def test_package_importable():
    assert yd_producer.__version__ == "0.1.0"


def test_scientific_stack_importable():
    import cfgrib
    import numpy
    import xarray

    assert numpy.__version__
    assert xarray.__version__
    assert cfgrib.__version__


def test_cfgrib_registered_as_xarray_backend():
    """cfgrib 注册为 xarray backend 证明 eccodes 运行时二进制真的可用。"""
    import cfgrib  # noqa: F401
    import xarray

    assert "cfgrib" in xarray.backends.list_engines()
