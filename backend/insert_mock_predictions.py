"""
mock predictions 3건 INSERT
- green:  alert_level의 50% 수준 예측
- yellow: alert_level과 warning_level 사이 예측
- red:    warning_level 이상 예측

각 관측소는 stations 테이블에서 임계수위가 가장 잘 정의된 것 중에서 자동 선택.

실행:
    python insert_mock_predictions.py

다시 돌리고 싶으면 (덮어쓰기):
    먼저 pgAdmin에서 DELETE FROM predictions; 후 실행
    또는 그냥 다시 실행 (predicted_at이 다르면 새 행으로 추가됨)
"""
import json
from datetime import datetime
from sqlalchemy import text
from database import engine


def make_predictions_json(base_wl, scenario):
    """
    h1~h6 예측 JSON 생성
    base_wl: 기준 수위
    scenario: "green" / "yellow" / "red"
    """
    if scenario == "green":
        # 시간이 갈수록 조금씩 오르지만 alert 미만 유지
        multipliers = [0.85, 0.87, 0.90, 0.92, 0.94, 0.95]
    elif scenario == "yellow":
        # alert 넘어서 warning 직전까지
        multipliers = [0.95, 1.00, 1.05, 1.10, 1.13, 1.15]
    else:  # red
        # warning 이상 (위험 도달)
        multipliers = [1.05, 1.15, 1.25, 1.30, 1.35, 1.40]
    
    result = {}
    for i, mult in enumerate(multipliers, start=1):
        predicted = round(base_wl * mult, 2)
        result[f"h{i}"] = {
            "predicted": predicted,
            "lower": round(predicted * 0.92, 2),
            "upper": round(predicted * 1.08, 2),
        }
    return result


# ────────────────────────────────────────────────
# 1. 적합한 관측소 3개 고르기
# ────────────────────────────────────────────────
# 조건: alert_level과 warning_level이 모두 NOT NULL이고
#       alert < warning (정상적인 임계수위)
print("=" * 60)
print("mock 대상 관측소 선정")
print("=" * 60)

import pandas as pd
df = pd.read_sql(
    """
    SELECT station_id, name, region, alert_level, warning_level
    FROM stations
    WHERE alert_level IS NOT NULL
      AND warning_level IS NOT NULL
      AND warning_level > alert_level
    ORDER BY station_id
    LIMIT 3
    """,
    engine
)

if len(df) < 3:
    print("⚠️ 임계수위 정상적인 관측소가 3개 미만입니다.")
    print(df)
    raise SystemExit()

print(df.to_string(index=False))


# ────────────────────────────────────────────────
# 2. 시나리오 매핑
# ────────────────────────────────────────────────
scenarios = ["green", "yellow", "red"]
now = datetime.now().replace(minute=0, second=0, microsecond=0)

mocks = []
for (_, row), scenario in zip(df.iterrows(), scenarios):
    alert = row['alert_level']
    warning = row['warning_level']
    
    # 기준 수위 = scenario에 맞게 base 선정
    if scenario == "green":
        base_wl = alert  # 곱하면 alert 미만
    elif scenario == "yellow":
        base_wl = alert  # 곱하면 alert ~ warning 사이
    else:  # red
        base_wl = warning  # 곱하면 warning 이상
    
    predictions = make_predictions_json(base_wl, scenario)
    
    mocks.append({
        'station_id': row['station_id'],
        'name': row['name'],
        'scenario': scenario,
        'alert_level': alert,
        'warning_level': warning,
        'predictions': predictions
    })


# ────────────────────────────────────────────────
# 3. INSERT
# ────────────────────────────────────────────────
print()
print("=" * 60)
print(f"mock INSERT (predicted_at = {now})")
print("=" * 60)

with engine.begin() as conn:
    for m in mocks:
        conn.execute(
            text("""
                INSERT INTO predictions (station_id, predicted_at, predictions)
                VALUES (:sid, :pa, CAST(:p AS JSONB))
            """),
            {
                'sid': m['station_id'],
                'pa': now,
                'p': json.dumps(m['predictions'])
            }
        )
        # 예측값 미리보기
        h6 = m['predictions']['h6']['predicted']
        print(f"  ✅ {m['station_id']} ({m['name']}) — {m['scenario']}")
        print(f"     alert={m['alert_level']}, warning={m['warning_level']}, h6_pred={h6}")


# ────────────────────────────────────────────────
# 4. 확인용 — 신호등 미리 계산
# ────────────────────────────────────────────────
print()
print("=" * 60)
print("예상 응답 (API 호출 시)")
print("=" * 60)

def compute_status(predicted_wl, alert_level, warning_level):
    if predicted_wl is None or alert_level is None or warning_level is None:
        return "gray"
    if predicted_wl >= warning_level:
        return "red"
    elif predicted_wl >= alert_level:
        return "yellow"
    else:
        return "green"

for m in mocks:
    statuses = {}
    for h, v in m['predictions'].items():
        statuses[h] = compute_status(v['predicted'], m['alert_level'], m['warning_level'])
    pin = "red" if "red" in statuses.values() else ("yellow" if "yellow" in statuses.values() else "green")
    print(f"\n  /stations/{m['station_id']}/predictions")
    print(f"    name: {m['name']}")
    print(f"    statuses: {statuses}")
    print(f"    pin_status: {pin}  (기대: {m['scenario']})")


# ────────────────────────────────────────────────
# 5. 호출해볼 URL
# ────────────────────────────────────────────────
print()
print("=" * 60)
print("✅ 완료. 테스트 URL")
print("=" * 60)
print()
print("uvicorn main:app --reload --port 8000 띄운 다음:")
print()
for m in mocks:
    print(f"  http://localhost:8000/stations/{m['station_id']}/predictions")
print()
print("  http://localhost:8000/stations/with-status")
print("  http://localhost:8000/alerts")
print()
