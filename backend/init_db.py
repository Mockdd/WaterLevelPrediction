import pandas as pd
from sqlalchemy import text
from database import engine

base = r'C:\Users\안서영\Desktop\hangang\HanRiver_FloodControl\final'

# ── 테이블 생성 ──────────────────────────────────────
with engine.connect() as conn:
    conn.execute(text("DROP TABLE IF EXISTS aws_observations CASCADE;"))
    conn.execute(text("DROP TABLE IF EXISTS aws_stations CASCADE;"))
    conn.execute(text("DROP TABLE IF EXISTS propagation CASCADE;"))
    conn.execute(text("DROP TABLE IF EXISTS predictions CASCADE;"))
    conn.execute(text("DROP TABLE IF EXISTS observations CASCADE;"))
    conn.execute(text("DROP TABLE IF EXISTS stations CASCADE;"))
    
    conn.execute(text("""
        CREATE TABLE stations (
            station_id VARCHAR PRIMARY KEY,
            name VARCHAR, region VARCHAR,
            lat FLOAT, lng FLOAT,
            alert_level FLOAT, warning_level FLOAT
        );
    """))
    conn.execute(text("""
        CREATE TABLE observations (
            id SERIAL PRIMARY KEY,
            station_id VARCHAR,
            datetime TIMESTAMP, water_level FLOAT
        );
    """))
    conn.execute(text("""
        CREATE TABLE predictions (
            id SERIAL PRIMARY KEY,
            station_id VARCHAR,
            predicted_at TIMESTAMP,
            predictions JSONB, statuses JSONB
        );
    """))
    conn.execute(text("""
        CREATE TABLE propagation (
            id SERIAL PRIMARY KEY,
            obscd_a VARCHAR, obscd_b VARCHAR,
            mean_lag_h FLOAT, n_events INT, grade VARCHAR
        );
    """))
    conn.execute(text("""
        CREATE TABLE aws_stations (
            stn_id VARCHAR PRIMARY KEY,
            stn_ko VARCHAR,
            lat FLOAT, lon FLOAT, addr VARCHAR
        );
    """))
    conn.execute(text("""
        CREATE TABLE aws_observations (
            id SERIAL PRIMARY KEY,
            stn_id VARCHAR,
            datetime TIMESTAMP, rainfall FLOAT
        );
    """))
    conn.commit()
    print("✅ 테이블 6개 생성")

# ── stations 적재 ─────────────────────────────────
df = pd.read_csv(f'{base}\\wl_stations.csv', encoding='utf-8-sig')
print(f"wl_stations: {len(df)}개")

df_st = df.rename(columns={
    'obscd': 'station_id',
    'obsnm': 'name',
    'lon': 'lng',
    'attwl': 'alert_level',
    'wrnwl': 'warning_level',
})[['station_id', 'name', 'region', 'lat', 'lng', 'alert_level', 'warning_level']]

df_st['station_id'] = df_st['station_id'].astype(str)
df_st.to_sql('stations', engine, if_exists='append', index=False)
print(f"✅ stations 적재: {len(df_st)}개")

# 권역별
print(f"\n권역별:")
print(df_st.groupby('region').size().to_string())

# ── aws_stations 적재 ─────────────────────────────
df_aws = pd.read_csv(f'{base}\\aws_stations.csv', encoding='utf-8-sig')
df_aws_final = df_aws.rename(columns={
    'law_addr': 'addr',
})[['stn_id', 'stn_ko', 'lat', 'lon', 'addr']].copy()

df_aws_final['stn_id'] = df_aws_final['stn_id'].astype(str)
df_aws_final.to_sql('aws_stations', engine, if_exists='append', index=False)
print(f"\n✅ aws_stations 적재: {len(df_aws_final)}개")

print("\n✅ Day 1 DB 완료!")
