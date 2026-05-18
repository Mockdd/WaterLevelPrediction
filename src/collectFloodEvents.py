"""Parses raw flood-warning text and cleans issuance / release pairs.

Pipeline (run via main()):
  1. Read 홍수정보시스템.txt and parse into structured rows.
  2. Clean incomplete issuance-release pairs per station point.
  3. Write the cleaned result to floodWarn20242025Parsed.csv.

Cleaning rules:
  - 변경 / 해제 with no preceding 발령 → removed.
  - 발령 with no subsequent 해제 → removed.
  - Consecutive 발령 where the new one is 경보 and the open cycle is 주의보
    → treated as an upgrade; action rewritten to '변경'.
  - Any other consecutive 발령 → earlier cycle discarded.

Typical usage:
    python collectFloodEvents.py
"""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from utils import LEVEL_RANK, WARN_PARSED_PATH, WARN_RAW_PATH

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KIND_LABEL: dict[str, str] = {"0": "주의보", "1": "경보", "3": "변경"}

WARN_FIELDNAMES: list[str] = [
    "record_no",
    "date",
    "datetime",
    "forecast_no",
    "point",
    "warn_level",
    "action",
    "raw_action",
    "wlobscd",
    "kind",
    "url",
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class CleanResult(NamedTuple):
    """Outcome of the pair-cleaning step.

    Attributes:
        kept: Rows that form complete issuance-release cycles.
        removed: Rows that were discarded as orphaned or incomplete.
        upgraded: Rows whose action was changed from 발령 to 변경 (escalation).
    """

    kept: list[dict[str, str]]
    removed: list[dict[str, str]]
    upgraded: list[dict[str, str]]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_warn_url(url: str) -> tuple[str, str]:
    """Extracts wlobscd and kind query parameters from a floodmap URL.

    Args:
        url: URL such as 'https://w.floodmap.go.kr?wlobscd=1013655&kind=0'.

    Returns:
        Tuple (wlobscd, kind) as strings; empty string when absent.
    """
    m1 = re.search(r"[?&]wlobscd=(\d+)", url)
    m2 = re.search(r"[?&]kind=(\d+)", url)
    return (m1.group(1) if m1 else ""), (m2.group(1) if m2 else "")


def parse_warn_text(text_path: Path = WARN_RAW_PATH) -> list[dict[str, str]]:
    """Parses the raw flood-warning text file into structured row dicts.

    The file format repeats blocks structured as:
      <record_number>
      <datetime>
      <forecast_no>
      <point> 지점 <raw_action>
      <url or dash>
      ...

    Args:
        text_path: Path to 홍수정보시스템.txt.

    Returns:
        List of row dicts with keys matching WARN_FIELDNAMES.

    Raises:
        FileNotFoundError: If text_path does not exist.
    """
    lines = [
        ln.rstrip()
        for ln in text_path.read_text(encoding="utf-8", errors="replace").splitlines()
    ]

    out_rows: list[dict[str, str]] = []
    i = 0

    while i < len(lines):
        ln = lines[i].strip()

        if not ln:
            i += 1
            continue

        # A pure integer marks the start of a new record.
        if not re.fullmatch(r"\d+", ln):
            i += 1
            continue

        rec_no = ln

        # Advance to datetime
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        dt = lines[j].strip() if j < len(lines) else ""

        # Advance to forecast_no
        j += 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        forecast_no = lines[j].strip() if j < len(lines) else ""

        j += 1
        items: list[str] = []
        urls: list[str] = []

        while j < len(lines):
            cur = lines[j].strip()
            if not cur:
                j += 1
                continue
            if re.fullmatch(r"\d+", cur):
                break
            if cur == "-":
                urls.append("")
                j += 1
                continue
            if cur.startswith("http"):
                urls.append(cur)
                j += 1
                continue
            items.append(cur)
            j += 1

        i = j

        for idx, item in enumerate(items):
            url = urls[idx] if idx < len(urls) else ""
            wlobscd, kind = _parse_warn_url(url) if url else ("", "")

            m = re.match(r"(.+?)\s+지점\s+(.+)$", item)
            point = m.group(1).strip() if m else item
            raw_action = m.group(2).strip() if m else ""

            if "해제" in raw_action:
                action = "해제"
            elif "변경" in raw_action:
                action = "변경"
            elif "발령" in raw_action:
                action = "발령"
            else:
                action = raw_action

            if "경보" in raw_action and "주의보" not in raw_action:
                warn_level = "경보"
            elif "주의보" in raw_action:
                warn_level = "주의보"
            else:
                warn_level = _KIND_LABEL.get(kind, "")

            out_rows.append(
                {
                    "record_no": rec_no,
                    "date": dt[:10],
                    "datetime": dt,
                    "forecast_no": forecast_no,
                    "point": point,
                    "warn_level": warn_level,
                    "action": action,
                    "raw_action": raw_action,
                    "wlobscd": wlobscd,
                    "kind": kind,
                    "url": url,
                }
            )

    return out_rows


# ---------------------------------------------------------------------------
# Pair cleaning
# ---------------------------------------------------------------------------


def _cycle_peak_level(cycle: list[dict[str, str]]) -> str:
    """Returns the highest warn_level observed in the current open cycle.

    Args:
        cycle: List of row dicts belonging to the same issuance cycle.

    Returns:
        '경보' if any row in the cycle is at 경보 level, else '주의보'.
    """
    max_rank = max(LEVEL_RANK.get(r["warn_level"], -1) for r in cycle)
    return "경보" if max_rank >= 1 else "주의보"


def clean_warn_pairs(rows: list[dict[str, str]]) -> CleanResult:
    """Removes rows that do not belong to a complete 발령-[변경]-해제 cycle.

    Each row must carry an '_idx' key (integer index in the original list)
    so that the kept set can be reconstructed in original order.

    Args:
        rows: Parsed warn rows, each containing an '_idx' key.

    Returns:
        CleanResult with kept, removed, and upgraded row lists.

    Raises:
        KeyError: If any row is missing the '_idx' key.
    """
    from collections import defaultdict

    by_point: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_point[row["point"]].append(row)

    keep_indices: set[int] = set()
    removed: list[dict[str, str]] = []
    upgraded: list[dict[str, str]] = []

    for _point, pts in by_point.items():
        pts_sorted = sorted(
            pts, key=lambda r: (r["datetime"], int(r["record_no"]))
        )
        current_cycle: list[dict[str, str]] = []

        for row in pts_sorted:
            action = row["action"]
            level = row["warn_level"]

            if action == "발령":
                if current_cycle:
                    peak = _cycle_peak_level(current_cycle)
                    if peak == "주의보" and level == "경보":
                        # Escalation recorded as 발령 → treat as 변경.
                        row["action"] = "변경"
                        row["raw_action"] = "홍수경보 변경(격상)"
                        current_cycle.append(row)
                        upgraded.append(row)
                    else:
                        removed.extend(current_cycle)
                        current_cycle = [row]
                else:
                    current_cycle = [row]

            elif action == "변경":
                if current_cycle:
                    current_cycle.append(row)
                else:
                    removed.append(row)

            elif action == "해제":
                if current_cycle:
                    current_cycle.append(row)
                    for r in current_cycle:
                        keep_indices.add(r["_idx"])
                    current_cycle = []
                else:
                    removed.append(row)

        removed.extend(current_cycle)

    kept = [r for r in rows if r["_idx"] in keep_indices]
    return CleanResult(kept=kept, removed=removed, upgraded=upgraded)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _write_warn_csv(
    rows: list[dict[str, str]],
    path: Path = WARN_PARSED_PATH,
) -> None:
    """Writes warn rows to a UTF-8-sig CSV, sorted newest first.

    Args:
        rows: Warn row dicts (must contain all WARN_FIELDNAMES keys).
        path: Output CSV path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_rows = sorted(
        rows,
        key=lambda r: (r["datetime"], int(r["record_no"])),
        reverse=True,
    )
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=WARN_FIELDNAMES, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(sorted_rows)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_clean_summary(result: CleanResult) -> None:
    """Prints a concise summary of the cleaning result to stdout.

    Args:
        result: CleanResult from clean_warn_pairs().
    """
    print(f"유지: {len(result.kept)}행")
    print(f"삭제: {len(result.removed)}행")
    print(f"격상 처리(발령→변경): {len(result.upgraded)}행")

    if result.removed:
        print("\n[삭제된 행]")
        for r in sorted(
            result.removed,
            key=lambda x: (x["datetime"], int(x["record_no"])),
        ):
            print(
                f"  record={r['record_no']:>3} | {r['datetime']} | "
                f"{r['point']} | {r['warn_level']} {r['action']} | "
                f"{r['forecast_no']}"
            )

    if result.upgraded:
        print("\n[격상 처리된 행 (발령→변경)]")
        for r in sorted(
            result.upgraded,
            key=lambda x: (x["datetime"], int(x["record_no"])),
        ):
            print(
                f"  record={r['record_no']:>3} | {r['datetime']} | "
                f"{r['point']} | 경보 변경(격상) | {r['forecast_no']}"
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Runs the full parse → clean → save pipeline."""
    # 1. Parse raw text
    raw_rows = parse_warn_text()
    by_action = Counter(r["action"] for r in raw_rows)
    issue_dates = sorted({r["date"] for r in raw_rows if r["action"] == "발령"})
    print(f"원본: {len(raw_rows)}행")
    print(
        f"발령={by_action['발령']} 해제={by_action['해제']} "
        f"변경={by_action['변경']}"
    )
    print(f"발령 날짜 ({len(issue_dates)}): {issue_dates}")

    # 2. Attach internal index for identity tracking
    for idx, row in enumerate(raw_rows):
        row["_idx"] = idx  # type: ignore[assignment]

    # 3. Clean pairs
    result = clean_warn_pairs(raw_rows)
    _print_clean_summary(result)

    # 4. Save
    _write_warn_csv(result.kept)
    print(f"\n저장 완료: {WARN_PARSED_PATH}")


if __name__ == "__main__":
    main()
