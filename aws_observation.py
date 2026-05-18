import requests
import pandas as pd
import time
from datetime import datetime, timedelta
from sqlalchemy import text
from database import engine

AWS_KEY = "ozyGSofcTqK8hkqH3A6ikg"

# DB aws_stations 가져오기 (이미 277개로 축소돼있음)
df_db = pd.read_sql("SELECT stn_id FROM aws_stations", engine)
target_stns = df_db['stn_id'].astype(str).tolist()
print(f"AWS 대상: {len(target_stns)}개\n")

# 기간 (최근 1주)
end = datetime.now().replace(minute=0, second=0, microsecond=0)
start = end - timedelta(days=7)
tm1 = start.strftime('%Y%m%d%H%M')
tm2 = end.strftime('%Y%m%d%H%M')

print(f"기간: {tm1} ~ {tm2}\n")
print("수집 시작...")

all_data = []
failed = []

for i, stn in enumerate(target_stns):
    url = (
        f"https://apihub.kma.go.kr/api/typ01/url/awsh.php"
        f"?tm1={tm1}&tm2={tm2}&stn={stn}&disp=1&help=0"
        f"&authKey={AWS_KEY}"
    )
    
    try:
        res = requests.get(url, timeout=30)
        for line in res.text.split('\n'):
            if not line or line.startswith('#') or '7777END' in line.upper():
                continue
            parts = line.split()
            if len(parts) < 7:
                continue
            try:
                tm = parts[0]
                stn_resp = parts[1]
                rn = float(parts[6])  # RN_HR1 (1시간 강수)
                if rn < 0:
                    rn = 0.0
                all_data.append({
                    'stn_id': stn_resp,
                    'datetime': datetime.strptime(tm[:10], '%Y%m%d%H'),
                    'rainfall': rn,
                })
            except (ValueError, IndexError):
                continue
    except Exception as e:
        failed.append(stn)
    
    if (i+1) % 30 == 0 or i == len(target_stns)-1:
        print(f"  [{i+1:3d}/{len(target_stns)}] {len(all_data):,}건")
    
    time.sleep(0.1)

df = pd.DataFrame(all_data)
print(f"\n수집: {len(df):,}건, {df['stn_id'].nunique()}개 AWS")
if failed:
    print(f"실패: {len(failed)}개")

# DB 적재
with engine.connect() as conn:
    conn.execute(text("TRUNCATE TABLE aws_observations RESTART IDENTITY;"))
    conn.commit()

df.to_sql('aws_observations', engine, if_exists='append', index=False, chunksize=10000)
print(f"\n✅ aws_observations 적재: {len(df):,}건")

if len(df) > 0:
    print(f"기간: {df['datetime'].min()} ~ {df['datetime'].max()}")