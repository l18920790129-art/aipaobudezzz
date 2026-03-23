"""
chat/views.py (v12-fixed)
修复：
1. 内存泄漏 - _memory_store 增加 LRU 淘汰机制
2. 移除手动 CORS 头（由 django-cors-headers 中间件统一处理）
3. save_message_to_db 逻辑优化
4. parsed_params key名统一
5. 增加输入长度限制
"""
import json
import logging
import re
from collections import OrderedDict

from django.http import StreamingHttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from openai import OpenAI

from .models import ChatSession, ChatMessage
from route_planner.models import UserPreference

logger = logging.getLogger(__name__)

# 使用 OrderedDict 实现 LRU 缓存，防止内存泄漏
_memory_store: OrderedDict = OrderedDict()
_MAX_SESSIONS = getattr(settings, 'MEMORY_STORE_MAX_SESSIONS', 200)
_MAX_MESSAGES = getattr(settings, 'MEMORY_STORE_MAX_MESSAGES', 40)

SYSTEM_PROMPT = """你是「路线大师」，专注厦门的运动路线规划AI助手。
能力：根据用户时间、目标、身体状态规划个性化路线，熟悉厦门所有运动场所。
支持：跑步、散步、骑行、徒步，考虑健康状况（脚踝/膝盖不适）。
重要：所有路线只在厦门岛内及周边规划，不会规划需要坐船/渡轮的跨岛路线。

当用户描述运动需求时，在回复末尾加 [PLAN_ROUTE]，系统会自动调用高德API生成真实路线地图。
回答简洁、实用，使用中文，适当使用Markdown格式。
注意：路线时间是按跑步/骑行配速计算的，不是步行时间。"""


def get_client():
    return OpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url=settings.DEEPSEEK_BASE_URL)


def _json_response(data, status=200):
    """统一 JSON 响应（CORS 由中间件处理）"""
    return JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})


def get_or_create_session(session_id: str) -> ChatSession:
    session, _ = ChatSession.objects.get_or_create(session_id=session_id)
    return session


def load_history_from_db(session_id: str) -> list:
    try:
        session = ChatSession.objects.get(session_id=session_id)
        msgs = session.messages.filter(role__in=['user', 'assistant']).order_by('created_at')
        return [{'role': m.role, 'content': m.content} for m in msgs]
    except ChatSession.DoesNotExist:
        return []


def save_message_to_db(session_id: str, role: str, content: str, extra_data: dict = None):
    try:
        session = get_or_create_session(session_id)
        ChatMessage.objects.create(
            session=session,
            role=role,
            content=content,
            extra_data=extra_data or {}
        )
        # 仅在用户消息且标题为空时更新标题
        update_fields = ['updated_at']
        if role == 'user' and not session.title:
            session.title = content[:30]
            update_fields.append('title')
        session.save(update_fields=update_fields)
    except Exception as e:
        logger.error(f"[DB] 保存消息失败: {e}")


def get_history(session_id: str) -> list:
    if session_id not in _memory_store:
        _memory_store[session_id] = load_history_from_db(session_id)
    else:
        # LRU: 移到末尾
        _memory_store.move_to_end(session_id)
    return _memory_store.get(session_id, [])


def add_msg(session_id: str, role: str, content: str, extra_data: dict = None):
    if session_id not in _memory_store:
        _memory_store[session_id] = []
    else:
        _memory_store.move_to_end(session_id)

    _memory_store[session_id].append({'role': role, 'content': content})

    # 限制每个会话的消息数
    if len(_memory_store[session_id]) > _MAX_MESSAGES:
        _memory_store[session_id] = _memory_store[session_id][-_MAX_MESSAGES:]

    # LRU 淘汰：超过最大会话数时移除最旧的
    while len(_memory_store) > _MAX_SESSIONS:
        _memory_store.popitem(last=False)

    save_message_to_db(session_id, role, content, extra_data)


ROUTE_KEYWORDS = ['跑步', '散步', '骑行', '健步走', '徒步', '慢跑', '快走', '运动', '锻炼']
ORIGIN_KEYWORDS = ['从', '出发', '起点', '在']


def needs_route_planning(text: str) -> bool:
    has_activity = any(kw in text for kw in ROUTE_KEYWORDS)
    has_origin = any(kw in text for kw in ORIGIN_KEYWORDS)
    return has_activity and has_origin


@csrf_exempt
def chat_message(request):
    """POST /api/chat/message/ - SSE流式响应"""
    if request.method == 'OPTIONS':
        return _json_response({})
    if request.method != 'POST':
        return _json_response({'error': '仅支持POST'}, 405)

    try:
        body = json.loads(request.body)
    except Exception as e:
        return _json_response({'error': f'JSON格式错误: {e}'}, 400)

    user_msg = body.get('message', '').strip()
    session_id = body.get('session_id', 'default')
    origin = body.get('origin', '').strip()

    if not user_msg:
        return _json_response({'error': '消息不能为空'}, 400)
    if len(user_msg) > 500:
        return _json_response({'error': '消息长度不能超过500字'}, 400)

    add_msg(session_id, 'user', user_msg)
    history = get_history(session_id)

    memory_context = ''
    try:
        pref, _ = UserPreference.objects.get_or_create(session_id=session_id)
        memory_context = pref.get_context_string()
    except Exception as e:
        logger.warning(f"[Chat] 获取用户偏好失败: {e}")

    system_with_memory = SYSTEM_PROMPT
    if memory_context and '第一次' not in memory_context:
        system_with_memory += f"\n\n[用户历史偏好] {memory_context}"

    messages_to_send = [{'role': 'system', 'content': system_with_memory}] + history

    def stream_gen():
        client = get_client()
        full_response = ''
        need_plan = False
        route_data = None
        rag_docs = []

        direct_plan = needs_route_planning(user_msg) or (origin and any(kw in user_msg for kw in ROUTE_KEYWORDS))

        if direct_plan:
            try:
                from route_planner.agent import plan_route_with_agent
                origin_name = origin if origin else None
                if not origin_name:
                    m = re.search(r'从(.{2,12}?)(?:出发|起)', user_msg)
                    if m:
                        origin_name = m.group(1)

                result = plan_route_with_agent(
                    user_query=user_msg,
                    session_id=session_id,
                    origin_name=origin_name,
                )
                if result.get('success'):
                    route_data = result
                    rag_docs = result.get('rag_docs', [])
                    yield f"data: {json.dumps({'type': 'route_plan', 'route_data': result}, ensure_ascii=False)}\n\n"
                    if rag_docs:
                        yield f"data: {json.dumps({'type': 'rag_context', 'docs': rag_docs}, ensure_ascii=False)}\n\n"
                    route = result.get('route', {})
                    route_summary = (
                        f"已完成路线规划：{route.get('total_distance_km')}km，"
                        f"按{route.get('activity_type', '跑步')}配速预计{route.get('total_duration_min')}分钟，"
                        f"途经{[w['name'] for w in result.get('waypoints', [])]}，"
                        f"推荐语：{result.get('recommendation', '')}"
                    )
                    messages_to_send.append({'role': 'system', 'content': route_summary})
                elif result.get('error'):
                    yield f"data: {json.dumps({'type': 'error', 'error': result['error']}, ensure_ascii=False)}\n\n"
            except Exception as e:
                logger.error(f"[Chat] 路线规划失败: {e}")
                yield f"data: {json.dumps({'type': 'error', 'error': f'路线规划失败: {str(e)}'}, ensure_ascii=False)}\n\n"

        try:
            stream = client.chat.completions.create(
                model=settings.DEEPSEEK_MODEL,
                messages=messages_to_send,
                temperature=0.7,
                max_tokens=1000,
                stream=True,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    full_response += token
                    if '[PLAN_ROUTE]' in full_response:
                        need_plan = True
                        token = token.replace('[PLAN_ROUTE]', '')
                    if token:
                        yield f"data: {json.dumps({'type': 'token', 'content': token}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"[Chat] DeepSeek失败: {e}")
            fallback = route_data.get('recommendation', '路线规划完成，请查看地图。') if route_data else f'抱歉，AI响应失败：{str(e)}'
            full_response = fallback
            yield f"data: {json.dumps({'type': 'token', 'content': fallback}, ensure_ascii=False)}\n\n"

        clean_response = full_response.replace('[PLAN_ROUTE]', '').strip()

        if need_plan and not route_data:
            try:
                from route_planner.agent import plan_route_with_agent, parse_user_intent
                params = parse_user_intent(user_msg)
                origin_name = origin or params.get('origin')
                result = plan_route_with_agent(user_query=user_msg, session_id=session_id, origin_name=origin_name)
                if result.get('success'):
                    route_data = result
                    rag_docs = result.get('rag_docs', [])
                    yield f"data: {json.dumps({'type': 'route_plan', 'route_data': result}, ensure_ascii=False)}\n\n"
                    if rag_docs:
                        yield f"data: {json.dumps({'type': 'rag_context', 'docs': rag_docs}, ensure_ascii=False)}\n\n"
            except Exception as e:
                logger.error(f"[Chat] 延迟路线规划失败: {e}")

        extra = {}
        if route_data:
            extra['route_data'] = route_data
            extra['agent_steps'] = route_data.get('agent_steps', [])
            extra['rag_docs'] = rag_docs
        add_msg(session_id, 'assistant', clean_response, extra)

        # 更新用户偏好（统一使用 'params' 键）
        try:
            pref, _ = UserPreference.objects.get_or_create(session_id=session_id)
            parsed = {}
            if route_data:
                parsed = route_data.get('params', route_data.get('parsed_params', {}))
            if not parsed:
                parsed = {'activity_type': '对话', 'duration_min': None, 'preferred_features': []}
            pref.add_query(user_msg, parsed, route_data.get('recommendation', '') if route_data else '')
            logger.info(f"[记忆] session={session_id} 已更新，总计{pref.session_count}次")
        except Exception as e:
            logger.error(f"[记忆] 更新失败: {e}")

        yield f"data: {json.dumps({'type': 'done', 'session_id': session_id}, ensure_ascii=False)}\n\n"

    resp = StreamingHttpResponse(stream_gen(), content_type='text/event-stream; charset=utf-8')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'
    return resp


@csrf_exempt
def session_list(request):
    if request.method == 'OPTIONS':
        return _json_response({})
    try:
        sessions = ChatSession.objects.all().order_by('-updated_at')[:50]
        data = []
        for s in sessions:
            last_user_msg = s.messages.filter(role='user').last()
            data.append({
                'session_id': s.session_id,
                'title': s.title or (last_user_msg.content[:30] if last_user_msg else '新对话'),
                'created_at': s.created_at.isoformat(),
                'updated_at': s.updated_at.isoformat(),
                'message_count': s.messages.count(),
            })
        return _json_response({'success': True, 'sessions': data})
    except Exception as e:
        logger.error(f"[sessions] 失败: {e}")
        return _json_response({'error': str(e)}, 500)


@csrf_exempt
def chat_history(request):
    if request.method == 'OPTIONS':
        return _json_response({})
    session_id = request.GET.get('session_id', 'default')
    try:
        session = ChatSession.objects.get(session_id=session_id)
        msgs = session.messages.filter(role__in=['user', 'assistant']).order_by('created_at')
        return _json_response({
            'success': True,
            'session_id': session_id,
            'messages': [m.to_dict() for m in msgs],
            'count': msgs.count(),
        })
    except ChatSession.DoesNotExist:
        return _json_response({'success': True, 'session_id': session_id, 'messages': [], 'count': 0})
    except Exception as e:
        return _json_response({'error': str(e)}, 500)


@csrf_exempt
def clear_session(request):
    if request.method == 'OPTIONS':
        return _json_response({})
    try:
        body = json.loads(request.body) if request.body else {}
    except Exception:
        body = {}
    session_id = body.get('session_id', 'default')
    if session_id in _memory_store:
        del _memory_store[session_id]
    ChatSession.objects.filter(session_id=session_id).delete()
    return _json_response({'success': True, 'message': f'会话 {session_id} 已清空'})


@csrf_exempt
def user_memory(request):
    if request.method == 'OPTIONS':
        return _json_response({})
    session_id = request.GET.get('session_id', 'default')
    try:
        pref, _ = UserPreference.objects.get_or_create(session_id=session_id)
        return _json_response({
            'success': True,
            'session_id': session_id,
            'session_count': pref.session_count,
            'activity_stats': pref.activity_stats,
            'preference_stats': pref.preference_stats,
            'recent_queries': pref.recent_queries[:10],
            'common_duration': pref.common_duration,
            'last_activity_type': pref.last_activity_type,
            'context_string': pref.get_context_string(),
        })
    except Exception as e:
        return _json_response({'error': str(e)}, 500)
