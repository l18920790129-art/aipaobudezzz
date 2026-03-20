from django.urls import path, include, re_path
from django.http import HttpResponse
from pathlib import Path


def serve_frontend(request):
    """直接返回index.html静态文件，不经过Django模板引擎（避免Vue {{}} 语法冲突）"""
    base_dir = Path(__file__).resolve().parent.parent
    index_path = base_dir / 'templates' / 'index.html'
    with open(index_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return HttpResponse(content, content_type='text/html; charset=utf-8')


urlpatterns = [
    path('api/route/', include('route_planner.urls')),
    path('api/chat/', include('chat.urls')),
    # 前端页面：所有非API路由都返回index.html（直接读文件，不经过Django模板引擎）
    re_path(r'^(?!api/).*$', serve_frontend, name='frontend'),
]
