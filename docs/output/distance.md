# DTW 거리 행렬 (`output/DTW/distance/`)

## 무엇인가

이벤트별로 관측소 쌍 전체에 대한 **DTW(Dynamic Time Warping) 거리 행렬**을 바이너리로 저장한 결과입니다. `src/run_dtw.py` 전체 파이프라인(`python src/run_dtw.py`)을 돌릴 때, 이벤트 윈도 parquet 하나마다 한 번씩 계산됩니다.

## 파일 이름

| 패턴 | 예시 |
|------|------|
| `{event_date}_dist.npy` | `2024-07-18_dist.npy` |

`event_date`는 윈도 파일명과 동일하게 `YYYY-MM-DD` 형식입니다 (`output/DTW/windows/{event_date}_wl.parquet`와 대응).

## 내용·형태

- **형식**: NumPy 배열 (`.npy`), `numpy.load`로 읽습니다.
- **크기**: \(N \times N\) 대칭 행렬 (`N` = 해당 이벤트에 데이터가 있는 관측소 수).
- **의미**: 행·열 순서는 같은 이벤트의 `output/DTW/clusters/{event_date}_clusters.csv`에 나오는 `obscd` 순서와 **동일**합니다. 인덱스 \(i, j\)는 그 이벤트에서 \(i\)번째·\(j\)번째 관측소 쌍의 DTW 거리입니다.
- **계산**: `dtaidistance`의 `distance_matrix_fast`, Sakoe–Chiba 밴드는 스크립트 상단 `DTW_WINDOW`(시간)로 제한됩니다. 대각선은 0으로 채워집니다.

## 입력 데이터

각 이벤트의 `hours_from_peak` 격자에 맞춘 정규 수위 시계열 `wl_norm` 피벗(보간 후)을 사용합니다. 자세한 윈도 생성은 `src/extract_peaks.py` 등을 참고하면 됩니다.

## 관련 산출물

같은 실행에서 `clusters/`, `similarity/`, `viz/`가 함께 갱신됩니다. 거리만 다시 쓰고 싶다면 전체 `run_dtw.py`를 재실행하거나, 해당 로직만 별도 스크립트로 호출해야 합니다(`--lag-only`는 거리 행렬을 만들지 않습니다).
