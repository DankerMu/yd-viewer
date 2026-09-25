#!/usr/bin/env bash
# Non-root shell tests for viewer/entrypoint.sh.
set -eu
# Ambient umask of a default login shell; the entrypoint must override it.
umask 022

if [ "$(id -u)" -eq 0 ]; then
  echo "test_entrypoint.sh must run as non-root" >&2
  exit 1
fi

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
ENTRYPOINT="$ROOT/viewer/entrypoint.sh"
if [ ! -f "$ENTRYPOINT" ]; then
  echo "missing entrypoint: $ENTRYPOINT" >&2
  exit 1
fi
if [ ! -x "$ENTRYPOINT" ]; then
  echo "entrypoint is not executable: $ENTRYPOINT" >&2
  exit 1
fi

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/yd-entrypoint.XXXXXX")"
cleanup() {
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

SENTINEL="$WORKDIR/uvicorn"
LAUNCH_LOG="$WORKDIR/launch.log"
STATIC_DIR="$WORKDIR/static"
OUT="$WORKDIR/stdio.log"
mkdir -p "$STATIC_DIR"

cat > "$SENTINEL" <<'EOF'
#!/usr/bin/env bash
set -eu
log="${YD_TEST_LAUNCH_LOG:?}"
{
  printf 'pid=%s\n' "$$"
  printf 'uid=%s\n' "$(id -u)"
  printf 'umask=%s\n' "$(umask)"
  printf 'argc=%s\n' "$#"
  i=1
  for arg in "$@"; do
    printf 'arg%s=%s\n' "$i" "$arg"
    i=$((i + 1))
  done
} > "$log"
exit 0
EOF
chmod +x "$SENTINEL"

uv_py() {
  UV_OFFLINE=1 UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never \
    uv run --offline --no-project --no-cache --no-python-downloads --python 3.12 python "$@"
}

clear_basemap_env() {
  unset YD_TIANDITU_KEY
  unset YD_BASEMAP_VECTOR_URL
  unset YD_BASEMAP_SATELLITE_URL
  unset YD_BASEMAP_TERRAIN_URL
  unset YD_BASEMAP_VECTOR_ANNOTATION_URL
  unset YD_BASEMAP_SATELLITE_ANNOTATION_URL
  unset YD_BASEMAP_TERRAIN_ANNOTATION_URL
}

assert_json_equal() {
  uv_py - "$1" "$2" <<'PY'
import ast
import json
import pathlib
import sys

got = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = ast.literal_eval(sys.argv[2])
if got != expected:
    raise SystemExit(f"JSON mismatch: {got!r} != {expected!r}")
PY
}

assert_exec() {
  uv_py - "$1" "$2" <<'PY'
import pathlib
import sys

log = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
launched = sys.argv[2]
fields = dict(line.split("=", 1) for line in log.splitlines() if "=" in line)
if fields.get("pid") != launched:
    raise SystemExit(
        f"PID mismatch: sentinel {fields.get('pid')!r} != launched {launched!r}"
    )
if fields.get("uid") == "0":
    raise SystemExit("sentinel ran as root")
if fields.get("umask") != "0002":
    raise SystemExit(f"umask={fields.get('umask')!r}, expected '0002'")
if fields.get("argc") != "6":
    raise SystemExit(f"argc={fields.get('argc')!r}, expected 6")
expected = {
    "arg1": "yd_viewer.app:create_app",
    "arg2": "--factory",
    "arg3": "--host",
    "arg4": "0.0.0.0",
    "arg5": "--port",
    "arg6": "8000",
}
for key, value in expected.items():
    if fields.get(key) != value:
        raise SystemExit(f"{key}={fields.get(key)!r}, expected {value!r}")
PY
}

assert_mode_664() {
  uv_py - "$1" <<'PY'
import os
import stat
import sys

mode = stat.S_IMODE(os.stat(sys.argv[1]).st_mode)
if mode != 0o664:
    raise SystemExit(f"{sys.argv[1]} mode {mode:o}, expected 664")
PY
}

assert_secret_absent() {
  if grep -F -q 'SECRET' "$1"; then
    echo "SECRET leaked in logs" >&2
    cat "$1" >&2
    exit 1
  fi
  if grep -F -q 'tk=' "$1"; then
    echo "tk= leaked in logs" >&2
    cat "$1" >&2
    exit 1
  fi
}

PROXY_EXPECTED='{"vector": {"tiles": ["api/basemap/tianditu/vec/{z}/{x}/{y}"], "annotation": ["api/basemap/tianditu/cva/{z}/{x}/{y}"]}, "satellite": {"tiles": ["api/basemap/tianditu/img/{z}/{x}/{y}"], "annotation": ["api/basemap/tianditu/cia/{z}/{x}/{y}"]}, "terrain": {"tiles": ["api/basemap/tianditu/ter/{z}/{x}/{y}"], "annotation": ["api/basemap/tianditu/cta/{z}/{x}/{y}"]}}'

VECTOR_SATELLITE_EXPECTED='{"vector": {"tiles": ["https://example.test/vec/{z}/{x}/{y}.png?tk=SECRET&q=\"quote\"\\slash"], "annotation": ["https://example.test/cva/{z}/{x}/{y}.png?tk=SECRET&nl=\nend"]}, "satellite": {"tiles": ["https://example.test/img/{z}/{x}/{y}.png?tk=SECRET"], "annotation": None}}'

run_case() {
  rm -f "$LAUNCH_LOG" "$STATIC_DIR/basemaps.json" "$OUT"
  set +e
  env -u YD_TIANDITU_KEY \
    -u YD_BASEMAP_VECTOR_URL \
    -u YD_BASEMAP_SATELLITE_URL \
    -u YD_BASEMAP_TERRAIN_URL \
    -u YD_BASEMAP_VECTOR_ANNOTATION_URL \
    -u YD_BASEMAP_SATELLITE_ANNOTATION_URL \
    -u YD_BASEMAP_TERRAIN_ANNOTATION_URL \
    PATH="$WORKDIR:$PATH" \
    YD_VIEWER_STATIC_DIR="$STATIC_DIR" \
    YD_TEST_LAUNCH_LOG="$LAUNCH_LOG" \
    "$@" \
    "$ENTRYPOINT" --port 1234 \
    >"$OUT" 2>&1 &
  launched=$!
  wait "$launched"
  status=$?
  set -e
}

# vector+annotation and satellite (no terrain); extra --port and YD_VIEWER_PORT ignored
clear_basemap_env
run_case \
  YD_VIEWER_PORT=9999 \
  YD_BASEMAP_VECTOR_URL='https://example.test/vec/{z}/{x}/{y}.png?tk=SECRET&q="quote"\slash' \
  YD_BASEMAP_VECTOR_ANNOTATION_URL=$'https://example.test/cva/{z}/{x}/{y}.png?tk=SECRET&nl=\nend' \
  YD_BASEMAP_SATELLITE_URL='https://example.test/img/{z}/{x}/{y}.png?tk=SECRET' \
  YD_BASEMAP_TERRAIN_ANNOTATION_URL='https://example.test/cta/{z}/{x}/{y}.png?tk=SECRET'
if [ "$status" -ne 0 ]; then
  echo "vector/satellite case exited $status" >&2
  cat "$OUT" >&2
  exit 1
fi
assert_secret_absent "$OUT"
if [ ! -f "$LAUNCH_LOG" ]; then
  echo "uvicorn sentinel did not run" >&2
  cat "$OUT" >&2
  exit 1
fi
assert_exec "$LAUNCH_LOG" "$launched"
assert_json_equal "$STATIC_DIR/basemaps.json" "$VECTOR_SATELLITE_EXPECTED"
assert_mode_664 "$STATIC_DIR/basemaps.json"

# proxy mode: key set -> six relative paths, URL envs ignored
clear_basemap_env
run_case \
  YD_TIANDITU_KEY='SECRET' \
  YD_BASEMAP_VECTOR_URL='https://example.test/vec/{z}/{x}/{y}.png?tk=SECRET' \
  YD_BASEMAP_VECTOR_ANNOTATION_URL='https://example.test/cva/{z}/{x}/{y}.png?tk=SECRET' \
  YD_BASEMAP_SATELLITE_URL='https://example.test/img/{z}/{x}/{y}.png?tk=SECRET'
if [ "$status" -ne 0 ]; then
  echo "proxy case exited $status" >&2
  cat "$OUT" >&2
  exit 1
fi
assert_secret_absent "$OUT"
assert_json_equal "$STATIC_DIR/basemaps.json" "$PROXY_EXPECTED"
assert_mode_664 "$STATIC_DIR/basemaps.json"
assert_exec "$LAUNCH_LOG" "$launched"

# empty env -> {}
clear_basemap_env
run_case
if [ "$status" -ne 0 ]; then
  echo "empty case exited $status" >&2
  cat "$OUT" >&2
  exit 1
fi
assert_json_equal "$STATIC_DIR/basemaps.json" '{}'
assert_exec "$LAUNCH_LOG" "$launched"

# annotation-only: no base key
clear_basemap_env
run_case YD_BASEMAP_VECTOR_ANNOTATION_URL='https://example.test/cva/only.png?tk=SECRET'
if [ "$status" -ne 0 ]; then
  echo "annotation-only case exited $status" >&2
  cat "$OUT" >&2
  exit 1
fi
assert_secret_absent "$OUT"
assert_json_equal "$STATIC_DIR/basemaps.json" '{}'
assert_exec "$LAUNCH_LOG" "$launched"

# empty-string bases ignored
clear_basemap_env
run_case \
  YD_BASEMAP_VECTOR_URL='' \
  YD_BASEMAP_SATELLITE_URL='' \
  YD_BASEMAP_TERRAIN_URL='' \
  YD_BASEMAP_VECTOR_ANNOTATION_URL='https://example.test/cva/empty-base.png?tk=SECRET'
if [ "$status" -ne 0 ]; then
  echo "empty-base case exited $status" >&2
  cat "$OUT" >&2
  exit 1
fi
assert_json_equal "$STATIC_DIR/basemaps.json" '{}'
assert_exec "$LAUNCH_LOG" "$launched"

# write failure: no exec
readonly_dir="$WORKDIR/readonly"
mkdir -p "$readonly_dir"
chmod a-w "$readonly_dir"
rm -f "$LAUNCH_LOG" "$OUT"
clear_basemap_env
set +e
env -u YD_TIANDITU_KEY \
  -u YD_BASEMAP_VECTOR_URL \
  -u YD_BASEMAP_SATELLITE_URL \
  -u YD_BASEMAP_TERRAIN_URL \
  -u YD_BASEMAP_VECTOR_ANNOTATION_URL \
  -u YD_BASEMAP_SATELLITE_ANNOTATION_URL \
  -u YD_BASEMAP_TERRAIN_ANNOTATION_URL \
  PATH="$WORKDIR:$PATH" \
  YD_VIEWER_STATIC_DIR="$readonly_dir" \
  YD_TEST_LAUNCH_LOG="$LAUNCH_LOG" \
  YD_BASEMAP_VECTOR_URL='https://example.test/vec.png?tk=SECRET' \
  "$ENTRYPOINT" \
  >"$OUT" 2>&1 &
fail_pid=$!
wait "$fail_pid"
fail_status=$?
set -e
if [ "$fail_status" -eq 0 ]; then
  echo "write-failure case exited 0" >&2
  cat "$OUT" >&2
  exit 1
fi
if [ -f "$LAUNCH_LOG" ]; then
  echo "uvicorn executed after static write failure" >&2
  exit 1
fi
assert_secret_absent "$OUT"

echo "test_entrypoint.sh: all cases passed"
