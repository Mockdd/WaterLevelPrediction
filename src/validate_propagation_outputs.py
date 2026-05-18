"""수계간(dtw_check) / 수계 내(watershed4) 전파 CSV 검증.

실행 (저장소 루트):
    python src/validate_propagation_outputs.py

출력: stdout + ``output/DTW/propagation/qa/validation_report.txt``
"""
from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_BASE = ROOT / "output" / "DTW"
PROP_WS = OUT_BASE / "propagation" / "watershed4"
PROP_STREAM = OUT_BASE / "propagation" / "stream_adjacent"
PROP_QA = OUT_BASE / "propagation" / "qa"
REPORT_PATH = PROP_QA / "validation_report.txt"
# run_dtw와 동일
HOURS_MIN = -72
HOURS_MAX = 120


def _report(lines: list[str], buf: StringIO) -> None:
    for ln in lines:
        print(ln)
        buf.write(ln + "\n")


def _check_schema_bounds(
    name: str,
    df: pd.DataFrame,
    required: list[str],
    n_events_max: int,
    max_lag_abs: float,
    buf: StringIO,
) -> bool:
    ok = True
    miss = [c for c in required if c not in df.columns]
    if miss:
        _report([f"[{name}] FAIL: 누락 컬럼 {miss}"], buf)
        ok = False
    if df.empty:
        _report([f"[{name}] SKIP: 빈 테이블"], buf)
        return ok

    bad_ne = (df["n_events"] > n_events_max).sum()
    if bad_ne:
        _report(
            [f"[{name}] FAIL: n_events > 이벤트 수({n_events_max}) 인 행 {bad_ne}개"],
            buf,
        )
        ok = False

    if "mean_lag_h" in df.columns:
        lag_bad = df["mean_lag_h"].abs() > max_lag_abs + 1e-6
        nlb = int(lag_bad.sum())
        if nlb:
            _report(
                [
                    f"[{name}] WARN: |mean_lag_h| > {max_lag_abs} 인 행 {nlb}개 "
                    "(교차상관 이론상 가능하나 윈도 밖이면 점검)",
                ],
                buf,
            )

    if "mean_dtw_dist" in df.columns:
        dtw_bad = ~np.isfinite(df["mean_dtw_dist"]) | (df["mean_dtw_dist"] < -1e-6)
        nd = int(dtw_bad.sum())
        if nd:
            _report([f"[{name}] FAIL: mean_dtw_dist 비정상 행 {nd}개"], buf)
            ok = False

    if ok and not miss:
        _report([f"[{name}] OK: 스키마·범위 기본 점검 통과"], buf)
    return ok


def _roundtrip_dtw_check(buf: StringIO) -> bool:
    sys.path.insert(0, str(ROOT / "src"))
    from run_dtw import (  # noqa: PLC0415
        compute_propagation_lag_dtw_check_notebook,
        load_obs_meta_dtw_check,
    )

    path = PROP_STREAM / "propagation.csv"
    if not path.exists():
        _report(["[roundtrip] SKIP: propagation/stream_adjacent/propagation.csv 없음"], buf)
        return True

    df_disk = pd.read_csv(path, dtype=str)
    for c in df_disk.columns:
        if c not in (
            "sphereLarge",
            "korStream_x",
            "obscd_upstream",
            "korObs_upstream",
            "obscd_downstream",
            "korObs_downstream",
        ):
            df_disk[c] = pd.to_numeric(df_disk[c], errors="coerce")

    df_fresh = compute_propagation_lag_dtw_check_notebook(load_obs_meta_dtw_check())
    if df_fresh.empty and df_disk.empty:
        _report(["[roundtrip] OK: 둘 다 빈 결과"], buf)
        return True

    cols = [c for c in df_fresh.columns if c in df_disk.columns]
    a = df_fresh[cols].sort_values(cols).reset_index(drop=True)
    b = df_disk[cols].sort_values(cols).reset_index(drop=True)
    if len(a) != len(b):
        _report(
            [
                f"[roundtrip] FAIL: 행 수 불일치 fresh={len(a)} disk={len(b)}",
            ],
            buf,
        )
        return False

    num_cols = [
        c
        for c in cols
        if c
        not in (
            "sphereLarge",
            "korStream_x",
            "obscd_upstream",
            "korObs_upstream",
            "obscd_downstream",
            "korObs_downstream",
        )
    ]
    ok = True
    for c in num_cols:
        diff = (a[c] - b[c]).abs()
        tol = 0.05 if "dtw" in c else 0.02
        worst = float(diff.max()) if len(diff) else 0.0
        if worst > tol:
            _report([f"[roundtrip] FAIL: 컬럼 '{c}' 최대 차 {worst} (허용 {tol})"], buf)
            ok = False
    if ok:
        _report(["[roundtrip] OK: 재계산 결과와 CSV 수치 일치 (허용 오차 내)"], buf)
    return ok


def _elevation_order(buf: StringIO) -> bool:
    sys.path.insert(0, str(ROOT / "src"))
    from run_dtw import load_obs_meta_dtw_check  # noqa: PLC0415

    path = PROP_STREAM / "propagation.csv"
    if not path.exists():
        _report(["[elevation] SKIP: propagation/stream_adjacent/propagation.csv 없음"], buf)
        return True

    meta = load_obs_meta_dtw_check().set_index("obscd")["water_elevation"]
    df = pd.read_csv(path, dtype=str)
    el_u = df["obscd_upstream"].astype(str).map(meta)
    el_d = df["obscd_downstream"].astype(str).map(meta)
    mask = el_u.notna() & el_d.notna()
    if not mask.any():
        _report(["[elevation] WARN: gdt 비어 비교 불가"], buf)
        return True

    viol = int((el_u[mask] + 1e-6 < el_d[mask]).sum())
    n = int(mask.sum())
    if viol:
        sub = df.loc[mask & (el_u + 1e-6 < el_d), [
            "korStream_x",
            "obscd_upstream",
            "obscd_downstream",
            "mean_lag_h",
        ]].head(15)
        _report(
            [
                f"[elevation] WARN: 상류 gdt < 하류 gdt 인 쌍 {viol}/{n} (지천·동일고·메타 오차 가능)",
                sub.to_string(index=False),
            ],
            buf,
        )
        return True

    _report([f"[elevation] OK: gdt 있는 {n}쌍 모두 상류 ≥ 하류"], buf)
    return True


def main() -> int:
    buf = StringIO()
    n_parquet = len(list((ROOT / "output" / "DTW" / "windows").glob("*_wl.parquet")))
    series_len = HOURS_MAX - HOURS_MIN + 1
    max_lag = float(series_len - 1)

    _report(
        [
            "=" * 60,
            "전파 결과 검증 (3종)",
            f"  이벤트 parquet 개수: {n_parquet}",
            f"  시계열 길이(시간): {series_len} → |lag| 상한 참고 {max_lag}",
            "=" * 60,
        ],
        buf,
    )

    # --- 1) 스키마·범위 ---
    p_nb = PROP_STREAM / "propagation.csv"
    p_ws = PROP_WS / "propagation.csv"
    if p_nb.exists():
        df_nb = pd.read_csv(p_nb)
        _check_schema_bounds(
            "dtw_check_stream",
            df_nb,
            [
                "sphereLarge",
                "korStream_x",
                "obscd_upstream",
                "obscd_downstream",
                "mean_lag_h",
                "mean_dtw_dist",
                "n_events",
            ],
            n_parquet,
            max_lag,
            buf,
        )
    else:
        _report(["[dtw_check_stream] SKIP: 파일 없음"], buf)

    if p_ws.exists():
        df_ws = pd.read_csv(p_ws)
        _check_schema_bounds(
            "watershed4",
            df_ws,
            [
                "watershed4",
                "obscd_a",
                "obscd_b",
                "mean_lag_h",
                "mean_dtw_dist",
                "n_events",
            ],
            n_parquet,
            max_lag,
            buf,
        )
    else:
        _report(["[watershed4] SKIP: propagation/watershed4/propagation.csv 없음"], buf)

    # --- 2) 라운드트립 ---
    _roundtrip_dtw_check(buf)

    # --- 3) 고도 순서 ---
    _elevation_order(buf)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(buf.getvalue(), encoding="utf-8")
    print(f"\n저장: {REPORT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
