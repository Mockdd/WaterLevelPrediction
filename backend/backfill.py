"""
백필 스크립트 — DB에 비어있는 구간을 채움

용도:
- scheduler는 "최근 1시간"만 받음
- 며칠 안 돌리면 그 사이 데이터가 비게 됨
- 이 스크립트는 DB 마지막 시각 ~ 현재까지 다 채워줌

실행:
    cd C:\\Users\\안서영\\Desktop\\hangang_backend
    python backfill.py

옵션:
    python backfill.py --days 7        # 마지막 시각 무시하고 무조건 7일치
    python backfill.py --water-only    # 수위만
    python backfill.py --aws-only      # 강수만
"""
import argparse
import requests
import xml.etree.ElementTree as ET
import pandas as pd
import time
from datetime import datetime, timedelta
from sqlalchemy import text
from database import engine
import warnings
warnings.filterwarnings('ignore')


# 키 (scheduler.py와 동일)
HRFCO_KEY = "94DDBB52-2444-4E86-A7C7-16D20A22F6C3"
AWS_KEY = "ozyGSofcTqK8hkqH3A6ikg"


# ────────────────────────────────────────────────
# DB 마지막 시각 찾기
# ────────────────────────────────────────────────
def get_last_datetime(table, datetime_col='datetime'):
    """테이블에서 가장 최근 datetime 반환 (없으면 None)"""
    sql = f"SELECT MAX({datetime_col}) as last_dt FROM {table}"
    with engine.connect() as conn:
        result = conn.execute(text(sql)).fetchone()
    return result[0] if result and result[0] else None


# ────────────────────────────────────────────────
# 수위 백필
# ────────────────────────────────────────────────
def backfill_water_level(start_dt, end_dt):
    """
    start_dt ~ end_dt 구간의 수위 데이터를 받아 DB 적재
    한강홍수통제소 API는 한 번에 긴 구간 요청 가능
    """
    print(f"\n{'='*60}")
    print(f"수위 백필: {start_dt} ~ {end_dt}")
    print(f"{'='*60}")
    
    start_str = start_dt.strftime('%Y%m%d%H')
    end_str = end_dt.strftime('%Y%m%d%H')
    
    # 대상 관측소
    df_db = pd.read_sql("SELECT station_id FROM stations", engine)
    target_obscd = df_db['station_id'].astype(str).tolist()
    print(f"  대상 관측소: {len(target_obscd)}개")
    
    all_data = []
    fail_count = 0
    
    for i, obscd in enumerate(target_obscd):
        if (i + 1) % 50 == 0:
            print(f"  진행: {i+1}/{len(target_obscd)} ({len(all_data)}건 누적)")
        
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
        except Exception:
            fail_count += 1
        time.sleep(0.05)
    
    df = pd.DataFrame(all_data)
    print(f"  수집 완료: {len(df)}건 (실패 {fail_count}개)")
    
    if len(df) == 0:
        print(f"  ⚠️ 데이터 없음")
        return
    
    # UPSERT (한 번에)
    print(f"  DB 적재 중...")
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
    print(f"  ✅ 수위 {len(df)}건 적재")


# ────────────────────────────────────────────────
# 강수 백필
# ────────────────────────────────────────────────
def backfill_rainfall(start_dt, end_dt):
    """
    start_dt ~ end_dt 구간의 강수 데이터를 받아 DB 적재
    AWS API도 한 번에 긴 구간 요청 가능
    """
    print(f"\n{'='*60}")
    print(f"강수 백필: {start_dt} ~ {end_dt}")
    print(f"{'='*60}")
    
    tm1 = start_dt.strftime('%Y%m%d%H%M')
    tm2 = end_dt.strftime('%Y%m%d%H%M')
    
    df_db = pd.read_sql("SELECT stn_id FROM aws_stations", engine)
    target_stns = df_db['stn_id'].astype(str).tolist()
    print(f"  대상 AWS: {len(target_stns)}개")
    
    all_data = []
    fail_count = 0
    
    for i, stn in enumerate(target_stns):
        if (i + 1) % 50 == 0:
            print(f"  진행: {i+1}/{len(target_stns)} ({len(all_data)}건 누적)")
        
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
        except Exception:
            fail_count += 1
        time.sleep(0.05)
    
    df = pd.DataFrame(all_data)
    print(f"  수집 완료: {len(df)}건 (실패 {fail_count}개)")
    
    if len(df) == 0:
        print(f"  ⚠️ 데이터 없음")
        return
    
    print(f"  DB 적재 중...")
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
    print(f"  ✅ 강수 {len(df)}건 적재")


# ────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=None,
                        help='무조건 N일치 (지정 시 DB 마지막 시각 무시)')
    parser.add_argument('--water-only', action='store_true',
                        help='수위만 받음')
    parser.add_argument('--aws-only', action='store_true',
                        help='강수만 받음')
    args = parser.parse_args()
    # 항상 최근 2일치 강제로
    args.days = args.days or 2
    
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    
    # 시작 시각 결정
    if args.days:
        start_water = now - timedelta(days=args.days)
        start_aws = now - timedelta(days=args.days)
        print(f"📅 강제: 최근 {args.days}일치")
    else:
        # DB 마지막 시각 + 1시간부터 시작
        last_water = get_last_datetime('observations')
        last_aws = get_last_datetime('aws_observations')
        
        print(f"📅 DB 현황")
        print(f"  observations 마지막:     {last_water}")
        print(f"  aws_observations 마지막: {last_aws}")
        print(f"  현재 시각:               {now}")
        
        # 마지막 시각 + 1시간부터 (이미 있는 거 또 받지 않게)
        # None이면 (테이블 비어있으면) 7일 전부터
        start_water = (last_water + timedelta(hours=1)) if last_water else (now - timedelta(days=7))
        start_aws = (last_aws + timedelta(hours=1)) if last_aws else (now - timedelta(days=7))
        
        # 이미 최신이면 skip
        if start_water > now:
            print(f"  → 수위는 이미 최신, skip")
            start_water = None
        if start_aws > now:
            print(f"  → 강수는 이미 최신, skip")
            start_aws = None
    
    # 실행
    if not args.aws_only and start_water:
        backfill_water_level(start_water, now)
    
    if not args.water_only and start_aws:
        backfill_rainfall(start_aws, now)
    
    print(f"\n{'='*60}")
    print(f"✅ 백필 완료: {datetime.now()}")
    print(f"{'='*60}")
    
    # 결과 확인
    last_water_new = get_last_datetime('observations')
    last_aws_new = get_last_datetime('aws_observations')
    print(f"\n📊 적재 후 DB 현황")
    print(f"  observations 마지막:     {last_water_new}")
    print(f"  aws_observations 마지막: {last_aws_new}")


if __name__ == "__main__":
    main()
