"""debate topology: pro ↔ con rounds with a judge; dual termination
(max_rounds AND judge verdict, 任务书 三·C.4).

Kind overrides: ``kind::debater`` and ``kind::judge`` replace the generic
agent node because rounds/verdict must live in graph state. Each debater
publishes its argument under key ``argument::<side>::r<round>`` with the
round as op version index — op_ids stay deterministic per round (C6).
"""


from langgraph.graph import END


def node_implementations(ctx):
    return {
        "kind::debater": _make_debater(ctx),
        "kind::judge": _make_judge(ctx),
        "route::judge_gate": _judge_gate(ctx),
    }


def _make_debater(ctx):
    def factory(node_cfg: dict):
        side = "pro" if node_cfg["id"] == "pro_turn" else "con"
        agent_id = node_cfg["agent"]

        def debater_turn(state: dict) -> dict:
            round_no = int(state.get("debate_round", 0)) + 1
            args = state.get("debate_arguments") or {}
            opponent = args.get("con" if side == "pro" else "pro", "(开场陈词)")
            prompt = ctx.prompt_for(
                agent_id, task=ctx.task, opponent_argument=opponent,
            ) + f"\n当前轮数:第 {round_no} 轮"
            ctx.emit("agent_card", ctx.agent_card(agent_id), agent_id=agent_id)
            result = ctx.runtime.execute(agent_id, prompt, {})
            entry = ctx.publish_entry(agent_id, f"{side}_turn",
                                      f"argument::{side}::r{round_no}",
                                      result.output, version_index=round_no)
            ctx.emit("agent_output",
                     {"agent_id": agent_id, "side": side, "round": round_no,
                      "output_chars": len(result.output)}, agent_id=agent_id)
            new_args = dict(args)
            new_args[side] = result.output
            return {"debate_round": round_no, "debate_arguments": new_args,
                    "worker_notes": [f"[r{round_no}|{side}|{agent_id}] {result.output}"],
                    "blackboard": [entry] if entry else []}

        return debater_turn

    return factory


def _make_judge(ctx):
    def factory(node_cfg: dict):
        judge_id = node_cfg["agent"]

        def judge_turn(state: dict) -> dict:
            round_no = int(state.get("debate_round", 0))
            args = state.get("debate_arguments") or {}
            prompt = ctx.prompt_for(
                judge_id,
                pro_argument=args.get("pro", "(无)"),
                con_argument=args.get("con", "(无)"),
            ) + f"\n当前轮数:第 {round_no} 轮 / 上限 {ctx.params.get('max_rounds', 4)} 轮"
            data = ctx.llm_json(prompt, system="你是辩论裁判,只输出 JSON。")
            verdict = str(data.get("verdict", "CONTINUE"))
            conclusion = str(data.get("conclusion", ""))
            entry = ctx.publish_entry(judge_id, "judge_turn", "conclusion",
                                      conclusion, version_index=round_no)
            ctx.emit("judge", {"round": round_no, "verdict": verdict,
                               "conclusion": conclusion}, agent_id=judge_id)
            return {"review_verdict": verdict, "final_report": conclusion,
                    "status": "concluded" if verdict == "CONCLUDED" else "debating",
                    "blackboard": [entry] if entry else []}

        return judge_turn

    return factory


def _judge_gate(ctx):
    def route(state: dict):
        # dual termination: judge verdict OR rounds exhausted (C18)
        if state.get("review_verdict") == "CONCLUDED":
            return END
        if int(state.get("debate_round", 0)) >= int(ctx.params.get("max_rounds", 4)):
            return END
        return "pro_turn"

    return route
