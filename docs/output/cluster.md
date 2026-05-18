# 클러스터·유사도 (`output/DTW/clusters/`, `similarity/`, `viz/`)

## 개요

`src/run_dtw.py` 전체 실행 시, 이벤트별 DTW 거리 행렬을 바탕으로 **계층적 클러스터링(Ward)** 과 **유사도 top-k** 를 저장하고, 요약 시각화를 `viz/`에 둡니다. 모든 이벤트를 돌린 뒤 **이벤트 간 클러스터 일관성**을 `clusters/consistency.csv`로 정리합니다.

## `clusters/{event_date}_clusters.csv`

| 컬럼 (예) | 설명 |
|-----------|------|
| `obscd` | 관측소 코드 |
| `korObs` | 관측소 표시명 |
| `cluster_id` | `fcluster(..., criterion="maxclust")`로 얻은 클러스터 라벨 (1부터) |
| `is_centroid` | 해당 클러스터에서 평균 DTW 거리가 가장 작은 **대표 관측소** 여부 |
| `silhouette_score` | 사전 계산 거리 행렬 기준 실루엣 |
| `intra_mean_dist` | 동일 클러스터 내 다른 관측소와의 평균 거리 |
| `event_date` | `YYYY-MM-DD` |

클러스터 개수 `N_CLUSTERS`는 `run_dtw.py` 상단 상수로 조정합니다.

## `clusters/consistency.csv`

이벤트마다 다른 클러스터가 배정될 수 있으므로, 관측소별로 **가장 자주 나온 클러스터**와 그 비율(`consistency_score`)을 집계합니다. `dominant_cluster`, `event_count` 등으로 후속 분석(`finalize_dtw.py`의 C4 등)에 쓰입니다.

## `similarity/{event_date}_topk.csv`

각 관측소에 대해 DTW 거리가 가까운 상위 `TOPK`(기본 5) 이웃 관측소와 거리를 행으로 저장합니다.

## `viz/`

| 파일 (예) | 용도 |
|-----------|------|
| `{event_date}_dendrogram.png` | Ward linkage 덴드로그램 |
| `{event_date}_heatmap.png` | 거리·클러스터 히트맵 |
| `{event_date}_cluster_profiles.png` | 클러스터별 평균 수위 프로파일 |
| `consistency_heatmap.png` | 이벤트 × 관측소 클러스터 일관성 히트맵 |

## 실행 순서 참고

1. `output/DTW/windows/*_wl.parquet` 존재  
2. `python src/run_dtw.py`  
3. (선택) `python src/finalize_dtw.py` — `docs/output/final.md` 참고  

`python src/run_dtw.py --lag-only`는 **클러스터·거리·유사도·viz를 건너뛰고** 전파(propagation)만 갱신합니다.
