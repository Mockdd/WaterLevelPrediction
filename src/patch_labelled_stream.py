"""floodWarnLabelled.csv의 korStream 컬럼을 obsFinalStreamReg.csv 기준으로 재정규화.

korStream 값을 obsFinalStreamReg.csv의 korStream_x에서 코드(codeWatershed)로 역조회하거나,
정규식으로 접미어를 제거하여 덮어씁니다.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELLED = ROOT / "output" / "DTW" / "floodWarnLabelled.csv"
OBS_REG = ROOT / "metadata_outputs" / "obsFinalStreamReg.csv"

STREAM_COL = "korStream"
ENCODINGS = ("utf-8-sig", "euc-kr", "cp949")

# 접미어 패턴 — $ 앵커로 마지막 천/강/보/댐/호/지 이후를 제거
SUFFIX_RE = re.compile(
    r"(천|강|보|댐|호|지)(상류|하류|중류|합류전|합류후|수위표|시점|합류|이전|이후)?$"
)


def normalize(name: str) -> str:
    name = name.strip()
    if not name or name == "-":
        return name
    m = SUFFIX_RE.search(name)
    if m:
        return name[: m.start() + 1]
    return name


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


# ── codeWatershed → 정규화된 korStream_x 매핑 (obsFinalStreamReg 기준) ──────
obs_rows, obs_enc = read_csv(OBS_REG)
print(f"obsFinalStreamReg loaded: {len(obs_rows)} rows (enc={obs_enc})")

# 동일 codeWatershed에서 첫 번째 korStream_x 사용
code_to_stream: dict[str, str] = {}
for row in obs_rows:
    code = row.get("codeWatershed", "").strip()
    stream = row.get("korStream_x", "").strip()
    if code and stream and code not in code_to_stream:
        code_to_stream[code] = stream

# ── floodWarnLabelled 읽기 ────────────────────────────────────────────────────
labelled_rows, _ = read_csv(LABELLED)
fieldnames = list(labelled_rows[0].keys())

changes = []
for row in labelled_rows:
    orig = row.get(STREAM_COL, "")
    code = row.get("codeWatershed", "").strip()

    # 1순위: obsFinalStreamReg에서 코드로 조회
    if code and code in code_to_stream:
        norm = code_to_stream[code]
    else:
        # 2순위: 정규식으로 접미어 제거
        norm = normalize(orig)

    if orig != norm:
        changes.append((orig, norm))
    row[STREAM_COL] = norm

print(f"\nPatched {len(changes)} rows:")
for before, after in sorted(set(changes)):
    print(f"  {before!r}  →  {after!r}")

with LABELLED.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(labelled_rows)

print(f"\nSaved → {LABELLED}")
