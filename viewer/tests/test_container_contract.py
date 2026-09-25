"""compose.example.yml, env.example and Dockerfile keep the ops contract.

Text assertions on purpose: PyYAML is not a dependency of this project.
"""

from __future__ import annotations

import re
from pathlib import Path

VIEWER_DIR = Path(__file__).resolve().parent.parent
COMPOSE = (VIEWER_DIR / "compose.example.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (VIEWER_DIR / "env.example").read_text(encoding="utf-8")
DOCKERFILE = (VIEWER_DIR / "Dockerfile").read_text(encoding="utf-8")

# agent-ops §9.2: the full list of operator-overridable variables.
ENV_KEYS = (
    "YD_VIEWER_INPUT_DIR",
    "YD_VIEWER_OUTPUT_DIR",
    "YD_VIEWER_PORT",
    "YD_TIANDITU_KEY",
    "YD_BASEMAP_CACHE_DIR",
    "YD_BASEMAP_VECTOR_URL",
    "YD_BASEMAP_SATELLITE_URL",
    "YD_BASEMAP_TERRAIN_URL",
    "YD_BASEMAP_VECTOR_ANNOTATION_URL",
    "YD_BASEMAP_SATELLITE_ANNOTATION_URL",
    "YD_BASEMAP_TERRAIN_ANNOTATION_URL",
)


def _entries(text: str) -> list[str]:
    return [
        line.strip()[2:].strip().strip('"')
        for line in text.splitlines()
        if line.strip().startswith("- ")
    ]


def _block(text: str, key: str) -> list[str]:
    """Lines of the `key:` block, kept until the indentation drops back."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != f"{key}:":
            continue
        indent = len(line) - len(line.lstrip())
        collected = []
        for following in lines[index + 1 :]:
            if not following.strip():
                continue
            if len(following) - len(following.lstrip()) <= indent:
                break
            collected.append(following)
        return collected
    raise AssertionError(f"missing block: {key}")


def test_compose_mounts_two_read_only_binds_and_one_writable_cache_bind():
    mounts = _entries("\n".join(_block(COMPOSE, "volumes")))

    assert len(mounts) == 3
    assert all(mount.startswith("/") for mount in mounts)
    read_only = [mount for mount in mounts if mount.endswith(":ro")]
    writable = [mount for mount in mounts if not mount.endswith(":ro")]
    assert len(read_only) == 2
    assert len(writable) == 1
    assert writable[0].endswith(":/cache")
    assert "YD_ROOT" not in COMPOSE
    # agent-ops §16.2 holds the live paths; the example must stay synthetic.
    assert "/home/" not in COMPOSE


def test_compose_adds_exactly_one_placeholder_cache_group():
    groups = re.findall(r"^\s*group_add:\s*\[(.*)\]\s*$", COMPOSE, flags=re.MULTILINE)

    assert groups == ['"1000"']


def test_compose_declares_no_named_volume():
    assert re.search(r"^volumes:", COMPOSE, flags=re.MULTILINE) is None
    assert "yd-basemap-cache" not in COMPOSE


def test_compose_publishes_only_the_loopback_port():
    assert _entries("\n".join(_block(COMPOSE, "ports"))) == [
        "127.0.0.1:${YD_VIEWER_PORT}:8000"
    ]


def test_compose_names_all_objects_with_the_yd_prefix():
    named = re.findall(
        r"^\s*(?:name|container_name|image):\s*(\S+)", COMPOSE, flags=re.MULTILINE
    )
    services = _block(COMPOSE, "services")
    networks = _entries("\n".join(_block(COMPOSE, "networks")))
    assert named and all(value.startswith("yd-") for value in named)
    assert services[0].strip().rstrip(":").startswith("yd-")
    assert networks == ["yd-network"]


def test_env_example_lists_every_operator_key_without_values():
    assigned = dict(
        line.split("=", 1)
        for line in ENV_EXAMPLE.splitlines()
        if "=" in line and not line.startswith("#")
    )

    assert tuple(assigned) == ENV_KEYS
    assert assigned["YD_TIANDITU_KEY"] == ""
    assert assigned["YD_BASEMAP_CACHE_DIR"] == "/cache"
    assert all(assigned[key] == "" for key in ENV_KEYS if key.endswith("_URL"))
    assert "tianditu.gov.cn" not in ENV_EXAMPLE


def test_static_dir_is_not_operator_overridable():
    assert "YD_VIEWER_STATIC_DIR" not in ENV_EXAMPLE
    assert "YD_VIEWER_STATIC_DIR" not in COMPOSE
    assert "YD_VIEWER_STATIC_DIR=/app/static" in DOCKERFILE


def test_image_creates_the_cache_directory_for_the_run_user():
    creation = re.search(r"mkdir[^\n]*/cache", DOCKERFILE)
    ownership = re.search(r"chown\s+10001:10001\s+/cache", DOCKERFILE)

    assert creation is not None
    assert ownership is not None
    assert "USER 10001" in DOCKERFILE
