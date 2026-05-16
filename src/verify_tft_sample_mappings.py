"""
샘플 패널의 (1) 상류 수위 lag shift, (2) AWS 강수 매핑 검증.

Usage::

  python src/verify_tft_sample_mappings.py --panel data/tft_processed_sample_train/tft_train.parquet
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd

from tft_preprocess import (
    LAG_MANIFEST,
    RAIN_MAP,
    ROOT,
    UPSTREAM_MAP,
    _obscd_str,
    _parse_lag0,
    attach_upstream,
    collect_waterlevel_station_ids,
    load_lag_table,
    load_rainfall_map,
    load_station_list,
)
from tft_s3_io import load_dotenv_root, load_kma_rainfall_range, load_waterlevel_range, make_s3


def _lag_int(row: pd.Series, col: str) -> int:
    if col not in row.index:
        return 0
    try:
        return max(0, int(float(row[col])))
    except (TypeError, ValueError):
        return 0


def verify_rainfall_map(panel: pd.DataFrame, rain_map: pd.DataFrame) -> pd.DataFrame:
    """obsWaterLevel_top1: station_id → stn_id_aws 일치 및 S3 RN 값 일치."""
    exp = rain_map.rename(columns={"stn_id_aws": "stn_id_aws_expected"})
    merged = panel[["station_id", "stn_id_aws", "datetime", "rn"]].drop_duplicates(
        ["station_id", "datetime"]
    )
    chk = merged.merge(exp[["station_id", "stn_id_aws_expected"]], on="station_id", how="left")
    chk["aws_map_ok"] = chk["stn_id_aws"].astype(str) == chk["stn_id_aws_expected"].astype(str)
    return chk


def build_raw_panel(
    panel: pd.DataFrame,
    wl_long: pd.DataFrame,
    rain_map: pd.DataFrame,
    rn_long: pd.DataFrame,
    mapping: pd.DataFrame,
    lag_tbl: pd.DataFrame,
) -> pd.DataFrame:
    """스케일 전 merge·upstream·RN (패널 키 기준)."""
    keys = panel[["station_id", "datetime", "split"]].drop_duplicates()
    out = keys.merge(
        wl_long.rename(columns={"obscd": "station_id", "value": "wl"}),
        on=["station_id", "datetime"],
        how="left",
    )
    out = out.merge(rain_map, on="station_id", how="left")
    out = out.merge(
        rn_long,
        left_on=["stn_id_aws", "datetime"],
        right_on=["stn_id", "datetime"],
        how="left",
    )
    out = out.drop(columns=["stn_id"], errors="ignore")
    return attach_upstream(out, wl_long, mapping, lag_tbl)


def verify_upstream_slots(
    panel: pd.DataFrame,
    raw: pd.DataFrame,
) -> pd.DataFrame:
    """스케일된 패널의 upstream 마스크 vs raw 재구성 일치 (값은 스케일 후라 비교 안 함)."""
    cols = [
        "station_id",
        "datetime",
        "upstream_wl_1_mask",
        "upstream_wl_2_mask",
    ]
    out = panel[cols].merge(
        raw[cols].rename(
            columns={
                "upstream_wl_1_mask": "upstream_wl_1_mask_raw",
                "upstream_wl_2_mask": "upstream_wl_2_mask_raw",
            }
        ),
        on=["station_id", "datetime"],
    )
    for slot in (1, 2):
        m = f"upstream_wl_{slot}_mask"
        rb = f"upstream_wl_{slot}_mask_raw"
        out[f"{m}_ok"] = out[m].astype(int) == out[rb].astype(int)
    return out


def spot_check_lag_shift(
    raw: pd.DataFrame,
    wl_long: pd.DataFrame,
    mapping: pd.DataFrame,
    lag_tbl: pd.DataFrame,
    *,
    atol: float = 1e-9,
) -> pd.DataFrame:
    """lag>0 관측소: upstream_wl_k(t) == wl[upstream_k](t-L)."""
    wl_p = wl_long.rename(columns={"obscd": "station_id", "value": "wl"})
    wl_by = {k: g.set_index("datetime")["wl"] for k, g in wl_p.groupby("station_id")}
    map_idx = mapping.set_index("station_id")
    lag_idx = lag_tbl.set_index("station_id")
    rows = []
    for sid in raw["station_id"].unique():
        if sid not in map_idx.index:
            continue
        rm = map_idx.loc[sid]
        rl = lag_idx.loc[sid] if sid in lag_idx.index else pd.Series(dtype=object)
        g = raw[raw["station_id"] == sid].sort_values("datetime")
        for slot in (1, 2):
            up = _obscd_str(rm.get(f"upstream_{slot}"))
            if not up or up not in wl_by or _parse_lag0(rm.get(f"upstream_{slot}_lag0")):
                continue
            L = _lag_int(rl, f"lag_steps_upstream_{slot}")
            if L <= 0:
                continue
            exp = wl_by[up].shift(L).reindex(g["datetime"].values)
            got = g[f"upstream_wl_{slot}"].values
            mask = g[f"upstream_wl_{slot}_mask"].astype(int) == 1
            ok = np.all(np.isclose(got[mask], exp.values[mask], rtol=0, atol=atol, equal_nan=True))
            rows.append({"station_id": sid, "slot": slot, "upstream_id": up, "lag_steps": L, "spot_ok": ok})
    return pd.DataFrame(rows)


def summarize_upstream_meta(
    stations: list[str], mapping: pd.DataFrame, lag_tbl: pd.DataFrame
) -> pd.DataFrame:
    map_idx = mapping.set_index("station_id")
    lag_idx = lag_tbl.set_index("station_id")
    rows = []
    for sid in stations:
        if sid not in map_idx.index:
            continue
        rm = map_idx.loc[sid]
        rl = lag_idx.loc[sid] if sid in lag_idx.index else pd.Series(dtype=object)
        for slot in (1, 2):
            up = _obscd_str(rm.get(f"upstream_{slot}"))
            lag0 = _parse_lag0(rm.get(f"upstream_{slot}_lag0"))
            lag = _lag_int(rl, f"lag_steps_upstream_{slot}") if len(rl) else 0
            rows.append(
                {
                    "station_id": sid,
                    "slot": slot,
                    "upstream_id": up or "",
                    "lag_steps": lag,
                    "lag0": lag0,
                    "lag_source": "upstream_lag_ccf_by_station_v20260514_0735.csv",
                }
            )
    return pd.DataFrame(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Verify upstream lag + AWS rainfall mappings")
    p.add_argument("--panel", required=True, help="검증할 패널 parquet (보통 tft_train.parquet)")
    p.add_argument("--start", default=None, help="S3 RN 재검증 시작일 (미지정 시 패널 min)")
    p.add_argument("--end", default=None, help="S3 RN 재검증 종료일")
    p.add_argument("--rainfall-map-csv", default=str(RAIN_MAP))
    p.add_argument("--upstream-map-csv", default=str(UPSTREAM_MAP))
    p.add_argument("--lag-manifest", default=str(LAG_MANIFEST))
    p.add_argument("--report", default=None, help="요약 CSV 경로")
    args = p.parse_args()

    panel = pd.read_parquet(args.panel)
    panel["station_id"] = panel["station_id"].astype(str).str.strip()
    panel["datetime"] = pd.to_datetime(panel["datetime"])
    stations = sorted(panel["station_id"].unique())

    rain_map = load_rainfall_map(Path(args.rainfall_map_csv))
    mapping = pd.read_csv(Path(args.upstream_map_csv), dtype=str)
    mapping["station_id"] = mapping["station_id"].astype(str).str.strip()
    lag_tbl = load_lag_table(Path(args.upstream_map_csv), Path(args.lag_manifest))

    print(f"Panel: {args.panel} | stations={len(stations)} rows={len(panel):,}")

    # (2) AWS mapping
    rain_chk = verify_rainfall_map(panel, rain_map)
    n_map_fail = int((~rain_chk["aws_map_ok"]).sum())
    missing_aws = int(rain_chk["stn_id_aws_expected"].isna().sum())
    print("\n[2] obsWaterLevel_top1 → stn_id_aws")
    print(f"  rows checked: {len(rain_chk):,}")
    print(f"  aws_map_ok: {(rain_chk['aws_map_ok']).sum():,} / {len(rain_chk):,}")
    if n_map_fail:
        bad = rain_chk[~rain_chk["aws_map_ok"]].head(5)
        print("  FAIL samples:\n", bad[["station_id", "stn_id_aws", "stn_id_aws_expected"]])

    load_dotenv_root()
    start = pd.Timestamp(args.start or panel["datetime"].min())
    end = pd.Timestamp(args.end or panel["datetime"].max())
    s3, bucket = make_s3()
    aws_ids = set(rain_chk["stn_id_aws"].dropna().astype(str))
    rn_long = load_kma_rainfall_range(s3, bucket, start, end, aws_ids, verbose=False)
    wl_obscds = collect_waterlevel_station_ids(stations, mapping)
    wl_long = load_waterlevel_range(s3, bucket, start, end, wl_obscds, verbose=False)
    raw = build_raw_panel(panel, wl_long, rain_map, rn_long, mapping, lag_tbl)
    rn_join = raw[["station_id", "datetime", "stn_id_aws", "rn"]].merge(
        rn_long.rename(columns={"rn": "rn_s3"}),
        left_on=["stn_id_aws", "datetime"],
        right_on=["stn_id", "datetime"],
        how="left",
    )
    both = rn_join["rn"].notna() & rn_join["rn_s3"].notna()
    rn_match = np.isclose(rn_join.loc[both, "rn"], rn_join.loc[both, "rn_s3"], rtol=0, atol=1e-6)
    print(f"  S3 RN vs raw merge: {rn_match.sum():,} / {both.sum():,} match (pre-scale)")

    # (1) Upstream lag
    up_chk = verify_upstream_slots(panel, raw)
    for slot in (1, 2):
        col = f"upstream_wl_{slot}_mask_ok"
        n_ok = int(up_chk[col].sum())
        print(f"\n[1] upstream_wl_{slot}_mask (scaled panel vs raw): {n_ok:,} / {len(up_chk):,} OK")
    spot = spot_check_lag_shift(raw, wl_long, mapping, lag_tbl)
    if len(spot):
        print(f"  lag>0 spot checks: {spot['spot_ok'].sum()} / {len(spot)} OK")
        print(spot.to_string(index=False))
    else:
        print("  lag>0 spot checks: (none in this station set)")
    meta = summarize_upstream_meta(stations, mapping, lag_tbl)
    print("\n  lag metadata (sample):")
    print(meta[meta["upstream_id"] != ""].head(12).to_string(index=False))

    ok = (
        n_map_fail == 0
        and up_chk["upstream_wl_1_mask_ok"].all()
        and up_chk["upstream_wl_2_mask_ok"].all()
        and (spot.empty or spot["spot_ok"].all())
        and (not both.sum() or rn_match.all())
    )
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        rain_chk.to_csv(Path(args.report).with_name("verify_rainfall.csv"), index=False)
        up_chk.head(5000).to_csv(Path(args.report).with_name("verify_upstream_sample.csv"), index=False)
        meta.to_csv(Path(args.report), index=False)

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
