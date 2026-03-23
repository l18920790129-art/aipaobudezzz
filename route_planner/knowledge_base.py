"""
knowledge_base.py (v12-fixed)
修复：
1. n_results=0 导致 ChromaDB 异常
2. 移除未使用的 embedding_functions 导入
3. MD5 使用 usedforsecurity=False
4. 增加线程安全保护
"""
import logging
import threading
import chromadb
from django.conf import settings

logger = logging.getLogger(__name__)

# ============================================================
# 厦门运动路线知识库原始数据
# ============================================================
XIAMEN_KNOWLEDGE_DOCS = [
    {
        "id": "xiamen_001",
        "text": "厦门环岛路是全国最美海滨马拉松赛道之一，全长约43公里，沿厦门岛南部海岸线延伸。路面为沥青铺装，平坦宽阔，适合各水平跑者。白城沙滩段视野开阔，可远眺鼓浪屿。椰风寨至曾厝垵段约4.7公里，是最受欢迎的晨跑路段。全程树荫较少，建议清晨或傍晚运动。",
        "metadata": {"area": "南部海滨", "activity": "跑步", "difficulty": "低", "tags": "海景,平坦,沥青路面,环岛路,白城,椰风寨,曾厝垵,马拉松"}
    },
    {
        "id": "xiamen_002",
        "text": "白城沙滩位于厦门大学南门外，是厦门最受欢迎的城市沙滩之一。从白城沙滩出发，沿环岛路向东行约4.7公里可到达曾厝垵文创村。该路段地势平缓，海风习习，脚踝友好，适合轻松跑和散步。曾厝垵有大量特色小吃，是运动后补给的好去处。",
        "metadata": {"area": "南部海滨", "activity": "散步,跑步", "difficulty": "低", "tags": "白城沙滩,曾厝垵,文创村,海景,脚踝友好,平坦"}
    },
    {
        "id": "xiamen_003",
        "text": "厦门大学校园内设有完善的跑步路线，林荫覆盖率高达65%以上，是夏季运动的首选。厦大芙蓉路两侧种植了大量凤凰木和榕树，形成天然绿色隧道。南普陀寺是厦门著名佛教圣地，寺院周边绿化良好，坡度适中。从椰风寨出发经厦大校园至南普陀寺约3.5公里，软路面比例约42%，适合有脚踝或膝盖不适的跑者。",
        "metadata": {"area": "中部校园", "activity": "跑步,徒步", "difficulty": "中", "tags": "厦大,树荫,绿化,南普陀寺,软路面,脚踝友好,校园"}
    },
    {
        "id": "xiamen_004",
        "text": "万石山植物园是厦门最大的城市植物园，占地约4.5平方公里，园内设有多条跑步步道，路面为土路和橡胶跑道，软路面比例高，对膝盖和脚踝的冲击较小。全程树荫覆盖率约82%，即使夏季正午也较为凉爽。需注意部分路段有坡度，累计爬升约95米，建议中级以上跑者选择。",
        "metadata": {"area": "中部山地", "activity": "跑步,徒步", "difficulty": "中", "tags": "万石山,植物园,树荫,软路面,膝盖友好,自然"}
    },
    {
        "id": "xiamen_005",
        "text": "五缘湾湿地公园位于厦门岛北部，是厦门最大的湿地公园，面积约390公顷。公园内设有环湖步道，全程约8公里，地势平坦，视野开阔。湿地生态丰富，是观鸟和自然徒步的好去处。周边有多个餐饮和休闲设施，适合家庭出行。",
        "metadata": {"area": "北部湿地", "activity": "散步,徒步", "difficulty": "低", "tags": "五缘湾,湿地公园,环湖步道,观鸟,家庭,平坦"}
    },
    {
        "id": "xiamen_006",
        "text": "鼓浪屿是厦门著名的步行岛，岛上禁止机动车，全岛步行道路网络完善。日光岩是岛上最高点，登顶可俯瞰整个厦门湾。菽庄花园、皓月园等景点步行可达。适合轻松散步和观光，但需乘船前往，建议安排半天至一天时间。",
        "metadata": {"area": "鼓浪屿", "activity": "散步,观光", "difficulty": "低", "tags": "鼓浪屿,步行,日光岩,景点,观光,海岛"}
    },
    {
        "id": "xiamen_007",
        "text": "胡里山炮台是厦门著名历史景点，建于清朝，炮台周边有宽阔的草坪和海景步道。从胡里山炮台沿海岸线向西步行约1.5公里可到达白城沙滩，沿途海景壮观。适合轻松散步和历史文化游览，路面平整，老人儿童均可。",
        "metadata": {"area": "南部海滨", "activity": "散步,观光", "difficulty": "低", "tags": "胡里山炮台,历史,海景,草坪,轻松,老人友好"}
    },
    {
        "id": "xiamen_008",
        "text": "中山公园是厦门市中心最大的综合性公园，占地约40公顷，园内有湖泊、草坪、林荫道等。公园内有专门的健身步道，全程约3公里，适合晨练和轻松跑步。周边公交便利，是市区居民日常运动的首选场所。",
        "metadata": {"area": "市中心", "activity": "跑步,散步", "difficulty": "低", "tags": "中山公园,市中心,健身步道,晨练,公园,便利"}
    },
    {
        "id": "xiamen_009",
        "text": "厦门马拉松赛道经过环岛路、滨海西大道等路段，全程42.195公里。其中环岛路段约18公里，是赛道中最美的部分，可同时看到大海和绿化。每年12月举办厦门国际马拉松，是国内最具影响力的马拉松赛事之一。",
        "metadata": {"area": "全岛", "activity": "跑步,马拉松", "difficulty": "高", "tags": "马拉松,环岛路,赛道,长跑,竞技"}
    },
    {
        "id": "xiamen_010",
        "text": "厦门骑行路线推荐：环岛路骑行全程约43公里，沿途设有专用骑行道。五缘湾至同安银城骑行约30公里，经过翔安大桥，风景优美。骑行建议避开早晚高峰期，携带充足饮水。厦门气候温和，全年均适合骑行，春秋季节最佳。",
        "metadata": {"area": "全岛", "activity": "骑行", "difficulty": "中", "tags": "骑行,环岛路,五缘湾,骑行道,全年"}
    },
]

# ============================================================
# ChromaDB 客户端（线程安全单例）
# ============================================================
_chroma_client = None
_route_collection = None
_memory_collection = None
_lock = threading.Lock()


def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        with _lock:
            if _chroma_client is None:
                _chroma_client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
                logger.info(f"[ChromaDB] 初始化完成，持久化目录: {settings.CHROMA_PERSIST_DIR}")
    return _chroma_client


def get_route_collection():
    """获取路线知识库集合"""
    global _route_collection
    if _route_collection is None:
        with _lock:
            if _route_collection is None:
                client = get_chroma_client()
                _route_collection = client.get_or_create_collection(
                    name="xiamen_routes",
                    metadata={"description": "厦门运动路线知识库"}
                )
                if _route_collection.count() == 0:
                    _init_route_knowledge(_route_collection)
    return _route_collection


def get_memory_collection():
    """获取用户历史对话记忆集合"""
    global _memory_collection
    if _memory_collection is None:
        with _lock:
            if _memory_collection is None:
                client = get_chroma_client()
                _memory_collection = client.get_or_create_collection(
                    name="user_memories",
                    metadata={"description": "用户历史对话记忆"}
                )
    return _memory_collection


def _init_route_knowledge(collection):
    """初始化厦门路线知识库"""
    logger.info("[ChromaDB] 初始化厦门路线知识库...")
    ids = [doc['id'] for doc in XIAMEN_KNOWLEDGE_DOCS]
    documents = [doc['text'] for doc in XIAMEN_KNOWLEDGE_DOCS]
    metadatas = [doc['metadata'] for doc in XIAMEN_KNOWLEDGE_DOCS]

    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
    )
    logger.info(f"[ChromaDB] 知识库初始化完成，共{len(ids)}条记录")


# ============================================================
# RAG检索
# ============================================================
def retrieve_route_knowledge(query: str, n_results: int = 3) -> list:
    """检索相关路线知识"""
    try:
        collection = get_route_collection()
        count = collection.count()
        # 修复：count 为 0 时直接返回空列表，避免 ChromaDB 异常
        if count == 0:
            logger.warning("[ChromaDB RAG] 知识库为空，跳过检索")
            return []
        actual_n = min(n_results, count)
        results = collection.query(
            query_texts=[query],
            n_results=actual_n,
        )
        docs = []
        if results and results.get('documents'):
            for i, doc in enumerate(results['documents'][0]):
                docs.append({
                    'text': doc,
                    'metadata': results['metadatas'][0][i] if results.get('metadatas') else {},
                    'distance': results['distances'][0][i] if results.get('distances') else 0,
                    'id': results['ids'][0][i] if results.get('ids') else '',
                })
        logger.info(f"[ChromaDB RAG] 查询: {query[:30]}, 检索到{len(docs)}条")
        return docs
    except Exception as e:
        logger.error(f"[ChromaDB RAG] 检索失败: {e}")
        return []


def add_memory(session_id: str, user_query: str, ai_response: str,
               route_info: str = '', metadata: dict = None):
    """将一次对话记录存入向量记忆库"""
    try:
        collection = get_memory_collection()
        import hashlib
        import time
        # 修复：使用 usedforsecurity=False 消除安全警告
        doc_id = f"mem_{session_id}_{hashlib.md5(user_query.encode(), usedforsecurity=False).hexdigest()[:8]}_{int(time.time())}"
        text = f"用户问：{user_query}\nAI回答：{ai_response[:200]}"
        if route_info:
            text += f"\n路线信息：{route_info}"

        meta = {'session_id': session_id, 'type': 'conversation'}
        if metadata:
            meta.update(metadata)

        collection.add(
            ids=[doc_id],
            documents=[text],
            metadatas=[meta],
        )
        logger.info(f"[ChromaDB Memory] 记忆已存储: {doc_id}")
    except Exception as e:
        logger.error(f"[ChromaDB Memory] 存储失败: {e}")


def retrieve_memory(session_id: str, query: str, n_results: int = 3) -> list:
    """检索用户历史记忆"""
    try:
        collection = get_memory_collection()
        count = collection.count()
        if count == 0:
            return []

        results = collection.query(
            query_texts=[query],
            n_results=min(n_results, count),
            where={'session_id': session_id} if count > 0 else None,
        )
        docs = []
        if results and results.get('documents'):
            for doc in results['documents'][0]:
                docs.append({'text': doc})
        return docs
    except Exception as e:
        logger.error(f"[ChromaDB Memory] 检索失败: {e}")
        return []
