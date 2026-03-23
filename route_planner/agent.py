"""
agent.py (v12-fixed)
修复：
1. 移除未使用的导入 (Optional, search_poi_by_keyword, plan_walking_route)
2. 缓存 LLM 实例避免重复创建
3. 知识图谱查询结果缓存
4. 增加超时保护
"""
import json
import re
import logging
from langchain_openai import ChatOpenAI
from django.conf import settings

from .amap_service import (
    search_poi_around,
    geocode, build_multi_segment_route,
    _haversine_distance
)
from .knowledge_base import retrieve_route_knowledge

logger = logging.getLogger(__name__)

# 厦门岛内默认起点（白城沙滩）
DEFAULT_START = {'name': '白城沙滩', 'lng': 118.100875, 'lat': 24.432281}

# 配速表（min/km）
PACE_MAP = {
    '散步': 15.0,
    '快走': 10.0,
    '健步走': 10.0,
    '轻松跑': 7.5,
    '跑步': 6.5,
    '慢跑': 7.0,
    '中等跑': 6.0,
    '耐力跑': 5.5,
    '高强度': 5.0,
    '骑行': 3.0,
    '徒步': 12.0,
}

WALKING_PACE = 12.5

# LLM 实例缓存
_llm_cache = {}


def get_llm(streaming: bool = False):
    cache_key = f"llm_{streaming}"
    if cache_key not in _llm_cache:
        _llm_cache[cache_key] = ChatOpenAI(
            model=settings.DEEPSEEK_MODEL,
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            temperature=0.3,
            streaming=streaming,
            request_timeout=30,
        )
    return _llm_cache[cache_key]


# ============================================================
# 解析用户意图
# ============================================================
INTENT_PARSE_PROMPT = """你是专业的厦门运动路线规划助手。请从用户输入中提取结构化参数，以JSON格式返回。

字段说明：
- duration_min: 整数，运动时长（分钟），无法判断时默认60
- activity_type: 跑步/骑行/徒步/散步/快走/慢跑
- intensity: 轻松/中等/耐力/高强度
- origin: 起点名称（用户明确说了的），否则为null
- destination: 终点名称（用户明确说了的），否则为null
- must_pass: 列表，用户明确说要经过的地点名称（如"经过将军祠"、"途经白鹭洲"）
- preferred_features: 列表，可包含 shade/water/scenic/sea_view/park/soft_surface
- avoid_features: 列表，可包含 stairs/concrete/traffic/crowd
- health_constraints: 列表，可包含 ankle/knee/heart
- city: 城市名称，固定为"厦门"
- user_notes: 其他备注

配速参考（用于估算距离）：
- 散步/健步走: 12-15 min/km
- 轻松跑/慢跑: 7-8 min/km
- 跑步（中等）: 6-6.5 min/km
- 耐力跑: 5.5 min/km
- 高强度: 5 min/km
- 骑行: 3 min/km

规则：
- 若提到脚踝/膝盖不适，health_constraints加入ankle/knee，preferred_features加soft_surface
- must_pass 必须包含用户明确说要经过的所有地点，这非常重要！
- 只返回JSON，不要有任何多余文字

用户输入：{user_input}"""


def parse_user_intent(user_input: str) -> dict:
    """调用DeepSeek解析用户意图，返回结构化参数"""
    llm = get_llm()
    prompt = INTENT_PARSE_PROMPT.format(user_input=user_input)
    try:
        response = llm.invoke(prompt)
        raw = response.content.strip()
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        raw = re.sub(r'^```\s*', '', raw)
        params = json.loads(raw)
        logger.info(f"[意图解析] 输入: {user_input[:50]}, 解析结果: {params}")
        return params
    except Exception as e:
        logger.error(f"[意图解析] 失败: {e}")
        return {
            "duration_min": 60,
            "activity_type": "跑步",
            "intensity": "中等",
            "origin": None,
            "destination": None,
            "must_pass": [],
            "preferred_features": [],
            "avoid_features": [],
            "health_constraints": [],
            "city": "厦门",
            "user_notes": user_input,
        }


def _get_pace(activity_type: str, intensity: str = '中等') -> float:
    """获取配速（min/km）"""
    if activity_type == '跑步':
        intensity_pace = {'轻松': 7.5, '中等': 6.5, '耐力': 5.5, '高强度': 5.0}
        return intensity_pace.get(intensity, 6.5)
    return PACE_MAP.get(activity_type, 6.5)


def _calc_running_duration(distance_m: int, pace_min_per_km: float) -> float:
    """根据距离和跑步配速计算实际运动时间（分钟）"""
    return round(distance_m / 1000 * pace_min_per_km, 1)


# ============================================================
# 核心路线规划函数
# ============================================================
def plan_route_with_agent(user_query: str, session_id: str,
                           origin_name: str = None,
                           origin_lng: float = None,
                           origin_lat: float = None) -> dict:
    """使用Agent规划路线，返回完整结果"""
    result = {
        'success': False,
        'params': {},
        'waypoints': [],
        'route': {},
        'pois': [],
        'rag_context': '',
        'rag_docs': [],
        'kg_nodes': [],
        'agent_steps': [],
        'recommendation': '',
    }

    # Step 1: 解析意图
    params = parse_user_intent(user_query)
    result['params'] = params
    result['agent_steps'].append({
        'step': '意图解析',
        'icon': '🧠',
        'result': f"活动: {params.get('activity_type')}, 时长: {params.get('duration_min')}分钟, 必经点: {params.get('must_pass', [])}"
    })

    city = '厦门'
    activity_type = params.get('activity_type', '跑步')
    intensity = params.get('intensity', '中等')
    duration_min = int(params.get('duration_min', 60))
    must_pass = params.get('must_pass', [])

    pace = _get_pace(activity_type, intensity)
    target_dist_km = duration_min / pace
    logger.info(f"[Agent] 目标距离: {target_dist_km:.1f}km (时长{duration_min}min, 配速{pace}min/km)")

    # Step 2: 确定起点坐标
    effective_origin = origin_name or params.get('origin')

    if origin_lng and origin_lat:
        start_point = {'name': origin_name or '起点', 'lng': origin_lng, 'lat': origin_lat}
    elif effective_origin:
        try:
            geo = geocode(effective_origin, city=city)
            start_point = {'name': geo['name'], 'lng': geo['lng'], 'lat': geo['lat']}
        except Exception as e:
            logger.warning(f"[Agent] 起点地理编码失败: {e}")
            start_point = dict(DEFAULT_START)
    else:
        start_point = dict(DEFAULT_START)

    result['agent_steps'].append({
        'step': '起点确定',
        'icon': '📍',
        'result': f"{start_point['name']} ({start_point['lng']:.4f}, {start_point['lat']:.4f})"
    })

    # Step 3: 知识图谱查询
    kg_nodes = []
    try:
        from .knowledge_graph import query_kg_for_route, init_knowledge_graph
        from .models import KGNode
        if KGNode.objects.count() == 0:
            init_knowledge_graph()
        preferred = params.get('preferred_features', [])
        constraints = params.get('health_constraints', [])
        kg_nodes = query_kg_for_route(activity_type, preferred, constraints)
        result['kg_nodes'] = kg_nodes
        result['agent_steps'].append({
            'step': '知识图谱查询',
            'icon': '🕸️',
            'result': f"推荐{len(kg_nodes)}个地点: {', '.join([n['name'] for n in kg_nodes[:3]])}"
        })
    except Exception as e:
        logger.warning(f"[Agent] 知识图谱查询失败: {e}")
        result['agent_steps'].append({'step': '知识图谱查询', 'icon': '🕸️', 'result': f'失败: {str(e)}'})

    # Step 4: RAG知识检索
    rag_docs = []
    try:
        rag_query = f"{activity_type} {intensity} {' '.join(params.get('preferred_features', []))}"
        rag_docs = retrieve_route_knowledge(rag_query, n_results=3)
    except Exception as e:
        logger.warning(f"[Agent] RAG检索失败: {e}")
    rag_context = "\n".join([doc['text'][:200] for doc in rag_docs])
    result['rag_context'] = rag_context
    result['rag_docs'] = rag_docs
    result['agent_steps'].append({
        'step': 'RAG知识检索',
        'icon': '📚',
        'result': f"检索到{len(rag_docs)}条相关知识"
    })

    # Step 5: 构建途经点列表
    waypoints = [start_point]

    # 5a: 用户明确指定的必经点
    must_pass_points = []
    for place_name in must_pass:
        try:
            geo = geocode(place_name, city=city)
            dist = _haversine_distance(
                start_point['lng'], start_point['lat'],
                geo['lng'], geo['lat']
            )
            if dist <= 15000:
                must_pass_points.append({
                    'name': geo['name'],
                    'lng': geo['lng'],
                    'lat': geo['lat'],
                })
                logger.info(f"[Agent] 必经点 '{place_name}' 地理编码成功: {geo['lng']},{geo['lat']}")
            else:
                logger.warning(f"[Agent] 必经点 '{place_name}' 距起点{dist/1000:.1f}km过远，跳过")
        except Exception as e:
            logger.warning(f"[Agent] 必经点 '{place_name}' 地理编码失败: {e}")

    waypoints.extend(must_pass_points)

    # 5b: POI搜索补充
    if len(waypoints) < 3:
        preferred = params.get('preferred_features', [])
        if 'sea_view' in preferred or 'scenic' in preferred:
            poi_keyword = '海滨公园 景观'
        elif 'park' in preferred or 'shade' in preferred:
            poi_keyword = '公园 植物园'
        elif activity_type == '骑行':
            poi_keyword = '骑行道 公园'
        else:
            poi_keyword = '公园 广场 景点'

        search_radius = min(int(target_dist_km * 500), 5000)
        pois = search_poi_around(
            start_point['lng'], start_point['lat'],
            radius=search_radius,
            keyword=poi_keyword,
            city=city,
            page_size=10
        )
        result['pois'] = pois

        max_poi_dist = target_dist_km * 1000 * 0.6
        for poi in pois:
            dist = poi.get('distance', 0)
            if dist and 300 < dist < max_poi_dist:
                already_added = any(
                    abs(wp['lng'] - poi['location']['lng']) < 0.001 and
                    abs(wp['lat'] - poi['location']['lat']) < 0.001
                    for wp in waypoints
                )
                if not already_added:
                    waypoints.append({
                        'name': poi['name'],
                        'lng': poi['location']['lng'],
                        'lat': poi['location']['lat'],
                    })
                    if len(waypoints) >= 4:
                        break
    else:
        result['pois'] = []

    result['agent_steps'].append({
        'step': 'POI搜索',
        'icon': '🔍',
        'result': f"找到{len(result['pois'])}个候选地点，必经点{len(must_pass_points)}个"
    })

    # 5c: 环形路线回到起点
    waypoints.append({**start_point, 'name': f"{start_point['name']}（返回）"})
    result['waypoints'] = waypoints
    result['agent_steps'].append({
        'step': '途经点规划',
        'icon': '🗺️',
        'result': f"规划{len(waypoints)}个途经点: {' → '.join([w['name'] for w in waypoints])}"
    })

    # Step 6: 高德路径规划
    try:
        route_data = build_multi_segment_route(waypoints, activity_type=activity_type)

        actual_dist_km = route_data['total_distance_km']
        running_duration_min = _calc_running_duration(
            route_data['total_distance_m'], pace
        )
        route_data['total_duration_min'] = running_duration_min
        route_data['running_pace_min_per_km'] = pace
        route_data['activity_type'] = activity_type

        failed = route_data.get('failed_segments', [])
        if failed:
            fail_info = ', '.join([f"{f['from']}→{f['to']}" for f in failed])
            result['agent_steps'].append({
                'step': '路线警告',
                'icon': '⚠️',
                'result': f"以下路段因水域阻隔或距离过远已跳过: {fail_info}"
            })

        result['route'] = route_data
        result['agent_steps'].append({
            'step': '路线规划',
            'icon': '🛣️',
            'result': f"总距离 {actual_dist_km}km，按{activity_type}配速预计 {running_duration_min}分钟，{len(route_data.get('polyline', []))}个坐标点"
        })
    except Exception as e:
        logger.error(f"[Agent] 路线规划失败: {e}")
        result['agent_steps'].append({'step': '路线规划', 'icon': '🛣️', 'result': f"规划失败: {str(e)}"})
        result['success'] = False
        result['error'] = str(e)
        return result

    # Step 7: 生成AI推荐语
    try:
        route_info = result.get('route', {})
        dist_km = route_info.get('total_distance_km', 0)
        dur_min = route_info.get('total_duration_min', duration_min)
        wp_names = ' → '.join([w['name'] for w in waypoints])
        kg_hint = ''
        if kg_nodes:
            kg_hint = f"知识图谱推荐特征：{', '.join(kg_nodes[0].get('features', [])[:3])}"

        rec_prompt = f"""请为以下厦门运动路线生成一段简洁有感染力的推荐语（80字以内）：
用户需求：{user_query}
路线：{wp_names}
距离：{dist_km}km，预计{dur_min}分钟
活动类型：{activity_type}
{kg_hint}
相关知识：{rag_context[:150] if rag_context else '厦门特色路线'}

直接给出推荐语，不要有前缀。"""

        llm = get_llm()
        rec_response = llm.invoke(rec_prompt)
        result['recommendation'] = rec_response.content.strip()
        result['agent_steps'].append({
            'step': 'AI推荐语生成',
            'icon': '✨',
            'result': result['recommendation'][:50] + '...'
        })
    except Exception as e:
        logger.warning(f"[Agent] 推荐语生成失败: {e}")
        result['recommendation'] = f"为您规划了一条{activity_type}路线，途经{' → '.join([w['name'] for w in waypoints[:3]])}，祝运动愉快！"

    result['success'] = True
    return result
