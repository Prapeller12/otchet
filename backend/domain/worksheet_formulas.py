"""Bounded, side-effect-free worksheet expressions over exact Decimal values."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal, DecimalException, localcontext
from functools import lru_cache


class FormulaError(ValueError):
    pass


@lru_cache(maxsize=256)
def parse_formula(expression: str) -> ast.Expression:
    if len(expression) > 500:
        raise FormulaError("Формула длиннее 500 символов")
    expression = expression.strip().removeprefix("=").strip().replace(";", ",")
    expression = re.sub(r"(?<![<>=!])=(?!=)", "==", expression.replace("<>", "!="))
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise FormulaError("Неверный синтаксис формулы") from exc
    nodes = list(ast.walk(tree))
    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Compare,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.USub,
        ast.UAdd,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
    )
    if len(nodes) > 120 or any(not isinstance(node, allowed) for node in nodes):
        raise FormulaError("Недопустимая или слишком сложная формула")
    for node in nodes:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise FormulaError("В формулах разрешены только числа и коды показателей")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in {
                "SUM",
                "MIN",
                "MAX",
                "IF",
                "ROUNDDOWN",
                "CUM",
            }:
                raise FormulaError("Неизвестная функция")
            if node.keywords or not node.args or len(node.args) > 50:
                raise FormulaError("Неверные аргументы функции")
            expected = {"IF": 3, "ROUNDDOWN": 2, "CUM": 1}.get(node.func.id)
            if expected is not None and len(node.args) != expected:
                raise FormulaError("Неверное число аргументов функции")
            if node.func.id == "CUM" and not isinstance(node.args[0], ast.Name):
                raise FormulaError("CUM принимает код показателя")
        if isinstance(node, ast.Compare) and len(node.ops) != 1:
            raise FormulaError("Цепочки сравнений не поддерживаются")
    return tree


def evaluate_formula(
    expression: str,
    resolve: Callable[[str], Decimal | None],
    cumulative: Callable[[str], Decimal | None],
) -> Decimal | None:
    tree = parse_formula(expression)

    def visit(node: ast.AST) -> Decimal | None:
        if isinstance(node, ast.Constant):
            # ast.unparse would round long decimals; recover the original literal.
            source = expression.strip().removeprefix("=").strip().replace(";", ",")
            source = re.sub(r"(?<![<>=!])=(?!=)", "==", source.replace("<>", "!="))
            value = Decimal(ast.get_source_segment(source, node) or str(node.value))
            if (
                not value.is_finite()
                or value.copy_abs() > Decimal("1e100")
                or (value != 0 and value.copy_abs() < Decimal("1e-100"))
            ):
                raise FormulaError("Число вне допустимого диапазона")
            return Decimal(0) if value == 0 else value
        if isinstance(node, ast.Name):
            return resolve(node.id)
        if isinstance(node, ast.UnaryOp):
            operand = visit(node.operand)
            return (
                None if operand is None else -operand if isinstance(node.op, ast.USub) else operand
            )
        if isinstance(node, (ast.BinOp, ast.Compare)):
            left = visit(node.left)
            right = visit(node.right if isinstance(node, ast.BinOp) else node.comparators[0])
            if left is None or right is None:
                return None
            op = node.op if isinstance(node, ast.BinOp) else node.ops[0]
            if isinstance(op, ast.Add):
                return left + right
            if isinstance(op, ast.Sub):
                return left - right
            if isinstance(op, ast.Mult):
                return left * right
            if isinstance(op, ast.Div):
                if right == 0:
                    raise FormulaError("Деление на ноль")
                return left / right
            comparisons = {
                ast.Eq: left == right,
                ast.NotEq: left != right,
                ast.Lt: left < right,
                ast.LtE: left <= right,
                ast.Gt: left > right,
                ast.GtE: left >= right,
            }
            if isinstance(op, ast.cmpop):
                return Decimal(int(comparisons[type(op)]))
            raise FormulaError("Недопустимый оператор")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name == "CUM" and isinstance(node.args[0], ast.Name):
                return cumulative(node.args[0].id)
            if name == "IF":
                condition = visit(node.args[0])
                return None if condition is None else visit(node.args[1 if condition else 2])
            values = [visit(arg) for arg in node.args]
            if any(value is None for value in values):
                return None
            numbers = [value for value in values if value is not None]
            if name == "SUM":
                return sum(numbers, Decimal(0))
            if name == "MIN":
                return min(numbers)
            if name == "MAX":
                return max(numbers)
            digits = numbers[1]
            if digits != digits.to_integral_value() or not -20 <= digits <= 20:
                raise FormulaError("Точность ROUNDDOWN должна быть целым числом от -20 до 20")
            return numbers[0].quantize(Decimal(1).scaleb(int(-digits)), rounding=ROUND_DOWN)
        raise FormulaError("Недопустимое выражение")

    try:
        with localcontext() as context:
            context.prec = 120
            result = visit(tree.body)
            if result is not None and (
                not result.is_finite()
                or result.copy_abs() > Decimal("1e100")
                or (result != 0 and result.copy_abs() < Decimal("1e-100"))
            ):
                raise FormulaError("Результат вне допустимого диапазона")
            return Decimal(0) if result == 0 else result
    except DecimalException as exc:
        raise FormulaError("Ошибка десятичного расчёта") from exc
