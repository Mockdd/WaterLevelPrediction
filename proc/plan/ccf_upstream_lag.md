# 계획(최종): 상류 수위 CCF 기반 lag 산출

| 항목 | 내용 |
|------|------|
| **상태** | 검토 반영 완료 — 구현·실행 단계로 진행 가능 |
| **선행** | S3 수위 1H 적재 `2023-03-01` ~ `2025-10-31` ([`docs/before_training.md` §1.1](../../docs/before_training.md)) |
| **최종 산출** | 버전된 lag CSV + **manifest** ([§6 결정서](#6-결정서-q1q6-승인-반영) 참고) |

---

## 0. CCF와 “다른 단계”를 다 고려해야 하나?

**전 단계(1~15)를 CCF 직전에 전부 구현할 필요는 없다.** 다만 아래는 **입력 일치·누수 방지** 때문에 CCF 설계에 **반드시** 묶인다.

| 단계·주제 | CCF와의 관계 |
|-----------|----------------|
| **4~5** 스켈레톤·S3 merge | CCF 입력 시계열의 **시간축·관측소 ID**의 근간. |
| **11** 결측 처리 | 본 계획 **Q1**: 일부 쌍에만 보간이 들어가므로, **11번과 동일 함수·파라미터**를 재사용할지 계약 필요. |
| **9** 이상치 | **Q2=A**: CCF 전에는 **미적용** — 다만 §4.2에서 보완. |
| **13** split | **train만** 적합 구간(`fit_*`). |
| **8** `shift` | lag CSV·`reliable`·`lag0` 정책을 그대로 소비. |
| **14** GroupNormalizer | CCF와 무관하지만 **동일 train 달력**을 쓴다는 점에서 문서만 교차 확인. |

나머지(강수, static, `wl_diff`, TFT 학습)는 **lag CSV 산출 직후에는 필수 아님**.

---

## 1. 목표·범위

### 1.1 목표

- `metadata_outputs/upstream_mapping_must.csv`의 **하류 `station_id` × 상류 슬롯(1,2)`** 마다 1H **정수 lag**를 산출한다.
- **val·test는 적합에 사용하지 않는다.**

### 1.2 범위

- 포함: train 구간 시계열 정렬 → (Q1 분기) 전처리 → CCF → 게이트 → CSV + manifest + 모니터링 요약.
- 제외: TFT 학습 실행, 강수 merge(단, 11번과 **동일 보간 함수**는 코드 import/공유 전제).

---

## 2. 가설·접근(불변)

- H1: 관측소별 **단일 lag** + 고정 `shift`로 TFT 학습 가능.
- H2: **train-only** 적합으로 lag 선택 누수 방지.
- H3: 저지지·짧은 겹침은 **명시적 fallback**이 임의 피크보다 낫다.

**CCF 수식:** [`src/run_dtw.py`](../../src/run_dtw.py) `crosscorr_lag_steps_dtw_check`(상류=ref, 하류=tgt, demean, full correlate), 탐색 `L ∈ [0, L_max]`.

---

## 3. 고정 상수(train·TFT·CCF 게이트)

| 변수 | 값 |
|------|-----|
| 전체 달력 | `2023-03-01` ~ `2025-10-31` |
| train / val / test | [`docs/before_training.md` §1.3](../../docs/before_training.md) 표와 동일 |
| CCF `fit_start` / `fit_end` | train과 동일 |
| `freq` | `1H` |
| `L_max` | 120 |
| `n_effective` 하한 | 336 (양쪽 **동시 유효** 시점만 카운트, **Q3=A**) |
| TFT `E` / `H` | 168 / 6 (8번·윈도 검증용) |

---

## 4. 문서 이력: 생성 → 수정 2회

### 4.1 초안

- 목표, Phase A~D, D0–D9 체크리스트 초안.

### 4.2 수정 1회 — 승인 결정(Q1~Q6) 반영 + 1차 보완

| 결정 | 내용 | 1차 보완(문제 → 대응) |
|------|------|------------------------|
| **Q1** | **train 창 내** 하류·상류 **동시 관측 가능 시각** 기준 격자에서, 결측 비율 \(=1 - n_{\text{both}} / N_{\text{skeleton}}}\) **< 0.30**이면 **11번과 동일 규칙으로만** 양 시리즈 보간 후 CCF; **≥ 0.30**이면 **보간 없이** 원시로 CCF(마스크는 여전히 동시 유효만). | **불연속 임계:** 0.299 vs 0.301 흔들림 → **정의를 “< 0.30” 엄격 부등호**로 고정. **파이프 불일치:** 8번 `shift`가 다른 브랜치를 쓰면 무의미 → lag CSV에 **`ccf_input_branch`** (`interpolated` \| `raw`) 기록, 8번은 **동일 브랜치**로 수위를 만든 뒤 shift. |
| **Q2** | **A** — CCF 전 9번 **미적용**. | **스파이크가 피크를 흔드는 위험** → Phase C에 **원시 CCF vs 9번 적용 후 CCF** 진단용 **비교 스크립트(옵션)** 를 두고, 이탈 큰 쌍만 리스트업(본 lag는 여전히 Q2=A 단일 경로). |
| **Q3** | **A** — `n_effective` = 동시 유효 시점 수. | 이미 Q1·게이트와 정합. |
| **Q4** | **A** — `L_max=120`, `n_effective≥336`. | **fallback 다발** 시에만 §3 수치 완화를 **재검토 의제**로 `proc/plan` 부속 메모에 링크(본 문서 §8). |
| **Q5** | **B** — `lag0==True`도 **행 유지**: `lag_steps=0`, `reliable=false`, `fallback_reason` (예: `lag0_true`). | **소비 코드 분기:** 8번은 `reliable==False` 또는 `lag0` 매핑 시 **값 0 마스킹**([`upstream_mapping_must.md`](../../docs/metadata_outputs/upstream_mapping_must.md))을 **강제**하는 단위 테스트 추가. |
| **Q6** | **B** — 파일명에 버전 포함. | **경로 고정 문제** → `metadata_outputs/upstream_lag_manifest.json`에 **`active_csv`**, `created_at`, `git_commit`(가능 시), `fit_start`, `fit_end` 기록; 8번·노트북은 **manifest만 읽기**. |

### 4.3 수정 2회 — 1차 보완의 구멍 메우기

| 이슈 | 추가 보완 |
|------|-----------|
| Q1 보간이 **장기 구간**을 메우면 신뢰 붕괴 | [`docs/waterlevel_missing_handling.md`](../../docs/waterlevel_missing_handling.md)의 **L2 상한(최대 연속 보간 길이 등)**을 CCF 보간에도 **그대로 적용**. 상한 초과 시 해당 구간은 보간하지 않고 NaN 유지 → 그 결과 결측률이 30% 넘으면 **raw 분기**로 자동 전환하는 **2단 규칙**을 구현 메모에 명시. |
| manifest 유실 | `upstream_lag_manifest.json`을 **git에 포함**(작은 JSON); lag CSV 본문은 용량 때문에 선택적으로만 커밋. |
| Q5 행 폭증·중복 실행 | 동일 `git_commit`+`fit_*`로 재실행 시 **idempotent**: manifest가 같으면 스킵 또는 `--force` 필요. |
| Q2 진단 스크립트 방치 | Phase C 완료 조건에 **“진단 리포트 1회 실행(로그만)”**을 넣어, 장기적으로 스파이크 민감도를 추적. |

---

## 5. 최종 작업 분해(Phase A~E)

### Phase A — 입력 계약

| ID | 작업 | 비고 |
|----|------|------|
| A1 | `station_id` ↔ `obscd` 정합 표 고정 | 단위 테스트 3건. |
| A2 | 전 기간 1H 스켈레톤 생성 | merge 규약과 동일 TZ. |
| A3 | S3 → 스켈레톤 left join | §1.1 키. |
| A4 | **Q1 구현:** 쌍·train 창에서 `missing_rate_joint` 계산 → 분기 → `ccf_input_branch` 결정 | 11번 보간 함수 공유. |
| A5 | **Q2:** 9번 **호출 안 함** | 진단은 Phase C 옵션. |

**완료 기준:** 리뷰어가 “어떤 열로 CCF하는지” 한 단락으로 설명 + `ccf_input_branch` 정의서 5줄.

---

### Phase B — CCF 구현

| ID | 작업 |
|----|------|
| B1 | `src/compute_upstream_lag_ccf.py`(가칭) CLI: `--fit-start/end`, `--bucket`, `--mapping`, `--manifest-out`, `--force` |
| B2 | 매핑 행 × 슬롯 순회; **D7** `NaN` upstream → 행 생략(또는 정책대로); **`lag0==True` → Q5 B 행** |
| B3 | CCF 입력: A4 분기 결과 테이블; **동시 유효 마스크**로 `n_effective` 산출 |
| B4 | `L∈[0,120]` 슬라이스 argmax; 2위 피크 규칙은 후속(선택) |
| B5 | 게이트 실패 시 **D6**: `lag_steps=0`, `reliable=false`, `fallback_reason` |
| B6 | **버전 CSV** 쓰기 + **manifest** 원자적 갱신(임시 파일 rename 패턴) |

---

### Phase C — 검증·QA

| ID | 작업 | 완료 조건 |
|----|------|-----------|
| C1 | 부호 스모크(N6) | 5쌍 플롯 |
| C2 | `stream_code`별 lag 분포 | 비정상 비율 임계 초과 시 알람 |
| C3 | 누수 자동 검사 | 적합 `datetime` ⊆ train |
| C4 | **Q2 진단(옵션)** 실행 로그 1건 | 원시 vs 9번 적용 CCF lag 차 상위 20쌍 |
| C5 | `ccf_input_branch` 비율·fallback_reason 집계 | `metadata_outputs/ccf_lag_run_summary.json` (소형) |

---

### Phase D — 문서·연동

- `docs/before_training.md` §1.3·§3·§4와 본 문서 **수치·Q1·Q6** 동기화.
- 8번 빌더: **manifest → active_csv** 읽기 + `ccf_input_branch`에 따른 수위 파이프.

### Phase E — 운영(선택)

- 재적재 후 lag 재계산 시 manifest만 바꾸면 되도록 **CI 스모크**: manifest 존재 + CSV 존재.

---

## 6. 결정서 (Q1~Q6 승인 반영)

| ID | 승인 내용 |
|----|-----------|
| **Q1** | train 창·동시 유효 격자 기준 **결측률 < 30%** → 11번과 **동일 보간(상한 포함)** 후 CCF; **≥ 30%** → **미보간** 원시로 CCF. `ccf_input_branch` 필수 기록. |
| **Q2** | **A** — CCF 전 9번 미적용. 진단은 Phase C 옵션. |
| **Q3** | **A** — `n_effective` = 양쪽 동시 유효 시점 수. |
| **Q4** | **A** — `L_max=120`, `n_effective≥336`. |
| **Q5** | **B** — `lag0==True` 포함 **행 유지** + `reliable=false` + `fallback_reason`. 8번에서 마스킹 계약 준수. |
| **Q6** | **B** — 예: `metadata_outputs/upstream_lag_xccf_vYYYYMMDD_HHMM.csv` + **`metadata_outputs/upstream_lag_manifest.json`** (`active_csv` 등). |

---

## 7. 산출물 스키마

### 7.1 lag CSV (버전 파일명)

기존 스키마에 다음 **추가**:

| 컬럼 | 설명 |
|------|------|
| `ccf_input_branch` | `interpolated` \| `raw` |
| `missing_rate_joint` | train 창·동시 격자 기준 결측 비율 [0,1] |
| `max_interp_run_applied` | Q1 보간 시 실제 적용된 최대 연속 보간 길이(상한 클립 결과) |

### 7.2 manifest JSON (고정 경로)

```json
{
  "active_csv": "metadata_outputs/upstream_lag_xccf_v20260514_1200.csv",
  "fit_start": "2023-03-01",
  "fit_end": "2024-08-31",
  "created_at": "ISO-8601",
  "git_commit": "optional"
}
```

---

## 8. 재검토 트리거(수치·정책)

다음 중 하나라도 만족하면 **Q4·Q1 임계** 재검토 회의를 연다.

- `reliable=false` 비율 > **40%** (경험적 임계, 실행 후 조정).
- `ccf_input_branch=raw` 비율 > **70%** (보간 혜택이 거의 없음).
- C3 누수 검사 실패.

---

## 9. 성공 기준(완료 정의)

1. manifest + 버전 CSV 존재, `active_csv`가 train 적합과 일치.  
2. C3 통과.  
3. D7·Q5와 상충하는 행 0건(단위 테스트).  
4. C1 스모크 산출물 1건 이상.  
5. C5 `ccf_lag_run_summary.json` 생성.

---

## 10. 참고 링크

- [`docs/before_training.md`](../../docs/before_training.md)  
- [`docs/metadata_outputs/upstream_mapping_must.md`](../../docs/metadata_outputs/upstream_mapping_must.md)  
- [`docs/waterlevel_missing_handling.md`](../../docs/waterlevel_missing_handling.md)  
- [`src/run_dtw.py`](../../src/run_dtw.py)
