"""Fixed yd native filenames, layout, and parameter/index bytes (#207)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal


class NativeSupportError(ValueError):
    """Malformed or unsupported native BC/SS/LAKE/river-BC input."""


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
_ELEMENT_SUPPORT_COLUMNS = ("BC", "SS", "LAKE")
_RIVER_SUPPORT_COLUMN = "BC"
_RIVER_IDENTITY_FILENAMES = (
    PREPARED_VARIANT_MESH_FILENAME,
    PREPARED_VARIANT_RIV_FILENAME,
    PREPARED_VARIANT_RIVSEG_FILENAME,
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


def _column_index(header: list[str], name: str, path: Path) -> int:
    try:
        return header.index(name)
    except ValueError as exc:
        raise NativeSupportError(
            f"{path} is missing required column {name!r}; got {list(header)!r}"
        ) from exc


def _nonzero_token(token: str) -> bool:
    text = token.strip()
    if not text:
        return False
    try:
        return float(text) != 0.0
    except ValueError:
        return text not in {"0", "0.0", "+0", "-0"}


def _declared_count(line: str, path: Path, label: str) -> int:
    tokens = line.split()
    if not tokens:
        raise NativeSupportError(f"{path} is missing the {label} count header")
    try:
        return int(tokens[0])
    except ValueError as cop:
        raise NativeSupportError(
            f"{path} {label} count is not an integer: {tokens[0]!r}"
        ) from cop


def _read_utf8_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as cop:
        raise NativeSupportError(f"{path} could not be read: {cop}") from cop
    except UnicodeDecodeError as cop:
        raise NativeSupportError(f"{path} is not valid UTF-8") from cop


def reject_unsupported_native_physics(baseline_root: Path) -> None:
    """Reject nonzero element BC/SS/LAKE or river BC; classify parse errors."""
    att_path = Path(baseline_root) / PREPARED_VARIANT_SP_ATT_FILENAME
    if not att_path.is_file():
        raise NativeSupportError(f"baseline is missing {att_path}")
    att_lines = _read_utf8_lines(att_path)
    if len(att_lines) < 2:
        raise NativeSupportError(f"{att_path} is too short to inspect BC/SS/LAKE")
    n_rows = _declared_count(att_lines[0], att_path, "element")
    att_header = att_lines[1].split()
    att_indexes = {
        name: _column_index(att_header, name, att_path)
        for name in _ELEMENT_SUPPORT_COLUMNS
    }
    att_data = att_lines[2 : 2 + n_rows]
    if len(att_data) != n_rows:
        raise NativeSupportError(
            f"{att_path} declared {n_rows} rows but has {len(att_data)}"
        )
    for row_number, raw in enumerate(att_data, start=1):
        tokens = raw.split()
        if len(tokens) < len(att_header):
            raise NativeSupportError(
                f"{att_path} row {row_number} is shorter than the declared schema"
            )
        for name, index in att_indexes.items():
            if _nonzero_token(tokens[index]):
                raise NativeSupportError(
                    "unsupported model input: nonzero element "
                    f"{name}={tokens[index]!r} in {att_path} row {row_number}"
                )

    riv_path = Path(baseline_root) / PREPARED_VARIANT_RIV_FILENAME
    if not riv_path.is_file():
        raise NativeSupportError(f"baseline is missing {riv_path}")
    riv_lines = _read_utf8_lines(riv_path)
    if len(riv_lines) < 2:
        raise NativeSupportError(f"{riv_path} is too short to inspect river BC")
    n_reaches = _declared_count(riv_lines[0], riv_path, "river")
    riv_header = riv_lines[1].split()
    bc_index = _column_index(riv_header, _RIVER_SUPPORT_COLUMN, riv_path)
    riv_data = riv_lines[2 : 2 + n_reaches]
    if len(riv_data) != n_reaches:
        raise NativeSupportError(
            f"{riv_path} declared {n_reaches} river rows but has {len(riv_data)}"
        )
    for row_number, raw in enumerate(riv_data, start=1):
        tokens = raw.split()
        if len(tokens) < len(riv_header):
            raise NativeSupportError(
                f"{riv_path} row {row_number} is shorter than the declared schema"
            )
        if _nonzero_token(tokens[bc_index]):
            raise NativeSupportError(
                "unsupported model input: nonzero river "
                f"BC={tokens[bc_index]!r} in {riv_path} row {row_number}"
            )
