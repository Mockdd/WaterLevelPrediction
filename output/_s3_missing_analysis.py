"""One-off: load HRFCO parquets from S3 and summarize missingness."""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import boto3
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COLLECTION_PERIODS = [
    (pd.Timestamp("2024-07-15"), pd.Timestamp("2024-07-29")),
    (pd.Timestamp("2025-08-12"), pd.Timestamp("2025-08-18")),
]

EXPECTED_PER_DAY_1H = 24


def make_s3() -> tuple:
    load_dotenv(ROOT / ".env")
    s3 = boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "ap-southeast-2"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_key", ""),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
    )
    bucket = (os.getenv("S3_BUCKET") or "").strip().strip('"')
    return s3, bucket


def read_key(s3, bucket: str, key: str) -> pd.DataFrame | None:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return pd.read_parquet(io.BytesIO(obj["Body"].read()))
    except Exception as e:
        print(f"  [skip] {key}: {e}")
        return None


def load_dtype(s3, bucket: str, dtype: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for pstart, pend in COLLECTION_PERIODS:
        cur = pstart
        while cur <= pend:
            key = f"hrfco/raw/{cur.year}/{dtype}/date={cur.date().isoformat()}/data.parquet"
            df = read_key(s3, bucket, key)
            if df is not None and not df.empty:
                frames.append(df)
            cur += pd.Timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out["value"] = pd.to_numeric(out.get("value"), errors="coerce")
    return out


def grid_missing_1h(df: pd.DataFrame) -> pd.DataFrame:
    """Per (obscd, calendar date): how many hourly slots missing vs 24."""
    d = df.dropna(subset=["datetime"]).copy()
    d["d"] = d["datetime"].dt.normalize()
    d["hour"] = d["datetime"].dt.floor("h")
    g = d.groupby(["obscd", "d"], observed=True).agg(
        rows=("hour", "count"),
        uniq_hours=("hour", "nunique"),
    )
    g = g.reset_index()
    g["missing_hours_est"] = (EXPECTED_PER_DAY_1H - g["uniq_hours"]).clip(lower=0)
    return g


def main() -> None:
    s3, bucket = make_s3()
    if not bucket:
        raise SystemExit("S3_BUCKET empty in .env")

    print(f"Bucket: {bucket}\n")

    for dtype in ("waterlevel", "rainfall"):
        print("=" * 72)
        print(f"[{dtype}]")
        print("=" * 72)
        df = load_dtype(s3, bucket, dtype)
        if df.empty:
            print("  (no parquet rows loaded)\n")
            continue

        n = len(df)
        v_na = df["value"].isna().sum()
        dt_na = df["datetime"].isna().sum()

        print(f"  Rows: {n:,}")
        print(f"  value is NA: {v_na:,} ({100 * v_na / n:.2f}%)")
        print(f"  datetime is NA: {dt_na:,} ({100 * dt_na / n:.2f}%)")

        # Per-file date coverage (partition date vs row date)
        if "date" in df.columns:
            try:
                part_dates = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
                row_dates = df["datetime"].dt.normalize()
                mismatch = (part_dates.notna() & row_dates.notna() & (part_dates != row_dates)).sum()
                print(f"  date column != datetime.date (rows): {mismatch:,}")
            except Exception:
                pass

        # By calendar day (from datetime)
        by_day = df.assign(day=df["datetime"].dt.date).groupby("day").size()
        print("\n  Rows per calendar day (datetime):")
        for day, cnt in by_day.items():
            print(f"    {day}: {cnt:,}")

        # Station-level value missingness (use size, not count — count excludes NA)
        st = df.groupby("obscd", observed=True)["value"].agg(rows="size", na_ct=lambda s: s.isna().sum())
        st["na_pct"] = 100 * st["na_ct"] / st["rows"].replace(0, pd.NA)
        print("\n  Per-station value NA% (top 15 worst):")
        worst = st.sort_values("na_pct", ascending=False).head(15)
        for obscd, row in worst.iterrows():
            pct = row["na_pct"] if pd.notna(row["na_pct"]) else float("nan")
            pct_s = f"{pct:.1f}%" if pct == pct else "n/a"
            print(
                f"    {obscd}: NA {row['na_ct']:.0f}/{row['rows']:.0f} ({pct_s})"
            )

        print(
            f"\n  Stations with value NA% > 0: {(st['na_pct'] > 0).sum()} / {len(st)}"
        )
        print(
            f"  Stations with value NA% > 5%: {(st['na_pct'] > 5).sum()} / {len(st)}"
        )
        full_na = (st["na_pct"] >= 100).sum()
        print(
            f"  Stations with 100% value NA (timestamps exist): {full_na} / {len(st)}"
        )

        # Hourly grid gaps
        gm = grid_missing_1h(df)
        total_missing_hours = int(gm["missing_hours_est"].sum())
        station_days = len(gm)
        max_slots = station_days * EXPECTED_PER_DAY_1H
        print("\n  Hourly grid (assume 24 slots/day/station, unique hour timestamps):")
        print(f"    (obscd, day) groups: {station_days:,}")
        print(f"    Sum of (24 - nunique_hours)+: {total_missing_hours:,}")
        if max_slots:
            print(
                f"    Approx gap rate vs 24×groups: "
                f"{100 * total_missing_hours / max_slots:.2f}%"
            )

        # Days with severely incomplete grid (>6 missing hours)
        bad_days = (gm["missing_hours_est"] > 6).sum()
        print(f"    (obscd, day) with >6 missing hourly slots: {bad_days:,}")

        # Duplicates: same station same hour two rows
        d2 = df.dropna(subset=["datetime"]).copy()
        d2["hour"] = d2["datetime"].dt.floor("h")
        dup = d2.duplicated(subset=["obscd", "hour"], keep=False).sum()
        print(f"    Rows in duplicate (obscd, hour) groups: {dup:,}")

        print()


if __name__ == "__main__":
    main()
