"""
관측소 × datetime(1H) 스켈레톤 생성 (``docs/before_training.md`` §1.4 단계 4).

S3 merge 전 격자만 만든다. 컬럼: ``station_id``, ``datetime``, ``split``.
수위·강수 merge 및 ``wl`` 결측 채움은 ``src/tft_preprocess.py`` 에서 이어진다.

Usage::

  python src/build_tft_skeleton.py
  python src/build_tft_skeleton.py --start 2024-06-15 --end 2024-06-22 --max-stations 10
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

import pandas as pd

from tft_preprocess import (
    DEFAULT_TEST_END,
    DEFAULT_TEST_START,
    DEFAULT_TRAIN_END,
    DEFAULT_TRAIN_START,
    DEFAULT_VAL_END,
    DEFAULT_VAL_START,
    OBS_TARGET,
    build_skeleton,
    load_station_list,
)

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "metadata_outputs" / "tft_skeleton.parquet"


def main() -> int:
    p = argparse.ArgumentParser(description="TFT station×datetime 1H skeleton")
    p.add_argument("--start", default=DEFAULT_TRAIN_START, help="캘린더 시작(포함)")
    p.add_argument("--end", default=DEFAULT_TEST_END, help="캘린더 끝(포함)")
    p.add_argument("--stations-csv", default=str(OBS_TARGET))
    p.add_argument("--max-stations", type=int, default=None)
    p.add_argument("--out", default=str(DEFAULT_OUT), help="출력 parquet 경로")
    args = p.parse_args()

    stations = load_station_list(Path(args.stations_csv), args.max_stations)
    if not stations:
        print("No station codes.", file=sys.stderr)
        return 1

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    sk = build_skeleton(stations, start, end)
    n_drop = int((sk["split"] == "drop").sum())
    sk = sk[sk["split"] != "drop"].copy()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sk.to_parquet(out_path, index=False)

    meta = {
        "stations_csv": str(Path(args.stations_csv).resolve()),
        "n_stations": len(stations),
        "calendar_start": args.start,
        "calendar_end": args.end,
        "n_rows": len(sk),
        "n_hours_per_station": int(sk.groupby("station_id").size().iloc[0]) if len(sk) else 0,
        "rows_dropped_outside_split": n_drop,
        "split_counts": sk["split"].value_counts().to_dict(),
        "splits": {
            "train": [DEFAULT_TRAIN_START, DEFAULT_TRAIN_END],
            "val": [DEFAULT_VAL_START, DEFAULT_VAL_END],
            "test": [DEFAULT_TEST_START, DEFAULT_TEST_END],
        },
        "s3_layout": {
            "waterlevel": "hrfco/raw/{year}/waterlevel/date={yyyy-mm-dd}/data.parquet",
            "rainfall": "kma/raw/{year}/aws_awsh_1h/date={yyyy-mm-dd}/data.parquet",
        },
        "output": str(out_path.resolve()),
    }
    meta_path = out_path.with_suffix(".meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"Stations: {len(stations)} | {args.start} .. {args.end}")
    print(f"Skeleton rows: {len(sk):,} (dropped outside split: {n_drop:,})")
    print("split:", dict(sk["split"].value_counts()))
    print(f"Wrote {out_path}")
    print(f"Wrote {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
