# LangGraph 与 CrewAI 分工(本地语料 · 演示用)

LangGraph 是图状态的编排层:每个 Agent 是图上的节点,边控制流向,共享 State 是黑板,
支持条件分支、循环、Send 并行与 checkpoint 持久化。它解决的是"控制流"问题——谁在什么
条件下接手、状态怎么合并、如何断点续跑与人工介入。

CrewAI 是角色化执行层:以角色(role)、目标(goal)、背景(backstory)定义一个 Crew,内聚
地完成一个子任务。它解决的是"执行"问题——把一类工作做成一个可复用的角色团队。

两者组合的成熟做法是分层:LangGraph 做顶层调度与状态管理,CrewAI 做叶子执行器,每个图
节点内部包一个 Crew。关键纪律是:Crew 的内置记忆必须关闭(memory=False),否则其内置
Chroma 存储会跨会话共享,造成记忆串台;记忆统一由引擎层的记忆模块按会话与 Agent 命名
空间管理。

Send API 是 LangGraph 实现并行派发的机制:主管节点拆出 N 个子任务后,用 Send 把每个
子任务路由到 worker 节点,N 路并行执行,最后在 fan-in 节点合并。注意 Send 的返回顺序
不确定,任何依赖到达顺序的逻辑都是 bug。
