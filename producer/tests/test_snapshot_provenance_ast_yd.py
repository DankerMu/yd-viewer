"""Issue #14 AST DB-free semantic discriminators."""

from __future__ import annotations

from pathlib import Path

from snapshot_provenance_fixtures import _forbidden_hits, _scan_files


def _targets() -> dict[str, str]:
    return {"producer/src/yd_producer/store/safe_fs.py": "packages/common/safe_fs.py"}


def _fake_repo(tmp_path: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_ast_guard_ignores_ordinary_names_messages_and_path_strings(
    tmp_path: Path,
) -> None:
    _fake_repo(
        tmp_path,
        {
            "producer/src/yd_producer/store/safe_fs.py": "x = 1\n",
            "producer/src/yd_producer/store/adapters.py": (
                'registry_manifest = "models/demo/registry.json"\n'
                'message = "forcing store registry lookup failed"\n'
                "mapping = dict(registry_manifest=registry_manifest)\n"
            ),
        },
    )

    assert not _forbidden_hits(tmp_path, _scan_files(tmp_path, _targets()))


def test_ast_guard_distinguishes_getenv_call_from_attribute_alias_and_environment_keys(
    tmp_path: Path,
) -> None:
    _fake_repo(
        tmp_path,
        {
            "producer/src/yd_producer/store/safe_fs.py": "x = 1\n",
            "producer/src/yd_producer/store/envs.py": (
                "import os\n"
                "alias = os.getenv\n"
                "bare_environment = os.environ\n"
                "one = os.getenv('DATABASE_URL')\n"
                "two = os.environ.get('DATABASE_URL')\n"
                "three = os.environ['DATABASE_URL']\n"
                "four = {'DATABASE_URL': 'ordinary'}\n"
                "five = call('DATABASE_URL')\n"
            ),
        },
    )

    assert _forbidden_hits(tmp_path, _scan_files(tmp_path, _targets())) == [
        "producer/src/yd_producer/store/envs.py:3: os.environ",
        "producer/src/yd_producer/store/envs.py:4: DATABASE_URL",
        "producer/src/yd_producer/store/envs.py:4: os.getenv",
        "producer/src/yd_producer/store/envs.py:5: DATABASE_URL",
        "producer/src/yd_producer/store/envs.py:5: os.environ",
        "producer/src/yd_producer/store/envs.py:6: DATABASE_URL",
        "producer/src/yd_producer/store/envs.py:6: os.environ",
    ]


def test_ast_guard_does_not_treat_from_app_import_name_as_module_path(
    tmp_path: Path,
) -> None:
    _fake_repo(
        tmp_path,
        {
            "producer/src/yd_producer/store/safe_fs.py": "x = 1\n",
            "producer/src/yd_producer/store/names.py": "from app import scheduler\n",
        },
    )

    assert not _forbidden_hits(tmp_path, _scan_files(tmp_path, _targets()))


def test_ast_guard_falls_back_to_raw_scan_for_unparseable_source(
    tmp_path: Path,
) -> None:
    _fake_repo(
        tmp_path,
        {
            "producer/src/yd_producer/store/safe_fs.py": "x = 1\n",
            "producer/src/yd_producer/store/unparseable.py": (
                'BROKEN = "unterminated\n'
                "from app.registry import models\n"
                "import scheduler\n"
            ),
        },
    )

    hits = _forbidden_hits(tmp_path, _scan_files(tmp_path, _targets()))

    assert {hit.split(":")[-1].strip() for hit in hits} == {"registry", "scheduler"}
