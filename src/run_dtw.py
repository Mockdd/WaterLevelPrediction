"""DTW analysis pipeline for flood event station similarity.

Outputs (output/DTW/):
  distance/{event}_dist.npy                   pairwise DTW distance matrix
  clusters/{event}_clusters.csv               cluster label + centroid flag
  clusters/consistency.csv                    cross-event consistency per station
  similarity/{event}_topk.csv                 top-5 nearest neighbors per station
  propagation/watershed4/propagation*.csv       수계 내(watershed4 전쌍)
  propagation/stream_adjacent/propagation*.csv   수계간(dtw_check 방식)
  propagation/stream_adjacent/propagation_peaktime_delta*.csv
      달력 시각 기준 피크 시각 차(상류→하류, wl_norm 최대 시각)
  propagation/stream_adjacent/propagation_cc_common_time*.csv
      공통 datetime 정렬 wl_norm에 scipy 교차상관 시차
  propagation/qa/validation_report.txt       (validate_propagation_outputs.py)
  viz/{event}_dendrogram.png
  viz/{event}_heatmap.png
  viz/{event}_cluster_profiles.png
  viz/consistency_heatmap.png

Usage:
    pip install dtaidistance scipy matplotlib
    python src/run_dtw.py
    python src/run_dtw.py --lag-only    # 교차상관 시차(propagation)만 계산·저장

Tune N_CLUSTERS after inspecting dendrograms and re-run.
"""

from __future__ import annotations

import argparse
import os
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import dendrogram, fcluster, linkage
from scipy.signal import correlate
from scipy.spatial.distance import squareform

try:
    from dtaidistance import dtw as dtw_lib
except ImportError as e:
    raise ImportError(
        "dtaidistance 미설치.\n  pip install dtaidistance 실행 후 재시도하세요."
    ) from e

from sklearn.metrics import silhouette_samples

warnings.filterwarnings("ignore")

# Windows 한글 폰트
plt.rcParams["font.family"]       = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT     = Path(__file__).resolve().parents[1]
WIN_DIR  = ROOT / "output" / "DTW" / "windows"
OUT_BASE = ROOT / "output" / "DTW"

for _d in ["distance", "clusters", "similarity", "propagation", "viz"]:
    (OUT_BASE / _d).mkdir(parents=True, exist_ok=True)

PROPAGATION_WS = OUT_BASE / "propagation" / "watershed4"
PROPAGATION_STREAM = OUT_BASE / "propagation" / "stream_adjacent"
PROPAGATION_QA = OUT_BASE / "propagation" / "qa"
for _p in (PROPAGATION_WS, PROPAGATION_STREAM, PROPAGATION_QA):
    _p.mkdir(parents=True, exist_ok=True)

OBS_TARGET = ROOT / "metadata_outputs" / "obsTarget.csv"
HRFCO_GDT_CACHE = ROOT / "metadata_outputs" / "hrfco_waterlevel_gdt.csv"

# dtw_check.ipynb — obsFinal stream_map 동일
STREAM_MAP_DTW_CHECK: dict[str, str] = {
    "평택수위표": "안성천",
    "동연교수위표": "진위천",
    "안성천상류": "안성천",
    "황구지천상류": "황구지천",
    "황구지천하류": "황구지천",
    "아산방조제상류": "안성천",
    "이동댐": "진위천",
    "삼척오십천상류": "삼척오십천",
    "삼천오십천하류": "삼척오십천",
}


def _obscd_key(x: object) -> str:
    """parquet 컬럼·메타 obscd 매칭용 (dtw_check 경로 전용)."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    if isinstance(x, (float, np.floating)) and float(x) == int(float(x)):
        x = int(float(x))
    s = str(x).strip()
    if len(s) > 2 and s.endswith(".0"):
        s = s[:-2]
    return s


DTW_WINDOW = 24    # Sakoe-Chiba band (±24 hours)
N_CLUSTERS = 7     # initial cluster count — adjust after viewing dendrograms
TOPK       = 5     # top-k similar stations per station

HOURS_MIN = -72    # -3 days
HOURS_MAX = 120    # +5 days

EVENT_FILES = sorted(WIN_DIR.glob("*_wl.parquet"))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _fetch_hrfco_gdt_map() -> dict[str, float]:
    """HRFCO waterlevel/info.json → obscd → 가우지 기준고 gdt (m). 실패 시 빈 dict."""
    try:
        import requests
        from dotenv import load_dotenv
    except ImportError:
        return {}
    load_dotenv(ROOT / ".env")
    tok = os.getenv("hrfco_token", "").strip().strip('"')
    if not tok:
        return {}
    url = f"https://api.hrfco.go.kr/{tok}/waterlevel/info.json"
    try:
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}
    out: dict[str, float] = {}
    for row in data.get("content", []):
        code = str(row.get("wlobscd", "")).strip()
        gdt_raw = row.get("gdt")
        if not code or gdt_raw is None or str(gdt_raw).strip() in ("", "-"):
            continue
        try:
            out[code] = float(str(gdt_raw).strip())
        except ValueError:
            continue
    return out


def _load_or_build_gdt_by_obscd() -> dict[str, float]:
    if HRFCO_GDT_CACHE.exists():
        gdf = pd.read_csv(HRFCO_GDT_CACHE, dtype=str)
        gdf["obscd"] = gdf["obscd"].astype(str).str.strip()
        gdf["gdt"] = pd.to_numeric(gdf["gdt"], errors="coerce")
        return dict(zip(gdf["obscd"], gdf["gdt"]))
    gmap = _fetch_hrfco_gdt_map()
    if gmap:
        pd.DataFrame(
            [{"obscd": k, "gdt": v} for k, v in sorted(gmap.items())],
        ).to_csv(HRFCO_GDT_CACHE, index=False, encoding="utf-8-sig")
    return gmap


def load_obs_meta() -> pd.DataFrame:
    """수계 내(watershed4) 전파용 — 기존과 동일 컬럼."""
    df = pd.read_csv(OBS_TARGET, index_col=0, dtype=str)
    df["obscd"] = df["codeObs"].str.replace(r"\.0$", "", regex=True)
    df["watershed4"] = df["codeWatershed"].str[:4]
    return df[["obscd", "korObs", "codeWatershed", "watershed4"]].drop_duplicates("obscd")


def load_obs_meta_dtw_check() -> pd.DataFrame:
    """
    ``dtw_check.ipynb`` 를 ``obsTarget.csv`` 에 적용할 때 사용하는 확장 메타.

    ``STREAM_MAP_DTW_CHECK``, HRFCO ``gdt``(캐시) 기반 ``water_elevation`` 포함.
    """
    df = pd.read_csv(OBS_TARGET, index_col=0, dtype=str)
    df["obscd"] = (
        df["codeObs"].str.replace(r"\.0$", "", regex=True).map(_obscd_key)
    )
    df["watershed4"] = df["codeWatershed"].str[:4]
    df["korStream_x"] = df["korStream_x"].replace(STREAM_MAP_DTW_CHECK)
    gdt_map = _load_or_build_gdt_by_obscd()
    df["water_elevation"] = df["obscd"].map(gdt_map)
    if "waterElevation" in df.columns:
        alt = pd.to_numeric(df["waterElevation"], errors="coerce")
        df["water_elevation"] = df["water_elevation"].fillna(alt)
    df["water_elevation"] = pd.to_numeric(df["water_elevation"], errors="coerce")
    cols = [
        "obscd",
        "korObs",
        "codeWatershed",
        "watershed4",
        "sphereLarge",
        "korStream_x",
        "water_elevation",
    ]
    return df[cols].drop_duplicates("obscd")


def load_window(
    parquet_path: Path,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """
    Load window parquet → time-series matrix.

    Returns:
        series_matrix  index=hours_from_peak, columns=obscd, values=wl_norm
        obscds         station code list (column order)
        kor_obs_list   station name list (matching obscds)
    """
    df     = pd.read_parquet(parquet_path)
    id_map = df[["obscd", "korObs"]].drop_duplicates("obscd").set_index("obscd")["korObs"]

    pivot = (
        df.pivot_table(
            index="hours_from_peak", columns="obscd",
            values="wl_norm", aggfunc="mean",
        )
        .reindex(range(HOURS_MIN, HOURS_MAX + 1))
    )

    # interpolate gaps, fill edges
    pivot = pivot.interpolate(method="linear", axis=0).ffill().bfill()
    pivot = pivot.dropna(axis=1)

    obscds       = list(pivot.columns)
    kor_obs_list = [id_map.get(o, o) for o in obscds]
    return pivot, obscds, kor_obs_list


def load_window_long(parquet_path: Path) -> pd.DataFrame:
    """이벤트 윈도 parquet의 long 포맷(``datetime``·``wl_norm``). dtw_check 쌍과 동일 ``obscd`` 키."""
    df = pd.read_parquet(parquet_path)
    df["obscd"] = df["obscd"].map(_obscd_key)
    if "datetime" not in df.columns:
        return pd.DataFrame()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    return df.dropna(subset=["datetime"])


def _median_hour_step(idx: pd.DatetimeIndex) -> float:
    if len(idx) < 2:
        return 1.0
    ts = idx.astype(np.int64) // 10**9
    d = np.diff(np.sort(ts)) / 3600.0
    m = float(np.nanmedian(d)) if d.size else 1.0
    if not np.isfinite(m) or m <= 0:
        return 1.0
    return m


def _peak_datetime_wl_norm(sub: pd.DataFrame) -> pd.Timestamp | float:
    sub = sub.dropna(subset=["datetime", "wl_norm"])
    if len(sub) < 1 or not np.any(np.isfinite(sub["wl_norm"].to_numpy())):
        return np.nan
    row = sub.loc[sub["wl_norm"].idxmax()]
    return row["datetime"]


def _iter_stream_adjacent_pairs_long(
    obs_meta: pd.DataFrame,
    parquet_path: Path,
    event_date: str,
    obscd_set: set[str],
) -> list[tuple[str, str, str, str, str, pd.DataFrame]]:
    """
    한 이벤트에 대해 (region, stream, ref_o, tgt_o, df_long) 쌍 리스트.
    ``obscd_set``은 ``load_window`` 결과와 동일(전파 CSV와 쌍 일치).
    """
    meta_idx = obs_meta.set_index("obscd")
    df_long = load_window_long(parquet_path)
    if df_long.empty:
        return []

    pairs: list[tuple[str, str, str, str, str, pd.DataFrame]] = []
    target_regions = sorted(
        obs_meta["sphereLarge"].dropna().astype(str).str.strip().unique()
    )

    for region in target_regions:
        stas_region = [
            o
            for o in obscd_set
            if o in meta_idx.index
            and str(meta_idx.at[o, "sphereLarge"]).strip() == region
        ]
        if len(stas_region) < 2:
            continue

        streams = sorted(
            {
                str(meta_idx.at[o, "korStream_x"]).strip()
                for o in stas_region
                if pd.notna(meta_idx.at[o, "korStream_x"])
                and str(meta_idx.at[o, "korStream_x"]).strip() != ""
            }
        )

        for stream in streams:
            sub_meta = obs_meta[
                (obs_meta["korStream_x"].astype(str).str.strip() == stream)
                & (obs_meta["sphereLarge"].astype(str).str.strip() == region)
            ].copy()
            sub_meta = sub_meta[sub_meta["obscd"].isin(obscd_set)]
            if sub_meta.empty:
                continue
            sub_meta = sub_meta.sort_values(
                "water_elevation",
                ascending=False,
                na_position="last",
            )
            obs_in_data = [
                str(o) for o in sub_meta["obscd"].tolist() if o in obscd_set
            ]
            if len(obs_in_data) < 2:
                continue

            for i in range(len(obs_in_data) - 1):
                ref_o = obs_in_data[i]
                tgt_o = obs_in_data[i + 1]
                pairs.append(
                    (event_date, region, stream, ref_o, tgt_o, df_long),
                )
    return pairs


def compute_propagation_peaktime_delta_stream(obs_meta: pd.DataFrame) -> pd.DataFrame:
    """
    피크-셀(hours_from_peak) 정렬이 아니라, 윈도 내 ``wl_norm`` 최대의 **실제 시각** 차.

    **양의 mean_lag_h**: 하류(tgt) 피크 시각이 상류(ref)보다 늦음.
    """
    rows: list[dict] = []
    meta_idx = obs_meta.set_index("obscd")

    for parquet_path in EVENT_FILES:
        event_date = parquet_path.stem.replace("_wl", "")
        series_matrix, obscds, _ = load_window(parquet_path)
        series_matrix = series_matrix.rename(columns=_obscd_key)
        obscd_set = set(series_matrix.columns)
        for _ev, region, stream, ref_o, tgt_o, df_long in _iter_stream_adjacent_pairs_long(
            obs_meta, parquet_path, event_date, obscd_set,
        ):
            d_ref = df_long[df_long["obscd"] == ref_o]
            d_tgt = df_long[df_long["obscd"] == tgt_o]
            t_a = _peak_datetime_wl_norm(d_ref)
            t_b = _peak_datetime_wl_norm(d_tgt)
            if not (pd.notna(t_a) and pd.notna(t_b)):
                continue
            lag_h = (t_b - t_a).total_seconds() / 3600.0
            if not np.isfinite(lag_h):
                continue
            rows.append({
                "event_date": event_date,
                "sphereLarge": region,
                "korStream_x": stream,
                "obscd_upstream": ref_o,
                "korObs_upstream": meta_idx.at[ref_o, "korObs"]
                if ref_o in meta_idx.index
                else ref_o,
                "obscd_downstream": tgt_o,
                "korObs_downstream": meta_idx.at[tgt_o, "korObs"]
                if tgt_o in meta_idx.index
                else tgt_o,
                "lag_peaktime_h": round(float(lag_h), 2),
            })

    if not rows:
        return pd.DataFrame()

    df_lag = pd.DataFrame(rows)
    agg = (
        df_lag.groupby(
            [
                "sphereLarge",
                "korStream_x",
                "obscd_upstream",
                "korObs_upstream",
                "obscd_downstream",
                "korObs_downstream",
            ],
        )
        .agg(
            mean_lag_h=("lag_peaktime_h", "mean"),
            median_lag_h=("lag_peaktime_h", "median"),
            std_lag_h=("lag_peaktime_h", "std"),
            n_events=("event_date", "count"),
        )
        .reset_index()
    )
    agg["mean_lag_h"] = agg["mean_lag_h"].round(2)
    agg["median_lag_h"] = agg["median_lag_h"].round(2)
    agg["std_lag_h"] = agg["std_lag_h"].fillna(0).round(2)
    return agg.sort_values(["sphereLarge", "korStream_x", "mean_lag_h"])


def compute_propagation_cc_common_time_stream(obs_meta: pd.DataFrame) -> pd.DataFrame:
    """
    두 관측소를 **공통 ``datetime``**으로 inner join한 ``wl_norm``에 대해
    ``crosscorr_lag_steps_dtw_check``와 동일한 scipy 상관·시차 정의.
    시차(시간)는 인덱스 간격의 중앙값(시간)으로 스케일.
    """
    rows: list[dict] = []
    meta_idx = obs_meta.set_index("obscd")

    for parquet_path in EVENT_FILES:
        event_date = parquet_path.stem.replace("_wl", "")
        series_matrix, obscds, _ = load_window(parquet_path)
        series_matrix = series_matrix.rename(columns=_obscd_key)
        obscd_set = set(series_matrix.columns)
        for _ev, region, stream, ref_o, tgt_o, df_long in _iter_stream_adjacent_pairs_long(
            obs_meta, parquet_path, event_date, obscd_set,
        ):
            d_ref = df_long[df_long["obscd"] == ref_o].dropna(
                subset=["datetime", "wl_norm"],
            )
            d_tgt = df_long[df_long["obscd"] == tgt_o].dropna(
                subset=["datetime", "wl_norm"],
            )
            r = d_ref.drop_duplicates("datetime").set_index("datetime")["wl_norm"]
            t = d_tgt.drop_duplicates("datetime").set_index("datetime")["wl_norm"]
            common = r.index.intersection(t.index)
            common = common.sort_values()
            if len(common) < 3:
                continue
            r_v = r.loc[common].to_numpy(dtype=np.float64)
            t_v = t.loc[common].to_numpy(dtype=np.float64)
            hour_step = _median_hour_step(common)
            lag_steps, cc_peak = crosscorr_lag_steps_dtw_check(r_v, t_v)
            lag_h = float(lag_steps) * hour_step
            rows.append({
                "event_date": event_date,
                "sphereLarge": region,
                "korStream_x": stream,
                "obscd_upstream": ref_o,
                "korObs_upstream": meta_idx.at[ref_o, "korObs"]
                if ref_o in meta_idx.index
                else ref_o,
                "obscd_downstream": tgt_o,
                "korObs_downstream": meta_idx.at[tgt_o, "korObs"]
                if tgt_o in meta_idx.index
                else tgt_o,
                "lag_cc_calendar_h": round(lag_h, 2)
                if np.isfinite(lag_h)
                else np.nan,
                "cc_peak": round(cc_peak, 4)
                if np.isfinite(cc_peak)
                else np.nan,
                "median_dt_h": round(hour_step, 4),
            })

    if not rows:
        return pd.DataFrame()

    df_lag = pd.DataFrame(rows)
    agg = (
        df_lag.groupby(
            [
                "sphereLarge",
                "korStream_x",
                "obscd_upstream",
                "korObs_upstream",
                "obscd_downstream",
                "korObs_downstream",
            ],
        )
        .agg(
            mean_lag_h=("lag_cc_calendar_h", "mean"),
            median_lag_h=("lag_cc_calendar_h", "median"),
            std_lag_h=("lag_cc_calendar_h", "std"),
            mean_cc_peak=("cc_peak", "mean"),
            median_dt_h=("median_dt_h", "median"),
            n_events=("event_date", "count"),
        )
        .reset_index()
    )
    agg["mean_lag_h"] = agg["mean_lag_h"].round(2)
    agg["median_lag_h"] = agg["median_lag_h"].round(2)
    agg["std_lag_h"] = agg["std_lag_h"].fillna(0).round(2)
    agg["mean_cc_peak"] = agg["mean_cc_peak"].round(4)
    agg["median_dt_h"] = agg["median_dt_h"].round(4)
    return agg.sort_values(["sphereLarge", "korStream_x", "mean_lag_h"])


# ---------------------------------------------------------------------------
# DTW distance matrix
# ---------------------------------------------------------------------------

def compute_dtw_matrix(series_matrix: pd.DataFrame) -> np.ndarray:
    series = [
        series_matrix[col].values.astype(np.double)
        for col in series_matrix.columns
    ]
    dist = dtw_lib.distance_matrix_fast(series, window=DTW_WINDOW, compact=False)
    dist = dist + dist.T          # upper triangle → symmetric
    np.fill_diagonal(dist, 0.0)
    return dist


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def cluster_stations(
    dist_matrix: np.ndarray,
    obscds: list[str],
    kor_obs_list: list[str],
    n_clusters: int,
) -> tuple[pd.DataFrame, np.ndarray]:
    """
    Ward hierarchical clustering.

    Returns:
        df_clusters  obscd, korObs, cluster_id, is_centroid
        linkage_mat  for dendrogram
    """
    Z      = linkage(squareform(dist_matrix), method="ward")
    labels = fcluster(Z, t=n_clusters, criterion="maxclust")

    # silhouette score per station (precomputed distance matrix)
    sil_scores = silhouette_samples(dist_matrix, labels, metric="precomputed")

    # intra-cluster mean distance per station
    intra_means: list[float] = []
    for i, cid in enumerate(labels):
        peers = np.where(labels == cid)[0]
        peers = peers[peers != i]
        intra_means.append(float(dist_matrix[i, peers].mean()) if len(peers) else 0.0)

    # centroid = station with minimum intra-cluster mean distance
    centroids: set[str] = set()
    for cid in np.unique(labels):
        idx = np.where(labels == cid)[0]
        sub = dist_matrix[np.ix_(idx, idx)]
        centroids.add(obscds[idx[sub.mean(axis=1).argmin()]])

    df = pd.DataFrame({
        "obscd":            obscds,
        "korObs":           kor_obs_list,
        "cluster_id":       labels.tolist(),
        "is_centroid":      [o in centroids for o in obscds],
        "silhouette_score": np.round(sil_scores, 4).tolist(),
        "intra_mean_dist":  [round(v, 4) for v in intra_means],
    })
    return df, Z


# ---------------------------------------------------------------------------
# Top-k similarity
# ---------------------------------------------------------------------------

def compute_topk(
    dist_matrix: np.ndarray,
    obscds: list[str],
    kor_obs_list: list[str],
) -> pd.DataFrame:
    rows = []
    for i, (oc, kn) in enumerate(zip(obscds, kor_obs_list)):
        d = dist_matrix[i].copy()
        d[i] = np.inf
        for rank, j in enumerate(np.argsort(d)[:TOPK], start=1):
            rows.append({
                "obscd":           oc,
                "korObs":          kn,
                "rank":            rank,
                "neighbor_obscd":  obscds[j],
                "neighbor_korObs": kor_obs_list[j],
                "dtw_dist":        round(float(dist_matrix[i, j]), 4),
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Propagation lag  (cross-correlation on aligned windows + DTW distance)
# ---------------------------------------------------------------------------

def crosscorr_time_lag_hours(
    s_ref: np.ndarray,
    s_tgt: np.ndarray,
    *,
    hour_step: float = 1.0,
) -> tuple[float, float]:
    """
    Demeaned full-mode `numpy.correlate`로 정수 샘플 시차를 구한 뒤 시간으로 환산.

    ``np.correlate``의 ``argmax - (len(s_tgt)-1)`` 는 *ref 대비 tgt의 지연*과 부호가
    반대이므로, 기존 peak-to-peak 정의와 맞추기 위해 부호를 뒤집는다.
    **양수**: *tgt*(B)가 *ref*(A)보다 늦게 반응(피크가 늦게 옴).

    Returns:
        (lag_hours, raw_cc_peak) — raw_cc_peak는 정규화 전 상관 합의 최댓값.
    """
    s_ref = np.asarray(s_ref, dtype=np.float64).ravel()
    s_tgt = np.asarray(s_tgt, dtype=np.float64).ravel()
    if s_ref.shape != s_tgt.shape or s_ref.size < 3:
        return float("nan"), float("nan")
    if not np.all(np.isfinite(s_ref)) or not np.all(np.isfinite(s_tgt)):
        return float("nan"), float("nan")
    if float(np.std(s_ref)) < 1e-12 or float(np.std(s_tgt)) < 1e-12:
        return float("nan"), float("nan")

    a = s_ref - np.mean(s_ref)
    b = s_tgt - np.mean(s_tgt)
    correlation = np.correlate(a, b, mode="full")
    raw_lag_idx = int(np.argmax(correlation) - (len(b) - 1))
    lag_h = -raw_lag_idx * hour_step
    cc_peak = float(np.max(correlation))
    return lag_h, cc_peak


def compute_propagation_lag(obs_meta: pd.DataFrame) -> pd.DataFrame:
    """
    같은 수계 관측소 쌍 (A, B)에 대해, 이벤트별로 정렬된 ``wl_norm`` 시계열에
    교차상관(슬라이딩 정렬)으로 시차를 구하고 이벤트 간 평균·표준편차를 낸다.
    동일 구간에 ``dtaidistance.dtw.distance_fast``(Sakoe–Chiba ``DTW_WINDOW``)로
    형태 유사도도 기록한다.

    **양의 mean_lag_h**: B가 A보다 늦게 올라오는 정렬(도달 지연)에 해당.
    """
    rows: list[dict] = []
    meta_idx = obs_meta.set_index("obscd")

    for parquet_path in EVENT_FILES:
        event_date = parquet_path.stem.replace("_wl", "")
        series_matrix, obscds, kor_obs_list = load_window(parquet_path)
        info = {
            o: {
                "kor": k,
                "ws": meta_idx.at[o, "watershed4"] if o in meta_idx.index else np.nan,
            }
            for o, k in zip(obscds, kor_obs_list)
        }

        by_ws: dict[str, list[str]] = {}
        for o in obscds:
            ws = info[o]["ws"]
            if pd.isna(ws):
                continue
            wk = str(ws)
            by_ws.setdefault(wk, []).append(o)

        for ws, stas in by_ws.items():
            for i in range(len(stas)):
                for j in range(i + 1, len(stas)):
                    a, b = stas[i], stas[j]
                    s_a = series_matrix[a].values.astype(np.float64)
                    s_b = series_matrix[b].values.astype(np.float64)
                    lag_h, cc_peak = crosscorr_time_lag_hours(s_a, s_b, hour_step=1.0)
                    try:
                        dtw_d = float(
                            dtw_lib.distance_fast(s_a, s_b, window=DTW_WINDOW)
                        )
                    except Exception:
                        dtw_d = float("nan")

                    rows.append({
                        "event_date":   event_date,
                        "watershed4":   ws,
                        "obscd_a":      a,
                        "korObs_a":     info[a]["kor"],
                        "obscd_b":      b,
                        "korObs_b":     info[b]["kor"],
                        "lag_a_to_b_h": round(lag_h, 2)
                        if np.isfinite(lag_h)
                        else np.nan,
                        "cc_peak":      round(cc_peak, 4)
                        if np.isfinite(cc_peak)
                        else np.nan,
                        "dtw_dist":     round(dtw_d, 4)
                        if np.isfinite(dtw_d)
                        else np.nan,
                    })

    if not rows:
        return pd.DataFrame()

    df_lag = pd.DataFrame(rows)
    agg = (
        df_lag.groupby(["watershed4", "obscd_a", "korObs_a", "obscd_b", "korObs_b"])
        .agg(
            mean_lag_h=("lag_a_to_b_h", "mean"),
            std_lag_h=("lag_a_to_b_h", "std"),
            mean_cc_peak=("cc_peak", "mean"),
            mean_dtw_dist=("dtw_dist", "mean"),
            n_events=("event_date", "count"),
        )
        .reset_index()
    )
    agg["mean_lag_h"] = agg["mean_lag_h"].round(2)
    agg["std_lag_h"]  = agg["std_lag_h"].fillna(0).round(2)
    agg["mean_cc_peak"] = agg["mean_cc_peak"].round(4)
    agg["mean_dtw_dist"] = agg["mean_dtw_dist"].round(4)
    return agg.sort_values(["watershed4", "mean_lag_h"])


def _minmax_norm_dtw_check(s: np.ndarray) -> np.ndarray:
    """dtw_check.ipynb: (x - min) / (max - min + 1e-9)."""
    s = np.asarray(s, dtype=np.float64).ravel()
    lo, hi = float(np.min(s)), float(np.max(s))
    return (s - lo) / (hi - lo + 1e-9)


def crosscorr_lag_steps_dtw_check(
    s_ref: np.ndarray,
    s_tgt: np.ndarray,
) -> tuple[float, float]:
    """
    dtw_check.ipynb (하천별 공통 피크 셀)과 동일:
    ``scipy.signal.correlate(tgt - mean(tgt), ref - mean(ref), mode='full')``,
    ``lag_steps = argmax - (len(ref) - 1)``.
    양수면 하류(tgt)가 늦게 피크(노트북 주석과 동일).
    """
    ref_s = np.asarray(s_ref, dtype=np.float64).ravel()
    tgt_s = np.asarray(s_tgt, dtype=np.float64).ravel()
    if ref_s.shape != tgt_s.shape or ref_s.size < 3:
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
    lag_steps = float(np.argmax(corr) - (len(ref_s) - 1))
    cc_peak = float(np.max(corr))
    return lag_steps, cc_peak


def compute_propagation_lag_dtw_check_notebook(obs_meta: pd.DataFrame) -> pd.DataFrame:
    """
    ``dtw_check.ipynb`` 방식 (인접 상·하류, 권역×하천):

    - ``sphereLarge`` 권역별로, ``korStream_x``(STREAM_MAP 적용) 하천별로 분리
    - ``water_elevation``(없으면 -∞) 내림차순 = 상류→하류 순서
    - 인접 관측소 쌍만 (상류 ref → 바로 아래 하류 tgt)
    - 시계열: 이벤트 윈도우 ``wl_norm`` (피크 정렬; 노트북은 달력 윈도우)
    - DTW: min–max 정규화 후 ``dtaidistance.dtw.distance`` (Sakoe–Chiba 없음)
    - 시차: ``crosscorr_lag_steps_dtw_check`` (scipy.signal.correlate, tgt/ref 순서 동일)
    """
    rows: list[dict] = []
    meta_idx = obs_meta.set_index("obscd")

    target_regions = sorted(
        obs_meta["sphereLarge"].dropna().astype(str).str.strip().unique()
    )

    for parquet_path in EVENT_FILES:
        event_date = parquet_path.stem.replace("_wl", "")
        series_matrix, obscds, kor_obs_list = load_window(parquet_path)
        series_matrix = series_matrix.rename(columns=_obscd_key)
        obscds = list(series_matrix.columns)
        obscd_set = set(obscds)

        for region in target_regions:
            stas_region = [
                o
                for o in obscds
                if o in meta_idx.index
                and str(meta_idx.at[o, "sphereLarge"]).strip() == region
            ]
            if len(stas_region) < 2:
                continue

            streams = sorted(
                {
                    str(meta_idx.at[o, "korStream_x"]).strip()
                    for o in stas_region
                    if pd.notna(meta_idx.at[o, "korStream_x"])
                    and str(meta_idx.at[o, "korStream_x"]).strip() != ""
                }
            )

            for stream in streams:
                sub_meta = obs_meta[
                    (obs_meta["korStream_x"].astype(str).str.strip() == stream)
                    & (obs_meta["sphereLarge"].astype(str).str.strip() == region)
                ].copy()
                sub_meta = sub_meta[sub_meta["obscd"].isin(obscd_set)]
                if sub_meta.empty:
                    continue
                sub_meta = sub_meta.sort_values(
                    "water_elevation",
                    ascending=False,
                    na_position="last",
                )
                obs_in_data = [str(o) for o in sub_meta["obscd"].tolist() if o in obscd_set]

                if len(obs_in_data) < 2:
                    continue

                for i in range(len(obs_in_data) - 1):
                    ref_o = obs_in_data[i]
                    tgt_o = obs_in_data[i + 1]
                    ref_s = series_matrix[ref_o].values.astype(np.float64)
                    tgt_s = series_matrix[tgt_o].values.astype(np.float64)

                    ref_norm = _minmax_norm_dtw_check(ref_s)
                    tgt_norm = _minmax_norm_dtw_check(tgt_s)
                    if (
                        not np.all(np.isfinite(ref_norm))
                        or not np.all(np.isfinite(tgt_norm))
                    ):
                        continue

                    try:
                        # 노트북은 dtw.distance(무제한); C 경로로 동일 밴드(전구간) 근사
                        nlen = int(len(ref_norm))
                        dtw_d = float(
                            dtw_lib.distance_fast(
                                ref_norm.astype(np.double),
                                tgt_norm.astype(np.double),
                                window=max(1, nlen - 1),
                            )
                        )
                    except Exception:
                        dtw_d = float("nan")

                    lag_steps, cc_peak = crosscorr_lag_steps_dtw_check(ref_s, tgt_s)

                    rows.append({
                        "event_date":     event_date,
                        "sphereLarge":    region,
                        "korStream_x":    stream,
                        "obscd_upstream": ref_o,
                        "korObs_upstream": meta_idx.at[ref_o, "korObs"]
                        if ref_o in meta_idx.index
                        else ref_o,
                        "obscd_downstream": tgt_o,
                        "korObs_downstream": meta_idx.at[tgt_o, "korObs"]
                        if tgt_o in meta_idx.index
                        else tgt_o,
                        "lag_hours_nb": round(lag_steps, 2)
                        if np.isfinite(lag_steps)
                        else np.nan,
                        "cc_peak": round(cc_peak, 4)
                        if np.isfinite(cc_peak)
                        else np.nan,
                        "dtw_dist_nb": round(dtw_d, 4)
                        if np.isfinite(dtw_d)
                        else np.nan,
                    })

    if not rows:
        return pd.DataFrame()

    df_lag = pd.DataFrame(rows)
    agg = (
        df_lag.groupby(
            [
                "sphereLarge",
                "korStream_x",
                "obscd_upstream",
                "korObs_upstream",
                "obscd_downstream",
                "korObs_downstream",
            ],
        )
        .agg(
            mean_lag_h=("lag_hours_nb", "mean"),
            median_lag_h=("lag_hours_nb", "median"),
            std_lag_h=("lag_hours_nb", "std"),
            mean_cc_peak=("cc_peak", "mean"),
            mean_dtw_dist=("dtw_dist_nb", "mean"),
            median_dtw_dist=("dtw_dist_nb", "median"),
            n_events=("event_date", "count"),
        )
        .reset_index()
    )
    agg["mean_lag_h"] = agg["mean_lag_h"].round(2)
    agg["median_lag_h"] = agg["median_lag_h"].round(2)
    agg["std_lag_h"] = agg["std_lag_h"].fillna(0).round(2)
    agg["mean_cc_peak"] = agg["mean_cc_peak"].round(4)
    agg["mean_dtw_dist"] = agg["mean_dtw_dist"].round(4)
    agg["median_dtw_dist"] = agg["median_dtw_dist"].round(4)
    return agg.sort_values(["sphereLarge", "korStream_x", "mean_lag_h"])


# ---------------------------------------------------------------------------
# Consistency analysis
# ---------------------------------------------------------------------------

def compute_consistency(all_cluster_dfs: list[pd.DataFrame]) -> pd.DataFrame:
    merged  = pd.concat(all_cluster_dfs, ignore_index=True)
    records = []
    for (obscd, kor_obs), grp in merged.groupby(["obscd", "korObs"]):
        counts   = grp["cluster_id"].value_counts()
        dominant = int(counts.idxmax())
        score    = round(counts.max() / len(grp), 3)
        records.append({
            "obscd":            obscd,
            "korObs":           kor_obs,
            "dominant_cluster": dominant,
            "consistency_score": score,
            "event_count":      len(grp),
        })
    return (
        pd.DataFrame(records)
        .sort_values(["dominant_cluster", "consistency_score"], ascending=[True, False])
    )


# ---------------------------------------------------------------------------
# Visualizations
# ---------------------------------------------------------------------------

def _save(fig: plt.Figure, path: Path) -> None:
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_dendrogram(
    Z: np.ndarray, kor_obs_list: list[str],
    event_date: str, out_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(max(14, len(kor_obs_list) * 0.18), 6))
    dendrogram(
        Z, labels=kor_obs_list,
        leaf_rotation=90, leaf_font_size=6,
        color_threshold=0.7 * Z[:, 2].max(), ax=ax,
    )
    ax.set_title(f"Dendrogram — {event_date}  (N_CLUSTERS={N_CLUSTERS})", fontsize=12)
    ax.set_ylabel("Ward distance")
    _save(fig, out_path)


def plot_heatmap(
    dist_matrix: np.ndarray, df_clusters: pd.DataFrame,
    event_date: str, out_path: Path,
) -> None:
    order = df_clusters.sort_values("cluster_id").index.tolist()
    dm    = dist_matrix[np.ix_(order, order)]
    ticks = df_clusters.loc[order, "korObs"].tolist()

    fig, ax = plt.subplots(figsize=(12, 10))
    im = ax.imshow(dm, aspect="auto", cmap="YlOrRd_r")
    plt.colorbar(im, ax=ax, label="DTW distance")
    ax.set_title(f"DTW Distance Heatmap — {event_date}", fontsize=12)

    step = max(1, len(ticks) // 30)
    ax.set_xticks(range(0, len(ticks), step))
    ax.set_xticklabels(ticks[::step], rotation=90, fontsize=5)
    ax.set_yticks(range(0, len(ticks), step))
    ax.set_yticklabels(ticks[::step], fontsize=5)

    # cluster boundary lines
    cluster_ids = df_clusters.sort_values("cluster_id")["cluster_id"].values
    for b in np.where(np.diff(cluster_ids))[0] + 1:
        ax.axhline(b - 0.5, color="royalblue", linewidth=1.0)
        ax.axvline(b - 0.5, color="royalblue", linewidth=1.0)

    _save(fig, out_path)


def plot_cluster_profiles(
    series_matrix: pd.DataFrame, df_clusters: pd.DataFrame,
    event_date: str, out_path: Path,
) -> None:
    cluster_ids = sorted(df_clusters["cluster_id"].unique())
    n = len(cluster_ids)
    fig, axes = plt.subplots(1, n, figsize=(4 * n, 4), sharey=True)
    if n == 1:
        axes = [axes]

    hours = series_matrix.index.values
    cmap  = plt.cm.tab10

    for cid, ax in zip(cluster_ids, axes):
        members = df_clusters[df_clusters["cluster_id"] == cid]["obscd"].tolist()
        members = [m for m in members if m in series_matrix.columns]
        if not members:
            continue

        mat   = series_matrix[members].values
        mean  = mat.mean(axis=1)
        std   = mat.std(axis=1)
        color = cmap(cid - 1)

        for col in mat.T:
            ax.plot(hours, col, alpha=0.12, color=color, linewidth=0.7)
        ax.plot(hours, mean, color=color, linewidth=2.0)
        ax.fill_between(hours, mean - std, mean + std, alpha=0.2, color=color)
        ax.axvline(0, color="black", linestyle="--", linewidth=0.8)

        centroid_name = df_clusters[
            (df_clusters["cluster_id"] == cid) & df_clusters["is_centroid"]
        ]["korObs"].values
        title = f"Cluster {cid}  (n={len(members)})"
        if len(centroid_name):
            title += f"\n대표: {centroid_name[0]}"
        ax.set_title(title, fontsize=9)
        ax.set_xlabel("hours from peak", fontsize=8)
        ax.set_xlim(hours[0], hours[-1])
        ax.set_ylim(-0.05, 1.05)
        if ax is axes[0]:
            ax.set_ylabel("normalized water level", fontsize=8)

    fig.suptitle(f"Cluster Profiles — {event_date}", fontsize=12)
    plt.tight_layout()
    _save(fig, out_path)


def plot_consistency(
    df_long: pd.DataFrame, all_events: list[str], out_path: Path,
) -> None:
    pivot = (
        df_long.pivot(index="korObs", columns="event_date", values="cluster_id")
        .reindex(columns=all_events)
    )
    fig, ax = plt.subplots(
        figsize=(max(8, len(all_events) * 2), max(10, len(pivot) * 0.2))
    )
    im = ax.imshow(
        pivot.values.astype(float), aspect="auto",
        cmap=plt.cm.tab10, vmin=1, vmax=N_CLUSTERS,
    )
    plt.colorbar(im, ax=ax, label="cluster_id", ticks=range(1, N_CLUSTERS + 1))
    ax.set_xticks(range(len(all_events)))
    ax.set_xticklabels(all_events, rotation=30, fontsize=9)
    ax.set_yticks(range(len(pivot)))
    ax.set_yticklabels(pivot.index.tolist(), fontsize=6)
    ax.set_title("Cluster Assignment Consistency  (station × event)", fontsize=12)
    _save(fig, out_path)


# ---------------------------------------------------------------------------
# Propagation (lag) I/O — shared by full pipeline and `--lag-only`
# ---------------------------------------------------------------------------

def write_propagation_lag_outputs(df_lag: pd.DataFrame) -> None:
    """Save ``propagation*.csv`` and print the same summaries as ``run()`` step 6."""
    print(f"\n{'─'*50}")
    print(
        "홍수파 전파 시차 (교차상관 정렬 · 같은 수계 쌍 + 정렬 구간 DTW 거리)",
    )
    if df_lag.empty:
        print("  [경고] 교차상관 시차 결과 없음 — 윈도 parquet 또는 수계 매칭 확인")
        return

    df_lag.to_csv(
        PROPAGATION_WS / "propagation.csv",
        index=False, encoding="utf-8-sig",
    )
    print(f"  저장: propagation/watershed4/propagation.csv  (전체 {len(df_lag)}쌍)")

    df_lag_reliable = (
        df_lag[df_lag["n_events"] >= 2]
        .sort_values(["watershed4", "mean_lag_h"])
    )
    df_lag_reliable.to_csv(
        PROPAGATION_WS / "propagation_reliable.csv",
        index=False, encoding="utf-8-sig",
    )
    print(
        "  저장: propagation/watershed4/propagation_reliable.csv  "
        f"(n_events≥2: {len(df_lag_reliable)}쌍)",
    )

    if not df_lag_reliable.empty:
        print("\n  [신뢰 쌍 상위 20개] (n_events≥2, |lag| 큰 순)")
        top = df_lag_reliable.reindex(
            df_lag_reliable["mean_lag_h"].abs().sort_values(ascending=False).index
        ).head(20)
        print(top.to_string(index=False))
    else:
        print("  [참고] n_events≥2 쌍 없음 — 이벤트가 1개뿐인 관측소 쌍이 대부분")
        print("  전체 쌍 상위 10개:")
        print(df_lag.head(10).to_string(index=False))


def write_propagation_lag_dtw_check_outputs(df_nb: pd.DataFrame) -> None:
    """dtw_check.ipynb 스타일 집계 CSV 저장."""
    print(f"\n{'─'*50}")
    print(
        "전파 시차 (dtw_check.ipynb 방식 · sphereLarge×korStream_x · "
        "gdt 해발고도순 인접 쌍 · min–max + DTW(distance) + scipy.correlate)",
    )
    if df_nb.empty:
        print("  [경고] 결과 없음 — 윈도 parquet, 메타(korStream_x·sphereLarge·gdt) 확인")
        return

    path_main = PROPAGATION_STREAM / "propagation.csv"
    df_nb.to_csv(path_main, index=False, encoding="utf-8-sig")
    print(f"  저장: propagation/stream_adjacent/propagation.csv  (전체 {len(df_nb)}쌍)")

    rel = df_nb[df_nb["n_events"] >= 2].sort_values(
        ["sphereLarge", "korStream_x", "mean_lag_h"],
    )
    path_rel = PROPAGATION_STREAM / "propagation_reliable.csv"
    rel.to_csv(path_rel, index=False, encoding="utf-8-sig")
    print(
        f"  저장: propagation/stream_adjacent/propagation_reliable.csv  "
        f"(n_events≥2: {len(rel)}쌍)",
    )

    if not rel.empty:
        print("\n  [dtw_check 방식 신뢰 쌍 상위 15개] (n_events≥2, |mean_lag_h| 큰 순)")
        top = rel.reindex(
            rel["mean_lag_h"].abs().sort_values(ascending=False).index
        ).head(15)
        print(top.to_string(index=False))


def write_propagation_stream_calendar_outputs(
    df_peak: pd.DataFrame,
    df_cc: pd.DataFrame,
) -> None:
    """달력축 기반 전파 CSV (``propagation_<방법>.csv``)."""
    print(f"\n{'─'*50}")
    print(
        "전파 시차 (달력 시간축 · stream_adjacent 쌍 동일 · "
        "① 피크 시각 차 ② 공통 datetime 교차상관)",
    )
    if not df_peak.empty:
        p = PROPAGATION_STREAM / "propagation_peaktime_delta.csv"
        df_peak.to_csv(p, index=False, encoding="utf-8-sig")
        print(f"  저장: propagation/stream_adjacent/propagation_peaktime_delta.csv  ({len(df_peak)}쌍)")
        rel = df_peak[df_peak["n_events"] >= 2].sort_values(
            ["sphereLarge", "korStream_x", "mean_lag_h"],
        )
        rel.to_csv(
            PROPAGATION_STREAM / "propagation_peaktime_delta_reliable.csv",
            index=False, encoding="utf-8-sig",
        )
        print(
            "  저장: propagation/stream_adjacent/propagation_peaktime_delta_reliable.csv  "
            f"(n_events≥2: {len(rel)}쌍)",
        )
    else:
        print("  [경고] propagation_peaktime_delta — 결과 없음 (datetime·parquet 확인)")

    if not df_cc.empty:
        p = PROPAGATION_STREAM / "propagation_cc_common_time.csv"
        df_cc.to_csv(p, index=False, encoding="utf-8-sig")
        print(f"  저장: propagation/stream_adjacent/propagation_cc_common_time.csv  ({len(df_cc)}쌍)")
        rel = df_cc[df_cc["n_events"] >= 2].sort_values(
            ["sphereLarge", "korStream_x", "mean_lag_h"],
        )
        rel.to_csv(
            PROPAGATION_STREAM / "propagation_cc_common_time_reliable.csv",
            index=False, encoding="utf-8-sig",
        )
        print(
            "  저장: propagation/stream_adjacent/propagation_cc_common_time_reliable.csv  "
            f"(n_events≥2: {len(rel)}쌍)",
        )
    else:
        print("  [경고] propagation_cc_common_time — 결과 없음")


def run_crosscorr_lag_only() -> None:
    """교차상관 시차·DTW 집계만 실행 (클러스터링·시각화 생략)."""
    print("=" * 60)
    print("교차상관 시차(propagation) 전용 실행")
    print("=" * 60)
    obs_meta = load_obs_meta()
    df_lag = compute_propagation_lag(obs_meta)
    write_propagation_lag_outputs(df_lag)
    obs_nb = load_obs_meta_dtw_check()
    df_nb = compute_propagation_lag_dtw_check_notebook(obs_nb)
    write_propagation_lag_dtw_check_outputs(df_nb)
    df_pt = compute_propagation_peaktime_delta_stream(obs_nb)
    df_cc_ct = compute_propagation_cc_common_time_stream(obs_nb)
    write_propagation_stream_calendar_outputs(df_pt, df_cc_ct)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    print("=" * 60)
    print(f"DTW 분석 시작  (N_CLUSTERS={N_CLUSTERS}, window={DTW_WINDOW}h)")
    print("=" * 60)

    obs_meta         = load_obs_meta()
    obs_meta_dtw     = load_obs_meta_dtw_check()
    all_cluster_dfs  : list[pd.DataFrame] = []
    all_events       : list[str]          = []

    for parquet_path in EVENT_FILES:
        event_date = parquet_path.stem.replace("_wl", "")
        print(f"\n{'─'*50}")
        print(f"[이벤트] {event_date}")

        # 1. Load
        series_matrix, obscds, kor_obs_list = load_window(parquet_path)
        n = len(obscds)
        print(f"  관측소 {n}개 / 시리즈 길이 {len(series_matrix)}h")

        # 2. DTW distance matrix
        print("  DTW 거리 행렬 계산 중...", end=" ", flush=True)
        dist_matrix = compute_dtw_matrix(series_matrix)
        np.save(OUT_BASE / "distance" / f"{event_date}_dist.npy", dist_matrix)
        print(f"완료 ({n}×{n})")

        # 3. Clustering
        df_clusters, Z = cluster_stations(dist_matrix, obscds, kor_obs_list, N_CLUSTERS)
        df_clusters["event_date"] = event_date
        df_clusters.to_csv(
            OUT_BASE / "clusters" / f"{event_date}_clusters.csv",
            index=False, encoding="utf-8-sig",
        )
        print(f"  클러스터링 완료")
        for cid in sorted(df_clusters["cluster_id"].unique()):
            grp      = df_clusters[df_clusters["cluster_id"] == cid]
            centroid = grp[grp["is_centroid"]]["korObs"].values[0]
            print(f"    Cluster {cid}: {len(grp):3d}개 관측소 / 대표: {centroid}")

        all_cluster_dfs.append(
            df_clusters[["obscd", "korObs", "cluster_id", "event_date"]]
        )
        all_events.append(event_date)

        # 4. Top-k similarity
        df_topk = compute_topk(dist_matrix, obscds, kor_obs_list)
        df_topk.to_csv(
            OUT_BASE / "similarity" / f"{event_date}_topk.csv",
            index=False, encoding="utf-8-sig",
        )

        # 5. Visualizations
        print("  시각화 저장 중...", end=" ", flush=True)
        plot_dendrogram(
            Z, kor_obs_list, event_date,
            OUT_BASE / "viz" / f"{event_date}_dendrogram.png",
        )
        plot_heatmap(
            dist_matrix, df_clusters.reset_index(drop=True),
            event_date, OUT_BASE / "viz" / f"{event_date}_heatmap.png",
        )
        plot_cluster_profiles(
            series_matrix, df_clusters,
            event_date, OUT_BASE / "viz" / f"{event_date}_cluster_profiles.png",
        )
        print("완료")

    # 6. Propagation lag (cross-correlation on window series)
    df_lag = compute_propagation_lag(obs_meta)
    write_propagation_lag_outputs(df_lag)
    df_nb = compute_propagation_lag_dtw_check_notebook(obs_meta_dtw)
    write_propagation_lag_dtw_check_outputs(df_nb)
    df_pt = compute_propagation_peaktime_delta_stream(obs_meta_dtw)
    df_cc_ct = compute_propagation_cc_common_time_stream(obs_meta_dtw)
    write_propagation_stream_calendar_outputs(df_pt, df_cc_ct)

    # 7. Consistency
    print(f"\n{'─'*50}")
    print("이벤트 간 일관성 분석...")
    df_long        = pd.concat(all_cluster_dfs, ignore_index=True)
    df_consistency = compute_consistency(all_cluster_dfs)
    df_consistency.to_csv(
        OUT_BASE / "clusters" / "consistency.csv",
        index=False, encoding="utf-8-sig",
    )
    plot_consistency(df_long, all_events, OUT_BASE / "viz" / "consistency_heatmap.png")

    high = df_consistency[df_consistency["consistency_score"] >= 0.8]
    print(f"  일관성 ≥ 0.8 관측소: {len(high)} / {len(df_consistency)}개")
    print("  저장: clusters/consistency.csv / viz/consistency_heatmap.png")

    # 8. Final summary
    print(f"\n{'='*60}")
    print("[완료] 출력 파일 목록")
    for f in sorted(OUT_BASE.rglob("*.*")):
        rel = f.relative_to(OUT_BASE)
        if rel.parts[0] in {"distance", "clusters", "similarity", "propagation", "viz"}:
            print(f"  output/DTW/{rel}")
    print("=" * 60)
    print(f"\n덴드로그램 확인 후 N_CLUSTERS 조정이 필요하면")
    print(f"  run_dtw.py 상단의 N_CLUSTERS = {N_CLUSTERS} 값을 수정 후 재실행하세요.")


def _cli() -> None:
    parser = argparse.ArgumentParser(description="DTW 분석 파이프라인")
    parser.add_argument(
        "--lag-only",
        action="store_true",
        help="교차상관 시차(propagation)·CSV 저장만 실행",
    )
    args = parser.parse_args()
    if args.lag_only:
        run_crosscorr_lag_only()
    else:
        run()


if __name__ == "__main__":
    _cli()
