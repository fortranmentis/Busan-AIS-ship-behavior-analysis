# 부산항 AIS 선박 행위 분석

**한국어** | [English](README.md)

> AIS 데이터 기반 부산항 선박 행위 식별 및 타임랩스 시각화

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![GeoPandas](https://img.shields.io/badge/GeoPandas-geospatial-139C5A)
![Matplotlib](https://img.shields.io/badge/Matplotlib-rendering-11557C)
![FFmpeg](https://img.shields.io/badge/FFmpeg-encoding-007808?logo=ffmpeg&logoColor=white)

부산항 AIS(선박자동식별장치) 항적 데이터를 분석해 선박의 행위를 식별하고, 그 결과를 Esri 위성영상 위에 1초 간격 프레임의 타임랩스 영상으로 렌더링하는 프로젝트입니다. 두 가지 분석 파이프라인을 제공합니다.

- **항행 상태 식별** — 고시 항로·정박지 폴리곤 대비 정박, 정류, 항로이탈 판정
- **운항 행위 식별** — 접안/하역, 조업, 예인작업 판정

## 데모

### 정박 / 정류 / 항로이탈 (`3docked_video_demo.py`)

매초 각 선박을 고시 항로·정박지 폴리곤과 대조해 분류합니다. 채운 원은 정박·정지 선박, 노란 테두리 원은 정류, 빨간 원은 방금 고시 항로를 벗어난 선박을 나타냅니다. 항해 중인 선박에는 SOG(대지속력) 라벨과 COG(대지침로) 화살표가 표시됩니다.

![정박, 정류, 항로이탈 데모](assets/ship_movement_DOCK_SPEED_demo2.gif)

### 운항 행위 (`2acting_video2_demo.py`)

접안 상태는 선박별로 지속되는 상태로 추적하고, 조업은 선종과 속력으로, 예인작업은 예인선의 속력과 인접 선박 존재 여부(KD-tree 근접 탐색)로 추정합니다.

![운항 행위 데모](assets/ship_movement_act2_demo.gif)

원본 해상도 MP4는 [`assets/`](assets/)에 있습니다.

## 행위 판정 규칙

### `3docked_video_demo.py` — 항행 상태

| 상태 | 조건 | 표시 |
|---|---|---|
| 정박 (정박지 내) | 고시 정박지 내 ∧ SOG ≤ 2.1노트 | 채운 원 |
| 정지 (정박지 외) | SOG ≤ 2.1노트 ∧ 200초 내 이동 ≤ 10 m 또는 침로 변화 ≥ 10° | 채운 원 |
| 정류 | SOG ≤ 2.1노트이나 정지로 보기 어려움 | 노란 테두리 원 |
| 항로이탈 | 직전 위치는 고시 항로 내 ∧ 현재 위치는 밖 (600초 이내) | 빨간 원 |
| 항해 중 | SOG > 2.1노트 | 방향 마커 + SOG 라벨 + COG 화살표 |

### `2acting_video2_demo.py` — 운항 행위

| 행위 | 조건 | 표시 |
|---|---|---|
| 접안 / 하역 | 해안선 100 m 버퍼 내 ∧ SOG ≤ 2.1노트 (SOG > 2.1노트가 될 때까지 상태 유지) | 채운 원 |
| 조업 | 어선 ∧ 0 ≤ SOG ≤ 5노트 | 파란 테두리 원 |
| 예인작업 | 예인선 ∧ 0.5 ≤ SOG ≤ 8노트 ∧ 약 570 m 내 다른 선박 존재 | 노란 테두리 원 |

### 선종별 색상 (AIS 선종코드)

| 코드 | 선종 | 색상 |
|---|---|---|
| 30 | 어선 | blue |
| 31, 32, 50, 52 | 견인, 도선, 예인선 | yellow |
| 36, 37 | 요트, 유람선 | lime |
| 40–49, 60–69 | 여객, 쾌속선 | pink |
| 70–79 | 화물선 | orange |
| 80–89 | 유조선 | red |
| 그 외 | 미분류 | gray |

## 동작 원리

```
AIS CSV ──► 전처리 (메인 프로세스에서 1회)          ──► 병렬 렌더링        ──► FFmpeg
            • 인코딩 폴백 로드, EPSG:3857 변환        • 워커 풀 1개,        • h264_nvenc
            • point-in-polygon 벡터 연산(sjoin)         프레임당 그리기만     실패 시
            • 선박별 시간 정렬 그룹화                  • 프레임 즉시           libx264 폴백
            • 접안 상태 결정적 계산                      디스크 저장
            • 위성 배스맵 1회 캐싱
```

핵심 설계:

- **공간 연산 전체 사전 계산.** 항로/정박지/항만 소속 여부를 AIS 좌표별로 공간 조인(sjoin) 1회로 계산하므로, 프레임 렌더 루프에서는 지오메트리 연산이 전혀 없습니다.
- **선박별 이진 탐색.** 항적을 MMSI별로 시간 정렬해 두고 각 프레임에서 `np.searchsorted`로 최신 위치를 찾습니다. 전체 데이터 필터링을 반복하지 않습니다.
- **Windows 안전 멀티프로세싱.** 무거운 데이터 로드는 메인 프로세스에서 1회만 수행하고 풀 initializer로 워커에 전달합니다. `spawn` 방식에서 워커마다 데이터가 재로딩되지 않습니다.
- **배스맵 1회 다운로드.** 위성 배스맵을 `contextily.bounds2img`로 한 번만 받아 모든 프레임에서 재사용합니다.
- **결정적 상태 계산.** 접안 상태는 선박별 시계열에서 forward-fill 상태기계로 계산하므로 프레임 처리 순서와 무관하게 재현 가능합니다.

## 요구 사항

- Python ≥ 3.10, 패키지: `pandas`, `numpy`, `geopandas`, `shapely`, `matplotlib`, `contextily`, `scipy`, `pillow`, `tqdm`, `psutil`
- FFmpeg (`PATH` 또는 활성 conda 환경 내)
- 한글 폰트 (나눔스퀘어 또는 맑은 고딕 자동 탐지)

```bash
conda create -n ais python=3.10 geopandas contextily scipy pillow tqdm psutil ffmpeg -c conda-forge
conda activate ais
```

## 사용법

```bash
# 정박 / 정류 / 항로이탈 영상
python 3docked_video_demo.py --frames 1000 --output dock_demo.mp4 --fps 30

# 운항 행위 영상
python 2acting_video2_demo.py --frames 1000 --output activity_demo.mp4 --fps 30
```

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--frames` | 100 | 렌더링할 1초 간격 프레임 수 |
| `--output` | 스크립트별 상이 | 출력 MP4 파일명 |
| `--fps` | 30 | 출력 영상 프레임레이트 |

## 입력 데이터

AIS 데이터와 고시 shapefile은 이 저장소에 **포함되어 있지 않습니다**. 스크립트는 작업 디렉터리에서 다음 파일을 찾습니다.

| 파일 | 내용 | 필수 컬럼 / 비고 |
|---|---|---|
| `busan_AIS2.csv` | AIS 동적 정보 | `MMSI`, `일시`, `경도`/`위도`(WGS 84), `SOG`, `COG`, `Heading` |
| `Static.csv` | 선박 정적 정보 | `MMSI`, `선종코드` |
| `제1광역항로_5.shp`, `제2광역항로_5.shp`, `지선항로_5.shp`, `고시항로_2023.shp` | 고시 항로 | EPSG:4326 |
| `고시정박지_2023.shp`, `정박지(KOMC)_2023_병합.shp`, `무역항_2024.shp` | 정박지 / 무역항 | EPSG:4326, `3docked_video_demo.py`에서 사용 |
| `100m버퍼3.shp` | 해안선 100 m 버퍼 | EPSG:4326, `2acting_video2_demo.py`에서 사용 |

## 저장소 구조

```
├── ais_common.py             # 공통 라이브러리: 데이터 로드, 공간 조인, 마커,
│                             #   범례, 침로 유틸, FFmpeg 러너
├── 2acting_video2_demo.py    # 운항 행위 파이프라인 (접안 / 조업 / 예인)
├── 3docked_video_demo.py     # 항행 상태 파이프라인 (정박 / 정류 / 항로이탈)
└── assets/                   # 데모 GIF 및 원본 해상도 MP4
```

## 참고

- 배스맵 타일: [contextily](https://github.com/geopandas/contextily)를 통한 Esri World Imagery
- 판정 임계값(속력, 거리, 시간 창)은 각 스크립트 상단에 상수로 정의되어 있어 다른 항만이나 데이터셋에 맞게 조정할 수 있습니다.
