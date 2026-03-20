"""
chat/models.py - 多轮对话历史存储（内存 + 数据库双轨）
"""
from django.db import models


class ChatSession(models.Model):
    """对话会话"""
    session_id = models.CharField(max_length=64, unique=True, db_index=True)
    title = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'chat_sessions'

    def __str__(self):
        return f"{self.title or '新对话'} ({self.session_id[:8]})"


class ChatMessage(models.Model):
    """对话消息"""
    ROLE_CHOICES = [('user', '用户'), ('assistant', 'AI助手'), ('system', '系统')]

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    # 附加数据（路线规划结果等）
    extra_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chat_messages'
        ordering = ['created_at']

    def to_dict(self):
        return {
            'role': self.role,
            'content': self.content,
            'extra_data': self.extra_data,
            'created_at': self.created_at.isoformat(),
        }
