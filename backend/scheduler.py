"""
1시간마다 자동 데이터 갱신 (B 백엔드)

기능:
- 수위 (한강홍수통제소 API)
- 강수 (기상청 AWS API)
- 예측 (A의 TFT 모델)

실행:
    cd C:\\Users\\안서영\\Desktop\\hangang_backend
    python scheduler.py

수동 호출:
    POST /admin/refresh (main.py)
"""
import schedule
import time
import requests
import xml.etree.ElementTree as ET
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import text
from database import engine
import warnings
warnings.filterwarnings('ignore')


# ────────────────────────────────────────────────────
# 키
# ────────────────────────────────────────────────────
HRFCO_KEY = "94DDBB52-2444-4E86-A7C7-16D20A22F6C3"
AWS_KEY = "ozyGSofcTqK8hkqH3A6ikg"

region_map = {'10': '한강', '11': '안성천', '12': '한강서해', '13': '한강동해'}


# ────────────────────────────────────────────────────
# 1. 수위 업데이트
# ────────────────────────────────────────────────────
def update_water_level():
    """최근 1시간 수위 데이터를 한강홍수통제소 API에서 받아 DB 적재"""
    print(f"\n[{datetime.now()}] 수위 업데이트 시작")
    
    # 최근 1시간
    end = datetime.now().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=1)
    start_str = start.strftime('%Y%m%d%H')
    end_str = end.strftime('%Y%m%d%H')
    
    # DB stations 가져오기 (대상 obscd 291개)
    df_db = pd.read_sql("SELECT station_id FROM stations", engine)
    target_obscd = df_db['station_id'].astype(str).tolist()
    
    all_data = []
    for obscd in target_obscd:
        url = (
            f"https://api.hrfco.go.kr/{HRFCO_KEY}/waterlevel/list/1H/"
            f"{obscd}/{start_str}/{end_str}.xml"
        )
        try:
            res = requests.get(url, timeout=30, verify=False)
            root = ET.fromstring(res.text)
            for item in root.findall('.//Waterlevel'):
                ymdhm = item.findtext('ymdhm')
                wl = item.findtext('wl')
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
    
    df = pd.DataFrame(all_data)
    if len(df) == 0:
        print(f"  ⚠️ 수위 데이터 없음")
        return
    
    # UPSERT (중복 방지)
    with engine.connect() as conn:
        for _, row in df.iterrows():
            conn.execute(text("""
                INSERT INTO observations (station_id, datetime, water_level)
                VALUES (:s, :d, :w)
                ON CONFLICT (station_id, datetime) DO UPDATE
                SET water_level = EXCLUDED.water_level
            """), {
                's': row['station_id'],
                'd': row['datetime'],
                'w': row['water_level']
            })
        conn.commit()
    
    print(f"  ✅ 수위 {len(df)}건 업데이트")


# ────────────────────────────────────────────────────
# 2. 강수 업데이트
# ────────────────────────────────────────────────────
def update_rainfall():
    """최근 1시간 강수 데이터를 기상청 AWS API에서 받아 DB 적재"""
    print(f"[{datetime.now()}] 강수 업데이트 시작")
    
    end = datetime.now().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=1)
    tm1 = start.strftime('%Y%m%d%H%M')
    tm2 = end.strftime('%Y%m%d%H%M')
    
    # DB aws_stations (277개)
    df_db = pd.read_sql("SELECT stn_id FROM aws_stations", engine)
    target_stns = df_db['stn_id'].astype(str).tolist()
    
    all_data = []
    for stn in target_stns:
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
                    rn = float(parts[6])  # RN_HR1
                    if rn < 0:
                        rn = 0.0
                    all_data.append({
                        'stn_id': stn_resp,
                        'datetime': datetime.strptime(tm[:10], '%Y%m%d%H'),
                        'rainfall': rn,
                    })
                except (ValueError, IndexError):
                    continue
        except:
            pass
        time.sleep(0.05)
    
    df = pd.DataFrame(all_data)
    if len(df) == 0:
        print(f"  ⚠️ 강수 데이터 없음")
        return
    
    # UPSERT
    with engine.connect() as conn:
        for _, row in df.iterrows():
            conn.execute(text("""
                INSERT INTO aws_observations (stn_id, datetime, rainfall)
                VALUES (:s, :d, :r)
                ON CONFLICT (stn_id, datetime) DO UPDATE
                SET rainfall = EXCLUDED.rainfall
            """), {
                's': row['stn_id'],
                'd': row['datetime'],
                'r': row['rainfall']
            })
        conn.commit()
    
    print(f"  ✅ 강수 {len(df)}건 업데이트")


# ────────────────────────────────────────────────────
# 3. 예측 실행 (A의 TFT)
# ────────────────────────────────────────────────────
def run_predictions():
    """A의 predict.py 호출 → predictions 테이블 적재"""
    print(f"[{datetime.now()}] 예측 실행 시작")
    
    try:
        # ⚠️ A가 predict.py 줄 때까지 비활성화
        # from predict import run_prediction
        # results_df = run_prediction()  # DataFrame: station_id, predicted_at, predictions, statuses
        # 
        # # DB 적재
        # results_df.to_sql('predictions', engine, if_exists='append', index=False)
        # print(f"  ✅ 예측 {len(results_df)}건")
        
        print(f"  ⏳ A predict.py 대기 중")
    except Exception as e:
        print(f"  ❌ 예측 오류: {e}")


# ────────────────────────────────────────────────────
# 4. 통합 실행 (1시간 1번 또는 수동)
# ────────────────────────────────────────────────────
def run_once():
    """모든 갱신 작업 1회 실행"""
    print(f"\n{'='*60}")
    print(f"[{datetime.now()}] 갱신 시작")
    print(f"{'='*60}")
    
    update_water_level()
    update_rainfall()
    run_predictions()
    
    print(f"\n[{datetime.now()}] 갱신 완료\n")


# ────────────────────────────────────────────────────
# 5. 메인 (스케줄러 작동)
# ────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"스케줄러 시작: {datetime.now()}")
    print("1시간마다 자동 실행, Ctrl+C로 종료\n")
    
    # 시작 시 1번 실행
    run_once()
    
    # 1시간마다 등록
    schedule.every(1).hours.do(run_once)
    
    # 무한 루프
    while True:
        schedule.run_pending()
        time.sleep(60)  # 1분마다 체크
