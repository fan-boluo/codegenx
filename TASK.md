任务文档


# TODO
跑接口测试
grep 工具 —— 貌似需要安装 ripgrep，需要分析它的使用方法
上下文压缩机制 —— 未深入研究
_memory_hits 从哪里来

# 后续
多 Provider 智能路由 —— 根据 Provider 健康状态（成功率、延迟）自动选择最优 Provider
  可复用之前 /health_check_task + health_check_service 的设计思路，接入 Prometheus 指标数据源

读写工具多并发执行，同时执行多个读，多个写
