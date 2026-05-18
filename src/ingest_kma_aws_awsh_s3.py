"""
KMA API허브 AWS 시간통계 ``awsh.php`` (typ01) → S3 Parquet 적재.

URL 형식 (예시)::

  https://apihub.kma.go.kr/api/typ01/url/awsh.php?var=RN&tm=201508121500&help=1&authKey=...
  (API허브 활용신청 예시 URL과 동일; 인증키는 ``WEATHER_AWS_key``)

환경 변수 (프로젝트 루트 ``.env``):

  WEATHER_AWS_key   기상청 API허브 인증키 (필수)
  S3_BUCKET         대상 버킷
  AWS_REGION        (선택)
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY

S3 키::

  s3://{S3_BUCKET}/kma/raw/{year}/aws_awsh_1h/date={YYYY-MM-DD}/data.parquet

컬럼: ``datetime`` (KST naive hourly), ``stn_id``, ``var``, ``value``, ``date``.
``--filter-stations-csv`` 사용 시 해당 CSV **위에서 아래 행 순서**로 ``stn_id``를 정렬해 저장한다.
필터 없이 전국이면 ``stn_id`` 숫자 순으로 정렬한다 (문자열 오름차순 아님).
기본 ``var``는 **강수(RN)** 만 수집합니다. 다른 요소는 ``--vars`` 로 지정.

Usage::

  python src/ingest_kma_aws_awsh_s3.py --dry-run --max-days 1
  python src/ingest_kma_aws_awsh_s3.py --start 2023-03-01 --end 2023-03-03
  python src/ingest_kma_aws_awsh_s3.py --vars RN,TA  # 다른 요소가 필요할 때만
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
from pathlib import Path

import boto3
import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
AWSH_URL = "https://apihub.kma.go.kr/api/typ01/url/awsh.php"
# 강수만 필요할 때 기본값 (awsh.php ``var`` 코드)
DEFAULT_VARS = ("RN",)


def redact_secrets(msg: str, auth_key: str) -> str:
    """로그·예외 문자열에서 인증키 노출 방지."""
    out = re.sub(r"(?i)authKey=[^&\s#]+", "authKey=***", msg)
    if auth_key:
        out = out.replace(auth_key, "***")
    return out


def load_dotenv_root() -> None:
    load_dotenv(ROOT / ".env")


def make_s3():
    load_dotenv_root()
    bucket = (os.getenv("S3_BUCKET") or "").strip().strip('"')
    if not bucket:
        raise SystemExit("S3_BUCKET is empty in .env")
    s3 = boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "ap-southeast-2"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_key", "") or None,
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY") or None,
    )
    return s3, bucket


def decode_response(content: bytes) -> str:
    for enc in ("cp949", "euc-kr", "utf-8-sig", "utf-8"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def parse_awsh_table(text: str) -> pd.DataFrame:
    """typ01 ``awsh.php`` 본문(텍스트)에서 헤더·데이터 표를 추출."""
    if not text or not text.strip():
        return pd.DataFrame()
    lines = [ln.rstrip("\r") for ln in text.splitlines()]
    end_i = None
    for i, ln in enumerate(lines):
        if "7777END" in ln:
            end_i = i
            break
    if end_i is None:
        head = "\n".join(lines[:15])
        raise ValueError(f"응답에 7777END가 없습니다. 앞부분:\n{head}")

    block = lines[:end_i]
    header_i = None
    for i in range(len(block) - 1, -1, -1):
        ln = block[i]
        if ln.startswith("#") and "STN" in ln.upper():
            header_i = i
            break
    if header_i is None:
        raise ValueError("STN 이 포함된 헤더 줄을 찾지 못했습니다.")

    raw = block[header_i].strip().split()
    if raw and raw[0].startswith("#"):
        raw = raw[1:]
    col_line = _dedupe_columns(raw)

    data_start = header_i + 2
    rows: list[list[str]] = []
    for j in range(data_start, len(block)):
        ln = block[j].strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        if len(parts) < 2:
            continue
        if len(parts) < len(col_line):
            continue
        if len(parts) > len(col_line):
            parts = parts[: len(col_line)]
        rows.append(parts)

    if not rows:
        return pd.DataFrame(columns=col_line)
    return pd.DataFrame(rows, columns=col_line)


def _dedupe_columns(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for n in names:
        base = n.strip() or "col"
        k = base
        if k not in seen:
            seen[k] = 0
            out.append(k)
        else:
            seen[k] += 1
            out.append(f"{base}_{seen[k]}")
    return out


def awsh_to_long(df: pd.DataFrame, var: str) -> pd.DataFrame:
    """파싱 표를 ``datetime, stn_id, var, value`` long 포맷으로."""
    if df.empty:
        return pd.DataFrame(columns=["datetime", "stn_id", "var", "value"])
    cols = list(df.columns)
    dt_col = cols[0]
    stn_col = None
    for c in cols:
        if c.upper() in ("STN", "STN_ID"):
            stn_col = c
            break
    if stn_col is None:
        stn_col = cols[1]

    val_col = None
    vu = var.upper()
    for c in cols:
        if c.upper() == vu or c.upper().startswith(vu + "_"):
            val_col = c
            break
    if val_col is None:
        val_col = cols[-1]

    s_val = df[val_col].astype(str).str.strip()
    s_val = s_val.replace({"-": pd.NA, "": pd.NA, ".": pd.NA})

    out = pd.DataFrame(
        {
            "datetime": pd.to_datetime(df[dt_col], format="%Y%m%d%H%M", errors="coerce"),
            "stn_id": df[stn_col].astype(str).str.strip(),
            "var": vu,
            "value": pd.to_numeric(s_val, errors="coerce"),
        }
    )
    out = out.dropna(subset=["datetime"])
    out["datetime"] = out["datetime"].dt.floor("h")
    return out.drop_duplicates(subset=["datetime", "stn_id", "var"], keep="last")


def fetch_awsh(
    session: requests.Session,
    auth_key: str,
    var: str,
    tm: str,
    *,
    max_retries: int = 4,
    backoff_sec: float = 2.0,
    request_timeout: float | tuple[float, float] = (60.0, 300.0),
) -> pd.DataFrame:
    """5xx·429·일시적 네트워크 오류(타임아웃, 연결 끊김 등) 시 재시도."""
    params = {"var": var.strip().upper(), "tm": tm, "help": "1", "authKey": auth_key}
    n = max(1, int(max_retries))
    for attempt in range(n):
        try:
            r = session.get(AWSH_URL, params=params, timeout=request_timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 < n:
                    time.sleep(backoff_sec * (2**attempt))
                    continue
            r.raise_for_status()
            text = decode_response(r.content)
            if "error" in text[:200].lower() or "ERROR" in text[:500]:
                raise ValueError(f"API 오류 가능: 응답 앞부분 {text[:300]!r}")
            raw = parse_awsh_table(text)
            return awsh_to_long(raw, var)
        except ValueError:
            raise
        except requests.RequestException as e:
            resp = getattr(e, "response", None)
            code = resp.status_code if resp is not None else None
            transient_http = code in (429, 500, 502, 503, 504)
            transient_net = isinstance(
                e,
                (
                    requests.Timeout,
                    requests.ConnectionError,
                    requests.exceptions.ChunkedEncodingError,
                ),
            )
            if (transient_http or transient_net) and attempt + 1 < n:
                time.sleep(backoff_sec * (2**attempt))
                continue
            raise
    raise RuntimeError("fetch_awsh: exhausted retries without return")


def load_station_filter_ordered(path: Path | None) -> tuple[set[str] | None, list[str] | None]:
    """CSV에서 지점 필터 집합과 **행 나타난 순서**(중복 제거)를 함께 반환."""
    if path is None:
        return None, None
    df = pd.read_csv(path, dtype=str)
    col = None
    for c in ("stn_id_aws", "stn_id", "STN", "stn"):
        if c in df.columns:
            col = c
            break
    if col is None:
        raise SystemExit(f"CSV에 stn_id_aws 등 지점 컬럼이 없습니다: {path}")
    s = df[col].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    ordered: list[str] = []
    seen: set[str] = set()
    for v in s:
        if pd.isna(v) or str(v).strip() == "" or str(v).lower() == "nan":
            continue
        sid = str(v).strip()
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    return seen, ordered


def sort_daily_aws_frame(df: pd.DataFrame, stn_order: list[str] | None) -> pd.DataFrame:
    """지점 정렬: ``stn_order`` 가 있으면 CSV 원본 순서, 없으면 ``stn_id`` 숫자 순."""
    if df.empty:
        return df
    out = df.copy()
    if stn_order:
        rank = {sid: i for i, sid in enumerate(stn_order)}
        out["_stn_ord"] = out["stn_id"].astype(str).map(rank)
        max_r = float(len(stn_order))
        out["_stn_ord"] = out["_stn_ord"].fillna(max_r + 1e6)
        out = out.sort_values(["_stn_ord", "datetime", "var"]).drop(columns=["_stn_ord"])
    else:
        out["_nstn"] = pd.to_numeric(out["stn_id"], errors="coerce")
        out = out.sort_values(["_nstn", "datetime", "var"], na_position="last").drop(
            columns=["_nstn"]
        )
    return out.reset_index(drop=True)


def upload_day(s3, bucket: str, year: int, day: pd.Timestamp, frame: pd.DataFrame, dry_run: bool) -> None:
    d_iso = day.date().isoformat()
    key = f"kma/raw/{year}/aws_awsh_1h/date={d_iso}/data.parquet"
    buf = io.BytesIO()
    frame.to_parquet(buf, index=False)
    body = buf.getvalue()
    if dry_run:
        print(f"  [dry-run] would put {len(frame):6d} rows -> s3://{bucket}/{key} ({len(body)} bytes)")
        return
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/octet-stream")
    print(f"  put {len(frame):6d} rows -> s3://{bucket}/{key}")


def iter_days(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    out: list[pd.Timestamp] = []
    cur = start.normalize()
    end_n = end.normalize()
    while cur <= end_n:
        out.append(cur)
        cur += pd.Timedelta(days=1)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="KMA awsh.php AWS 1H → S3 parquet (long)")
    p.add_argument("--start", type=str, default="2023-03-01")
    p.add_argument("--end", type=str, default="2025-10-31")
    p.add_argument(
        "--vars",
        type=str,
        default=",".join(DEFAULT_VARS),
        help="쉼표 구분 요소 코드 (기본: RN=강수만)",
    )
    p.add_argument("--sleep", type=float, default=0.15, help="요청 간 간격(초)")
    p.add_argument(
        "--retries",
        type=int,
        default=6,
        help="실패 시 재시도 횟수(5xx·429·Read timeout 등)",
    )
    p.add_argument("--backoff", type=float, default=2.0, help="재시도 초기 대기(초), 지수 증가")
    p.add_argument(
        "--request-timeout",
        type=float,
        default=300.0,
        help="HTTP 읽기 타임아웃(초). connect는 min(60, 이 값)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-days", type=int, default=None, help="테스트: 처음 N일만")
    p.add_argument(
        "--filter-stations-csv",
        type=str,
        default=None,
        help="지정 CSV의 stn_id_aws 등으로 관측소 필터; **CSV 행 순서**로 일별 Parquet 내 stn_id 정렬",
    )
    args = p.parse_args()

    load_dotenv_root()
    auth = (os.getenv("WEATHER_AWS_key") or "").strip().strip('"')
    if not auth:
        print("WEATHER_AWS_key missing or empty in .env", file=sys.stderr)
        return 1

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    if end < start:
        print("--end must be >= --start", file=sys.stderr)
        return 1

    vars_list = [v.strip().upper() for v in args.vars.split(",") if v.strip()]
    if not vars_list:
        print("No variables in --vars", file=sys.stderr)
        return 1

    stn_filter, stn_order = load_station_filter_ordered(
        Path(args.filter_stations_csv) if args.filter_stations_csv else None
    )

    days = iter_days(start, end)
    if args.max_days is not None:
        days = days[: int(args.max_days)]

    if not args.dry_run:
        s3, bucket = make_s3()
    else:
        s3, bucket = None, (os.getenv("S3_BUCKET") or "DRY_RUN_BUCKET")

    session = requests.Session()
    session.headers.update({"User-Agent": "FloodAX-ingest-kma-aws/1.0"})

    read_to = max(30.0, float(args.request_timeout))
    connect_to = min(60.0, read_to)
    request_timeout = (connect_to, read_to)

    if stn_order is not None:
        print(
            f"Station filter: {args.filter_stations_csv} | n_unique={len(stn_order)} "
            f"(Parquet 행 정렬: 이 CSV 행 순서)",
            flush=True,
        )

    print(
        f"Days: {len(days)} | vars={vars_list} | "
        f"requests/day≈{24 * len(vars_list)} | dry_run={args.dry_run}",
        flush=True,
    )

    for di, day in enumerate(days):
        day_frames: list[pd.DataFrame] = []
        dstr = day.strftime("%Y%m%d")
        for h in range(24):
            tm = f"{dstr}{h:02d}00"
            for var in vars_list:
                try:
                    part = fetch_awsh(
                        session,
                        auth,
                        var,
                        tm,
                        max_retries=args.retries,
                        backoff_sec=args.backoff,
                        request_timeout=request_timeout,
                    )
                    if stn_filter is not None and not part.empty:
                        part = part[part["stn_id"].isin(stn_filter)].copy()
                    if not part.empty:
                        day_frames.append(part)
                except Exception as e:
                    safe = redact_secrets(str(e), auth)
                    print(f"  [fail] {day.date()} {tm} var={var}: {safe}", file=sys.stderr, flush=True)
                time.sleep(max(0.0, args.sleep))

        if not day_frames:
            print(f"  [skip] {day.date()} no rows", flush=True)
            continue

        big = pd.concat(day_frames, ignore_index=True)
        big = big.drop_duplicates(subset=["datetime", "stn_id", "var"], keep="last")
        big = sort_daily_aws_frame(big, stn_order)
        big["date"] = day.date().isoformat()

        upload_day(s3, bucket, day.year, day, big, args.dry_run)
        print(f"  [{di+1}/{len(days)}] {day.date()} rows={len(big)}", flush=True)

    print("Done.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
