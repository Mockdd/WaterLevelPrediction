"""
XGBoost 학습용 패널 전처리 스크립트 (src/tft_preprocess.py 정합 계승).

과거 72시간(3일)의 데이터창을 수평 피처(Lagged Features)로 변환하고,
미래 1, 2, 3, 6시간 후의 수위(Target)를 Multi-Output 형태로 생성합니다.

Usage::
  python src/xgb_preprocess.py --max-stations 5 --out-dir data/xgb_processed
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

# 기본 학습/검증/테스트 달력 분기 기준 계승
DEFAULT_TRAIN_START = "2023-03-01"
DEFAULT_TRAIN_END = "2024-08-31"
DEFAULT_VAL_START = "2024-09-01"
DEFAULT_VAL_END = "2025-03-31"
DEFAULT_TEST_START = "2025-04-01"
DEFAULT_TEST_END = "2025-10-31"

INTERP_LIMIT_H = 24
MISSING_RATE_THRESH = 0.30
FLOOD_SEASON_MONTHS = (7, 8, 9)

# 메타데이터 경로 계승
OBS_STATIONS_CSV = ROOT / "metadata_outputs" / "obsFinalStreamReg.csv"
RAIN_MAP = ROOT / "metadata_outputs" / "obsWaterLevel_top1.csv"
UPSTREAM_MAP = ROOT / "metadata_outputs" / "upstream_mapping_must.csv"
LAG_MANIFEST = ROOT / "metadata_outputs" / "upstream_lag_manifest.json"

TARGET_COL = "wl"
DIFF_COL = "wl_diff"
RN_COL = "rn"

# 예측하려는 미래 Horizon 시점들 (1시간 후, 2시간 후, 3시간 후, 6시간 후)
TARGET_HORIZONS = [1, 2, 3, 6]


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
    t = pd.to_datetime(dt)
    out = pd.Series("drop", index=t.index, dtype=object)
    out[(t >= DEFAULT_TRAIN_START) & (t <= DEFAULT_TRAIN_END)] = "train"
    out[(t >= DEFAULT_VAL_START) & (t <= DEFAULT_VAL_END)] = "val"
    out[(t >= DEFAULT_TEST_START) & (t <= DEFAULT_TEST_END)] = "test"
    return out


def build_skeleton(stations: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    idx = pd.date_range(start.normalize(), end.normalize() + pd.Timedelta(hours=23), freq="h")
    parts = [pd.DataFrame({"station_id": sid, "datetime": idx}) for sid in stations]
    sk = pd.concat(parts, ignore_index=True)
    sk["split"] = assign_split(sk["datetime"])
    return sk


def remove_outliers_iqr(s: pd.Series, train_mask: pd.Series, datetimes: pd.Series | None = None, k: float = 4.0) -> pd.Series:
    tm = train_mask.reindex(s.index, fill_value=False)
    train_vals = s.loc[tm].dropna()
    if len(train_vals) < 10:
        return s
    q1, q3 = train_vals.quantile(0.25), train_vals.quantile(0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return s
    lo, hi = q1 - k * iqr, q3 + k * iqr
    out = s.copy()
    is_out = (out < lo) | (out > hi)
    if datetimes is not None:
        dt = pd.to_datetime(datetimes).reindex(s.index)
        is_out = is_out & ~dt.dt.month.isin(FLOOD_SEASON_MONTHS)
    out.loc[is_out] = np.nan
    return out


def impute_waterlevel(s: pd.Series, train_mask: pd.Series, interp_limit: int) -> pd.Series:
    tm = train_mask.reindex(s.index, fill_value=False)
    train_s = s.loc[tm]
    miss_rate = float(train_s.isna().mean()) if len(train_s) else 1.0
    if miss_rate >= MISSING_RATE_THRESH:
        return s
    return s.interpolate(method="linear", limit_area="inside", limit=interp_limit)


def _upstream_slot_series(panel: pd.DataFrame, sid: str, slot: int, map_idx: pd.DataFrame, lag_idx: pd.DataFrame, wl_by_st: dict[str, pd.Series]) -> tuple[pd.Series, pd.Series]:
    u_col = f"upstream_{slot}"
    lag_col = f"lag_steps_upstream_{slot}"
    lag0_col = f"upstream_{slot}_lag0"
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


def attach_upstream(panel: pd.DataFrame, wl_by_st: dict[str, pd.Series], mapping: pd.DataFrame, lag_tbl: pd.DataFrame) -> pd.DataFrame:
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


def build_xgb_features_and_targets(panel: pd.DataFrame, window_size: int) -> pd.DataFrame:
    """
    XGBoost 학습을 위해 3일(72시간) 과거 창을 시점별 가로 열(Lagged Features)로 눕히고,
    미래 1, 2, 3, 6시간 뒤의 데이터를 Target Column으로 생성합니다.
    """
    feature_cols = [TARGET_COL, DIFF_COL, RN_COL, "upstream_wl_1", "upstream_wl_2"]
    final_parts = []

    for sid, g in panel.groupby("station_id"):
        g = g.sort_values("datetime").copy()

        # 1. 미래 다중 시점 Target 생성 (t+1, t+2, t+3, t+6)
        for h in TARGET_HORIZONS:
            g[f"target_lead_{h}"] = g[TARGET_COL].shift(-h)

        # 2. 과거 window_size(72) 동안의 시차 피처(Lagged Features) 생성
        lag_frames = []
        for lag in range(window_size):
            # lag=0 이 현재 시점(t), lag=1이 1시간 전(t-1) ...
            lag_df = g[feature_cols].shift(lag)
            lag_df.columns = [f"{col}_lag_{lag}" for col in feature_cols]
            lag_frames.append(lag_df)

        lags_combined = pd.concat(lag_frames, axis=1)

        # 3. 기본 메타데이터 및 미래 시점 고정 정보(달력 변수) 결합
        meta_df = g[["station_id", "datetime", "split"]].copy()
        dt = meta_df["datetime"]
        hour = dt.dt.hour + dt.dt.minute / 60.0
        month = dt.dt.month

        meta_df["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
        meta_df["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
        meta_df["month_sin"] = np.sin(2 * np.pi * (month - 1) / 12.0)
        meta_df["month_cos"] = np.cos(2 * np.pi * (month - 1) / 12.0)

        # Target 컬럼들 결합
        target_cols = [f"target_lead_{h}" for h in TARGET_HORIZONS]
        targets_df = g[target_cols]

        station_panel = pd.concat([meta_df, lags_combined, targets_df], axis=1)

        # 과거 윈도우 유실 및 미래 타겟 유실 행 제거 (Drop NaN)
        # 과거 데이터가 온전히 확보되고 미래 6시간 뒤 정답까지 존재하는 행만 남김
        station_panel = station_panel.dropna(subset=[f"{TARGET_COL}_lag_{window_size-1}"] + target_cols)
        final_parts.append(station_panel)

    return pd.concat(final_parts, ignore_index=True)


def main() -> int:
    p = argparse.ArgumentParser(description="XGBoost용 가로 압축 패널 데이터 전처리")
    p.add_argument("--start", default="2023-03-01")
    p.add_argument("--end", default="2025-10-31")
    p.add_argument("--stations-csv", default=str(RAIN_MAP))
    p.add_argument("--rainfall-map-csv", default=str(RAIN_MAP))
    p.add_argument("--upstream-map-csv", default=str(UPSTREAM_MAP))
    p.add_argument("--lag-manifest", default=str(LAG_MANIFEST))
    p.add_argument("--out-dir", default=str(ROOT / "data" / "xgb_processed"))
    p.add_argument("--max-stations", type=int, default=None)
    p.add_argument("--interp-limit", type=int, default=INTERP_LIMIT_H)
    p.add_argument("--encoder-length", type=int, default=72, help="과거 관찰 시간 범위 (3일 = 72시간)")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    load_dotenv_root()
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)

    stations = load_station_list(Path(args.stations_csv), args.max_stations)
    if not stations:
        raise SystemExit("No station codes.")

    print(f"[XGB Preprocess] 관측소 개수: {len(stations)} | 기간: {args.start} ~ {args.end}")
    sk = build_skeleton(stations, start, end)
    sk = sk[sk["split"] != "drop"].copy()

    mapping = pd.read_csv(Path(args.upstream_map_csv), dtype=str)
    mapping["station_id"] = mapping["station_id"].astype(str).str.strip()
    lag_tbl = load_lag_table(Path(args.upstream_map_csv), Path(args.lag_manifest))

    wl_obscds = collect_waterlevel_station_ids(stations, mapping)
    s3, bucket = make_s3()

    print("S3 수위 데이터 로드 중...")
    wl_long = load_waterlevel_range(s3, bucket, start, end, wl_obscds)

    print("S3 강수 데이터 로드 중...")
    rain_map = load_rainfall_map(Path(args.rainfall_map_csv))
    aws_ids = set(rain_map.loc[rain_map["station_id"].isin(stations), "stn_id_aws"].dropna().unique())
    rn_long = load_kma_rainfall_range(s3, bucket, start, end, aws_ids)

    print("수위 데이터 정제 및 이상치 처리 중 (Jul-Sep 보호)...")
    sk_split = sk[["datetime", "split"]].drop_duplicates()
    frames, wl_by_st = [], {}

    for sid in sorted(wl_obscds):
        sub = wl_long[wl_long["obscd"] == sid].copy()
        if sub.empty:
            continue
        sub = sub.merge(sk_split, on="datetime", how="left")
        sub = sub.sort_values("datetime").reset_index(drop=True)

        # 이상치 정제 및 보간 적용
        train_mask = sub["split"] == "train"
        after_outlier = remove_outliers_iqr(sub["value"].copy(), train_mask=train_mask, datetimes=sub["datetime"])
        wl_imp = impute_waterlevel(after_outlier, train_mask=train_mask, interp_limit=args.interp_limit)

        sub["station_id"] = sid
        sub[TARGET_COL] = wl_imp.values
        frames.append(sub[["station_id", "datetime", TARGET_COL]])
        wl_by_st[sid] = pd.Series(wl_imp.values, index=pd.to_datetime(sub["datetime"]))

    cleaned_wl_long = pd.concat(frames, ignore_index=True)
    panel = sk.merge(cleaned_wl_long, on=["station_id", "datetime"], how="left")
    panel = panel.merge(rain_map, on="station_id", how="left")
    panel = panel.merge(rn_long, left_on=["stn_id_aws", "datetime"], right_on=["stn_id", "datetime"], how="left")
    panel = panel.drop(columns=["stn_id"], errors="ignore")

    panel = attach_upstream(panel, wl_by_st, mapping, lag_tbl)

    print("과거 및 차분 결측치 보충 중...")
    parts = []
    for sid, g in panel.groupby("station_id", sort=True):
        g = g.sort_values("datetime").copy()
        train_mask = g["split"] == "train"
        for uc in ("upstream_wl_1", "upstream_wl_2"):
            if uc in g.columns:
                g[uc] = impute_waterlevel(g[uc], train_mask=train_mask, interp_limit=args.interp_limit)
        if RN_COL in g.columns:
            g[RN_COL] = g[RN_COL].fillna(0.0)
        g[DIFF_COL] = g[TARGET_COL].diff()
        parts.append(g)

    panel = pd.concat(parts, ignore_index=True)

    # NaN 방어 조치
    for uc in ("upstream_wl_1", "upstream_wl_2", DIFF_COL, TARGET_COL):
        if uc in panel.columns:
            panel[uc] = panel[uc].fillna(0.0)

    print(f"XGBoost Tabular 형태로 변환 중 (윈도우 크기: {args.encoder_length}시간)...")
    xgb_panel = build_xgb_features_and_targets(panel, window_size=int(args.encoder_length))

    # 스케일링 대상 피처 컬럼들 자동 수집 (생성된 모든 Lagged 변수들 대상)
    scale_cols = [c for c in xgb_panel.columns if any(p in c for p in ["_lag_"])]

    # Train 세트 기준으로 정규화 학습(Fit)
    train_df = xgb_panel[xgb_panel["split"] == "train"]
    scalers = {}
    train_fill = {}

    for sid, g in train_df.groupby("station_id"):
        for col in scale_cols:
            key = f"{sid}|{col}"
            v = g[[col]].astype(float)
            mask = v[col].notna()
            if mask.sum() < 2:
                continue
            sc = StandardScaler()
            sc.fit(v.loc[mask])
            scalers[key] = sc
            train_fill[key] = float(v.loc[mask, col].mean())

    # 스케일링 배포 변환 (Leakage 방지)
    print("관측소별 피처 스케일링 진행 중...")
    for sid, g in xgb_panel.groupby("station_id"):
        idx = g.index
        for col in scale_cols:
            key = f"{sid}|{col}"
            if key not in scalers:
                continue
            v = g[[col]].astype(float)
            fill = train_fill.get(key, 0.0)
            xgb_panel.loc[idx, col] = scalers[key].transform(v.fillna(fill)).ravel()

    # 무한값 및 유출 잔여결측치 0.0 최종 방어
    xgb_panel[scale_cols] = xgb_panel[scale_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # 최종 세트 저장 분기
    import joblib
    joblib.dump({"scalers": scalers, "train_fill": train_fill}, out_dir / "xgb_scalers.joblib")

    for split in ("train", "val", "test"):
        sub = xgb_panel[xgb_panel["split"] == split]
        sub.to_parquet(out_dir / f"xgb_{split}.parquet", index=False)
        print(f"  {split}: {len(sub):,} 행 추출 완료 -> {out_dir / f'xgb_{split}.parquet'}")

    print("XGBoost 데이터 전처리가 성공적으로 완료되었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())