# -*- coding: utf-8 -*-
"""
정박/정류/항로이탈 식별 데모 영상 생성

busan_AIS2.csv의 항적을 1초 간격 프레임으로 렌더링해 mp4를 만든다.
- 정박지 내 + 저속(SOG<=2.1)                    → 정박 (채운 원)
- 정박지 밖 정지(10m 이내 이동 or 침로 10°+ 변화) → 정박 표시 (채운 원)
- 그 외 저속                                    → 정류 (노란 테두리 원)
- 항로 내→외 이탈(600초 이내)                    → 항로이탈 (빨간 원)
- SOG 라벨 + COG 점선 화살표

리팩토링 내용:
- 공통 코드는 ais_common.py로 이동
- 항로/정박지 소속 여부를 렌더 루프의 contains 반복 대신 sjoin으로 1회 전처리
- 무거운 로드는 전부 main()으로 이동 (Windows spawn에서 워커별 재실행 방지)
- ffmpeg h264_nvenc 가용성 확인 후 libx264 자동 폴백 (-crf/-cq 올바르게 사용)
- 침로차 계산에 360° 랩어라운드 적용
- 저속인데 이전 점이 없거나 오래된 경우에도 기본 마커를 그리도록 분기 보완
"""

import argparse
import os
import tempfile
import time
from multiprocessing import Pool

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyArrowPatch
from PIL import Image
from tqdm import tqdm

import ais_common as ac

# -------------------------------
# 상수
# -------------------------------
FRAME_STEP_SEC = 1
MAX_FRAMES = 100
FPS = 30
RADIUS_STATIONARY = 200      # 정박 표시 원 반경 (EPSG:3857)
RADIUS_LOITERING = 800       # 정류 표시 원 반경
RADIUS_DEPARTURE = 50        # 항로이탈 표시 원 반경
BAR_SIZE = 700               # 선박 마커 크기
ARROW_LENGTH = 1500          # COG 화살표 길이
SOG_LABEL_OFFSET = 300       # SOG 라벨 y 오프셋
DEPARTURE_WINDOW_SEC = 600   # 항로이탈 판정: 이전 점과의 최대 시간차
STOP_WINDOW_SEC = 200        # 정지 판정: 이전 점과의 최대 시간차
STOP_DISTANCE_M = 10         # 정지 판정: 이동 거리 임계값
TURN_THRESHOLD_DEG = 10      # 정지 판정: 침로 변화 임계값
DEFAULT_OUTPUT = 'ship_movement_DOCK_SPEED_demo2.mp4'

# 워커 전역 (init_worker에서 채워짐)
W = {}


def init_worker(payload):
    ac.setup_korean_font()
    W.update(payload)


def build_legend():
    return (
        [ac.legend_ship(c, l) for c, l in ac.SHIP_TYPE_LEGEND]
        + [
            ac.legend_ring('black', '정박'),
            ac.legend_ring('yellow', '정류'),
            ac.legend_ring('red', '항로이탈'),
        ]
    )


def process_frame(frame):
    current_time = W['start_time'] + pd.Timedelta(seconds=frame * FRAME_STEP_SEC)
    current_time_np = current_time.to_datetime64()

    fig, ax = plt.subplots(figsize=(15, 10), dpi=200)
    minx, miny, maxx, maxy = W['bbox']
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.imshow(W['basemap'], extent=W['basemap_extent'], interpolation='lanczos')
    W['all_routes'].plot(ax=ax, edgecolor='white', facecolor='none')

    for ship_id, (times, group) in W['ship_groups'].items():
        idx = np.searchsorted(times, current_time_np, side='right') - 1
        if idx < 0:
            continue
        latest = group.iloc[idx]
        previous = group.iloc[idx - 1] if idx > 0 else None

        sx, sy = latest['x'], latest['y']
        color = W['ship_colors'].get(ship_id, 'gray')
        sog = latest['SOG']

        # 항로이탈: 직전 점은 항로 내, 현재는 밖 (전처리된 in_route 플래그 사용)
        if previous is not None:
            time_diff = (latest['일시'] - previous['일시']).total_seconds()
            if previous['in_route'] and not latest['in_route'] \
                    and time_diff < DEPARTURE_WINDOW_SEC:
                ax.add_patch(plt.Circle((sx, sy), RADIUS_DEPARTURE,
                                        color='red', fill=False, linewidth=1))

        drew_stationary = False
        if sog <= ac.SOG_STOPPED:
            if latest['in_anchorage']:
                ax.add_patch(plt.Circle((sx, sy), RADIUS_STATIONARY,
                                        color=color, fill=True))
                drew_stationary = True
            elif previous is not None:
                time_diff = (latest['일시'] - previous['일시']).total_seconds()
                if time_diff <= STOP_WINDOW_SEC:
                    distance = ac.haversine(latest['경도'], latest['위도'],
                                            previous['경도'], previous['위도'])
                    if distance <= STOP_DISTANCE_M \
                            or ac.heading_diff(latest['Heading'],
                                               previous['Heading']) >= TURN_THRESHOLD_DEG:
                        ax.add_patch(plt.Circle((sx, sy), RADIUS_STATIONARY,
                                                color=color, fill=True))
                        drew_stationary = True
                    else:
                        # 정류: 저속이지만 정지로 보기 어려움
                        ax.add_patch(plt.Circle((sx, sy), RADIUS_LOITERING,
                                                color='yellow', fill=False, linewidth=1))

        if not drew_stationary:
            bar = ac.create_bar(sx, sy, latest['Heading'], BAR_SIZE)
            ax.add_patch(plt.Polygon(bar, closed=True, facecolor=color, edgecolor=color))

        if sog > 0.0:
            ax.text(sx, sy + SOG_LABEL_OFFSET, f'{sog:.1f}', color='yellow',
                    fontsize=6, ha='center', va='bottom')

        # COG 점선 화살표 (COG 컬럼이 없거나 결측이면 Heading 사용)
        cog = latest['COG'] if W['has_cog'] and pd.notna(latest.get('COG')) \
            else latest['Heading']
        cog_rad = ac.heading_to_math_rad(cog)
        arrow = FancyArrowPatch(
            (sx, sy),
            (sx + ARROW_LENGTH * np.cos(cog_rad), sy + ARROW_LENGTH * np.sin(cog_rad)),
            color='red', linewidth=0.8, linestyle='-', arrowstyle='->',
            mutation_scale=3, shrinkA=0, shrinkB=0,
        )
        ax.add_patch(arrow)

    ax.set_title(f'Ship Positions at {current_time}')
    ax.legend(handles=build_legend(), loc='lower right')
    plt.axis('off')
    plt.tight_layout()
    return ac.fig_to_rgb(fig)


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    start_time_execution = time.time()

    parser = argparse.ArgumentParser(description='정박/정류/항로이탈 식별 데모 영상 생성')
    parser.add_argument('--frames', type=int, default=MAX_FRAMES, help='최대 프레임 수')
    parser.add_argument('--output', default=DEFAULT_OUTPUT, help='출력 mp4 파일명')
    parser.add_argument('--fps', type=int, default=FPS)
    args = parser.parse_args()

    ac.setup_korean_font()

    print('데이터 로드 중...')
    df, gdf = ac.load_ais_csv('busan_AIS2.csv')
    ship_colors = ac.load_ship_colors('Static.csv')

    all_routes = ac.load_shapes('제1광역항로_5.shp', '제2광역항로_5.shp',
                                '지선항로_5.shp', '고시항로_2023.shp')
    all_anchorages = ac.load_shapes('고시정박지_2023.shp', '정박지(KOMC)_2023_병합.shp',
                                    '무역항_2024.shp')

    print('전처리(항로/정박지 소속) 계산 중...')
    df['in_route'] = ac.points_within(gdf, all_routes)
    df['in_anchorage'] = ac.points_within(gdf, all_anchorages)

    ship_groups = ac.build_ship_groups(df)

    start_time = df['일시'].min()
    end_time = df['일시'].max()
    total_frames = min(args.frames,
                       int((end_time - start_time).total_seconds() / FRAME_STEP_SEC))

    bbox = tuple(gdf.total_bounds)
    print('배스맵 다운로드 중...')
    basemap, basemap_extent = ac.fetch_basemap(*bbox)

    payload = {
        'ship_groups': ship_groups,
        'ship_colors': ship_colors,
        'all_routes': all_routes,
        'basemap': basemap,
        'basemap_extent': basemap_extent,
        'bbox': bbox,
        'start_time': start_time,
        'has_cog': 'COG' in df.columns,
    }

    num_processes = ac.optimal_process_count()
    print(f'Using {num_processes} processes, {total_frames} frames')

    with tempfile.TemporaryDirectory() as tmpdir:
        with tqdm(total=total_frames, desc='Generating and saving frames') as pbar:
            with Pool(processes=num_processes, initializer=init_worker,
                      initargs=(payload,)) as pool:
                for i, img in enumerate(pool.imap(process_frame,
                                                  range(total_frames), chunksize=10)):
                    Image.fromarray(img).save(os.path.join(tmpdir, f'frame_{i:05d}.png'))
                    pbar.update()

        ac.run_ffmpeg(os.path.join(tmpdir, 'frame_%05d.png'), args.output,
                      fps=args.fps, prefer_nvenc=True)

    print(f'전체 실행 시간: {time.time() - start_time_execution:.2f}초')
    print('동영상 생성 완료:', args.output)


if __name__ == '__main__':
    main()
