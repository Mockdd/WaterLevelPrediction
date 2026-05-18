"""최종 클러스터 및 top-k 산출 - 다양한 방법 비교.

최종 클러스터 선정 방법 (4가지):
  C1. Dominant      : 이벤트 걸쳐 가장 많이 배정된 클러스터 (최빈값)
  C2. Sil-weighted  : 실루엣 점수로 가중 투표 (신뢰도 높은 이벤트 우선)
  C3. Best-event    : 관측소별 실루엣 점수 최고 이벤트의 클러스터 채택
  C4. Consensus     : consistency_score >= 임계값인 관측소만 배정, 나머지 'unstable'

최종 top-k 선정 방법 (4가지):
  T1. Avg-distance  : 이벤트 간 DTW 거리 평균 → 거리 작은 순 top-k
  T2. Frequency     : 이벤트별 top-k에 등장한 횟수 → 빈도 높은 순 top-k
  T3. Sil-weighted  : 실루엣 점수로 가중한 DTW 거리 평균 → top-k
  T4. Intersection  : 과반(≥ n_events/2) 이벤트에서 top-k에 포함된 관측소만

출력 (output/DTW/final/):
  clusters_C1_dominant.csv
  clusters_C2_sil_weighted.csv
  clusters_C3_best_event.csv
  clusters_C4_consensus.csv
  clusters_method_comparison.csv   ← 4가지 방법 나란히 비교
  topk_T1_avg_dist.csv
  topk_T2_frequency.csv
  topk_T3_sil_weighted.csv
  topk_T4_intersection.csv
  topk_method_comparison.csv       ← 관측소별 방법 간 일치 여부 비교

Usage:
    python src/finalize_dtw.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT       = Path(__file__).resolve().parents[1]
DTW_DIR    = ROOT / "output" / "DTW"
CLUS_DIR   = DTW_DIR / "clusters"
SIM_DIR    = DTW_DIR / "similarity"
FINAL_DIR  = DTW_DIR / "final"
FINAL_DIR.mkdir(exist_ok=True)

TOPK               = 5
CONSISTENCY_CUTOFF = 0.6   # C4: 이 값 미만이면 'unstable'

EVENT_FILES = sorted(CLUS_DIR.glob("*_clusters.csv"))


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------

def load_all_clusters() -> pd.DataFrame:
    """5개 이벤트 clusters CSV를 합쳐 반환."""
    frames = []
    for f in EVENT_FILES:
        df = pd.read_csv(f, dtype={"obscd": str})
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def load_all_topk() -> pd.DataFrame:
    """5개 이벤트 topk CSV를 합쳐 반환."""
    frames = []
    for f in sorted(SIM_DIR.glob("*_topk.csv")):
        df = pd.read_csv(f, dtype={"obscd": str, "neighbor_obscd": str})
        event = f.stem.replace("_topk", "")
        df["event_date"] = event
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Final cluster methods
# ---------------------------------------------------------------------------

def method_C1_dominant(df: pd.DataFrame) -> pd.DataFrame:
    """최빈 클러스터 (단순 다수결)."""
    rows = []
    for (obscd, kor_obs), grp in df.groupby(["obscd", "korObs"]):
        counts   = grp["cluster_id"].value_counts()
        dominant = int(counts.idxmax())
        score    = round(counts.max() / len(grp), 3)
        rows.append({"obscd": obscd, "korObs": kor_obs,
                     "final_cluster": dominant, "confidence": score,
                     "method": "C1_dominant"})
    return pd.DataFrame(rows)


def method_C2_sil_weighted(df: pd.DataFrame) -> pd.DataFrame:
    """실루엣 점수로 가중 투표.
    각 이벤트의 실루엣 점수(양수만)를 가중치로 클러스터별 합산.
    """
    rows = []
    for (obscd, kor_obs), grp in df.groupby(["obscd", "korObs"]):
        # 음수 실루엣은 0으로 클리핑 (해당 이벤트에서 배정이 불안정)
        grp = grp.copy()
        grp["weight"] = grp["silhouette_score"].clip(lower=0)

        # 가중합이 모두 0이면 단순 최빈값으로 fallback
        if grp["weight"].sum() == 0:
            grp["weight"] = 1.0

        weighted = grp.groupby("cluster_id")["weight"].sum()
        best_cid = int(weighted.idxmax())
        confidence = round(weighted.max() / weighted.sum(), 3)
        rows.append({"obscd": obscd, "korObs": kor_obs,
                     "final_cluster": best_cid, "confidence": confidence,
                     "method": "C2_sil_weighted"})
    return pd.DataFrame(rows)


def method_C3_best_event(df: pd.DataFrame) -> pd.DataFrame:
    """관측소별 실루엣 점수가 가장 높은 이벤트의 클러스터 채택."""
    rows = []
    for (obscd, kor_obs), grp in df.groupby(["obscd", "korObs"]):
        best_row   = grp.loc[grp["silhouette_score"].idxmax()]
        rows.append({"obscd": obscd, "korObs": kor_obs,
                     "final_cluster": int(best_row["cluster_id"]),
                     "confidence":    round(float(best_row["silhouette_score"]), 3),
                     "best_event":    best_row["event_date"],
                     "method":        "C3_best_event"})
    return pd.DataFrame(rows)


def method_C4_consensus(df: pd.DataFrame, cutoff: float = CONSISTENCY_CUTOFF) -> pd.DataFrame:
    """consistency_score >= cutoff인 관측소만 배정, 나머지 'unstable'."""
    c1 = method_C1_dominant(df)
    counts = df.groupby("obscd")["cluster_id"].value_counts()

    rows = []
    for _, row in c1.iterrows():
        if row["confidence"] >= cutoff:
            final = int(row["final_cluster"])
            label = str(final)
        else:
            final = -1
            label = "unstable"
        rows.append({"obscd": row["obscd"], "korObs": row["korObs"],
                     "final_cluster": final, "final_label": label,
                     "confidence": row["confidence"], "method": "C4_consensus",
                     "cutoff": cutoff})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Final top-k methods
# ---------------------------------------------------------------------------

def method_T1_avg_dist(df_topk: pd.DataFrame) -> pd.DataFrame:
    """이벤트 간 DTW 거리 평균 → top-k."""
    agg = (
        df_topk.groupby(["obscd", "korObs", "neighbor_obscd", "neighbor_korObs"])
        ["dtw_dist"]
        .agg(mean_dist="mean", n_events="count")
        .reset_index()
    )
    rows = []
    for (obscd, kor_obs), grp in agg.groupby(["obscd", "korObs"]):
        top = grp.nsmallest(TOPK, "mean_dist").reset_index(drop=True)
        for rank, r in top.iterrows():
            rows.append({"obscd": obscd, "korObs": kor_obs,
                         "rank": rank + 1,
                         "neighbor_obscd":  r["neighbor_obscd"],
                         "neighbor_korObs": r["neighbor_korObs"],
                         "score": round(r["mean_dist"], 4),
                         "n_events": int(r["n_events"]),
                         "method": "T1_avg_dist"})
    return pd.DataFrame(rows)


def method_T2_frequency(df_topk: pd.DataFrame) -> pd.DataFrame:
    """이벤트별 top-k 등장 횟수 → 빈도 높은 순 top-k."""
    counts = (
        df_topk.groupby(["obscd", "korObs", "neighbor_obscd", "neighbor_korObs"])
        .size()
        .reset_index(name="freq")
    )
    rows = []
    for (obscd, kor_obs), grp in counts.groupby(["obscd", "korObs"]):
        top = grp.nlargest(TOPK, "freq").reset_index(drop=True)
        for rank, r in top.iterrows():
            rows.append({"obscd": obscd, "korObs": kor_obs,
                         "rank": rank + 1,
                         "neighbor_obscd":  r["neighbor_obscd"],
                         "neighbor_korObs": r["neighbor_korObs"],
                         "score": int(r["freq"]),
                         "method": "T2_frequency"})
    return pd.DataFrame(rows)


def method_T3_sil_weighted(df_topk: pd.DataFrame, df_clus: pd.DataFrame) -> pd.DataFrame:
    """실루엣 점수로 가중한 DTW 거리 평균 → top-k.
    가중치 = clip(silhouette_score, 0, None), 0이면 1로 대체.
    """
    sil_map = (
        df_clus[["obscd", "event_date", "silhouette_score"]]
        .assign(weight=lambda x: x["silhouette_score"].clip(lower=0.01))
        .set_index(["obscd", "event_date"])["weight"]
    )

    def get_weight(obscd, event_date):
        try:
            return sil_map.loc[(obscd, event_date)]
        except KeyError:
            return 1.0

    df_topk = df_topk.copy()
    df_topk["weight"] = df_topk.apply(
        lambda r: get_weight(r["obscd"], r["event_date"]), axis=1
    )
    df_topk["weighted_dist"] = df_topk["dtw_dist"] * (1 / df_topk["weight"])

    agg = (
        df_topk.groupby(["obscd", "korObs", "neighbor_obscd", "neighbor_korObs"])
        .apply(lambda g: pd.Series({
            "weighted_mean_dist": np.average(g["dtw_dist"], weights=g["weight"]),
            "n_events": len(g),
        }))
        .reset_index()
    )

    rows = []
    for (obscd, kor_obs), grp in agg.groupby(["obscd", "korObs"]):
        top = grp.nsmallest(TOPK, "weighted_mean_dist").reset_index(drop=True)
        for rank, r in top.iterrows():
            rows.append({"obscd": obscd, "korObs": kor_obs,
                         "rank": rank + 1,
                         "neighbor_obscd":  r["neighbor_obscd"],
                         "neighbor_korObs": r["neighbor_korObs"],
                         "score": round(r["weighted_mean_dist"], 4),
                         "n_events": int(r["n_events"]),
                         "method": "T3_sil_weighted"})
    return pd.DataFrame(rows)


def method_T4_intersection(df_topk: pd.DataFrame) -> pd.DataFrame:
    """과반 이벤트(>= n_events/2)에서 top-k에 포함된 관측소만 채택.
    해당 조건 충족 관측소가 TOPK 미만이면 available로 모두 포함.
    """
    n_total_events = df_topk["event_date"].nunique()
    threshold = n_total_events / 2

    counts = (
        df_topk.groupby(["obscd", "korObs", "neighbor_obscd", "neighbor_korObs"])
        .size()
        .reset_index(name="freq")
    )
    # 과반 이상 등장한 이웃
    stable = counts[counts["freq"] >= threshold].copy()

    # 과반 미달이면 가장 빈도 높은 순으로 채움
    rows = []
    for (obscd, kor_obs), grp in counts.groupby(["obscd", "korObs"]):
        stable_grp = grp[grp["freq"] >= threshold].nlargest(TOPK, "freq")
        if len(stable_grp) < TOPK:
            # 부족분은 빈도 순으로 보충
            extra = grp[grp["freq"] < threshold].nlargest(
                TOPK - len(stable_grp), "freq"
            )
            stable_grp = pd.concat([stable_grp, extra]).head(TOPK)

        for rank, r in stable_grp.reset_index(drop=True).iterrows():
            rows.append({"obscd": obscd, "korObs": kor_obs,
                         "rank": rank + 1,
                         "neighbor_obscd":  r["neighbor_obscd"],
                         "neighbor_korObs": r["neighbor_korObs"],
                         "score": int(r["freq"]),
                         "is_stable": r["freq"] >= threshold,
                         "method": "T4_intersection"})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Comparison tables
# ---------------------------------------------------------------------------

def build_cluster_comparison(c1, c2, c3, c4) -> pd.DataFrame:
    base = c1[["obscd", "korObs"]].copy()
    for df, col in [(c1, "C1_dominant"), (c2, "C2_sil_weighted"),
                    (c3, "C3_best_event"), (c4, "C4_consensus")]:
        val_col = "final_label" if "final_label" in df.columns else "final_cluster"
        merged = df[["obscd", val_col, "confidence"]].copy()
        merged.columns = ["obscd", col, f"{col}_conf"]
        base = base.merge(merged, on="obscd", how="left")

    # 4가지 방법이 모두 동의하는지
    clus_cols = ["C1_dominant", "C2_sil_weighted", "C3_best_event"]
    base["all_agree"] = base[clus_cols].nunique(axis=1) == 1
    return base


def build_topk_comparison(t1, t2, t3, t4) -> pd.DataFrame:
    """각 방법의 rank-1 이웃이 일치하는지 비교."""
    rows = []
    for method_df, name in [(t1, "T1"), (t2, "T2"), (t3, "T3"), (t4, "T4")]:
        r1 = method_df[method_df["rank"] == 1][["obscd", "korObs", "neighbor_korObs"]].copy()
        r1.columns = ["obscd", "korObs", f"{name}_rank1"]
        rows.append(r1)

    comp = rows[0]
    for r in rows[1:]:
        comp = comp.merge(r[["obscd", r.columns[-1]]], on="obscd", how="outer")

    rank1_cols = [c for c in comp.columns if "rank1" in c]
    comp["rank1_agree"] = comp[rank1_cols].nunique(axis=1) == 1
    return comp


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    print("=" * 60)
    print("최종 클러스터 / top-k 산출 (다방법 비교)")
    print("=" * 60)

    df_clus = load_all_clusters()
    df_topk = load_all_topk()
    print(f"클러스터 데이터: {len(df_clus)}행 ({df_clus['event_date'].nunique()}개 이벤트)")
    print(f"top-k 데이터:   {len(df_topk)}행")

    # ── Cluster methods ─────────────────────────────────────────
    print("\n[클러스터 선정]")

    c1 = method_C1_dominant(df_clus)
    c2 = method_C2_sil_weighted(df_clus)
    c3 = method_C3_best_event(df_clus)
    c4 = method_C4_consensus(df_clus)

    for df, name, fname in [
        (c1, "C1 Dominant",       "clusters_C1_dominant.csv"),
        (c2, "C2 Sil-weighted",   "clusters_C2_sil_weighted.csv"),
        (c3, "C3 Best-event",     "clusters_C3_best_event.csv"),
        (c4, "C4 Consensus",      "clusters_C4_consensus.csv"),
    ]:
        df.to_csv(FINAL_DIR / fname, index=False, encoding="utf-8-sig")
        n_stable = (df["final_cluster"] != -1).sum() if "final_cluster" in df.columns else len(df)
        unstable = len(df) - n_stable
        print(f"  {name}: 저장 완료 ({n_stable}개 배정, {unstable}개 unstable)")

    # 비교표
    comp_c = build_cluster_comparison(c1, c2, c3, c4)
    comp_c.to_csv(FINAL_DIR / "clusters_method_comparison.csv", index=False, encoding="utf-8-sig")
    agree_rate = comp_c["all_agree"].mean()
    print(f"\n  [비교] 4방법 완전 일치 관측소: {comp_c['all_agree'].sum()}/{len(comp_c)} ({agree_rate:.1%})")

    # 방법 간 불일치 사례 출력
    disagree = comp_c[~comp_c["all_agree"]][["korObs", "C1_dominant", "C2_sil_weighted", "C3_best_event", "C4_consensus"]]
    if not disagree.empty:
        print(f"\n  [불일치 사례 상위 10개]")
        print(disagree.head(10).to_string(index=False))

    # ── Top-k methods ────────────────────────────────────────────
    print("\n[Top-k 선정]")

    t1 = method_T1_avg_dist(df_topk)
    t2 = method_T2_frequency(df_topk)
    t3 = method_T3_sil_weighted(df_topk, df_clus)
    t4 = method_T4_intersection(df_topk)

    for df, name, fname in [
        (t1, "T1 Avg-distance",   "topk_T1_avg_dist.csv"),
        (t2, "T2 Frequency",      "topk_T2_frequency.csv"),
        (t3, "T3 Sil-weighted",   "topk_T3_sil_weighted.csv"),
        (t4, "T4 Intersection",   "topk_T4_intersection.csv"),
    ]:
        df.to_csv(FINAL_DIR / fname, index=False, encoding="utf-8-sig")
        n_sta = df["obscd"].nunique()
        print(f"  {name}: 저장 완료 ({n_sta}개 관측소)")

    # rank-1 비교표
    comp_t = build_topk_comparison(t1, t2, t3, t4)
    comp_t.to_csv(FINAL_DIR / "topk_method_comparison.csv", index=False, encoding="utf-8-sig")
    agree_rate_t = comp_t["rank1_agree"].mean()
    print(f"\n  [비교] rank-1 4방법 일치 관측소: {comp_t['rank1_agree'].sum()}/{len(comp_t)} ({agree_rate_t:.1%})")

    disagree_t = comp_t[~comp_t["rank1_agree"]].drop(columns=["rank1_agree"])
    if not disagree_t.empty:
        print(f"\n  [rank-1 불일치 사례 상위 10개]")
        print(disagree_t.head(10).to_string(index=False))

    # ── Summary ─────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"저장 위치: output/DTW/final/")
    print(f"  clusters_C1_dominant.csv        ← 단순 다수결")
    print(f"  clusters_C2_sil_weighted.csv    ← 실루엣 가중 투표")
    print(f"  clusters_C3_best_event.csv      ← 최고 신뢰 이벤트 채택")
    print(f"  clusters_C4_consensus.csv       ← 일관성 {CONSISTENCY_CUTOFF} 미만 → unstable")
    print(f"  clusters_method_comparison.csv  ← 4방법 나란히 비교")
    print(f"  topk_T1_avg_dist.csv            ← 거리 평균")
    print(f"  topk_T2_frequency.csv           ← 등장 빈도")
    print(f"  topk_T3_sil_weighted.csv        ← 실루엣 가중 거리 평균")
    print(f"  topk_T4_intersection.csv        ← 과반 이벤트 교집합")
    print(f"  topk_method_comparison.csv      ← rank-1 방법 간 비교")
    print("=" * 60)


if __name__ == "__main__":
    run()
