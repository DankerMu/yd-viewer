#!/bin/sh
set -eu
umask 002  # 与 NWM 共享的瓦片缓存须组可写：目录 775、文件 664（agent-ops §9.2）

UV_OFFLINE=1 UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never \
  uv run --offline --no-project --no-cache --no-python-downloads --python 3.12 python -c '
import json
import os
from pathlib import Path

static_dir = os.environ.get("YD_VIEWER_STATIC_DIR")
if not static_dir:
    raise SystemExit("YD_VIEWER_STATIC_DIR is required")

PROXY_LAYERS = (("vector", "vec", "cva"), ("satellite", "img", "cia"), ("terrain", "ter", "cta"))
PROXY_PATH = "api/basemap/tianditu/%s/{z}/{x}/{y}"

payload = {}
if os.environ.get("YD_TIANDITU_KEY"):
    for name, base_layer, annotation_layer in PROXY_LAYERS:
        payload[name] = {
            "tiles": [PROXY_PATH % base_layer],
            "annotation": [PROXY_PATH % annotation_layer],
        }
else:
    for name, _base_layer, _annotation_layer in PROXY_LAYERS:
        base = os.environ.get("YD_BASEMAP_%s_URL" % name.upper())
        if not base:
            continue
        annotation = os.environ.get("YD_BASEMAP_%s_ANNOTATION_URL" % name.upper())
        payload[name] = {
            "tiles": [base],
            "annotation": [annotation] if annotation else None,
        }

Path(static_dir, "basemaps.json").write_text(
    json.dumps(payload, ensure_ascii=False),
    encoding="utf-8",
)
'

uvicorn yd_viewer.app:create_app --factory --host 0.0.0.0 --port 8000
