"""
Darts ``TemporalFusionTransformer`` 학습 스크립트 (FloodAX).

Colab 예::

  %cd /content/FloodAX
  !pip install -q "darts[torch]" pytorch-lightning torch pandas pyarrow
  !python src/tft_preprocess.py --out-dir /content/drive/MyDrive/floodax/tft_processed
  !python src/train_tft_darts.py --processed-dir /content/drive/MyDrive/floodax/tft_processed

Usage::

  python src/train_tft_darts.py --processed-dir data/tft_processed
  python src/train_tft_darts.py --run-preprocess --max-stations 10
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

import joblib
import numpy as np
import pandas as pd

TARGET_COL = "wl"
PAST_COV_COLS = [
    "wl_diff",
    "rn",
    "upstream_wl_1",
    "upstream_wl_2",
    "upstream_wl_1_mask",
    "upstream_wl_2_mask",
    "was_imputed",
]
FUTURE_COV_COLS = ["hour_sin", "hour_cos", "month_sin", "month_cos"]


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--processed-dir",
        type=str,
        default=str(_ROOT.parent / "data" / "tft_processed"),
        help="tft_preprocess 산출 디렉터리 (tft_panel.parquet, scalers.joblib)",
    )
    p.add_argument(
        "--run-preprocess",
        action="store_true",
        help="학습 전 tft_preprocess.py 실행",
    )


def add_data_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("data")
    g.add_argument("--target-col", default=TARGET_COL, help="타깃 시계열 컬럼")
    g.add_argument(
        "--past-cov-cols",
        type=str,
        default=",".join(PAST_COV_COLS),
        help="과거 공변량(쉼표 구분)",
    )
    g.add_argument(
        "--future-cov-cols",
        type=str,
        default=",".join(FUTURE_COV_COLS),
        help="미래 알려진 공변량(쉼표 구분)",
    )
    g.add_argument(
        "--min-train-length",
        type=int,
        default=None,
        help="관측소별 최소 train 길이 (미지정 시 encoder+output+1)",
    )
    g.add_argument(
        "--max-series",
        type=int,
        default=None,
        help="디버그: 학습에 쓸 최대 관측소 수",
    )


def add_model_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("model (TFT)")
    g.add_argument("--input-chunk-length", type=int, default=168, help="encoder 길이 E")
    g.add_argument("--output-chunk-length", type=int, default=6, help="예측 horizon H")
    g.add_argument("--hidden-size", type=int, default=64)
    g.add_argument("--lstm-layers", type=int, default=1)
    g.add_argument("--num-attention-heads", type=int, default=4)
    g.add_argument("--dropout", type=float, default=0.1)
    g.add_argument("--hidden-continuous-size", type=int, default=16)
    g.add_argument("--add-relative-index", action=argparse.BooleanOptionalAction, default=True)


def add_experiment_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("experiment")
    g.add_argument("--experiment-name", default="floodax_tft_baseline")
    g.add_argument("--work-dir", default=str(_ROOT.parent / "experiments" / "tft"))
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--batch-size", type=int, default=128)
    g.add_argument("--n-epochs", type=int, default=50)
    g.add_argument("--learning-rate", type=float, default=1e-3)
    g.add_argument("--patience", type=int, default=5, help="early stopping patience")
    g.add_argument("--num-workers", type=int, default=0, help="DataLoader workers (Colab: 0)")
    g.add_argument(
        "--accelerator",
        default="auto",
        choices=("auto", "gpu", "cpu"),
        help="PyTorch Lightning accelerator",
    )
    g.add_argument("--max-stations", type=int, default=None, help="--run-preprocess 시 전달")
    g.add_argument("--preprocess-start", default="2023-03-01")
    g.add_argument("--preprocess-end", default="2025-10-31")
    g.add_argument(
        "--eligibility-csv",
        type=str,
        default=str(_ROOT.parent / "metadata_outputs" / "tft_station_eligibility.csv"),
        help="included_tft_train==Y 필터 (tft_preprocess 산출)",
    )


def add_output_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("output")
    g.add_argument(
        "--quantiles",
        type=str,
        default="0.1,0.5,0.9",
        help="QuantileRegression 분위 (쉼표)",
    )
    g.add_argument(
        "--checkpoint-name",
        default="tft_model.pt",
        help="work-dir/experiment-name/ 아래 저장 파일명",
    )
    g.add_argument(
        "--save-predictions",
        action="store_true",
        help="val 구간 예측 parquet 저장",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Train Darts TFT on preprocessed FloodAX panel",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_common_args(p)
    add_data_args(p)
    add_model_args(p)
    add_experiment_args(p)
    add_output_args(p)
    return p


def load_panel(processed_dir: Path) -> pd.DataFrame:
    path = processed_dir / "tft_panel.parquet"
    if not path.is_file():
        raise SystemExit(f"Missing {path}. Run: python src/tft_preprocess.py --out-dir {processed_dir}")
    return pd.read_parquet(path)


def load_eligible_station_ids(eligibility_csv: Path) -> set[str]:
    """``tft_station_eligibility.csv`` 에서 ``included_tft_train==Y`` 만."""
    if not eligibility_csv.is_file():
        print(f"Warning: eligibility file missing ({eligibility_csv}); using all stations in panel.")
        return set()
    df = pd.read_csv(eligibility_csv, dtype=str)
    df["station_id"] = df["station_id"].astype(str).str.strip()
    ok = df["included_tft_train"].astype(str).str.upper() == "Y"
    return set(df.loc[ok, "station_id"])


def df_to_timeseries(
    g: pd.DataFrame,
    target_col: str,
    past_cols: list[str],
    future_cols: list[str],
):
    from darts import TimeSeries

    g = g.sort_values("datetime")
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


def series_dict_from_panel(
    panel: pd.DataFrame,
    *,
    target_col: str,
    past_cols: list[str],
    future_cols: list[str],
    split: str,
    min_len: int,
    max_series: int | None,
    allowed_stations: set[str] | None = None,
) -> tuple[list, list | None, list | None, list[str]]:
    sub = panel[panel["split"] == split]
    targets, pasts, futures, ids = [], [], [], []
    for sid, g in sub.groupby("station_id", sort=True):
        sid_s = str(sid)
        if allowed_stations is not None and allowed_stations and sid_s not in allowed_stations:
            continue
        if len(g) < min_len:
            continue
        t, p, f = df_to_timeseries(g, target_col, past_cols, future_cols)
        if not np.isfinite(t.values()).any():
            continue
        targets.append(t)
        pasts.append(p)
        futures.append(f)
        ids.append(sid_s)
        if max_series is not None and len(ids) >= max_series:
            break
    past_list = pasts if any(p is not None for p in pasts) else None
    fut_list = futures if any(f is not None for f in futures) else None
    return targets, past_list, fut_list, ids


def run_preprocess(args: argparse.Namespace, processed_dir: Path) -> None:
    import subprocess

    cmd = [
        sys.executable,
        str(_ROOT / "tft_preprocess.py"),
        "--out-dir",
        str(processed_dir),
        "--start",
        args.preprocess_start,
        "--end",
        args.preprocess_end,
    ]
    if args.max_stations is not None:
        cmd.extend(["--max-stations", str(args.max_stations)])
    print("Running preprocess:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> int:
    args = build_parser().parse_args()
    processed_dir = Path(args.processed_dir)
    if args.run_preprocess:
        run_preprocess(args, processed_dir)

    try:
        import torch
        from darts.models import TFTModel
        from darts.utils.likelihood_models import QuantileRegression
    except ImportError as e:
        raise SystemExit(
            "darts/torch not installed. Colab: pip install 'darts[torch]' pytorch-lightning torch"
        ) from e

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    panel = load_panel(processed_dir)
    allowed = load_eligible_station_ids(Path(args.eligibility_csv))
    if allowed:
        print(f"Eligible stations for TFT (train): {len(allowed)} from {args.eligibility_csv}")
    meta_path = processed_dir / "preprocess_meta.json"
    if meta_path.is_file():
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        print("Preprocess meta:", json.dumps(meta, ensure_ascii=False, indent=2))

    past_cols = [c.strip() for c in args.past_cov_cols.split(",") if c.strip()]
    future_cols = [c.strip() for c in args.future_cov_cols.split(",") if c.strip()]
    quantiles = [float(q) for q in args.quantiles.split(",")]

    min_len = args.min_train_length or (
        args.input_chunk_length + args.output_chunk_length + 1
    )

    train_tgt, train_past, train_fut, train_ids = series_dict_from_panel(
        panel,
        target_col=args.target_col,
        past_cols=past_cols,
        future_cols=future_cols,
        split="train",
        min_len=min_len,
        max_series=args.max_series,
        allowed_stations=allowed or None,
    )
    val_tgt, val_past, val_fut, val_ids = series_dict_from_panel(
        panel,
        target_col=args.target_col,
        past_cols=past_cols,
        future_cols=future_cols,
        split="val",
        min_len=min_len,
        max_series=args.max_series,
        allowed_stations=allowed or None,
    )

    if not train_tgt:
        raise SystemExit("No training series. Check preprocess output / min length.")

    print(f"Train series: {len(train_tgt)} | Val series: {len(val_tgt)}")

    exp_dir = Path(args.work_dir) / args.experiment_name
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "train_args.json").write_text(
        json.dumps(vars(args), indent=2, default=str), encoding="utf-8"
    )
    (exp_dir / "train_station_ids.json").write_text(
        json.dumps(train_ids, indent=2), encoding="utf-8"
    )

    pl_kw = {}
    if args.accelerator != "auto":
        pl_kw["accelerator"] = args.accelerator

    model = TFTModel(
        input_chunk_length=args.input_chunk_length,
        output_chunk_length=args.output_chunk_length,
        hidden_size=args.hidden_size,
        lstm_layers=args.lstm_layers,
        num_attention_heads=args.num_attention_heads,
        dropout=args.dropout,
        hidden_continuous_size=args.hidden_continuous_size,
        add_relative_index=args.add_relative_index,
        batch_size=args.batch_size,
        n_epochs=args.n_epochs,
        optimizer_kwargs={"lr": args.learning_rate},
        likelihood=QuantileRegression(quantiles=quantiles),
        pl_trainer_kwargs={
            "max_epochs": args.n_epochs,
            "enable_progress_bar": True,
            **pl_kw,
        },
        force_reset=True,
        save_checkpoints=True,
        random_state=args.seed,
    )

    model.fit(
        series=train_tgt,
        past_covariates=train_past,
        future_covariates=train_fut,
        val_series=val_tgt if val_tgt else None,
        val_past_covariates=val_past if val_tgt else None,
        val_future_covariates=val_fut if val_tgt else None,
        verbose=True,
        num_loader_workers=args.num_workers,
    )

    ckpt_path = exp_dir / args.checkpoint_name
    model.save(str(ckpt_path))
    print(f"Saved model → {ckpt_path}")

    if args.save_predictions and val_tgt:
        rows = []
        for sid, tgt, past, fut in zip(val_ids, val_tgt, val_past or [None] * len(val_tgt), val_fut or [None] * len(val_tgt)):
            pred = model.predict(
                n=args.output_chunk_length,
                series=tgt,
                past_covariates=past,
                future_covariates=fut,
                num_samples=1,
            )
            pdf = pred.to_dataframe()
            pdf["station_id"] = sid
            rows.append(pdf.reset_index())
        if rows:
            out_pred = pd.concat(rows, ignore_index=True)
            pred_path = exp_dir / "val_predictions.parquet"
            out_pred.to_parquet(pred_path, index=False)
            print(f"Val predictions → {pred_path}")

    if (processed_dir / "scalers.joblib").is_file():
        joblib.dump(
            {"note": "inverse transform per station|col keys in tft_preprocess"},
            exp_dir / "scalers_ref.joblib",
        )

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
