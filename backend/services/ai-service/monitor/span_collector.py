from infra.mysql.session import get_db_session
from monitor.span_context import SpanContext


class SpanCollector:
    """
    缓冲
    """
    def __init__(self):
        # 主缓冲区：待写入 MySQL 的 Span 列表
        self._buffer: list[dict] = []

        # MySQL 连接池
        self._mysql = get_db_session()

        # 配置
        self._max_buffer = 200  # 缓冲区上限，超过就强制 flush
        self._flush_interval = 10  # 定时 flush 间隔（秒）



    def append(self,span:SpanRecord):

        self._buffer.append(span)


    def flush(self):
        pass


