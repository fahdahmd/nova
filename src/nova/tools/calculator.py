"""Safe arithmetic evaluation.

The expression is parsed to an AST and walked by an explicit allow-list of node
types. ``eval``/``exec`` are never used, no names resolve except a fixed set of
maths functions and constants, and results are bounded so a tiny expression
cannot burn CPU or memory.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import Any, ClassVar, Final

from pydantic import BaseModel, Field

from nova.tools.base import RiskTier, Tool, ToolContext, ToolResult

MAX_EXPRESSION_CHARS: Final = 200
MAX_POWER_EXPONENT: Final = 128
MAX_ABS_RESULT: Final = 1e200

_BINARY_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": lambda *values: math.fsum(values),
    "sqrt": math.sqrt,
    "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "exp": math.exp,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "degrees": math.degrees,
    "radians": math.radians,
    "floor": math.floor,
    "ceil": math.ceil,
    "trunc": math.trunc,
    "factorial": math.factorial,
    "gcd": math.gcd,
    "hypot": math.hypot,
}

_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}


class CalculatorError(ValueError):
    """Raised when an expression is unsupported or cannot be evaluated."""


def evaluate(expression: str) -> float | int:
    """Evaluate an arithmetic expression safely."""
    text = (expression or "").strip()
    if not text:
        raise CalculatorError("the expression is empty")
    if len(text) > MAX_EXPRESSION_CHARS:
        raise CalculatorError(f"the expression is longer than {MAX_EXPRESSION_CHARS} characters")

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"could not parse the expression: {exc.msg}") from exc

    result = _eval_node(tree.body)
    if isinstance(result, bool) or not isinstance(result, int | float):
        raise CalculatorError("the expression did not produce a number")
    if isinstance(result, float) and (math.isnan(result) or math.isinf(result)):
        raise CalculatorError("the result is not a finite number")
    if abs(result) > MAX_ABS_RESULT:
        raise CalculatorError("the result is too large")
    return result


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise CalculatorError("only numbers are allowed")
        return node.value

    if isinstance(node, ast.BinOp):
        handler = _BINARY_OPS.get(type(node.op))
        if handler is None:
            raise CalculatorError(f"operator {type(node.op).__name__} is not allowed")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow):
            _guard_power(left, right)
        try:
            return handler(left, right)
        except ZeroDivisionError as exc:
            raise CalculatorError("division by zero") from exc
        except (OverflowError, ValueError) as exc:
            raise CalculatorError(str(exc)) from exc

    if isinstance(node, ast.UnaryOp):
        handler_unary = _UNARY_OPS.get(type(node.op))
        if handler_unary is None:
            raise CalculatorError(f"operator {type(node.op).__name__} is not allowed")
        return handler_unary(_eval_node(node.operand))

    if isinstance(node, ast.Call):
        return _eval_call(node)

    if isinstance(node, ast.Name):
        if node.id.lower() in _CONSTANTS:
            return _CONSTANTS[node.id.lower()]
        raise CalculatorError(f"unknown name {node.id!r}")

    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(element) for element in node.elts)

    raise CalculatorError(f"{type(node).__name__} expressions are not allowed")


def _eval_call(node: ast.Call) -> Any:
    if not isinstance(node.func, ast.Name):
        raise CalculatorError("only direct function calls are allowed")
    name = node.func.id.lower()
    function = _FUNCTIONS.get(name)
    if function is None:
        raise CalculatorError(f"unknown function {node.func.id!r}")
    if node.keywords:
        raise CalculatorError("keyword arguments are not supported")
    if len(node.args) > 8:
        raise CalculatorError("too many arguments")

    arguments = [_eval_node(argument) for argument in node.args]
    if name == "factorial":
        value = arguments[0] if arguments else None
        if not isinstance(value, int) or value < 0 or value > 170:
            raise CalculatorError("factorial accepts whole numbers from 0 to 170")
    try:
        return function(*arguments)
    except ZeroDivisionError as exc:
        raise CalculatorError("division by zero") from exc
    except (TypeError, ValueError, OverflowError) as exc:
        raise CalculatorError(f"{name}: {exc}") from exc


def _guard_power(base: Any, exponent: Any) -> None:
    """Stop 9**9**9 from locking up the process."""
    if not isinstance(exponent, int | float):
        raise CalculatorError("exponents must be numbers")
    if abs(exponent) > MAX_POWER_EXPONENT:
        raise CalculatorError(f"exponents above {MAX_POWER_EXPONENT} are not allowed")
    if isinstance(base, int | float) and abs(base) > 1e6 and abs(exponent) > 8:
        raise CalculatorError("that power is too large to compute")


class CalculatorArgs(BaseModel):
    model_config = {"extra": "forbid"}

    expression: str = Field(
        min_length=1,
        max_length=MAX_EXPRESSION_CHARS,
        description="Arithmetic expression, e.g. '(19*37)/4' or 'sqrt(2)*pi'.",
    )


class Calculator(Tool):
    name: ClassVar[str] = "calculator"
    description: ClassVar[str] = "Evaluate an arithmetic expression. Use it for any real maths."
    args_model: ClassVar[type[BaseModel]] = CalculatorArgs
    risk: ClassVar[RiskTier] = RiskTier.SAFE

    async def run(self, args: CalculatorArgs, context: ToolContext) -> ToolResult:
        del context
        try:
            value = evaluate(args.expression)
        except CalculatorError as exc:
            return ToolResult.failure(str(exc))
        rendered = f"{value:.10g}" if isinstance(value, float) else str(value)
        return ToolResult(
            ok=True,
            content=f"{args.expression} = {rendered}",
            data={"expression": args.expression, "result": rendered},
        )
