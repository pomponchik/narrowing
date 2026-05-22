"""
Monkey-patch on `mypy.checker.TypeChecker.visit_assignment_stmt`.

Mypy 1.14 has no plugin hook on `AssignmentStmt`. To validate predicate
literals at assignment sites (`x: PositiveInt = -5`) we wrap the existing
method with a post-check that resolves the lvalue's annotation to a record
in the plugin registry and, if the rvalue is a literal, evaluates the
predicate and emits a typing error.

On mypyc-compiled mypy the patched method is bypassed by direct C call
sites; the patch is still installed (Python-level), but `visit_assignment_stmt`
calls go straight to the original. We detect this at load time and emit a
one-time `UserWarning` advising the user to install pure-Python mypy.
"""
import warnings
from typing import Optional

import mypy.checker as _mypy_checker_module
from mypy.checker import TypeChecker
from mypy.nodes import AssignmentStmt
from mypy.types import LiteralType, UnboundType

from narrowing.plugin import PredicateRecord, predicate_registry

_original_visit_assignment_statement = TypeChecker.visit_assignment_stmt


def _patched_visit_assignment_statement(self: TypeChecker, statement: AssignmentStmt) -> None:
    _original_visit_assignment_statement(self, statement)
    try:
        _post_check(self, statement)
    except Exception as exception:  # noqa: BLE001
        warnings.warn(
            f'narrowing: assignment-hook post-check raised {type(exception).__name__}: {exception} - '
            'literal narrowing may not work; check mypy compatibility',
            stacklevel=2,
        )


def _post_check(checker: TypeChecker, statement: AssignmentStmt) -> None:
    """
    Inspect a single-lvalue annotated assignment statement and, if its
    annotation maps to a registered predicate, evaluate it against a
    literal rvalue.
    """
    unanalyzed_type = statement.unanalyzed_type
    if unanalyzed_type is None:
        return
    if len(statement.lvalues) != 1:
        return
    rvalue = statement.rvalue
    record = _lookup_record_for_annotation(checker, unanalyzed_type)
    if record is None or record.predicate is None:
        return
    rvalue_type = checker.expr_checker.accept(rvalue)
    literal: Optional[LiteralType] = None
    if isinstance(rvalue_type, LiteralType):
        literal = rvalue_type
    else:
        last_known_value: object = getattr(rvalue_type, 'last_known_value', None)
        if isinstance(last_known_value, LiteralType):
            literal = last_known_value
    if literal is None:
        return
    value = literal.value
    try:
        passed = bool(record.predicate(value))
    except Exception:  # noqa: BLE001
        return
    if not passed:
        checker.msg.fail(
            f'narrowing: predicate rejected literal {value!r}',
            rvalue,
        )


def _lookup_record_for_annotation(
    checker: TypeChecker,
    unanalyzed_type: object,
) -> Optional[PredicateRecord]:
    """
    Resolve an annotation node to a `PredicateRecord`.

    Bare alias names are resolved via the module symtab or a registry-suffix
    fallback (for aliases defined inside functions/classes). Subscripted
    annotations match an inline E-form by line/column.
    """
    if not isinstance(unanalyzed_type, UnboundType):
        return None
    if not unanalyzed_type.args:
        alias_fullname = _resolve_alias_fullname(checker, unanalyzed_type.name)
        if alias_fullname is not None:
            record = predicate_registry.get(alias_fullname)
            if record is not None:  # pragma: no branch
                return record
    line: object = getattr(unanalyzed_type, 'line', -1)
    column: object = getattr(unanalyzed_type, 'column', -1)
    position_key = f'__e_form__:{line}:{column}'
    record = predicate_registry.get(position_key)
    if record is not None:
        return record
    return None


def _resolve_alias_fullname(checker: TypeChecker, name: str) -> Optional[str]:
    """
    Resolve a short alias name to a key in `predicate_registry`.

    First checks the current module's symbol table; falls back to matching
    registry keys by trailing path segment when the alias was defined inside
    a function or class scope and is therefore absent from the module symtab.
    """
    # `checker.tree` and downstream symbol-table nodes are typed `Any` by the
    # mypy plugin surface; we read documented attributes by name.
    tree: object = getattr(checker, 'tree', None)
    if tree is not None:
        symbol: object = tree.names.get(name)  # type: ignore[attr-defined, misc]
        if symbol is not None:
            node: object = getattr(symbol, 'node', None)
            fullname_attribute_first: object = getattr(node, 'fullname', None)
            fullname_attribute_fallback: object = getattr(node, '_fullname', None)
            fullname_attribute: object = fullname_attribute_first or fullname_attribute_fallback
            if isinstance(fullname_attribute, str) and fullname_attribute in predicate_registry:
                return fullname_attribute
    suffix = '.' + name
    for fullname in predicate_registry:
        if fullname.endswith(suffix):
            return fullname
    return None


def _is_compiled_mypy() -> bool:
    """Return True when the loaded mypy is a mypyc-compiled binary."""
    file_attribute: object = getattr(_mypy_checker_module, '__file__', '')
    return isinstance(file_attribute, str) and file_attribute.endswith(('.so', '.pyd'))


def _apply_patch() -> None:
    # `assignment` arises because our wrapper renames the second parameter
    # (`statement` vs mypy's `s`); the call signature is otherwise identical.
    TypeChecker.visit_assignment_stmt = _patched_visit_assignment_statement  # type: ignore[method-assign, assignment]
    if _is_compiled_mypy():  # pragma: no cover
        warnings.warn(
            'narrowing: mypy is mypyc-compiled - literal narrowing on assignments '
            '(`x: PositiveInt = -5`) is not supported because direct C call sites '
            'bypass the Python-level patch. Install pure-Python mypy with '
            '`pip install --no-binary mypy mypy` for full feature support.',
            stacklevel=2,
        )


_apply_patch()
