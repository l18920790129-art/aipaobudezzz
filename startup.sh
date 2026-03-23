#!/bin/bash
set -e
echo "=== 运动智能伴侣 · 路线大师 v12 启动中 ==="

# 端口配置
if [ -z "$PORT" ]; then
    export PORT=10000
fi
echo ">>> 监听端口: $PORT"

# 环境变量检查
if [ -z "$DEEPSEEK_API_KEY" ]; then
    echo "⚠️  警告: DEEPSEEK_API_KEY 未设置，AI功能将不可用"
fi
if [ -z "$AMAP_WEB_KEY" ]; then
    echo "⚠️  警告: AMAP_WEB_KEY 未设置，地图功能将不可用"
fi

# 数据库迁移（带重试）
echo ">>> 执行数据库迁移..."
MAX_RETRIES=5
RETRY_COUNT=0
until python manage.py migrate --noinput 2>&1; do
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "❌ 数据库迁移失败，已重试 $MAX_RETRIES 次"
        exit 1
    fi
    echo ">>> 数据库连接失败，${RETRY_COUNT}/${MAX_RETRIES} 次重试，等待 3 秒..."
    sleep 3
done
echo ">>> 数据库迁移完成"

# 后台初始化知识图谱（不阻塞启动）
echo ">>> 后台初始化知识图谱..."
(python manage.py shell -c "
from route_planner.models import KGNode
from route_planner.knowledge_graph import init_knowledge_graph
if KGNode.objects.count() == 0:
    init_knowledge_graph()
    print('知识图谱初始化完成')
else:
    print(f'知识图谱已存在 {KGNode.objects.count()} 个节点')
" 2>&1 | tee /tmp/kg_init.log) &

# Worker 数量：免费版用1个，付费版可增加
WORKERS=${GUNICORN_WORKERS:-2}
echo ">>> Worker 数量: $WORKERS"

# 启动 Gunicorn（增加 worker 数量以支持 SSE 并发）
echo ">>> 启动 Gunicorn on 0.0.0.0:$PORT ..."
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:$PORT" \
    --workers "$WORKERS" \
    --threads 4 \
    --timeout 180 \
    --graceful-timeout 30 \
    --log-level info \
    --access-logfile -
