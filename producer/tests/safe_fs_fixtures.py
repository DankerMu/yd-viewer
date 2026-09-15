# NWM@8ae9b8f2 tests/test_safe_fs.py
"""yd structural glue: shared fd-closed assertion for split safe_fs tests.

Moved snapshot bodies retain existing registered adaptations. This module
does not import test functions.
"""

from __future__ import annotations

import errno
import os

import pytest


def _assert_fd_closed(file_fd: int) -> None:
    with pytest.raises(OSError) as info:
        os.fstat(file_fd)
    assert info.value.errno == errno.EBADF
