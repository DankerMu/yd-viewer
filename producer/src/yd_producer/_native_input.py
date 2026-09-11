"""Fixed yd native filenames, layout, and parameter/index bytes (#207)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

PREPARED_VARIANT_CALIBRATED_STATE_FILENAME = "yd.cfg.ic"
PREPARED_VARIANT_PARAMETER_FILENAME = "yd.cfg.para"
PREPARED_VARIANT_CALIB_FILENAME = "yd.cfg.calib"
PREPARED_VARIANT_MESH_FILENAME = "yd.sp.mesh"
PREPARED_VARIANT_SP_ATT_FILENAME = "yd.sp.att"
PREPARED_VARIANT_RIV_FILENAME = "yd.sp.riv"
PREPARED_VARIANT_RIVSEG_FILENAME = "yd.sp.rivseg"
PREPARED_VARIANT_LC_FILENAME = "yd.para.lc"
PREPARED_VARIANT_SOIL_FILENAME = "yd.para.soil"
PREPARED_VARIANT_GEOL_FILENAME = "yd.para.geol"
PREPARED_VARIANT_LAI_FILENAME = "yd.tsd.lai"
PREPARED_VARIANT_MF_FILENAME = "yd.tsd.mf"
PREPARED_VARIANT_BINDING_FILENAME = "yd.binding"
PREPARED_VARIANT_HANDOFF_FILENAME = "yd.direct-grid-handoff.json"
PREPARED_VARIANT_FORCING_INDEX_FILENAME = "yd.tsd.forc"

NATIVE_MODEL_FILENAMES = (
    PREPARED_VARIANT_CALIBRATED_STATE_FILENAME,
    PREPARED_VARIANT_PARAMETER_FILENAME,
    PREPARED_VARIANT_CALIB_FILENAME,
    PREPARED_VARIANT_MESH_FILENAME,
    PREPARED_VARIANT_SP_ATT_FILENAME,
    PREPARED_VARIANT_RIV_FILENAME,
    PREPARED_VARIANT_RIVSEG_FILENAME,
    PREPARED_VARIANT_LC_FILENAME,
    PREPARED_VARIANT_SOIL_FILENAME,
    PREPARED_VARIANT_GEOL_FILENAME,
    PREPARED_VARIANT_LAI_FILENAME,
    PREPARED_VARIANT_MF_FILENAME,
)
PREPARED_VARIANT_CHECKSUM_FILENAMES = frozenset(
    (*NATIVE_MODEL_FILENAMES, PREPARED_VARIANT_BINDING_FILENAME)
)
PREPARED_VARIANT_FIXED_FILENAMES = frozenset(
    (
        *NATIVE_MODEL_FILENAMES,
        PREPARED_VARIANT_BINDING_FILENAME,
        PREPARED_VARIANT_HANDOFF_FILENAME,
    )
)
PREPARED_VARIANT_ENTRY_COUNT = 14
STAGED_VARIANT_FILE_COUNT = 14
STAGED_FILE_COUNT = 15
NATIVE_INPUT_DIR = Path("input") / "yd"
NATIVE_INPUT_PARENT = Path("input")
NATIVE_FORCING_INDEX_PATH_LINE = "."

_ASSIGNMENT = re.compile(
    r"^(?P<prefix>[ \t]*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<equals>[ \t]*=[ \t]*)(?P<value>[^\r\n]*)(?P<ending>\r?\n)?$"
)
_NATIVE_LINE = re.compile(
    r"^(?P<prefix>[ \t]*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<space>[ \t]+)(?P<value>[^\r\n]*)(?P<ending>\r?\n)?$"
)
_PARAMETERS = {
    "START": "0",
    "END": "7",
    "DT_QR_DOWN": "60",
    "Update_IC_STEP": "720",
    "BINARY_OUTPUT": "1",
    "ASCII_OUTPUT": "0",
}
_PARAMETER_ALIASES = {key.casefold(): key for key in _PARAMETERS}


def native_run_paths(model_root: Path) -> dict[str, Path]:
    nested = model_root / NATIVE_INPUT_DIR
    return {
        "state_path": nested / PREPARED_VARIANT_CALIBRATED_STATE_FILENAME,
        "parameter_path": nested / PREPARED_VARIANT_PARAMETER_FILENAME,
        "forcing_index_path": nested / PREPARED_VARIANT_FORCING_INDEX_FILENAME,
    }


def is_native_run_directory(
    *,
    path: Path,
    state_path: Path,
    parameter_path: Path,
    forcing_index_path: Path,
) -> bool:
    expected = native_run_paths(path)
    return (
        state_path == expected["state_path"]
        and parameter_path == expected["parameter_path"]
        and forcing_index_path == expected["forcing_index_path"]
    )


def rewrite_forcing_index_path(content: bytes) -> bytes:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("SHUD index must be strict UTF-8.") from error
    lines = text.splitlines(keepends=True)
    if len(lines) < 2:
        raise ValueError("SHUD index must have a path line.")
    second = lines[1]
    ending = (
        "\r\n" if second.endswith("\r\n") else ("\n" if second.endswith("\n") else "")
    )
    lines[1] = f"{NATIVE_FORCING_INDEX_PATH_LINE}{ending}"
    return "".join(lines).encode("utf-8")


def render_shud_parameters(
    content: bytes,
    *,
    end: Literal["7", "0.5"] = "7",
    mode: Literal["legacy", "native"] = "legacy",
    max_bytes: int,
) -> bytes:
    if not isinstance(end, str) or end not in {"7", "0.5"}:
        raise ValueError("SHUD END must be '7' or '0.5'.")
    if mode not in {"legacy", "native"}:
        raise ValueError("SHUD parameter mode must be 'legacy' or 'native'.")
    if not isinstance(content, bytes):
        raise TypeError("Parameter content must be bytes.")
    if len(content) > max_bytes:
        raise ValueError(f"Parameter content exceeds {max_bytes} bytes.")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Parameter content must be strict UTF-8.") from error
    lines = text.splitlines(keepends=True)
    ending = next(
        (
            "\r\n" if line.endswith("\r\n") else "\n"
            for line in lines
            if line.endswith(("\n", "\r"))
        ),
        "\n",
    )
    if mode == "native":
        return _render_native(lines, end=end, ending=ending)
    return _render_legacy(lines, end=end, ending=ending)


def _render_legacy(lines: list[str], *, end: str, ending: str) -> bytes:
    result = list(lines)
    for key, value in (_PARAMETERS | {"END": end}).items():
        matches: list[tuple[int, str, re.Match[str]]] = []
        token = re.compile(rf"(?<![A-Za-z0-9_])\{{\{{{key}\}}\}}(?![A-Za-z0-9_])")
        shell = re.compile(rf"(?<![A-Za-z0-9_])\$\{{{key}\}}(?![A-Za-z0-9_])")
        for number, line in enumerate(result):
            visible = line.split("#", 1)[0]
            if not visible.strip():
                continue
            assignment = _ASSIGNMENT.fullmatch(line)
            if assignment is not None and assignment.group("key") == key:
                matches.append((number, "assignment", assignment))
                continue
            matches.extend((number, "token", item) for item in token.finditer(visible))
            matches.extend((number, "shell", item) for item in shell.finditer(visible))
        if len(matches) > 1:
            raise ValueError(
                f"SHUD parameter {key!r} has multiple authoritative occurrences."
            )
        if not matches:
            if result and not result[-1].endswith(("\n", "\r")):
                result[-1] += ending
            result.append(f"{key} = {value}{ending}")
            continue
        number, kind, match = matches[0]
        line = result[number]
        if kind == "assignment":
            old = match.group("value")
            comment = ""
            if "#" in old:
                before, after = old.split("#", 1)
                comment = before[len(before.rstrip(" \t")) :] + "#" + after
            result[number] = (
                f"{match.group('prefix')}{key}{match.group('equals')}{value}"
                f"{comment}{match.group('ending') or ''}"
            )
        else:
            result[number] = line[: match.start()] + value + line[match.end() :]
    return "".join(result).encode("utf-8")


def _render_native(lines: list[str], *, end: str, ending: str) -> bytes:
    result = list(lines)
    values = _PARAMETERS | {"END": end}
    seen: dict[str, int] = {}
    for number, line in enumerate(result):
        visible = line.split("#", 1)[0]
        if not visible.strip():
            continue
        native = _NATIVE_LINE.fullmatch(line)
        assignment = None if native is not None else _ASSIGNMENT.fullmatch(line)
        match = native or assignment
        if match is None:
            continue
        canonical = _PARAMETER_ALIASES.get(match.group("key").casefold())
        if canonical is None:
            continue
        if canonical in seen:
            raise ValueError(
                f"SHUD parameter {canonical!r} has multiple authoritative occurrences."
            )
        seen[canonical] = number
        result[number] = (
            f"{canonical}\t{values[canonical]}{match.group('ending') or ''}"
        )
    missing = [key for key in values if key not in seen]
    if missing:
        if result and not result[-1].endswith(("\n", "\r")):
            result[-1] += ending
        for key in missing:
            result.append(f"{key}\t{values[key]}{ending}")
    return "".join(result).encode("utf-8")
