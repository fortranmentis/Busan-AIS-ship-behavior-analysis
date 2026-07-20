# Busan AIS Ship Behavior Analysis

[한국어](README.ko.md) | **English**

> AIS-based vessel behavior detection and time-lapse visualization for the Port of Busan, Korea.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![GeoPandas](https://img.shields.io/badge/GeoPandas-geospatial-139C5A)
![Matplotlib](https://img.shields.io/badge/Matplotlib-rendering-11557C)
![FFmpeg](https://img.shields.io/badge/FFmpeg-encoding-007808?logo=ffmpeg&logoColor=white)

This project analyzes ship trajectories from AIS (Automatic Identification System) data in the Port of Busan and renders the results as frame-by-frame time-lapse videos over Esri satellite imagery. Two analysis pipelines are provided:

- **Navigational status detection** — anchoring, loitering, and route deviation against officially designated fairways and anchorages
- **Operational activity detection** — berthing/cargo handling, fishing, and towing operations

## Demo

### Anchoring / Loitering / Route deviation (`3docked_video_demo.py`)

Vessels are classified every second against designated fairway and anchorage polygons. Filled circles mark anchored/stationary ships, yellow rings mark loitering, red rings mark ships that have just left a designated route. Each moving vessel shows its SOG label and a COG vector arrow.

![Anchoring, loitering and route deviation demo](assets/ship_movement_DOCK_SPEED_demo2.gif)

### Operational activity (`2acting_video2_demo.py`)

Berthing status is tracked as a persistent per-vessel state, fishing activity is inferred from vessel type and speed, and towing operations are inferred from tug speed plus proximity to another vessel (KD-tree neighbor search).

![Operational activity demo](assets/ship_movement_act2_demo.gif)

Full-resolution MP4 versions are available in [`assets/`](assets/).

## Behavior classification rules

### `3docked_video_demo.py` — navigational status

| Status | Condition | Marker |
|---|---|---|
| Anchored (in anchorage) | Inside a designated anchorage ∧ SOG ≤ 2.1 kn | Filled circle |
| Stationary (outside) | SOG ≤ 2.1 kn ∧ moved ≤ 10 m or heading changed ≥ 10° within 200 s | Filled circle |
| Loitering | SOG ≤ 2.1 kn but not stationary | Yellow ring |
| Route deviation | Previous fix inside a designated route ∧ current fix outside, within 600 s | Red ring |
| Under way | SOG > 2.1 kn | Directional marker + SOG label + COG arrow |

### `2acting_video2_demo.py` — operational activity

| Activity | Condition | Marker |
|---|---|---|
| Berthing / cargo handling | Inside 100 m coastal buffer ∧ SOG ≤ 2.1 kn (state persists until SOG > 2.1 kn) | Filled circle |
| Fishing | Fishing vessel ∧ 0 ≤ SOG ≤ 5 kn | Blue ring |
| Towing operation | Tug ∧ 0.5 ≤ SOG ≤ 8 kn ∧ another vessel within ≈ 570 m | Yellow ring |

### Vessel type color coding (AIS ship type code)

| Code | Category | Color |
|---|---|---|
| 30 | Fishing | blue |
| 31, 32, 50, 52 | Tug / pilot / towing | yellow |
| 36, 37 | Yacht / pleasure craft | lime |
| 40–49, 60–69 | Passenger / high-speed craft | pink |
| 70–79 | Cargo | orange |
| 80–89 | Tanker | red |
| other | Unclassified | gray |

## How it works

```
AIS CSV ──► preprocess (main process, once)          ──► parallel rendering ──► FFmpeg
            • encoding-tolerant load, EPSG:3857        • one worker pool,       • h264_nvenc
            • vectorized point-in-polygon (sjoin)        per-frame drawing        with libx264
            • per-vessel time-sorted groups              only                     fallback
            • deterministic berthing state             • frames streamed
            • satellite basemap cached once              straight to disk
```

Key design points:

- **All spatial predicates are precomputed.** Route/anchorage/port membership is resolved once per AIS fix with a vectorized spatial join, so the per-frame render loop does no geometry tests.
- **Per-vessel binary search.** Trajectories are grouped by MMSI and time-sorted; each frame locates the latest fix with `np.searchsorted` instead of filtering the full dataset.
- **Windows-safe multiprocessing.** Heavy data loading happens once in the main process and is shipped to workers through the pool initializer, so `spawn` does not re-execute it per worker.
- **Single basemap fetch.** The satellite basemap is downloaded once (`contextily.bounds2img`) and reused by every frame.
- **Deterministic state.** Berthing status is derived from each vessel's own time series (forward-filled state machine), independent of frame processing order.

## Requirements

- Python ≥ 3.10 with: `pandas`, `numpy`, `geopandas`, `shapely`, `matplotlib`, `contextily`, `scipy`, `pillow`, `tqdm`, `psutil`
- FFmpeg on `PATH` (or in the active conda environment)
- A Korean-capable font (NanumSquare or Malgun Gothic is auto-detected)

```bash
conda create -n ais python=3.10 geopandas contextily scipy pillow tqdm psutil ffmpeg -c conda-forge
conda activate ais
```

## Usage

```bash
# Anchoring / loitering / route deviation video
python 3docked_video_demo.py --frames 1000 --output dock_demo.mp4 --fps 30

# Operational activity video
python 2acting_video2_demo.py --frames 1000 --output activity_demo.mp4 --fps 30
```

| Option | Default | Description |
|---|---|---|
| `--frames` | 100 | Number of 1-second frames to render |
| `--output` | script-specific | Output MP4 filename |
| `--fps` | 30 | Output video frame rate |

## Input data

The AIS data and official shapefiles are **not** included in this repository. The scripts expect the following files in the working directory:

| File | Content | Required columns / notes |
|---|---|---|
| `busan_AIS2.csv` | Dynamic AIS fixes | `MMSI`, `일시` (timestamp), `경도`/`위도` (lon/lat, WGS 84), `SOG`, `COG`, `Heading` |
| `Static.csv` | Static vessel info | `MMSI`, `선종코드` (AIS ship type code) |
| `항로.shp` | Designated fairways | EPSG:4326 |
| `정박지.shp`, `항구.shp` | Anchorages / trade port | EPSG:4326, used by `3docked_video_demo.py` |
| `해안선버퍼.shp` | 100 m coastal buffer | EPSG:4326, used by `2acting_video2_demo.py` |

Shapefile names above are placeholders — point the `load_shapes()` calls in each script's `main()` to your own files.

## Repository structure

```
├── ais_common.py             # Shared library: data loading, spatial joins, markers,
│                             #   legends, heading utilities, FFmpeg runner
├── 2acting_video2_demo.py    # Operational activity pipeline (berthing / fishing / towing)
├── 3docked_video_demo.py     # Navigational status pipeline (anchoring / loitering / deviation)
└── assets/                   # Demo GIFs and full-resolution MP4s
```

## Notes

- Basemap tiles: Esri World Imagery via [contextily](https://github.com/geopandas/contextily).
- Detection thresholds (speed, distance, time windows) are defined as constants at the top of each script and can be tuned for other ports or datasets.
