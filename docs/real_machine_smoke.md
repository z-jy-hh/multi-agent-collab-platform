# 真机 smoke 证据（2026-10-17）

> 目的：证明「B1/B2 诚实语义」与「W0-1 模型来源」在**真实运行**下成立，而不是只在测试里。

## 运行

- 命令：`run_topology("supervisor", "回答：1+1 等于几？只输出数字。", run_id="smoke-…")`
- 凭据：仓库 `.env`（`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MODEL`），由 `crews.factory._load_env_file()` 载入
- 结果：`status=done`，22 个事件，1 张 agent 卡，3 条产出

## 观测（trace 原始值）

| 字段 | 观测值 | 结论 |
|---|---|---|
| `agent_card.executor` | **`crewai`** | 真实运行时才出现；离线为 `scripted`（B1 正确） |
| `agent_card.model` | **`deepseek-chat`** | 来自 `worker_model_name()`，不是 `null`（W0-1 生效） |
| `agent_card.platform_model` | `deepseek-chat` | 与 worker 模型**分两个字段**，不混用（B2 正确） |
| worker `agent_output` 键集 | `agent_id, backend_session_id, executor, output_chars, subtask_id` | 契约按设计落在 worker |
| summarize `agent_output` 键集 | `agent_id, output_chars, stage` | **无** executor/session —— 与「契约收窄到 worker」一致（B3） |

## 这条证据**证明**什么

1. 真实路径下 `executor=crewai`、`model` 非空 ⇒ 「原生/API」标识第一次有真数据可依；
2. 事件契约的「worker-only」是有意设计，不是遗漏；
3. 离线/真机两条路径的取值差异**可被区分**（scripted vs crewai）。

## 追加：**原生真机跑通**（同日第二次 smoke）

环境：`AGENT_REGISTRY=1`、`DISPATCH_HETERO=1`、`DSH_BIN=<…/@deepseek-ai/dsh/lib/bin.js>`、`DSH_EXPECTED_VERSION=0.0.1`

结果：

| 观测 | 值 |
|---|---|
| 被派发 agent | `dsh-exec`（legacy binding）+ **`researcher`（routing.map 生效）** |
| 事件序列 | `hetero_dispatch_started` → `dsh_session` ×2 → `hetero_dispatch_result` |
| **`audit_refs.backend_session_id`** | **`54686511-2135-4e02-9a95-b6227640d7ca`**（真 ACP 会话） |
| 结束原因 | `end_turn` |
| `wb` | 跳过（`enabled:false`，符合预期） |

**这条证明**：`routing.map` 能把**真实 agent_id** 接到原生后端；原生会话 id 真的存在 ⇒ UI 的「原生」徽标第一次有真证据。

**过程中修掉的两个真问题**（已补测试）：

1. `dsh_bin` 解析为空仍去 spawn（`node "" --profile acp`）⇒ 卡满 60s 才超时。现在**立刻**报 `backend.command_unresolved`（environment 类）。
2. 版本锁无机器级覆盖 ⇒ 部署的 CLI 报 `0.0.1` 时握手必失败。现在 `DSH_EXPECTED_VERSION` 可覆盖（该变量**本来就在 `command_env.allow` 里**）。

## 追加：**清理重复派发后的前后对比**（同日第三次 smoke）

| 指标 | 清理前 | 清理后 |
|---|---|---|
| `dispatched_agents` | `["dsh-exec", "researcher"]` | **`["researcher"]`** |
| 事件数 | 9（含 2 次 started / 2 次 result / 1 次 skipped） | **4** |
| `dsh_session` 事件 | 4 条（2 会话 × 2） | **2 条（1 会话）** |
| 真实会话 id | `54686511-…` | `db66ea1e-…`（`end_turn`） |

原因：`bindings` 里的占位 `dsh-exec/wb-exec` **每次 run 都会派发**，与 `routing.map` 的真实 `researcher` 叠加 ⇒ 同一后端开两个会话。
处理：移除占位 bindings（`bindings: {}`），真实 agent_id 统一走 `routing.map`；并加测试钉住「每个映射 agent 恰好派发一次」。

## 追加：**权限申请真机端到端**（同日第四次 smoke）

构造：`permission_mode="read-only"` + 任务「在工作区创建文件」⇒ 写操作被沙箱拒绝并**发起 escalate**，从而触发权限帧。

观测（真实）：

| 步骤 | 证据 |
|---|---|
| 请求到达 | `spi_permission_request`，`tool_call_id=call_00_r2dS…`，`options=["allow-once","reject-once"]`（**服务端给的，未发明**） |
| 程序化应答 | 通过 `session_registry.get(run_id).answer_permission(...)` 回 `allow-once` ⇒ 发出 `permission_answered` |
| **超时路径** | 第二次请求 `call_00_ZJ0…` **未应答** ⇒ **45s 后自动拒绝**：`decision=reject-once`、`timed_out=True`、`reason="host did not answer within 45s — auto reject-once"`；该轮以 `cancelled` 结束，**进程没有挂死** |
| 会话 | `backend_session_id=428e5fea-0a28-45e4-b6f3-8ee4bb1778bf` |

**结论**：权限卡的关键链路（请求 → 只列服务端选项 → 应答 → 审计事件）与**超时自动拒绝**都在真机上被触发过。

**边界（诚实）**：这是「主机程序化应答」，**不是浏览器点按钮**；浏览器路径由 `addEventListener` 注册 + node 渲染测试覆盖，未做真浏览器点击。

## 这条证据**不证明**什么（诚实边界）

1. **没有 ACP 会话**：本次没开 `DISPATCH_HETERO`，因此没有 `hetero_dispatch_result`、没有 `backend_session_id` ⇒ **「原生」标识仍未被真机证明**；
2. **没有 wb**：skeleton + 双闸，未接通；
3. **prompt 仍未持久化**：B2 窗口写「未携带」是事实，不是占位。

## 复现

```
.venv\Scripts\python.exe -c "from crews.factory import _load_env_file; _load_env_file();
from graphs.runner import run_topology; run_topology('supervisor','1+1=?',run_id='smoke-manual')"
```
跑完检查 `runs/smoke-manual/trace.jsonl` 里 `agent_card` 的 `executor`/`model`，然后 `cleanup_run`。

## 下一步（要真机证明「原生」）

需要：`AGENT_REGISTRY=1` + `DISPATCH_HETERO=1` + `dsh` CLI 可用（本机已具备），
且 registry 的 `bindings` 能命中真实 agent_id（当前 `dsh-exec/wb-exec` **不在 9 卡内**，见 G12）。
—— 这正是「bindings ↔ 9 卡」必须先解决的原因。

---

## 追加：**W4-1 角色级原生执行**（2026-10-18，HEAD `0ed42b3`）

环境：`NATIVE_RUNTIME=dsh`、`NATIVE_AGENTS=researcher`、`NATIVE_FALLBACK=off`、`AGENT_REGISTRY=1`、
`DISPATCH_HETERO=0`、`DSH_BIN=<…/@deepseek-ai/dsh/lib/bin.js>`、**`DSH_EXPECTED_VERSION=0.0.1`**
（ACP `initialize.agentInfo.version` 是 `0.0.1`；package 版本是 `0.2.0-rc.2` —— 这是 W4 的最先失败点，v3 任务书曾写错）。

| 判据 | 观测（原始值） |
|---|---|
| **C2 单角色** | run `38292fb8-1687-447c-a02e-6e3c55adb505`；`status=done`；答案 **42**；`backend_session_id=1b13c17b-f805-449f-9da9-ab218869d19e`；`executor=acp`；`stop_reason=end_turn`；`session_scope="38292fb8-…:researcher"`；`health=100.0`；`run_finished` 末行 `degraded=false` |
| **B 第二题** | run `aa8941b8-59be-4d7f-bc0c-abce1b2464b6`；答案 **9**；session `5767b237-676e-4b42-b0ad-7deaef67be27`；`health=100.0` —— 与 C2 的 session 不同（每 run 新会话） |
| **C2d 双 turn** | 同一 session `dadaef03-5388-498c-b0c0-dab068ebba45`；`new=1`、`close=1`、`turn_seq=[1,2]`；两轮输出均非空 |
| **C2c 工具面** | run `ff882309-0c42-4506-b3bb-fabec7a98c2e`；文件 `runs/dsh_dispatch/ff882309-…/hello.txt` = `w4-7f3a`；trace 有 `tool_call`/`tool_call_update` 帧 |
| **C2b 真机混合** | run `89d8aa2d-f0cf-4092-8779-6438510a8b6f`；`researcher` 卡片 `executor=acp/backend=dsh/model=null`，`writer/synth/analyst/judge` 为 `crewai`；`agent_output researcher subtask=s1 executor=acp session=5642c71f-…`；`writer stage=summarize executor=crewai`；`health=100.0`；`done/degraded=false` |
| **C4 回退（环境类）** | 空 `DSH_BIN` + `NATIVE_FALLBACK=crewai` ⇒ `native_fallback{code:"backend.command_unresolved"}`；`agent_output.executor=crewai`、`fallback=true`；`degraded=true` |

**这条证据证明**：角色层真的接到了 ACP（真会话 id 进 `agent_output`）、同会话可顺序复用、工具面真的可用、同一 run 内原生与 CrewAI 共存、环境失败能显式回退并留证。

**不证明**（诚实边界）：

1. 延迟/成本（无对照测量）；
2. 权限卡全链路（浏览器套件是 route fixture；CLI 无应答方）；
3. 健康分的**源头**：补强 D 只在检查器里窄口径豁免 bridge 审计 op（`agent_id=="dsh_bridge"` + `key` 前缀 `dsh_session::`），
   `dsh_bridge/session.py::_record` 仍写 `clock={}`（红线件未动）。修复前每次 run 83.3，修复后三次真机 run 均 100.0。
4. C4 的 scripted 形态未跑（本机 `.env` 有 key）；离线版已覆盖。

复现：见 `reports/W4_native_runtime_report.md` §9。

---

## 追加：**W6 profile 真机 E2E**（2026-10-18，HEAD `6355c55`）

环境：`NATIVE_RUNTIME=dsh`、`NATIVE_AGENTS=researcher`、`PROFILE_IDENTITY=1`、`AGENT_REGISTRY=1`、`DISPATCH_HETERO=0`、`DSH_EXPECTED_VERSION=0.0.1`。

| 项 | 观测 |
|---|---|
| run | `w6-profile-e2e`（single） |
| 答案 | **42** |
| `profile_bound` | `selection_id=sel-03732666d6b8`；`researcher@1`、`profile_sha256=d0edfbd44829…`、`prompt_sha256=5630aed6…`、`rule up-conf-5d6a3edc` |
| `agent_output` | `profile_selection_id=sel-97535ab545a6`、`executor=acp`、`backend_session_id=db220019-597e-4ff4-8c13-269eb70cd504`、`stop_reason=end_turn` |
| 会话 | `dsh_session new` → `close`（同一 id） |
| 健康 | `100.0 all checks passed` |
| `profile_check` | **0 条**（hard `path_under` 通过，不是没检查） |

**不证明**：UI → `POST /api/runs` → 真机的整链（本次直接 `run_topology`）；hard 失败重试的真机行为；多 agent 真机 profile。
详见 `reports/W6_profile_e2e_report.md`。
