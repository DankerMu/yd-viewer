# yd-viewer

本地用合成数据跑全栈，不是 node-27 / M5 现场验收，也不是真实流域几何。

## 前提

在本目录（`viewer/`）执行下面这一条命令，不要从仓库根目录跑。

- [`uv`](https://docs.astral.sh/uv/)（Python ≥ 3.12）
- Node.js 22 与 Corepack；命令通过 `corepack pnpm@10.11.0` 对齐 `frontend/package.json` 的 `packageManager`
- macOS 或 Linux（用 POSIX 进程组结束本命令拉起的子进程）

## 一条命令起全栈

命令会先按 frozen lockfile 安装前端、再 `uv sync --frozen` 安装后端，然后用现有 `tests/synthetic.py` 的 `write_geometry` / `write_dat` / `write_done` 在独占临时目录写一份双源、168 行的合成 `YD_ROOT`。静态目录另建，只写入 `basemaps.json` 为 `{}`（无瓦片、无 key、不读私有 env）。只把已有的三个目录变量传给后端子进程环境副本。随后启动真实 `yd_viewer.app:create_app` 工厂（`127.0.0.1:8000`）和 Vite（`127.0.0.1:5173 --strictPort`）。Vite 把 `/api`、`/geometry`、`/basemaps.json` 代理到该后端。

8000 或 5173 已被占用时直接失败，不会换端口，也不会留半套进程。Ctrl-C、SIGTERM、SIGHUP（关闭终端）或任一子进程退出时，向本命令记录的进程组发信号并等待它们退出，再删除本命令创建的临时目录。

打开 <http://127.0.0.1:5173/>，点图上那条对角线河段，应出现 GFS 与 IFS 两条 168 点曲线。生成器把每条河都画成重合的 `[0,0]–[1,1]`，边界是 `[0,0]–[1,0]–[1,1]` 三角形，所以地图只是占位。默认单元格是很小的 m³/day 值，API 再除以 86400，流量都落在 `<1` 色档。两个 source 的列编号互为反序，曲线才能分开；没有改生成器，也没有手写 DAT。

```sh
corepack pnpm@10.11.0 --dir frontend install --frozen-lockfile &&
uv sync --frozen &&
uv run -- python - <<'PY'
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path("tests").resolve()))
from synthetic import write_dat, write_done, write_geometry

root = None
children = []


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def signal_groups(sig: int) -> None:
    for child in children:
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass


def stop() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal_groups(signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(
        group_alive(child.pid) for child in children
    ):
        for child in children:
            child.poll()
        time.sleep(0.1)
    signal_groups(signal.SIGKILL)
    for child in children:
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)


def terminate(_signum, _frame) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    raise SystemExit(1)


signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGHUP, terminate)
reach_ids = (1, 2)
try:
    root = Path(tempfile.mkdtemp(prefix="yd-viewer-dev-"))
    write_geometry(root / "input", reach_ids=reach_ids)
    cycle = "2026082712"
    for source, column_ids in (
        ("gfs", reach_ids),
        ("ifs", tuple(reversed(reach_ids))),
    ):
        source_dir = root / "output" / cycle / source
        write_dat(source_dir / "yd.rivqdown.dat", column_ids=column_ids, rows=168)
        write_done(source_dir / "DONE")
    static_dir = root / "static"
    static_dir.mkdir()
    (static_dir / "basemaps.json").write_text("{}", encoding="utf-8")
    print(f"dataset {root}", flush=True)

    backend_env = os.environ.copy()
    backend_env["YD_VIEWER_INPUT_DIR"] = str(root / "input")
    backend_env["YD_VIEWER_OUTPUT_DIR"] = str(root / "output")
    backend_env["YD_VIEWER_STATIC_DIR"] = str(static_dir)

    children.append(
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "yd_viewer.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            env=backend_env,
            start_new_session=True,
        )
    )
    children.append(
        subprocess.Popen(
            [
                "corepack",
                "pnpm@10.11.0",
                "--dir",
                "frontend",
                "dev",
                "--host",
                "127.0.0.1",
                "--port",
                "5173",
                "--strictPort",
            ],
            start_new_session=True,
        )
    )
    print(f"backend {children[0].pid}", flush=True)
    print(f"frontend {children[1].pid}", flush=True)
    print("http://127.0.0.1:5173/", flush=True)
    while True:
        for child in children:
            code = child.poll()
            if code is not None:
                raise SystemExit(code or 1)
        time.sleep(0.2)
except KeyboardInterrupt:
    pass
finally:
    stop()
PY
```
