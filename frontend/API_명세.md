# 한강홍수통제소 수위 예측 API 명세 v1.1

> B 백엔드 (서영) → C 프론트엔드
> 작성일: 2026-05-16
> v1.1 변경점: `/stations/with-status`, `/alerts` 추가, 신호등 계산 로직 명시, 에러 응답 통일

## Base URL
```
http://localhost:8000
```
개발 중에는 로컬 호스트. 배포 시 변경 예정.

---

## 데이터 출처
- **수위**: 한강홍수통제소 API (1시간 단위)
- **강수**: 기상청 AWS API (1시간 단위)
- **예측**: TFT 모델 (1~6시간 후)

## 갱신 주기
- **자동**: 1시간마다 (scheduler.py)
- **수동**: `POST /admin/refresh` (발표 시연용, 1~3분 소요)

## 권역 (region) 값
다음 4개 중 하나:
- `한강`
- `안성천`
- `한강서해`
- `한강동해`

## 에러 응답 형식
모든 에러는 HTTP 상태 코드 + JSON으로 통일:
```json
{
  "detail": "에러 메시지"
}
```
- `404`: 존재하지 않는 관측소, 예측 데이터 없음
- `500`: 서버 내부 오류

---

## 엔드포인트 목록

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/stations` | 관측소 목록 (지도용) |
| GET | `/stations/with-status` ⭐ | 관측소 + 예측 + 신호등 통합 |
| GET | `/alerts` ⭐ | 위험 관측소 리스트 |
| GET | `/stations/{id_or_name}` | 관측소 상세 (ID/이름) |
| GET | `/stations/{id}/observations` | 과거 수위 (그래프용) |
| GET | `/stations/{id}/predictions` | 특정 관측소 예측 |
| GET | `/stations/{id}/upstream` | 상류 관측소 |
| POST | `/admin/refresh` | 수동 새로고침 |

---

## 1. 전체 관측소 목록

```
GET /stations
GET /stations?region=한강
```

**파라미터:**
- `region` (선택): `한강` / `안성천` / `한강서해` / `한강동해`

**응답:**
```json
{
  "count": 291,
  "stations": [
    {
      "station_id": "1018500",
      "name": "한강대교",
      "region": "한강",
      "lat": 37.51,
      "lng": 126.96,
      "alert_level": 6.5,
      "warning_level": 8.5
    }
  ]
}
```

**용도:** 지도에 핀 표시할 위치만 필요할 때. **신호등까지 필요하면 `/stations/with-status` 사용 권장.**

---

## 2. ⭐ 지도용 통합 API

```
GET /stations/with-status
GET /stations/with-status?region=한강
```

**용도:** 지도 첫 로딩 시 1번 호출로 끝나도록 만든 API. 관측소 정보 + 최신 예측 + 신호등을 한 번에 받음.

**응답:**
```json
{
  "count": 291,
  "stations": [
    {
      "station_id": "1018500",
      "name": "한강대교",
      "region": "한강",
      "lat": 37.51,
      "lng": 126.96,
      "alert_level": 6.5,
      "warning_level": 8.5,
      
      "predicted_at": "2026-05-16T14:00:00",
      "predictions": {
        "h1": {"predicted": 2.5, "lower": 2.2, "upper": 2.8},
        "h2": {"predicted": 2.7, "lower": 2.3, "upper": 3.0},
        "h3": {"predicted": 3.0, "lower": 2.6, "upper": 3.4},
        "h4": {"predicted": 3.2, "lower": 2.7, "upper": 3.7},
        "h5": {"predicted": 3.5, "lower": 2.9, "upper": 4.0},
        "h6": {"predicted": 3.7, "lower": 3.0, "upper": 4.3}
      },
      "statuses": {
        "h1": "green", "h2": "green", "h3": "green",
        "h4": "green", "h5": "green", "h6": "green"
      },
      "pin_status": "green"
    }
  ]
}
```

**필드 설명:**
- `pin_status`: 6개 horizon 중 가장 위험한 색 (지도 핀 색용)
- 예측이 아직 없는 관측소는 `predicted_at`, `predictions`, `statuses` 모두 `null`, `pin_status`는 `"gray"`

**용도:** 지도 첫 진입 시 1회 호출로 모든 핀 색 결정.

---

## 3. ⭐ 위험 관측소 리스트

```
GET /alerts
```

**용도:** "지금 위험한 관측소를 우선순위로 보여줘". `red` 먼저, `yellow` 다음 순.

**응답:**
```json
{
  "count": 5,
  "alerts": [
    {
      "station_id": "1018500",
      "name": "한강대교",
      "region": "한강",
      "lat": 37.51,
      "lng": 126.96,
      "alert_level": 6.5,
      "warning_level": 8.5,
      "predicted_at": "2026-05-16T14:00:00",
      "predictions": { /* h1~h6 */ },
      "statuses": { /* h1~h6 */ },
      "pin_status": "red"
    }
  ]
}
```

빈 경우 `{"count": 0, "alerts": []}`.

---

## 4. 관측소 상세

```
GET /stations/{station_id_or_name}
```

**파라미터:**
- `station_id_or_name`: 관측소 ID(숫자) 또는 이름 일부 (예: `1018500` 또는 `송정교`)

**응답 (성공):**
```json
{
  "station_id": "1018500",
  "name": "한강대교",
  "region": "한강",
  "lat": 37.51,
  "lng": 126.96,
  "alert_level": 6.5,
  "warning_level": 8.5
}
```

**응답 (실패, 404):**
```json
{
  "detail": "관측소 '...'를 찾을 수 없습니다."
}
```

---

## 5. 과거 수위 (그래프용)

```
GET /stations/{station_id}/observations?hours=24
```

**파라미터:**
- `hours` (선택, 기본 24, 범위 1~168): 최근 N시간

**응답:**
```json
{
  "station_id": "1018500",
  "count": 24,
  "observations": [
    {"datetime": "2026-05-15T14:00:00", "water_level": 2.5},
    {"datetime": "2026-05-15T15:00:00", "water_level": 2.6}
  ]
}
```

**정렬:** 오래된 → 최신 (그래프 그리기 용이).

---

## 6. 예측 결과 (특정 관측소)

```
GET /stations/{station_id}/predictions
```

**응답 (성공):**
```json
{
  "station_id": "1018500",
  "predicted_at": "2026-05-16T14:00:00",
  "predictions": {
    "h1": {"predicted": 2.5, "lower": 2.2, "upper": 2.8},
    "h2": {"predicted": 2.7, "lower": 2.3, "upper": 3.0},
    "h3": {"predicted": 3.0, "lower": 2.6, "upper": 3.4},
    "h4": {"predicted": 3.2, "lower": 2.7, "upper": 3.7},
    "h5": {"predicted": 3.5, "lower": 2.9, "upper": 4.0},
    "h6": {"predicted": 3.7, "lower": 3.0, "upper": 4.3}
  },
  "statuses": {
    "h1": "green",
    "h2": "green",
    "h3": "yellow",
    "h4": "yellow",
    "h5": "red",
    "h6": "red"
  }
}
```

**필드 설명:**
- `h1` ~ `h6`: 1시간 후 ~ 6시간 후 예측
- `predicted`: 예측 수위 (median, 50% quantile)
- `lower`, `upper`: 신뢰구간 (10%, 90% quantile)
  - 그래프에 음영 영역으로 표시하면 불확실성 시각화 가능
  - 예: 선 그래프(predicted) + 그 위아래 음영(lower~upper)
- `statuses`: 각 horizon의 신호등 색 (계산식은 아래 참조)

**응답 (예측 없음, 404):**
```json
{
  "detail": "관측소 '...'의 예측 데이터가 없습니다."
}
```

---

## 신호등 계산 기준 (B에서 계산)

각 horizon의 `predicted` 값을 해당 관측소의 임계수위와 비교:

| 색상 | 조건 |
|------|------|
| `green` | `predicted < alert_level` |
| `yellow` | `alert_level ≤ predicted < warning_level` |
| `red` | `predicted ≥ warning_level` |
| `gray` | 비교 불가 (임계수위 또는 예측값 없음) |

**`pin_status`** (지도 핀 색): h1~h6의 statuses 중 가장 위험한 색을 사용.
우선순위: `red` > `yellow` > `green` > `gray`.

---

## 7. 상류 관측소

```
GET /stations/{station_id}/upstream
```

해당 관측소의 상류 + 시차 (propagation 분석 결과).

**응답:**
```json
{
  "station_id": "1018500",
  "count": 2,
  "upstream": [
    {
      "upstream_id": "1015650",
      "upstream_name": "팔당대교",
      "mean_lag_h": 2.5,
      "n_events": 12,
      "grade": "A"
    },
    {
      "upstream_id": "1014640",
      "upstream_name": "광나루",
      "mean_lag_h": 4.0,
      "n_events": 10,
      "grade": "B"
    }
  ]
}
```

**용도:** "상류 N시간 전에 ↑ → 여기도 ↑" 시각화.

데이터 없는 경우 `{"station_id": "...", "count": 0, "upstream": []}`.

---

## 8. 수동 새로고침 (발표용)

```
POST /admin/refresh
```

수위 + 강수 + 예측 즉시 갱신. 발표 시연용 버튼.

**⚠️ 1~3분 소요됨** (291개 수위 + 277개 강수 API 호출). 호출 시 fetch 타임아웃을 충분히 길게 설정 권장 (180초 이상).

**응답 (성공):**
```json
{
  "status": "ok",
  "message": "Refresh completed",
  "updated_at": "2026-05-16T14:30:00"
}
```

**응답 (실패, 500):**
```json
{
  "detail": "..."
}
```

---

## 사용 예 (JavaScript)

```javascript
// 1. 지도 첫 진입 - 모든 핀 + 신호등 (한 번 호출)
const res = await fetch('http://localhost:8000/stations/with-status');
const data = await res.json();
data.stations.forEach(s => {
  // map.addPin(s.lat, s.lng, s.name, s.pin_status);
});

// 2. 핀 클릭 시 - 상세
async function onPinClick(stationId) {
  // 상세 정보
  const detail = await fetch(`http://localhost:8000/stations/${stationId}`).then(r => r.json());
  
  // 예측
  const pred = await fetch(`http://localhost:8000/stations/${stationId}/predictions`).then(r => r.json());
  
  // 과거 24시간 그래프
  const obs = await fetch(`http://localhost:8000/stations/${stationId}/observations?hours=24`).then(r => r.json());
  
  // 상류 정보
  const upstream = await fetch(`http://localhost:8000/stations/${stationId}/upstream`).then(r => r.json());
}

// 3. 알림 패널
const alerts = await fetch('http://localhost:8000/alerts').then(r => r.json());
// alerts.alerts: red/yellow 관측소 순서대로

// 4. 수동 새로고침 (1~3분 소요)
await fetch('http://localhost:8000/admin/refresh', {
  method: 'POST',
  signal: AbortSignal.timeout(180000)  // 3분 타임아웃
});
```

---

## DB 스키마 (참고)

```
stations         관측소 메타 (291개)
  station_id, name, region, lat, lng, alert_level, warning_level

observations     수위 시계열
  id, station_id, datetime, water_level
  UNIQUE (station_id, datetime)

predictions      TFT 예측 결과
  id, station_id, predicted_at, predictions (JSONB)
  predictions 구조: {"h1": {"predicted":..,"lower":..,"upper":..}, ..., "h6": {...}}

propagation      상하류 관계
  id, obscd_a, obscd_b, mean_lag_h, n_events, grade

aws_stations     강수 관측소 메타 (277개)
  stn_id, stn_ko, lat, lon, addr

aws_observations 강수 시계열
  id, stn_id, datetime, rainfall
  UNIQUE (stn_id, datetime)
```

---

## 시스템 구성

```
한강홍수통제소 API → backfill/scheduler → DB(observations)
   기상청 AWS API  ↗                    DB(aws_observations)
                                              ↓
TFT 모델 (A 파트) → DB(predictions)
                                              ↓
                                    main.py (FastAPI)
                                              ↓
                                          C 프론트
```

---

## 문의

B 백엔드 - 서영
