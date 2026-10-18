# R17 令↔报告↔复核索引（order report index）· ACCT1 交付件 3/3

> **建立依据**：工单 `workorder_ACCT1_three_ledgers.md`（sha256_16 `cd52cd325f8fb4bf`）A3/A5 + 附录 A-3；源头 = `reports/R17_program_logic_audit_by_dsh.md` §5（S4 撞名）/§6（D1 复核遮蔽）/§12A。
> **机检**：`tests/tools/ledgercheck.py`：索引内每个 `reports/` 路径必须真实存在；每个库外令件以其 sha256_16 与实件一致。
> **锚定声明**：**库外件以 hash 为准**——令文件在仓库外（`D:\dsh-work\…`），以绝对路径 + sha256_16 锚定；库内报告/复核件以路径存在性为准（内容由 git 跟踪 + 逐轮复核保证，不重复记 hash 以免正常的报告增补破坏账目）。
> **范围声明**：本册登记**本仓（multi_agent_collab）**的令↔报告↔复核三元组。库侧线（`workorder_A_*`、`workorder_N1_library_*` 等记忆库仓工单）不入本册；**非 `.md` 文件不入账**（探针脚本、bundle 等以各报告内的原始输出为证）。
> **纪律**：**append-only + 稳定排序**（按线 → 行号；历史行只增不改）。`reviewed` 取值**五值**（B-5 三态 + B-8 遗留降级 + ACCT2 第四态）：`yes`（有复核件，**必须带对象证据**——该复核件引用的提交号，写入"复核件"或"结论"格；**配对不得只靠文件名**）/ `partial`（ACCT2：**有被审对象、有复核件，但链或锚点不可证**——结论格须含**恰好一次** `不可证理由：<非空文本>`（出现 0 次或 ≥2 次 ⇒ 硬失败），复核件格须含复核件路径，行内须含**至少一个锚点引用**（被事故作废的锚点照样可引用））/ `no`（未见复核件，须给处置 + 确认方式一句话）/ `unknown`（记录件等无复核语义的行；**不得出现** `不可证理由：` 前缀，防状态互串——硬失败）/ `n/a-legacy`（B-8 遗留降级：复核件确实未引用任何提交号的 yes 行，降级并**在结论格注明降级原因**，不许留在 yes 冒充）。**棘轮（B-8/F-5）**：`ledgercheck.py` 冻结"yes 且无证据"基线集合（26 行，2026-10-12 已逐行回填对象证据）；**新增行再现该情形 ⇒ 硬失败 exit≠0**，并打印棘轮余量。
> **五值判别树（ACCT2 / 令 `09a74ff3b264f916` A-2）**：先看**对象证据可不可核**——对象可核且提交链可核 ⇒ `yes`；对象可核而提交链/锚点不可核 ⇒ `partial`；有被审对象但未见复核件 ⇒ `no`；无令、无被审对象的记录件 ⇒ `unknown`；B-8 历史降级 ⇒ `n/a-legacy`。**唯一落位**：五值各自有且只有一个落位条件，出现两可即改判并留痕。
> **配对规则（F-2/B-5）**：报告↔复核按**同名前缀配对 + §七显式别名表**，至少兼容三种形态——`R17_P0_review.md`（无作者后缀）/ `*_review_by_dsh.md` / `*_review_by_zcode.md`；**首行对象行必须文本吻合**（同名前缀命中但对象行不符 ⇒ 不构成配对，`reviewed: no` 并留张力注记）。
> **独立性与不采信自述**：**同线自审不算独立复核**——复核者与被复核者同源时 `independent: no`；本轮全部成对复核均为 dsh↔WB 或 dsh↔zcode 交叉，`independent: yes`。

## 一 · WB 画布线（R17_CANVAS，执行方 WorkBuddy，令在 `D:\dsh-work\2026-10-10\`）

| 行号 | 令文件 | sha256_16 | 报告 | 复核件 | 结论 | reviewed | reviewed_by | independent |
|---|---|---|---|---|---|---|---|---|
| A1 | `D:\dsh-work\2026-10-10\平台新模式_P1执行任务书_终版.md` | `5f0c7d6679d35639` | `reports/2026-10-10_P1_delivery_report.md` | `reports/R17_P1_review_by_dsh.md`（**对象证据**（F-5 回填）：复核基线 HEAD `917ea11`，期初事故链 `09ae152`→`337a246`→`20d6223` 见件内） | 修改后通过（B7 夹具补齐 + B2 收紧为条件；F1 由 DSH 修复后达标） | yes | dsh | yes |
| A2 | `D:\dsh-work\2026-10-10\workorder_P1FIX_F2F5_A10.md` | `97fc350b3b797ffe` | `reports/R17_P1FIX_report.md` | `reports/R17_P1FIX_review_by_dsh.md`（**对象证据**（F-5 回填）：报告提交 `edaac75`，复核期末态 `e3f5835`，提交链 `726ea00`→`c9ddec9`） | 通过（阻断 0；判别力经 DSH 独立重做确认） | yes | dsh | yes |
| A3 | `D:\dsh-work\2026-10-10\workorder_P1HOSTPICK_host_pick_and_pairing.md` | `cc2599dbf0a6ed77` | `reports/R17_P1HOSTPICK_report.md` | `reports/R17_P1HOSTPICK_review_by_dsh.md`（**对象证据**（F-5 回填）：复核基线 HEAD `3db495a`，提交链 `edaac75`→`5b796b3`→`312b2f6`→`9d1cb8d`→`3db495a`） | 通过（附录 B 判据；R2 由 DSH 补入） | yes | dsh | yes |
| A4 | `D:\dsh-work\2026-10-10\workorder_P2_logiccanvas_stage2_and_Cprime.md` | `d24b14312defa30f` | `reports/R17_P2_CANVAS_report.md` | `reports/R17_P2_CANVAS_review_by_dsh.md`（**对象证据**（F-5 回填）：被审 HEAD `1de6832`，提交链 `40c4dbb`→`ec9edfd`→`53d652f`→`0a9f7e4`/`1de6832`） | 复核不通过（判据隔离与门禁证据；实现逐项通过）→ 经 P2-FIX/P2-FIX2 收口 | yes | dsh | yes |
| A5 | `D:\dsh-work\2026-10-10\workorder_P2FIX_r5_order_dependence.md` | `43a062783c03b561` | `reports/R17_P2FIX_report.md` | `reports/R17_P2FIX_review_by_dsh.md`（**对象证据**（F-5 回填）：被审 HEAD `f7e788e`，提交链 `bca73a6`→`4875d96`→`e96e479`） | 不通过（抖动未消除：时间栅栏非确定性） | yes | dsh | yes |
| A6 | `D:\dsh-work\2026-10-10\workorder_P2FIX2_r5_interleaving.md` | `0f37e591032154c9` | `reports/R17_P2FIX2_report.md` | `reports/R17_P2FIX2_review_by_dsh.md`（**对象证据**（F-5 回填）：被审 HEAD `e6d56c7`，提交链 `b1a7731`→`1e8fe14`→`e0268e5`→`bf0dc62`→`e6d56c7`） | 通过（R5 模扇出调度等价成立） | yes | dsh | yes |
| A7 | `D:\dsh-work\2026-10-10\workorder_P3_trace_footprint_hygiene.md` | `3086eb4d86ad5a96` | `reports/R17_P3_footprint_report.md` | — | 报告已交付（提交 `97a29b4`）；门禁收口 + DSH 独立复核待回填（令升版链 `7521dac46f456819`→`f5e4642b4d5023ba`→`a7cd4654648d2bd5`→`3086eb4d86ad5a96`） | no | — | — |
| A8 | `D:\dsh-work\2026-10-10\workorder_ACCT1_three_ledgers.md` | `cd52cd325f8fb4bf` | `reports/R17_ACCT1_report.md` | — | 本令交付（待 DSH 复核） | no | — | — |

## 二 · WB 其他执行线

| 行号 | 令文件 | sha256_16 | 报告 | 复核件 | 结论 | reviewed | reviewed_by | independent |
|---|---|---|---|---|---|---|---|---|
| B1 | `D:\dsh-work\2026-09-25\workorder_N1_FIX1.md` | `843ae87705f3435c` | `reports/R17_N1FIX1_report.md` | `reports/R17_N1FIX1_review_by_dsh.md`（**对象证据**（F-5 回填）：对象提交 `9cc8e14`（代码）+`d308096`/`2f8580f`（报告，HEAD）；事故勘误：哈希已不可解析，由恢复链 `ef00d85`/`ad96614` 承载） | PASS（P1–P5 + K7 + 两线回归；另附 1 条改进项） | yes | dsh | yes |
| B2 | `D:\dsh-work\2026-09-25\workorder_R17_SEAL_TOOLING.md` | `0bfecdf2f542668c` | `reports/R17_SEAL_TOOLING_report.md` | `reports/R17_SEAL_TOOLING_review_by_dsh.md`（**对象证据**（F-5 回填）：`d60a2e5`+`255f802`+`53198b1`（报告）；事故勘误：哈希已不可解析，由恢复链承载） | PASS（J1–J4 + 任务②） | yes | dsh | yes |
| B3 | `D:\dsh-work\2026-09-25\workorder_CONV_PANEL.md` | `e69166387331ad0c` | `reports/R17_CONV_PANEL_report.md` | — | 交付；未见复核件（审计 D1 点名。确认方式：已按同名前缀 + 全库 `reports/` grep 核过，无 `R17_CONV_PANEL_review*`） | no | — | — |
| B4 | `D:\dsh-work\2026-10-10\记忆库联通自改进闭环方案_V2_定稿.md`（**依据件，非工单令**） | `30c9ee9cb22f3151` | `reports/R17_ARCHIVE_EXPORT_report.md` | — | 交付；未见复核件（审计 D1 点名。确认方式：已按同名前缀 + 全库 `reports/` grep 核过，无对应 `*_review*` 件） | no | — | — |

## 三 · zcode 适配器线（P0–P5；早期令在设计文档内，无独立工单文件）

| 行号 | 令文件 | sha256_16 | 报告 | 复核件 | 结论 | reviewed | reviewed_by | independent |
|---|---|---|---|---|---|---|---|---|
| C1 | —（设计内令：`docs/r17_final_plan.md` §6） | — | `reports/R17_P0_report.md` | `reports/R17_SPI_P0_review_by_dsh.md`（**对象证据**：复核提交 `f594258`；对象行 = SPI 抽取 P0；**15 锚点因 2026-10-10 事故失效（171 之一）**，如 `e6629a1`；内容层 3/4 哈希与 `test_dsh_acp_client` 11+1 经 DSH 复核通过。历史张力注记（B-5）：同名前缀命中的 `R17_P0_review.md` 对象行对应 `R17_P0_p5_fixes_report.md`，不构成本报告配对——本件为 DSH 补独立复核，闭合 ACCT1 D1 缺口） | 部分证实：内容层可证且通过（3/4 哈希逐字节一致、别名 shim 语义成立、11 passed + 1 skipped）。不可证理由：交付链（W1/W2/W3 顺序与作者）、两轮回滚演练、报告↔提交对应所需锚点属 2026-10-10 事故作废的 171 锚点之一（如 `e6629a1`），对象已失，无法独立核验（非执行方过失） | partial | dsh | yes |
| C2 | `D:\dsh-work\2026-09-25\workorder_P0_p5_fixes.md` | `c9f7b35477ba95e4` | `reports/R17_P0_p5_fixes_report.md` | `reports/R17_P0_review.md`（**对象证据**：对象行原文"P0（P5 阻塞项修复 F1–F6）"文本吻合；提交 `1384921` + `be98190`） | 部分通过（F1/F2/F3/F5/F6 过；F4 新阻塞） | yes | dsh | yes |
| C3 | `D:\dsh-work\2026-09-25\workorder_P0b_fixes.md` | `6530869233362600` | `reports/R17_P0b_report.md` | `reports/R17_P0b_review.md`（**对象证据**（F-5 回填）：交付提交 `6c76203`/`3965624`/`d14b1c9`，终点 HEAD `d14b1c9`） | PASS（附登记一条既存 flaky） | yes | dsh | yes |
| C4 | —（设计内令：`docs/r17_multi_agent_adapter_design.md` v3 §3/§6） | — | `reports/R17_P1_report.md` | `reports/R17_P1_review.md`（**对象证据**（F-5 回填）：起点 `0402e9d` → 终点 `d7945a9`，6 交付件） | 不通过（P1-F1 编码脆弱必修） | yes | dsh | yes |
| C5 | —（修复令：`R17_P1_review.md` 阻塞项） | — | `reports/R17_P1F1_report.md` | `reports/R17_P1F1_review.md`（**对象证据**（F-5 回填）：终点 `c68e1d9`，交付件 `318f1c3`/`5ac1f5a`） | PASS（P1 阶段整体放行） | yes | dsh | yes |
| C6 | —（设计内令：设计 v3 §2/§7-P2） | — | `reports/R17_P2_report.md` | `reports/R17_P2_review.md`（**对象证据**（F-5 回填）：起点 `a7a7506` → 终点 `256efbd`） | PASS（C1–C7 + 回滚独立复现） | yes | dsh | yes |
| C7 | —（设计内令：设计 v3 §2.4/§7-P3） | — | `reports/R17_P3_report.md` | `reports/R17_P3_review.md`（**对象证据**（F-5 回填）：交付提交 `842d557`/`e8b6bb7`，终点 `0efc2bd`） | PASS（1 条落点缺项必修，不阻塞） | yes | dsh | yes |
| C8 | —（设计内令：设计 v3 §3.3/§7-P4） | — | `reports/R17_P4_report.md` | `reports/R17_P4_review.md`（**对象证据**（F-5 回填）：交付提交 `cc1ef3d`/`896084f`，终点 `9c9d8ec`） | 不通过（3 阻塞 + 1 澄清） | yes | dsh | yes |
| C9 | —（修复令：`R17_P4_review.md`） | — | `reports/R17_P4FIX_report.md` | `reports/R17_P4FIX_review.md`（**对象证据**（F-5 回填）：交付提交 `6c48a52`/`1199e76`/`dea50e6`，终点 `56ecf33`） | 不通过（F1 空转） | yes | dsh | yes |
| C10 | —（修复令：`R17_P4FIX_review.md`） | — | `reports/R17_P4FIX2_report.md` | `reports/R17_P4FIX2_review.md`（**对象证据**（F-5 回填）：交付提交 `e32f95e`/`363ad9b`，终点 `aeba789`） | 不通过（4 阻塞）→ 转 dsh 执行（§16） | yes | dsh | yes |
| C11 | —（设计内令：设计 v3 §7-P5） | — | `reports/R17_P5_report.md` | `reports/R17_P5_review.md`（**对象证据**（F-5 回填）：提交链 `9a9b5d0`/`5698262`/`ce4914f`，终点 HEAD `ce4914f`） | 条件不通过（基础设施真且绿；2 阻塞 + 2 视图 + 1 判据未交付） | yes | dsh | yes |

## 四 · zcode BC/FLAKY/N 线 与 dsh 实现线

| 行号 | 令文件 | sha256_16 | 报告 | 复核件 | 结论 | reviewed | reviewed_by | independent |
|---|---|---|---|---|---|---|---|---|
| D1 | `D:\dsh-work\2026-09-25\workorder_BC_p5_liveness_trust_v2.md` | `cfe3154e123eb17e` | `reports/R17_P5_BC_report.md` | `reports/R17_BC_FLAKY1_review.md`（**对象证据**（F-5 回填）：提交 `f98dea3`/`951709a`/`70e343f`，终点 HEAD `70e343f`） | 不通过（B1/B2/B3/C3/C4 未交付或不可用） | yes | dsh | yes |
| D2 | `D:\dsh-work\2026-09-25\workorder_FLAKY1_health_rotation.md` | `abcc2a5adb387508` | `reports/R17_FLAKY1_report.md` | `reports/R17_BC_FLAKY1_review.md`（同轮双令；**对象证据**（F-5 回填）：同 D1——`f98dea3`/`951709a`/`70e343f`，HEAD `70e343f`） | 不通过（以删除 N5 守卫换绿，须回退） | yes | dsh | yes |
| D3 | `D:\dsh-work\2026-09-25\workorder_BC_FLAKY1_fixes.md` | `0c29271f74478902` | `reports/R17_BCFIX_report.md` | `reports/R17_BCFIX_review.md`（**对象证据**（F-5 回填）：交付提交 `18c1050`/`39583a6`/`2aa85cd`/`cc21eca`/`fc6cd57`，终点 HEAD `fc6cd57`） | 不通过（且为本轮回归） | yes | dsh | yes |
| D4 | `D:\dsh-work\2026-09-25\workorder_BCFIX2_fixes.md` | `ef88f0e736fbb925` | `reports/R17_BCFIX2_report.md` | `reports/R17_BCFIX2_review_by_dsh.md`（**对象证据**（F-5 回填）：对象 `8075afd`→`26f8322`（HEAD）；事故勘误：哈希已不可解析，由恢复链承载） | NOT PASS（仅判据 11 一条不过；恢复绿已达成，不建议整链回滚） | yes | dsh | yes |
| D5 | `D:\dsh-work\2026-09-25\workorder_BCFIX3_next.md` | `88b140bd0b5269df` | `reports/R17_BCFIX3_report.md` | `reports/R17_BCFIX3_review_by_dsh.md`（**对象证据**（F-5 回填）：增量提交 `c191e66`+`3264291`（HEAD）；事故勘误：哈希已不可解析，由恢复链承载） | PASS（9 条判据全过） | yes | dsh | yes |
| D6 | `D:\dsh-work\2026-09-25\workorder_N2_redaction.md` | `17cac3312d525f20` | `reports/R17_N2_report.md` | `reports/R17_N2_review_by_dsh.md` + `reports/R17_N2FIX1_review_by_dsh.md`（F1 增补核验）（**对象证据**（F-5 回填）：`6fdc4b2`→`9dada1a`→`d3caf99`（HEAD），增补核验对象 `2196e07`；事故勘误：哈希已不可解析，由恢复链承载） | PASS（10/10 判据；F1 增补经 N2-FIX1 核验收口） | yes | dsh | yes |
| D7 | —（无令；对象为提交 `0413f79`/`968958b`，N2-FIX1 复核所提两条小项） | — | —（无独立报告） | `reports/R17_N1a_review_by_dsh.md`（**对象证据**（F-5 回填）：对象提交 `0413f79`+`968958b`；事故勘误：哈希已不可解析，由恢复链承载） | PASS（两条小项正确落地） | yes | dsh | yes |
| D8 | —（dsh 执行决策：`docs/r17_final_plan.md` §15 扩围批准） | — | `reports/R17_P4FIX3_report.md` | `reports/R17_P4_review_by_zcode.md`（**对象证据**（F-5 回填）：被审实现提交 `8a8b161`） | zcode 复核 dsh 实现（S1/S3/S4/S5 成立） | yes | zcode | yes |
| D9 | —（dsh 执行决策：`docs/r17_final_plan.md` §16） | — | `reports/R17_P4FIX4_report.md` | `reports/R17_P4FIX4_review_by_zcode.md`（**对象证据**（F-5 回填）：被审修复提交 `bca200a`） | zcode 复核 dsh 修复：PASS（F1 分歧另见 reconcile） | yes | zcode | yes |
| D10 | —（分歧处理记录，非令） | — | `reports/R17_P4FIX4_reconcile.md` | — | dsh 对 zcode F1 的对照复现记录 | no | — | — |

## 五 · 记录件（非令↔报告三元组；reviewed = unknown）

| 行号 | 令文件 | sha256_16 | 报告 | 复核件 | 结论 | reviewed | reviewed_by | independent |
|---|---|---|---|---|---|---|---|---|
| E1 | — | — | `reports/R17_review.md` | —（本身即设计轮复核） | 设计可开工（P0 放行） | unknown | — | — |
| E2 | — | — | `reports/R17_p0_baseline.md` | — | 回滚锚点登记（tag `r17-p0-baseline`） | unknown | — | — |
| E3 | — | — | `reports/R17_closeout_handover.md` | — | R17 平台线收尾总报告（移交版） | unknown | — | — |
| E4 | — | — | `reports/R17_ROI1_closure.md` | — | ROI-1 收口记录（所有者授权） | unknown | — | — |
| E5 | — | — | `reports/R17_program_logic_audit_by_dsh.md` | — | 整任务审计（本三本账的建立依据） | unknown | — | — |
| E6 | — | — | `reports/INCIDENT_2026-10-10.md` | — | `.git` 对象库损毁与恢复事故报告 | unknown | — | — |

## 六 · 未复核项清单与处置建议（D1）

| 行 | 件 | 处置建议 |
|---|---|---|
| B3 | `reports/R17_CONV_PANEL_report.md` | 提请 DSH 补独立复核；若裁定只读回放面板属低风险 UI 件，可由用户批准降级为"W 自验 + 抽查"并在此行补记 |
| B4 | `reports/R17_ARCHIVE_EXPORT_report.md` | 提请 DSH 补独立复核（PII 两阶段判据可机检，复核成本应低）；其引用提交哈希因事故作废，复核时以 `INCIDENT_2026-10-10.md` 映射表为准 |
| C1 | `reports/R17_P0_report.md`（zcode SPI 抽取 P0） | **已闭合（ACCT2）**：DSH 出补独立复核件 `reports/R17_SPI_P0_review_by_dsh.md`（提交 `f594258`）判定**部分证实** ⇒ 本行改 `partial`（不可证理由=交付链与两轮回滚演练的锚点属作废 171 之一）。历史裁定（B-5）保留：同名前缀的 `R17_P0_review.md` 不构成本报告配对 |
| A7 | `workorder_P3_trace_footprint_hygiene.md` | 非缺口——报告已交付（`reports/R17_P3_footprint_report.md`，提交 `97a29b4`），门禁收口后待 DSH 复核，交付后自然成对 |
| A8 | `reports/R17_ACCT1_report.md` | 非缺口——本令报告，待 DSH 复核 |
| D10 | `reports/R17_P4FIX4_reconcile.md` | 分歧处理记录；其结论已被 D9（zcode 复核 PASS）间接覆盖，建议维持现状并留痕 |

## 七 · 命名与所有权规则（A5；order_template.md 的对应增节见 §遗留说明）

- **令** = `workorder_<线>_<slug>.md`（库外 `D:\dsh-work\<日期>\`）；**报告** = `<线>_<轮>_report.md`；**复核** = `..._review_by_<作者>.md`。
- **两执行方防撞名**：WB 画布线报告用专名（`R17_P2_CANVAS_report.md`）；zcode 线沿用历史名；**WB 画布 P3 交付时预留名 `R17_P3_CANVAS_report.md`**（避开 zcode 的 `R17_P3_report.md`）。
- **别名映射（既有文件不重命名）**：
  - `reports/R17_P2_report.md` = **zcode SPI 线 P2**（DshBackend 组合），与 WB 画布 P2（`R17_P2_CANVAS_report.md`）互不覆盖；
  - `reports/R17_P3_report.md` = **zcode 线 P3**；WB 画布 P3 尚未交付（见上预留名）；
  - `reports/R17_P0_report.md` = zcode **SPI 抽取** P0；`reports/R17_P0_p5_fixes_report.md` = zcode **P5 阻塞修复**（同弧第二个"P0"）；
  - `workorder_BC_p5_liveness_trust.md`（v1，`d779fed5ed22f2bc`）已被 `_v2`（`cfe3154e123eb17e`）取代，v1 不再单列行。
- **共享文件所有权表**：

| 文件 | 谁可改 | 规则 |
|---|---|---|
| `docs/order_template.md` | 须 DSH + 用户双批的令 | 既有门禁 4 条不可削弱；两执行方均不得单方改 |
| `tests/tools/*`（含本令新增 `ledgercheck.py`） | 执行方改、复核方核 | 任何改动须在交付报告披露；判别力不得削弱 |
| `docs/criteria_ledger.md` / `docs/endpoint_authority.md` / 本册 | **append-only**：追加条目须令或复核裁定；历史条目不可改 | 新增端点须扩围令；判据降级须在"变更史"登记 |
| `reports/*.md` | 报告由交付方写；复核件由复核方写 | `reviewed` 标记只在本册维护 |

## 八 · 变更史（本账自身）

| 日期 | 变更 | 依据 |
|---|---|---|
| 2026-10-11 | 初版建立：6 节 35 行（WB 画布 8 / WB 其他 4 / zcode 适配 11 / BC·N·dsh 10 / 记录件 6——含本令 A8 行）；未复核 6 行（含 1 项新发现 C1） | 工单 `cd52cd325f8fb4bf` A3/A5 + 附录 A-3 |
| 2026-10-12 | **B-5 裁定落地（F-2）**：① `reviewed` 改三态 `yes/no/unknown`（原 `n/a` 6 行 → `unknown`）；② C2 补对象证据（对象行原文吻合；提交 `1384921`+`be98190`）；③ C1 维持 `no` 并写明张力注记（同名前缀命中但对象行不符；DSH 撤回"假阴性"判断）；④ 册头固化**配对规则**（同名前缀 + §七别名表 + 首行对象行必须吻合）与 **yes 行对象证据要求**；⑤ A7 行令指纹随令升版更新为 `a7cd4654648d2bd5`；⑥ §六 C1 处置更新为"已裁定" | 令 `a7cd4654648d2bd5` F-2 + 附录 B-5（DSH 撤回其前次裁定） |
| 2026-10-12 | **B-8/F-5 对象证据棘轮落地**：① 26 行"yes 且无证据"（A1–A6/B1–B2/C3–C11/D1–D9）**逐行回填对象证据**——以其复核件实际引用的被审提交号写入"复核件"格（其中 B1/B2/D4/D5/D6/D7 六行复核件带 2026-10-10 事故勘误，哈希不可解析、由恢复链 `ef00d85`/`ad96614` 承载，如实标注）；② 复核件引用扫描结论：**26/26 均引用提交号，零行降级 `n/a-legacy`**；③ 棘轮在 `ledgercheck.py` 固化：基线集合冻结（26 行），**新增行再现 yes-无证据 ⇒ 硬失败 exit≠0**，打印棘轮余量（当前 margin=26）；④ A7 行令指纹更新 `3086eb4d86ad5a96` + 报告列回填（`97a29b4`）；⑤ 册头纪律行补 `n/a-legacy` 第四态与棘轮声明；⑥ §六 A7 处置同步 | 令 `3086eb4d86ad5a96` B-8/F-5 |
| 2026-10-12 | **ACCT2 第四状态 `partial` 落地**：① 册头纪律行扩**五值**（新增 `partial`：内容可证、链或锚点不可证；结论格恰好一次 `不可证理由：`，复核件格带路径，行内 ≥1 锚点引用——作废锚点照样可引用；`unknown` 禁出现该前缀）+ 新增**五值判别树**与唯一落位要求（A-2）；② C1 行 `no`→`partial`（DSH 补独立复核件 `reports/R17_SPI_P0_review_by_dsh.md`，提交 `f594258`；对象证据含作废锚点引用 `e6629a1`）；③ §六 C1 处置更新为"已闭合"；④ 机检同步：`ledgercheck.py` 五值枚举、`yes`/`partial` 均须对象证据、`partial` 另须恰好一次不可证理由（0 或 ≥2 ⇒ 硬失败）、`unknown` 行禁前缀（硬失败）、selftest 扩 2 例；F-5 棘轮不受影响 | 令 `09a74ff3b264f916` ① + 附录 A-1/A-2 |
