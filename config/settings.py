"""
运动智能伴侣 · 路线大师 - Django Settings
技术栈：Django + LangChain + PostgreSQL + ChromaDB + DeepSeek + 高德API
支持 Railway 部署（DATABASE_URL 自动解析）
"""
from pathlib import Path
import os
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'sports-companion-secret-key-aipaobudezzz-2026')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'route_planner',
    'chat',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ===== 数据库配置（优先使用 Railway 的 DATABASE_URL）=====
DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600)
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'sports_db'),
            'USER': os.environ.get('DB_USER', 'sports_user'),
            'PASSWORD': os.environ.get('DB_PASSWORD', 'sports_pass123'),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '5432'),
        }
    }

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
# Django 5.x 使用 STORAGES 替代已弃用的 STATICFILES_STORAGE
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS 配置（允许所有来源）
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = ['*']

# ===== API Keys =====
AMAP_WEB_KEY = os.environ.get('AMAP_WEB_KEY', '29b9bdb25de113178710004b939bbd58')
AMAP_JS_KEY = os.environ.get('AMAP_JS_KEY', '213a968a1964c78c07a2883e9590820b')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', 'sk-196ac58ce714488bb5044394cdb96f65')
DEEPSEEK_BASE_URL = 'https://api.deepseek.com/v1'
DEEPSEEK_MODEL = 'deepseek-chat'

# ===== ChromaDB 配置 =====
CHROMA_PERSIST_DIR = os.environ.get('CHROMA_PERSIST_DIR', str(BASE_DIR / 'chroma_db'))

# ===== 默认城市 =====
DEFAULT_CITY = '厦门'
DEFAULT_CITY_ADCODE = '350200'

# ===== 日志配置 =====
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'route_planner': {'level': 'INFO', 'handlers': ['console'], 'propagate': False},
        'chat': {'level': 'INFO', 'handlers': ['console'], 'propagate': False},
    },
}
