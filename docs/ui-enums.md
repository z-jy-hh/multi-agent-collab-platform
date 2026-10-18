# UI 枚举定义（M11 · 唯一来源）

> 目的：界面上的状态词**只有这一份定义**，前端常量、后端事件、判据都从这里取值。
> 规则：**新增状态必须先在本文登记**，再改代码；未登记的字符串不得出现在 UI。

---

## 1 · 界面七态（`state`）

| 值 | 含义 | 判据（怎么判定） | 允许的交互 |
|---|---|---|---|
| `empty` | 无任务/无窗口 | 左栏 `runs` 为空 | 仅新建 |
| `live` | 运行中，事件在流 | `run.status == running` 且 SSE 连接中 | 发送/暂停/取消 |
| `paused` | 画面暂停（**执行未停**） | 客户端 `paused=true`；服务端仍 running | 继续/取消 |
| `done` | 正常结束 | `run.status == done` 且无待处理申请 | 只读 + 回放/回滚标记 |
| `failed` | 失败结束 | `run.status in (failed, cancelled)` | 只读 + 重试（若端点） |
| `replay` | 回放历史版本 | `viewing_version != latest` | 只读；输入禁用 |
| `offline` | 事件流断开 | SSE `onerror` 且未恢复 | 只读 + 重连 |

**互斥规则**：`replay` 优先于其它状态；`offline` 优先于 `live/paused/done/failed`（单独成态，不再拼 `live+offline`）；`paused` 优先于运行状态。未知 `run.status` 一律回退 `live`，**不得新增第八个值**。

## 2 · 权限申请四态（`perm`）

| 值 | 含义 | 判定来源 | UI |
|---|---|---|---|
| `pending` | 已请求、未回答 | 收到 `spi_permission_request` 且无对应 `permission_answered` | 橙卡 + 倒计时（**需 deadline 字段**） |
| `allowed` | 已允许 | `permission_answered.option_id == allow-once` | 绿条，2s 收起 |
| `rejected` | 已拒绝 | `permission_answered.option_id == reject-once` | 红条，2s 收起 |
| `expired` | 超时未答 | 服务端 **`spi_permission_expired`**（含 `tool_call_id`/`timeout_s`；只标记对应卡片，不清空其它 agent 的卡） | 灰条"已超时，该轮已自动拒绝" |

**禁止**：`allow-all` / `remember` / `auto-approve` —— 协议与产品都不提供。

## 3 · 后端四态（`backend`）

| 值 | 含义 | 判定来源 |
|---|---|---|
| `available` | 可调度 | registry `enabled:true` 且 gate 通过 |
| `unauthorized` | 需授权 | `requires_authorization:true` 且无 consent token |
| `unreachable` | 不可达 | **观测到** `hetero_dispatch_error`，或 `hetero_dispatch_result` 带 `error`（注意：`AGENT_REGISTRY=0` 也会让健康后端显示此态） |
| `disabled` | 已禁用 | registry `enabled:false` |

**注意**：`wb` 现在是 **skeleton + disabled + 双闸** ⇒ UI 只能显示 `unreachable`/`disabled`，**不得显示"可用"**。

## 4 · 能力标识（两个不同概念，别混）

### 4.1 事件里的 `executor` = **谁在执行**（运行时）

| 值 | 含义 | 来源 |
|---|---|---|
| `crewai` | CrewAI runtime（有 API key 的真实运行） | `agent_card.executor` / `agent_output.executor` |
| `scripted` | 离线脚本 runtime（默认 `fake` 模式） | 同上 |
| `acp` | **W4 起**：角色级原生执行（dsh ACP，`ACPRuntime`） | `agent_output.executor`（该 agent 的真实执行者） |
| `unknown` | 其它 runtime 类名 | 同上 |

> `agent_card.model` = **worker 真实模型**（有 key 时由 `crews.factory.worker_model_name()` 提供，无 `openai/` 前缀）；
> 离线 `ScriptedRuntime` 下为 `null`（没有可声明的模型）。
> 评委/规划模型另放 `platform_model`，**不得**拿它冒充 worker 模型。
> **W4 补充**：原生角色的模型由后端管理、平台不可观测 ⇒ `agent_card.model` 为 `null`，不得填 `platform_model`。
> `agent_card.executor` 是**计划**（卡片在执行前发出），`agent_output.executor` 是**事实**；回退时两者可不同，以 `agent_output` 为准。

### 4.2 UI 的「原生 / API / 未接通」= **有没有真会话**

| 值 | 含义 | 判定 |
|---|---|---|
| `native` | 背后有真 ACP 会话 | **W4 起**：任一事件的 `backend_session_id` 非空（原生角色事件带真会话 id；hetero 事件仍看 `audit_refs.backend_session_id`） |
| `api` | 无后端会话 | 上述字段为空 |
| `unconnected` | 后端存在但未接通 | 后端态 `unreachable`/`disabled` |

**断言**：`native` ⇔ **某个事件**的 `backend_session_id != null`。
**W4 起**：§4.2 旧句「图事件的 `backend_session_id` 恒 `null`、不得由图事件推断」**只描述 W4 之前的事实**，自 W4 起由本节取代（历史条目保留，见 §7）。

## 5 · 与代码的对应

| 枚举 | 前端常量 | 后端来源 |
|---|---|---|
| 七态 | `STATE` | `run.status` + 客户端 `paused`/`replay`/SSE |
| 权限四态 | `PERM` | `spi_permission_request` / `permission_answered` / `spi_permission_expired` |
| 后端四态 | `BACKEND` | `GET /api/backends`（**W1b 已入册**，只读；不在册的前端不得假定存在） |
| 执行者 | `EXECUTOR` | `agent_card.executor`（计划）/ `agent_output.executor`（事实），值：`crewai`/`scripted`/`acp`/`unknown` |
| 原生/API | （无独立常量） | 取值见 `api/static/ui/backends.js` 的 `backendBadge` 与 `index.html` 的 `turnBadge`；`原生` 仅当存在真会话 id |

## 6 · 变更规则

1. 新增枚举值 ⇒ 先改本文 + 判据 + 前端常量，再改实现；
2. 删除/改名 ⇒ 需同步事件与判据，禁止只改 UI；
3. 本文与代码不一致时，**以本文为准**，并开 issue 修代码。

## 7 · 变更史（append-only）

| 日期 | 变更 | 依据 |
|---|---|---|
| 2026-10-18 | `EXECUTOR` 增 `acp`；§4.1 补「卡片=计划 / agent_output=事实」；§4.2 的 `native` 判定扩为「任一事件带 `backend_session_id`」，旧句标注为 W4 之前的事实 | W4-1（`docs/../W4_task_book.md` D7；`ACPRuntime` 角色级原生 + `agent_output.backend_session_id`） |
