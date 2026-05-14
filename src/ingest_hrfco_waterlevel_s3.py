"""
HRFCO 수위 1H API → S3 Parquet 적재.

S3 키·스키마는 ``src/_s3_missing_analysis.py`` 가 읽는 형식과 맞춘다.

  s3://{S3_BUCKET}/hrfco/raw/{year}/waterlevel/date={YYYY-MM-DD}/data.parquet

컬럼: ``datetime`` (tz-naive hourly), ``obscd`` (str), ``value`` (수위 m), ``date`` (partition 날짜 문자열).

API는 요청당 기간 상한(통상 1H 기준 약 1년)이 있어, 긴 구간은 **여러 청크**로 나눠 호출한다.

환경 변수 (프로젝트 루트 ``.env``):

  hrfco_token       HRFCO API 토큰
  S3_BUCKET         대상 버킷
  AWS_REGION        (선택)
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY

Usage::

  python src/ingest_hrfco_waterlevel_s3.py
  python src/ingest_hrfco_waterlevel_s3.py --dry-run --max-stations 3
  python src/ingest_hrfco_waterlevel_s3.py --no-missingness
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import boto3
import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OBS_TARGET = ROOT / "metadata_outputs" / "obsTarget.csv"
MISSINGNESS_DEFAULT = (
    ROOT / "metadata_outputs" / "hrfco_waterlevel_missingness_by_station_day.csv"
)

# HRFCO 1H: 요청 구간이 1년을 넘지 않도록 (여유 포함)
CHUNK_MAX_DAYS = 330


def _ymdhm_range(start: pd.Timestamp, end: pd.Timestamp) -> tuple[str, str]:
    """HRFCO list/1H 시작·끝 시각 문자열 (시간 포함)."""
    s = start.normalize()
    e = end.normalize() + pd.Timedelta(hours=23)
    return s.strftime("%Y%m%d%H"), e.strftime("%Y%m%d%H")


def iter_api_chunks(
    start: pd.Timestamp, end: pd.Timestamp, max_days: int = CHUNK_MAX_DAYS
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """[start, end] 달력 구간을 각각 최대 max_days일 이하로 쪼갠 (시작, 끝) 리스트."""
    out: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    cur = start.normalize()
    end_n = end.normalize()
    while cur <= end_n:
        chunk_end = min(end_n, cur + pd.Timedelta(days=max_days - 1))
        out.append((cur, chunk_end))
        cur = chunk_end + pd.Timedelta(days=1)
    return out


def load_obscds_from_obs_target(max_stations: int | None) -> list[str]:
    df = pd.read_csv(OBS_TARGET, index_col=0, dtype=str)
    s = (
        df["codeObs"]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
    )
    out = sorted(s.dropna().unique())
    if max_stations is not None:
        out = out[: int(max_stations)]
    return out


def fetch_waterlevel_1h(obscd: str, ymdhm_start: str, ymdhm_end: str, token: str) -> list[dict]:
    url = f"https://api.hrfco.go.kr/{token}/waterlevel/list/1H/{obscd}/{ymdhm_start}/{ymdhm_end}.xml"
    r = requests.get(url, timeout=240)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    rows: list[dict] = []
    for item in root.findall(".//Waterlevel"):
        wl_val = item.findtext("wl")
        if wl_val is None or not str(wl_val).strip() or str(wl_val).strip() in ("-", ""):
            continue
        try:
            wl = float(str(wl_val).strip())
        except ValueError:
            continue
        ymdhm = item.findtext("ymdhm")
        if not ymdhm:
            continue
        rows.append({"obscd": str(obscd).strip(), "datetime": ymdhm, "value": wl})
    return rows


def fetch_station_all_chunks(
    obscd: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    token: str,
    *,
    chunk_sleep: float,
) -> list[dict]:
    chunks = iter_api_chunks(start, end)
    all_rows: list[dict] = []
    for j, (c0, c1) in enumerate(chunks):
        ys, ye = _ymdhm_range(c0, c1)
        part = fetch_waterlevel_1h(obscd, ys, ye, token)
        all_rows.extend(part)
        if j + 1 < len(chunks):
            time.sleep(max(0.0, chunk_sleep))
    return all_rows


def rows_to_daily_frames(rows: list[dict]) -> dict[pd.Timestamp, pd.DataFrame]:
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"], format="%Y%m%d%H", errors="coerce")
    df = df.dropna(subset=["datetime"])
    df["datetime"] = df["datetime"].dt.floor("h")
    df = df.drop_duplicates(subset=["obscd", "datetime"], keep="last")
    df["date"] = df["datetime"].dt.date.astype(str)
    by_day: dict[pd.Timestamp, pd.DataFrame] = {}
    for day_str, g in df.groupby("date", sort=True):
        d = pd.Timestamp(day_str)
        g = g.drop(columns=["date"]).reset_index(drop=True)
        by_day[d] = g
    return by_day


def make_s3():
    load_dotenv(ROOT / ".env")
    bucket = (os.getenv("S3_BUCKET") or "").strip().strip('"')
    if not bucket:
        raise SystemExit("S3_BUCKET is empty in .env")
    s3 = boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "ap-southeast-2"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_key", "") or None,
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY") or None,
    )
    return s3, bucket


def upload_day(s3, bucket: str, year: int, day: pd.Timestamp, frame: pd.DataFrame, dry_run: bool) -> None:
    d_iso = day.date().isoformat()
    key = f"hrfco/raw/{year}/waterlevel/date={d_iso}/data.parquet"
    buf = io.BytesIO()
    frame.to_parquet(buf, index=False)
    body = buf.getvalue()
    if dry_run:
        print(f"  [dry-run] would put {len(frame):6d} rows -> s3://{bucket}/{key} ({len(body)} bytes)")
        return
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/octet-stream")
    print(f"  put {len(frame):6d} rows -> s3://{bucket}/{key}")


def daily_grid_stats(merged: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """일·관측소별 원시 행 수·유일 시각 수(1H 격자 결측 요약)."""
    if merged.empty:
        return pd.DataFrame(
            columns=[
                "obscd",
                "calendar_date",
                "row_count",
                "uniq_hours",
                "missing_hours_est",
                "value_na",
            ]
        )
    m = merged.copy()
    m["hour"] = m["datetime"].dt.floor("h")
    g = (
        m.groupby("obscd", sort=True)
        .agg(
            row_count=("obscd", "size"),
            uniq_hours=("hour", "nunique"),
            value_na=("value", lambda x: int(x.isna().sum())),
        )
        .reset_index()
    )
    g["calendar_date"] = day.strftime("%Y-%m-%d")
    g["missing_hours_est"] = (24 - g["uniq_hours"].clip(upper=24)).clip(lower=0).astype(int)
    return g


def merge_upload_accumulated(
    acc: dict[pd.Timestamp, list[pd.DataFrame]],
    s3,
    bucket: str,
    dry_run: bool,
    *,
    missingness_rows: list[pd.DataFrame] | None,
) -> None:
    """같은 날짜에 여러 관측소 배치를 합쳐 한 번에 업로드."""
    for day in sorted(acc.keys()):
        parts = acc[day]
        if not parts:
            continue
        merged = pd.concat(parts, ignore_index=True)
        merged = merged.drop_duplicates(subset=["obscd", "datetime"], keep="last")
        merged = merged.sort_values(["obscd", "datetime"]).reset_index(drop=True)
        merged["date"] = day.date().isoformat()
        if missingness_rows is not None:
            missingness_rows.append(daily_grid_stats(merged.drop(columns=["date"]), day))
        upload_day(s3, bucket, day.year, day, merged, dry_run)


def main() -> int:
    p = argparse.ArgumentParser(description="HRFCO 1H waterlevel → S3 raw parquet")
    p.add_argument("--start", type=str, default="2023-03-01", help="시작일 (YYYY-MM-DD)")
    p.add_argument("--end", type=str, default="2025-10-31", help="종료일 (YYYY-MM-DD)")
    p.add_argument(
        "--max-stations",
        type=int,
        default=None,
        help="테스트용: 관측소 개수 상한 (미지정이면 obsTarget 전체)",
    )
    p.add_argument("--dry-run", action="store_true", help="S3 업로드 없이 요약만")
    p.add_argument("--sleep", type=float, default=0.12, help="관측소 간 API 간격(초)")
    p.add_argument(
        "--chunk-sleep",
        type=float,
        default=0.08,
        help="동일 관측소·연속 청크 간 간격(초)",
    )
    p.add_argument(
        "--missingness-out",
        type=str,
        default=str(MISSINGNESS_DEFAULT),
        help="관측소·일별 원시 격자 요약 CSV 경로",
    )
    p.add_argument(
        "--no-missingness",
        action="store_true",
        help="결측 요약 CSV를 쓰지 않음",
    )
    args = p.parse_args()

    load_dotenv(ROOT / ".env")
    token = (os.getenv("hrfco_token") or "").strip().strip('"')
    if not token:
        print("hrfco_token missing in .env", file=sys.stderr)
        return 1

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    if end < start:
        print("--end must be >= --start", file=sys.stderr)
        return 1

    chunks = iter_api_chunks(start, end)
    obscds = load_obscds_from_obs_target(args.max_stations)
    if not obscds:
        print("No station codes from obsTarget.", file=sys.stderr)
        return 1

    print(
        f"Stations: {len(obscds)} | range {args.start}..{args.end} | "
        f"API chunks per station: {len(chunks)} | dry_run={args.dry_run}"
    )

    if not args.dry_run:
        s3, bucket = make_s3()
    else:
        s3, bucket = None, (os.getenv("S3_BUCKET") or "DRY_RUN_BUCKET")

    acc: dict[pd.Timestamp, list[pd.DataFrame]] = defaultdict(list)
    missingness_parts: list[pd.DataFrame] | None = [] if not args.no_missingness else None

    for i, obscd in enumerate(obscds):
        try:
            rows = fetch_station_all_chunks(
                obscd, start, end, token, chunk_sleep=args.chunk_sleep
            )
        except Exception as e:
            print(f"  [fail] {obscd}: {e}", file=sys.stderr)
            rows = []
        by_day = rows_to_daily_frames(rows)
        for d, frame in by_day.items():
            if d < start.normalize() or d > end.normalize():
                continue
            acc[d].append(frame)
        n_chunks = len(iter_api_chunks(start, end))
        print(
            f"  [{i+1:4d}/{len(obscds)}] {obscd}: API rows={len(rows):6d} "
            f"days_in_range={len(by_day):4d} (chunks={n_chunks})"
        )
        time.sleep(max(0.0, args.sleep))

    if not acc:
        print("No rows to upload.", file=sys.stderr)
        return 2

    print("\nUploading by calendar day …")
    merge_upload_accumulated(acc, s3, bucket, args.dry_run, missingness_rows=missingness_parts)

    if missingness_parts:
        miss_path = Path(args.missingness_out)
        miss_path.parent.mkdir(parents=True, exist_ok=True)
        big = pd.concat(missingness_parts, ignore_index=True)
        big = big.sort_values(["calendar_date", "obscd"]).reset_index(drop=True)
        big.to_csv(miss_path, index=False, encoding="utf-8-sig")
        print(f"Wrote missingness summary: {miss_path} ({len(big)} rows)")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
