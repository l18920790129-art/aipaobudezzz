from django.urls import path
from . import views

urlpatterns = [
    path('plan/', views.plan_route, name='plan_route'),
    path('pois/', views.search_pois, name='search_pois'),
    path('geocode/', views.geocode_address, name='geocode_address'),
    path('kg/', views.knowledge_graph_api, name='knowledge_graph'),
    path('history/', views.route_history_api, name='route_history'),
    path('health/', views.health_check, name='health_check'),
]
