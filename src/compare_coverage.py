"""홍수특보 발령 하천과 동일 하천명 관측소 vs obsFinal 전체 관측소 비교."""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELLED = ROOT / "output" / "DTW" / "floodWarnLabelled.csv"
OBS_REG = ROOT / "metadata_outputs" / "obsFinalStreamReg.csv"
ENCODINGS = ("utf-8-sig", "euc-kr", "cp949")


def read_csv(path, encodings=ENCODINGS):
    for enc in encodings:
        try:
            with path.open(encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            if rows:
                return rows
        except (UnicodeDecodeError, csv.Error):
            continue
    raise RuntimeError(f"Cannot read {path}")


# ── 1. coverageStream 고유 관측소 수집 ────────────────────────────────────────
labelled = read_csv(LABELLED)

covered_stations: set[str] = set()
warned_streams: set[str] = set()

for row in labelled:
    stream = row.get("korStream", "").strip()
    coverage = row.get("coverageStream", "").strip()
    if stream:
        warned_streams.add(stream)
    if coverage:
        for s in coverage.split(";"):
            s = s.strip()
            if s:
                covered_stations.add(s)

# ── 2. obsFinalStreamReg 전체 관측소 수집 ────────────────────────────────────
obs = read_csv(OBS_REG)
all_stations: set[str] = set()
for row in obs:
    s = row.get("korObs", "").strip()
    if s:
        all_stations.add(s)

# ── 3. 비교 ───────────────────────────────────────────────────────────────────
in_coverage = covered_stations & all_stations
not_covered = all_stations - covered_stations

print("=" * 60)
print("홍수특보 발령 하천 목록")
print("=" * 60)
for s in sorted(warned_streams):
    print(f"  {s}")

print()
print("=" * 60)
print(f"obsFinal 전체 관측소: {len(all_stations)}개")
print(f"coverageStream 고유 관측소: {len(covered_stations)}개")
print(f"  → obsFinal에 존재하는 관측소: {len(in_coverage)}개  "
      f"({len(in_coverage)/len(all_stations)*100:.1f}%)")
print(f"  → obsFinal에 없는 관측소:     {len(covered_stations)-len(in_coverage)}개")
print(f"커버되지 않은 관측소: {len(not_covered)}개  "
      f"({len(not_covered)/len(all_stations)*100:.1f}%)")
print("=" * 60)

print(f"\n[커버된 관측소 {len(in_coverage)}개]")
for s in sorted(in_coverage):
    print(f"  {s}")

print(f"\n[커버되지 않은 관측소 {len(not_covered)}개]")
for s in sorted(not_covered):
    # 해당 관측소의 korStream_x 확인
    stream = next(
        (r.get("korStream_x", "") for r in obs if r.get("korObs", "").strip() == s), ""
    )
    print(f"  {s}  (하천: {stream})")
