"""pipeline topology: fixed order A→B→C, no dispatch, no arbitration.

Uses the generic ``fixed_agent`` kind; each stage sees the blackboard (which
contains the previous stages' outputs) as its context.
"""


def node_implementations(ctx):
    return {}
