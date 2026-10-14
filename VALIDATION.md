# Validation record

[English README](README.md) · [한국어 README](README.ko.md)

**Audit date:** 2026-10-14 (Asia/Seoul). **Scope:** local working-folder inputs and the three public demo modules. Input inspection, source review, and media readability are distinguished from end-to-end execution and classification validity.

## Completed checks

| Evidence | Method | Result |
|---|---|---|
| Dynamic CSV | Full Python `csv.DictReader` scan; CP949 decode; empty fields; distinct MMSIs; timestamp string extrema | `busan_AIS2.csv`: 787,591 rows, 1,070 MMSIs; `2022-09-01 00:00:00`–`2022-09-01 05:59:58` |
| Dynamic schema | Header and empty-field counts | `MMSI`, `일시`, `위도`, `경도`, `SOG`, `COG`, `Heading`; no empty fields |
| Coordinates | Numeric conversion; longitude `[-180, 180]`, latitude `[-90, 90]` | Zero failures; no regional plausibility, land/sea, or jump checks |
| Speed | Numeric, finite, nonnegative SOG | Zero failures; excessive positive speeds/AIS sentinels not separately screened |
| Heading sentinel | Count literal `511` or `511.0` | 455,162 rows, 57.79% |
| Static CSV | Full CP949 scan; MMSI uniqueness; missing fields | 55,630 rows, 55,630 MMSIs, zero duplicate MMSI rows; 318 empty ship-type fields |
| Metadata coverage | Dynamic identifiers minus static identifiers | 13 absent; 1,057 matched (98.79%). Matching does not guarantee valid ship type |
| Spatial files | Companion existence; SHP/DBF binary headers; `.prj` text | Eight required sets complete and declared WGS 84 geographic |
| Source syntax | `ast.parse` on UTF-8 source | Three public Python modules passed |
| GIF readability | Pillow decoded every frame; summed frame durations | Both previews: 720 × 480, 167 frames, 16.7 seconds |

Timestamp extrema used the supplied sortable strings, not a full parsing/timezone audit. Empty-field counts do not detect sentinel codes or semantically invalid values. These findings apply to the local files, which are excluded from the public repository.

### Spatial inventory

Counts below are DBF header record counts, not geometry-validity results or counts of individual polygon parts.

| Layer | Records | Use |
|---|---:|---|
| `제1광역항로_5.shp` | 1 | Both: routes |
| `제2광역항로_5.shp` | 9 | Both: routes |
| `지선항로_5.shp` | 32 | Both: routes |
| `고시항로_2023.shp` | 144 | Both: routes |
| `고시정박지_2023.shp` | 149 | Navigation: `in_anchorage` |
| `정박지(KOMC)_2023_병합.shp` | 245 | Navigation: `in_anchorage` |
| `무역항_2024.shp` | 35 | Navigation: `in_anchorage` |
| `100m버퍼3.shp` | 2,467 | Activity: `in_port` |

The loader forcibly assigns EPSG:4326 before projecting. Required local inputs declare WGS 84, but topology, overlap, source attribution, validity dates, and buffer width were not checked. Alternative local `100m버퍼2.shp` declares WGS 84 / UTM Zone 52N and is not used by the public demos.

### Source-review findings

- Latest fixes are held without interpolation or age limits.
- `Heading=511` is not excluded from orientation or heading-change calculations.
- Navigation `in_anchorage` includes trade-port polygons.
- Loitering requires a previous fix within 200 seconds; fairway exit uses a strict `< 600`-second gap.
- Berthing takes priority and persists until SOG exceeds 2.1 knots.
- Towing-related candidates use color group yellow: codes `31`, `32`, `50`, `52`.
- Proximity is approximately 567.7 EPSG:3857 units, not a calibrated ground distance.
- Demo media were not regenerated or matched to current source parameters.

## Not performed

| Validation layer | Status / reason |
|---|---|
| Imports and complete execution | Not run; inspected environments lack required geospatial/rendering packages; FFmpeg not on `PATH` |
| New encoding, codec fallback and GPU execution | Not run |
| Existing MP4 decode, dimensions and timing | Not checked; file presence/link targets inspected |
| Complete spatial joins and polygon topology | Not run |
| Buffer width / ground-distance calibration | Not checked |
| Dynamic duplicates, reporting gaps and jump outliers | Not quantified |
| Precision/recall and event correctness | Not measured; independent event labels not supplied |
| Cross-platform execution, memory and runtime | Not benchmarked |

## Reproduction checks

Run these locally after installing the README environment and supplying the private inputs. They are proposed checks, not previously passed execution results.

### Syntax and CLI

```bash
python -m py_compile ais_common.py 2acting_video2_demo.py 3docked_video_demo.py
python 3docked_video_demo.py --help
python 2acting_video2_demo.py --help
```

### Input profile

Save this snippet as a local Python file beside the inputs. It prints aggregates without vessel identifiers or trajectories.

```python
import pandas as pd

dynamic = pd.read_csv('busan_AIS2.csv', encoding='cp949', dtype={'MMSI': 'string'})
static = pd.read_csv('Static.csv', encoding='cp949', dtype={'MMSI': 'string'})
timestamps = pd.to_datetime(dynamic['일시'], format='%Y-%m-%d %H:%M:%S', errors='raise')
print('rows:', len(dynamic), 'MMSIs:', dynamic['MMSI'].nunique())
print('time range:', timestamps.min(), timestamps.max())
print('Heading 511:', pd.to_numeric(dynamic['Heading'], errors='coerce').eq(511).sum())
print('missing:', dynamic.isna().sum().to_dict())
print('duplicate MMSI/timestamp rows:', dynamic.duplicated(['MMSI', '일시']).sum())
print('static duplicate MMSIs:', static['MMSI'].duplicated().sum())
print('unmatched dynamic MMSIs:', len(set(dynamic['MMSI']) - set(static['MMSI'])))
```

This adds parsed timestamp validation and duplicate-time checks beyond the completed audit. Pandas missing-value interpretation can differ from empty-string counts.

### Rendering and encoding

```bash
python 3docked_video_demo.py --frames 10 --fps 30 --output navigation_check.mp4
python 2acting_video2_demo.py --frames 10 --fps 30 --output activity_check.mp4
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,nb_frames,r_frame_rate -of json navigation_check.mp4
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,nb_frames,r_frame_rate -of json activity_check.mp4
```

Given at least ten seconds of input, expect ten new H.264 frames at 3000 × 2000 and 30 FPS. Review orientation, legends, boundaries, and stale fixes visually. Successful encoding does not establish label accuracy.

## Evaluation before analytical claims

Define event labels and review windows first. Treat anchoring, berthing, cargo handling, fishing, and towing as distinct hypotheses. Include vessel class, reporting gaps, missing directions, boundary cases, and different dates/ports. Use independent reference evidence, document adjudication, and report per-class errors rather than inferring accuracy from an animation.

## 한국어 검증 요약

2026-10-14 로컬 입력 전수 읽기, 공간 파일 헤더·동반 파일 확인, Python 문법 검사, GIF 전체 프레임 디코딩을 수행했습니다. AIS는 787,591행·1,070개 MMSI이며 `Heading=511`은 455,162행(57.79%)입니다. 정적 자료는 55,630행으로 MMSI 중복은 없고 동적 MMSI 13개가 미결합입니다. 필수 공간 레이어 8개는 동반 파일과 WGS 84 선언을 확인했습니다.

전체 영상 재생성, 기존 MP4 디코딩, 공간 조인·위상·버퍼 폭 검증, 동적 중복·공백 정량화, 정답 대조와 성능 벤치마크는 수행하지 않았습니다. 입력 기본 품질과 문서 정합성의 근거이며 행위 식별 정확도가 검증되었다는 뜻이 아닙니다. 영상은 기존 예시이고 원본 CSV·공간 자료는 공개 저장소에 포함되지 않습니다.
