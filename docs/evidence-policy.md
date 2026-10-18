# 能力证据变更控制（W3-4 口径）

> 目的：`capabilities.*.evidence` 是对现实的**断言**。新观测只能按下列规则改动它，
> 且**改动必须有人批**。工具从不直接改 registry。

## 一 · 三档证据与四条规则

| 档 | 前缀 | 含义 |
|---|---|---|
| 2 | `measured@` | 真机/真帧观测 |
| 1 | `static@` | 来自配置/文档等静态依据 |
| 0 | `assumed-fail-closed` | 无证据，按 fail-closed 处理 |

| 动作 | 条件 | 是否可应用 |
|---|---|---|
| `upgrade` | 新档 > 旧档 | ✅ 需人批 |
| `same` | 同档、细节可能更新 | ✅ 需人批（刷新溯源） |
| `downgrade` | 新档 < 旧档 | ❌ **拒绝**，除非有明确指令 |
| `refused` | 未知 capability / 观测无 evidence / 前缀非法 | ❌ 永不可应用（即使填了批准） |

## 二 · 谁批、存哪

- **谁批**：业主。载体沿用扩围令格式：`approved_by`（人）+ `order_ref`（令文件/会话授权）。
- **存哪**：提案 `reports/probes/proposal_<utc>.json`（`status: pending-approval`）；
  批准记录 append-only 写入 `docs/evidence_approvals.md`。
- **旧 evidence**：**只升不删**；刷新同档时保留旧文本于提案的 `old` 字段，便于追溯。
- **谁改 registry**：人（或经人批的脚本）。本工具**只生成提案**。

## 三 · 本次实跑（真机证据 → 提案）

来源：`reports/probe_evidence_20261017T130211Z.json`
提案：`reports/probes/proposal_20261017T141224Z.json`

| capability | 动作 | 说明 |
|---|---|---|
| `usage_shape` | `same` | 真机 `context_used` 帧，与现有 `measured@真机 4 帧` 同档，细节可刷新 |
| `supports_resume` | `same` | `session/list+resume` 实测成功，与现有 `measured@…` 同档 |
| `paid` | **`downgrade`（拒绝）** | 探针无账户数据，**不得**把 `measured@本地进程` 降为 assumed |
| `egress` | **`downgrade`（拒绝）** | 未做出网观测，**不得**削弱现有 `measured@` |

> 这两条 `downgrade` 是**设计预期**：探针没测到的东西，不能拿“没测到”当新证据。

## 四 · 基线豁免

本政策生效前，registry 里已有的 17 条 `measured@` 证据为**基线**，不再补批；
此后**任何**改动都需走提案 + 批准 + 台账。

## 五 · 复现

```
.venv\Scripts\python.exe tests\tools\probe_capabilities.py            # 真机观测
.venv\Scripts\python.exe tests\tools\propose_evidence.py reports\probe_evidence_<ts>.json
```
