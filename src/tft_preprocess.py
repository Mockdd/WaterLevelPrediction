"""
TFT 학습용 패널 전처리 (``docs/before_training.md`` 정합).

S3: HRFCO 수위 ``hrfco/raw/.../waterlevel/``, KMA 강수 ``kma/raw/.../aws_awsh_1h/``.
메타: ``obsFinalStreamReg.csv`` (기본 관측소), ``obsWaterLevel_top1.csv``, ``upstream_mapping_must.csv``,
      ``upstream_lag_manifest.json`` → lag CSV.

Colab 예::

  %cd /content/FloodAX
  !pip install -q pandas pyarrow boto3 python-dotenv scikit-learn
  !python src/tft_preprocess.py --out-dir /content/drive/MyDrive/floodax/tft_processed

Usage::

  python src/tft_preprocess.py
  python src/tft_preprocess.py --max-stations 5 --out-dir data/tft_processed
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from tft_s3_io import (
    ROOT,
    load_dotenv_root,
    load_kma_rainfall_range,
    load_waterlevel_range,
    make_s3,
)

# before_training.md §1.3
DEFAULT_TRAIN_START = "2023-03-01"
DEFAULT_TRAIN_END = "2024-08-31"
DEFAULT_VAL_START = "2024-09-01"
DEFAULT_VAL_END = "2025-03-31"
DEFAULT_TEST_START = "2025-04-01"
DEFAULT_TEST_END = "2025-10-31"

INTERP_LIMIT_H = 24
MISSING_RATE_THRESH = 0.30

OBS_STATIONS_CSV = ROOT / "metadata_outputs" / "obsFinalStreamReg.csv"
OBS_TARGET = OBS_STATIONS_CSV  # alias (HRFCO ingest·3권 전체와 동일 기준)
RAIN_MAP = ROOT / "metadata_outputs" / "obsWaterLevel_top1.csv"
UPSTREAM_MAP = ROOT / "metadata_outputs" / "upstream_mapping_must.csv"
LAG_MANIFEST = ROOT / "metadata_outputs" / "upstream_lag_manifest.json"
ELIGIBILITY_OUT = ROOT / "metadata_outputs" / "tft_station_eligibility.csv"

TARGET_COL = "wl"
DIFF_COL = "wl_diff"
RN_COL = "rn"


def _obscd_str(x: object) -> str | None:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    s = str(x).strip().replace(".0", "")
    if not s or s.lower() == "nan":
        return None
    return s


def _parse_lag0(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return False
    return str(v).strip().lower() in ("true", "1", "t", "yes")


def load_station_list(path: Path, max_stations: int | None) -> list[str]:
    df = pd.read_csv(path, index_col=0, dtype=str)
    s = df["codeObs"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    codes = sorted(s[(s != "") & (s.str.lower() != "nan")].dropna().unique())
    if max_stations is not None:
        codes = codes[: int(max_stations)]
    return codes


def load_rainfall_map(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["station_id"] = df["obscd_wl"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    df["stn_id_aws"] = df["stn_id_aws"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    return df[["station_id", "stn_id_aws"]].drop_duplicates("station_id")


def collect_waterlevel_station_ids(stations: list[str], mapping: pd.DataFrame) -> set[str]:
    """패널 관측소 + 상류 1·2 코드까지 S3 수위 로드 대상."""
    ids = set(stations)
    sub = mapping[mapping["station_id"].astype(str).isin(stations)]
    for col in ("upstream_1", "upstream_2"):
        if col not in sub.columns:
            continue
        for v in sub[col]:
            oid = _obscd_str(v)
            if oid:
                ids.add(oid)
    return ids


def load_lag_table(mapping_path: Path, manifest_path: Path) -> pd.DataFrame:
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    lag_path = ROOT / manifest["active_csv"]
    if not lag_path.is_file():
        lag_path = Path(manifest["active_csv"])
    lag = pd.read_csv(lag_path, dtype=str)
    lag["station_id"] = lag["station_id"].astype(str).str.strip()
    return lag


def assign_split(dt: pd.Series) -> pd.Series:
    """Calendar split labels (before_training.md §1.3)."""
    t = pd.to_datetime(dt)
    out = pd.Series("drop", index=t.index, dtype=object)
    out[(t >= DEFAULT_TRAIN_START) & (t <= DEFAULT_TRAIN_END)] = "train"
    out[(t >= DEFAULT_VAL_START) & (t <= DEFAULT_VAL_END)] = "val"
    out[(t >= DEFAULT_TEST_START) & (t <= DEFAULT_TEST_END)] = "test"
    return out


def build_skeleton(stations: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    idx = pd.date_range(start.normalize(), end.normalize() + pd.Timedelta(hours=23), freq="h")
    parts = [
        pd.DataFrame({"station_id": sid, "datetime": idx}) for sid in stations
    ]
    sk = pd.concat(parts, ignore_index=True)
    sk["split"] = assign_split(sk["datetime"])
    return sk


def impute_waterlevel(
    s: pd.Series,
    *,
    train_mask: pd.Series,
    interp_limit: int,
) -> tuple[pd.Series, pd.Series, str]:
    """train 구간 결측률로 보간 여부 결정 (CCF Q1·§1.5). ≥30%이면 보간 없음."""
    tm = train_mask.reindex(s.index, fill_value=False)
    train_s = s.loc[tm]
    miss_rate = float(train_s.isna().mean()) if len(train_s) else 1.0
    was = pd.Series(0, index=s.index, dtype=np.int8)
    if miss_rate >= MISSING_RATE_THRESH:
        return s, was, "no_interp_high_missing"
    out = s.interpolate(method="linear", limit_area="inside", limit=interp_limit)
    was = (out.notna() & s.isna()).astype(np.int8)
    return out, was, "interpolated"


def build_station_eligibility(
    panel: pd.DataFrame,
    *,
    min_required_hours: int,
) -> pd.DataFrame:
    """before_training.md §1.5 — 관측소별 TFT 학습 포함 여부."""
    rows: list[dict] = []
    for sid, g in panel.groupby("station_id", sort=True):
        tr = g[g["split"] == "train"]
        n_hours = len(tr)
        n_valid = int(tr[TARGET_COL].notna().sum()) if n_hours else 0
        miss_rate = 1.0 - (n_valid / n_hours) if n_hours else 1.0
        branch = (
            str(tr["impute_branch"].iloc[0])
            if "impute_branch" in tr.columns and len(tr)
            else "unknown"
        )
        reasons: list[str] = []
        if n_valid == 0:
            reasons.append("no_waterlevel_in_train")
        if miss_rate >= MISSING_RATE_THRESH:
            reasons.append("missing_rate_ge_30pct")
        if n_hours < min_required_hours:
            reasons.append("too_short_for_window")
        included = not reasons
        rows.append(
            {
                "station_id": sid,
                "n_hours_train": n_hours,
                "n_valid_wl_train": n_valid,
                "missing_rate_wl_train": round(miss_rate, 4),
                "impute_branch": branch,
                "min_required_hours": min_required_hours,
                "included_tft_train": "Y" if included else "N",
                "exclude_reason": ";".join(reasons),
            }
        )
    return pd.DataFrame(rows)


def remove_outliers_iqr(s: pd.Series, k: float = 4.0) -> pd.Series:
    v = s.dropna()
    if len(v) < 10:
        return s
    q1, q3 = v.quantile(0.25), v.quantile(0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return s
    lo, hi = q1 - k * iqr, q3 + k * iqr
    out = s.copy()
    out[(out < lo) | (out > hi)] = np.nan
    return out


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    dt = df["datetime"]
    hour = dt.dt.hour + dt.dt.minute / 60.0
    month = dt.dt.month
    df = df.copy()
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    df["month_sin"] = np.sin(2 * np.pi * (month - 1) / 12.0)
    df["month_cos"] = np.cos(2 * np.pi * (month - 1) / 12.0)
    return df


def add_time_idx(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["time_idx"] = df.groupby("station_id").cumcount()
    return df


def _upstream_slot_series(
    panel: pd.DataFrame,
    sid: str,
    slot: int,
    map_idx: pd.DataFrame,
    lag_idx: pd.DataFrame,
    wl_by_st: dict[str, pd.Series],
) -> tuple[pd.Series, pd.Series]:
    """관측소 ``sid`` 에 대해 상류 슬롯 시계열·마스크 반환."""
    u_col = f"upstream_{slot}"
    lag_col = f"lag_steps_upstream_{slot}"
    lag0_col = f"upstream_{slot}_lag0"
    n = len(panel)
    nan_s = pd.Series(np.nan, index=panel.index, dtype=np.float64)
    zero_mask = pd.Series(0, index=panel.index, dtype=np.int8)

    if sid not in map_idx.index:
        return nan_s, zero_mask
    row_m = map_idx.loc[sid]
    row_l = lag_idx.loc[sid] if sid in lag_idx.index else None
    up_id = _obscd_str(row_m.get(u_col))
    if up_id is None or up_id not in wl_by_st:
        return nan_s, zero_mask

    lag_steps = 0
    if row_l is not None and lag_col in row_l.index:
        try:
            lag_steps = int(float(row_l[lag_col]))
        except (TypeError, ValueError):
            lag_steps = 0
    lag_steps = max(0, lag_steps)

    shifted = wl_by_st[up_id].shift(lag_steps).reindex(panel["datetime"].values)
    shifted.index = panel.index
    if _parse_lag0(row_m.get(lag0_col)):
        return pd.Series(0.0, index=panel.index), zero_mask
    mask = shifted.notna().astype(np.int8)
    return shifted, mask


def attach_upstream(
    panel: pd.DataFrame,
    wl_long: pd.DataFrame,
    mapping: pd.DataFrame,
    lag_tbl: pd.DataFrame,
) -> pd.DataFrame:
    """상류 수위 lag shift — 하류 시각 t 에 상류 ``upstream(t - L)`` (pandas ``shift(L)``)."""
    wl_p = wl_long.rename(columns={"obscd": "station_id", "value": "wl_src"})
    wl_by_st = {k: g.set_index("datetime")["wl_src"] for k, g in wl_p.groupby("station_id")}
    map_idx = mapping.set_index("station_id")
    lag_idx = lag_tbl.set_index("station_id")

    parts: list[pd.DataFrame] = []
    for sid, g in panel.groupby("station_id", sort=True):
        g = g.sort_values("datetime").copy()
        u1, m1 = _upstream_slot_series(g, sid, 1, map_idx, lag_idx, wl_by_st)
        u2, m2 = _upstream_slot_series(g, sid, 2, map_idx, lag_idx, wl_by_st)
        g["upstream_wl_1"] = u1.values
        g["upstream_wl_2"] = u2.values
        g["upstream_wl_1_mask"] = m1.values
        g["upstream_wl_2_mask"] = m2.values
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def fit_transform_scalers(
    train_df: pd.DataFrame,
    cols: list[str],
) -> tuple[pd.DataFrame, dict[str, StandardScaler]]:
    """GroupNormalizer 대체: 관측소별 train 구간만 fit."""
    scalers: dict[str, StandardScaler] = {}
    out = train_df.copy()
    for sid, g in train_df.groupby("station_id"):
        idx = g.index
        for col in cols:
            if col not in g.columns:
                continue
            key = f"{sid}|{col}"
            sc = StandardScaler()
            v = g[[col]].astype(float)
            mask = v.notna().all(axis=1)
            if mask.sum() < 2:
                continue
            sc.fit(v[mask])
            scalers[key] = sc
            transformed = sc.transform(v.fillna(v[mask].mean()))
            out.loc[idx, col] = transformed.ravel()
    return out, scalers


def apply_scalers(df: pd.DataFrame, scalers: dict[str, StandardScaler], cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for sid, g in df.groupby("station_id"):
        idx = g.index
        for col in cols:
            key = f"{sid}|{col}"
            if key not in scalers or col not in g.columns:
                continue
            v = g[[col]].astype(float)
            mask = v[col].notna()
            fill = float(v.loc[mask, col].mean()) if mask.any() else 0.0
            out.loc[idx, col] = scalers[key].transform(v.fillna(fill)).ravel()
    return out


def build_panel(args: argparse.Namespace) -> tuple[pd.DataFrame, dict]:
    load_dotenv_root()
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    stations = load_station_list(Path(args.stations_csv), args.max_stations)
    if not stations:
        raise SystemExit("No station codes.")

    print(f"Stations: {len(stations)} | calendar {args.start} .. {args.end}")
    sk = build_skeleton(stations, start, end)
    sk = sk[sk["split"] != "drop"].copy()

    mapping = pd.read_csv(Path(args.upstream_map_csv), dtype=str)
    mapping["station_id"] = mapping["station_id"].astype(str).str.strip()
    lag_tbl = load_lag_table(Path(args.upstream_map_csv), Path(args.lag_manifest))

    wl_obscds = collect_waterlevel_station_ids(stations, mapping)
    s3, bucket = make_s3()
    print(f"Loading S3 waterlevel … ({len(wl_obscds)} obs incl. upstream)")
    wl_long = load_waterlevel_range(s3, bucket, start, end, wl_obscds)

    rain_map = load_rainfall_map(Path(args.rainfall_map_csv))
    aws_ids = set(
        rain_map.loc[rain_map["station_id"].isin(stations), "stn_id_aws"].dropna().unique()
    )
    print("Loading S3 KMA rainfall (RN) …")
    rn_long = load_kma_rainfall_range(s3, bucket, start, end, aws_ids)

    panel = sk.merge(
        wl_long.rename(columns={"obscd": "station_id", "value": TARGET_COL}),
        on=["station_id", "datetime"],
        how="left",
    )

    panel = panel.merge(rain_map, on="station_id", how="left")
    panel = panel.merge(
        rn_long,
        left_on=["stn_id_aws", "datetime"],
        right_on=["stn_id", "datetime"],
        how="left",
    )
    panel = panel.drop(columns=["stn_id"], errors="ignore")

    panel = attach_upstream(panel, wl_long, mapping, lag_tbl)

    parts: list[pd.DataFrame] = []
    for sid, g in panel.groupby("station_id", sort=True):
        g = g.sort_values("datetime").copy()
        train_mask = g["split"] == "train"
        g[TARGET_COL] = remove_outliers_iqr(g[TARGET_COL])
        wl_imp, was_imp, wl_branch = impute_waterlevel(
            g[TARGET_COL], train_mask=train_mask, interp_limit=args.interp_limit
        )
        g[TARGET_COL] = wl_imp
        g["was_imputed"] = was_imp
        g["impute_branch"] = wl_branch
        for uc in ("upstream_wl_1", "upstream_wl_2"):
            if uc in g.columns:
                g[uc] = remove_outliers_iqr(g[uc])
                g[uc], _, _ = impute_waterlevel(
                    g[uc], train_mask=train_mask, interp_limit=args.interp_limit
                )
        if RN_COL in g.columns:
            g[RN_COL] = g[RN_COL].fillna(0.0)
        g[DIFF_COL] = g[TARGET_COL].diff()
        parts.append(g)

    panel = pd.concat(parts, ignore_index=True)
    eligibility = build_station_eligibility(
        panel,
        min_required_hours=int(args.encoder_length) + int(args.prediction_length),
    )
    panel = add_time_features(panel)
    panel = add_time_idx(panel)

    scale_cols = [
        c
        for c in [TARGET_COL, DIFF_COL, RN_COL, "upstream_wl_1", "upstream_wl_2"]
        if c in panel.columns
    ]
    _, scalers = fit_transform_scalers(panel.loc[panel["split"] == "train"], scale_cols)
    panel_scaled = apply_scalers(panel, scalers, scale_cols)

    n_included = int((eligibility["included_tft_train"] == "Y").sum())
    meta = {
        "stations": stations,
        "stations_included_tft_train": n_included,
        "scale_cols": scale_cols,
        "train": [DEFAULT_TRAIN_START, DEFAULT_TRAIN_END],
        "val": [DEFAULT_VAL_START, DEFAULT_VAL_END],
        "test": [DEFAULT_TEST_START, DEFAULT_TEST_END],
        "interp_limit_h": args.interp_limit,
        "missing_rate_thresh": MISSING_RATE_THRESH,
        "encoder_length": args.encoder_length,
        "prediction_length": args.prediction_length,
        "eligibility_csv": str(Path(args.eligibility_out).resolve()),
    }
    return panel_scaled, {"scalers": scalers, "meta": meta, "eligibility": eligibility}


def main() -> int:
    p = argparse.ArgumentParser(description="TFT panel preprocess (S3 → local parquet)")
    p.add_argument("--start", default="2023-03-01")
    p.add_argument("--end", default="2025-10-31")
    p.add_argument("--stations-csv", default=str(OBS_TARGET))
    p.add_argument("--rainfall-map-csv", default=str(RAIN_MAP))
    p.add_argument("--upstream-map-csv", default=str(UPSTREAM_MAP))
    p.add_argument("--lag-manifest", default=str(LAG_MANIFEST))
    p.add_argument("--out-dir", default=str(ROOT / "data" / "tft_processed"))
    p.add_argument("--max-stations", type=int, default=None)
    p.add_argument("--interp-limit", type=int, default=INTERP_LIMIT_H)
    p.add_argument("--encoder-length", type=int, default=168)
    p.add_argument("--prediction-length", type=int, default=6)
    p.add_argument(
        "--eligibility-out",
        type=str,
        default=str(ELIGIBILITY_OUT),
        help="관측소 자격 CSV (before_training §1.5)",
    )
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    panel, aux = build_panel(args)
    elig_path = Path(args.eligibility_out)
    elig_path.parent.mkdir(parents=True, exist_ok=True)
    aux["eligibility"].to_csv(elig_path, index=False, encoding="utf-8-sig")
    print(
        f"Wrote eligibility: {elig_path} "
        f"({(aux['eligibility']['included_tft_train'] == 'Y').sum()}/"
        f"{len(aux['eligibility'])} included for TFT train)"
    )
    panel_path = out_dir / "tft_panel.parquet"
    panel.to_parquet(panel_path, index=False)

    import joblib

    joblib.dump(aux["scalers"], out_dir / "scalers.joblib")
    with open(out_dir / "preprocess_meta.json", "w", encoding="utf-8") as f:
        json.dump(aux["meta"], f, indent=2, ensure_ascii=False)

    for split in ("train", "val", "test"):
        sub = panel[panel["split"] == split]
        sub.to_parquet(out_dir / f"tft_{split}.parquet", index=False)
        print(f"  {split}: {len(sub):,} rows → {out_dir / f'tft_{split}.parquet'}")

    print(f"Wrote panel ({len(panel):,} rows) → {panel_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
