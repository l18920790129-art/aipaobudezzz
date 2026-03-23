"""
knowledge_graph.py (v12-fixed)
修复：
1. build_networkx_graph 缓存，避免每次查询重建
2. 环岛路坐标修正（使用环岛海滨浴场POI坐标，已验证一致）
3. 曾厝垵坐标验证通过（118.125370,24.425286 与高德POI完全一致）
"""
import logging
import networkx as nx
from django.db import transaction

logger = logging.getLogger(__name__)

# ===== 厦门核心地点数据（全部真实GCJ-02坐标，高德API验证）=====
XIAMEN_NODES = [
    {
        'node_id': 'baicheng_beach',
        'name': '白城沙滩',
        'node_type': '海滩',
        'description': '厦门大学南门外的天然沙滩，沙质细腻，海景开阔，是跑步和散步的热门起点。沿环岛路向东可到达胡里山炮台和曾厝垵。',
        'gcj_lng': 118.100875, 'gcj_lat': 24.432281,
        'suitable_activities': ['跑步', '散步', '健步走'],
        'surface_types': ['沙滩', '木栈道', '沥青路'],
        'features': ['海景', '日出', '无障碍', '平坦'],
    },
    {
        'node_id': 'xiamen_university',
        'name': '厦门大学',
        'node_type': '学校',
        'description': '中国最美大学之一，思明校区位于思明南路422号，校园内有环形跑道和绿荫步道，适合晨跑。',
        'gcj_lng': 118.101683, 'gcj_lat': 24.436084,
        'suitable_activities': ['跑步', '散步', '骑行'],
        'surface_types': ['塑胶跑道', '沥青路', '石板路'],
        'features': ['树荫', '历史建筑', '安静'],
    },
    {
        'node_id': 'huandao_road',
        'name': '环岛路',
        'node_type': '景区',
        'description': '厦门最著名的滨海大道，全长约43km，沿途海景壮观，设有专用骑行道和跑步道。环岛路海滨浴场段是最受欢迎的跑步路段。',
        'gcj_lng': 118.123419, 'gcj_lat': 24.425424,
        'suitable_activities': ['跑步', '骑行', '散步'],
        'surface_types': ['塑胶跑道', '沥青路'],
        'features': ['海景', '骑行道', '观景台', '平坦'],
    },
    {
        'node_id': 'wanshishan',
        'name': '万石山植物园',
        'node_type': '公园',
        'description': '厦门最大的植物园，占地约4.5平方公里，山地地形，有多条登山步道。园内树荫覆盖率约82%，即使夏季正午也较为凉爽。',
        'gcj_lng': 118.109661, 'gcj_lat': 24.449081,
        'suitable_activities': ['徒步', '散步'],
        'surface_types': ['石阶', '土路', '木栈道'],
        'features': ['树荫', '植物', '山景', '清凉'],
    },
    {
        'node_id': 'wuyuan_wetland',
        'name': '五缘湾湿地公园',
        'node_type': '湿地',
        'description': '厦门岛北部最大的湿地公园，面积约390公顷，环湖步道约8km，地势平坦，适合轻松散步和骑行。',
        'gcj_lng': 118.178133, 'gcj_lat': 24.516071,
        'suitable_activities': ['散步', '骑行', '健步走'],
        'surface_types': ['木栈道', '沥青路', '软路面'],
        'features': ['湿地', '候鸟', '平坦', '树荫'],
    },
    {
        'node_id': 'zengtuo_an',
        'name': '曾厝垵',
        'node_type': '商业',
        'description': '厦门著名的文艺渔村，小巷众多，适合慢跑探索，周边有海滩。大量特色小吃，是运动后补给的好去处。',
        'gcj_lng': 118.125370, 'gcj_lat': 24.425286,
        'suitable_activities': ['散步', '慢跑'],
        'surface_types': ['石板路', '沥青路'],
        'features': ['文艺', '海景', '美食', '人文'],
    },
    {
        'node_id': 'hulishan_fort',
        'name': '胡里山炮台',
        'node_type': '景区',
        'description': '历史文化景区，位于曾厝垵路2号，周边有海滨步道，可俯瞰台湾海峡。',
        'gcj_lng': 118.106383, 'gcj_lat': 24.429475,
        'suitable_activities': ['散步', '健步走'],
        'surface_types': ['石板路', '沥青路'],
        'features': ['历史', '海景', '炮台'],
    },
    {
        'node_id': 'nanputuo_temple',
        'name': '南普陀寺',
        'node_type': '景区',
        'description': '厦门著名佛教圣地，位于思明南路515号，背靠五老峰，有登山步道，适合徒步。',
        'gcj_lng': 118.097143, 'gcj_lat': 24.442641,
        'suitable_activities': ['徒步', '散步'],
        'surface_types': ['石阶', '石板路'],
        'features': ['文化', '山景', '登山'],
    },
    {
        'node_id': 'zhongshan_park',
        'name': '中山公园',
        'node_type': '公园',
        'description': '厦门市中心最大的综合性公园，占地约40公顷，园内有湖泊、草坪、林荫道。有专门的健身步道约3公里，适合晨练和轻松跑步。',
        'gcj_lng': 118.090076, 'gcj_lat': 24.459187,
        'suitable_activities': ['跑步', '散步', '健步走', '慢跑'],
        'surface_types': ['塑胶跑道', '沥青路', '石板路'],
        'features': ['树荫', '公园', '便利', '平坦'],
    },
    {
        'node_id': 'jiangjun_ci',
        'name': '将军祠',
        'node_type': '交通',
        'description': '厦门市区重要交通枢纽，地铁1号线将军祠站，周边有铁路文化公园和万石山植物园入口，是多条跑步路线的途经点。',
        'gcj_lng': 118.099835, 'gcj_lat': 24.460767,
        'suitable_activities': ['跑步', '散步', '健步走'],
        'surface_types': ['沥青路', '石板路'],
        'features': ['交通便利', '历史', '人文'],
    },
    {
        'node_id': 'bailuzhou_park',
        'name': '白鹭洲公园',
        'node_type': '公园',
        'description': '厦门市中心的城市公园，位于白鹭洲路，紧邻筼筜湖，是市民休闲运动的热门场所。',
        'gcj_lng': 118.093828, 'gcj_lat': 24.473704,
        'suitable_activities': ['跑步', '散步', '健步走'],
        'surface_types': ['沥青路', '石板路', '木栈道'],
        'features': ['湖景', '树荫', '公园', '平坦'],
    },
    {
        'node_id': 'bailuzhou_west',
        'name': '白鹭洲公园西公园',
        'node_type': '公园',
        'description': '白鹭洲公园的西侧部分，位于白鹭洲路565号，绕行公园内部步道，绿树成荫，空气清新，可作为中途休憩点。',
        'gcj_lng': 118.089333, 'gcj_lat': 24.472813,
        'suitable_activities': ['跑步', '散步', '健步走'],
        'surface_types': ['沥青路', '石板路'],
        'features': ['树荫', '公园', '安静'],
    },
    {
        'node_id': 'yundang_lake',
        'name': '筼筜湖',
        'node_type': '湿地',
        'description': '厦门市中心的城市内湖，环湖步道约6公里，沿途视野开阔，适合长距离有氧慢跑。',
        'gcj_lng': 118.094224, 'gcj_lat': 24.475764,
        'suitable_activities': ['跑步', '散步', '健步走', '骑行'],
        'surface_types': ['沥青路', '木栈道'],
        'features': ['湖景', '平坦', '树荫', '环湖步道'],
    },
    {
        'node_id': 'music_square',
        'name': '音乐广场',
        'node_type': '公园',
        'description': '位于湖滨南路与斗西路交叉口西侧，紧邻筼筜湖和白鹭洲公园，是环湖跑步路线的重要节点。',
        'gcj_lng': 118.084312, 'gcj_lat': 24.467453,
        'suitable_activities': ['跑步', '散步', '健步走'],
        'surface_types': ['沥青路', '石板路'],
        'features': ['广场', '湖景', '休闲'],
    },
    {
        'node_id': 'haiwan_park',
        'name': '海湾公园',
        'node_type': '公园',
        'description': '位于西堤东路49号，面朝大海，可远眺鼓浪屿和海沧，是厦门西海岸线上的重要公园。',
        'gcj_lng': 118.076287, 'gcj_lat': 24.473095,
        'suitable_activities': ['散步', '跑步', '健步走'],
        'surface_types': ['沥青路', '石板路'],
        'features': ['海景', '公园', '日落'],
    },
    {
        'node_id': 'yanwu_bridge',
        'name': '演武大桥观景平台',
        'node_type': '景区',
        'description': '位于演武大桥与演武路交叉口，全长约824米，被认为是离海平面最近的大桥观景平台，可观赏鼓浪屿和海沧港景色。',
        'gcj_lng': 118.090262, 'gcj_lat': 24.433305,
        'suitable_activities': ['散步', '健步走'],
        'surface_types': ['石板路'],
        'features': ['海景', '观景台', '日落'],
    },
    {
        'node_id': 'hongshan_park',
        'name': '鸿山公园',
        'node_type': '公园',
        'description': '位于思明南路331号，是厦门市区的一座山地公园，有登山步道，可俯瞰中山路和鼓浪屿。',
        'gcj_lng': 118.086731, 'gcj_lat': 24.447105,
        'suitable_activities': ['徒步', '散步'],
        'surface_types': ['石阶', '石板路'],
        'features': ['山景', '历史', '登山'],
    },
    {
        'node_id': 'railway_park',
        'name': '铁路文化公园',
        'node_type': '公园',
        'description': '位于万寿路11号，由废弃铁路改建而成，全长约4.5公里，是厦门独特的线性公园，适合慢跑和散步。距将军祠地铁站步行约250米。',
        'gcj_lng': 118.102223, 'gcj_lat': 24.460383,
        'suitable_activities': ['跑步', '散步', '慢跑'],
        'surface_types': ['沥青路', '软路面'],
        'features': ['树荫', '安静', '文艺', '铁路遗迹'],
    },
    {
        'node_id': 'huwei_mountain',
        'name': '狐尾山公园',
        'node_type': '山地',
        'description': '位于湖滨中路旁，山顶有气象主题公园和海上明珠塔，登山步道适合徒步锻炼。',
        'gcj_lng': 118.086277, 'gcj_lat': 24.483896,
        'suitable_activities': ['徒步', '散步'],
        'surface_types': ['石阶', '土路'],
        'features': ['山景', '登山', '观景台'],
    },
    {
        'node_id': 'wuyuan_beach',
        'name': '五缘湾沙滩',
        'node_type': '海滩',
        'description': '位于厦门岛北部环岛干道旁，沙质细腻，适合沙滩跑步和散步。',
        'gcj_lng': 118.171013, 'gcj_lat': 24.529622,
        'suitable_activities': ['跑步', '散步'],
        'surface_types': ['沙滩', '木栈道'],
        'features': ['海景', '沙滩', '平坦'],
    },
    {
        'node_id': 'guanyin_mountain',
        'name': '观音山沙滩',
        'node_type': '海滩',
        'description': '位于环岛东路商业街17号，厦门东海岸的大型沙滩，适合沙滩运动。',
        'gcj_lng': 118.199506, 'gcj_lat': 24.493697,
        'suitable_activities': ['跑步', '散步'],
        'surface_types': ['沙滩', '沥青路'],
        'features': ['海景', '沙滩', '商业配套'],
    },
    {
        'node_id': 'yefengzhai',
        'name': '椰风寨',
        'node_type': '海滩',
        'description': '位于环岛南路，是环岛路上最受欢迎的海滨休闲区之一，有沙滩和游乐设施。',
        'gcj_lng': 118.161867, 'gcj_lat': 24.442613,
        'suitable_activities': ['跑步', '散步', '骑行'],
        'surface_types': ['沙滩', '沥青路'],
        'features': ['海景', '沙滩', '休闲'],
    },
    {
        'node_id': 'zhonglun_park',
        'name': '忠仑公园',
        'node_type': '公园',
        'description': '位于金尚路77号，厦门岛内较大的山地公园，有多条步道，春季樱花盛开。',
        'gcj_lng': 118.150039, 'gcj_lat': 24.483488,
        'suitable_activities': ['跑步', '散步', '徒步'],
        'surface_types': ['石阶', '沥青路', '土路'],
        'features': ['树荫', '山景', '樱花'],
    },
]

# ===== 地点间关系 =====
XIAMEN_EDGES = [
    ('baicheng_beach', 'xiamen_university', '步行可达', 0.5, '步行约6分钟'),
    ('baicheng_beach', 'hulishan_fort', '步行可达', 1.0, '沿海滨步道步行约12分钟'),
    ('baicheng_beach', 'yanwu_bridge', '步行可达', 0.8, '步行约10分钟'),
    ('baicheng_beach', 'huandao_road', '路线连接', 2.0, '经环岛路相连'),
    ('baicheng_beach', 'zengtuo_an', '骑行可达', 3.0, '沿环岛路骑行约15分钟'),
    ('xiamen_university', 'nanputuo_temple', '步行可达', 0.5, '步行约6分钟'),
    ('xiamen_university', 'wanshishan', '步行可达', 2.0, '步行约25分钟'),
    ('xiamen_university', 'yanwu_bridge', '步行可达', 0.6, '步行约8分钟'),
    ('huandao_road', 'zengtuo_an', '路线连接', 1.0, '环岛路途经'),
    ('huandao_road', 'hulishan_fort', '路线连接', 0.8, '环岛路途经'),
    ('huandao_road', 'yefengzhai', '路线连接', 3.0, '环岛路途经'),
    ('zhongshan_park', 'jiangjun_ci', '步行可达', 1.0, '步行约12分钟'),
    ('jiangjun_ci', 'railway_park', '步行可达', 0.3, '步行约3分钟，距地铁站250米'),
    ('jiangjun_ci', 'wanshishan', '步行可达', 1.5, '步行约18分钟'),
    ('zhongshan_park', 'hongshan_park', '步行可达', 1.2, '步行约15分钟'),
    ('zhongshan_park', 'railway_park', '步行可达', 1.0, '步行约12分钟'),
    ('bailuzhou_park', 'bailuzhou_west', '相邻', 0.3, '公园内步行约4分钟'),
    ('bailuzhou_park', 'yundang_lake', '步行可达', 0.5, '紧邻筼筜湖'),
    ('bailuzhou_west', 'yundang_lake', '步行可达', 0.4, '紧邻筼筜湖'),
    ('yundang_lake', 'music_square', '步行可达', 0.8, '环湖步道步行约10分钟'),
    ('music_square', 'bailuzhou_west', '步行可达', 0.6, '步行约8分钟'),
    ('yundang_lake', 'haiwan_park', '步行可达', 1.5, '沿湖滨西路步行约18分钟'),
    ('yundang_lake', 'huwei_mountain', '步行可达', 1.0, '步行约12分钟'),
    ('zhongshan_park', 'music_square', '步行可达', 1.5, '沿湖滨南路步行约18分钟'),
    ('zhongshan_park', 'bailuzhou_park', '步行可达', 2.0, '步行约25分钟'),
    ('wuyuan_wetland', 'wuyuan_beach', '步行可达', 1.5, '步行约18分钟'),
    ('wuyuan_wetland', 'guanyin_mountain', '骑行可达', 4.0, '骑行约15分钟'),
    ('wanshishan', 'nanputuo_temple', '步行可达', 1.0, '步行约12分钟'),
    ('nanputuo_temple', 'hongshan_park', '步行可达', 1.5, '步行约18分钟'),
    ('zengtuo_an', 'hulishan_fort', '步行可达', 1.2, '步行约15分钟'),
    ('zhongshan_park', 'jiangjun_ci', '适合同行', 1.0, '同适合市区跑步'),
    ('bailuzhou_park', 'yundang_lake', '适合同行', 1.0, '同适合环湖跑步'),
    ('baicheng_beach', 'huandao_road', '适合同行', 1.0, '同适合海滨跑步'),
    ('wuyuan_wetland', 'wuyuan_beach', '适合同行', 1.0, '同适合北部休闲'),
    ('zhongshan_park', 'railway_park', '适合同行', 1.0, '同适合市区慢跑'),
]

# ===== 缓存 NetworkX 图实例 =====
_cached_graph = None


def build_networkx_graph() -> nx.DiGraph:
    """构建 NetworkX 有向图（带缓存）"""
    global _cached_graph
    if _cached_graph is not None:
        return _cached_graph

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

    _cached_graph = G
    return G


def init_knowledge_graph():
    """初始化知识图谱到PostgreSQL（幂等操作）"""
    from .models import KGNode, KGEdge

    logger.info("[KG] 开始初始化/更新厦门知识图谱...")
    try:
        with transaction.atomic():
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
                else:
                    logger.info(f"[KG] 更新节点: {nd['name']}")

            fake_ids = ['haha_park', 'lianxin_garden']
            deleted_count = KGNode.objects.filter(node_id__in=fake_ids).delete()[0]
            if deleted_count:
                logger.info(f"[KG] 已删除{deleted_count}个虚假节点")

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
    """根据活动类型和特征查询知识图谱，返回推荐节点列表"""
    from .models import KGNode

    G = build_networkx_graph()

    candidate_nodes = []
    for node_id, data in G.nodes(data=True):
        score = 0
        if activity in data.get('suitable_activities', []):
            score += 3
        node_features = data.get('features', [])
        for feat in features:
            feat_map = {
                'sea_view': '海景', 'scenic': '风景', 'shade': '树荫',
                'park': '公园', 'soft_surface': '软路面', 'water': '水站',
            }
            mapped = feat_map.get(feat, feat)
            if mapped in node_features:
                score += 1
        if 'ankle' in constraints or 'knee' in constraints:
            if '软路面' in node_features or '木栈道' in node_features or '平坦' in node_features:
                score += 2
            if '石阶' in node_features or '登山' in node_features:
                score -= 2
        if score > 0:
            candidate_nodes.append((node_id, score, data))

    candidate_nodes.sort(key=lambda x: x[1], reverse=True)

    result = []
    for node_id, score, data in candidate_nodes[:8]:
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
    """使用NetworkX路径算法，从起点出发找最优路线途经点"""
    G = build_networkx_graph()
    if origin_id not in G:
        return []

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
