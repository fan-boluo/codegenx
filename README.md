# CodeGenX

CodeGenX is a Python-based microservice platform for AI-driven code generation, visual editing, cloud deployment, and enterprise management.

# 混合协议
- 用户域 gateway -> user-service 使用 gRPC + Nacos
- 生成域 gateway -> chat-service -> app-service -> ai-service 使用 HTTP/SSE + Nacos
- 前端出口继续保持 HTTP/SSE，不引入前端协议变化
- 
# 微服务调用：

网关接口层  
    ↓
Proxy（代理层,gateway）  
    ↓
ServiceGrpcClient（gRPC 调用层,gateway）
    ↓
Nacos 服务发现  （infra）        

Nacos 服务注册 （service）
   ⬆
ServiceServicer(grpc服务层,service)
   ⬆
grpc_pb2 / grpc_pb2_grpc (service)
   ⬆
proto (service)
   ⬆
实际业务功能

部署：
使用静态资源访问+nginx部署的方式
前端点击预览，可以调用后端接口访问静态资源
前端点击部署：nginx部署



可视化修改

使用前端页面内嵌ifram来实现，当选中子网站元素时，向父网站汇报



## 目录介绍

api-gateway:网关，要实现限流，JWT认证，黑名单拦截等功能
services:python 微服务,ai-service、chat-service、app-service、user-service，分别处理 AI 生成服务、聊天服务、应用/工作区服务、用户服务
shared：共享模块

## 技术栈

要完成的python服务的技术栈，计划如下：

- 语言：Python + FastAPI

- 限流：Redis + Lua 脚本实现 **令牌桶 / 滑动窗口限流**

- 鉴权：JWT 解析、用户权限校验、接口访问控制

- 服务发现与调用：Nacos + grpc

- 高可用：k8s 多实例部署 + 健康检查 + 自动熔断

- 大模型架构：langchain / openAI

# 2 项目完成情况

| 模块           | 功能      | 备注  |
| ------------ | ------- | --- |
| ai-gateway   | JWT鉴权   | 已完成 |
|              | 限流      |     |
|              | 黑名单拦截   |     |
|              | 其它      |     |
| user-service | 注册      | 已完成 |
|              | 登录      | 已完成 |
|              | 查询      | 已完成 |
|              | 其它      |     |
| app-service  | 应用创建    |     |
|              | 代码生成    |     |
|              | 代码保存    |     |
|              | 代码下载    |     |
|              | 代码部署    |     |
|              | 调用日志记录  |     |
| chat-service | 聊天编排    |     |
|              | 聊天历史服务  |     |
| api-service  | 大模型调用   |     |
|              | 大模型调用监控 |     |