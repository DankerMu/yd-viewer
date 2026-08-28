"""`yd_producer.store.object_store` 最小用例（清单 §4 风险 4：pin 上无干净可快照测试，本文件为 yd 新写）。

期望值来源全部是 pin `8ae9b8f2` 的源码或独立 oracle，不从实现回读：

- `sha256_bytes` 的摘要来自 `printf 'yd-producer snapshot oracle' | shasum -a 256`
  与 `printf '' | shasum -a 256`。
- 覆盖语义来自 `packages/common/safe_fs.py` 的 `atomic_write_bytes_no_follow`
  (L138-206)：写临时文件后走 `os.replace(temp_name, target.name, ...)`(L172)，
  即**覆盖**（非 no-clobber）。
- 拒绝分型来自 `packages/common/object_store.py`：`normalize_object_key`(L44) 对空键
  与含 `..` 的键抛 `ValueError`(L65/L74)；绝对路径不由它拒绝（L72 `strip("/")`），
  而是在 `resolve_path`(L273) 处经 `validate_object_path` 判定前缀非法后抛 `ValueError`(L277)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yd_producer.store.object_path import validate_object_path
from yd_producer.store.object_store import (
    OBJECT_KIND_ABSENT,
    OBJECT_KIND_DIRECTORY,
    OBJECT_KIND_FILE,
    LocalObjectStore,
    ObjectStoreError,
    normalize_object_key,
    sha256_bytes,
)

_KEY = "raw/gfs/2026050700/gfs.t00z.pgrb2.0p25.f000.bundle.grib2"


def test_sha256_bytes_matches_external_shasum_oracle() -> None:
    assert (
        sha256_bytes(b"yd-producer snapshot oracle")
        == "64f842983385094cd375542db2c6bc7ac1ddf05378743f29d08b805a0efb0bc1"
    )
    assert (
        sha256_bytes(b"")
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_normalize_object_key_strips_slashes_and_keeps_key() -> None:
    assert (
        normalize_object_key("/raw/gfs/2026050700/a.grib2/")
        == "raw/gfs/2026050700/a.grib2"
    )
    assert (
        normalize_object_key("  raw/gfs/2026050700/a.grib2  ")
        == "raw/gfs/2026050700/a.grib2"
    )


def test_normalize_object_key_applies_configured_bare_prefix() -> None:
    assert normalize_object_key("nhms/raw/gfs/a.grib2", "nhms/") == "raw/gfs/a.grib2"


def test_normalize_object_key_decodes_s3_uri_below_prefix() -> None:
    assert (
        normalize_object_key("s3://bucket/base/raw/gfs/a%20b.grib2", "s3://bucket/base")
        == "raw/gfs/a b.grib2"
    )


@pytest.mark.parametrize("key", ["", "   ", "\n"])
def test_normalize_object_key_rejects_empty_key(key: str) -> None:
    with pytest.raises(ValueError, match="Object key is empty."):
        normalize_object_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "raw/../../etc/passwd",
        "raw/gfs/../../../secret",
        "../raw/gfs/a.grib2",
    ],
)
def test_normalize_object_key_rejects_parent_traversal(key: str) -> None:
    with pytest.raises(ValueError, match="must not contain '\\.\\.'"):
        normalize_object_key(key)


def test_resolve_path_rejects_absolute_path_key(tmp_path: Path) -> None:
    # 绝对路径在 normalize_object_key 处只被 strip("/")，真正的拒绝闸门是
    # resolve_path -> validate_object_path 的前缀白名单。
    store = LocalObjectStore(root=tmp_path)

    with pytest.raises(ValueError, match="Unrecognized object path prefix"):
        store.resolve_path("/etc/passwd")


def test_resolve_path_rejects_traversal_key(tmp_path: Path) -> None:
    store = LocalObjectStore(root=tmp_path)

    with pytest.raises(ValueError, match="must not contain '\\.\\.'"):
        store.resolve_path("raw/gfs/../../../etc/passwd")


def test_resolve_path_rejects_empty_key(tmp_path: Path) -> None:
    store = LocalObjectStore(root=tmp_path)

    with pytest.raises(ValueError, match="Object key is empty."):
        store.resolve_path("")


def test_write_read_roundtrip_and_derived_metadata(tmp_path: Path) -> None:
    store = LocalObjectStore(root=tmp_path)
    payload = b"yd-producer snapshot oracle"

    uri = store.write_bytes_atomic(_KEY, payload)

    assert uri == _KEY  # 无 object_store_prefix 时 uri_for_key 返回归一化键本身
    assert store.exists(_KEY) is True
    assert store.object_kind(_KEY) == OBJECT_KIND_FILE
    assert store.read_bytes(_KEY) == payload
    assert store.size(_KEY) == len(payload)
    assert (
        store.checksum(_KEY)
        == "64f842983385094cd375542db2c6bc7ac1ddf05378743f29d08b805a0efb0bc1"
    )
    assert store.size_and_checksum(_KEY) == (
        len(payload),
        "64f842983385094cd375542db2c6bc7ac1ddf05378743f29d08b805a0efb0bc1",
    )
    assert (tmp_path / _KEY).read_bytes() == payload


def test_write_bytes_atomic_overwrites_an_existing_object(tmp_path: Path) -> None:
    # pin 语义：atomic_write_bytes_no_follow 以 os.replace 落地（safe_fs.py L172），
    # 对已存在对象是覆盖而非 no-clobber。
    store = LocalObjectStore(root=tmp_path)
    store.write_bytes_atomic(_KEY, b"first")

    store.write_bytes_atomic(_KEY, b"second")

    assert store.read_bytes(_KEY) == b"second"
    assert store.size(_KEY) == len(b"second")


def test_read_bytes_limited_refuses_beyond_the_byte_ceiling(tmp_path: Path) -> None:
    store = LocalObjectStore(root=tmp_path)
    store.write_bytes_atomic(_KEY, b"0123456789")

    assert store.read_bytes_limited(_KEY, max_bytes=10) == b"0123456789"
    with pytest.raises(ObjectStoreError, match="exceeds read limit"):
        store.read_bytes_limited(_KEY, max_bytes=4)
    with pytest.raises(ValueError, match="max_bytes must be non-negative."):
        store.read_bytes_limited(_KEY, max_bytes=-1)


def test_absent_and_directory_kinds_are_distinguished(tmp_path: Path) -> None:
    store = LocalObjectStore(root=tmp_path)

    assert store.exists(_KEY) is False
    assert store.object_kind(_KEY) == OBJECT_KIND_ABSENT

    (tmp_path / _KEY).mkdir(parents=True)

    assert store.exists(_KEY) is True
    assert store.object_kind(_KEY) == OBJECT_KIND_DIRECTORY


def test_read_of_a_missing_object_is_a_structured_refusal(tmp_path: Path) -> None:
    store = LocalObjectStore(root=tmp_path)

    with pytest.raises(ObjectStoreError, match="Failed to read object"):
        store.read_bytes(_KEY)


def test_delete_is_idempotent_and_removes_the_object(tmp_path: Path) -> None:
    store = LocalObjectStore(root=tmp_path)
    store.write_bytes_atomic(_KEY, b"payload")

    store.delete(_KEY)
    store.delete(_KEY)

    assert store.exists(_KEY) is False


def test_uri_for_key_folds_the_configured_prefix(tmp_path: Path) -> None:
    store = LocalObjectStore(root=tmp_path, object_store_prefix="s3://bucket/base")

    assert store.uri_for_key(_KEY) == f"s3://bucket/base/{_KEY}"


def test_iter_bytes_streams_the_whole_object(tmp_path: Path) -> None:
    store = LocalObjectStore(root=tmp_path)
    store.write_bytes_atomic(_KEY, b"0123456789")

    assert list(store.iter_bytes(_KEY, chunk_size=4)) == [b"0123", b"4567", b"89"]
    with pytest.raises(ValueError, match="chunk_size must be positive."):
        list(store.iter_bytes(_KEY, chunk_size=0))


def test_validate_object_path_alone_accepts_parent_traversal() -> None:
    """`validate_object_path` 单独调用**不是**穿越闸门——pin 语义，本 PR 刻意不改。

    它只做前缀白名单匹配（`packages/common/storage.py` 的 `_match_pattern`：变量段原样
    捕获，不校验段内容），所以 `..` 会被当成合法的 `{cycle_time}` 值。真正闭合
    containment 的是复合入口 `LocalObjectStore.resolve_path` =
    `normalize_object_key`（拒 `..` 与空键）→ `validate_object_path`（前缀白名单，拒
    绝对路径 strip 后的非法前缀）→ `relative_to(root)`。清单 §1 该行 `剥离点` 为 `无`，
    组 3/7/13 若把本函数读成穿越闸门即为误用。
    """
    result = validate_object_path("raw/gfs/../../../etc/passwd")

    assert result.valid is True
    assert result.category == "raw"
    assert result.components == {"source": "gfs", "cycle_time": ".."}
    assert result.error is None
