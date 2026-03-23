from django.urls import path, include, re_path
from django.http import HttpResponse
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def serve_frontend(request):
    """直接返回index.html静态文件，不经过Django模板引擎（避免Vue {{}} 语法冲突）"""
    base_dir = Path(__file__).resolve().parent.parent
    index_path = base_dir / 'templates' / 'index.html'
    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return HttpResponse(content, content_type='text/html; charset=utf-8')
    except FileNotFoundError:
        logger.error(f"[Frontend] 模板文件不存在: {index_path}")
        return HttpResponse(
            '<h1>503 服务暂不可用</h1><p>前端页面正在部署中，请稍后刷新重试。</p>',
            content_type='text/html; charset=utf-8',
            status=503,
        )
    except Exception as e:
        logger.error(f"[Frontend] 读取模板失败: {e}")
        return HttpResponse(
            '<h1>500 内部错误</h1><p>加载页面时发生错误，请联系管理员。</p>',
            content_type='text/html; charset=utf-8',
            status=500,
        )


urlpatterns = [
    path('api/route/', include('route_planner.urls')),
    path('api/chat/', include('chat.urls')),
    # 前端页面：所有非API路由都返回index.html
    re_path(r'^(?!api/).*$', serve_frontend, name='frontend'),
]
