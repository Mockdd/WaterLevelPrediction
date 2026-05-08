"""Collect hourly waterlevel & rainfall from HRFCO API and upload to S3.

Data flow:
  obsTarget.csv (118 stations)
    → HRFCO API (waterlevel / rainfall, 1H interval, date range)
    → Parquet (gzip)
    → S3: s3://{bucket}/hrfco/raw/{year}/{dtype}/date={YYYY-MM-DD}/data.parquet

Collection periods:
  Period 1: 2024-07-16 ~ 2024-07-29
  Period 2: 2025-08-12 ~ 2025-08-18

Usage:
    python src/collect_hrfco_s3.py

Required .env keys:
    hrfco_token           = "..."   # HRFCO API key
    AWS_ACCESS_KEY_ID     = "..."   # IAM access key ID  (currently stored as AWS_key)
    AWS_SECRET_ACCESS_KEY = "..."   # IAM secret key     (must be added to .env)
    AWS_REGION            = "ap-southeast-2"
    S3_BUCKET             = "floodax-387362989633-ap-southeast-2-an"
"""

from __future__ import annotations

import csv
import io
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import boto3
import pandas as pd
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
OBS_TARGET_PATH = ROOT / "metadata_outputs" / "obsTarget.csv"

COLLECTION_PERIODS: list[tuple[date, date]] = [
    (date(2024, 7, 15), date(2024, 7, 29)),
    (date(2025, 8, 12), date(2025, 8, 18)),
]

DATA_TYPES: list[str] = ["waterlevel", "rainfall"]

HRFCO_BASE = "https://api.hrfco.go.kr"
INTERVAL = "1H"

# 초당 요청 제한 대응 (API rate-limit)
REQUEST_DELAY_SEC = 0.3
RETRY_COUNT = 3
RETRY_DELAY_SEC = 5


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def load_config() -> dict[str, str]:
    """Load credentials from .env and return as config dict."""
    load_dotenv(ROOT / ".env")

    # .env에서 키 이름이 다를 수 있어 fallback 포함
    cfg = {
        "hrfco_token": os.getenv("hrfco_token", "").strip().strip('"'),
        "aws_access_key": (
            os.getenv("AWS_ACCESS_KEY_ID")
            or os.getenv("AWS_key", "")
        ).strip().strip('"'),
        "aws_secret_key": os.getenv("AWS_SECRET_ACCESS_KEY", "").strip().strip('"'),
        "aws_region": os.getenv("AWS_REGION", "ap-southeast-2").strip().strip('"'),
        "s3_bucket": os.getenv("S3_BUCKET", "floodax-387362989633-ap-southeast-2-an").strip().strip('"'),
    }

    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise EnvironmentError(
            f"[오류] .env에 다음 항목이 없거나 비어 있습니다: {missing}\n"
            "  → .env에 AWS_SECRET_ACCESS_KEY 추가 필요"
        )
    return cfg


# ---------------------------------------------------------------------------
# Observation station list
# ---------------------------------------------------------------------------

def load_obs_target(path: Path = OBS_TARGET_PATH) -> pd.DataFrame:
    """Read obsTarget.csv and return DataFrame with codeObs as string."""
    df = pd.read_csv(path, index_col=0, dtype=str)
    df["codeObs"] = df["codeObs"].str.replace(r"\.0$", "", regex=True)
    print(f"[관측소] 총 {len(df)}개 로드: {path.name}")
    return df


# ---------------------------------------------------------------------------
# HRFCO API
# ---------------------------------------------------------------------------

def _fetch_hrfco(
    token: str,
    dtype: str,
    obscd: str,
    start_dt: str,
    end_dt: str,
) -> list[dict[str, Any]]:
    """Call HRFCO time-series API and return list of observation records.

    URL pattern (confirmed working via testing):
        GET https://api.hrfco.go.kr/{token}/{dtype}/list/{interval}/{obscd}/{startDt}/{endDt}.json
        e.g. .../waterlevel/list/1H/1006630/202407160000/202407292300.json

    Date format in path: YYYYMMDDHHMM (12 digits).
    Response ymdhm format: YYYYMMDDHH (10 digits) for 1H interval.

    Args:
        token:    HRFCO API key.
        dtype:    'waterlevel' or 'rainfall'.
        obscd:    Station code string (e.g. '1006630').
        start_dt: 'YYYYMMDD' string (will be padded to '0000').
        end_dt:   'YYYYMMDD' string (will be padded to '2300').

    Returns:
        List of record dicts from API response['content'], or [] on error.
    """
    url = (
        f"{HRFCO_BASE}/{token}/{dtype}/list/{INTERVAL}"
        f"/{obscd}/{start_dt}0000/{end_dt}2300.json"
    )

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, dict) and "code" in data and "content" not in data:
                print(f"    [API 오류 {data.get('code')}] {data.get('message', '')}")
                return []

            return data.get("content", [])

        except requests.exceptions.HTTPError:
            if resp.status_code == 404:
                return []
            print(f"    [HTTP {resp.status_code}] {obscd}/{dtype} (시도 {attempt}/{RETRY_COUNT})")
        except Exception as e:
            print(f"    [오류] {obscd}/{dtype}: {e} (시도 {attempt}/{RETRY_COUNT})")

        if attempt < RETRY_COUNT:
            time.sleep(RETRY_DELAY_SEC)

    return []


def fetch_period(
    token: str,
    dtype: str,
    obscd: str,
    obs_name: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Fetch all records for one station and one period.

    Returns DataFrame with columns: [obscd, korObs, dtype, datetime, value].
    Empty DataFrame if no data.
    """
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")

    records = _fetch_hrfco(token, dtype, obscd, start_str, end_str)
    time.sleep(REQUEST_DELAY_SEC)

    if not records:
        return pd.DataFrame()

    df = pd.json_normalize(records)

    # 수위: wlobscd / 강수량: rfobscd (필드명이 dtype마다 다름)
    value_col = "wl" if dtype == "waterlevel" else "rf"

    if "ymdhm" not in df.columns:
        print(f"    [경고] ymdhm 컬럼 없음: {obscd}/{dtype}, 컬럼={list(df.columns)}")
        return pd.DataFrame()

    df["obscd"] = obscd
    df["korObs"] = obs_name
    df["dtype"] = dtype
    df["value"] = pd.to_numeric(df.get(value_col), errors="coerce")

    # ymdhm 형식: 1H → YYYYMMDDHH (10자리), 10M → YYYYMMDDHHMM (12자리)
    ymdhm_len = df["ymdhm"].astype(str).str.len().max()
    if ymdhm_len <= 10:
        df["datetime"] = pd.to_datetime(df["ymdhm"].astype(str), format="%Y%m%d%H", errors="coerce")
    else:
        df["datetime"] = pd.to_datetime(df["ymdhm"].astype(str), format="%Y%m%d%H%M", errors="coerce")

    df = df.dropna(subset=["datetime"])
    df["date"] = df["datetime"].dt.date
    df = df.sort_values("datetime")

    return df[["obscd", "korObs", "dtype", "datetime", "date", "value"]]


# ---------------------------------------------------------------------------
# S3 upload
# ---------------------------------------------------------------------------

def build_s3_key(year: int, dtype: str, dt: date) -> str:
    """Build S3 object key with Hive-style date partition.

    Example:
        hrfco/raw/2024/waterlevel/date=2024-07-16/data.parquet
    """
    return f"hrfco/raw/{year}/{dtype}/date={dt.isoformat()}/data.parquet"


def df_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    """Serialize DataFrame to Parquet bytes (gzip compressed)."""
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, compression="gzip", engine="pyarrow")
    return buf.getvalue()


def upload_to_s3(
    s3_client: Any,
    bucket: str,
    key: str,
    data: bytes,
) -> None:
    """Upload bytes to S3. Overwrites if key already exists."""
    s3_client.put_object(Bucket=bucket, Key=key, Body=data)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run() -> None:
    """Full collection pipeline: API → Parquet → S3."""
    cfg = load_config()
    df_obs = load_obs_target()

    # S3 client
    s3 = boto3.client(
        "s3",
        region_name=cfg["aws_region"],
        aws_access_key_id=cfg["aws_access_key"],
        aws_secret_access_key=cfg["aws_secret_key"],
    )

    total_uploaded = 0
    total_skipped = 0

    for period_start, period_end in COLLECTION_PERIODS:
        year = period_start.year
        n_days = (period_end - period_start).days + 1
        print(f"\n{'='*60}")
        print(f"[기간] {period_start} ~ {period_end}  ({n_days}일, {year}년)")
        print(f"{'='*60}")

        for dtype in DATA_TYPES:
            print(f"\n  [{dtype.upper()}] 수집 시작")

            # 날짜별로 데이터 누적
            daily_frames: dict[date, list[pd.DataFrame]] = {}
            current = period_start
            while current <= period_end:
                daily_frames[current] = []
                current += timedelta(days=1)

            # 관측소별 API 호출
            n_obs = len(df_obs)
            for idx, (i, row) in enumerate(df_obs.iterrows(), start=1):
                obscd = str(row["codeObs"]).strip()
                obs_name = str(row["korObs"]).strip()

                if not obscd or obscd == "nan":
                    continue

                print(f"  [{idx:>3}/{n_obs}] {obs_name} ({obscd}) ...", end=" ", flush=True)

                df_fetched = fetch_period(
                    cfg["hrfco_token"], dtype, obscd, obs_name,
                    period_start, period_end,
                )

                if df_fetched.empty:
                    print("데이터 없음")
                    total_skipped += 1
                    continue

                print(f"{len(df_fetched)}행 수집")

                # 날짜별로 분류
                for dt, group in df_fetched.groupby("date"):
                    if dt in daily_frames:
                        daily_frames[dt].append(group)

            # 날짜별 Parquet → S3 업로드
            for dt, frames in sorted(daily_frames.items()):
                if not frames:
                    continue

                df_day = pd.concat(frames, ignore_index=True)
                parquet_bytes = df_to_parquet_bytes(df_day)
                s3_key = build_s3_key(year, dtype, dt)

                upload_to_s3(s3, cfg["s3_bucket"], s3_key, parquet_bytes)
                print(
                    f"    ✓ s3://{cfg['s3_bucket']}/{s3_key}"
                    f"  ({len(df_day)}행, {len(parquet_bytes)//1024}KB)"
                )
                total_uploaded += 1

    print(f"\n{'='*60}")
    print(f"[완료] 업로드 {total_uploaded}개 파일 / 데이터 없음 {total_skipped}건")
    print(f"{'='*60}")


if __name__ == "__main__":
    run()
