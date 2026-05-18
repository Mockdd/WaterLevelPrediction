# FloodAX - 홍수대응 의사결정 지원 플랫폼 (Frontend)

한강 유역 291개 관측소의 TFT 기반 수위 예측 결과를 실시간으로 시각화하는 관제 대시보드입니다.

## 주요 기능

- **실시간 관제 지도**: 291개 관측소 신호등 마커 (경보/주의/정상/데이터없음)
- **예측 수위 차트**: h1~h6 (1~6시간 후) 예측 결과 및 신뢰구간 시각화
- **과거 24시간 수위**: 실측 수위 시계열 비교
- **대응 우선순위 패널**: 경보·주의 관측소 우선 정렬, 관측소 검색
- **수동 갱신**: 집중호우 상황에서 즉시 예측 재계산
- **반응형 UI**: PC 3분할 구조 및 모바일 하단 시트 지원

## 기술 스택

- React 19 (Create React App)
- Leaflet + react-leaflet (지도)
- Vworld WMTS 타일 (국토지리정보원)
- Recharts (차트)
- axios (HTTP)

## 시작하기

### 환경 변수 설정

프로젝트 루트에 `.env.local` 파일 생성:

```
REACT_APP_VWORLD_KEY=발급받은_Vworld_API_키
REACT_APP_API_BASE_URL=https://백엔드_ngrok_URL
```

### 설치 및 실행

```bash
npm install
npm start
```

브라우저에서 [http://localhost:3000](http://localhost:3000) 접속

## 폴더 구조

```
src/
├── App.js                  # 루트 컴포넌트, 데이터 fetch
├── api/
│   └── index.js            # API 클라이언트
├── components/
│   ├── Header/             # 헤더, 실시간 시계, 갱신 버튼
│   ├── Map/                # Leaflet 지도
│   ├── Marker/             # 관측소 신호등 마커
│   ├── Panel/              # 대응 우선순위 패널, 상세 정보 창
│   ├── Chart/              # 예측/관측 수위 차트
│   └── Timeline/           # 줌 확대 시 타임라인 뷰
├── constants/
│   └── colors.js           # 색상 시스템
└── hooks/
```

## API 연동

백엔드 API는 `setupProxy.js`를 통해 프록시 처리됩니다.
ngrok URL이 바뀐 경우 `.env.local`의 `REACT_APP_API_BASE_URL`을 업데이트하세요.

| 엔드포인트 | 설명 |
|---|---|
| `GET /stations/with-status` | 전체 관측소 + 예측 결과 |
| `GET /stations/{id}/predictions` | 특정 관측소 예측 |
| `GET /stations/{id}/observations` | 과거 24시간 실측 수위 |
| `GET /alerts` | 경보·주의 관측소 목록 |
| `POST /admin/refresh` | 수동 데이터 갱신 (1~3분 소요) |

