"""
UNIQUE 제약 추가
scheduler가 UPSERT (중복 방지)할 때 필요

실행 (한 번만):
    python add_constraints.py
"""
from sqlalchemy import text
from database import engine

with engine.connect() as conn:
    # observations: station_id + datetime 유니크
    try:
        conn.execute(text("""
            ALTER TABLE observations 
            ADD CONSTRAINT unique_obs 
            UNIQUE (station_id, datetime);
        """))
        print("✅ observations UNIQUE 제약 추가")
    except Exception as e:
        if 'already exists' in str(e):
            print("ℹ️ observations UNIQUE 이미 있음")
        else:
            print(f"❌ {e}")
    
    # aws_observations: stn_id + datetime 유니크
    try:
        conn.execute(text("""
            ALTER TABLE aws_observations 
            ADD CONSTRAINT unique_aws_obs 
            UNIQUE (stn_id, datetime);
        """))
        print("✅ aws_observations UNIQUE 제약 추가")
    except Exception as e:
        if 'already exists' in str(e):
            print("ℹ️ aws_observations UNIQUE 이미 있음")
        else:
            print(f"❌ {e}")
    
    conn.commit()

print("\n✅ 완료")
