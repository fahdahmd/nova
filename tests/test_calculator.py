"""The calculator must evaluate arithmetic and nothing else."""

from __future__ import annotations

import math

import pytest

from nova.tools.calculator import CalculatorError, evaluate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2+2", 4),
        ("(19*37)/4", 175.75),
        ("7 % 3", 1),
        ("-7 // 2", -4),
        ("2**10", 1024),
        ("round(3.14159, 2)", 3.14),
        ("max(3, 7, 2)", 7),
        ("min(3, 7, 2)", 2),
        ("abs(-12.5)", 12.5),
        ("factorial(5)", 120),
        ("gcd(12, 18)", 6),
        ("floor(2.9)", 2),
        ("ceil(2.1)", 3),
    ],
)
def test_evaluates_plain_arithmetic(expression: str, expected: float) -> None:
    assert evaluate(expression) == pytest.approx(expected)


def test_supports_constants_and_functions() -> None:
    assert evaluate("sqrt(2)*pi") == pytest.approx(math.sqrt(2) * math.pi)
    assert evaluate("log10(1000)") == pytest.approx(3)
    assert evaluate("degrees(pi)") == pytest.approx(180)


@pytest.mark.parametrize(
    "expression",
    [
        '__import__("os").system("echo pwned")',
        "open('/etc/passwd')",
        "eval('1+1')",
        "exec('x=1')",
        "(lambda: 1)()",
        "().__class__",
        "globals()",
        "x + 1",
        "[1, 2, 3]",
        "{'a': 1}",
        "1 if True else 2",
        "[i for i in range(3)]",
        "print(1)",
        "1; import os",
        "a := 2",
        "sqrt.__class__",
    ],
)
def test_refuses_anything_that_is_not_arithmetic(expression: str) -> None:
    with pytest.raises(CalculatorError):
        evaluate(expression)


def test_refuses_attribute_access_and_dunders() -> None:
    with pytest.raises(CalculatorError):
        evaluate("(1).__class__.__bases__")


def test_reports_division_by_zero_clearly() -> None:
    with pytest.raises(CalculatorError, match="division by zero"):
        evaluate("1/0")
    with pytest.raises(CalculatorError, match="division by zero"):
        evaluate("5 % 0")


def test_caps_exponents_so_it_cannot_hang() -> None:
    with pytest.raises(CalculatorError, match="exponents above"):
        evaluate("9**9**9")
    with pytest.raises(CalculatorError, match="too large"):
        evaluate("99999999**9")


def test_rejects_non_finite_results() -> None:
    with pytest.raises(CalculatorError, match="not a finite number"):
        evaluate("1e308*10")
    with pytest.raises(CalculatorError, match="not a finite number"):
        evaluate("inf")


def test_rejects_empty_and_overlong_input() -> None:
    with pytest.raises(CalculatorError, match="empty"):
        evaluate("   ")
    with pytest.raises(CalculatorError, match="longer than"):
        evaluate("1+" * 150 + "1")


def test_factorial_is_bounded() -> None:
    with pytest.raises(CalculatorError, match="0 to 170"):
        evaluate("factorial(5000)")
    with pytest.raises(CalculatorError, match="0 to 170"):
        evaluate("factorial(-3)")


def test_booleans_are_not_numbers() -> None:
    with pytest.raises(CalculatorError, match="only numbers"):
        evaluate("True + 1")


def test_keyword_arguments_are_refused() -> None:
    with pytest.raises(CalculatorError, match="keyword arguments"):
        evaluate("round(1.234, ndigits=2)")
