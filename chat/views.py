"""
chat/views.py
- DeepSeek 多轮对话（SSE流式输出）
- 历史对话持久化写入 PostgreSQL（ChatSession + ChatMessage）
- 会话列表接口
- 长期记忆自动更新（UserPreference.add_query）
- RAG上下文注入
"""
import json
import logging
import re

from django.http import StreamingHttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from openai import OpenAI

from .models import ChatSession, ChatMessage
from route_planner.models import UserPreference

logger = logging.getLogger(__name__)

# ===== 内存缓存（加速多轮对话，同时持久化到DB） =====
_memory_store: dict = {}

SYSTEM_PROMPT = """你是「路线大师」，专注厦门的运动路线规划AI助手。
能力：根据用户时间、目标、身体状态规划个性化路线，熟悉厦门运动场所。
支持：跑步、散步、骑行、徒步，考虑健康状况（脚踝/膝盖不适）。

当用户描述运动需求并提到起点时，在回复末尾加 [PLAN_ROUTE]，系统会自动调用高德API生成路线地图。
回答简洁、实用，使用中文，适当使用Markdown格式。"""


def get_client():
    return OpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url=settings.DEEPSEEK_BASE_URL)


# ===== DB操作 =====
def get_or_create_session(session_id: str) -> ChatSession:
    session, _ = ChatSession.objects.get_or_create(session_id=session_id)
    return session


def load_history_from_db(session_id: str) -> list:
    """从DB加载历史消息到内存"""
    try:
        session = ChatSession.objects.get(session_id=session_id)
        msgs = session.messages.filter(role__in=['user', 'assistant']).order_by('created_at')
        return [{'role': m.role, 'content': m.content} for m in msgs]
    except ChatSession.DoesNotExist:
        return []


def save_message_to_db(session_id: str, role: str, content: str, extra_data: dict = None):
    """保存消息到PostgreSQL"""
    try:
        session = get_or_create_session(session_id)
        ChatMessage.objects.create(
            session=session,
            role=role,
            content=content,
            extra_data=extra_data or {}
        )
        # 更新会话标题（取第一条用户消息前30字）
        if role == 'user' and not session.title:
            session.title = content[:30]
        session.save(update_fields=['title', 'updated_at'])
    except Exception as e:
        logger.error(f"[DB] 保存消息失败: {e}")


def get_history(session_id: str) -> list:
    """优先从内存，若无则从DB加载"""
    if session_id not in _memory_store:
        _memory_store[session_id] = load_history_from_db(session_id)
    return _memory_store.get(session_id, [])


def add_msg(session_id: str, role: str, content: str, extra_data: dict = None):
    """添加消息到内存+DB"""
    if session_id not in _memory_store:
        _memory_store[session_id] = []
    _memory_store[session_id].append({'role': role, 'content': content})
    if len(_memory_store[session_id]) > 40:
        _memory_store[session_id] = _memory_store[session_id][-40:]
    save_message_to_db(session_id, role, content, extra_data)


def cors_resp(data, status=200):
    r = JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})
    r['Access-Control-Allow-Origin'] = '*'
    r['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    r['Access-Control-Allow-Headers'] = 'Content-Type'
    return r


# ===== 判断是否需要路线规划 =====
ROUTE_KEYWORDS = ['跑步', '散步', '骑行', '健步走', '徒步', '慢跑', '快走']
ORIGIN_KEYWORDS = ['从', '出发', '起点']


def needs_route_planning(text: str) -> bool:
    has_activity = any(kw in text for kw in ROUTE_KEYWORDS)
    has_origin = any(kw in text for kw in ORIGIN_KEYWORDS)
    return has_activity and has_origin


# ===== 主对话接口 =====
@csrf_exempt
def chat_message(request):
    """POST /api/chat/message/ - SSE流式响应"""
    if request.method == 'OPTIONS':
        r = JsonResponse({})
        r['Access-Control-Allow-Origin'] = '*'
        r['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        r['Access-Control-Allow-Headers'] = 'Content-Type'
        return r
    if request.method != 'POST':
        return cors_resp({'error': '仅支持POST'}, 405)

    try:
        body = json.loads(request.body)
    except Exception as e:
        return cors_resp({'error': f'JSON错误: {e}'}, 400)

    user_msg = body.get('message', '').strip()
    session_id = body.get('session_id', 'default')
    origin = body.get('origin', '').strip()

    if not user_msg:
        return cors_resp({'error': '消息不能为空'}, 400)

    # 保存用户消息（内存+DB）
    add_msg(session_id, 'user', user_msg)
    history = get_history(session_id)

    # 获取用户长期记忆上下文
    memory_context = ''
    try:
        pref, _ = UserPreference.objects.get_or_create(session_id=session_id)
        memory_context = pref.get_context_string()
    except Exception:
        pass

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

        # 先检查是否直接触发路线规划（含起点关键词）
        direct_plan = needs_route_planning(user_msg) or (origin and any(kw in user_msg for kw in ROUTE_KEYWORDS))

        if direct_plan:
            try:
                from route_planner.agent import plan_route_with_agent, parse_user_intent
                # 提取起点
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
                    # 发送路线数据事件
                    yield f"data: {json.dumps({'type': 'route_plan', 'route_data': result}, ensure_ascii=False)}\n\n"
                    # 发送RAG上下文
                    if rag_docs:
                        yield f"data: {json.dumps({'type': 'rag_context', 'docs': rag_docs}, ensure_ascii=False)}\n\n"
                    # 把路线结果注入对话上下文
                    route_summary = (
                        f"已完成路线规划：{result.get('route', {}).get('total_distance_km')}km，"
                        f"途经{[w['name'] for w in result.get('waypoints', [])]}，"
                        f"推荐语：{result.get('recommendation', '')}"
                    )
                    messages_to_send.append({'role': 'system', 'content': route_summary})
            except Exception as e:
                logger.error(f"[Chat] 路线规划失败: {e}")
                yield f"data: {json.dumps({'type': 'error', 'error': f'路线规划失败: {str(e)}'}, ensure_ascii=False)}\n\n"

        # DeepSeek 流式输出
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

        # 如果DeepSeek要求规划但之前没有直接规划
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

        # 保存AI回复到DB（含路线数据）
        extra = {}
        if route_data:
            extra['route_data'] = route_data
            extra['agent_steps'] = route_data.get('agent_steps', [])
            extra['rag_docs'] = rag_docs
        add_msg(session_id, 'assistant', clean_response, extra)

        # ===== 更新长期记忆 =====
        try:
            pref, _ = UserPreference.objects.get_or_create(session_id=session_id)
            parsed = route_data.get('parsed_params', {}) if route_data else {}
            if not parsed:
                # 简单提取
                parsed = {'activity_type': '对话', 'duration_min': None, 'preferred_features': []}
            pref.add_query(user_msg, parsed, route_data.get('recommendation', '') if route_data else '')
            logger.info(f"[记忆] session={session_id} 已更新，总计{pref.session_count}次")
        except Exception as e:
            logger.error(f"[记忆] 更新失败: {e}")

        yield f"data: {json.dumps({'type': 'done', 'session_id': session_id}, ensure_ascii=False)}\n\n"

    resp = StreamingHttpResponse(stream_gen(), content_type='text/event-stream; charset=utf-8')
    resp['Access-Control-Allow-Origin'] = '*'
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'
    return resp


# ===== 会话列表接口 =====
@csrf_exempt
def session_list(request):
    """GET /api/chat/sessions/ - 返回所有会话列表"""
    if request.method == 'OPTIONS':
        r = JsonResponse({})
        r['Access-Control-Allow-Origin'] = '*'
        return r
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
        return cors_resp({'success': True, 'sessions': data})
    except Exception as e:
        logger.error(f"[sessions] 失败: {e}")
        return cors_resp({'error': str(e)}, 500)


# ===== 历史消息接口 =====
@csrf_exempt
def chat_history(request):
    """GET /api/chat/history/?session_id=xxx"""
    if request.method == 'OPTIONS':
        return cors_resp({})
    session_id = request.GET.get('session_id', 'default')
    try:
        session = ChatSession.objects.get(session_id=session_id)
        msgs = session.messages.filter(role__in=['user', 'assistant']).order_by('created_at')
        return cors_resp({
            'success': True,
            'session_id': session_id,
            'messages': [m.to_dict() for m in msgs],
            'count': msgs.count(),
        })
    except ChatSession.DoesNotExist:
        return cors_resp({'success': True, 'session_id': session_id, 'messages': [], 'count': 0})
    except Exception as e:
        return cors_resp({'error': str(e)}, 500)


# ===== 清空会话 =====
@csrf_exempt
def clear_session(request):
    """POST /api/chat/clear/"""
    if request.method == 'OPTIONS':
        return cors_resp({})
    try:
        body = json.loads(request.body) if request.body else {}
    except Exception:
        body = {}
    session_id = body.get('session_id', 'default')
    if session_id in _memory_store:
        del _memory_store[session_id]
    ChatSession.objects.filter(session_id=session_id).delete()
    return cors_resp({'success': True, 'message': f'会话 {session_id} 已清空'})


# ===== 用户记忆接口 =====
@csrf_exempt
def user_memory(request):
    """GET /api/chat/memory/?session_id=xxx"""
    if request.method == 'OPTIONS':
        return cors_resp({})
    session_id = request.GET.get('session_id', 'default')
    try:
        pref, _ = UserPreference.objects.get_or_create(session_id=session_id)
        return cors_resp({
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
        return cors_resp({'error': str(e)}, 500)
