# 최종 클러스터·Top-k (`output/DTW/final/`)

## 무엇인가

여러 홍수 이벤트에 대해 이미 계산된 **이벤트별 클러스터**(`output/DTW/clusters/*_clusters.csv`)와 **top-k 유사도**(`output/DTW/similarity/*_topk.csv`)를 읽어, 관측소마다 **한 벌의 “최종” 라벨·이웃 목록**을 여러 규칙으로 만든 결과입니다. 스크립트: `src/finalize_dtw.py`.

```bash
python src/finalize_dtw.py
```

`output/DTW/final/` 디렉터리가 없으면 생성됩니다.

## 클러스터 통합 방법 (C1–C4)

| 파일 | 요약 |
|------|------|
| `clusters_C1_dominant.csv` | 이벤트 전체에서 **최빈** 클러스터 |
| `clusters_C2_sil_weighted.csv` | 이벤트별 **실루엣 점수 가중** 투표 |
| `clusters_C3_best_event.csv` | 관측소별 **실루엣이 가장 높은 이벤트**의 클러스터 채택 |
| `clusters_C4_consensus.csv` | `consistency_score`가 임계값 이상일 때만 배정, 그렇지 않으면 `unstable` 등으로 표시 (임계값은 스크립트 내 `CONSISTENCY_CUTOFF`) |

## Top-k 통합 방법 (T1–T4)

| 파일 | 요약 |
|------|------|
| `topk_T1_avg_dist.csv` | 이벤트 간 DTW 거리 **평균**이 작은 순으로 top-k |
| `topk_T2_frequency.csv` | 이벤트별 top-k에 **포함된 횟수**가 많은 이웃 우선 |
| `topk_T3_sil_weighted.csv` | 실루엣으로 가중한 평균 거리 기반 top-k |
| `topk_T4_intersection.csv` | **과반수 이상**의 이벤트에서 공통으로 top-k에 든 이웃만 유지 |

## 비교용 표

- `clusters_method_comparison.csv`: 관측소별 C1–C4 결과와 `all_agree`(네 방법 모두 동일 여부).
- `topk_method_comparison.csv`: rank-1 이웃이 T1–T4에서 얼마나 일치하는지 등.

## 입력 전제

`output/DTW/clusters/`에 `*_clusters.csv`가 있고, `similarity/`에 `*_topk.csv`가 있어야 합니다. 먼저 `python src/run_dtw.py`로 이벤트별 산출물을 생성한 뒤 `finalize_dtw.py`를 실행하는 흐름이 자연스럽습니다.

## 다른 실험 폴더

과거 실행물은 `output/DTW/results/<실험명>/final/` 등에 복사·보관된 경우가 있습니다. 내용 형식은 위와 동일한 CSV 패턴을 따릅니다.
