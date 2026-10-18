# 基线变更记录（baseline changes）

> 规则：**不改基线就不改这里；改基线必须在这里留一行**。
> 每次变更必须包含：旧值 → 新值、原因、证据（diff/双跑一致）、提交、验证人。

## 2026-10-17 · M1/W0-1 事件契约 v2（**rev2：B1/B2 诚实性修订**）

评审（子 agent `20bc73a7`，8/10）指出 rev1 的两处语义错误，rev2 修正后**再次重定**：

| 问题 | rev1 | rev2 |
|---|---|---|
| `executor` | 硬编码 `"crewai"`（默认离线跑的是 `ScriptedRuntime` ⇒ 说假话） | `_runtime_kind(runtime)` → `scripted` / `crewai` |
| `model` | 取平台 LLM（评委模型），当成 worker 模型 | `model: null`（worker 模型在 runtime 之后，尚未可知）+ `platform_model`（评委模型，名字如实） |

| 摘要 | rev1 | rev2 |
|---|---|---|
| supervisor | `774399a0…` | `a758511d…` |
| debate | `bd6df8f3…` | `61a06c18…` |
| R5 fixture | `e97af48c…` | `53fb3cac…` |

**rev1 → rev2 的差异证明**：payload key-set 只新增 `platform_model`，无删除（`agent_card`）；`agent_output` 的 key-set 不变（仅 `executor` 取值由 `crewai` 变 `scripted`）。

**契约收窄（B3）**：`agent_output` 的 `executor`/`backend_session_id` **仅加在 worker 节点**（`builder.py` 的 worker emit）；summarize / generic / debate 三处 `agent_output` 形态不变（debate 仍发 `agent_output`，只是不带这两个字段）。

**native 判据（B4）**：图事件的 `backend_session_id` 恒为 `null`（平台 worker 没有后端会话）。**"原生"只能由 `hetero_dispatch_result.audit_refs.backend_session_id` 判定**，不得由图事件推断。

---

## 2026-10-17 · M1/W0-1 事件契约 v2（rev1）

| 项 | 内容 |
|---|---|
| 原因 | 图事件补字段：`agent_card` + `executor`/`model`；`agent_output` + `executor`/`backend_session_id`。目的：UI 能诚实标注「原生 / API」，并让“标原生必有会话”可机检 |
| 影响对象 | `tests/integration/test_mem1_guardrails.py::G1_SUPERVISOR_DIGEST`、`G1_DEBATE_DIGEST`；`tests/integration/test_p2_adversarial.py::BASELINE_DIGEST` |
| 旧值（supervisor） | `e2f75d7b027a8e500fc91c482400a43e63ed500ebeba460a556009b39aebef75` |
| 新值（supervisor） | `774399a08d12af493bc2e1ad14eb30753d8e4bbcf5e3fa57f5a9081f14ca9ee9` |
| 旧值（debate） | `2c9926adbf2b3de064f1ba952563389babce42506be57b464c7a0acc971cfbae` |
| 新值（debate） | `bd6df8f3b30477449b9dc07fb144fb5aeb85badfd0701122e50a81679ef7cf1c` |
| 旧值（R5 supervisor fixture） | `67b45120cd702737ab38f86d0c43664193ec5895223edbfe64fb8391439ee6b5` |
| 新值（R5 supervisor fixture） | `e97af48c8649418850bef8eb368f965a147aa134c57fdbae7a2c8333f20640e0` |

### 证据（按 `change-plan.v1.md` §C 的四步流程）

1. **先证 diff，不抄摘要**：改动前用探针复现三个旧值（逐字一致），改动后重跑并对比 payload key-set：

   | 事件 | 新增字段 | 删除字段 |
   |---|---|---|
   | `agent_card` | `executor`, `model` | 无 |
   | `agent_output` | `executor`, `backend_session_id` | 无 |

   debate 模板只出现 `agent_card`（无 worker），与事实一致。
2. **同一改动画两次**：两次摘要一致（确定性）。
3. **留痕**：本文件。
4. **全量门禁**：`pytest` 全绿 + `ledgercheck PASS` + `audit_invariants --check clean`。

### 兼容性说明

旧 `trace.jsonl` 不含新字段。UI 约定（待 W1a 渲染器落地时实现并测试）：
- 缺 `backend_session_id` ⇒ 标 `API`（**不得**标 `原生`）；
- 缺 `model` ⇒ 显示 `—`；
- 缺 `executor` ⇒ 视为 `crewai`（平台 worker）。

### 历史声明（保留，不删）

> 旧值证明的是「MEM1 包装未改变行为」这一**历史命题**：该命题在当时的冻结与双跑中已成立。
> 本次重定不推翻它，只是把守卫从「证明过去」转为「守住未来」。

---

## W4-1 · 角色级原生执行的证据口径（2026-10-18，append）

**变更（只在 native run 出现；`NATIVE_RUNTIME` 未设时零新增）**：

| 事件 | 新增字段 | 说明 |
|---|---|---|
| `agent_output` | `executor="acp"`、真实 `backend_session_id`、`stop_reason`、`session_scope`、`fallback` | 仅该 agent 实际走原生/回退时 |
| `agent_card` | `backend: "dsh"` | 仅原生角色；`model` 仍为 `null`（后端模型不可观测） |
| `run_finished` | `degraded` | 仅 native run，`false`/`true` |
| 新事件 | `native_role_error` / `native_fallback` / `native_close_timeout` | 原生失败/回退/关闭超时 |

**取代关系（明确边界）**：本文 §B3（`executor`/`backend_session_id` 只加在 worker）与 §B4（「图事件 `backend_session_id` 恒 `null`、
`native` 只认 hetero 事件」）**只描述 W4 之前的事实**；自 W4 起由本节的「native-only 字段 + 任一事件带会话 id 即可判 native」取代。
历史条目**保留不删**（append-only）。

**旧值→新值**：N/A（不是改既有字段取值，而是**只在 native run 新增**字段/事件；off 态由 C6 基线逐事件比对守住）。

**证据**：`reports/W4_native_runtime_report.md`（C1/C2/C6 原始输出）+ `reports/w4_off_baseline.json`（W4-1-0，parent `9a91e7d`）。
**提交**：见报告「提交区间」。**验证人**：独立复核方（待）。

---

## W6 · 身份模型（P0–P2，2026-10-18，append）

**变更（仅在显式绑定 profile 的 run 出现）**：

| 事件/字段 | 变化 | 说明 |
|---|---|---|
| `profile_bound` | 新事件 | run 级绑定表：`selection_id` + 每 agent 的 profile/rule sha |
| `agent_output.profile_selection_id` | 新增（仅绑定非空） | 回指 `profile_bound`；off 态零新增 |
| `agent_card` | **不变** | 仍为计划级证据；C6/R5 canonical 不受影响 |
| `native_session_rotated` | 新事件（native，`used/size ≥ 0.7`） | O6 上下文轮换：旧会话 close → 下一轮 new；`rotate_at=0` 可关 |
| 行为 | flag=1 + `profiles` 时 preset 作 prompt 源 + brief | `PROFILE_IDENTITY=0` 回旧路径 |

**旧值→新值**：N/A（新字段只在绑定非空时出现；默认参数不变）。
**基线**：旧 C6 基线保留（`PROFILE_IDENTITY=0`）；flag=1 新基线 `reports/p2_profile_baseline.json`（固定 fixture，single 有序 / supervisor 多重集）。
**证据**：`tests/integration/test_profile_baseline.py` + `test_profile_identity_dual.py`；P2 UI 抽屉在 `api/static/ui/profiles.js`。
