"""floodWarnLabelled.csv에 coverageStream 컬럼 추가.

coverageStream: korStream 값이 동일한 obsFinalStreamReg.csv 관측소 목록 (세미콜론 구분).
"""
import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELLED = ROOT / "output" / "DTW" / "floodWarnLabelled.csv"
OBS_REG = ROOT / "metadata_outputs" / "obsFinalStreamReg.csv"
ENCODINGS = ("utf-8-sig", "euc-kr", "cp949")


def read_csv(path: Path, encodings=ENCODINGS):
    for enc in encodings:
        try:
            with path.open(encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            if rows:
                return rows, enc
        except (UnicodeDecodeError, csv.Error):
            continue
    raise RuntimeError(f"Cannot read {path}")


# ── korStream_x → 관측소명 목록 매핑 ─────────────────────────────────────────
obs_rows, obs_enc = read_csv(OBS_REG)
print(f"obsFinalStreamReg: {len(obs_rows)} rows (enc={obs_enc})")

stream_to_stations: dict[str, list[str]] = defaultdict(list)
for row in obs_rows:
    stream = row.get("korStream_x", "").strip()
    station = row.get("korObs", "").strip()
    if stream and station:
        stream_to_stations[stream].append(station)

print(f"Unique korStream_x values: {len(stream_to_stations)}")

# ── floodWarnLabelled 읽기 및 컬럼 추가 ──────────────────────────────────────
labelled_rows, _ = read_csv(LABELLED)
fieldnames = list(labelled_rows[0].keys())

if "coverageStream" not in fieldnames:
    fieldnames.append("coverageStream")

hit = miss = 0
for row in labelled_rows:
    stream = row.get("korStream", "").strip()
    stations = stream_to_stations.get(stream, [])
    row["coverageStream"] = ";".join(stations)
    if stations:
        hit += 1
    else:
        miss += 1

print(f"\ncoverageStream 매핑: {hit}개 행 성공 / {miss}개 행 미매핑")
if miss:
    unmatched = sorted({r["korStream"] for r in labelled_rows if not r["coverageStream"]})
    print(f"미매핑 korStream 값: {unmatched}")

# 미리보기 (고유값)
preview = {}
for row in labelled_rows:
    k = row["korStream"]
    if k not in preview:
        preview[k] = row["coverageStream"]

print("\n[미리보기] korStream → coverageStream (고유값)")
for k, v in sorted(preview.items()):
    stations_list = v.split(";") if v else []
    print(f"  {k!r}  ({len(stations_list)}개)  →  {v[:80]}{'...' if len(v)>80 else ''}")

# 저장
with LABELLED.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(labelled_rows)

print(f"\nSaved → {LABELLED}")
