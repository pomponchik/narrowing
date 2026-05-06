"""
Self-tests for the mypy plugin compatibility layer.

Verifies that the patch on `TypeChecker.visit_assignment_stmt` is installed,
that the compiled-mypy advisory warning fires when applicable, and that the
post-check stays silent on the typical no-op paths.
"""
import importlib
import warnings

from mypy.checker import TypeChecker
from mypy.types import UnboundType

from narrowing import assignment_hook
from narrowing.plugin import (
    NarrowingPlugin,
    PredicateRecord,
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
