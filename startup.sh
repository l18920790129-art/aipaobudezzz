#!/bin/bash
set -e
echo "=== 运动智能伴侣 · 路线大师 启动中 ==="

# Railway/Render 动态注入 PORT 环境变量
# Render 免费版默认使用 10000 端口
if [ -z "$PORT" ]; then
    export PORT=10000
fi
echo ">>> 监听端口: $PORT"

# 数据库迁移
echo ">>> 执行数据库迁移..."
python manage.py migrate --noinput

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

# 启动 Gunicorn
echo ">>> 启动 Gunicorn on 0.0.0.0:$PORT ..."
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:$PORT" \
    --workers 1 \
    --timeout 180 \
    --log-level info \
    --access-logfile -
