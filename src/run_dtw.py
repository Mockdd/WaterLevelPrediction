"""DTW analysis pipeline for flood event station similarity.

Outputs (output/DTW/):
  distance/{event}_dist.npy                   pairwise DTW distance matrix
  clusters/{event}_clusters.csv               cluster label + centroid flag
  clusters/consistency.csv                    cross-event consistency per station
  similarity/{event}_topk.csv                 top-5 nearest neighbors per station
  propagation/peak_lag.csv                    peak-to-peak lag (same-watershed pairs)
  viz/{event}_dendrogram.png
  viz/{event}_heatmap.png
  viz/{event}_cluster_profiles.png
  viz/consistency_heatmap.png

Usage:
    pip install dtaidistance scipy matplotlib
    python src/run_dtw.py

Tune N_CLUSTERS after inspecting dendrograms and re-run.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import dendrogram, fcluster, linkage
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

PEAKS_CSV  = OUT_BASE / "peaks.csv"
OBS_TARGET = ROOT / "metadata_outputs" / "obsTarget.csv"

DTW_WINDOW = 24    # Sakoe-Chiba band (±24 hours)
N_CLUSTERS = 7     # initial cluster count — adjust after viewing dendrograms
TOPK       = 5     # top-k similar stations per station

HOURS_MIN = -72    # -3 days
HOURS_MAX = 120    # +5 days

EVENT_FILES = sorted(WIN_DIR.glob("*_wl.parquet"))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_obs_meta() -> pd.DataFrame:
    df = pd.read_csv(OBS_TARGET, index_col=0, dtype=str)
    df["obscd"]      = df["codeObs"].str.replace(r"\.0$", "", regex=True)
    df["watershed4"] = df["codeWatershed"].str[:4]
    return df[["obscd", "korObs", "codeWatershed", "watershed4"]].drop_duplicates("obscd")


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
# Propagation lag  (peak-to-peak, same-watershed pairs)
# ---------------------------------------------------------------------------

def compute_propagation_lag(obs_meta: pd.DataFrame) -> pd.DataFrame:
    """
    For every same-watershed pair (A, B), compute mean peak-to-peak lag
    across events.  Positive lag means B's peak occurs after A's.
    """
    df_peaks = pd.read_csv(PEAKS_CSV, parse_dates=["peak_time"])
    df_peaks["obscd"] = df_peaks["obscd"].astype(str).str.replace(r"\.0$", "", regex=True)
    df_peaks = df_peaks.merge(obs_meta[["obscd", "watershed4"]], on="obscd", how="left")
    df_peaks = df_peaks[df_peaks["has_peak"] & df_peaks["peak_time"].notna()].copy()

    rows = []
    for event_date, ev_grp in df_peaks.groupby("event_date"):
        ev_grp = ev_grp.set_index("obscd")
        for _, ws_grp in ev_grp.groupby("watershed4"):
            stas = ws_grp.index.tolist()
            for i in range(len(stas)):
                for j in range(i + 1, len(stas)):
                    a, b = stas[i], stas[j]
                    lag  = (
                        ws_grp.loc[b, "peak_time"] - ws_grp.loc[a, "peak_time"]
                    ).total_seconds() / 3600
                    rows.append({
                        "event_date":   event_date,
                        "watershed4":   ws_grp["watershed4"].iloc[0],
                        "obscd_a":      a,
                        "korObs_a":     ws_grp.loc[a, "korObs"],
                        "obscd_b":      b,
                        "korObs_b":     ws_grp.loc[b, "korObs"],
                        "lag_a_to_b_h": round(lag, 2),
                    })

    if not rows:
        return pd.DataFrame()

    df_lag = pd.DataFrame(rows)
    agg = (
        df_lag.groupby(["watershed4", "obscd_a", "korObs_a", "obscd_b", "korObs_b"])
        ["lag_a_to_b_h"]
        .agg(mean_lag_h="mean", std_lag_h="std", n_events="count")
        .reset_index()
    )
    agg["mean_lag_h"] = agg["mean_lag_h"].round(2)
    agg["std_lag_h"]  = agg["std_lag_h"].fillna(0).round(2)
    return agg.sort_values(["watershed4", "mean_lag_h"])


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
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    print("=" * 60)
    print(f"DTW 분석 시작  (N_CLUSTERS={N_CLUSTERS}, window={DTW_WINDOW}h)")
    print("=" * 60)

    obs_meta         = load_obs_meta()
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

    # 6. Propagation lag
    print(f"\n{'─'*50}")
    print("홍수파 전파 시간 계산 중 (같은 수계 쌍, peak-to-peak)...")
    df_lag = compute_propagation_lag(obs_meta)
    if not df_lag.empty:
        # 전체 저장
        df_lag.to_csv(
            OUT_BASE / "propagation" / "peak_lag.csv",
            index=False, encoding="utf-8-sig",
        )
        print(f"  저장: propagation/peak_lag.csv  (전체 {len(df_lag)}쌍)")

        # n_events >= 2인 신뢰 쌍만 별도 저장
        df_lag_reliable = (
            df_lag[df_lag["n_events"] >= 2]
            .sort_values(["watershed4", "mean_lag_h"])
        )
        df_lag_reliable.to_csv(
            OUT_BASE / "propagation" / "peak_lag_reliable.csv",
            index=False, encoding="utf-8-sig",
        )
        print(f"  저장: propagation/peak_lag_reliable.csv  (n_events≥2: {len(df_lag_reliable)}쌍)")

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
    else:
        print("  [경고] 같은 수계 관측소 쌍 없음 — obs_meta 확인 필요")

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


if __name__ == "__main__":
    run()
