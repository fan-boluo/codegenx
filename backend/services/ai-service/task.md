# 项目背景说明
backend/services/ai-serice下是一个前端代码编写的智能体服务，你要完善这个服务，
整个服务架构借鉴了Claude code的实现形式
目录结构说明：
入口：app.py,核心方法：generate_code_stream
bot:旧的智能体，之后准备移除的
bak:旧代码备份的
learn-claudcode:一个简易的教学示范
上面的这些都是后续要移除的，其它的则是实际要用到的

backend/cc：
src : claude code 的源码
backend：仿照claude code 的部分功能做的一个基础的架构

我在做ai-service的架构调整和重构，将bot下面的内容外移一层出来到ai-service下面，并且将bot原来的双层消息队列的传递方式做了去除，借鉴了Claudecode的方式，也就是cc/backend下面的方式，以SessionStore为全局session存储单例，每个session对应一个QueryEngine，每个QueryEngine里面有这个会话所需的组件：上下文、记忆、工具、skill、会话、压缩、监控等。

# 当前进度：

1）ai-service各模块结构完成，bot下的一些有用的代码外移复制出来了，并和cc/backend的一些放到一起了

2）以app.generate_code_stream为入口，在cc/backend的基础上完成了基本的消息收发

# 任务：你需要继续完善ai-service：

1)当前实现的generate_code_stream应该还有一些bug，修复它并测试基本流程可以正常使用;之前的入口还会从AgentAdapterService经过，现在就直接跳过吧，但是需要一个健康监测服务，直接在app.py写就可以了

2）工具：工具部分应该还需要一个grep工具，可以根据cc/src的源码下 tools/GrepTool补充，仅仅需要文本的grep检索即可，其它文件形式不用。这个新增到ai-service/tools/grep.py

3）上下文：这部分要做个整合，目前的上下文在ai-service/context.py中管理，之前bot的是在bot/agent/context.py中，根据bot之前的入口bot/agent/runtime.py梳理上下文的流程，并将它有用的部分整合到目前的context中

4）压缩：同上下文，整合到现在的compact中

5)记忆：bot下的记忆使用的是qdrant向量存储方式，完全摒弃这种方式，使用现在ai-service中的文件留存的方式

6）监测管线，之前bot为了做监测设置了RuntimeSessionState，RuntimeTurnState等，目前先不加，后续再借鉴Claude code的组织形式再加

7）其它你认为应该迁移的，检查之前bot里面的压缩，上下文管理逻辑有没有在迁移之后的里面体现出来
8）做完自己测一遍