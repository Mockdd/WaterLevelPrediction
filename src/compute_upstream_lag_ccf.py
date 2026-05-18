"""
상류 수위 교차상관(CCF)으로 time lag 추정 (S3 Parquet, train 구간만).

- **DTW·run_dtw와 무관.** scipy ``correlate`` 만 사용한다.
- 매핑: ``metadata_outputs/upstream_mapping_must.csv`` (하류 ``station_id`` 행당 상류 1·2).
- S3: ``hrfco/raw/{year}/waterlevel/date={YYYY-MM-DD}/data.parquet``
- 적합: 기본 train ``2023-03-01`` ~ ``2024-08-31`` (val/test 미사용).
- Q1(MVP): 동시 유효 격자 결측률 < 30% → linear 보간(limit=24h); 아니면 raw.

산출 CSV: **관측소(하류)당 1행**, ``lag_steps_upstream_1``, ``lag_steps_upstream_2`` (1H 스텝) 등.

Usage::

  python src/compute_upstream_lag_ccf.py --dry-run --max-stations 5
  python src/compute_upstream_lag_ccf.py
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

import boto3
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from scipy.signal import correlate

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPPING = ROOT / "metadata_outputs" / "upstream_mapping_must.csv"
DEFAULT_MANIFEST = ROOT / "metadata_outputs" / "upstream_lag_manifest.json"
MISSING_RATE_THRESH = 0.30
INTERP_LIMIT_STEPS = 24
N_EFFECTIVE_MIN = 336
L_MAX_DEFAULT = 120
METHOD_TAG = "ccf_demean_full_v1"


def _obscd_str(x: object) -> str | None:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    s = str(x).strip()
    if not s or s.lower() == "nan":
        return None
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _parse_lag0(v: object) -> bool:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return False
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("", "nan"):
        return False
    return s in ("true", "1", "t", "yes")


def ccf_lag_peak_demean(
    upstream: np.ndarray,
    downstream: np.ndarray,
    *,
    l_max: int,
) -> tuple[float, float]:
    """
    상류(ref)·하류(tgt) 각각 평균 제거 후 full 상관, lag ∈ [0, l_max] 에서 피크.
    양의 lag: 상류가 하류보다 선행하는 정렬(시계열 ``shift`` 와 맞추려면 구현에서 한 번 더 검증).
    """
    ref_s = np.asarray(upstream, dtype=np.float64).ravel()
    tgt_s = np.asarray(downstream, dtype=np.float64).ravel()
    n = len(ref_s)
    if ref_s.shape != tgt_s.shape or n < 3:
        return float("nan"), float("nan")
    if not np.all(np.isfinite(ref_s)) or not np.all(np.isfinite(tgt_s)):
        return float("nan"), float("nan")
    if float(np.std(ref_s)) < 1e-12 or float(np.std(tgt_s)) < 1e-12:
        return float("nan"), float("nan")
    corr = correlate(
        tgt_s - np.mean(tgt_s),
        ref_s - np.mean(ref_s),
        mode="full",
    )
    lags = np.arange(len(corr), dtype=np.int64) - (n - 1)
    mask = (lags >= 0) & (lags <= int(l_max))
    if not np.any(mask):
        return float("nan"), float("nan")
    sub = corr[mask]
    j = int(np.argmax(sub))
    lag_steps = float(lags[mask][j])
    cc_peak = float(sub[j])
    return lag_steps, cc_peak


def make_s3():
    load_dotenv(ROOT / ".env")
    bucket = (os.getenv("S3_BUCKET") or "").strip().strip('"')
    if not bucket:
        raise SystemExit("S3_BUCKET empty in .env")
    s3 = boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "ap-southeast-2"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_key", "") or None,
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY") or None,
    )
    return s3, bucket


def s3_key_waterlevel_day(d: pd.Timestamp) -> str:
    return f"hrfco/raw/{d.year}/waterlevel/date={d.date().isoformat()}/data.parquet"


def load_train_long(
    s3,
    bucket: str,
    fit_start: pd.Timestamp,
    fit_end: pd.Timestamp,
    obscds: set[str],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for d in pd.date_range(fit_start.normalize(), fit_end.normalize(), freq="D"):
        key = s3_key_waterlevel_day(d)
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            df = pd.read_parquet(io.BytesIO(obj["Body"].read()))
        except Exception as e:
            print(f"  [skip] {key}: {e}", file=sys.stderr)
            continue
        if df.empty:
            continue
        df = df.copy()
        df["obscd"] = df["obscd"].astype(str).str.strip()
        df = df[df["obscd"].isin(obscds)]
        if df.empty:
            continue
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df["value"] = pd.to_numeric(df.get("value"), errors="coerce")
        df = df.dropna(subset=["datetime"])
        df["datetime"] = df["datetime"].dt.floor("h")
        lo = pd.Timestamp(fit_start).normalize()
        hi = pd.Timestamp(fit_end).normalize() + pd.Timedelta(hours=23)
        df = df[(df["datetime"] >= lo) & (df["datetime"] <= hi)]
        frames.append(df[["datetime", "obscd", "value"]])
    if not frames:
        return pd.DataFrame(columns=["datetime", "obscd", "value"])
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["obscd", "datetime"], keep="last")
    return out.sort_values(["obscd", "datetime"])


def _max_interp_run(is_interpolated: pd.Series) -> int:
    if is_interpolated.empty or not is_interpolated.any():
        return 0
    grp = (is_interpolated != is_interpolated.shift()).cumsum()
    runs = is_interpolated[is_interpolated].groupby(grp).size()
    return int(runs.max()) if len(runs) else 0


def prepare_pair_series(
    idx: pd.DatetimeIndex,
    u_raw: pd.Series,
    d_raw: pd.Series,
    *,
    missing_thresh: float,
    interp_limit: int,
) -> tuple[np.ndarray, np.ndarray, str, float, int, str]:
    u = u_raw.reindex(idx)
    d = d_raw.reindex(idx)
    both0 = u.notna() & d.notna()
    n = len(idx)
    if n == 0:
        return np.array([]), np.array([]), "raw", 1.0, 0, "empty_index"
    miss_rate = 1.0 - (both0.sum() / n)

    if miss_rate < missing_thresh:
        u2 = u.copy()
        d2 = d.copy()
        u2 = u2.interpolate(method="linear", limit_area="inside", limit=interp_limit)
        d2 = d2.interpolate(method="linear", limit_area="inside", limit=interp_limit)
        u_filled = u2.notna() & u.isna()
        d_filled = d2.notna() & d.isna()
        max_run = max(_max_interp_run(u_filled), _max_interp_run(d_filled))
        u_out, d_out = u2, d2
        branch = "interpolated"
    else:
        u_out, d_out = u, d
        branch = "raw"
        max_run = 0

    both = u_out.notna() & d_out.notna()
    if both.sum() < 3:
        return np.array([]), np.array([]), branch, float(miss_rate), max_run, "too_few_joint"

    uu = u_out[both].to_numpy(dtype=np.float64)
    dd = d_out[both].to_numpy(dtype=np.float64)
    return uu, dd, branch, float(miss_rate), max_run, ""


def _slot_ccf_result(
    wide: pd.DataFrame,
    idx: pd.DatetimeIndex,
    sid: str,
    uid: str | None,
    lag0: bool,
    *,
    l_max: int,
    n_min: int,
    missing_thresh: float,
    interp_limit: int,
) -> dict:
    """슬롯 하나에 대한 lag·메타."""
    empty = {
        "lag_steps": np.nan,
        "reliable": np.nan,
        "max_corr": np.nan,
        "n_effective": np.nan,
        "fallback_reason": "",
        "ccf_input_branch": "",
        "missing_rate_joint": np.nan,
        "max_interp_run_applied": np.nan,
    }
    if uid is None:
        out = empty.copy()
        out["fallback_reason"] = "no_upstream_mapping"
        return out
    if lag0:
        return {
            "lag_steps": 0.0,
            "reliable": False,
            "max_corr": np.nan,
            "n_effective": 0.0,
            "fallback_reason": "lag0_true",
            "ccf_input_branch": "raw",
            "missing_rate_joint": np.nan,
            "max_interp_run_applied": 0.0,
        }
    if sid not in wide.columns or uid not in wide.columns:
        return {
            "lag_steps": 0.0,
            "reliable": False,
            "max_corr": np.nan,
            "n_effective": 0.0,
            "fallback_reason": "missing_series_train_window",
            "ccf_input_branch": "raw",
            "missing_rate_joint": np.nan,
            "max_interp_run_applied": 0.0,
        }

    u_arr, d_arr, branch, miss_rate, max_run, skip = prepare_pair_series(
        idx,
        wide[uid],
        wide[sid],
        missing_thresh=missing_thresh,
        interp_limit=interp_limit,
    )
    n_eff = int(len(u_arr))
    if skip == "empty_index":
        fb = "empty_index"
    elif skip == "too_few_joint":
        fb = "too_few_joint"
    else:
        fb = ""

    if n_eff < n_min or fb:
        return {
            "lag_steps": 0.0,
            "reliable": False,
            "max_corr": np.nan,
            "n_effective": float(n_eff),
            "fallback_reason": fb or "n_effective_below_min",
            "ccf_input_branch": branch,
            "missing_rate_joint": miss_rate,
            "max_interp_run_applied": float(max_run),
        }

    lag_steps, cc_peak = ccf_lag_peak_demean(u_arr, d_arr, l_max=l_max)
    if not np.isfinite(lag_steps) or not np.isfinite(cc_peak):
        return {
            "lag_steps": 0.0,
            "reliable": False,
            "max_corr": np.nan,
            "n_effective": float(n_eff),
            "fallback_reason": "ccf_undefined",
            "ccf_input_branch": branch,
            "missing_rate_joint": miss_rate,
            "max_interp_run_applied": float(max_run),
        }

    return {
        "lag_steps": float(round(lag_steps)),
        "reliable": True,
        "max_corr": float(cc_peak),
        "n_effective": float(n_eff),
        "fallback_reason": "",
        "ccf_input_branch": branch,
        "missing_rate_joint": miss_rate,
        "max_interp_run_applied": float(max_run),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="CCF upstream time lag (S3, train-only)")
    p.add_argument("--fit-start", default="2023-03-01")
    p.add_argument("--fit-end", default="2024-08-31")
    p.add_argument("--mapping", type=str, default=str(DEFAULT_MAPPING))
    p.add_argument("--out-dir", type=str, default=str(ROOT / "metadata_outputs"))
    p.add_argument("--l-max", type=int, default=L_MAX_DEFAULT)
    p.add_argument("--n-effective-min", type=int, default=N_EFFECTIVE_MIN)
    p.add_argument("--missing-thresh", type=float, default=MISSING_RATE_THRESH)
    p.add_argument("--interp-limit", type=int, default=INTERP_LIMIT_STEPS)
    p.add_argument("--max-stations", type=int, default=None, help="디버그: 앞에서 N개 하류 행만")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    fit_start = pd.Timestamp(args.fit_start)
    fit_end = pd.Timestamp(args.fit_end)
    if fit_end < fit_start:
        print("--fit-end must be >= --fit-start", file=sys.stderr)
        return 1

    mapping = pd.read_csv(Path(args.mapping), dtype=str)
    if args.max_stations is not None:
        mapping = mapping.iloc[: max(0, int(args.max_stations))].copy()

    need: set[str] = set()
    for _, r in mapping.iterrows():
        sid = _obscd_str(r.get("station_id"))
        if sid:
            need.add(sid)
        for slot in (1, 2):
            uid = _obscd_str(r.get(f"upstream_{slot}"))
            if uid:
                need.add(uid)

    print(
        f"Stations(rows): {len(mapping)} | train {fit_start.date()}..{fit_end.date()} | "
        f"distinct obscd needed: {len(need)}"
    )

    s3, bucket = make_s3()
    long_df = load_train_long(s3, bucket, fit_start, fit_end, need)
    if long_df.empty:
        print("No S3 rows in train window for required obscd.", file=sys.stderr)
        return 2

    wide = long_df.pivot_table(
        index="datetime", columns="obscd", values="value", aggfunc="last"
    ).sort_index()
    idx = wide.index

    out_rows: list[dict] = []
    for _, r in mapping.iterrows():
        sid = _obscd_str(r.get("station_id"))
        if not sid:
            continue
        base = {
            "station_id": sid,
            "station_name": r.get("station_name", ""),
            "stream_code": r.get("stream_code", ""),
            "upstream_1": r.get("upstream_1", ""),
            "upstream_1_name": r.get("upstream_1_name", ""),
            "upstream_2": r.get("upstream_2", ""),
            "upstream_2_name": r.get("upstream_2_name", ""),
            "fit_start": fit_start.date().isoformat(),
            "fit_end": fit_end.date().isoformat(),
            "freq": "1H",
            "method": METHOD_TAG,
        }
        for slot in (1, 2):
            uid = _obscd_str(r.get(f"upstream_{slot}"))
            lag0 = _parse_lag0(r.get(f"upstream_{slot}_lag0"))
            slot_res = _slot_ccf_result(
                wide,
                idx,
                sid,
                uid,
                lag0,
                l_max=int(args.l_max),
                n_min=int(args.n_effective_min),
                missing_thresh=float(args.missing_thresh),
                interp_limit=int(args.interp_limit),
            )
            base[f"lag_steps_upstream_{slot}"] = slot_res["lag_steps"]
            base[f"reliable_upstream_{slot}"] = slot_res["reliable"]
            base[f"max_corr_upstream_{slot}"] = slot_res["max_corr"]
            base[f"n_effective_upstream_{slot}"] = slot_res["n_effective"]
            base[f"fallback_reason_upstream_{slot}"] = slot_res["fallback_reason"]
            base[f"ccf_input_branch_upstream_{slot}"] = slot_res["ccf_input_branch"]
            base[f"missing_rate_joint_upstream_{slot}"] = slot_res["missing_rate_joint"]
            base[f"max_interp_run_upstream_{slot}"] = slot_res["max_interp_run_applied"]

        out_rows.append(base)

    res = pd.DataFrame(out_rows)
    r1 = res.get("reliable_upstream_1")
    r2 = res.get("reliable_upstream_2")
    n_ok = int(pd.Series(r1).eq(True).sum() + pd.Series(r2).eq(True).sum())
    print(f"reliable slots (1+2): {n_ok} / {2*len(res)}")

    if args.dry_run:
        print("[dry-run] skip writing CSV/manifest")
        print(res.head(3).to_string())
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = pd.Timestamp.utcnow().strftime("%Y%m%d_%H%M")
    csv_name = f"upstream_lag_ccf_by_station_v{tag}.csv"
    csv_path = out_dir / csv_name
    res.to_csv(csv_path, index=False, encoding="utf-8-sig")

    manifest = {
        "active_csv": str(csv_path.relative_to(ROOT)).replace("\\", "/"),
        "fit_start": fit_start.date().isoformat(),
        "fit_end": fit_end.date().isoformat(),
        "created_at": pd.Timestamp.utcnow().isoformat() + "Z",
        "git_commit": (os.getenv("GIT_COMMIT") or "").strip() or None,
        "l_max": int(args.l_max),
        "n_effective_min": int(args.n_effective_min),
        "missing_rate_thresh": float(args.missing_thresh),
        "mapping_csv": str(Path(args.mapping).relative_to(ROOT)).replace("\\", "/"),
    }
    man_path = Path(DEFAULT_MANIFEST)
    tmp = man_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(man_path)

    print(f"Wrote {csv_path}")
    print(f"Wrote {man_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
