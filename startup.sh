#!/bin/bash
set -e
echo "=== 运动智能伴侣 · 路线大师 启动中 ==="

# Railway 动态注入 PORT 环境变量，需要在运行时读取
# 如果 PORT 未设置，使用默认值 8080
if [ -z "$PORT" ]; then
    export PORT=8080
fi
echo ">>> 监听端口: $PORT"

# 数据库迁移
echo ">>> 执行数据库迁移..."
python manage.py migrate --noinput

# 初始化知识图谱（忽略错误）
echo ">>> 初始化知识图谱..."
python manage.py shell -c "
from route_planner.models import KGNode
from route_planner.knowledge_graph import init_knowledge_graph
if KGNode.objects.count() == 0:
    init_knowledge_graph()
    print('知识图谱初始化完成')
else:
    print(f'知识图谱已存在 {KGNode.objects.count()} 个节点')
" || echo "知识图谱初始化跳过"

# 启动 Gunicorn
echo ">>> 启动 Gunicorn on 0.0.0.0:$PORT ..."
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:$PORT" \
    --workers 2 \
    --timeout 180 \
    --log-level info \
    --access-logfile -
