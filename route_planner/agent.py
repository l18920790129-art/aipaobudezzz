"""
agent.py - LangChain Agent 核心逻辑
基于 LangChain + DeepSeek 构建路线规划智能体
Tools:
  1. search_poi_tool       - 高德POI搜索
  2. plan_route_tool       - 高德路径规划
  3. query_knowledge_tool  - ChromaDB知识库检索（RAG）
  4. get_user_memory_tool  - 用户历史偏好查询
  5. query_kg_tool         - 知识图谱查询（NetworkX + PostgreSQL）
"""
import json
import logging
from typing import Optional
from langchain.agents import AgentExecutor, create_react_agent
from langchain.tools import Tool
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from django.conf import settings

from .amap_service import (
    search_poi_by_keyword, search_poi_around,
    plan_walking_route, geocode, build_multi_segment_route
)
from .knowledge_base import retrieve_route_knowledge

logger = logging.getLogger(__name__)


# ============================================================
# DeepSeek LLM 初始化
# ============================================================
def get_llm(streaming: bool = False):
    return ChatOpenAI(
        model=settings.DEEPSEEK_MODEL,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL,
        temperature=0.3,
        streaming=streaming,
    )


# ============================================================
# Agent Tools 定义
# ============================================================
def _search_poi_tool_func(input_str: str) -> str:
    """搜索厦门POI地点，输入: '关键词' 或 '关键词|城市'"""
    try:
        parts = input_str.strip().split('|')
        keyword = parts[0].strip()
        city = parts[1].strip() if len(parts) > 1 else '厦门'
        pois = search_poi_by_keyword(keyword, city=city, page_size=5)
        if not pois:
            return f"未找到与'{keyword}'相关的地点"
        result = []
        for p in pois[:5]:
            loc = p['location']
            result.append(f"- {p['name']}（{p['address'] or '厦门'}）坐标: {loc['lng']},{loc['lat']}")
        return "找到以下地点：\n" + "\n".join(result)
    except Exception as e:
        logger.error(f"[POI搜索Tool] 错误: {e}")
        return f"POI搜索失败: {str(e)}"


def _plan_route_tool_func(input_str: str) -> str:
    """规划步行路线，输入: '起点经度,起点纬度|终点经度,终点纬度'"""
    try:
        parts = input_str.strip().split('|')
        if len(parts) < 2:
            return "输入格式错误，需要: 起点经度,起点纬度|终点经度,终点纬度"
        origin_parts = parts[0].strip().split(',')
        dest_parts = parts[1].strip().split(',')
        origin_lng, origin_lat = float(origin_parts[0]), float(origin_parts[1])
        dest_lng, dest_lat = float(dest_parts[0]), float(dest_parts[1])
        route = plan_walking_route(origin_lng, origin_lat, dest_lng, dest_lat)
        dist_km = round(route['distance'] / 1000, 2)
        dur_min = round(route['duration'] / 60, 1)
        return (f"路线规划成功：总距离 {dist_km}km，预计步行时间 {dur_min}分钟，"
                f"共{len(route['steps'])}个路段，{len(route['polyline'])}个坐标点")
    except Exception as e:
        logger.error(f"[路线规划Tool] 错误: {e}")
        return f"路线规划失败: {str(e)}"


def _query_knowledge_tool_func(query: str) -> str:
    """查询厦门运动路线知识库（RAG），输入: 查询关键词"""
    try:
        docs = retrieve_route_knowledge(query, n_results=3)
        if not docs:
            return "知识库中未找到相关信息"
        result = []
        for doc in docs:
            meta = doc.get('metadata', {})
            result.append(f"[{meta.get('area', '厦门')}] {doc['text'][:200]}")
        return "知识库检索结果：\n" + "\n\n".join(result)
    except Exception as e:
        logger.error(f"[知识库Tool] 错误: {e}")
        return f"知识库查询失败: {str(e)}"


def _get_user_memory_tool_func(session_id: str) -> str:
    """获取用户历史偏好，输入: session_id"""
    try:
        from .models import UserPreference
        pref, _ = UserPreference.objects.get_or_create(session_id=session_id)
        return pref.get_context_string()
    except Exception as e:
        logger.error(f"[用户记忆Tool] 错误: {e}")
        return "暂无用户历史数据"


def _query_kg_tool_func(input_str: str) -> str:
    """
    查询知识图谱推荐地点，输入: '活动类型|特征1,特征2|约束1,约束2'
    例如: '跑步|海景,树荫|ankle'
    """
    try:
        from .knowledge_graph import query_kg_for_route, init_knowledge_graph
        from .models import KGNode
        if KGNode.objects.count() == 0:
            init_knowledge_graph()

        parts = input_str.strip().split('|')
        activity = parts[0].strip() if parts else '跑步'
        features = [f.strip() for f in parts[1].split(',')] if len(parts) > 1 else []
        constraints = [c.strip() for c in parts[2].split(',')] if len(parts) > 2 else []

        nodes = query_kg_for_route(activity, features, constraints)
        if not nodes:
            return "知识图谱中未找到匹配地点"

        result = []
        for n in nodes[:5]:
            feat_str = '、'.join(n.get('features', [])[:3])
            result.append(f"- {n['name']}（{n['type']}）特征: {feat_str}，推荐度: {n['score']}")
        return "知识图谱推荐地点：\n" + "\n".join(result)
    except Exception as e:
        logger.error(f"[知识图谱Tool] 错误: {e}")
        return f"知识图谱查询失败: {str(e)}"


# 构建Tool列表
AGENT_TOOLS = [
    Tool(
        name="search_poi",
        func=_search_poi_tool_func,
        description=(
            "搜索厦门的地点、景点、公园等POI信息。"
            "输入格式: '关键词' 或 '关键词|城市'。"
            "例如: '白城沙滩' 或 '公园|厦门'"
        )
    ),
    Tool(
        name="plan_route",
        func=_plan_route_tool_func,
        description=(
            "规划两点之间的步行路线，获取距离和时间。"
            "输入格式: '起点经度,起点纬度|终点经度,终点纬度'。"
            "坐标为高德GCJ-02坐标系。"
        )
    ),
    Tool(
        name="query_knowledge",
        func=_query_knowledge_tool_func,
        description=(
            "查询厦门运动路线知识库（RAG），获取路线特征、难度、适合人群等信息。"
            "输入: 查询关键词或问题。"
            "例如: '适合脚踝不适的路线' 或 '有树荫的跑步路线'"
        )
    ),
    Tool(
        name="query_kg",
        func=_query_kg_tool_func,
        description=(
            "查询厦门地点知识图谱，获取地点关系和推荐。"
            "输入格式: '活动类型|特征1,特征2|约束1,约束2'。"
            "例如: '跑步|海景,树荫|ankle' 或 '散步|平坦|knee'"
        )
    ),
]


# ============================================================
# 解析用户意图（提取结构化参数）
# ============================================================
INTENT_PARSE_PROMPT = """你是专业的运动路线规划助手。请从用户输入中提取结构化参数，以JSON格式返回。

字段说明：
- duration_min: 整数，运动时长（分钟），无法判断时默认60
- activity_type: 跑步/骑行/徒步/散步
- intensity: 轻松/中等/耐力/高强度
- origin: 起点名称（用户明确说了的），否则为null
- destination: 终点名称（用户明确说了的），否则为null
- preferred_features: 列表，可包含 shade/water/scenic/sea_view/park/soft_surface
- avoid_features: 列表，可包含 stairs/concrete/traffic/crowd
- health_constraints: 列表，可包含 ankle/knee/heart
- city: 城市名称，默认"厦门"
- user_notes: 其他备注

配速参考（用于估算距离）：
- 散步: 15 min/km，轻松跑: 7.5 min/km，中等跑: 6.5 min/km
- 耐力跑: 6.0 min/km，高强度: 5.0 min/km，骑行: 3.0 min/km

规则：
- 若提到脚踝/膝盖不适，health_constraints加入ankle/knee，preferred_features加soft_surface
- 只返回JSON，不要有任何多余文字

用户输入：{user_input}"""


def parse_user_intent(user_input: str) -> dict:
    """调用DeepSeek解析用户意图，返回结构化参数"""
    import re
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
            "preferred_features": [],
            "avoid_features": [],
            "health_constraints": [],
            "city": "厦门",
            "user_notes": user_input,
        }


# ============================================================
# 核心路线规划函数（供views调用）
# ============================================================
def plan_route_with_agent(user_query: str, session_id: str,
                           origin_name: str = None,
                           origin_lng: float = None,
                           origin_lat: float = None) -> dict:
    """
    使用Agent规划路线，返回完整结果（含知识图谱、RAG文档、路线坐标）
    """
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
        'result': f"活动: {params.get('activity_type')}, 时长: {params.get('duration_min')}分钟, 约束: {params.get('health_constraints', [])}"
    })

    # Step 2: 确定起点坐标
    city = params.get('city', '厦门')
    effective_origin = origin_name or params.get('origin')

    if origin_lng and origin_lat:
        start_point = {'name': origin_name or '起点', 'lng': origin_lng, 'lat': origin_lat}
    elif effective_origin:
        try:
            geo = geocode(effective_origin, city=city)
            start_point = {'name': geo['name'], 'lng': geo['lng'], 'lat': geo['lat']}
        except Exception as e:
            logger.warning(f"[Agent] 起点地理编码失败: {e}")
            start_point = {'name': '白城沙滩', 'lng': 118.100875, 'lat': 24.432281}
    else:
        start_point = {'name': '白城沙滩', 'lng': 118.100875, 'lat': 24.432281}

    result['agent_steps'].append({
        'step': '起点确定',
        'icon': '📍',
        'result': f"{start_point['name']} ({start_point['lng']:.4f}, {start_point['lat']:.4f})"
    })

    # Step 3: 知识图谱查询
    try:
        from .knowledge_graph import query_kg_for_route, init_knowledge_graph
        from .models import KGNode
        if KGNode.objects.count() == 0:
            init_knowledge_graph()
        activity_type = params.get('activity_type', '跑步')
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
    activity_type = params.get('activity_type', '跑步')
    rag_query = f"{activity_type} {params.get('intensity', '')} {' '.join(params.get('preferred_features', []))}"
    rag_docs = retrieve_route_knowledge(rag_query, n_results=3)
    rag_context = "\n".join([doc['text'][:200] for doc in rag_docs])
    result['rag_context'] = rag_context
    result['rag_docs'] = rag_docs
    result['agent_steps'].append({
        'step': 'RAG知识检索',
        'icon': '📚',
        'result': f"检索到{len(rag_docs)}条相关知识"
    })

    # Step 5: 搜索目标POI
    duration_min = params.get('duration_min', 60)
    preferred = params.get('preferred_features', [])

    if 'sea_view' in preferred or 'scenic' in preferred:
        poi_keyword = '海滨公园 景观'
    elif 'park' in preferred or 'shade' in preferred:
        poi_keyword = '公园 植物园'
    elif activity_type == '骑行':
        poi_keyword = '骑行道 公园'
    else:
        poi_keyword = '公园 广场 景点'

    pois = search_poi_around(
        start_point['lng'], start_point['lat'],
        radius=5000,
        keyword=poi_keyword,
        city=city,
        page_size=8
    )
    result['pois'] = pois
    result['agent_steps'].append({
        'step': 'POI搜索',
        'icon': '🔍',
        'result': f"在{start_point['name']}周边{5}km内找到{len(pois)}个地点"
    })

    # Step 6: 选择途经点
    pace_map = {'散步': 15.0, '跑步': 6.5, '骑行': 3.0, '徒步': 12.0, '快走': 10.0}
    pace = pace_map.get(activity_type, 6.5)
    target_dist_km = duration_min / pace

    waypoints = [start_point]
    if pois:
        for poi in pois[:3]:
            dist = poi.get('distance', 0)
            if dist and dist > 200:
                waypoints.append({
                    'name': poi['name'],
                    'lng': poi['location']['lng'],
                    'lat': poi['location']['lat'],
                })
                if len(waypoints) >= 3:
                    break

    if len(waypoints) < 2:
        waypoints.append({'name': '曾厝垵', 'lng': 118.1082, 'lat': 24.4458})

    # 环形路线：回到起点
    waypoints.append({**start_point, 'name': f"{start_point['name']}（返回）"})
    result['waypoints'] = waypoints
    result['agent_steps'].append({
        'step': '途经点规划',
        'icon': '🗺️',
        'result': f"规划{len(waypoints)}个途经点: {' → '.join([w['name'] for w in waypoints])}"
    })

    # Step 7: 高德路径规划
    try:
        route_data = build_multi_segment_route(waypoints, activity_type=activity_type)
        result['route'] = route_data
        result['agent_steps'].append({
            'step': '路线规划',
            'icon': '🛣️',
            'result': f"总距离 {route_data['total_distance_km']}km，预计 {route_data['total_duration_min']}分钟，{len(route_data.get('polyline', []))}个坐标点"
        })
    except Exception as e:
        logger.error(f"[Agent] 路线规划失败: {e}")
        result['agent_steps'].append({'step': '路线规划', 'icon': '🛣️', 'result': f"规划失败: {str(e)}"})

    # Step 8: 生成AI推荐语
    try:
        route_info = result.get('route', {})
        dist_km = route_info.get('total_distance_km', 0)
        dur_min = route_info.get('total_duration_min', duration_min)
        wp_names = ' → '.join([w['name'] for w in waypoints])
        kg_hint = ''
        if result['kg_nodes']:
            kg_hint = f"知识图谱推荐特征：{', '.join(result['kg_nodes'][0].get('features', [])[:3])}"

        rec_prompt = f"""请为以下路线生成一段简洁有感染力的推荐语（80字以内）：
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
