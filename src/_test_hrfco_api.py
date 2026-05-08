"""HRFCO API 이력 데이터 엔드포인트 탐색.

python src/_test_hrfco_api.py
"""

import os
import requests
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

TOKEN = os.getenv("hrfco_token", "").strip()
BASE  = "https://api.hrfco.go.kr"
OBS   = "1006630"   # 횡성군(오산교)

print(f"TOKEN: {TOKEN[:8]}...")
print("=" * 60)

# ── 테스트: URL 경로에 날짜 넣기, 다양한 형식 시도 ────────────────
# Pattern D에서 YYYYMMDD(8자리)는 "잘못된 날짜 형식" 오류
# → YYYYMMDDHHMM(12자리) 또는 YYYYMMDDHHMMSS(14자리) 시도

date_formats = [
    ("YYYYMMDDHHMM (12자리)", "202407160000", "202407182300"),
    ("YYYYMMDDHH (10자리)",   "2024071600",   "2024071823"),
    ("YYYYMMDDHHmm 시작만",   "202407160000", "202407290000"),
]

print("\n[테스트 1] URL 경로에 날짜 포함 - 다양한 형식")
for label, start, end in date_formats:
    url = f"{BASE}/{TOKEN}/waterlevel/list/1H/{OBS}/{start}/{end}.json"
    try:
        r = requests.get(url, timeout=10)
        d = r.json()
        cnt = len(d.get("content", []))
        msg = d.get("message", "")
        code = d.get("code", "")
        print(f"  [{label}]")
        print(f"    URL: .../{OBS}/{start}/{end}.json")
        print(f"    HTTP {r.status_code} | content={cnt}건 | code={code} | msg={msg}")
        if cnt > 0:
            c = d["content"]
            print(f"    첫 ymdhm: {c[0].get('ymdhm')} / 마지막: {c[-1].get('ymdhm')}")
            print(f"    첫 번째 행: {c[0]}")
    except Exception as e:
        print(f"    오류: {e}")

# ── 테스트 2: 10M interval로도 동일하게 시도 ────────────────────
print("\n[테스트 2] 10M interval + URL 경로 날짜")
url_10m = f"{BASE}/{TOKEN}/waterlevel/list/10M/{OBS}/202407160000/202407162300.json"
try:
    r = requests.get(url_10m, timeout=10)
    d = r.json()
    cnt = len(d.get("content", []))
    msg = d.get("message", "")
    print(f"  HTTP {r.status_code} | content={cnt}건 | msg={msg}")
    if cnt > 0:
        c = d["content"]
        print(f"  첫 ymdhm: {c[0].get('ymdhm')} / 마지막: {c[-1].get('ymdhm')}")
except Exception as e:
    print(f"  오류: {e}")

# ── 테스트 3: rainfall 동일 구조 확인 ───────────────────────────
print("\n[테스트 3] rainfall endpoint 확인")
url_rf = f"{BASE}/{TOKEN}/rainfall/list/1H/{OBS}/202407160000/202407182300.json"
try:
    r = requests.get(url_rf, timeout=10)
    d = r.json()
    cnt = len(d.get("content", []))
    msg = d.get("message", "")
    print(f"  HTTP {r.status_code} | content={cnt}건 | msg={msg}")
    if cnt > 0:
        c = d["content"]
        print(f"  첫 번째 행: {c[0]}")
except Exception as e:
    print(f"  오류: {e}")

print("\n결과 붙여넣어 주세요!")
