r"""`yd_producer.runlock` 的行为测试（非阻塞 flock 封装，任务 12.3）。

判别器纪律（issue #23 裁决 11，fixture 复核实测 darwin 24.6.0）：进程内「持有即跳过」
用例的**第一持有者也必须经同一个封装**取锁，MUST NOT 由测试自己直接 `fcntl.flock`。
XNU 把 `flock` 与 `lockf` 并进同一条 lock list，测试自持 `flock` 时封装侧的 `lockf`
仍报 `EAGAIN`，于是「`flock` 换 `lockf`」这个变异体照样走跳过分支而存活。两端同经封装
则该变异体让两把锁都变成同进程不冲突的 `lockf`，第二次进入会**真执行**，用例变红。

`fcntl.flock` 的锁挂在 open file description 上，故同一进程内两次独立 `open()` 互相
冲突——进程内用例因此是有效判别器。spec 的 Scenario 写的是「另一进程」，另有一条子进程
用例正面覆盖那条字面 WHEN。

每一条可能阻塞的用例都自带 `_deadline` 超时（`SIGALRM`）：去掉 `LOCK_NB` 的变异体会让
`flock` 永久阻塞，没有超时的话挂死的是测试自身而不是变异体。信号处理函数**抛异常**，
故 PEP 475 的自动重启不适用，阻塞中的 `flock` 会被打断成 `TimeoutError`。
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import signal
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from typing import Any

import pytest

from yd_producer import runlock

#: 单条用例允许的最长阻塞（秒）。真实路径全部是非阻塞的，只有变异体会撞到它。
_DEADLINE_SECONDS = 5.0


@contextlib.contextmanager
def _deadline(seconds: float = _DEADLINE_SECONDS) -> Iterator[None]:
    """用例自带的硬超时：到点抛 `TimeoutError`，打断阻塞中的系统调用。"""

    def _fire(signum: int, frame: object) -> None:
        raise TimeoutError(f"用例超过 {seconds}s 未返回：疑似阻塞取锁")

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class RecordingAction:
    """记录型被包裹对象：记下被调用几次，可选地抛出异常。"""

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def lock_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """`cron.lock_path`：绝对路径，父目录已存在（现场由部署创建）。"""
    run_dir = tmp_path.resolve() / "run"
    run_dir.mkdir()
    return run_dir / "yd-producer.lock"


def test_second_entry_in_the_same_process_skips(lock_path: pathlib.Path) -> None:
    """已持锁时第二次进入封装立即跳过：被包裹对象零调用，进程不阻塞。

    两端同经封装（裁决 11）：外层用同一个 `run_with_lock` 持锁。
    """
    inner_action = RecordingAction(result="inner-ran")

    def outer_body() -> runlock.RunLockResult:
        return runlock.run_with_lock(lock_path=lock_path, action=inner_action)

    with _deadline():
        outer = runlock.run_with_lock(lock_path=lock_path, action=outer_body)

    assert outer.acquired is True
    inner = outer.value
    assert isinstance(inner, runlock.RunLockResult)
    # 跳过是成功语义，且与「跑过了」可区分：判 `acquired`，不判 `value`
    assert inner.acquired is False
    assert inner.value is None
    assert inner.lock_path == lock_path
    assert inner_action.calls == 0


def test_lock_held_by_another_process_skips(lock_path: pathlib.Path) -> None:
    """spec 字面 WHEN：子进程持锁期间父进程进入封装 -> 跳过、零调用、不阻塞。"""
    script = textwrap.dedent(
        f"""
        import fcntl, os, sys

        fd = os.open({str(lock_path)!r}, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        sys.stdout.write("locked\\n")
        sys.stdout.flush()
        sys.stdin.readline()
        """
    )
    action = RecordingAction()
    holder = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        with _deadline():
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "locked"
            result = runlock.run_with_lock(lock_path=lock_path, action=action)
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=10)

    assert result.acquired is False
    assert result.value is None
    assert action.calls == 0


def test_lock_is_reusable_and_the_lock_file_survives_release(
    lock_path: pathlib.Path,
) -> None:
    """释放后可再取；锁文件在释放后**仍存在**（删掉即在新 inode 上重建锁，互斥失效）。"""
    first = RecordingAction(result=1)
    second = RecordingAction(result=2)

    with _deadline():
        first_result = runlock.run_with_lock(lock_path=lock_path, action=first)
        assert lock_path.is_file()
        second_result = runlock.run_with_lock(lock_path=lock_path, action=second)

    assert (first_result.acquired, first_result.value) == (True, 1)
    assert (second_result.acquired, second_result.value) == (True, 2)
    assert first.calls == 1
    assert second.calls == 1
    assert lock_path.is_file()


def test_lock_is_released_when_the_action_raises(lock_path: pathlib.Path) -> None:
    """被包裹对象抛异常 -> 异常向外传播，且锁已释放（同棵树第二次进入能拿到）。"""
    boom = RecordingAction(error=RuntimeError("发布段炸了"))
    after = RecordingAction(result="ran")

    with _deadline():
        with pytest.raises(RuntimeError, match="发布段炸了"):
            runlock.run_with_lock(lock_path=lock_path, action=boom)
        recovered = runlock.run_with_lock(lock_path=lock_path, action=after)

    assert boom.calls == 1
    assert recovered.acquired is True
    assert after.calls == 1
    assert lock_path.is_file()


@pytest.mark.parametrize("configured", ["yd.lock", "~/yd.lock"])
def test_non_absolute_lock_path_fails_closed_before_any_side_effect(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    """相对路径与 `~` 前缀都拒：报错指名 `cron.lock_path`，零锁文件、零调用。

    `HOME` 与 cwd 都指到 `tmp_path`：闸门被搬到 `open()` 之后的变异体会在 cwd 造出
    `yd.lock`（或在展开后的 home 里造出锁文件），两处断言各杀一种。
    """
    home = tmp_path.resolve() / "home"
    home.mkdir()
    cwd = tmp_path.resolve() / "cwd"
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)
    action = RecordingAction()

    with _deadline(), pytest.raises(runlock.RunLockError, match="cron.lock_path"):
        runlock.run_with_lock(lock_path=configured, action=action)

    assert action.calls == 0
    assert list(cwd.iterdir()) == []
    assert list(home.iterdir()) == []
    assert pathlib.Path.home() == home
    assert not (home / "yd.lock").exists()
    assert not os.path.lexists(cwd / "yd.lock")
