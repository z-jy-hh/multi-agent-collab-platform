# R17 运行时开关清单（机器生成）

> 生成脚本：`D:\dsh-work\2026-09-25\seal_gen_switches.py`；提交后可复核：`git grep -nE "os\\.environ\\.get\\(|os\\.getenv\\(" -- ':!tests' ':!runs'` 应与表内读取点一致。

> 扫描范围：全仓 `*.py`（排除 tests/ .venv .uvcache __pycache__ runs/ reports/ .git vendor）；匹配 `os.environ.get` / `os.getenv` / `os.environ[...]` / `*_ENV`·`*_GATE` 常量间接读取；允许一层嵌套括号与跨行调用。

> 判据：读取点(文件:行)与默认值均取自代码原文（默认值表达式逐字摘录）；语义列（影响面/后果）由执行方按其引用的代码行整理，无臆测值。


## 一 · 环境变量开关

| 名称 | 读取点(文件:行) | 默认值(代码原文) | 影响面 | 关闭/开启后果 |
|---|---|---|---|---|
| `AGENT_REGISTRY` | `agent_bridge/factory.py:32`（read(via const GATE_ENV)）<br>`agent_bridge/registry.py:526`（read(via const REGISTRY_ENV)）<br>`main.py:87`（read） | "0" | 注册表路径总闸（P2 回滚红线）。!=1 时 build_backend 直接返回 None：不 import registry 模块、不读 registry 文件、不构造任何后端；main.py doctor 行也跳过。=1 时注册表路径生效并输出 doctor 行。 | 关闭：回 R16 行为（旧构造路径）；开启：异构分发注册表生效 |
| `CREWAI_STORAGE_DIR` | `inspector/runner.py:209`（read） | (no default arg) | crewai 存储目录名（inspector/runner.py:209 探测自检；layercheck 固定设为 layercheck_probe 以防探针残留——见 layercheck.py:134）。 | 未设：用 cwd 名；设置：重定向 crewai 存储（测试隔离用） |
| `DISPATCH_BACKEND_OVERRIDES` | `agent_bridge/factory.py:41`（read） | (no default arg) | 后端构造 JSON 覆盖（离线测试钩子，hetero_dispatch.py:49-55）；坏 JSON 是响亮失败。线上不应设置。 | 缺省：注册表默认构造；设置 JSON：覆盖 node_bin/dsh_bin_js 等 |
| `DISPATCH_CONSENT_TOKEN` | `agent_bridge/dispatch_hooks.py:168`（read）<br>`api/routes.py:103`（read） | (no default arg) | 每次运行授权令牌（异构分发 paid/出境后端必需；api/routes.py:67 缺失时 pre_run_gate 拒绝）。 | 缺省（未设）：无授权 ⇒ 需授权后端一律拒绝；设置：按值匹配放行 |
| `DISPATCH_COUNTERS_ALLOW_CORRUPT` | `agent_bridge/dispatch_hooks.py:96`（read） | (no default arg) | 审查发现 S6 的反绕过：计量文件损坏时默认 fail-closed 拒绝分发，仅显式 =1 才放行（否则等于零用量绕过配额）。 | 缺省：损坏计量 ⇒ 拒绝分发；=1：放行（所有者显式担责） |
| `DISPATCH_DATA_OPT_IN` | `agent_bridge/dispatch_hooks.py:168`（read） | (no default arg) | 数据出境显式同意（与 consent 令牌同时读取，dispatch_hooks.py:154）。 | 缺省：=未同意（出境后端拒绝）；=1：本 run 同意数据出境 |
| `DISPATCH_HETERO` | `agent_bridge/dispatch.py:141`（read(via const DISPATCH_ENV)） | "0" | 异构分发总开关（P4）。=1 时 create_run 走 pre_run_gate：绑定解析、SPI 分发、预算/并发/授权检查全启用；!=1 时整个新路径不进入（api/routes.py:75 注释，dispatch_hooks.py:7-22）。 | 关闭：旧（pre-P4）路径完整运行，无绑定无门控；开启：新门控路径生效 |
| `DSH_ACP_ENABLED` | `api/events.py:27`（read） | "0" | dsh 后端事件 SSE 可见性（P5/D1）。=1 时 dsh 事件进 SSE 流；否则被过滤（后端本身仍可离线运行）。 | 关闭：dsh 事件不出现在 SSE；开启：全事件透传 |
| `DSH_BIN` | `agent_bridge/backends/dsh.py:148`（read）<br>`main.py:107`（read） | (no default arg) | dsh 二进制 JS 入口路径。backends/dsh.py:143 作为命令模板占位符 {dsh_bin} 的值，缺空串时 SpiError(kind=environment) fail-closed（:150-152）；main.py:107 供 dsh 自检；tests/integration/test_dsh_acp_client.py:285 未设则真机冒烟 skip（四口径 473/5 vs 472/6 分界）。 | 未设：dsh 后端命令解析 fail-closed、冒烟 skip；设置：真机 ACP 可用 |
| `DSH_DISPATCH_ROOT` | `agent_bridge/roots.py:24`（read）<br>`dsh_bridge/session.py:62`（read） | (no default arg) | 分发工作区根覆盖。roots.py:24-27 未设则 <project>/runs；dsh_bridge/session.py:62-63 未设则 <project>/runs/dsh_dispatch。 | 未设：默认 runs/ 族目录；设置：重定位分发工作区（隔离多实例用） |
| `DSH_EXPECTED_VERSION` | `agent_bridge/backends/dsh.py:443`（read）<br>`main.py:108`（read） | (no default arg) | dsh 自检（cmd_dsh_check）的期望版本锚，未设则不比对（main.py:108）。 | 未设：跳过版本比对；设置：版本不符报 FAIL |
| `DSH_HOME` | `api/routes.py:207`（read） | os.path.expanduser("~/.dsh") | 统一视图（F5）读 dsh 会话日志的根，缺省 ~/.dsh（api/routes.py:159）。 | 未设：~/.dsh/sessions；设置：指向其他 dsh 主目录 |
| `MODEL` | `core/llm.py:59`（read）<br>`crews/factory.py:60`（read） | (no default arg) | 模型名。core/llm.py:59 缺省 DEFAULT_MODEL；crews/factory.py:59 缺省 deepseek-chat。 | 未设：deepseek-chat；设置：换模型 |
| `NATIVE_AGENTS` | `agent_bridge/runtime.py:73`（read） | (no default arg) | 原生投放面（逗号分隔，必须是 routing.map 子集；空串=空集=全部走 inner，合法回滚形态）。 | 未设：取 routing.map 键；设置：收窄/关闭 |
| `NATIVE_FALLBACK` | `agent_bridge/runtime.py:77`（read） | (no default arg) | 原生失败策略（W4-1）。off=响亮失败；crewai=显式回退并打 degraded。 | off：不换跑道；crewai：回退 + degraded |
| `NATIVE_MAX_TURNS` | `agent_bridge/runtime.py:95`（read） | (no default arg) | 单 run 原生 turn 总额度（映射到 DshBackend 的 turns 预算）。 | 默认 8；耗尽 native.exhausted |
| `NATIVE_QUEUE_MAX` | `agent_bridge/runtime.py:81`（read） | (no default arg) | 原生 turn 在途上限（含正在跑的那个，>=1；0/负数=配置错）。 | 默认 8；溢出 native.queue_full |
| `NATIVE_QUEUE_WAIT_S` | `agent_bridge/runtime.py:88`（read） | (no default arg) | 等待原生 turn 的上限秒数（循环轮询 host cancel）。 | 默认 600；超时 native.queue_timeout |
| `NATIVE_RUNTIME` | `agent_bridge/runtime.py:69`（read）<br>`graphs/runner.py:143`（read） | (no default arg) | 角色级原生执行总开关（W4-1）。off=零新增字段/事件；dsh=routing.map 里的角色经 ACPRuntime 落到 dsh ACP（需 AGENT_REGISTRY=1）。 | 未设/off：既有行为；dsh：原生路径生效 |
| `OPENAI_API_KEY` | `core/llm.py:58`（read）<br>`core/llm.py:125`（read）<br>`crews/factory.py:67`（read）<br>`inspector/runner.py:468`（read） | "" (empty string); (no default arg) | LLM 密钥。core/llm.py:125 未设 ⇒ FakeLLM 离线模式（任务书 §五回滚）；设置 ⇒ OpenAICompatClient。crews/factory.py:57、inspector/runner.py:468 同源。 | 未设：全链 FakeLLM（无网）；设置：真模型调用 |
| `OPENAI_BASE_URL` | `core/llm.py:57`（read）<br>`crews/factory.py:68`（read）<br>`inspector/runner.py:469`（read） | "https://api.deepseek.com/v1"; (no default arg) | LLM 端点。core/llm.py:57 缺省 DEFAULT_BASE_URL；crews/factory.py:58 缺省 https://api.deepseek.com/v1。 | 未设：DeepSeek 官方端点；设置：可指向兼容端点/离线桩 |
| `R16_TRACE` | `agent_bridge/acp_client.py:48`（read） | (no default arg) | ACP 客户端协议轨迹文件（acp_client.py:48，模块导入时一次求值）；设置后逐行追协议日志（_trace）。 | 未设：无轨迹；设置：轨迹写指定文件（排查协议问题用） |
| `REDACTION_ENABLED` | `core/redact.py:69`（read） | "1" | 脱敏总开关（N2）。唯一显式 "0" 才关；其余一律开。关闭后所有持久化写点（trace/op store/run meta/run state）不再掩码。 | 关闭：明文入库（K13c 变异已证扫描器必红）；开启：默认态 |
| `TAVILY_API_KEY` | `crews/tools/tavily.py:16`（read） | "" (empty string) | Tavily 搜索工具密钥（crews/tools/tavily.py:16，缺省空串 ⇒ 工具不可用）。 | 未设：搜索工具不可用；设置：启用联网搜索 |
| `WB_AUDIT_DIR` | `api/routes.py:218`（read） | os.path.expanduser("~/.workbuddy/audit-log") | 统一视图（F4）读 WB 审计日志目录，缺省 ~/.workbuddy/audit-log（api/routes.py:170-171）。 | 未设：~/.workbuddy/audit-log；设置：指向其他审计目录 |
| `WB_CLI` | `agent_bridge/backends/wb.py:61`（read）<br>`agent_bridge/wb_check.py:61`（read） | (no default arg) | WB 命令行路径。backends/wb.py:61 占位符 {wb_cli} 缺值即 SpiError fail-closed；wb_check.py:61 还有 shutil.which("codebuddy") 兜底。 | 未设且 PATH 无 codebuddy：wb 后端构造失败/自检 ENV-FAIL；设置：可用 |
| `WB_ENABLED` | `agent_bridge/factory.py:60`（read(via const WB_GATE_ENV)） | "0" | WB 后端二级闸，须 AGENT_REGISTRY=1 且 WB_ENABLED=1 才构造 WbBackend（factory.py:63-65 wb 分支二次检查）。C4 反证：=0 时 registry 文件读点与 WbBackend.__init__ 均不触发（tests/unit/test_spi_wb.py:92-143）。 | 关闭：wb 后端不可达（build 返回 None）；开启：wb 后端可路由 |

## 二 · 配置文件开关（非 env）

| 文件:行 | 原文 |
|---|---|
| agents/registry.yaml:63 | `enabled: true` |
| agents/registry.yaml:64 | `requires_authorization: false` |
| agents/registry.yaml:111 | `enabled: false                       # 红线：默认关闭` |
| agents/registry.yaml:112 | `requires_authorization: true         # paid + 需登录 → 每次运行显式授权（consent token 落审计）` |
| configs/evolve.yaml:2 | `mode: propose          # propose | auto (--evolve auto overrides for one run)` |
| configs/evolve.yaml:3 | `K: 3                   # golden-set runs per version (>= 3, never 1-vs-1)` |
| configs/evolve.yaml:4 | `threshold: 0.01        # KEEP iff score_delta > threshold (== → ROLLBACK)` |

## 三 · 非开关项（扫描命中但如实排除）

- `LOCALAPPDATA` — inspector/runner.py:212 — Windows 标准环境变量，仅探测 legacy CrewAI 目录存在性，非开关
- `os.environ[k] = v (core/llm.py:35)` — .env 文件装载点（env 优先于 .env），是写入机制不是开关
- `_ENV_KEEP 透传 (agent_bridge/acp_client.py:166-169)` — 子进程环境白名单透传（D2：显式 per-dispatch 值优先），非开关

## 四 · 测试注入（仅 tests/ 内出现，运行时不可达）

- 上述开关在 tests/ 内大量 monkeypatch.setenv 注入（如 test_pre_run_gate.py / test_backend_factory.py / test_spi_wb.py / test_dispatch.py），属判据证据，不是运行时开关；未列入第一节。

## 五 · 三员专答（令 §一②判据）

- `AGENT_REGISTRY`：**是 env**，读取点 main.py:87、agent_bridge/factory.py:32（经常量 GATE_ENV，factory.py:24）、agent_bridge/registry.py:484（经 REGISTRY_ENV，registry.py:81）；默认 "0"（代码实参）。
- `WB_ENABLED`：**是 env**，读取点 agent_bridge/factory.py:39（经常量 WB_GATE_ENV，factory.py:25）；默认 "0"。
- `DISPATCH_HETERO`：**是 env**，读取点 agent_bridge/dispatch.py:141（经常量 DISPATCH_ENV，dispatch.py:47）；默认 "0"。
> 更正记录：`reports/R17_closeout_handover.md` §5.2 曾记「三者不经 os.environ 读取」——与代码不符（均经 os.environ.get 读取，只是经模块级常量间接引用），以本清单为准。
