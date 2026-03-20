"""
route_planner/models.py - PostgreSQL 地理实体存储
"""
from django.db import models


class PoiEntity(models.Model):
    """高德POI地理实体（缓存，减少API调用）"""
    poi_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=500, blank=True)
    category = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=50, default='厦门')
    gcj_lng = models.FloatField()
    gcj_lat = models.FloatField()
    wgs_lng = models.FloatField(null=True, blank=True)
    wgs_lat = models.FloatField(null=True, blank=True)
    tel = models.CharField(max_length=100, blank=True)
    poi_type = models.CharField(max_length=50, default='general')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'poi_entities'
        indexes = [
            models.Index(fields=['city', 'poi_type']),
            models.Index(fields=['name']),
        ]

    def to_dict(self):
        return {
            'poi_id': self.poi_id,
            'name': self.name,
            'address': self.address,
            'category': self.category,
            'city': self.city,
            'location': {'lng': self.gcj_lng, 'lat': self.gcj_lat},
            'poi_type': self.poi_type,
        }


class RouteHistory(models.Model):
    """用户路线规划历史"""
    session_id = models.CharField(max_length=64, db_index=True)
    user_query = models.TextField()
    parsed_params = models.JSONField(default=dict)
    origin_name = models.CharField(max_length=200, blank=True)
    origin_lng = models.FloatField(null=True, blank=True)
    origin_lat = models.FloatField(null=True, blank=True)
    route_result = models.JSONField(default=dict)
    ai_response = models.TextField(blank=True)
    total_time_s = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'route_history'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['session_id', '-created_at'])]


class UserPreference(models.Model):
    """用户长期偏好记忆"""
    session_id = models.CharField(max_length=64, unique=True, db_index=True)
    session_count = models.IntegerField(default=0)
    preference_stats = models.JSONField(default=dict)
    activity_stats = models.JSONField(default=dict)
    recent_queries = models.JSONField(default=list)
    common_duration = models.IntegerField(null=True, blank=True)
    last_activity_type = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_preferences'

    def add_query(self, query: str, params: dict, recommended_route: str = ''):
        self.session_count += 1
        activity = params.get('activity_type', '跑步')
        self.activity_stats[activity] = self.activity_stats.get(activity, 0) + 1
        self.last_activity_type = activity
        for feature in params.get('preferred_features', []):
            self.preference_stats[feature] = self.preference_stats.get(feature, 0) + 1
        if params.get('duration_min'):
            self.common_duration = params['duration_min']
        entry = {
            'query': query[:100],
            'activity': activity,
            'duration': params.get('duration_min', 0),
            'recommended': recommended_route,
        }
        queries = self.recent_queries or []
        queries.insert(0, entry)
        self.recent_queries = queries[:20]
        self.save()

    def get_context_string(self) -> str:
        if self.session_count == 0:
            return "这是用户第一次使用，暂无历史偏好数据。"
        parts = [f"用户已使用{self.session_count}次。"]
        if self.activity_stats:
            top_activity = max(self.activity_stats, key=self.activity_stats.get)
            parts.append(f"最常进行的活动是{top_activity}。")
        if self.preference_stats:
            top_prefs = sorted(self.preference_stats.items(), key=lambda x: x[1], reverse=True)[:3]
            pref_names = {'scenic': '风景', 'shade': '树荫', 'sea_view': '海景',
                          'park': '公园', 'water': '水站', 'soft_surface': '软路面'}
            pref_str = '、'.join([pref_names.get(k, k) for k, _ in top_prefs])
            parts.append(f"偏好特征：{pref_str}。")
        if self.common_duration:
            parts.append(f"常用运动时长约{self.common_duration}分钟。")
        return ''.join(parts)


# ===== 知识图谱模型 =====
class KGNode(models.Model):
    """知识图谱节点（厦门地点实体）"""
    NODE_TYPES = [
        ('海滩', '海滩'), ('公园', '公园'), ('学校', '学校'),
        ('景区', '景区'), ('体育', '体育场馆'), ('商业', '商业区'),
        ('交通', '交通枢纽'), ('山地', '山地'), ('湿地', '湿地'),
    ]
    node_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200, db_index=True)
    node_type = models.CharField(max_length=20, choices=NODE_TYPES)
    description = models.TextField(blank=True)
    gcj_lng = models.FloatField(null=True, blank=True)
    gcj_lat = models.FloatField(null=True, blank=True)
    city = models.CharField(max_length=50, default='厦门')
    # 运动属性
    suitable_activities = models.JSONField(default=list)   # ['跑步','散步','骑行']
    surface_types = models.JSONField(default=list)          # ['软路面','木栈道','塑胶']
    features = models.JSONField(default=list)               # ['海景','树荫','无障碍']
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'kg_nodes'
        indexes = [models.Index(fields=['city', 'node_type'])]

    def to_dict(self):
        return {
            'id': self.node_id,
            'name': self.name,
            'type': self.node_type,
            'description': self.description,
            'lng': self.gcj_lng,
            'lat': self.gcj_lat,
            'suitable_activities': self.suitable_activities,
            'features': self.features,
        }


class KGEdge(models.Model):
    """知识图谱边（地点间关系）"""
    RELATION_TYPES = [
        ('相邻', '地理相邻'),
        ('路线连接', '路线连接'),
        ('属于', '行政归属'),
        ('适合同行', '适合同一活动'),
        ('步行可达', '步行可达'),
        ('骑行可达', '骑行可达'),
    ]
    source = models.ForeignKey(KGNode, on_delete=models.CASCADE, related_name='out_edges')
    target = models.ForeignKey(KGNode, on_delete=models.CASCADE, related_name='in_edges')
    relation = models.CharField(max_length=20, choices=RELATION_TYPES)
    weight = models.FloatField(default=1.0)   # 关系强度/距离
    description = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'kg_edges'
        unique_together = [('source', 'target', 'relation')]

    def to_dict(self):
        return {
            'id': self.pk,
            'source_id': self.source.node_id,
            'source_name': self.source.name,
            'target_id': self.target.node_id,
            'target_name': self.target.name,
            'relation': self.relation,
            'weight': self.weight,
        }
