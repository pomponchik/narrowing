"""
Predicate body validation, shared between runtime and the mypy plugin.

Two source paths are supported. B-form lambdas are checked from bytecode
without requiring source: only LOAD_GLOBAL/LOAD_NAME/LOAD_DEREF instructions
are treated as free names; LOAD_ATTR is ignored so attribute access on the
lambda's argument is allowed. E-form strings are parsed and walked as AST.

Both paths apply the same allowlist: the predicate's argument, a fixed set of
builtins, and stdlib names imported by the surrounding module identified by
object identity.
"""
import ast
import dis
import sys
import types
from typing import Callable, Dict, FrozenSet, List, Mapping, Set, Tuple

from denial import InnerNone

from narrowing.common.stdlib_names import STDLIB_MODULE_NAMES

BUILTINS_ALLOWLIST: FrozenSet[str] = frozenset({
    'len', 'bool', 'int', 'float', 'str', 'bytes', 'tuple', 'list', 'dict',
    'set', 'frozenset', 'type', 'isinstance', 'issubclass', 'hasattr',
    'getattr', 'abs', 'min', 'max', 'sum', 'all', 'any', 'round', 'divmod',
    'pow', 'repr', 'ord', 'chr', 'enumerate', 'zip', 'range', 'reversed',
    'sorted', 'next', 'iter',
    'True', 'False', 'None', 'NotImplemented', 'Ellipsis',
})

CO_VARARGS = 0x04
CO_VARKEYWORDS = 0x08


def _is_stdlib_name(name: str, caller_globals: Mapping[str, object]) -> bool:
    """
    Resolve `name` through `caller_globals` to a stdlib module or attribute.

    Returns True if the name binds to a stdlib module itself (`import re`)
    or to a direct attribute of one (`from re import match`). Identity is
    compared by `is`, so reassignment after import (`match = 42`) breaks
    the relation and the name is rejected.
    """
    resolved = caller_globals.get(name, InnerNone)
    if resolved is InnerNone:
        return False
    if isinstance(resolved, types.ModuleType) and resolved.__name__ in STDLIB_MODULE_NAMES:
        return True
    for module_name in STDLIB_MODULE_NAMES:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        attribute: object = getattr(module, name, InnerNone)
        if attribute is resolved:
            return True
    return False


def validate_lambda(function: object) -> None:
    """
    Bytecode-level validation of a lambda predicate.

    Ensures the lambda has exactly one positional argument, no defaults, no
    closure captures, and that all free names (LOAD_GLOBAL/LOAD_NAME/LOAD_DEREF)
    resolve through the lambda's module globals to either the builtin allowlist
    or a stdlib import.
    """
    # `types.LambdaType` is `types.FunctionType` whose stub carries `Any`-typed
    # attributes (`__code__`, `__defaults__`, `__globals__`); narrow once here
    # and treat downstream access as legitimate Any from typeshed.
    if not isinstance(function, types.LambdaType) or function.__name__ != '<lambda>':  # type: ignore[misc]
        raise TypeError(f'narrowing: second argument must be a lambda, got {function!r}')

    code = function.__code__
    if code.co_argcount != 1 or code.co_kwonlyargcount != 0:
        raise TypeError('narrowing: lambda must take exactly one positional argument')
    if code.co_flags & (CO_VARARGS | CO_VARKEYWORDS):
        raise TypeError('narrowing: lambda must take exactly one positional argument')
    if function.__defaults__ is not None or function.__kwdefaults__ is not None:  # type: ignore[misc]
        raise TypeError('narrowing: lambda must not have default values')
    if code.co_freevars:
        raise TypeError('narrowing: lambda must not capture closure variables')

    free_names: Set[str] = set()
    # `dis.Instruction` is a NamedTuple whose fields are typed `Any` in typeshed
    # (see `issue_typeshed.md` - upstream proposal to narrow `argval`).
    for instruction in dis.get_instructions(code):  # type: ignore[misc]
        if instruction.opname in ('LOAD_GLOBAL', 'LOAD_NAME', 'LOAD_DEREF', 'LOAD_CLASSDEREF'):  # type: ignore[misc]
            free_names.add(str(instruction.argval))  # type: ignore[misc]
    function_globals: Mapping[str, object] = function.__globals__
    for name in free_names:
        if name in BUILTINS_ALLOWLIST:
            continue
        if _is_stdlib_name(name, function_globals):
            continue
        raise TypeError(f'narrowing: predicate body references free name {name!r}')


def check_predicate_ast(
    node: ast.AST,
    allowed_argument: str,
    caller_globals: Mapping[str, object],
    extra_locals: FrozenSet[str] = frozenset(),
) -> None:
    """
    Recursively walk an expression AST and reject disallowed free names.

    Used both by the E-form (string predicates) and by the plugin to validate
    predicate bodies before their literal evaluation. The argument name is
    bound in scope, plus walrus targets, comprehension variables, and nested
    lambda parameters.
    """
    scope: Set[str] = {allowed_argument}
    scope.update(extra_locals)
    _walk_ast(node, scope, caller_globals)


def _walk_ast(node: ast.AST, scope: Set[str], caller_globals: Mapping[str, object]) -> None:
    if isinstance(node, ast.Name):
        name = node.id
        if name in scope:
            return
        if name in BUILTINS_ALLOWLIST:
            return
        if _is_stdlib_name(name, caller_globals):
            return
        raise TypeError(f'narrowing: predicate body references free name {name!r}')
    if isinstance(node, ast.Lambda):
        nested_scope = set(scope)
        for argument in node.args.args:
            nested_scope.add(argument.arg)
        if node.args.vararg:
            nested_scope.add(node.args.vararg.arg)
        if node.args.kwarg:
            nested_scope.add(node.args.kwarg.arg)
        for argument in node.args.kwonlyargs:
            nested_scope.add(argument.arg)
        _walk_ast(node.body, nested_scope, caller_globals)
        return
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        comprehension_scope = set(scope)
        for generator in node.generators:
            for target in _names_in_target(generator.target):
                comprehension_scope.add(target)
            _walk_ast(generator.iter, comprehension_scope, caller_globals)
            for condition in generator.ifs:
                _walk_ast(condition, comprehension_scope, caller_globals)
        if isinstance(node, ast.DictComp):
            _walk_ast(node.key, comprehension_scope, caller_globals)
            _walk_ast(node.value, comprehension_scope, caller_globals)
        else:
            _walk_ast(node.elt, comprehension_scope, caller_globals)
        return
    if isinstance(node, ast.NamedExpr):
        for target in _names_in_target(node.target):
            scope.add(target)
        _walk_ast(node.value, scope, caller_globals)
        return
    for child in ast.iter_child_nodes(node):
        _walk_ast(child, scope, caller_globals)


def _names_in_target(target: ast.AST) -> Tuple[str, ...]:
    if isinstance(target, ast.Name):
        return (target.id,)
    # `ast.AST.elts` is only present on Tuple/List nodes; getattr-style access
    # avoids needing a separate isinstance dispatch.
    names: List[str] = []
    for element in target.elts:  # type: ignore[attr-defined, misc]
        names.extend(_names_in_target(element))  # type: ignore[misc]
    return tuple(names)


def make_string_predicate(
    expression_string: str,
    caller_globals: Mapping[str, object],
) -> Callable[[object], object]:
    """
    Compile a string predicate into a callable that takes a value and returns
    the result of evaluating the expression with `x` bound to it.

    Raises TypeError on empty input, syntactically invalid expressions, or
    expressions whose body references disallowed free names.
    """
    if not expression_string.strip():
        raise TypeError('narrowing: predicate string must not be empty')
    try:
        tree = ast.parse(expression_string, mode='eval')
    except SyntaxError:
        # `SyntaxError.msg` differs between Python versions (3.9: "unexpected
        # EOF while parsing"; 3.10+: "invalid syntax"). Drop the version-
        # specific tail to keep the diagnostic stable; mypy already shows the
        # offending expression at its line/column.
        raise TypeError('narrowing: invalid predicate expression') from None

    check_predicate_ast(tree.body, allowed_argument='x', caller_globals=caller_globals)

    code = compile(tree, '<narrowing predicate>', 'eval')
    evaluation_globals: Dict[str, object] = {'__builtins__': __builtins__}
    evaluation_globals.update({
        name: value
        for name, value in caller_globals.items()
        if name in BUILTINS_ALLOWLIST or _is_stdlib_name(name, caller_globals)
    })

    def predicate(value: object) -> object:
        # `eval` returns Any by stub; we accept that - the caller treats the
        # result as truthy/falsy.
        return eval(code, evaluation_globals, {'x': value})  # type: ignore[misc]

    return predicate
