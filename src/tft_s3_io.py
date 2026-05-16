"""S3 Parquet loaders for TFT pipeline (HRFCO waterlevel + KMA AWS rainfall)."""
from __future__ import annotations

import io
import os
from pathlib import Path

import boto3
import pandas as pd
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv_root() -> None:
    load_dotenv(ROOT / ".env")


def make_s3():
    load_dotenv_root()
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


def s3_key_waterlevel_day(d: pd.Timestamp) -> str:
    return f"hrfco/raw/{d.year}/waterlevel/date={d.date().isoformat()}/data.parquet"


def s3_key_kma_rainfall_day(d: pd.Timestamp) -> str:
    return f"kma/raw/{d.year}/aws_awsh_1h/date={d.date().isoformat()}/data.parquet"


def read_s3_parquet(s3, bucket: str, key: str) -> pd.DataFrame | None:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return pd.read_parquet(io.BytesIO(obj["Body"].read()))
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    except Exception:
        return None


def load_waterlevel_range(
    s3,
    bucket: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    obscds: set[str] | None = None,
    *,
    verbose: bool = True,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for d in pd.date_range(start.normalize(), end.normalize(), freq="D"):
        key = s3_key_waterlevel_day(d)
        df = read_s3_parquet(s3, bucket, key)
        if df is None or df.empty:
            continue
        df = df.copy()
        df["obscd"] = df["obscd"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
        if obscds is not None:
            df = df[df["obscd"].isin(obscds)]
        if df.empty:
            continue
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce").dt.floor("h")
        df["value"] = pd.to_numeric(df.get("value"), errors="coerce")
        df = df.dropna(subset=["datetime"])
        lo = start.normalize()
        hi = end.normalize() + pd.Timedelta(hours=23)
        df = df[(df["datetime"] >= lo) & (df["datetime"] <= hi)]
        frames.append(df[["datetime", "obscd", "value"]])
    if not frames:
        return pd.DataFrame(columns=["datetime", "obscd", "value"])
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["obscd", "datetime"], keep="last")
    if verbose:
        print(f"  waterlevel rows: {len(out):,} | stations: {out['obscd'].nunique()}")
    return out.sort_values(["obscd", "datetime"])


def load_kma_rainfall_range(
    s3,
    bucket: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    stn_ids: set[str] | None = None,
    *,
    rainfall_var: str = "RN",
    verbose: bool = True,
) -> pd.DataFrame:
    """KMA ``aws_awsh_1h`` long → ``datetime, stn_id, rn``."""
    frames: list[pd.DataFrame] = []
    var_u = rainfall_var.strip().upper()
    for d in pd.date_range(start.normalize(), end.normalize(), freq="D"):
        key = s3_key_kma_rainfall_day(d)
        df = read_s3_parquet(s3, bucket, key)
        if df is None or df.empty:
            continue
        df = df.copy()
        if "var" in df.columns:
            df = df[df["var"].astype(str).str.upper() == var_u]
        if df.empty:
            continue
        df["stn_id"] = df["stn_id"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
        if stn_ids is not None:
            df = df[df["stn_id"].isin(stn_ids)]
        if df.empty:
            continue
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce").dt.floor("h")
        df["rn"] = pd.to_numeric(df.get("value"), errors="coerce")
        df = df.dropna(subset=["datetime"])
        lo = start.normalize()
        hi = end.normalize() + pd.Timedelta(hours=23)
        df = df[(df["datetime"] >= lo) & (df["datetime"] <= hi)]
        frames.append(df[["datetime", "stn_id", "rn"]])
    if not frames:
        return pd.DataFrame(columns=["datetime", "stn_id", "rn"])
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["stn_id", "datetime"], keep="last")
    if verbose:
        print(f"  rainfall rows: {len(out):,} | aws stn: {out['stn_id'].nunique()}")
    return out.sort_values(["stn_id", "datetime"])
