import requests
import xml.etree.ElementTree as ET
import pandas as pd
import time
from datetime import datetime, timedelta
from sqlalchemy import text
from database import engine
import warnings
warnings.filterwarnings('ignore')

API_KEY = "94DDBB52-2444-4E86-A7C7-16D20A22F6C3"

region_map = {
    '10': '한강',
    '11': '안성천',
    '12': '한강서해',
    '13': '한강동해'
}

# 관측소 코드 추출
url = f"https://api.hrfco.go.kr/{API_KEY}/waterlevel/list/1H.xml"
response = requests.get(url, timeout=30, verify=False)
root = ET.fromstring(response.text)
obscd_list = list(set([item.findtext('wlobscd') for item in root.findall('.//Waterlevel')]))
hrfco_obscd = [code for code in obscd_list if code and code[:2] in region_map]
print(f"4권역 obscd: {len(hrfco_obscd)}개")

# DB stations만
df_db = pd.read_sql("SELECT station_id FROM stations", engine)
db_set = set(df_db['station_id'].astype(str))
target_obscd = [c for c in hrfco_obscd if c in db_set]
print(f"DB 매칭: {len(target_obscd)}개\n")

# 최근 1주
end = datetime.now().replace(minute=0, second=0, microsecond=0)
start = end - timedelta(days=7)
start_str = start.strftime('%Y%m%d%H')
end_str = end.strftime('%Y%m%d%H')

print(f"기간: {start_str} ~ {end_str}\n")
print(f"수집 시작...")

all_data = []
for i, obscd in enumerate(target_obscd):
    url = f"https://api.hrfco.go.kr/{API_KEY}/waterlevel/list/1H/{obscd}/{start_str}/{end_str}.xml"
    
    try:
        res = requests.get(url, timeout=30, verify=False)
        root = ET.fromstring(res.text)
        for item in root.findall('.//Waterlevel'):
            wl = item.findtext('wl')
            ymdhm = item.findtext('ymdhm')
            if wl and wl != '-':
                try:
                    all_data.append({
                        'station_id': obscd,
                        'datetime': datetime.strptime(ymdhm[:10], '%Y%m%d%H'),
                        'water_level': float(wl),
                    })
                except:
                    continue
    except:
        pass
    
    time.sleep(0.05)
    if (i+1) % 50 == 0:
        print(f"  진행중... {i+1}/{len(target_obscd)}")

df = pd.DataFrame(all_data)
print(f"\n수집 완료: {len(df):,}건, {df['station_id'].nunique()}개 관측소")

base = r'C:\Users\안서영\Desktop\hangang\HanRiver_FloodControl\final'
# csv 저장
save_path = f'{base}\\wl_observations.csv'
df.to_csv(save_path, index=False, encoding='utf-8-sig')

# DB 적재
with engine.connect() as conn:
    conn.execute(text("TRUNCATE TABLE observations RESTART IDENTITY;"))
    conn.commit()

df.to_sql('observations', engine, if_exists='append', index=False, chunksize=10000)
print(f"\n✅ observations 적재: {len(df):,}건")

if len(df) > 0:
    print(f"기간: {df['datetime'].min()} ~ {df['datetime'].max()}")
