FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 收集静态文件（忽略错误）
RUN python manage.py collectstatic --noinput || true

# 暴露端口（Railway使用PORT环境变量动态分配）
EXPOSE 8080

# 确保startup.sh有执行权限
RUN chmod +x startup.sh

# 启动脚本（迁移 + 初始化知识图谱 + 启动gunicorn）
CMD ["/bin/bash", "startup.sh"]
