# 전파 시차·정렬 (`output/DTW/propagation/`)

## 개요

관측소 **쌍**에 대해 홍수 이벤트 윈도에서 수위 형태가 얼마나 **시간적으로 어긋나 있는지**를 숫자로 모은 결과입니다. `src/run_dtw.py`의 전체 실행 또는 **`python src/run_dtw.py --lag-only`** 로 갱신됩니다. (lag-only는 DTW 거리·클러스터는 생략하고 전파 CSV만 다시 씁니다.)

입력: `output/DTW/windows/{event_date}_wl.parquet` (long 포맷, `hours_from_peak`, `datetime`, `wl_norm` 등). 메타: `metadata_outputs/obsTarget.csv` (및 stream 경로용 HRFCO `gdt` 캐시 등).

## `propagation/watershed4/`

같은 **수계 코드 앞 4자리(`watershed4`)** 안에 있는 관측소 **모든 쌍**에 대해:

- 이벤트마다 `hours_from_peak`로 정렬된 `wl_norm`에 **교차상관**으로 시차(`lag_a_to_b_h`), DTW 거리 등을 계산하고,
- 쌍·수계별로 이벤트 간 **평균·표준편차**를 집계합니다.

| 파일 | 설명 |
|------|------|
| `propagation.csv` | 전체 쌍 |
| `propagation_reliable.csv` | `n_events >= 2` 인 쌍만 (불안정한 평균 제외 목적) |

**부호 해석(요약)**: `mean_lag_h`가 양수이면 B가 A보다 **늦게** 올라오는 정렬(도달 지연 쪽 해석)에 맞춰져 있습니다. 자세한 정의는 `run_dtw.py`의 `crosscorr_time_lag_hours` 주석을 참고하세요.

## `propagation/stream_adjacent/`

`dtw_check.ipynb`와 맞춘 **권역(`sphereLarge`) × 하천(`korStream_x`)** 안에서, HRFCO 고도(`water_elevation`) 순으로 정렬한 **인접 상·하류 쌍**만 다룹니다.

| 파일 | 설명 |
|------|------|
| `propagation.csv` / `propagation_reliable.csv` | 피크-시간축(`hours_from_peak`) 정렬 행렬에 min–max 정규화·DTW·`scipy.signal.correlate` 시차 (`lag_hours_nb` 등) |
| `propagation_peaktime_delta.csv` (+ `_reliable`) | **달력 `datetime`**에서 `wl_norm` 최대 시각의 차 (하류 − 상류, 시간) |
| `propagation_cc_common_time.csv` (+ `_reliable`) | 두 관측소를 **공통 `datetime`**으로 inner join한 뒤 동일한 correlate 정의로 시차 (샘플 간격 중앙값으로 시간 환산) |

## `propagation/qa/`

| 파일 | 설명 |
|------|------|
| `validation_report.txt` | `python src/validate_propagation_outputs.py` 실행 시 stdout과 동일 내용을 파일로 저장 |

검증 스크립트는 스키마·`n_events` 상한·거리·라그 범위 등 기본 점검과, 가능하면 디스크의 `stream_adjacent/propagation.csv`와 재계산 결과 **라운드트립** 비교를 수행합니다.

## 주의

- 윈도 parquet에 **`datetime`** 컬럼이 없으면 달력 기반 두 파일(`propagation_peaktime_delta*`, `propagation_cc_common_time*`)은 비어 있거나 경고만 출력될 수 있습니다. `src/extract_peaks.py`로 윈도를 다시 만들면 `datetime`이 포함됩니다.
- 전파는 **물리적 파동속도**가 아니라 **통계적 정렬 시차**에 가깝습니다. 해석 시 상류·하류 정의, 이벤트 선택, 윈도 길이의 영향을 함께 고려하는 것이 좋습니다.
