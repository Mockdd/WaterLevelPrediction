"""obsFinal.csv의 하천명 컬럼 정규화: 천/강/보/댐/호 뒤 접미어 제거 후 저장."""
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "metadata_outputs" / "obsFinal.csv"
OUTPUT = ROOT / "metadata_outputs" / "obsFinalStreamReg.csv"

STREAM_COL = "korStream_x"
ENCODINGS = ("euc-kr", "utf-8-sig", "cp949")

# 천/강/보/댐/호 뒤에 붙은 접미어(상류, 하류, 중류, 합류전, 합류후, 수위표 등) 제거
SUFFIX_PATTERN = re.compile(r"(천|강|보|댐|호|지)(상류|하류|중류|합류전|합류후|수위표|시점|합류|이전|이후|이하|이상|구간.*)?$")


def normalize_stream(name: str) -> str:
    name = name.strip()
    if not name or name == "-":
        return name
    m = SUFFIX_PATTERN.search(name)
    if m:
        return name[: m.start() + 1]  # 천/강/보/... 한 글자까지 포함, 이후 제거
    return name


def read_csv(path: Path):
    for enc in ENCODINGS:
        try:
            with path.open(encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            if rows:
                return rows, enc
        except (UnicodeDecodeError, csv.Error):
            continue
    raise RuntimeError(f"Cannot read {path}")


def main():
    rows, enc = read_csv(INPUT)
    print(f"Loaded {len(rows)} rows with encoding={enc}")

    # nameStream* 컬럼 탐색
    fieldnames = list(rows[0].keys())
    stream_cols = [c for c in fieldnames if c.lower().startswith("namestream") or c == STREAM_COL]
    print(f"Stream columns found: {stream_cols}")

    # 변환 미리 보기 (고유값)
    before_after = {}
    for row in rows:
        for col in stream_cols:
            val = row.get(col, "").strip()
            norm = normalize_stream(val)
            if val != norm:
                before_after[val] = norm

    print(f"\nTransformations ({len(before_after)} unique changes):")
    for k, v in sorted(before_after.items()):
        print(f"  {k!r}  →  {v!r}")

    # 실제 변환 적용
    for row in rows:
        for col in stream_cols:
            if col in row:
                row[col] = normalize_stream(row[col])

    # 저장
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nSaved → {OUTPUT}")


if __name__ == "__main__":
    main()
