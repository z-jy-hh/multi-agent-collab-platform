# R17 端点权威清单（endpoint authority）· ACCT1 交付件 2/3

> **建立依据**：工单 `workorder_ACCT1_three_ledgers.md`（sha256_16 `cd52cd325f8fb4bf`）A2 + 附录 A-2；源头 = `reports/R17_program_logic_audit_by_dsh.md` §2（S1 端点账目不自洽）。
> **红线第 4 次修订（计数 → 白名单）**：自此**本文件是端点唯一权威**。旧表述「2 个受约束写端点（`/annotations`、`/allocation`）+ 1 个计算型端点（`/api/plan/draft`）」为**计数式**，会漏掉未列入清单的同类写面；本清单把**全部 20 条路由（POST 8 / GET 12）**在册，并收紧为：
> **「端点白名单：新增任何路由（含 GET 与计算型 POST）须先出扩围申请（扩围令），无令不入册，不入册不上线。」**
> **机检**：`tests/tools/ledgercheck.py` 对本表与 `api/routes.py` 的 `@app.` 装饰器做**精确三键比对（method + path + handler 名）**：0 未登记 / 0 幽灵；改路径、换处理函数都会被抓。
> **入册规则（A-2）**：每条目必须带 `approved_by` / `order_ref`；**没有批准来源的条目不得入册**。
> **纪律**：**append-only + 稳定排序**（POST 组在前、GET 组在后，组内按本版登记顺序固定；历史条目只增不改）。

## 一 · 端点登记表（现测 **21** 条；20 条基线 + W1B 新增 `GET /api/backends`）

| # | 方法 | 路径 | 处理函数 | 改状态 | 可写文件/面 | 现行约束 | 红线保护 | 归属轮次 | approved_by | order_ref |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | POST | `/api/runs` | `create_run` | 是（创建 run，最强写） | `runs/<id>/` 目录 + `run_meta.json`（及后续 run 状态/事件文件） | 目录创建即登记 meta；`DISPATCH_HETERO=1` 时 pre_run_gate 前置（绑定/预算/并发/授权） | 是（白名单在册） | G6+G7a（提交 `fe3d2b6`，历史弧） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有；P0 扩围：可选 `profiles` body 字段（不新增路由，2026-10-18） |
| 2 | POST | `/api/runs/{run_id}/cancel` | `cancel_run` | 是（状态机 → cancel） | run 状态文件/取消事件 | 状态机变迁经 runner；不可逆转向需事件留痕 | 是 | G6+G7a（`fe3d2b6`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 3 | POST | `/api/runs/{run_id}/recover` | `recover_run` | 是（恢复状态机） | `recovery.json` + op-log | op-log 幂等；计量损坏 fail-closed（`DISPATCH_COUNTERS_ALLOW_CORRUPT` 语义） | 是 | R17 恢复基线前既有（`ef00d85`；精确引入轮因 2026-10-10 事故不可考）；B2 状态机经 BCFIX2/3 修复 | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 4 | POST | `/api/runs/{run_id}/review` | `record_review` | 否（追加评审产物） | run 目录内评审产物文件 | 待决/已答可区分（P0b 判据面） | 是 | R17 恢复基线前既有（`ef00d85`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 5 | POST | `/api/runs/{run_id}/permission` | `answer_permission` | 是（权限状态机应答） | permission 应答事件/状态 | 选项须在待决集合内（伪造选项 422）；`answered_by` 三态 | 是 | R17 恢复基线前既有（`ef00d85`；P5 权限卡判据面） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 6 | POST | `/api/runs/{run_id}/annotations` | `add_annotation` | 否（追加注记） | annotations 存储（写前经掩码） | **受约束写**：写前脱敏（`REDACTION_ENABLED` 默认开）；模板第 24 行红线点名 | 是（红线点名端点） | R17 P1 画布线（提交 `743c657`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | `平台新模式_P1执行任务书_终版.md`（sha256_16 `5f0c7d6679d35639`） |
| 7 | POST | `/api/runs/{run_id}/allocation` | `add_allocation_decision` | 否（追加决定） | allocation 决定存储（写前经掩码） | **受约束写**：写前脱敏；模板第 24 行红线点名 | 是（红线点名端点） | R17 P1-HOSTPICK 线（提交 `5b796b3`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | `workorder_P1HOSTPICK_host_pick_and_pairing.md`（sha256_16 `cc2599dbf0a6ed77`） |
| 8 | POST | `/api/plan/draft` | `plan_draft` | 否（**纯计算，零副作用**，DSH 端点探针已验） | 无 | fail-closed（坏输入响亮拒绝）；不落盘 | 是（红线点名计算型端点） | R17 P2 画布线（提交 `40c4dbb`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | `workorder_P2_logiccanvas_stage2_and_Cprime.md`（sha256_16 `d24b14312defa30f`） |
| 9 | GET | `/` | `index` | 否 | 无（读静态页） | 只读 | 是（白名单在册） | G6+G7a（`fe3d2b6`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 10 | GET | `/api/sessions` | `list_sessions` | 否 | 无 | 只读（dsh 会话列表，`DSH_HOME`） | 是 | G6+G7a（`fe3d2b6`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 11 | GET | `/api/doctor` | `run_doctor_endpoint` | 否 | 无（诊断只读） | 只读；doctor 输出走自检 | 是 | R17 恢复基线前既有（`ef00d85`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 12 | GET | `/api/evolve/versions` | `evolve_versions` | 否 | 无 | 只读 | 是 | N2 线（提交 `ff4ba59`，只读 GET） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | `workorder_N2_redaction.md`（sha256_16 `17cac3312d525f20`） |
| 13 | GET | `/api/modes` | `list_modes` | 否 | 无 | 只读；topologies 键（P1 画布线扩展）；P0/P2 扩围 additive：`agents/profiles/skills/rules/profile_error`（不新增路由，2026-10-18） | 是 | G6+G7a（`fe3d2b6`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 14 | GET | `/api/runs` | `list_runs` | 否 | 无 | 只读列表 | 是 | G6+G7a（`fe3d2b6`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 15 | GET | `/api/runs/{run_id}/artifacts` | `get_artifacts` | 否 | 无 | 只读 | 是 | R17 恢复基线前既有（`ef00d85`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 16 | GET | `/api/runs/{run_id}/events` | `stream_events` | 否 | 无（SSE 流） | 只读；`DSH_ACP_ENABLED` 过滤 dsh 事件（must-answer 豁免） | 是 | G6+G7a（`fe3d2b6`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 17 | GET | `/api/runs/{run_id}/health` | `get_health` | 否 | 无 | 只读（存活面） | 是 | G6+G7a（`fe3d2b6`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 18 | GET | `/api/runs/{run_id}/state` | `get_state` | 否 | 无 | 只读 | 是 | G6+G7a（`fe3d2b6`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 19 | GET | `/api/runs/{run_id}/trace` | `get_trace` | 否 | 无 | 只读 | 是 | R17 恢复基线前既有（`ef00d85`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 20 | GET | `/api/runs/{run_id}/unified` | `get_unified_view` | 否 | 无（读 dsh 会话/WB 审计日志，`DSH_HOME`/`WB_AUDIT_DIR`） | 只读；来源缺失显式标注（F4/F5 判据面） | 是 | R17 恢复基线前既有（`ef00d85`） | DSH 附录 A-2（令 `cd52cd325f8fb4bf`） | 基线既有（扩围令 N/A） |
| 21 | GET | `/api/backends` | `list_backends` | 否 | 无（只读 registry 文件） | 只读；**禁返回 `command`/`command_env`/`cwd`/凭据**；载入失败 503 | 是（白名单在册） | W1B（2026-10-17） | 业主口头授权（2026-10-17） | `workorder_W1B_backends_endpoint.md`（sha16 `ba628e32ee892135`） |

> 注（#10 `/api`）：根路径 GET handler 名以 `ledgercheck` 机检从 `api/routes.py` 实读为准；本表登记值若与实读不符，机检会判「幽灵」并阻断——这正是白清单的咬合点。

## 二 · 归属轮次勘定说明（诚实边界）

- `git log -S"<path>" -- api/routes.py` 可锚定 **#1/#2/#9/#10/#13/#14/#16/#17/#18**（`fe3d2b6` G6+G7a）、**#6**（`743c657`）、**#7**（`5b796b3`）、**#8**（`40c4dbb`）、**#12**（`ff4ba59`）。
- **#3/#4/#5/#11/#15/#19/#20** 的最早可见提交是 `ef00d85`（2026-10-10 `.git` 对象库损毁后的恢复基线，历史深度截断至 R14）——**精确引入轮不可考**，如实登记为「恢复基线前既有」，不臆测。
- 事故映射见 `reports/INCIDENT_2026-10-10.md`。

## 三 · 改动权（与命名与所有权规则衔接，全文见 `docs/order_report_index.md` §四）

| 改动类型 | 所需批准 | 说明 |
|---|---|---|
| 新增任何路由（POST 或 GET） | **扩围令**（先申请后入册） | 白名单语义：无令不入册、不入册不上线 |
| 修改既有端点约束（变严） | 执行方在报告披露 + 复核方确认 | 只能更严 |
| 修改既有端点约束（变宽）/换处理函数 | **扩围令** | 视同新增处理 |
| 修正本表笔误（不触语义） | 执行方修正 + 报告披露 | 须在 ledgercheck 保持 0/0 |

## 四 · 变更史（本账自身）

| 日期 | 变更 | 依据 |
|---|---|---|
| 2026-10-11 | 初版建立：20 条全量入册（POST 8 / GET 12）；红线第 4 次修订生效（计数 → 白名单） | 工单 `cd52cd325f8fb4bf` A2/A4 + 附录 A-2；DSH 审计 §2 |
