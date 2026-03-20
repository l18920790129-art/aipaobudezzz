"""
amap_service.py - 高德地图 API 真实调用模块
功能：
1. POI搜索（关键字搜索 + 周边搜索）
2. 步行路径规划
3. 骑行路径规划
4. 地理编码（地址转坐标）
5. GCJ-02 → WGS84 坐标转换
"""
import math
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

AMAP_KEY = settings.AMAP_WEB_KEY
AMAP_BASE = 'https://restapi.amap.com'


# ============================================================
# 坐标转换：GCJ-02 → WGS84
# ============================================================
def _transform_lat(x, y):
    ret = -100.0 + 2.0*x + 3.0*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
    ret += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
    ret += (20.0*math.sin(y*math.pi) + 40.0*math.sin(y/3.0*math.pi)) * 2.0/3.0
    ret += (160.0*math.sin(y/12.0*math.pi) + 320*math.sin(y*math.pi/30.0)) * 2.0/3.0
    return ret


def _transform_lon(x, y):
    ret = 300.0 + x + 2.0*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
    ret += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
    ret += (20.0*math.sin(x*math.pi) + 40.0*math.sin(x/3.0*math.pi)) * 2.0/3.0
    ret += (150.0*math.sin(x/12.0*math.pi) + 300.0*math.sin(x/30.0*math.pi)) * 2.0/3.0
    return ret


def gcj02_to_wgs84(gcj_lat: float, gcj_lon: float) -> tuple:
    """高德GCJ-02坐标转WGS84坐标"""
    a = 6378245.0
    ee = 0.00669342162296594323
    x = gcj_lon - 105.0
    y = gcj_lat - 35.0
    d_lat = _transform_lat(x, y)
    d_lon = _transform_lon(x, y)
    rad_lat = gcj_lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    d_lon = (d_lon * 180.0) / (a / sqrt_magic * math.cos(rad_lat) * math.pi)
    return round(gcj_lat - d_lat, 6), round(gcj_lon - d_lon, 6)


# ============================================================
# 地理编码：地址/地名 → GCJ-02坐标
# ============================================================
def geocode(address: str, city: str = '厦门') -> dict:
    """
    地理编码：将地址/地名转换为GCJ-02坐标
    返回: {'name': str, 'lng': float, 'lat': float, 'formatted_address': str}
    """
    url = f"{AMAP_BASE}/v3/geocode/geo"
    params = {
        'key': AMAP_KEY,
        'address': address,
        'city': city,
        'output': 'JSON',
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"[高德地理编码] 查询: {address}, 状态: {data.get('status')}")

        if data.get('status') == '1' and data.get('geocodes'):
            geo = data['geocodes'][0]
            location = geo.get('location', '')
            if location and ',' in location:
                lng, lat = location.split(',')
                return {
                    'name': address,
                    'lng': float(lng),
                    'lat': float(lat),
                    'formatted_address': geo.get('formatted_address', address),
                    'adcode': geo.get('adcode', ''),
                }
        raise ValueError(f"地理编码失败: {data.get('info', '未知错误')}")
    except requests.RequestException as e:
        logger.error(f"[高德地理编码] 网络错误: {e}")
        raise


# ============================================================
# POI搜索：关键字搜索
# ============================================================
def search_poi_by_keyword(keyword: str, city: str = '厦门',
                           poi_types: str = '', page: int = 1,
                           page_size: int = 10) -> list:
    """
    高德POI关键字搜索
    返回: [{'poi_id', 'name', 'address', 'category', 'location': {'lng', 'lat'}, 'tel'}, ...]
    """
    url = f"{AMAP_BASE}/v3/place/text"
    params = {
        'key': AMAP_KEY,
        'keywords': keyword,
        'city': city,
        'citylimit': 'true',
        'output': 'JSON',
        'offset': page_size,
        'page': page,
        'extensions': 'base',
    }
    if poi_types:
        params['types'] = poi_types

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"[高德POI搜索] 关键词: {keyword}, 城市: {city}, 结果数: {data.get('count', 0)}")

        if data.get('status') != '1':
            logger.warning(f"[高德POI搜索] 失败: {data.get('info')}")
            return []

        pois = []
        for p in data.get('pois', []):
            location = p.get('location', '')
            if not location or ',' not in location:
                continue
            lng, lat = location.split(',')
            pois.append({
                'poi_id': p.get('id', ''),
                'name': p.get('name', ''),
                'address': p.get('address', '') if isinstance(p.get('address'), str) else '',
                'category': p.get('type', ''),
                'location': {'lng': float(lng), 'lat': float(lat)},
                'tel': p.get('tel', '') if isinstance(p.get('tel'), str) else '',
                'distance': p.get('distance', ''),
            })
        return pois
    except requests.RequestException as e:
        logger.error(f"[高德POI搜索] 网络错误: {e}")
        return []


# ============================================================
# POI搜索：周边搜索
# ============================================================
def search_poi_around(center_lng: float, center_lat: float,
                       radius: int = 3000, keyword: str = '',
                       poi_types: str = '', city: str = '厦门',
                       page_size: int = 10) -> list:
    """
    高德POI周边搜索
    center_lng/lat: GCJ-02坐标
    radius: 搜索半径（米）
    """
    url = f"{AMAP_BASE}/v3/place/around"
    params = {
        'key': AMAP_KEY,
        'location': f"{center_lng},{center_lat}",
        'radius': radius,
        'output': 'JSON',
        'offset': page_size,
        'page': 1,
        'extensions': 'base',
        'sortrule': 'distance',
    }
    if keyword:
        params['keywords'] = keyword
    if poi_types:
        params['types'] = poi_types

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"[高德周边搜索] 中心: ({center_lng},{center_lat}), 半径: {radius}m, 关键词: {keyword}")

        if data.get('status') != '1':
            logger.warning(f"[高德周边搜索] 失败: {data.get('info')}")
            return []

        pois = []
        for p in data.get('pois', []):
            location = p.get('location', '')
            if not location or ',' not in location:
                continue
            lng, lat = location.split(',')
            pois.append({
                'poi_id': p.get('id', ''),
                'name': p.get('name', ''),
                'address': p.get('address', '') if isinstance(p.get('address'), str) else '',
                'category': p.get('type', ''),
                'location': {'lng': float(lng), 'lat': float(lat)},
                'tel': p.get('tel', '') if isinstance(p.get('tel'), str) else '',
                'distance': int(p.get('distance', 0)) if p.get('distance') else 0,
            })
        return pois
    except requests.RequestException as e:
        logger.error(f"[高德周边搜索] 网络错误: {e}")
        return []


# ============================================================
# 步行路径规划
# ============================================================
def plan_walking_route(origin_lng: float, origin_lat: float,
                        dest_lng: float, dest_lat: float) -> dict:
    """
    高德步行路径规划 v3
    origin/dest: GCJ-02坐标
    返回: {
        'distance': int,  # 总距离（米）
        'duration': int,  # 总时间（秒）
        'steps': [...],   # 路段列表
        'polyline': [(lng, lat), ...],  # GCJ-02路线坐标点
    }
    """
    url = f"{AMAP_BASE}/v3/direction/walking"
    params = {
        'key': AMAP_KEY,
        'origin': f"{origin_lng},{origin_lat}",
        'destination': f"{dest_lng},{dest_lat}",
        'output': 'JSON',
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"[高德步行规划] {origin_lng},{origin_lat} → {dest_lng},{dest_lat}, 状态: {data.get('status')}")

        if data.get('status') != '1':
            raise ValueError(f"步行路径规划失败: {data.get('info', '未知错误')}")

        route = data.get('route', {})
        paths = route.get('paths', [])
        if not paths:
            raise ValueError("步行路径规划返回空路径")

        path = paths[0]
        steps = path.get('steps', [])

        # 提取所有坐标点（GCJ-02）
        all_points = []
        step_list = []
        for step in steps:
            polyline_str = step.get('polyline', '')
            if polyline_str:
                for point_str in polyline_str.split(';'):
                    if ',' in point_str:
                        lng_s, lat_s = point_str.split(',')
                        all_points.append((float(lng_s), float(lat_s)))

            step_list.append({
                'instruction': step.get('instruction', ''),
                'distance': int(step.get('distance', 0)),
                'duration': int(step.get('duration', 0)),
                'road': step.get('road', ''),
                'action': step.get('action', ''),
            })

        return {
            'distance': int(path.get('distance', 0)),
            'duration': int(path.get('duration', 0)),
            'steps': step_list,
            'polyline': all_points,  # GCJ-02坐标列表
        }
    except requests.RequestException as e:
        logger.error(f"[高德步行规划] 网络错误: {e}")
        raise


# ============================================================
# 骑行路径规划
# ============================================================
def plan_cycling_route(origin_lng: float, origin_lat: float,
                        dest_lng: float, dest_lat: float) -> dict:
    """
    高德骑行路径规划 v4
    """
    url = f"{AMAP_BASE}/v4/direction/bicycling"
    params = {
        'key': AMAP_KEY,
        'origin': f"{origin_lng},{origin_lat}",
        'destination': f"{dest_lng},{dest_lat}",
        'output': 'JSON',
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"[高德骑行规划] 状态: {data.get('errcode', data.get('status'))}")

        # 骑行API返回格式略有不同
        if data.get('errcode') != 0 and data.get('status') != '1':
            raise ValueError(f"骑行路径规划失败: {data.get('errmsg', data.get('info', '未知错误'))}")

        route = data.get('data', data.get('route', {}))
        paths = route.get('paths', [])
        if not paths:
            raise ValueError("骑行路径规划返回空路径")

        path = paths[0]
        steps = path.get('steps', [])
        all_points = []
        step_list = []
        for step in steps:
            polyline_str = step.get('polyline', '')
            if polyline_str:
                for point_str in polyline_str.split(';'):
                    if ',' in point_str:
                        lng_s, lat_s = point_str.split(',')
                        all_points.append((float(lng_s), float(lat_s)))
            step_list.append({
                'instruction': step.get('instruction', ''),
                'distance': int(step.get('distance', 0)),
                'duration': int(step.get('duration', 0)),
                'road': step.get('road', ''),
            })

        return {
            'distance': int(path.get('distance', 0)),
            'duration': int(path.get('duration', 0)),
            'steps': step_list,
            'polyline': all_points,
        }
    except requests.RequestException as e:
        logger.error(f"[高德骑行规划] 网络错误: {e}")
        raise


# ============================================================
# 构建完整路线（起点 → 途经点 → 终点）
# ============================================================
def build_multi_segment_route(waypoints: list, activity_type: str = '步行') -> dict:
    """
    多段路线规划：将多个途经点连接成完整路线
    waypoints: [{'name': str, 'lng': float, 'lat': float}, ...]  GCJ-02坐标
    返回完整路线信息
    """
    if len(waypoints) < 2:
        raise ValueError("至少需要2个途经点")

    all_polyline = []
    total_distance = 0
    total_duration = 0
    segment_details = []

    for i in range(len(waypoints) - 1):
        origin = waypoints[i]
        dest = waypoints[i + 1]

        try:
            if activity_type in ['骑行']:
                seg = plan_cycling_route(
                    origin['lng'], origin['lat'],
                    dest['lng'], dest['lat']
                )
            else:
                seg = plan_walking_route(
                    origin['lng'], origin['lat'],
                    dest['lng'], dest['lat']
                )

            all_polyline.extend(seg['polyline'])
            total_distance += seg['distance']
            total_duration += seg['duration']
            segment_details.append({
                'from': origin['name'],
                'to': dest['name'],
                'distance': seg['distance'],
                'duration': seg['duration'],
                'steps': seg['steps'][:3],  # 只保留前3步
            })
        except Exception as e:
            logger.warning(f"[多段路线] 第{i+1}段规划失败: {e}")
            # 用直线连接作为降级
            all_polyline.append((origin['lng'], origin['lat']))
            all_polyline.append((dest['lng'], dest['lat']))

    return {
        'total_distance_m': total_distance,
        'total_duration_s': total_duration,
        'total_distance_km': round(total_distance / 1000, 2),
        'total_duration_min': round(total_duration / 60, 1),
        'polyline': all_polyline,  # GCJ-02坐标列表
        'waypoints': waypoints,
        'segment_details': segment_details,
    }
