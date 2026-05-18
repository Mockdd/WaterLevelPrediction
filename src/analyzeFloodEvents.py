"""Cross-analysis of flood events, warning records, and observation metadata.

Three analysis tasks are run sequentially by main():
  1. Cross-compare floodEventsHan20242025.csv vs floodWarn20242025Parsed.csv:
     identify mismatches, missing stations, and level discrepancies.
  2. Label every row of the parsed warning CSV with watershed / stream /
     designFlood metadata and write floodWarnLabelled.csv.
  3. Generate obsVsWarnJijaReport.md comparing designFlood-designated
     observation stations against the warning record.

Typical usage:
    python analyzeFloodEvents.py
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Optional

from utils import (
    EVENTS_WARN_REPORT_PATH,
    FLOOD_EVENTS_PATH,
    LEVEL_RANK,
    OBS_STREAM_REG_PATH,
    OBS_WARN_REPORT_PATH,
    WARN_LABELLED_PATH,
    WARN_PARSED_PATH,
    build_obs_lookups,
    build_point_wlobscd_map,
    design_flood_columns,
    find_obs_row,
    has_design_flood,
    label_warn_rows,
    norm_code,
    read_obs,
)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

WarnCycle = dict[str, str]  # keys: point, wlobscd, issue_date, end_date, peak_level


# ---------------------------------------------------------------------------
# Warning-cycle reconstruction
# ---------------------------------------------------------------------------


def build_warn_cycles(
    warn_rows: list[dict[str, str]],
) -> list[WarnCycle]:
    """Reconstructs 발령-[변경]-해제 cycles from parsed warning rows.

    Args:
        warn_rows: Rows from floodWarn20242025Parsed.csv.

    Returns:
        List of cycle dicts with keys:
          point, wlobscd, issue_date, end_date (may be absent), peak_level.
    """
    by_point: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in warn_rows:
        by_point[row["point"].strip()].append(row)

    cycles: list[WarnCycle] = []

    for point, rows in by_point.items():
        rows_sorted = sorted(rows, key=lambda r: r["datetime"])
        current: Optional[WarnCycle] = None

        for row in rows_sorted:
            action = row["action"]
            if action == "발령":
                current = {
                    "point": point,
                    "wlobscd": row.get("wlobscd", "").strip(),
                    "issue_date": row["date"],
                    "peak_level": row["warn_level"],
                }
            elif action == "변경" and current is not None:
                if (
                    LEVEL_RANK.get(row["warn_level"], 0)
                    > LEVEL_RANK.get(current["peak_level"], 0)
                ):
                    current["peak_level"] = row["warn_level"]
            elif action == "해제" and current is not None:
                current["end_date"] = row["date"]
                cycles.append(current)
                current = None

    return cycles


# ---------------------------------------------------------------------------
# Events vs warn cross-analysis
# ---------------------------------------------------------------------------


def _date_to_8digit(datetime_str: str) -> str:
    """Converts 'yyyy-mm-dd' or 'yyyyMMdd' to 8-digit 'yyyyMMdd'.

    Args:
        datetime_str: Date or datetime string.

    Returns:
        First 8 characters after removing hyphens.
    """
    return datetime_str.replace("-", "")[:8]


def analyze_events_vs_warn(
    events: list[dict[str, str]],
    warn_rows: list[dict[str, str]],
    obs_by_code: dict[str, dict[str, str]],
    design_cols: list[str],
) -> None:
    """Prints a cross-analysis report to stdout.

    Sections:
      1. Summary counts.
      2. Event stations absent from warn records.
      3. Warn-issued stations absent from events.
      4. Per-event warn-cycle match and level discrepancy notes.

    Args:
        events: Rows from floodEventsHan20242025.csv.
        warn_rows: Rows from floodWarn20242025Parsed.csv.
        obs_by_code: Mapping from normalised codeObs to obs row.
        design_cols: Column names carrying designFlood values.
    """
    cycles = build_warn_cycles(warn_rows)

    event_stations: dict[str, str] = {}
    for r in events:
        if r["station_code"] not in event_stations:
            event_stations[r["station_code"]] = r["korObs"]

    warn_issue_pts: set[str] = {
        r["point"].strip() for r in warn_rows if r["action"] == "발령"
    }
    warn_all_pts: set[str] = {r["point"].strip() for r in warn_rows}

    _SEP = "=" * 70

    # 1. Summary
    print(_SEP)
    print("1. 기록 기준 요약")
    print(_SEP)
    print(f"  flood_events : {len(events)}개 레코드, {len(event_stations)}개 지점")
    print(
        f"  floodWarn    : {len(warn_rows)}개 레코드, "
        f"{len(warn_all_pts)}개 고유지점, {len(cycles)}개 발령사이클"
    )

    # 2. Events-only stations (not in warn)
    print(f"\n{_SEP}")
    print("2. flood_events 지점 중 floodWarn 없는 지점")
    print(_SEP)
    events_only = [
        (code, name)
        for code, name in sorted(event_stations.items())
        if name not in warn_all_pts
    ]
    if events_only:
        for code, name in events_only:
            print(f"  {name} (code={code})")
    else:
        print("  없음 (전부 floodWarn에도 존재)")

    # 3. Warn-only stations (not in events)
    print(f"\n{_SEP}")
    print("3. floodWarn 발령 지점 중 flood_events 없는 지점")
    print(_SEP)
    event_names: set[str] = set(event_stations.values())
    for pt in sorted(warn_issue_pts):
        if pt in event_names:
            continue
        wlobscd = next(
            (
                r.get("wlobscd", "").strip()
                for r in warn_rows
                if r["point"].strip() == pt and r.get("wlobscd", "").strip()
            ),
            "",
        )
        obs = obs_by_code.get(norm_code(wlobscd)) if wlobscd else None
        in_design = (
            "Y"
            if obs and has_design_flood(obs, design_cols)
            else "N"
        )
        pt_cycles = [
            (c["issue_date"], c["peak_level"])
            for c in cycles
            if c["point"] == pt
        ]
        print(
            f"  {pt} | wlobscd={wlobscd} | designFlood={in_design} | "
            f"발령:{pt_cycles}"
        )

    # 4. Per-event detail
    print(f"\n{_SEP}")
    print("4. flood_events 레코드별 floodWarn 사이클 매칭 상세")
    print(_SEP)
    for r in sorted(events, key=lambda x: (x["event_date"], x["korObs"])):
        name = r["korObs"]
        date = r["event_date"]
        level_e = r["level"]
        wl_max = r["wl_max_m"]
        alt_m = r["alert_m"]

        matched = [
            c
            for c in cycles
            if c["point"] == name
            and _date_to_8digit(c["issue_date"]) <= date
            <= _date_to_8digit(c.get("end_date", "9999-12-31"))
        ]

        warn_level_str = (
            "/".join(c["peak_level"] for c in matched) if matched else "없음"
        )
        note = ""
        if matched:
            official_peak = max(
                LEVEL_RANK.get(c["peak_level"], 0) for c in matched
            )
            event_rank = 1 if level_e == "ALERT" else 0
            if official_peak > event_rank:
                note = (
                    f"[주의] 공식특보={matched[0]['peak_level']} > "
                    f"수위기반={level_e} (일별최대={wl_max}, alert기준={alt_m})"
                )
        print(
            f"  {date} {name:20s} | 수위레벨={level_e:8s} | "
            f"공식특보={warn_level_str} | {note}"
        )


# ---------------------------------------------------------------------------
# Labelled CSV output
# ---------------------------------------------------------------------------


def write_labelled_csv(
    warn_rows: list[dict[str, str]],
    base_fieldnames: list[str],
    obs_by_name: dict[str, dict[str, str]],
    obs_by_code: dict[str, dict[str, str]],
    design_cols: list[str],
    out_path: Path = WARN_LABELLED_PATH,
) -> None:
    """Labels warn rows with metadata and writes the result to CSV.

    Args:
        warn_rows: Rows from the parsed warning CSV.
        base_fieldnames: Original CSV fieldnames.
        obs_by_name: Lookup from korObs to obs row.
        obs_by_code: Lookup from normalised codeObs to obs row.
        design_cols: Columns that carry designFlood values.
        out_path: Destination CSV path.
    """
    labelled, fieldnames = label_warn_rows(
        warn_rows, base_fieldnames, obs_by_name, obs_by_code, design_cols
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=fieldnames, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(labelled)
    print(f"  {out_path.name}: {len(labelled)}행 저장")


# ---------------------------------------------------------------------------
# Obs vs warn Markdown report
# ---------------------------------------------------------------------------


def write_obs_warn_report(
    obs_rows: list[dict[str, str]],
    obs_by_name: dict[str, dict[str, str]],
    obs_by_code: dict[str, dict[str, str]],
    design_cols: list[str],
    warn_rows: list[dict[str, str]],
    out_path: Path = OBS_WARN_REPORT_PATH,
) -> None:
    """Generates a Markdown report comparing obsFinal vs warning records.

    The report contains:
      - designFlood stations WITH a warning history.
      - designFlood stations WITHOUT a warning history.
      - Per-watershed table of all warning points.
      - Stations present in warnings but absent from obsFinal.

    Args:
        obs_rows: All rows from obsFinal.csv.
        obs_by_name: Lookup from korObs to obs row.
        obs_by_code: Lookup from normalised codeObs to obs row.
        design_cols: Columns that carry designFlood values.
        warn_rows: Rows from the parsed warning CSV.
        out_path: Destination Markdown file path.
    """
    obs_design = {
        name: row
        for name, row in obs_by_name.items()
        if has_design_flood(row, design_cols)
    }

    point_wlobscd = build_point_wlobscd_map(warn_rows)
    warn_points = sorted(point_wlobscd)

    # Build per-point metadata
    point_meta: dict[str, dict[str, str]] = {}
    for pt in warn_points:
        wlobscd = point_wlobscd[pt]
        obs = find_obs_row(pt, wlobscd, obs_by_name, obs_by_code)
        if obs:
            point_meta[pt] = {
                "wlobscd": wlobscd,
                "codeObs": norm_code(obs["codeObs"]),
                "sphereLarge": obs.get("sphereLarge", "").strip(),
                "codeWatershed": obs.get("codeWatershed", "").strip(),
                "korStream": obs.get("korStream_x", "").strip(),
                "in_design": "Y" if has_design_flood(obs, design_cols) else "N",
            }
        else:
            point_meta[pt] = {
                "wlobscd": wlobscd,
                "codeObs": "",
                "sphereLarge": "",
                "codeWatershed": "",
                "korStream": "",
                "in_design": "N(미등록)",
            }

    warned_codes: set[str] = {
        point_meta[pt]["codeObs"]
        for pt in warn_points
        if point_meta[pt]["codeObs"]
    }
    not_warned = {
        name: row
        for name, row in sorted(obs_design.items())
        if norm_code(row["codeObs"]) not in warned_codes
    }

    by_sphere: dict[str, list[str]] = defaultdict(list)
    for pt in warn_points:
        sp = point_meta[pt]["sphereLarge"] or "미확인"
        by_sphere[sp].append(pt)

    lines: list[str] = []
    lines += [
        "# obsFinal.csv vs floodWarnJija_2024_2025 비교 분석 보고서\n",
        "- 분석 대상 기간: 2024 ~ 2025년\n",
        f"- obsFinal 전체 관측소: {len(obs_rows)}개\n",
        f"- obsFinal 중 designFlood 지정: {len(obs_design)}개\n",
        f"- floodWarnJija 고유 지점: {len(warn_points)}개\n",
        "",
        "---\n",
        "## (1) designFlood 지정 관측소의 특보 발령 이력 유무\n",
        f"### 특보 발령 이력 있음: {len(obs_design) - len(not_warned)}개\n",
        "",
        "| korObs | codeObs | 권역 | 수계코드 | 하천명 |",
        "|--------|---------|------|---------|-------|",
    ]
    for name, row in sorted(obs_design.items()):
        if norm_code(row["codeObs"]) in warned_codes:
            lines.append(
                f"| {name} | {norm_code(row['codeObs'])} "
                f"| {row.get('sphereLarge', '').strip()} "
                f"| {row.get('codeWatershed', '').strip()} "
                f"| {row.get('korStream_x', '').strip()} |"
            )
    lines += [
        "",
        f"### 특보 발령 이력 없음: {len(not_warned)}개\n",
        "",
        "| korObs | codeObs | 권역 | 수계코드 | 하천명 |",
        "|--------|---------|------|---------|-------|",
    ]
    for name, row in not_warned.items():
        lines.append(
            f"| {name} | {norm_code(row['codeObs'])} "
            f"| {row.get('sphereLarge', '').strip()} "
            f"| {row.get('codeWatershed', '').strip()} "
            f"| {row.get('korStream_x', '').strip()} |"
        )
    lines += ["", "---\n", "## (2)(3) floodWarnJija 지점별 수계·하천·권역\n", ""]

    for sp in sorted(by_sphere):
        pts = by_sphere[sp]
        lines += [
            f"### 권역: {sp} ({len(pts)}개)\n",
            "| 지점 | wlobscd | 수계코드 | 하천명 | designFlood |",
            "|------|---------|---------|-------|------------|",
        ]
        for pt in pts:
            m = point_meta[pt]
            lines.append(
                f"| {pt} | {m['wlobscd']} | {m['codeWatershed']} "
                f"| {m['korStream']} | {m['in_design']} |"
            )
        lines.append("")

    not_in_obs = [
        pt for pt in warn_points if point_meta[pt]["in_design"] == "N(미등록)"
    ]
    if not_in_obs:
        lines += [
            "---\n",
            "## obsFinal 미등록 지점 (designFlood 미지정)\n",
        ]
        for pt in not_in_obs:
            lines.append(f"- {pt} (wlobscd={point_meta[pt]['wlobscd']})")
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  {out_path.name} 저장")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Runs all three analysis tasks sequentially."""
    # Load shared data
    obs_rows = read_obs(OBS_STREAM_REG_PATH)
    d_cols = design_flood_columns(obs_rows)
    obs_by_name, obs_by_code = build_obs_lookups(obs_rows)

    warn_rows: list[dict[str, str]] = []
    warn_fieldnames: list[str] = []
    with WARN_PARSED_PATH.open(encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        warn_fieldnames = list(reader.fieldnames or [])
        warn_rows = list(reader)

    events: list[dict[str, str]] = []
    with FLOOD_EVENTS_PATH.open(encoding="utf-8-sig") as fh:
        events = list(csv.DictReader(fh))

    # ── Task 1: Cross-analysis ──────────────────────────────────────────────
    print("\n[Task 1] flood_events vs floodWarn 교차 분석")
    analyze_events_vs_warn(events, warn_rows, obs_by_code, d_cols)

    # ── Task 2: Labelled CSV ────────────────────────────────────────────────
    print("\n[Task 2] 라벨링 CSV 저장")
    write_labelled_csv(
        warn_rows, warn_fieldnames, obs_by_name, obs_by_code, d_cols
    )

    # ── Task 3: Obs vs warn Markdown report ─────────────────────────────────
    print("\n[Task 3] obsFinal vs floodWarn 보고서 저장")
    write_obs_warn_report(
        obs_rows, obs_by_name, obs_by_code, d_cols, warn_rows
    )


if __name__ == "__main__":
    main()
