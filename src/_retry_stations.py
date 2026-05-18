"""특정 관측소만 재수집 후 S3 업로드.

Usage:
    python src/_retry_stations.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect_hrfco_s3 import (
    COLLECTION_PERIODS,
    DATA_TYPES,
    df_to_parquet_bytes,
    fetch_period,
    build_s3_key,
    load_config,
    upload_to_s3,
)
from datetime import timedelta
import boto3

RETRY_STATIONS = [
    {"obscd": "1007640", "korObs": "여주시(율극교)"},
    {"obscd": "1007620", "korObs": "여주시(강천리)"},
    {"obscd": "1002610", "korObs": "평창군(장평교)"},
]


def run() -> None:
    cfg = load_config()

    s3 = boto3.client(
        "s3",
        region_name=cfg["aws_region"],
        aws_access_key_id=cfg["aws_access_key"],
        aws_secret_access_key=cfg["aws_secret_key"],
    )

    total_uploaded = 0

    for period_start, period_end in COLLECTION_PERIODS:
        year = period_start.year
        print(f"\n{'='*60}")
        print(f"[기간] {period_start} ~ {period_end}  ({year}년)")
        print(f"{'='*60}")

        for dtype in DATA_TYPES:
            print(f"\n  [{dtype.upper()}]")

            daily_frames: dict = {}
            current = period_start
            while current <= period_end:
                daily_frames[current] = []
                current += timedelta(days=1)

            for sta in RETRY_STATIONS:
                obscd    = sta["obscd"]
                kor_obs  = sta["korObs"]
                print(f"  {kor_obs} ({obscd}) ...", end=" ", flush=True)

                df = fetch_period(
                    cfg["hrfco_token"], dtype, obscd, kor_obs,
                    period_start, period_end,
                )

                if df.empty:
                    print("데이터 없음")
                    continue

                print(f"{len(df)}행 수집")
                for dt, grp in df.groupby("date"):
                    if dt in daily_frames:
                        daily_frames[dt].append(grp)

            for dt, frames in sorted(daily_frames.items()):
                if not frames:
                    continue
                import pandas as pd
                df_day    = pd.concat(frames, ignore_index=True)
                s3_key    = build_s3_key(year, dtype, dt)
                existing  = _load_existing(s3, cfg["s3_bucket"], s3_key)

                if existing is not None:
                    # 기존 파일에서 해당 관측소 행만 교체 후 재업로드
                    retry_codes = {s["obscd"] for s in RETRY_STATIONS}
                    df_base = existing[~existing["obscd"].isin(retry_codes)]
                    df_day  = pd.concat([df_base, df_day], ignore_index=True)

                upload_to_s3(s3, cfg["s3_bucket"], s3_key, df_to_parquet_bytes(df_day))
                print(f"    ✓ {s3_key}  ({len(df_day)}행)")
                total_uploaded += 1

    print(f"\n[완료] 업로드 {total_uploaded}개 파일")


def _load_existing(s3, bucket: str, key: str):
    """S3에서 기존 parquet 읽기. 없으면 None 반환."""
    import io
    import pandas as pd
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return pd.read_parquet(io.BytesIO(obj["Body"].read()))
    except Exception:
        return None


if __name__ == "__main__":
    run()
