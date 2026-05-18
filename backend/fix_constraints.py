"""
중복 데이터 확인 → 정리 → UNIQUE 제약 추가

이전 add_constraints.py가 실패한 원인:
- 이미 중복 데이터가 있어서 UNIQUE 제약 못 검
- 한 트랜잭션에서 첫 에러 나면 뒤 명령도 다 실패

이 스크립트는:
1. 각 작업을 별도 트랜잭션으로 분리 (begin())
2. 중복 데이터 먼저 확인하고 정리
3. 그 다음 제약 추가

실행:
    python fix_constraints.py
"""
from sqlalchemy import text
from database import engine


def check_duplicates(table, key_cols):
    """중복 데이터 개수 확인"""
    cols = ', '.join(key_cols)
    sql = f"""
        SELECT {cols}, COUNT(*) as cnt
        FROM {table}
        GROUP BY {cols}
        HAVING COUNT(*) > 1
    """
    with engine.connect() as conn:
        result = conn.execute(text(sql)).fetchall()
    return result


def remove_duplicates(table, key_cols):
    """
    중복 행 제거 (id가 가장 큰 것만 남김)
    DELETE ... WHERE id NOT IN (SELECT MAX(id) GROUP BY ...)
    """
    cols = ', '.join(key_cols)
    sql = f"""
        DELETE FROM {table}
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM {table}
            GROUP BY {cols}
        )
    """
    with engine.begin() as conn:  # begin() = 자동 commit/rollback
        result = conn.execute(text(sql))
        return result.rowcount


def add_constraint(table, name, cols):
    """UNIQUE 제약 추가 (별도 트랜잭션)"""
    cols_str = ', '.join(cols)
    sql = f"""
        ALTER TABLE {table}
        ADD CONSTRAINT {name}
        UNIQUE ({cols_str})
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
        return "added"
    except Exception as e:
        if 'already exists' in str(e):
            return "exists"
        else:
            return f"error: {e}"


# ────────────────────────────────────────────────
# 1. observations
# ────────────────────────────────────────────────
print("=" * 60)
print("1. observations 검사")
print("=" * 60)

dups = check_duplicates('observations', ['station_id', 'datetime'])
print(f"  중복 (station_id, datetime) 조합: {len(dups)}개")

if len(dups) > 0:
    print(f"  예시: {dups[:3]}")
    print(f"  → 중복 제거 중...")
    deleted = remove_duplicates('observations', ['station_id', 'datetime'])
    print(f"  ✅ {deleted}건 삭제")
else:
    print(f"  ✅ 중복 없음")

result = add_constraint('observations', 'unique_obs', ['station_id', 'datetime'])
if result == "added":
    print(f"  ✅ unique_obs 제약 추가")
elif result == "exists":
    print(f"  ℹ️ unique_obs 이미 있음")
else:
    print(f"  ❌ {result}")


# ────────────────────────────────────────────────
# 2. aws_observations
# ────────────────────────────────────────────────
print()
print("=" * 60)
print("2. aws_observations 검사")
print("=" * 60)

dups = check_duplicates('aws_observations', ['stn_id', 'datetime'])
print(f"  중복 (stn_id, datetime) 조합: {len(dups)}개")

if len(dups) > 0:
    print(f"  예시: {dups[:3]}")
    print(f"  → 중복 제거 중...")
    deleted = remove_duplicates('aws_observations', ['stn_id', 'datetime'])
    print(f"  ✅ {deleted}건 삭제")
else:
    print(f"  ✅ 중복 없음")

result = add_constraint('aws_observations', 'unique_aws_obs', ['stn_id', 'datetime'])
if result == "added":
    print(f"  ✅ unique_aws_obs 제약 추가")
elif result == "exists":
    print(f"  ℹ️ unique_aws_obs 이미 있음")
else:
    print(f"  ❌ {result}")


print()
print("=" * 60)
print("완료")
print("=" * 60)
