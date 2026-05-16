# TFT 입출력·산출물 스펙 (Colab / 팀 공유)

학습: `src/train_tft_darts.py` · 전처리: `src/tft_preprocess.py` · 평가: `src/eval_tft_darts.py`  
정책: [`before_training.md`](before_training.md) · 평가: [`tft_evaluation.md`](tft_evaluation.md)

---

## 1. Colab에서 학습하기

### 1.1 준비

```python
# 셀 1: 클론 + 의존성
%cd /content
!git clone https://github.com/<ORG>/FloodAX.git   # 팀 repo URL
%cd FloodAX
!pip install -q "darts[torch]" pytorch-lightning torch pandas pyarrow boto3 python-dotenv scikit-learn joblib
```

```python
# 셀 2: Drive 마운트 (가중치·전처리 산출 영구 저장)
from google.colab import drive
drive.mount("/content/drive")
PROCESSED = "/content/drive/MyDrive/floodax/tft_processed"   # 전처리 결과
WORK      = "/content/drive/MyDrive/floodax/experiments/tft" # 학습 산출
```

**전처리** (Colab에서 S3 직접 — `.env`에 `S3_BUCKET`, AWS 키, `WEATHER_AWS_key` 필요):

```bash
!python -u src/tft_preprocess.py \
  --out-dir /content/drive/MyDrive/floodax/tft_processed
```

이미 로컬에서 전처리했다면 **폴더 통째로 Drive에 업로드**하고 `PROCESSED`만 맞추면 됨.

### 1.2 학습

```bash
!python -u src/train_tft_darts.py \
  --processed-dir /content/drive/MyDrive/floodax/tft_processed \
  --work-dir /content/drive/MyDrive/floodax/experiments/tft \
  --experiment-name baseline_full_v1 \
  --eligibility-csv /content/drive/MyDrive/floodax/tft_processed/tft_station_eligibility.csv \
  --n-epochs 50 \
  --accelerator gpu \
  --save-predictions
```

### 1.3 평가 (선택)

```bash
!python -u src/eval_tft_darts.py \
  --split val \
  --experiment-name baseline_full_v1 \
  --processed-dir /content/drive/MyDrive/floodax/tft_processed \
  --work-dir /content/drive/MyDrive/floodax/experiments/tft
```

---

## 2. 모델 가중치 — 어디에·어떻게 저장되나

학습 종료 시 **실험 디렉터리** 하나에 묶입니다.

```
{work_dir}/{experiment_name}/
├── tft_model.pt              ← ★ Darts TFT 전체 (가중치+구조+하이퍼)
├── train_args.json             ← 학습 CLI 인자 전부 (재현용)
├── train_station_ids.json      ← fit에 쓴 관측소 ID 목록
├── scalers_ref.joblib          ← 참고용 메모 (역스케일은 processed-dir 쪽)
├── val_predictions.parquet     ← --save-predictions 시만
└── eval_val/                   ← eval_tft_darts.py 실행 시
    ├── metrics_val.json
    ├── predictions_val.parquet
    └── dataset_summary.json
```

| 항목 | 값 |
|------|-----|
| 기본 `work_dir` | `experiments/tft` |
| 기본 `experiment_name` | `floodax_tft_baseline` |
| 체크포인트 파일명 | `tft_model.pt` (`--checkpoint-name`으로 변경 가능) |
| 저장 API | `TFTModel.save(path)` (Darts 네이티브 pickle/torch) |
| 로드 | `TFTModel.load(path)` (`eval_tft_darts.py` 동일) |

학습 중 Lightning 체크포인트도 켜져 있음 (`save_checkpoints=True`)이나, **팀 공유·추론의 기준 파일은 `tft_model.pt`** 로 통일하는 것을 권장.

**Colab 권장:** `work_dir`를 **Google Drive** 아래로 두어 런타임 종료 후에도 유지.

---

## 3. 팀원에게 전달할 패키지

### 3.1 최소 세트 (추론·평가만)

| # | 경로 | 필수 | 용도 |
|---|------|------|------|
| 1 | `{work_dir}/{experiment_name}/tft_model.pt` | ✅ | 학습된 TFT |
| 2 | `{work_dir}/{experiment_name}/train_args.json` | ✅ | E/H, covariate 목록, lr 등 재현 |
| 3 | `{work_dir}/{experiment_name}/train_station_ids.json` | ✅ | 학습에 포함된 관측소 |
| 4 | `{processed_dir}/scalers.joblib` | ✅ | 수위·강수·상류 **역스케일** |
| 5 | `{processed_dir}/preprocess_meta.json` | ✅ | 기간·scale_cols·관측소 수 |
| 6 | `{processed_dir}/tft_station_eligibility.csv` | ✅ | 평가/추론 관측소 필터 |

### 3.2 권장 세트 (재전처리 없이 동일 입력 재구성)

위 6개 + 아래:

| # | 경로 | 용도 |
|---|------|------|
| 7 | `{processed_dir}/tft_panel.parquet` | 학습·평가 입력 패널 (스케일됨) |
| 8 | `{processed_dir}/tft_{train,val,test}.parquet` | split별 (선택) |
| 9 | `eval_{split}/metrics_*.json` | 성능 보고 |
| 10 | `eval_{split}/predictions_*.parquet` | 예측 상세 |

### 3.3 전처리부터 재현 (S3 접근 가능한 팀원)

| # | 경로 |
|---|------|
| 11 | `metadata_outputs/obsFinalStreamReg.csv` |
| 12 | `metadata_outputs/obsWaterLevel_top1.csv` |
| 13 | `metadata_outputs/upstream_mapping_must.csv` |
| 14 | `metadata_outputs/upstream_lag_manifest.json` + active lag CSV |
| 15 | `experiments/tft/tft_experiment_plan.json` |
| 16 | Git **commit hash** (메일/슬랙에 텍스트) |

**.env / AWS 키 / KMA 키는 공유 금지** — S3 접근 권한은 별도 IAM으로.

### 3.4 한 줄 요약

> **모델 폴더(`tft_model.pt` + `train_args.json` + `train_station_ids.json`) + 전처리 폴더(`scalers.joblib` + `preprocess_meta.json` + `tft_station_eligibility.csv` + 가능하면 `tft_panel.parquet`)**

---

## 4. 물리 입력 (S3 원시)

### 4.1 HRFCO 수위

| 항목 | 스펙 |
|------|------|
| S3 키 | `hrfco/raw/{year}/waterlevel/date={YYYY-MM-DD}/data.parquet` |
| 컬럼 | `datetime`, `obscd`, `value`, `date` |
| `obscd` | 수위 관측소 = 패널 `station_id` |
| `value` | 수위(m), 1H |
| 관측소 목록 | `metadata_outputs/obsFinalStreamReg.csv` → `codeObs` |

### 4.2 KMA AWS 강수

| 항목 | 스펙 |
|------|------|
| S3 키 | `kma/raw/{year}/aws_awsh_1h/date={YYYY-MM-DD}/data.parquet` |
| 컬럼 | `datetime`, `stn_id`, `var`, `value`, `date` |
| 사용 변수 | `var == "RN"` |
| `stn_id` | AWS 관측소 — `obsWaterLevel_top1.csv`의 `stn_id_aws`와 join |

### 4.3 상류 수위 (파생)

| 항목 | 스펙 |
|------|------|
| 매핑 | `upstream_mapping_must.csv` → `upstream_1`, `upstream_2` |
| lag | `upstream_lag_ccf_by_station_v20260514_0735.csv` → `lag_steps_upstream_1/2` |
| 규칙 | 하류 시각 `t`에서 `upstream_wl_k(t) = wl[upstream_k](t - L)` (`shift(L)`) |
| lag0 | `upstream_*_lag0==True` → 값 0, mask 0 |

---

## 5. 패널 스키마 (`tft_panel.parquet`) — 모델 직전 입력

한 행 = **`station_id` × `datetime`(1H)**. 수치는 **관측소별 StandardScaler** 적용 후( train 구간으로 fit).

| 컬럼 | Darts 역할 | dtype | 설명 |
|------|------------|-------|------|
| `station_id` | index | str | HRFCO `codeObs` |
| `datetime` | time | datetime64 | 1H, KST 기준(원시와 동일) |
| `split` | — | str | `train` / `val` / `test` |
| `wl` | **target** | float | 수위(스케일) |
| `wl_diff` | past cov | float | `wl` 1차 차분(스케일) |
| `rn` | past cov | float | 강수 mm(스케일), 결측→0 |
| `stn_id_aws` | — | str | 매핑 검증용, 모델 미투입 |
| `upstream_wl_1` | past cov | float | 상류1 수위(스케일) |
| `upstream_wl_2` | past cov | float | 상류2 수위(스케일) |
| `upstream_wl_1_mask` | past cov | int8 | 1=유효, 0=결측/lag0 |
| `upstream_wl_2_mask` | past cov | int8 | 동일 |
| `was_imputed` | past cov | int8 | 수위 보간 여부 |
| `impute_branch` | — | str | `interpolated` / `no_interp_high_missing` |
| `hour_sin`, `hour_cos` | future cov | float | 시각 주기 |
| `month_sin`, `month_cos` | future cov | float | 월 주기 |
| `time_idx` | — | int | 관측소 내 0…T-1 (참고용) |

**스케일 대상** (`preprocess_meta.scale_cols`): `wl`, `wl_diff`, `rn`, `upstream_wl_1`, `upstream_wl_2`  
**스케일 키** (`scalers.joblib`): `"{station_id}|{col}"` → `sklearn.preprocessing.StandardScaler`

---

## 6. Darts TFT에 들어가는 변수 그룹

| 그룹 | 컬럼 (기본) | 윈도 |
|------|-------------|------|
| **Target** | `wl` | 과거 E=168 스텝 + 미래 H=6 예측 |
| **Past covariates** | `wl_diff`, `rn`, `upstream_wl_1`, `upstream_wl_2`, `upstream_wl_1_mask`, `upstream_wl_2_mask`, `was_imputed` | encoder 구간만 |
| **Future covariates** | `hour_sin`, `hour_cos`, `month_sin`, `month_cos` | encoder + **예측 H 구간** (미래 시각 알려짐) |

| 하이퍼 | 기본 | CLI |
|--------|------|-----|
| Encoder E | 168 | `--input-chunk-length` |
| Horizon H | 6 | `--output-chunk-length` |
| 분위 | 0.1, 0.5, 0.9 | `--quantiles` |

**학습에 쓰는 관측소:** `tft_station_eligibility.csv`에서 `included_tft_train == Y`  
**최소 시계열 길이:** `E + H + 1` (= 175 시간)

---

## 7. 모델 출력 (추론)

| 항목 | 스펙 |
|------|------|
| 형식 | Darts `TimeSeries`, `QuantileRegression` |
| 의미 | 앞으로 **H=6**시간의 **스케일된 `wl`** 분포 |
| 분위 | 기본 **0.1, 0.5, 0.9** — 점예측은 **0.5** 사용 |
| lead | 1h, 2h, 3h, 6h (= 1…H 스텝) |
| 물리 단위 변환 | `scalers.joblib`의 `{station_id}\|wl` 역변환 → **수위(m)** |

`predict(n=H, series=..., past_covariates=..., future_covariates=...)`  
- `series`: `datetime <= t₀` 까지 이력  
- `future_covariates`: `t₀+1 … t₀+H` 시간 피처 필요  

평가 스크립트 상세: [`tft_evaluation.md`](tft_evaluation.md).

---

## 8. 시간 split (고정)

| split | 시작 | 끝 | 용도 |
|-------|------|-----|------|
| train | 2023-03-01 | 2024-08-31 | fit, scaler fit, eligibility |
| val | 2024-09-01 | 2025-03-31 | early stopping, `eval --split val` |
| test | 2025-04-01 | 2025-10-31 | hold-out, `eval --split test` |

---

## 9. 관측소 자격 (`tft_station_eligibility.csv`)

| 컬럼 | 설명 |
|------|------|
| `station_id` | 수위 관측소 |
| `n_hours_train` | train 스켈레톤 시각 수 |
| `n_valid_wl_train` | train 유효 수위 수 |
| `missing_rate_wl_train` | train 결측 비율 |
| `impute_branch` | 보간 분기 |
| `included_tft_train` | `Y` / `N` — **학습·평가 포함** |
| `exclude_reason` | 제외 사유 |

---

## 10. 재현 체크리스트

- [ ] `tft_model.pt` + `train_args.json` 버전 일치  
- [ ] `scalers.joblib`가 해당 `tft_preprocess` 실행과 쌍  
- [ ] `preprocess_meta.json`의 `encoder_length`/`prediction_length` = 학습 E/H  
- [ ] `train_station_ids.json` ⊆ eligibility `Y`  
- [ ] Git commit / `tft_experiment_plan.json` 실험 id 기록  
- [ ] test 성능은 **한 번만** 보고 (튜닝 후 재사용 금지)

---

## 11. 관련 파일

| 문서/코드 | 내용 |
|-----------|------|
| `docs/before_training.md` | 결측·CCF·split 정책 |
| `docs/tft_evaluation.md` | val/test 평가 |
| `experiments/tft/tft_experiment_plan.json` | 실험 계획 |
| `src/train_tft_darts.py` | 학습 |
| `src/eval_tft_darts.py` | 평가 |
| `src/tft_preprocess.py` | 전처리 |
