"""
`conftest` for the typing tests.

`pytest-mypy-testing` discovers `@pytest.mark.mypy_testing` functions with an
AST pass that keeps only decorator *names*: the arguments of
`@pytest.mark.skipif(condition, reason=...)` are dropped. pytest then sees a
`skipif` marker carrying no condition and treats it as an *unconditional*
skip, so a plain `@pytest.mark.skipif` would skip the mypy test on every
Python version regardless of its condition.

`pytest_collection_modifyitems` below re-reads the `skipif` decorators from
the AST, evaluates their conditions, and swaps that bogus unconditional skip
for a real one — keeping `@pytest.mark.skipif(...)` behaving as written.
"""
import ast
import sys

import pytest


def pytest_collection_modifyitems(items):
    """Make `@pytest.mark.skipif` honour its condition on `mypy_testing` items."""
    for item in items:
        mypy_item = getattr(item, 'mypy_item', None)
        if mypy_item is None or mypy_item.func_node is None:
            continue
        skipif_decorators = [
            decorator
            for decorator in mypy_item.func_node.decorator_list
            if _is_skipif(decorator)
        ]
        if not skipif_decorators:
            continue
        # Drop the condition-less `skipif` marker pytest-mypy-testing attached
        # (see the module docstring) and re-apply each condition for real.
        item.own_markers = [mark for mark in item.own_markers if mark.name != 'skipif']
        for decorator in skipif_decorators:
            if _condition_holds(decorator):
                item.add_marker(pytest.mark.skip(reason=_reason(decorator)))


def _is_skipif(decorator):
    """Return True when `decorator` is a `*.skipif(...)` call node."""
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == 'skipif'
    )


def _condition_holds(decorator):
    """Evaluate the first positional argument of a `skipif` decorator."""
    code = compile(ast.Expression(decorator.args[0]), '<skipif condition>', 'eval')
    return bool(eval(code, {'sys': sys}))


def _reason(decorator):
    """Return the `reason=` keyword of a `skipif` decorator, or a default."""
    for keyword in decorator.keywords:
        if keyword.arg == 'reason' and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return 'skipif condition is true'
