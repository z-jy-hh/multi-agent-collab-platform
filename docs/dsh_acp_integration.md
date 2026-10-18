# dsh ACP 接入说明（R16 · D1–D8）

> 平台侧把 DeepSeek Harness（dsh）接入为本平台的外部执行 agent。实现位于 `dsh_bridge/`，
> 自检命令 `main.py dsh-check`。本文是接入契约、护栏语义与运维降级的唯一说明。

## 1 · 通道与版本

| 项 | 值 |
|---|---|
| 主通道 | ACP v1 over stdio：`node <DSH_BIN> --profile acp`（JSON-RPC，`\n` 分帧） |
| 辅通道 | headless：`… --profile headless [--json] [--session-id <id>] "<task>"`（一次性） |
| 弃用 | HTTP `/api/*`（浏览器信任栅栏内的内部 RPC，无公开契约）；`--profile sdk`（无取消、无权限回路） |
| 版本锁 | `@deepseek-ai/*` **0.2.0-rc.2**（rc 阶段接口会漂移：会话格式已 v3→v4）；`dsh --version` 校验 |
| 环境变量 | `DSH_BIN`（bin.js 绝对路径）、`DSH_PERMISSION_MODE`（档位）、`DSH_HOME`（隔离实例）、`DSH_DISPATCH_ROOT`（派发根，默认 `runs/dsh_dispatch`）、`DSH_EXPECTED_VERSION`（版本锁断言） |
| 子进程环境 | **白名单**：仅系统必要项 + 上表变量；key/token 不可能进入子进程环境 |

## 2 · 派发根与目录边界（红线）

- 每次派发 = 一个 dsh 会话，cwd **一律**为 `runs/dsh_dispatch/<run_id>/`（仓库内、gitignored、绝对路径）；
- dsh 的可写边界 = **会话不可变 cwd（单根）**——平台不支持也不使用多根白名单；
- 派发前必须过 **cwd 冒烟**（create/delete probe，`dsh_bridge.session.cwd_smoke`）；
- **本机权限前置**：派发根需要"当前用户：完全控制"的**可继承** ACL（本机已由委托方施加）。
  裸机/换机上缺失时，dsh 子进程根本不会 spawn（`SetNamedSecurityInfoW failed (Win32 5)`）——
  `dsh-check` 会把它报为 **ENV-FAIL**（环境问题），绝不与任务失败混为一谈；
- 该 ACL 是**本机一次性操作，不在仓库里**——系统更新/换机/权限被重写时，cwd 冒烟是第一道哨兵。

## 3 · 权限档位与策略

### 档位（子进程环境变量 `DSH_PERMISSION_MODE`）

| 档位 | sandbox | approval | 用途 |
|---|---|---|---|
| `read-only` | read-only | ask | 只读体检/评审 |
| `workspace-write` | workspace-write | ask | **默认**；可写边界 = cwd |
| `danger-full-access` | danger-full-access | never | 仅委托方显式授权的单次运行，必须落审计 |

### 平台策略（`dsh_bridge/permissions.py`，F5 按真机帧形状重定）

**真实帧形状（两次实测一致）**：`session/request_permission` 只带
`sessionId / toolCall.toolCallId / options`——**没有路径、没有 rawInput、没有 title**。
因此"路径在 cwd 内=放行"的判定在生产上不可达（旧策略用合成路径测试是绿的，现实只会命中默认拒绝）。

| 场景 | 决策 |
|---|---|
| 默认（workspace-write / read-only，无人工介入） | **reject-once（fail-closed）** |
| 座舱"权限申请卡"宿主批准（human_hook，受闸超时约束） | allow-once |
| 座舱"权限申请卡"宿主拒绝 | reject-once |
| 运行档 = danger-full-access（委托方显式授权，approval=never） | allow-once（审计） |
| 帧形状无法识别（无 toolCallId） | reject-once |

- 应答**只允许** `{"outcome":{"outcome":"selected","optionId":"allow-once"|"reject-once"}}`（dsh 的映射：`allow-once→allowed-once`，其余→rejected；不得发明 optionId）；
- **权限回路超时**：dsh 服务端**永不自行超时**（实测挂满 240s）→ 平台闸默认 45s；超时/宿主未答 ⇒ **自动 reject-once + 立即 `session/cancel`（通知形式）**，全部落审计；
- 宿主可经座舱"权限申请卡"人工批准（`human_hook`，受同一超时约束）；
- 每次应答写 op-log（private scope）：`{ts, run_id, session_id, tool_call_id, decision, reason, policy_latency_ms, timed_out, target}`。

## 4 · 预算闸（`dsh_bridge/budget.py`，F7 按真机帧形状修订）

- **真机 usage_update 帧形状（实测）**：`{"used": 10575, "size": 1000000}` —— ACP
  **上下文压力口径**（used=当前上下文占用，size=上下文窗口），**不等价于计费 token**；
- 提取器同时支持两族字段：`used/size`（仪表语义：used 取 max，不做累加；闸 `budget_context_used`）
  与 `*token*` 族（累加；闸 `budget_tokens`，计价走平台自建价格表
  `price_table[model] = (每 1k 输入, 每 1k 输出)`）；
- dsh 的 token 计数是 4 字符/token 启发式（对 CJK/JSON 偏大），dsh 宿主里的第三方账本**不是契约**
  ——计费级数据若需要精确，须另立来源；
- **未知形状告警**：usage 帧没有任何已知数值字段时计 `unrecognized_count` 并发
  `dsh_usage_unrecognized` 事件——绝不静默计 0（F7 根因）；
- 触发：`budget_context_used` / `budget_tokens` / `budget_money` 任一超限 ⇒
  **先 `session/cancel`（通知形式），必要时 close**；事件进平台事件流 + op-log + health.json；
  触发是**锁存**的（不会自行解除）；
- **字段普查哨兵**：`dsh-check --full` 输出 `field_census` 行（实际 usage 键名 + 权限帧键名）
  ——rc 阶段字段名可能再变，普查行就是记录。

## 5 · 取消与超时语义（实测教训）

- `session/cancel` **必须是无 `id` 的通知**——请求形式会被 dsh **静默忽略**（实测跑了 35 次工具调用）；
- 权限请求**必须应答**——不答则回合永久挂住（实测挂满 240s）；
- 回合超时（平台侧默认 600s，可配）⇒ cancel ⇒ 宽限 ⇒ close ⇒ kill，全路径幂等；
- `stopReason` 归一：`end_turn`（正常）/ `cancelled`（取消或审批被取消）/ 其它原样记录；
- 任何 handler 异常：包容（不杀 run）**但必须可见**（stderr + `dsh_bridge_error` 事件），
  预算路径的异常会失败安全地取消回合——**绝不静默假死**（R16 实测教训）。

## 5.5 · 事件与黑板的接线边界（F9）

- `dsh_turn_event` / `dsh_message_chunk` / `dsh_tool_call` / `usage` 等事件由 runner 的
  `emit` 回调产出——**派发台/SSE 接线属下一轮**（`DSH_ACP_ENABLED` 开关随之落地）；
- `MappedEvent.blackboard_entry`（工具调用条目）目前**只有产出、没有消费者**——黑板落库
  同属下一轮接线；在接线落地前，请勿假设工具调用已进黑板/体检。

## 6 · 结构化结果契约

dsh **没有**"强制最终回复满足 JSON schema"的开关 ⇒ 平台约定：
1. 任务提示里要求 agent **用工具把结果 JSON 写进 cwd 下 `dsh_result.json`**；
2. 回合结束后平台读该文件并按**平台自己的 schema**（`core.schemas.validate_against_schema`）校验；
3. 校验失败 → 降级为"仅正文"（`result_data=None` + 显式校验错误），重试由调用方决定。

## 7 · 自检与运维

```
.venv\Scripts\python.exe main.py dsh-check            # 基础档：环境/握手/会话/列表（无 token 消耗）
.venv\Scripts\python.exe main.py dsh-check --full     # 完整档：真模型权限/取消往返（消耗 token，需委托方授权）
```

- `PASS` 全绿 → 接入面可用；`ENV-FAIL` → 修环境（ACL/DSH_BIN/版本）；`FAIL` → 平台或 dsh 侧缺陷，响亮上报；
- **运维降级**：`DSH_ACP_ENABLED`（默认关闭）——派发台接线（下一轮）后，未设此变量平台不发生任何 dsh 子进程，
  行为与 R15 交付态一致；回滚 = `git revert` 对应 commit（无迁移、无既有文件改写）；
- 诊断：`R16_TRACE=<path>` 记录 ACP 收发流水（排查挂死/漂移的第一工具）。
