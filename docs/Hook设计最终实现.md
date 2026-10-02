
原本的设计文档：Hook设计.md,以下是修改过的：
对于要不要放hook的原则：
Hook 是执行流上的**拦截 / 扩展点**，核心价值是把**横切关注点**（监控、日志、安全、记忆、注入、策略）从核心执行循环里抽离出来 —— 让主流程保持显式、可推理，同时允许这些能力按需插入。判断 "要不要放 hook" 只有一个试金石：**删掉这个 hook，主流程是否还能正确运行**

目前的事件及触发点
event：on_user_input ,name:inputput_safety_check 用户安全检查
event：on_session_start ,name:report_session_start 初始化session，turn,telemetry对象，上报session总数、活跃session数到监控
event：on_turn_start ,name:report_turn_start monitor 监控
event：before_build ,name:inject_dynamic_prompts  目前什么也没有，保持现状

event：before_llm_invoke ,name:report_prompt_tokens  这个prompt token计算和实际差别太大，也没有任何拦截
event：after_llm_invoke ,name:report_llm_usage  模型返回的实际使用量

event：before_tool_call ,name:report_tool_start   具体怎么检查后续再说吧
event：before_tool_call ,name:safe_path_guard
event：before_tool_call ,name:file_tool_param_guard

event：after_tool_call ,name:persist_tool_log 
event：after_tool_call ,name:report_tool_end

event：on_complete ,name:output_safety_check  ? 必须要经过的一个环节，做成hook不好吧
event：internal/on_error ,name:report_error

event：on_turn_end ,name:report_turn_end
event：on_turn_end ,name:memory_turn_signal

event：on_session_end ,name:report_session_end
event：on_session_end ,name:memory_session_end


关于span: 现在的span做的是监控，已经在monitor部分有这类功能了，这里就去掉了
链路追踪，后续有可能再补充吧