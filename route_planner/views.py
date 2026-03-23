"""
route_planner/views.py (v12-fixed)
修复：
1. 移除手动 CORS 头（由中间件统一处理）
2. 版本号更新为 v12
3. add_query 参数统一为3个
4. 健康检查增加数据库连通性检测
"""
import json
import time
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .agent import plan_route_with_agent
from .amap_service import search_poi_by_keyword, geocode
from .models import RouteHistory, UserPreference

logger = logging.getLogger(__name__)


def _json_response(data, status=200):
    """统一 JSON 响应（CORS 由中间件处理）"""
    return JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})


@csrf_exempt
def plan_route(request):
    """POST /api/route/plan/"""
    if request.method == 'OPTIONS':
        return _json_response({})
    if request.method != 'POST':
        return _json_response({'error': '仅支持POST请求'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return _json_response({'error': f'请求体JSON格式错误: {str(e)}'}, status=400)

    user_query = body.get('query', '').strip()
    session_id = body.get('session_id', 'default')
    origin_name = body.get('origin_name', '').strip() or None
    origin_lng = body.get('origin_lng')
    origin_lat = body.get('origin_lat')

    if not user_query:
        return _json_response({'error': '请输入运动需求'}, status=400)
    if len(user_query) > 500:
        return _json_response({'error': '输入内容过长'}, status=400)

    t_start = time.time()
    logger.info(f"[路线规划] session={session_id}, query={user_query[:50]}")

    try:
        result = plan_route_with_agent(
            user_query=user_query,
            session_id=session_id,
            origin_name=origin_name,
            origin_lng=origin_lng,
            origin_lat=origin_lat,
        )
    except Exception as e:
        import traceback
        logger.error(f"[路线规划] Agent失败: {e}\n{traceback.format_exc()}")
        return _json_response({'error': f'路线规划失败: {str(e)}'}, status=500)

    t_total = round(time.time() - t_start, 2)

    try:
        params = result.get('params', {})
        route_data = result.get('route', {})
        waypoints = result.get('waypoints', [])
        RouteHistory.objects.create(
            session_id=session_id,
            user_query=user_query,
            parsed_params=params,
            origin_name=origin_name or (waypoints[0].get('name', '') if waypoints else ''),
            origin_lng=origin_lng or (waypoints[0].get('lng') if waypoints else None),
            origin_lat=origin_lat or (waypoints[0].get('lat') if waypoints else None),
            route_result=route_data,
            ai_response=result.get('recommendation', ''),
            total_time_s=t_total,
        )
        # 修复：add_query 统一传3个参数
        pref, _ = UserPreference.objects.get_or_create(session_id=session_id)
        pref.add_query(user_query, params, result.get('recommendation', ''))
    except Exception as e:
        logger.warning(f"[路线规划] 数据库保存失败: {e}")

    route = result.get('route', {})
    polyline = route.get('polyline', [])
    polyline_for_frontend = [[p[0], p[1]] for p in polyline if len(p) >= 2]

    return _json_response({
        'success': True,
        'session_id': session_id,
        'user_query': user_query,
        'parsed_params': result.get('params', {}),
        'waypoints': result.get('waypoints', []),
        'route': {
            'total_distance_km': route.get('total_distance_km', 0),
            'total_duration_min': route.get('total_duration_min', 0),
            'polyline': polyline_for_frontend,
            'segment_details': route.get('segment_details', []),
            'failed_segments': route.get('failed_segments', []),
            'running_pace_min_per_km': route.get('running_pace_min_per_km', 0),
            'activity_type': route.get('activity_type', ''),
        },
        'pois': result.get('pois', [])[:5],
        'rag_context': result.get('rag_context', ''),
        'rag_docs': result.get('rag_docs', []),
        'kg_nodes': result.get('kg_nodes', []),
        'agent_steps': result.get('agent_steps', []),
        'recommendation': result.get('recommendation', ''),
        'performance': {'total_time_s': t_total},
    })


@csrf_exempt
def search_pois(request):
    """GET /api/route/pois/?keyword=公园&city=厦门"""
    if request.method == 'OPTIONS':
        return _json_response({})
    keyword = request.GET.get('keyword', '').strip()
    city = request.GET.get('city', '厦门')
    if not keyword:
        return _json_response({'error': '请提供搜索关键词'}, status=400)
    try:
        pois = search_poi_by_keyword(keyword, city=city, page_size=10)
        return _json_response({'success': True, 'keyword': keyword, 'city': city, 'count': len(pois), 'pois': pois})
    except Exception as e:
        return _json_response({'error': str(e)}, status=500)


@csrf_exempt
def geocode_address(request):
    """GET /api/route/geocode/?address=白城沙滩&city=厦门"""
    if request.method == 'OPTIONS':
        return _json_response({})
    address = request.GET.get('address', '').strip()
    city = request.GET.get('city', '厦门')
    if not address:
        return _json_response({'error': '请提供地址'}, status=400)
    try:
        result = geocode(address, city=city)
        return _json_response({'success': True, **result})
    except Exception as e:
        return _json_response({'error': str(e)}, status=500)


@csrf_exempt
def knowledge_graph_api(request):
    """GET /api/route/kg/?activity=跑步&features=海景,树荫&constraints=ankle"""
    if request.method == 'OPTIONS':
        return _json_response({})
    try:
        from .knowledge_graph import query_kg_for_route, init_knowledge_graph
        from .models import KGNode, KGEdge

        # 确保图谱已初始化
        if KGNode.objects.count() == 0:
            init_knowledge_graph()

        activity = request.GET.get('activity', '跑步')
        features_str = request.GET.get('features', '')
        constraints_str = request.GET.get('constraints', '')
        features = [f.strip() for f in features_str.split(',') if f.strip()]
        constraints = [c.strip() for c in constraints_str.split(',') if c.strip()]

        nodes = query_kg_for_route(activity, features, constraints)

        node_ids = [n['node_id'] for n in nodes]
        edges = KGEdge.objects.filter(
            source__node_id__in=node_ids,
            target__node_id__in=node_ids
        ).select_related('source', 'target')[:30]

        return _json_response({
            'success': True,
            'activity': activity,
            'features': features,
            'constraints': constraints,
            'nodes': nodes,
            'edges': [e.to_dict() for e in edges],
            'total_nodes': KGNode.objects.count(),
            'total_edges': KGEdge.objects.count(),
        })
    except Exception as e:
        logger.error(f"[KG API] 失败: {e}")
        return _json_response({'error': str(e)}, status=500)


@csrf_exempt
def route_history_api(request):
    """GET /api/route/history/?session_id=xxx"""
    if request.method == 'OPTIONS':
        return _json_response({})
    session_id = request.GET.get('session_id', 'default')
    try:
        histories = RouteHistory.objects.filter(session_id=session_id).order_by('-created_at')[:20]
        return _json_response({
            'success': True,
            'session_id': session_id,
            'count': histories.count(),
            'histories': [{
                'id': h.id,
                'user_query': h.user_query,
                'origin_name': h.origin_name,
                'created_at': h.created_at.isoformat(),
                'total_time_s': h.total_time_s,
            } for h in histories],
        })
    except Exception as e:
        return _json_response({'error': str(e)}, status=500)


@require_http_methods(['GET'])
def health_check(request):
    """健康检查端点 - 增加数据库连通性检测"""
    db_ok = True
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        db_ok = False

    status_code = 200 if db_ok else 503
    return _json_response({
        'status': 'ok' if db_ok else 'degraded',
        'service': '运动智能伴侣·路线大师',
        'version': 'v12.0',
        'database': 'connected' if db_ok else 'disconnected',
    }, status=status_code)
