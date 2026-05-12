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
        unanalyzed_type = UnboundType('UniqueAliasNameForCompatTest', [])
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


def test_extract_literal_value_for_true():
    from mypy.nodes import NameExpr
    expression = NameExpr('True')
    expression.fullname = 'builtins.True'
    assert isinstance_hook._extract_literal_value(expression) is True


def test_extract_literal_value_for_false():
    from mypy.nodes import NameExpr
    expression = NameExpr('False')
    expression.fullname = 'builtins.False'
    assert isinstance_hook._extract_literal_value(expression) is False


def test_extract_literal_value_for_none():
    from mypy.nodes import NameExpr
    expression = NameExpr('None')
    expression.fullname = 'builtins.None'
    assert isinstance_hook._extract_literal_value(expression) is None


def test_extract_literal_value_returns_unset_for_unrelated_nameexpr():
    from mypy.nodes import NameExpr
    expression = NameExpr('something_else')
    expression.fullname = 'somewhere.something_else'
    assert isinstance_hook._extract_literal_value(expression) is isinstance_hook._UNSET


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


def test_compile_lambda_from_source_returns_uncached_when_no_path():
    """When `chk.tree.path` is unavailable, fall back to uncached compile (also returns None here)."""
    from mypy.nodes import LambdaExpr

    class _Tree:
        path = None

    class _Checker:
        chk = _Tree()

    predicate_argument = LambdaExpr()
    predicate_argument.line = 1
    predicate_argument.column = 0
    result = isinstance_hook._compile_lambda_from_source(_Checker(), predicate_argument)  # type: ignore[arg-type]
    assert result is None


def test_cache_set_evicts_oldest_when_exceeds_max_entries():
    """LRU eviction kicks in once cache size crosses `_MAX_CACHE_ENTRIES`."""
    from collections import OrderedDict
    cache: OrderedDict = OrderedDict()
    for i in range(isinstance_hook._MAX_CACHE_ENTRIES + 5):
        isinstance_hook._cache_set(cache, f'key_{i}', i)
    assert len(cache) == isinstance_hook._MAX_CACHE_ENTRIES
    # First 5 keys evicted
    assert 'key_0' not in cache
    assert 'key_4' not in cache
    assert 'key_5' in cache
    assert f'key_{isinstance_hook._MAX_CACHE_ENTRIES + 4}' in cache


def _make_checker_with_names(names_dict):  # type: ignore[no-untyped-def]
    """Construct a stub `ExpressionChecker` whose `.chk.tree.names` is `names_dict`."""
    class _Tree:
        names = names_dict

    class _CheckerInternal:
        tree = _Tree()

    class _Checker:
        chk = _CheckerInternal()

    return _Checker()


def test_build_caller_globals_skips_non_string_symbol_name():
    """A non-str key in `chk.tree.names` is ignored, not crashed on."""
    class _SymtabNode:
        node = None

    checker = _make_checker_with_names({object(): _SymtabNode()})
    result = isinstance_hook._build_caller_globals(checker)  # type: ignore[arg-type]
    assert result == {}


def test_build_caller_globals_skips_node_with_non_string_fullname():
    """A SymbolTableNode whose `node.fullname` isn't a str is skipped."""
    class _NarrowedNode:
        fullname = 123  # not a string

    class _SymtabNode:
        node = _NarrowedNode()

    checker = _make_checker_with_names({'broken': _SymtabNode()})
    result = isinstance_hook._build_caller_globals(checker)  # type: ignore[arg-type]
    assert result == {}


def test_build_caller_globals_resolves_stdlib_attribute_via_dotted_fullname():
    """`from os.path import join` (fullname has a dot) resolves to the attribute object."""
    import os.path

    class _NarrowedNode:
        fullname = 'os.path.join'

    class _SymtabNode:
        node = _NarrowedNode()

    checker = _make_checker_with_names({'join': _SymtabNode()})
    result = isinstance_hook._build_caller_globals(checker)  # type: ignore[arg-type]
    assert result.get('join') is os.path.join


def test_load_module_ast_reads_source_from_disk_when_tree_source_is_none(tmp_path):
    """When `tree.source` is None, `_load_module_ast` reads the file from disk and parses it."""
    file_path = tmp_path / 'sample.py'
    file_path.write_text('x = 1\n')

    class _Tree:
        path = str(file_path)
        source = None

    isinstance_hook._module_ast_cache.pop(str(file_path), None)
    parsed = isinstance_hook._load_module_ast(_Tree())
    assert parsed is not None
    assert str(file_path) in isinstance_hook._module_ast_cache


def test_load_module_ast_caches_none_on_syntax_error(tmp_path):
    """A file with invalid Python syntax caches `None` so we don't re-parse on every call."""
    file_path = tmp_path / 'broken.py'
    file_path.write_text('def: invalid python\n')

    class _Tree:
        path = str(file_path)
        source = None

    isinstance_hook._module_ast_cache.pop(str(file_path), None)
    parsed = isinstance_hook._load_module_ast(_Tree())
    assert parsed is None
    assert isinstance_hook._module_ast_cache[str(file_path)] is None


def test_compile_lambda_uncached_returns_none_when_no_lambda_at_line(tmp_path):
    """If `predicate_argument.line` doesn't match any lambda in the source, returns None."""
    file_path = tmp_path / 'no_lambdas.py'
    file_path.write_text('x = 1\ny = 2\n')

    class _Tree:
        path = str(file_path)
        source = None

    class _CheckerInternal:
        tree = _Tree()

    class _Checker:
        chk = _CheckerInternal()

    from mypy.nodes import LambdaExpr
    predicate_argument = LambdaExpr()
    predicate_argument.line = 99999
    predicate_argument.column = 0

    isinstance_hook._module_ast_cache.pop(str(file_path), None)
    result = isinstance_hook._compile_lambda_uncached(_Checker(), predicate_argument)  # type: ignore[arg-type]
    assert result is None


def test_compile_lambda_uncached_finds_lambda_at_matching_line(tmp_path):
    """When a lambda exists at `predicate_argument.line`, it gets compiled into a callable."""
    file_path = tmp_path / 'with_lambda.py'
    file_path.write_text('result = (lambda x: x > 0)(5)\n')

    class _Tree:
        path = str(file_path)
        source = None
        names = {}  # type: ignore[var-annotated]

    class _CheckerInternal:
        tree = _Tree()

    class _Checker:
        chk = _CheckerInternal()

    from mypy.nodes import LambdaExpr
    predicate_argument = LambdaExpr()
    predicate_argument.line = 1
    predicate_argument.column = 11

    isinstance_hook._module_ast_cache.pop(str(file_path), None)
    callable_predicate = isinstance_hook._compile_lambda_uncached(_Checker(), predicate_argument)  # type: ignore[arg-type]
    assert callable_predicate is not None
    assert callable_predicate(5) is True
    assert callable_predicate(-1) is False


def test_post_check_direct_literal_type_rvalue_passes_predicate():
    """A `LiteralType` rvalue (no `last_known_value` wrapper) goes through the direct branch."""
    from mypy.types import LiteralType

    fullname = '__test_direct_literal__.UniqueAliasNameForCompatTest'
    predicate_registry[fullname] = PredicateRecord('int', 'lambda x: x > 0', lambda x: x > 0)  # type: ignore[arg-type, operator]

    try:
        class _LiteralRvalue:
            pass

        class _ExprChecker:
            def accept(self, _):  # type: ignore[no-untyped-def]
                return LiteralType(value=5, fallback=None)  # type: ignore[arg-type]

        class _Msg:
            failures = []  # type: ignore[var-annotated]
            def fail(self, message, _context):  # type: ignore[no-untyped-def]
                self.failures.append(message)

        class _Checker:
            expr_checker = _ExprChecker()
            msg = _Msg()
            tree = None

        class _Stmt:
            unanalyzed_type = UnboundType('UniqueAliasNameForCompatTest', [])
            lvalues = [object()]
            rvalue = _LiteralRvalue()

        checker = _Checker()
        assignment_hook._post_check(checker=checker, statement=_Stmt())  # type: ignore[arg-type]
        assert checker.msg.failures == []
    finally:
        del predicate_registry[fullname]


def test_post_check_direct_literal_type_rvalue_fails_predicate():
    """A `LiteralType` rvalue that fails the predicate emits the standard failure message."""
    from mypy.types import LiteralType

    fullname = '__test_direct_literal_fail__.UniqueAliasNameForCompatTest'
    predicate_registry[fullname] = PredicateRecord('int', 'lambda x: x > 0', lambda x: x > 0)  # type: ignore[arg-type, operator]

    try:
        class _LiteralRvalue:
            pass

        class _ExprChecker:
            def accept(self, _):  # type: ignore[no-untyped-def]
                return LiteralType(value=-5, fallback=None)  # type: ignore[arg-type]

        class _Msg:
            failures = []  # type: ignore[var-annotated]
            def fail(self, message, _context):  # type: ignore[no-untyped-def]
                self.failures.append(message)

        class _Checker:
            expr_checker = _ExprChecker()
            msg = _Msg()
            tree = None

        class _Stmt:
            unanalyzed_type = UnboundType('UniqueAliasNameForCompatTest', [])
            lvalues = [object()]
            rvalue = _LiteralRvalue()

        checker = _Checker()
        assignment_hook._post_check(checker=checker, statement=_Stmt())  # type: ignore[arg-type]
        assert any('predicate rejected literal -5' in message for message in checker.msg.failures)
    finally:
        del predicate_registry[fullname]


def test_post_check_swallows_predicate_exception():
    """A predicate that raises against the literal is silenced (no failure emitted)."""
    from mypy.types import LiteralType

    def bomb(_value):  # type: ignore[no-untyped-def]
        raise RuntimeError('boom')

    fullname = '__test_exception_swallow__.UniqueAliasNameForCompatTest'
    predicate_registry[fullname] = PredicateRecord('int', 'lambda x: 1/x > 0', bomb)

    try:
        class _ExprChecker:
            def accept(self, _):  # type: ignore[no-untyped-def]
                return LiteralType(value=0, fallback=None)  # type: ignore[arg-type]

        class _Msg:
            failures = []  # type: ignore[var-annotated]
            def fail(self, message, _context):  # type: ignore[no-untyped-def]
                self.failures.append(message)

        class _Checker:
            expr_checker = _ExprChecker()
            msg = _Msg()
            tree = None

        class _Stmt:
            unanalyzed_type = UnboundType('UniqueAliasNameForCompatTest', [])
            lvalues = [object()]
            rvalue = object()

        checker = _Checker()
        assignment_hook._post_check(checker=checker, statement=_Stmt())  # type: ignore[arg-type]
        assert checker.msg.failures == []
    finally:
        del predicate_registry[fullname]


def test_lookup_record_for_annotation_returns_record_via_alias_fullname():
    """When the alias fullname is registered, `_lookup_record_for_annotation` returns its record."""
    fullname = '__test_alias_lookup__.SomeAlias'
    record = PredicateRecord('int', '', None)
    predicate_registry[fullname] = record

    try:
        unanalyzed = UnboundType('SomeAlias', [])

        class _Tree:
            class _Symbol:
                node = type('Node', (), {'fullname': fullname})()
            names = {'SomeAlias': _Symbol()}

        class _Checker:
            tree = _Tree()

        resolved = assignment_hook._lookup_record_for_annotation(_Checker(), unanalyzed)  # type: ignore[arg-type]
        assert resolved is record
    finally:
        del predicate_registry[fullname]


def test_maybe_emit_literal_rejection_swallows_predicate_exception():
    """Inline-isinstance predicate that raises against a literal — no failure emitted."""
    from mypy.nodes import CallExpr, IntExpr, LambdaExpr, NameExpr

    def bomb(_value):  # type: ignore[no-untyped-def]
        raise RuntimeError('boom')

    saved_extract = isinstance_hook._extract_call_form_predicate
    isinstance_hook._extract_call_form_predicate = lambda _checker, _arg: bomb  # type: ignore[assignment]
    failures = []

    try:
        class _Msg:
            def fail(self, message, _context):  # type: ignore[no-untyped-def]
                failures.append(message)

        class _CheckerInternal:
            msg = _Msg()

        class _Checker:
            chk = _CheckerInternal()

        callee = NameExpr('Narrowed')
        callee.fullname = NARROWED_FULLNAME
        narrowed_call = CallExpr(callee, [NameExpr('int'), LambdaExpr()], [], [])  # type: ignore[arg-type]
        expression = CallExpr(NameExpr('isinstance'), [IntExpr(0), narrowed_call], [], [])  # type: ignore[arg-type]
        isinstance_hook._maybe_emit_literal_rejection(_Checker(), expression)  # type: ignore[arg-type]
        assert failures == []
    finally:
        isinstance_hook._extract_call_form_predicate = saved_extract


def test_build_caller_globals_skips_flat_fullname_not_in_sys_modules():
    """Flat (no-dot) fullname not in `sys.modules` — skipped without crash."""
    class _NarrowedNode:
        fullname = '__nonexistent_flat_module_unique__'

    class _SymtabNode:
        node = _NarrowedNode()

    checker = _make_checker_with_names({'whatever': _SymtabNode()})
    result = isinstance_hook._build_caller_globals(checker)  # type: ignore[arg-type]
    assert 'whatever' not in result


def test_compile_lambda_uncached_keeps_closer_candidate_against_farther_one(tmp_path):
    """When a second lambda on the same line is farther from the target, the first wins."""
    file_path = tmp_path / 'two_lambdas_alt.py'
    # First lambda starts ~col 4, second ~col 30. Target near first.
    file_path.write_text('a = (lambda x: x > 0); b = (lambda x: x < 999)\n')

    class _Tree:
        path = str(file_path)
        source = None
        names = {}  # type: ignore[var-annotated]

    class _CheckerInternal:
        tree = _Tree()

    class _Checker:
        chk = _CheckerInternal()

    from mypy.nodes import LambdaExpr
    predicate_argument = LambdaExpr()
    predicate_argument.line = 1
    predicate_argument.column = 4  # closer to the FIRST lambda

    isinstance_hook._module_ast_cache.pop(str(file_path), None)
    callable_predicate = isinstance_hook._compile_lambda_uncached(_Checker(), predicate_argument)  # type: ignore[arg-type]
    assert callable_predicate is not None
    # First lambda has `x > 0`, not `x < 999`.
    assert callable_predicate(5) is True
    assert callable_predicate(-5) is False


def test_build_caller_globals_continues_when_dotted_parent_lacks_attribute():
    """Dotted fullname where parent module exists but attribute is absent — skipped silently."""
    import sys
    import types

    fake_module = types.ModuleType('__fake_parent_for_caller_globals__')
    sys.modules['__fake_parent_for_caller_globals__'] = fake_module
    try:
        class _NarrowedNode:
            fullname = '__fake_parent_for_caller_globals__.absent_attr'

        class _SymtabNode:
            node = _NarrowedNode()

        checker = _make_checker_with_names({'absent_attr': _SymtabNode()})
        result = isinstance_hook._build_caller_globals(checker)  # type: ignore[arg-type]
        assert 'absent_attr' not in result
    finally:
        del sys.modules['__fake_parent_for_caller_globals__']


def test_compile_lambda_uncached_picks_closest_column_when_multiple_lambdas_on_same_line(tmp_path):
    """When two lambdas live on the same line, the one closest to `target_column` wins."""
    file_path = tmp_path / 'two_lambdas.py'
    file_path.write_text('a = (lambda x: x > 0); b = (lambda x: x < 0)\n')

    class _Tree:
        path = str(file_path)
        source = None
        names = {}  # type: ignore[var-annotated]

    class _CheckerInternal:
        tree = _Tree()

    class _Checker:
        chk = _CheckerInternal()

    from mypy.nodes import LambdaExpr
    predicate_argument = LambdaExpr()
    predicate_argument.line = 1
    predicate_argument.column = 30  # closer to the SECOND lambda

    isinstance_hook._module_ast_cache.pop(str(file_path), None)
    callable_predicate = isinstance_hook._compile_lambda_uncached(_Checker(), predicate_argument)  # type: ignore[arg-type]
    assert callable_predicate is not None
    # Second lambda has `x < 0` body
    assert callable_predicate(-5) is True
    assert callable_predicate(5) is False


def test_load_module_ast_uses_in_memory_source_without_reading_disk(tmp_path):
    """When `tree.source` is set, `_load_module_ast` uses it directly (skips file read)."""
    file_path = tmp_path / 'in_memory.py'
    file_path.write_text('this is invalid python on disk\n')

    class _Tree:
        path = str(file_path)
        source = 'x = 1\n'  # valid in-memory source overrides the broken disk content

    isinstance_hook._module_ast_cache.pop(str(file_path), None)
    parsed = isinstance_hook._load_module_ast(_Tree())
    assert parsed is not None


def test_build_narrowed_class_falls_back_when_metaclass_conflicts():
    """Base with an incompatible metaclass triggers the try/except fallback (`bases=()`)."""
    class _CustomMeta(type):
        pass

    class _CustomBase(metaclass=_CustomMeta):
        pass

    # Construct narrowed class directly: this exercises the `except TypeError`
    # path that catches metaclass conflicts and retries with `bases=()`.
    from narrowing.narrowed import _build_narrowed_class
    narrowed_class = _build_narrowed_class(
        _CustomBase, lambda _value: True, '<lambda>',
    )
    # The metaclass conflict fallback yields a class whose `__mro__` does NOT
    # include `_CustomBase` (since `bases=()` was used).
    assert _CustomBase not in narrowed_class.__mro__


def test_patched_visit_index_expr_single_index_falls_through_to_accept():
    """For `Narrowed[X]` (single index, not TupleExpr) the patch still binds the index."""
    from mypy.nodes import IndexExpr, NameExpr

    base = NameExpr('Narrowed')
    base.fullname = NARROWED_FULLNAME
    inner_index = NameExpr('something')

    expression = IndexExpr(base, inner_index)

    accepted: list = []

    class _Analyzer:
        # NameExpr.accept dispatches via `visitor.visit_name_expr(self)`; both
        # the base and the index pass through, so we need to handle that name.
        def visit_name_expr(self, node):  # type: ignore[no-untyped-def]
            accepted.append(node)

    isinstance_hook._patched_visit_index_expr(_Analyzer(), expression)  # type: ignore[arg-type]
    # Base + index get accepted; original visit_index_expr is NOT called.
    assert base in accepted
    assert inner_index in accepted


def test_compile_lambda_from_source_caches_subsequent_calls():
    """Second call with same key hits the LRU cache and returns the same result."""
    from mypy.nodes import LambdaExpr

    class _Tree:
        path = '/tmp/__definitely_not_a_real_python_module__.py'
        source = None

    class _CheckerInternal:
        tree = _Tree()

    class _Checker:
        chk = _CheckerInternal()

    predicate_argument = LambdaExpr()
    predicate_argument.line = 1
    predicate_argument.column = 0
    first = isinstance_hook._compile_lambda_from_source(_Checker(), predicate_argument)  # type: ignore[arg-type]
    second = isinstance_hook._compile_lambda_from_source(_Checker(), predicate_argument)  # type: ignore[arg-type]
    assert first is None
    assert second is None
    # Cache entry was created for this position; ensure key is present.
    assert ('/tmp/__definitely_not_a_real_python_module__.py', 1, 0) in isinstance_hook._predicate_cache


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
