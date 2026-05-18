"""Extract flood event peaks and DTW analysis windows from S3.

Pipeline:
  S3 parquet (waterlevel)
    → for each event_date: find per-station peak within ±2 days
    → extract -3~+5 day window around peak
    → min-max normalize within window
    → exclude stations with >30% missing
    → save peaks.csv + per-event window parquets

Event dates (user-specified):
  2024: 2024-07-18, 2024-07-23, 2024-07-24, 2024-07-25
  2025: 2025-08-14

Usage:
    python src/extract_peaks.py

Outputs (output/DTW/):
    peaks.csv                              peak metadata (station × event)
    windows/{event_date}_wl.parquet        normalized time series per event
"""

from __future__ import annotations

import io
import os
from datetime import timedelta
from pathlib import Path

import boto3
import pandas as pd
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR  = ROOT / "output" / "DTW"
WIN_DIR  = OUT_DIR / "windows"
OUT_DIR.mkdir(parents=True, exist_ok=True)
WIN_DIR.mkdir(exist_ok=True)

COLLECTION_PERIODS = [
    (pd.Timestamp("2024-07-15"), pd.Timestamp("2024-07-29")),
    (pd.Timestamp("2025-08-12"), pd.Timestamp("2025-08-18")),
]

EVENT_DATES = [
    pd.Timestamp("2024-07-18"),
    pd.Timestamp("2024-07-23"),
    pd.Timestamp("2024-07-24"),
    pd.Timestamp("2024-07-25"),
    pd.Timestamp("2025-08-14"),
]

PEAK_SEARCH_DAYS = 2    # ±2일 이내에서 최고 수위 탐색
WINDOW_PRE_DAYS  = 3    # 피크 기준 이전 3일
WINDOW_POST_DAYS = 5    # 피크 기준 이후 5일
MISSING_THRESHOLD = 0.30  # 결측률 30% 초과 시 제외

EXPECTED_HOURS = (WINDOW_PRE_DAYS + WINDOW_POST_DAYS) * 24 + 1  # 193 포인트


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

def make_s3_client() -> tuple:
    load_dotenv(ROOT / ".env")
    s3 = boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "ap-southeast-2"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_key", ""),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
    )
    bucket = os.getenv("S3_BUCKET", "")
    return s3, bucket


def _read_parquet_from_s3(s3, bucket: str, key: str) -> pd.DataFrame | None:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return pd.read_parquet(io.BytesIO(obj["Body"].read()))
    except s3.exceptions.NoSuchKey:
        return None
    except Exception as e:
        print(f"  [S3 오류] {key}: {e}")
        return None


def load_waterlevel(s3, bucket: str) -> pd.DataFrame:
    """Load all waterlevel parquets from S3 for both collection periods."""
    frames: list[pd.DataFrame] = []
    for pstart, pend in COLLECTION_PERIODS:
        current = pstart
        print(f"  {pstart.date()} ~ {pend.date()} 로드 중...")
        while current <= pend:
            key = (
                f"hrfco/raw/{current.year}/waterlevel"
                f"/date={current.date().isoformat()}/data.parquet"
            )
            df = _read_parquet_from_s3(s3, bucket, key)
            if df is not None:
                frames.append(df)
            current += timedelta(days=1)

    if not frames:
        return pd.DataFrame()

    df_all = pd.concat(frames, ignore_index=True)
    df_all["datetime"] = pd.to_datetime(df_all["datetime"])
    df_all["value"] = pd.to_numeric(df_all["value"], errors="coerce")
    return df_all.sort_values(["obscd", "datetime"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Peak extraction
# ---------------------------------------------------------------------------

def find_peaks(df_wl: pd.DataFrame) -> pd.DataFrame:
    """For each station × event, find peak_time within ±PEAK_SEARCH_DAYS."""
    stations = df_wl[["obscd", "korObs"]].drop_duplicates("obscd")
    records = []

    for event_dt in EVENT_DATES:
        search_start = event_dt - timedelta(days=PEAK_SEARCH_DAYS)
        search_end   = event_dt + timedelta(days=PEAK_SEARCH_DAYS)

        df_search = df_wl[
            (df_wl["datetime"] >= search_start) &
            (df_wl["datetime"] <= search_end)
        ]

        for _, sta in stations.iterrows():
            obscd   = sta["obscd"]
            kor_obs = sta["korObs"]
            df_sta  = df_search[df_search["obscd"] == obscd].dropna(subset=["value"])

            if df_sta.empty:
                records.append({
                    "event_date": event_dt.date(),
                    "obscd": obscd, "korObs": kor_obs,
                    "peak_time": pd.NaT, "peak_wl": float("nan"),
                    "has_peak": False,
                })
                continue

            peak_row = df_sta.loc[df_sta["value"].idxmax()]
            records.append({
                "event_date": event_dt.date(),
                "obscd": obscd, "korObs": kor_obs,
                "peak_time": peak_row["datetime"],
                "peak_wl": peak_row["value"],
                "has_peak": True,
            })

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Window extraction + normalization
# ---------------------------------------------------------------------------

def extract_windows(
    df_wl: pd.DataFrame,
    df_peaks: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Extract -WINDOW_PRE_DAYS ~ +WINDOW_POST_DAYS around each peak.
    Applies min-max normalization within the window.

    Returns:
        df_peaks   (updated with window stats + included flag)
        df_windows (long format: event_date, obscd, datetime,
                    hours_from_peak, wl_raw, wl_norm)
    """
    df_peaks = df_peaks.copy()
    for col in ["window_start", "window_end"]:
        df_peaks[col] = pd.NaT
    df_peaks["n_expected"]   = EXPECTED_HOURS
    df_peaks["n_actual"]     = 0
    df_peaks["missing_rate"] = 1.0
    df_peaks["included"]     = False

    window_frames: list[pd.DataFrame] = []

    for idx, peak in df_peaks[df_peaks["has_peak"]].iterrows():
        peak_time = pd.Timestamp(peak["peak_time"])
        win_start = peak_time - timedelta(days=WINDOW_PRE_DAYS)
        win_end   = peak_time + timedelta(days=WINDOW_POST_DAYS)

        df_peaks.at[idx, "window_start"] = win_start
        df_peaks.at[idx, "window_end"]   = win_end

        df_win = df_wl[
            (df_wl["obscd"] == peak["obscd"]) &
            (df_wl["datetime"] >= win_start) &
            (df_wl["datetime"] <= win_end)
        ].copy()

        n_actual     = int(df_win["value"].notna().sum())
        missing_rate = round(max(0.0, 1.0 - n_actual / EXPECTED_HOURS), 4)

        df_peaks.at[idx, "n_actual"]     = n_actual
        df_peaks.at[idx, "missing_rate"] = missing_rate
        df_peaks.at[idx, "included"]     = missing_rate <= MISSING_THRESHOLD

        if missing_rate > MISSING_THRESHOLD:
            continue

        # min-max normalization within window
        wl     = df_win["value"]
        wl_min = wl.min()
        wl_max = wl.max()
        df_win["wl_norm"] = (
            (wl - wl_min) / (wl_max - wl_min) if wl_max > wl_min else 0.0
        )
        df_win["wl_raw"] = wl
        df_win["hours_from_peak"] = (
            (df_win["datetime"] - peak_time).dt.total_seconds() / 3600
        ).round(0).astype(int)
        df_win["event_date"] = peak["event_date"]

        window_frames.append(
            df_win[["event_date", "obscd", "korObs",
                    "datetime", "hours_from_peak", "wl_raw", "wl_norm"]]
        )

    df_windows = (
        pd.concat(window_frames, ignore_index=True)
        if window_frames else pd.DataFrame()
    )
    return df_peaks, df_windows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    s3, bucket = make_s3_client()

    # 1. Load S3 data
    print("=" * 60)
    print("S3 수위 데이터 로드")
    print("=" * 60)
    df_wl = load_waterlevel(s3, bucket)
    if df_wl.empty:
        print("[오류] 데이터 없음. S3 버킷 및 경로를 확인하세요.")
        return
    print(
        f"→ 전체 {len(df_wl):,}행 / "
        f"관측소 {df_wl['obscd'].nunique()}개 / "
        f"기간 {df_wl['datetime'].min().date()} ~ {df_wl['datetime'].max().date()}"
    )

    # 2. Peak extraction
    print("\n피크 추출 (이벤트날 ±2일 내 최고 수위)")
    df_peaks = find_peaks(df_wl)
    n_peak = df_peaks["has_peak"].sum()
    n_nopeak = (~df_peaks["has_peak"]).sum()
    print(f"→ 피크 발견 {n_peak}개 / 데이터 없음 {n_nopeak}개")

    # 3. Window extraction + normalization
    print("\n윈도우 추출 (-3~+5일) 및 Min-Max 정규화")
    df_peaks, df_windows = extract_windows(df_wl, df_peaks)

    n_included = int(df_peaks["included"].sum())
    n_excl_miss = int((~df_peaks["included"] & df_peaks["has_peak"]).sum())
    print(f"→ DTW 포함: {n_included}개 / 결측 초과 제외: {n_excl_miss}개")

    # 4. Save peaks.csv
    peaks_path = OUT_DIR / "peaks.csv"
    df_peaks.to_csv(peaks_path, index=False, encoding="utf-8-sig")
    print(f"\n저장: {peaks_path.relative_to(ROOT)}")

    # 5. Save per-event window parquets
    if not df_windows.empty:
        for event_date, grp in df_windows.groupby("event_date"):
            out_path = WIN_DIR / f"{event_date}_wl.parquet"
            grp.to_parquet(out_path, index=False)
            n_sta = grp["obscd"].nunique()
            print(f"저장: output/DTW/windows/{event_date}_wl.parquet  "
                  f"({n_sta}개 관측소, {len(grp):,}행)")

    # 6. Summary
    print("\n" + "=" * 60)
    print("[이벤트별 포함 관측소 수]")
    summary = (
        df_peaks[df_peaks["included"]]
        .groupby("event_date")["obscd"]
        .count()
        .reset_index()
        .rename(columns={"obscd": "n_stations"})
    )
    print(summary.to_string(index=False))
    print("=" * 60)

    # 7. Missing rate distribution (간단 분포)
    has_peak = df_peaks[df_peaks["has_peak"]].copy()
    bins = [0, 0.05, 0.10, 0.20, 0.30, 0.50, 1.01]
    labels = ["0~5%", "5~10%", "10~20%", "20~30%", "30~50%", ">50%"]
    has_peak["miss_bin"] = pd.cut(has_peak["missing_rate"], bins=bins, labels=labels, right=False)
    print("\n[결측률 분포 (피크 있는 관측소만)]")
    print(has_peak["miss_bin"].value_counts().sort_index().to_string())


if __name__ == "__main__":
    run()
