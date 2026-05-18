"""
TFT validation / test 평가 (Darts).

학습된 모델로 val·test 구간 rolling forecast → 역스케일 → 지표 산출.

Usage::

  python -u src/eval_tft_darts.py --split val --experiment-name smoke_sample_v1 \
    --processed-dir data/tft_processed_sample_train \
    --eligibility-csv data/tft_processed_sample_train/tft_station_eligibility.csv

  python -u src/eval_tft_darts.py --split test --experiment-name baseline_full_v1
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

from tft_eval_common import (
    TARGET_COL,
    compute_metrics,
    df_to_timeseries,
    forecast_median_series,
    inverse_scale_column,
    load_eligible_station_ids,
    load_panel,
    load_preprocess_meta,
    load_scalers,
    load_train_args,
    series_list_for_eval,
    split_end_ts,
    split_start_ts,
    summarize_dataset,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Evaluate Darts TFT on val/test splits",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--split", choices=("val", "test", "both"), default="val")
    p.add_argument("--processed-dir", type=str, default=str(_ROOT.parent / "data" / "tft_processed"))
    p.add_argument(
        "--eligibility-csv",
        type=str,
        default=str(_ROOT.parent / "metadata_outputs" / "tft_station_eligibility.csv"),
    )
    p.add_argument("--work-dir", type=str, default=str(_ROOT.parent / "experiments" / "tft"))
    p.add_argument("--experiment-name", type=str, required=True)
    p.add_argument(
        "--model-path",
        type=str,
        default=None,
        help="미지정 시 work-dir/experiment-name/tft_model.pt",
    )
    p.add_argument("--target-col", default=TARGET_COL)
    p.add_argument("--past-cov-cols", type=str, default=None, help="미지정 시 train_args.json")
    p.add_argument("--future-cov-cols", type=str, default=None)
    p.add_argument("--input-chunk-length", type=int, default=None)
    p.add_argument("--output-chunk-length", type=int, default=None)
    p.add_argument("--stride", type=int, default=None, help="rolling origin 간격(시간). 기본=output_chunk_length")
    p.add_argument("--num-samples", type=int, default=200, help="quantile 추정용 샘플 수. 높을수록 정확하나 느림")
    p.add_argument("--max-series", type=int, default=None)
    p.add_argument("--leads", type=str, default="1,2,3,6", help="리드(시간)별 지표")
    p.add_argument("--out-dir", type=str, default=None, help="기본: work-dir/experiment-name/eval_{split}")
    return p


def _resolve_train_cfg(args: argparse.Namespace, exp_dir: Path) -> dict:
    train_args = load_train_args(exp_dir)
    cfg = {
        "target_col": args.target_col or train_args.get("target_col", TARGET_COL),
        "past_cov_cols": args.past_cov_cols or train_args.get("past_cov_cols", ""),
        "future_cov_cols": args.future_cov_cols or train_args.get("future_cov_cols", ""),
        "input_chunk_length": args.input_chunk_length or train_args.get("input_chunk_length", 168),
        "output_chunk_length": args.output_chunk_length or train_args.get("output_chunk_length", 6),
    }
    cfg["past_cols"] = [c.strip() for c in str(cfg["past_cov_cols"]).split(",") if c.strip()]
    cfg["future_cols"] = [c.strip() for c in str(cfg["future_cov_cols"]).split(",") if c.strip()]
    return cfg


def rolling_forecasts_for_station(
    model,
    panel_st: pd.DataFrame,
    *,
    eval_split: str,
    horizon: int,
    stride: int,
    min_hist: int,
    num_samples: int,
    target_col: str,
    past_cols: list[str],
    future_cols: list[str],
) -> pd.DataFrame:
    eval_start = split_start_ts(eval_split)
    eval_end = split_end_ts(eval_split)
    g = panel_st.sort_values("datetime").reset_index(drop=True)
    sid = str(g["station_id"].iloc[0])

    if len(g) < horizon + 2:
        print(f"[{sid}] Skipped: insufficient total data length ({len(g)} < {horizon + 2})")
        return pd.DataFrame()

    last_origin = eval_end - pd.Timedelta(hours=horizon)
    # val 시작 시점이 아닌 min_hist 이후부터 origin 시작
    # → 첫 origin에서 encoder 입력(min_hist)이 항상 충분히 확보됨
    first_origin = eval_start + pd.Timedelta(hours=min_hist)
    origins = g.loc[(g["datetime"] >= first_origin) & (g["datetime"] <= last_origin), "datetime"]
    origins = origins.iloc[::stride]

    if origins.empty:
        print(f"[{sid}] Skipped: no valid origins for forecast in split {eval_split}")
        return pd.DataFrame()

    rows: list[dict] = []
    total_origins = len(origins)
    print(f"[{sid}] Evaluation start... Total origins: {total_origins}")

    for i, origin in enumerate(origins):
        origin = pd.Timestamp(origin)
        try:
            g_hist = g[g["datetime"] <= origin]
            if len(g_hist) < min_hist:
                continue
            # target 컬럼이 전부 NaN이면 예측 불가 → 스킵
            if g_hist[target_col].isna().all():
                continue

            series_cut, past_cut, _ = df_to_timeseries(
                g_hist, target_col, past_cols, future_cols
            )

            fut_cut = None
            if future_cols:
                fut_start_dt = series_cut.start_time()
                fut_end_dt = origin + pd.Timedelta(hours=horizon)
                complete_dt_range = pd.date_range(start=fut_start_dt, end=fut_end_dt, freq="h")

                # target_col 포함하여 선택 (df_to_timeseries KeyError 방지)
                all_cols = list(set([target_col] + past_cols + future_cols))
                g_fut_subset = g[["datetime"] + all_cols].set_index("datetime")
                g_fut_continuous = g_fut_subset.reindex(complete_dt_range).ffill().bfill()
                g_fut_df = g_fut_continuous.reset_index().rename(columns={"index": "datetime"})

                _, _, fut_cut = df_to_timeseries(g_fut_df, target_col, past_cols, future_cols)

            if i % 50 == 0:
                print(f"  [{sid}] step {i}/{total_origins} (origin: {origin})")

            pred = model.predict(
                n=horizon,
                series=series_cut,
                past_covariates=past_cut,
                future_covariates=fut_cut,
                num_samples=num_samples,
            )
            med = forecast_median_series(pred)
            for lead_h, (t_end, yhat) in enumerate(med.items(), start=1):
                t_end = pd.Timestamp(t_end)
                rows.append(
                    {
                        "station_id": sid,
                        "origin_datetime": origin,
                        "target_datetime": t_end,
                        "lead_h": lead_h,
                        "yhat": float(yhat),
                    }
                )
        except Exception as exc:
            print(f"  [{sid}] ERROR at origin {origin}: {exc}")
            rows.append(
                {
                    "station_id": sid,
                    "origin_datetime": origin,
                    "target_datetime": pd.NaT,
                    "lead_h": -1,
                    "yhat": np.nan,
                    "error": str(exc),
                }
            )

    return pd.DataFrame(rows)


def inverse_predictions(
    pred_df: pd.DataFrame,
    scalers: dict,
    target_col: str,
) -> pd.DataFrame:
    out = pred_df.copy()
    out["yhat_physical"] = np.nan
    out["y_physical"] = np.nan
    for sid, idx in out.groupby("station_id").groups.items():
        try:
            out.loc[idx, "yhat_physical"] = inverse_scale_column(
                sid, out.loc[idx, "yhat"].values, scalers, target_col
            )
            out.loc[idx, "y_physical"] = inverse_scale_column(
                sid, out.loc[idx, "y"].values, scalers, target_col
            )
        except KeyError:
            continue
    return out


def aggregate_metrics(pred_df: pd.DataFrame, leads: list[int]) -> dict:
    overall: dict = {}
    by_lead: dict = {}
    by_station: list[dict] = []

    valid = pred_df[pred_df["lead_h"] > 0].copy()
    for lead in leads:
        sub = valid[valid["lead_h"] == lead]
        by_lead[str(lead)] = compute_metrics(
            sub["y_physical"].values, sub["yhat_physical"].values
        )

    by_lead["all"] = compute_metrics(
        valid["y_physical"].values, valid["yhat_physical"].values
    )
    overall["by_lead"] = by_lead

    for (sid, lead), g in valid.groupby(["station_id", "lead_h"]):
        m = compute_metrics(g["y_physical"].values, g["yhat_physical"].values)
        by_station.append({"station_id": sid, "lead_h": int(lead), **m})
    overall["by_station"] = by_station
    return overall


def run_eval_split(
    split: str,
    *,
    model,
    panel: pd.DataFrame,
    scalers: dict,
    cfg: dict,
    allowed: set[str],
    args: argparse.Namespace,
    out_dir: Path,
) -> dict:
    horizon = int(cfg["output_chunk_length"])
    stride = args.stride or horizon
    min_len = int(cfg["input_chunk_length"]) + horizon + 1
    leads = [int(x) for x in args.leads.split(",") if x.strip()]

    _, _, _, ids, _, _ = series_list_for_eval(
        panel,
        eval_split=split,
        target_col=cfg["target_col"],
        past_cols=cfg["past_cols"],
        future_cols=cfg["future_cols"],
        min_len=min_len,
        allowed_stations=allowed or None,
        max_series=args.max_series,
    )
    if not ids:
        raise SystemExit(f"No series for split={split}. Check panel / eligibility.")

    frames: list[pd.DataFrame] = []
    for sid in ids:
        g_st = panel[panel["station_id"] == sid]
        pdf = rolling_forecasts_for_station(
            model,
            g_st,
            eval_split=split,
            horizon=horizon,
            stride=stride,
            min_hist=min_len,
            num_samples=args.num_samples,
            target_col=cfg["target_col"],
            past_cols=cfg["past_cols"],
            future_cols=cfg["future_cols"],
        )
        if not pdf.empty:
            frames.append(pdf)

    if not frames:
        raise SystemExit(f"No forecasts produced for split={split}")

    pred = pd.concat(frames, ignore_index=True)
    pred = pred[pred["lead_h"] > 0]
    act = panel[["station_id", "datetime", cfg["target_col"]]].rename(
        columns={"datetime": "target_datetime", cfg["target_col"]: "y"}
    )
    pred = pred.merge(act, on=["station_id", "target_datetime"], how="left")
    pred = inverse_predictions(pred, scalers, cfg["target_col"])

    out_dir.mkdir(parents=True, exist_ok=True)
    pred_path = out_dir / f"predictions_{split}.parquet"
    pred.to_parquet(pred_path, index=False)

    metrics = aggregate_metrics(pred, leads)
    metrics["split"] = split
    metrics["n_stations"] = len(ids)
    metrics["n_forecast_rows"] = int(len(pred))
    metrics["stride_h"] = stride
    metrics["horizon_h"] = horizon
    metrics["leads_evaluated"] = leads

    metrics_path = out_dir / f"metrics_{split}.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    station_df = pd.DataFrame(metrics["by_station"])
    station_df.to_csv(out_dir / f"metrics_by_station_{split}.csv", index=False, encoding="utf-8-sig")

    lead_df = pd.DataFrame(
        [{"lead_h": int(k) if k != "all" else "all", **v} for k, v in metrics["by_lead"].items()]
    )
    lead_df.to_csv(out_dir / f"metrics_by_lead_{split}.csv", index=False, encoding="utf-8-sig")

    print(f"\n=== {split.upper()} ===")
    print(f"  stations: {len(ids)} | forecast rows: {len(pred):,}")
    for lead in leads:
        m = metrics["by_lead"].get(str(lead), {})
        print(
            f"  lead {lead}h: MAE={m.get('mae', float('nan')):.4f} "
            f"RMSE={m.get('rmse', float('nan')):.4f} NSE={m.get('nse', float('nan')):.4f} (n={m.get('n', 0)})"
        )
    print(f"  wrote {pred_path}")
    print(f"  wrote {metrics_path}")
    return metrics


def main() -> int:
    args = build_parser().parse_args()
    processed_dir = Path(args.processed_dir)
    exp_dir = Path(args.work_dir) / args.experiment_name
    model_path = Path(args.model_path) if args.model_path else exp_dir / "tft_model.pt"
    if not model_path.is_file():
        raise SystemExit(f"Model not found: {model_path}")

    try:
        from darts.models import TFTModel
    except ImportError as e:
        raise SystemExit("pip install 'darts[torch]' pytorch-lightning torch") from e

    panel = load_panel(processed_dir)
    scalers, _train_fill = load_scalers(processed_dir)
    meta = load_preprocess_meta(processed_dir)
    allowed = load_eligible_station_ids(Path(args.eligibility_csv))
    cfg = _resolve_train_cfg(args, exp_dir)

    splits = ["val", "test"] if args.split == "both" else [args.split]
    base_out = Path(args.out_dir) if args.out_dir else exp_dir / f"eval_{args.split}"

    ds_summary = summarize_dataset(
        panel,
        eligibility_csv=Path(args.eligibility_csv),
        preprocess_meta=meta,
        allowed_stations=allowed or None,
    )
    ds_summary["eval"] = {
        "experiment_name": args.experiment_name,
        "model_path": str(model_path.resolve()),
        "splits_run": splits,
        "stride_h": args.stride or cfg["output_chunk_length"],
        "num_samples": args.num_samples,
        "leads": args.leads,
    }
    summary_path = base_out / "dataset_summary.json"
    base_out.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(ds_summary, f, indent=2, ensure_ascii=False)
    print(f"Dataset summary → {summary_path}")

    print(f"Loading model {model_path}")
    model = TFTModel.load(str(model_path))

    all_metrics: dict = {}
    for split in splits:
        out_dir = base_out if len(splits) == 1 else base_out / split
        all_metrics[split] = run_eval_split(
            split,
            model=model,
            panel=panel,
            scalers=scalers,
            cfg=cfg,
            allowed=allowed,
            args=args,
            out_dir=out_dir,
        )

    if len(splits) > 1:
        combined_path = base_out / "metrics_all.json"
        with open(combined_path, "w", encoding="utf-8") as f:
            json.dump(all_metrics, f, indent=2, ensure_ascii=False)
        print(f"\nCombined metrics → {combined_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
