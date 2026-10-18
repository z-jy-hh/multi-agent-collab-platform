# R17 判据台账（criteria ledger）· ACCT1 交付件 1/3

> **建立依据**：工单 `workorder_ACCT1_three_ledgers.md`（sha256_16 `cd52cd325f8fb4bf`）A1 + 附录 A-1；源头 = `reports/R17_program_logic_audit_by_dsh.md` §3（S2 判据侵蚀）§12B（第一版）。
> **纪律**：**append-only + 稳定排序**（模块表按路径升序；后续轮次只追加/在"变更史"列追加新行，不覆盖历史条目）。
> **机检**：`tests/tools/ledgercheck.py`（校验 ① collect 每个节点均有归属 **0 无归属**；② 台账显式键均真实存在 **0 幽灵**；③ 桶计数/比例输出；④ parametrize 用例数核对——计数漂移为 WARN 不阻断）。

## 0 · 收集口径与对应键声明（A-1 第 4/7 条）

- **收集命令**（仓库根执行，env 剥净钉死，与门禁同口径）：
  `/usr/bin/env -u PYTHONPATH ./.venv/Scripts/python.exe -m pytest --collect-only -q`
  （pytest.ini：`testpaths = tests`，默认 `test_*.py` 发现；本令未改任何收集配置。）
- **登记时点**：2026-10-11（ACCT1 开工现测，HEAD `f9dc4a5`，树 CLEAN）：**69 个测试模块 / 533 个唯一收集节点 / 545 条用例（参数化增量 +12）**。
  **现测（2026-10-17，HEAD 见 git log）：94 模块 / 701 唯一节点 / 721 用例**（W0/W1a/W3/复核修复新增守卫所致；M-UI-BROWSER 模块累计 23 节点/31 用例，自 `b87a881` 套件落地起 +11 节点/+11 用例）。
  **现测（2026-10-18，P0 收尾后）：102 模块 / 763 唯一节点 / 784 用例**（新增 `M-UI-BROWSER`、`M-NATIVE-RUNTIME`、P0 收尾守卫）。
  - 口径注：审计与令文基线称"528 函数 / 48 文件"；现测为准（A-4 精神）——"唯一收集节点" = 收集行去参数化后缀（`[...]`）的 `文件::类::函数` 或 `文件::函数`；类分组（`TestX::test_y`）各计一节点。
- **对应键**：**显式映射表 = 模块级默认 + 函数级例外**（附录 A-1）：
  - 模块级默认：`tests/.../test_x.py` 整模块归入某判据组（§2 表）；
  - 函数级例外：仅当同模块跨多条判据时在 §3 显式列 `文件::节点名`（**当前为空**，见 §3 声明）；
  - 双向校验在这套映射上仍是**节点级精确**的。
- **桶**：`named`（保护具名判据/机制）与 `infra`（基建/阴性/工具自测）两桶都报计数与比例；**infra 桶逐条给归属理由**；infra 桶现测 **22 节点（2.9%）**，未超 ~60 条阈值。
- **账不掩盖缺口**：计数漂移、无用例、缺条目均如实输出，不删测试、不改口径。
- **自指处理（谁校验校验器）**：`tests/tools/ledgercheck.py` 自身**不含** `test_*` 可收集用例（文件名不匹配默认发现模式），故不进入 §2 模块表；其自测以 `--selftest` 独立运行并列入门禁第 6 项。校验器的**外部校验** = 复核方（DSH）独立跑 `ledgercheck.py` 并抽查 2–3 条台账条目（反证规则）；若未来给 ledgercheck 加 pytest 用例，须在本账 §2 登记（归 `I-TOOLS`）。

## 1 · 判据组登记（组级元数据）

| 组 ID | 组名 | 桶 | 节点数(登记) | 保护测试 | 当前强度 | 引入轮 | 强度变更历史 | 判别器 |
|---|---|---|---|---|---|---|---|---|
| C-CANVAS | 画布 C 系（C1–C6, C4b, C4c） | named | 7 | `tests/integration/test_logic_canvas.py` | 强（含掩码/上限边界） | R17 P1 画布（令 `5f0c7d6679d35639`） | F1 掩码修复、F5 上限新增（P1FIX，令 `97fc350b3b797ffe`） | 掩码摘除必红（DSH 独立重做确认，`R17_P1FIX_review_by_dsh.md`） |
| C-NOMINATE | 自荐分配 B 系（B1–B7b） | named | 7 | `tests/integration/test_allocation_nominate.py` | 强（含 ≥2 人自荐） | R17 P1 画布（令 `5f0c7d6679d35639`） | F2 收紧 B2、B7b 新增（P1FIX） | B2 收紧反例（自荐 <2 人必拒） |
| C-HOSTPICK | 宿主选人 Q 系（Q1–Q9 + Q2b + R2） | named | 24 | `tests/integration/test_allocation_hostpick.py` | 强 | R17 P1-HOSTPICK（令 `cc2599dbf0a6ed77`） | R2 外部活路径断言由 DSH 补入（`R17_P1HOSTPICK_review_by_dsh.md`） | 外部活路径注入必红（R2） |
| C-DRAFT | 画布草稿 R1–R4 | named | 12 | `tests/integration/test_p2_plan_draft.py` | 强（含单框/环/未知字段/零副作用） | R17 P2 画布（令 `d24b14312defa30f`） | K1 纠偏（孤立框合法；`R17_P2_CANVAS_review_by_dsh.md`） | 零副作用探针（POST /api/plan/draft 后 runs/ 无新增，DSH 端点探针） |
| C-PRIME | C′ 对抗组 R5/R6/R7/K3 | named | 5 | `tests/integration/test_p2_adversarial.py` | **R5 = 模扇出调度等价（槽位稳定排序；窗口含 blackboard_write/agent_output/agent_card/node_enter(worker)）——非逐字节等价**；R6/R7/K3 维持原强度 | R17 P2 画布（令 `d24b14312defa30f`） | **P2 逐字节等价 → P2-FIX 模扇出交错等价（令 `43a062783c03b561`）→ P2-FIX2 模扇出调度等价（令 `0f37e591032154c9` 附录 F）——两次降级，本行即集中登记** | C3 响亮失败（第三 agent 注入窗口必红）+ C5 反例 4 条 + 判别器在克隆内先声明基线（P2FIX2 附录 F） |
| M-N2-REDACT | 脱敏机制（N2/N1-FIX1 规则精度） | named | 42 | `tests/unit/core/test_redact.py` + `tests/integration/test_n2_redaction.py` + `tests/unit/tools/test_redaction_check.py` | 强（含 sk- 左边界、幂等/确定性、DB 列扫描、写点接线） | N2 线（令 `17cac3312d525f20`） | N1-FIX1（令 `843ae87705f3435c`）：sk- 左边界修复 + rules n2-v1→n2-v2 | 摘除掩码必红（K13c 变异已证） |
| M-CORE-STATE | 核心状态机（CRDT/matcher/memory/WAL/paths） | named | 63 | `tests/unit/core/*`（除 redact/负例，10 模块，见 §2） | 强（回归级） | G 系–R16 各轮（红线冻结件 matcher/memory 的守卫层） | 无降级记录 | none（回归组；红线触碰审计由 `R17_program_logic_audit_by_dsh.md` §1 承担） |
| M-DISPATCH-GATE | 异构派发与门（P4 线） | named | 71 | `test_dispatch.py` + `test_hetero_dispatch.py` + `test_pre_run_gate.py` + `test_backend_factory.py` + `test_agent_registry.py` + `test_dispatch_hooks_e2e.py` | 强（开关语义反证 + 门负例 + 预算/并发/授权） | zcode P4 线（设计内令，`docs/r17_final_plan.md` §7-P4） | P4FIX2 pre_run_gate 重写；N2 授权/出境门收紧 | 门负例（B3 寻址/门开关反证，`test_dispatch.py::TestGateNegatives` 等） |
| M-SPI-BACKEND | SPI 与后端（dsh/wb） | named | 132 | `test_spi_dsh.py` + `test_spi_wb.py` + `test_resume.py` + `test_s0a_contracts.py` + `test_backend_registry.py` + `test_negotiation.py` + `test_probes.py` + `test_evidence.py` + `test_w0_contracts.py` + `test_dsh_command_resolution.py` + `test_routing_map.py` + `test_registry_bindings.py` + `tests/integration/test_dsh_*.py`（17 模块，见 §2） | 强（SPI 一致性套件 + C3/C5 fail-closed + 预算跳闸 + S0-a 契约 + W2 注册表 + W3 协商） | zcode P2（SPI 抽取）/P3/P5 线（设计内令） | 无降级记录 | C3/C5 fail-closed 负例；S0-a 契约；W2 代码级闸不可绕过；W3 无共同模式必须响亮拒绝 |
| M-EVOLVER | 演化器（K 三跑/阈值/版本） | named | 24 | `tests/unit/evolver/*`（3 模块） | 强（回归级） | G5/R14 线 | 无降级记录 | none（回归组） |
| M-INSPECTOR | 巡检/清理/隔离 | named | 57 | `tests/unit/inspector/*`（6 模块） | 强（零丢失 sha256/还原逐字节/基础设施目录不隔离） | G4/R15/FLAKY1 线 | FLAKY-1 轮转修复（令 `abcc2a5adb387508`，该轮判不通过后经 BCFIX 系收口） | 隔离碰撞响亮（`test_restore_collision_is_loud` 等） |
| M-RECOVERY | 存活与恢复（B 系，BC 线） | named | 35 | `test_recovery.py` + `test_reconcile.py` + `tests/integration/test_bcfix2.py` | 强（B2 状态机 + op-log 幂等 + 链校验） | BCFIX2/BCFIX3 线（令 `ef88f0e736fbb925`/`88b140bd0b5269df`） | BCFIX2 重写受控时间测试；BCFIX3 R1 复验 | B2 序列反例（`test_recovery.py::TestB2RealSequence`） |
| M-P0P5 | zcode P0b/P5 线判据（SSE/统一视图/权限卡/对账） | named | 102 | `tests/integration/test_p0b.py` + `tests/integration/test_p5_features.py` + `tests/unit/test_ui_assets.py` + `tests/unit/test_ui_local_only.py` + `tests/unit/test_ui_pure_js.py` + `tests/unit/test_ui_enums.py` + `tests/unit/test_ui_io.py` + `tests/unit/test_ui_backends.py` + `tests/unit/test_ui_machine.py` + `tests/unit/test_ui_perm_window.py` + `tests/unit/test_ui_backend_badge.py` + `tests/unit/test_ui_geometry.py` | 强 | zcode P0b/P5 线（设计内令） | P5 复核 CONDITIONAL NOT-PASS 后修（权限卡/过滤豁免） | 权限卡空转反例（`R17_P5_review.md` 阻塞项对应用例）；S1：模块外置/顺序/纯函数；权限选项必须来自服务端帧；属性必须转义；枚举与文档必须同源；B2 轮次不得伪造 prompt；W1b 禁用后端不可打开 |
| M-G-REG | G0–G7 回归 | named | 25 | `tests/integration/test_g*.py`（8 模块） | 回归级 | G0–G7 弧 | 无降级记录 | none（回归组） |
| M-R-REG | R13–R16 回归 | named | 35 | `tests/integration/test_r1[3-6]*.py` + `test_round5_c2.py`（7 模块 + W1B 端点模块） | 回归级 | R13–R16 弧 | 无降级记录 | none（回归组） |
| M-H1-ARCHIVE | 归档导出（记忆库联通 H1 线） | named | 12 | `tests/unit/test_archive_export.py` + `tests/integration/test_archive_export_runner.py` | 强（含 PII 两阶段：脱敏后 counts=={} 自检） | 记忆库联通 V2 方案（依据件 `30c9ee9cb22f3151`） | 无降级记录 | 导出失败响亮（错误 json + 失败事件用例） |
| T-FOOTPRINT | trace 足迹卫生判据（T2/T4/T5 + B-2 迟到 emit） | named | 5 | `tests/integration/test_trace_footprint.py` | 强（正常+异常双路径 closed 断言；N=200 句柄不增长；尾事件存活；迟到 emit 惰性） | R17 P3（令 `a7cd4654648d2bd5`） | 无降级记录 | 摘 close 单变异哑火（flush 兜底）、**双变异必红**（T3 残留 18 实证）；丢 unsubscribe 必红（B-2）；判据↔判别器映射见 `reports/R17_P3_footprint_report.md` |
| M-UI-BROWSER | 真浏览器交互（拖拽/缩放/置顶/权限卡） | named | 23 | `tests/integration/test_ui_browser_interaction.py` | 强（真浏览器 + 27 变异自证） | 2026-10-17 v2 方案 | 新建 | M1–M28（M14 已随旧 catch 分支退役）各自必红；拖拽边界/缩放对边/点击 POST/超时只标对应卡；矩阵见 `D:\dsh-work\2026-10-17\browser_interaction_plan.md` §6 |
| MEM1-GUARD | agent 失败模式护栏判据（G1–G5 + B-1/B-2 语义） | named | 8 | `tests/integration/test_mem1_guardrails.py` | 强（G1 两拓扑摘要恒等，supervisor 走 P2FIX2 canonical——实测 1/6 扇出写序竞态；G2 逐 id 覆盖 + 未配置必不注入 + 块形状全 agent 一致；G3 源码纯度 + yaml 变更即注入变化；G4 载荷 on/off 恒等；G5 投递件「已落地」两态语义（三根有界搜索 + 多处命中互证 + 缺失仍红 + 内容全验 redact 残留 0，MEM1FIX 更严口径） | R17 MEM1（令 `93fef01e3a1761a1`） | 无降级记录 | G1 摘要冻结自 bundle 克隆 `df6b9a3` 与实现树双侧对齐；判别器①写死内联⇒G3 红 ②空注入⇒G2 红 ③越界注入⇒G2 红；判据↔判别器映射见 `reports/R17_MEM1_report.md` |
| M-NATIVE-RUNTIME | 角色级原生执行（ACPRuntime / dsh ACP） | named | 31 | `tests/unit/test_native_runtime.py` + `tests/integration/test_native_runtime_offline.py` | 强（C1 证据+诱饵、C1b 复用、C3 回退/响亮失败、C5/C10 并发与故障注入、C11 权限归属、C7 close 回收子进程、O6 上下文轮换；真机 C2/C2b/C2c/C2d/C4 在报告） | W4-1（`D:\dsh-work\2026-10-17\W4_task_book.md` v4）+ P2c | 新建 | C1 填 `None`/读磁盘收据必红；C3 去掉 fallback 必红；C10b 去掉 `_dead` 卡到超时；C10c 不调 close 则无 close 帧；C11 静态绑 agent 则归属错；C7 不 close 则 `proc.poll()` 仍为 None；O6 去掉阈值则轮换测试必红 |
| M-PROFILE-IDENTITY | 身份模型：slot/agent/profile + 规则/预置/组合 | named | 21 | `tests/integration/test_profile_identity.py` + `test_profile_guard.py` + `test_profile_identity_dual.py` + `test_profile_baseline.py` | 强（只收 Confirmed、内容派生 id、能力路由、四契约拒绝扩权、钉版漂移、冲突检测、guard hard/soft、双跑注入、UI 接线、flag=1 基线） | P0/P1/P2（`identity-model-plan.md` v1） | 新建 | 去掉 Confirmed 过滤 ⇒ 已拒绝条目进 catalog 必红；改工具允许集 ⇒ 扩权必红；改规则正文 ⇒ `verify_pinned` 必红；去掉 guard 重试 ⇒ hard 测试必红 |
| I-NEGATIVE | 阴性用例（架构守卫） | infra | 5 | `tests/unit/_negatives/*` + `tests/unit/core/_negatives/*` | 阴性（守卫必须保持红） | G 系引入 | 无降级记录 | 自身即判别器（违规样例必红） |
| I-TOOLS | 门禁工具自测 | infra | 17 | `tests/unit/tools/test_audit_fingerprints.py` + `tests/unit/tools/test_liveness_check.py` | 强（含变异金丝雀 `TestMutationCanary`） | SEAL-TOOLING / BCFIX 线 | SEAL-TOOLING（令 `0bfecdf2f542668c`）加指纹审计 | 变异金丝雀（破坏派生使检查器致盲必红） |

**桶计数（由 `ledgercheck.py --emit-summary` 生成；模块表求和；禁止手写——F-1）**：named = 733 节点（97.1%）/ infra = 22 节点（2.9%）；合计 755。
（infra 22 < 60 阈值：粒度未设错；逐条理由见 §2 表"归属理由"列。）

## 2 · 模块归属表（模块级默认；按路径升序，append-only）

| 模块 | 组 | 桶 | 函数(登记) | 用例(登记) | 归属理由 |
|---|---|---|---|---|---|
| `tests/integration/test_allocation_hostpick.py` | C-HOSTPICK | named | 24 | 24 | 画布弧判据（Q 系） |
| `tests/integration/test_allocation_nominate.py` | C-NOMINATE | named | 7 | 7 | 画布弧判据（B 系） |
| `tests/integration/test_archive_export_runner.py` | M-H1-ARCHIVE | named | 2 | 2 | H1 归档线 runner 级判据 |
| `tests/integration/test_bcfix2.py` | M-RECOVERY | named | 12 | 12 | BC 线 B2 状态机/统一视图判据 |
| `tests/integration/test_dispatch_hooks_e2e.py` | M-DISPATCH-GATE | named | 3 | 3 | 门 e2e（创建 run 门前/门后） |
| `tests/integration/test_dsh_acp_client.py` | M-SPI-BACKEND | named | 12 | 12 | dsh 后端 ACP 客户端 |
| `tests/integration/test_dsh_budget.py` | M-SPI-BACKEND | named | 9 | 9 | dsh 预算 |
| `tests/integration/test_dsh_events_map.py` | M-SPI-BACKEND | named | 8 | 8 | dsh 事件映射 |
| `tests/integration/test_dsh_permissions.py` | M-SPI-BACKEND | named | 7 | 7 | dsh 权限应答 |
| `tests/integration/test_dsh_result_contract.py` | M-SPI-BACKEND | named | 3 | 3 | dsh 结果契约 |
| `tests/integration/test_dsh_runner.py` | M-SPI-BACKEND | named | 6 | 6 | dsh runner |
| `tests/integration/test_dsh_selfcheck.py` | M-SPI-BACKEND | named | 4 | 4 | dsh 自检 |
| `tests/integration/test_dsh_session.py` | M-SPI-BACKEND | named | 8 | 8 | dsh 会话 |
| `tests/integration/test_g0_min_crew.py` | M-G-REG | named | 1 | 1 | G0 回归 |
| `tests/integration/test_g2_single_agent_offline.py` | M-G-REG | named | 3 | 3 | G2 回归 |
| `tests/integration/test_g3_arbitration.py` | M-G-REG | named | 4 | 4 | G3 回归 |
| `tests/integration/test_g3_topologies_fake.py` | M-G-REG | named | 2 | 5 | G3 回归（参数化 2 节点 5 用例） |
| `tests/integration/test_g4_inspector.py` | M-G-REG | named | 4 | 4 | G4 回归 |
| `tests/integration/test_g5_evolver.py` | M-G-REG | named | 6 | 6 | G5 回归 |
| `tests/integration/test_g6_api.py` | M-G-REG | named | 3 | 3 | G6 回归 |
| `tests/integration/test_g7_evolve_api.py` | M-G-REG | named | 2 | 2 | G7 回归 |
| `tests/integration/test_logic_canvas.py` | C-CANVAS | named | 7 | 7 | 画布弧判据（C 系） |
| `tests/integration/test_n2_redaction.py` | M-N2-REDACT | named | 3 | 3 | 脱敏集成判据 |
| `tests/integration/test_native_runtime_offline.py` | M-NATIVE-RUNTIME | named | 15 | 15 | W4-1 离线验收（证据/复用/回退/并发/故障注入/权限归属/close 回收） |
| `tests/integration/test_p0b.py` | M-P0P5 | named | 9 | 9 | P0b 线判据（F4/N1/N2 应答） |
| `tests/integration/test_p2_adversarial.py` | C-PRIME | named | 5 | 5 | C′ 对抗判据（R5 现强度=模扇出调度等价，见 §1 变更史） |
| `tests/integration/test_p2_plan_draft.py` | C-DRAFT | named | 12 | 12 | 画布弧判据（R1–R4） |
| `tests/integration/test_p5_features.py` | M-P0P5 | named | 15 | 15 | P5 线判据（SSE/统一视图/权限卡） |
| `tests/integration/test_profile_guard.py` | M-PROFILE-IDENTITY | named | 5 | 5 | P1：hard 重试/失败、soft 只告警、工具缺口、brief 未投递 |
| `tests/integration/test_profile_identity.py` | M-PROFILE-IDENTITY | named | 11 | 11 | P0/P2：解析/预置/组合/钉版/冲突/API 目录/UI 接线 |
| `tests/integration/test_profile_identity_dual.py` | M-PROFILE-IDENTITY | named | 4 | 4 | P1：flag on/off、brief 注入、profile_bound、形状等价 |
| `tests/integration/test_profile_baseline.py` | M-PROFILE-IDENTITY | named | 1 | 1 | P2：flag=1 固定 fixture 基线（single 有序 / supervisor 多重集） |
| `tests/integration/test_r13_crewai_fallback.py` | M-R-REG | named | 1 | 1 | R13 回归 |
| `tests/integration/test_r14_evolve_auto_flow.py` | M-R-REG | named | 8 | 8 | R14 回归 |
| `tests/integration/test_r15_bus_resilience.py` | M-R-REG | named | 1 | 1 | R15 回归 |
| `tests/integration/test_r15_doctor_api.py` | M-R-REG | named | 2 | 2 | R15 回归 |
| `tests/integration/test_r15_replay_api.py` | M-R-REG | named | 6 | 6 | R15 回归 |
| `tests/integration/test_r16_f_fixes.py` | M-R-REG | named | 8 | 8 | R16 回归 |
| `tests/integration/test_round5_c2.py` | M-R-REG | named | 1 | 1 | round5 回归 |
| `tests/unit/_negatives/test_network_violation.py` | I-NEGATIVE | infra | 1 | 1 | 阴性：网络违规样例必须保持红（架构守卫自证） |
| `tests/unit/core/_negatives/test_import_violations.py` | I-NEGATIVE | infra | 4 | 4 | 阴性：分层导入违规样例必须保持红 |
| `tests/unit/core/test_blackboard_reducer.py` | M-CORE-STATE | named | 5 | 5 | 黑板 reducer |
| `tests/unit/core/test_crdt.py` | M-CORE-STATE | named | 15 | 15 | 向量时钟/HLC/MV 寄存器 |
| `tests/unit/core/test_events_writer.py` | M-CORE-STATE | named | 3 | 3 | 事件写点（含脱敏接线面） |
| `tests/unit/core/test_late_joiner.py` | M-CORE-STATE | named | 4 | 4 | 迟加入 |
| `tests/unit/core/test_matcher.py` | M-CORE-STATE | named | 10 | 10 | 匹配打分/门（红线冻结件守卫） |
| `tests/unit/core/test_memory_idempotent.py` | M-CORE-STATE | named | 3 | 5 | 记忆幂等（参数化 3 节点 5 用例） |
| `tests/unit/core/test_memory_isolation.py` | M-CORE-STATE | named | 6 | 6 | 记忆隔离（红线冻结件守卫） |
| `tests/unit/test_native_runtime.py` | M-NATIVE-RUNTIME | named | 16 | 17 | W4-1 单元（开关/队列/锁存/回退策略/适配器未接线；O6 轮换） |
| `tests/unit/core/test_paths.py` | M-CORE-STATE | named | 7 | 7 | 路径族 |
| `tests/unit/core/test_redact.py` | M-N2-REDACT | named | 26 | 26 | 脱敏规则（sk- 左边界/幂等/写点接线） |
| `tests/unit/core/test_state_reducers.py` | M-CORE-STATE | named | 6 | 6 | 状态 reducer |
| `tests/unit/core/test_storage_wal.py` | M-CORE-STATE | named | 4 | 4 | WAL |
| `tests/unit/evolver/test_backfill.py` | M-EVOLVER | named | 8 | 8 | 回填 |
| `tests/unit/evolver/test_evolve_decide.py` | M-EVOLVER | named | 5 | 5 | 演化决策（K 三跑/阈值） |
| `tests/unit/evolver/test_versions.py` | M-EVOLVER | named | 11 | 11 | 版本账 |
| `tests/unit/inspector/test_crewai_redirect.py` | M-INSPECTOR | named | 3 | 3 | crewai 重定向 |
| `tests/unit/inspector/test_crewai_trace.py` | M-INSPECTOR | named | 25 | 25 | crewai 轨迹 |
| `tests/unit/inspector/test_doctor_storage.py` | M-INSPECTOR | named | 10 | 10 | doctor 存储 |
| `tests/unit/inspector/test_memory_invariant.py` | M-INSPECTOR | named | 2 | 2 | W4-1 D：bridge 审计 op 豁免的窄口径（agent 时钟缺口仍红） |
| `tests/unit/inspector/test_run_cleanup.py` | M-INSPECTOR | named | 8 | 8 | run 清理 |
| `tests/unit/inspector/test_run_quarantine.py` | M-INSPECTOR | named | 9 | 9 | run 隔离（零丢失/还原逐字节） |
| `tests/unit/test_agent_registry.py` | M-DISPATCH-GATE | named | 20 | 27 | 注册表/工厂（参数化 20 节点 27 用例——A-4 计数对账：27 为用例数） |
| `tests/unit/test_archive_export.py` | M-H1-ARCHIVE | named | 10 | 10 | H1 归档导出判据（PII 两阶段） |
| `tests/unit/test_backend_factory.py` | M-DISPATCH-GATE | named | 6 | 6 | 后端工厂开关 |
| `tests/unit/test_dispatch.py` | M-DISPATCH-GATE | named | 19 | 19 | 派发门/寻址/续跑 |
| `tests/unit/test_hetero_dispatch.py` | M-DISPATCH-GATE | named | 6 | 6 | 异构分发覆盖 |
| `tests/unit/test_pre_run_gate.py` | M-DISPATCH-GATE | named | 14 | 14 | pre_run_gate |
| `tests/unit/test_reconcile.py` | M-RECOVERY | named | 14 | 14 | 对账链/孤儿清扫 |
| `tests/unit/test_recovery.py` | M-RECOVERY | named | 9 | 9 | B1 存活/B2 恢复序列 |
| `tests/unit/test_resume.py` | M-SPI-BACKEND | named | 5 | 5 | dsh 续跑/wb 拒绝语义 |
| `tests/unit/test_ui_assets.py` | M-P0P5 | named | 21 | 21 | S1 资产拆分守卫（util/api/state/window/card/dock/io/backends/machine 外置、顺序、纯函数/纯数据） |
| `tests/unit/test_ui_pure_js.py` | M-P0P5 | named | 5 | 5 | S1 纯渲染器实执行（node）：选项来自服务端、失败关闭、属性转义、8 手柄、版本行 |
| `tests/unit/test_ui_enums.py` | M-P0P5 | named | 3 | 3 | S1 枚举与文档同源（值必须被 ui-enums.md 收录） |
| `tests/unit/test_ui_io.py` | M-P0P5 | named | 4 | 4 | W1a B2 轮次块（node 实执行）：prompt 未携带、原生会话、转义、缺响应标注 |
| `tests/unit/test_ui_backends.py` | M-P0P5 | named | 5 | 5 | W1b 后端选择器（node 实执行）：四态映射、禁用不可开、属性转义 |
| `tests/unit/test_ui_machine.py` | M-P0P5 | named | 8 | 8 | W1a 七态推导（node 实执行）+ 与 enums 同源 |
| `tests/unit/test_ui_perm_window.py` | M-P0P5 | named | 4 | 4 | W1a-2 权限卡进窗口（队列标签/仅服务端选项/旧面板已移除） |
| `tests/unit/test_ui_p0_shell.py` | M-P0P5 | named | 8 | 8 | P0 收尾守卫（运行历史不编版本号、rearrange 几何、窗口槽单调、画布顶随工具栏、默认轨迹页、状态栏/权限接线） |
| `tests/unit/test_ui_backend_badge.py` | M-P0P5 | named | 7 | 7 | W1a-3 窗口徽标（真会话才原生；禁用/不可达/需授权/API）|
| `tests/unit/test_ui_live_wiring.py` | M-P0P5 | named | 3 | 3 | F1/F2/F9 实时接线（hetero 事件已注册、失败结果不得显示原生、超时只标记对应卡） |
| `tests/unit/test_ui_geometry.py` | M-P0P5 | named | 8 | 8 | W1a-4 拖拽/缩放几何（node 实执行；最小尺寸/边界收敛） |
| `tests/integration/test_backends_endpoint.py` | M-R-REG | named | 8 | 8 | W1B 端点 + W0-2 投影 + F4 只读端点例外 + 503 响亮失败 |
| `tests/unit/test_ui_local_only.py` | M-P0P5 | named | 2 | 2 | M10 本地置顶/归档守卫（不写服务端） |
| `tests/unit/test_s0a_contracts.py` | M-SPI-BACKEND | named | 4 | 4 | S0-a 契约（SpiError.code；executor/platform_model/worker model 语义） |
| `tests/unit/test_backend_registry.py` | M-SPI-BACKEND | named | 6 | 6 | W2 注册表化（数据驱动构造；代码级闸不可绕过） |
| `tests/unit/test_dispatch_negotiation.py` | M-DISPATCH-GATE | named | 3 | 3 | W3-2 派发前协商（支持即记录、不支持即跳过、不构建） |
| `tests/unit/test_negotiation.py` | M-SPI-BACKEND | named | 8 | 8 | W3 模式协商（顺序优先/无共同模式拒/协议 v2 拒/modes lint） |
| `tests/unit/test_probes.py` | M-SPI-BACKEND | named | 6 | 6 | W3-3 探针契约（未知必须 fail-closed；egress 需 opt-in；resume 三步） |
| `tests/unit/test_evidence.py` | M-SPI-BACKEND | named | 7 | 7 | W3-4 证据变更控制（只升不降；提案永不自动批准；无批准不可应用） |
| `tests/unit/test_w0_contracts.py` | M-SPI-BACKEND | named | 4 | 4 | W0-2/4/6（协议版本投影；授权闸；超时 expired 事件） |
| `tests/unit/test_dsh_command_resolution.py` | M-SPI-BACKEND | named | 6 | 6 | 真机发现：空 bin fail-fast；版本锁 env 覆盖；resume 必须带 cwd |
| `tests/unit/test_routing_map.py` | M-SPI-BACKEND | named | 4 | 4 | G12 routing.map（真实 agent_id -> 后端；未知后端 lint 拒绝） |
| `tests/unit/test_registry_bindings.py` | M-SPI-BACKEND | named | 6 | 6 | G12 去重 + 门（占位 binding 已移除；映射后端禁用时门必须拒） |
| `tests/unit/test_spi_dsh.py` | M-SPI-BACKEND | named | 9 | 9 | dsh SPI 一致性 + C5 fail-closed |
| `tests/unit/test_spi_wb.py` | M-SPI-BACKEND | named | 10 | 10 | wb SPI 一致性 + C3 fail-closed |
| `tests/integration/test_trace_footprint.py` | T-FOOTPRINT | named | 5 | 5 | trace 足迹卫生判据（T2/T4/T5 + B-2；P3 令 `a7cd4654648d2bd5`；2026-10-12 自 tests/unit 迁入——tests/unit 禁 import graphs 的既存层规则，见变更史） |
| `tests/integration/test_ui_browser_interaction.py` | M-UI-BROWSER | named | 23 | 31 | 真浏览器交互（拖拽/缩放/置顶/权限卡点击链路）；playwright-core + 系统 Edge/Chrome，`ctx.route` 免服务 |
| `tests/integration/test_mem1_guardrails.py` | MEM1-GUARD | named | 8 | 8 | agent 失败模式护栏判据（G1–G5；MEM1 令 `93fef01e3a1761a1`；supervisor 并发夹具走 P2FIX2 canonical，debate 串行裸字节） |
| `tests/unit/tools/test_audit_fingerprints.py` | I-TOOLS | infra | 7 | 7 | 工具自测：指纹审计（SEAL-TOOLING 交付） |
| `tests/unit/tools/test_liveness_check.py` | I-TOOLS | infra | 10 | 10 | 工具自测：存活检查（含变异金丝雀，守卫判别力自证） |
| `tests/unit/tools/test_redaction_check.py` | M-N2-REDACT | named | 13 | 13 | 脱敏校验工具判别力（含 DB 列扫描；归 N2 组因其校验对象是脱敏不变量本体） |

## 3 · 函数级例外（当前为空）

> 声明：本令当时（2026-10-11）现测 **69 个模块均整模块归属单一判据组**，无跨组模块，故函数级例外为空（0 条）。
> 2026-10-17 现测 **74 个模块**（S0/S1 新增 5 个测试模块），仍全部整模块归属，例外表仍为空。
> ledgercheck 的幽灵校验（显式键必须真实存在）在空集上平凡通过；一旦未来出现跨组模块，须在此表登记：
> 格式：`| `tests/.../test_x.py::节点名` | 组 ID | 理由 |`

| 模块::节点 | 组 | 理由 |
|---|---|---|
| （空——无跨组模块） | — | — |

## 4 · 变更史（本账自身）

| 日期 | 变更 | 依据 |
|---|---|---|
| 2026-10-11 | 初版建立：18 组 / 69 模块 / 533 节点 / 545 用例（现测 HEAD `f9dc4a5`） | 工单 `cd52cd325f8fb4bf` A1 + 附录 A-1；DSH 审计 §12B |
| 2026-10-12 | F-1/B-4 落地：① M-SPI-BACKEND 组级 **82→81** 更正（定位 = 组级多算 1，模块表 8+9+8+7+3+6+4+8+5+9+10=81 为准）；② 新增 **T-FOOTPRINT** 组（`tests/unit/test_trace_footprint.py`，5 节点）；③ §1 手写合计行**废除**，改由 `ledgercheck.py --emit-summary` 生成回填（named 516 / infra 22 / 合计 538）；④ 新增 **A9 三方对账**（组级表求和 == 模块表求和 == collect 唯一节点数，任一不等判红）+ **F-4 格式漂移 selftest** | 令 `a7cd4654648d2bd5` F-1/F-4 + 附录 B-4 |
| 2026-10-12 | **T-FOOTPRINT 测试位迁移**：`tests/unit/test_trace_footprint.py` → `tests/integration/test_trace_footprint.py`（`git mv`，节点数 5 不变）。原因：该文件顶层 `from graphs import runner` 违反既存层规则"tests/unit 禁 import graphs"（layercheck core scope 不拷贝 `graphs/`，临时目录收集即 ImportError → J2(a)/(b) exit=1 → 门禁红）。非判据削弱：5 判据原样随文件迁移，判定逻辑零改动；模块表与组表路径同步更新 | 令 `3086eb4d86ad5a96` T7 no-regression + 既存层规则（`test_archive_export.py` docstring 先例）；门禁实证 `pytest_core.20261012T141848Z.log` |
| 2026-10-12 | **新增 MEM1-GUARD 组**（`tests/integration/test_mem1_guardrails.py`，8 节点）：agent 失败模式护栏判据 G1–G5（默认 off 两拓扑摘要恒等 / on 态按 agent 作用域注入 / 数据驱动禁内联 / 只进 prompt 不进评分 / 投递件合规 + redact 残留 0）。§1 合计 538→546（emit-summary 再生成）。G1 口径注记：supervisor 夹具实测存在 R5 族扇出写序竞态（6 跑 1 次少数派），改走 P2FIX2 canonical（判据文件内声明）；debate 串行稳定（6/6）保持裸字节。摘要冻结自 bundle 克隆 `df6b9a3` 与实现树双侧对齐 | MEM1 令 `93fef01e3a1761a1`（段二 G1–G5 + 附录 B-1/B-2） |
| 2026-10-13 | **G5 口径变更（强度更严，MEM1FIX）**：投递件断言由「单点存在（inbox）」改为「**已落地两态语义**」——三根有界搜索（`inbox` / `landed/**`（日期倒序显式排序）/ 库内 `30-tasks`；任一根不存在 ⇒ 硬失败并列出缺失根；`MAX_DEPTH=2` 封顶，禁无界通配）+ ≥1 命中（全无 ⇒ 红，真实缺失仍被抓）+ 多处命中 sha256 **互证**（不一致 ⇒ 红并列出全部路径+各自哈希，防回填污染）+ 内容全验跑首件（四节齐 + `redact()` 残留 0，`assert_content` 助手）。**强度判定：单点存在 ⇒ 两态存在 + 内容全验 + 缺失仍红 = 更严，无降级**。背景：原断言把瞬态运输区当永存归档，投递件 2026-10-13 依法落地（`landed/20261013/` + 库内 `30-tasks/`，均 `46b051fd670167d4`）后必然爆红（ACCT2 门禁第 1 档 fail-fast，根因实证非执行方过失） | MEM1FIX 令 `4dac215981d2cdfa`（Q1–Q3 + 附录 A-1/A-2） |
| 2026-10-18 | **新增 M-NATIVE-RUNTIME 组**（`tests/unit/test_native_runtime.py` 13 节点/14 用例 + `tests/integration/test_native_runtime_offline.py` 14 节点/14 用例）：W4-1 角色级原生执行。合计 **730 节点 / 751 用例 / 97 模块**，named 708 / infra 22；A9 三方对账 OK | W4-1（`W4_task_book.md` v4；判据 C1–C11） |
| 2026-10-18 | **D：`memory_invariant` 豁免 bridge 审计 op**（`inspector/checks/memory_invariant.py` + `tests/unit/inspector/test_memory_invariant.py`，2 节点）：`DshSessionManager._record` 写的 `dsh_session::<run_id>` 映射 op（`agent_id=dsh_bridge`、`clock={}`）不是 agent 发布，逐 op 检查会把它误判为「时钟缺口」⇒ 每次 native/hetero run 健康 83.3。豁免**只认** `agent_id=="dsh_bridge"` 且 `key` 前缀 `dsh_session::`；agent 时钟缺口、非映射 key、跨会话泄漏仍红（单测钉住）。合计 **730 节点 / 751 用例 / 97 模块**，named 708 / infra 22 | W4-1 补强 D（既有缺陷，W4 暴露；未碰 `dsh_bridge` 红线件） |
| 2026-10-18 | **C：C7 自动化**（`tests/integration/test_native_runtime_offline.py` +1 节点）：`close()` 后断言 ACP 直接子进程 `proc.poll() is not None`（≤5s），并以「close 前必须活着」作阴性对照；只覆盖直接子进程，孙进程需 Job Object（R5）。合计 **731 节点 / 752 用例 / 97 模块**，named 709 / infra 22 | W4-1 补强 C（把手工进程核对变成可复跑判据） |
| 2026-10-18 | **P1 身份模型**（`graphs/profile_guard.py`、`graphs/runner.py`、`graphs/builder.py`、`tests/integration/test_profile_guard.py` +5、`test_profile_identity_dual.py` +4）：`PROFILE_IDENTITY=0/1`；flag=1 时 preset 作 prompt 源 + brief 前缀 + `profile_bound` + `agent_output.profile_selection_id`；guard hard 重试 1 次→`ProfileViolation`、soft 只 `profile_check`；默认仍 0。合计 **750 节点 / 771 用例 / 100 模块**，named 728 / infra 22 | `identity-model-plan.md` v1 P1（默认未切换） |
| 2026-10-18 | **P2/P2c 身份模型**（`api/static/ui/profiles.js`、`api/routes.py`、`graphs/runner.py`、`graphs/identity.py`、`agent_bridge/runtime.py`、`tests/integration/test_profile_baseline.py`）：默认 `PROFILE_IDENTITY=1`（仅带 `profiles` 时绑定）；`/api/modes` 增 `rules`；UI 画像抽屉；flag=1 基线；O4 分层 brief；O5 brief 上限；O6 按 `usage_update` 阈值轮换会话。合计 **755 节点 / 776 用例 / 101 模块**，named 733 / infra 22 | `identity-model-plan.md` v1 P2 |
