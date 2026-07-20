# -*- coding: utf-8 -*-
"""
선박 행위(접안/하역, 조업, 예인작업) 식별 데모 영상 생성

busan_AIS2.csv의 항적을 1초 간격 프레임으로 렌더링해 mp4를 만든다.
- 항만 100m 버퍼 내 + 저속(SOG<=2.1)  → 접안/하역 (채운 원)
- 어선(blue) SOG 0~5                 → 조업 (파란 테두리 원)
- 예인선(yellow) SOG 0.5~8 + 근접선박 → 예인작업 (노란 테두리 원)

리팩토링 내용:
- 공통 코드는 ais_common.py로 이동
- EPSG:3857 변환 + 배스맵 1회 캐싱 (프레임마다 타일 다운로드 제거)
- 선박별 그룹화 + searchsorted (프레임마다 전체 필터링 제거)
- 접안 상태를 전처리에서 결정적으로 계산 (워커별 전역 dict 상태 버그 제거)
- 프레임을 즉시 디스크에 저장 (전 프레임 RAM 보관 제거)
- 무거운 로드는 전부 main()으로 이동 (Windows spawn에서 워커별 재실행 방지)
"""

import argparse
import os
import tempfile
from multiprocessing import Pool

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image
from scipy.spatial import KDTree
from tqdm import tqdm

import ais_common as ac

# -------------------------------
# 상수 (기존 도(degree) 단위 값을 EPSG:3857 미터로 환산)
# -------------------------------
FRAME_STEP_SEC = 1
MAX_FRAMES = 100
FPS = 30
PROXIMITY_RADIUS = 0.0051 * ac.M3857_PER_DEG    # 예인 근접 판정 반경 (구 0.0051도)
DOCKED_DOT_RADIUS = 0.002 * ac.M3857_PER_DEG    # 접안 표시 원 (구 0.002도)
ACTIVITY_RING_RADIUS = 0.008 * ac.M3857_PER_DEG  # 조업/예인 원 (구 0.008도)
BAR_SIZE = 0.01 * ac.M3857_PER_DEG              # 선박 마커 크기 (구 0.01도)
FISHING_SOG_MAX = 5.0       # 조업 판정 SOG 상한
TOWING_SOG_MIN = 0.5        # 예인작업 판정 SOG 범위
TOWING_SOG_MAX = 8.0
DEFAULT_OUTPUT = 'ship_movement_act2_demo.mp4'

# 워커 전역 (init_worker에서 채워짐)
W = {}


def compute_docked_status(group):
    """선박 시계열에서 접안 상태를 결정적으로 계산.

    항만 내 저속이면 접안 시작, 고속이면 해제, 그 외에는 직전 상태 유지.
    (기존에는 워커별 전역 dict라 프레임 처리 순서에 따라 결과가 달라졌음)
    """
    state = np.where(group['in_port'] & (group['SOG'] <= ac.SOG_STOPPED), 1.0,
                     np.where(group['SOG'] > ac.SOG_STOPPED, 0.0, np.nan))
    return pd.Series(state).ffill().fillna(0.0).astype(bool).values


def init_worker(payload):
    ac.setup_korean_font()
    W.update(payload)


def build_legend():
    return (
        [ac.legend_ship(c, l) for c, l in ac.SHIP_TYPE_LEGEND]
        + [
            ac.legend_ring('blue', '조업'),
            ac.legend_ring('yellow', '예인작업'),
            ac.legend_dot('blue', '하역(어선)'),
            ac.legend_dot('lime', '승하선(요트, 유람선)'),
            ac.legend_dot('pink', '승하선(여객, 쾌속선)'),
            ac.legend_dot('orange', '하역(화물선)'),
            ac.legend_dot('red', '하역(유조선)'),
        ]
    )


def process_frame(frame):
    current_time = W['start_time'] + pd.Timedelta(seconds=frame * FRAME_STEP_SEC)
    current_time_np = current_time.to_datetime64()

    # 각 선박의 현재 시점 최신 위치
    latest_rows = {}
    for ship_id, (times, group) in W['ship_groups'].items():
        idx = np.searchsorted(times, current_time_np, side='right') - 1
        if idx >= 0:
            latest_rows[ship_id] = group.iloc[idx]

    fig, ax = plt.subplots(figsize=(15, 10), dpi=200)
    minx, miny, maxx, maxy = W['bbox']
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.imshow(W['basemap'], extent=W['basemap_extent'], interpolation='lanczos')
    W['all_routes'].plot(ax=ax, edgecolor='white', facecolor='none')

    # 예인 근접 판정: 현재 위치들로 KDTree 1회 구성
    proximity = {}
    if latest_rows:
        ids = list(latest_rows.keys())
        coords = np.array([[latest_rows[i]['x'], latest_rows[i]['y']] for i in ids])
        tree = KDTree(coords)
        for k, ship_id in enumerate(ids):
            neighbors = tree.query_ball_point(coords[k], r=PROXIMITY_RADIUS)
            proximity[ship_id] = any(n != k for n in neighbors)

    for ship_id, row in latest_rows.items():
        sx, sy = row['x'], row['y']
        color = W['ship_colors'].get(ship_id, 'gray')
        sog = row['SOG']

        if row['docked']:
            ax.add_patch(plt.Circle((sx, sy), DOCKED_DOT_RADIUS, color=color, fill=True))
            continue

        if color == 'blue' and 0 <= sog <= FISHING_SOG_MAX:
            ax.add_patch(plt.Circle((sx, sy), ACTIVITY_RING_RADIUS,
                                    color='blue', fill=False, linewidth=1))
        elif (color == 'yellow' and TOWING_SOG_MIN <= sog <= TOWING_SOG_MAX
              and proximity.get(ship_id, False)):
            ax.add_patch(plt.Circle((sx, sy), ACTIVITY_RING_RADIUS,
                                    color='yellow', fill=False, linewidth=1))

        bar = ac.create_bar(sx, sy, row['Heading'], BAR_SIZE)
        ax.add_patch(plt.Polygon(bar, closed=True, facecolor=color, edgecolor=color))

    ax.set_title(f'Ship Positions at {current_time}')
    ax.legend(handles=build_legend(), loc='lower right')
    ax.set_axis_off()  # EPSG:3857 원시 좌표 눈금은 의미가 없어 숨김
    return ac.fig_to_rgb(fig)


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    parser = argparse.ArgumentParser(description='선박 행위 식별 데모 영상 생성')
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
    port_buffer = ac.load_shapes('100m버퍼3.shp')

    print('전처리(항만 소속/접안 상태) 계산 중...')
    df['in_port'] = ac.points_within(gdf, port_buffer)

    ship_groups = ac.build_ship_groups(df)
    for ship_id, (times, group) in ship_groups.items():
        group['docked'] = compute_docked_status(group)

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
    }

    num_processes = ac.optimal_process_count()
    print(f'Using {num_processes} processes, {total_frames} frames')

    with tempfile.TemporaryDirectory() as tmpdir:
        with tqdm(total=total_frames, desc='Generating frames') as pbar:
            with Pool(processes=num_processes, initializer=init_worker,
                      initargs=(payload,)) as pool:
                for i, img in enumerate(pool.imap(process_frame,
                                                  range(total_frames), chunksize=10)):
                    Image.fromarray(img).save(os.path.join(tmpdir, f'frame_{i:05d}.png'))
                    pbar.update()

        ac.run_ffmpeg(os.path.join(tmpdir, 'frame_%05d.png'), args.output, fps=args.fps)

    print('동영상 생성 완료:', args.output)


if __name__ == '__main__':
    main()
