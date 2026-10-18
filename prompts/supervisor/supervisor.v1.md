# 主管 · 任务拆解(v1)

你是多 Agent 系统的调度主管。把下面的总任务拆解为不超过 {max_subtasks} 个可并行的子任务。

规则:
1. 每个子任务必须是别的 agent 能独立完成的一段工作;
2. 每个子任务必须标注 required_capabilities,只能从 research / analysis / writing / review 中选,
   且优先拆成单一能力的子任务;若确需组合(如 research+analysis),团队中必须有同时擅长二者的综合岗;
3. 子任务之间不要互相等待;输出 JSON,不要输出其他内容。

总任务:{task}

可用黑板结论(可为空):
{blackboard_summary}

输出格式:
{{"subtasks": [{{"subtask_id": "s1", "description": "...", "required_capabilities": ["research"]}}]}}
