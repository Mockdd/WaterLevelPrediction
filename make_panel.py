"""
make_panel.py v5 - 단계별 로그 추가 버전
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from sqlalchemy import create_engine
from sklearn.preprocessing import StandardScaler


STATIC_DIR = Path("./model")
ENCODER_LENGTH = 48
SCALE_COLS = ['wl', 'wl_diff', 'rn', 'upstream_wl_1', 'upstream_wl_2']


def log_step(step, df, cols=None):
    """단계별 데이터 상태 출력"""
    print(f"\n{'='*60}")
    print(f"[STEP] {step}")
    print(f"{'='*60}")
    print(f"  row: {len(df):,}")
    print(f"  station 수: {df['station_id'].nunique() if 'station_id' in df.columns else 'N/A'}")
    if cols:
        for c in cols:
            if c in df.columns:
                nan_cnt = df[c].isna().sum()
                if df[c].dtype in ['float64','float32','int64']:
                    print(f"  {c}: NaN={nan_cnt}, min={df[c].min():.3f}, max={df[c].max():.3f}, mean={df[c].mean():.3f}")
                else:
                    print(f"  {c}: NaN={nan_cnt}, dtype={df[c].dtype}")


def load_static_assets():
    print("\n[로드] 정적 자산 (scalers, aws_mapping, propagation)")
    scalers_obj = joblib.load(STATIC_DIR / "scalers.joblib")
    scalers = scalers_obj.get('scalers', scalers_obj) if isinstance(scalers_obj, dict) else scalers_obj
    
    aws_mapping = pd.read_csv(STATIC_DIR / "aws_mapping.csv")
    propagation = pd.read_csv(STATIC_DIR / "propagation.csv")
    
    print(f"  scalers: {len(scalers)} keys")
    print(f"  aws_mapping: {len(aws_mapping)} row")
    print(f"  propagation: {len(propagation)} row")
    
    return scalers, aws_mapping, propagation


def get_recent_data_from_db(engine, hours=72):
    print(f"\n[로드] PostgreSQL 최근 {hours}시간 데이터")
    obs = pd.read_sql(f"""
        SELECT station_id, datetime, water_level as wl
        FROM observations
        WHERE datetime > NOW() - INTERVAL '{hours} hours'
        ORDER BY station_id, datetime
    """, engine)
    
    aws = pd.read_sql(f"""
        SELECT stn_id, datetime, rainfall as rn
        FROM aws_observations
        WHERE datetime > NOW() - INTERVAL '{hours} hours'
        ORDER BY stn_id, datetime
    """, engine)
    
    print(f"  obs(수위): {len(obs):,} row, {obs['station_id'].nunique()} station")
    print(f"  aws(강수): {len(aws):,} row, {aws['stn_id'].nunique()} station")
    
    return obs, aws


def make_panel(obs, aws, aws_mapping, propagation, scalers):
    log_step("0. raw obs (PostgreSQL)", obs, ['wl'])
    
    # 타입 통일
    obs['station_id'] = obs['station_id'].astype(str)
    aws['stn_id'] = aws['stn_id'].astype(str)
    aws_mapping['station_id'] = aws_mapping['station_id'].astype(str)
    aws_mapping['stn_id_aws'] = aws_mapping['stn_id_aws'].astype(str)
    propagation['station_id'] = propagation['station_id'].astype(str)
    
    if 'upstream_1' in propagation.columns:
        propagation['upstream_1'] = propagation['upstream_1'].astype(str)
    if 'upstream_2' in propagation.columns:
        propagation['upstream_2'] = propagation['upstream_2'].astype(str)
    
    # fake scaler 즉석 재학습
    fake_count = 0
    for sid in obs['station_id'].unique():
        key = f"{sid}|wl"
        if key in scalers:
            sc = scalers[key]
            if abs(sc.scale_[0] - 1.0) < 1e-6 and abs(sc.mean_[0]) < 1e-6:
                fake_count += 1
                sub = obs[obs['station_id']==sid]
                if sub['wl'].notna().sum() >= 2 and sub['wl'].std() > 0:
                    new_sc = StandardScaler()
                    new_sc.fit(sub[['wl']].values)
                    scalers[key] = new_sc
    
    if fake_count > 0:
        print(f"\n[처리] fake scaler {fake_count}개 즉석 재학습 완료")
    
    # 1. AWS 매핑
    obs = obs.merge(aws_mapping, on='station_id', how='left')
    log_step("1. AWS 매핑 후 (stn_id_aws 추가)", obs, ['wl','stn_id_aws'])
    
    # 2. AWS 강수 merge
    obs = obs.merge(
        aws, 
        left_on=['stn_id_aws','datetime'],
        right_on=['stn_id','datetime'], 
        how='left'
    )
    obs = obs.drop(columns=['stn_id'], errors='ignore')
    obs['rn'] = obs['rn'].fillna(0)
    log_step("2. 강수 데이터 merge 후", obs, ['wl','rn'])
    
    # 보간 (시간 인덱스 연속화)
    print(f"\n[처리] 시간 공백 보간 시작...")
    filled = []
    for sid, g in obs.groupby('station_id'):
        g = g.set_index('datetime').sort_index()
        full_range = pd.date_range(g.index.min(), g.index.max(), freq='h')
        g = g.reindex(full_range)
        g['wl'] = g['wl'].interpolate(method='linear').ffill().bfill()
        g['rn'] = g['rn'].fillna(0)
        g['station_id'] = sid
        g['stn_id_aws'] = g['stn_id_aws'].ffill().bfill()
        g = g.reset_index().rename(columns={'index':'datetime'})
        filled.append(g)
    obs = pd.concat(filled, ignore_index=True)
    log_step("2.5 보간 후 (시간 연속)", obs, ['wl','rn'])
    
    # 3. wl_diff
    obs = obs.sort_values(['station_id','datetime'])
    obs['wl_diff'] = obs.groupby('station_id')['wl'].diff().fillna(0)
    log_step("3. wl_diff 계산 후", obs, ['wl','wl_diff'])
    
    # 4. upstream
    obs['upstream_wl_1'] = 0.0
    obs['upstream_wl_2'] = 0.0
    obs['upstream_wl_1_mask'] = 1
    obs['upstream_wl_2_mask'] = 1
    
    upstream_applied = 0
    for _, prop_row in propagation.iterrows():
        sid = prop_row['station_id']
        for n in [1, 2]:
            up_col = f'upstream_{n}'
            lag_col = f'lag_steps_upstream_{n}'
            if pd.notna(prop_row.get(up_col)) and prop_row.get(up_col) != 'nan':
                if pd.notna(prop_row.get(lag_col)):
                    up_id = str(prop_row[up_col])
                    try:
                        lag_h = int(prop_row[lag_col])
                    except (ValueError, TypeError):
                        continue
                    
                    upstream_data = obs[obs['station_id']==up_id][['datetime','wl']].copy()
                    if len(upstream_data) == 0:
                        continue
                    upstream_data['datetime'] = upstream_data['datetime'] + pd.Timedelta(hours=lag_h)
                    upstream_data = upstream_data.rename(columns={'wl': f'upstream_wl_{n}_temp'})
                    
                    mask = obs['station_id'] == sid
                    sub = obs.loc[mask, ['datetime']].merge(upstream_data, on='datetime', how='left')
                    obs.loc[mask, f'upstream_wl_{n}'] = sub[f'upstream_wl_{n}_temp'].fillna(0).values
                    obs.loc[mask, f'upstream_wl_{n}_mask'] = sub[f'upstream_wl_{n}_temp'].isna().astype(int).values
                    upstream_applied += 1
    
    print(f"\n[처리] upstream 매핑 적용: {upstream_applied}건")
    log_step("4. upstream 추가 후", obs, ['upstream_wl_1','upstream_wl_2'])
    
    # 5. 메타
    obs['was_imputed'] = 0
    
    # 6. 시간 변수
    hour = obs['datetime'].dt.hour
    month = obs['datetime'].dt.month
    obs['hour_sin'] = np.sin(2*np.pi*hour/24)
    obs['hour_cos'] = np.cos(2*np.pi*hour/24)
    obs['month_sin'] = np.sin(2*np.pi*(month-1)/12)
    obs['month_cos'] = np.cos(2*np.pi*(month-1)/12)
    
    # 7. time_idx
    obs['time_idx'] = obs.groupby('station_id').cumcount()
    log_step("5-7. 시간 변수 + time_idx 추가 후", obs, ['hour_sin','month_sin'])
    
    # 8. 정규화
    print(f"\n[처리] 정규화 시작...")
    scaled_count = 0
    for col in SCALE_COLS:
        for sid in obs['station_id'].unique():
            key = f"{sid}|{col}"
            if key in scalers:
                mask = obs['station_id'] == sid
                values = obs.loc[mask, col].values.reshape(-1, 1)
                obs.loc[mask, col] = scalers[key].transform(values).flatten()
                scaled_count += 1
    print(f"  정규화 적용: {scaled_count}건")

    # ⭐ 여기 추가 - 정규화 다 끝난 후
    for col in SCALE_COLS:
        obs[col] = obs[col].clip(-5, 5)
    print(f"  clip(-5, 5) 적용")
    
    log_step("8. 정규화 후 (최종)", obs, ['wl','wl_diff','rn'])
    
    return obs


def main():
    print("="*60)
    print("make_panel.py 시작")
    print("="*60)
    
    engine = create_engine("postgresql://postgres:ssss9372@localhost:5432/postgres")
    
    scalers, aws_mapping, propagation = load_static_assets()
    obs, aws = get_recent_data_from_db(engine, hours=72)
    panel = make_panel(obs, aws, aws_mapping, propagation, scalers)
    
    print(f"\n{'='*60}")
    print(f"[panel 완성] {len(panel):,} row, {panel['station_id'].nunique()} station")
    print(f"{'='*60}")
    
    print("\n[predict.py 호출]")
    from predict import run_prediction
    result_df = run_prediction(live_data_df=panel)
    
    # DB 적재
    import json
    from sqlalchemy import types

    result_df['predictions'] = result_df['predictions'].apply(json.dumps)
    result_df.to_sql(
        'predictions', 
        engine, 
        if_exists='append', 
        index=False,
        dtype={'predictions': types.JSON})

    print(f"\n✅ {len(result_df)} 관측소 예측 완료 → predictions 테이블 적재")


if __name__ == "__main__":
    main()
