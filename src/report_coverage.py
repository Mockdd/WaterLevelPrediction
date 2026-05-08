"""홍수특보 관측소 커버리지 분석 보고서 생성 → floodWarnCoverageReport.md"""
import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELLED = ROOT / "output" / "DTW" / "floodWarnLabelled.csv"
OBS_REG = ROOT / "metadata_outputs" / "obsFinalStreamReg.csv"
OUT = ROOT / "output" / "DTW" / "floodWarnCoverageReport.md"
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


# ── 데이터 로드 ───────────────────────────────────────────────────────────────
labelled = read_csv(LABELLED)
obs = read_csv(OBS_REG)

# obsFinal 전체 관측소
all_stations: dict[str, str] = {}  # korObs → korStream_x
for row in obs:
    name = row.get("korObs", "").strip()
    stream = row.get("korStream_x", "").strip()
    if name:
        all_stations[name] = stream

# coverageStream 고유 관측소 수집 + 홍수특보 발령 하천
covered_stations: set[str] = set()
warned_streams: set[str] = set()
warned_stations: set[str] = set()  # 실제 홍수특보 발령 관측소 (point)

for row in labelled:
    stream = row.get("korStream", "").strip()
    point = row.get("point", "").strip()
    coverage = row.get("coverageStream", "").strip()
    action = row.get("action", "").strip()
    if stream:
        warned_streams.add(stream)
    if point and action == "발령":
        warned_stations.add(point)
    if coverage:
        for s in coverage.split(";"):
            s = s.strip()
            if s:
                covered_stations.add(s)

in_coverage = covered_stations & set(all_stations.keys())
not_covered = set(all_stations.keys()) - covered_stations

# 하천별 관측소 목록 (obsFinalStreamReg 기준)
stream_to_obs: dict[str, list[str]] = defaultdict(list)
for row in obs:
    stream = row.get("korStream_x", "").strip()
    name = row.get("korObs", "").strip()
    if stream and name:
        stream_to_obs[stream].append(name)

# 커버되지 않은 관측소를 하천별로 그룹화
not_covered_by_stream: dict[str, list[str]] = defaultdict(list)
for name in sorted(not_covered):
    stream = all_stations.get(name, "-")
    not_covered_by_stream[stream].append(name)

# ── 보고서 작성 ───────────────────────────────────────────────────────────────
lines = []
lines += [
    "# 홍수특보 관측소 커버리지 분석 보고서\n",
    "\n",
    "> **분석 대상**: 2024~2025년 홍수특보 발령 이력 (`floodWarnLabelled.csv`)  \n",
    "> **관측소 기준**: `obsFinalStreamReg.csv` (하천명 정규화 완료)\n",
    "\n",
    "---\n",
    "\n",
]

# 1. 요약
lines += [
    "## 1. 요약\n",
    "\n",
    f"| 항목 | 수량 | 비율 |\n",
    f"|------|-----:|-----:|\n",
    f"| obsFinal 전체 관측소 | **{len(all_stations)}개** | 100% |\n",
    f"| 홍수특보 발령 하천과 동일 하천명 관측소 | **{len(in_coverage)}개** | **{len(in_coverage)/len(all_stations)*100:.1f}%** |\n",
    f"| 커버되지 않은 관측소 | **{len(not_covered)}개** | **{len(not_covered)/len(all_stations)*100:.1f}%** |\n",
    "\n",
    f"전체 **{len(all_stations)}개** 관측소 중 **{len(in_coverage)}개(25.1%)** 가 "
    f"2024~2025년에 홍수특보가 발령된 적 있는 하천에 속합니다.  \n",
    f"나머지 **{len(not_covered)}개(74.9%)** 는 해당 기간 내 홍수특보 발령 이력이 없는 하천에 위치합니다.\n",
    "\n",
    "---\n",
    "\n",
]

# 2. 홍수특보 발령 하천 목록
lines += [
    "## 2. 홍수특보 발령 하천 목록\n",
    "\n",
    f"총 **{len(warned_streams)}개** 고유 하천명\n",
    "\n",
]
for i, s in enumerate(sorted(warned_streams), 1):
    cnt = len(stream_to_obs.get(s, []))
    lines.append(f"{i}. **{s}** — 소속 관측소 {cnt}개\n")
lines.append("\n---\n\n")

# 3. 커버된 관측소 목록 (하천별)
lines += [
    "## 3. 커버된 관측소 목록 (홍수특보 발령 하천 소속, 총 {n}개)\n".format(n=len(in_coverage)),
    "\n",
]
for stream in sorted(warned_streams):
    stations_in_stream = [s for s in stream_to_obs.get(stream, []) if s in in_coverage]
    if not stations_in_stream:
        continue
    lines.append(f"### {stream} ({len(stations_in_stream)}개)\n\n")
    for st in sorted(stations_in_stream):
        marker = " ⚑" if st in warned_stations else ""
        lines.append(f"- {st}{marker}\n")
    lines.append("\n")

lines += [
    "> ⚑ 표시: 해당 기간 내 실제 홍수특보 발령 관측소\n",
    "\n",
    "---\n",
    "\n",
]

# 4. 커버되지 않은 관측소 목록 (하천별)
lines += [
    "## 4. 커버되지 않은 관측소 목록 (총 {n}개)\n".format(n=len(not_covered)),
    "\n",
    "홍수특보 발령 이력이 없는 하천에 위치한 관측소입니다.\n",
    "\n",
]
for stream in sorted(not_covered_by_stream.keys()):
    stations = not_covered_by_stream[stream]
    lines.append(f"### {stream} ({len(stations)}개)\n\n")
    for st in sorted(stations):
        lines.append(f"- {st}\n")
    lines.append("\n")

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8-sig") as f:
    f.writelines(lines)

print(f"Report saved → {OUT}")
print(f"  홍수특보 발령 하천: {len(warned_streams)}개")
print(f"  커버된 관측소: {len(in_coverage)}개 / 전체 {len(all_stations)}개 ({len(in_coverage)/len(all_stations)*100:.1f}%)")
print(f"  미커버 관측소: {len(not_covered)}개 ({len(not_covered)/len(all_stations)*100:.1f}%)")
