# R17 最终方案 · 多 agent 统一接入（定稿）

- 性质：**决策与执行总纲**（一页拍板 + 阶段门 + 风险总账 + 不变量 + 进化路线）
- 权威分工：**实现规格的权威仍是 `docs/r17_multi_agent_adapter_design.md`（v3）**；本件是它的**合并摘要 + 决策面**，不替代、不修改 v3
- 起草：dsh 侧（合并 v3 设计 + R17 复核 D 组/附录 A/B + 外部证据）；执行：`zcode`；复核：dsh 侧（独立实证，不采信自述）
- 前置基线：R16 **11/11 判据通过**（`reports/R16_review.md` §9.4）；R15 8/8 通过；全量回归 246 passed / 5 skipped
- 本件状态：**待用户拍板**（两件事：批准 P0 开工 / 是否授权 WB 真机探针）

---

## 1 · 一页总纲

| 项 | 定稿 |
|---|---|
| **目标** | 把"再接入一个 agent"从"写一套桥"降为"**填一条配置**"；dsh 与 WorkBuddy 作为**同一种 ACP 叶子**被编排 |
| **形态** | **F1 保守增强**：保持 Supervisor + Blackboard 骨架，新增三个横切层——**契约层**（SPI + registry）、**不变量层**（零静默）、**验证层**（独立来源/分歧检测） |
| **三条总纲** | **零未知**（每条风险上表有主有检测）· **零无界**（每条有硬上限）· **零静默**（异常要么拒在 spawn 前、要么响亮报错、要么被对账抓到） |
| **不承诺** | **"零风险"字面不可达**；本方案承诺的是"**有界 + 可检出 + 不静默**"，并把 6 项不可消除项**明列在册** |
| **落地** | 六阶段 P0–P5，每阶段**开关 + 回退一步 + 三态判据**；实现条目 W1–W24，一项一 commit |
| **默认值** | WB `enabled: false` + `requires_authorization: true`（付费 + 登录）；平台永不设放宽旗标；离线默认只描述平台自身 |
| **红线** | dsh 行为**零变化**（逐字平移 + shim + 复跑探针/变异）；不改 dsh/WB 本体；不接未文档化接口 |

---

## 2 · 架构定稿

| 件 | 内容 |
|---|---|
| **SPI** | 任务级 **1**（`execute_task` 必需）+ 会话级 **6**（`open_session`/`prompt`/`events`/`answer_permission`/`cancel`/`close`）= **7**；`initialize`/`shutdown` 可选 no-op |
| **归一化事件** | `{type, payload, raw, ts}`；`type` = 五类 + `unknown`（未知 `sessionUpdate` **原样存档 + 计数，不猜**） |
| **能力标志（17 项，全量必填，各带 evidence）** | `protocol_version` / `transport` / `permission_has_path` / `permission_options` / `usage_shape` / `cancel_is_notification` / `server_times_out` / `supports_resume` / `supports_background` / `paid` / `single_prompt_per_session` / `single_root_cwd` / `single_handler_slot` / `audit_hash_chain` / `sandbox_semantics` / `supports_elicitation` / **`egress`** |
| **注册表** | `agents/registry.yaml` = 唯一接入面（command / cwd 模板 / 档位白名单 / deadline / 预算 / 能力标志 / 健康检查 / 开关 / `platform_limits`）；**未知键 = lint 错误**（不是忽略）；前缀路由 `dsh:` / `wb:`；`core/matcher.py` **零改动**（靠 `bindings` 映射） |
| **派发前门（五连 + 扩展）** | `enabled` → `requires_authorization && consent` → 平台级 `platform_limits`（日/时总量、并发上限）→ `version_lock`（握手 v1，拒 v2）→ cwd 容器冒烟 → argv 禁旗标断言 |
| **双模式** | 会话模式（长驻、多轮、可中途人工介入）；一次性模式（`execute_task` 全包） |
| **回合收尾** | **每回合单一仲裁者**（终态锁存 first-writer-wins）；四类取消路径只投递意图 |

---

## 3 · 跨后端契约（权限 / 预算 / 审计 / 数据）

| 面 | 统一契约 | 后端差异处理 |
|---|---|---|
| **权限** | 档位 **closed set**；argv **禁放宽旗标**；应答**只回当帧出现过的 `optionId`**；默认拒绝 + 人工卡 + 运行档；45s deadline ⇒ 超时=拒绝+取消 | dsh `permission_has_path=false`（实测）⇒ **禁止路径分支**；WB 同按 false 处理（未证） |
| **预算** | 每 run `BudgetSpec` + **平台级** `platform_limits`（日/时总量 + 每后端并发上限，op-log 聚合计数）；超限⇒**拒派不进 spawn** | `usage_shape=context_used_size`（dsh 实测：真机只有 `{used,size}`）⇒ **只做上下文压力闸**；`paid` 后端必须 `requires_authorization`；**金额上限无数据源**（如实写） |
| **审计** | 全局唯一 `op_id` + **先写后派发** + raw 全量随行；**双源对账**（平台 op-log ↔ WB 哈希链 / dsh 会话日志）+ 链校验 + `audit_gap` 告警 | dsh 无哈希链（`audit_hash_chain=false`）；"绝对不丢"**不承诺**，只承诺"**静默丢 = 可检出**" |
| **数据** | 每后端 `egress` 标志；派发门与 op `dispatch` 行**必带** `egress`；出网需 opt-in + 可外发路径白名单 + 落盘前**确定性脱敏** | "**默认离线**"（平台自测/FakeLLM）与"**出网派发**"（真任务）**不得用同一句话表述**；一经送出**不可撤销** |

---

## 4 · 风险总账（39 条 → 三类处置）

| 组 | 条数 | 真解决（实现条目） | 纳入设计 | 登记/文档化 |
|---|---|---|---|---|
| **R1–R13**（后端/供应链/环境） | 13 | R1/R2/R3/R5/R9/R12 等（见 v3 §6.1） | R4/R6/R7/R8/R10/R11 | R13 孤儿（W7 巡检 + 单独行） |
| **N1–N13**（转递面/证据层/治理） | 13 | **N1**（W14 数据/指令分离信封）· **N2**（W15 确定性脱敏）· **N3**（W16 数据去向 + opt-in） | N8/N9/N10/N12/N13 | N4/N5/N6/N7/N11（含理由 + 回头时机 + 当下检测） |
| **T1–T9**（平台/跨系统/人） | 9 | **T1**（W17 出网语义）· **T2**（W19 op-log 写路径）· **T3**（W18 单一仲裁者） | **T4**（W20 重启收养/清理）· **T5**（W21 到达序口径） | T6（W22 对账 SOP）· T7（W23 运维手册）· T8（判据落位，实现 W24 预留）· T9（W23 契约可校验） |

**6 项"不可消除"（必须知情，不得从册中删除）**
1. 数据一经外发**不可撤回**（N3）；
2. WB **服务端计费**拿不到事件流 ⇒ 只能保证上限、不保证账单准确（T6/F-3）；
3. **ACL 本机前置**换机失效 ⇒ 靠哨兵把"静默坏"变"显式失败"（F-8）；
4. **上游版本漂移**（ACP v2 draft、dsh rc、WB 自更新）⇒ 握手 + pin + 快照哨兵；
5. **人的行为**（授权疲劳）⇒ 可读摘要 + 减少打扰 + 频次/批准率告警（N6）；
6. **分布式"绝对不丢"**不可证 ⇒ 只承诺"可检出"（F2 教训）。

---

## 5 · 不变量与变异矩阵（"零静默"的可证明形态）

> 判据形态：每条不变量 = **1 条正常用例（必须绿）+ 1 条变异用例（必须红）**；红/绿都要贴原始输出。

| # | 不变量 | 变异用例（故意破坏 ⇒ 必须被抓） | 落位 |
|---|---|---|---|
| I1 | 永不未授权出网 | 标 `egress=false` 却真出网 | W17 |
| I2 | 永无无上限花费 | 超平台级日/时总量或并发上限 | W13 |
| I3 | 永不"配置即越权" | registry 写 `danger-full-access` / `-y` / `bypassPermissions` | 既有 T1/T2 |
| I4 | 永不发明 `optionId` | 假服务端给 `allow-always`，平台仍照回 | 既有 T3 |
| I5 | 永不静默版本漂移 | 假服务端回 `protocolVersion:2` | 既有 D1 语义 |
| I6 | 永不"配了不生效" | `usage_shape=context_used_size` 却配 `budget_tokens` | 既有 T5 |
| I7 | 回合终态唯一且只写一次 | 四路径两两/全并发 | **W18** |
| I8 | 审计缺口必被发现 | 抽掉一条 op 行 | 既有双源对账 |
| I9 | 永不丢账（宁可拒派） | 队列打满后仍继续派发 | **W19** |
| I10 | "未跑"永不冒充通过 | 把某后端标"未跑"却显示绿色 | 四类退出码 + 必跑最小集 |

---

## 6 · 落地阶段与门

| 阶段 | 内容 | 开关 | 回退一步 | 关键判据 |
|---|---|---|---|---|
| **P0** | `agent_bridge/` 建包；`AcpClient` **逐字平移**；`dsh_bridge/acp_client.py` 变 `sys.modules` 别名 shim（**连私有名导出**） | 无（纯平移） | `git revert` | **复跑复核方 2 个探针文件 + 变异测试全绿**；dsh 行为零变化 |
| **P1** | `agents/registry.yaml` + loader/lint + doctor 健康行 | `AGENT_REGISTRY=0/1`（默认 0） | 关开关回旧路径 | lint 全用例绿；未知键/保留字拒载 |
| **P2** | dsh 走 SPI（行为不变） | 同上 | `git revert` W5 | R16 D1 九条探针 + 变异测试仍全绿 |
| **P3** | **WB 离线档**（SPEC-DERIVED fixture，不接真机） | `WB_BACKEND=0/1`（默认 0） | 关开关 | 假服务端全链绿；`wb-check` 离线（CLI 存在性 + `--help` 快照） |
| **P4** | matcher 异构派发 + 五连门 + 平台级 limits + **压测 ≥8 并发** | `DISPATCH_HETERO=0/1`（默认 0） | 关开关 | 零丢行 + p95 < 派发门预算 + 拒绝路径用例绿 |
| **P5** | UI 接线（emit→SSE + 派发台 + egress 常驻显示） | `DSH_ACP_ENABLED`（**W9 新建**） | 关开关 | 事件可见性 + 权限卡往返 + 无中间态 |

阶段间**硬序**：P0→P5；任一阶段"不通过"即停，修复重验后才进下一阶段；**"未跑"必须留原因（授权/环境），不得写成通过**。

---

## 7 · 外部证据锚（说明"我们不是特例"）

| 外部结论 | 出处 | 对应我们的条目 |
|---|---|---|
| **MAST**：1600+ trace、7 框架，14 种失败模式聚成 **3 类 = 系统设计问题 / agent 间失配 / 任务验证不足** | [arXiv 2503.13657](https://arxiv.org/abs/2503.13657)（NeurIPS 2025） | ① ↔ T2/T3/R6；② ↔ **N1**/T5/N8；③ ↔ **T8**/F5/F7 |
| **OWASP Agentic Top 10**：把"提示注入/过度授权"列为一等 agentic 风险 | [OWASP GenAI 发布](https://genai.owasp.org/2025/12/09/owasp-genai-security-project-releases-top-10-risks-and-mitigations-for-agentic-ai-security/)（[解读](https://entro.security/blog/the-owasp-agentic-top-10-2026-what-it-means-for-ai-agents-and-non-human-identities/)） | ↔ **N1**/R1/R3 |
| 模式目录：**Debate 成本成倍**；**Voting 若同模型/同提示/同数据则错误高度相关**；**Swarm 难 debug、易循环、成本不可预测、治理更难——企业别起步就 Swarm** | [协作模式目录](https://github.com/kaduoxzero/AI-Agent/blob/main/06-multi-agent/02-collaboration-patterns.md) | ↔ **T8 需 ≥2 独立来源**；印证 Supervisor + Blackboard 选型；**F3 不做** |
| 生态做法：可观测性/轨迹标准（OTel GenAI 约定、轨迹评测）+ 预算限额强制 | [traceeval-cli](https://pypi.org/project/traceeval-cli/) 等 | ↔ T2/T6/双源对账；进化项 **E5** |

**只有三条是我们独有的**（文献兜不住）：Windows/ACL 环境、WB 第三方付费云服务、以及我们这套偏严的证据纪律。

---

## 8 · 进化路线

| 形式 | 内容 | 建议 |
|---|---|---|
| **F1 保守增强** | Supervisor + Blackboard + 契约层 + 不变量层 + 验证层 | **现在做**（本方案） |
| **F2 结构化升级** | 显式状态机 + 事件溯源（run = 事件流，黑板 = 投影，终态由仲裁者落） | **下一个大版本**：一次性收口 T3/T5/T2 |
| **F3 网状自治（Swarm）** | agent 直连、无中心 | **不做**（文献明确警告；我们连 T2/T3 才刚收口） |

**进化项**：E1 不变量+变异矩阵（本方案已含）· E2 独立验证者/分歧检测 · E3 失败模式回流（trace → MAST 式分类 → evolver）· E4 组合拓扑（Supervisor 下挂子图 + Reviewer）· E5 trace 对齐 OTel GenAI 语义约定 · E6 一实例一 `DSH_HOME`（会话级档位）· E7 成本半自动对账。

---

## 9 · 明确不做 + 未决项

**不做（16 条非目标，摘要）**：不接 WB `--serve`/HTTP/streamable-http、不接 `--bg`、不接 WB 插件与内置 MCP、不接未文档化 `/api`、不做 ACP Registry 发现（**刻意收窄攻击面**）、不嵌原生界面（v1）、不默认放宽任何档位（含 `-y`/`bypassPermissions`/`danger-full-access`）、不为测试调真模型、不接 ACP v2、不做 Swarm、不做跨后端因果断言…

**未决 / 需授权**：
1. **WB 真机探针**（`--acp` 握手 / 权限往返 / 用量形状 / 审计链读取）——需用户**单独授权**（付费 + 需登录）；授权前 WB 一律 `assumed-fail-closed` + `enabled: false`，**不得对外表述为"已可用"**；
2. **W24（T8 ≥2 独立来源）**：判据落位已定，**实现待用户确认**；
3. **core 层改动**（若 W19 的包装方案经复核认定不够）——需用户批准升级到 `core/`；
4. **保留期策略**（N7：raw 证据长期、机器产物归档）——取值待用户拍板。

---

## 10 · 责任与纪律

| 项 | 定 |
|---|---|
| 实施 | `zcode`，一项一 commit，消息带条目 id（`R17-W*`） |
| 复核 | **dsh 侧独立实证**：复跑既有 9 条探针 + 变异测试 + 逐条变异矩阵；**证据只增不改**；"未跑"标明原因与到期 |
| 禁止 | 执行方自证；把"未跑"写成"通过"；删改已核事实；削弱已认可强项（私有名 shim、`usage_shape` lint、T1/T2/T3、双源对账、快照哨兵、非目标清单） |
| 环境 | 派发根 ACL 前置**不在仓库**（本机一次性）；部署前置清单入 `docs/`；`*-check` 四类退出码 0/1/2/3 |

---

## 11 · 决策记录

| 日期 | 决定 | 内容与约束 |
|---|---|---|
| 2026-10-03 | **A 项批准：P0 开工** | 用户批准开始 P0（SPI 抽取 / `AcpClient` 逐字平移 / `dsh_bridge/acp_client.py` 变 shim），**附加硬约束：必须可回滚** |
| 2026-10-03 | B 项（WB 真机探针）**暂缓** | 等 P3 离线档跑通后再一次性授权（避免付费调用花在未定型配置上）；授权前 WB 维持 `assumed-fail-closed` + `enabled: false` |

**"可回滚"的落地要求（P0 起生效）**
1. **三层回滚**：① 前置基线 tag（`r17-p0-baseline`）② 逐条 `git revert`（W 条目级）③ 原子回退（`git reset --hard <tag>`，仅在无并行工作时）。
2. **回滚必须被演练**（不是宣称）：P0 完成后**真做一次** revert → 复跑判据 → 记录 → 重新应用 → 再复跑；两次结果都要贴原始输出。
3. **回滚判据**：回到基线后 ① `git status` 干净 ② 被改文件 sha256 == 基线值 ③ 全量回归 246 passed / 5 skipped ④ 复核方 2 个探针文件可跑（D1 九条 9/9、变异测试仍能抓住错误形态）⑤ `audit_invariants --check frozen/clean` PASS。
4. **P0 的爆炸半径**：仅"平移 + 别名"两件；**不得**夹带 P1+ 内容、不得改任何默认档位/开关/WB 相关代码。

---

## 12 · 外部方案借鉴清单（2026-10-04 追加 · dsh 侧）

> 评估对象：[tmux-bridge-mcp](https://github.com/howardpen9/tmux-bridge-mcp)（独立 MCP 服务端，让 tmux 各 pane 里的 CLI agent 互读互写）。**结论：借鉴其 5 项做法，不采纳其传输与安全模型。**
> 本机实测：`tmux` 不在 PATH；`wsl.exe` 存在但**无发行版** ⇒ 该方案在本机当前不可运行（且我们两个后端都是 Windows 原生进程）。

### 12.1 采纳（落在哪）

| # | 借鉴项 | 落点 | 为什么值 |
|---|---|---|---|
| B1 | **read-act-read + read guard**（写之前必须先读，拿到新鲜证据才许写） | **P4 派发门新增硬前置**：`fresh preflight`（TTL 内成功的 `*-check`/握手读）未通过 ⇒ **拒派**；该"读"落 op-log | 把"没有新鲜证据就不许动"从**习惯**变成**门**；成本一条检查 + 一个用例 |
| B2 | **"哪些闸是顺序辅助、哪些是强制"要写明**（他们的 read guard 自己声明"不是安全边界"） | 设计 §6 增一行**闸性质表**：每个门标注 `enforcement` / `sequencing-aid` | 直击我们反复踩的"**假安全**"类缺陷（F7/F10/D2 同源） |
| B3 | **label → resolve 寻址** | **路由铁律**：`agents/registry.yaml` 的 `bindings` 是**唯一**寻址真相；**任何 agent 自报的身份/名称不得用于路由或审计** | 他们的 pane 标签"未经认证、谁都能改名"正是反面教材 |
| B4 | **一条命令自检**（`tmux_doctor`） | 已有：dsh `dsh-check` ✅ / P3 的 `wb-check` 离线档 | 仅确认模式统一，无需新工 |
| B5 | **"何时不该用"对照表** | 最终方案 §9 增一小节「**何时不该走平台**」：一次性小任务 → 原生 CLI；需要长期累积上下文的角色 → 会话持久/嵌原生界面 | 他们的对比表把"取舍"讲清了；我们缺一节同类话术 |
| B6 | **"跨轮持久上下文"是真需求** | 演进项 **E8**：为"需要累积上下文"的角色（如持续跟踪同一 PR 的 reviewer）保留**可续接会话**（dsh `supports_resume` 实测可用；WB `--session-id/-r` 已有但未证） | 他们唯一说服人的论据，值得记成需求而不是忽略 |

### 12.2 不采纳（记明理由）

| # | 不采纳项 | 理由 |
|---|---|---|
| N-a | **用 tmux/pane 抓屏做平台的机器通道** | 会一次丢掉 ACP 的结构化帧、权限闸、预算闸、双源审计；且需先架 WSL。社区先例同向（nax 从 PTY 抓屏迁到 ACP，原话"eliminates PTY scraping"） |
| N-b | **其安全模型**（任意 pane 可读可写、无 per-pane 访问控制、标签未认证、无加密认证、"**不要用于 multi-tenant**"） | 正是我们 **N1（跨 agent 提示注入）/R1/R3** 要防的形态；**不采纳，但作为 N1 的外部证据锚引用**（真实实现，说明"信任本地 agent"假设会得到一条零闸门通道） |
| N-c | **mesh/Swarm 作为平台编排** | 已在 §8 定为不做（F3）；平台维持 Supervisor + Blackboard |
| N-d | **`kimi-tmux` 式 `--print` + 工具块解析** | 属 PTY/文本抓取一类，已被 N-a 的理由覆盖 |
| N-e | **把 MCP 当"启动/驱动 agent"的通道** | 不变：MCP 在我们是**工具面**（平台当 MCP server 给 agent 挂工具），agent 驱动一律走 ACP |

### 12.3 参考

- tmux-bridge-mcp：[GitHub](https://github.com/howardpen9/tmux-bridge-mcp) · [README（安全模型节）](https://raw.githubusercontent.com/howardpen9/tmux-bridge-mcp/main/README.md)
- 相邻方案（同为 pane/终端路线，不进本轮评估）：[smux](https://github.com/ShawnPana/smux) · [agent-bridge](https://github.com/raysonmeng/agent-bridge) · [libtmux-mcp](https://pypi.org/project/libtmux-mcp/)

---

## 13 · 何时不该走平台（B5 · 2026-10-04 · dsh 侧）

> 与 §9「明确不做」互补：§9 说**平台不做什么**，本节省说**这活不该交给平台**。目的是别把便宜的活套进重的壳。

| 场景 | 建议 | 为什么 |
|---|---|---|
| 一次性小任务（≤1 分钟、无审计需求） | **直接用原生 CLI**（`dsh --profile headless` / `codebuddy -p`） | 平台的价值在编排 / 审计 / 限额 / 护栏；套壳只增加延迟与失败面 |
| 需要**长期累积上下文**的角色（持续跟同一 PR 的 reviewer） | **会话持久**（演进项 E8）+ 必要时嵌原生界面 | 平台若不能续接会话，人自然退回"开个终端手动喂" ⇒ 那一刻平台的审计与权限**全部失效**（比省这点工贵得多） |
| 需要**人工频繁干预**的探索性工作 | **嵌原生界面**（dsh web 的 token URL / WB Web UI） | 平台的派发台面向"有界任务"，不面向交互式探索 |
| 高度不确定、指望"多 agent 自动收敛"的任务 | 明说平台**不承诺**自动成功（见 T8 聚合错误） | 多个 agent **同向出错**时聚合结果会自洽；投票不产生独立判断（外部证据见 §7） |
| 跨机器 / 多租户 / 对外暴露 | **不做**（见 §12.2 N-a/N-b） | 现有沙箱与信任模型是为**单机单用户**设计的 |
| 需要精确计费核算 | 只把它当**上限**保证，不当账单（见 T6/F-3） | dsh 真机无 token 字段、WB 计费在服务端 ⇒ 平台拿不到计费事件流 |

---

## 14 · 决策记录（续）：**WorkBuddy 暂停**（2026-10-04 · 用户决定）

**决定**：**WB 先不弄**。处理方式 = **冻结在原地 + 登记**（**不删代码**），P4 起 WB 相关条目一律降级。

| 项 | 处置 |
|---|---|
| `WbBackend` / `wb_check.py` / WB registry 条目 | **保留但冻结**：`enabled: false` + 全部能力 `assumed-fail-closed`；**不再推进、不再新增 WB 逻辑**（删掉反而要再走一轮复核，且它默认惰性、零运行成本） |
| **WB 真机探针授权**（`--acp` 握手/权限/用量/审计链） | **问题撤回**（不再作为待办、不再询问授权） |
| P4-FIX 的 WB 腿 | 记 **待解除暂停**：① B6 `resume` 的 WB 侧（原为"明确拒绝"）② F4 的 "paid 无 consent" 端到端负例 ③ `--strict-mcp-config` 真机评估 |
| P3 的 `reconcile.py`（双源对账） | **保留**——它不绑 WB，dsh 会话日志/平台 op-log 同样适用 |
| 风险登记 | WB 专属条目（**T6 服务端计费**、**D2/W-7 插件与内置 MCP 面**、**N3 的 WB 数据去向**）标 **"因暂停而不活跃"**，**不删除**（解除暂停时原样生效） |

**由此带来的实际变化（诚实版）**

- **活跃面变窄**：6 项"不可消除"里的 **3 项**（WB 服务端计费 / WB 插件供应链 / WB 数据去向）**从活跃面移除**；剩下的仍是 dsh 侧与平台侧的（dsh 无路径证据的权限、`{used,size}` 无计费口径、ACL 本机前置、版本漂移、人的行为、分布式不丢）。
- **不变的**：T1–T9（平台/跨系统/人）与 N1/N2（跨 agent 注入、raw 脱敏）**与后端无关**，照旧生效；P4 的全部阻塞项（**门未接线**、C3 混合拓扑 run、flaky 诊断）**与 WB 无关**，照旧要修。
- **对 P4 的影响**：F1 接线、F3 `resume`（**只做 dsh**，`supports_resume=true` 已实测）、F4 负例（改用 dsh + registry 字段构造）、F5 flaky 诊断 —— **全部可在 dsh 单后端下完成**；F2 的"混合拓扑离线 run"改为**既有 agent + dsh** 两种叶子。
- **解除暂停的条件**（写清，避免将来又靠记忆）：① 用户明确解锁；② 先做**真机帧录制**（`--help` 快照已有）+ `version_lock` 填值 + 一次授权真机往返；③ WB 的 `assumed-fail-closed` 逐条升级需**真机证据**（不得凭静态推断改 true）。

---

## 15 · 决策记录（续二）：**批准 P4 扩围到 graph 层**（2026-10-04 · 用户决定）

**背景**：P4-FIX 复核（`reports/R17_P4FIX_review.md`）判"仍不通过"——**F1 接线空转**（`pre_run_gate` 在门开时只 `DispatchGate()` 构造后 `_ = gate`，一个检查都不跑）。执行方把设计 §7-P4 的验收判据①（**混合拓扑离线 run** = per-subtask 异构派发）**显式登记**为"需扩围到 `graphs/runner.py` 的 agent→backend 路由，超出红线，需所有者批准"。

**决定**：**批准扩围**。

| 项 | 内容 |
|---|---|
| 允许改动 | `graphs/runner.py` 的 **agent→backend 路由**（经 registry `bindings`），以及为它服务的 `agent_bridge/**` 与 `tests/**` |
| 仍禁止 | `core/matcher.py`、`api/routes.py`（除既有接线点）、`crews/`、`configs/`、`prompts/`、`dsh_bridge/` 语义 |
| 连带要求 | ① `tests/tools/layercheck.py` 的修剪环境拷贝清单需随新下层/新依赖同步（现为手工维护，**自动化**仍列演进项）；② **回滚演练范围**须覆盖新增改动（`graphs/runner.py` 的 blob 判等）；③ C3 混合拓扑 run 以**既有 agent + dsh** 两叶子申报 |
| 不因此放宽的 | F1 仍必须**真跑检查**（不是构造即通过）；tripwire 必须用**必定失败的条件**证明"门开必拒、门关必过" |
| 若后续再扩围 | 一律回到本表更新，不得由执行方自行决定 |

---

## 16 · 决策记录（续三）：**执行改由 dsh 侧承担**（2026-10-04 · 用户决定）

**背景**：P4 连卡三轮（门没接线 → 接线但空转 → 真跑检查但无人能满足它、且自家 e2e 稳定失败）。用户决定：**由 dsh 侧直接实现**剩余部分。

| 项 | 内容 |
|---|---|
| **分离声明（重要）** | 本轮起 **dsh 侧同时承担执行与复核**，**执行/复核分离被打破**。替代保障：① **客观验收**（我原有的探针/变异矩阵/四口径/回滚演练，全部可复跑）；② 用户在需要时可抽检或指定第三方复核；③ 所有判据以**原始输出**留痕，不采信叙述 |
| 实现范围 | `agent_bridge/{dispatch,dispatch_hooks}.py` 重写/补齐、新增 `{gate_counters,health,hetero_dispatch}.py`、`graphs/runner.py` 真调 `execute_task`、`api/routes.py` 接线点（锁令牌 + 释放）、测试 |
| 仍然禁止 | `core/matcher.py`、`crews/`、`configs/`、`prompts/`、`dsh_bridge/` 语义；WB 维持暂停 |
| 验收方式 | 四编码口径全量 + `frozen`/`clean`/`layercheck` + 我自造的"必定失败条件"tripwire（门开必拒/门关必过）+ **离线混合 run 证据**（假 ACP 服务端，无模型无网络）+ 独立回滚演练 |
| 复核债务 | 若日后有第三方复核，**优先复核本节的实现**（我自己写的代码我最可能看漏） |
