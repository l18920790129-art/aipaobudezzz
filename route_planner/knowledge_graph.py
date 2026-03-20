"""
knowledge_graph.py - 厦门地点知识图谱
使用 NetworkX 构建图结构，持久化到 PostgreSQL（KGNode + KGEdge）
"""
import logging
import networkx as nx
from django.db import transaction

logger = logging.getLogger(__name__)

# ===== 厦门核心地点数据（真实地理信息）=====
XIAMEN_NODES = [
    {
        'node_id': 'baicheng_beach',
        'name': '白城沙滩',
        'node_type': '海滩',
        'description': '厦门大学附近的天然沙滩，沙质细腻，海景开阔，是跑步和散步的热门起点。',
        'gcj_lng': 118.100875, 'gcj_lat': 24.432281,
        'suitable_activities': ['跑步', '散步', '健步走'],
        'surface_types': ['沙滩', '木栈道'],
        'features': ['海景', '日出', '无障碍'],
    },
    {
        'node_id': 'xiamen_university',
        'name': '厦门大学南门',
        'node_type': '学校',
        'description': '中国最美大学之一，校园内有环形跑道和绿荫步道，适合晨跑。',
        'gcj_lng': 118.097, 'gcj_lat': 24.443,
        'suitable_activities': ['跑步', '散步', '骑行'],
        'surface_types': ['塑胶跑道', '沥青路', '石板路'],
        'features': ['树荫', '历史建筑', '安静'],
    },
    {
        'node_id': 'huandao_road',
        'name': '环岛路',
        'node_type': '景区',
        'description': '厦门最著名的滨海大道，全长约43km，沿途海景壮观，设有专用骑行道和跑步道。',
        'gcj_lng': 118.148, 'gcj_lat': 24.419,
        'suitable_activities': ['跑步', '骑行', '散步'],
        'surface_types': ['塑胶跑道', '沥青路'],
        'features': ['海景', '骑行道', '观景台'],
    },
    {
        'node_id': 'wanshishan',
        'name': '万石山植物园',
        'node_type': '公园',
        'description': '厦门最大的植物园，山地地形，有多条登山步道，适合爬山和徒步。',
        'gcj_lng': 118.108, 'gcj_lat': 24.463,
        'suitable_activities': ['徒步', '爬山', '散步'],
        'surface_types': ['石阶', '土路', '木栈道'],
        'features': ['树荫', '植物', '山景', '清凉'],
    },
    {
        'node_id': 'wuyuan_wetland',
        'name': '五缘湾湿地公园',
        'node_type': '湿地',
        'description': '厦门岛北部的湿地公园，环湖步道约5km，地势平坦，适合轻松散步和骑行。',
        'gcj_lng': 118.156, 'gcj_lat': 24.513,
        'suitable_activities': ['散步', '骑行', '健步走'],
        'surface_types': ['木栈道', '沥青路', '软路面'],
        'features': ['湿地', '候鸟', '平坦', '树荫'],
    },
    {
        'node_id': 'zengtuo_an',
        'name': '曾厝垵',
        'node_type': '商业',
        'description': '厦门著名的文艺渔村，小巷众多，适合慢跑探索，周边有海滩。',
        'gcj_lng': 118.131, 'gcj_lat': 24.426,
        'suitable_activities': ['散步', '慢跑'],
        'surface_types': ['石板路', '沥青路'],
        'features': ['文艺', '海景', '美食', '人文'],
    },
    {
        'node_id': 'hulishan_fort',
        'name': '胡里山炮台',
        'node_type': '景区',
        'description': '历史文化景区，周边有海滨步道，可俯瞰台湾海峡。',
        'gcj_lng': 118.118, 'gcj_lat': 24.428,
        'suitable_activities': ['散步', '健步走'],
        'surface_types': ['石板路', '沥青路'],
        'features': ['历史', '海景', '炮台'],
    },
    {
        'node_id': 'lianxin_garden',
        'name': '连心园',
        'node_type': '公园',
        'description': '白城沙滩附近的小型公园，绿化好，有软路面步道，适合脚踝不适者。',
        'gcj_lng': 118.107, 'gcj_lat': 24.436,
        'suitable_activities': ['散步', '慢跑', '健步走'],
        'surface_types': ['软路面', '草地', '木栈道'],
        'features': ['树荫', '安静', '软路面', '无障碍'],
    },
    {
        'node_id': 'haha_park',
        'name': '哈哈公园',
        'node_type': '公园',
        'description': '厦门大学附近的休闲公园，设施完善，适合家庭运动。',
        'gcj_lng': 118.105, 'gcj_lat': 24.440,
        'suitable_activities': ['散步', '跑步', '健步走'],
        'surface_types': ['塑胶跑道', '软路面'],
        'features': ['设施完善', '树荫', '儿童友好'],
    },
    {
        'node_id': 'nanputuo_temple',
        'name': '南普陀寺',
        'node_type': '景区',
        'description': '厦门著名寺庙，背靠五老峰，有登山步道，适合徒步。',
        'gcj_lng': 118.097, 'gcj_lat': 24.449,
        'suitable_activities': ['徒步', '散步'],
        'surface_types': ['石阶', '石板路'],
        'features': ['文化', '山景', '登山'],
    },
]

# ===== 地点间关系 =====
XIAMEN_EDGES = [
    ('baicheng_beach', 'lianxin_garden', '步行可达', 0.8, '步行约10分钟'),
    ('baicheng_beach', 'haha_park', '步行可达', 1.2, '步行约15分钟'),
    ('baicheng_beach', 'hulishan_fort', '步行可达', 1.5, '步行约20分钟'),
    ('baicheng_beach', 'zengtuo_an', '骑行可达', 3.0, '骑行约15分钟'),
    ('baicheng_beach', 'huandao_road', '路线连接', 2.0, '经环岛路相连'),
    ('xiamen_university', 'baicheng_beach', '步行可达', 1.0, '步行约12分钟'),
    ('xiamen_university', 'nanputuo_temple', '步行可达', 0.5, '步行约6分钟'),
    ('xiamen_university', 'wanshishan', '步行可达', 2.0, '步行约25分钟'),
    ('lianxin_garden', 'haha_park', '相邻', 0.4, '相邻公园'),
    ('lianxin_garden', 'xiamen_university', '步行可达', 1.5, '步行约18分钟'),
    ('huandao_road', 'zengtuo_an', '路线连接', 1.0, '环岛路途经'),
    ('huandao_road', 'hulishan_fort', '路线连接', 0.8, '环岛路途经'),
    ('wuyuan_wetland', 'huandao_road', '骑行可达', 8.0, '骑行约30分钟'),
    ('wanshishan', 'nanputuo_temple', '步行可达', 1.0, '步行约12分钟'),
    ('zengtuo_an', 'hulishan_fort', '步行可达', 1.2, '步行约15分钟'),
    # 适合同行关系
    ('baicheng_beach', 'lianxin_garden', '适合同行', 1.0, '同适合脚踝不适者'),
    ('wuyuan_wetland', 'lianxin_garden', '适合同行', 1.0, '同适合平坦路线'),
    ('huandao_road', 'wuyuan_wetland', '适合同行', 1.0, '同适合骑行'),
]


def build_networkx_graph() -> nx.DiGraph:
    """构建 NetworkX 有向图"""
    G = nx.DiGraph()
    for node in XIAMEN_NODES:
        G.add_node(
            node['node_id'],
            name=node['name'],
            node_type=node['node_type'],
            features=node['features'],
            suitable_activities=node['suitable_activities'],
        )
    for src, tgt, rel, weight, desc in XIAMEN_EDGES:
        G.add_edge(src, tgt, relation=rel, weight=weight, description=desc)
    return G


def init_knowledge_graph():
    """初始化知识图谱到PostgreSQL（幂等操作）"""
    from .models import KGNode, KGEdge

    # 检查是否已初始化
    if KGNode.objects.count() >= len(XIAMEN_NODES):
        logger.info("[KG] 知识图谱已存在，跳过初始化")
        return True

    logger.info("[KG] 开始初始化厦门知识图谱...")
    try:
        with transaction.atomic():
            # 写入节点
            node_map = {}
            for nd in XIAMEN_NODES:
                obj, created = KGNode.objects.update_or_create(
                    node_id=nd['node_id'],
                    defaults={
                        'name': nd['name'],
                        'node_type': nd['node_type'],
                        'description': nd['description'],
                        'gcj_lng': nd['gcj_lng'],
                        'gcj_lat': nd['gcj_lat'],
                        'suitable_activities': nd['suitable_activities'],
                        'surface_types': nd['surface_types'],
                        'features': nd['features'],
                    }
                )
                node_map[nd['node_id']] = obj
                if created:
                    logger.info(f"[KG] 新增节点: {nd['name']}")

            # 写入边
            for src_id, tgt_id, rel, weight, desc in XIAMEN_EDGES:
                if src_id in node_map and tgt_id in node_map:
                    KGEdge.objects.update_or_create(
                        source=node_map[src_id],
                        target=node_map[tgt_id],
                        relation=rel,
                        defaults={'weight': weight, 'description': desc}
                    )

        logger.info(f"[KG] 初始化完成：{KGNode.objects.count()}个节点，{KGEdge.objects.count()}条边")
        return True
    except Exception as e:
        logger.error(f"[KG] 初始化失败: {e}")
        return False


def query_kg_for_route(activity: str, features: list, constraints: list) -> list:
    """
    根据活动类型和特征查询知识图谱，返回推荐节点列表
    """
    from .models import KGNode

    # 构建NetworkX图
    G = build_networkx_graph()

    # 筛选适合的节点
    candidate_nodes = []
    for node_id, data in G.nodes(data=True):
        score = 0
        # 活动匹配
        if activity in data.get('suitable_activities', []):
            score += 3
        # 特征匹配
        for feat in features:
            if feat in data.get('features', []):
                score += 1
        # 约束过滤（脚踝不适 → 优先软路面）
        if 'ankle' in constraints:
            node_features = data.get('features', [])
            if '软路面' in node_features or '木栈道' in node_features:
                score += 2
            if '石阶' in node_features:
                score -= 2
        if score > 0:
            candidate_nodes.append((node_id, score, data))

    # 按得分排序
    candidate_nodes.sort(key=lambda x: x[1], reverse=True)

    # 从DB获取完整信息
    result = []
    for node_id, score, data in candidate_nodes[:6]:
        try:
            db_node = KGNode.objects.get(node_id=node_id)
            result.append({
                'node_id': node_id,
                'name': data['name'],
                'type': data['node_type'],
                'score': score,
                'features': data.get('features', []),
                'lng': db_node.gcj_lng,
                'lat': db_node.gcj_lat,
            })
        except KGNode.DoesNotExist:
            result.append({
                'node_id': node_id,
                'name': data['name'],
                'type': data['node_type'],
                'score': score,
                'features': data.get('features', []),
            })

    return result


def get_route_path_kg(origin_id: str, activity: str, max_stops: int = 3) -> list:
    """
    使用NetworkX路径算法，从起点出发找最优路线途经点
    """
    G = build_networkx_graph()
    if origin_id not in G:
        return []

    # 找从起点出发的邻居节点（步行/骑行可达）
    walk_edges = [(u, v, d) for u, v, d in G.edges(origin_id, data=True)
                  if d.get('relation') in ['步行可达', '路线连接', '相邻']]
    walk_edges.sort(key=lambda x: x[2].get('weight', 99))

    waypoints = [origin_id]
    for u, v, d in walk_edges[:max_stops]:
        if v not in waypoints:
            v_data = G.nodes[v]
            if activity in v_data.get('suitable_activities', []):
                waypoints.append(v)

    return waypoints
