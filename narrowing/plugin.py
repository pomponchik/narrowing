"""
Mypy plugin for `narrowing`.

Two surface forms register a TypeAlias targeting the underlying T:

    PositiveInt = Narrowed(int, lambda x: x > 0)   # via get_dynamic_class_hook
    PositiveInt = Narrowed[int, 'x > 0']           # via get_type_analyze_hook

Both populate `predicate_registry` with a `PredicateRecord` carrying the
reconstructed callable. The assignment-hook reads that registry to evaluate
predicates against literal rvalues at type-check time.
"""
import ast
from typing import Any, Callable, Dict, Optional
from typing import Type as TypingType

from mypy.nodes import (
    GDEF,
    CallExpr,
    LambdaExpr,
    RefExpr,
    StrExpr,
    SymbolTableNode,
    TypeAlias,
    TypeInfo,
)
from mypy.plugin import (
    AnalyzeTypeContext,
    DynamicClassDefContext,
    FunctionContext,
    Plugin,
)
from mypy.types import (
    AnyType,
    Instance,
    RawExpressionType,
    Type,
    TypeOfAny,
    TypeType,
    UnboundType,
)
from mypy.typevars import fill_typevars_with_any

from narrowing.lambda_check import check_predicate_ast

NARROWED_FULLNAME = 'narrowing.narrowed.Narrowed'
NARROWED_REEXPORT = 'narrowing.Narrowed'


class PredicateRecord:
    """
    A reconstructed predicate associated with one alias. `predicate` may be
    None when the source could not be located for B-form lambdas.
    """

    __slots__ = ('base_fullname', 'predicate', 'source_repr')

    def __init__(
        self,
        base_fullname: str,
        source_repr: str,
        predicate: Optional[Callable[[object], object]],
    ) -> None:
        self.base_fullname = base_fullname
        self.source_repr = source_repr
        self.predicate = predicate


predicate_registry: Dict[str, PredicateRecord] = {}


def _resolve_base_typeinfo(base_node: object) -> Optional[TypeInfo]:
    """Resolve a NameExpr/MemberExpr-like node to a TypeInfo, if available."""
    if isinstance(base_node, RefExpr):
        node = base_node.node
        if isinstance(node, TypeInfo):
            return node
    return None


def _register_alias(api: Any, name: str, target: Type) -> str:  # type: ignore[misc]
    """
    Register `name` in the current module's symbol table as a TypeAlias on
    `target`, returning the alias's full name.

    `api` is mypy's `SemanticAnalyzerPluginInterface`, which is structurally
    typed and only available as an opaque object inside plugin contexts; we
    accept Any here and access the documented attributes by name.
    """
    fullname: str = f'{api.cur_mod_id}.{name}'  # type: ignore[misc]
    alias = TypeAlias(target, fullname, line=-1, column=-1)
    api.add_symbol_table_node(name, SymbolTableNode(GDEF, alias))  # type: ignore[misc]
    return fullname


def call_form_dynamic_class_hook(context: DynamicClassDefContext) -> None:
    """
    Handle ``PositiveInt = Narrowed(int, lambda x: x > 0)``.

    Validates the call shape, resolves the base type, registers the alias,
    and stores a reconstructed predicate in `predicate_registry` for the
    assignment-hook to consume.
    """
    call = context.call
    if not isinstance(call, CallExpr):  # pragma: no cover
        return
    fallback_target: Type = AnyType(TypeOfAny.from_error)

    if len(call.args) != 2:
        context.api.fail(
            f'narrowing: expected Narrowed(T, predicate), got {len(call.args)} positional argument(s)',
            call,
        )
        _register_alias(context.api, context.name, fallback_target)
        return
    base_argument, predicate_argument = call.args

    if isinstance(predicate_argument, StrExpr):
        context.api.fail(
            'narrowing: string predicate is only valid in subscript form Narrowed[T, "expr"]',
            call,
        )
        _register_alias(context.api, context.name, fallback_target)
        return
    if not isinstance(predicate_argument, LambdaExpr):
        context.api.fail('narrowing: second argument must be a lambda', call)
        _register_alias(context.api, context.name, fallback_target)
        return
    if len(predicate_argument.arg_names) != 1:
        context.api.fail(
            'narrowing: lambda must take exactly one positional argument',
            call,
        )
        _register_alias(context.api, context.name, fallback_target)
        return

    base_typeinfo = _resolve_base_typeinfo(base_argument)
    if base_typeinfo is None:
        base_representation: object = getattr(base_argument, 'name', base_argument)
        context.api.fail(
            f'narrowing: cannot resolve base type {base_representation!r}',
            call,
        )
        _register_alias(context.api, context.name, fallback_target)
        return

    target = Instance(base_typeinfo, [])
    alias_fullname = _register_alias(context.api, context.name, target)
    predicate = _reconstruct_call_form_predicate(context.api, predicate_argument)
    predicate_registry[alias_fullname] = PredicateRecord(
        base_fullname=base_typeinfo.fullname,
        source_repr='<lambda>',
        predicate=predicate,
    )


def _reconstruct_call_form_predicate(  # type: ignore[misc]
    api: Any,
    predicate_argument: LambdaExpr,
) -> Optional[Callable[[object], object]]:
    """
    Re-parse the user's module source, locate the lambda by line/column,
    and compile its body into a callable. Returns None when the source
    is unavailable or the lambda cannot be uniquely identified.

    `api` is the same opaque `SemanticAnalyzerPluginInterface`-shaped object
    as in `_register_alias`; documented attributes are accessed by name.
    """
    try:
        module_id: str = api.cur_mod_id  # type: ignore[misc]
        module_file = api.modules.get(module_id)  # type: ignore[misc]
        module_file_object: object = module_file
        if module_file_object is None:  # pragma: no cover
            return None
        source_attribute: object = getattr(module_file_object, 'source', None)
        source: Optional[str] = source_attribute if isinstance(source_attribute, str) else None
        if source is None:  # pragma: no cover
            path: object = getattr(module_file_object, 'path', None)
            if not isinstance(path, str):
                return None
            with open(path) as file_handle:
                source = file_handle.read()
        tree = ast.parse(source)
        target_line: int = predicate_argument.line
        target_column: int = predicate_argument.column
        candidate: Optional[ast.Lambda] = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Lambda) and node.lineno == target_line:
                if (  # pragma: no branch
                    candidate is None
                    or abs(node.col_offset - target_column) < abs(candidate.col_offset - target_column)
                ):
                    candidate = node
        if candidate is None or len(candidate.args.args) != 1:  # pragma: no cover  # type: ignore[misc]
            return None
        argument_name = candidate.args.args[0].arg
        code = compile(ast.Expression(candidate.body), '<narrowing predicate>', 'eval')
        evaluation_globals: Dict[str, object] = {'__builtins__': __builtins__}

        def predicate(value: object) -> object:
            return eval(code, evaluation_globals, {argument_name: value})  # type: ignore[misc]

        return predicate
    except Exception:  # pragma: no cover  # noqa: BLE001
        return None


def subscript_form_type_analyze_hook(context: AnalyzeTypeContext) -> Type:
    """
    Handle ``Narrowed[int, 'x > 0']`` in type-analysis position.

    Validates the parameter shape and the predicate string, then resolves
    the base type. Registers the predicate under a position-derived key so
    the assignment-hook can locate it for inline annotations.
    """
    if not isinstance(context.type, UnboundType):  # pragma: no cover
        return AnyType(TypeOfAny.from_error)
    arguments = list(context.type.args)
    if len(arguments) != 2:
        context.api.fail(
            f'narrowing: expected Narrowed[T, "expr"], got {len(arguments)} parameter(s)',
            context.context,
        )
        return AnyType(TypeOfAny.from_error)

    base_unbound, predicate_node = arguments
    if not isinstance(predicate_node, RawExpressionType) or not isinstance(predicate_node.literal_value, str):
        context.api.fail('narrowing: predicate must be a string', context.context)
        return AnyType(TypeOfAny.from_error)
    expression_string: str = predicate_node.literal_value

    try:
        tree = ast.parse(expression_string, mode='eval')
    except SyntaxError:
        # See `lambda_check.make_string_predicate` for why the SyntaxError
        # message itself is intentionally dropped (version-stable diagnostic).
        context.api.fail('narrowing: invalid predicate expression', context.context)
        return AnyType(TypeOfAny.from_error)
    try:
        check_predicate_ast(tree.body, allowed_argument='x', caller_globals={})
    except TypeError as exception:
        context.api.fail(str(exception), context.context)
        return AnyType(TypeOfAny.from_error)

    base_resolved = context.api.analyze_type(base_unbound)
    predicate: Optional[Callable[[object], object]] = None
    try:
        compiled_code = compile(tree, '<narrowing predicate>', 'eval')
        evaluation_globals: Dict[str, object] = {'__builtins__': __builtins__}

        def _evaluate_predicate(value: object) -> object:
            return eval(compiled_code, evaluation_globals, {'x': value})  # type: ignore[misc]

        predicate = _evaluate_predicate
    except Exception:  # pragma: no cover  # noqa: BLE001
        predicate = None

    base_fullname = ''
    if isinstance(base_resolved, Instance):  # pragma: no branch
        base_fullname = base_resolved.type.fullname
    position_key = f'__e_form__:{context.context.line}:{context.context.column}'
    predicate_registry[position_key] = PredicateRecord(
        base_fullname=base_fullname,
        source_repr=expression_string,
        predicate=predicate,
    )
    return base_resolved


def narrowed_call_function_hook(context: FunctionContext) -> Type:
    """
    Make `Narrowed(int, lambda x: ...)` usable as the second argument of
    `isinstance`/`issubclass` by returning `Type[base]` instead of `Narrowed[base]`.

    Without this hook the call expression has type `Narrowed[base]`, which
    `isinstance` rejects (`expected "_ClassInfo"`). Returning `TypeType(base)`
    matches the shape `mypy.checker.get_isinstance_type` accepts and lets
    flow-narrowing kick in.

    For generic bases (`list`, `dict`, etc.) `fill_typevars` produces an
    `Instance` with `Any`-filled parameters; otherwise mypy reveals a bare
    `list` instead of `list[Any]` in the narrowed branch.

    Falls back to `default_return_type` when the base argument can't be
    resolved to a `TypeInfo` (Any / Optional / Union / unknown name).
    """
    if not context.args or not context.args[0]:
        return context.default_return_type
    base_argument = context.args[0][0]
    base_typeinfo = _resolve_base_typeinfo(base_argument)
    if base_typeinfo is None:
        return context.default_return_type
    if base_typeinfo.type_vars:
        filled = fill_typevars_with_any(base_typeinfo)
        # `fill_typevars_with_any` returns Instance | TupleType per stub; for
        # our use only Instance is valid as the inner type of TypeType.
        if isinstance(filled, Instance):
            return TypeType(filled)
        return context.default_return_type  # pragma: no cover
    return TypeType(Instance(base_typeinfo, []))


from narrowing import assignment_hook as _assignment_hook  # noqa: E402, F401, I001  # late import to break the circular dependency; importing applies the runtime patch
from narrowing import isinstance_hook as _isinstance_hook  # noqa: E402, F401  # late import; importing applies the inline-isinstance patch


class NarrowingPlugin(Plugin):
    """Mypy plugin entry point dispatching to the call/subscript form hooks."""

    def get_dynamic_class_hook(
        self,
        fullname: str,
    ) -> Optional[Callable[[DynamicClassDefContext], None]]:
        if fullname in (NARROWED_FULLNAME, NARROWED_REEXPORT):
            return call_form_dynamic_class_hook
        return None

    def get_type_analyze_hook(
        self,
        fullname: str,
    ) -> Optional[Callable[[AnalyzeTypeContext], Type]]:
        if fullname in (NARROWED_FULLNAME, NARROWED_REEXPORT):
            return subscript_form_type_analyze_hook
        return None

    def get_function_hook(
        self,
        fullname: str,
    ) -> Optional[Callable[[FunctionContext], Type]]:
        if fullname in (NARROWED_FULLNAME, NARROWED_REEXPORT):
            return narrowed_call_function_hook
        return None


def plugin(version: str) -> TypingType[Plugin]:  # noqa: ARG001
    return NarrowingPlugin
