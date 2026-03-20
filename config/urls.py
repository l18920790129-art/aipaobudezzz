from django.urls import path, include, re_path
from django.views.generic import TemplateView

urlpatterns = [
    path('api/route/', include('route_planner.urls')),
    path('api/chat/', include('chat.urls')),
    # 前端页面：所有非API路由都返回index.html（SPA支持）
    re_path(r'^(?!api/).*$', TemplateView.as_view(template_name='index.html'), name='frontend'),
]
