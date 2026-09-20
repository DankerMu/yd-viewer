#!/bin/sh
set -eu

UV_OFFLINE=1 UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never \
  uv run --offline --no-project --no-cache --no-python-downloads --python 3.12 python -c '
import json
import os
from pathlib import Path

static_dir = os.environ.get("YD_VIEWER_STATIC_DIR")
if not static_dir:
    raise SystemExit("YD_VIEWER_STATIC_DIR is required")

payload = {}
for name in ("vector", "satellite", "terrain"):
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

exec uvicorn yd_viewer.app:create_app --factory --host 0.0.0.0 --port 8000
