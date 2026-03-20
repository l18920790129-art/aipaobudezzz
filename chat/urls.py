from django.urls import path
from . import views

urlpatterns = [
    path('message/', views.chat_message, name='chat_message'),
    path('history/', views.chat_history, name='chat_history'),
    path('sessions/', views.session_list, name='session_list'),
    path('clear/', views.clear_session, name='clear_session'),
    path('memory/', views.user_memory, name='user_memory'),
]
