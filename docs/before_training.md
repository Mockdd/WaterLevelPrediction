# 학습 전 데이터 준비 — 누락 점검 & CCF 결정

본 문서는 TFT 등 시계열 모델에 넣기 **직전** 파이프라인에서 흔히 빠지는 사항을 모아 두고, **교차상관(CCF)으로 상류 수위 lag을 정할 때** 점검·기록할 항목을 리스트로 둔다.

### 워크플로 합의(별도 실험 트랙 없음)

**CCF에 필요한 결정사항(D0–D9, 품질 게이트 등)은 “실험 폴더만 따로 돌린 뒤 한 번에 확정”하지 않고, 본 파이프라인(수위 적재 → 스켈레톤·merge → CCF·lag 산출 → 학습 입력까지)을 돌리면서** 부족한 가정을 채우고, 이 문서를 **그때그때 갱신**한다.  
이렇게 해야 **A1(결측/보간)·D0(S3/API)**처럼 “실제로 학습에 쓰는 수위와 같은 축·같은 전처리”가 CCF와 자동으로 맞는다.

관련 문서: [`docs/waterlevel_missing_handling.md`](waterlevel_missing_handling.md)(결측), [`docs/metadata_outputs/upstream_mapping_must.md`](metadata_outputs/upstream_mapping_must.md)(상류 매핑·`lag0` 정책).

---

## 1. 데이터 준비 과정(참고 흐름)

아래 번호는 대화에서 정리된 **준비 순서**이다. 실제 구현 순서와 다를 수 있으나, “무엇이 먼저 고정되어야 하는지” 점검용으로 쓴다.

| 단계 | 내용 |
|------|------|
| 1 | 강수 관측소 ↔ 수위 관측소 매핑 테이블 |
| 2 | 상류 관측소 2개 지정 → `metadata_outputs/upstream_mapping_must.csv` |
| 3 | CCF로 상류별 time lag 추정 → `metadata_outputs/upstream_lag_ccf_by_stationv20260514_0735.csv |  
| 4 | `station_id` × `datetime` 스켈레톤 |
| 5 | 수위 long merge |
| 6 | 관측소 static 메타 merge |
| 7 | 강수 merge ([1] 매핑 정보 기반 등) |
| 8 | `upstream_wl_1`, `upstream_wl_2` — `shift(lag)` |
| 9 | 이상치 제거 |
| 10 | `wl_diff` 계산 |
| 11 | 결측 처리 |
| 12 | `hour`, `month`, `time_idx` 생성 |
| 13 | train / val / test split | 
| 14 | `GroupNormalizer` fit(train만) → transform |
| 15 | `TimeSeriesDataSet` 생성 |

---

## 1.1 수위 원시 적재(데이터 fetch)

- **기간(합의):** `2023-03-01` ~ `2025-10-31` · **1H** · 관측소 **`metadata_outputs/obsFinalStreamReg.csv`** (한강·동해·서해; `obsTarget.csv`는 3권 부분집합 ~118개).  
- **S3 키:** `hrfco/raw/{year}/waterlevel/date={YYYY-MM-DD}/data.parquet` — 컬럼 `datetime`, `obscd`, `value`, `date` (`src/_s3_missing_analysis.py` 와 동일).  
- **실행:** `python src/ingest_hrfco_waterlevel_s3.py` (인자 생략 시 **전 구간**·전 관측소). `.env`: `hrfco_token`, `S3_BUCKET`, AWS 자격. API **요청당 기간 상한**은 스크립트가 **청크(기본 최대 330일)**로 나눠 처리한다.  
- **원시 격자 요약:** `metadata_outputs/hrfco_waterlevel_missingness_by_station_day.csv` — 컬럼 `obscd`, `calendar_date`, `row_count`, `uniq_hours`, `missing_hours_est`, `value_na` (같은 실행에서 갱신, `--no-missingness` 로 생략).  
- **이후 4~5단계:** 스켈레톤·merge는 **위 S3·1H**만 사용한다(S3에 없는 일자는 스켈레톤상 결측으로 남는다).

## 1.2 소스

백필은 **API → S3**, CCF·학습용 읽기는 **S3**만 쓴다.

## 1.3 Split·TFT 윈도·CCF (고정안 — 검토)

**데이터 달력(전체):** `2023-03-01` ~ `2025-10-31` (1H, S3 적재와 동일).

### train / val / test (시간순 블록)

| 구간 | 시작(포함) | 끝(포함) |
|------|------------|----------|
| train | `2023-03-01` | `2024-08-31` |
| val | `2024-09-01` | `2025-03-31` |
| test | `2025-04-01` | `2025-10-31` |

### TFT 윈도(고정)

| 항목 | 값 |
|------|-----|
| encoder 최대 길이 **E** | 168 (1H 스텝) |
| 최대 예측 lead **H** | 6 (1h·2h·3h·6h) |

### CCF (이 split과 맞춤)

| 항목 | 고정(1차안) | 검토 포인트 |
|------|-------------|-------------|
| **적합 구간** | `fit_start=2023-03-01`, `fit_end=2024-08-31` (= train) | val·test는 lag 피크·임계값 튜닝에 **미사용** |
| **D1 시계열** | **Q1:** train 창·동시 유효 격자에서 결측률 **<30%**이면 **11번과 동일 보간(상한 포함)** 후 CCF, **≥30%**이면 **미보간** 원시로 CCF. `ccf_input_branch` 기록. 상세·보완은 아래 **§3 D0–D9**·**§2.1(A1–A7)** 표와 동일 선상에서 고정한다. | 8번은 **동일 분기**로 수위 생성 후 `shift`. |
| **D3 필터** | 없음(전 train 사용) | 필요 시 고수위 부분집합은 **train 안 통계만** |
| **D4 `L_max`** | 120 스텝(120h), `L ∈ [0,120]` | **E=168** 이하·전파 상한 검토 |
| **D5 게이트** | 쌍별 `n_effective ≥ 336`(14일×24h) 미만이면 비채택 | `max_corr`·2위 피크 차 임계는 구현·플롯 보고 조정 |
| **D6 fallback** | 채택 실패 시 `lag_steps=0`, `reliable=false`, `fallback_reason` 기록 | 스트림 내 중앙값 등으로 바꿀지 검토 |
| **D7** | `upstream_mapping_must.md` (`lag0`, NaN) | `lag0==True`는 **행 유지(Q5 B)** + 8번 마스킹 |
| **D8 부호** | `src/run_dtw.py` 의 `crosscorr_lag_steps_dtw_check`(ref=상류, tgt=하류)와 **동일 정의**로 `L`→`shift` 매핑 문서화 | 스모크 N6 |
| **D9 산출** | **버전 파일명** `upstream_lag_xccf_v*.csv` + 고정 **`metadata_outputs/upstream_lag_manifest.json`** (`active_csv` 등). 상세는 **§3 D9**·**§4 N5·N7**와 `upstream_lag_manifest.json` 스키마를 따른다. | 버전·커밋 A7 |

**GroupNormalizer:** train 캘린더만 fit(§2.3 C4).

---

## 1.4 스켈레톤 vs 학습 데이터셋

| 개념 | 정의 | 행 단위 |
|------|------|---------|
| **스켈레톤(패널)** | `station_id` × `datetime`(1H) **전 기간** 격자 + merge·전처리 결과 | 관측소·시각당 1행 |
| **training dataset** | 스켈레톤 중 `split==train` 만 골라 **관측소별 시계열**로 만든 뒤, Darts TFT **슬라이딩 윈도**(E=168, H=6) | 윈도 1개 = 샘플 1개 |

- 스켈레톤 **한 장**에 `train` / `val` / `test` 가 **모두** 들어 있다 (`split` 컬럼).
- S3에 값이 없어도 스켈레톤 **행은 유지**하고 `wl=NaN` 으로 남긴다(결측률·자격 판단용).
- TFT `fit` 에 넣는 관측소 목록은 스켈레톤과 **다를 수 있다** → §1.5 자격 표 참고.

구현: `src/tft_preprocess.py` → `tft_panel.parquet`, `tft_{train,val,test}.parquet`.

---

## 1.5 학습 전 관측소 문서화 (필수 산출)

학습 직전에 **관측소 단위 1행** 요약을 고정해 두고, 정책 변경 시 **같은 실행**으로 다시 생성한다.

### 산출 파일

| 파일 | 경로(기본) | 용도 |
|------|------------|------|
| **관측소 자격 표** | `metadata_outputs/tft_station_eligibility.csv` | TFT 학습 포함/제외·사유 |
| 전처리 메타 | `data/tft_processed/preprocess_meta.json` | 기간·E/H·포함 관측소 수 |
| (선택) 패널 | `data/tft_processed/tft_panel.parquet` | 스켈레톤+피처 전체 |

`src/tft_preprocess.py` 실행 시 자격 표를 **자동 생성**한다.

### 자격 표 컬럼(최소)

| 컬럼 | 설명 |
|------|------|
| `station_id` | 수위 관측소 코드 (`codeObs`) |
| `n_hours_train` | train 구간 스켈레톤 시각 수 |
| `n_valid_wl_train` | train 구간에서 수위 유효(비결측) 시각 수 |
| `missing_rate_wl_train` | train 구간 수위 결측 비율 `1 - n_valid/n_hours` |
| `impute_branch` | `interpolated` (<30%, limit 24h) 또는 `no_interp_high_missing` (≥30%) |
| `min_required_hours` | `encoder_length + prediction_length` (기본 174) |
| `included_tft_train` | `Y` / `N` — Darts `fit` 에 넣을지 |
| `exclude_reason` | 제외 시 사유(복수는 `;` 구분) |

### TFT 학습 포함 규칙 (1차 합의)

train 구간(`2023-03-01` ~ `2024-08-31`) 기준:

1. **`missing_rate_wl_train` ≥ 0.30** → `included_tft_train=N` (`exclude_reason=missing_rate_ge_30pct`). 스켈레톤 행은 유지, **보간하지 않음**.
2. **`n_valid_wl_train == 0`** → `N` (`no_waterlevel_in_train`).
3. **`n_hours_train < encoder_length + prediction_length`** → `N` (`too_short_for_window`).
4. 위에 해당 없으면 → `Y`.

CCF(Q1)와 동일하게 **결측률 판단은 train 구간만** 쓴다(val/test로 자격을 정하지 않음).

### 스켈레톤 행 vs 관측소 제외

| 상황 | 스켈레톤 행 | TFT 학습 |
|------|-------------|----------|
| 수위 전무 | 있음 (`wl` NaN) | **제외** |
| 결측 ≥30% | 있음, 보간 안 함 | **제외** |
| 결측 <30% | 있음, limit 24h 보간 | **포함**(윈도 조건 충족 시) |

제외 관측소는 **행을 삭제하지 않는다**. 감사·결측 리포트·나중 재처리를 위해 패널에 남긴다.

### 학습 스크립트 연동

- `src/tft_preprocess.py` → 패널·자격 표 생성.
- `src/train_tft_darts.py` → `tft_station_eligibility.csv` 의 `included_tft_train==Y` 만 `fit`.
- `src/eval_tft_darts.py` → val/test rolling forecast·지표 (상세: [`docs/tft_evaluation.md`](tft_evaluation.md)).
- 합숙/재현 시 **“메타 관측소 N개 중 학습 M개”** 를 자격 표와 함께 제시한다.

## 1.6 Validation / test 평가

- **문서:** [`docs/tft_evaluation.md`](tft_evaluation.md) — 데이터셋 정의, 지표(MAE·RMSE·NSE·MAPE·bias), rolling 평가 절차, 출력 파일.
- **실행:** `python -u src/eval_tft_darts.py --split val|test|both --experiment-name <name> --processed-dir <dir>`
- **지표는 역스케일 수위(m)** 기준; test는 최종 hold-out 1회만 보고.

### 전처리 코드에서 아직 선택·미구현(참고)

| 항목 | 상태 | 메모 |
|------|------|------|
| 관측소 자격 CSV | **구현됨** | `tft_station_eligibility.csv` |
| train 구간 30% 분기 보간 | **구현됨** | 전 구간 시계열에 동일 분기 적용 |
| 윈도 내 장기 결측 샘플 제외 | **미구현** | `waterlevel_missing_handling` M2 — Darts 샘플러·커스텀 필터 후속 |
| `obsFinalStreamReg` vs `obsTarget` | **기본 `obsFinalStreamReg`** | 한강 3권만: `--stations-csv metadata_outputs/obsTarget.csv` |
| TFT I/O 상세 | [`docs/tft_io_spec.md`](tft_io_spec.md) | 입출력·Colab·팀 공유 산출물 |

---


## 2. 놓치기 쉬운 것들(점검 리스트)

### 2.1 CCF·상류 수위 lag과 직접 맞닿는 항목(우선 반영)

CCF 또는 `shift(lag)`와 **정의가 어긋나면** 조용히 성능만 나빠지는 경우가 많다. 아래는 **한 가지로 통일해 문서·코드에 박아둘** 항목이다.

| ID | 항목 | 왜 중요한지 | 점검 질문 |
|----|------|-------------|-----------|
| **A1** | **결측·보간(11번)과 CCF 입력의 일치** | 학습 테이블은 보간된 수위인데 CCF는 원시로 하면 피크 lag이 달라질 수 있다. | CCF는 **shift에 쓰는 수위와 동일 전처리**인가, 아니면 **원측정+마스크만**인가? 둘 중 하나로 명시했는가? |
| **A2** | **이상치 제거(9번)와 CCF의 일치** | 한쪽만 제거하면 lag 추정 분포와 학습 입력이 어긋난다. | CCF 전에 9번과 **동일 규칙**을 적용하는가, **의도적으로 미적용**하는가? |
| **A3** | **CCF 피크 ↔ `shift` 방향(부호)** | 구현마다 “양의 lag” 의미가 달라 **반대로 shift**하는 실수가 흔하다. | “하류 시점 `t`의 상류 피처 = `upstream(t − L)`”처럼 **한 문장으로 정의**했는가? CCF 라이브러리 출력을 **L**에 어떻게 매핑했는가? |
| **A4** | **쌍별 유효 길이 `n_effective`** | 겹치는 관측이 짧으면 CCF 피크가 사실상 랜덤이다. | 쌍마다 유효 시점 수를 저장하는가? 임계 미만이면 **fallback·신뢰 플래그**가 있는가? |
| **A5** | **lag는 “시간”이 아니라 “스텝”** | 해상도가 바뀌면 같은 물리 지연도 스텝 수가 바뀐다. | lag CSV에 **freq(예: 1H)**를 같이 저장하는가? 리샘플 변경 시 **lag 재추정**을 강제하는가? |
| **A6** | **CCF 품질 게이트** | 저상관·다봉에서 argmax는 불안정하다. | `max_corr` 하한, `L_max`, 2위 피크와의 차이 등 **채택 조건**이 정의되어 있는가? |
| **A7** | **재현성** | 나중에 “왜 이 lag인지”를 추적해야 한다. | CCF에 쓴 **캘린더 구간·커밋·스켈레톤 규칙·코드 버전**을 산출물 메타에 남기는가? |

### 2.2 `upstream_mapping_must.csv`와 파이프라인 정합

| ID | 항목 | 점검 질문 |
|----|------|-----------|
| **B1** | `upstream_*` NaN | 해당 슬롯 **lag 피처 미생성**으로 코드·스키마가 맞는가? |
| **B2** | `upstream_*_lag0 == True` | **CCF 생략**, lag=0·값 마스킹 등 **문서 정책과 동일**한가? |
| **B3** | `upstream_*_lag0 == False` | CCF 후보에 넣되, **A6 게이트** 실패 시 fallback이 정의되어 있는가? |
| **B4** | `fix_1` / `fix_2` | 모델 입력에 실수로 넣지 않았는가? |

### 2.3 누수·분할·하이퍼파라미터

| ID | 항목 | 점검 질문 |
|----|------|-----------|
| **C1** | lag 추정 구간 | CCF에 **test 구간을 넣지 않는다**는 원칙이 명시되어 있는가? **val을 lag 튜닝**에 쓰지 않는가(쓰면 선택 누수)? |
| **C2** | “완화안” 사용 시 | train만이 아니라 더 긴 과거를 쓸 경우 **문서화·고정**되어 있는가? |
| **C3** | 홍수기만 CCF 등 | 그 필터 통계를 **어느 구간에서만** 봤는지(미래 test 반영 여부) 명시되어 있는가? |
| **C4** | `GroupNormalizer` | **train만 fit** 후 val/test에 동일 적용이 코드와 일치하는가? |
| **C5** | 윈도 샘플링 | encoder 길이·prediction horizon이 **split 경계**와 충돌하지 않는가(앞쪽 버림 등)? |

### 2.4 시간축·정렬

| ID | 항목 | 점검 질문 |
|----|------|-----------|
| **D1** | 타임존 | 원시·스켈레톤·merge 전부 **동일 TZ(또는 UTC 단일)**인가? |
| **D2** | DST | 서머타임이 있는 지역/원천이면 **중복·누락 시각** 처리 규칙이 있는가? |
| **D3** | 리샘플 규칙 | `mean` / `instant` / `asfreq` 등 **학습·CCF·서빙**이 동일한가? |

### 2.5 강수 매핑·공변량

| ID | 항목 | 점검 질문 |
|----|------|-----------|
| **E1** | nearest 정의 | 거리·동일 수계 제약·동점 타이브레이크가 고정되어 있는가? |
| **E2** | 강수 결측 | 수위만 있고 강수 없을 때 **마스크·대체**가 학습·서빙 동일한가? |

### 2.6 TFT / `TimeSeriesDataSet` 계약

| ID | 항목 | 점검 질문 |
|----|------|-----------|
| **F1** | known vs unknown | 상류 수위·강수·시간 특성이 **문서화된 그룹**과 일치하는가? |
| **F2** | `time_idx` | 연속 정수·그룹별 시작이 라이브러리 요구와 맞는가? |
| **F3** | 그룹 키 | `group_ids`(예: 관측소)가 **서빙 시 동일**한가? |
| **F4** | encoder 대 lag | `L`이 encoder 길이 대비 **비정상적으로 크지** 않은가(물리 상한·게이트)? |

### 2.7 학습 ↔ 서빙

| ID | 항목 | 점검 질문 |
|----|------|-----------|
| **G1** | 동일 함수 | `docs/waterlevel_missing_handling.md`의 **L2 동일 계약**을 CCF 산출 lag 적용·결측·정규화까지 포함해 지키는가? |
| **G2** | lag CSV | 서빙 시 **동일 파일(또는 동일 버전)**을 읽는가? |

---

## 3. Cross-correlation — 결정·기록할 항목(D0–D9)

아래 항목은 **본 파이프라인을 돌리며** CCF를 붙일 때 순서대로 채우거나 수정한다(사전에 전부 고정할 필요 없음). 다만 **D8(부호)·D9(산출 스키마)**는 구현이 갈라지지 않도록 가능한 한 일찍 고정하는 것이 좋다.

| # | 결정 사항 | 선택지 예시 | 메모 |
|---|-----------|-------------|------|
| D0 | **HRFCO 읽기 경로** | API 직호출 / S3 적재 후 읽기 | **§1.1–1.2** — 백필 **API→S3**, 이후 **S3**만 |
| D1 | **CCF에 쓸 시계열** | (a) 원시/스켈레톤 수위 (b) 저주파 제거 후 (c) 차분 \(\Delta W\) | raw vs 전처리는 **A1·A2**와 함께 고정 |
| D2 | **적합 캘린더 구간** | train만 / test 이전 전체(단, test는 피크 선택에 미사용) 등 | **C1·C2**와 연결 |
| D3 | **시점 필터(선택)** | 전 기간 vs 홍수기만 vs 공분산 임계 이상만 | 필터를 쓰면 **C3** 기록 |
| D4 | **탐색 lag 범위** | `L ∈ [0, L_max]` 스텝; `L_max` 물리·구간 길이 기반 | 스텝=**A5**와 연동 |
| D5 | **채택 규칙** | `max_corr` 하한, 1·2위 피크 차이, `n_effective` 하한 | **A6** |
| D6 | **비채택 시 fallback** | lag=0, 스트림 내 중앙값, 수동 테이블 등 | 산출물에 `fallback_reason` 권장 |
| D7 | **`lag0`/NaN 처리** | `lag0=True`·`upstream` NaN은 CCF 제외 규칙 | **B1·B2** |
| D8 | **부호·컨벤션** | CCF 출력 인덱스 → `L` → `pandas.shift` 한 줄 매핑 | **A3** 문서화 |
| D9 | **산출물 형식** | 버전 CSV + `upstream_lag_manifest.json` | **§1.3 D9**·**§4 N5·N7** |

---

## 4. Cross-correlation — 필요한 사항(데이터·산출·코드)

점검할 때 **준비됨 / 미정**으로 표시해 두면 좋다.

| # | 필요 사항 | 설명 |
|---|-----------|------|
| N0 | **수위 소스 경로** | **§1.1** S3 키·기간·`ingest` 스크립트와 동일하게 고정 |
| N1 | **정렬된 수위** | CCF 쌍 `(하류 station_id, upstream_k)`마다 **동일 `datetime` 격자**上的 `W_u`, `W_s` (학습에서 shift할 freq와 동일) |
| N2 | **매핑 테이블** | `upstream_mapping_must.csv`와 join 가능한 키(`station_id`, `upstream_1`, `upstream_2`) |
| N3 | **마스크 또는 결측 규칙** | 쌍별 유효 시점 정의(**A1**과 일치) |
| N4 | **train 경계** | **§1.3** — CCF `fit_start` / `fit_end` (= train) |
| N5 | **산출 테이블** | 최소: `station_id`, `upstream_slot`, `upstream_station_id`, `lag_steps`, `freq`, `fit_start`, `fit_end`, `max_corr`, `n_effective`, `method`, (선택) `reliable`, `fallback_reason` |
| N6 | **스모크 테스트** | 소수 쌍에 대해 **시각적으로** upstream shift 정렬 확인(**A3**) |
| N7 | **버전 관리** | lag 파일명 또는 메타에 **데이터 스냅샷·커밋** 기록(**A7**) |

---

## 5. 다음 액션(권장)

1. **CCF 규약:** 본 문서 **§1.3**·**§3–§4**·**§2.1** — 구현·검증·manifest 규약.  
2. **본 파이프라인**에서 §1.1 적재를 유지한 채 CCF 스크립트·**§1.3**·§3·§4를 동기화한다.  
3. **섹션 2.1(A1–A7)**은 CCF·merge 변경 시 리그레션 체크리스트로 쓴다.  
4. **학습 전:** `python src/tft_preprocess.py` → `tft_station_eligibility.csv` 확인 후 `python src/train_tft_darts.py`.  
5. 정책이 바뀌면 본 문서·[`docs/metadata_outputs/upstream_mapping_must.md`](metadata_outputs/upstream_mapping_must.md)를 함께 갱신한다.
