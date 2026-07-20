# -*- coding: utf-8 -*-
"""
AIS 영상 생성 스크립트 공통 모듈

2acting_video2_demo.py / 3docked_video_demo.py 등에서 복사-붙여넣기로
중복되던 코드를 한 곳에 모았다.
- CSV 인코딩 폴백 로드, 선종코드→색상 매핑
- shapefile 로드 + EPSG:3857 변환 + buffer(0) 정리
- 항로/정박지/항만 소속 여부 벡터 연산(sjoin)
- 선박 마커, 범례, heading 유틸
- ffmpeg 실행 (h264_nvenc 가용 시 사용, 없으면 libx264 폴백)
"""

import math
import os
import subprocess
import sys

import matplotlib
matplotlib.use('Agg')  # 워커 프로세스 헤드리스 렌더링용 (pyplot import 전에 설정)

import contextily as ctx
import geopandas as gpd
import matplotlib.font_manager as fm
import matplotlib.path as mpath
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psutil

# -------------------------------
# 공통 상수 (기존 스크립트들의 매직넘버)
# -------------------------------
SOG_STOPPED = 2.1          # 정지 판정 SOG(knot)
HEADING_NOT_AVAILABLE = 511  # AIS Heading 미수신 값

# EPSG:3857에서 경도 1도의 x축 길이(m). 기존 도(degree) 단위 값 환산용
M3857_PER_DEG = 111319.49079327358

CSV_ENCODINGS = ('utf-8-sig', 'cp949', 'euc-kr', 'utf-8')


# -------------------------------
# 폰트
# -------------------------------
def setup_korean_font():
    """OS별 한글 폰트를 찾아 matplotlib에 등록한다."""
    candidates = [
        # 기존 스크립트가 쓰던 Linux 경로
        ('/usr/share/fonts/NANUMSQUARER.TTF', 'NanumSquare'),
        ('/usr/share/fonts/truetype/nanum/NanumGothic.ttf', 'NanumGothic'),
        # Windows 기본 폰트
        (r'C:\Windows\Fonts\NANUMSQUARER.TTF', 'NanumSquare'),
        (r'C:\Windows\Fonts\malgun.ttf', 'Malgun Gothic'),
    ]
    for path, name in candidates:
        if os.path.exists(path):
            fm.fontManager.ttflist.insert(0, fm.FontEntry(fname=path, name=name))
            plt.rcParams.update({'font.size': 10, 'font.family': name,
                                 'axes.unicode_minus': False})
            return name
    print('경고: 한글 폰트를 찾지 못했습니다. 범례가 깨질 수 있습니다.', file=sys.stderr)
    return None


# -------------------------------
# 데이터 로드
# -------------------------------
def read_csv_kr(path, **kwargs):
    """인코딩을 순차 시도하며 CSV를 읽는다(chardet 100바이트 감지 대체)."""
    last_err = None
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(path, encoding=enc, **kwargs)
        except (UnicodeDecodeError, UnicodeError) as e:
            last_err = e
    raise last_err


def load_ais_csv(path):
    """AIS 동적 데이터 로드: 일시 파싱 + EPSG:3857 x/y 컬럼 추가."""
    df = read_csv_kr(path)
    df['일시'] = pd.to_datetime(df['일시'], format='%Y-%m-%d %H:%M:%S')
    gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df['경도'], df['위도']), crs='EPSG:4326'
    ).to_crs(epsg=3857)
    df['x'] = gdf.geometry.x
    df['y'] = gdf.geometry.y
    return df, gdf


def get_ship_color(ship_type):
    """선종코드 → 표시 색상."""
    try:
        ship_type = int(ship_type)
    except (ValueError, TypeError):
        return 'gray'
    if ship_type == 30:
        return 'blue'      # 어선
    elif ship_type in (31, 32, 50, 52):
        return 'yellow'    # 견인, 도선, 예인선
    elif ship_type in (36, 37):
        return 'lime'      # 요트, 유람선
    elif 40 <= ship_type <= 49 or 60 <= ship_type <= 69:
        return 'pink'      # 여객, 쾌속선
    elif 70 <= ship_type <= 79:
        return 'orange'    # 화물선
    elif 80 <= ship_type <= 89:
        return 'red'       # 유조선
    return 'gray'


def load_ship_colors(static_path):
    """Static.csv에서 MMSI → 색상 dict 생성."""
    df2 = read_csv_kr(static_path, on_bad_lines='skip')
    return df2.set_index('MMSI')['선종코드'].map(get_ship_color).to_dict()


def load_shapes(*paths):
    """shapefile들을 로드해 EPSG:3857로 변환·buffer(0) 정리 후 하나로 합친다."""
    gdfs = []
    for p in paths:
        gdf = gpd.read_file(p)
        gdf = gdf.set_crs(epsg=4326, allow_override=True).to_crs(epsg=3857)
        gdf['geometry'] = gdf['geometry'].buffer(0)
        gdfs.append(gdf)
    return pd.concat(gdfs, ignore_index=True)


def points_within(gdf_points, gdf_polygons):
    """각 포인트가 폴리곤 집합 내부에 있는지 불리언 Series로 반환(sjoin 1회).

    렌더 루프 안에서 매 프레임 point-in-polygon을 반복하던 것을 대체한다.
    """
    joined = gpd.sjoin(gdf_points[['geometry']], gdf_polygons[['geometry']],
                       how='inner', predicate='within')
    return pd.Series(gdf_points.index.isin(joined.index), index=gdf_points.index)


def build_ship_groups(df):
    """MMSI별로 시간 정렬된 (times ndarray, group df) dict 생성."""
    groups = {}
    for ship, group in df.groupby('MMSI'):
        group = group.sort_values('일시').reset_index(drop=True)
        groups[ship] = (group['일시'].values, group)
    return groups


# -------------------------------
# Heading 유틸
# -------------------------------
def heading_to_math_rad(heading, compass=False):
    """heading(도) → 그리기용 라디안.

    compass=False: 기존 스크립트와 동일하게 수학각(동=0, 반시계)으로 그대로 사용.
    compass=True : AIS 나침반각(북=0, 시계방향)을 수학각으로 보정.
    """
    if compass:
        return np.radians(90.0 - heading)
    return np.radians(heading)


def heading_diff(a, b):
    """두 침로의 최소 각도차(0~180). 360° 랩어라운드 처리(359° vs 1° → 2)."""
    d = abs(a - b) % 360.0
    return 360.0 - d if d > 180.0 else d


def haversine(lon1, lat1, lon2, lat2):
    """두 위경도 지점 사이 거리(m)."""
    R = 6371000
    lon1, lat1, lon2, lat2 = map(math.radians, (lon1, lat1, lon2, lat2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# -------------------------------
# 그래픽 요소
# -------------------------------
def create_bar(x, y, heading, size, compass=False):
    """heading 방향을 가리키는 오각형(선박 마커) 꼭짓점 목록."""
    angle = heading_to_math_rad(heading, compass=compass)
    dx = size * np.cos(angle)
    dy = size * np.sin(angle)
    width = size * 0.2
    return [
        (x - width * np.sin(angle), y + width * np.cos(angle)),
        (x + width * np.sin(angle), y - width * np.cos(angle)),
        (x + dx + 0.5 * width * np.sin(angle), y + dy - 0.5 * width * np.cos(angle)),
        (x + dx * 1.2, y + dy * 1.2),
        (x + dx - 0.5 * width * np.sin(angle), y + dy + 0.5 * width * np.cos(angle)),
    ]


def create_custom_triangle():
    """범례용 총알 모양 마커 path."""
    return mpath.Path([
        (-0.2, -0.5), (0.2, -0.5), (0.2, 0.3), (0, 0.5), (-0.2, 0.3), (-0.2, -0.5),
    ])


def legend_ship(color, label):
    """선박(총알 마커) 범례 항목."""
    return plt.Line2D([0], [0], marker=create_custom_triangle(), color='none',
                      markerfacecolor=color, markersize=10, label=label)


def legend_ring(color, label):
    """테두리 원 범례 항목."""
    return plt.Line2D([0], [0], marker='o', color='none', markerfacecolor='none',
                      markeredgecolor=color, markersize=10, label=label,
                      markeredgewidth=1.5)


def legend_dot(color, label):
    """채운 원 범례 항목."""
    return plt.Line2D([0], [0], marker='o', color='none',
                      markerfacecolor=color, markersize=5, label=label)


SHIP_TYPE_LEGEND = [
    ('blue', '어선'),
    ('yellow', '견인, 도선, 예인선'),
    ('lime', '요트, 유람선'),
    ('pink', '여객, 쾌속선'),
    ('orange', '화물선'),
    ('red', '유조선'),
    ('gray', '미분류'),
]


# -------------------------------
# 배스맵 / 멀티프로세싱 / ffmpeg
# -------------------------------
def fetch_basemap(minx, miny, maxx, maxy):
    """위성 배스맵을 1회 다운로드해 (이미지, extent) 반환."""
    return ctx.bounds2img(minx, miny, maxx, maxy,
                          source=ctx.providers.Esri.WorldImagery)


def optimal_process_count():
    return min(psutil.cpu_count(logical=False) + 2, psutil.cpu_count(logical=True))


def fig_to_rgb(fig):
    """matplotlib figure → RGB ndarray (figure는 닫음)."""
    fig.canvas.draw()
    img = np.array(fig.canvas.buffer_rgba())[:, :, :3]
    plt.close(fig)
    return img


def _ffmpeg_exe():
    """PATH에서 ffmpeg를 찾고, 없으면 conda 환경의 Library/bin에서 찾는다."""
    import shutil
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    conda_ffmpeg = os.path.join(sys.prefix, 'Library', 'bin', 'ffmpeg.exe')
    if os.path.exists(conda_ffmpeg):
        return conda_ffmpeg
    raise FileNotFoundError('ffmpeg를 찾을 수 없습니다. PATH를 확인하세요.')


def _nvenc_available(ffmpeg_exe):
    try:
        out = subprocess.run([ffmpeg_exe, '-hide_banner', '-encoders'],
                             capture_output=True, text=True, check=True).stdout
        return 'h264_nvenc' in out
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def run_ffmpeg(frames_pattern, output_file, fps=30, prefer_nvenc=False):
    """PNG 프레임 시퀀스를 mp4로 인코딩.

    nvenc 선호 시 가용성 확인 후 사용하되, 인코딩 실패(드라이버/파라미터
    미지원 등)하면 libx264로 자동 재시도한다.
    """
    ffmpeg_exe = _ffmpeg_exe()

    def build_cmd(codec_args):
        return [
            ffmpeg_exe, '-y',
            '-framerate', str(fps),
            '-i', frames_pattern,
            *codec_args,
            # yuv420p는 짝수 해상도 필요 → 홀수면 잘라냄
            '-vf', 'crop=trunc(iw/2)*2:trunc(ih/2)*2',
            '-pix_fmt', 'yuv420p',
            '-threads', str(os.cpu_count()),
            output_file,
        ]

    if prefer_nvenc and _nvenc_available(ffmpeg_exe):
        print(f'FFmpeg 인코딩 (h264_nvenc): {output_file}')
        try:
            subprocess.run(build_cmd(['-c:v', 'h264_nvenc', '-cq', '19']), check=True)
            return
        except subprocess.CalledProcessError:
            print('h264_nvenc 인코딩 실패 → libx264로 재시도합니다.', file=sys.stderr)

    print(f'FFmpeg 인코딩 (libx264): {output_file}')
    subprocess.run(build_cmd(['-c:v', 'libx264', '-preset', 'medium', '-crf', '12']),
                   check=True)
