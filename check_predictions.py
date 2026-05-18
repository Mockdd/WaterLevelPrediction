"""방금 넣은 predictions 값 확인"""
import pandas as pd
from database import engine

# 가장 최근 predicted_at 기준
df = pd.read_sql("""
    SELECT station_id, predicted_at, predictions
    FROM predictions
    WHERE predicted_at = (SELECT MAX(predicted_at) FROM predictions)
    LIMIT 10
""", engine)

print(f"방금 넣은 row: {len(df)}개\n")

# NaN 포함 여부 확인
nan_count = df['predictions'].astype(str).str.contains('NaN').sum()
print(f"NaN 포함 row: {nan_count}개 / {len(df)}개\n")

# 샘플 3개 출력
print("샘플 3개:")
for i in range(min(3, len(df))):
    print(f"\n[{df.iloc[i]['station_id']}] {df.iloc[i]['predicted_at']}")
    print(f"  {df.iloc[i]['predictions']}")
