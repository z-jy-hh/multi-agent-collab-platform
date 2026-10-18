# multi_agent_collab · 模块化多模式多 Agent 协同平台

LangGraph(调度)+ CrewAI(执行)的混合多 Agent 系统,带能力匹配派活、记忆隔离防串台、
向量时钟冲突仲裁、自检(inspector)与自进化(evolver)。

## 快速开始

```powershell
# 0) 环境(任务书 C3;默认缓存目录被拒,必须显式设 UV_CACHE_DIR)
$env:UV_CACHE_DIR = "<项目根>\.uvcache"
& "$env:USERPROFILE\.local\bin\uv.exe" venv --python 3.11 "<项目根>\.venv"
& "$env:USERPROFILE\.local\bin\uv.exe" pip install --python "<项目根>\.venv\Scripts\python.exe" `
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 1) 体检(离线全过;api_key 只回显 len + 末 4 位)
.venv\Scripts\python.exe main.py doctor

# 2) 跑任务(无 key 自动走 FakeLLM 离线模式;有 key 填 .env 后 --llm real)
.venv\Scripts\python.exe main.py run "AI 技术调研报告:多Agent协同的工程现状" --topology supervisor --llm fake

# 3) Web 控制台 http://127.0.0.1:8000(被占则 --port 8001)
.venv\Scripts\python.exe server.py

# 4) 自进化闭环(propose 只出候选;auto 应用并对比,变差自动回滚)
.venv\Scripts\python.exe main.py evolve --mode propose
.venv\Scripts\python.exe main.py evolve --mode auto
```

`.env`:由**委托人本人**把 `.env.example` 复制为 `.env`(UTF-8)填入 `OPENAI_API_KEY=`
(R27,执行方绝不创建 .env)。没有 key 时系统整体退化到 FakeLLM 离线模式(§五回滚)。

## 测试

```powershell
npm ci                        # 仅 playwright-core（1 个包）；UI 浏览器用例需要
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tests\tools\gate_all.py   # 5 步：pytest/ledger/audit/layer/ui_mutation
.venv\Scripts\python.exe tests\tools\ui_mutation_probe.py   # 27 条变异自证（全量）
```

`tests/integration/test_ui_browser_interaction.py` 用**真实 Edge/Chrome**（无头）跑拖拽/缩放/置顶/权限卡；
`ctx.route` 喂静态文件与 `/api` fixture，**不起服务、不占端口**。缺 `node_modules` 或浏览器时用例 **fail（不 skip）**；
无 Edge 的机器用 `UI_BROWSER_PATH` 指向 msedge/chrome 可执行文件。

## 四种协同模式(共用同一内核)

| 模式 | 拓扑 | 说明 |
|---|---|---|
| supervisor(默认) | 主管拆解 → 能力匹配派活 → Send 并行 → 冲突仲裁 → 汇总 → 审核返工(≤2 轮) | 完整链路,含冲突仲裁 |
| single | 单 agent 直接对话 | 无调度 |
| pipeline | 固定 A→B→C | 无调度无仲裁 |
| debate | 正反方 + 裁判 | 双终止:最大轮数 **且** 裁判可判定 |

**新增模式 = `configs/topologies/<name>.yaml` 一个文件**(重启后 `/api/modes` 自动多一项,
前端下拉由后端驱动,零代码改动——判据 9 已证)。

## 能力匹配公式(core/matcher.py,C7)

```
score(agent, subtask) = Σ_{c∈required} [ 1.0·(c∈agent.domains) + 0.6·strength[c]/10 ] / |required|
```

两条硬门,**任一不满足即停止派活并上报缺什么,禁止硬派**:
1. coverage:每个 `required_capabilities` 至少有一个 agent 的 `strength[c] ≥ min_strength`;
2. score:被选 agent 得分 ≥ min_score。
没有单个 agent 能覆盖子任务全部能力 → `NoEligibleAgent`;能力本身无人覆盖 → `NoCapabilityMatch`。
LLM 只在分差 < gap_threshold 时参与平局裁决,不得推翻硬门,理由进事件流。

## 记忆与一致性(视频概念 ↔ 代码)

| 概念 | 模块 |
|---|---|
| sessionId 会话隔离防串台 | core/memory.py `AgentMemoryGuard`(跨命名空间 → NamespaceViolation) |
| 共享全局黑板(只发布不覆盖) | core/state.py 纯 reducer 合并 + guard.publish-only |
| 幂等 ID(重放安全) | core/ids.py uuid5 确定性派生(C6) |
| 向量时钟四态 / HLC 全序 | core/crdt.py(dominates/dominated/concurrent/equal;LWW 禁裸 time.time) |
| 分片冲突检测 + 仲裁 | graphs/builder.py 仲裁节点:rebase 或 pick,LLM 不可用时 LWW 兜底 |
| 历史补齐 | core/memory.py `ops_since(watermark)` 迟到者按水位拉增量 |
| 记忆池化风险规避 | crews/factory.py 一律 `memory=False`(C15),引擎记忆是唯一真相 |

## 自检(inspector)与自进化(evolver)

- 运行时卫兵:节点输出 schema 校验(graphs/builder.py `guard_node`)、超时/死循环协作式上限
  (recursion_limit + 轮数上限,C18 不承诺强杀)。
- 赛后体检:六检查器(schema/timeout/memory_invariant/match_quality/arbitration/cost)
  → `runs/<run_id>/health.json`。
- `main.py doctor`:环境/依赖/配置/存储/离线冒烟/连通性(Python 栈,R26)/CrewAI 存储重定向/
  **项目外足迹实测**(`crewai_mkdir_trace`,round-8:子进程拦截创建调用并拒绝项目外写入)。
- 进化闭环:`analyze → propose → guard(白名单 prompts/ 与 configs/,代码级护栏)→
  snapshot → apply → golden K=3 对比 → decide()(KEEP 当且仅当 delta>threshold;== 即回滚)
  → 回滚字节级还原(哈希证明)`。默认 propose,`--evolve auto`/`main.py evolve --mode auto` 才应用;
  `mode: off` 整体关闭。**进化系统永不改代码,只改提示词与配置**(C8)。
- **版本库语义(N1)**:`prompts/.versions/vNNN/` 里的文件是**应用改动前的备份**(回滚依据);
  被否决/被采纳的候选内容由 `record.json` 的 `diff` 字段承载
  (`候选 = 快照 + 优化规则`,`unified diff`,fromfile `a/<rel>` / tofile `b/<rel>`)。
  历史记录的 diff 由 `evolver/backfill_diffs.py` 幂等回填(只增键,不改既有字段)。
  **记录状态语义(round-14 R14-1)**:auto 流程对同一 version 先写 `decision="PENDING"`
  临时记录、golden 对比后再**且仅再**升级为一次 final(`KEEP`/`ROLLBACK`)——对已 final
  的记录、PENDING 重复写、他目录同号记录仍一律 `GuardViolation`(R10-1 append-only
  意图不变);PENDING 视作"进行中",占用其版本号(`next_version()` 不复用崩溃残号),
  `check_record_consistency` 对其执行同等身份校验。**任何异常必回滚**(round-14 R14-2):
  apply → golden → 写最终记录 → 裁定 整段任何异常都从快照**字节级还原** live 文件
  (逐文件 sha256 校验输出)后原样抛出;回滚自身失败独立醒目报错。事故登记(20260926,
  R13 追加轮真 LLM 复跑暴露):旧代码临时记录误写为 final `KEEP` 被自家闸门拒绝,
  异常落在 apply 之后、回滚之前,live 残留候选态——已由 R14-1/R14-2 修复,
  离线回归见 `tests/integration/test_r14_evolve_auto_flow.py`。
  版本目录枚举口径(round-9 R9-2 收敛,round-10 R10-2 定案,round-11 R11-2 补"无上限"):
  唯一入口 `core.paths.version_dirs` —— 目录名为 `v` + **至少 3 位数字**(`\d{3,}`):
  `v001`..`v999` 与跨千位后的 `v1000`/`v1234` 均接受(库自身写出格式为
  `v{N:03d}`,超千位自然变宽,放宽向后兼容);`v1`/`v01`(不足 3 位)与
  `vX92_tmp`/`v004_hidden` 等非规范名**一律拒绝**。**无上限**(R11-2 文书化,
  不新增拒绝规则):任意 ≥3 位数字宽度均接受——`v999999` 甚至更宽都是规范名;
  `next_version()` 取现有 max+1,超千位后目录名随写出格式 `v{N:03d}` 自然变宽
  (如 v999999 → 下一版 v1000000),枚举侧接受写出端可能产生的任何宽度。
  POSIX 语义微测(O4 降级,round-10 R10-4):本平台守卫依赖的三条路径语义
  (①`Path.is_relative_to` 为分量级判定;②同名前缀**兄弟**目录判 outside;
  ③POSIX 大小写**敏感**——Windows 镜像行为,见上文大小写口径)由
  `tests/tools/posix_semantics_probe.py` 在 Linux/macOS 上一条命令验证:
  `python tests/tools/posix_semantics_probe.py`——只用标准库、**零写盘**、
  零项目依赖(appdirs 缺席时该项 SKIP 不失败),输出 JSON + 期望值对照表,
  `all_passed` 为唯一判定(exit 0/1)。本机(Windows,无 WSL/容器)**未跑**,
  该脚本把 O4 从"未验"降级为"第三方一条命令可验"。版本记录一致性由
  `evolver.versions.check_record_consistency` 在**写入侧**(`save_record`,
  round-10 R10-1)与维护侧(`backfill`)双重把关:坏 record(版本号与目录
  不符 / 同号重复)当场 `GuardViolation`,不留半成品。

## API

`GET /api/modes` · `POST /api/runs` · `GET /api/runs/{id}/events`(SSE,15s 心跳 +
Last-Event-ID 续传,终止事件后收流)· `POST /api/runs/{id}/cancel`(协作式)·
`GET /api/runs` · `GET /api/runs/{id}/state|health` · `GET /api/sessions`

## 已知边界(诚实声明)

- **CrewAI 的项目外写入(整改 N3 披露)**:`crewai` 在 **import 期**就会创建
  `%LOCALAPPDATA%\CrewAI\<CREWAI_STORAGE_DIR 或 当前目录名>`(`crewai_core.paths.db_storage_path`
  → `appdirs.user_data_dir` 后立即 mkdir)。该**基目录无法用环境变量改变**
  (`CREWAI_STORAGE_DIR` 只能改子目录名)。本项目不写该目录下的任何文件内容,但 import 行为本身
  会产生目录。**实测基目录含 4 项(round-8 清理后)**:`multi_agent_collab`、`default`、
  `credentials`、`layercheck_probe`(层检固定名)。收敛措施:layercheck 子进程现固定
  `CREWAI_STORAGE_DIR=layercheck_probe`,不再产生新的随机名目录;历史上 5 个随机名残留目录
  (`layercheck_8z9o7nx3`/`layercheck_ctjnmvly`/`layercheck_mkkqze_6`/`layercheck_smvbpqrh`/
  `layercheck_s_rbjv1_`)已按委托方授权于 round-8 **整体备份后删除**(备份
  `D:\dsh-work\2026-09-25\crewai_residue_backup\`,删前逐文件 sha256 与源端比对通过)。
  另注:该基目录下 `credentials\secret.key`、`multi_agent_collab\*.db` 由 crewai 自身
  import/首次 kickoff 写出,**非本项目数据**(见下一条)。
- **另有两处项目外写入,appdirs 补丁够不着(round-8 实测披露)**:`crewai` 在 import / 首次
  kickoff 时还会写两处,均**硬编码**、**无官方环境开关**、不经 `appdirs`:
  a) `%USERPROFILE%\.config\crewai`(空配置目录)—— `crewai_core/settings.py:26`
  `DEFAULT_CONFIG_PATH = Path.home()/".config"/"crewai"/"settings.json"`,`:76`
  `get_writable_config_path()` 建父目录并试写 `.crewai_write_test`(crewai 自行删除);
  b) `%LOCALAPPDATA%\CrewAI\credentials\secret.key`(44 B 凭据文件)——
  `crewai_core/token_manager.py:88` 直接取 `os.environ["LOCALAPPDATA"]/"crewai/credentials"`。
  **处置(round-8 决策①:只测量+披露,明确不动第三方内部)**:补丁 `DEFAULT_CONFIG_PATH` /
  替换 `token_manager` 本可消除二者,但会把 per-user 凭据搬进项目树(可能进备份/仓库)且随
  crewai 升级失效,代价大于收益 —— 故保留原位并**如实登记**。测量手段:
  `inspector/crewai_trace.py` 子进程拦截 `Path.mkdir`/`os.mkdir`/`os.makedirs`,**拒绝**一切
  项目外创建并记录调用栈,再调用**真实**的 `db_storage_path()`;doctor 的
  `crewai_mkdir_trace` 检查据此三态判定 —— 存储路径解析到项目外 → FAIL;出现**白名单外**的
  新位置 → FAIL(新泄漏必须被看见,不许静默吸收);仅命中已披露白名单 → PASS 且**逐条点名**
  来源行。零污染判据(J2(c))同步升级为**内容级**(`size:sha256`,覆盖 `%LOCALAPPDATA%\CrewAI`
  与 `%USERPROFILE%\.config\crewai`),因为**名字级判据看不见内容漂移**——实测反例:某 139 KB
  kickoff DB 被写过而名集 **9→9** 不变。边界:SQLite `-wal`/`-shm` 边车**易失**(连只读打开
  都可能生成),不入指纹(否则判据会飘,比窄更糟);数据库文件本身入指纹。
  大小写口径(round-9 R9-3):crewai 硬编码的是小写 `crewai/credentials`,Windows 文件系统
  大小写不敏感,故大小写字面差异**不代表位置不同**——白名单匹配按路径分量、对大小写不敏感
  (`_is_inside` 用 `Path.is_relative_to`,Windows 路径语义);doctor 披露中保留**原始字面**
  (如小写 `crewai`)以便与 crewai 源码逐字对账。
- **trace 代际语义(复审 C5/B5,round-4 F2 收口)**:同 run_id 重跑**不会覆盖也不会静默丢弃**
  旧 trace——既有 `trace.jsonl` 轮换为 `trace.jsonl.1`(`.N` = 第 N 代历史,最旧在前),新运行
  从空文件开始。详见 `core/events.py` 的类 docstring。
- **本机环境两事实:瞬态探针 + 宿主 safe-delete shim(round-9/10 实测,一处清单)**:
  两者常被混为一谈又常被漏看其一,故合并登记——
  ①**`.crewai_write_test` 是瞬态文件**:crewai 在 `get_writable_config_path()`
  试写后**自行删除**,`--check clean` 的 J2(c) 快照若恰好落在 import 窗口内会报
  `ADDED .crewai_write_test`。处置:**重跑一次**即可(窗口外快照恢复一致);**不要**据此删文件或
  重录基线——那会掩盖真正的内容级泄漏。round-9 R9-6 起判据侧以**具名排除**收口
  (`audit_invariants.TRANSIENT_FILENAMES`,排除时逐次打印 NOTE,绝非静默)——这是把
  自湮灭文件挡在指纹判据外,**不是基线重录**:基线与既往证据一字未动,真正的
  内容级泄漏依旧会被看见。round-10 R10-3 起,整个覆盖区**整体消失**(目录被合法清理)
  也会以 WARN 出声而非静默吸收。
  ②**宿主 safe-delete shim 边界**:本机宿主以回收站方式包办
  `os.remove`/`os.rmdir`/`shutil.rmtree`,且对仓库内路径**间歇性 fail-closed**
  (`SHFileOperationW 失败: 0x2`)。因此需要自清理的测试夹具(g7 非规范目录注入)采用
  **「先同盘改名移出仓库、再尽力删除」**两步:改名不属于删除语义、必然生效,一步即恢复
  porcelain;删除失败只影响仓库外暂存目录,不影响被测仓库。同盘是因为 `os.rename` 不能跨盘符。
  ①②的相互作用:shim 的 fail-closed 偶尔会让 crewai 的自删动作失败,使"应已消失"的
  `.crewai_write_test` **残留数分钟**——此时它已被 R9-6 具名排除挡在指纹判据外,
  不会造成假红;残留物可待 shim 恢复后由 crewai 或人工清走,无需紧急处置。
  round-13 R13-1 起该相互作用的**第二个后果**(crewai 误判不可写后回退
  `<Temp>/crewai_settings.json`,crewai_core/settings.py:76 链,对 **Temp 根**发起
  mkdir)也有了判据侧收口:`measure_crewai_mkdir_trace` 按**动态精确等值**披露该回退
  ——父进程自行复算子进程上报的 `temp_root`(大小写保留、仅分隔符归一,禁任何
  前后缀/大小写模糊:`<temp>_evil`、子目录、大小写变体、缺 `temp_root` 字段一律
  仍 FAIL),命中则逐条点名来源 + 打一条 NOTE——**是披露,不是泄漏**,且**不影响**
  其它项目外位置的判定(白名单外新位置依旧 FAIL);该豁免**不写入**静态后缀白名单
  `KNOWN_OUTSIDE_SUFFIXES`(防过宽放行)。
- **验收抓取纪律(round-7 W4)**:`pytest tests -q` 的 `FAILED tests/...::test_...` 行出现在
  末尾汇总行(如 `1 failed, 113 passed, ...`)**之前**——只截取输出末 1-2 行会丢失失败用例名
  (第六轮"未留名瞬态"即由此而来)。抓失败名单请用 `pytest tests -q -rf` 或保留完整输出;
  doctor 类失败时 `test_g4_inspector` 现在会把完整 `report.checks` 逐行打印进失败信息,
  便于直接定名。
- **S4 自检的假阴性边界(round-7 W5 选① → round-8 C1 已闭合)**:`measure_crewai_redirect`
  构造的是 **appdirs** 的路径字符串(`appdirs.user_data_dir(<name>, "CrewAI")`)。若 crewai
  将来**换库**(如 platformdirs)或不再走 appdirs,该自检会报 PASS 而实际失效(**假阴性**)。
  round-7 登记的升级路径("在**子进程**内实测 crewai 真正使用的存储路径并断言其位于项目内,
  不得在主进程调 `crewai_core.paths.db_storage_path()`——它会在调用时 mkdir")**已于 round-8
  按此落地**:`inspector/crewai_trace.py` + doctor 检查 `crewai_mkdir_trace`,实测反向已红
  (中和重定向 → `resolved_inside=False` → FAIL,且 `refused=16` 保证红态下也零项目外创建)。
  S4 检查予以保留:它给出"补丁本身是否生效"的廉价一阶信号,两者互补。
- **交付态 git status(登记,D2)**:跑 `tests/tools/audit_invariants.py --check clean` 与
  `tests/integration/test_round5_c2.py` 会在 `reports/layercheck/` 留下**合法的未跟踪**
  时间戳日志 —— 因此交付态 `git status --porcelain` 不为空属预期(判定标准是 J2(b) 的
  范围判据:每条均为 `??` 且位于 `reports/layercheck/` 下)。
- **判据 2a 的覆盖口径**:layercheck 的单测树覆盖 = **除 `tests/unit/inspector` 外**的
  unit 树 + 全部 integration —— inspector 的 doctor 存储夹具属上层包(round-5 S4),
  由真实环境全量回归覆盖,不在裁剪环境内。
- **F5 回退事实(round-4)**:`prompts/worker/writer.v1.md` 现盘 = pre-v003 字节
  (sha16=65001572f6057832);v003 的 record.json 仍为 **KEEP** 且 diff 完整保留在
  `prompts/.versions/v003/`——历史记录与现盘状态 intentionally 不一致(回退是人为决定)。
- 无 key = FakeLLM 离线:输出是脚本化的,进化对比的 delta 主要来自延迟噪声——真实效果
  差异需填 key 后(`--llm real` / Web)由 12c 端到端验证。
- 取消是协作式的(节点间检查),不承诺"杀掉死循环"(Python 线程不可强杀,C18)。
- 默认演示语料为 `data/corpus/` 三篇自撰文档(离线可复现,C9);Tavily 仅配 key 时可选。
- `prompts/worker/writer.v1.md` 曾含 v003 追加的一行"补充约束(v2)"——来自一次由**延迟噪声**
  触发的 KEEP(delta 0.0108 恰好越阈 0.01)。**复审 round-4(F5)已将该行回退**(现盘恢复为
  pre-v003 字节,sha16=65001572f6057832):其真实效果从未单独验证,且锚断言自 C2 起改由
  固化 fixture 承载(`tests/fixtures/v003_*.md`),与现盘解耦。v003 的 diff 完整保留在
  `prompts/.versions/v003/`,需要时可人为重新应用。
- **行尾约定(复审 C1)**:`.gitattributes` 限定 `prompts/** text eol=lf`、`*.json text eol=lf`
  ——审计发现旧库 `core.autocrlf=true` 会让 checkout 把锚文件变回 CRLF、字节锚不可复现;
  限域 renormalize 后,工作树与新克隆均为 LF,锚值仓库级可复现(新克隆实测 CR=0、sha16
  =a5ad2f34a30508f8)。历史 v003/v004 快照文件亦已随之规范为 LF,故"回滚旧快照带回 CRLF"
  的隐患已消除。
