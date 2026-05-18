"""TFT 평가 공통: 데이터 로드, 역스케일, 지표, 패널 요약."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

TARGET_COL = "wl"

# before_training.md §1.3 (tft_preprocess 와 동일)
SPLIT_RANGES = {
    "train": ("2023-03-01", "2024-08-31"),
    "val": ("2024-09-01", "2025-03-31"),
    "test": ("2025-04-01", "2025-10-31"),
}


def load_panel(processed_dir: Path) -> pd.DataFrame:
    path = processed_dir / "tft_panel.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path}")
    df = pd.read_parquet(path)
    df["station_id"] = df["station_id"].astype(str).str.strip()
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df


def load_scalers(processed_dir: Path) -> tuple[dict[str, Any], dict[str, float]]:
    path = processed_dir / "scalers.joblib"
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path}")
    obj = joblib.load(path)
    if isinstance(obj, dict) and "scalers" in obj:
        return obj["scalers"], obj.get("train_fill") or {}
    return obj, {}


def load_preprocess_meta(processed_dir: Path) -> dict[str, Any]:
    path = processed_dir / "preprocess_meta.json"
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_eligible_station_ids(eligibility_csv: Path) -> set[str]:
    if not eligibility_csv.is_file():
        return set()
    df = pd.read_csv(eligibility_csv, dtype=str)
    df["station_id"] = df["station_id"].astype(str).str.strip()
    ok = df["included_tft_train"].astype(str).str.upper() == "Y"
    return set(df.loc[ok, "station_id"])


def load_train_args(exp_dir: Path) -> dict[str, Any]:
    path = exp_dir / "train_args.json"
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def inverse_scale_column(
    station_id: str,
    values: np.ndarray,
    scalers: dict[str, Any],
    col: str = TARGET_COL,
) -> np.ndarray:
    key = f"{station_id}|{col}"
    if key not in scalers:
        raise KeyError(f"No scaler for {key}")
    sc = scalers[key]
    v = np.asarray(values, dtype=float).reshape(-1, 1)
    return sc.inverse_transform(v).ravel()


def split_start_ts(split: str) -> pd.Timestamp:
    if split not in SPLIT_RANGES:
        raise ValueError(f"Unknown split: {split}")
    return pd.Timestamp(SPLIT_RANGES[split][0])


def split_end_ts(split: str) -> pd.Timestamp:
    if split not in SPLIT_RANGES:
        raise ValueError(f"Unknown split: {split}")
    return pd.Timestamp(SPLIT_RANGES[split][1]) + pd.Timedelta(hours=23)


def df_to_timeseries(
    g: pd.DataFrame,
    target_col: str,
    past_cols: list[str],
    future_cols: list[str],
):
    from darts import TimeSeries

    g = g.sort_values("datetime").copy()

    # NaN 채우기: ffill → bfill → 0으로 남은 NaN 처리
    fill_cols = [c for c in [target_col] + past_cols + future_cols if c in g.columns]
    g[fill_cols] = g[fill_cols].ffill().bfill().fillna(0)

    ts_target = None
    if target_col and target_col in g.columns:
        ts_target = TimeSeries.from_dataframe(
            g,
            time_col="datetime",
            value_cols=target_col,
            fill_missing_dates=False,
            freq="h",
        )

    ts_past = None
    if past_cols:
        use = [c for c in past_cols if c in g.columns]
        if use:
            ts_past = TimeSeries.from_dataframe(
                g, time_col="datetime", value_cols=use, fill_missing_dates=False, freq="h"
            )

    ts_future = None
    if future_cols:
        use = [c for c in future_cols if c in g.columns]
        if use:
            ts_future = TimeSeries.from_dataframe(
                g, time_col="datetime", value_cols=use, fill_missing_dates=False, freq="h"
            )

    return ts_target, ts_past, ts_future


def series_list_for_eval(
    panel: pd.DataFrame,
    *,
    eval_split: str,
    target_col: str,
    past_cols: list[str],
    future_cols: list[str],
    min_len: int,
    allowed_stations: set[str] | None,
    max_series: int | None,
) -> tuple[list, list | None, list | None, list[str], pd.Timestamp, pd.Timestamp]:
    """
    평가용 시계열: train(및 val)까지 context 포함, ``eval_split`` 구간에서만 점수 산출.

    각 관측소에 대해 ``datetime <= eval_split 끝`` 까지의 패널로 TimeSeries 생성.
    """
    eval_end = split_end_ts(eval_split)
    targets, pasts, futures, ids = [], [], [], []
    for sid, g_all in panel.groupby("station_id", sort=True):
        sid_s = str(sid)
        if allowed_stations and sid_s not in allowed_stations:
            continue
        g = g_all[g_all["datetime"] <= eval_end].sort_values("datetime")
        if len(g) < min_len:
            continue
        # target 컬럼이 전부 NaN인 관측소 스킵
        if g[target_col].isna().all():
            print(f"[{sid_s}] Skipped: {target_col} all NaN")
            continue
        t, p, f = df_to_timeseries(g, target_col, past_cols, future_cols)
        if t is None or not np.isfinite(t.values()).any():
            continue
        targets.append(t)
        pasts.append(p)
        futures.append(f)
        ids.append(sid_s)
        if max_series is not None and len(ids) >= max_series:
            break
    eval_start = split_start_ts(eval_split)
    past_list = pasts if any(p is not None for p in pasts) else None
    fut_list = futures if any(f is not None for f in futures) else None
    return targets, past_list, fut_list, ids, eval_start, eval_end


def forecast_median_series(forecast_ts) -> pd.Series:
    """QuantileRegression 출력에서 중앙(0.5) 시계열 → pandas Series."""
    if hasattr(forecast_ts, "quantile") and getattr(forecast_ts, "n_samples", 1) > 1:
        try:
            med = forecast_ts.quantile(0.5)
            return med.to_series() if hasattr(med, "to_series") else pd.Series(
                med.values().flatten(), index=med.time_index
            )
        except Exception:
            pass
    if hasattr(forecast_ts, "to_dataframe"):
        pdf = forecast_ts.to_dataframe()
        if isinstance(pdf.columns, pd.MultiIndex):
            cols = pdf.columns.get_level_values(-1)
            if "0.5" in cols:
                return pdf.xs("0.5", axis=1, level=-1).iloc[:, 0]
        str_cols = pdf.columns.astype(str)
        if "0.5" in str_cols:
            return pdf[pdf.columns[str_cols == "0.5"][0]]
        return pdf.iloc[:, 0]
    return pd.Series(forecast_ts.values().flatten(), index=forecast_ts.time_index)


def forecasts_to_long_df(
    station_id: str,
    forecast_ts,
    *,
    eval_start: pd.Timestamp,
    eval_end: pd.Timestamp,
) -> pd.DataFrame:
    """historical_forecasts 결과 → (origin_time, lead, datetime, yhat_scaled)."""
    s = forecast_median_series(forecast_ts)
    s.index = pd.to_datetime(s.index)
    rows: list[dict] = []
    if hasattr(forecast_ts, "to_dataframe"):
        pdf = forecast_ts.to_dataframe().reset_index()
        time_col = pdf.columns[0]
        val_cols = [c for c in pdf.columns if c != time_col]
        for _, row in pdf.iterrows():
            t = pd.Timestamp(row[time_col])
            if t < eval_start or t > eval_end:
                continue
            for i, col in enumerate(val_cols, start=1):
                rows.append(
                    {
                        "station_id": station_id,
                        "datetime": t,
                        "lead_h": i,
                        "yhat": float(row[col]),
                    }
                )
        if rows:
            return pd.DataFrame(rows)
    for t, v in s.items():
        t = pd.Timestamp(t)
        if t < eval_start or t > eval_end:
            continue
        rows.append({"station_id": station_id, "datetime": t, "lead_h": 1, "yhat": float(v)})
    return pd.DataFrame(rows)


def attach_actuals(
    pred_df: pd.DataFrame,
    panel: pd.DataFrame,
    target_col: str,
) -> pd.DataFrame:
    """lead별 실제 시각 = origin + lead (1H freq)."""
    out = pred_df.copy()
    if "origin_datetime" not in out.columns:
        out["origin_datetime"] = out["datetime"] - pd.to_timedelta(out["lead_h"], unit="h")
    out["target_datetime"] = out["origin_datetime"] + pd.to_timedelta(out["lead_h"], unit="h")
    act = panel[["station_id", "datetime", target_col]].rename(
        columns={"datetime": "target_datetime", target_col: "y"}
    )
    out = out.merge(act, on=["station_id", "target_datetime"], how="left")
    return out


def compute_metrics(y: np.ndarray, yhat: np.ndarray) -> dict[str, float | int]:
    mask = np.isfinite(y) & np.isfinite(yhat)
    y = y[mask]
    yhat = yhat[mask]
    n = int(len(y))
    if n == 0:
        return {"n": 0, "mae": np.nan, "rmse": np.nan, "mape": np.nan, "nse": np.nan, "bias": np.nan}
    err = yhat - y
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err**2)))
    denom = np.maximum(np.abs(y), 1e-6)
    mape = float(np.mean(np.abs(err) / denom) * 100.0)
    ss_res = float(np.sum(err**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    nse = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else np.nan
    bias = float(np.mean(err))
    return {"n": n, "mae": mae, "rmse": rmse, "mape": mape, "nse": nse, "bias": bias}


def summarize_dataset(
    panel: pd.DataFrame,
    *,
    eligibility_csv: Path | None,
    preprocess_meta: dict[str, Any],
    allowed_stations: set[str] | None,
) -> dict[str, Any]:
    """평가 리포트용 데이터셋 요약."""
    summary: dict[str, Any] = {
        "preprocess_meta": preprocess_meta,
        "splits_calendar": SPLIT_RANGES,
        "by_split": {},
    }
    if eligibility_csv and eligibility_csv.is_file():
        elig = pd.read_csv(eligibility_csv, dtype=str)
        elig["station_id"] = elig["station_id"].astype(str).str.strip()
        summary["eligibility_csv"] = str(eligibility_csv.resolve())
        summary["n_stations_eligibility"] = int(len(elig))
        summary["n_included_tft_train"] = int(
            (elig["included_tft_train"].astype(str).str.upper() == "Y").sum()
        )
    for split in ("train", "val", "test"):
        sub = panel[panel["split"] == split]
        if allowed_stations:
            sub = sub[sub["station_id"].isin(allowed_stations)]
        wl = sub[TARGET_COL]
        summary["by_split"][split] = {
            "calendar": list(SPLIT_RANGES[split]),
            "n_rows": int(len(sub)),
            "n_stations": int(sub["station_id"].nunique()),
            "datetime_min": str(sub["datetime"].min()) if len(sub) else None,
            "datetime_max": str(sub["datetime"].max()) if len(sub) else None,
            "wl_missing_rate": float(wl.isna().mean()) if len(sub) else None,
            "wl_mean": float(wl.mean()) if wl.notna().any() else None,
        }
    return summary
