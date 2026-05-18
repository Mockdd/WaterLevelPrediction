"""
Optional: download a small HRFCO 1H waterlevel sample into experiments/data/.

CCF·lag 결정의 **권장 경로는 본 파이프라인(S3 등)에서 읽는 것**이다
(``docs/before_training.md`` 워크플로 합의). 이 스크립트는 토큰·네트워크만
있을 때 빠르게 손대볼 **보조용**이며, 합의된 산출물을 대체하지 않는다.

Uses ``hrfco_token`` from project root ``.env`` (same as ``src/run_dtw.py``).
No API keys are stored in the repository.

Usage (from repo root):
  python experiments/scripts/download_sample_wl.py
"""
from __future__ import annotations

import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "experiments" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Eastern stream 1001 chain (upstream_mapping_must.csv) — short API crawl
OBSCDS = ["1001602", "1001603", "1001605", "1001607", "1001613", "1001615"]
# 2024 Jul–Aug (홍수기 샘플; dtw_check.ipynb와 동일 기간 유형)
START = "2024070100"
END = "2024083123"
OUT_CSV = DATA_DIR / "wl_1h_sample_202407_202408.csv"


def load_token() -> str:
    try:
        from dotenv import load_dotenv
        import os

        load_dotenv(ROOT / ".env")
        return (os.getenv("hrfco_token") or "").strip().strip('"')
    except Exception:
        return ""


def fetch_station(obscd: str, token: str) -> list[dict]:
    url = f"https://api.hrfco.go.kr/{token}/waterlevel/list/1H/{obscd}/{START}/{END}.xml"
    rows: list[dict] = []
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    for item in root.findall(".//Waterlevel"):
        wl_val = item.findtext("wl")
        if wl_val is None or not str(wl_val).strip() or str(wl_val).strip() in ("-", ""):
            continue
        try:
            wl = float(str(wl_val).strip())
        except ValueError:
            continue
        ymdhm = item.findtext("ymdhm")
        if not ymdhm:
            continue
        rows.append(
            {
                "obscd": obscd,
                "datetime": ymdhm,
                "wl": wl,
                "fw": item.findtext("fw"),
            }
        )
    return rows


def main() -> int:
    token = load_token()
    if not token:
        print("Missing hrfco_token in .env — cannot download.", file=sys.stderr)
        return 1
    all_rows: list[dict] = []
    for i, obscd in enumerate(OBSCDS):
        try:
            part = fetch_station(obscd, token)
            all_rows.extend(part)
            print(f"  {obscd}: {len(part)} rows")
        except Exception as e:
            print(f"  [fail] {obscd}: {e}", file=sys.stderr)
        time.sleep(0.15)
        if (i + 1) % 3 == 0:
            time.sleep(0.3)

    if not all_rows:
        print("No data downloaded.", file=sys.stderr)
        return 2

    df = pd.DataFrame(all_rows)
    df["datetime"] = pd.to_datetime(df["datetime"], format="%Y%m%d%H", errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values(["obscd", "datetime"])
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"Wrote {OUT_CSV} ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
