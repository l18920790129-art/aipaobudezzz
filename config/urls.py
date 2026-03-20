from django.urls import path, include

urlpatterns = [
    path('api/route/', include('route_planner.urls')),
    path('api/chat/', include('chat.urls')),
]
