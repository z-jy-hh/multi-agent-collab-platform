"""single topology: one agent, one call — no dispatch, no arbitration.

Uses the generic ``single_agent`` kind; the ask node's output already lands
in final_report via builder.make_generic_agent_node.
"""


def node_implementations(ctx):
    return {}
