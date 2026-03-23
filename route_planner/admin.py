from django.contrib import admin
from .models import PoiEntity, RouteHistory, UserPreference, KGNode, KGEdge


@admin.register(PoiEntity)
class PoiEntityAdmin(admin.ModelAdmin):
    list_display = ('name', 'city', 'category', 'poi_type', 'created_at')
    list_filter = ('city', 'poi_type', 'category')
    search_fields = ('name', 'address')


@admin.register(RouteHistory)
class RouteHistoryAdmin(admin.ModelAdmin):
    list_display = ('session_id', 'user_query', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('session_id', 'user_query')


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ('session_id', 'session_count', 'last_activity_type', 'common_duration', 'updated_at')
    search_fields = ('session_id',)


@admin.register(KGNode)
class KGNodeAdmin(admin.ModelAdmin):
    list_display = ('name', 'node_type', 'city', 'gcj_lat', 'gcj_lng', 'created_at')
    list_filter = ('node_type', 'city')
    search_fields = ('name', 'description')


@admin.register(KGEdge)
class KGEdgeAdmin(admin.ModelAdmin):
    list_display = ('source', 'target', 'relation', 'weight', 'created_at')
    list_filter = ('relation',)
