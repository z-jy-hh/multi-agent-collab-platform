# 派发契约（G12 / W0 之后的事实版）

> 目的：把“agent 怎么落到后端”的现行规则写死，避免文档与代码漂移。
> 相关实现：`agent_bridge/hetero_dispatch.py`、`agent_bridge/dispatch_hooks.py`、
> `agent_bridge/negotiation.py`、`agents/registry.yaml`。

## 一 · 谁算“绑定”（两代并存，语义不同）

| 键 | 形状 | 状态 | 语义 |
|---|---|---|---|
| `routing.map` | `{real_agent_id: backend_id}` | **现行** | 真实 9 卡 → 后端；**每个映射 agent 每次 run 恰好派发一次** |
| `bindings` | `{legacy_id: "backend:"}` | **兼容** | 旧占位 agent（`dsh-exec`/`wb-exec`）；**已从 registry 移除**，仅保留解析能力 |

> 为什么移除：占位 binding 与 `routing.map` 叠加会**同一后端开两个会话**（真机实测 2→1 的修复，见 `docs/real_machine_smoke.md`）。

## 二 · 派发前的闸（真实顺序）

0. **总开关**：`DISPATCH_HETERO=1`，否则整个 pre-pass 不跑（`runner.py`），本文件的其余条目都不适用。
1. **后端存在且启用**：`enabled=false` ⇒ 跳过 `backend absent or disabled in registry`（**先于**同意闸）。
2. **同意闸（W0-4）**：`requires_authorization=true` 且无 `DISPATCH_CONSENT_TOKEN` ⇒ **跳过** `requires-authorization`。
3. **模式协商（W3-2）**：声明了 `modes` ⇒ `negotiate()`；无交集 ⇒ 跳过 `no-common-mode`，**不构建后端**。
4. **注册表闸（最后）**：`AGENT_REGISTRY=1` 在 `build_backend` 内检查；关闭时先发 `hetero_dispatch_started`、再发 `hetero_dispatch_error`（"factory gate"）——所以健康后端也会显示 **`不可达`**。
5. **代码级闸**：如 `wb` 的 `WB_ENABLED`，由 `register_backend(..., gate=...)` 决定，**注册表绕不过**。

## 三 · 后端状态（四态，与 `ui/enums.js` 的 `BACKEND` 对齐）

| 状态 | 判据 | 展示 |
|---|---|---|
| `available` | `enabled=true`、未要求授权 | `可打开` / `API` |
| `unauthorized` | `requires_authorization=true` | `需授权` |
| `unreachable` | **观测到**派发错误（`hetero_dispatch_error`，或 `hetero_dispatch_result.error`） | `不可达` |
| `disabled` | `enabled=false` | `未接通` |

> `native` **不是**后端状态，是证据状态：只有 `hetero_dispatch_result.audit_refs.backend_session_id != null` **且无 `error`** 才显示“原生”。

## 四 · 可观测事件

| 事件 | 含义 |
|---|---|
| `hetero_dispatch_started` | 开始构建/派发（**注册表闸关闭时也会发**） |
| `mode_negotiated` | 协商选中（adapter/transport/protocol_version） |
| `hetero_dispatch_result` | 结束；**可能带 `error`**（dsh 把 prompt 失败转成 `TaskResult.error`），此时**不是**成功 |
| `hetero_dispatch_error` | 构建/派发抛错（后端窗口转 `不可达`，清会话） |
| `hetero_dispatch_skipped` | 跳过，`reason ∈ {backend absent or disabled, requires-authorization, no-common-mode}` |
| `hetero_dispatch_resolved` | 解析出的绑定（`bindings` + `routing.map`，`agent_id → backend_id`），在派发前发出 |

## 五 · 不变式（测试钉住）

- 对一个**已启用、已授权、协商通过**的映射 agent，每次 run **恰好一次** `hetero_dispatch_started`（跳过的 agent 是 0 次，属预期）；
- 无会话 id 或 `result.error` ⇒ 界面**不得**显示“原生”；
- `no-common-mode` / `requires-authorization` ⇒ **不得构建后端**；
- `dsh_bin` 未解析 ⇒ 立刻 `SpiError(kind=environment, code=backend.command_unresolved)`（不干等超时；注意它**不是**事件字段，`hetero_dispatch_error` 只带 `error` 文本）。

## 六 · 已决策的例外（F4）

**只读展示端点**（`GET /api/backends`、`GET /api/modes`）在 `AGENT_REGISTRY=0` 时**仍可读 registry**。

- 理由：它们是**展示**面；若也受闸，默认配置下界面全空，比“读到但不派发”更糟。
- 边界：**派发侧不变**——`build_backend()` 在闸关闭时仍返回 `None`（构造不出任何后端）。
- 机检：`tests/integration/test_backends_endpoint.py::test_read_only_endpoints_are_exempt_from_the_registry_gate` 同时钉住两半（端点 200 + 构造 None）。
- 若将来要严格化：给这两个端点加 `registry_gate()` 检查，并把该测试改成期望 503/空列表。

## 七 · 原生角色派发（W4-1，append）

> 本节由 W4-1 引入；与 §一–§六 的既有规则**并存**，只在 `NATIVE_RUNTIME=dsh` 时生效。

- **前置**：`NATIVE_RUNTIME=dsh` 且 `AGENT_REGISTRY=1`；两者缺一即**配置类失败**（`native.registry_gate`），run 不启动、不回退。
- **让位（exactly-once）**：`native_agents = NATIVE_AGENTS ∩ routing.map`。pre-pass 对它们**不派发**，只发
  `hetero_dispatch_skipped{reason:"native-role-runtime"}`；**§四的 reason 集合自此扩为**
  `{backend absent or disabled, requires-authorization, no-common-mode, native-role-runtime}`。
  集合在 `preflight` 冻结；即使 preflight 退化到 inner，也**不放回** pre-pass（否则同一角色跑两次）。
- **执行**：该角色由图内 `ACPRuntime` 经 dsh ACP 执行；`agent_output` 带真实 `backend_session_id` / `executor="acp"` /
  `stop_reason` / `session_scope`；`dsh_session` 给出会话三元组；`dsh_turn_event` 带 `agent_id` + `turn_seq`。
- **关闭顺序**：`runtime.close()`（发 `dsh_session(close)`）**早于** `run_finished` / `export_snapshot`；T5 的「末行是
  `run_finished`」不变（错误路径仍无 `run_finished`，见既有事实）。
- **失败**：默认**响亮**（`NATIVE_FALLBACK=off`）；`=crewai` 才回退，且必须发 `native_fallback` + `run_finished.degraded=true`。
  配置类错误**永不回退**。
