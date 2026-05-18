"""
TFT 모델 실시간 수위 예측 추론 스크립트 (B 수정 버전 v3)

v3 변경:
- TimeSeries.from_dataframe에 freq="h", fill_missing_dates=True 추가
- 시간 공백 자동 채움
"""
import warnings
import joblib
import torch
import numpy as np
import pandas as pd
from datetime import datetime

warnings.filterwarnings("ignore")

from darts import TimeSeries
from darts.models import TFTModel

TARGET_COL = "wl"
PAST_COV_COLS = [
    "wl_diff", "rn", "upstream_wl_1", "upstream_wl_2",
    "upstream_wl_1_mask", "upstream_wl_2_mask", "was_imputed"
]
FUTURE_COV_COLS = ["hour_sin", "hour_cos", "month_sin", "month_cos"]

HORIZON = 6
ENCODER_LENGTH = 48
NUM_SAMPLES = 200


def _generate_future_covariates(start_dt, end_dt):
    future_dates = pd.date_range(start=start_dt, end=end_dt, freq='h')
    df = pd.DataFrame({"datetime": future_dates})
    hour = df["datetime"].dt.hour + df["datetime"].dt.minute / 60.0
    month = df["datetime"].dt.month
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24.0).astype("float32")
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24.0).astype("float32")
    df["month_sin"] = np.sin(2 * np.pi * (month - 1) / 12.0).astype("float32")
    df["month_cos"] = np.cos(2 * np.pi * (month - 1) / 12.0).astype("float32")
    return df


def _inverse_scale(station_id, value, scalers):
    key = f"{station_id}|{TARGET_COL}"
    if key not in scalers:
        return float(value)
    sc = scalers[key]
    v_arr = np.array([[float(value)]], dtype=float)
    return float(sc.inverse_transform(v_arr)[0][0])


def _load_model_cpu(model_path):
    try:
        return TFTModel.load(model_path, map_location=torch.device('cpu'))
    except TypeError:
        pass
    original_load = torch.load
    def patched_load(*args, **kwargs):
        kwargs['map_location'] = torch.device('cpu')
        return original_load(*args, **kwargs)
    torch.load = patched_load
    try:
        model = TFTModel.load(model_path)
    finally:
        torch.load = original_load
    return model


def run_prediction(
    live_data_df=None,
    model_path="model/tft_model.pt",
    scaler_path="model/scalers.joblib"
):
    try:
        print(f"[*] 모델 로드: {model_path}")
        model = _load_model_cpu(model_path)
        model.trainer_params = {"accelerator": "cpu"}
        print(f"[*] 스케일러 로드: {scaler_path}")
        scaler_obj = joblib.load(scaler_path)
        scalers = scaler_obj.get("scalers", scaler_obj) if isinstance(scaler_obj, dict) else scaler_obj
        print(f"[*] 모델/스케일러 로드 성공")
    except Exception as e:
        print(f"[예측 실패] 모델 또는 스케일러 로드 에러: {e}")
        return pd.DataFrame()

    if live_data_df is None or len(live_data_df) == 0:
        print("[예측 실패] live_data_df 비어있음")
        return pd.DataFrame()

    live_data_df = live_data_df.copy()
    live_data_df["datetime"] = pd.to_datetime(live_data_df["datetime"])
    for c in live_data_df.columns:
        if pd.api.types.is_numeric_dtype(live_data_df[c]) and live_data_df[c].dtype == 'float64':
            live_data_df[c] = live_data_df[c].astype('float32')

    predicted_at = live_data_df["datetime"].max()
    print(f"[*] 예측 기준 시간: {predicted_at}")
    print(f"[*] 대상 관측소: {live_data_df['station_id'].nunique()}개")

    results = []
    success_cnt = 0
    skip_cnt = 0
    error_cnt = 0
    first_errors = []

    for sid, g in live_data_df.groupby("station_id"):
        sid_s = str(sid).strip()
        g = g.sort_values("datetime").copy()
        g_hist = g[g["datetime"] <= predicted_at].tail(ENCODER_LENGTH)

        if len(g_hist) < ENCODER_LENGTH:
            skip_cnt += 1
            continue

        try:
            # ⭐ v3 변경 - freq="h", fill_missing_dates=True 추가
            ts_target = TimeSeries.from_dataframe(
                g_hist,
                time_col="datetime",
                value_cols=TARGET_COL,
                freq="h",
                fill_missing_dates=True
            )
            past_cols_available = [c for c in PAST_COV_COLS if c in g_hist.columns]
            ts_past = TimeSeries.from_dataframe(
                g_hist,
                time_col="datetime",
                value_cols=past_cols_available,
                freq="h",
                fill_missing_dates=True
            )

            future_start_dt = predicted_at - pd.Timedelta(hours=ENCODER_LENGTH - 1)
            future_end_dt = predicted_at + pd.Timedelta(hours=HORIZON)
            fut_df = _generate_future_covariates(future_start_dt, future_end_dt)
            ts_future = TimeSeries.from_dataframe(
                fut_df,
                time_col="datetime",
                value_cols=FUTURE_COV_COLS,
                freq="h",
                fill_missing_dates=True
            )

            pred = model.predict(
                n=HORIZON,
                series=ts_target,
                past_covariates=ts_past,
                future_covariates=ts_future,
                num_samples=NUM_SAMPLES,
                verbose=False
            )

            predictions_json = {}
            try:
                lower_vals = pred.quantile(0.1).values().flatten()
                median_vals = pred.quantile(0.5).values().flatten()
                upper_vals = pred.quantile(0.9).values().flatten()
                for h in range(1, HORIZON + 1):
                    idx = h - 1
                    predicted = _inverse_scale(sid_s, median_vals[idx], scalers)
                    lower = _inverse_scale(sid_s, lower_vals[idx], scalers)
                    upper = _inverse_scale(sid_s, upper_vals[idx], scalers)
                    predictions_json[f"h{h}"] = {
                        "predicted": round(predicted, 3),
                        "lower": round(lower, 3),
                        "upper": round(upper, 3)
                    }
            except Exception:
                pred_values = pred.values().flatten()
                for h in range(1, HORIZON + 1):
                    idx = h - 1
                    predicted = _inverse_scale(sid_s, pred_values[idx], scalers)
                    predictions_json[f"h{h}"] = {
                        "predicted": round(predicted, 3),
                        "lower": round(predicted, 3),
                        "upper": round(predicted, 3)
                    }

            results.append({
                "station_id": sid_s,
                "predicted_at": predicted_at,
                "predictions": predictions_json
            })
            success_cnt += 1

        except Exception as e:
            error_cnt += 1
            if len(first_errors) < 3:
                first_errors.append(f"[{sid_s}] {type(e).__name__}: {str(e)[:150]}")
            continue

    final_df = pd.DataFrame(results)
    print(f"\n[*] 결과 요약:")
    print(f"    성공: {success_cnt}개")
    print(f"    스킵 (데이터 부족): {skip_cnt}개")
    print(f"    에러: {error_cnt}개")
    if first_errors:
        print(f"\n[*] 첫 3개 에러 샘플:")
        for e in first_errors:
            print(f"    {e}")

    return final_df


if __name__ == "__main__":
    print("=" * 60)
    print("predict.py 단독 테스트")
    print("=" * 60)
    try:
        test_df = pd.read_parquet("model/tft_panel.parquet")
        print(f"테스트 panel: {len(test_df):,} row")
        result = run_prediction(live_data_df=test_df)
        if not result.empty:
            print("\n샘플 결과:")
            print(result.head(3).to_string())
            print(f"\n샘플 predictions:")
            print(result.iloc[0]["predictions"])
    except FileNotFoundError:
        print("model/tft_panel.parquet 없음.")
