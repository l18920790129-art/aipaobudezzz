"""
amap_service.py (v12-fixed)
修复：
1. 使用 requests.Session 连接池（减少TCP握手开销）
2. 增加请求重试机制
3. 统一超时配置
4. 保留渡轮过滤和直线降级禁止逻辑
"""
import math
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.conf import settings

logger = logging.getLogger(__name__)

AMAP_KEY = settings.AMAP_WEB_KEY
AMAP_BASE = 'https://restapi.amap.com'
AMAP_TIMEOUT = 15

FERRY_KEYWORDS = ['轮渡', '渡轮', '渡口', '轮船', '坐船', '乘船', '摆渡', 'ferry']

# 连接池（单例）
_session = None


def _get_session():
    """获取带重试机制的 requests Session"""
    global _session
    if _session is None:
        _session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=5, pool_maxsize=10)
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
    return _session


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


def _haversine_distance(lng1, lat1, lng2, lat2) -> float:
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def _contains_ferry(steps: list) -> bool:
    for step in steps:
        instruction = step.get('instruction', '') or ''
        road = step.get('road', '') or ''
        action = step.get('action', '') or ''
        text = instruction + road + action
        for kw in FERRY_KEYWORDS:
            if kw in text:
                return True
    return False


def geocode(address: str, city: str = '厦门') -> dict:
    url = f"{AMAP_BASE}/v3/geocode/geo"
    params = {'key': AMAP_KEY, 'address': address, 'city': city, 'output': 'JSON'}
    try:
        session = _get_session()
        resp = session.get(url, params=params, timeout=AMAP_TIMEOUT)
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


def search_poi_by_keyword(keyword: str, city: str = '厦门',
                           poi_types: str = '', page: int = 1,
                           page_size: int = 10) -> list:
    url = f"{AMAP_BASE}/v3/place/text"
    params = {
        'key': AMAP_KEY, 'keywords': keyword, 'city': city,
        'citylimit': 'true', 'output': 'JSON',
        'offset': page_size, 'page': page, 'extensions': 'base',
    }
    if poi_types:
        params['types'] = poi_types
    try:
        session = _get_session()
        resp = session.get(url, params=params, timeout=AMAP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') != '1':
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


def search_poi_around(center_lng: float, center_lat: float,
                       radius: int = 3000, keyword: str = '',
                       poi_types: str = '', city: str = '厦门',
                       page_size: int = 10) -> list:
    url = f"{AMAP_BASE}/v3/place/around"
    params = {
        'key': AMAP_KEY,
        'location': f"{center_lng},{center_lat}",
        'radius': radius, 'output': 'JSON',
        'offset': page_size, 'page': 1,
        'extensions': 'base', 'sortrule': 'distance',
    }
    if keyword:
        params['keywords'] = keyword
    if poi_types:
        params['types'] = poi_types
    try:
        session = _get_session()
        resp = session.get(url, params=params, timeout=AMAP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') != '1':
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


def plan_walking_route(origin_lng: float, origin_lat: float,
                        dest_lng: float, dest_lat: float,
                        reject_ferry: bool = True) -> dict:
    """高德步行路径规划 v3，reject_ferry=True 时拒绝渡轮路段"""
    url = f"{AMAP_BASE}/v3/direction/walking"
    params = {
        'key': AMAP_KEY,
        'origin': f"{origin_lng},{origin_lat}",
        'destination': f"{dest_lng},{dest_lat}",
        'output': 'JSON',
    }
    try:
        session = _get_session()
        resp = session.get(url, params=params, timeout=AMAP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"[高德步行规划] {origin_lng},{origin_lat} -> {dest_lng},{dest_lat}, 状态: {data.get('status')}")

        if data.get('status') != '1':
            raise ValueError(f"步行路径规划失败: {data.get('info', '未知错误')}")

        route = data.get('route', {})
        paths = route.get('paths', [])
        if not paths:
            raise ValueError("步行路径规划返回空路径")

        path = paths[0]
        steps = path.get('steps', [])

        if reject_ferry and _contains_ferry(steps):
            raise ValueError("路线包含渡轮/轮渡路段，两点之间有水域阻隔，请选择同一陆地区域内的途经点")

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
            'polyline': all_points,
        }
    except requests.RequestException as e:
        logger.error(f"[高德步行规划] 网络错误: {e}")
        raise


def plan_cycling_route(origin_lng: float, origin_lat: float,
                        dest_lng: float, dest_lat: float) -> dict:
    url = f"{AMAP_BASE}/v4/direction/bicycling"
    params = {
        'key': AMAP_KEY,
        'origin': f"{origin_lng},{origin_lat}",
        'destination': f"{dest_lng},{dest_lat}",
        'output': 'JSON',
    }
    try:
        session = _get_session()
        resp = session.get(url, params=params, timeout=AMAP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
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


def build_multi_segment_route(waypoints: list, activity_type: str = '步行') -> dict:
    """多段路线规划，禁止直线降级连接"""
    if len(waypoints) < 2:
        raise ValueError("至少需要2个途经点")

    all_polyline = []
    total_distance = 0
    total_duration = 0
    segment_details = []
    failed_segments = []

    for i in range(len(waypoints) - 1):
        origin = waypoints[i]
        dest = waypoints[i + 1]

        straight_dist = _haversine_distance(
            origin['lng'], origin['lat'],
            dest['lng'], dest['lat']
        )
        if straight_dist > 15000:
            logger.warning(f"[多段路线] 第{i+1}段直线距离{straight_dist:.0f}m过远，跳过")
            failed_segments.append({
                'from': origin['name'],
                'to': dest['name'],
                'reason': f"两点距离{straight_dist/1000:.1f}km过远，超出合理范围"
            })
            continue

        try:
            if activity_type in ['骑行']:
                seg = plan_cycling_route(
                    origin['lng'], origin['lat'],
                    dest['lng'], dest['lat']
                )
            else:
                seg = plan_walking_route(
                    origin['lng'], origin['lat'],
                    dest['lng'], dest['lat'],
                    reject_ferry=True
                )

            all_polyline.extend(seg['polyline'])
            total_distance += seg['distance']
            total_duration += seg['duration']
            segment_details.append({
                'from': origin['name'],
                'to': dest['name'],
                'distance': seg['distance'],
                'duration': seg['duration'],
                'steps': seg['steps'][:3],
            })
            logger.info(f"[多段路线] 第{i+1}段成功: {origin['name']} -> {dest['name']}, {seg['distance']}m")

        except Exception as e:
            logger.warning(f"[多段路线] 第{i+1}段跳过({e}): {origin['name']} -> {dest['name']}")
            failed_segments.append({
                'from': origin['name'],
                'to': dest['name'],
                'reason': str(e)
            })

    if not segment_details:
        reasons = '; '.join([f['reason'] for f in failed_segments])
        raise ValueError(f"所有路段规划均失败，请重新选择途经点。原因：{reasons}")

    return {
        'total_distance_m': total_distance,
        'total_duration_s': total_duration,
        'total_distance_km': round(total_distance / 1000, 2),
        'total_duration_min': round(total_duration / 60, 1),
        'polyline': all_polyline,
        'waypoints': waypoints,
        'segment_details': segment_details,
        'failed_segments': failed_segments,
    }
