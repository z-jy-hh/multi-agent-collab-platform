"""Safe arithmetic calculator tool (no eval of arbitrary code)."""

from __future__ import annotations

import ast
import operator

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_ALLOWED_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}


def calc(expression: str) -> float:
    """Evaluate a pure arithmetic expression (digits, + - * / ** % parentheses)."""

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            return _ALLOWED_BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(node.op)](_eval(node.operand))
        raise ValueError(f"unsupported expression element: {ast.dump(node)[:60]}")

    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute, ast.Call, ast.Lambda)):
            raise ValueError("names/calls are not allowed in calc")
    return _eval(tree)


if __name__ == "__main__":
    import sys

    print(calc(" ".join(sys.argv[1:]) or "1+2*3"))
