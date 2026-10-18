"""P2-2 · 提案三 C′：supervisor 返工环的可选对抗收敛增强。

红线：debate（configs/topologies/debate.yaml、debater_pro/con/judge 三张卡）
零字节、零引用；core/matcher.py 不触碰；本模块是**新增**路径。

行为（workorder 附录 C K3）：
- 开关 ``review_adversarial``（默认 false，来自 supervisor.yaml defaults）：
  **off 时本模块完全不进入** —— 零额外 LLM 调用、零事件、零 payload 变化
  （R5 逐字节等价的直接前提；K3：off 态 arbitration **没有 source 键**）。
- on 且本轮 review verdict == REWORK 且轮数闸未到顶（``max_review_rounds``，
  复用既有闸，不加新副本）时，触发**一次**对抗评审（= 每返工轮至多 1 次额外
  LLM 调用，与轮数闸构成双重有界）：
  1. 一个 ``llm_json`` 调用：对当前报告做对抗性攻击并给出收敛结论；
  2. emit **既有 ``arbitration`` 事件类型**（利于回滚证据 3「无新增事件类型」），
     payload 形如 {key, action, value, why, source:"enhanced"} —— 相对缺省形态
     **只新增 source 字段**（R7），缺省事件本身不加 source；
  3. 对抗意见并入下一轮 ``review_comments``（仅 on 态；off 态内容不变）。
"""
from __future__ import annotations

from typing import Any

_ENHANCED_MARKER = "对抗收敛"


def adversarial_enabled(ctx) -> bool:
    return bool(ctx.params.get("review_adversarial", False))


def adversarial_pass(ctx, state: dict, verdict: str, comments: str,
                     round_no: int) -> tuple[str, str]:
    """Run ONE adversarial review pass. Caller (make_review_node) only
    invokes this when the switch is on AND verdict == REWORK AND the round
    gate has NOT been exhausted; this function still guards all three and
    passes through untouched otherwise (belt and braces)."""
    max_rounds = int(ctx.params.get("max_review_rounds", 2))
    if not adversarial_enabled(ctx):
        return verdict, comments
    if verdict != "REWORK" or round_no >= max_rounds:
        return verdict, comments

    report = str(state.get("final_report", ""))
    data: dict[str, Any] = ctx.llm_json(
        f"你是对抗评审者。下面是一份待签发的报告与审稿人意见。"
        f"先尽力攻击报告（找反例、漏洞、过强结论），再给出可执行的收敛结论。"
        f"审稿人意见：{comments}\n报告：{report}\n"
        f'只输出 JSON: {{"critique": "攻击要点(一段话)", '
        f'"converge": "收敛后的执行结论(一段话)"}}',
        system="你是对抗收敛评审者,只输出 JSON。")
    critique = str(data.get("critique", ""))
    converge = str(data.get("converge", ""))
    # R7: reuses the EXISTING arbitration event type; payload keeps the
    # default field shape {key, action, value, why} and ONLY ADDS source.
    ctx.emit("arbitration", {"key": None, "action": "pick",
                             "value": converge, "why": critique,
                             "source": "enhanced"})
    merged = (comments + "\n" if comments else "") + \
        f"[{_ENHANCED_MARKER}] {critique} 收敛:{converge}"
    return verdict, merged
