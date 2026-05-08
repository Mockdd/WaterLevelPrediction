"""Shared utilities for flood-event data I/O, metadata lookup, and display.

This module provides:
  - Project-wide path constants.
  - obsFinal.csv reading with automatic encoding detection.
  - Station-code normalisation and designFlood column helpers.
  - Observation-row lookup by name or station code.
  - Warning-row wlobscd mapping builder.
  - Warn-row labelling with watershed / stream / designFlood metadata.
  - Threshold comparison table printer.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

ROOT: Path = Path(__file__).resolve().parents[1]

# Inputs
OBS_PATH: Path = ROOT / "metadata_outputs" / "obsFinal.csv"
OBS_STREAM_REG_PATH: Path = ROOT / "metadata_outputs" / "obsFinalStreamReg.csv"
WARN_RAW_PATH: Path = ROOT / "metadata" / "홍수정보시스템.txt"

# Outputs
DTW_DIR: Path = ROOT / "output" / "DTW"
WARN_PARSED_PATH: Path = DTW_DIR / "floodWarn20242025Parsed.csv"
WARN_LABELLED_PATH: Path = DTW_DIR / "floodWarnLabelled.csv"
FLOOD_EVENTS_PATH: Path = DTW_DIR / "floodEventsHan20242025.csv"
OBS_WARN_REPORT_PATH: Path = DTW_DIR / "obsVsWarnJijaReport.md"
EVENTS_WARN_REPORT_PATH: Path = DTW_DIR / "eventsVsWarnComparison.md"

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

LEVEL_RANK: dict[str, int] = {"주의보": 0, "경보": 1}

_OBS_ENCODINGS: tuple[str, ...] = ("euc-kr", "utf-8-sig", "cp949")
_DESIGN_FLOOD_PREFIX: str = "designFlood"
_INVALID_VALUES: frozenset[str] = frozenset({"", "-", "nan"})

# ---------------------------------------------------------------------------
# obsFinal helpers
# ---------------------------------------------------------------------------


def read_obs(path: Path = OBS_PATH) -> list[dict[str, str]]:
    """Reads obsFinal.csv, trying common Korean encodings in order.

    Args:
        path: Path to obsFinal.csv. Defaults to the project-level constant.

    Returns:
        List of row dicts from csv.DictReader.

    Raises:
        RuntimeError: If no encoding in _OBS_ENCODINGS can read the file.
    """
    for enc in _OBS_ENCODINGS:
        try:
            with path.open(encoding=enc) as fh:
                rows = list(csv.DictReader(fh))
            if rows:
                return rows
        except (UnicodeDecodeError, csv.Error):
            continue
    raise RuntimeError(
        f"Cannot read {path} with any of {_OBS_ENCODINGS}"
    )


def design_flood_columns(obs_rows: list[dict[str, str]]) -> list[str]:
    """Returns column names whose names start with 'designFlood'.

    Args:
        obs_rows: Rows loaded from obsFinal.csv.

    Returns:
        List of matching column name strings.
    """
    if not obs_rows:
        return []
    return [c for c in obs_rows[0] if c.startswith(_DESIGN_FLOOD_PREFIX)]


def has_design_flood(
    row: dict[str, str],
    design_cols: list[str],
) -> bool:
    """Returns True when at least one designFlood column has a valid value.

    Args:
        row: A single row from obsFinal.csv.
        design_cols: Column names to inspect (from design_flood_columns()).

    Returns:
        True if any column value is non-empty, non-dash, and non-'nan'.
    """
    return any(
        row.get(c, "").strip() not in _INVALID_VALUES for c in design_cols
    )


# ---------------------------------------------------------------------------
# Code normalisation
# ---------------------------------------------------------------------------


def norm_code(value: str) -> str:
    """Normalises a station-code string by stripping trailing '.0'.

    Args:
        value: Raw code string such as '1013655.0' or '1013655'.

    Returns:
        Integer-string representation, e.g. '1013655'.
        Returns the stripped input string if numeric conversion fails.
    """
    try:
        return str(int(float(value)))
    except (ValueError, TypeError):
        return str(value).strip()


# ---------------------------------------------------------------------------
# Observation-row lookup
# ---------------------------------------------------------------------------


def build_obs_lookups(
    obs_rows: list[dict[str, str]],
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Builds name-keyed and code-keyed lookup dicts for obs rows.

    Args:
        obs_rows: Rows loaded from obsFinal.csv.

    Returns:
        Tuple of (obs_by_name, obs_by_code) where
          obs_by_name maps korObs → row,
          obs_by_code maps normalised codeObs → row.
    """
    obs_by_name = {r["korObs"].strip(): r for r in obs_rows}
    obs_by_code = {norm_code(r["codeObs"]): r for r in obs_rows}
    return obs_by_name, obs_by_code


def find_obs_row(
    point: str,
    wlobscd: str,
    obs_by_name: dict[str, dict[str, str]],
    obs_by_code: dict[str, dict[str, str]],
) -> Optional[dict[str, str]]:
    """Looks up an observation row by display name or station code.

    Matching priority:
      1. Exact match on korObs.
      2. Normalised codeObs match via wlobscd.
      3. Partial string containment on korObs (fallback).

    Args:
        point: Station display name, e.g. '가평군(가평교)'.
        wlobscd: Station code string (may be empty).
        obs_by_name: Mapping from korObs to observation row.
        obs_by_code: Mapping from normalised codeObs to observation row.

    Returns:
        Matching row dict, or None if no match is found.
    """
    if point in obs_by_name:
        return obs_by_name[point]
    code = norm_code(wlobscd)
    if wlobscd and code in obs_by_code:
        return obs_by_code[code]
    for name, row in obs_by_name.items():
        if point in name or name in point:
            return row
    return None


# ---------------------------------------------------------------------------
# Warning-row helpers
# ---------------------------------------------------------------------------


def build_point_wlobscd_map(
    warn_rows: list[dict[str, str]],
) -> dict[str, str]:
    """Builds a point-name → first valid wlobscd mapping.

    Args:
        warn_rows: Rows from the parsed flood-warning CSV.

    Returns:
        Dict mapping point name to its wlobscd string.
    """
    mapping: dict[str, str] = {}
    for row in warn_rows:
        pt = row["point"].strip()
        code = row.get("wlobscd", "").strip()
        if pt not in mapping:
            mapping[pt] = code
        elif not mapping[pt] and code:
            mapping[pt] = code
    return mapping


def label_warn_rows(
    warn_rows: list[dict[str, str]],
    base_fieldnames: list[str],
    obs_by_name: dict[str, dict[str, str]],
    obs_by_code: dict[str, dict[str, str]],
    design_cols: list[str],
) -> tuple[list[dict[str, str]], list[str]]:
    """Appends watershed / stream / designFlood label columns to warn rows.

    For each row the function looks up the corresponding observation station
    and annotates sphereLarge, codeWatershed, korStream, and in_designFlood.

    Args:
        warn_rows: Rows from the parsed flood-warning CSV.
        base_fieldnames: Original CSV fieldnames list.
        obs_by_name: Lookup from korObs to obs row.
        obs_by_code: Lookup from normalised codeObs to obs row.
        design_cols: Column names that carry designFlood values.

    Returns:
        Tuple of (labelled_rows, extended_fieldnames).
    """
    extra = ["sphereLarge", "codeWatershed", "korStream", "in_designFlood"]
    point_wlobscd = build_point_wlobscd_map(warn_rows)
    out: list[dict[str, str]] = []

    for row in warn_rows:
        pt = row["point"].strip()
        wlobscd = point_wlobscd.get(pt, row.get("wlobscd", "").strip())
        obs = find_obs_row(pt, wlobscd, obs_by_name, obs_by_code)
        new_row = dict(row)
        if obs:
            new_row["sphereLarge"] = obs.get("sphereLarge", "").strip()
            new_row["codeWatershed"] = obs.get("codeWatershed", "").strip()
            new_row["korStream"] = obs.get("korStream_x", "").strip()
            new_row["in_designFlood"] = (
                "Y" if has_design_flood(obs, design_cols) else "N"
            )
        else:
            new_row["sphereLarge"] = ""
            new_row["codeWatershed"] = ""
            new_row["korStream"] = ""
            new_row["in_designFlood"] = "N(미등록)"
        out.append(new_row)

    return out, base_fieldnames + extra


# ---------------------------------------------------------------------------
# Display utilities
# ---------------------------------------------------------------------------


def print_threshold_table(
    obs_rows: list[dict[str, str]],
    station_names: list[str],
) -> None:
    """Prints advisory / alert / design-flood thresholds for selected stations.

    Args:
        obs_rows: Rows from obsFinal.csv.
        station_names: korObs values to include in the table.
    """
    header = (
        f"{'korObs':<22}  {'주의보(m)':>9}  {'경보(m)':>7}"
        f"  {'계획홍수위(m)':>12}  {'계획홍수량(m3/s)':>16}"
    )
    print(header)
    print("-" * 75)
    name_set = set(station_names)
    for row in obs_rows:
        if row.get("korObs", "").strip() not in name_set:
            continue
        print(
            f"{row['korObs'].strip():<22}  "
            f"{row.get('aFLAdvisory(m)', '').strip():>9}  "
            f"{row.get('aFLAlert(m)', '').strip():>7}  "
            f"{row.get('designFloodLevel(m)', '').strip():>12}  "
            f"{row.get('designFloodCharge(m3/sec)', '').strip():>16}"
        )
