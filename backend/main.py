"""
한강홍수통제소 수위 예측 API (B 백엔드)

실행:
    cd C:\\Users\\안서영\\Desktop\\hangang_backend
    uvicorn main:app --reload --port 8000

문서:
    http://localhost:8000/docs

엔드포인트:
    GET  /                                  서비스 정보
    GET  /stations                          관측소 목록 (region 필터)
    GET  /stations/with-status              지도용 통합 (관측소+예측+신호등)
    GET  /alerts                            위험 관측소 리스트
    GET  /stations/{id_or_name}             관측소 상세 (ID/이름 검색)
    GET  /stations/{id}/observations        과거 수위 (그래프용)
    GET  /stations/{id}/predictions         예측 결과 (1~6h + 신호등)
    GET  /stations/{id}/upstream            상류 관측소 (propagation)
    POST /admin/refresh                     수동 새로고침 (1~3분 소요)
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from typing import Optional
import pandas as pd
from sqlalchemy import text
from database import engine

app = FastAPI(title="한강홍수통제소 수위 예측 API", version="1.1")

# CORS 세팅
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ────────────────────────────────────────────────────
# 헬퍼 함수
# ────────────────────────────────────────────────────
def clean_df(df):
    """NaN → None 변환 (JSON 응답 깨짐 방지)"""
    if df.empty:
        return df
    return df.astype(object).where(pd.notna(df), None)


def compute_status(predicted_wl, alert_level, warning_level):
    """
    예측 수위와 임계수위 비교해 신호등 색 결정
    
    green:  predicted < alert
    yellow: alert <= predicted < warning
    red:    predicted >= warning
    gray:   비교 불가 (임계수위 또는 예측값 None)
    """
    if predicted_wl is None or alert_level is None or warning_level is None:
        return "gray"
    
    if predicted_wl >= warning_level:
        return "red"
    elif predicted_wl >= alert_level:
        return "yellow"
    else:
        return "green"


def compute_statuses_dict(predictions_dict, alert_level, warning_level):
    """
    predictions dict의 각 horizon에 대해 신호등 계산
    
    입력: {"h1": {"predicted": 2.5, ...}, "h2": {...}, ...}
    출력: {"h1": "green", "h2": "yellow", ...}
    """
    if isinstance(predictions_dict, str):
        import json
        predictions_dict = json.loads(predictions_dict)
    statuses = {}
    for horizon, value in predictions_dict.items():
        if isinstance(value, dict):
            wl = value.get("predicted")
        else:
            wl = value  # 혹시 단순 숫자로 저장됐을 경우 대비
        statuses[horizon] = compute_status(wl, alert_level, warning_level)
    return statuses


def worst_status(statuses_dict):
    """
    statuses 중 가장 위험한 색 반환 (지도 핀 색용)
    red > yellow > green > gray
    """
    if "red" in statuses_dict.values():
        return "red"
    if "yellow" in statuses_dict.values():
        return "yellow"
    if "green" in statuses_dict.values():
        return "green"
    return "gray"


# ────────────────────────────────────────────────────
# 0. 루트
# ────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "service": "한강홍수통제소 수위 예측 API",
        "version": "1.1",
        "docs": "/docs",
    }


# ────────────────────────────────────────────────────
# 1. 전체 관측소 목록 (지도용)
# ────────────────────────────────────────────────────
@app.get("/stations")
def get_stations(region: Optional[str] = Query(None, description="한강 / 안성천 / 한강서해 / 한강동해")):
    """관측소 목록. region 파라미터로 권역 필터링 가능."""
    if region:
        df = pd.read_sql(
            text("SELECT * FROM stations WHERE region = :r ORDER BY region, station_id"),
            engine,
            params={"r": region}
        )
    else:
        df = pd.read_sql(
            "SELECT * FROM stations ORDER BY region, station_id",
            engine
        )
    df = clean_df(df)
    
    return {
        "count": len(df),
        "stations": df.to_dict('records')
    }


# ────────────────────────────────────────────────────
# 2. 지도용 통합 API ⭐ (관측소 + 최신 예측 + 신호등 한 번에)
# ────────────────────────────────────────────────────
@app.get("/stations/with-status")
def get_stations_with_status(region: Optional[str] = Query(None)):
    """
    지도 첫 로딩용. 모든 관측소 + 최신 예측 + 신호등을 한 번에 제공.
    C가 핀 그릴 때 1번 호출로 끝남.
    """
    # 1. 관측소
    if region:
        df_st = pd.read_sql(
            text("SELECT * FROM stations WHERE region = :r"),
            engine, params={"r": region}
        )
    else:
        df_st = pd.read_sql("SELECT * FROM stations", engine)
    
    # 2. 각 관측소의 최신 예측 1건 (DISTINCT ON으로 한 번에)
    df_pred = pd.read_sql(
        """
        SELECT DISTINCT ON (station_id)
            station_id, predicted_at, predictions
        FROM predictions
        ORDER BY station_id, predicted_at DESC
        """,
        engine
    )
    
    # 3. 합치기
    df_st = clean_df(df_st)
    df_pred = clean_df(df_pred)
    pred_map = {row['station_id']: row for _, row in df_pred.iterrows()}
    
    results = []
    for _, st in df_st.iterrows():
        sid = st['station_id']
        alert = st['alert_level']
        warning = st['warning_level']
        
        entry = {
            "station_id": sid,
            "name": st['name'],
            "region": st['region'],
            "lat": st['lat'],
            "lng": st['lng'],
            "alert_level": alert,
            "warning_level": warning,
        }
        
        if sid in pred_map:
            pred_row = pred_map[sid]
            predictions = pred_row['predictions']
            statuses = compute_statuses_dict(predictions, alert, warning)
            entry["predicted_at"] = str(pred_row['predicted_at'])
            entry["predictions"] = predictions
            entry["statuses"] = statuses
            entry["pin_status"] = worst_status(statuses)
        else:
            entry["predicted_at"] = None
            entry["predictions"] = None
            entry["statuses"] = None
            entry["pin_status"] = "gray"
        
        results.append(entry)
    
    return {
        "count": len(results),
        "stations": results
    }


# ────────────────────────────────────────────────────
# 3. 위험 관측소 리스트 (대응 우선순위)
# ────────────────────────────────────────────────────
@app.get("/alerts")
def get_alerts():
    """
    statuses에 red/yellow 있는 관측소만 필터.
    red 먼저, yellow 다음 순서.
    """
    # 모든 관측소 + 최신 예측
    df_st = pd.read_sql("SELECT * FROM stations", engine)
    df_pred = pd.read_sql(
        """
        SELECT DISTINCT ON (station_id)
            station_id, predicted_at, predictions
        FROM predictions
        ORDER BY station_id, predicted_at DESC
        """,
        engine
    )
    
    df_st = clean_df(df_st)
    df_pred = clean_df(df_pred)
    
    if len(df_pred) == 0:
        return {"count": 0, "alerts": []}
    
    pred_map = {row['station_id']: row for _, row in df_pred.iterrows()}
    
    red_list = []
    yellow_list = []
    
    for _, st in df_st.iterrows():
        sid = st['station_id']
        if sid not in pred_map:
            continue
        
        pred_row = pred_map[sid]
        statuses = compute_statuses_dict(pred_row['predictions'], st['alert_level'], st['warning_level'])
        pin = worst_status(statuses)
        
        if pin not in ("red", "yellow"):
            continue
        
        entry = {
            "station_id": sid,
            "name": st['name'],
            "region": st['region'],
            "lat": st['lat'],
            "lng": st['lng'],
            "alert_level": st['alert_level'],
            "warning_level": st['warning_level'],
            "predicted_at": str(pred_row['predicted_at']),
            "predictions": pred_row['predictions'],
            "statuses": statuses,
            "pin_status": pin,
        }
        
        if pin == "red":
            red_list.append(entry)
        else:
            yellow_list.append(entry)
    
    alerts = red_list + yellow_list
    return {
        "count": len(alerts),
        "alerts": alerts
    }


# ────────────────────────────────────────────────────
# 4. 특정 관측소 상세 (ID 또는 이름)
# ────────────────────────────────────────────────────
@app.get("/stations/{station_id_or_name}")
def get_station_detail(station_id_or_name: str):
    """
    관측소 상세. station_id(숫자) 또는 name(예: 송정교)으로 검색.
    """
    df = pd.read_sql(
        text("""
            SELECT * FROM stations
            WHERE station_id = :q
               OR name LIKE :like_q
            LIMIT 1
        """),
        engine,
        params={"q": station_id_or_name, "like_q": f"%{station_id_or_name}%"}
    )
    
    if len(df) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"관측소 '{station_id_or_name}'를 찾을 수 없습니다."
        )
    
    df = clean_df(df)
    return df.to_dict('records')[0]


# ────────────────────────────────────────────────────
# 5. 과거 수위 (그래프용)
# ────────────────────────────────────────────────────
@app.get("/stations/{station_id}/observations")
def get_observations(
    station_id: str,
    hours: int = Query(24, ge=1, le=168, description="최근 N시간 (1~168)")
):
    """
    최근 N시간 수위 데이터. 오래된 순 → 최신 순으로 정렬.
    """
    df = pd.read_sql(
        text("""
            SELECT datetime, water_level
            FROM observations
            WHERE station_id = :sid
            ORDER BY datetime DESC
            LIMIT :h
        """),
        engine,
        params={"sid": station_id, "h": hours}
    )
    df = df.sort_values('datetime')
    df = clean_df(df)
    
    return {
        "station_id": station_id,
        "count": len(df),
        "observations": df.to_dict('records')
    }


# ────────────────────────────────────────────────────
# 6. 예측 결과 (1~6h, 신호등 포함)
# ────────────────────────────────────────────────────
@app.get("/stations/{station_id}/predictions")
def get_predictions(station_id: str):
    """
    특정 관측소의 최신 예측 + 신호등.
    신호등은 B에서 stations의 alert/warning_level 기준으로 계산.
    """
    # 1. 예측
    df_pred = pd.read_sql(
        text("""
            SELECT predicted_at, predictions
            FROM predictions
            WHERE station_id = :sid
            ORDER BY predicted_at DESC
            LIMIT 1
        """),
        engine,
        params={"sid": station_id}
    )
    
    if len(df_pred) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"관측소 '{station_id}'의 예측 데이터가 없습니다."
        )
    
    df_pred = clean_df(df_pred)
    row = df_pred.iloc[0]
    predictions = row['predictions']
    
    # 2. 임계수위
    df_st = pd.read_sql(
        text("SELECT alert_level, warning_level FROM stations WHERE station_id = :sid"),
        engine,
        params={"sid": station_id}
    )
    
    if len(df_st) == 0:
        alert_level = None
        warning_level = None
    else:
        df_st = clean_df(df_st)
        alert_level = df_st.iloc[0]['alert_level']
        warning_level = df_st.iloc[0]['warning_level']
    
    # 3. 신호등 계산
    statuses = compute_statuses_dict(predictions, alert_level, warning_level)
    
    return {
        "station_id": station_id,
        "predicted_at": str(row['predicted_at']),
        "predictions": predictions,
        "statuses": statuses,
    }


# ────────────────────────────────────────────────────
# 7. 상류 관측소 (propagation)
# ────────────────────────────────────────────────────
@app.get("/stations/{station_id}/upstream")
def get_upstream(station_id: str):
    """
    이 관측소에 영향 주는 상류 관측소 (시차 포함).
    """
    df = pd.read_sql(
        text("""
            SELECT
                p.obscd_a as upstream_id,
                s.name as upstream_name,
                p.mean_lag_h,
                p.n_events,
                p.grade
            FROM propagation p
            LEFT JOIN stations s ON p.obscd_a = s.station_id
            WHERE p.obscd_b = :sid
            ORDER BY p.mean_lag_h
        """),
        engine,
        params={"sid": station_id}
    )
    df = clean_df(df)
    
    return {
        "station_id": station_id,
        "count": len(df),
        "upstream": df.to_dict('records')
    }


# ────────────────────────────────────────────────────
# 8. 수동 새로고침 (1~3분 소요)
# ────────────────────────────────────────────────────
@app.post("/admin/refresh")
def admin_refresh():
    """
    수위 + 강수 + 예측 즉시 갱신. 발표 시연용.
    ⚠️ 1~3분 소요됨 (291개 수위 + 277개 강수 API 호출).
    호출 측 fetch는 타임아웃 충분히 길게 (180000ms 권장).
    """
    try:
        from scheduler import run_once
        run_once()
        return {
            "status": "ok",
            "message": "Refresh completed",
            "updated_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
