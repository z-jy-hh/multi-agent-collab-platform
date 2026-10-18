# 能力探针证据（W3-3b · 真机）

> 工具：`tests/tools/probe_capabilities.py`（操作型，不被 pytest 收集）
> 产物：`reports/probe_evidence_<utc>.json`
> 环境：`DSH_BIN=<…/@deepseek-ai/dsh/lib/bin.js>`、`DSH_EXPECTED_VERSION=0.0.1`

## 本次实测结果

| capability | 值 | evidence | 依据 |
|---|---|---|---|
| `usage_shape` | **`context_used_size`** | `measured@context_used observed` | 真机 `usage_update` 帧：`{'used': 10785, 'size': 1000000}` |
| `supports_resume` | **`true`** | `measured@session/list+resume` | 同 run/cwd 下 `session/list` 找到会话 → `session/resume` 成功 |
| `paid` | `null` | `assumed-fail-closed` | 无账户数据源，不猜 |
| `egress` | `null` | `assumed-fail-closed` | 未做出网观测，不猜 |

## 过程中修掉的真 bug

`DshBackend.resume_session` 调用 `manager.resume(run_id, platform_session_id, dsh_session_id)` 时**漏了 keyword-only 的 `cwd`** ⇒ 运行时 `TypeError`，而 registry 里 `supports_resume: true` 是**测出来的**——也就是说**能力成立但功能不可用**。

修法：补 `cwd`（默认 `runs/dsh_dispatch/<run_id>`，与写边界一致），并加 2 条测试（默认 cwd / 显式 cwd）。

## 这个证据**证明**

1. `usage_shape` 不是声明的，是从真机帧推出来的；
2. `supports_resume` 真的能 list + resume（不是“文档说支持”）；
3. 探针契约（未知 ⇒ fail-closed）在真实数据上工作正常。

## 这个证据**不证明**

1. `paid` / `egress` 仍无观测（无账户/无出网读数）；
2. resume 只在「同 run/cwd」内验证过；跨 cwd 被契约拒绝（这是设计，不是缺陷）；
3. 未在 `wb` 上做任何探针（skeleton）。

## 复现

```
$env:DSH_BIN="<...>/@deepseek-ai/dsh/lib/bin.js"; $env:DSH_EXPECTED_VERSION="0.0.1"
.venv\Scripts\python.exe tests\tools\probe_capabilities.py
```
