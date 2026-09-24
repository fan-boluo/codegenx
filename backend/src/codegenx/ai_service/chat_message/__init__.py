"""聊天消息 MySQL 存储包。

原聊天记录以 chat_history_*.jsonl 文件落盘（SessionManager），现统一存 MySQL
chat_message 表（DDL 见 init/chat_message.sql）：
- 会话内 seq 单调递增，既是消息顺序也是记忆提取水位的载体；
- content 列存整条消息的 JSON 序列化，role 列冗余便于 SQL 过滤；
- payload_ref 预留超长内容外置，当前未启用。
"""
from codegenx.ai_service.chat_message.store import ChatMessageStore, get_chat_message_store

__all__ = ["ChatMessageStore", "get_chat_message_store"]
