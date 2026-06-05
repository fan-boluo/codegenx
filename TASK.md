任务文档

# TODO

自己可以做的：
工具db_name没有做安全校验
DescribeCsvTool 128行 文件大小小于1MB会显示0.0MB，这个改为更友好的显示："小于1M"

上下文压缩机制 研究Claude 或openclaw的压缩机制
记忆管理  研究Claude openclaw
_memory_hits 从哪里来

# 后续

多 Provider 智能路由 —— 根据 Provider 健康状态（成功率、延迟）自动选择最优 Provider
  可复用之前 /health_check_task + health_check_service 的设计思路，接入 Prometheus 指标数据源

读写工具多并发执行，同时执行多个读，多个写
