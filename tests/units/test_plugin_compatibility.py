"""
Self-tests for the mypy plugin compatibility layer.

Verifies that the patch on `TypeChecker.visit_assignment_stmt` is installed,
that the compiled-mypy advisory warning fires when applicable, and that the
post-check stays silent on the typical no-op paths.
"""
import importlib
import warnings

from mypy.checker import TypeChecker
from mypy.checkexpr import ExpressionChecker
from mypy.semanal import SemanticAnalyzer
from mypy.types import UnboundType

from narrowing import assignment_hook, isinstance_hook
from narrowing.plugin import (
    NARROWED_FULLNAME,
    NARROWED_REEXPORT,
    NarrowingPlugin,
    PredicateRecord,
    narrowed_call_function_hook,
    plugin,
    predicate_registry,
)


def test_plugin_loads():
    """The `plugin` factory returns the plugin class for any version string."""
    assert plugin('1.14.1') is NarrowingPlugin


def test_assignment_hook_is_installed():
    """The patch is bound to `TypeChecker.visit_assignment_stmt` after import."""
    assert TypeChecker.visit_assignment_stmt is assignment_hook._patched_visit_assignment_statement


def test_compiled_mypy_emits_advisory_warning():
    """When mypy is mypyc-compiled, reloading the hook surfaces a UserWarning."""
    if not assignment_hook._is_compiled_mypy():
        return

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        importlib.reload(assignment_hook)

    messages = [str(warning.message) for warning in caught if 'mypyc-compiled' in str(warning.message)]
    assert messages, 'expected compiled-mypy warning was not emitted'


def test_post_check_skips_when_no_annotation():
    """An assignment with no `unanalyzed_type` returns silently."""
    class _Statement:
        unanalyzed_type = None
        lvalues = []
        rvalue = None

    assignment_hook._post_check(checker=None, statement=_Statement())  # type: ignore[arg-type]


def test_post_check_skips_multi_lvalue_assignments():
    """Multi-lvalue assignments aren't candidates for predicate narrowing."""
    class _Statement:
        unanalyzed_type = UnboundType('PositiveInt', [])
        lvalues = [object(), object()]
        rvalue = None

    assignment_hook._post_check(checker=None, statement=_Statement())  # type: ignore[arg-type]


def test_resolve_alias_fullname_falls_back_to_suffix_match_when_tree_is_none():
    """Without a module symtab, a registry suffix match resolves the fullname."""
    fullname = '__test_no_tree__.SomeAlias'
    predicate_registry[fullname] = PredicateRecord('int', '', None)

    try:
        class _Checker:
            tree = None

        assert assignment_hook._resolve_alias_fullname(_Checker(), 'SomeAlias') == fullname  # type: ignore[arg-type]
    finally:
        del predicate_registry[fullname]


def test_resolve_alias_fullname_returns_none_for_unknown_name():
    class _Tree:
        names: dict = {}

    class _Checker:
        tree = _Tree()

    assert assignment_hook._resolve_alias_fullname(_Checker(), 'definitely_not_registered') is None  # type: ignore[arg-type]


def test_lookup_record_for_non_unbound_type_returns_none():
    assert assignment_hook._lookup_record_for_annotation(None, object()) is None  # type: ignore[arg-type]


def test_post_check_swallows_predicate_exception_and_does_not_fail():
    """If a predicate raises while evaluating against a literal, no error is reported."""
    import mypy.api

    source = (
        'from narrowing import Narrowed\n'
        # Predicate references attribute of int that always exists, but division
        # throws ZeroDivisionError when called on the literal 0 — the predicate
        # raises, which the hook should swallow.
        'BombInt = Narrowed(int, lambda x: 1 / x > 0)\n'
        'y: BombInt = 0\n'
    )
    out, _err, _code = mypy.api.run(['-c', source, '--no-incremental'])

    assert 'predicate rejected literal' not in out


def test_get_function_hook_returns_callable_for_narrowed_fullname():
    """Plugin returns the function hook for both the canonical and the re-exported fullname."""
    from mypy.options import Options
    plugin_instance = NarrowingPlugin(Options())
    assert plugin_instance.get_function_hook(NARROWED_FULLNAME) is narrowed_call_function_hook
    assert plugin_instance.get_function_hook(NARROWED_REEXPORT) is narrowed_call_function_hook
    assert plugin_instance.get_function_hook('os.path.join') is None


def test_function_hook_returns_default_for_zero_args():
    """When `context.args` is empty the hook falls back to default_return_type."""
    sentinel_default: object = object()

    class _Context:
        args: list = []
        default_return_type = sentinel_default

    result = narrowed_call_function_hook(_Context())  # type: ignore[arg-type]
    assert result is sentinel_default


def test_isinstance_hook_visit_call_expr_inner_is_installed():
    """Patch is bound to `ExpressionChecker.visit_call_expr_inner` after import."""
    assert ExpressionChecker.visit_call_expr_inner is isinstance_hook._patched_visit_call_expr_inner


def test_isinstance_hook_visit_index_expr_is_installed():
    """Patch is bound to `SemanticAnalyzer.visit_index_expr` after import."""
    assert SemanticAnalyzer.visit_index_expr is isinstance_hook._patched_visit_index_expr


def test_isinstance_hook_compiled_mypy_emits_advisory_warning():
    """When mypy is mypyc-compiled, reloading the hook surfaces a UserWarning."""
    if not isinstance_hook._is_compiled_mypy():
        return

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        importlib.reload(isinstance_hook)

    messages = [str(warning.message) for warning in caught if 'mypyc-compiled' in str(warning.message)]
    assert messages, 'expected compiled-mypy warning was not emitted'


def test_function_hook_returns_default_for_unresolved_base():
    """When `_resolve_base_typeinfo` returns None the hook falls back to default."""
    sentinel_default: object = object()

    class _UnresolvableExpression:
        node = None

    class _Context:
        args = [[_UnresolvableExpression()], []]
        default_return_type = sentinel_default

    result = narrowed_call_function_hook(_Context())  # type: ignore[arg-type]
    assert result is sentinel_default


def test_extract_literal_value_for_int():
    from mypy.nodes import IntExpr
    assert isinstance_hook._extract_literal_value(IntExpr(5)) == 5


def test_extract_literal_value_for_str():
    from mypy.nodes import StrExpr
    assert isinstance_hook._extract_literal_value(StrExpr('hello')) == 'hello'


def test_extract_literal_value_for_float():
    from mypy.nodes import FloatExpr
    assert isinstance_hook._extract_literal_value(FloatExpr(1.5)) == 1.5


def test_extract_literal_value_for_negated_int():
    from mypy.nodes import IntExpr, UnaryExpr
    assert isinstance_hook._extract_literal_value(UnaryExpr('-', IntExpr(5))) == -5


def test_extract_literal_value_for_negated_float():
    from mypy.nodes import FloatExpr, UnaryExpr
    assert isinstance_hook._extract_literal_value(UnaryExpr('-', FloatExpr(2.5))) == -2.5


def test_extract_literal_value_returns_unset_for_non_literal():
    from mypy.nodes import NameExpr
    result = isinstance_hook._extract_literal_value(NameExpr('something'))
    assert result is isinstance_hook._UNSET


def test_extract_literal_value_returns_unset_for_negated_non_literal():
    from mypy.nodes import NameExpr, UnaryExpr
    result = isinstance_hook._extract_literal_value(UnaryExpr('-', NameExpr('something')))
    assert result is isinstance_hook._UNSET


def test_is_narrowed_ref_false_for_non_refexpr():
    assert isinstance_hook._is_narrowed_ref(object()) is False


def test_is_narrowed_ref_false_for_unrelated_fullname():
    from mypy.nodes import NameExpr
    expression = NameExpr('something')
    expression.fullname = 'os.path.join'
    assert isinstance_hook._is_narrowed_ref(expression) is False


def test_is_narrowed_ref_true_for_canonical_fullname():
    from mypy.nodes import NameExpr
    expression = NameExpr('Narrowed')
    expression.fullname = NARROWED_FULLNAME
    assert isinstance_hook._is_narrowed_ref(expression) is True


def test_is_narrowed_ref_true_for_reexport_fullname():
    from mypy.nodes import NameExpr
    expression = NameExpr('Narrowed')
    expression.fullname = NARROWED_REEXPORT
    assert isinstance_hook._is_narrowed_ref(expression) is True


def test_extract_lambda_from_call_returns_none_for_non_callexpr():
    from mypy.nodes import NameExpr
    assert isinstance_hook._extract_lambda_from_call(NameExpr('x')) is None


def test_extract_lambda_from_call_returns_none_for_non_refexpr_callee():
    from mypy.nodes import CallExpr, IntExpr
    expression = CallExpr(IntExpr(5), [], [], [])  # type: ignore[arg-type]
    assert isinstance_hook._extract_lambda_from_call(expression) is None


def test_extract_lambda_from_call_returns_none_for_non_narrowed_callee():
    from mypy.nodes import CallExpr, NameExpr
    callee = NameExpr('OtherFactory')
    callee.fullname = 'somewhere.OtherFactory'
    expression = CallExpr(callee, [object(), object()], [], [])  # type: ignore[arg-type]
    assert isinstance_hook._extract_lambda_from_call(expression) is None


def test_extract_lambda_from_call_returns_none_for_wrong_arity():
    from mypy.nodes import CallExpr, NameExpr
    callee = NameExpr('Narrowed')
    callee.fullname = NARROWED_FULLNAME
    expression = CallExpr(callee, [object()], [], [])  # type: ignore[arg-type]
    assert isinstance_hook._extract_lambda_from_call(expression) is None


def test_extract_lambda_from_call_returns_none_for_non_lambda_predicate():
    from mypy.nodes import CallExpr, IntExpr, NameExpr
    callee = NameExpr('Narrowed')
    callee.fullname = NARROWED_FULLNAME
    expression = CallExpr(callee, [NameExpr('int'), IntExpr(5)], [], [])  # type: ignore[arg-type]
    assert isinstance_hook._extract_lambda_from_call(expression) is None


def test_extract_subscript_form_predicate_returns_none_for_non_refexpr_base():
    from mypy.nodes import IndexExpr, IntExpr
    expression = IndexExpr(IntExpr(5), IntExpr(0))  # type: ignore[arg-type]
    assert isinstance_hook._extract_subscript_form_predicate(None, expression) is None  # type: ignore[arg-type]


def test_extract_subscript_form_predicate_returns_none_for_non_narrowed_base():
    from mypy.nodes import IndexExpr, IntExpr, NameExpr
    base = NameExpr('SomethingElse')
    base.fullname = 'somewhere.SomethingElse'
    expression = IndexExpr(base, IntExpr(0))  # type: ignore[arg-type]
    assert isinstance_hook._extract_subscript_form_predicate(None, expression) is None  # type: ignore[arg-type]


def test_extract_subscript_form_predicate_returns_none_for_non_tuple_index():
    from mypy.nodes import IndexExpr, IntExpr, NameExpr
    base = NameExpr('Narrowed')
    base.fullname = NARROWED_FULLNAME
    expression = IndexExpr(base, IntExpr(0))  # type: ignore[arg-type]
    assert isinstance_hook._extract_subscript_form_predicate(None, expression) is None  # type: ignore[arg-type]


def test_extract_subscript_form_predicate_returns_none_for_non_str_predicate():
    from mypy.nodes import IndexExpr, IntExpr, NameExpr, TupleExpr
    base = NameExpr('Narrowed')
    base.fullname = NARROWED_FULLNAME
    index = TupleExpr([NameExpr('int'), IntExpr(5)])
    expression = IndexExpr(base, index)
    assert isinstance_hook._extract_subscript_form_predicate(None, expression) is None  # type: ignore[arg-type]


def test_extract_subscript_form_predicate_returns_none_for_invalid_predicate_string():
    from mypy.nodes import IndexExpr, NameExpr, StrExpr, TupleExpr
    base = NameExpr('Narrowed')
    base.fullname = NARROWED_FULLNAME
    index = TupleExpr([NameExpr('int'), StrExpr('x >')])  # syntax error in predicate

    class _FakeChecker:
        chk = None

    expression = IndexExpr(base, index)
    assert isinstance_hook._extract_subscript_form_predicate(_FakeChecker(), expression) is None  # type: ignore[arg-type]


def test_extract_base_typeinfo_from_index_returns_none_for_non_typeinfo():
    from mypy.nodes import NameExpr
    name = NameExpr('something')
    name.node = None
    assert isinstance_hook._extract_base_typeinfo_from_index(name) is None


def test_maybe_substitute_argument_returns_none_for_non_indexexpr():
    from mypy.nodes import NameExpr
    assert isinstance_hook._maybe_substitute_argument(None, NameExpr('x')) is None  # type: ignore[arg-type]


def test_maybe_substitute_argument_returns_none_for_non_refexpr_base():
    from mypy.nodes import IndexExpr, IntExpr
    expression = IndexExpr(IntExpr(5), IntExpr(0))  # type: ignore[arg-type]
    assert isinstance_hook._maybe_substitute_argument(None, expression) is None  # type: ignore[arg-type]


def test_maybe_substitute_argument_returns_none_for_non_narrowed_base():
    from mypy.nodes import IndexExpr, IntExpr, NameExpr
    base = NameExpr('SomethingElse')
    base.fullname = 'somewhere.SomethingElse'
    expression = IndexExpr(base, IntExpr(0))  # type: ignore[arg-type]
    assert isinstance_hook._maybe_substitute_argument(None, expression) is None  # type: ignore[arg-type]


def test_maybe_substitute_argument_returns_none_for_unresolvable_base_typeinfo():
    from mypy.nodes import IndexExpr, NameExpr, StrExpr, TupleExpr
    base = NameExpr('Narrowed')
    base.fullname = NARROWED_FULLNAME
    inner_base = NameExpr('something')
    inner_base.node = None
    index = TupleExpr([inner_base, StrExpr('x > 0')])
    expression = IndexExpr(base, index)
    assert isinstance_hook._maybe_substitute_argument(None, expression) is None  # type: ignore[arg-type]


def test_load_module_ast_returns_none_for_no_path():
    class _Tree:
        path = None
    assert isinstance_hook._load_module_ast(_Tree()) is None


def test_load_module_ast_returns_none_for_empty_path():
    class _Tree:
        path = ''
    assert isinstance_hook._load_module_ast(_Tree()) is None


def test_build_caller_globals_returns_empty_when_tree_has_no_names():
    class _Tree:
        names = None

    class _Checker:
        chk = None

    class _ExpressionChecker:
        chk = _Tree()

    result = isinstance_hook._build_caller_globals(_ExpressionChecker())  # type: ignore[arg-type]
    assert result == {}


def test_patched_visit_call_expr_inner_swallows_substitution_exception_and_emits_warning():
    """If `_maybe_substitute_argument` raises, the wrapper emits a warning and delegates to original."""
    from mypy.nodes import CallExpr, NameExpr

    sentinel = RuntimeError('synthetic')

    def boom(*_args, **_kwargs):
        raise sentinel

    saved_substitute = isinstance_hook._maybe_substitute_argument
    saved_original = isinstance_hook._original_visit_call_expr_inner
    isinstance_hook._maybe_substitute_argument = boom

    class _FakeOriginal:
        def __call__(self, *_args, **_kwargs):
            return 'ok'

    isinstance_hook._original_visit_call_expr_inner = _FakeOriginal()

    callee = NameExpr('isinstance')
    callee.fullname = 'builtins.isinstance'
    expression = CallExpr(callee, [object(), object()], [], [])  # type: ignore[arg-type]

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            result = isinstance_hook._patched_visit_call_expr_inner(object(), expression)  # type: ignore[arg-type]

        messages = [str(warning.message) for warning in caught if 'synthetic' in str(warning.message)]
        assert messages, 'expected synthetic-error warning was not emitted'
        assert result == 'ok'
    finally:
        isinstance_hook._maybe_substitute_argument = saved_substitute
        isinstance_hook._original_visit_call_expr_inner = saved_original


def test_patched_visit_swallows_post_check_exception_and_emits_warning():
    """If post_check itself raises, the wrapper logs a warning and returns the original result."""
    sentinel = RuntimeError('synthetic')

    def boom(*_args, **_kwargs):
        raise sentinel

    original = assignment_hook._post_check
    assignment_hook._post_check = boom
    saved_original = assignment_hook._original_visit_assignment_statement

    class _FakeOriginal:
        def __call__(self, *_args, **_kwargs):
            return 'ok'

    assignment_hook._original_visit_assignment_statement = _FakeOriginal()

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            assignment_hook._patched_visit_assignment_statement(object(), object())  # type: ignore[arg-type]

        messages = [str(warning.message) for warning in caught if 'synthetic' in str(warning.message)]
        assert messages, 'expected synthetic-error warning was not emitted'
    finally:
        assignment_hook._post_check = original
        assignment_hook._original_visit_assignment_statement = saved_original
