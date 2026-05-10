"""
Monkey-patches enabling inline `isinstance(value, Narrowed[T, 'expr'])`.

Two patches working together:

1. `SemanticAnalyzer.visit_index_expr` — for `Narrowed[T, 'expr']` we skip
   `analyze_type_application`, which would otherwise interpret the predicate
   string as a type and emit `"Invalid type comment or annotation"` from
   `mypy/typeanal.py:1340` during the semantic-analysis pass (long before any
   plugin hook fires).
2. `ExpressionChecker.visit_call_expr_inner` — for `isinstance(value, Narrowed[T, 'expr'])`
   we substitute the second argument with a synthetic `NameExpr` pointing at
   the base `TypeInfo`. Mypy then treats the call as a regular
   `isinstance(value, int)`-like check, suppressing
   `"Parameterized generics cannot be used with class or instance checks"`
   (`mypy/checkexpr.py:545`) and enabling flow narrowing.

The plugin's `get_function_hook` covers the call form
`isinstance(value, Narrowed(int, ...))` separately and works on mypyc-compiled
mypy; the two patches here are Python-level and bypassed by mypyc. We detect
the compiled binary at load time and emit a one-time `UserWarning`.
"""
import ast
import sys
import warnings
from typing import Callable, Dict, Optional

import mypy.checkexpr as _mypy_checkexpr_module
from mypy.checkexpr import ExpressionChecker
from mypy.nodes import (
    CallExpr,
    Expression,
    FloatExpr,
    IndexExpr,
    IntExpr,
    LambdaExpr,
    NameExpr,
    RefExpr,
    StrExpr,
    TupleExpr,
    TypeInfo,
    UnaryExpr,
)
from mypy.semanal import SemanticAnalyzer
from mypy.types import Instance, Type, TypeType
from mypy.typevars import fill_typevars_with_any

from narrowing.lambda_check import make_string_predicate
from narrowing.plugin import NARROWED_FULLNAME, NARROWED_REEXPORT

_original_visit_call_expr_inner = ExpressionChecker.visit_call_expr_inner
_original_visit_index_expr = SemanticAnalyzer.visit_index_expr


def _is_narrowed_ref(reference: object) -> bool:
    """True when `reference` resolves to the `Narrowed` class (after binding)."""
    if not isinstance(reference, RefExpr):
        return False
    base_fullname: object = getattr(reference, 'fullname', None)
    return base_fullname in (NARROWED_FULLNAME, NARROWED_REEXPORT)


def _patched_visit_index_expr(self: SemanticAnalyzer, expression: IndexExpr) -> None:
    """
    Skip `analyze_type_application` for `Narrowed[T, 'expr']` subscripts.

    The default semantic analyzer would interpret the predicate string as a
    type and fail with `"Invalid type comment or annotation"` from
    `mypy/typeanal.py:1340`. We bind the base first (so we can recognise
    Narrowed by `.fullname`), then for Narrowed-bases we bind the index items
    manually but never set `expression.analyzed`, leaving the IndexExpr as a
    runtime expression for the type-checking phase to substitute later.
    """
    expression.base.accept(self)
    if _is_narrowed_ref(expression.base):
        if isinstance(expression.index, TupleExpr):
            for item in expression.index.items:
                item.accept(self)
        else:
            expression.index.accept(self)
        return
    # Restore default flow for non-Narrowed subscripts. The original
    # visit_index_expr re-binds the base, but doing it twice is harmless and
    # cheaper than reimplementing its full logic here.
    _original_visit_index_expr(self, expression)


def _patched_visit_call_expr_inner(
    self: ExpressionChecker,
    expression: CallExpr,
    allow_none_return: bool = False,
) -> Type:
    try:
        if (
            isinstance(expression.callee, NameExpr)
            and expression.callee.fullname in ('builtins.isinstance', 'builtins.issubclass')
            and len(expression.args) == 2
        ):
            _maybe_emit_literal_rejection(self, expression)
            substituted = _maybe_substitute_argument(self, expression.args[1])
            if substituted is not None:
                # Leave the substitution in place: mypy's `find_isinstance_check`
                # runs later (during `visit_if_stmt`) and looks up the type of
                # `e.args[1]` to compute narrowing. Restoring the original
                # IndexExpr would lose the stored synthetic type and crash
                # with KeyError.
                expression.args[1] = substituted
    except Exception as exception:  # noqa: BLE001
        warnings.warn(
            f'narrowing: isinstance-hook substitution raised {type(exception).__name__}: {exception} — '
            'inline narrowing for `Narrowed[T, "expr"]` may not work; check mypy compatibility',
            stacklevel=2,
        )
    return _original_visit_call_expr_inner(self, expression, allow_none_return)


def _maybe_emit_literal_rejection(checker: ExpressionChecker, expression: CallExpr) -> None:
    """
    If `expression` is `isinstance(LITERAL, Narrowed(...))` and the predicate
    rejects the literal, emit a mypy error. Silent skip on every other shape
    (non-literal value, unresolvable predicate, predicate-raises).
    """
    literal_value = _extract_literal_value(expression.args[0])
    if literal_value is _UNSET:
        return
    predicate = _extract_call_form_predicate(checker, expression.args[1])
    if predicate is None:
        return
    try:
        passed = bool(predicate(literal_value))
    except Exception:  # noqa: BLE001
        return
    if not passed:
        checker.chk.msg.fail(
            f'narrowing: predicate rejected literal {literal_value!r}',
            expression,
        )


_UNSET: object = object()


def _extract_literal_value(argument: Expression) -> object:
    """Return the Python literal value of `argument` or `_UNSET` if not a literal."""
    if isinstance(argument, IntExpr):
        return argument.value
    if isinstance(argument, StrExpr):
        return argument.value
    if isinstance(argument, FloatExpr):
        return argument.value
    if isinstance(argument, UnaryExpr) and argument.op == '-':
        inner = argument.expr
        if isinstance(inner, IntExpr):
            return -inner.value
        if isinstance(inner, FloatExpr):
            return -inner.value
    return _UNSET


def _extract_call_form_predicate(
    checker: ExpressionChecker,
    argument: Expression,
) -> Optional[Callable[[object], object]]:
    """
    For `Narrowed(base, lambda x: ...)` reconstruct the lambda predicate from
    the module source. For `Narrowed[base, "expr"]` compile the predicate
    string directly. Returns None on shape mismatch or unavailable source.
    """
    if isinstance(argument, IndexExpr):
        return _extract_subscript_form_predicate(checker, argument)
    predicate_argument = _extract_lambda_from_call(argument)
    if predicate_argument is None:
        return None
    return _compile_lambda_from_source(checker, predicate_argument)


def _extract_lambda_from_call(argument: Expression) -> Optional[LambdaExpr]:
    """Pull the lambda predicate out of a `Narrowed(base, lambda x: ...)` call."""
    if not isinstance(argument, CallExpr):
        return None
    callee = argument.callee
    if not isinstance(callee, RefExpr):
        return None
    callee_fullname: object = getattr(callee, 'fullname', None)
    if callee_fullname not in (NARROWED_FULLNAME, NARROWED_REEXPORT):
        return None
    if len(argument.args) != 2:
        return None
    predicate_argument = argument.args[1]
    if not isinstance(predicate_argument, LambdaExpr):
        return None
    return predicate_argument


def _extract_subscript_form_predicate(
    checker: ExpressionChecker,
    argument: IndexExpr,
) -> Optional[Callable[[object], object]]:
    """For `Narrowed[T, 'expr']` compile the predicate-string into a callable."""
    base = argument.base
    if not isinstance(base, RefExpr):
        return None
    base_fullname: object = getattr(base, 'fullname', None)
    if base_fullname not in (NARROWED_FULLNAME, NARROWED_REEXPORT):
        return None
    if not isinstance(argument.index, TupleExpr) or len(argument.index.items) != 2:
        return None
    expression_node = argument.index.items[1]
    if not isinstance(expression_node, StrExpr):
        return None
    caller_globals = _build_caller_globals(checker)
    try:
        return make_string_predicate(expression_node.value, caller_globals)
    except TypeError:
        return None


def _build_caller_globals(checker: ExpressionChecker) -> Dict[str, object]:
    """
    Reconstruct the calling module's globals from `chk.tree.names` so that
    string predicates can reference imported stdlib modules (`re.match(...)`).

    Each `SymbolTableNode.node` is examined: stdlib modules and their
    attributes are resolved via `sys.modules`. Anything we can't resolve
    cleanly is skipped — `make_string_predicate` falls back to silent reject
    when a name doesn't resolve.
    """
    chk_object: object = getattr(checker, 'chk', None)
    module_tree: object = getattr(chk_object, 'tree', None)
    names_attribute: object = getattr(module_tree, 'names', None)
    if not isinstance(names_attribute, dict):
        return {}
    result: Dict[str, object] = {}
    for symbol_name, symbol_table_node in names_attribute.items():
        if not isinstance(symbol_name, str):
            continue
        node: object = getattr(symbol_table_node, 'node', None)
        fullname_attribute: object = getattr(node, 'fullname', None)
        if not isinstance(fullname_attribute, str):
            continue
        # Try to resolve as a stdlib module reference (`import re`).
        # `sys.modules` is typed as `Mapping[str, ModuleType]` but values are
        # accessed as Any per stub; the `is not None` guard keeps narrowing.
        module_object: object = sys.modules.get(fullname_attribute)
        if module_object is not None:
            result[symbol_name] = module_object
            continue
        # Try to resolve as `from X import Y` where Y is an attribute of stdlib X.
        if '.' in fullname_attribute:
            parent_name, attribute_name = fullname_attribute.rsplit('.', 1)
            parent_module: object = sys.modules.get(parent_name)
            if parent_module is not None and hasattr(parent_module, attribute_name):
                result[symbol_name] = getattr(parent_module, attribute_name)  # type: ignore[misc]
    return result


_module_ast_cache: Dict[str, Optional[ast.Module]] = {}
_predicate_cache: Dict[int, Optional[Callable[[object], object]]] = {}


def _compile_lambda_from_source(
    checker: ExpressionChecker,
    predicate_argument: LambdaExpr,
) -> Optional[Callable[[object], object]]:
    """
    Re-parse the module source and compile the lambda body into a callable.

    Caches per module path (`_module_ast_cache`) and per `LambdaExpr` identity
    (`_predicate_cache`) to avoid re-reading and re-parsing on every isinstance
    call mypy visits.
    """
    cache_key = id(predicate_argument)
    if cache_key in _predicate_cache:
        return _predicate_cache[cache_key]
    result = _compile_lambda_uncached(checker, predicate_argument)
    _predicate_cache[cache_key] = result
    return result


def _compile_lambda_uncached(
    checker: ExpressionChecker,
    predicate_argument: LambdaExpr,
) -> Optional[Callable[[object], object]]:
    tree_object: object = getattr(checker, 'chk', None)
    module_tree: object = getattr(tree_object, 'tree', None)
    ast_tree = _load_module_ast(module_tree)
    if ast_tree is None:
        return None
    target_line = predicate_argument.line
    target_column = predicate_argument.column
    candidate: Optional[ast.Lambda] = None
    for node in ast.walk(ast_tree):
        if isinstance(node, ast.Lambda) and node.lineno == target_line:
            if (
                candidate is None
                or abs(node.col_offset - target_column) < abs(candidate.col_offset - target_column)
            ):
                candidate = node
    if candidate is None or len(candidate.args.args) != 1:
        return None
    argument_name = candidate.args.args[0].arg
    code = compile(ast.Expression(candidate.body), '<narrowing predicate>', 'eval')
    evaluation_globals: Dict[str, object] = {'__builtins__': __builtins__}

    def predicate(value: object) -> object:
        return eval(code, evaluation_globals, {argument_name: value})  # type: ignore[misc]

    return predicate


def _load_module_ast(module_tree: object) -> Optional[ast.Module]:
    """Read + parse the module source once per path; cache the result."""
    path_attribute: object = getattr(module_tree, 'path', None)
    if not isinstance(path_attribute, str) or not path_attribute:
        return None
    if path_attribute in _module_ast_cache:
        return _module_ast_cache[path_attribute]
    source_attribute: object = getattr(module_tree, 'source', None)
    source: Optional[str] = source_attribute if isinstance(source_attribute, str) else None
    if source is None:
        try:
            with open(path_attribute) as file_handle:
                source = file_handle.read()
        except OSError:
            _module_ast_cache[path_attribute] = None
            return None
    try:
        parsed = ast.parse(source)
    except SyntaxError:
        _module_ast_cache[path_attribute] = None
        return None
    _module_ast_cache[path_attribute] = parsed
    return parsed


def _maybe_substitute_argument(checker: ExpressionChecker, argument: Expression) -> Optional[NameExpr]:
    """Return a synthetic NameExpr replacing a `Narrowed[T, "expr"]` subscript, or None."""
    if not isinstance(argument, IndexExpr):
        return None
    base = argument.base
    if not isinstance(base, RefExpr):
        return None
    base_fullname: object = getattr(base, 'fullname', None)
    if base_fullname not in (NARROWED_FULLNAME, NARROWED_REEXPORT):
        return None
    base_typeinfo = _extract_base_typeinfo_from_index(argument.index)
    if base_typeinfo is None:
        return None
    if base_typeinfo.type_vars:
        filled = fill_typevars_with_any(base_typeinfo)
        if not isinstance(filled, Instance):
            return None
        synthetic_type: Type = TypeType(filled)
    else:
        synthetic_type = TypeType(Instance(base_typeinfo, []))
    synthetic = NameExpr(base_typeinfo.name)
    synthetic.fullname = base_typeinfo.fullname
    synthetic.node = base_typeinfo
    synthetic.kind = base.kind
    synthetic.line = argument.line
    synthetic.column = argument.column
    checker.chk.store_type(synthetic, synthetic_type)
    return synthetic


def _extract_base_typeinfo_from_index(index_expression: Expression) -> Optional[TypeInfo]:
    if isinstance(index_expression, TupleExpr) and index_expression.items:
        first = index_expression.items[0]
    else:
        first = index_expression
    if isinstance(first, RefExpr) and isinstance(first.node, TypeInfo):
        return first.node
    return None


def _is_compiled_mypy() -> bool:
    """Return True when the loaded mypy is a mypyc-compiled binary."""
    file_attribute: object = getattr(_mypy_checkexpr_module, '__file__', '')
    return isinstance(file_attribute, str) and file_attribute.endswith(('.so', '.pyd'))


def _apply_patch() -> None:
    # `assignment` arises because our wrappers rename mypy's parameter names
    # (`expression`/`expr` vs mypy's `e`/`expr`); call signatures are otherwise
    # identical.
    ExpressionChecker.visit_call_expr_inner = _patched_visit_call_expr_inner  # type: ignore[method-assign, assignment]
    SemanticAnalyzer.visit_index_expr = _patched_visit_index_expr  # type: ignore[method-assign, assignment]
    if _is_compiled_mypy():  # pragma: no cover
        warnings.warn(
            'narrowing: mypy is mypyc-compiled — inline isinstance for `Narrowed[T, "expr"]` '
            'subscript form (and literal-validation in inline isinstance) is not supported '
            'because direct C call sites bypass the Python-level patch. Install pure-Python '
            'mypy with `pip install --no-binary mypy mypy` for full feature support.',
            stacklevel=2,
        )


_apply_patch()
