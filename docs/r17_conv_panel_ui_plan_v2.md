# 控制台「对话/时间线」面板 · UI 方案 v2（终版）

> 作者：dsh（评审 + 定稿）｜送审对象：WB 方案 `conv_panel_plan_v1.md`｜日期：2026-10-08
> 依据：`ui-ux-pro-max` 技能（本地已装，pin `1a2c459…`，MIT）+ 仓库既有 UI 与测试锚点 + 我对 v1 的逐条复核
> 范围：**只读回放**（已结束 run）；二期 SSE 实时不在本方案内。**后端零改动**（沿用既有 trace/artifacts 端点）。

---

## 一 · 对 v1 的评审结论：**方向对，7 处须改**

**总评**：v1 的数据面梳理（端点、事件形状、`op_id → artifacts` 关联、红线约束）**扎实可采**；问题集中在**交互隐喻、可访问性、长串与三态、red-line 推论**四处。

| # | v1 的问题 | 为什么是问题（依据） | 改成什么 |
|---|---|---|---|
| **C1** | 用**发言气泡**映射 agent 发言 | 气泡是**消费级聊天隐喻**；控制台要的是**扫读密度**。技能检索 `chat/timeline bubble` **0 命中**（如实报告），而其 style 命中为 **Minimalism & Swiss**（Best For: dashboards / enterprise apps / professional tools） | 改为**左对齐"事件块"时间线**：左侧一条 2px 语义色条 + 事件类型图标 + 标题行（agent/键/时间）+ 正文块。**不做左右交替、不做圆角气泡** |
| **C2** | agent **仅按颜色**区分 | 技能 Priority 10 anti-pattern：「Relying on color alone to convey meaning」 | **三重编码**：① agent 名文本（始终显示）② 2px 色条/首字母徽标 ③ 图标。色板取自下节 token，深色底对比 ≥4.5:1 |
| **C3** | 未处理**长 id/键溢出** | `op_id`/`key`/`agent_id` 是长串，撑破容器是运维 UI 最常见崩点。技能 html-tailwind 规则「Compact label layout」**Severity: High**：集合用 `flex-wrap + gap`；单个标签 `min-w-0 + nowrap + truncate`；**图标不得被压缩**；反例正是 `overflow-hidden` 硬裁 | 标题行套用该规则：容器 `min-width:0`，标签 `white-space:nowrap; overflow:hidden; text-overflow:ellipsis`，图标 `flex:none`；完整值走 `title` + **点击复制** + viewer |
| **C4** | 只有"加载/截断"提示，**缺空态与错误态** | 技能 Loading Indicators **Severity: High**（近即时操作不要闪、要 `aria-busy`）；Empty States（"Show helpful message and action"，反例"Blank empty screens"） | 显式三态：**未加载**（"未加载该 run 的对话"+ 主动按钮）/ **加载中**（`aria-busy="true"` + 骨架 ≤2 行，<150ms 不显示）/ **失败**（就近错误行 + 重试按钮，非顶部飘条） |
| **C5** | 「展开更早」未定义**滚动锚点** | 顶部追加内容会把用户视线推走 | 展开时**保持锚点**：记录 `scrollHeight` 差并把 `scrollTop` 加上差值；一次性追加 ≤400 条，按钮显示剩余条数 |
| **C6** | node 名**直接进阶段分隔线**（v1 §八.2 自问） | 测试锚点禁止 `supervisor`/`codebuddy`/`subprocess` 字面出现在 HTML。**node 名不可控 ⇒ 一旦含禁词，grep 判据即破**（把原名塞进 `title`/`data-*` 同样会被 grep 到） | **node 名一律不进 DOM**：白名单映射表（`plan→计划`、`dispatch→分派`、`execute→执行`、`review→评审`、`recover→恢复`…），**未知 node ⇒ 通用标签「阶段」+ 序号**；原始 node 名只在内存中用于映射，不写入 DOM 文本/属性/注释 |
| **C7** | 焦点/键盘/动效未提 | Focus States **Severity: High**（含模态内控件必须有可见焦点环）；Focus Not Obscured；Motion：`prefers-reduced-motion` 下不得播放非必要动效 | 见 §五 交互规范：可见焦点环、viewer 模态**焦点陷阱 + Esc + 焦点归还**、滚动区不被 sticky 条遮挡、`reduced-motion` 下动画时长置 0 |

**v1 §八 三问的明确答复**：
1. **op_id 关联**：端点本就按 `run_id` 取 ⇒ 前端缓存键必须是 **`runId → opId → value`**（不是 session 键）；切 run 清缓存。`op_id` 不在 artifacts 中 ⇒ 显示**显式占位**「正文不可得（op 缺失）」而非空白。
2. **禁词风险**：见 **C6** —— node 名**不得进 DOM**，用白名单映射 + 未知兜底；这层间接**必须接受**，且是唯一安全做法。
3. **放置**：**选侧栏折叠区块**（与 `dc-section` 同构、可与派发台并存对照）；折叠状态存 `localStorage` 且**默认折叠**。不选"底部 tab 并列"——会让"对话"与"历史"互斥，破坏对照排查。

---

## 二 · 设计系统（可直接落成 CSS 变量）

**风格**：Minimalism & Swiss（技能命中，Best For: dashboards/enterprise）｜**密度**：Dense（8/10）｜**动效**：Subtle（2/10）｜**变体**：Centered/Minimal（3/10）

**颜色 token**（深色；取自技能给出的配色，落到既有变量层，**语义化命名**，组件内禁止裸 hex）
```
--bg:#0F172A  --card:#1B2336  --card-fg:#F8FAFC  --fg:#F8FAFC
--muted:#272F42  --muted-fg:#94A3B8  --border:#475569  --ring:#FFFFFF
--primary:#1E293B  --accent:#22C55E  --on-accent:#0F172A  --destructive:#EF4444
事件语义色条：阶段 #64748B｜计划 #38BDF8｜分派 #A78BFA｜执行 #22C55E｜仲裁 #F59E0B｜评审 #F472B6｜失败 #EF4444
```
*约束*：正文/次要文本在 `--bg` 与 `--card` 上对比 **≥4.5:1**；`--muted-fg` 仅用于**非关键**元信息；颜色**永不单独承载语义**（C2）。

**字体**（**不引入 webfont**：内部/离线自包含优先；此处与技能建议的 Google Fonts 冲突，按"离线可控"取舍）
```
--font-ui: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif
--font-mono: ui-monospace, "Cascadia Mono", Consolas, monospace      /* op_id / key / 计数 */
数字用 font-variant-numeric: tabular-nums（计数与时间对齐）
基准 13–14px（控制台密度，非 16px 营销页）；行高 1.45；正文行长 ≤ 90ch
```

**间距/圆角/动效**
```
间距 4/8 节奏：--sp-1:4 --sp-2:8 --sp-3:12 --sp-4:16 --sp-5:24
圆角：--r-sm:4（标签）--r-md:6（卡片/按钮）
动效 token：--t-fast:120ms --t-base:180ms --t-slow:240ms，缓动 cubic-bezier(.2,.7,.3,1)
@media (prefers-reduced-motion: reduce) → 所有 duration 置 0ms（保留状态变化，不保留位移/淡入）
```

---

## 三 · 布局与信息架构
```
侧栏（宽 320–380px，可折叠）
└─ conv-section（与 dc-section 同构）
   ├─ 头部：标题「对话」+ 状态徽标（未加载/加载中/失败/已加载 N 条）+ 折叠箭头
   ├─ 工具行：run 标识（截断+复制）·「加载对话」·「展开更早（剩 N）」
   └─ 时间线（滚动容器，max-height 60vh）
      └─ 事件块（左色条 + 图标 + 标题行 + 正文块）
```
- **时间线顺序**：按 `seq` 升序；**同 seq** 按 `(seq, type)` 稳定排序（不依赖 ts）。
- **正文块**：≤2000 字符 + 省略号 + 「查看全文」（复用既有 viewer）；超长用 `line-clamp` 视觉截断而**不是** `overflow:hidden` 硬裁（C3）。
- **滚动区**：容器 `overflow:auto`；不被 sticky 头/底部条遮挡（Focus Not Obscured）。
- **虚拟化**：首屏 ≤800 条；单次追加 ≤400 条；节点总数 >1500 时对已渲染块启用 `content-visibility:auto`（避免全量重排）。**不做虚拟滚动**（v1 正确）。

---

## 四 · 组件清单与状态（每个都要有 hover/focus/disabled 三态）
| 组件 | 规格 | 状态 |
|---|---|---|
| 事件块 | 左 2px 语义色条；标题行：图标 + agent 名（文本）+ key（mono，截断+复制）+ 时间（tabular） | 默认/hover（`--card` 提亮 4%）/focus-visible/展开中 |
| 计划卡 | subtask 列表（`description` 全文，可折叠超 6 条） | 同上 |
| 分派行 | `subtask_id → agent_id (score)`；score 右对齐 tabular | 同上 |
| agent 名片行 | role / domains（`flex-wrap` 标签，套 C3 规则） | 同上 |
| 仲裁/评审卡 | `action`/`verdict` 用语义色徽标；`why`/`comments` 全文块 | 同上 |
| 健康行/结束标记 | score + summary；status 用语义色徽标 | 同上 |
| 按钮（加载/展开/复制/全文） | 高 28px（桌面，鼠标为主）；有 `:focus-visible` 环；disabled 明显不可点 | 默认/hover/active/disabled/focus |
| 三态占位 | 未加载 / 加载中（`aria-busy`）/ 失败（错误行+重试） | —— |

**图标**：一律**内联 SVG**（技能硬规则：**不得用 emoji 当图标**）；统一 16px、`stroke-width:1.5`、同一族；装饰性图标 `aria-hidden="true"`。

---

## 五 · 交互与键盘规范（C7）
1. **焦点**：所有可交互元素 `:focus-visible{outline:2px solid var(--ring);outline-offset:2px}`；**不得** `outline:none` 无替代。
2. **viewer 模态**：打开时焦点移到标题/关闭键；**Tab 循环限制在模态内**（焦点陷阱）；`Esc` 关闭；关闭后焦点**归还**触发按钮；`aria-modal="true"` + `role="dialog"`。
3. **键盘**：Tab 顺序 = 视觉顺序；「展开更早」`Enter/Space` 均可用；时间线可用 `↑/↓` 在事件块间移动（可选，实现则需保持焦点可见）。
4. **加载**：点击「加载对话」后 150ms 内不显示任何 loading 视觉（避免闪烁）；超过则显示骨架 + `aria-busy="true"`；失败显示就近错误行 + 重试。
5. **动效**：新事件块 `opacity 0→1` 120–160ms；**不做**位移+缩放组合；`reduced-motion` 下不播。
6. **复制**：复制完整 key/op_id/正文 ⇒ 复制成功显示 1.2s 的 `role="status"` 提示（"已复制"）。

---

## 六 · 数据映射（沿用 v1 的锚，补三处硬化）
| 事件 | 渲染 | 硬化点 |
|---|---|---|
| `node_enter` | 阶段分隔线 | **白名单映射**，未知 ⇒ 「阶段」（**node 名不进 DOM**，C6） |
| `plan` | 计划卡 | subtask 数 >6 折叠 |
| `assignment` | 分派行 | agent_id 文本化（不靠色） |
| `agent_card` | 名片行 | domains 套 C3 |
| `blackboard_write`(+相邻 `agent_output`) | 事件块（正文取自 artifacts） | 缓存键 `runId→opId`；缺失 ⇒ 显式占位；**`esc()` 强制**，正文用 `textContent` |
| `arbitration` | 仲裁卡 | `why` 全文 |
| `review` | 评审卡 | `comments` 全文；`verdict` 语义徽标 |
| `health` / `run_finished` | 健康行 / 结束标记 | —— |
- **脱敏掩码** `⟨REDACTED:…⟩` **原样显示**，前端不解读不还原（v1 已对，保留）。
- **禁止 `innerHTML` 拼接未转义数据**；一切来自端点的字符串先过 `esc()`（或 `textContent`）。

---

## 七 · 验收判据（可机检优先；交付时逐条贴原始输出）
- **A1 红线**：`pytest tests/integration/test_g6_api.py tests/integration/test_p5_features.py -q` 全绿；`grep -nE "supervisor|codebuddy|subprocess"` 于 `index.html` **0 命中**；8 个既有 id 全在。
- **A2 无外链依赖**：`grep -nE "fonts\.googleapis|@import url\(http|https?://[^\"']+\.(woff2?|css)"` **0 命中**（离线自包含）。
- **A3 长串不溢出**（脚本注入 200 字符 op_id/agent_id）⇒ 容器 `scrollWidth <= clientWidth + 1`，标签出现省略号。
- **A4 三态可达**：脚本依次断言存在 `[data-conv-state="idle|loading|error|ready"]`；`aria-busy` 仅在 loading 出现。
- **A5 键盘**：Tab 序列覆盖全部可交互元素（无跳过、无走出可视区）；viewer 打开后 Tab 循环在模态内、`Esc` 关闭、焦点归还（断言 `document.activeElement` === 触发按钮）。
- **A6 reduced-motion**：`matchMedia('(prefers-reduced-motion: reduce)')` 命中时计算样式动画时长 = 0s。
- **A7 折叠状态**：刷新后保持上次折叠态（localStorage）。
- **A8 大 run**：首屏渲染 ≤800 块；点「展开更早」后 `scrollTop` 相对位移 ≤4px（锚点保持）。
- **A9 回归**：四口径 4×(473 passed / 5 skipped，设 `DSH_BIN`) + layercheck + `--check frozen|clean|liveness|redaction|failure` 全 PASS（**先 commit 再跑 clean**）。
- **A10 冒烟**：真历史 run（含评审轮）渲染出 阶段→计划→分派→发言→仲裁→评审→结束 全链，**贴截图**。

---

## 八 · 明确不做（保留 v1 的克制，并加三条）
1. 后端零改动（不加/不改端点）
2. 不做聊天输入框（只读回放；写路径涉及产品语义）
3. 不做 SSE 实时、不做虚拟滚动、不做跨 run 聚合
4. 不动 `dc-*` 派发台既有逻辑与 DOM
5. 不引入框架/构建链/webfont（新增）
6. 不用 emoji 当图标、不引入左右交替气泡（新增）
7. 不把 node 原名写入 DOM（新增，见 C6）

## 九 · 二期（不在本方案）
进行中 run 的 SSE 增量追加 + 与 `dc-events` 的订阅合并策略（避免两套订阅）；届时需先定"谁持有 EventSource"。
